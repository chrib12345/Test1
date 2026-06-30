#!/usr/bin/env python3
"""SOTP Excel generator — command-line entry point.

Usage:
    python sotp.py SPGI                 # build from configs/SPGI.yaml
    python sotp.py SPGI --live          # refresh market/peer quotes via yfinance
    python sotp.py MSFT -o out.xlsx     # custom output path
    python sotp.py SPGI --config my.yaml

Type a stock symbol; the tool builds an 11-sheet Sum-of-the-Parts valuation
workbook modeled on the SPGI master template. Market data is refreshed live
(when --live and the network allow); per-segment financials and valuation
multiples are read from configs/<TICKER>.yaml — the analyst-judgment inputs a
SOTP requires — which you refine over time (blue = input, yellow = estimate).
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from sotp_gen.build import build_workbook          # noqa: E402
from sotp_gen.model import load_yaml               # noqa: E402
from sotp_gen import providers                      # noqa: E402

CONFIG_DIR = os.path.join(HERE, "configs")


def resolve_config(ticker, explicit):
    if explicit:
        return explicit
    path = os.path.join(CONFIG_DIR, f"{ticker.upper()}.yaml")
    return path if os.path.exists(path) else None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate a SOTP valuation workbook for a stock symbol.")
    ap.add_argument("ticker", help="Stock symbol, e.g. SPGI")
    ap.add_argument("-o", "--output", help="Output .xlsx path (default <TICKER>_SOTP.xlsx)")
    ap.add_argument("-c", "--config", help="Path to a config YAML (default configs/<TICKER>.yaml)")
    ap.add_argument("--live", action="store_true", help="Refresh market & peer quotes via yfinance")
    args = ap.parse_args(argv)

    ticker = args.ticker.upper()
    cfg_path = resolve_config(ticker, args.config)
    if not cfg_path:
        print(f"No config found for {ticker}.")
        print(f"  Create {os.path.join('configs', ticker + '.yaml')} (copy configs/SPGI.yaml as a template).")
        print("  A SOTP needs per-segment financials & multiples, which no free API supplies cleanly.")
        return 2

    print(f"Loading {cfg_path} ...")
    cfg = load_yaml(cfg_path)
    cfg.setdefault("meta", {}).setdefault("ticker", ticker)

    if args.live:
        print("Refreshing live data (yfinance) ...")
        cfg = providers.enrich(cfg, ticker, live=True)

    print("Building workbook ...")
    wb = build_workbook(cfg)
    out = args.output or f"{ticker}_SOTP.xlsx"
    wb.save(out)
    print(f"Wrote {out}  ({len(wb.sheetnames)} sheets: {', '.join(wb.sheetnames)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
