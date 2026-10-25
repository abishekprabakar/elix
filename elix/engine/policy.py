"""Purchase policy evaluation, rules R01 to R12.

`evaluate_purchase` runs every rule, even after one fails, because a reviewer
needs the full picture. The outcome follows a fixed precedence: deny beats
needs_approval beats needs_info beats auto_approve. Nothing here calls a model.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

from elix.contracts import (
    AgentConfig, Check, Decision, Outcome, Policy, Role, Severity, Sku, State, Vendor, WorkItem,
    RULE_FLAGS, FlagCode,
)
from elix.engine.audit import canonical
from elix.engine.clock import iso, parse_ts
from elix.engine.context import ROLE_RANK, Ctx
from elix.engine.fmt import duration, fmt_pct, money, month_name, possessive, seconds_between, short_addr
from elix.engine.store import exposure_cents, item_number

PAYEE_COOLDOWN_SECONDS = 72 * 3600
DUPLICATE_WINDOW_SECONDS = 15 * 60
SPLIT_WINDOW_SECONDS = 60 * 60
MIN_PURPOSE_CHARS = 12
NOT_COUNTED = [State.REJECTED.value, State.DENIED.value, State.FAILED.value]
COMMITTED = [State.NEW.value, State.PROPOSED.value, State.NEEDS_INFO.value, State.NEEDS_APPROVAL.value,
             State.APPROVED.value]

# Phrases that claim approval or try to steer the reviewer. Matched case-insensitively.
INSTRUCTION_PATTERNS = [
    r"pre-?approved",
    r"already (been )?approved",
    r"approved by (finance|accounting|the controller|management|purchasing|the cfo)",
    r"skip (the )?review",
    r"no review (is )?(needed|required)",
    r"without (a )?review",
    r"(ignore|disregard) (all |any |the |previous |prior |above |your |these |those )*"
    r"(instructions|rules|policy|policies|limits|checks|review|caps?|budgets?)",
    r"do not (escalate|flag|review|hold)",
    r"auto-?approve (this|it)",
    r"bypass",
    r"override (the )?(policy|limits|controls|checks)",
]
_INSTRUCTIONS = re.compile("|".join(f"(?:{p})" for p in INSTRUCTION_PATTERNS), re.IGNORECASE)

R10_QUESTION = ("What is this purchase for? Reply with a specific purpose and the task reference it supports, "
                "for example \"Task ref hail-v7\".")

RULE_NAMES = {
    "R01": "Agent may propose",
    "R02": "Known vendor and product",
    "R03": "Payee matches vendor master",
    "R04": "Not a duplicate",
    "R05": "402 amount matches request",
    "R06": "Price within contract",
    "R07": "Within per purchase cap",
    "R08": "Within monthly budget",
    "R09": "Not a split purchase",
    "R10": "Purpose and task stated",
    "R11": "No instructions in vendor text",
    "R12": "Covered by an auto approve policy",
}


@dataclass
class Evaluation:
    decision: Decision
    flags: list[FlagCode]
    spend_role: Optional[Role]
    payee_hold: bool
    duplicate_of: Optional[str] = None
    policy: Optional[Policy] = None
    account: Optional[str] = None
    question: Optional[str] = None
    links: list[dict] = field(default_factory=list)


def find_sku(vendor: Optional[Vendor], sku: Optional[str]) -> Optional[Sku]:
    if vendor is None or sku is None:
        return None
    return next((s for s in vendor.skus if s.sku == sku), None)


def policy_version(ctx: Ctx) -> str:
    active = [p.model_dump(mode="json", include={"id", "version", "kind", "scope", "params"})
              for p in ctx.store.policies(status="active")]
    return hashlib.sha256(canonical(active).encode()).hexdigest()[:12]


def choose_account(ctx: Ctx, agent_id: Optional[str], vendor: Optional[Vendor], sku: Optional[str]) -> tuple[str, Optional[str]]:
    """The first active coding policy that matches wins, otherwise the vendor's default account."""
    for pol in ctx.store.policies(status="active", kind="coding"):
        if _scope_matches(pol, agent_id, vendor.id if vendor else None, sku):
            return pol.params["account"], pol.id
    if vendor is None:
        return "6990", None
    return vendor.default_account, None


def _scope_matches(pol: Policy, agent_id: Optional[str], vendor_id: Optional[str], sku: Optional[str]) -> bool:
    scope = pol.scope
    if scope.get("agent_id") and scope["agent_id"] != agent_id:
        return False
    if scope.get("vendor_id") and scope["vendor_id"] != vendor_id:
        return False
    if scope.get("sku") and scope["sku"] != sku:
        return False
    return True


def covering_policy(ctx: Ctx, agent_id: str, vendor_id: Optional[str], sku: Optional[str], amount: int) -> Optional[Policy]:
    for pol in ctx.store.policies(status="active", kind="auto_approve"):
        if _scope_matches(pol, agent_id, vendor_id, sku) and amount <= int(pol.params.get("max_amount_cents", 0)):
            return pol
    return None


# ---------------------------------------------------------------- rules


def _check(rule_id: str, passed: bool, severity: Severity, detail: str, **evidence) -> Check:
    return Check(rule_id=rule_id, name=RULE_NAMES[rule_id], passed=passed,
                 severity=Severity.info if passed else severity, detail=detail, evidence=evidence)


def r01_permission(ctx: Ctx, actor_id: str, agent: Optional[AgentConfig]) -> Check:
    actor = ctx.config.actor(actor_id)
    ok = actor is not None and actor.role == Role.agent and agent is not None and agent.actor_id == actor_id
    if ok:
        return _check("R01", True, Severity.deny, f"{agent.name} is a configured agent buying for itself.")
    return _check("R01", False, Severity.deny,
                  f"{ctx.config.actor_name(actor_id)} is not allowed to propose purchases for this agent.",
                  actor=actor_id)


def r02_vendor(item: WorkItem, vendor: Optional[Vendor], sku: Optional[Sku]) -> Check:
    if vendor is None:
        return _check("R02", False, Severity.escalate,
                      f"{item.vendor_id} is not in the vendor master, so the controller has to approve a new vendor.",
                      vendor_id=item.vendor_id, role="controller")
    if vendor.status != "active":
        return _check("R02", False, Severity.escalate, f"{vendor.name} is blocked in the vendor master.",
                      vendor_id=vendor.id, role="controller")
    if sku is None:
        return _check("R02", False, Severity.escalate,
                      f"{vendor.name} has no product called {item.sku} in the vendor master.",
                      vendor_id=vendor.id, sku=item.sku, role="controller")
    return _check("R02", True, Severity.escalate, f"{vendor.name} is an active vendor and {item.sku} is on its contract.",
                  vendor_id=vendor.id)


def r03_payee(ctx: Ctx, item: WorkItem, vendor: Optional[Vendor], now: str) -> Check:
    if vendor is None:
        return _check("R03", True, Severity.escalate, "Not applicable because the vendor is not in the master.")
    master = vendor.payout_address
    changed_at = vendor.payout_address_changed_at
    recent = bool(changed_at) and seconds_between(changed_at, now) < PAYEE_COOLDOWN_SECONDS
    ago = duration(seconds_between(changed_at, now)) if changed_at else ""
    evidence = {"master_address": master, "previous_address": vendor.previous_payout_address,
                "changed_at": changed_at, "role": "controller"}

    if item.challenge is None:
        verified = ctx.store.payee_verified(vendor.id, vendor.bank_account_last4 or "bank", changed_at) if recent else None
        if recent and not verified:
            return _check("R03", False, Severity.escalate,
                          f"{possessive(vendor.name)} payment details changed {ago} ago.", **evidence)
        return _check("R03", True, Severity.escalate,
                      f"Paid by bank to the account in {possessive(vendor.name)} master record"
                      f"{' ending ' + vendor.bank_account_last4 if vendor.bank_account_last4 else ''}.", **evidence)

    pay_to = item.challenge.pay_to
    evidence["pay_to"] = pay_to
    verified = ctx.store.payee_verified(vendor.id, pay_to, changed_at)
    if verified:
        evidence["verified_by"] = verified["verified_by"]
    mismatch = master is None or pay_to.lower() != master.lower()
    if mismatch and not verified:
        detail = (f"The 402 challenge asks to pay {short_addr(pay_to)} but {possessive(vendor.name)} master record "
                  f"says {short_addr(master)}")
        detail += f", and that record changed {ago} ago." if recent else "."
        return _check("R03", False, Severity.escalate, detail, **evidence)
    if recent and not verified:
        previous = vendor.previous_payout_address
        detail = (f"The 402 challenge asks to pay {short_addr(pay_to)}, which replaced {possessive(vendor.name)} "
                  f"previous payout address {short_addr(previous)} only {ago} ago.") if previous else (
                  f"{possessive(vendor.name)} payout address changed to {short_addr(pay_to)} only {ago} ago.")
        return _check("R03", False, Severity.escalate, detail, **evidence)
    detail = f"The 402 challenge pays {short_addr(pay_to)}, the address in {possessive(vendor.name)} master record."
    if verified:
        detail = (f"The 402 challenge pays {short_addr(pay_to)}, which "
                  f"{ctx.config.actor_name(verified['verified_by'])} verified after the change.")
    return _check("R03", True, Severity.escalate, detail, **evidence)


def r04_duplicate(ctx: Ctx, item: WorkItem, agent_name: str) -> tuple[Check, Optional[WorkItem]]:
    since = iso(parse_ts(item.created_at) - timedelta(seconds=DUPLICATE_WINDOW_SECONDS))
    prior = ctx.store.query_items(
        kind="purchase", agent_id=item.requested_by, vendor_id=item.vendor_id, sku=item.sku,
        created_from=since, created_to=item.created_at, not_state=NOT_COUNTED, exclude_id=item.id,
        order="created_at ASC, id ASC",
    )
    same = [p for p in prior if is_same_order(p, item) and item_number(p.id) < item_number(item.id)]
    if not same:
        return _check("R04", True, Severity.deny,
                      "No identical request from this agent in the last 15 minutes."), None
    original = same[0]
    gap = duration(seconds_between(original.created_at, item.created_at))
    status = {
        State.POSTED: "after the first request was paid", State.PAID: "after the first request was paid",
        State.APPROVED: "after the first request was approved",
        State.NEEDS_APPROVAL: "later, while the first request was still waiting for approval",
        State.NEEDS_INFO: "later, while the first request was still waiting for an answer",
    }.get(original.state, "after the first request")
    detail = f"{agent_name} asked for the same {_order_words(original)} at the same price {gap} {status}."
    return _check("R04", False, Severity.deny, detail, duplicate_of=original.id,
                  seconds_after=seconds_between(original.created_at, item.created_at),
                  original_state=original.state.value), original


def is_same_order(a: WorkItem, b: WorkItem) -> bool:
    """An HTTP retry resends the same body: same product, quantity, amount and task."""
    return (a.vendor_id == b.vendor_id and a.sku == b.sku and a.quantity == b.quantity
            and a.amount_cents == b.amount_cents and (a.task_ref or "") == (b.task_ref or ""))


def _order_words(item: WorkItem) -> str:
    title = item.title or f"{item.quantity} {item.sku}"
    return title.split(" from ")[0]


def r05_amount(item: WorkItem, agent_name: str) -> Check:
    if item.challenge is None:
        return _check("R05", True, Severity.escalate, "Not applicable because no 402 challenge was involved.")
    asked = item.challenge.amount_cents
    if abs(asked - item.amount_cents) <= 1:
        return _check("R05", True, Severity.escalate,
                      f"The 402 challenge asks for {money(asked)}, the amount {agent_name} requested.",
                      challenge_cents=asked, requested_cents=item.amount_cents)
    return _check("R05", False, Severity.escalate,
                  f"The 402 challenge asks for {money(asked)} but {agent_name} requested {money(item.amount_cents)}.",
                  challenge_cents=asked, requested_cents=item.amount_cents, role="budget_owner")


def r06_price(item: WorkItem, sku: Optional[Sku]) -> Check:
    if sku is None:
        return _check("R06", True, Severity.escalate, "Not applicable because there is no contract price on file.")
    contract = sku.unit_price_cents
    limit = contract * (1 + sku.tolerance_pct / 100)
    requested = item.unit_price_cents or contract
    challenged = (item.challenge.amount_cents / item.quantity) if item.challenge and item.quantity else 0
    unit = max(requested, challenged)
    source = "The vendor's 402 challenge prices it at" if challenged > requested else "The request prices it at"
    evidence = {"contract_cents": contract, "unit_cents": round(unit, 4), "tolerance_pct": sku.tolerance_pct}
    if unit <= limit + 1e-9:
        return _check("R06", True, Severity.escalate,
                      f"{_unit_money(unit)} per {sku.unit} is within {sku.tolerance_pct:g} percent of the "
                      f"{_unit_money(contract)} contract price.", **evidence)
    over = (unit - contract) / contract * 100
    return _check("R06", False, Severity.escalate,
                  f"{source} {_unit_money(unit)} per {sku.unit}, {fmt_pct(over)} percent above the "
                  f"{_unit_money(contract)} contract price.", role="budget_owner", **evidence)


def _unit_money(cents: float) -> str:
    if abs(cents - round(cents)) < 1e-6:
        return money(int(round(cents)))
    return f"${cents / 100:,.4f}".rstrip("0")


def r07_cap(item: WorkItem, agent: Optional[AgentConfig], amount: int) -> Check:
    if agent is None:
        return _check("R07", True, Severity.escalate, "Not applicable because no agent limits apply.")
    cap = agent.per_txn_cap_cents
    if amount <= cap:
        return _check("R07", True, Severity.escalate,
                      f"{money(amount)} is within {possessive(agent.name)} {money(cap)} limit per purchase.",
                      cap_cents=cap, amount_cents=amount)
    return _check("R07", False, Severity.escalate,
                  f"{money(amount)} is over {possessive(agent.name)} {money(cap)} limit per purchase.",
                  cap_cents=cap, amount_cents=amount, role="budget_owner")


def r08_budget(ctx: Ctx, item: WorkItem, agent: Optional[AgentConfig], amount: int, period: str) -> Check:
    if agent is None:
        return _check("R08", True, Severity.escalate, "Not applicable because no agent budget applies.")
    spent = ctx.store.sum_items("paid_cents", kind="purchase", agent_id=agent.actor_id, paid_period=period)
    committed = ctx.store.sum_items("exposure_cents", kind="purchase", agent_id=agent.actor_id, state=COMMITTED,
                                    exclude_id=item.id)
    budget = agent.monthly_budget_cents
    total = spent + committed + amount
    evidence = {"spent_cents": spent, "committed_cents": committed, "amount_cents": amount, "budget_cents": budget,
                "period": period}
    month = month_name(period)
    if total <= budget:
        return _check("R08", True, Severity.escalate,
                      f"{agent.name} has spent {money(spent)} of its {money(budget)} {month} budget with "
                      f"{money(committed)} waiting, so this fits.", **evidence)
    pct = total / budget * 100
    if pct > ctx.config.budget_hard_deny_pct:
        return _check("R08", False, Severity.deny,
                      f"This would bring {agent.name} to {money(total)} in {month}, {fmt_pct(pct)} percent of its "
                      f"{money(budget)} budget and past the {ctx.config.budget_hard_deny_pct} percent hard limit.",
                      **evidence)
    return _check("R08", False, Severity.escalate,
                  f"This would bring {agent.name} to {money(total)} in {month}, over its {money(budget)} budget.",
                  role="budget_owner", **evidence)


def r09_split(ctx: Ctx, item: WorkItem, agent: Optional[AgentConfig], vendor: Optional[Vendor], amount: int,
              duplicate_of: Optional[str]) -> Check:
    if agent is None:
        return _check("R09", True, Severity.escalate, "Not applicable because no agent limits apply.")
    since = iso(parse_ts(item.created_at) - timedelta(seconds=SPLIT_WINDOW_SECONDS))
    prior = ctx.store.query_items(
        kind="purchase", agent_id=agent.actor_id, vendor_id=item.vendor_id, created_from=since,
        created_to=item.created_at, not_state=NOT_COUNTED, exclude_id=item.id, order="created_at ASC, id ASC",
    )
    # An identical retry is the same order, not a second slice of a split one.
    prior = [p for p in prior if item_number(p.id) < item_number(item.id) and p.id != duplicate_of and not is_same_order(p, item)]
    vendor_name = vendor.name if vendor else item.vendor_id
    cap = agent.per_txn_cap_cents
    if not prior:
        return _check("R09", True, Severity.escalate,
                      f"This is {possessive(agent.name)} only purchase from {vendor_name} in the last hour.")
    total = amount + sum(exposure_cents(p) for p in prior)
    count = len(prior) + 1
    evidence = {"item_ids": [p.id for p in prior], "total_cents": total, "cap_cents": cap, "count": count}
    if total <= cap or amount > cap:
        detail = (f"{possessive(agent.name)} {count} purchases from {vendor_name} in the last hour total "
                  f"{money(total)}, within its {money(cap)} limit per purchase.")
        if amount > cap:
            detail = "Not treated as a split because this purchase alone is over the limit per purchase."
        return _check("R09", True, Severity.escalate, detail, **evidence)
    return _check("R09", False, Severity.escalate,
                  f"{agent.name} made {count} purchases from {vendor_name} in the last hour totaling {money(total)}, "
                  f"over its {money(cap)} limit per purchase.", role="budget_owner", **evidence)


def r10_purpose(item: WorkItem) -> Check:
    purpose = (item.purpose or "").strip()
    task = (item.task_ref or "").strip()
    problems = []
    if len(purpose) < MIN_PURPOSE_CHARS:
        problems.append("the purpose is missing or too short to review" if purpose else "there is no purpose")
    if not task:
        problems.append("there is no task reference")
    if not problems:
        return _check("R10", True, Severity.ask, f"The request states a purpose and task {task}.", task_ref=task)
    detail = "The request cannot be reviewed because " + " and ".join(problems) + "."
    return _check("R10", False, Severity.ask, detail, purpose_chars=len(purpose), task_ref=task or None)


def r11_instructions(item: WorkItem) -> Check:
    sources = [("memo", item.memo or "")]
    if item.challenge is not None:
        sources.append(("vendor's 402 description", item.challenge.description or ""))
    for label, text in sources:
        match = _INSTRUCTIONS.search(text)
        if match:
            snippet = _snippet(text, match.start(), match.end())
            return _check("R11", False, Severity.info,
                          f"The {label} says \"{snippet}\". Elix treats that as data and ignores the claim.",
                          source=label, matched=match.group(0), snippet=snippet)
    return _check("R11", True, Severity.info, "The memo and vendor text contain no approval claims or instructions.")


def _snippet(text: str, start: int, end: int, width: int = 60) -> str:
    left = max(0, text.rfind(".", 0, start) + 1)
    right = text.find(".", end)
    right = len(text) if right == -1 else right
    snippet = text[left:right].strip()
    if len(snippet) > 140:
        snippet = text[max(0, start - width):min(len(text), end + width)].strip()
    return snippet.rstrip(".")


def r12_policy(ctx: Ctx, item: WorkItem, agent: Optional[AgentConfig], vendor: Optional[Vendor],
               sku: Optional[Sku], amount: int) -> tuple[Check, Optional[Policy]]:
    if agent is None:
        return _check("R12", False, Severity.info, "No auto approve policy applies to this actor."), None
    pol = covering_policy(ctx, agent.actor_id, item.vendor_id, item.sku, amount)
    if pol is not None:
        return _check(
            "R12", True, Severity.escalate,
            f"Covered by {pol.id} v{pol.version}, which auto approves {agent.name} buying "
            f"{_product_words(ctx, vendor, sku)} up to {money(pol.params['max_amount_cents'])}.",
            policy_id=pol.id, policy_version=pol.version, max_amount_cents=pol.params["max_amount_cents"],
            precedent_count=pol.evidence.get("precedent_count"),
        ), pol
    default = agent.auto_approve_under_cents
    if amount < default:
        return _check("R12", True, Severity.escalate,
                      f"{money(amount)} is under {possessive(agent.name)} {money(default)} limit for purchases "
                      f"without review.", default_cents=default), None
    return _check("R12", False, Severity.info,
                  f"No auto approve policy covers this, and {money(amount)} is over {possessive(agent.name)} "
                  f"{money(default)} limit for purchases without review.", default_cents=default,
                  role="budget_owner"), None


def _product_words(ctx: Ctx, vendor: Optional[Vendor], sku: Optional[Sku]) -> str:
    from elix.engine.explain import product_phrase

    return product_phrase(ctx.config, vendor, sku.sku if sku else None)


# ---------------------------------------------------------------- evaluation


def evaluate_purchase(ctx: Ctx, item: WorkItem, actor_id: str) -> Evaluation:
    from elix.engine.explain import decision_summary

    agent = ctx.config.agent(item.requested_by)
    vendor = ctx.vendor(item.vendor_id)
    sku = find_sku(vendor, item.sku)
    amount = exposure_cents(item)
    now = ctx.clock.now()
    agent_name = agent.name if agent else ctx.config.actor_name(item.requested_by)

    dup_check, original = r04_duplicate(ctx, item, agent_name)
    r12, pol = r12_policy(ctx, item, agent, vendor, sku, amount)
    checks = [
        r01_permission(ctx, actor_id, agent),
        r02_vendor(item, vendor, sku),
        r03_payee(ctx, item, vendor, now),
        dup_check,
        r05_amount(item, agent_name),
        r06_price(item, sku),
        r07_cap(item, agent, amount),
        r08_budget(ctx, item, agent, amount, ctx.clock.period()),
        r09_split(ctx, item, agent, vendor, amount, original.id if original else None),
        r10_purpose(item),
        r11_instructions(item),
        r12,
    ]
    failed = {c.rule_id: c for c in checks if not c.passed}
    flags = [RULE_FLAGS[c.rule_id] for c in checks if not c.passed and c.rule_id in RULE_FLAGS]

    payee_hold = "R03" in failed
    escalation_roles = [Role(c.evidence.get("role", "budget_owner")) for c in failed.values()
                        if c.severity == Severity.escalate and c.rule_id != "R03"]
    # R11 and R12 are info checks. Neither changes the outcome on its own account, but either one means
    # nothing authorizes paying this without a person, so the spend needs at least the budget owner.
    not_automatable = "R11" in failed or "R12" in failed
    spend_roles = escalation_roles + ([Role.budget_owner] if not_automatable else [])
    spend_role = max(spend_roles, key=lambda r: ROLE_RANK[r]) if spend_roles else None

    if any(c.severity == Severity.deny for c in failed.values()):
        outcome, spend_role, payee_hold = Outcome.deny, None, False
    elif escalation_roles or payee_hold:
        outcome = Outcome.needs_approval
    elif any(c.severity == Severity.ask for c in failed.values()):
        outcome, spend_role = Outcome.needs_info, None
    elif not_automatable:
        outcome = Outcome.needs_approval
    else:
        outcome = Outcome.auto_approve
    if "R11" in failed:
        pol = None  # instructions in vendor text never earn automation from a learned policy

    required = None
    if outcome == Outcome.needs_approval:
        roles = [r for r in (spend_role, Role.controller if payee_hold else None) if r]
        required = max(roles, key=lambda r: ROLE_RANK[r])

    account, coding_policy = choose_account(ctx, item.requested_by, vendor, item.sku)
    summary = decision_summary(ctx, item, outcome, checks, spend_role, payee_hold,
                               pol if outcome == Outcome.auto_approve else None, original)
    decision = Decision(outcome=outcome, required_approver_role=required, checks=checks, summary=summary,
                        policy_version=policy_version(ctx), payee_hold=payee_hold)
    links = []
    if original is not None:
        links.append({"rel": "duplicate_of", "id": original.id})
    if pol is not None and outcome == Outcome.auto_approve:
        links.append({"rel": "policy", "id": pol.id})
    if coding_policy:
        links.append({"rel": "policy", "id": coding_policy})
    return Evaluation(
        decision=decision, flags=flags, spend_role=spend_role, payee_hold=payee_hold,
        duplicate_of=original.id if original else None, policy=pol if outcome == Outcome.auto_approve else None,
        account=account, question=R10_QUESTION if outcome == Outcome.needs_info else None, links=links,
    )
