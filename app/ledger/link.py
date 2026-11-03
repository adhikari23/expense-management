"""Link related SMS into one ledger entry and decide what each entry *is*.

Every money message starts as its own group. Rules then merge groups:

  1. exact duplicate SMS (same sender + body within 10 min)
  2. same reference number, amount and direction
  3. known duplicate template pairs (ACH alert + ACH update, CC reversal + refund,
     NEFT beneficiary notice + NEFT debit). No generic "same amount, close in time"
     rule: it merged unrelated payments (PPF vs NPS, UPI vs ATM) in real data.
  4. credit-card bill: card-side "payment received" (1-2 SMS) + the savings debit
     that paid it  ->  transfer, never expense or income
  5. loan account credit + the savings debit that funded it -> home-loan prepayment
  6. transfers between your own accounts / to your own name -> transfer
  7. leftovers: debit -> expense, credit -> income, reversal -> refund
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Optional

from app.ledger.config import Config
from app.ledger.templates import Parsed

LEDGER_TYPES = {"TXN", "CARD_PAYMENT", "REFUND", "CONFIRMATION"}
OWN_ACCOUNT_CHANNELS = {"NEFT", "RTGS", "IMPS", "TRANSFER", "NETBANKING"}


@dataclass
class Event:
    msg_id: int
    ts: datetime
    family: str
    sender_id: str
    body: str
    body_source: str
    p: Parsed
    account: Optional[str] = None
    group: int = 0
    role: str = "primary"  # primary | duplicate | confirmation | leg | review
    note: str = ""

    @property
    def amount(self) -> float:
        return self.p.amount or 0.0


@dataclass
class Group:
    id: int
    members: list[Event] = field(default_factory=list)
    kind: Optional[str] = None  # expense | income | refund | transfer
    subkind: Optional[str] = None
    counter_account: Optional[str] = None
    category: Optional[str] = None
    category_reason: str = ""
    display_name: Optional[str] = None
    amount_override: Optional[float] = None

    @property
    def primary(self) -> Event:
        return next(e for e in self.members if e.role == "primary")

    def linked(self) -> bool:
        return len(self.members) > 1 or self.kind is not None


class Linker:
    def __init__(self, cfg: Config, events: Iterable[Event]):
        self.cfg = cfg
        self.events = sorted(events, key=lambda e: (e.ts, e.msg_id))
        self.groups: dict[int, Group] = {}
        self.review: list[Event] = []
        for e in self.events:
            if e.p.msg_type == "TXN" and (e.p.currency != "INR" or not e.p.amount):
                e.role, e.note = "review", f"foreign currency {e.p.currency}" if e.p.currency != "INR" else "no amount"
                self.review.append(e)
                continue
            e.group = e.msg_id
            self.groups[e.msg_id] = Group(id=e.msg_id, members=[e])

    # ------------------------------------------------------------------ helpers
    def _merge(self, keep: Event, other: Event, role: str, note: str) -> None:
        """Fold other's group into keep's group."""
        if keep.group == other.group:
            return
        src = self.groups.pop(other.group)
        dst = self.groups[keep.group]
        for m in src.members:
            if m.role == "primary":
                m.role, m.note = role, note
            m.group = dst.id
            dst.members.append(m)
        dst.kind = dst.kind or src.kind
        dst.subkind = dst.subkind or src.subkind
        dst.counter_account = dst.counter_account or src.counter_account

    def _primaries(self, pred=lambda e: True) -> list[Event]:
        return [g.primary for g in self.groups.values() if pred(g.primary)]

    def _free(self, e: Event) -> bool:
        """Not yet claimed by a transfer / loan / card rule (duplicate SMS are fine)."""
        return self.groups[e.group].kind is None

    @staticmethod
    def _close(a: float, b: float, tol: float = 1.0) -> bool:
        return abs(a - b) <= tol

    def _acct_type(self, account_id: Optional[str]) -> Optional[str]:
        acct = self.cfg.account(account_id)
        return acct.type if acct else None

    # ------------------------------------------------------------------ rules
    def dedupe_exact(self) -> None:
        last: dict[tuple[str, str], Event] = {}
        for e in self.events:
            if not e.group:
                continue
            key = (e.family, " ".join(e.body.split()))
            prev = last.get(key)
            if prev and e.ts - prev.ts <= timedelta(minutes=10):
                self._merge(prev, e, "duplicate", "identical SMS")
                continue
            last[key] = e

    def dedupe_ref(self) -> None:
        seen: dict[tuple[str, str, int], Event] = {}
        for e in self.events:
            if not e.group or not e.p.ref or e.role != "primary":
                continue
            key = (e.p.ref, e.p.direction or "", round(e.amount * 100))
            if key in seen:
                self._merge(seen[key], e, "duplicate", f"same ref {e.p.ref}")
            else:
                seen[key] = e

    def _pair(self, tpl_keep: str, tpl_dup: str, window: timedelta, role: str, note: str,
              same_account: bool = True, copy_payee: bool = False) -> None:
        keeps = [e for e in self.events if e.p.template == tpl_keep and e.role == "primary"]
        dups = [e for e in self.events if e.p.template == tpl_dup and e.role == "primary"]
        used: set[int] = set()
        for d in dups:
            best = None
            for k in keeps:
                if k.msg_id in used or not self._close(k.amount, d.amount, 0.01):
                    continue
                if same_account and k.account != d.account:
                    continue
                gap = abs(k.ts - d.ts)
                if gap <= window and (best is None or gap < abs(best.ts - d.ts)):
                    best = k
            if best:
                used.add(best.msg_id)
                if copy_payee and d.p.counterparty and not best.p.counterparty:
                    best.p.counterparty = d.p.counterparty
                self._merge(best, d, role, note)

    def dedupe_pairs(self) -> None:
        self._pair("hdfc_ach_alert", "hdfc_debit_update", timedelta(days=3), "duplicate", "ACH alert + update")
        self._pair("hdfc_cc_refund", "hdfc_cc_reversed", timedelta(days=3), "duplicate", "reversal + refund notice")
        for out_tpl in ("hdfc_neft_out", "hdfc_netbanking_payment", "hdfc_upi_sent"):
            self._pair(out_tpl, "hdfc_neft_beneficiary", timedelta(days=2), "confirmation",
                       "NEFT beneficiary notice", same_account=False, copy_payee=True)

    def link_card_payments(self) -> None:
        # (a) cluster card-side confirmations for the same payment
        card_side = self._primaries(lambda e: e.p.msg_type == "CARD_PAYMENT")
        for i, a in enumerate(card_side):
            if self.groups.get(a.group) is None or self.groups[a.group].primary is not a:
                continue
            for b in card_side[i + 1:]:
                if b.ts - a.ts > timedelta(days=3):
                    break
                if b.account == a.account and self._close(a.amount, b.amount) and self.groups[b.group].primary is b:
                    self._merge(a, b, "confirmation", "second card-side notice")
        clusters = [g for g in self.groups.values() if g.primary.p.msg_type == "CARD_PAYMENT"]
        for g in clusters:
            g.kind, g.subkind, g.counter_account = "transfer", "cc_bill", g.primary.account

        # (b) find the savings debit that paid each card payment
        def source_candidates(target_amt: float, when: datetime) -> list[Event]:
            out = []
            for e in self._primaries(lambda e: e.p.msg_type == "TXN" and e.p.direction == "debit"):
                if self._acct_type(e.account) != "savings" or not self._free(e):
                    continue
                # CRED and similar apps apply small discounts: the bank debit can be a
                # little *less* than what the card receives (seen: ₹3-₹51).
                shortfall = target_amt - e.amount
                if not (-1.0 <= shortfall <= max(60.0, 0.005 * target_amt)):
                    continue
                lag = when - e.ts
                if timedelta(days=-1) <= lag <= timedelta(days=4):
                    out.append(e)
            return out

        unmatched = []
        for g in clusters:
            card_evt = g.primary
            cands = source_candidates(card_evt.amount, card_evt.ts)
            hinted = [c for c in cands if self.cfg.is_card_bill_payee(c.p.counterparty) or c.p.channel == "BBPS"]
            pick = None
            if hinted:
                pick = min(hinted, key=lambda c: (abs(card_evt.amount - c.amount) > 1, abs(card_evt.ts - c.ts)))
            elif cands and card_evt.amount >= 1000:
                exact = [c for c in cands if self._close(c.amount, card_evt.amount, 1.0)]
                if len(exact) == 1:
                    pick = exact[0]
            if pick is None:
                unmatched.append(g)
                continue
            self._promote(pick, g, "card-side payment notice")

        # (c) one bank debit that paid two cards (e.g. CRED paying two cards at once)
        for debit in self._primaries(lambda e: e.p.msg_type == "TXN" and e.p.direction == "debit"):
            if not (self._free(debit) and self.cfg.is_card_bill_payee(debit.p.counterparty)):
                continue
            pool = [g for g in unmatched if g.id in self.groups and timedelta(days=-1) <= g.primary.ts - debit.ts <= timedelta(days=4)]
            for i, g1 in enumerate(pool):
                hit = next((g2 for g2 in pool[i + 1:] if self._close(g1.primary.amount + g2.primary.amount, debit.amount)), None)
                if hit:
                    g = self.groups[debit.group]
                    g.kind, g.subkind = "transfer", "cc_bill"
                    g.counter_account = f"{g1.primary.account}+{hit.primary.account}"
                    for other in (g1, hit):
                        for m in other.members:
                            m.role = "confirmation" if m.role == "primary" else m.role
                            m.note = m.note or "card-side payment notice (split)"
                            m.group = g.id
                            g.members.append(m)
                        self.groups.pop(other.id)
                        unmatched.remove(other)
                    break

        # (d) bill payments whose card-side SMS never arrived
        for debit in self._primaries(lambda e: e.p.msg_type == "TXN" and e.p.direction == "debit"):
            if self._free(debit) and self._acct_type(debit.account) == "savings" and self.cfg.is_card_bill_payee(debit.p.counterparty):
                g = self.groups[debit.group]
                g.kind, g.subkind = "transfer", "cc_bill"
                debit.note = "card bill payee, card-side SMS not found"

    def _promote(self, new_primary: Event, group: Group, note: str) -> None:
        """Make new_primary (a free event) the primary of `group`."""
        own = self.groups.pop(new_primary.group)
        for m in group.members:
            if m.role == "primary":
                m.role, m.note = "confirmation", note
        for m in own.members:
            m.group = group.id
        group.members[:0] = own.members

    def link_loans(self) -> None:
        for credit in self._primaries(lambda e: e.p.msg_type == "TXN" and self._acct_type(e.account) == "loan"
                                      and e.p.direction == "credit"):
            g = self.groups[credit.group]
            acct = self.cfg.account(credit.account)
            g.kind = "expense"
            g.subkind = "loan_prepayment"
            g.counter_account = credit.account
            g.category = (acct.prepayment_category if acct else None) or "Loan Prepayment"
            cands = [e for e in self._primaries(lambda e: e.p.msg_type == "TXN" and e.p.direction == "debit")
                     if self._acct_type(e.account) == "savings" and self._free(e)
                     and self._close(e.amount, credit.amount, 0.01) and abs(e.ts - credit.ts) <= timedelta(days=3)]
            if cands:
                pick = min(cands, key=lambda c: abs(c.ts - credit.ts))
                self._promote(pick, g, "loan account credit")
        for debit in self._primaries(lambda e: e.p.msg_type == "TXN" and e.p.direction == "debit"):
            if self._free(debit) and self.cfg.is_loan_emi_payee(debit.p.counterparty):
                g = self.groups[debit.group]
                mandate = next((m.p.mandate for m in g.members if m.p.mandate), None)
                loan = self.cfg.loan_for_emi(mandate)
                g.kind, g.subkind = "expense", "loan_emi"
                g.counter_account = loan.id if loan else None
                g.category = (loan.emi_category if loan else None) or "Loan EMI"

    def link_self_transfers(self) -> None:
        def own_counterparty(e: Event) -> bool:
            cp = e.p.counterparty or ""
            if self.cfg.is_owner(cp):
                return True
            if cp.lower().startswith("a/c"):
                return any(self.cfg.resolve(f, cp) for a in self.cfg.accounts for f in a.families)
            return False

        txns = lambda pred: self._primaries(lambda e: e.p.msg_type == "TXN" and pred(e))
        for debit in txns(lambda e: e.p.direction == "debit"):
            if not self._free(debit) or self._acct_type(debit.account) != "savings":
                continue
            if self.cfg.is_own_deposit(debit.p.counterparty):
                continue  # FD / PPF: counted as Investments in finalize()
            is_self = own_counterparty(debit)
            unknown_bank_transfer = debit.p.channel in OWN_ACCOUNT_CHANNELS and not debit.p.counterparty
            if not is_self and debit.p.channel not in OWN_ACCOUNT_CHANNELS:
                continue
            legs = [c for c in txns(lambda e: e.p.direction == "credit")
                    if self._free(c) and c.account and c.account != debit.account
                    and self._acct_type(c.account) == "savings"
                    and self._close(c.amount, debit.amount, 0.01)
                    and timedelta(0) <= c.ts - debit.ts <= timedelta(days=3)]
            g = self.groups[debit.group]
            if legs and (is_self or debit.amount >= 1000):
                leg = min(legs, key=lambda c: c.ts - debit.ts)
                g.kind, g.subkind, g.counter_account = "transfer", "self", leg.account
                self._merge(debit, leg, "leg", "credit leg of own transfer")
                continue
            if not (is_self or unknown_bank_transfer):
                continue
            # Own-name money that leaves again from another own account soon after is
            # just a hop (e.g. HDFC -> SBI -> loan): the later debit is the real outflow.
            hop = next((d for d in txns(lambda e: e.p.direction == "debit")
                        if d is not debit and self._free(d) and d.account != debit.account
                        and self._acct_type(d.account) == "savings" and own_counterparty(d)
                        and self._close(d.amount, debit.amount, 0.01)
                        and timedelta(0) <= d.ts - debit.ts <= timedelta(days=3)), None)
            if hop:
                g.kind, g.subkind, g.counter_account = "transfer", "self", hop.account
                debit.note = f"own transfer, money left again as msg {hop.msg_id}"
                continue
            # You confirmed these are NEFT/own-name transfers into the home loan whose
            # loan-side SMS never arrives.
            loan = self.cfg.default_prepayment_loan()
            g.kind, g.subkind = "expense", "loan_prepayment"
            g.counter_account = loan.id if loan else None
            g.category = (loan.prepayment_category if loan else None) or "Loan Prepayment"
            debit.note = "assumed loan prepayment (no payee / receiving SMS)"
        for credit in txns(lambda e: e.p.direction == "credit"):
            if self._free(credit) and own_counterparty(credit):
                g = self.groups[credit.group]
                g.kind, g.subkind = "transfer", "self"

    def resolve_refund_accounts(self) -> None:
        by_ref = {e.p.ref: e for e in self.events if e.p.ref and e.p.msg_type == "TXN"}
        for e in self.events:
            if e.p.msg_type == "REFUND" and not e.account and e.p.ref in by_ref:
                e.account = by_ref[e.p.ref].account
                e.note = f"refund of msg {by_ref[e.p.ref].msg_id}"

    def finalize(self) -> None:
        for g in self.groups.values():
            if g.kind:
                continue
            p = g.primary
            if p.p.msg_type == "REFUND":
                g.kind, g.subkind, g.category = "refund", "refund", "Refund"
            elif p.p.msg_type == "CONFIRMATION":
                g.kind = "orphan_confirmation"  # notice without the matching debit; not counted
            elif p.p.direction == "debit":
                g.kind = "expense"
                if self.cfg.is_own_deposit(p.p.counterparty):
                    g.subkind, g.category = "investment", "Investments"
                elif p.p.channel == "ATM":
                    g.subkind, g.category = "cash", "Cash Withdrawal"
            else:
                g.kind = "income"
                if self.cfg.is_salary_payer(p.p.counterparty):
                    g.subkind, g.category = "salary", "Salary"

    def run(self) -> list[Group]:
        self.resolve_refund_accounts()
        self.dedupe_exact()
        self.dedupe_ref()
        self.dedupe_pairs()
        self.link_card_payments()
        self.link_loans()
        self.link_self_transfers()
        self.finalize()
        return sorted(self.groups.values(), key=lambda g: (g.primary.ts, g.id))
