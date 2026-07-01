"""Normalization tests (Section 9): title_map caching + location heuristics."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from hiring_tracker.adapters.base import FetchResult, NormalizedPosting
from hiring_tracker.diff import apply_snapshot
from hiring_tracker.models import Company, JobPosting, TitleMap
from hiring_tracker.normalize import (
    classify_titles,
    enrich_postings,
    normalize_location,
)

T0 = datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc)


def _fetch(postings):
    return FetchResult(
        postings=list(postings), raw_body=[p.model_dump() for p in postings], http_status=200
    )


def _seed(session):
    c = Company(name="Delta", ats_type="lever", ats_token="delta")
    session.add(c)
    session.flush()
    apply_snapshot(session, c, _fetch([
        NormalizedPosting(source_job_id="1", title="Senior Backend Engineer", raw_location="New York, NY, United States"),
        NormalizedPosting(source_job_id="2", title="Account Executive", raw_location="London, UK"),
        NormalizedPosting(source_job_id="3", title="Senior Backend Engineer", raw_location="Remote"),
    ]), T0)
    session.commit()
    return c


def _fake_classifier(titles):
    table = {
        "Senior Backend Engineer": ("Engineering", "IC-Senior"),
        "Account Executive": ("Sales", "IC-Mid"),
    }
    return [
        {"title": t, "norm_function": table[t][0], "norm_seniority": table[t][1]}
        for t in titles if t in table
    ]


def test_classify_titles_dedupes_and_caches(session):
    _seed(session)
    n = classify_titles(session, _fake_classifier)
    session.commit()
    # Two *distinct* titles, though three postings.
    assert n == 2
    assert session.scalar(select(func.count()).select_from(TitleMap)) == 2

    # Re-running classifies nothing new (cache hit).
    assert classify_titles(session, _fake_classifier) == 0


def test_enrich_writes_back_norm_fields(session):
    _seed(session)
    classify_titles(session, _fake_classifier)
    session.commit()
    updated = enrich_postings(session)
    session.commit()
    assert updated == 3
    jp = session.scalar(select(JobPosting).where(JobPosting.source_job_id == "1"))
    assert jp.norm_function == "Engineering"
    assert jp.norm_seniority == "IC-Senior"
    assert jp.norm_country == "US"
    assert jp.normalized_at is not None


def test_normalize_location():
    assert normalize_location("New York, NY, United States") == ("US", "New York")
    assert normalize_location("London, UK")[0] == "GB"
    assert normalize_location("Remote") == (None, None)
    assert normalize_location(None) == (None, None)
