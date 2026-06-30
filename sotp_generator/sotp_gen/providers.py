"""Data providers — market quotes (yfinance) and best-effort segment data
(SEC EDGAR XBRL). Every fetch is wrapped so a network failure degrades to an
empty result and the generator falls back to the config file. Nothing here is
required for offline (config-driven) generation.
"""
from __future__ import annotations
import copy

SEC_UA = "sotp-generator research contact@example.com"
SEC_FACTS = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/us-gaap/{tag}.json"
SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"


def deep_merge(base, overlay):
    """Recursively overlay ``overlay`` onto ``base`` (overlay wins on conflict)."""
    out = copy.deepcopy(base)
    for k, v in (overlay or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        elif v is not None:
            out[k] = v
    return out


def fetch_market(ticker):
    """Return a ``market`` dict from yfinance, or {} on any failure."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        fi = t.fast_info
        info = {}
        try:
            info = t.info  # richer, but flaky; optional
        except Exception:
            pass
        price = fi.get("lastPrice") or info.get("currentPrice")
        shares = fi.get("shares") or info.get("sharesOutstanding")
        out = {}
        if price:
            out["price"] = round(float(price), 2)
        if fi.get("yearLow"):
            out["low52"] = round(float(fi["yearLow"]), 2)
        if fi.get("yearHigh"):
            out["high52"] = round(float(fi["yearHigh"]), 2)
        if shares:
            out["basic_shares"] = round(float(shares) / 1e6, 1)
        if info.get("targetMeanPrice"):
            out["_target"] = float(info["targetMeanPrice"])
        return out
    except Exception as e:  # offline / blocked / not installed
        print(f"  [market] yfinance unavailable ({e.__class__.__name__}); using config.")
        return {}


def fetch_peer_quote(ticker):
    """Return {'price':..,'shares':..} for a comp peer, or {} on failure."""
    try:
        import yfinance as yf
        fi = yf.Ticker(ticker).fast_info
        out = {}
        if fi.get("lastPrice"):
            out["price"] = round(float(fi["lastPrice"]), 2)
        if fi.get("shares"):
            out["shares"] = round(float(fi["shares"]) / 1e6, 0)
        return out
    except Exception:
        return {}


def _cik_for(ticker):
    import requests
    r = requests.get(SEC_TICKERS, headers={"User-Agent": SEC_UA}, timeout=20)
    r.raise_for_status()
    for row in r.json().values():
        if row["ticker"].upper() == ticker.upper():
            return int(row["cik_str"])
    return None


def fetch_segments(ticker):
    """Best-effort reportable-segment revenue from SEC EDGAR XBRL.

    Returns a list of ``{'name':.., 'fy_revenue':..}`` dicts (flagged for review)
    or [] when unavailable. Segment *operating profit* is rarely tagged cleanly,
    so callers must still supply margins/multiples via the config — this only
    seeds segment names and revenue to cut manual entry.
    """
    try:
        import requests
        cik = _cik_for(ticker)
        if not cik:
            return []
        # RevenueFromContractWithCustomerExcludingAssessedTax carries the
        # segment members in many filers' XBRL; parsing the dimensional
        # breakdown reliably needs the full company facts frames, which vary by
        # filer. We surface a clear notice rather than guess wrong.
        print("  [segments] SEC EDGAR reachable; reportable-segment dimensional "
              "parsing varies by filer — review/extend configs/<TICKER>.yaml.")
        return []
    except Exception as e:
        print(f"  [segments] SEC EDGAR unavailable ({e.__class__.__name__}); using config.")
        return []


def enrich(cfg, ticker, live=False):
    """Overlay live data onto a base config when ``live`` and reachable."""
    if not live:
        return cfg
    out = copy.deepcopy(cfg)
    market = fetch_market(ticker)
    target = market.pop("_target", None)
    if market:
        out["market"] = deep_merge(out.get("market", {}), market)
        print(f"  [market] refreshed: {market}")
    if target:
        out.setdefault("street", {})["target"] = round(target, 2)
    # peer quotes
    for seg in out.get("segments", []):
        for peer in seg.get("comps", []):
            q = fetch_peer_quote(peer.get("ticker", ""))
            if q:
                peer.update(q)
    fetch_segments(ticker)  # advisory only
    return out
