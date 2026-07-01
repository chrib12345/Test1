"""Orchestrates a full watchlist poll (Section 8 + Section 10).

  * Syncs config/watchlist.yaml into the companies table (upsert).
  * Polls each active company through its adapter.
  * Each company runs in its own transaction so one failure never rolls back
    another company's work, and a broken fetch flags the company (failed
    snapshot) rather than dropping it.
  * Produces a RunSummary and flags stale companies.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .adapters import AdapterError, get_adapter
from .config import Settings, WatchlistEntry, get_settings, load_watchlist
from .db import session_scope
from .diff import DiffOutcome, apply_snapshot, record_failed_snapshot
from .logging_util import RunSummary, get_logger
from .models import Company, JobPosting, Snapshot
from .util import utcnow

log = get_logger("ingest")


def sync_watchlist(session: Session, entries: Optional[list[WatchlistEntry]] = None) -> int:
    """Upsert the watchlist into companies. Natural key: ticker + ats_token,
    or (ats_type, ats_token) when ticker is null. Returns count synced."""
    entries = entries if entries is not None else load_watchlist()
    existing = session.scalars(select(Company)).all()

    def key(ticker, ats_type, ats_token):
        return (ticker, ats_token) if ticker else (f"__{ats_type}", ats_token)

    by_key = {key(c.ticker, c.ats_type, c.ats_token): c for c in existing}
    for e in entries:
        k = key(e.ticker, e.ats_type, e.ats_token)
        c = by_key.get(k)
        if c is None:
            c = Company(
                ticker=e.ticker,
                name=e.name,
                ats_type=e.ats_type,
                ats_token=e.ats_token,
                careers_url=e.careers_url,
                notes=e.notes,
                is_active=True,
            )
            session.add(c)
        else:
            c.name = e.name
            c.ats_type = e.ats_type
            c.careers_url = e.careers_url
            c.is_active = True
            if e.notes:
                c.notes = e.notes
    log.info("watchlist synced: %d entries", len(entries))
    return len(entries)


def poll_company(
    session: Session,
    company: Company,
    fetched_at: datetime,
    settings: Settings,
) -> DiffOutcome:
    """Poll a single company and apply the diff. Fails loud into a failed snapshot."""
    # The custom fallback is driven by careers_url, not an ats_token.
    token = company.careers_url if company.ats_type == "custom" else company.ats_token
    if not token:
        return record_failed_snapshot(
            session, company,
            error_detail=f"missing token/careers_url for ats_type={company.ats_type}",
            fetched_at=fetched_at,
        )
    try:
        adapter = get_adapter(company.ats_type)
        try:
            result = adapter.fetch(token)
        finally:
            close = getattr(adapter, "close", None)
            if callable(close):
                close()
    except AdapterError as e:
        return record_failed_snapshot(
            session, company, error_detail=str(e), fetched_at=fetched_at,
            http_status=getattr(e, "http_status", None),
        )
    except Exception as e:  # never let one company kill the run
        return record_failed_snapshot(
            session, company, error_detail=f"{type(e).__name__}: {e}",
            fetched_at=fetched_at,
        )

    # Adapter-emitted fragility notes get appended to companies.notes once.
    for note in result.notes:
        if not company.notes or note not in company.notes:
            company.notes = ((company.notes + " | ") if company.notes else "") + note

    return apply_snapshot(
        session, company, result, fetched_at,
        repost_window_days=settings.repost_window_days,
    )


def compute_stale_companies(session: Session, stale_after_days: int) -> list[str]:
    """A company is stale if its latest snapshot failed, its last successful
    snapshot is older than the threshold, or it has open postings not confirmed
    by the most recent successful snapshot."""
    stale: list[str] = []
    now = utcnow()
    companies = session.scalars(select(Company).where(Company.is_active.is_(True))).all()
    for c in companies:
        latest = session.scalars(
            select(Snapshot).where(Snapshot.company_id == c.id)
            .order_by(Snapshot.fetched_at.desc(), Snapshot.id.desc()).limit(1)
        ).first()
        latest_ok = session.scalars(
            select(Snapshot).where(
                Snapshot.company_id == c.id, Snapshot.status.in_(("ok", "empty"))
            ).order_by(Snapshot.fetched_at.desc(), Snapshot.id.desc()).limit(1)
        ).first()
        if latest is None:
            stale.append(c.name)
            continue
        if latest.status == "failed":
            stale.append(c.name)
            continue
        if latest_ok is None or (now - latest_ok.fetched_at) > timedelta(days=stale_after_days):
            stale.append(c.name)
            continue
        # Open postings not seen at/after the latest successful snapshot.
        stale_open = session.scalars(
            select(JobPosting.id).where(
                JobPosting.company_id == c.id,
                JobPosting.is_open.is_(True),
                JobPosting.last_seen_at < latest_ok.fetched_at,
            ).limit(1)
        ).first()
        if stale_open is not None:
            stale.append(c.name)
    return stale


def run_watchlist(
    settings: Optional[Settings] = None,
    fetched_at: Optional[datetime] = None,
    only_ticker: Optional[str] = None,
) -> RunSummary:
    """Run one full poll across the active watchlist. Zero unhandled exceptions:
    a run either completes or reports exactly which companies failed and why."""
    settings = settings or get_settings()
    run_ts = fetched_at or utcnow()
    summary = RunSummary()

    # Sync watchlist in its own transaction.
    with session_scope() as s:
        sync_watchlist(s)

    with session_scope() as s:
        q = select(Company).where(Company.is_active.is_(True))
        if only_ticker:
            q = q.where(Company.ticker == only_ticker)
        companies = s.scalars(q.order_by(Company.id)).all()
        company_ids = [(c.id, c.name) for c in companies]

    for cid, name in company_ids:
        summary.companies_polled += 1
        # Per-company transaction for isolation.
        try:
            with session_scope() as s:
                company = s.get(Company, cid)
                outcome = poll_company(s, company, run_ts, settings)
        except Exception as e:  # storage-level failure for this company
            summary.record_failure(name, f"storage error: {type(e).__name__}: {e}")
            log.exception("company=%s storage failure", name)
            continue

        if outcome.status == "failed":
            summary.record_failure(name, "fetch failed (see snapshots.error_detail)")
        elif outcome.status == "empty":
            summary.empty += 1
        else:
            summary.ok += 1
        outcome.merge_into_summary(summary)

    with session_scope() as s:
        summary.stale_companies = compute_stale_companies(s, settings.stale_after_days)

    print(summary.render())
    return summary
