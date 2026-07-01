"""Metrics tests (Section 9): opened/closed/net, repost exclusion, breakdowns."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from hiring_tracker.adapters.base import FetchResult, NormalizedPosting
from hiring_tracker.diff import apply_snapshot
from hiring_tracker.metrics import compute_company_metrics, watchlist_rollup, materialize_metrics
from hiring_tracker.models import Company, JobPosting

T0 = datetime(2026, 4, 1, 8, 0, tzinfo=timezone.utc)


def _np(i, title="Engineer", dept="Engineering", loc="NYC", desc=None):
    return NormalizedPosting(
        source_job_id=str(i), title=title, raw_department=dept, raw_location=loc,
        description_plain=desc if desc is not None else f"jd-{i}",
    )


def _fetch(postings):
    return FetchResult(
        postings=list(postings), raw_body=[p.model_dump() for p in postings], http_status=200
    )


def _company(session):
    c = Company(name="Gamma", ats_type="greenhouse", ats_token="gamma")
    session.add(c)
    session.flush()
    return c


def test_metrics_exclude_reposts_from_opened(session):
    c = _company(session)
    # T0: open 1,2,3 (job 3 has a distinctive repost tuple)
    apply_snapshot(session, c, _fetch([
        _np(1), _np(2),
        _np(3, title="Growth PM", dept="Product", loc="SF", desc="same"),
    ]), T0)
    session.commit()
    # T1: close 3, open 4 as a repost of 3's tuple
    apply_snapshot(session, c, _fetch([
        _np(1), _np(2),
        _np(4, title="Growth PM", dept="Product", loc="SF", desc="same"),
    ]), T0 + timedelta(days=1))
    session.commit()

    as_of = T0 + timedelta(days=1, hours=1)
    m = compute_company_metrics(session, c.id, as_of)

    assert m.open_count == 3           # {1,2,4}
    assert m.opened_7d == 3            # 1,2,3 opened; 4 is a repost -> excluded
    assert m.closed_7d == 1            # job 3 closed
    assert m.net_7d == 2
    assert m.repost_rate_30d == 0.25   # 1 repost / 4 gross opens


def test_breakdowns_over_open_set(session):
    c = _company(session)
    apply_snapshot(session, c, _fetch([_np(1), _np(2), _np(3)]), T0)
    session.commit()
    # Enrich norm fields directly to exercise the breakdown grouping.
    for jp in session.scalars(select(JobPosting)).all():
        jp.norm_function = "Engineering"
        jp.norm_seniority = "IC-Senior"
        jp.norm_metro = "New York"
    session.commit()

    m = compute_company_metrics(session, c.id, T0 + timedelta(hours=1))
    assert m.by_function == {"Engineering": 3}
    assert m.by_seniority == {"IC-Senior": 3}
    assert m.by_metro == {"New York": 3}


def test_materialize_and_rollup(session):
    c = _company(session)
    apply_snapshot(session, c, _fetch([_np(1), _np(2)]), T0)
    session.commit()
    as_of = T0 + timedelta(hours=1)
    materialize_metrics(session, as_of)
    session.commit()

    roll = watchlist_rollup(session, as_of.date())
    assert roll["companies"] == 1
    assert roll["open_count"] == 2
