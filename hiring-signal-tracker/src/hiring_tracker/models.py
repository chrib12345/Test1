"""SQLAlchemy models mirroring the Postgres data model (Section 6).

Design principles baked in here:
  * Panel data, append-only. `snapshots` is one row per poll; `job_postings`
    is slowly-changing with an open/closed lifecycle and first/last_seen_at.
  * Provenance on every row (source_ats, fetch timestamp, raw payload retained).
  * The ATS's own identifier is the stable key:
    unique (company_id, source_ats, source_job_id).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# JSONB on Postgres; degrade to generic JSON elsewhere (e.g. sqlite in tooling).
JSONB_TYPE = JSONB().with_variant(
    __import__("sqlalchemy").JSON(), "sqlite"
)


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    ats_type: Mapped[str] = mapped_column(Text, nullable=False)
    ats_token: Mapped[str] = mapped_column(Text, nullable=False)
    careers_url: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    notes: Mapped[str | None] = mapped_column(Text)

    snapshots: Mapped[list["Snapshot"]] = relationship(back_populates="company")
    postings: Mapped[list["JobPosting"]] = relationship(back_populates="company")

    __table_args__ = (
        CheckConstraint(
            "ats_type in ('greenhouse','lever','ashby','workable','custom')",
            name="companies_ats_type_check",
        ),
    )


class Snapshot(Base):
    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("companies.id"), nullable=False
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ats_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    posting_count: Mapped[int | None] = mapped_column(Integer)
    payload_hash: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_detail: Mapped[str | None] = mapped_column(Text)

    company: Mapped["Company"] = relationship(back_populates="snapshots")
    raw_payload: Mapped["RawPayload"] = relationship(
        back_populates="snapshot", uselist=False
    )

    __table_args__ = (
        CheckConstraint(
            "status in ('ok','failed','empty')", name="snapshots_status_check"
        ),
        Index("ix_snapshots_company_fetched", "company_id", "fetched_at"),
    )


class RawPayload(Base):
    __tablename__ = "raw_payloads"

    snapshot_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("snapshots.id"), primary_key=True
    )
    body: Mapped[dict] = mapped_column(JSONB_TYPE, nullable=False)

    snapshot: Mapped["Snapshot"] = relationship(back_populates="raw_payload")


class JobPosting(Base):
    __tablename__ = "job_postings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("companies.id"), nullable=False
    )
    source_ats: Mapped[str] = mapped_column(Text, nullable=False)
    source_job_id: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    raw_department: Mapped[str | None] = mapped_column(Text)
    raw_team: Mapped[str | None] = mapped_column(Text)
    raw_location: Mapped[str | None] = mapped_column(Text)
    remote_flag: Mapped[bool | None] = mapped_column(Boolean)
    employment_type: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    apply_url: Mapped[str | None] = mapped_column(Text)
    description_hash: Mapped[str | None] = mapped_column(Text)
    comp_summary: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Repost linkage (Section 8.7): a fresh posting that matches a recently
    # closed one on (title, department, location, description_hash).
    reposted_from: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("job_postings.id")
    )

    # Enriched by the Claude normalization layer (Phase 2):
    norm_function: Mapped[str | None] = mapped_column(Text)
    norm_seniority: Mapped[str | None] = mapped_column(Text)
    norm_country: Mapped[str | None] = mapped_column(Text)
    norm_metro: Mapped[str | None] = mapped_column(Text)
    normalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    company: Mapped["Company"] = relationship(back_populates="postings")
    events: Mapped[list["PostingEvent"]] = relationship(back_populates="posting")

    __table_args__ = (
        UniqueConstraint(
            "company_id", "source_ats", "source_job_id", name="uq_posting_natural_key"
        ),
        Index("ix_postings_company_open", "company_id", "is_open"),
        Index("ix_postings_company_first_seen", "company_id", "first_seen_at"),
        Index("ix_postings_norm_function", "norm_function"),
        CheckConstraint(
            "not (is_open = true and closed_at is not null)",
            name="postings_open_closed_consistency",
        ),
    )


class PostingEvent(Base):
    __tablename__ = "posting_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    posting_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("job_postings.id"), nullable=False
    )
    company_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("companies.id"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    event_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    snapshot_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("snapshots.id")
    )

    posting: Mapped["JobPosting"] = relationship(back_populates="events")

    __table_args__ = (
        CheckConstraint(
            "event_type in ('opened','closed','reappeared','reposted','edited')",
            name="posting_events_type_check",
        ),
        Index("ix_posting_events_company_at", "company_id", "event_at"),
    )


class TitleMap(Base):
    __tablename__ = "title_map"

    raw_title: Mapped[str] = mapped_column(Text, primary_key=True)
    norm_function: Mapped[str] = mapped_column(Text, nullable=False)
    norm_seniority: Mapped[str] = mapped_column(Text, nullable=False)
    mapped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    mapped_by: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="claude"
    )


class MetricsDaily(Base):
    __tablename__ = "metrics_daily"

    company_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("companies.id"), primary_key=True
    )
    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)
    open_count: Mapped[int] = mapped_column(Integer, nullable=False)
    opened_7d: Mapped[int] = mapped_column(Integer, nullable=False)
    closed_7d: Mapped[int] = mapped_column(Integer, nullable=False)
    net_7d: Mapped[int] = mapped_column(Integer, nullable=False)
    opened_30d: Mapped[int] = mapped_column(Integer, nullable=False)
    closed_30d: Mapped[int] = mapped_column(Integer, nullable=False)
    by_function: Mapped[dict] = mapped_column(
        JSONB_TYPE, nullable=False, server_default="{}"
    )
    by_seniority: Mapped[dict] = mapped_column(
        JSONB_TYPE, nullable=False, server_default="{}"
    )
    by_metro: Mapped[dict] = mapped_column(
        JSONB_TYPE, nullable=False, server_default="{}"
    )
    repost_rate_30d: Mapped[float | None] = mapped_column(Numeric)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
