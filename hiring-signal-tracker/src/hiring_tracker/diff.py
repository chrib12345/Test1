"""Snapshot diff engine (Section 8) — the heart of the tracker.

Per company, per poll, given the current open set (or a failed fetch), this:
  1. writes a snapshot row (ok / failed / empty) with provenance;
  2. diffs the incoming id set against the last known open set;
  3. drives the posting lifecycle: opened / surviving(+edited) / closed;
  4. detects reappearances (same id reopens) and reposts (a new id that
     matches a recently-closed posting's identity tuple).

The whole operation is keyed on (company_id, source_ats, source_job_id) and
driven by set membership, so re-running the same poll is a no-op. It is also
deterministic and replayable: ``first_seen_at`` is written once on insert and
never moved afterward.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .adapters.base import FetchResult, NormalizedPosting
from .logging_util import get_logger
from .models import Company, JobPosting, PostingEvent, RawPayload, Snapshot
from .util import sha256_bytes

log = get_logger("diff")

# Fields that constitute a repost identity match on a *different* closed posting.
# (title, raw_department, raw_location, description_hash)


def canonical_payload_hash(body) -> str:
    """Stable sha256 over a JSON-serializable payload (order-independent)."""
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return sha256_bytes(blob.encode("utf-8"))


@dataclass
class DiffOutcome:
    snapshot_id: Optional[int] = None
    status: str = "ok"
    posting_count: int = 0
    opened: int = 0
    closed: int = 0
    reappeared: int = 0
    reposted: int = 0
    edited: int = 0
    skipped_unchanged: bool = False

    def merge_into_summary(self, summary) -> None:
        summary.postings_opened += self.opened
        summary.postings_closed += self.closed
        summary.postings_reappeared += self.reappeared
        summary.postings_reposted += self.reposted
        summary.postings_edited += self.edited
        if self.skipped_unchanged:
            summary.unchanged_skips += 1


def _emit(session: Session, posting: JobPosting, company_id: int, event_type: str,
          snapshot_id: int, event_at: datetime) -> None:
    session.add(
        PostingEvent(
            posting_id=posting.id,
            company_id=company_id,
            event_type=event_type,
            event_at=event_at,
            snapshot_id=snapshot_id,
        )
    )


def _last_successful_snapshot(session: Session, company_id: int) -> Optional[Snapshot]:
    return session.scalars(
        select(Snapshot)
        .where(Snapshot.company_id == company_id, Snapshot.status.in_(("ok", "empty")))
        .order_by(Snapshot.fetched_at.desc(), Snapshot.id.desc())
        .limit(1)
    ).first()


def record_failed_snapshot(
    session: Session,
    company: Company,
    error_detail: str,
    fetched_at: datetime,
    http_status: int | None = None,
    duration_ms: int | None = None,
) -> DiffOutcome:
    """Write a failed snapshot. Never deletes or closes postings (Section 8.1)."""
    snap = Snapshot(
        company_id=company.id,
        fetched_at=fetched_at,
        ats_type=company.ats_type,
        status="failed",
        http_status=http_status,
        posting_count=None,
        payload_hash=None,
        duration_ms=duration_ms,
        error_detail=error_detail[:4000] if error_detail else None,
    )
    session.add(snap)
    session.flush()
    log.warning("company=%s fetch FAILED: %s", company.name, error_detail)
    return DiffOutcome(snapshot_id=snap.id, status="failed")


def apply_snapshot(
    session: Session,
    company: Company,
    fetch: FetchResult,
    fetched_at: datetime,
    repost_window_days: int = 45,
) -> DiffOutcome:
    """Apply a successful fetch: write snapshot, run the lifecycle diff."""
    incoming: list[NormalizedPosting] = fetch.postings
    posting_count = len(incoming)
    status = "empty" if posting_count == 0 else "ok"
    payload_hash = canonical_payload_hash(fetch.raw_body)

    snap = Snapshot(
        company_id=company.id,
        fetched_at=fetched_at,
        ats_type=company.ats_type,
        status=status,
        http_status=fetch.http_status,
        posting_count=posting_count,
        payload_hash=payload_hash,
        duration_ms=fetch.duration_ms,
        error_detail=None,
    )
    session.add(snap)
    session.flush()  # need snap.id for provenance + events

    # Provenance: every ok/empty snapshot retains its raw payload for replay.
    session.add(RawPayload(snapshot_id=snap.id, body=_jsonable(fetch.raw_body)))

    outcome = DiffOutcome(
        snapshot_id=snap.id, status=status, posting_count=posting_count
    )

    # Skip-if-unchanged: identical payload to the last successful snapshot still
    # writes the snapshot row (time series) but skips the diff work (Section 8.2).
    prev = _last_successful_snapshot_before(session, company.id, snap.id)
    if prev is not None and prev.payload_hash == payload_hash:
        outcome.skipped_unchanged = True
        # Still advance last_seen_at on the open set so staleness stays accurate.
        _touch_open_last_seen(session, company, fetched_at)
        log.info("company=%s payload unchanged; diff skipped", company.name)
        return outcome

    _run_diff(session, company, incoming, snap.id, fetched_at, repost_window_days, outcome)
    log.info(
        "company=%s ok posts=%d opened=%d closed=%d reappeared=%d reposted=%d edited=%d",
        company.name, posting_count, outcome.opened, outcome.closed,
        outcome.reappeared, outcome.reposted, outcome.edited,
    )
    return outcome


def _last_successful_snapshot_before(
    session: Session, company_id: int, before_snapshot_id: int
) -> Optional[Snapshot]:
    return session.scalars(
        select(Snapshot)
        .where(
            Snapshot.company_id == company_id,
            Snapshot.status.in_(("ok", "empty")),
            Snapshot.id < before_snapshot_id,
        )
        .order_by(Snapshot.id.desc())
        .limit(1)
    ).first()


def _touch_open_last_seen(session: Session, company: Company, fetched_at: datetime) -> None:
    open_rows = session.scalars(
        select(JobPosting).where(
            JobPosting.company_id == company.id, JobPosting.is_open.is_(True)
        )
    ).all()
    for jp in open_rows:
        if jp.last_seen_at < fetched_at:
            jp.last_seen_at = fetched_at


def _run_diff(
    session: Session,
    company: Company,
    incoming: list[NormalizedPosting],
    snapshot_id: int,
    fetched_at: datetime,
    repost_window_days: int,
    outcome: DiffOutcome,
) -> None:
    ats = company.ats_type
    cid = company.id

    # Deduplicate incoming by source_job_id (defensive: some feeds repeat).
    incoming_by_id: dict[str, NormalizedPosting] = {}
    for p in incoming:
        incoming_by_id[p.source_job_id] = p

    # All existing rows for this company on this ATS, keyed by source_job_id.
    existing_rows = session.scalars(
        select(JobPosting).where(
            JobPosting.company_id == cid, JobPosting.source_ats == ats
        )
    ).all()
    existing_by_id: dict[str, JobPosting] = {r.source_job_id: r for r in existing_rows}
    open_ids = {r.source_job_id for r in existing_rows if r.is_open}

    incoming_ids = set(incoming_by_id)
    surviving_ids = incoming_ids & open_ids
    vanished_ids = open_ids - incoming_ids
    fresh_ids = incoming_ids - open_ids  # new rows OR reappearing closed rows

    window_start = fetched_at - timedelta(days=repost_window_days)

    # 5. Surviving: bump last_seen; emit 'edited' on a material change.
    for sid in surviving_ids:
        jp = existing_by_id[sid]
        np = incoming_by_id[sid]
        jp.last_seen_at = fetched_at
        new_hash = np.description_hash
        changed = (
            (np.title or "") != (jp.title or "")
            or (np.raw_location or None) != (jp.raw_location or None)
            or new_hash != jp.description_hash
        )
        if changed:
            jp.title = np.title
            jp.raw_location = np.raw_location
            jp.description_hash = new_hash
            # Refresh other mutable descriptive fields too.
            jp.raw_department = np.raw_department
            jp.raw_team = np.raw_team
            jp.remote_flag = np.remote_flag
            jp.employment_type = np.employment_type
            jp.comp_summary = np.comp_summary
            _emit(session, jp, cid, "edited", snapshot_id, fetched_at)
            outcome.edited += 1

    # 6. Vanished: close.
    for sid in vanished_ids:
        jp = existing_by_id[sid]
        jp.is_open = False
        jp.closed_at = fetched_at
        _emit(session, jp, cid, "closed", snapshot_id, fetched_at)
        outcome.closed += 1

    # 4 + 7. Fresh ids: reappear (closed row with same id) or open (new row).
    for sid in fresh_ids:
        np = incoming_by_id[sid]
        existing = existing_by_id.get(sid)
        if existing is not None:
            # Same id was previously closed -> reappear (Section 8.7).
            existing.is_open = True
            existing.closed_at = None
            existing.last_seen_at = fetched_at
            # first_seen_at intentionally left untouched (history is the product).
            _emit(session, existing, cid, "reappeared", snapshot_id, fetched_at)
            outcome.reappeared += 1
            continue

        jp = JobPosting(
            company_id=cid,
            source_ats=ats,
            source_job_id=np.source_job_id,
            title=np.title,
            raw_department=np.raw_department,
            raw_team=np.raw_team,
            raw_location=np.raw_location,
            remote_flag=np.remote_flag,
            employment_type=np.employment_type,
            url=np.url,
            apply_url=np.apply_url,
            description_hash=np.description_hash,
            comp_summary=np.comp_summary,
            first_seen_at=fetched_at,
            last_seen_at=fetched_at,
            is_open=True,
        )
        session.add(jp)
        session.flush()  # need jp.id for events + repost linkage
        _emit(session, jp, cid, "opened", snapshot_id, fetched_at)
        outcome.opened += 1

        # Repost detection: a genuinely new posting that matches a recently
        # closed *different* posting on the identity tuple.
        match = _find_repost_source(
            existing_rows, np, window_start, exclude_id=jp.id
        )
        if match is not None:
            jp.reposted_from = match.id
            _emit(session, jp, cid, "reposted", snapshot_id, fetched_at)
            outcome.reposted += 1


def _find_repost_source(
    existing_rows: list[JobPosting],
    np: NormalizedPosting,
    window_start: datetime,
    exclude_id: int,
) -> Optional[JobPosting]:
    """Most-recently-closed posting matching the identity tuple within window."""
    target = (
        np.title or "",
        np.raw_department or None,
        np.raw_location or None,
        np.description_hash,
    )
    candidates = [
        r
        for r in existing_rows
        if r.id != exclude_id
        and not r.is_open
        and r.closed_at is not None
        and r.closed_at >= window_start
        and (
            (r.title or "", r.raw_department or None, r.raw_location or None,
             r.description_hash)
            == target
        )
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda r: r.closed_at)


def _jsonable(body):
    """Ensure the raw body round-trips through JSON for jsonb storage."""
    return json.loads(json.dumps(body, default=str))
