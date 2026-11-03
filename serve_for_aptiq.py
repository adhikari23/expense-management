#!/usr/bin/env python3
"""Aptiq sync server (phone <-> Mac, same Wi-Fi).

  GET    /                          web app (web/index.html) - works in any phone or desktop browser
  GET    /api/transactions          ledger as JSON (used by the web app)
  GET    /transactions.csv          ledger CSV (used by the Aptiq iPhone app)
  GET    /holdings.json             investments holdings (unchanged)
  GET    /categories                valid categories
  GET    /review?month=YYYY-MM&limit=50   'Needs Review' payees, biggest money first
  POST   /refresh                   read new SMS from chat.db, rebuild, republish
  POST   /review/suggest            {"month"?, "limit"?}  AI category suggestions (Claude) for review payees
  POST   /rules                     {"payee_key", "category", "name"?}  tag a payee everywhere
  POST   /transactions              add one by hand: {"date": "YYYY-MM-DD", "amount", "direction":
                                     "expense"|"income", "merchant"?, "category"?, "payment_mode"?, "note"?}
  PATCH  /transactions/<txn_id>     {"category"?, "merchant"?, "amount"?, "direction"?,
                                     "apply_to": "payee" | "txn"}  (default "txn")
  DELETE /transactions/<txn_id>     hide this transaction (kept in data/rules.json, survives rebuilds)

Edits are stored as rules / overrides in data/rules.json and the CSV is rebuilt
from the ledger, so nothing the app changes is lost on the next SMS refresh.

Usage:
  .venv/bin/python serve_for_aptiq.py [port]   # default port 8000
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).parent))
from app.ledger import categories, manual, service  # noqa: E402

try:  # optional AI layer: the server and all rule-based features run without it
    from app.ledger import ai_suggest
except Exception:  # pragma: no cover
    ai_suggest = None

DATA_PATH = service.DASHBOARD_CSV
# Optional mutual-fund portfolio for the app's Savings tab (built by a separate project).
HOLDINGS_PATH = Path(os.environ.get("APTIQ_HOLDINGS_PATH",
                                    str(Path.home() / "aptiq" / "investments" / "data" / "holdings.json")))
WEB_DIR = Path(__file__).parent / "web"
WEB_FILES = {  # url path -> (file, content type); nothing else under web/ is served
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
}
TXN_PATH = re.compile(r"^/transactions/([^/?#]+)$")


class Handler(BaseHTTPRequestHandler):
    server_version = "AptiqSync/2.0"

    # ------------------------------------------------------------------ plumbing
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _guard(self, fn) -> None:
        try:
            fn()
        except ValueError as e:
            self._json(400, {"error": str(e)})
        except PermissionError as e:
            self._json(500, {"error": f"cannot read Messages database: {e}. Give Full Disk Access to "
                                      f"{Path(sys.executable).resolve()} in System Settings > Privacy & Security."})
        except Exception as e:  # pragma: no cover - surfaced to the app as text
            traceback.print_exc()
            self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204, b"", "text/plain")

    # ------------------------------------------------------------------ GET
    def do_GET(self) -> None:  # noqa: N802
        self._guard(self._get)

    def _get(self) -> None:
        url = urlparse(self.path)
        if url.path in WEB_FILES:
            name, ctype = WEB_FILES[url.path]
            self._send(200, (WEB_DIR / name).read_bytes(), ctype)
        elif url.path == "/api/transactions":
            if not DATA_PATH.exists():
                service.refresh(do_ingest=False, publish=True)
            with DATA_PATH.open(newline="") as fh:
                rows = list(csv.DictReader(fh))
            for r in rows:
                r["amount"] = float(r["amount"] or 0)
                r["category_confidence"] = float(r.get("category_confidence") or 0)
            self._json(200, {"transactions": rows, "generated_at": DATA_PATH.stat().st_mtime,
                             "demo": os.environ.get("EXPENSE_DEMO") == "1"})
        elif url.path == "/transactions.csv":
            if not DATA_PATH.exists():
                service.refresh(do_ingest=False, publish=True)
            self._send(200, DATA_PATH.read_bytes(), "text/csv; charset=utf-8")
        elif url.path == "/holdings.json":
            if not HOLDINGS_PATH.exists():
                self._json(404, {"error": "holdings.json missing - run holdings_updater.py"})
                return
            self._send(200, HOLDINGS_PATH.read_bytes(), "application/json; charset=utf-8")
        elif url.path == "/categories":
            self._json(200, {"expense": categories.EXPENSE_CATEGORIES, "income": categories.INCOME_CATEGORIES})
        elif url.path == "/review":
            q = parse_qs(url.query)
            limit = int(q.get("limit", ["50"])[0])
            month = q.get("month", [None])[0] or None
            self._json(200, {"month": month, "payees": service.review_queue(limit, month=month)})
        elif url.path == "/health":
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "not found"})

    # ------------------------------------------------------------------ POST
    def do_POST(self) -> None:  # noqa: N802
        self._guard(self._post)

    def _post(self) -> None:
        path = urlparse(self.path).path
        if path == "/refresh":
            res = service.refresh(do_ingest=True, publish=True)
            failed = [name for name, ok, _ in res["checks"] if not ok]
            self._json(200, {"ingested": res["ingested"], "published": res["published"], "failed_checks": failed})
        elif path == "/review/suggest":
            # Always 200: suggestions are optional. On failure the app gets whatever
            # is cached plus a short note, and everything else keeps working.
            body = self._body()
            rows, error = [], None
            if ai_suggest is None:
                error = "AI suggestions are not installed on the Mac."
            else:
                try:
                    rows, error = ai_suggest.suggestions(body.get("month") or None, int(body.get("limit") or 80))
                except Exception as e:  # noqa: BLE001
                    error = f"AI suggestions failed: {type(e).__name__}"
            self._json(200, {"suggestions": rows, "error": error})
        elif path == "/transactions":
            row = manual.add(self._body())
            res = service.refresh(do_ingest=False, publish=True)
            self._json(200, {"added": row, "published": res["published"]})
        elif path == "/rules":
            body = self._body()
            key = (body.get("payee_key") or "").strip()
            if not key:
                raise ValueError("payee_key is required")
            entry = categories.tag_payee(key, body.get("category"), body.get("name"))
            res = service.refresh(do_ingest=False, publish=True)
            self._json(200, {"payee_key": key, "rule": entry, "published": res["published"]})
        else:
            self._json(404, {"error": "not found"})

    # ------------------------------------------------------------------ PATCH / DELETE
    def do_PATCH(self) -> None:  # noqa: N802
        self._guard(self._patch)

    def _patch(self) -> None:
        m = TXN_PATH.match(urlparse(self.path).path)
        if not m:
            self._json(404, {"error": "not found"})
            return
        if manual.find(m.group(1)):
            body = self._body()
            row = manual.update(m.group(1), body)
            service.refresh(do_ingest=False, publish=True)
            self._json(200, {"updated": m.group(1), "manual": row})
            return
        msg_id = service.lookup_msg_id(m.group(1))
        if msg_id is None:
            self._json(404, {"error": "txn_id not found"})
            return
        body = self._body()
        apply_to = body.get("apply_to", "txn")
        key = service.payee_of(msg_id)
        txn_fields: dict = {}
        tagged = None
        if body.get("category") or body.get("merchant"):
            if apply_to == "payee" and key:
                tagged = categories.tag_payee(key, body.get("category"), body.get("merchant"))
                # a payee tag should win over an older one-off edit of this txn
                txn_fields.update(category=None, merchant=None)
            else:
                if body.get("category"):
                    txn_fields["category"] = body["category"]
                if body.get("merchant"):
                    txn_fields["merchant"] = body["merchant"]
        if body.get("amount") is not None:
            txn_fields["amount"] = float(body["amount"])
        if body.get("direction"):
            txn_fields["kind"] = "income" if body["direction"] in ("credit", "income") else "expense"
        if txn_fields:
            categories.override_txn(msg_id, **txn_fields)
        res = service.refresh(do_ingest=False, publish=True)
        self._json(200, {"updated": m.group(1), "apply_to": apply_to, "payee_key": key,
                         "payee_rule": tagged, "published": res["published"]})

    def do_DELETE(self) -> None:  # noqa: N802
        self._guard(self._delete)

    def _delete(self) -> None:
        m = TXN_PATH.match(urlparse(self.path).path)
        if m and manual.delete(m.group(1)):
            res = service.refresh(do_ingest=False, publish=True)
            self._json(200, {"deleted": m.group(1), "remaining": res["published"]})
            return
        msg_id = service.lookup_msg_id(m.group(1)) if m else None
        if msg_id is None:
            self._json(404, {"error": "txn_id not found"})
            return
        categories.override_txn(msg_id, exclude=True)
        res = service.refresh(do_ingest=False, publish=True)
        self._json(200, {"deleted": m.group(1), "remaining": res["published"]})

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"{self.log_date_time_string()} {self.address_string()} {fmt % args}\n")


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    service.refresh(do_ingest=False, publish=True)
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Aptiq sync serving {DATA_PATH} on 0.0.0.0:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
