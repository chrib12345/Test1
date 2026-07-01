# Hiring-Signal Tracker

A headless alternative-data service that tracks job-posting activity across a
watchlist of 25–100 public companies. On a schedule it snapshots each company's
full set of open requisitions, diffs consecutive snapshots into a hiring
lifecycle, and exposes momentum/composition signals for event-driven investment
research (operational momentum, cost-cut signals, post-merger integration
hiring, sector rotation).

> **The signal, precisely.** The unit of raw data is a *job posting*, never a
> hire. A posting that disappears means filled **or** pulled **or** unpublished —
> the feed cannot tell you which, so it is recorded as `closed`, never as a hire.
> Everything downstream is a **momentum and mix** signal to corroborate against
> filings. The tool never claims a posting equals a hire.

## Architecture (four layers)

1. **Ingestion** — per-ATS adapters (Greenhouse, Lever, Ashby, plus Workable and
   a guarded generic-careers fallback). Each fetches the current open set for one
   company and normalizes it to a common `NormalizedPosting` shape.
2. **Storage** — Postgres. Append-only snapshots, slowly-changing postings with an
   open/closed lifecycle, lifecycle events, raw payloads, a normalization cache,
   and materialized daily metrics. Optional `pgvector` for dedup / semantic search.
3. **Normalization** — a batched Claude call maps messy raw titles to a controlled
   function/seniority taxonomy and normalizes location. Cached by raw title so
   only novel titles cost a call.
4. **Analytics** — materialized per-company and watchlist metrics plus a
   Claude-authored weekly digest that flags notable deltas.

## Repo layout

```
hiring-signal-tracker/
  CLAUDE.md                 # the build spec
  pyproject.toml
  .env.example
  config/watchlist.yaml     # ticker, name, ats_type, ats_token, careers_url
  migrations/               # Alembic + canonical DDL (.sql)
  src/hiring_tracker/
    config.py db.py models.py util.py logging_util.py
    adapters/               # greenhouse, lever, ashby, workable, custom_careers
    ingest.py diff.py normalize.py metrics.py digest.py
    vector.py schedule.py preflight.py cli.py
    dashboard/app.py        # read-only FastAPI
  tests/                    # diff, idempotency, adapter-contract, metrics, normalize
```

## Setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev,dashboard,vector]'
cp .env.example .env        # then edit DATABASE_URL (+ ANTHROPIC_API_KEY for phases 2/3)
```

Stack: Python 3.11+, `httpx` (+`tenacity`), SQLAlchemy 2 + Alembic, `psycopg` v3,
`pydantic`, `PyYAML`. All timestamps are stored `timestamptz` in UTC.

### Database

Point `DATABASE_URL` at your Postgres (the same instance as the rest of the
pipeline). Apply the schema with Alembic:

```bash
alembic upgrade head          # 0001 core schema, 0002 optional pgvector
```

or apply `migrations/ddl_0001_initial.sql` (and `ddl_0002_pgvector.sql`) directly.

**Managed Postgres (Supabase).** The schema has been provisioned on project
`yecrwnvjnvrkputpzqyb` (all 8 tables). Row-Level Security is enabled with no
policies, so the public REST/anon API cannot read the data — access is via the
direct `DATABASE_URL` connection only, which is the intended posture for this
backend-only service (it stores public posting data, no candidate PII). Get the
connection string from the Supabase dashboard → Project Settings → Database.

## Usage

```bash
hiring-tracker sync-watchlist        # upsert config/watchlist.yaml -> companies
hiring-tracker verify-adapter Stripe # pull a live sample; print raw + parsed (do this first!)
hiring-tracker poll --then-metrics   # one full watchlist poll, then materialize metrics
hiring-tracker normalize             # Phase 2: classify titles + enrich (needs ANTHROPIC_API_KEY)
hiring-tracker metrics               # Phase 3: materialize metrics_daily + rollup
hiring-tracker digest                # Phase 3: weekly Claude narrative
hiring-tracker embed                 # Phase 4: pgvector embeddings (guarded)
hiring-tracker preflight             # Section 12 integrity checker (exit 0 = clean)
uvicorn hiring_tracker.dashboard.app:app --port 8080   # read-only dashboard
```

### ⚠️ Verify adapters against a live sample before trusting data

ATS endpoints and response schemas drift. Section 0.2 of the spec is
non-negotiable: pull one real company per platform, inspect the raw JSON, and
confirm the field mapping still holds **before** relying on the output.

```bash
hiring-tracker verify-adapter --ats greenhouse --token stripe
hiring-tracker verify-adapter --ats lever      --token netflix
hiring-tracker verify-adapter --ats ashby      --token notion
```

> **Build-environment note.** This project was built in a sandbox whose egress
> policy blocked outbound HTTPS to the ATS hosts (`boards-api.greenhouse.io`,
> `api.lever.co`, `api.ashbyhq.com`, `www.workable.com`). The adapters are coded
> strictly to the documented Section 7 schemas and covered by offline
> contract tests, but the **live** verification step still needs to be run in an
> environment with network access to those hosts. The opt-in live tests
> (`HST_LIVE=1 pytest -m live`) and `verify-adapter` are provided for exactly
> that. The `watchlist.yaml` tokens are illustrative and must be confirmed.

## Scheduling

Poll cadence is **daily** by default (weekly is fine and cheaper). Wire
`hiring_tracker.schedule:scheduled_poll` into the existing Composio
scheduled-routine layer. OS-scheduler fallbacks (cron / Windows Task Scheduler)
are documented in `src/hiring_tracker/schedule.py`.

## Testing

Tests run against a real Postgres for schema fidelity. Point
`HST_TEST_DATABASE_URL` at a throwaway database (default:
`postgresql+psycopg://postgres@127.0.0.1:5433/hiring_tracker_test`):

```bash
pytest -q                 # 22 offline tests (diff, idempotency, contracts, metrics, normalize)
HST_LIVE=1 pytest -m live  # opt-in: hits real ATS endpoints
```

## Guardrails (Section 13)

- No LinkedIn scraping, no login-walled sources. Public ATS APIs and, where
  necessary, robots-respecting careers pages only.
- No paid data source in the core build.
- The tool never claims a posting equals a hire — in code, metrics, or digest.
- Stores raw payloads and public posting data only. No candidate personal data.
