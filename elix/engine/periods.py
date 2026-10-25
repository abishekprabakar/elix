"""Accounting periods (calendar months). Closed periods never reopen."""

from __future__ import annotations

from elix.contracts import ClosePreview, Period, Role, State, WorkItem, WorkItemKind
from elix.engine import audit
from elix.engine.clock import next_period
from elix.engine.context import GATEWAY, Ctx
from elix.engine.fmt import period_name
from elix.engine.permissions import approver_name, can
from elix.engine.workflow import conflict, deny

BLOCKING_STATES = (State.NEW, State.PROPOSED, State.NEEDS_INFO, State.NEEDS_APPROVAL, State.APPROVED, State.PAID)


def ensure_period(ctx: Ctx, period_id: str) -> Period:
    """Return the period, opening it (audited) the first time anything touches it."""
    existing = ctx.store.get_period(period_id)
    if existing:
        return existing
    period = Period(id=period_id, status="open")
    with ctx.store.tx():
        ctx.store.save_period(period)
        event = audit.append_event(
            ctx.store, at=ctx.clock.now(), actor=GATEWAY, action="open_period", subject=period_id, allowed=True,
            reason=f"{period_name(period_id)} opened.", diff={"status": [None, "open"]},
        )
        payload, event_json = period.model_dump(mode="json"), event.model_dump(mode="json")
        ctx.store.on_commit(lambda: (ctx.bus.publish("audit", event_json), ctx.bus.publish("period", payload)))
    return period


def is_open(ctx: Ctx, period_id: str) -> bool:
    period = ctx.store.get_period(period_id)
    return period is None or period.status == "open"


def current_period(ctx: Ctx) -> str:
    """The clock's period, or the next open one if someone already closed it."""
    period = ctx.clock.period()
    while not is_open(ctx, period):
        period = next_period(period)
    return period


def posting_date(ctx: Ctx) -> str:
    """Today, unless today's period is closed. Then the first day of the next open period."""
    period = current_period(ctx)
    return ctx.clock.today() if period == ctx.clock.period() else f"{period}-01"


def blockers(ctx: Ctx, period_id: str) -> list[dict]:
    """Items that must be resolved before the period can close."""
    found: list[dict] = []
    items = ctx.store.query_items(period=period_id, state=[s.value for s in BLOCKING_STATES], order="created_at ASC")
    for item in items:
        if item.kind == WorkItemKind.policy:
            continue
        found.append({"item_id": item.id, "reason": blocker_reason(ctx, item)})
    return found


def blocker_reason(ctx: Ctx, item: WorkItem) -> str:
    agent_id = item.data.get("agent_id")
    title = item.title or item.id
    if item.kind == WorkItemKind.accrual:
        if item.state == State.APPROVED:
            return f"{item.id} is an approved accrual that has not posted yet."
        return f"{item.id} is an accrual waiting for {approver_name(ctx.config, Role.controller, None)} to approve it."
    if item.state == State.NEEDS_INFO and (item.data.get("intake") or {}).get("status") == "failed":
        return (f"{item.id} is an invoice PDF that failed its intake checks. "
                f"{approver_name(ctx.config, Role.accountant, None)} has to reject it.")
    if item.state == State.NEEDS_INFO:
        return f"{item.id} is waiting for {ctx.config.actor_name(agent_id or item.requested_by)} to answer a question about {title}."
    if item.state == State.NEEDS_APPROVAL:
        role = item.required_approver_role
        who = approver_name(ctx.config, role, agent_id)
        if item.data.get("payee_hold") and not item.data.get("payee_verified"):
            return f"{item.id} is held until {approver_name(ctx.config, role, None)} verifies the payee for {title}."
        return f"{item.id} is waiting for {who} to approve {title}."
    if item.state in (State.APPROVED, State.PAID):
        return f"{item.id} was approved but has not been paid and posted yet."
    return f"{item.id} is still open ({item.state.value.replace('_', ' ').lower()})."


def close_preview(ctx: Ctx, actor: str, period_id: str) -> ClosePreview:
    from elix.engine.accruals import propose_accruals

    verdict = can(ctx.config, actor, "close_preview")
    if not verdict.allowed:
        raise deny(ctx, actor=actor, action="close_preview", subject=period_id, reason=verdict.reason,
                   rule_id=verdict.rule_id)
    period = ctx.store.get_period(period_id)
    if period is not None and period.status == "closed":
        raise conflict(ctx, actor=actor, action="close_preview", subject=period_id,
                       reason=f"{period_name(period_id)} is already closed.")
    accruals = propose_accruals(ctx, period_id, requested_by=actor)
    found = blockers(ctx, period_id)
    return ClosePreview(period=period_id, accruals=accruals, blockers=found, can_close=not found)


def close(ctx: Ctx, actor: str, period_id: str) -> Period:
    verdict = can(ctx.config, actor, "close")
    if not verdict.allowed:
        raise deny(ctx, actor=actor, action="close", subject=period_id, reason=verdict.reason, rule_id=verdict.rule_id)
    period = ensure_period(ctx, period_id)
    if period.status == "closed":
        raise conflict(ctx, actor=actor, action="close", subject=period_id,
                       reason=f"{period_name(period_id)} is already closed.")
    found = blockers(ctx, period_id)
    if found:
        raise conflict(
            ctx, actor=actor, action="close", subject=period_id,
            reason=f"{period_name(period_id)} cannot close while {len(found)} "
                   f"{'item is' if len(found) == 1 else 'items are'} still open.",
            evidence={"blockers": found}, details=found,
        )
    closed = Period(id=period_id, status="closed", closed_by=actor, closed_at=ctx.clock.now())
    with ctx.store.tx():
        ctx.store.save_period(closed)
        event = audit.append_event(
            ctx.store, at=ctx.clock.now(), actor=actor, action="close", subject=period_id, allowed=True,
            reason=f"{ctx.config.actor_name(actor)} closed {period_name(period_id)}.",
            diff={"status": ["open", "closed"], "closed_by": [None, actor]},
        )
        payload, event_json = closed.model_dump(mode="json"), event.model_dump(mode="json")
        ctx.store.on_commit(lambda: (ctx.bus.publish("audit", event_json), ctx.bus.publish("period", payload)))
    ensure_period(ctx, next_period(period_id))
    return closed


def reopen(ctx: Ctx, actor: str, period_id: str) -> None:
    verdict = can(ctx.config, actor, "reopen")
    raise deny(ctx, actor=actor, action="reopen", subject=period_id, reason=verdict.reason, rule_id=verdict.rule_id)
