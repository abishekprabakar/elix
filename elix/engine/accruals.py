"""Month end accruals for usage that has not been invoiced yet.

For every invoiced vendor and every billing window of the period that has
started, Elix finds the stretches of time no invoice line covers yet, measures
usage in them from the meters (net of what it already paid over x402), prices
it at contract rates, and proposes an accrual for the controller. Posting books
it on the last day of the period and books the reversal on the first day of
the next.
"""

from __future__ import annotations

from datetime import timedelta

from elix.contracts import State, Vendor, WorkItem, WorkItemKind
from elix.engine.clock import iso, next_period, parse_ts, period_start
from elix.engine.context import POLICY, Ctx
from elix.engine.fmt import cents_of, day, join_words, money, month_name, qty
from elix.engine.policy import find_sku
from elix.engine.reconcile import metered_quantity, paid_elsewhere
from elix.engine.workflow import create

INVOICED_BILLING = ("monthly_invoice", "semi_monthly_invoice")


def billing_windows(vendor: Vendor, period: str) -> list[tuple[str, str]]:
    start = period_start(period)
    end = period_start(next_period(period))
    if vendor.billing == "semi_monthly_invoice":
        middle = start + timedelta(days=15)
        return [(iso(start), iso(middle)), (iso(middle), iso(end))]
    return [(iso(start), iso(end))]


def invoiced_intervals(ctx: Ctx, vendor: Vendor) -> list[tuple[str, str]]:
    """Usage windows already billed by this vendor's invoices (any state except refused ones)."""
    spans: list[tuple[str, str]] = []
    for inv in ctx.store.query_items(kind="invoice", vendor_id=vendor.id, not_state=["REJECTED", "DENIED", "FAILED"]):
        for line in inv.data.get("invoice", {}).get("lines", []):
            window = line.get("usage_window")
            if window:
                spans.append((iso(parse_ts(window["start"])), iso(parse_ts(window["end"]))))
    return spans


def uncovered(start: str, end: str, covered: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Parts of [start, end) that no covered interval touches."""
    gaps: list[tuple[str, str]] = []
    cursor = start
    for s, e in sorted((max(s, start), min(e, end)) for s, e in covered if s < end and e > start):
        if s > cursor:
            gaps.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < end:
        gaps.append((cursor, end))
    return gaps


def accrual_lines(ctx: Ctx, vendor: Vendor, gaps: list[tuple[str, str]], whole_window: bool) -> list[dict]:
    lines: list[dict] = []
    metered_skus = ctx.config.metered_skus.get(vendor.id, set())
    for sku in vendor.skus:
        if sku.sku not in metered_skus:
            continue
        metered = paid = 0.0
        records = 0
        for start, end in gaps:
            used, count = metered_quantity(ctx, vendor.id, sku.sku, start, end)
            metered += used
            records += count
            paid += paid_elsewhere(ctx, vendor.id, sku.sku, start, end).quantity
        # Priced like an invoice line (quantity to two decimals), so the reversal and the real invoice net to zero.
        quantity = round(max(0.0, metered - paid), 2)
        if quantity <= 0:
            continue
        lines.append({
            "sku": sku.sku, "description": sku.description, "quantity": quantity, "rate_cents": sku.unit_price_cents,
            "amount_cents": cents_of(quantity, sku.unit_price_cents), "account": vendor.default_account,
            "metered_qty": round(metered, 4), "meter_records": records, "paid_elsewhere_qty": round(paid, 4),
            "source": "agent_meter",
        })
    if whole_window:
        for sku_id, quantity in ctx.config.recurring_lines.get(vendor.id, []):
            sku = find_sku(vendor, sku_id)
            if sku is None:
                continue
            lines.append({
                "sku": sku_id, "description": sku.description, "quantity": quantity,
                "rate_cents": sku.unit_price_cents, "amount_cents": cents_of(quantity, sku.unit_price_cents),
                "account": vendor.default_account, "source": "contract",
            })
    return lines


def accrual_sentence(ctx: Ctx, vendor: Vendor, period: str, window: tuple[str, str], gaps: list[tuple[str, str]],
                     lines: list[dict]) -> str:
    def phrase(line: dict) -> str:
        template = ctx.config.usage_phrases.get(line["sku"], "{qty} " + line["description"].lower())
        return template.format(qty=qty(round(line["quantity"], 1)))

    used = [phrase(line) for line in lines if line.get("source") == "agent_meter"]
    fixed = [phrase(line) for line in lines if line.get("source") != "agent_meter"]
    first = gaps[0][0]
    through = day(iso(parse_ts(gaps[-1][1]) - timedelta(seconds=1)))
    when = f"through {through}" if first == window[0] and vendor.billing == "monthly_invoice" else \
        f"from {day(first)} to {through}"
    if vendor.billing == "semi_monthly_invoice":
        which = f"the invoice for {day(window[0])} to {day(iso(parse_ts(window[1]) - timedelta(days=1)))}"
    else:
        which = f"the {month_name(period)} invoice"
    parts = []
    if used:
        parts.append(f"{join_words(used)} used {when}")
    parts.extend(fixed)
    return f"Accrued {join_words(parts)} at contract rates because {which} from {vendor.name} has not arrived."


def propose_accruals(ctx: Ctx, period: str, requested_by: str) -> list[WorkItem]:
    now = ctx.clock.now()
    proposed: list[WorkItem] = []
    existing = {(i.vendor_id, i.data.get("window", {}).get("start")): i
                for i in ctx.store.query_items(kind="accrual", period=period, not_state=["REJECTED"])}
    for vendor in ctx.store.vendors():
        if vendor.billing not in INVOICED_BILLING:
            continue
        covered = invoiced_intervals(ctx, vendor)
        for start, end in billing_windows(vendor, period):
            if start >= now:
                continue
            if (vendor.id, start) in existing:
                proposed.append(existing[(vendor.id, start)])
                continue
            gaps = uncovered(start, min(end, now), covered)
            if not gaps:
                continue
            whole = gaps == [(start, min(end, now))]
            lines = accrual_lines(ctx, vendor, gaps, whole)
            total = sum(line["amount_cents"] for line in lines)
            if total <= 0:
                continue
            sentence = accrual_sentence(ctx, vendor, period, (start, end), gaps, lines)
            last = iso(parse_ts(gaps[-1][1]) - timedelta(seconds=1))
            item = WorkItem(
                id="", kind=WorkItemKind.accrual, state=State.NEW, period=period, created_at="", updated_at="",
                requested_by=POLICY, proposed_by=POLICY, required_approver_role="controller",
                vendor_id=vendor.id, amount_cents=total, account=vendor.default_account,
                title=f"Accrual for {vendor.name}, {day(gaps[0][0])} to {day(last)}", simulated=True,
                data={"lines": lines, "window": {"start": start, "end": end, "through": min(end, now)},
                      "uninvoiced": [{"start": s, "end": e} for s, e in gaps], "sentence": sentence,
                      "triggered_by": requested_by, "assumptions": {
                          "meter_source": "agent_meter", "rates": "contract", "period": period,
                          "reason": "invoice not received", "net_of_x402_receipts": True}},
            )
            proposed.append(create(
                ctx, item, actor=POLICY, action="propose",
                reason=f"Elix proposed a {money(total)} accrual for {vendor.name}. {sentence}",
                evidence={"lines": lines, "triggered_by": requested_by, "uninvoiced": gaps},
            ))
    return proposed
