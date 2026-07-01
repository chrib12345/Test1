"""Weekly digest (Phase 3, Section 9).

Feeds recent metrics_daily deltas to Claude and produces a short narrative that
flags companies with notable acceleration, deceleration, function-mix shifts,
freezes, or a spiking repost rate.

Corroboration-oriented: the digest points to what to verify against filings. It
never asserts hires. A posting is a demand signal, not a headcount count.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .logging_util import get_logger
from .models import Company, MetricsDaily
from .util import utcnow

log = get_logger("digest")

# Generator: (context_dict) -> narrative string
DigestGenerator = Callable[[dict], str]

_SYSTEM_PROMPT = (
    "You write a weekly hiring-signal digest for an event-driven equity research "
    "team. Input is per-company job-posting metrics and week-over-week deltas. "
    "Rules:\n"
    "- A job posting is a demand signal, NOT a hire. Never assert or imply hires, "
    "headcount, or completed hiring. Say 'openings', 'postings', 'demand'.\n"
    "- Flag only NOTABLE moves: acceleration/deceleration in open roles, "
    "function-mix shifts (e.g. a sales surge or an engineering freeze), and "
    "spiking repost rates (a data-quality caveat).\n"
    "- Frame everything as something to corroborate against filings/transcripts.\n"
    "- Be concise: a short intro then 1-2 bullet lines per flagged company. If "
    "nothing is notable, say so plainly."
)


def build_digest_context(
    session: Session, as_of: Optional[date] = None, lookback_days: int = 7
) -> dict:
    """Assemble per-company current vs prior metrics for the digest."""
    as_of = as_of or utcnow().date()
    prior_date = as_of - timedelta(days=lookback_days)

    companies = {c.id: c for c in session.scalars(select(Company)).all()}
    current = {
        m.company_id: m
        for m in session.scalars(
            select(MetricsDaily).where(MetricsDaily.as_of_date == as_of)
        ).all()
    }
    # Prior: nearest metrics row on/around prior_date (<= prior_date, most recent).
    prior: dict[int, MetricsDaily] = {}
    for cid in current:
        p = session.scalars(
            select(MetricsDaily)
            .where(
                MetricsDaily.company_id == cid,
                MetricsDaily.as_of_date <= prior_date,
            )
            .order_by(MetricsDaily.as_of_date.desc())
            .limit(1)
        ).first()
        if p:
            prior[cid] = p

    items = []
    for cid, m in current.items():
        c = companies.get(cid)
        p = prior.get(cid)
        items.append(
            {
                "company": c.name if c else str(cid),
                "ticker": c.ticker if c else None,
                "open_count": m.open_count,
                "opened_7d": m.opened_7d,
                "closed_7d": m.closed_7d,
                "net_7d": m.net_7d,
                "opened_30d": m.opened_30d,
                "closed_30d": m.closed_30d,
                "repost_rate_30d": float(m.repost_rate_30d) if m.repost_rate_30d is not None else None,
                "by_function": m.by_function,
                "by_seniority": m.by_seniority,
                "delta_open_count": (m.open_count - p.open_count) if p else None,
                "delta_net_7d": (m.net_7d - p.net_7d) if p else None,
                "prior_by_function": p.by_function if p else None,
            }
        )
    return {"as_of_date": as_of.isoformat(), "lookback_days": lookback_days, "companies": items}


def _anthropic_generator(settings: Settings) -> DigestGenerator:
    import anthropic

    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required for the digest (set it in .env)")
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def generate(context: dict) -> str:
        user = (
            "Write the weekly digest for this data. Return markdown.\n\n"
            + json.dumps(context, ensure_ascii=False)
        )
        msg = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=2048,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")

    return generate


def generate_digest(
    settings: Optional[Settings] = None,
    generator: Optional[DigestGenerator] = None,
    as_of: Optional[date] = None,
) -> str:
    from .db import session_scope

    settings = settings or get_settings()
    gen = generator or _anthropic_generator(settings)
    with session_scope() as s:
        context = build_digest_context(s, as_of)
    if not context["companies"]:
        return f"# Hiring-Signal Digest — {context['as_of_date']}\n\nNo metrics available yet."
    narrative = gen(context)
    log.info("digest generated for %d companies", len(context["companies"]))
    return narrative
