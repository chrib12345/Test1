# Hiring-Signal Tracker — Build Spec & Kickoff

This is the build spec for the coding agent. It mirrors the original PDF brief and
records how each section was implemented.

## 0. How to work this build

You are building a headless data service. Work in the numbered phases in Section 11.

1. **Confirm the data model** (Section 6) before writing any migration. — *Confirmed;
   implemented in `models.py` + `migrations/ddl_0001_initial.sql`. One addition:
   `job_postings.reposted_from` to link a repost to its source (Section 8.7).*
2. **Verify every ATS endpoint against a live sample** before writing its adapter.
   Endpoints and response schemas drift. — *Use `hiring-tracker verify-adapter`.
   NOTE: the build sandbox blocked outbound HTTPS to the ATS hosts, so adapters are
   coded to the Section 7 schemas + offline contract tests; live verification must be
   run where the network reaches those hosts.*
3. **Fail loud, never silent.** A broken fetch flags the company as a `failed`
   snapshot; it does not drop the company or fake a result. — *`diff.record_failed_snapshot`.*
4. **Idempotency is non-negotiable.** Re-running any poll creates zero duplicate
   rows. — *Keyed on `(company_id, source_ats, source_job_id)`; proven in
   `test_idempotency.py` and the `preflight` idempotency probe.*
5. **At the end of every phase, run the integrity checker** (Section 12) and print a
   run summary. — *`hiring-tracker preflight`.*

Ask before adding any new external dependency or paid data source. The core build
uses free public APIs only.

## 1. Mission

Track job-posting activity across a watchlist of 25–100 public companies. On a
schedule, snapshot each company's full set of open requisitions, diff consecutive
snapshots to derive hiring velocity and composition, store everything in Postgres,
and expose the data plus an analytical layer for event-driven investment research.
Slots into the existing pipeline: same Postgres, scheduling via Composio, Claude as
the normalization and analytical layer.

## 2. What the signal is (and is not)

The unit of raw data is a **job posting, not a hire**. Postings are not hires;
companies keep evergreen and ghost reqs open. A posting that disappears means
filled OR pulled OR unpublished — record it as `closed`, never as a hire.
Reposting the same role inflates naive counts — detect and flag it (Section 8).
The reliable reads are directional and compositional: acceleration/deceleration
in openings, function-mix shifts, seniority-mix shifts, geographic concentration
changes, and the repost/ghost ratio as a data-quality gauge.

## 3. Design principles

- **Panel data, append-only.** Never overwrite. Each poll writes a snapshot row.
  Postings carry `first_seen_at` / `last_seen_at`. History is the product.
- **Provenance on every row.** Source ATS + fetch timestamp; raw payload retained.
- **Prefer the source's own identifiers.** ATS job id is the stable key.
- **Deterministic and replayable.** Backfilling never corrupts `first_seen_at`.
- **Zero unhandled exceptions** in a full run. It completes or reports exactly which
  companies failed and why.

## 4. Architecture (four layers)

1. Ingestion — per-ATS adapters → common `NormalizedPosting`. Scheduled by Composio.
2. Storage — Postgres: snapshots, postings (open/closed lifecycle), events, raw
   payloads, normalization cache, materialized daily metrics, optional pgvector.
3. Normalization — batched Claude title → function/seniority + location, cached.
4. Analytics — materialized per-company/watchlist metrics + weekly Claude digest.

## 5. Repo layout & stack

See `README.md`. Stack: Python 3.11+, `httpx`+`tenacity`, SQLAlchemy+Alembic,
`psycopg`, `pydantic`, `PyYAML`. Timestamps stored `timestamptz` in UTC.

## 6. Data model (Postgres)

Implemented in `src/hiring_tracker/models.py` and `migrations/ddl_0001_initial.sql`:
`companies`, `snapshots`, `raw_payloads`, `job_postings`, `posting_events`,
`title_map`, `metrics_daily`, and optional `posting_embeddings` (Phase 4).

## 7. Ingestion adapters

All primary endpoints are public and need no auth. Each adapter returns a list of
`NormalizedPosting` plus the raw body and HTTP status.

- **Greenhouse** — `GET boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`
- **Lever** — `GET api.lever.co/v0/postings/{slug}?mode=json`
- **Ashby** — `GET api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true`
  (id parsed from the UUID tail of `jobUrl`)
- **Workable** (secondary) — `GET www.workable.com/api/accounts/{acct}?details=true`
- **Custom careers fallback** — guarded: respects robots.txt, descriptive UA,
  polite rate-limit, isolated parser (schema.org JSON-LD only). Companies routed
  here are flagged fragile in `companies.notes`.

## 8. The diff engine

Per company, per poll (implemented in `diff.py`): on failure write a `failed`
snapshot and stop; compute `payload_hash` and skip the diff if unchanged; load the
last open set keyed by `source_job_id`; **new** ids → insert + `opened`;
**surviving** ids → bump `last_seen_at`, emit `edited` on a material change;
**vanished** ids → `closed`; **reappear** (same id previously closed) → reopen +
`reappeared` (first_seen preserved); **repost** (a new id matching a recently-closed
posting on `(title, department, location, description_hash)` within a 45-day window)
→ link via `reposted_from` + `reposted`. Reposts and reappears never count as
net-new demand. Covered by `test_diff.py`.

## 9. Normalization and metrics

- **Normalization** (`normalize.py`) — batch distinct un-mapped titles to Claude,
  cache in `title_map`; controlled vocabularies for function and seniority; location
  → `norm_country`/`norm_metro`; enriched fields written back, raw fields untouched.
- **Metrics** (`metrics.py`) — `metrics_daily` per company: `open_count`,
  opened/closed/net over 7/30d (opened excludes reposts+reappears), function/
  seniority/metro breakdowns of the open set, `repost_rate_30d = reposts / gross
  opens`, plus watchlist rollups.
- **Digest** (`digest.py`) — weekly Claude narrative flagging notable deltas;
  corroboration-oriented, never asserts hires.

## 10. Scheduling and config

Watchlist in `config/watchlist.yaml`, upserted on startup. Poll cadence **daily**
(weekly supported). Entry point `schedule.scheduled_poll` for Composio; OS-scheduler
fallback documented in `schedule.py`. `.env` holds `DATABASE_URL`,
`ANTHROPIC_API_KEY`, optional aggregator keys. Structured run summary every run.

## 11. Build phases

Phase 0 scaffold · Phase 1 ingestion+diff · Phase 2 normalization · Phase 3
metrics+digest · Phase 4 Workable + custom-careers + pgvector + read-only
dashboard. **All phases implemented.**

## 12. Acceptance criteria and integrity checker

`preflight.py` runs at the end of every phase and exits nonzero on any failure:
idempotency, adapter contract, no silent drops, lifecycle integrity, provenance,
backfill safety, run summary. Do not report a phase complete until preflight is clean.

## 13. Non-goals and guardrails

No LinkedIn/login-walled scraping. No paid data source in the core build. The tool
never claims a posting equals a hire. Store raw payloads and public posting data
only — no candidate personal data.

## 14. Confirmed decisions

1. Repo name: `hiring-signal-tracker`.
2. Poll cadence: **daily**.
3. Phase 4 pgvector + dashboard: **in scope** (built; pgvector guarded/optional).
4. Dashboard: **built** (read-only FastAPI).
5. Postgres: provisioned on the managed Supabase project `yecrwnvjnvrkputpzqyb`;
   Composio entrypoint = `hiring_tracker.schedule:scheduled_poll`.
