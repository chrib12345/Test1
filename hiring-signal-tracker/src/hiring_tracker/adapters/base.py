"""Adapter protocol + the common NormalizedPosting shape.

Every ATS adapter fetches the current open set for one company and normalizes
it to a list of ``NormalizedPosting``. It also returns the raw body and HTTP
status so the caller can persist a provenance snapshot.

Adapters must *fail loud*: a fetch error raises ``AdapterError`` and the caller
records a ``failed`` snapshot. Adapters never invent or drop postings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, field_validator
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..util import html_to_plain, sha256_text


class NormalizedPosting(BaseModel):
    """Common shape produced by every adapter (Section 7)."""

    source_job_id: str
    title: str
    raw_department: Optional[str] = None
    raw_team: Optional[str] = None
    raw_location: Optional[str] = None
    remote_flag: Optional[bool] = None
    employment_type: Optional[str] = None
    url: Optional[str] = None
    apply_url: Optional[str] = None
    description_plain: Optional[str] = None
    comp_summary: Optional[str] = None

    @field_validator("source_job_id", "title")
    @classmethod
    def _required_nonempty(cls, v: str) -> str:
        if v is None or str(v).strip() == "":
            raise ValueError("required field is empty")
        return str(v).strip()

    @property
    def description_hash(self) -> Optional[str]:
        return sha256_text(self.description_plain)


@dataclass
class FetchResult:
    """What an adapter returns for one company poll."""

    postings: list[NormalizedPosting]
    raw_body: Any  # JSON-serializable; retained in raw_payloads
    http_status: int
    duration_ms: int = 0
    notes: list[str] = field(default_factory=list)


class AdapterError(Exception):
    """Raised when a fetch or parse fails. Caller records a failed snapshot."""

    def __init__(self, message: str, http_status: int | None = None):
        super().__init__(message)
        self.http_status = http_status


@runtime_checkable
class Adapter(Protocol):
    ats_type: str

    def fetch(self, token: str) -> FetchResult:  # pragma: no cover - protocol
        ...


# Transient HTTP conditions worth retrying.
_RETRYABLE = (httpx.TransportError, httpx.HTTPStatusError)


def default_client(user_agent: str, timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": user_agent, "Accept": "application/json"},
        timeout=timeout,
        follow_redirects=True,
    )


@retry(
    reraise=True,
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=16),
    retry=retry_if_exception_type(_RETRYABLE),
)
def get_json(client: httpx.Client, url: str) -> tuple[Any, int]:
    """GET a URL and parse JSON, with retry/backoff on transient failures.

    Returns (parsed_json, http_status). Raises for non-2xx so the caller can
    record a failed snapshot with the status code.
    """
    resp = client.get(url)
    # 4xx (except 429) are not retried: they are structural, not transient.
    if resp.status_code == 429 or resp.status_code >= 500:
        resp.raise_for_status()
    if resp.status_code >= 400:
        raise AdapterError(
            f"GET {url} -> HTTP {resp.status_code}", http_status=resp.status_code
        )
    try:
        return resp.json(), resp.status_code
    except ValueError as e:
        raise AdapterError(f"GET {url}: invalid JSON: {e}", http_status=resp.status_code)


class BaseHttpAdapter:
    """Shared plumbing for the JSON ATS adapters."""

    ats_type: str = "base"

    def __init__(self, user_agent: str, client: httpx.Client | None = None):
        self._owns_client = client is None
        self.client = client or default_client(user_agent)

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # subclasses implement fetch()
    @staticmethod
    def _plain(raw_html: str | None) -> str | None:
        return html_to_plain(raw_html)
