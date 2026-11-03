"""AI category suggestions for 'Needs Review' payees, via your Claude Code login.

Runs the `claude` CLI in non-interactive mode (claude -p) with all tools
disabled, no saved session and a JSON schema, so it can only answer with
categories from your list. It uses the account you are signed in with in
Claude Code - no separate API key.

Only suggests - nothing is categorised until you accept a suggestion in the app
(which saves a normal payee tag). Suggestions are cached per payee in
data/ledger.db (table ai_suggestions), so each payee is sent to Claude once.

What is sent: payee name / UPI ID, money in or out, payment channel, count and
amounts, and one sample SMS with long digit runs (account numbers, references)
masked. Nothing else leaves the Mac.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from typing import Optional

from app.ledger import categories, service, store
from app.ledger.config import DATA_DIR, load_config

# Where the CLI may live (launchd jobs don't get your shell's PATH).
CLAUDE_CANDIDATES = ["/opt/homebrew/bin/claude", "/usr/local/bin/claude", os.path.expanduser("~/.local/bin/claude")]
TIMEOUT_S = 120
BATCH = 40

# Never suggested: "Needs Review" is the absence of a category, and "UPI Spend" is
# your rule for unknown UPI payments under ₹500 - review payees are ₹500+.
NOT_SUGGESTED = {categories.NEEDS_REVIEW, categories.UPI_SPEND}


def _allowed(cats: list[str]) -> list[str]:
    return [c for c in cats if c not in NOT_SUGGESTED]

SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS ai_suggestions (
    payee_key  TEXT NOT NULL,
    kind       TEXT NOT NULL,      -- expense | income
    category   TEXT NOT NULL,
    confidence TEXT NOT NULL,      -- high | medium | low
    reason     TEXT,
    model      TEXT,
    created_at TEXT,
    PRIMARY KEY (payee_key, kind)
);
"""

SYSTEM = """You categorise bank transactions for one person in India, from their bank SMS.
For each payee, pick the single most likely category from the allowed list for its direction.

Guidance:
- UPI IDs like q123@ybl, paytmqr..., bharatpe..., vyapar... are shop QR codes; the name may be missing.
- Indian merchant names are often truncated (e.g. "GRAND PALACE H" is likely a hotel, "M S ... SWEETS" a sweet shop).
- Payments to or from a person (friend or family member): use "FnF Transfer". Use high confidence only when the
  surname matches the account owner's surname or the payee is clearly a person; keep it low when the payee
  might be a small shop paid by UPI.
- Prefer "Other" (money out) or "Other Income" (money in) over a confident wrong guess.
- confidence: high = the name clearly says what it is; medium = a reasonable inference; low = a guess.
- reason: at most 8 words, plain English.
"""


class SuggestError(RuntimeError):
    """Shown to the user as a note (e.g. Claude Code not signed in)."""


def _claude_bin() -> str:
    found = shutil.which("claude") or next((c for c in CLAUDE_CANDIDATES if os.path.exists(c)), None)
    if not found:
        raise SuggestError("AI suggestions need Claude Code on this Mac (the `claude` command was not found).")
    return found


def _run_claude(prompt: str, schema: dict) -> dict:
    """One non-interactive Claude Code call. Returns its JSON result object."""
    cmd = [
        _claude_bin(), "-p",
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--system-prompt", SYSTEM,
        "--tools", "",                 # answer only: no file, shell or web access
        "--no-session-persistence",
        "--effort", "low",
    ]
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([env.get("PATH", ""), "/opt/homebrew/bin", "/usr/local/bin"])
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=TIMEOUT_S,
                              cwd=str(DATA_DIR), env=env)
    except subprocess.TimeoutExpired:
        raise SuggestError("Claude took too long - try again.")
    except OSError as e:
        raise SuggestError(f"Could not start Claude Code: {e.strerror or e}")
    try:
        out = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        out = {}
    if proc.returncode != 0 or out.get("is_error") or not out:
        detail = " ".join(str(out.get("result") or proc.stderr or "").split())[:160]
        if re.search(r"log ?in|logged|auth|credential|token|expired|401", detail, re.I):
            raise SuggestError("Claude Code isn't signed in (or the login expired) - run `claude` on the Mac and /login.")
        if re.search(r"limit|429|quota|usage", detail, re.I):
            raise SuggestError("Claude usage limit reached - suggestions will work again later.")
        raise SuggestError(f"Claude Code returned an error: {detail or 'exit code ' + str(proc.returncode)}")
    return out


def _mask(text: str) -> str:
    return re.sub(r"\d{5,}", lambda m: "#" * len(m.group(0)), " ".join((text or "").split()))[:220]


def _schema() -> dict:
    allowed = _allowed(categories.ALL_CATEGORIES)
    return {
        "type": "object",
        "properties": {
            "suggestions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "payee_key": {"type": "string"},
                        "category": {"type": "string", "enum": allowed},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                        "reason": {"type": "string"},
                    },
                    "required": ["payee_key", "category", "confidence", "reason"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["suggestions"],
        "additionalProperties": False,
    }


def _describe(con, payee: dict) -> dict:
    sample = con.execute(
        "SELECT body, amount FROM ledger WHERE payee_key = ? AND kind = ? ORDER BY ts DESC LIMIT 1",
        (payee["payee_key"], payee["kind"]),
    ).fetchone()
    return {
        "payee_key": payee["payee_key"],
        "name": payee.get("name") or payee["payee_key"],
        "direction": "money out" if payee["kind"] == "expense" else "money in",
        "channel": payee.get("channel") or "",
        "transactions": payee["n"],
        "total_inr": round(payee["total"]),
        "looks_like_person": payee.get("hint") == "person",
        "sample_sms": _mask(sample["body"]) if sample else "",
    }


def _ask_claude(items: list[dict]) -> list[dict]:
    cfg = load_config()
    surname = next((n.split()[-1] for n in cfg.owner_names if " " in n), "")
    prompt = (
        f"Account owner's surname: {surname or 'unknown'}.\n"
        f"Allowed categories for money out: {_allowed(categories.EXPENSE_CATEGORIES)}\n"
        f"Allowed categories for money in: {_allowed(categories.INCOME_CATEGORIES)}\n"
        "Return one suggestion per payee_key below, using the same payee_key.\n\n"
        + json.dumps(items, ensure_ascii=False, indent=1)
    )
    try:
        out = _run_claude(prompt, _schema())
    except SuggestError:
        raise
    except Exception as e:  # never let the optional AI step break anything
        raise SuggestError(f"AI suggestions failed: {type(e).__name__}")
    structured = out.get("structured_output")
    if not isinstance(structured, dict):
        try:
            structured = json.loads(out.get("result") or "{}")
        except (TypeError, json.JSONDecodeError):
            return []
    return structured.get("suggestions", []) if isinstance(structured, dict) else []


def suggestions(month: Optional[str] = None, limit: int = 80) -> tuple[list[dict], Optional[str]]:
    """([{payee_key, kind, category, confidence, reason}], error) for the review list.

    Cache first, then Claude for the rest. If Claude fails (no/expired key, rate
    limit, offline...), the cached suggestions are still returned with the error.
    """
    payees = service.review_queue(limit, month=month)
    con = store.connect()
    con.executescript(SCHEMA_DDL)
    cached = {
        (r["payee_key"], r["kind"]): {**dict(r), "category": categories.canonical(r["category"])}
        for r in con.execute("SELECT payee_key, kind, category, confidence, reason FROM ai_suggestions")
    }
    missing = [p for p in payees if (p["payee_key"], p["kind"]) not in cached]
    error = None
    for i in range(0, len(missing), BATCH):
        chunk = missing[i:i + BATCH]
        by_key = {p["payee_key"]: p for p in chunk}
        try:
            answer = _ask_claude([_describe(con, p) for p in chunk])
        except SuggestError as e:
            error = str(e)
            break
        for s in answer:
            s["category"] = categories.canonical(s.get("category"))
            payee = by_key.get(s.get("payee_key"))
            if not payee:
                continue
            allowed = categories.INCOME_CATEGORIES if payee["kind"] == "income" else categories.EXPENSE_CATEGORIES
            if s["category"] not in allowed or s["category"] in NOT_SUGGESTED:
                continue  # wrong direction for this payee: don't suggest it
            row = {"payee_key": payee["payee_key"], "kind": payee["kind"], "category": s["category"],
                   "confidence": s["confidence"], "reason": (s.get("reason") or "")[:80]}
            con.execute(
                "INSERT OR REPLACE INTO ai_suggestions VALUES (?,?,?,?,?,?,?)",
                (row["payee_key"], row["kind"], row["category"], row["confidence"], row["reason"], "claude-code",
                 datetime.now().isoformat(timespec="seconds")),
            )
            cached[(row["payee_key"], row["kind"])] = row
        con.commit()
    con.close()
    rows = [
        {"payee_key": p["payee_key"], "kind": p["kind"],
         **{k: cached[(p["payee_key"], p["kind"])][k] for k in ("category", "confidence", "reason")}}
        for p in payees if (p["payee_key"], p["kind"]) in cached
    ]
    return rows, error
