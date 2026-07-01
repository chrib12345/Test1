"""Diff engine tests over synthetic snapshot sequences (Section 8).

Covers: open, survive, edit, close, reappear, repost, and a failed fetch in the
middle of a sequence.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from hiring_tracker.adapters.base import FetchResult, NormalizedPosting
from hiring_tracker.diff import apply_snapshot, record_failed_snapshot
from hiring_tracker.models import Company, JobPosting, PostingEvent

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _np(job_id, title="Engineer", dept="Engineering", loc="NYC", desc="jd"):
    return NormalizedPosting(
        source_job_id=str(job_id),
        title=title,
        raw_department=dept,
        raw_location=loc,
        description_plain=desc,
    )


def _fetch(postings):
    # raw_body is a stable serialization of the set so identical sets hash equal.
    body = [p.model_dump() for p in postings]
    return FetchResult(postings=list(postings), raw_body=body, http_status=200)


def _company(session, ats="greenhouse"):
    c = Company(name="Acme", ats_type=ats, ats_token="acme")
    session.add(c)
    session.flush()
    return c


def _poll(session, company, postings, at):
    out = apply_snapshot(session, company, _fetch(postings), at, repost_window_days=45)
    session.commit()
    return out


def _events(session, company_id, etype=None):
    q = select(func.count()).select_from(PostingEvent).where(
        PostingEvent.company_id == company_id
    )
    if etype:
        q = q.where(PostingEvent.event_type == etype)
    return session.scalar(q)


def test_open(session):
    c = _company(session)
    out = _poll(session, c, [_np(1), _np(2)], T0)
    assert out.opened == 2
    assert _events(session, c.id, "opened") == 2
    assert session.scalar(select(func.count()).select_from(JobPosting)) == 2


def test_survive_bumps_last_seen(session):
    c = _company(session)
    _poll(session, c, [_np(1), _np(2)], T0)
    out = _poll(session, c, [_np(1), _np(2), _np(3)], T0 + timedelta(days=1))
    assert out.opened == 1  # only job 3
    jp1 = session.scalar(select(JobPosting).where(JobPosting.source_job_id == "1"))
    assert jp1.last_seen_at == T0 + timedelta(days=1)
    assert jp1.first_seen_at == T0  # unchanged


def test_edit(session):
    c = _company(session)
    _poll(session, c, [_np(1, title="Engineer")], T0)
    out = _poll(session, c, [_np(1, title="Senior Engineer")], T0 + timedelta(days=1))
    assert out.edited == 1
    jp = session.scalar(select(JobPosting).where(JobPosting.source_job_id == "1"))
    assert jp.title == "Senior Engineer"


def test_close(session):
    c = _company(session)
    _poll(session, c, [_np(1), _np(2)], T0)
    out = _poll(session, c, [_np(1)], T0 + timedelta(days=1))
    assert out.closed == 1
    jp2 = session.scalar(select(JobPosting).where(JobPosting.source_job_id == "2"))
    assert jp2.is_open is False
    assert jp2.closed_at == T0 + timedelta(days=1)


def test_reappear_preserves_first_seen(session):
    c = _company(session)
    _poll(session, c, [_np(1)], T0)
    _poll(session, c, [], T0 + timedelta(days=1))  # empty -> close job 1
    out = _poll(session, c, [_np(1)], T0 + timedelta(days=2))
    assert out.reappeared == 1
    assert out.opened == 0
    jp = session.scalar(select(JobPosting).where(JobPosting.source_job_id == "1"))
    assert jp.is_open is True
    assert jp.closed_at is None
    assert jp.first_seen_at == T0  # history preserved


def test_repost_links_source(session):
    c = _company(session)
    _poll(session, c, [_np(1, title="Growth PM", dept="Product", loc="SF", desc="same")], T0)
    _poll(session, c, [], T0 + timedelta(days=1))  # close job 1
    out = _poll(
        session, c,
        [_np(2, title="Growth PM", dept="Product", loc="SF", desc="same")],
        T0 + timedelta(days=2),
    )
    assert out.reposted == 1
    assert out.opened == 1
    new = session.scalar(select(JobPosting).where(JobPosting.source_job_id == "2"))
    old = session.scalar(select(JobPosting).where(JobPosting.source_job_id == "1"))
    assert new.reposted_from == old.id
    assert _events(session, c.id, "reposted") == 1


def test_failed_fetch_in_middle_does_not_close(session):
    c = _company(session)
    _poll(session, c, [_np(1), _np(2)], T0)
    # Middle poll fails: must not close or drop anything.
    record_failed_snapshot(session, c, "boom", T0 + timedelta(days=1), http_status=500)
    session.commit()
    open_count = session.scalar(
        select(func.count()).select_from(JobPosting).where(JobPosting.is_open.is_(True))
    )
    assert open_count == 2
    # Recovery poll closes the genuinely-vanished job.
    out = _poll(session, c, [_np(1)], T0 + timedelta(days=2))
    assert out.closed == 1


def test_unchanged_payload_skips_diff(session):
    c = _company(session)
    _poll(session, c, [_np(1), _np(2)], T0)
    out = _poll(session, c, [_np(1), _np(2)], T0 + timedelta(days=1))
    assert out.skipped_unchanged is True
    assert out.opened == 0 and out.closed == 0
    # last_seen still advances so staleness stays accurate.
    jp = session.scalar(select(JobPosting).where(JobPosting.source_job_id == "1"))
    assert jp.last_seen_at == T0 + timedelta(days=1)
