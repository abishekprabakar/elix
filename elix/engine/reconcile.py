"""Invoice reconciliation, rules R20 to R23.

Each invoice line is matched three ways: against the contract rate, against
the usage meter for the line's window, and against x402 receipts Elix itself
paid for the same usage. The third match is what catches a cross rail double
bill. No single rail could, because none of them sees the others.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

from elix.contracts import (
    Check, Decision, FlagCode, Outcome, ReconciliationLine, ReconciliationProposal, Role, Severity,
    VendorInvoice, WorkItem, RULE_FLAGS,
)
from elix.engine.clock import parse_ts
from elix.engine.context import Ctx
from elix.engine.explain import nouns
from elix.engine.fmt import cents_of, day, fmt_pct, join_words, money, qty, short_hash
from elix.engine.policy import find_sku, policy_version
from elix.engine.store import item_number

QTY_TOLERANCE = 0.02

RULE_NAMES = {
    "R20": "Invoice quantity within metered usage",
    "R21": "Invoice rate matches contract",
    "R22": "Not already paid on another rail",
    "R23": "Invoice number not seen before",
}


@dataclass
class PaidElsewhere:
    quantity: float = 0.0
    receipts: list[dict] = field(default_factory=list)


@dataclass
class Match:
    proposal: ReconciliationProposal
    decision: Decision
    flags: list[FlagCode]
    duplicate_of: Optional[str] = None


# ---------------------------------------------------------------- windows


def overlap_seconds(a_start: str, a_end: str, b_start: str, b_end: str) -> float:
    start = max(parse_ts(a_start), parse_ts(b_start))
    end = min(parse_ts(a_end), parse_ts(b_end))
    return max(0.0, (end - start).total_seconds())


def span_seconds(start: str, end: str) -> float:
    return max(1.0, (parse_ts(end) - parse_ts(start)).total_seconds())


def window_words(start: str, end: str) -> str:
    """Oct 1 to Oct 15, treating a midnight end as exclusive."""
    end_dt = parse_ts(end)
    if end_dt.hour == 0 and end_dt.minute == 0 and end_dt.second == 0:
        end_dt = end_dt - timedelta(days=1)
    start_text, end_text = day(start), day(end_dt)
    return start_text if start_text == end_text else f"{start_text} to {end_text}"


# ---------------------------------------------------------------- the three sources


def metered_quantity(ctx: Ctx, vendor_id: str, sku: str, start: str, end: str) -> tuple[float, int]:
    """Usage in the window, prorating meter records that straddle its edges."""
    total, count = 0.0, 0
    for row in ctx.store.meters_overlapping(vendor_id, sku, start, end):
        share = overlap_seconds(row["window_start"], row["window_end"], start, end) / span_seconds(
            row["window_start"], row["window_end"])
        total += row["quantity"] * share
        count += 1
    return round(total, 6), count


def paid_elsewhere(ctx: Ctx, vendor_id: str, sku: str, start: str, end: str) -> PaidElsewhere:
    """Quantity of this usage already paid over x402, from receipts whose usage window overlaps."""
    skus = {sku} | {bought for (v, bought), metered in ctx.config.sku_equivalents.items()
                    if v == vendor_id and metered == sku}
    found = PaidElsewhere()
    for item in ctx.store.query_items(kind="purchase", vendor_id=vendor_id, state=["PAID", "POSTED"],
                                      order="created_at ASC"):
        if item.sku not in skus or item.receipt is None or item.receipt.rail != "x402":
            continue
        window = item.data.get("usage_window")
        if not window:
            continue
        share = overlap_seconds(window["start"], window["end"], start, end) / span_seconds(window["start"], window["end"])
        if share <= 0:
            continue
        quantity = (item.quantity or 0) * share
        found.quantity += quantity
        found.receipts.append({
            "item_id": item.id, "tx_ref": item.receipt.tx_ref, "sku": item.sku, "quantity": round(quantity, 6),
            "paid_at": item.data.get("paid_at"), "usage_window": window, "amount_cents": item.receipt.amount_cents,
            "payload_sha256": item.receipt.payload_sha256,
        })
    found.quantity = round(found.quantity, 6)
    return found


@dataclass
class BilledBefore:
    quantity: float = 0.0
    invoices: list[dict] = field(default_factory=list)


def billed_before(ctx: Ctx, vendor_id: str, sku: str, start: str, end: str, item_id: str) -> BilledBefore:
    """Usage in this window the vendor already billed on an earlier invoice with a different number."""
    found = BilledBefore()
    for other in ctx.store.query_items(kind="invoice", vendor_id=vendor_id, not_state=["REJECTED", "DENIED", "FAILED"],
                                       exclude_id=item_id, order="created_at ASC"):
        if item_number(other.id) > item_number(item_id):
            continue
        for line in other.data.get("invoice", {}).get("lines", []):
            window = line.get("usage_window")
            if line.get("sku") != sku or not window:
                continue
            share = overlap_seconds(window["start"], window["end"], start, end) / span_seconds(window["start"], window["end"])
            if share <= 0:
                continue
            quantity = float(line["quantity"]) * share
            found.quantity += quantity
            found.invoices.append({"item_id": other.id, "invoice_number": other.data.get("invoice_number"),
                                   "line_id": line.get("line_id"), "quantity": round(quantity, 6)})
    found.quantity = round(found.quantity, 6)
    return found


# ---------------------------------------------------------------- matching


def match(ctx: Ctx, invoice: VendorInvoice, item: WorkItem) -> Match:
    vendor = ctx.vendor(invoice.vendor_id)
    vendor_name = vendor.name if vendor else invoice.vendor_id
    metered_skus = ctx.config.metered_skus.get(invoice.vendor_id, set())

    duplicates = [i for i in ctx.store.query_items(kind="invoice", vendor_id=invoice.vendor_id,
                                                   invoice_number=invoice.invoice_number, exclude_id=item.id,
                                                   order="created_at ASC")
                  if item_number(i.id) < item_number(item.id)]
    duplicate = duplicates[0] if duplicates else None

    lines: list[ReconciliationLine] = []
    r20_fail, r21_fail, r22_fail = [], [], []
    double_billed_cents = 0
    for line in invoice.lines:
        rec, flags = _match_line(ctx, invoice, line, vendor, vendor_name, line.sku in metered_skus, item.id)
        lines.append(rec)
        if "R20" in flags:
            r20_fail.append(rec)
        if "R21" in flags:
            r21_fail.append(rec)
        if "R22" in flags:
            r22_fail.append(rec)
            double_billed_cents += rec.evidence.get("double_billed_cents", 0)

    line_sum = sum(line.amount_cents for line in invoice.lines)
    arithmetic_gap = invoice.total_cents - line_sum
    dispute = sum(rec.variance_cents for rec in lines) + max(0, arithmetic_gap)
    dispute = min(dispute, invoice.total_cents)
    pay = invoice.total_cents - dispute

    checks = [
        _check("R20", not r20_fail, Severity.escalate,
               "Every metered line is within 2 percent of the usage meter." if not r20_fail else
               " ".join(rec.finding for rec in r20_fail)),
        _check("R21", not r21_fail and arithmetic_gap == 0, Severity.escalate,
               _rate_detail(r21_fail, arithmetic_gap, line_sum, invoice.total_cents)),
        _check("R22", not r22_fail, Severity.escalate,
               "No line bills usage that Elix already paid over x402." if not r22_fail else
               " ".join(rec.finding for rec in r22_fail),
               receipts=[r for rec in r22_fail for r in rec.evidence.get("receipts", [])]),
        _check("R23", duplicate is None, Severity.deny,
               f"{vendor_name} has not sent invoice {invoice.invoice_number} before." if duplicate is None else
               f"{vendor_name} invoice {invoice.invoice_number} was already received as {duplicate.id}.",
               duplicate_of=duplicate.id if duplicate else None),
    ]
    flags = [RULE_FLAGS[c.rule_id] for c in checks if not c.passed]
    threshold = ctx.config.invoice_auto_approve_under_cents
    accountant = ctx.config.humans_with_role(Role.accountant)
    accountant_name = accountant[0].name if accountant else "an accountant"

    if duplicate is not None:
        outcome, required = Outcome.deny, None
        summary = f"Denied because {vendor_name} invoice {invoice.invoice_number} was already received as {duplicate.id}."
    elif dispute > 0 or flags:
        outcome, required = Outcome.needs_approval, Role.accountant
        summary = _dispute_summary(pay, dispute, r22_fail, r20_fail, r21_fail, vendor_name)
    elif invoice.total_cents < threshold:
        outcome, required = Outcome.auto_approve, None
        summary = (f"Auto approved because every line matches the contract and the usage meter and "
                   f"{money(invoice.total_cents)} is under the {money(threshold)} limit for invoices without review.")
    else:
        outcome, required = Outcome.needs_approval, Role.accountant
        summary = (f"Every line matches the contract and the usage meter. Needs approval from {accountant_name} "
                   f"because {money(invoice.total_cents)} is over the {money(threshold)} limit for invoices "
                   f"without review.")

    proposal = ReconciliationProposal(
        invoice_number=invoice.invoice_number, vendor_id=invoice.vendor_id, lines=lines,
        invoice_total_cents=invoice.total_cents, pay_cents=pay if duplicate is None else 0,
        dispute_cents=dispute if duplicate is None else 0,
        summary=_proposal_summary(pay, dispute, r22_fail, vendor_name) if dispute and duplicate is None else summary,
        dispute_note=_dispute_note(invoice, vendor_name, lines, pay, dispute) if dispute and duplicate is None else None,
    )
    decision = Decision(outcome=outcome, required_approver_role=required, checks=checks, summary=summary,
                        policy_version=policy_version(ctx))
    return Match(proposal=proposal, decision=decision, flags=flags, duplicate_of=duplicate.id if duplicate else None)


def _match_line(ctx: Ctx, invoice: VendorInvoice, line, vendor, vendor_name: str, metered: bool, item_id: str):
    sku = find_sku(vendor, line.sku)
    contract = sku.unit_price_cents if sku else None
    rate_ok = contract is not None and line.unit_price_cents == contract
    _, noun_plural, _ = nouns(ctx.config, invoice.vendor_id, line.sku)
    flags: set[str] = set()
    evidence: dict = {"contract_rate_cents": contract}

    metered_qty: Optional[float] = None
    paid = PaidElsewhere()
    billed = BilledBefore()
    expected: float = line.quantity
    window = line.usage_window
    if window is not None:
        billed = billed_before(ctx, invoice.vendor_id, line.sku, window.start, window.end, item_id)
        evidence["window"] = {"start": window.start, "end": window.end}
        if billed.invoices:
            evidence["billed_before"] = billed.invoices
    if metered and window is not None:
        metered_qty, count = metered_quantity(ctx, invoice.vendor_id, line.sku, window.start, window.end)
        paid = paid_elsewhere(ctx, invoice.vendor_id, line.sku, window.start, window.end)
        expected = max(0.0, metered_qty - paid.quantity - billed.quantity)
        evidence.update({"meter_records": count, "metered_qty": metered_qty, "receipts": paid.receipts})
    elif billed.quantity:
        expected = max(0.0, line.quantity - billed.quantity)

    over_tolerance = line.quantity > expected * (1 + QTY_TOLERANCE) + 1e-9
    billable = expected if over_tolerance else line.quantity
    fair_rate = contract if contract is not None else line.unit_price_cents
    fair_cents = cents_of(billable, fair_rate)
    variance = max(0, line.amount_cents - fair_cents)

    findings: list[str] = []
    overbilled = 0.0
    if metered_qty is not None and line.quantity > metered_qty * (1 + QTY_TOLERANCE) + 1e-9:
        flags.add("R20")
        overbilled = line.quantity - metered_qty
        over = (line.quantity - metered_qty) / metered_qty * 100 if metered_qty else 100.0
        findings.append(f"{vendor_name} billed {qty(line.quantity)} {noun_plural} for {window_words(window.start, window.end)} "
                        f"but the usage meter shows {qty(round(metered_qty, 2))}, {fmt_pct(over)} percent more.")
    rebilled = 0.0
    if billed.quantity > 0 and over_tolerance:
        rebilled = max(0.0, min(billed.quantity, line.quantity - expected - overbilled))
        if rebilled > 1e-6:
            flags.add("R20")
            numbers = join_words([f"{b['invoice_number']} ({b['item_id']})" for b in billed.invoices[:3]])
            evidence["rebilled_qty"] = round(rebilled, 6)
            findings.append(f"{qty(round(rebilled, 2))} of the {qty(line.quantity)} {noun_plural} billed for "
                            f"{window_words(window.start, window.end)} were already billed on {numbers}.")
    double_qty = 0.0
    if metered_qty is not None and paid.quantity > 0 and over_tolerance:
        double_qty = max(0.0, min(paid.quantity, line.quantity - expected - overbilled - rebilled))
        if double_qty > 1e-6:
            flags.add("R22")
            evidence["double_billed_qty"] = round(double_qty, 6)
            evidence["double_billed_cents"] = cents_of(round(double_qty, 6), fair_rate)
            findings.append(_double_bill_finding(ctx, line.quantity, double_qty, noun_plural, window, paid))
    if not rate_ok:
        flags.add("R21")
        if contract is None:
            findings.append(f"{line.sku} has no contract rate on file for {vendor_name}.")
        else:
            findings.append(f"{line.description} is billed at {money(line.unit_price_cents)} per unit but the contract "
                            f"rate is {money(contract)}.")
    if cents_of(line.quantity, line.unit_price_cents) != line.amount_cents:
        findings.append(f"The line amount {money(line.amount_cents)} does not equal {qty(line.quantity)} times "
                        f"{money(line.unit_price_cents)}.")
    if not findings:
        if metered_qty is not None:
            findings.append(f"Matches the usage meter, {qty(round(metered_qty, 2))} {noun_plural} for "
                            f"{window_words(window.start, window.end)}"
                            + (f" after {qty(round(paid.quantity, 2))} already paid over x402." if paid.quantity else "."))
        else:
            findings.append("Fixed contract line at the contract rate.")

    rec = ReconciliationLine(
        line_id=line.line_id, description=line.description, invoiced_qty=line.quantity,
        metered_qty=round(metered_qty, 4) if metered_qty is not None else None,
        paid_elsewhere_qty=round(paid.quantity, 4), expected_qty=round(expected, 4),
        contract_rate_cents=contract, invoiced_rate_cents=line.unit_price_cents, rate_ok=rate_ok,
        variance_cents=variance, finding=" ".join(findings), evidence=evidence,
    )
    return rec, flags


def _double_bill_finding(ctx: Ctx, invoiced: float, double_qty: float, noun_plural: str, window,
                         paid: PaidElsewhere) -> str:
    receipts = paid.receipts
    agents = {ctx.config.actor_name(ctx.store.get_item(r["item_id"]).requested_by) for r in receipts}
    who = join_words(sorted(agents)) or "an agent"
    refs = join_words([f"{r['item_id']}, settlement {short_hash(r['tx_ref'], 12)}" for r in receipts[:3]])
    paid_days = sorted({day(r["paid_at"]) for r in receipts if r.get("paid_at")})
    when = f" on {join_words(paid_days)}" if paid_days and len(paid_days) <= 2 else ""
    return (f"{qty(round(double_qty, 2))} of the {qty(invoiced)} {noun_plural} billed for "
            f"{window_words(window.start, window.end)} were hours {who} already paid for over x402{when} ({refs}).")


def _check(rule_id: str, passed: bool, severity: Severity, detail: str, **evidence) -> Check:
    return Check(rule_id=rule_id, name=RULE_NAMES[rule_id], passed=passed,
                 severity=Severity.info if passed else severity, detail=detail, evidence=evidence)


def _rate_detail(failed: list[ReconciliationLine], gap: int, line_sum: int, total: int) -> str:
    parts = []
    if failed:
        parts.extend(rec.finding for rec in failed)
    if gap:
        parts.append(f"The lines add up to {money(line_sum)} but the invoice total is {money(total)}.")
    return " ".join(parts) if parts else "Every line is billed at the contract rate and the total adds up."


def _proposal_summary(pay: int, dispute: int, double: list[ReconciliationLine], vendor_name: str) -> str:
    text = f"Pay {money(pay)} and dispute {money(dispute)}."
    if double:
        receipts = [r for rec in double for r in rec.evidence.get("receipts", [])]
        refs = join_words([f"{r['item_id']}, settlement {short_hash(r['tx_ref'], 12)}" for r in receipts[:3]])
        days = sorted({day(r["paid_at"]) for r in receipts if r.get("paid_at")})
        qty_total = sum(rec.evidence.get("double_billed_qty", 0) for rec in double)
        text += (f" {vendor_name} billed {qty(round(qty_total, 2))} hours that were already paid over x402"
                 f"{' on ' + join_words(days) if days and len(days) <= 2 else ''} ({refs}).")
    return text


def _dispute_summary(pay: int, dispute: int, double, over, rate, vendor_name: str) -> str:
    text = _proposal_summary(pay, dispute, double, vendor_name) if dispute else "Needs review."
    reasons = []
    if over:
        reasons.append("bills more usage than the meter shows")
    if rate:
        reasons.append("bills a rate that differs from the contract")
    if reasons:
        text += f" The invoice {join_words(reasons)}."
    return text + " Needs approval from an accountant."


def _dispute_note(invoice: VendorInvoice, vendor_name: str, lines: list[ReconciliationLine], pay: int,
                  dispute: int) -> str:
    issues = [rec.finding for rec in lines if rec.variance_cents]
    body = " ".join(issues)
    return (f"Hello {vendor_name} billing team. Invoice {invoice.invoice_number} bills {money(invoice.total_cents)}. "
            f"{body} We are paying {money(pay)} now and disputing {money(dispute)}. Please send a credit note for "
            f"{money(dispute)} or reply with usage records that show otherwise. Thank you. "
            f"Kestrel Labs accounts payable")
