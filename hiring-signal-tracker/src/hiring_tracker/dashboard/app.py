"""Read-only FastAPI dashboard (Phase 4, optional).

Exposes the panel data for humans without any write path:
  GET /                      -> HTML overview (watchlist + latest metrics)
  GET /api/companies         -> companies + open counts
  GET /api/metrics           -> latest metrics_daily rows
  GET /api/rollup            -> watchlist rollup
  GET /api/company/{id}      -> company detail: open postings + recent events

Run:  uvicorn hiring_tracker.dashboard.app:app --port 8080
(requires the `dashboard` extra: pip install -e '.[dashboard]')
"""

from __future__ import annotations

from datetime import date

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select

from ..db import session_scope
from ..metrics import watchlist_rollup
from ..models import Company, JobPosting, MetricsDaily, PostingEvent

app = FastAPI(title="Hiring-Signal Tracker", version="0.1.0")


@app.get("/api/companies")
def api_companies():
    with session_scope() as s:
        rows = s.execute(
            select(
                Company.id, Company.ticker, Company.name, Company.ats_type,
                func.count(JobPosting.id).filter(JobPosting.is_open.is_(True)),
            )
            .outerjoin(JobPosting, JobPosting.company_id == Company.id)
            .where(Company.is_active.is_(True))
            .group_by(Company.id)
            .order_by(Company.name)
        ).all()
    return [
        {"id": r[0], "ticker": r[1], "name": r[2], "ats_type": r[3], "open_count": r[4]}
        for r in rows
    ]


@app.get("/api/metrics")
def api_metrics(as_of: str | None = None):
    with session_scope() as s:
        target = date.fromisoformat(as_of) if as_of else s.scalar(
            select(func.max(MetricsDaily.as_of_date))
        )
        if target is None:
            return []
        rows = s.scalars(
            select(MetricsDaily).where(MetricsDaily.as_of_date == target)
        ).all()
        return [
            {
                "company_id": m.company_id,
                "as_of_date": m.as_of_date.isoformat(),
                "open_count": m.open_count,
                "opened_7d": m.opened_7d,
                "closed_7d": m.closed_7d,
                "net_7d": m.net_7d,
                "opened_30d": m.opened_30d,
                "closed_30d": m.closed_30d,
                "repost_rate_30d": float(m.repost_rate_30d) if m.repost_rate_30d is not None else None,
                "by_function": m.by_function,
                "by_seniority": m.by_seniority,
                "by_metro": m.by_metro,
            }
            for m in rows
        ]


@app.get("/api/rollup")
def api_rollup(as_of: str | None = None):
    with session_scope() as s:
        target = date.fromisoformat(as_of) if as_of else s.scalar(
            select(func.max(MetricsDaily.as_of_date))
        )
        if target is None:
            return {}
        return watchlist_rollup(s, target)


@app.get("/api/company/{company_id}")
def api_company(company_id: int):
    with session_scope() as s:
        c = s.get(Company, company_id)
        if c is None:
            raise HTTPException(404, "company not found")
        open_postings = s.scalars(
            select(JobPosting)
            .where(JobPosting.company_id == company_id, JobPosting.is_open.is_(True))
            .order_by(JobPosting.first_seen_at.desc())
        ).all()
        events = s.scalars(
            select(PostingEvent)
            .where(PostingEvent.company_id == company_id)
            .order_by(PostingEvent.event_at.desc())
            .limit(50)
        ).all()
        return {
            "company": {"id": c.id, "name": c.name, "ticker": c.ticker,
                        "ats_type": c.ats_type, "notes": c.notes},
            "open_postings": [
                {"id": p.id, "title": p.title, "location": p.raw_location,
                 "norm_function": p.norm_function, "norm_seniority": p.norm_seniority,
                 "first_seen_at": p.first_seen_at.isoformat(),
                 "reposted_from": p.reposted_from}
                for p in open_postings
            ],
            "recent_events": [
                {"posting_id": e.posting_id, "event_type": e.event_type,
                 "event_at": e.event_at.isoformat()}
                for e in events
            ],
        }


@app.get("/", response_class=HTMLResponse)
def index():
    companies = api_companies()
    rows = "".join(
        f"<tr><td>{c['name']}</td><td>{c['ticker'] or ''}</td>"
        f"<td>{c['ats_type']}</td><td style='text-align:right'>{c['open_count']}</td></tr>"
        for c in companies
    )
    total_open = sum(c["open_count"] for c in companies)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Hiring-Signal Tracker</title>
<style>
 body{{font-family:system-ui,Segoe UI,Arial;margin:2rem;color:#111}}
 h1{{margin-bottom:.2rem}} .sub{{color:#666;margin-top:0}}
 table{{border-collapse:collapse;width:100%;max-width:820px}}
 th,td{{border-bottom:1px solid #eee;padding:.5rem .75rem;text-align:left}}
 th{{background:#fafafa}} .note{{color:#888;font-size:.85rem;margin-top:1.5rem}}
</style></head><body>
<h1>Hiring-Signal Tracker</h1>
<p class="sub">Read-only panel. Postings are a demand signal, not hires.</p>
<p><b>{len(companies)}</b> active companies &middot; <b>{total_open}</b> open postings</p>
<table><thead><tr><th>Company</th><th>Ticker</th><th>ATS</th><th style="text-align:right">Open</th></tr></thead>
<tbody>{rows}</tbody></table>
<p class="note">APIs: <code>/api/companies</code>, <code>/api/metrics</code>,
<code>/api/rollup</code>, <code>/api/company/{{id}}</code></p>
</body></html>"""
