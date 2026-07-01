"""Small pure helpers: hashing, HTML->plain, time."""

from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sha256_text(text: str | None) -> str | None:
    """Stable sha256 of a text blob; None passes through."""
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def html_to_plain(raw: str | None) -> str | None:
    """Decode HTML entities and strip tags to a normalized plain string.

    Used to derive `description_plain` and a stable `description_hash`. Whitespace
    is collapsed so cosmetic reflows do not spuriously change the hash.
    """
    if raw is None:
        return None
    # ATS payloads are frequently HTML-entity-encoded (e.g. Greenhouse content).
    decoded = html.unescape(raw)
    no_tags = _TAG_RE.sub(" ", decoded)
    collapsed = _WS_RE.sub(" ", no_tags).strip()
    return collapsed or None
