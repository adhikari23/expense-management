"""Phase 1 ledger tests: templates on real SMS formats + linking rules.

    .venv/bin/python -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import unittest
from datetime import datetime

from app.ledger import categories
from app.ledger.config import Account, Config
from app.ledger.link import Event, Linker
from app.ledger.templates import parse

CFG = Config(
    owner_names=["RAVI KUMAR", "RAVIKUMAR"],
    owner_exact_names=["RAVI"],
    accounts=[
        Account("hdfc_sb_1111", "savings", ["HDFCBK"], ["1111"], debit_cards=["4444"]),
        Account("hdfc_cc_2222", "credit_card", ["HDFCBK"], ["2222"]),
        Account("sbi_cc_3333", "credit_card", ["SBICRD"], ["3333"], default=True),
        Account("sbi_sb_5257", "savings", ["CBSSBI"], ["099999"]),
        Account("sbi_home_loan", "loan", ["CBSSBI"], ["99900700001", "700001"],
                emi_mandates=["HDFC0000000000000002"], default_emi=True, default_prepayment=True,
                emi_category="Home Loan EMI", prepayment_category="Home Loan Prepayment"),
        Account("sbi_home_loan_insurance", "loan", ["CBSSBI"], ["99900700002", "700002"],
                emi_mandates=["HDFC0000000000000001"],
                emi_category="Home Loan Insurance EMI", prepayment_category="Home Loan Insurance Prepayment"),
    ],
    card_bill_payees=["CRED", "CRED CLUB"],
    loan_emi_payees=["RETAIL ASSETS CPC"],
    salary_payers=["ACME TECH"],
    own_deposit_payees=["FD THROUGH"],
)


class TemplateTests(unittest.TestCase):
    def check(self, family, body, **expected):
        p = parse(family, body)
        for key, value in expected.items():
            self.assertEqual(getattr(p, key), value, f"{key} for: {body[:60]}")
        return p

    def test_hdfc_card_upi_takes_payee_not_helpline(self):
        p = self.check(
            "HDFCBK",
            "Txn Rs.19.00 On HDFC Bank Card 2222 At paytmqr1abc23@ptys by UPI 100000000001 On 27-09 "
            "Not You? Call 11111586161/SMS BLOCK CC 2222 to 7308080808",
            msg_type="TXN", amount=19.0, direction="debit", account_kind="credit_card", ref="100000000001",
        )
        self.assertEqual(p.counterparty, "paytmqr1abc23@ptys")
        self.assertEqual(p.vpa, "paytmqr1abc23@ptys")

    def test_hdfc_upi_sent_multiline(self):
        self.check(
            "HDFCBK",
            "Sent Rs.28.50\nFrom HDFC Bank A/C *1111\nTo SBIMOPS\nOn 01/01/26\nRef 100000000006\nNot You?",
            msg_type="TXN", amount=28.5, counterparty="SBIMOPS", account_hint="1111",
        )

    def test_hdfc_upi_credit_uses_vpa_as_counterparty(self):
        self.check(
            "HDFCBK",
            "Credit Alert! Rs.150.00 credited to HDFC Bank A/c XX1111 on 30-06-26 from VPA 9000000001@axl (UPI 100000000002)",
            msg_type="TXN", direction="credit", amount=150.0, counterparty="9000000001@axl",
        )

    def test_ach_update_strips_mandate_code_but_keeps_names(self):
        self.check(
            "HDFCBK",
            "UPDATE: INR 15,000.00 debited from HDFC Bank XX1111 on 22-JUN-26. Info: ACH D- Groww Pay Services "
            "P-0000AB1CD234. Avl bal:INR 1,23,456.78",
            amount=15000.0, channel="ACH", counterparty="Groww Pay Services",
        )
        self.check(
            "CBSSBI",
            "Your A/C XXXXX099999 Debited INR 1,000.00 on 01/07/26 -Transferred to Mr. RAVIKUMAR. Avl Balance INR 1,000.50-SBI",
            counterparty="Mr. RAVIKUMAR",
        )

    def test_salary_neft(self):
        self.check(
            "HDFCBK",
            "Update! INR 1,50,000.00 deposited in HDFC Bank A/c XX1111 on 30-JUN-26 for NEFT Cr-INDB0000006-ACME "
            "TECH PRIVATE LIMITED-RAVI KUMAR-INDBH00000000001.Avl bal INR 3,45,678.90.",
            direction="credit", amount=150000.0, counterparty="ACME TECH PRIVATE LIMITED",
        )

    def test_card_payment_notices(self):
        self.check(
            "HDFCBK",
            "DEAR HDFCBANK CARDMEMBER, PAYMENT OF Rs. 12000.00 RECEIVED TOWARDS YOUR CREDIT CARD ENDING WITH 2222 ON 22-6-2026.",
            msg_type="CARD_PAYMENT", amount=12000.0, account_hint="2222",
        )
        self.check(
            "HDFCBK",
            "HDFC Bank Cardmember, Online Payment of Rs.12000 vide Ref# 900000001AbCdEf was credited to your card ending 2222",
            msg_type="CARD_PAYMENT", amount=12000.0,
        )
        self.check("FEDSCP", "Yay! We've received your payment of ₹9,999.50 towards your Scapia Federal credit card. -Federal Bank",
                   msg_type="CARD_PAYMENT", amount=9999.50)

    def test_scapia_spend_without_currency_prefix(self):
        self.check(
            "FEDSCP",
            "7,500.00 at Agoda on your Scapia Federal Visa credit card was successful. And you've earned 10% rewards",
            msg_type="TXN", amount=7500.0, counterparty="Agoda",
        )

    def test_foreign_currency_is_not_rupees(self):
        self.check("ICICIT", "VND 12,34,567.00 spent using ICICI Bank Card XX5555 on 17-Jul-26 on .OP*Overseas Store. Avl Limit: INR 2",
                   msg_type="TXN", currency="VND")

    def test_statement_total_and_date(self):
        self.check(
            "MYSBIC",
            "E-statement of SBI Credit Card ending XX95 dated 05/06/2026 has been mailed. If not received, SMS ENRS to "
            "5676791. Total Amt Due Rs 688; Min Amt Due Rs 200; Payable by 25/06/2026.",
            msg_type="STATEMENT", amount=688.0, statement_date="2026-06-05",
        )

    def test_non_transactions_never_reach_ledger(self):
        cases = {
            ("PHONPE", "IRCTCTOURISM has requested money from you on PhonePe.Rs.999.00 will be debited from your account"): "REQUEST",
            ("HDFCBK", "Amount Due Rs.18000 on HDFC Bank Credit Card 2222. Pay instantly by 09/JAN/2026"): "STATEMENT",
            ("ICICIT", "Pay Total Due of Rs 45,000.00 or Minimum Due Rs 2,250.00 by 02-Nov-25 for ICICI Bank Credit Card XX5555."): "REMINDER",
            ("HDFCBK", "E-Mandate! Rs.1200.00 will be deducted on 02/04/26, 00:00:00 For AIRTEL mandate"): "REMINDER",
            ("ICICIT", "Txn of INR 80,000.00 is declined on ICICI Bank Credit Card XX5555, as the amt exceeds the Per Txn Limit"): "DECLINED",
            ("ICICIO", "123456 is the OTP for INR 2500.00 transaction on Agoda Compa using ICICI Bank Credit Card XX5555."): "NON_BANK",
            ("ICICIP", "Dear Customer transaction of Rs 1124.00 for your ICICIPru policy H1234567 has been initiated"): "CONFIRMATION",
            ("HDFCBK", "Reward Points Credited: 418 To HDFC Bank Credit Card 2222 On 08/FEB/2026"): "INFO",
        }
        for (family, body), expected in cases.items():
            self.assertEqual(parse(family, body).msg_type, expected, body[:60])


def ev(msg_id, ts, family, body, account=None):
    p = parse(family, body)
    if account is None and p.account_kind:
        account = CFG.resolve(family, p.account_hint, p.account_kind)
    return Event(msg_id, datetime.fromisoformat(ts), family, family, body, "text", p, account)


CRED_DEBIT = "Sent Rs.11973.00 From HDFC Bank A/C *1111 To CRED Club On 22/06/26 Ref 111111111111 Not You?"
CARD_A = "DEAR HDFCBANK CARDMEMBER, PAYMENT OF Rs. 12000.00 RECEIVED TOWARDS YOUR CREDIT CARD ENDING WITH 2222 ON 22-6-2026."
CARD_B = "HDFC Bank Cardmember, Online Payment of Rs.12000 vide Ref# 900000001AbCdEf was credited to your card ending 2222 On 22/JUN/2026"


class LinkTests(unittest.TestCase):
    def run_linker(self, *events):
        groups = Linker(CFG, events).run()
        return {g.primary.msg_id: g for g in groups}

    def test_card_bill_is_one_transfer_despite_three_sms_and_cred_discount(self):
        groups = self.run_linker(
            ev(1, "2026-06-22 09:00:00", "HDFCBK", CRED_DEBIT),
            ev(2, "2026-06-22 09:05:00", "HDFCBK", CARD_A),
            ev(3, "2026-06-23 08:00:00", "HDFCBK", CARD_B),
        )
        self.assertEqual(list(groups), [1])
        g = groups[1]
        self.assertEqual((g.kind, g.subkind, g.counter_account), ("transfer", "cc_bill", "hdfc_cc_2222"))
        self.assertEqual(len(g.members), 3)

    def test_card_spend_is_still_an_expense(self):
        groups = self.run_linker(
            ev(1, "2026-06-10 10:00:00", "HDFCBK",
               "Spent Rs.256 On HDFC Bank Card 2222 At FLIPKART1231144 On 2026-06-10:10:00:00.Not You?"),
            ev(2, "2026-06-22 09:00:00", "HDFCBK", CRED_DEBIT),
            ev(3, "2026-06-22 09:05:00", "HDFCBK", CARD_A),
        )
        self.assertEqual(groups[1].kind, "expense")
        self.assertEqual(groups[2].kind, "transfer")

    def test_ach_alert_and_update_are_one_emi(self):
        groups = self.run_linker(
            ev(1, "2026-06-15 06:00:00", "HDFCBK",
               "PAYMENT ALERT! INR 20000.00 deducted from HDFC Bank A/C No 1111 towards RETAIL ASSETS CPC CITY-S UMRN: HDFC0000000000000002"),
            ev(2, "2026-06-16 09:00:00", "HDFCBK",
               "UPDATE: INR 20,000.00 debited from HDFC Bank XX1111 on 15-JUN-26. Info: ACH D- RETAIL ASSETS CPC CI-NODNABCDE260. Avl bal:INR 2,34,567.89"),
        )
        self.assertEqual(list(groups), [1])
        self.assertEqual((groups[1].kind, groups[1].category), ("expense", "Home Loan EMI"))

    def test_loan_credit_funded_from_savings_is_one_prepayment(self):
        groups = self.run_linker(
            ev(1, "2026-06-15 10:00:00", "CBSSBI",
               "Your A/C XXXXX099999 Debited INR 12,000.00 on 15/06/26 -Transferred to Mr. RAVIKUMAR. Avl Balance INR 4,000.25-SBI"),
            ev(2, "2026-06-15 10:00:05", "CBSSBI",
               "Dear Customer, Your loan A/C XXXXX700001 Credited INR 12,000.00 on 15/06/26,deposit transfer from Mr. RAVIKUMAR.-SBI. Download YONO"),
        )
        self.assertEqual(list(groups), [1])
        self.assertEqual((groups[1].kind, groups[1].category), ("expense", "Home Loan Prepayment"))

    def test_two_loans_split_by_mandate_and_account(self):
        groups = self.run_linker(
            ev(1, "2026-06-15 06:00:00", "HDFCBK",
               "PAYMENT ALERT! INR 600.00 deducted from HDFC Bank A/C No 1111 towards RETAIL ASSETS CPC CITY-S UMRN: HDFC0000000000000001"),
            ev(2, "2026-07-15 09:00:00", "HDFCBK",
               "UPDATE: INR 20,000.00 debited from HDFC Bank XX1111 on 15-JUL-26. Info: ACH D- RETAIL ASSETS CPC CI-NODNABCDE260. Avl bal:INR 5"),
            ev(3, "2026-06-15 10:00:05", "CBSSBI",
               "Dear Customer, Your loan A/C XXXXX700002 Credited INR 8,000.00 on 15/06/26,deposit transfer from Mr. RAVIKUMAR.-SBI."),
        )
        self.assertEqual((groups[1].category, groups[1].counter_account), ("Home Loan Insurance EMI", "sbi_home_loan_insurance"))
        # update SMS without UMRN falls back to the main loan
        self.assertEqual((groups[2].category, groups[2].counter_account), ("Home Loan EMI", "sbi_home_loan"))
        self.assertEqual(groups[3].category, "Home Loan Insurance Prepayment")

    def test_own_name_hop_then_prepayment_counted_once(self):
        groups = self.run_linker(
            ev(1, "2026-04-15 10:00:00", "HDFCBK",
               "Sent Rs.60000.00 From HDFC Bank A/C *1111 To RAVI  KUMAR On 15/04/26 Ref 100000000007 Not You?"),
            ev(2, "2026-04-15 12:00:00", "CBSSBI",
               "Your A/C XXXXX099999 Debited INR 60,000.00 on 15/04/26 -Transferred to Mr. RAVIKUMAR. Avl Balance INR 1.00-SBI"),
        )
        self.assertEqual((groups[1].kind, groups[1].subkind), ("transfer", "self"))
        self.assertEqual((groups[2].kind, groups[2].category), ("expense", "Home Loan Prepayment"))

    def test_neft_without_payee_is_prepayment(self):
        groups = self.run_linker(ev(1, "2026-06-01 10:00:00", "HDFCBK",
            "Amt Deducted! Rs.60,000.00 from your HDFC Bank A/c XX1111 for NEFT transaction via HDFC Bank Online Banking"))
        self.assertEqual((groups[1].kind, groups[1].category), ("expense", "Home Loan Prepayment"))

    def test_salary(self):
        groups = self.run_linker(ev(1, "2026-06-30 10:00:00", "HDFCBK",
            "Update! INR 1,50,000.00 deposited in HDFC Bank A/c XX1111 on 30-JUN-26 for NEFT Cr-INDB0000006-ACME "
            "TECH PRIVATE LIMITED-RAVI KUMAR-INDBH00000000001.Avl bal INR 3,45,678.90."))
        self.assertEqual((groups[1].kind, groups[1].category), ("income", "Salary"))

    def test_same_amount_different_payments_stay_separate(self):
        # Regression: a generic "same amount within 1h" rule merged these in real data.
        groups = self.run_linker(
            ev(1, "2026-06-01 09:35:31", "HDFCBK",
               "Sent Rs.1000.00 From HDFC Bank A/C *1111 To AMIT SEN On 01/06/26 Ref 100000000003 Not You?"),
            ev(2, "2026-06-01 10:12:53", "HDFCBK",
               "Withdrawn Rs.1000 From HDFC Bank Card x4444 At MAIN ROAD On 2026-06-01:10:12:48 Bal Rs.1"),
        )
        self.assertEqual(sorted(groups), [1, 2])
        self.assertEqual(groups[2].category, "Cash Withdrawal")

    def test_upi_reversal_on_card_is_refund(self):
        groups = self.run_linker(
            ev(1, "2026-06-10 10:00:00", "HDFCBK",
               "Txn Rs.146.00 On HDFC Bank Card 2222 At q100000001@ybl by UPI 100000000005 On 10-06 Not You?"),
            ev(2, "2026-06-11 10:00:00", "HDFCBK",
               "Your UPI transaction of Rs 146.00 has been reversed in your HDFC Bank Credit Card due to technical problem (UPI Ref no. 100000000005)"),
        )
        self.assertEqual(groups[1].kind, "expense")
        self.assertEqual(groups[2].kind, "refund")
        self.assertEqual(groups[2].primary.account, "hdfc_cc_2222")


class CategoryTests(unittest.TestCase):
    def setUp(self):
        self.rules = {"payees": {}, "overrides": {}}

    def cat(self, *events, account_type=None):
        groups = Linker(CFG, events).run()
        g = groups[0]
        acct = CFG.account(g.primary.account)
        return categories.categorize(g, account_type or (acct.type if acct else ""), self.rules)

    def test_payee_key_normalises_store_codes_and_gateways(self):
        self.assertEqual(categories.payee_key("FLIPKART1231144", None), "FLIPKART")
        self.assertEqual(categories.payee_key("PPSL*Agoda Comp", None), "AGODA COMP")
        self.assertEqual(categories.payee_key("a/c **1349", None), "A/C 1349")
        self.assertEqual(categories.payee_key("anything", "Q123@YBL"), "q123@ybl")

    def test_merchant_keyword_not_sms_boilerplate(self):
        cat, reason, _ = self.cat(ev(1, "2026-06-10 10:00:00", "HDFCBK",
            "Spent Rs.256 On HDFC Bank Card 2222 At FLIPKART1231144 On 2026-06-10:10:00:00.Not You? SMS BLOCK UPI"))
        self.assertEqual((cat, reason), ("Shopping", "keyword:flipkart"))

    def test_small_unknown_upi_is_upi_spend_and_500_plus_needs_review(self):
        qr = ev(1, "2026-06-10 10:00:00", "HDFCBK",
                "Txn Rs.19.00 On HDFC Bank Card 2222 At paytmqr1abc23@ptys by UPI 100000000001 On 27-09 Not You?")
        self.assertEqual(self.cat(qr)[:2], (categories.UPI_SPEND, "small_upi:no_match"))
        just_under = ev(2, "2026-06-10 10:00:00", "HDFCBK",
                        "Sent Rs.499.00 From HDFC Bank A/C *1111 To AMIT SEN On 01/06/26 Ref 100000000003 Not You?")
        self.assertEqual(self.cat(just_under)[0], categories.UPI_SPEND)
        person = ev(3, "2026-06-10 10:00:00", "HDFCBK",
                    "Sent Rs.500.00 From HDFC Bank A/C *1111 To AMIT SEN On 01/06/26 Ref 100000000004 Not You?")
        self.assertEqual(self.cat(person)[:2], (categories.NEEDS_REVIEW, "review:person"))

    def test_unknown_card_spend_needs_review_even_if_small(self):
        card = ev(1, "2026-06-10 10:00:00", "SBICRD",
                  "Rs.120.00 spent on your SBI Credit Card ending 3333 at ASSPL on 08/06/26. Trxn. not done by you?")
        self.assertEqual(self.cat(card)[0], categories.NEEDS_REVIEW)

    def test_unknown_income_needs_review(self):
        credit = ev(1, "2026-06-10 10:00:00", "HDFCBK",
                    "Credit Alert! Rs.150.00 credited to HDFC Bank A/c XX1111 on 30-06-26 from VPA 9000000001@axl (UPI 100000000002)")
        self.assertEqual(self.cat(credit)[0], categories.NEEDS_REVIEW)
        self.rules["payees"]["9000000001@axl"] = {"category": categories.FNF}
        self.assertEqual(self.cat(credit)[0], categories.FNF)

    def test_old_family_and_friends_names_map_to_fnf(self):
        credit = ev(1, "2026-06-10 10:00:00", "HDFCBK",
                    "Credit Alert! Rs.150.00 credited to HDFC Bank A/c XX1111 on 30-06-26 from VPA 9000000001@axl (UPI 100000000002)")
        for old in ("Family", "Friends Transfer"):
            self.rules["payees"]["9000000001@axl"] = {"category": old}
            self.assertEqual(self.cat(credit)[0], "FnF Transfer")
        self.assertNotIn("Family", categories.ALL_CATEGORIES)
        self.assertNotIn("Friends Transfer", categories.ALL_CATEGORIES)

    def test_payee_tag_and_txn_override(self):
        qr = ev(1, "2026-06-10 10:00:00", "HDFCBK",
                "Txn Rs.19.00 On HDFC Bank Card 2222 At paytmqr1abc23@ptys by UPI 100000000001 On 27-09 Not You?")
        self.rules["payees"]["paytmqr1abc23@ptys"] = {"category": "Food & Dining", "name": "Tea stall"}
        self.assertEqual(self.cat(qr), ("Food & Dining", "user:payee", "Tea stall"))
        self.rules["overrides"]["1"] = {"category": "Groceries"}
        self.assertEqual(self.cat(qr)[:2], ("Groceries", "user:txn"))

    def test_your_recategorisation_beats_rule_categories(self):
        emi = ev(1, "2026-06-15 06:00:00", "HDFCBK",
                 "PAYMENT ALERT! INR 20000.00 deducted from HDFC Bank A/C No 1111 towards RETAIL ASSETS CPC CITY-S UMRN: HDFC0000000000000002")
        self.assertEqual(self.cat(emi)[0], "Home Loan EMI")
        self.rules["payees"]["RETAIL ASSETS CPC CITY S"] = {"category": "Other"}
        self.assertEqual(self.cat(emi)[:2], ("Other", "user:payee"))

    def test_tags_never_turn_a_card_bill_payment_into_spend(self):
        self.rules["payees"]["CRED CLUB"] = {"category": "Shopping"}
        groups = Linker(CFG, [ev(1, "2026-06-22 09:00:00", "HDFCBK", CRED_DEBIT),
                              ev(2, "2026-06-22 09:05:00", "HDFCBK", CARD_A)]).run()
        self.assertEqual(categories.categorize(groups[0], "savings", self.rules)[0], "Credit Card Bill Payment")


class ManualTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from app.ledger import manual
        self.manual = manual
        self._orig = manual.DB_PATH
        manual.DB_PATH = Path(tempfile.mkdtemp()) / "ledger.db"

    def tearDown(self):
        self.manual.DB_PATH = self._orig

    def test_add_edit_delete_and_csv(self):
        m = self.manual
        row = m.add({"date": "2026-10-05", "amount": "250", "direction": "credit",
                     "merchant": "Rahul", "category": "Friends Transfer"})  # old name -> FnF
        self.assertEqual((row["direction"], row["amount"], len(row["id"])), ("income", 250.0, 16))
        self.assertEqual(row["category"], "FnF Transfer")
        m.update(row["id"], {"amount": 300, "category": "Family"})
        csv_row = m.csv_rows()[0]
        self.assertEqual((csv_row["amount"], csv_row["category"], csv_row["month"]), ("300.00", "FnF Transfer", "2026-10"))
        # every rebuild copies manual rows into the ledger table
        from app.ledger import store
        con = store.connect(m.DB_PATH)
        self.assertEqual(m.copy_into_ledger(con), 1)
        got = con.execute("SELECT kind, category, amount, month FROM ledger").fetchone()
        self.assertEqual(tuple(got), ("income", "FnF Transfer", 300.0, "2026-10"))
        con.close()
        self.assertTrue(m.delete(row["id"]))
        self.assertEqual(m.load(), [])

    def test_rejects_bad_input(self):
        for bad in ({"date": "2026-10-05", "amount": 0}, {"date": "05/10/2026", "amount": 5},
                    {"date": "2026-10-05", "amount": 5, "category": "Bogus"}):
            with self.assertRaises(ValueError):
                self.manual.add(bad)


class AiSuggestTests(unittest.TestCase):
    """AI is optional: failures must return cached suggestions + a note, never raise."""

    def setUp(self):
        import json
        import subprocess
        import tempfile
        import types
        from pathlib import Path
        from app.ledger import ai_suggest, store
        self.ai, self.json, self.types, self.subprocess = ai_suggest, json, types, subprocess
        db = Path(tempfile.mkdtemp()) / "ledger.db"
        self._patches = [(ai_suggest.store, "connect", ai_suggest.store.connect),
                         (ai_suggest.service, "review_queue", ai_suggest.service.review_queue),
                         (ai_suggest.subprocess, "run", ai_suggest.subprocess.run),
                         (ai_suggest, "_claude_bin", ai_suggest._claude_bin)]
        real_connect = store.connect
        ai_suggest.store.connect = lambda *a, **k: real_connect(db)
        ai_suggest._claude_bin = lambda: "/fake/claude"
        self.payees = [
            {"payee_key": "GRAND PALACE H", "kind": "expense", "n": 1, "total": 3938.0, "name": "GRAND PALACE H",
             "channel": "CARD", "hint": "merchant"},
            {"payee_key": "ROHAN DAS", "kind": "expense", "n": 1, "total": 10000.0, "name": "ROHAN DAS",
             "channel": "UPI", "hint": "person"},
        ]
        ai_suggest.service.review_queue = lambda limit, month=None: list(self.payees)
        self.calls = []

    def tearDown(self):
        for obj, name, val in self._patches:
            setattr(obj, name, val)

    def _cli(self, category=None, *, returncode=0, result=None, raise_=None):
        """Fake `claude -p` process."""
        json, types = self.json, self.types

        def run(cmd, input=None, **kw):
            self.calls.append(cmd)
            if raise_:
                raise raise_
            if category is None:
                return types.SimpleNamespace(returncode=returncode, stderr="",
                                             stdout=json.dumps({"is_error": True, "result": result}))
            items = json.loads(input.split("\n\n", 1)[1])
            out = {"suggestions": [{"payee_key": it["payee_key"], "category": category, "confidence": "high",
                                    "reason": "t"} for it in items]}
            return types.SimpleNamespace(returncode=0, stderr="",
                                         stdout=json.dumps({"is_error": False, "structured_output": out}))
        self.ai.subprocess.run = run

    def test_uses_claude_code_cli_without_tools(self):
        self._cli("Travel")
        rows, err = self.ai.suggestions()
        self.assertEqual((len(rows), err), (2, None))
        cmd = self.calls[0]
        self.assertEqual(cmd[:2], ["/fake/claude", "-p"])
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")  # no file/shell/web access
        self.assertIn("--json-schema", cmd)
        self.assertIn("--no-session-persistence", cmd)
        self.assertEqual(self.ai.suggestions()[0], rows)  # second call served from cache
        self.assertEqual(len(self.calls), 1)

    def test_failures_keep_cache_and_return_a_note(self):
        self._cli("Travel")
        self.ai.suggestions()
        self.payees.append({"payee_key": "NEW SHOP", "kind": "expense", "n": 1, "total": 900.0, "name": "NEW SHOP",
                            "channel": "CARD", "hint": "merchant"})
        cases = [
            (dict(returncode=1, result="Invalid API key · Please run /login"), "signed in"),
            (dict(returncode=1, result="Claude usage limit reached"), "limit"),
            (dict(raise_=self.subprocess.TimeoutExpired("claude", 120)), "too long"),
            (dict(raise_=FileNotFoundError(2, "No such file")), "Claude Code"),
            (dict(raise_=RuntimeError("boom")), "failed"),
        ]
        for kwargs, expected in cases:
            self._cli(None, **kwargs)
            rows, err = self.ai.suggestions()
            self.assertEqual(len(rows), 2, "cached suggestions must still be returned")
            self.assertIn(expected, err)

    def test_missing_cli_is_a_note(self):
        self.ai._claude_bin = self._patches[3][2]
        self.ai.shutil.which = lambda _: None
        old = self.ai.CLAUDE_CANDIDATES
        self.ai.CLAUDE_CANDIDATES = ["/nope/claude"]
        try:
            rows, err = self.ai.suggestions()
            self.assertEqual(rows, [])
            self.assertIn("Claude Code", err)
        finally:
            self.ai.CLAUDE_CANDIDATES = old
            import shutil
            self.ai.shutil.which = shutil.which

    def test_wrong_direction_suggestions_are_dropped(self):
        self._cli("Salary")  # income-only category for money-out payees
        self.assertEqual(self.ai.suggestions()[0], [])

    def test_upi_spend_is_never_suggested(self):
        self._cli("UPI Spend")  # rule-only category (unknown UPI under ₹500)
        self.assertEqual(self.ai.suggestions()[0], [])
        schema_enum = self.ai._schema()["properties"]["suggestions"]["items"]["properties"]["category"]["enum"]
        self.assertNotIn("UPI Spend", schema_enum)
        self.assertNotIn("Needs Review", schema_enum)


if __name__ == "__main__":
    unittest.main()
