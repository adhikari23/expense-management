"""SQLite store for the ledger: raw messages are kept; everything else is rebuilt each run."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.ledger.config import DATA_DIR

LEDGER_DB_PATH = DATA_DIR / "ledger.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_messages (
    msg_id      INTEGER PRIMARY KEY,   -- chat.db message ROWID
    guid        TEXT,
    ts          TEXT NOT NULL,         -- local time, 'YYYY-MM-DD HH:MM:SS'
    sender_id   TEXT,
    family      TEXT,                  -- normalised sender, e.g. HDFCBK
    service     TEXT,
    body        TEXT NOT NULL,
    body_source TEXT
);

CREATE TABLE IF NOT EXISTS events (
    msg_id       INTEGER PRIMARY KEY REFERENCES raw_messages(msg_id),
    msg_type     TEXT NOT NULL,
    template     TEXT,
    direction    TEXT,
    amount       REAL,
    currency     TEXT,
    account      TEXT,
    account_hint TEXT,
    channel      TEXT,
    counterparty TEXT,
    vpa          TEXT,
    ref          TEXT,
    group_id     INTEGER,
    role         TEXT,
    note         TEXT
);

CREATE TABLE IF NOT EXISTS ledger (
    group_id     INTEGER PRIMARY KEY,
    primary_msg  INTEGER,
    ts           TEXT,
    date         TEXT,
    month        TEXT,
    kind         TEXT,     -- expense | income | refund | transfer
    subkind      TEXT,
    amount       REAL,
    currency     TEXT,
    account      TEXT,
    counter_account TEXT,
    channel      TEXT,
    counterparty TEXT,
    vpa          TEXT,
    ref          TEXT,
    category     TEXT,
    category_reason TEXT,
    payee_key    TEXT,
    display_name TEXT,
    msg_ids      TEXT,
    body         TEXT
);

-- Transactions you added by hand in the app. Never rebuilt; copied into `ledger` on every build.
CREATE TABLE IF NOT EXISTS manual_transactions (
    id           TEXT PRIMARY KEY,
    date         TEXT NOT NULL,     -- YYYY-MM-DD
    amount       REAL NOT NULL,
    direction    TEXT NOT NULL,     -- expense | income
    merchant     TEXT,
    category     TEXT,
    payment_mode TEXT,
    note         TEXT,
    created_at   TEXT,
    updated_at   TEXT
);

CREATE TABLE IF NOT EXISTS statements (
    msg_id     INTEGER PRIMARY KEY,
    ts         TEXT,
    account    TEXT,
    total_due  REAL
);
"""


def connect(path: Path = LEDGER_DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    cols = {r[1] for r in con.execute("PRAGMA table_info(ledger)")}
    if cols and "payee_key" not in cols:
        con.execute("DROP TABLE ledger")  # derived table, rebuilt every run
    con.executescript(SCHEMA)
    return con
