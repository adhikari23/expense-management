"""Expense ledger from bank SMS.

    python -m app.ledger                       # ingest new SMS, rebuild, run checks, export (no publish)
    python -m app.ledger --no-ingest           # rebuild from data/ledger.db only
    python -m app.ledger --publish             # also write data/transactions.csv for the Aptiq app
    python -m app.ledger review [--month 2026-10] [--limit 30]   # payees to tag, biggest money first
    python -m app.ledger tag KEY CATEGORY [--name "Tea stall"]
    python -m app.ledger categories            # list valid categories

Outputs (data/ledger/): ledger.csv, events.csv, review.csv, reconciliation.csv.
Tags and per-transaction edits: data/rules.json.
"""

from __future__ import annotations

import argparse
import sys

from app.ledger import categories, service, store


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in {"review", "tag", "categories"}:
        return _subcommand(sys.argv[1], sys.argv[2:])

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-ingest", action="store_true", help="skip reading chat.db")
    ap.add_argument("--publish", action="store_true", help="write data/transactions.csv for the Aptiq app (backs up old)")
    args = ap.parse_args()

    res = service.refresh(do_ingest=not args.no_ingest, publish=args.publish, backup=args.publish)
    if not args.no_ingest:
        print(f"ingested {res['ingested']} new messages")
    print("message types:", ", ".join(f"{k}={v}" for k, v in sorted(res["type_counts"].items(), key=lambda x: -x[1])))
    _print_summary()
    print("\nAcceptance checks:")
    for name, ok, detail in res["checks"]:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -> {detail}" if detail and not ok else ""))
    _print_recon(res["recon"])
    if args.publish:
        print(f"\npublished {res['published']} rows -> {service.DASHBOARD_CSV}")


def _subcommand(cmd: str, argv: list[str]) -> None:
    if cmd == "categories":
        print("\n".join(categories.ALL_CATEGORIES))
        return
    if cmd == "review":
        ap = argparse.ArgumentParser(prog="python -m app.ledger review")
        ap.add_argument("--limit", type=int, default=30)
        ap.add_argument("--month", help="only this month, YYYY-MM")
        a = ap.parse_args(argv)
        rows = service.review_queue(a.limit, month=a.month)
        print(f"{'payee key':38s} {'now':18s} {'txns':>5s} {'total':>12s}  last")
        for r in rows:
            print(f"{r['payee_key'][:38]:38s} {r['category'][:18]:18s} {r['n']:5d} ₹{r['total']:>11,.0f}  {r['last_date']}")
        print('\nTag one:  python -m app.ledger tag "<payee key>" "<category>" [--name "..."]')
        return
    ap = argparse.ArgumentParser(prog="python -m app.ledger tag")
    ap.add_argument("key")
    ap.add_argument("category")
    ap.add_argument("--name")
    a = ap.parse_args(argv)
    categories.tag_payee(a.key, a.category, a.name)
    res = service.refresh(do_ingest=False, publish=True)
    print(f"tagged {a.key!r} -> {a.category}; republished {res['published']} rows")


def _print_summary() -> None:
    con = store.connect()
    print("\nLedger by kind:")
    for r in con.execute(
        "SELECT kind, COALESCE(subkind,'') AS sub, COUNT(*) n, ROUND(SUM(amount)) amt FROM ledger GROUP BY 1, 2 ORDER BY 1, 4 DESC"
    ):
        print(f"  {r['kind']:20s} {r['sub']:16s} {r['n']:5d}  ₹{r['amt'] or 0:>12,.0f}")
    print("\nExpenses by category:")
    for r in con.execute(
        "SELECT category, COUNT(*) n, ROUND(SUM(amount)) amt FROM ledger WHERE kind = 'expense' GROUP BY 1 ORDER BY 3 DESC"
    ):
        print(f"  {r['category']:32s} {r['n']:5d}  ₹{r['amt']:>12,.0f}")
    con.close()


def _print_recon(recon: list[dict]) -> None:
    if not recon:
        return
    print("\nCard reconciliation (statement totals vs transactions seen; cumulative over all cycles):")
    first: dict[str, dict] = {}
    last: dict[str, dict] = {}
    spend: dict[str, float] = {}
    for r in recon:
        first.setdefault(r["account"], r)
        last[r["account"]] = r
        spend[r["account"]] = spend.get(r["account"], 0) + r["spends"]
    for acct, r in last.items():
        share = abs(r["cumulative_diff"]) / spend[acct] if spend[acct] else 0
        print(f"  {acct:15s} {first[acct]['from'][:7]}..{r['to'][:7]}  spends ₹{spend[acct]:>10,.0f}"
              f"  unexplained ₹{r['cumulative_diff']:>9,.0f}  ({share:.1%})")


if __name__ == "__main__":
    main()
