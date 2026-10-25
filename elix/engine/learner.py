"""The learner proposes policies from history and from corrections. It never activates them.

Thresholds, parameters and evidence are computed here in code. A proposal only
takes effect after the controller approves it, and the controller cannot approve
a policy they proposed.
"""

from __future__ import annotations

import math
from collections import defaultdict
from elix.contracts import FlagCode, Policy, State, WorkItem, WorkItemKind
from elix.engine import audit
from elix.engine.clock import period_end_date, period_start
from elix.engine.context import LEARNER, Ctx
from elix.engine.explain import account_label, product_phrase
from elix.engine.fmt import day, join_words, money, money_short, possessive
from elix.engine.periods import current_period
from elix.engine.workflow import create, merged_data, transition

MIN_PRECEDENTS = 25
MIN_RECODES = 3
PERCENTILE = 0.95
ROUND_TO_CENTS = 500
TOLERATED_FLAGS = {FlagCode.OVER_TXN_CAP}


def percentile(values: list[int], pct: float) -> int:
    """Nearest rank percentile."""
    ordered = sorted(values)
    rank = max(1, math.ceil(pct * len(ordered)))
    return ordered[rank - 1]


def round_up(cents: int, step: int = ROUND_TO_CENTS) -> int:
    return int(math.ceil(cents / step) * step)


def _human_spend_approvers(item: WorkItem) -> list[str]:
    return [a["actor"] for a in item.approvals if a.get("kind") == "spend" and a.get("actor", "").startswith("human:")]


def _same_policy_exists(ctx: Ctx, kind: str, scope: dict, params: dict) -> bool:
    for pol in ctx.store.policies(kind=kind):
        if pol.status != "retired" and pol.scope == scope and pol.params == params:
            return True
    return False


def learn_from_history(ctx: Ctx, triggered_by: str = LEARNER) -> list[Policy]:
    closed = sorted(p.id for p in ctx.store.periods() if p.status == "closed")
    if not closed:
        return []
    window_start = period_start(closed[0]).strftime("%Y-%m-%d")
    window_end = period_end_date(closed[-1])
    window = f"{window_start} to {window_end}"

    groups: dict[tuple[str, str, str], list[WorkItem]] = defaultdict(list)
    for item in ctx.store.query_items(kind="purchase", order="created_at ASC"):
        if item.period in closed and item.vendor_id and item.sku:
            groups[(item.requested_by, item.vendor_id, item.sku)].append(item)

    proposals: list[Policy] = []
    for (agent_id, vendor_id, sku), items in sorted(groups.items()):
        agent = ctx.config.agent(agent_id)
        vendor = ctx.vendor(vendor_id)
        if agent is None or vendor is None:
            continue
        rejected = [i for i in items if i.state == State.REJECTED]
        if rejected:
            continue
        payee_changed = any(FlagCode.PAYEE_CHANGED in i.flags for i in items) or (
            vendor.payout_address_changed_at is not None
            and window_start <= vendor.payout_address_changed_at[:10] <= window_end)
        if payee_changed:
            continue
        precedents = [i for i in items if i.state == State.POSTED and _human_spend_approvers(i)
                      and set(i.flags) <= TOLERATED_FLAGS]
        if len(precedents) < MIN_PRECEDENTS:
            continue
        amounts = [i.receipt.amount_cents if i.receipt else i.amount_cents for i in precedents]
        limit = min(round_up(percentile(amounts, PERCENTILE)), agent.per_txn_cap_cents)
        if limit <= agent.auto_approve_under_cents:
            continue
        scope = {"agent_id": agent_id, "vendor_id": vendor_id, "sku": sku}
        params = {"max_amount_cents": limit}
        if _same_policy_exists(ctx, "auto_approve", scope, params):
            continue
        approvers = sorted({a for i in precedents for a in _human_spend_approvers(i)})
        names = join_words([ctx.config.actor_name(a) for a in approvers])
        first, last = precedents[0].created_at, precedents[-1].created_at
        rationale = (f"Auto approve {agent.name} buying {product_phrase(ctx.config, vendor, sku)} up to "
                     f"{money_short(limit)}. {len(precedents)} purchases like this from {day(first)} to {day(last)} "
                     f"were approved by {names} and none were rejected. The largest was {money(max(amounts))}.")
        evidence = {
            "precedent_count": len(precedents), "sample_ids": [i.id for i in precedents[-5:]], "window": window,
            "max_seen_cents": max(amounts), "p95_cents": percentile(amounts, PERCENTILE), "rejections": 0,
            "approvers": approvers, "triggered_by": triggered_by,
        }
        proposals.append(_propose(ctx, "auto_approve", scope, params, rationale, evidence, triggered_by,
                                  title=f"Auto approve {agent.name} buying {product_phrase(ctx.config, vendor, sku)} "
                                        f"up to {money_short(limit)}",
                                  sample_ids=evidence["sample_ids"]))
    return proposals


def learn_from_corrections(ctx: Ctx, triggered_by: str = LEARNER) -> list[Policy]:
    rows = []
    for row in ctx.store.recodes():
        correction = ctx.store.get_item(row["correction_item"])
        if correction is not None and correction.state == State.REJECTED:
            continue
        rows.append(row)
    targets: dict[tuple, set[str]] = defaultdict(set)
    groups: dict[tuple, list] = defaultdict(list)
    for row in rows:
        targets[(row["agent_id"], row["vendor_id"], row["from_account"])].add(row["to_account"])
        groups[(row["agent_id"], row["vendor_id"], row["from_account"], row["to_account"])].append(row)

    proposals: list[Policy] = []
    for (agent_id, vendor_id, from_account, to_account), recodes in sorted(groups.items(), key=lambda kv: str(kv[0])):
        if not agent_id or not vendor_id or len(recodes) < MIN_RECODES:
            continue
        if len(targets[(agent_id, vendor_id, from_account)]) > 1:
            continue  # people disagree on where these belong, so there is nothing to learn yet
        scope = {"agent_id": agent_id, "vendor_id": vendor_id}
        params = {"account": to_account}
        if _same_policy_exists(ctx, "coding", scope, params):
            continue
        vendor = ctx.vendor(vendor_id)
        vendor_name = vendor.name if vendor else vendor_id
        agent_name = ctx.config.actor_name(agent_id)
        people = join_words(sorted({ctx.config.actor_name(r["actor"]) for r in recodes}))
        rationale = (f"Code {possessive(agent_name)} purchases from {vendor_name} to "
                     f"{account_label(ctx.config, to_account)} instead of {account_label(ctx.config, from_account)}. "
                     f"{people} recoded {len(recodes)} of these entries and none of the corrections were rejected.")
        evidence = {
            "precedent_count": len(recodes), "sample_ids": [r["correction_item"] for r in recodes][-5:],
            "journal_entries": [r["je_id"] for r in recodes], "from_account": from_account,
            "rejections": 0, "triggered_by": triggered_by,
        }
        proposals.append(_propose(ctx, "coding", scope, params, rationale, evidence, triggered_by,
                                  title=f"Code {possessive(agent_name)} {vendor_name} purchases to "
                                        f"{account_label(ctx.config, to_account)}",
                                  sample_ids=evidence["sample_ids"]))
    return proposals


def _propose(ctx: Ctx, kind: str, scope: dict, params: dict, rationale: str, evidence: dict, triggered_by: str,
             *, title: str, sample_ids: list[str]) -> Policy:
    now = ctx.clock.now()
    with ctx.store.tx():
        policy = Policy(id=f"pol-{ctx.store.next_counter('policy'):03d}", version=1, kind=kind, status="proposed",
                        scope=scope, params=params, rationale=rationale, evidence=evidence, proposed_by=LEARNER,
                        created_at=now)
        item = WorkItem(
            id="", kind=WorkItemKind.policy, state=State.NEW, period=current_period(ctx), created_at="",
            updated_at="", requested_by=LEARNER, proposed_by=LEARNER, required_approver_role="controller",
            vendor_id=scope.get("vendor_id"), sku=scope.get("sku"), title=title, simulated=True,
            amount_cents=int(params.get("max_amount_cents", 0)), account=params.get("account"),
            links=[{"rel": "precedent", "id": s} for s in sample_ids],
            data={"policy_id": policy.id, "rationale": rationale, "policy_kind": kind,
                  "agent_id": scope.get("agent_id"), "triggered_by": triggered_by},
        )
        item = create(ctx, item, actor=LEARNER, action="propose",
                      reason=f"The learner proposed {policy.id}. {rationale}",
                      evidence={"policy_id": policy.id, **evidence}, refs=[policy.id])
        policy = policy.model_copy(update={"work_item_id": item.id})
        ctx.store.save_policy(policy)
        _audit_policy(ctx, policy, LEARNER, "propose", f"{policy.id} was proposed and waits for the controller.",
                      {"status": [None, "proposed"]})
    return policy


def decide_policy(ctx: Ctx, actor: str, policy_id: str, approve: bool, note: str = "") -> Policy:
    from elix.engine.errors import NotFound

    policy = ctx.store.get_policy(policy_id)
    if policy is None or not policy.work_item_id:
        raise NotFound(f"There is no policy called {policy_id}.")
    item = ctx.store.get_item(policy.work_item_id)
    assert item is not None
    now = ctx.clock.now()
    name = ctx.config.actor_name(actor)
    with ctx.store.tx():
        if approve:
            transition(ctx, item, "approve", actor, patch={
                "approved_by": actor,
                "approvals": [*item.approvals, {"actor": actor, "role": "controller", "at": now, "note": note,
                                                "kind": "policy"}],
            }, reason=f"{name} approved {policy.id}" + (f" with the note \"{note.rstrip('.')}.\"" if note else "."),
                refs=[policy.id])
            policy = policy.model_copy(update={"status": "active", "approved_by": actor, "activated_at": now})
            _audit_policy(ctx, policy, actor, "activate", f"{policy.id} v{policy.version} is active.",
                          {"status": ["proposed", "active"], "approved_by": [None, actor]})
        else:
            transition(ctx, item, "reject", actor, patch={
                "data": merged_data(item, rejection={"actor": actor, "at": now, "note": note}),
            }, reason=f"{name} rejected {policy.id}" + (f" with the note \"{note.rstrip('.')}.\"" if note else "."),
                refs=[policy.id])
            policy = policy.model_copy(update={"status": "rejected"})
            _audit_policy(ctx, policy, actor, "reject", f"{policy.id} was rejected and will not be used.",
                          {"status": ["proposed", "rejected"]})
        ctx.store.save_policy(policy)
    return policy


def _audit_policy(ctx: Ctx, policy: Policy, actor: str, action: str, reason: str, diff: dict) -> None:
    event = audit.append_event(ctx.store, at=ctx.clock.now(), actor=actor, action=action, subject=policy.id,
                               allowed=True, reason=reason, diff=diff,
                               refs=[policy.work_item_id] if policy.work_item_id else [])
    payload, event_json = policy.model_dump(mode="json"), event.model_dump(mode="json")
    ctx.store.on_commit(lambda: (ctx.bus.publish("audit", event_json), ctx.bus.publish("policy", payload)))
