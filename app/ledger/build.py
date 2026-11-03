"""Rebuild events + ledger tables from raw_messages."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from app.ledger import categories, manual
from app.ledger.config import Config
from app.ledger.link import LEDGER_TYPES, Event, Group, Linker
from app.ledger.templates import parse

def _resolve_account(cfg: Config, family: str, p) -> str | None:
    if not p.account_kind:
        return None
    return cfg.resolve(family, p.account_hint, p.account_kind)


def build(con: sqlite3.Connection, cfg: Config) -> tuple[list[Group], list[Event], dict[str, int]]:
    events: list[Event] = []
    type_counts: dict[str, int] = {}
    statements = []
    con.execute("DELETE FROM events")
    con.execute("DELETE FROM ledger")
    con.execute("DELETE FROM statements")

    for row in con.execute("SELECT * FROM raw_messages ORDER BY msg_id").fetchall():
        p = parse(row["family"], row["body"])
        type_counts[p.msg_type] = type_counts.get(p.msg_type, 0) + 1
        account = _resolve_account(cfg, row["family"], p)
        if p.msg_type == "STATEMENT" and p.amount is not None and account:
            ts = f"{p.statement_date} 00:00:00" if p.statement_date else row["ts"]
            statements.append((row["msg_id"], ts, account, p.amount))
        if p.msg_type not in LEDGER_TYPES and p.msg_type != "UNPARSED":
            continue
        if p.msg_type == "CONFIRMATION" and p.template == "merchant_receipt":
            continue
        e = Event(
            msg_id=row["msg_id"],
            ts=datetime.strptime(row["ts"], "%Y-%m-%d %H:%M:%S"),
            family=row["family"],
            sender_id=row["sender_id"],
            body=row["body"],
            body_source=row["body_source"],
            p=p,
            account=account,
        )
        events.append(e)

    unparsed = [e for e in events if e.p.msg_type == "UNPARSED"]
    for e in unparsed:
        e.role, e.note = "review", "no template matched"
    linker = Linker(cfg, [e for e in events if e.p.msg_type != "UNPARSED"])
    groups = linker.run()
    review = unparsed + linker.review

    rules = categories.load_rules()
    for g in groups:
        _categorize(g, cfg, rules)

    con.executemany(
        "INSERT INTO statements (msg_id, ts, account, total_due) VALUES (?, ?, ?, ?)", statements
    )
    for e in events:
        con.execute(
            "INSERT INTO events (msg_id, msg_type, template, direction, amount, currency, account, account_hint,"
            " channel, counterparty, vpa, ref, group_id, role, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (e.msg_id, e.p.msg_type, e.p.template, e.p.direction, e.p.amount, e.p.currency, e.account,
             e.p.account_hint, e.p.channel, e.p.counterparty, e.p.vpa, e.p.ref, e.group or None, e.role, e.note),
        )
    for g in groups:
        p = g.primary
        con.execute(
            "INSERT INTO ledger (group_id, primary_msg, ts, date, month, kind, subkind, amount, currency, account,"
            " counter_account, channel, counterparty, vpa, ref, category, category_reason, payee_key, display_name,"
            " msg_ids, body) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (g.id, p.msg_id, p.ts.strftime("%Y-%m-%d %H:%M:%S"), p.ts.strftime("%Y-%m-%d"), p.ts.strftime("%Y-%m"),
             g.kind, g.subkind, g.amount_override or p.amount, p.p.currency,
             p.account if p.p.msg_type != "CARD_PAYMENT" else None,
             g.counter_account, p.p.channel, p.p.counterparty, p.p.vpa, p.p.ref, g.category,
             g.category_reason, categories.payee_key(p.p.counterparty, p.p.vpa), g.display_name,
             ",".join(str(m.msg_id) for m in g.members), p.body),
        )
    manual.copy_into_ledger(con)  # your hand-added transactions
    con.commit()
    return groups, review, type_counts


def _categorize(g: Group, cfg: Config, rules: dict) -> None:
    if g.kind == "orphan_confirmation":
        g.category, g.category_reason = "", "not counted"
        return
    acct = cfg.account(g.primary.account)
    ov = rules["overrides"].get(str(g.primary.msg_id), {})
    if ov.get("amount"):
        g.amount_override = float(ov["amount"])
    g.category, g.category_reason, g.display_name = categories.categorize(
        g, acct.type if acct else "", rules, cfg.upi_spend_max, cfg.extra_keywords)
    if ov.get("exclude"):
        g.kind, g.category_reason = "excluded", "user:deleted"
    if ov.get("kind") in ("expense", "income"):
        g.kind = ov["kind"]
    if ov.get("merchant"):
        g.display_name = ov["merchant"]
