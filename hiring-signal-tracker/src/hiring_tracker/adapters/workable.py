"""Workable adapter (secondary).

GET https://www.workable.com/api/accounts/{acct}?details=true
plus companion .../locations and .../departments.

Field mapping (Section 7):
  source_job_id = shortcode
  title         = title
  url           = url

NOTE: Verify against a live sample before trusting output (Section 0.2).
"""

from __future__ import annotations

import time

from .base import AdapterError, BaseHttpAdapter, FetchResult, NormalizedPosting, get_json

BASE = "https://www.workable.com/api/accounts"


def _remote_flag(job: dict) -> bool | None:
    if job.get("remote") is not None:
        return bool(job.get("remote"))
    tele = job.get("telecommuting")
    return bool(tele) if tele is not None else None


class WorkableAdapter(BaseHttpAdapter):
    ats_type = "workable"

    def fetch(self, token: str) -> FetchResult:
        started = time.monotonic()
        url = f"{BASE}/{token}?details=true"
        data, status = get_json(self.client, url)
        if not isinstance(data, dict):
            raise AdapterError(
                f"workable: unexpected schema for {token}", http_status=status
            )
        jobs = data.get("jobs") or data.get("results") or []
        postings: list[NormalizedPosting] = []
        for job in jobs:
            shortcode = job.get("shortcode") or job.get("id")
            if not shortcode:
                raise AdapterError(
                    "workable: posting with no shortcode", http_status=status
                )
            location_bits = [
                job.get("city"),
                job.get("region"),
                job.get("country"),
            ]
            raw_location = ", ".join(b for b in location_bits if b) or job.get("location")
            postings.append(
                NormalizedPosting(
                    source_job_id=str(shortcode),
                    title=job.get("title") or "",
                    raw_department=job.get("department"),
                    raw_location=raw_location,
                    remote_flag=_remote_flag(job),
                    employment_type=job.get("employment_type") or job.get("type"),
                    url=job.get("url") or job.get("application_url"),
                    apply_url=job.get("application_url") or job.get("shortlink"),
                    description_plain=self._plain(job.get("description")),
                )
            )
        duration_ms = int((time.monotonic() - started) * 1000)
        return FetchResult(
            postings=postings,
            raw_body=data,
            http_status=status,
            duration_ms=duration_ms,
        )
