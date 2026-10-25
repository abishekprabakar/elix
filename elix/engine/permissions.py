"""`can()` is the single permission gate.

Every state change asks it first. It answers from the actor's role, the item,
and four segregation of duties rules:

1. Nobody approves an item they proposed, and agents never approve anything.
2. A payee hold is released only by the controller. A budget owner's approval of
   the spend does not release it.
3. Learned policies need a human controller, who cannot approve a policy they proposed.
4. Only system:gateway may pay or post. No human or agent token can.
"""

from __future__ import annotations

from typing import NamedTuple, Optional

from elix.contracts import ActorKind, Role, WorkItem, WorkItemKind
from elix.engine.context import ROLE_RANK, WorldConfig
from elix.engine.fmt import possessive


class Verdict(NamedTuple):
    allowed: bool
    reason: str
    rule_id: str = "R31"


ALLOW = Verdict(True, "", "")

HUMAN_ROLES = {Role.budget_owner, Role.accountant, Role.controller, Role.auditor}

# Actions a role may take regardless of the item. Item specific checks follow below.
ROLE_ACTIONS: dict[Role, set[str]] = {
    Role.agent: {"propose", "answer", "read", "replay"},
    Role.budget_owner: {"approve", "reject", "ask", "read", "query", "learn", "run_scenarios", "ingest_invoice"},
    Role.accountant: {"approve", "reject", "recode", "answer", "ask", "read", "query", "learn", "close_preview",
                      "run_scenarios", "ingest_invoice", "record_credit", "intake_invoice"},
    Role.controller: {"approve", "reject", "recode", "answer", "ask", "read", "query", "learn", "close_preview",
                      "verify_payee", "close", "run_scenarios", "ingest_invoice", "record_credit", "intake_invoice"},
    Role.auditor: {"read", "query", "run_scenarios"},
    Role.sync: {"create", "ingest", "update_vendor", "record_credit"},
    Role.policy: {"evaluate", "match", "auto_approve", "escalate", "ask", "deny", "propose", "fail", "intake_check"},
    Role.gateway: {"pay", "post", "fail", "open_period"},
    Role.learner: {"propose"},
}

ACTION_WORDS = {
    "approve": "approve", "reject": "reject", "verify_payee": "verify a payee", "recode": "recode journal lines",
    "close": "close a period", "reopen": "reopen a period", "pay": "pay", "post": "post to the ledger",
    "propose": "propose a purchase", "answer": "answer questions", "ask": "ask questions", "learn": "run the learner",
    "close_preview": "prepare a period close", "query": "query the audit trail", "ingest_invoice": "enter invoices",
    "edit": "edit a posted journal entry", "record_credit": "record vendor credit notes",
    "post_credit": "post to the ledger", "intake_invoice": "upload invoice PDFs",
}


def approver_name(config: WorldConfig, role: Optional[Role], agent_id: Optional[str]) -> str:
    """Who has to act for a given role, by name."""
    if role == Role.budget_owner and agent_id:
        agent = config.agent(agent_id)
        if agent:
            return config.actor_name(agent.owner_id)
    if role is not None:
        people = config.humans_with_role(role)
        if people:
            return people[0].name
    return "the controller"


def can(config: WorldConfig, actor_id: str, action: str, item: Optional[WorkItem] = None) -> Verdict:
    actor = config.actor(actor_id)
    if actor is None:
        return Verdict(False, f"{actor_id} is not a known actor.", "R01")
    name = actor.name

    if action == "reopen":
        return Verdict(False, "Closed periods are never reopened. Fixes post in the open period as corrections.", "R30")
    if action == "edit":
        return Verdict(False, "Posted journal entries are never edited. Fixes post as a reversal and a repost.", "R30")
    if action in ("pay", "post", "post_credit"):
        if actor.role == Role.gateway:
            return ALLOW
        return Verdict(False, f"Only the Elix gateway can {ACTION_WORDS[action]}. {name} cannot.", "R31")

    if action not in ROLE_ACTIONS.get(actor.role, set()):
        if actor.role == Role.auditor:
            return Verdict(False, f"{name} has read only access as the auditor.", "R31")
        if actor.role == Role.agent and action in ("approve", "reject", "verify_payee"):
            return _agent_approval_denied(config, actor_id, item)
        if action == "verify_payee":
            return Verdict(False, f"Payee changes need the controller. "
                                  f"{approver_name(config, Role.controller, None)} has to verify the new payout address.",
                           "R31")
        word = ACTION_WORDS.get(action, action.replace("_", " "))
        return Verdict(False, f"{name} cannot {word} in the {actor.role.value.replace('_', ' ')} role.",
                       "R01" if action == "propose" else "R31")

    if item is None:
        return ALLOW

    if action == "read":
        if actor.role == Role.agent and _agent_of(item) != actor_id:
            return Verdict(False, f"{name} can only see its own requests.", "R31")
        return ALLOW
    if action == "propose" and actor.role == Role.agent:
        if _agent_of(item) != actor_id:
            return Verdict(False, f"{name} can only propose purchases for itself.", "R01")
        return ALLOW
    if action == "answer" and actor.role == Role.agent:
        if _agent_of(item) != actor_id:
            return Verdict(False, f"{name} can only answer questions on its own requests.", "R31")
        return ALLOW
    if action in ("approve", "reject"):
        return _approval(config, actor_id, action, item)
    if action == "verify_payee":
        return _verify_payee(config, actor_id, item)
    return ALLOW


def _agent_of(item: WorkItem) -> Optional[str]:
    if item.requested_by.startswith("agent:"):
        return item.requested_by
    agent = item.data.get("agent_id")
    return agent if isinstance(agent, str) else None


def _required_role(item: WorkItem) -> Optional[Role]:
    if item.kind == WorkItemKind.purchase:
        role = item.data.get("spend_role")
        return Role(role) if role else None
    if item.kind in (WorkItemKind.accrual, WorkItemKind.policy):
        return Role.controller
    if item.kind == WorkItemKind.correction:
        return Role.accountant
    return item.required_approver_role or Role.accountant


def _agent_approval_denied(config: WorldConfig, actor_id: str, item: Optional[WorkItem]) -> Verdict:
    name = config.actor_name(actor_id)
    if item is None:
        return Verdict(False, "Agents cannot approve anything.", "R31")
    who = approver_name(config, _required_role(item) or item.required_approver_role, _agent_of(item))
    if _agent_of(item) == actor_id:
        return Verdict(False, f"{name} cannot approve its own request. An approval has to come from {who}.", "R31")
    return Verdict(False, f"Agents cannot approve anything. An approval has to come from {who}.", "R31")


def _approval(config: WorldConfig, actor_id: str, action: str, item: WorkItem) -> Verdict:
    actor = config.actor(actor_id)
    assert actor is not None
    name = actor.name
    if actor.kind != ActorKind.human:
        return _agent_approval_denied(config, actor_id, item)
    if actor_id in (item.proposed_by, item.requested_by):
        noun = "item" if item.kind != WorkItemKind.correction else "correction"
        return Verdict(False, f"{name} proposed this {noun}, so someone else has to approve it.", "R31")

    if item.kind == WorkItemKind.purchase:
        return _purchase_approval(config, actor_id, action, item)
    if item.kind == WorkItemKind.invoice:
        if actor.role in (Role.accountant, Role.controller):
            return ALLOW
        return Verdict(False, f"Invoices are approved by an accountant. {approver_name(config, Role.accountant, None)} "
                              f"or the controller has to approve this one.", "R31")
    if item.kind == WorkItemKind.correction:
        if actor.role in (Role.accountant, Role.controller):
            return ALLOW
        return Verdict(False, "Corrections are approved by an accountant or the controller who did not propose them.", "R31")
    if item.kind in (WorkItemKind.accrual, WorkItemKind.policy):
        if actor.role == Role.controller:
            return ALLOW
        noun = "an accrual" if item.kind == WorkItemKind.accrual else "a policy"
        return Verdict(False, f"Only the controller can {action} {noun}. "
                              f"{approver_name(config, Role.controller, None)} has to decide this one.", "R31")
    return Verdict(False, f"{name} cannot {action} this item.", "R31")


def _purchase_approval(config: WorldConfig, actor_id: str, action: str, item: WorkItem) -> Verdict:
    actor = config.actor(actor_id)
    assert actor is not None
    agent_id = _agent_of(item)
    agent = config.agent(agent_id) if agent_id else None
    agent_name = config.actor_name(agent_id)
    spend_role = _required_role(item)
    payee_hold = bool(item.data.get("payee_hold")) and not item.data.get("payee_verified")

    if action == "reject" and actor.role == Role.controller and (payee_hold or spend_role == Role.controller):
        return ALLOW

    if spend_role is None:
        # Nothing about the spend needs a person; only the controller's payee check remains.
        return Verdict(False, f"Payee changes need the controller. "
                              f"{approver_name(config, Role.controller, None)} has to verify the new payout address.", "R31")

    if spend_role == Role.budget_owner:
        if actor.role == Role.budget_owner:
            if agent and agent.owner_id == actor_id:
                return ALLOW
            owner = config.actor_name(agent.owner_id) if agent else "the agent's owner"
            return Verdict(False, f"{actor.name} does not own {agent_name}. An approval has to come from {owner}.", "R31")
        owner = approver_name(config, Role.budget_owner, agent_id)
        return Verdict(False, f"This purchase needs {possessive(agent_name)} budget owner. "
                              f"An approval has to come from {owner}.", "R31")

    if ROLE_RANK.get(actor.role, 0) >= ROLE_RANK[spend_role] and actor.role != Role.accountant:
        return ALLOW
    who = approver_name(config, spend_role, agent_id)
    return Verdict(False, f"This purchase needs the {spend_role.value.replace('_', ' ')}. "
                          f"An approval has to come from {who}.", "R31")


def _verify_payee(config: WorldConfig, actor_id: str, item: WorkItem) -> Verdict:
    actor = config.actor(actor_id)
    assert actor is not None
    if actor.role == Role.controller:
        return ALLOW
    return Verdict(False, f"Payee changes need the controller. "
                          f"{approver_name(config, Role.controller, None)} has to verify the new payout address.", "R31")
