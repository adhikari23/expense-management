"""Copy incoming SMS/RCS from chat.db into data/ledger.db (incremental, idempotent).

Bodies stored in `attributedBody` are decoded from the typedstream NSString
payload directly, so non-ASCII text (₹) survives and no length-prefix junk
("+o", "+~") leaks into the message.
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Optional


CHAT_DB_PATH = Path(os.getenv("CHAT_DB_PATH", str(Path.home() / "Library/Messages/chat.db")))

# Fallback for attributedBody blobs without a readable NSString header:
# pick the longest sentence-like ASCII run.
ASCII_BLOCK_RE = re.compile(rb"[\t\n\r -~]{6,}")

IGNORE_BLOCKS = {
    "streamtyped",
    "NSMutableAttributedString",
    "NSAttributedString",
    "NSObject",
    "NSMutableString",
    "NSString",
    "NSDictionary",
    "NSNumber",
    "NSValue",
    "NSMutableData",
    "NSData",
    "NSArray",
    "DDSCannerResult",
    "__kIMMessagePartAttributeName",
}


def decode_attributed_body_hex(attributed_body_hex: Optional[str]) -> str:
    if not attributed_body_hex:
        return ""

    try:
        blob = bytes.fromhex(attributed_body_hex)
    except ValueError:
        return ""

    parts = [
        match.group().decode("utf-8", errors="ignore").strip()
        for match in ASCII_BLOCK_RE.finditer(blob)
    ]
    parts = [part for part in parts if part and part not in IGNORE_BLOCKS]

    sentence_like = [part for part in parts if " " in part and any(ch.isalpha() for ch in part)]
    if sentence_like:
        best = max(sentence_like, key=len)
    elif parts:
        best = max(parts, key=len)
    else:
        return ""

    return re.sub(r"\s+", " ", best).strip()


_FAMILY_PREFIX = re.compile(r"^[A-Z]{2}-")
_FAMILY_SUFFIX = re.compile(r"-[A-Z]$")


def sender_family(sender_id: str) -> str:
    """'VM-HDFCBK-S(smsft_fi)' -> 'HDFCBK'. Phone numbers are returned unchanged."""
    s = (sender_id or "").split("(")[0].strip().upper()
    if s.startswith("+") or s.isdigit():
        return s
    s = _FAMILY_PREFIX.sub("", s)
    s = _FAMILY_SUFFIX.sub("", s)
    return s


def decode_attributed_body(blob: Optional[bytes]) -> str:
    if not blob:
        return ""
    idx = blob.find(b"NSString")
    if idx >= 0:
        plus = blob.find(b"\x2b", idx + len(b"NSString"))
        if plus >= 0 and plus - idx < 16:
            i = plus + 1
            length = blob[i]
            i += 1
            if length == 0x81:
                length = int.from_bytes(blob[i:i + 2], "little")
                i += 2
            elif length == 0x82:
                length = int.from_bytes(blob[i:i + 3], "little")
                i += 3
            text = blob[i:i + length].decode("utf-8", errors="replace")
            if text.strip():
                return text
    return decode_attributed_body_hex(blob.hex())


def ingest(ledger: sqlite3.Connection, chat_db: Path = CHAT_DB_PATH) -> int:
    """Insert messages newer than the last ingested ROWID. Returns rows added."""
    last = ledger.execute("SELECT COALESCE(MAX(msg_id), 0) FROM raw_messages").fetchone()[0]
    src = sqlite3.connect(f"file:{chat_db}?mode=ro", uri=True)
    rows = src.execute(
        """
        SELECT m.ROWID, m.guid,
               datetime(m.date / 1000000000 + 978307200, 'unixepoch', 'localtime'),
               COALESCE(h.id, ''), m.service, m.text, m.attributedBody
        FROM message m LEFT JOIN handle h ON h.ROWID = m.handle_id
        WHERE m.is_from_me = 0 AND m.service IN ('SMS', 'RCS') AND m.ROWID > ?
        ORDER BY m.ROWID
        """,
        (last,),
    ).fetchall()
    src.close()

    added = 0
    for rowid, guid, ts, sender, service, text, attributed in rows:
        if text and text.strip():
            body, source = text, "text"
        else:
            body, source = decode_attributed_body(attributed), "attributedBody"
        body = body.replace("￼", "").strip()
        if not body:
            continue
        ledger.execute(
            "INSERT OR IGNORE INTO raw_messages (msg_id, guid, ts, sender_id, family, service, body, body_source)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rowid, guid, ts, sender, sender_family(sender), service, body, source),
        )
        added += 1
    ledger.commit()
    return added
