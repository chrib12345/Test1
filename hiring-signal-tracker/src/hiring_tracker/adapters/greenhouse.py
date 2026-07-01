"""Greenhouse adapter.

GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
Response: { "jobs": [ ... ] }

Field mapping (Section 7):
  source_job_id = str(id)
  title         = title
  raw_location  = location.name
  url           = absolute_url
  description   = content  (HTML-entity-encoded; decode + strip for the hash)
  department    = via optional GET .../departments

NOTE: Verify against a live sample before trusting output (Section 0.2).
Endpoints and schemas drift.
"""

from __future__ import annotations

import time

from .base import AdapterError, BaseHttpAdapter, FetchResult, NormalizedPosting, get_json

BASE = "https://boards-api.greenhouse.io/v1/boards"


class GreenhouseAdapter(BaseHttpAdapter):
    ats_type = "greenhouse"

    def _departments(self, token: str) -> dict[int, str]:
        """Best-effort department lookup keyed by job id. Non-fatal on failure."""
        try:
            data, _ = get_json(self.client, f"{BASE}/{token}/departments")
        except (AdapterError, Exception):
            return {}
        mapping: dict[int, str] = {}
        for dept in data.get("departments", []) if isinstance(data, dict) else []:
            name = dept.get("name")
            for job in dept.get("jobs", []) or []:
                jid = job.get("id")
                if jid is not None and name:
                    mapping[jid] = name
        return mapping

    def fetch(self, token: str) -> FetchResult:
        started = time.monotonic()
        url = f"{BASE}/{token}/jobs?content=true"
        data, status = get_json(self.client, url)
        if not isinstance(data, dict) or "jobs" not in data:
            raise AdapterError(
                f"greenhouse: unexpected schema for {token}: missing 'jobs'",
                http_status=status,
            )
        dept_by_id = self._departments(token)

        postings: list[NormalizedPosting] = []
        for job in data["jobs"]:
            jid = job.get("id")
            if jid is None:
                # Fail loud on a missing stable id rather than fabricating one.
                raise AdapterError("greenhouse: job with no id", http_status=status)
            location = (job.get("location") or {}).get("name")
            postings.append(
                NormalizedPosting(
                    source_job_id=str(jid),
                    title=job.get("title") or "",
                    raw_location=location,
                    raw_department=dept_by_id.get(jid),
                    url=job.get("absolute_url"),
                    apply_url=job.get("absolute_url"),
                    description_plain=self._plain(job.get("content")),
                )
            )
        duration_ms = int((time.monotonic() - started) * 1000)
        return FetchResult(
            postings=postings,
            raw_body=data,
            http_status=status,
            duration_ms=duration_ms,
        )
