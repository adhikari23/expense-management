"""Loads config/accounts.json and resolves SMS account/card numbers to own accounts."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
# Overridable so demo mode (scripts/demo.py) never touches your real data.
DATA_DIR = Path(os.environ.get("EXPENSE_DATA_DIR", str(ROOT / "data")))
CONFIG_PATH = Path(os.environ.get("EXPENSE_CONFIG", str(ROOT / "config" / "accounts.json")))


@dataclass
class Account:
    id: str
    type: str  # savings | credit_card | loan
    families: list[str]
    numbers: list[str]
    debit_cards: list[str] = field(default_factory=list)
    default: bool = False
    emi_mandates: list[str] = field(default_factory=list)
    default_emi: bool = False
    default_prepayment: bool = False
    emi_category: Optional[str] = None
    prepayment_category: Optional[str] = None


@dataclass
class Config:
    owner_names: list[str]
    accounts: list[Account]
    card_bill_payees: list[str]
    loan_emi_payees: list[str]
    salary_payers: list[str]
    owner_exact_names: list[str] = field(default_factory=list)
    own_deposit_payees: list[str] = field(default_factory=list)
    upi_spend_max: float = 500.0
    # Your own merchant words per category, e.g. {"Shopping": ["my local store"]}.
    extra_keywords: dict = field(default_factory=dict)

    def account(self, account_id: Optional[str]) -> Optional[Account]:
        for acct in self.accounts:
            if acct.id == account_id:
                return acct
        return None

    def resolve(self, family: str, number: Optional[str], kind: Optional[str] = None) -> Optional[str]:
        """Map (sender family, digits from SMS, expected type) to an account id."""
        candidates = [a for a in self.accounts if family in a.families and (kind is None or a.type == kind)]
        digits = re.sub(r"\D", "", number or "")
        if digits:
            for acct in candidates:
                for num in acct.numbers:
                    if digits.endswith(num) or num.endswith(digits):
                        return acct.id
                for card in acct.debit_cards:
                    if digits.endswith(card) or card.endswith(digits):
                        return acct.id
            return None
        defaults = [a for a in candidates if a.default]
        if len(defaults) == 1:
            return defaults[0].id
        if len(candidates) == 1:
            return candidates[0].id
        return None

    def loan_for_emi(self, mandate: Optional[str]) -> Optional[Account]:
        """Loan account an EMI debit belongs to: by mandate (UMRN), else the default EMI loan."""
        loans = [a for a in self.accounts if a.type == "loan"]
        for acct in loans:
            if mandate and mandate in acct.emi_mandates:
                return acct
        return next((a for a in loans if a.default_emi), None)

    def default_prepayment_loan(self) -> Optional[Account]:
        return next((a for a in self.accounts if a.type == "loan" and a.default_prepayment), None)

    def is_owner(self, name: Optional[str]) -> bool:
        norm = _norm(name)
        if not norm:
            return False
        if any(norm == _norm(n) for n in self.owner_exact_names):
            return True
        return any(_norm(owner) in norm for owner in self.owner_names)

    def is_card_bill_payee(self, name: Optional[str]) -> bool:
        return _matches_any(name, self.card_bill_payees)

    def is_loan_emi_payee(self, name: Optional[str]) -> bool:
        return _matches_any(name, self.loan_emi_payees)

    def is_own_deposit(self, name: Optional[str]) -> bool:
        return _matches_any(name, self.own_deposit_payees)

    def is_salary_payer(self, name: Optional[str]) -> bool:
        return _matches_any(name, self.salary_payers)


def _norm(text: Optional[str]) -> str:
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", (text or "").upper()).split())


def _matches_any(name: Optional[str], needles: list[str]) -> bool:
    norm = f" {_norm(name)} "
    return any(f" {_norm(n)} " in norm for n in needles) if norm.strip() else False


def load_config(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        raise SystemExit(
            f"Missing {path}. Copy config/accounts.example.json to config/accounts.json "
            "and fill in your own accounts (see README)."
        )
    data = json.loads(path.read_text())
    accounts = [
        Account(
            id=a["id"],
            type=a["type"],
            families=a.get("families", []),
            numbers=[str(n) for n in a.get("numbers", [])],
            debit_cards=[str(n) for n in a.get("debit_cards", [])],
            default=bool(a.get("default", False)),
            emi_mandates=a.get("emi_mandates", []),
            default_emi=bool(a.get("default_emi", False)),
            default_prepayment=bool(a.get("default_prepayment", False)),
            emi_category=a.get("emi_category"),
            prepayment_category=a.get("prepayment_category"),
        )
        for a in data.get("accounts", [])
    ]
    return Config(
        owner_names=data.get("owner_names", []),
        accounts=accounts,
        card_bill_payees=data.get("card_bill_payees", []),
        loan_emi_payees=data.get("loan_emi_payees", []),
        salary_payers=data.get("salary_payers", []),
        owner_exact_names=data.get("owner_exact_names", []),
        own_deposit_payees=data.get("own_deposit_payees", []),
        upi_spend_max=float(data.get("upi_spend_max", 500)),
        extra_keywords={k: list(v) for k, v in (data.get("extra_keywords") or {}).items()},
    )
