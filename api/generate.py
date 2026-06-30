"""Vercel serverless function: POST a SOTP config (JSON) -> .xlsx download."""
from http.server import BaseHTTPRequestHandler
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sotp_generator"))
from sotp_gen.build import build_workbook  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b"{}"
            cfg = json.loads(raw or b"{}")
            ticker = ((cfg.get("meta") or {}).get("ticker") or "TICKER").upper()
            wb = build_workbook(cfg)
            buf = io.BytesIO()
            wb.save(buf)
            data = buf.getvalue()
            self.send_response(200)
            self.send_header("Content-Type",
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", f'attachment; filename="{ticker}_SOTP.xlsx"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:  # surface a readable error to the UI
            msg = json.dumps({"error": f"{e.__class__.__name__}: {e}"}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
