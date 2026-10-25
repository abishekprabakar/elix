"""Vendor credit notes that close a dispute: the checks, the entry, the scenario and the API roles."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from elix.contracts import Denied, InvoiceLine, MeterRecord, State, UsageWindow, VendorCreditNote, VendorInvoice
from elix.engine.errors import Conflict, Invalid, NotFound
from elix.rails import make_fake_registry
from elix.scenarios import list_scenarios, run_scenario
from elix.server import create_app

from conftest import copy_db

INVOICE = "LUM-2026-10A"
CREDIT = "LUM-CN-2026-10A"


def approved_double_bill(engine):
    invoice_id = run_scenario(engine, "cross-rail-double-bill", "human:priya")["result_ids"][0]
    engine.approve("human:sam", invoice_id, "Dispute sent to Lumen")
    return invoice_id


def credit(amount: int, number: str = CREDIT, vendor: str = "ven_lumen", invoice: str = INVOICE, **kw):
    return VendorCreditNote(vendor_id=vendor, credit_number=number, invoice_number=invoice, amount_cents=amount,
                            reason=kw.pop("reason", "Hours already paid over x402"), **kw)


def balance(engine, account: str, period: str = "2026-10") -> int:
    return next(b.balance_cents for b in engine.balances(period) if b.account == account)


def invoice_ap(engine, item) -> int:
    """What the invoice still owes in 2010, from every entry linked to the invoice item."""
    total = 0
    for entry in engine.store.entries(source_work_item=item.id):
        total += sum(l.credit_cents - l.debit_cents for l in entry.lines if l.account == "2010")
    return total


def last_refusal(engine, subject: str):
    return engine.audit(subject=subject, allowed=False, limit=1)[0]


# --------------------------------------------------------------- the entry


def test_full_credit_closes_the_dispute_and_clears_2150_and_the_invoice_balance(seeded):
    invoice_id = approved_double_bill(seeded)
    before = seeded.get_item(invoice_id)
    assert before.data["dispute_status"] == "open" and before.data["dispute_open_cents"] == 126_000
    assert balance(seeded, "2150") == -126_000  # a debit balance in a liability account, the amount disputed
    assert invoice_ap(seeded, before) == 126_000

    after = seeded.record_credit_note(credit(126_000))
    assert after.id == invoice_id and after.state == State.POSTED
    assert after.data["dispute_status"] == "credited" and after.data["dispute_open_cents"] == 0
    assert after.data["credited_cents"] == 126_000 and after.data["dispute_cents"] == 126_000
    assert after.data["pay_cents"] == 1_223_400  # existing keys are unchanged
    note = after.data["credit_notes"][0]
    assert note["credit_number"] == CREDIT and note["applied_cents"] == 126_000 and note["recorded_by"] == "system:sync"
    entry = seeded.store.get_entry(note["je_id"])
    assert entry.id == after.journal_entry_ids[-1] and entry.source_work_item == invoice_id
    debits = sum(l.debit_cents for l in entry.lines)
    assert debits == sum(l.credit_cents for l in entry.lines) == 126_000
    assert {(l.account, l.debit_cents, l.credit_cents) for l in entry.lines} == {("2010", 126_000, 0),
                                                                                  ("2150", 0, 126_000)}
    assert all(l.vendor_id == "ven_lumen" and l.cost_center == "ML" for l in entry.lines)
    assert balance(seeded, "2150") == 0
    assert invoice_ap(seeded, after) == 0
    assert seeded.trial_balance_ok("2026-10") and seeded.verify_chain().ok

    explanation = seeded.work_item(invoice_id).explanation
    sentence = (f"Lumen Compute issued credit note {CREDIT} for $1,260.00 against {INVOICE}. It matched the open "
                f"dispute, so Elix closed it and booked Dr 2010 Accounts payable, Cr 2150 Vendor disputes.")
    assert sentence in explanation
    why = seeded.why(INVOICE)
    assert sentence in why.answer and "paid $12,234.00, $1,260.00 short" in why.answer
    assert "until Lumen Compute issues a credit note" not in why.answer
    assert {"record_credit", "post_credit"} <= {s.action for s in why.steps}
    assert seeded.why(CREDIT).subject == invoice_id
    assert "—" not in explanation and "—" not in why.answer


def test_partial_credit_leaves_the_rest_open_until_a_second_credit(seeded):
    invoice_id = approved_double_bill(seeded)
    first = seeded.record_credit_note(credit(50_000, "LUM-CN-2026-10A-1"), actor_id="human:sam")
    assert first.data["dispute_status"] == "partly_credited" and first.data["dispute_open_cents"] == 76_000
    assert first.data["credit_notes"][0]["recorded_by"] == "human:sam"
    assert balance(seeded, "2150") == -76_000 and invoice_ap(seeded, first) == 76_000
    assert "left $760.00 open" in seeded.work_item(invoice_id).explanation
    assert "$760.00 is still in 2150 Vendor disputes" in seeded.why(INVOICE).answer

    second = seeded.record_credit_note(credit(76_000, "LUM-CN-2026-10A-2"), actor_id="human:priya")
    assert second.data["dispute_status"] == "credited" and second.data["dispute_open_cents"] == 0
    assert second.data["credited_cents"] == 126_000 and len(second.data["credit_notes"]) == 2
    assert balance(seeded, "2150") == 0 and invoice_ap(seeded, second) == 0
    assert "It matched the $760.00 still open, so Elix closed the dispute" in seeded.work_item(invoice_id).explanation


def test_a_credit_within_one_cent_closes_the_dispute_exactly(seeded):
    invoice_id = approved_double_bill(seeded)
    item = seeded.record_credit_note(credit(125_999))
    note = item.data["credit_notes"][0]
    assert item.data["dispute_status"] == "credited" and note["applied_cents"] == 126_000
    assert note["rounding_cents"] == -1 and balance(seeded, "2150") == 0 and invoice_ap(seeded, item) == 0
    assert "within a cent" in seeded.work_item(invoice_id).explanation


# --------------------------------------------------------------- refusals, each audited


def test_duplicate_credit_number_is_refused_and_audited(seeded):
    invoice_id = approved_double_bill(seeded)
    seeded.record_credit_note(credit(50_000))
    with pytest.raises(Conflict) as err:
        seeded.record_credit_note(credit(10_000), actor_id="human:sam")
    assert "already applied to LUM-2026-10A" in err.value.message
    event = last_refusal(seeded, invoice_id)
    assert event.seq == err.value.audit_seq and event.action == "denied:record_credit"
    assert event.actor == "human:sam" and event.evidence["rule_id"] == "R25"
    item = seeded.get_item(invoice_id)
    assert item.data["dispute_open_cents"] == 76_000 and len(item.data["credit_notes"]) == 1
    # The number is seen per vendor, whatever its case.
    with pytest.raises(Conflict):
        seeded.record_credit_note(credit(10_000, CREDIT.lower()))


def test_credit_above_the_open_dispute_is_refused(seeded):
    invoice_id = approved_double_bill(seeded)
    with pytest.raises(Conflict) as err:
        seeded.record_credit_note(credit(126_002))
    assert "only $1,260.00 is still open" in err.value.message
    event = last_refusal(seeded, invoice_id)
    assert event.evidence["rule_id"] == "R24" and event.seq == err.value.audit_seq
    assert balance(seeded, "2150") == -126_000
    seeded.record_credit_note(credit(26_000, "LUM-CN-2026-10A-1"))
    with pytest.raises(Conflict):
        seeded.record_credit_note(credit(100_002, "LUM-CN-2026-10A-2"))


def test_credit_on_an_invoice_that_is_not_approved_is_refused(seeded):
    invoice_id = run_scenario(seeded, "cross-rail-double-bill", "human:priya")["result_ids"][0]
    with pytest.raises(Conflict) as err:
        seeded.record_credit_note(credit(126_000))
    assert "has not been approved yet" in err.value.message and "Sam Ortiz" in err.value.message
    event = last_refusal(seeded, invoice_id)
    assert event.action == "denied:record_credit" and event.evidence["rule_id"] == "R24"
    assert seeded.get_item(invoice_id).state == State.NEEDS_APPROVAL
    assert seeded.get_item(invoice_id).data.get("credit_notes") is None


def test_unknown_invoice_wrong_vendor_and_paid_in_full_are_refused(seeded):
    invoice_id = approved_double_bill(seeded)
    with pytest.raises(NotFound):
        seeded.record_credit_note(credit(1_000, invoice="LUM-2026-10Z"))
    assert last_refusal(seeded, "LUM-2026-10Z").action == "denied:record_credit"
    with pytest.raises(Invalid) as err:
        seeded.record_credit_note(credit(1_000, vendor="ven_aws"))
    assert "is a Lumen Compute invoice" in err.value.message
    assert last_refusal(seeded, invoice_id).evidence["vendor_id"] == "ven_aws"
    with pytest.raises(Invalid):
        seeded.record_credit_note(credit(0))
    september = seeded.store.query_items(kind="invoice", invoice_number="LUM-2026-09B")[0]
    assert september.state == State.POSTED
    with pytest.raises(Conflict) as err:
        seeded.record_credit_note(credit(1_000, invoice="LUM-2026-09B"))
    assert "paid in full" in err.value.message


def test_only_sync_accountants_and_the_controller_record_credit_notes(seeded):
    invoice_id = approved_double_bill(seeded)
    for actor in ("human:dev", "human:maya", "agent:forge", "human:jordan", "system:policy"):
        with pytest.raises(Denied) as err:
            seeded.record_credit_note(credit(10_000), actor_id=actor)
        assert err.value.audit_seq is not None
    assert seeded.get_item(invoice_id).data["dispute_open_cents"] == 126_000
    assert seeded.verify_chain().ok


# --------------------------------------------------------------- closed periods roll forward


def test_credit_posts_in_the_next_open_period_when_the_invoice_month_is_closed(elix):
    """A minimal world: Forge pays 600 spot hours, Lumen bills them again, Sam short pays, Priya closes October."""
    from conftest import buy

    window = UsageWindow(start="2026-10-01T00:00:00Z", end="2026-10-16T00:00:00Z")
    url = elix.resource_url("ven_lumen", "h100-spot-hr", 600, start="2026-10-09T00:00:00Z",
                            end="2026-10-11T00:00:00Z")
    spot = buy(elix, "agent:forge", "ven_lumen", "h100-spot-hr", 600, resource_url=url, task_ref="spot-1",
               purpose="Spot hours to train hail-v7")
    assert elix.approve("human:dev", spot.id).state == State.POSTED
    elix.ingest_meter([MeterRecord(vendor_id="ven_lumen", sku="h100-ondemand-hr", quantity=2140,
                                   window_start=window.start, window_end=window.end)])
    lines = [InvoiceLine(line_id="1", description="Reserved H100 cluster", sku="h100-reserved-halfmonth", quantity=1,
                         unit_price_cents=900_000, amount_cents=900_000, usage_window=window),
             InvoiceLine(line_id="2", description="On demand H100 hours", sku="h100-ondemand-hr", quantity=2140,
                         unit_price_cents=210, amount_cents=449_400, usage_window=window)]
    invoice = elix.ingest_invoice(VendorInvoice(vendor_id="ven_lumen", invoice_number=INVOICE, period="2026-10",
                                                issued_at="2026-10-24T16:00:00Z", due_at="2026-11-23T16:00:00Z",
                                                lines=lines, total_cents=1_349_400))
    assert invoice.data["dispute_cents"] == 126_000
    elix.approve("human:sam", invoice.id)
    preview = elix.close_preview("human:priya", "2026-10")
    for accrual in preview.accruals:
        elix.approve("human:priya", accrual.id)
    assert elix.close_period("human:priya", "2026-10").status == "closed"

    item = elix.record_credit_note(credit(126_000), actor_id="human:sam")
    entry = elix.store.get_entry(item.data["credit_notes"][0]["je_id"])
    assert entry.period == "2026-11" and entry.date == "2026-11-01"
    assert item.period == "2026-10" and item.data["dispute_status"] == "credited"
    assert invoice_ap(elix, item) == 0
    assert next(b.balance_cents for b in elix.balances("2026-11") if b.account == "2150") == 0


# --------------------------------------------------------------- the scenario


def test_vendor_credit_scenario_is_ready_only_after_the_short_pay_is_approved(seeded):
    def row():
        return next(s for s in list_scenarios(seeded) if s["id"] == "vendor-credit")

    assert [s["id"] for s in list_scenarios(seeded)][:2] == ["cross-rail-double-bill", "vendor-credit"]
    first = row()
    assert first["title"] == "Lumen credits the double billed hours" and not first["ready"]
    assert "Run the double bill scenario" in first["not_ready_reason"] and "Sam" in first["not_ready_reason"]
    with pytest.raises(Conflict):
        run_scenario(seeded, "vendor-credit", "human:priya")
    invoice_id = run_scenario(seeded, "cross-rail-double-bill", "human:priya")["result_ids"][0]
    assert not row()["ready"]
    seeded.approve("human:sam", invoice_id)
    assert row()["ready"]

    result = run_scenario(seeded, "vendor-credit", "human:priya")
    item = seeded.get_item(invoice_id)
    assert result["result_ids"] == [invoice_id, item.journal_entry_ids[-1]]
    note = item.data["credit_notes"][0]
    assert note["credit_number"] == CREDIT and note["amount_cents"] == 126_000 and note["recorded_by"] == "system:sync"
    assert item.data["dispute_status"] == "credited" and balance(seeded, "2150") == 0
    assert result["message"].startswith(f"Lumen Compute issued credit note {CREDIT} for $1,260.00 against {INVOICE}.")
    assert "—" not in result["message"]
    after = row()
    assert not after["ready"] and "already issued credit note" in after["not_ready_reason"]
    # The auditor's question still answers, now with the credit in it.
    assert "issued credit note" in run_scenario(seeded, "audit-question", "human:jordan")["message"]


# --------------------------------------------------------------- the API


@pytest.fixture()
def api(seeded_template, tmp_path):
    db = tmp_path / "elix.db"
    copy_db(seeded_template, db)
    app = create_app(db, make_fake_registry(), live_clock=False)
    with TestClient(app) as client:
        yield client
    app.state.elix_state.close()


def post_credit(api, token: str, body: dict, invoice: str = INVOICE):
    return api.post(f"/api/invoices/{invoice}/credit-notes", headers={"Authorization": f"Bearer {token}"}, json=body)


def test_api_records_credit_notes_for_accountants_and_the_controller_only(api):
    sam = {"Authorization": "Bearer tok_sam"}
    invoice_id = api.post("/api/scenarios/cross-rail-double-bill/run", headers={"Authorization": "Bearer tok_priya"},
                          json={}).json()["result_ids"][0]
    body = {"vendor_id": "ven_lumen", "credit_number": "LUM-CN-2026-10A-1", "amount_cents": 50_000,
            "reason": "Partial credit while Lumen checks its meter"}
    early = post_credit(api, "tok_sam", body)
    assert early.status_code == 409 and early.json()["error"]["code"] == "conflict"
    assert api.post(f"/api/work-items/{invoice_id}/approve", headers=sam, json={}).status_code == 200

    for token in ("tok_dev", "tok_maya", "tok_forge", "tok_scout", "tok_jordan"):
        refused = post_credit(api, token, body)
        assert refused.status_code == 403, token
        error = refused.json()["error"]
        assert error["code"] == "denied" and error["audit_seq"] and error["rule_id"] == "R31"
    assert post_credit(api, "tok_nobody", body).status_code == 401

    session = api.get("/api/session", headers=sam).json()
    assert session["can"]["record_credit_notes"] and session["can"]["upload_invoices"]
    assert not api.get("/api/session", headers={"Authorization": "Bearer tok_dev"}).json()["can"]["record_credit_notes"]

    partial = post_credit(api, "tok_sam", body)
    assert partial.status_code == 201
    detail = partial.json()
    assert detail["item"]["id"] == invoice_id and detail["item"]["data"]["dispute_status"] == "partly_credited"
    assert detail["item"]["data"]["dispute_open_cents"] == 76_000 and len(detail["journal_entries"]) == 3

    again = post_credit(api, "tok_priya", body)
    assert again.status_code == 409 and again.json()["error"]["audit_seq"]
    too_much = post_credit(api, "tok_priya", {**body, "credit_number": "LUM-CN-2026-10A-2", "amount_cents": 90_000})
    assert too_much.status_code == 409 and "only $760.00 is still open" in too_much.json()["error"]["message"]
    wrong_vendor = post_credit(api, "tok_priya", {**body, "vendor_id": "ven_aws", "credit_number": "AWS-CN-1"})
    assert wrong_vendor.status_code == 400 and wrong_vendor.json()["error"]["code"] == "invalid"
    missing = post_credit(api, "tok_priya", body, invoice="LUM-2099-01A")
    assert missing.status_code == 404
    rest = post_credit(api, "tok_priya", {**body, "credit_number": "LUM-CN-2026-10A-2", "amount_cents": 76_000})
    assert rest.status_code == 201 and rest.json()["item"]["data"]["dispute_status"] == "credited"
    assert "It matched the $760.00 still open" in rest.json()["explanation"]
    balances = api.get("/api/balances", headers=sam, params={"period": "2026-10"}).json()
    assert next(b for b in balances["balances"] if b["account"] == "2150")["balance_cents"] == 0
    assert balances["trial_balance_ok"]
    assert api.get("/api/audit/verify", headers=sam).json()["ok"]
