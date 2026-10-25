"""Vendor credit notes that close a dispute.

When Elix short pays an invoice, the disputed amount sits in 2150 Vendor
disputes against the invoice's open balance in 2010 Accounts payable. A credit
note from the vendor is how that dispute ends. Sync can receive one from a
vendor feed, and an accountant or the controller can key in one the vendor sent
by email. Elix checks it against the invoice first, and every refusal is
audited. The gateway then books Dr 2010 / Cr 2150 for the credited amount.

Rules
    R24  the credit names an approved invoice from the same vendor that still has an open dispute,
         and is not more than what is still open
    R25  the credit note number has not been seen before for this vendor
"""

from __future__ import annotations

from typing import Any, Optional

from elix.contracts import Role, State, VendorCreditNote, WorkItem
from elix.engine import gateway
from elix.engine.clock import parse_ts
from elix.engine.context import SYNC, Ctx
from elix.engine.disputes import credit_notes, dispute_open_cents, dispute_status  # noqa: F401  (re-exported)
from elix.engine.errors import Conflict, Invalid, NotFound
from elix.engine.fmt import day, money
from elix.engine.permissions import approver_name, can
from elix.engine.workflow import audit_denied, deny, merged_data, transition

ROUNDING_CENTS = 1


# ---------------------------------------------------------------- finding credit notes


def find_credit(ctx: Ctx, vendor_id: str, credit_number: str) -> Optional[tuple[WorkItem, dict[str, Any]]]:
    """The invoice and credit note entry if this vendor's credit number was already applied."""
    wanted = credit_number.strip().lower()
    for item in ctx.store.query_items(kind="invoice", vendor_id=vendor_id, order="created_at ASC"):
        for note in credit_notes(item):
            if str(note.get("credit_number", "")).strip().lower() == wanted:
                return item, note
    return None


def find_by_credit_number(ctx: Ctx, credit_number: str) -> Optional[WorkItem]:
    wanted = credit_number.strip().lower()
    for item in ctx.store.query_items(kind="invoice", state="POSTED", order="created_at ASC"):
        if any(str(n.get("credit_number", "")).strip().lower() == wanted for n in credit_notes(item)):
            return item
    return None


# ---------------------------------------------------------------- recording a credit note


def record(ctx: Ctx, actor: str, credit: VendorCreditNote) -> WorkItem:
    """Check a vendor credit note against its invoice, record it, and have the gateway book it."""
    verdict = can(ctx.config, actor, "record_credit")
    if not verdict.allowed:
        raise deny(ctx, actor=actor, action="record_credit", subject=credit.invoice_number or "credit_note",
                   reason=verdict.reason, rule_id=verdict.rule_id)

    number = (credit.credit_number or "").strip()
    invoice_number = (credit.invoice_number or "").strip()
    vendor = ctx.vendor(credit.vendor_id)
    vendor_name = vendor.name if vendor else credit.vendor_id
    evidence = {"vendor_id": credit.vendor_id, "credit_number": number, "invoice_number": invoice_number,
                "amount_cents": credit.amount_cents}

    def refuse(exc: Exception, subject: str, reason: str, rule_id: str = "R24") -> Exception:
        audit_denied(ctx, actor=actor, action="record_credit", subject=subject, reason=reason, rule_id=rule_id,
                     evidence=evidence, exc=exc)
        return exc

    if not number:
        message = "A credit note needs the vendor's credit note number."
        raise refuse(_invalid(message), invoice_number or "credit_note", message)
    if credit.amount_cents <= 0:
        message = f"Credit note {number} has to be for more than $0.00."
        raise refuse(_invalid(message), invoice_number or "credit_note", message)
    if credit.issued_at:
        try:
            parse_ts(credit.issued_at)
        except ValueError:
            message = f"The issue date on credit note {number} is not a date Elix can read."
            raise refuse(_invalid(message), invoice_number or "credit_note", message)

    matches = ctx.store.query_items(kind="invoice", invoice_number=invoice_number, order="created_at ASC") \
        if invoice_number else []
    if not matches:
        message = (f"Elix has no invoice {invoice_number or 'without a number'}, so {vendor_name} credit note "
                   f"{number} cannot be applied.")
        raise refuse(_not_found(message), invoice_number or "credit_note", message)
    mine = [m for m in matches if m.vendor_id == credit.vendor_id]
    if not mine:
        owner = ctx.vendor(matches[0].vendor_id)
        message = (f"{invoice_number} is {_article(owner.name if owner else matches[0].vendor_id)} invoice, so a "
                   f"credit note from {vendor_name} cannot be applied to it.")
        raise refuse(_invalid(message), matches[0].id, message)
    item = next((m for m in mine if m.state == State.POSTED and m.data.get("dispute_cents")), None) \
        or next((m for m in mine if m.state not in (State.DENIED, State.REJECTED)), None) or mine[0]

    if item.state != State.POSTED:
        if item.state in (State.NEW, State.PROPOSED, State.NEEDS_APPROVAL, State.NEEDS_INFO):
            who = approver_name(ctx.config, item.required_approver_role or Role.accountant, None)
            message = (f"{invoice_number} has not been approved yet, so it has no dispute to credit. {who} has to "
                       f"approve the short pay first.")
        elif item.state == State.FAILED:
            message = f"The payment for {invoice_number} failed, so it has no dispute to credit."
        else:
            message = f"{invoice_number} was {item.state.value.lower()}, so it has no dispute to credit."
        raise refuse(_conflict(message), item.id, message)

    open_cents = dispute_open_cents(item)
    if open_cents <= 0:
        if not item.data.get("dispute_cents"):
            message = f"{invoice_number} was paid in full with nothing disputed, so there is nothing to credit."
        else:
            closing = next((n for n in reversed(credit_notes(item)) if n.get("applied_cents")), None)
            message = (f"The dispute on {invoice_number} is already closed"
                       + (f" by credit note {closing['credit_number']}." if closing else "."))
        raise refuse(_conflict(message), item.id, message)

    seen = find_credit(ctx, credit.vendor_id, number)
    if seen is not None:
        other, note = seen
        when = f" on {day(note['recorded_at'])}" if note.get("recorded_at") else ""
        message = (f"{vendor_name} credit note {number} was already applied to {other.data.get('invoice_number')}"
                   f"{when}, so Elix will not apply it twice.")
        raise refuse(_conflict(message), item.id, message, rule_id="R25")

    if credit.amount_cents > open_cents + ROUNDING_CENTS:
        message = (f"Credit note {number} is for {money(credit.amount_cents)} but only {money(open_cents)} is still "
                   f"open in dispute on {invoice_number}, so Elix will not apply it.")
        raise refuse(_conflict(message), item.id, message)

    # A credit within a cent of what is open closes the dispute and books exactly what is open, so 2150 and the
    # invoice's balance in 2010 both clear. The cent is recorded on the credit note.
    closes = abs(credit.amount_cents - open_cents) <= ROUNDING_CENTS
    applied = open_cents if closes else credit.amount_cents
    now = ctx.clock.now()
    note = {
        "credit_number": number, "amount_cents": credit.amount_cents, "applied_cents": applied,
        "rounding_cents": credit.amount_cents - applied, "open_before_cents": open_cents,
        "disputed_cents": int(item.data.get("dispute_cents") or 0), "reason": (credit.reason or "").strip(),
        "issued_at": credit.issued_at or now, "recorded_at": now, "recorded_by": actor,
        "cost_center": item.cost_center or _cost_center(ctx, item), "je_id": None,
    }
    name = ctx.config.actor_name(actor)
    verb = "received" if actor == SYNC else "recorded"
    reason = (f"{name} {verb} {vendor_name} credit note {number} for {money(credit.amount_cents)} against "
              f"{invoice_number}" + _reason_text(note["reason"]))
    with ctx.store.tx():
        item = transition(ctx, item, "record_credit", actor,
                          patch={"data": merged_data(item, credit_notes=[*credit_notes(item), note])},
                          reason=reason, evidence={**evidence, "applied_cents": applied,
                                                   "open_before_cents": open_cents, "closes_dispute": closes})
        item = gateway.post_credit_note(ctx, item, number)
    return item


# ---------------------------------------------------------------- helpers


def _cost_center(ctx: Ctx, item: WorkItem) -> Optional[str]:
    """The cost center of the x402 purchases the dispute is about, when they all share one."""
    centers = set()
    for link in item.links:
        if link.rel != "reconciles":
            continue
        other = ctx.store.get_item(link.id)
        if other is not None and other.cost_center:
            centers.add(other.cost_center)
    return centers.pop() if len(centers) == 1 else None


def _reason_text(reason: str) -> str:
    reason = (reason or "").strip()
    return f" with the reason \"{reason.rstrip('.')}.\"" if reason else "."


def _article(name: str) -> str:
    return f"an {name}" if name[:1].lower() in "aeiou" else f"a {name}"


def _invalid(message: str) -> Invalid:
    exc = Invalid(message)
    exc.audit_seq = None  # type: ignore[attr-defined]
    return exc


def _not_found(message: str) -> NotFound:
    exc = NotFound(message)
    exc.audit_seq = None  # type: ignore[attr-defined]
    return exc


def _conflict(message: str) -> Conflict:
    return Conflict(message)
