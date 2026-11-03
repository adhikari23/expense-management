"""Phase 2 categorisation: your tags first, then merchant keywords, then people.

Order (first hit wins) for a ledger entry (transfers are fixed and skip this):
  1. per-transaction override you made in the app          -> reason user:txn
  2. your tag for this payee (UPI ID or merchant)            -> reason user:payee
  3. category fixed by linking (EMI, prepayment, salary, refund, cash...)
  4. keyword match on the merchant name (never the SMS boilerplate)
  5. nothing matched, money out by UPI under ₹500 (config upi_spend_max) -> UPI Spend
  6. otherwise                                               -> Needs Review
     (people are never guessed: you tag them FnF Transfer once)

All rule-based; no AI/LLM is involved.

Tags and overrides live in data/rules.json and are written by the sync server
when you edit in the app, or by `python -m app.ledger tag`.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.ledger import keywords as kw
from app.ledger.config import DATA_DIR

# ---------------------------------------------------------------------------
# Merchant / business VPA signatures. If a handle matches ANY of these it is
# treated as a merchant collection, NOT a person-to-person transfer.
# ---------------------------------------------------------------------------
MERCHANT_VPA_PATTERNS = [
    r"paytmqr",             # Paytm merchant QR      -> paytmqrxxxxxx@ptys
    r"^q\d{6,}@",           # PhonePe merchant QR    -> q535579031@ybl
    r"bharatpe",            # BharatPe
    r"vyapar",              # Vyapar billing         -> vyapar.175693452415@hdfcb
    r"@rzp\b|\.rzp@",       # Razorpay
    r"@payu\b",             # PayU
    r"\.pz@",               # PhonePe aggregator
    r"merchant",
    r"^\w+bb\d{6,}@",       # bank-biz handle        -> ujjbb56911331181@ujjivan
    r"^ujj",                # Ujjivan Bank biz
]

RULES_PATH = DATA_DIR / "rules.json"
_LOCK = threading.Lock()

NEEDS_REVIEW = "Needs Review"
FNF = "FnF Transfer"  # friends and family

# Old names still accepted (from older app builds / saved data) and mapped.
LEGACY_NAMES = {"Friends Transfer": FNF, "Family": FNF, "Personal Transfer": FNF}


def canonical(category: Optional[str]) -> Optional[str]:
    return LEGACY_NAMES.get(category, category) if category else category
UPI_SPEND = "UPI Spend"
UPI_CHANNELS = {"UPI", "UPI_MANDATE"}

EXPENSE_CATEGORIES = [
    "Food & Dining", "Groceries", "Shopping", "Transport", "Travel", "Utilities", "Entertainment",
    "Health", "Rent", "Insurance", "Home Loan EMI", "Home Loan Prepayment", "Home Loan Insurance EMI",
    "Home Loan Insurance Prepayment", "Investments", FNF, UPI_SPEND, "Cash Withdrawal", "Fees & Tax",
    "Other", NEEDS_REVIEW,
]
INCOME_CATEGORIES = ["Salary", FNF, "Interest / Dividend", "Refund", "Investments", "Other Income",
                     NEEDS_REVIEW]
ALL_CATEGORIES = list(dict.fromkeys(EXPENSE_CATEGORIES + INCOME_CATEGORIES))

TRAVEL = {
    "irctc", "indian railway", "railways", "uts", "ixigo", "confirmtkt", "makemytrip", "goibibo", "yatra",
    "cleartrip", "cleartri", "redbus", "abhibus", "railyatri", "agoda", "booking com", "airbnb", "oyo", "hotel", "resort",
    "indigo", "air india", "akasa", "spicejet", "vistara", "scapia", "lemon tree", "taj",
}
TRANSPORT = (kw.TRANSPORT - TRAVEL - {"makemytrip", "goibibo", "yatra", "cleartrip", "redbus", "abhibus",
                                      "railyatri", "ixigo", "confirmtkt", "irctc"}) | {"uber india", "sbimops"}
FOOD = kw.FOOD_DINING | {"bakery", "sweets", "darbar", "dhaba", "kitchen", "biryani", "momos", "tea"}
GROCERIES = kw.GROCERIES | {"mega mart", "vishal mega mart", "poultry", "kirana", "general store", "dairy", "fresh"}
SHOPPING = (kw.SHOPPING - kw.SHOPPING_EXCLUDE) | {"bluestone", "v mart", "westside", "emporium",
                                                  "jewell", "fashion", "garments", "sleepyhead"}
ENTERTAINMENT = {"bookmyshow", "book my show", "bigtree", "pvr", "inox", "cinepolis", "park", "gaming"}
UTILITIES = kw.UTILITIES | {"jio platforms", "airtel payments", "apple media", "google play", "oracle"}
INSURANCE = kw.INSURANCE | {"policybazaar", "care health", "sbi general"}
INVESTMENTS = kw.INVESTMENTS | {"bse", "iccl", "nps trust", "pgim", "mutual fund", "ppf"}
FEES_TAX = kw.BANK_CHARGES | {"cbdt", "cbdttax", "income tax", "gst", "challan", "traffic"}

KEYWORD_RULES: list[tuple[str, set[str]]] = [
    ("Fees & Tax", FEES_TAX),
    ("Insurance", INSURANCE),
    ("Investments", INVESTMENTS),
    ("Rent", kw.RENT),
    ("Travel", TRAVEL),
    ("Utilities", UTILITIES),
    ("Health", kw.HEALTH),
    ("Entertainment", ENTERTAINMENT),
    ("Food & Dining", FOOD),
    ("Groceries", GROCERIES),
    ("Transport", TRANSPORT),
    ("Shopping", SHOPPING),
]
INCOME_KEYWORDS: list[tuple[str, set[str]]] = [
    ("Salary", kw.SALARY),
    ("Interest / Dividend", kw.INCOME_INTEREST | {"interest"}),
    ("Investments", INVESTMENTS | {"groww", "redemption", "kotak life", "maturity"}),
]

BUSINESS_WORDS = {
    "store", "stores", "mart", "enterprise", "enterprises", "pvt", "ltd", "limited", "private", "services", "service",
    "traders", "trading", "bakery", "hotel", "restaurant", "pharma", "medical", "medicos", "sweets", "centre", "center",
    "agency", "agencies", "industries", "solutions", "technologies", "india", "bank", "shop", "foods", "corporation",
    "corp", "llp", "co", "company", "and", "&", "electricals", "hardware", "motors", "petroleum", "clinic", "hospital",
    "infrastructure", "developers", "developer", "jewellers", "parlour", "salon", "studio", "classes", "academy",
    "school", "electrical", "electronics", "suites", "lights", "global", "bastralay", "upi", "web", "retail",
}


# ----------------------------------------------------------------------------- payee keys

def payee_key(counterparty: Optional[str], vpa: Optional[str]) -> str:
    """Stable key for 'the same payee': the UPI ID if there is one, else the normalised name."""
    if vpa:
        return vpa.lower()
    acct = re.match(r"^a/c\s*\**(\d+)", (counterparty or "").strip(), re.I)
    if acct:
        return f"A/C {acct.group(1)}"
    name = (counterparty or "").upper()
    name = re.sub(r"^(?:PPSL|RAZ|OP|PYU|PAYU|CF)\s*\*", "", name)  # payment-gateway prefixes
    name = re.sub(r"[^A-Z ]+", " ", name)  # digits / punctuation (FLIPKART1231144 -> FLIPKART)
    return " ".join(name.split())


def _norm(text: str) -> str:
    text = re.sub(r"([a-z])(\d)", r"\1 \2", (text or "").lower())
    return " ".join(re.sub(r"[^a-z0-9@.&]+", " ", text).split())


def _keyword(text: str, rules: list[tuple[str, set[str]]]) -> Optional[tuple[str, str]]:
    hay = f" {_norm(text)} "
    for category, words in rules:
        for w in sorted(words, key=len, reverse=True):
            w = w.strip().lower()
            # whole word, or a distinctive brand glued into a token ("ppslagodacompanypte")
            if w and (f" {w} " in hay or (len(w) >= 5 and " " not in w and w in hay)):
                return category, w
    return None


def is_merchant_vpa(vpa: str) -> bool:
    return any(re.search(p, vpa, re.I) for p in MERCHANT_VPA_PATTERNS)


def looks_like_person(name: Optional[str], vpa: Optional[str], channel: Optional[str], account_type: str) -> bool:
    if account_type == "credit_card":
        return False  # card payments (incl. RuPay-card UPI) only go to merchants
    if vpa:
        return not is_merchant_vpa(vpa)
    if channel not in {"UPI", "IMPS", "NEFT", "TRANSFER"} or not name:
        return False
    words = re.sub(r"[^A-Za-z ]+", " ", name).lower().split()
    return 1 <= len(words) <= 5 and not any(w in BUSINESS_WORDS for w in words)


# ----------------------------------------------------------------------------- rules store

def migrate_names() -> int:
    """Rewrite saved tags/overrides that use old category names. Returns rows changed."""
    with _LOCK:
        data = load_rules()
        changed = 0
        for section in ("payees", "overrides"):
            for entry in data[section].values():
                new = canonical(entry.get("category"))
                if new != entry.get("category"):
                    entry["category"] = new
                    changed += 1
        if changed:
            _save(data)
    return changed


def load_rules() -> dict:
    try:
        data = json.loads(RULES_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        data = {}
    data.setdefault("payees", {})
    data.setdefault("overrides", {})
    return data


def _save(data: dict) -> None:
    RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = RULES_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False))
    tmp.replace(RULES_PATH)


def tag_payee(key: str, category: Optional[str] = None, name: Optional[str] = None) -> dict:
    category = canonical(category)
    if category and category not in ALL_CATEGORIES:
        raise ValueError(f"unknown category {category!r}; choose from {ALL_CATEGORIES}")
    with _LOCK:
        data = load_rules()
        entry = data["payees"].get(key, {})
        if category:
            entry["category"] = category
        if name:
            entry["name"] = name
        entry["updated_at"] = datetime.now().isoformat(timespec="seconds")
        data["payees"][key] = entry
        _save(data)
    return entry


def override_txn(msg_id: int, **fields) -> dict:
    """fields: category, merchant, amount, kind ('expense'|'income'), exclude (bool). None removes."""
    if fields.get("category"):
        fields["category"] = canonical(fields["category"])
    if fields.get("category") and fields["category"] not in ALL_CATEGORIES:
        raise ValueError(f"unknown category {fields['category']!r}")
    with _LOCK:
        data = load_rules()
        entry = data["overrides"].get(str(msg_id), {})
        for k, v in fields.items():
            if v is None:
                entry.pop(k, None)
            else:
                entry[k] = v
        if entry:
            entry["updated_at"] = datetime.now().isoformat(timespec="seconds")
            data["overrides"][str(msg_id)] = entry
        else:
            data["overrides"].pop(str(msg_id), None)
        _save(data)
    return entry


# ----------------------------------------------------------------------------- categorise

def categorize(group, account_type: str, rules: dict, upi_spend_max: float = 500.0,
               extra_keywords: Optional[dict] = None) -> tuple[str, str, Optional[str]]:
    """Return (category, reason, display_name) for a ledger group."""
    p = group.primary
    key = payee_key(p.p.counterparty, p.p.vpa)
    tag = rules["payees"].get(key, {})
    display = tag.get("name")
    override = rules["overrides"].get(str(p.msg_id), {})

    # Transfers are never spend/income, so tags don't apply to them.
    if group.subkind == "cc_bill":
        return "Credit Card Bill Payment", "link:cc_bill", display
    if group.subkind == "self":
        return "Self Transfer", "link:self", display
    # Your explicit choices win over everything the rules decided.
    if override.get("category"):
        return canonical(override["category"]), "user:txn", override.get("merchant") or display
    if tag.get("category"):
        return canonical(tag["category"]), "user:payee", display
    if group.category:
        return group.category, f"rule:{group.subkind or group.kind}", display

    name = p.p.counterparty or ""
    allowed = INCOME_CATEGORIES if group.kind == "income" else EXPENSE_CATEGORIES
    personal = [(canonical(c), {w.lower() for w in words}) for c, words in (extra_keywords or {}).items()
                if canonical(c) in allowed]
    if personal:  # your own words from config/accounts.json "extra_keywords"
        hit = _keyword(name, personal)
        if hit:
            return hit[0], f"keyword:{hit[1]}", display
    if group.kind == "income":
        hit = _keyword(name, INCOME_KEYWORDS) or _keyword(p.body, [INCOME_KEYWORDS[1]])
        if hit:
            return hit[0], f"keyword:{hit[1]}", display
    else:
        hit = _keyword(name, KEYWORD_RULES)
        if hit:
            return hit[0], f"keyword:{hit[1]}", display

    hint = "person" if looks_like_person(name, p.p.vpa, p.p.channel, account_type) else "no_match"
    amount = group.amount_override or p.amount
    if group.kind == "expense" and p.p.channel in UPI_CHANNELS and amount < upi_spend_max:
        return UPI_SPEND, f"small_upi:{hint}", display
    return NEEDS_REVIEW, f"review:{hint}", display
