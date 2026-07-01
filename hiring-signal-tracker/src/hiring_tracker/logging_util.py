"""Structured logging + a run-summary accumulator.

Every full watchlist run prints a summary: companies polled, ok/failed/empty
counts, total postings opened and closed, and any company flagged stale.
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

_CONFIGURED = False


def configure_logging(level: int = logging.INFO) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s :: %(message)s")
    )
    root = logging.getLogger("hiring_tracker")
    root.setLevel(level)
    root.handlers[:] = [handler]
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(f"hiring_tracker.{name}")


@dataclass
class RunSummary:
    """Accumulates counts across a full watchlist poll."""

    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    companies_polled: int = 0
    ok: int = 0
    failed: int = 0
    empty: int = 0
    postings_opened: int = 0
    postings_closed: int = 0
    postings_reappeared: int = 0
    postings_reposted: int = 0
    postings_edited: int = 0
    unchanged_skips: int = 0
    stale_companies: list[str] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)

    def record_failure(self, company: str, detail: str) -> None:
        self.failed += 1
        self.failures.append({"company": company, "detail": detail})

    def to_dict(self) -> dict:
        return asdict(self)

    def render(self) -> str:
        lines = [
            "=" * 60,
            "RUN SUMMARY",
            "=" * 60,
            f"  companies polled : {self.companies_polled}",
            f"  ok / failed / empty : {self.ok} / {self.failed} / {self.empty}",
            f"  unchanged (skipped diff) : {self.unchanged_skips}",
            f"  postings opened : {self.postings_opened}",
            f"  postings closed : {self.postings_closed}",
            f"  reappeared / reposted / edited : "
            f"{self.postings_reappeared} / {self.postings_reposted} / {self.postings_edited}",
            f"  stale companies : {', '.join(self.stale_companies) or '(none)'}",
        ]
        if self.failures:
            lines.append("  failures:")
            for f in self.failures:
                lines.append(f"    - {f['company']}: {f['detail']}")
        lines.append("=" * 60)
        return "\n".join(lines)

    def as_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)
