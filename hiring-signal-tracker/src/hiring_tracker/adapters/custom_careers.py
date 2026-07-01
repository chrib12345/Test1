"""Custom-careers fallback adapter (guarded).

For companies on none of the supported ATS platforms. This is deliberately
conservative and isolated so that a site restructure fails *only that one
company*, never the run.

Guards:
  * Respects robots.txt for the configured User-Agent.
  * Sets a descriptive User-Agent and rate-limits politely.
  * Only extracts schema.org JobPosting objects from JSON-LD <script> blocks.
    We do NOT attempt bespoke HTML scraping per site -- that is too fragile to
    trust as an investment signal. If a site exposes no JSON-LD JobPosting, the
    adapter returns zero postings and flags the company as fragile.

Any company routed here should be marked in companies.notes so we know the data
is more fragile than an official ATS feed.
"""

from __future__ import annotations

import json
import re
import time
import urllib.robotparser as robotparser
from typing import Any
from urllib.parse import urlparse

import httpx

from ..util import html_to_plain
from .base import AdapterError, BaseHttpAdapter, FetchResult, NormalizedPosting

_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def _iter_jsonld_objects(payload: Any):
    """Yield dict objects from a parsed JSON-LD payload, flattening @graph."""
    if isinstance(payload, list):
        for item in payload:
            yield from _iter_jsonld_objects(item)
    elif isinstance(payload, dict):
        if "@graph" in payload and isinstance(payload["@graph"], list):
            for item in payload["@graph"]:
                yield from _iter_jsonld_objects(item)
        else:
            yield payload


def _is_job_posting(obj: dict) -> bool:
    t = obj.get("@type")
    if isinstance(t, list):
        return any(str(x).lower() == "jobposting" for x in t)
    return str(t).lower() == "jobposting"


class CustomCareersAdapter(BaseHttpAdapter):
    ats_type = "custom"

    def __init__(self, user_agent: str, client: httpx.Client | None = None,
                 request_delay_s: float = 1.0):
        super().__init__(user_agent, client)
        self.user_agent = user_agent
        self.request_delay_s = request_delay_s

    def _robots_allows(self, url: str) -> bool:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        rp = robotparser.RobotFileParser()
        try:
            resp = self.client.get(robots_url)
            if resp.status_code >= 400:
                # No robots.txt => allowed by convention.
                return True
            rp.parse(resp.text.splitlines())
        except Exception:
            # If robots is unreachable, be conservative and allow only the
            # explicit page the operator configured.
            return True
        return rp.can_fetch(self.user_agent, url)

    def fetch(self, token: str) -> FetchResult:
        """`token` is the careers_url for the custom fallback."""
        started = time.monotonic()
        url = token
        if not urlparse(url).scheme:
            raise AdapterError(f"custom: careers_url must be absolute, got {url!r}")

        if not self._robots_allows(url):
            raise AdapterError(f"custom: robots.txt disallows fetching {url}")

        time.sleep(self.request_delay_s)  # polite rate-limit
        try:
            resp = self.client.get(url)
        except httpx.HTTPError as e:
            raise AdapterError(f"custom: fetch failed for {url}: {e}")
        status = resp.status_code
        if status >= 400:
            raise AdapterError(f"custom: GET {url} -> HTTP {status}", http_status=status)

        postings: list[NormalizedPosting] = []
        raw_blocks: list[Any] = []
        for block in _JSONLD_RE.findall(resp.text):
            try:
                payload = json.loads(block)
            except ValueError:
                continue
            for obj in _iter_jsonld_objects(payload):
                if not _is_job_posting(obj):
                    continue
                raw_blocks.append(obj)
                sid = (
                    obj.get("identifier", {}).get("value")
                    if isinstance(obj.get("identifier"), dict)
                    else obj.get("identifier")
                ) or obj.get("url") or obj.get("title")
                loc = None
                jl = obj.get("jobLocation")
                if isinstance(jl, dict):
                    addr = jl.get("address", {})
                    if isinstance(addr, dict):
                        loc = ", ".join(
                            b
                            for b in [addr.get("addressLocality"), addr.get("addressRegion"),
                                      addr.get("addressCountry")]
                            if b
                        ) or None
                postings.append(
                    NormalizedPosting(
                        source_job_id=str(sid),
                        title=str(obj.get("title") or "").strip() or "(untitled)",
                        raw_location=loc,
                        employment_type=obj.get("employmentType"),
                        url=obj.get("url") or url,
                        description_plain=html_to_plain(obj.get("description")),
                    )
                )
        duration_ms = int((time.monotonic() - started) * 1000)
        notes = []
        if not postings:
            notes.append(
                "custom: no schema.org JobPosting JSON-LD found; data unavailable"
            )
        return FetchResult(
            postings=postings,
            raw_body={"source_url": url, "jobposting_jsonld": raw_blocks},
            http_status=status,
            duration_ms=duration_ms,
            notes=notes,
        )
