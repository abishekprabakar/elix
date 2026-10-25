"""Learner thresholds and exclusions, correction based learning, corrections, and gateway invariants."""

from __future__ import annotations

import re

import pytest

from elix.contracts import Denied, State
from elix.engine import learner
from elix.engine.errors import Conflict, Invalid
from elix.engine.learner import percentile, round_up

from conftest import ROOT, buy


def test_percentile_and_rounding():
    assert percentile([1800] * 70 + [3600] * 30, 0.95) == 3600
    assert round_up(3600) == 4000 and round_up(3500) == 3500 and round_up(121_800) == 122_000


def test_learner_proposes_from_history_and_never_activates(seeded):
    proposed = seeded.learn("human:sam")
    assert proposed and all(p.status == "proposed" for p in proposed)
    assert seeded.policies("active") == []
    terrafield = next(p for p in proposed if p.scope.get("sku") == "scene-10m")
    assert terrafield.params["max_amount_cents"] == 4_000
    assert terrafield.evidence["precedent_count"] >= 200
    assert len(terrafield.evidence["sample_ids"]) == 5 and terrafield.evidence["approvers"] == ["human:dev"]
    assert terrafield.evidence["triggered_by"] == "human:sam"
    item = seeded.get_item(terrafield.work_item_id)
    assert item.kind.value == "policy" and item.state == State.PROPOSED
    assert terrafield.id in [i.data.get("policy_id") for i in seeded.inbox("human:priya")]


def test_scene_3m_is_excluded_because_of_the_rejection(seeded):
    skus = {p.scope.get("sku") for p in seeded.learn()}
    assert "scene-3m" not in skus


def test_groups_below_threshold_are_not_proposed(seeded):
    skus = {p.scope.get("sku") for p in seeded.learn()}
    assert "station-day" not in skus      # about 13 weekly purchases
    assert "query-1k" not in skus         # auto approved, never approved by a person


def test_learning_is_idempotent(seeded):
    first = seeded.learn()
    assert first and seeded.learn() == []


def test_payee_change_blocks_learning(seeded):
    from elix.rails.keys import vendor_address

    seeded.update_vendor_payout("ven_terrafield", vendor_address("ven_terrafield", 1), changed_at="2026-09-15T12:00:00Z")
    assert "scene-10m" not in {p.scope.get("sku") for p in seeded.learn()}


def test_controller_cannot_approve_a_policy_they_proposed(seeded):
    policy = seeded.learn()[0]
    item = seeded.get_item(policy.work_item_id)
    with seeded.store.tx():
        seeded.store.save_item(item.model_copy(update={"proposed_by": "human:priya"}))
    with pytest.raises(Denied):
        seeded.approve_policy("human:priya", policy.id)
    with pytest.raises(Denied):
        seeded.approve_policy("human:dev", policy.id)


def test_correction_based_learning_needs_three_agreeing_recodes(seeded):
    september = [i for i in seeded.store.query_items(kind="purchase", vendor_id="ven_signalboard", period="2026-09",
                                                     state="POSTED")
                 if not seeded.store.get_entry(i.journal_entry_ids[0]).reversed_by]
    assert learner.learn_from_corrections(seeded.ctx) == []  # two recodes so far
    seeded.recode("human:sam", september[0].journal_entry_ids[0], 0, "6210", "Marketing data")
    coding = [p for p in seeded.policies("proposed") if p.kind == "coding"]
    assert len(coding) == 1 and coding[0].evidence["precedent_count"] == 3


def test_conflicting_recodes_block_correction_learning(seeded):
    september = [i for i in seeded.store.query_items(kind="purchase", vendor_id="ven_signalboard", period="2026-09",
                                                     state="POSTED")
                 if not seeded.store.get_entry(i.journal_entry_ids[0]).reversed_by]
    seeded.recode("human:sam", september[0].journal_entry_ids[0], 0, "6120", "Software")
    seeded.recode("human:sam", september[1].journal_entry_ids[0], 0, "6210", "Marketing data")
    assert [p for p in seeded.policies("proposed") if p.kind == "coding"] == []


def test_open_period_recode(seeded):
    october = seeded.store.query_items(kind="purchase", vendor_id="ven_signalboard", period="2026-10", state="POSTED")[0]
    je = october.journal_entry_ids[0]
    correction = seeded.recode("human:sam", je, 0, "6210", "Marketing data")
    assert not correction.data["closed_period"]
    assert not seeded.audit(subject=je, allowed=False)
    posted = seeded.approve("human:priya", correction.id)
    assert posted.state == State.POSTED and seeded.store.get_entry(je).reversed_by == posted.journal_entry_ids[0]
    with pytest.raises(Conflict):
        seeded.recode("human:sam", je, 0, "6120", "again")


def test_recode_validation_and_permissions(seeded):
    je = seeded.store.query_items(kind="purchase", vendor_id="ven_signalboard", period="2026-10", state="POSTED")[0]
    je_id = je.journal_entry_ids[0]
    with pytest.raises(Denied):
        seeded.recode("human:dev", je_id, 0, "6210", "")
    with pytest.raises(Denied):
        seeded.recode("human:jordan", je_id, 0, "6210", "")
    with pytest.raises(Invalid):
        seeded.recode("human:sam", je_id, 1, "6210", "cash line")
    with pytest.raises(Invalid):
        seeded.recode("human:sam", je_id, 0, "6110", "same account")
    with pytest.raises(Invalid):
        seeded.recode("human:sam", je_id, 9, "6210", "no such line")


# --------------------------------------------------------------- gateway


def test_only_the_gateway_imports_post_entry_or_calls_pay():
    engine = ROOT / "elix"
    offenders = []
    for path in engine.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(("elix/rails/", "elix/vendors/", "elix/agents/")):
            continue  # the rails implement pay(); they never import the ledger
        text = path.read_text()
        if rel in ("elix/engine/gateway.py", "elix/engine/ledger.py"):
            continue
        if re.search(r"\bpost_entry\b|\breverse_entry\b", text):
            offenders.append(f"{rel} uses post_entry or reverse_entry")
        if re.search(r"\.pay\(", text):
            offenders.append(f"{rel} calls .pay(")
    assert offenders == []
    gateway = (engine / "engine" / "gateway.py").read_text()
    assert "from elix.engine.ledger import post_entry" in gateway and ".pay(" in gateway
    for path in (engine / "rails").rglob("*.py"):
        assert "post_entry" not in path.read_text()


def test_gateway_never_pays_twice(seeded):
    from elix.engine import gateway

    item = buy(seeded, "agent:atlas", "ven_terrafield", "scene-10m", 1, task_ref="once")
    paid = seeded.approve("human:dev", item.id)
    with pytest.raises(Conflict):
        gateway.execute_purchase(seeded.ctx, paid)
    with pytest.raises(Conflict):
        gateway.execute_purchase(seeded.ctx, paid.model_copy(update={"state": State.APPROVED}))


def test_gateway_refuses_an_unverified_payee(seeded):
    from elix.engine import gateway
    from elix.rails.keys import vendor_address

    item = buy(seeded, "agent:scout", "ven_parsec", "query-1k", 1, pay_to_override=vendor_address("ven_parsec", 1))
    assert item.data["payee_hold"]
    with pytest.raises(Conflict):
        gateway.execute_purchase(seeded.ctx, item.model_copy(update={"state": State.APPROVED}))


def test_a_rail_refusal_fails_the_item_and_nothing_posts(seeded):
    from elix import contracts

    rail = seeded.registry.rail("x402")
    original = rail.pay

    def refuse(intent, challenge):
        raise contracts.RailError("challenge changed since approval")

    rail.pay = refuse
    try:
        item = buy(seeded, "agent:atlas", "ven_terrafield", "scene-10m", 1, task_ref="refused")
        failed = seeded.approve("human:dev", item.id)
    finally:
        rail.pay = original
    assert failed.state == State.FAILED and failed.receipt is None and not failed.journal_entry_ids
    assert "challenge changed since approval" in failed.data["error"].lower()


def test_unreachable_vendor_fails_at_proposal(seeded):
    rail = seeded.registry.rail("x402")
    original = rail.challenge

    def down(intent):
        from elix.contracts import RailError

        raise RailError("vendor unreachable")

    rail.challenge = down
    try:
        item = buy(seeded, "agent:atlas", "ven_terrafield", "scene-10m", 1, task_ref="down")
    finally:
        rail.challenge = original
    assert item.state == State.FAILED and "could not get payment terms" in item.data["error"]


def test_engine_has_no_network_or_model_calls():
    for path in (ROOT / "elix" / "engine").glob("*.py"):
        text = path.read_text()
        for module in ("httpx", "anthropic", "requests", "urllib.request", "socket"):
            assert not re.search(rf"^\s*(import|from) {re.escape(module)}\b", text, re.M), (path, module)
