"""Adapter schema-contract tests (Section 12.2).

Offline contract tests mock each ATS endpoint and assert the adapter returns
NormalizedPosting objects with the required fields and correct types, and that
schema drift fails loudly. A `live` test (opt-in via HST_LIVE=1) hits the real
endpoints when the network allows it.
"""

from __future__ import annotations

import os

import httpx
import pytest
import respx

from hiring_tracker.adapters import get_adapter
from hiring_tracker.adapters.ashby import AshbyAdapter
from hiring_tracker.adapters.base import AdapterError, NormalizedPosting
from hiring_tracker.adapters.greenhouse import GreenhouseAdapter
from hiring_tracker.adapters.lever import LeverAdapter

UA = "test-agent/1.0"

GREENHOUSE_BODY = {
    "jobs": [
        {
            "id": 12345,
            "title": "Staff Software Engineer",
            "location": {"name": "New York, NY"},
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/12345",
            "content": "&lt;p&gt;Build &lt;b&gt;things&lt;/b&gt;.&lt;/p&gt;",
        }
    ]
}
GREENHOUSE_DEPTS = {
    "departments": [{"name": "Engineering", "jobs": [{"id": 12345}]}]
}

LEVER_BODY = [
    {
        "id": "abc-123",
        "text": "Account Executive",
        "categories": {
            "team": "Sales",
            "department": "Revenue",
            "location": "Remote - US",
            "commitment": "Full-time",
        },
        "workplaceType": "remote",
        "hostedUrl": "https://jobs.lever.co/acme/abc-123",
        "applyUrl": "https://jobs.lever.co/acme/abc-123/apply",
        "descriptionPlain": "Sell things responsibly.",
    }
]

ASHBY_BODY = {
    "apiVersion": "1",
    "jobs": [
        {
            "title": "Product Designer",
            "department": "Design",
            "team": "Core",
            "location": "London, UK",
            "isRemote": False,
            "employmentType": "FullTime",
            "jobUrl": "https://jobs.ashbyhq.com/acme/2f1c9e6a-1111-2222-3333-444455556666",
            "applyUrl": "https://jobs.ashbyhq.com/acme/2f1c9e6a-1111-2222-3333-444455556666/application",
            "descriptionPlain": "Design delightful things.",
            "compensation": {"scrapeableCompensationSalarySummary": "$120k-$160k"},
        }
    ],
}

REQUIRED_FIELDS = ("source_job_id", "title")


def _assert_contract(postings: list[NormalizedPosting]):
    assert postings, "adapter returned no postings"
    for p in postings:
        assert isinstance(p, NormalizedPosting)
        for f in REQUIRED_FIELDS:
            assert getattr(p, f), f"missing required field {f}"
            assert isinstance(getattr(p, f), str)
        assert p.remote_flag is None or isinstance(p.remote_flag, bool)
        assert p.description_hash is None or isinstance(p.description_hash, str)


@respx.mock
def test_greenhouse_contract():
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(
        return_value=httpx.Response(200, json=GREENHOUSE_BODY)
    )
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/departments").mock(
        return_value=httpx.Response(200, json=GREENHOUSE_DEPTS)
    )
    with GreenhouseAdapter(UA) as a:
        res = a.fetch("acme")
    _assert_contract(res.postings)
    p = res.postings[0]
    assert p.source_job_id == "12345"
    assert p.raw_location == "New York, NY"
    assert p.raw_department == "Engineering"
    # HTML-entity-encoded content is decoded + stripped for the hash.
    assert "Build things" in (p.description_plain or "")


@respx.mock
def test_lever_contract():
    respx.get("https://api.lever.co/v0/postings/acme").mock(
        return_value=httpx.Response(200, json=LEVER_BODY)
    )
    with LeverAdapter(UA) as a:
        res = a.fetch("acme")
    _assert_contract(res.postings)
    p = res.postings[0]
    assert p.source_job_id == "abc-123"
    assert p.raw_team == "Sales"
    assert p.remote_flag is True
    assert p.employment_type == "Full-time"


@respx.mock
def test_ashby_contract_parses_uuid_from_url():
    respx.get("https://api.ashbyhq.com/posting-api/job-board/acme").mock(
        return_value=httpx.Response(200, json=ASHBY_BODY)
    )
    with AshbyAdapter(UA) as a:
        res = a.fetch("acme")
    _assert_contract(res.postings)
    p = res.postings[0]
    assert p.source_job_id == "2f1c9e6a-1111-2222-3333-444455556666"
    assert p.comp_summary == "$120k-$160k"
    assert p.remote_flag is False


@respx.mock
def test_schema_drift_fails_loud():
    # 'jobs' key renamed -> adapter must raise, not silently return empty.
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(
        return_value=httpx.Response(200, json={"postings": []})
    )
    with GreenhouseAdapter(UA) as a:
        with pytest.raises(AdapterError):
            a.fetch("acme")


@respx.mock
def test_http_error_raises_adapter_error():
    respx.get("https://api.lever.co/v0/postings/acme").mock(
        return_value=httpx.Response(404)
    )
    with LeverAdapter(UA) as a:
        with pytest.raises(AdapterError):
            a.fetch("acme")


# --- opt-in live checks -----------------------------------------------------

LIVE = os.environ.get("HST_LIVE", "0").strip().lower() in {"1", "true", "yes"}


@pytest.mark.live
@pytest.mark.skipif(not LIVE, reason="set HST_LIVE=1 to hit real ATS endpoints")
@pytest.mark.parametrize(
    "ats,token",
    [("greenhouse", "stripe"), ("lever", "netflix"), ("ashby", "notion")],
)
def test_live_adapter_contract(ats, token):
    adapter = get_adapter(ats)
    try:
        res = adapter.fetch(token)
    except AdapterError as e:
        pytest.skip(f"live endpoint unavailable: {e}")
    finally:
        getattr(adapter, "close", lambda: None)()
    _assert_contract(res.postings)
