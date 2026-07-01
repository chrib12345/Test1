"""Normalization + enrichment (Phase 2).

Maps messy raw titles to a controlled function/seniority taxonomy via a batched
Claude call, cached in ``title_map`` (only novel titles cost a call). Also
normalizes raw_location into norm_country / norm_metro. Enriched fields and
``normalized_at`` are written back to job_postings; raw fields are left intact.
"""

from __future__ import annotations

import json
import re
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .logging_util import get_logger
from .models import JobPosting, TitleMap
from .util import utcnow

log = get_logger("normalize")

FUNCTIONS = [
    "Engineering", "Product", "Data/Analytics", "Design", "Sales", "Marketing",
    "Customer Success", "Operations", "Supply Chain", "Finance",
    "Legal/Compliance", "HR/Recruiting", "G&A", "Executive",
]
SENIORITIES = [
    "IC-Junior", "IC-Mid", "IC-Senior", "Manager", "Director", "VP", "C-Suite",
]

_SYSTEM_PROMPT = (
    "You classify job titles for an equity-research hiring-signal tracker. "
    "For each title, return the single best function and seniority from the "
    "controlled vocabularies. Do not invent categories. A posting is a signal "
    "of demand, never a hire.\n"
    f"norm_function must be one of: {', '.join(FUNCTIONS)}.\n"
    f"norm_seniority must be one of: {', '.join(SENIORITIES)}.\n"
    "Return ONLY a JSON array of objects with keys: title, norm_function, "
    "norm_seniority. No prose."
)

# TitleClassifier: (list[str]) -> list[dict(title, norm_function, norm_seniority)]
TitleClassifier = Callable[[list[str]], list[dict]]


def _anthropic_classifier(settings: Settings) -> TitleClassifier:
    """Build a classifier backed by the Anthropic API."""
    import anthropic  # imported lazily so the core build has no hard dep at runtime

    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is required for normalization (set it in .env)"
        )
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def classify(titles: list[str]) -> list[dict]:
        user = "Classify these titles:\n" + json.dumps(titles, ensure_ascii=False)
        msg = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=4096,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(
            b.text for b in msg.content if getattr(b, "type", None) == "text"
        )
        return _parse_classifier_json(text)

    return classify


def _parse_classifier_json(text: str) -> list[dict]:
    """Extract the JSON array from a model response, tolerating code fences."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        raise ValueError(f"classifier returned no JSON array: {text[:200]!r}")
    return json.loads(text[start : end + 1])


def _validate_mapping(row: dict) -> Optional[tuple[str, str, str]]:
    title = (row.get("title") or "").strip()
    fn = (row.get("norm_function") or "").strip()
    sen = (row.get("norm_seniority") or "").strip()
    if not title or fn not in FUNCTIONS or sen not in SENIORITIES:
        return None
    return title, fn, sen


def classify_titles(
    session: Session,
    classifier: TitleClassifier,
    batch_size: int = 60,
) -> int:
    """Classify all distinct titles not yet present in title_map. Returns count
    of new mappings persisted."""
    cached = set(session.scalars(select(TitleMap.raw_title)).all())
    distinct_titles = set(
        session.scalars(
            select(JobPosting.title).where(JobPosting.title.isnot(None)).distinct()
        ).all()
    )
    todo = sorted(t for t in distinct_titles if t and t not in cached)
    if not todo:
        log.info("normalize: no new titles to classify")
        return 0

    persisted = 0
    for i in range(0, len(todo), batch_size):
        batch = todo[i : i + batch_size]
        rows = classifier(batch)
        by_title = {}
        for row in rows:
            v = _validate_mapping(row)
            if v:
                by_title[v[0]] = (v[1], v[2])
        for title in batch:
            mapped = by_title.get(title)
            if mapped is None:
                log.warning("normalize: no valid mapping for title=%r; skipping", title)
                continue
            session.merge(
                TitleMap(
                    raw_title=title,
                    norm_function=mapped[0],
                    norm_seniority=mapped[1],
                    mapped_by="claude",
                )
            )
            persisted += 1
        session.flush()
        log.info("normalize: classified batch %d..%d", i, i + len(batch))
    return persisted


# --- Location normalization (heuristic; offline-capable) --------------------

_US_STATES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
    "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
    "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT",
    "VA","WA","WV","WI","WY","DC",
}
_COUNTRY_HINTS = {
    "united states": "US", "usa": "US", "u.s.": "US", "us": "US",
    "united kingdom": "GB", "uk": "GB", "england": "GB",
    "canada": "CA", "germany": "DE", "france": "FR", "ireland": "IE",
    "india": "IN", "australia": "AU", "singapore": "SG", "japan": "JP",
    "netherlands": "NL", "spain": "ES", "brazil": "BR", "remote": None,
}


def normalize_location(raw: str | None) -> tuple[Optional[str], Optional[str]]:
    """Best-effort (norm_country, norm_metro) from a raw location string."""
    if not raw:
        return None, None
    text = raw.strip()
    parts = [p.strip() for p in re.split(r"[,/|]", text) if p.strip()]
    country: Optional[str] = None
    metro: Optional[str] = None

    for p in parts:
        low = p.lower()
        if low in _COUNTRY_HINTS:
            country = _COUNTRY_HINTS[low]
        if p.upper() in _US_STATES:
            country = country or "US"

    # Metro = first non-country, non-"remote" token (typically the city).
    for p in parts:
        low = p.lower()
        if low in {"remote", "remote - us", "hybrid"} or low in _COUNTRY_HINTS:
            continue
        if p.upper() in _US_STATES:
            continue
        metro = p
        break

    if country is None and any(x.upper() in _US_STATES for x in parts):
        country = "US"
    return country, metro


def enrich_postings(session: Session, only_open: bool = False) -> int:
    """Write norm_function/seniority (from title_map) and norm_country/metro
    (from the location heuristic) back onto job_postings. Returns rows updated."""
    tmap = {
        t.raw_title: (t.norm_function, t.norm_seniority)
        for t in session.scalars(select(TitleMap)).all()
    }
    q = select(JobPosting)
    if only_open:
        q = q.where(JobPosting.is_open.is_(True))
    updated = 0
    now = utcnow()
    for jp in session.scalars(q).all():
        changed = False
        mapped = tmap.get(jp.title)
        if mapped and (jp.norm_function != mapped[0] or jp.norm_seniority != mapped[1]):
            jp.norm_function, jp.norm_seniority = mapped
            changed = True
        country, metro = normalize_location(jp.raw_location)
        if jp.norm_country != country or jp.norm_metro != metro:
            jp.norm_country, jp.norm_metro = country, metro
            changed = True
        if changed:
            jp.normalized_at = now
            updated += 1
    log.info("normalize: enriched %d postings", updated)
    return updated


def run_normalization(settings: Optional[Settings] = None,
                      classifier: Optional[TitleClassifier] = None) -> dict:
    """Full Phase 2 pass: classify novel titles, then enrich postings."""
    from .db import session_scope

    settings = settings or get_settings()
    clf = classifier or _anthropic_classifier(settings)
    with session_scope() as s:
        new_titles = classify_titles(s, clf)
    with session_scope() as s:
        enriched = enrich_postings(s)
    return {"new_titles": new_titles, "enriched": enriched}
