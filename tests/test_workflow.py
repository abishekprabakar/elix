"""Every transition in the state machine table, plus refusals and audit atomicity."""

from __future__ import annotations

import pytest

from elix.contracts import Denied, State, WorkItem, WorkItemKind
from elix.engine import audit
from elix.engine.errors import Conflict
from elix.engine.workflow import TRANSITIONS, create, transition

ACTORS = {
    # (kind, action) -> actor allowed to take it in this test world
    ("purchase", "propose"): "agent:atlas",
    ("purchase", "answer"): "agent:atlas",
    ("purchase", "approve"): "human:dev",
    ("purchase", "reject"): "human:dev",
    ("purchase", "verify_payee"): "human:priya",
    ("invoice", "create"): "system:sync",
    ("invoice", "answer"): "human:sam",
    ("invoice", "approve"): "human:sam",
    ("invoice", "reject"): "human:sam",
    ("invoice", "record_credit"): "system:sync",
    ("accrual", "propose"): "system:policy",
    ("accrual", "approve"): "human:priya",
    ("accrual", "reject"): "human:priya",
    ("correction", "recode"): "human:sam",
    ("correction", "approve"): "human:priya",
    ("correction", "reject"): "human:priya",
    ("policy", "propose"): "system:learner",
    ("policy", "approve"): "human:priya",
    ("policy", "reject"): "human:priya",
}
SYSTEM = {"evaluate": "system:policy", "match": "system:policy", "auto_approve": "system:policy",
          "escalate": "system:policy", "deny": "system:policy", "pay": "system:gateway", "post": "system:gateway",
          "post_credit": "system:gateway", "intake_check": "system:policy"}


def actor_for(kind: WorkItemKind, state, action: str) -> str:
    if (kind.value, action) in ACTORS:
        return ACTORS[(kind.value, action)]
    if action == "ask":
        return "system:policy" if state == State.PROPOSED else "human:dev"
    if action == "fail":
        return "system:policy" if state == State.NEW else "system:gateway"
    return SYSTEM[action]


def make_item(kind: WorkItemKind, state: State) -> WorkItem:
    requested = {"purchase": "agent:atlas", "invoice": "system:sync", "accrual": "system:policy",
                 "correction": "human:sam", "policy": "system:learner"}[kind.value]
    return WorkItem(id="", kind=kind, state=state, period="2026-10", created_at="", updated_at="",
                    requested_by=requested, proposed_by=requested, vendor_id="ven_terrafield",
                    data={"agent_id": "agent:atlas", "spend_role": "budget_owner", "payee_hold": True})


def stored(elix, kind: WorkItemKind, state: State) -> WorkItem:
    """Put an item directly in a state so one transition can be tested in isolation (test only)."""
    item = make_item(kind, state).model_copy(update={
        "id": f"WI-{elix.store.next_counter('work_item', start=1001):04d}",
        "created_at": elix.clock.now(), "updated_at": elix.clock.now()})
    with elix.store.tx():
        elix.store.save_item(item)
    return item


CASES = [(kind, from_state, action, targets) for (kind, from_state, action), targets in TRANSITIONS.items()]


@pytest.mark.parametrize("kind,from_state,action,targets", CASES,
                         ids=[f"{k.value}:{f.value if f else 'none'}:{a}" for k, f, a, _ in CASES])
def test_every_transition(elix, kind, from_state, action, targets):
    actor = actor_for(kind, from_state, action)
    before = elix.store.last_audit()["seq"]
    if from_state is None:
        result = create(elix.ctx, make_item(kind, State.NEW), actor=actor, action=action, reason="Created in a test.")
    else:
        result = transition(elix.ctx, stored(elix, kind, from_state), action, actor, reason="Moved in a test.")
    assert result.state == targets[0]
    assert elix.store.get_item(result.id).state == targets[0]
    event = audit.events(elix.store, subjects=[result.id], limit=1)[0]
    assert event.seq == before + 1 and event.allowed and event.action == action and event.actor == actor


@pytest.mark.parametrize("kind,from_state,action,targets",
                         [c for c in CASES if len(c[3]) > 1],
                         ids=[f"{k.value}:{f.value}:{a}" for k, f, a, t in CASES if len(t) > 1])
def test_alternate_targets(elix, kind, from_state, action, targets):
    for target in targets:
        item = stored(elix, kind, from_state)
        assert transition(elix.ctx, item, action, actor_for(kind, from_state, action), to_state=target).state == target


def test_invalid_transition_is_refused_and_audited(elix):
    item = stored(elix, WorkItemKind.purchase, State.POSTED)
    with pytest.raises(Conflict) as err:
        transition(elix.ctx, item, "approve", "human:dev")
    assert err.value.audit_seq is not None
    event = audit.events(elix.store, subjects=[item.id], limit=1)[0]
    assert not event.allowed and event.action == "denied:approve" and event.seq == err.value.audit_seq
    assert elix.store.get_item(item.id).state == State.POSTED


def test_permission_denial_is_audited_with_rule_and_state_unchanged(elix):
    item = stored(elix, WorkItemKind.purchase, State.NEEDS_APPROVAL)
    with pytest.raises(Denied) as err:
        transition(elix.ctx, item, "approve", "agent:atlas")
    assert err.value.rule_id == "R31"
    event = audit.events(elix.store, subjects=[item.id], limit=1)[0]
    assert event.seq == err.value.audit_seq and not event.allowed
    assert event.evidence["rule_id"] == "R31"
    assert elix.store.get_item(item.id).state == State.NEEDS_APPROVAL


def test_denial_inside_a_rolled_back_transaction_is_still_recorded(elix):
    item = stored(elix, WorkItemKind.purchase, State.NEEDS_APPROVAL)
    with pytest.raises(Denied) as err:
        with elix.store.tx():
            transition(elix.ctx, item, "approve", "agent:atlas")
    events = audit.events(elix.store, subjects=[item.id])
    assert len(events) == 1 and events[0].seq == err.value.audit_seq
    assert elix.verify_chain().ok


def test_change_and_audit_are_one_transaction(elix, monkeypatch):
    item = stored(elix, WorkItemKind.purchase, State.NEEDS_APPROVAL)

    def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(audit, "append_event", broken)
    with pytest.raises(RuntimeError):
        transition(elix.ctx, item, "approve", "human:dev")
    monkeypatch.undo()
    assert elix.store.get_item(item.id).state == State.NEEDS_APPROVAL


def test_transition_records_a_diff(elix):
    item = stored(elix, WorkItemKind.purchase, State.NEEDS_APPROVAL)
    transition(elix.ctx, item, "approve", "human:dev", patch={"approved_by": "human:dev"})
    event = audit.events(elix.store, subjects=[item.id], limit=1)[0]
    assert event.diff["state"] == ["NEEDS_APPROVAL", "APPROVED"]
    assert event.diff["approved_by"] == [None, "human:dev"]
