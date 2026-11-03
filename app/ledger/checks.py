"""Acceptance checks for Phase 1 and the credit-card statement reconciliation report."""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta

from app.ledger.config import Config

COUNTED = ("expense", "income", "refund")

NON_TXN_TEXT = re.compile(
    r"requested money|autopay request|will be (?:debited|deducted)|amount due|amt due|total due|"
    r"statement (?:is )?generated|\bOTP\b", re.I)
JUNK_MERCHANT = re.compile(r"^\+?\d{8,}\.?$|^[A-Z]{2}-[A-Z]{4,}|^\d{4}-\d{2}-\d{2}")


def run_checks(con: sqlite3.Connection, cfg: Config) -> list[tuple[str, bool, str]]:
    rows = con.execute(
        f"SELECT * FROM ledger WHERE kind IN ({','.join('?' * len(COUNTED))})", COUNTED
    ).fetchall()
    results: list[tuple[str, bool, str]] = []

    bad = [r for r in rows if NON_TXN_TEXT.search(r["body"])]
    results.append(("No reminders/requests/statements/OTPs counted", not bad, _sample(bad)))

    bad = [r for r in rows if JUNK_MERCHANT.search((r["counterparty"] or "").strip())]
    results.append(("No phone numbers / sender ids as merchant", not bad, _sample(bad, "counterparty")))

    card_payment_msgs = {r[0] for r in con.execute("SELECT msg_id FROM events WHERE msg_type = 'CARD_PAYMENT'")}
    bad = [r for r in rows if r["primary_msg"] in card_payment_msgs]
    results.append(("Card-side 'payment received' never counted as income", not bad, _sample(bad)))

    bad = [r for r in rows if r["kind"] == "expense" and _acct_type(cfg, r["account"]) == "savings"
           and cfg.is_card_bill_payee(r["counterparty"])]
    results.append(("Card bill payments (CRED/BBPS/...) never counted as expense", not bad, _sample(bad, "counterparty")))

    bad = [r for r in rows if cfg.is_owner(r["counterparty"]) and r["subkind"] not in ("loan_prepayment", "investment")]
    results.append(("Transfers to/from own name never counted", not bad, _sample(bad, "counterparty")))

    refs = Counter((r["ref"], r["kind"]) for r in rows if r["ref"])
    dup = [k for k, n in refs.items() if n > 1]
    results.append(("No two counted rows share a reference number", not dup, ", ".join(str(k[0]) for k in dup[:5])))

    orphan = con.execute(
        "SELECT r.sender_id FROM events e JOIN raw_messages r USING (msg_id)"
        " WHERE e.account IS NULL AND e.msg_type IN ('TXN', 'CARD_PAYMENT') AND e.role != 'review'"
    ).fetchall()
    results.append(("Every money SMS maps to one of your accounts (config/accounts.json)", not orphan,
                    f"{len(orphan)} rows, senders: {sorted({o[0] for o in orphan})[:5]}"))

    unparsed = con.execute("SELECT COUNT(*) FROM events WHERE role = 'review'").fetchone()[0]
    total_money = con.execute(
        "SELECT COUNT(*) FROM events WHERE msg_type IN ('TXN','CARD_PAYMENT','REFUND','UNPARSED')").fetchone()[0]
    coverage = 1 - unparsed / total_money if total_money else 1
    results.append((f"Template coverage >= 99% ({coverage:.2%}, {unparsed} in review)", coverage >= 0.99, ""))
    return results


def _acct_type(cfg: Config, account_id) -> str:
    acct = cfg.account(account_id)
    return acct.type if acct else ""


def _sample(rows, field: str = "body") -> str:
    if not rows:
        return ""
    return f"{len(rows)} rows, e.g. " + " | ".join(str(r[field])[:80] for r in rows[:3])


# Amount the card actually received (one row per payment, not per notice).
CARD_PAYMENTS = (
    "FROM events e JOIN raw_messages r USING (msg_id) WHERE e.msg_type = 'CARD_PAYMENT' AND e.account = ?"
    " AND COALESCE(e.note, '') != 'second card-side notice'"
)


def reconcile_cards(con: sqlite3.Connection) -> list[dict]:
    """Compare consecutive statement totals with the transactions seen between them.

    expected_due(n) = due(n-1) - payments + spends - refunds   (window: statement SMS to statement SMS)

    The statement SMS arrives days after the cycle closes, so a single cycle's
    `diff` swings +/- as transactions land on the wrong side of the boundary.
    `cumulative_diff` (sum of diffs since the first statement) cancels that out;
    what is left is fees, interest, EMI conversions and spends with no SMS.
    """
    out = []
    for (account,) in con.execute("SELECT DISTINCT account FROM statements ORDER BY account").fetchall():
        stmts = con.execute(
            "SELECT ts, total_due FROM statements WHERE account = ? ORDER BY ts", (account,)
        ).fetchall()
        # keep the first statement/amount-due SMS of each cycle
        uniq = []
        for s in stmts:
            if uniq and (_dt(s["ts"]) - _dt(uniq[-1]["ts"])).days < 15:
                continue
            uniq.append(dict(s))
        # A card payment of exactly the statement total proves the statement already
        # existed, so the cycle boundary is no later than that payment.
        for s in uniq:
            paid = con.execute(
                f"SELECT MIN(r.ts) {CARD_PAYMENTS} AND ABS(e.amount - ?) < 1 AND r.ts < ? AND r.ts > datetime(?, '-10 days')",
                (account, s["total_due"], s["ts"], s["ts"]),
            ).fetchone()[0]
            if paid:
                s["ts"] = (_dt(paid) - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
        cumulative = 0.0
        for prev, cur in zip(uniq, uniq[1:]):
            window = (prev["ts"], cur["ts"])
            spends = _sum(con, "kind = 'expense' AND account = ?", account, window)
            refunds = _sum(con, "kind = 'refund' AND account = ?", account, window)
            payments = round(con.execute(
                f"SELECT COALESCE(SUM(e.amount), 0) {CARD_PAYMENTS} AND r.ts > ? AND r.ts <= ?", (account, *window)
            ).fetchone()[0], 2)
            expected = prev["total_due"] - payments + spends - refunds
            out.append({
                "account": account,
                "from": prev["ts"][:10],
                "to": cur["ts"][:10],
                "prev_due": prev["total_due"],
                "payments": payments,
                "spends": spends,
                "refunds": refunds,
                "expected_due": round(expected, 2),
                "statement_due": cur["total_due"],
                "diff": round(cur["total_due"] - expected, 2),
            })
            cumulative += cur["total_due"] - expected
            out[-1]["cumulative_diff"] = round(cumulative, 2)
    return out


def _dt(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")


def _sum(con, where: str, param, window) -> float:
    q = f"SELECT COALESCE(SUM(amount), 0) FROM ledger WHERE {where} AND ts > ? AND ts <= ?"
    return round(con.execute(q, (param, *window)).fetchone()[0], 2)
