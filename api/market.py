"""Vercel serverless function: GET ?ticker=XXX -> best-effort live market data.

Uses Yahoo's public chart endpoint (stdlib only, no heavy deps). Returns a small
JSON dict; on any failure returns {"_error": ...} so the UI can fall back to
manual entry. Vercel egress generally reaches Yahoo even though some sandboxes
block it.
"""
from http.server import BaseHTTPRequestHandler
import json
import urllib.parse
import urllib.request


def fetch(ticker):
    out = {}
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(ticker)}?range=1y&interval=1d")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.load(r)
        res = d["chart"]["result"][0]
        meta = res.get("meta", {})
        price = meta.get("regularMarketPrice")
        if price:
            out["price"] = round(float(price), 2)
        closes = [c for c in res["indicators"]["quote"][0].get("close", []) if c is not None]
        if closes:
            out["low52"] = round(min(closes), 2)
            out["high52"] = round(max(closes), 2)
    except Exception as e:
        out["_error"] = f"{e.__class__.__name__}: {e}"
    return out


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = urllib.parse.urlparse(self.path).query
        ticker = (urllib.parse.parse_qs(qs).get("ticker", [""])[0] or "").upper()
        payload = json.dumps(fetch(ticker) if ticker else {"_error": "no ticker"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
