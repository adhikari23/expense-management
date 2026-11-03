"""Operations shared by the CLI and the phone sync server."""

from __future__ import annotations

import csv
import hashlib
import shutil
import sqlite3
import threading
import time
from pathlib import Path

from app.ledger import categories, ingest, manual, store
from app.ledger.build import build
from app.ledger.checks import COUNTED, reconcile_cards, run_checks
from app.ledger.config import DATA_DIR, load_config

OUT_DIR = DATA_DIR / "ledger"
DASHBOARD_CSV = DATA_DIR / "transactions.csv"
DASHBOARD_COLUMNS = [
    "txn_id", "message_id", "txn_time", "txn_date", "month", "sender_id", "direction", "amount", "currency",
    "merchant", "category", "category_confidence", "category_reason", "reference_id", "body_source",
    "payment_mode", "message_body", "payee_key",
]
REVIEW_QUERY = """
SELECT e.msg_id, r.ts, NULL AS amount, e.note AS reason, r.body
  FROM events e JOIN raw_messages r USING (msg_id) WHERE e.role = 'review'
UNION ALL
SELECT l.primary_msg, l.ts, l.amount, 'counted as ' || l.kind || ' but payee unknown', l.body
  FROM ledger l WHERE l.kind IN ('expense', 'income') AND COALESCE(l.counterparty, '') = ''
   AND l.amount >= 1000 AND COALESCE(l.subkind, '') != 'loan_prepayment'
ORDER BY 2
"""

_LOCK = threading.RLock()


def txn_id(msg_id: int, guid: str) -> str:
    return hashlib.sha1(f"{msg_id}|{guid}".encode()).hexdigest()[:16]


def refresh(*, do_ingest: bool = True, publish: bool = True, backup: bool = False) -> dict:
    """Ingest (optional) -> rebuild -> export -> publish. Thread-safe."""
    with _LOCK:
        cfg = load_config()
        con = store.connect()
        added = ingest.ingest(con) if do_ingest else 0
        groups, review, type_counts = build(con, cfg)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        _export_query(con, OUT_DIR / "ledger.csv", "SELECT * FROM ledger ORDER BY ts")
        _export_query(con, OUT_DIR / "events.csv",
                      "SELECT e.*, r.ts, r.sender_id, r.body FROM events e JOIN raw_messages r USING (msg_id) ORDER BY r.ts")
        _export_query(con, OUT_DIR / "review.csv", REVIEW_QUERY)
        recon = reconcile_cards(con)
        _write_csv(OUT_DIR / "reconciliation.csv", recon)
        checks = run_checks(con, cfg)
        published = publish_csv(con, backup=backup) if publish else 0
        con.close()
        return {"ingested": added, "type_counts": type_counts, "checks": checks, "recon": recon,
                "published": published}


def publish_csv(con: sqlite3.Connection, backup: bool = False) -> int:
    """Write the CSV the Aptiq app reads. Transfers are left out."""
    rows = con.execute(
        f"SELECT l.*, r.sender_id, r.body_source, r.guid FROM ledger l JOIN raw_messages r ON r.msg_id = l.primary_msg"
        f" WHERE l.kind IN ({','.join('?' * len(COUNTED))}) ORDER BY l.ts", COUNTED
    ).fetchall()
    out = [{
        "txn_id": txn_id(r["primary_msg"], r["guid"]),
        "message_id": r["primary_msg"],
        "txn_time": r["ts"],
        "txn_date": r["date"],
        "month": r["month"],
        "sender_id": r["sender_id"],
        "direction": "expense" if r["kind"] == "expense" else "income",
        "amount": f"{r['amount']:.2f}",
        "currency": r["currency"],
        "merchant": r["display_name"] or r["counterparty"] or r["vpa"] or "Unknown",
        "category": r["category"] or categories.NEEDS_REVIEW,
        "category_confidence": "1.00" if (r["category_reason"] or "").startswith(("rule", "link", "keyword", "user")) else "0.50",
        "category_reason": r["category_reason"],
        "reference_id": r["ref"] or "",
        "body_source": r["body_source"],
        "payment_mode": r["channel"] or "",
        "message_body": r["body"],
        "payee_key": r["payee_key"] or "",
    } for r in rows]
    out += manual.csv_rows()  # transactions you added by hand
    out.sort(key=lambda r: r["txn_time"])
    if backup and DASHBOARD_CSV.exists():
        shutil.copy2(DASHBOARD_CSV, DASHBOARD_CSV.with_name(f"transactions.csv.bak.{int(time.time())}"))
    tmp = DASHBOARD_CSV.with_suffix(".csv.tmp")
    _write_csv(tmp, out, DASHBOARD_COLUMNS)
    tmp.replace(DASHBOARD_CSV)
    return len(out)


def review_queue(limit: int = 50, month: str | None = None) -> list[dict]:
    """'Needs Review' payees, biggest money first; optionally only one month (YYYY-MM).
    One tag fixes every past and future transaction of that payee."""
    con = store.connect()
    where_month = "AND month = ?" if month else ""
    rows = con.execute(
        f"""
        SELECT payee_key, category, kind, COUNT(*) AS n, ROUND(SUM(amount), 2) AS total,
               MAX(date) AS last_date, MAX(COALESCE(display_name, counterparty, vpa)) AS name,
               MAX(channel) AS channel, MAX(primary_msg) AS sample_msg,
               CASE WHEN MAX(category_reason) LIKE '%person' THEN 'person' ELSE 'merchant' END AS hint
          FROM ledger
         WHERE kind IN ('expense', 'income') AND category = ? {where_month}
           AND COALESCE(payee_key, '') != ''
         GROUP BY payee_key, kind
         ORDER BY total DESC
         LIMIT ?
        """, (categories.NEEDS_REVIEW, *([month] if month else []), limit),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def lookup_msg_id(tid: str) -> int | None:
    con = store.connect()
    for row in con.execute("SELECT primary_msg, r.guid FROM ledger l JOIN raw_messages r ON r.msg_id = l.primary_msg"):
        if txn_id(row[0], row[1]) == tid:
            con.close()
            return row[0]
    con.close()
    return None


def payee_of(msg_id: int) -> str | None:
    con = store.connect()
    row = con.execute("SELECT payee_key FROM ledger WHERE primary_msg = ?", (msg_id,)).fetchone()
    con.close()
    return row[0] if row else None


def _export_query(con: sqlite3.Connection, path: Path, query: str) -> None:
    cur = con.execute(query)
    cols = [d[0] for d in cur.description]
    _write_csv(path, [dict(zip(cols, row)) for row in cur.fetchall()], cols)


def _write_csv(path: Path, rows: list[dict], cols: list[str] | None = None) -> None:
    cols = cols or (list(rows[0].keys()) if rows else [])
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
