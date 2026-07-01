"""Idempotency: re-running any poll creates zero duplicate rows (Section 8)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from hiring_tracker.adapters.base import FetchResult, NormalizedPosting
from hiring_tracker.diff import apply_snapshot
from hiring_tracker.models import Company, JobPosting, PostingEvent, Snapshot

T0 = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)


def _np(i):
    return NormalizedPosting(source_job_id=str(i), title=f"Role {i}", raw_location="Remote")


def _fetch(postings):
    return FetchResult(
        postings=list(postings), raw_body=[p.model_dump() for p in postings], http_status=200
    )


def _company(session):
    c = Company(name="Beta", ats_type="lever", ats_token="beta")
    session.add(c)
    session.flush()
    return c


def test_rerun_same_poll_is_noop(session):
    c = _company(session)
    postings = [_np(1), _np(2), _np(3)]

    apply_snapshot(session, c, _fetch(postings), T0)
    session.commit()
    p1 = session.scalar(select(func.count()).select_from(JobPosting))
    e1 = session.scalar(select(func.count()).select_from(PostingEvent))

    # Re-run the exact same poll (same fetched_at, same payload).
    apply_snapshot(session, c, _fetch(postings), T0)
    session.commit()
    p2 = session.scalar(select(func.count()).select_from(JobPosting))
    e2 = session.scalar(select(func.count()).select_from(PostingEvent))

    assert p1 == p2 == 3
    assert e1 == e2 == 3  # 3 'opened', no dupes


def test_rerun_with_new_timestamp_still_no_dupes(session):
    c = _company(session)
    postings = [_np(1), _np(2)]
    apply_snapshot(session, c, _fetch(postings), T0)
    session.commit()

    # Same set, later timestamp: set-membership makes it a no-op for lifecycle.
    apply_snapshot(session, c, _fetch(postings), T0 + timedelta(days=1))
    session.commit()

    assert session.scalar(select(func.count()).select_from(JobPosting)) == 2
    assert session.scalar(
        select(func.count()).select_from(PostingEvent).where(
            PostingEvent.event_type != "opened"
        )
    ) == 0
    # Two snapshots recorded (time series preserved).
    assert session.scalar(select(func.count()).select_from(Snapshot)) == 2


def test_natural_key_uniqueness_holds(session):
    c = _company(session)
    apply_snapshot(session, c, _fetch([_np(1)]), T0)
    session.commit()
    # Re-apply many times; unique(company, source_ats, source_job_id) prevents dupes.
    for k in range(5):
        apply_snapshot(session, c, _fetch([_np(1)]), T0 + timedelta(days=k + 1))
        session.commit()
    assert session.scalar(select(func.count()).select_from(JobPosting)) == 1
