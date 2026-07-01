"""Ashby adapter.

GET https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true
Response: { "apiVersion", "jobs": [...], "compensation"? }

Field mapping (Section 7):
  title            = title
  raw_department   = department
  raw_team         = team
  raw_location     = location
  remote_flag      = isRemote (or workplaceType)
  employment_type  = employmentType
  url              = jobUrl
  apply_url        = applyUrl
  description_plain = descriptionPlain
  comp_summary     = compensation.scrapeableCompensationSalarySummary

Ashby's list has no separate id field: parse the UUID from the tail of jobUrl
(e.g. .../ashby/<uuid>) and use that as source_job_id.

Schema re-check index: https://developers.ashbyhq.com/llms.txt
NOTE: Verify against a live sample before trusting output (Section 0.2).
"""

from __future__ import annotations

import re
import time

from .base import AdapterError, BaseHttpAdapter, FetchResult, NormalizedPosting, get_json

BASE = "https://api.ashbyhq.com/posting-api/job-board"

# Trailing UUID (with or without hyphens) at the end of a jobUrl path.
_UUID_TAIL = re.compile(
    r"([0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12})"
    r"/?$"
)


def _job_id_from_url(job_url: str | None) -> str | None:
    if not job_url:
        return None
    m = _UUID_TAIL.search(job_url.rstrip("/"))
    return m.group(1) if m else None


def _remote_flag(job: dict) -> bool | None:
    if "isRemote" in job and job["isRemote"] is not None:
        return bool(job["isRemote"])
    wt = job.get("workplaceType")
    if wt is None:
        return None
    return str(wt).strip().lower() == "remote"


class AshbyAdapter(BaseHttpAdapter):
    ats_type = "ashby"

    def fetch(self, token: str) -> FetchResult:
        started = time.monotonic()
        url = f"{BASE}/{token}?includeCompensation=true"
        data, status = get_json(self.client, url)
        if not isinstance(data, dict) or "jobs" not in data:
            raise AdapterError(
                f"ashby: unexpected schema for {token}: missing 'jobs'",
                http_status=status,
            )
        postings: list[NormalizedPosting] = []
        for job in data["jobs"]:
            job_url = job.get("jobUrl")
            # Prefer an explicit id if a future schema adds one; else the UUID tail.
            sid = job.get("id") or _job_id_from_url(job_url)
            if not sid:
                raise AdapterError(
                    f"ashby: could not derive source_job_id from jobUrl={job_url!r}",
                    http_status=status,
                )
            comp = job.get("compensation") or {}
            postings.append(
                NormalizedPosting(
                    source_job_id=str(sid),
                    title=job.get("title") or "",
                    raw_department=job.get("department"),
                    raw_team=job.get("team"),
                    raw_location=job.get("location"),
                    remote_flag=_remote_flag(job),
                    employment_type=job.get("employmentType"),
                    url=job_url,
                    apply_url=job.get("applyUrl"),
                    description_plain=job.get("descriptionPlain"),
                    comp_summary=comp.get("scrapeableCompensationSalarySummary"),
                )
            )
        duration_ms = int((time.monotonic() - started) * 1000)
        return FetchResult(
            postings=postings,
            raw_body=data,
            http_status=status,
            duration_ms=duration_ms,
        )
