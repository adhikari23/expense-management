"""Per-bank SMS templates.

Each template is a regex over the whitespace-normalised body plus fixed facts
(message type, direction, channel, which kind of own account it touches).
`parse()` returns a Parsed record, or a non-transaction type for messages that
must never reach the ledger (reminders, requests, statements, promos...).

Message types:
  TXN           money moved on one of your accounts / cards
  CARD_PAYMENT  card-side "payment received" for a credit-card bill
  REFUND        reversal / refund credited back
  CONFIRMATION  second message about a txn already reported elsewhere
                (NEFT beneficiary notice, insurer / MF receipts)
  STATEMENT     card statement; total due kept for reconciliation
  REMINDER, REQUEST, MANDATE, DECLINED, INFO, OTP  -> ignored
  UNPARSED      bank message that looks like money moved but no template matched
  NON_BANK      sender is not a bank / card issuer
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

AMT = r"(?P<amt>[0-9][0-9,]*(?:\.\d{1,2})?)"

BANK_FAMILIES = {
    "HDFCBK", "HDFCBN", "ICICIT", "ICICIB", "SBICRD", "MYSBIC", "SBICHR", "FEDSCP", "FEDBNK",
    "CBSSBI", "SBIBNK", "SBIUPI", "SBIINB", "SBIGEN", "AXISBK", "KOTAKB",
}
# Merchants / insurers / AMCs / wallets that send receipts for payments already
# reported by a bank. Their amounts are never added to the ledger.
CONFIRMATION_FAMILIES = {
    "ICICIP", "AXISMF", "KOTAKL", "HDFCMF", "SBIMFD", "NIMFND", "MFCENT", "CARETX",
}
REQUEST_FAMILIES = {"PHONPE", "PAYTM", "GPAY"}


@dataclass
class Parsed:
    msg_type: str
    template: Optional[str] = None
    direction: Optional[str] = None  # debit | credit (relative to `account_kind`)
    amount: Optional[float] = None
    currency: str = "INR"
    account_kind: Optional[str] = None  # savings | credit_card | loan
    account_hint: Optional[str] = None  # digits as printed in the SMS
    channel: Optional[str] = None
    counterparty: Optional[str] = None
    vpa: Optional[str] = None
    ref: Optional[str] = None
    statement_date: Optional[str] = None  # YYYY-MM-DD when the SMS states it
    mandate: Optional[str] = None  # NACH/ACH mandate reference (UMRN)


@dataclass
class Template:
    id: str
    families: tuple[str, ...]
    pattern: re.Pattern
    msg_type: str
    direction: Optional[str] = None
    account_kind: Optional[str] = None
    channel: Optional[str] = None


def T(id, families, pattern, msg_type, direction=None, account_kind=None, channel=None):
    return Template(id, tuple(families), re.compile(pattern, re.IGNORECASE), msg_type, direction, account_kind, channel)


HDFC = ("HDFCBK", "HDFCBN")
ICICI = ("ICICIT", "ICICIB")
SBICARD = ("SBICRD", "MYSBIC")
SCAPIA = ("FEDSCP",)
SBI = ("CBSSBI", "SBIUPI", "SBIINB", "SBIBNK")

TEMPLATES: list[Template] = [
    # ---------------- HDFC savings account ----------------
    T("hdfc_upi_sent", HDFC,
      rf"^Sent Rs\.?\s?{AMT} From HDFC Bank A/C \*?(?P<acct>\d+) To (?P<payee>.+?) On \S+ Ref (?P<ref>\d+)",
      "TXN", "debit", "savings", "UPI"),
    T("hdfc_upi_mandate", HDFC,
      rf"^UPI Mandate: Sent Rs\.?\s?{AMT} from HDFC Bank A/c \*?(?P<acct>\d+) To (?P<payee>.+?) \d\d/\d\d/\d\d Ref (?P<ref>\d+)",
      "TXN", "debit", "savings", "UPI_MANDATE"),
    T("hdfc_upi_credit", HDFC,
      rf"^Credit Alert! Rs\.?\s?{AMT} credited to HDFC Bank A/c XX(?P<acct>\d+) on \S+ from VPA (?P<vpa>\S+) \(UPI (?P<ref>\d+)\)",
      "TXN", "credit", "savings", "UPI"),
    T("hdfc_ach_alert", HDFC,
      rf"^PAYMENT ALERT! INR {AMT} deducted from HDFC Bank A/C No (?P<acct>\d+) towards (?P<payee>.+?) UMRN: ?(?P<mandate>\w+)",
      "TXN", "debit", "savings", "ACH"),
    T("hdfc_debit_update", HDFC,
      rf"^UPDATE: INR {AMT} debited from HDFC Bank XX(?P<acct>\d+) on \S+ Info: (?P<payee>.+?)\.? Avl bal",
      "TXN", "debit", "savings", None),
    T("hdfc_neft_in", HDFC,
      rf"^Update! INR {AMT} deposited in HDFC Bank A/c XX(?P<acct>\d+) on \S+ for (?P<channel>NEFT|RTGS|IMPS) Cr-[A-Z0-9]+-(?P<payee>.+?)-(?P<beneficiary>[^-]+)-(?P<ref>[A-Z0-9]+)\.?\s?Avl",
      "TXN", "credit", "savings", None),
    T("hdfc_deposit_generic", HDFC,
      rf"^Update! INR {AMT} deposited in HDFC Bank A/c XX(?P<acct>\d+) on \S+ for (?P<payee>.+?)\.\s?Avl",
      "TXN", "credit", "savings", None),
    T("hdfc_imps_in", HDFC,
      rf"^Received! INR {AMT} in HDFC Bank A/c xx(?P<acct>\d+) On \S+ For (?P<channel>IMPS|NEFT|UPI) -?(?P<payee>.+?)- ?(?P<ref>\d+)",
      "TXN", "credit", "savings", None),
    T("hdfc_ac_to_ac", HDFC,
      rf"^HDFC Bank: ?Rs\.? {AMT} debited from a/c \*(?P<acct>\d+) on \S+ to (?P<payee>a/c \*+\d+|VPA \S+|.+?) \(UPI Ref No\.? (?P<ref>\d+)\)",
      "TXN", "debit", "savings", "UPI"),
    T("hdfc_neft_out", HDFC,
      rf"^Amt Deducted! Rs\.?{AMT} from your HDFC Bank A/c XX(?P<acct>\d+) for (?P<channel>NEFT|RTGS|IMPS) (?:transaction|txn)",
      "TXN", "debit", "savings", None),
    T("hdfc_netbanking_payment", HDFC,
      rf"^Payment Successful! Rs\.? {AMT} from A/c \**(?P<acct>\d+) to (?P<payee>.+?) via HDFC Bank NetBanking",
      "TXN", "debit", "savings", "NETBANKING"),
    T("hdfc_dc_spent", HDFC,
      rf"^Spent Rs\.?{AMT} From HDFC Bank Card x(?P<acct>\d+) At (?P<payee>.+?) On \d{{4}}-",
      "TXN", "debit", "savings", "DEBIT_CARD"),
    T("hdfc_dc_withdrawal", HDFC,
      rf"^Withdrawn Rs\.?{AMT} From HDFC Bank Card x(?P<acct>\d+) At (?P<payee>.+?) On \d{{4}}-",
      "TXN", "debit", "savings", "ATM"),
    T("hdfc_ppf_transfer", HDFC,
      rf"^Alert! Rs\.? {AMT} transferred to your (?P<payee>PPF/SSY A/c No\. \S+) via HDFC Bank",
      "TXN", "debit", "savings", "NETBANKING"),
    T("hdfc_neft_beneficiary", HDFC,
      rf"^HDFC Bank ?: (?P<channel>NEFT|RTGS|IMPS) money transfer Txn No (?P<ref>\S+) for Rs (?:INR )?{AMT} has been credited to (?P<payee>.+?) on",
      "CONFIRMATION", "debit", "savings", None),
    # ---------------- HDFC credit cards ----------------
    T("hdfc_cc_upi", HDFC,
      rf"^Txn Rs\.?{AMT} On HDFC Bank Card (?P<acct>\d+) At (?P<payee>\S+) by UPI (?P<ref>\d+)",
      "TXN", "debit", "credit_card", "UPI"),
    T("hdfc_cc_spent", HDFC,
      rf"^Spent Rs\.?{AMT} On HDFC Bank Card (?P<acct>\d+) At (?P<payee>.+?) On \d{{4}}-",
      "TXN", "debit", "credit_card", "CARD"),
    T("hdfc_cc_payment_a", HDFC,
      rf"^DEAR HDFCBANK CARDMEMBER, PAYMENT OF Rs\.? {AMT} RECEIVED TOWARDS YOUR CREDIT CARD ENDING WITH (?P<acct>\d+)",
      "CARD_PAYMENT", "credit", "credit_card", None),
    T("hdfc_cc_payment_b", HDFC,
      rf"^HDFC Bank Cardmember, (?:Online )?Payment of Rs\.? ?{AMT}(?: vide Ref# (?P<ref>\S+))? was credited to your card ending (?P<acct>\d+)",
      "CARD_PAYMENT", "credit", "credit_card", None),
    T("hdfc_cc_upi_reversal", HDFC,
      rf"^Your UPI transaction of Rs\.? ?{AMT} has been reversed in your HDFC Bank Credit Card .*?UPI Ref no\.? (?P<ref>\d+)",
      "REFUND", "credit", "credit_card", "UPI"),
    T("hdfc_cc_reversed", HDFC,
      rf"^Transaction Reversed! ?On HDFC Bank CREDIT Card xx(?P<acct>\d+) Amt: Rs\.?{AMT} By (?P<payee>.+?) On",
      "REFUND", "credit", "credit_card", "CARD"),
    T("hdfc_cc_refund", HDFC,
      rf"^Alert! Rs\.? {AMT} refunded by (?P<payee>.+?) on \S+ & adjusted against HDFC Bank Credit Card (?P<acct>\d+)",
      "REFUND", "credit", "credit_card", "CARD"),
    T("hdfc_cc_amount_due", HDFC,
      rf"^(?:Amount Due|Amt Due:) Rs\.? ?{AMT} on HDFC Bank Credit Card (?P<acct>\d+)",
      "STATEMENT", None, "credit_card"),
    T("hdfc_cc_statement", HDFC,
      rf"^HDFC Bank Credit Card XX(?P<acct>\d+) Statement: Total due: Rs\.?{AMT}",
      "STATEMENT", None, "credit_card"),
    # ---------------- ICICI credit card ----------------
    T("icici_cc_spent_a", ICICI,
      rf"^(?P<ccy>[A-Z]{{3}}) ?{AMT} spent using ICICI Bank Card XX(?P<acct>\d+) on \S+ on (?P<payee>.+?)\. Avl",
      "TXN", "debit", "credit_card", "CARD"),
    T("icici_cc_spent_b", ICICI,
      rf"^Rs\.? ?{AMT} spent on ICICI Bank Card XX(?P<acct>\d+) on \S+ at (?P<payee>.+?)\. Avl",
      "TXN", "debit", "credit_card", "CARD"),
    T("icici_cc_payment", ICICI,
      rf"^Payment of Rs\.? ?{AMT} has been received on your ICICI Bank Credit Card XX(?P<acct>\d+)",
      "CARD_PAYMENT", "credit", "credit_card", "BBPS"),
    T("icici_cc_refund", ICICI,
      rf"^ICICI Bank Credit Card XX(?P<acct>\d+) credited/refunded with Rs\.? ?{AMT}",
      "REFUND", "credit", "credit_card", "CARD"),
    T("icici_cc_statement", ICICI,
      rf"^ICICI Bank Credit Card XX(?P<acct>\d+) Statement is sent .*? Total of Rs\.? ?{AMT}",
      "STATEMENT", None, "credit_card"),
    # ---------------- SBI Card ----------------
    T("sbicard_spent", SBICARD,
      rf"^(?:Rs\.?|(?P<ccy>[A-Z]{{3}})) ?{AMT} spent on your SBI Credit Card ending (?P<acct>\d+) at (?P<payee>.+?) on \d",
      "TXN", "debit", "credit_card", "CARD"),
    T("sbicard_payment", SBICARD,
      rf"^We have received payment of Rs\.? ?{AMT} via (?P<channel>\w+) & the same has been credited to your SBI Credit Card",
      "CARD_PAYMENT", "credit", "credit_card", None),
    T("sbicard_reversal", SBICARD,
      rf"^Rs\.? ?{AMT} has been credited to your SBI Credit Card x*(?P<acct>\d+), towards reversal/cashback from (?P<payee>.+?) for trxn",
      "REFUND", "credit", "credit_card", "CARD"),
    T("sbicard_statement", SBICARD,
      rf"^E-statement of SBI Credit Card ending XX(?P<acct>\d+) dated (?P<stmt_date>\d\d/\d\d/\d{{4}}).*?Total Amt Due Rs\.? ?{AMT}",
      "STATEMENT", None, "credit_card"),
    # ---------------- Scapia (Federal) credit card ----------------
    T("scapia_reversal", SCAPIA,
      rf"^Hi! Your txn (?:of|for) ₹?{AMT} at (?P<payee>.+?) on your Scapia Federal \w+ credit card has been reversed",
      "REFUND", "credit", "credit_card", "CARD"),
    T("scapia_refund", SCAPIA,
      rf"received a refund of ₹?{AMT} from (?P<payee>.+?) on your Scapia Federal",
      "REFUND", "credit", "credit_card", "CARD"),
    T("scapia_spent", SCAPIA,
      rf"^(?:Hi! )?(?:Your txn of )?₹?{AMT} at (?P<payee>.+?) on your Scapia Federal \w+ credit card (?:was successful|earned you)",
      "TXN", "debit", "credit_card", "CARD"),
    T("scapia_payment", SCAPIA,
      rf"(?:received your payment of )?₹?{AMT} towards your Scapia Federal credit card",
      "CARD_PAYMENT", "credit", "credit_card", None),
    # ---------------- SBI savings / loan ----------------
    T("sbi_ac_debit", SBI,
      rf"^Your A/C X+(?P<acct>\d+) Debited INR {AMT} on \S+ ?-?(?:Transferred to|trf to) (?P<payee>.+?)\.? Avl Bal",
      "TXN", "debit", "savings", "TRANSFER"),
    T("sbi_upi_debit", SBI,
      rf"^Dear UPI user A/C X(?P<acct>\d+) debited by {AMT} on date \S+ trf to (?P<payee>.+?) Refno (?P<ref>\d+)",
      "TXN", "debit", "savings", "UPI"),
    T("sbi_ac_credit", SBI,
      rf"^Dear Customer, Your A/C X+(?P<acct>\d+) (?:has been )?credited (?:by|with)? ?(?:INR|Rs\.?) ?{AMT} on \S+ ?(?:by|from|-)? ?(?P<payee>.+?)(?:\.|Avl|-SBI)",
      "TXN", "credit", "savings", None),
    T("sbi_loan_credit", SBI,
      rf"^Dear Customer, Your loan A/C X+(?P<acct>\d+) Credited INR {AMT} on [^,]+, ?(?P<note>deposit transfer|transfer|\w+) from (?P<payee>.+?)\.?-SBI",
      "TXN", "credit", "loan", "TRANSFER"),
    T("sbi_loan_charge", SBI,
      rf"^Your Loan X+(?P<acct>\d+) debited by Rs\.? ?{AMT} on \S+\. Current o/s",
      "INFO", "debit", "loan"),
    T("sbi_home_loan_disbursal", SBI,
      rf"^Dear Mr/Ms \w+, Home Loan of Rs\.? ?{AMT} disbursed",
      "INFO", None, "loan"),
]

# ---------------- Non-transaction messages (checked after templates) ----------------
NON_TXN_RULES: list[tuple[str, re.Pattern]] = [
    ("OTP", re.compile(r"\bOTP\b|one time password|verification code|login code", re.I)),
    ("DECLINED", re.compile(r"declined|has been rejected|failed due to|transaction failed", re.I)),
    ("REQUEST", re.compile(r"requested money|has requested|collect request|autopay request|sent you an autopay|payment request", re.I)),
    ("MANDATE", re.compile(r"mandate registration|nach mandate|mandate set|mandate (?:is )?successfully|upi-mandate|e-mandate is|mandate.*(?:created|cancelled|revoked|paused)", re.I)),
    ("REMINDER", re.compile(
        r"amount due|amt due|total due|minimum due|min\.? ?due|is due|will be (?:debited|deducted)|payable by|"
        r"pay (?:instantly|now)|due date|overdue|e-mandate! rs", re.I)),
    ("STATEMENT", re.compile(r"statement (?:is )?generated|statement for|statement is (?:sent|here)|e-statement", re.I)),
    ("INFO", re.compile(
        r"reward points|credit limit|eligible for|offer|congratulations|hurry|last chance|flexipay|convert .*emi|"
        r"loan facility|upi registration|enabled|disabled|usage settings|activate|activation|maintenance|"
        r"beware|fraudster|logged in|token registration|inactive|important update|voucher|annual fee waiver|"
        r"\bpin\b|cyber|has been initiated", re.I)),
]

MONEY_HINT = re.compile(r"(?:rs\.?|inr|₹)\s?[0-9]", re.I)
MOVE_HINT = re.compile(r"debited|credited|spent|sent|deducted|received|paid|withdrawn|deposited|transferred|txn", re.I)


def normalise(body: str) -> str:
    return " ".join((body or "").split())


def _amount(text: str) -> Optional[float]:
    try:
        return float(text.replace(",", ""))
    except (TypeError, ValueError):
        return None


def _iso_date(ddmmyyyy: Optional[str]) -> Optional[str]:
    if not ddmmyyyy:
        return None
    d, m, y = ddmmyyyy.split("/")
    return f"{y}-{m}-{d}"


def _clean_payee(payee: Optional[str]) -> Optional[str]:
    if not payee:
        return None
    p = payee.strip().rstrip(".").strip()
    p = re.sub(r"^ACH D-\s*", "", p, flags=re.I)
    p = re.sub(r"[-\s]+(?:P-)?(?=[A-Z]*\d)[A-Z0-9]{8,}$", "", p)  # trailing mandate / ref codes
    return p or None


_VPA = re.compile(r"[a-z0-9][a-z0-9._-]*@[a-z][a-z0-9]+", re.I)


def parse(family: str, body: str) -> Parsed:
    text = normalise(body)

    for tpl in TEMPLATES:
        if family not in tpl.families:
            continue
        m = tpl.pattern.search(text)
        if not m:
            continue
        g = m.groupdict()
        payee = _clean_payee(g.get("payee"))
        vpa = g.get("vpa")
        if not vpa and payee and _VPA.fullmatch(payee):
            vpa = payee
        if not payee and vpa:
            payee = vpa
        channel = g.get("channel") or tpl.channel
        if tpl.id == "hdfc_debit_update":
            raw_info = (g.get("payee") or "").upper()
            channel = "ACH" if raw_info.startswith("ACH") else ("UPI" if raw_info.startswith("UPI") else None)
        return Parsed(
            msg_type=tpl.msg_type,
            template=tpl.id,
            direction=tpl.direction,
            amount=_amount(g.get("amt") or ""),
            currency=(g.get("ccy") or "INR").upper(),
            account_kind=tpl.account_kind,
            account_hint=g.get("acct"),
            channel=channel,
            counterparty=payee,
            vpa=vpa.lower() if vpa else None,
            ref=g.get("ref"),
            statement_date=_iso_date(g.get("stmt_date")),
            mandate=g.get("mandate"),
        )

    if family in CONFIRMATION_FAMILIES:
        return Parsed(msg_type="CONFIRMATION", template="merchant_receipt")
    if family in REQUEST_FAMILIES and re.search(r"request", text, re.I):
        return Parsed(msg_type="REQUEST", template="wallet_request")

    if family not in BANK_FAMILIES and family not in REQUEST_FAMILIES:
        return Parsed(msg_type="NON_BANK")

    for msg_type, pattern in NON_TXN_RULES:
        if pattern.search(text):
            return Parsed(msg_type=msg_type, template=f"rule:{msg_type.lower()}")

    if MONEY_HINT.search(text) and MOVE_HINT.search(text):
        return Parsed(msg_type="UNPARSED")
    return Parsed(msg_type="INFO", template="rule:no_money")
