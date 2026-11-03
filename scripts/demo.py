#!/usr/bin/env python3
"""Demo mode: made-up bank SMS -> ledger -> web app, without touching your data.

    python3 scripts/demo.py               # build demo/ and serve it on http://localhost:8001
    python3 scripts/demo.py --build-only  # just (re)build demo/

Everything lives in demo/ (git-ignored): a fake Messages database, the ledger
built from it with config/accounts.example.json, a fake mutual-fund portfolio,
a few payee tags and cached AI suggestions. Every name and number is invented.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"
CHAT_DB = DEMO / "chat.db"
DATA = DEMO / "data"
HOLDINGS = DEMO / "holdings.json"
CONFIG = ROOT / "config" / "accounts.example.json"
APPLE_EPOCH = 978307200
MONTHS_BACK = 6

rng = random.Random(42)
_ref = [500000000000]


def ref() -> str:
    _ref[0] += rng.randint(1000, 90000)
    return str(_ref[0])


def money(v: float) -> str:
    """Indian grouping: 150000 -> 1,50,000.00"""
    whole, frac = f"{v:.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups + [tail]) + "." + frac


def month_starts() -> list[date]:
    today = date.today()
    out, y, m = [], today.year, today.month
    for _ in range(MONTHS_BACK):
        out.insert(0, date(y, m, 1))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out


def sms_for_month(start: date) -> list[tuple[datetime, str, str]]:
    """(when, sender, body) for one month of a made-up person's bank SMS."""
    today = date.today()
    nxt = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    last = min(nxt - timedelta(days=1), today)
    msgs: list[tuple[datetime, str, str]] = []

    def at(day: int, hour: int = 10) -> datetime | None:
        if day > last.day:
            return None
        return datetime(start.year, start.month, day, hour, rng.randint(0, 59), rng.randint(0, 59))

    def add(day, sender, body, hour=None):
        when = at(day, hour if hour is not None else rng.randint(8, 21))
        if when:
            msgs.append((when, sender, body))

    d = lambda day, fmt: date(start.year, start.month, min(day, last.day)).strftime(fmt)  # noqa: E731
    hdfc, sbi, icici, scapia = "VM-HDFCBK-S", "JK-SBICRD-S", "ICICIT-S", "TX-FEDSCP-S"

    # salary, EMI (alert + update), SIP, rent
    add(28, hdfc, f"Update! INR 1,50,000.00 deposited in HDFC Bank A/c XX1111 on {d(28, '%d-%b-%y').upper()} for NEFT "
                  f"Cr-ACME0000001-ACME TECH PRIVATE LIMITED-RAVI KUMAR-ACMEN{ref()}.Avl bal INR 2,{rng.randint(10,99)},000.00.", 11)
    add(15, hdfc, "PAYMENT ALERT! INR 20000.00 deducted from HDFC Bank A/C No 1111 towards RETAIL ASSETS CPC CITY-S "
                  "UMRN: HDFC0000000000000002", 6)
    add(15, hdfc, f"UPDATE: INR 20,000.00 debited from HDFC Bank XX1111 on {d(15, '%d-%b-%y').upper()}. Info: ACH D- "
                  f"RETAIL ASSETS CPC CI-NODN{rng.randint(100000, 999999)}. Avl bal:INR 1,{rng.randint(10, 99)},500.00", 9)
    add(5, hdfc, "PAYMENT ALERT! INR 5000.00 deducted from HDFC Bank A/C No 1111 towards Groww Pay Services Pvt Ltd "
                 f"UMRN: HDFC00000000000000{rng.randint(10, 99)}", 7)
    add(2, hdfc, f"Sent Rs.18000.00 From HDFC Bank A/C *1111 To SUNRISE HOMES On {d(2, '%d/%m/%y')} Ref {ref()} Not You? "
                 "Call 18002586161/SMS BLOCK UPI to 7308080808")

    # card spends (HDFC card 2222, SBI card 3333, ICICI 5555, Scapia)
    for day in rng.sample(range(1, 28), 3):
        amt = rng.choice([980, 1240, 1675, 2130])
        add(day, hdfc, f"Spent Rs.{amt} On HDFC Bank Card 2222 At BIGBASKET On {d(day, '%Y-%m-%d')}:18:22:10.Not You? "
                       "To Block+Reissue Call 18002586161/SMS BLOCK CC 2222 to 7308080808")
    for day in rng.sample(range(1, 28), 4):
        amt = rng.choice([320, 410, 545, 690])
        merchant = rng.choice(["SWIGGY", "ZOMATO", "DOMINOS PIZZA"])
        add(day, hdfc, f"Spent Rs.{amt} On HDFC Bank Card 2222 At {merchant} On {d(day, '%Y-%m-%d')}:20:41:02.Not You? "
                       "To Block+Reissue Call 18002586161/SMS BLOCK CC 2222 to 7308080808", 20)
    for day in rng.sample(range(1, 28), 8):   # tea stall QR on the RuPay card: small UPI spends
        add(day, hdfc, f"Txn Rs.{rng.choice([20, 25, 30, 40])}.00 On HDFC Bank Card 2222 At paytmqr7demo01@ptys by UPI {ref()} "
                       f"On {d(day, '%d-%m')} Not You? Call 18002586161/SMS BLOCK CC 2222 to 7308080808", 16)
    for day in rng.sample(range(1, 28), 3):
        add(day, sbi, f"Rs.{rng.choice([180, 245, 320, 410])}.00 spent on your SBI Credit Card ending 3333 at UBER INDIA on "
                      f"{d(day, '%d/%m/%y')}. Trxn. not done by you? Report at https://sbicard.com/Dispute")
    add(6, icici, f"INR 649.00 spent using ICICI Bank Card XX5555 on {d(6, '%d-%b-%y')} on NETFLIX. Avl Limit: INR 1,95,351.00. "
                  "If not you, call 1800 2662/SMS BLOCK 5555 to 9215676766.")
    add(12, scapia, f"{money(rng.choice([1499, 2799, 3999]))} at Amazon on your Scapia Federal Visa credit card was successful. "
                    "Not you? Go to Scapia support on the app.- Federal Bank")
    add(9, hdfc, f"Sent Rs.799.00 From HDFC Bank A/C *1111 To Airtel Payments Bank On {d(9, '%d/%m/%y')} Ref {ref()} Not You? "
                 "Call 18002586161/SMS BLOCK UPI to 7308080808")
    add(18, hdfc, f"Sent Rs.{rng.choice([360, 540, 880])}.00 From HDFC Bank A/C *1111 To APOLLO PHARMACY On {d(18, '%d/%m/%y')} "
                  f"Ref {ref()} Not You? Call 18002586161/SMS BLOCK UPI to 7308080808")

    # unknown payees -> Needs Review; people -> review with "looks like a person"
    add(20, hdfc, f"Sent Rs.1250.00 From HDFC Bank A/C *1111 To KRISHNA STORES On {d(20, '%d/%m/%y')} Ref {ref()} Not You? "
                  "Call 18002586161/SMS BLOCK UPI to 7308080808")
    add(24, hdfc, f"Sent Rs.3000.00 From HDFC Bank A/C *1111 To PRIYA SHARMA On {d(24, '%d/%m/%y')} Ref {ref()} Not You? "
                  "Call 18002586161/SMS BLOCK UPI to 7308080808")
    add(26, hdfc, f"Credit Alert! Rs.1500.00 credited to HDFC Bank A/c XX1111 on {d(26, '%d-%m-%y')} from VPA amit.sen@okaxis "
                  f"(UPI {ref()})")

    # credit-card bill: CRED debit (with a small discount) + card-side notice -> one transfer, not spending
    bill = rng.choice([8450, 9120, 7680])
    add(22, hdfc, f"Sent Rs.{bill - 12}.00 From HDFC Bank A/C *1111 To CRED Club On {d(22, '%d/%m/%y')} Ref {ref()} Not You? "
                  "Call 18002586161/SMS BLOCK UPI to 7308080808", 9)
    add(22, hdfc, f"DEAR HDFCBANK CARDMEMBER, PAYMENT OF Rs. {bill}.00 RECEIVED TOWARDS YOUR CREDIT CARD ENDING WITH 2222 ON "
                  f"{d(22, '%-d-%-m-%Y')}.YOUR AVAILABLE LIMIT IS RS. 1,00,000.00", 10)

    # things that must NOT be counted
    add(11, "ICICIO-S", f"{rng.randint(100000, 999999)} is the OTP for INR 649.00 transaction on NETFLIX using ICICI Bank "
                        "Credit Card XX5555. OTPs are SECRET. DO NOT disclose")
    add(13, "PHONPE-S", "IRCTC has requested money from you on PhonePe.Rs.845.50 will be debited from your account on "
                        "approving the request - https://phone.pe/PHONPE/demo")
    add(19, hdfc, f"Amount Due Rs.{bill} on HDFC Bank Credit Card 2222. Pay instantly by {d(28, '%d/%b/%Y').upper()} "
                  "via PayZapp > Bill Pay > Credit Card")
    return msgs


def one_offs() -> list[tuple[datetime, str, str]]:
    starts = month_starts()
    m = starts[-2]
    when = lambda day, h=12: datetime(m.year, m.month, day, h, 5)  # noqa: E731
    return [
        (when(7), "VM-HDFCBK-S", f"Sent Rs.2094.95 From HDFC Bank A/C *1111 To IRCTC CF On {when(7):%d/%m/%y} Ref {ref()} "
                                 "Not You? Call 18002586161/SMS BLOCK UPI to 7308080808"),
        (when(9), "TX-FEDSCP-S", "7,786.97 at Agoda on your Scapia Federal Visa credit card was successful. "
                                 "Not you? Go to Scapia support on the app.- Federal Bank"),
        (when(16), "VM-HDFCBK-S", f"Alert! Rs. 499 refunded by AMAZON PAY on {when(16):%d/%b/%Y} & adjusted against "
                                  "HDFC Bank Credit Card 2222 View updated balance here: https://example.invalid"),
    ]


def build_chat_db(messages: list[tuple[datetime, str, str]]) -> None:
    CHAT_DB.unlink(missing_ok=True)
    con = sqlite3.connect(CHAT_DB)
    con.executescript("""
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE message (ROWID INTEGER PRIMARY KEY, guid TEXT, date INTEGER, handle_id INTEGER,
                              is_from_me INTEGER, service TEXT, text TEXT, attributedBody BLOB);
    """)
    handles: dict[str, int] = {}
    for i, (when, sender, body) in enumerate(sorted(messages), start=1):
        if sender not in handles:
            handles[sender] = len(handles) + 1
            con.execute("INSERT INTO handle VALUES (?, ?)", (handles[sender], sender))
        apple_ns = int((when.timestamp() - APPLE_EPOCH) * 1e9)
        con.execute("INSERT INTO message VALUES (?, ?, ?, ?, 0, 'SMS', ?, NULL)",
                    (i, f"demo-{i}", apple_ns, handles[sender], body))
    con.commit()
    con.close()


def build_holdings() -> None:
    funds = [
        ("Bluechip Equity Fund - Direct Growth (demo)", "Equity", "Demo AMC One", 240000, 0.21),
        ("Flexi Cap Fund - Direct Growth (demo)", "Equity", "Demo AMC Two", 310000, 0.17),
        ("Midcap Opportunities Fund - Direct Growth (demo)", "Equity", "Demo AMC Three", 120000, 0.28),
        ("Short Duration Fund - Direct Growth (demo)", "Debt", "Demo AMC One", 150000, 0.07),
        ("Balanced Advantage Fund - Direct Growth (demo)", "Hybrid", "Demo AMC Two", 90000, 0.11),
        ("ELSS Tax Saver - Direct Growth (demo)", "Equity", "Demo AMC Three", 60000, 0.19),
    ]
    schemes, by_cat = [], {}
    for i, (name, catg, amc, invested, gain) in enumerate(funds):
        value = round(invested * (1 + gain), 2)
        nav = round(10 + i * 7.5, 4)
        schemes.append({"scheme_name": name, "scheme_code": f"9{i:05d}", "isin": f"INF000DEMO{i:02d}", "amc": amc,
                        "folio": f"DEMO{i:04d}", "category": catg, "units": round(value / nav, 3),
                        "avg_cost": round(invested / (value / nav), 4), "invested_amount": invested, "current_nav": nav,
                        "current_value": value, "nav_date": date.today().isoformat(), "nav_source": "demo",
                        "pnl": round(value - invested, 2), "pnl_pct": round(gain * 100, 2)})
        c = by_cat.setdefault(catg, {"category": catg, "invested": 0.0, "current_value": 0.0, "count": 0})
        c["invested"] += invested
        c["current_value"] += value
        c["count"] += 1
    for c in by_cat.values():
        c["pnl"] = round(c["current_value"] - c["invested"], 2)
        c["pnl_pct"] = round(c["pnl"] / c["invested"] * 100, 2)
    inv = sum(s["invested_amount"] for s in schemes)
    val = sum(s["current_value"] for s in schemes)
    HOLDINGS.write_text(json.dumps({
        "as_of": date.today().isoformat(), "statement_as_of": date.today().isoformat(),
        "generated_at": datetime.now().isoformat(), "stale_schemes": [], "by_folio": [],
        "totals": {"invested": inv, "current_value": round(val, 2), "pnl": round(val - inv, 2),
                   "pnl_pct": round((val - inv) / inv * 100, 2), "scheme_count": len(schemes), "folio_count": len(schemes)},
        "by_category": list(by_cat.values()), "schemes": schemes,
    }, indent=1))


def demo_env() -> dict:
    env = dict(os.environ)
    env.update(EXPENSE_DATA_DIR=str(DATA), EXPENSE_CONFIG=str(CONFIG), CHAT_DB_PATH=str(CHAT_DB),
               APTIQ_HOLDINGS_PATH=str(HOLDINGS), EXPENSE_DEMO="1")
    return env


def build() -> None:
    shutil.rmtree(DEMO, ignore_errors=True)
    DATA.mkdir(parents=True)
    messages = [m for start in month_starts() for m in sms_for_month(start)] + one_offs()
    build_chat_db(messages)
    build_holdings()
    (DATA / "rules.json").write_text(json.dumps({"overrides": {}, "payees": {
        "SUNRISE HOMES": {"category": "Rent", "name": "Sunrise Homes (rent)"},
        "paytmqr7demo01@ptys": {"category": "Food & Dining", "name": "Tea stall"},
    }}, indent=2))
    # Build in a child process so the ledger modules read the demo paths.
    code = r"""
from app.ledger import ai_suggest, manual, service, store
from datetime import date
manual.add({"date": date.today().replace(day=1).isoformat(), "amount": 600, "direction": "expense",
            "merchant": "Vegetable market", "category": "Groceries", "payment_mode": "Cash", "note": "Paid in cash"})
service.refresh(do_ingest=True, publish=True)
con = store.connect(); con.executescript(ai_suggest.SCHEMA_DDL)
guess = {"KRISHNA STORES": ("Groceries", "medium", "Name suggests a grocery store")}
for p in service.review_queue(80):
    cat, conf, why = guess.get(p["payee_key"], ("FnF Transfer", "low", "Personal UPI, likely friend")
                               if p.get("hint") == "person" else ("Other", "low", "Not enough to go on"))
    con.execute("INSERT OR REPLACE INTO ai_suggestions VALUES (?,?,?,?,?,?,datetime('now'))",
                (p["payee_key"], p["kind"], cat, conf, why, "demo"))
con.commit()
print("demo rows:", len(service.review_queue(80)), "to review")
"""
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=demo_env(), check=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--port", type=int, default=8001)
    args = ap.parse_args()
    build()
    print(f"Demo built in {DEMO}")
    if args.build_only:
        return
    print(f"Open http://localhost:{args.port}  (Ctrl+C to stop)")
    os.execve(sys.executable, [sys.executable, str(ROOT / "serve_for_aptiq.py"), str(args.port)], demo_env())


if __name__ == "__main__":
    main()
