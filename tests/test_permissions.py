"""can() for every role, plus the four segregation of duties rules."""

from __future__ import annotations

import pytest

from elix.contracts import State, WorkItem, WorkItemKind
from elix.engine.permissions import ROLE_ACTIONS, can
from elix.world.config import KESTREL

ROLE_OF = {
    "agent": "agent:atlas", "budget_owner": "human:dev", "accountant": "human:sam", "controller": "human:priya",
    "auditor": "human:jordan", "sync": "system:sync", "policy": "system:policy", "gateway": "system:gateway",
    "learner": "system:learner",
}
ALL_ACTIONS = sorted({a for actions in ROLE_ACTIONS.values() for a in actions} | {"pay", "post", "reopen", "edit"})


def purchase(agent="agent:atlas", spend_role="budget_owner", payee_hold=False, **data) -> WorkItem:
    return WorkItem(id="WI-9001", kind=WorkItemKind.purchase, state=State.NEEDS_APPROVAL, period="2026-10",
                    created_at="2026-10-24T16:00:00Z", updated_at="2026-10-24T16:00:00Z", requested_by=agent,
                    proposed_by=agent, data={"agent_id": agent, "spend_role": spend_role, "payee_hold": payee_hold,
                                             **data})


def item(kind: WorkItemKind, proposed_by: str, requested_by: str | None = None) -> WorkItem:
    return WorkItem(id="WI-9002", kind=kind, state=State.PROPOSED, period="2026-10",
                    created_at="2026-10-24T16:00:00Z", updated_at="2026-10-24T16:00:00Z",
                    requested_by=requested_by or proposed_by, proposed_by=proposed_by)


@pytest.mark.parametrize("role", sorted(ROLE_OF))
@pytest.mark.parametrize("action", ALL_ACTIONS)
def test_role_matrix_without_item(role, action):
    """Without an item, can() answers purely from the role's action list."""
    verdict = can(KESTREL, ROLE_OF[role], action)
    if action in ("reopen", "edit"):
        expected = False
    elif action in ("pay", "post"):
        expected = role == "gateway"
    else:
        expected = action in ROLE_ACTIONS[KESTREL.actor(ROLE_OF[role]).role]
    assert verdict.allowed is expected, (role, action, verdict.reason)
    if not verdict.allowed:
        assert verdict.reason and verdict.rule_id


def test_auditor_is_read_only():
    for action in ("approve", "reject", "recode", "close", "verify_payee", "learn", "ask"):
        verdict = can(KESTREL, "human:jordan", action, purchase())
        assert not verdict.allowed
        assert "read only" in verdict.reason
    assert can(KESTREL, "human:jordan", "read", purchase()).allowed
    assert can(KESTREL, "human:jordan", "query").allowed


def test_unknown_actor_is_denied():
    assert not can(KESTREL, "human:mallory", "read").allowed


# --------------------------------------------------------------- segregation rule 1


def test_agent_cannot_approve_its_own_request():
    verdict = can(KESTREL, "agent:scout", "approve", purchase(agent="agent:scout"))
    assert not verdict.allowed
    assert verdict.rule_id == "R31"
    assert verdict.reason == "Scout cannot approve its own request. An approval has to come from Maya Chen."


def test_agent_cannot_approve_anything_else_either():
    verdict = can(KESTREL, "agent:forge", "approve", purchase(agent="agent:atlas"))
    assert not verdict.allowed and "Agents cannot approve anything" in verdict.reason
    for kind in (WorkItemKind.invoice, WorkItemKind.policy, WorkItemKind.accrual, WorkItemKind.correction):
        assert not can(KESTREL, "agent:forge", "approve", item(kind, "system:policy")).allowed


def test_nobody_approves_what_they_proposed():
    correction = item(WorkItemKind.correction, "human:sam")
    verdict = can(KESTREL, "human:sam", "approve", correction)
    assert not verdict.allowed and "proposed this correction" in verdict.reason
    assert can(KESTREL, "human:priya", "approve", correction).allowed
    by_priya = item(WorkItemKind.correction, "human:priya")
    assert not can(KESTREL, "human:priya", "approve", by_priya).allowed
    assert can(KESTREL, "human:sam", "approve", by_priya).allowed


def test_budget_owner_only_approves_agents_they_own():
    assert can(KESTREL, "human:dev", "approve", purchase(agent="agent:atlas")).allowed
    verdict = can(KESTREL, "human:dev", "approve", purchase(agent="agent:scout"))
    assert not verdict.allowed and verdict.reason == "Dev Okafor does not own Scout. An approval has to come from Maya Chen."
    assert can(KESTREL, "human:maya", "approve", purchase(agent="agent:scout")).allowed


def test_controller_role_purchases_need_the_controller():
    controller_item = purchase(spend_role="controller")
    assert can(KESTREL, "human:priya", "approve", controller_item).allowed
    assert not can(KESTREL, "human:dev", "approve", controller_item).allowed
    # The controller does not approve spend that belongs to a budget owner.
    assert not can(KESTREL, "human:priya", "approve", purchase(spend_role="budget_owner")).allowed
    assert not can(KESTREL, "human:sam", "approve", purchase()).allowed


# --------------------------------------------------------------- segregation rule 2


def test_payee_hold_needs_the_controller():
    held = purchase(spend_role="budget_owner", payee_hold=True)
    assert can(KESTREL, "human:dev", "approve", held).allowed  # Dev may approve the spend
    verdict = can(KESTREL, "human:dev", "verify_payee", held)
    assert not verdict.allowed and verdict.reason.startswith("Payee changes need the controller.")
    assert can(KESTREL, "human:priya", "verify_payee", held).allowed
    assert can(KESTREL, "human:priya", "reject", held).allowed
    payee_only = purchase(spend_role=None, payee_hold=True)
    assert not can(KESTREL, "human:dev", "approve", payee_only).allowed


# --------------------------------------------------------------- segregation rule 3


def test_policies_need_a_human_controller_who_did_not_propose_them():
    learned = item(WorkItemKind.policy, "system:learner")
    assert can(KESTREL, "human:priya", "approve", learned).allowed
    assert not can(KESTREL, "human:sam", "approve", learned).allowed
    assert not can(KESTREL, "human:dev", "approve", learned).allowed
    assert not can(KESTREL, "system:learner", "approve", learned).allowed
    by_priya = item(WorkItemKind.policy, "human:priya")
    assert not can(KESTREL, "human:priya", "approve", by_priya).allowed


# --------------------------------------------------------------- segregation rule 4


@pytest.mark.parametrize("actor", ["human:priya", "human:dev", "human:sam", "human:jordan", "agent:atlas",
                                   "system:policy", "system:sync", "system:learner"])
def test_only_the_gateway_pays_or_posts(actor):
    for action in ("pay", "post"):
        verdict = can(KESTREL, actor, action, purchase())
        assert not verdict.allowed and verdict.rule_id == "R31"
    assert can(KESTREL, "system:gateway", "pay", purchase()).allowed
    assert can(KESTREL, "system:gateway", "post").allowed


def test_agents_read_only_their_own_items():
    assert can(KESTREL, "agent:atlas", "read", purchase(agent="agent:atlas")).allowed
    assert not can(KESTREL, "agent:scout", "read", purchase(agent="agent:atlas")).allowed
    assert can(KESTREL, "human:jordan", "read", purchase(agent="agent:atlas")).allowed


def test_closed_periods_never_reopen():
    verdict = can(KESTREL, "human:priya", "reopen")
    assert not verdict.allowed and verdict.rule_id == "R30"
