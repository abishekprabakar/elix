"""R20 to R23 with passing and failing cases, the cross rail double bill, and accruals."""

from __future__ import annotations

from elix.contracts import FlagCode, InvoiceLine, MeterRecord, State, UsageWindow, VendorInvoice
from elix.engine.accruals import uncovered

from conftest import buy

WINDOW = UsageWindow(start="2026-10-01T00:00:00Z", end="2026-10-08T00:00:00Z")


def meter(engine, hours_per_day=100.0, days=7, sku="h100-ondemand-hr", vendor="ven_lumen"):
    engine.ingest_meter([MeterRecord(vendor_id=vendor, sku=sku, agent_id="agent:forge", quantity=hours_per_day,
                                     window_start=f"2026-10-{d:02d}T00:00:00Z", window_end=f"2026-10-{d + 1:02d}T00:00:00Z")
                         for d in range(1, days + 1)])


def invoice(number, quantity, rate=210, total=None, vendor="ven_lumen", sku="h100-ondemand-hr"):
    amount = round(quantity * rate)
    return VendorInvoice(vendor_id=vendor, invoice_number=number, period="2026-10", issued_at="2026-10-24T16:00:00Z",
                         due_at="2026-11-23T16:00:00Z", total_cents=total if total is not None else amount,
                         lines=[InvoiceLine(line_id="1", description="On demand H100 hours", sku=sku,
                                            quantity=quantity, unit_price_cents=rate, amount_cents=amount,
                                            usage_window=WINDOW)])


def check(item, rule):
    return next(c for c in item.decision.checks if c.rule_id == rule)


def test_clean_invoice_passes_every_rule(elix):
    meter(elix)
    item = elix.ingest_invoice(invoice("L-1", 700))
    assert all(c.passed for c in item.decision.checks)
    assert item.data["dispute_cents"] == 0 and item.state == State.NEEDS_APPROVAL  # $1,470 is over $500
    assert item.required_approver_role.value == "accountant"


def test_small_clean_invoice_is_auto_approved_and_paid(elix):
    meter(elix, hours_per_day=20, days=7)
    item = elix.ingest_invoice(invoice("L-2", 140))
    assert item.state == State.POSTED and item.approved_by == "system:policy" and item.receipt.rail == "bank"


def test_r20_quantity_over_meter(elix):
    meter(elix)
    item = elix.ingest_invoice(invoice("L-3", 800))
    assert not check(item, "R20").passed and FlagCode.INVOICE_QTY_OVER_METER in item.flags
    assert item.data["dispute_cents"] == 100 * 210 and item.state == State.NEEDS_APPROVAL


def test_r20_within_two_percent_is_fine(elix):
    meter(elix)
    item = elix.ingest_invoice(invoice("L-4", 710))
    assert check(item, "R20").passed and item.data["dispute_cents"] == 0


def test_r21_rate_mismatch(elix):
    meter(elix)
    item = elix.ingest_invoice(invoice("L-5", 700, rate=240))
    assert not check(item, "R21").passed and FlagCode.INVOICE_RATE_MISMATCH in item.flags
    assert item.data["dispute_cents"] == 700 * 30


def test_r21_total_that_does_not_add_up(elix):
    meter(elix)
    item = elix.ingest_invoice(invoice("L-6", 700, total=700 * 210 + 5_000))
    assert not check(item, "R21").passed and item.data["dispute_cents"] == 5_000


def test_r22_cross_rail_double_bill(elix):
    meter(elix, hours_per_day=100)
    url = elix.resource_url("ven_lumen", "h100-spot-hr", 200, start="2026-10-02T00:00:00Z", end="2026-10-04T00:00:00Z")
    spot = buy(elix, "agent:forge", "ven_lumen", "h100-spot-hr", 200, resource_url=url)
    elix.approve("human:dev", spot.id)
    meter(elix, hours_per_day=0, days=0)
    # The meter counts the 200 spot hours too.
    elix.ingest_meter([MeterRecord(vendor_id="ven_lumen", sku="h100-ondemand-hr", agent_id="agent:forge", quantity=100,
                                   window_start=f"2026-10-0{d}T00:00:00Z", window_end=f"2026-10-0{d + 1}T00:00:00Z")
                       for d in (2, 3)])
    item = elix.ingest_invoice(invoice("L-7", 900))
    assert not check(item, "R22").passed and check(item, "R20").passed
    assert FlagCode.CROSS_RAIL_DOUBLE_BILL in item.flags
    assert item.data["dispute_cents"] == 200 * 210 and item.data["pay_cents"] == 700 * 210
    assert {"rel": "reconciles", "id": spot.id} in [l.model_dump() for l in item.links]


def test_r22_partial_window_overlap_is_prorated(elix):
    meter(elix, hours_per_day=100)
    url = elix.resource_url("ven_lumen", "h100-spot-hr", 200, start="2026-10-07T00:00:00Z", end="2026-10-09T00:00:00Z")
    spot = buy(elix, "agent:forge", "ven_lumen", "h100-spot-hr", 200, resource_url=url)
    elix.approve("human:dev", spot.id)
    elix.ingest_meter([MeterRecord(vendor_id="ven_lumen", sku="h100-ondemand-hr", agent_id="agent:forge", quantity=100,
                                   window_start="2026-10-07T00:00:00Z", window_end="2026-10-08T00:00:00Z")])
    item = elix.ingest_invoice(invoice("L-8", 800))
    line = item.data["reconciliation"]["lines"][0]
    assert line["paid_elsewhere_qty"] == 100 and item.data["dispute_cents"] == 100 * 210


def test_r23_duplicate_invoice_is_denied(elix):
    meter(elix)
    first = elix.ingest_invoice(invoice("L-9", 700))
    again = elix.ingest_invoice(invoice("L-9", 700))
    assert check(first, "R23").passed
    assert again.state == State.DENIED and FlagCode.DUPLICATE_INVOICE in again.flags
    assert {"rel": "duplicate_of", "id": first.id} in [l.model_dump() for l in again.links]


def test_uncovered_intervals():
    covered = [("2026-10-01T00:00:00Z", "2026-10-16T00:00:00Z")]
    assert uncovered("2026-10-01T00:00:00Z", "2026-10-24T16:00:00Z", covered) == [
        ("2026-10-16T00:00:00Z", "2026-10-24T16:00:00Z")]
    assert uncovered("2026-10-01T00:00:00Z", "2026-10-16T00:00:00Z", covered) == []
    assert uncovered("2026-10-01T00:00:00Z", "2026-10-10T00:00:00Z", []) == [
        ("2026-10-01T00:00:00Z", "2026-10-10T00:00:00Z")]


def test_accrual_then_reversal_and_idempotent_preview(seeded):
    first = seeded.close_preview("human:priya", "2026-10")
    second = seeded.close_preview("human:priya", "2026-10")
    assert [a.id for a in first.accruals] == [a.id for a in second.accruals]
    anthropic = next(a for a in first.accruals if a.vendor_id == "ven_anthropic")
    assert anthropic.proposed_by == "system:policy" and anthropic.data["triggered_by"] == "human:priya"
    posted = seeded.approve("human:priya", anthropic.id)
    accrual, reversal = (seeded.store.get_entry(j) for j in posted.journal_entry_ids)
    debit = sum(l.debit_cents for l in accrual.lines)
    assert debit == anthropic.amount_cents
    assert {l.account for l in accrual.lines} == {"5120", "2110"}
    assert reversal.reverses == accrual.id and reversal.date == "2026-11-01"
    assert seeded.store.get_period("2026-11").status == "open"


def test_r20_usage_rebilled_under_a_new_invoice_number(elix):
    meter(elix)
    first = elix.ingest_invoice(invoice("L-10", 700))
    assert first.data["dispute_cents"] == 0
    again = elix.ingest_invoice(invoice("L-10-R", 700))
    assert check(again, "R23").passed  # a new number, so not a duplicate invoice
    assert not check(again, "R20").passed and FlagCode.INVOICE_QTY_OVER_METER in again.flags
    line = again.data["reconciliation"]["lines"][0]
    assert line["evidence"]["billed_before"][0]["item_id"] == first.id
    assert again.data["dispute_cents"] == 700 * 210 and "already billed on L-10" in line["finding"]


def test_r20_fixed_line_billed_twice(elix):
    def reserved(number):
        return VendorInvoice(vendor_id="ven_lumen", invoice_number=number, period="2026-10",
                             issued_at="2026-10-24T16:00:00Z", due_at="2026-11-23T16:00:00Z", total_cents=900_000,
                             lines=[InvoiceLine(line_id="1", description="Reserved cluster", sku="h100-reserved-halfmonth",
                                                quantity=1, unit_price_cents=900_000, amount_cents=900_000,
                                                usage_window=WINDOW)])

    elix.ingest_invoice(reserved("R-1"))
    second = elix.ingest_invoice(reserved("R-2"))
    assert FlagCode.INVOICE_QTY_OVER_METER in second.flags and second.data["dispute_cents"] == 900_000
