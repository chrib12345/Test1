"""Command-line entrypoint.

    hiring-tracker init-db          # create schema (dev; prefer Alembic in prod)
    hiring-tracker sync-watchlist   # upsert config/watchlist.yaml into companies
    hiring-tracker poll             # run one full watchlist poll (+ --then-metrics)
    hiring-tracker verify-adapter TICKER|--ats greenhouse --token stripe
    hiring-tracker normalize        # Phase 2: classify titles + enrich
    hiring-tracker metrics          # Phase 3: materialize metrics_daily
    hiring-tracker digest           # Phase 3: weekly Claude narrative
    hiring-tracker embed            # Phase 4: pgvector embeddings
    hiring-tracker preflight        # Section 12 integrity checker
"""

from __future__ import annotations

import argparse
import json
import sys

from .config import load_watchlist
from .logging_util import configure_logging, get_logger

log = get_logger("cli")


def _cmd_init_db(args) -> int:
    from .db import get_engine
    from .models import Base

    engine = get_engine()
    Base.metadata.create_all(engine)
    print("schema created (dev mode). For production use: alembic upgrade head")
    return 0


def _cmd_sync_watchlist(args) -> int:
    from .db import session_scope
    from .ingest import sync_watchlist

    with session_scope() as s:
        n = sync_watchlist(s)
    print(f"watchlist synced: {n} companies")
    return 0


def _cmd_poll(args) -> int:
    from .ingest import run_watchlist

    summary = run_watchlist(only_ticker=args.ticker)
    if args.then_metrics:
        from .metrics import run_metrics

        run_metrics()
    if args.json:
        print(summary.as_json())
    return 0 if not summary.failures else 0  # failures reported, run still completes


def _cmd_verify_adapter(args) -> int:
    """Pull one real company and print the raw JSON + parsed contract (Section 0.2)."""
    from .adapters import get_adapter

    if args.ticker:
        entry = next((e for e in load_watchlist() if e.ticker == args.ticker), None)
        if entry is None:
            print(f"no watchlist entry with ticker={args.ticker}", file=sys.stderr)
            return 2
        ats, token = entry.ats_type, (entry.careers_url if entry.ats_type == "custom" else entry.ats_token)
    else:
        ats, token = args.ats, args.token
    if not ats or not token:
        print("provide --ats and --token, or a --ticker in the watchlist", file=sys.stderr)
        return 2

    adapter = get_adapter(ats)
    try:
        result = adapter.fetch(token)
    finally:
        getattr(adapter, "close", lambda: None)()
    print(f"HTTP {result.http_status} in {result.duration_ms}ms — {len(result.postings)} postings")
    if result.postings:
        print("first parsed posting:")
        print(json.dumps(result.postings[0].model_dump(), indent=2))
    if result.notes:
        print("notes:", "; ".join(result.notes))
    return 0


def _cmd_normalize(args) -> int:
    from .normalize import run_normalization

    res = run_normalization()
    print(json.dumps(res, indent=2))
    return 0


def _cmd_metrics(args) -> int:
    from .metrics import run_metrics

    res = run_metrics()
    print(json.dumps(res, indent=2, default=str))
    return 0


def _cmd_digest(args) -> int:
    from .digest import generate_digest

    print(generate_digest())
    return 0


def _cmd_embed(args) -> int:
    from .db import session_scope
    from .vector import embed_open_postings

    with session_scope() as s:
        n = embed_open_postings(s)
    print(f"embedded {n} postings")
    return 0


def _cmd_preflight(args) -> int:
    from .preflight import run_preflight

    report = run_preflight()
    print(report.render())
    return 0 if report.ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hiring-tracker", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db").set_defaults(func=_cmd_init_db)
    sub.add_parser("sync-watchlist").set_defaults(func=_cmd_sync_watchlist)

    sp = sub.add_parser("poll")
    sp.add_argument("--ticker", default=None, help="poll only this ticker")
    sp.add_argument("--then-metrics", action="store_true", dest="then_metrics")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=_cmd_poll)

    va = sub.add_parser("verify-adapter")
    va.add_argument("ticker", nargs="?", default=None)
    va.add_argument("--ats", default=None)
    va.add_argument("--token", default=None)
    va.set_defaults(func=_cmd_verify_adapter)

    sub.add_parser("normalize").set_defaults(func=_cmd_normalize)
    sub.add_parser("metrics").set_defaults(func=_cmd_metrics)
    sub.add_parser("digest").set_defaults(func=_cmd_digest)
    sub.add_parser("embed").set_defaults(func=_cmd_embed)
    sub.add_parser("preflight").set_defaults(func=_cmd_preflight)
    return p


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
