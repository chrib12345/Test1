"""Metrics materialization (Phase 3, Section 9).

Materializes metrics_daily per company:
  * open_count                      : current open set size
  * opened/closed/net over 7 & 30d  : opened excludes reposts and reappears
  * by_function / by_seniority / by_metro : breakdowns of the current open set
  * repost_rate_30d                 : reposts / gross opens (data-quality gauge)
Plus watchlist-level rollups on top.

Reposts and reappears never count as net-new hiring demand.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .logging_util import get_logger
from .models import Company, JobPosting, MetricsDaily, PostingEvent
from .util import utcnow

log = get_logger("metrics")


def _window_start(as_of: datetime, days: int) -> datetime:
    return as_of - timedelta(days=days)


def _count_events(
    session: Session, company_id: int, event_type: str, since: datetime,
    until: datetime, exclude_reposts: bool = False,
) -> int:
    q = (
        select(func.count())
        .select_from(PostingEvent)
        .where(
            PostingEvent.company_id == company_id,
            PostingEvent.event_type == event_type,
            PostingEvent.event_at > since,
            PostingEvent.event_at <= until,
        )
    )
    if exclude_reposts:
        q = q.join(JobPosting, JobPosting.id == PostingEvent.posting_id).where(
            JobPosting.reposted_from.is_(None)
        )
    return session.scalar(q) or 0


def _breakdowns(session: Session, company_id: int) -> tuple[dict, dict, dict]:
    """Counts over the current open set, bucketing nulls as 'Unknown'."""
    rows = session.execute(
        select(
            JobPosting.norm_function,
            JobPosting.norm_seniority,
            JobPosting.norm_metro,
        ).where(JobPosting.company_id == company_id, JobPosting.is_open.is_(True))
    ).all()
    by_fn: Counter = Counter()
    by_sen: Counter = Counter()
    by_metro: Counter = Counter()
    for fn, sen, metro in rows:
        by_fn[fn or "Unknown"] += 1
        by_sen[sen or "Unknown"] += 1
        by_metro[metro or "Unknown"] += 1
    return dict(by_fn), dict(by_sen), dict(by_metro)


def compute_company_metrics(
    session: Session, company_id: int, as_of: datetime
) -> MetricsDaily:
    open_count = session.scalar(
        select(func.count()).select_from(JobPosting).where(
            JobPosting.company_id == company_id, JobPosting.is_open.is_(True)
        )
    ) or 0

    d7, d30 = _window_start(as_of, 7), _window_start(as_of, 30)
    opened_7d = _count_events(session, company_id, "opened", d7, as_of, exclude_reposts=True)
    closed_7d = _count_events(session, company_id, "closed", d7, as_of)
    opened_30d = _count_events(session, company_id, "opened", d30, as_of, exclude_reposts=True)
    closed_30d = _count_events(session, company_id, "closed", d30, as_of)

    gross_opens_30d = _count_events(session, company_id, "opened", d30, as_of)
    reposts_30d = _count_events(session, company_id, "reposted", d30, as_of)
    repost_rate_30d = (reposts_30d / gross_opens_30d) if gross_opens_30d else None

    by_fn, by_sen, by_metro = _breakdowns(session, company_id)

    return MetricsDaily(
        company_id=company_id,
        as_of_date=as_of.date(),
        open_count=open_count,
        opened_7d=opened_7d,
        closed_7d=closed_7d,
        net_7d=opened_7d - closed_7d,
        opened_30d=opened_30d,
        closed_30d=closed_30d,
        by_function=by_fn,
        by_seniority=by_sen,
        by_metro=by_metro,
        repost_rate_30d=repost_rate_30d,
        computed_at=utcnow(),
    )


def materialize_metrics(
    session: Session, as_of: Optional[datetime] = None
) -> int:
    """Upsert metrics_daily for every active company for as_of's date."""
    as_of = as_of or utcnow()
    companies = session.scalars(
        select(Company).where(Company.is_active.is_(True))
    ).all()
    count = 0
    for c in companies:
        m = compute_company_metrics(session, c.id, as_of)
        session.merge(m)  # PK (company_id, as_of_date) -> idempotent per day
        count += 1
    log.info("metrics_daily materialized for %d companies (as_of=%s)", count, as_of.date())
    return count


def watchlist_rollup(session: Session, as_of: Optional[date] = None) -> dict:
    """Aggregate the day's metrics_daily rows into a watchlist-level view."""
    as_of = as_of or utcnow().date()
    rows = session.scalars(
        select(MetricsDaily).where(MetricsDaily.as_of_date == as_of)
    ).all()
    agg = {
        "as_of_date": as_of.isoformat(),
        "companies": len(rows),
        "open_count": sum(r.open_count for r in rows),
        "opened_7d": sum(r.opened_7d for r in rows),
        "closed_7d": sum(r.closed_7d for r in rows),
        "net_7d": sum(r.net_7d for r in rows),
        "opened_30d": sum(r.opened_30d for r in rows),
        "closed_30d": sum(r.closed_30d for r in rows),
        "by_function": dict(_merge_counters(r.by_function for r in rows)),
        "by_seniority": dict(_merge_counters(r.by_seniority for r in rows)),
        "by_metro": dict(_merge_counters(r.by_metro for r in rows)),
    }
    return agg


def _merge_counters(dicts) -> Counter:
    out: Counter = Counter()
    for d in dicts:
        out.update(d or {})
    return out


def run_metrics(as_of: Optional[datetime] = None) -> dict:
    from .db import session_scope

    as_of = as_of or utcnow()
    with session_scope() as s:
        n = materialize_metrics(s, as_of)
    with session_scope() as s:
        roll = watchlist_rollup(s, as_of.date())
    return {"companies": n, "rollup": roll}
