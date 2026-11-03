"""Transactions you add by hand (from the app's developer option).

Stored in the ledger database (table manual_transactions in data/ledger.db).
Unlike the SMS-derived tables, this table is never rebuilt: every rebuild copies
its rows into the `ledger` table, so they show up in summaries, review, checks
and the published CSV like any other transaction.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.ledger import categories, store
from app.ledger.config import DATA_DIR

DB_PATH: Path = store.LEDGER_DB_PATH
LEGACY_JSON = DATA_DIR / "manual_transactions.json"
_LOCK = threading.Lock()
EDITABLE = {"date", "amount", "direction", "merchant", "category", "payment_mode", "note"}
COLUMNS = ["id", "date", "amount", "direction", "merchant", "category", "payment_mode", "note",
           "created_at", "updated_at"]


def _con() -> sqlite3.Connection:
    con = store.connect(DB_PATH)
    _migrate_json(con)
    return con


def _migrate_json(con: sqlite3.Connection) -> None:
    """One-time import of entries saved before they moved into the database."""
    if DB_PATH != store.LEDGER_DB_PATH or not LEGACY_JSON.exists():
        return
    try:
        rows = json.loads(LEGACY_JSON.read_text())
    except (OSError, json.JSONDecodeError):
        return
    for r in rows:
        con.execute(
            "INSERT OR IGNORE INTO manual_transactions (id, date, amount, direction, merchant, category,"
            " payment_mode, note, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (r["id"], r["date"], r["amount"], r["direction"], r["merchant"], r["category"],
             r.get("payment_mode", ""), r.get("note", ""), r.get("created_at"), r.get("created_at")),
        )
    con.commit()
    LEGACY_JSON.rename(LEGACY_JSON.with_suffix(".json.migrated"))


def migrate_names() -> int:
    con = _con()
    n = 0
    for old, new in categories.LEGACY_NAMES.items():
        n += con.execute("UPDATE manual_transactions SET category = ? WHERE category = ?", (new, old)).rowcount
    con.commit()
    con.close()
    return n


def load() -> list[dict]:
    con = _con()
    rows = [dict(r) for r in con.execute(f"SELECT {', '.join(COLUMNS)} FROM manual_transactions ORDER BY date, id")]
    con.close()
    return rows


def _validate(entry: dict) -> dict:
    out = dict(entry)
    try:
        out["amount"] = round(float(out["amount"]), 2)
    except (KeyError, TypeError, ValueError):
        raise ValueError("amount is required and must be a number")
    if out["amount"] <= 0:
        raise ValueError("amount must be greater than 0")
    direction = out.get("direction", "expense")
    out["direction"] = "income" if direction in ("income", "credit") else "expense"
    try:
        datetime.strptime(out.get("date") or "", "%Y-%m-%d")
    except ValueError:
        raise ValueError("date must be YYYY-MM-DD")
    category = categories.canonical(out.get("category")) or categories.NEEDS_REVIEW
    if category not in categories.ALL_CATEGORIES:
        raise ValueError(f"unknown category {category!r}")
    out["category"] = category
    out["merchant"] = (out.get("merchant") or "").strip() or "Manual entry"
    out["payment_mode"] = (out.get("payment_mode") or "").strip()
    out["note"] = (out.get("note") or "").strip()
    return out


def _write(con: sqlite3.Connection, row: dict) -> None:
    con.execute(
        "INSERT OR REPLACE INTO manual_transactions (id, date, amount, direction, merchant, category,"
        " payment_mode, note, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        tuple(row.get(c) for c in COLUMNS),
    )
    con.commit()


def add(entry: dict) -> dict:
    row = _validate({k: entry.get(k) for k in EDITABLE if k in entry})
    now = datetime.now().isoformat(timespec="seconds")
    row.update(id="m" + uuid.uuid4().hex[:15], created_at=now, updated_at=now)  # same length as SMS ids
    with _LOCK:
        con = _con()
        _write(con, row)
        con.close()
    return row


def find(txn_id: str) -> Optional[dict]:
    return next((r for r in load() if r["id"] == txn_id), None)


def update(txn_id: str, fields: dict) -> Optional[dict]:
    with _LOCK:
        current = find(txn_id)
        if current is None:
            return None
        merged = {**current, **{k: v for k, v in fields.items() if k in EDITABLE and v is not None}}
        row = {**_validate(merged), "id": txn_id, "created_at": current["created_at"],
               "updated_at": datetime.now().isoformat(timespec="seconds")}
        con = _con()
        _write(con, row)
        con.close()
    return row


def delete(txn_id: str) -> bool:
    with _LOCK:
        con = _con()
        n = con.execute("DELETE FROM manual_transactions WHERE id = ?", (txn_id,)).rowcount
        con.commit()
        con.close()
    return n > 0


def copy_into_ledger(con: sqlite3.Connection) -> int:
    """Called by every rebuild: put manual rows into the `ledger` table."""
    rows = con.execute(f"SELECT {', '.join(COLUMNS)} FROM manual_transactions ORDER BY date, id").fetchall()
    for i, r in enumerate(rows, start=1):
        con.execute(
            "INSERT INTO ledger (group_id, primary_msg, ts, date, month, kind, subkind, amount, currency, account,"
            " counter_account, channel, counterparty, vpa, ref, category, category_reason, payee_key, display_name,"
            " msg_ids, body) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (-i, None, f"{r['date']} 12:00:00", r["date"], r["date"][:7], r["direction"], "manual", r["amount"],
             "INR", "manual", None, r["payment_mode"] or None, r["merchant"], None, r["id"], r["category"],
             "user:manual", categories.payee_key(r["merchant"], None), r["merchant"], "",
             r["note"] or "Added manually"),
        )
    return len(rows)


def csv_rows() -> list[dict]:
    """Rows in the published CSV format (txn_id = manual id, so the app can edit/delete)."""
    return [{
        "txn_id": r["id"],
        "message_id": "",
        "txn_time": f"{r['date']} 12:00:00",
        "txn_date": r["date"],
        "month": r["date"][:7],
        "sender_id": "manual",
        "direction": r["direction"],
        "amount": f"{r['amount']:.2f}",
        "currency": "INR",
        "merchant": r["merchant"],
        "category": r["category"],
        "category_confidence": "1.00",
        "category_reason": "user:manual",
        "reference_id": "",
        "body_source": "manual",
        "payment_mode": r["payment_mode"],
        "message_body": r["note"] or "Added manually",
        "payee_key": categories.payee_key(r["merchant"], None),
    } for r in load()]
