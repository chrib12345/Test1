"""Lever adapter.

GET https://api.lever.co/v0/postings/{slug}?mode=json
Response: a JSON array of postings.

Field mapping (Section 7):
  source_job_id   = id
  title           = text
  raw_team        = categories.team
  raw_department  = categories.department
  raw_location    = categories.location
  employment_type = categories.commitment
  remote_flag     = derived from workplaceType
  url             = hostedUrl
  apply_url       = applyUrl
  description_plain = descriptionPlain

NOTE: Verify against a live sample before trusting output (Section 0.2).
"""

from __future__ import annotations

import time
from typing import Any

from .base import AdapterError, BaseHttpAdapter, FetchResult, NormalizedPosting, get_json

BASE = "https://api.lever.co/v0/postings"


def _remote_from_workplace(workplace: Any) -> bool | None:
    if workplace is None:
        return None
    return str(workplace).strip().lower() == "remote"


class LeverAdapter(BaseHttpAdapter):
    ats_type = "lever"

    def fetch(self, token: str) -> FetchResult:
        started = time.monotonic()
        url = f"{BASE}/{token}?mode=json"
        data, status = get_json(self.client, url)
        if not isinstance(data, list):
            raise AdapterError(
                f"lever: expected a JSON array for {token}, got {type(data).__name__}",
                http_status=status,
            )
        postings: list[NormalizedPosting] = []
        for job in data:
            jid = job.get("id")
            if not jid:
                raise AdapterError("lever: posting with no id", http_status=status)
            cats = job.get("categories") or {}
            postings.append(
                NormalizedPosting(
                    source_job_id=str(jid),
                    title=job.get("text") or "",
                    raw_team=cats.get("team"),
                    raw_department=cats.get("department"),
                    raw_location=cats.get("location"),
                    employment_type=cats.get("commitment"),
                    remote_flag=_remote_from_workplace(job.get("workplaceType")),
                    url=job.get("hostedUrl"),
                    apply_url=job.get("applyUrl"),
                    description_plain=job.get("descriptionPlain"),
                )
            )
        duration_ms = int((time.monotonic() - started) * 1000)
        return FetchResult(
            postings=postings,
            raw_body=data,
            http_status=status,
            duration_ms=duration_ms,
        )
