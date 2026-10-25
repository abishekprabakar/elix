"""Recodes and corrections.

Posted entries are never edited. A recode becomes a correction item that, once
someone other than its proposer approves it, posts a reversal of the original
entry and a repost with the corrected account, both dated today. When the
original entry sits in a closed period, the refused in place edit is audited
(R30) and the fix lands in the open period. Every recode is also kept as a
precedent for the learner.
"""

from __future__ import annotations

from elix.contracts import Role, State, WorkItem, WorkItemKind
from elix.engine.context import Ctx
from elix.engine.errors import Conflict, Invalid, NotFound
from elix.engine.explain import account_label
from elix.engine.fmt import money, period_name
from elix.engine.periods import current_period, is_open
from elix.engine.permissions import can
from elix.engine.workflow import audit_denied, create, deny


def recode(ctx: Ctx, actor: str, je_id: str, line_index: int, new_account: str, note: str = "") -> WorkItem:
    from elix.engine.learner import learn_from_corrections

    verdict = can(ctx.config, actor, "recode")
    if not verdict.allowed:
        raise deny(ctx, actor=actor, action="recode", subject=je_id, reason=verdict.reason, rule_id=verdict.rule_id)
    entry = ctx.store.get_entry(je_id)
    if entry is None:
        raise NotFound(f"There is no journal entry called {je_id}.")
    if entry.reversed_by:
        raise Conflict(f"{je_id} was already reversed by {entry.reversed_by}. Recode the entry that replaced it instead.")
    if not 0 <= line_index < len(entry.lines):
        raise Invalid(f"{je_id} has no line {line_index + 1}.")
    line = entry.lines[line_index]
    target = ctx.config.account(new_account)
    source = ctx.config.account(line.account)
    if target is None:
        raise Invalid(f"Account {new_account} is not in the chart of accounts.")
    if target.type != "expense" or source is None or source.type != "expense":
        raise Invalid("Only expense lines can be recoded, and only to another expense account.")
    if new_account == line.account:
        raise Invalid(f"Line {line_index + 1} of {je_id} is already coded to {new_account}.")
    open_corrections = [i for i in ctx.store.query_items(kind="correction", state=["PROPOSED", "APPROVED"])
                        if i.data.get("je_id") == je_id]
    if open_corrections:
        raise Conflict(f"{je_id} already has a correction waiting, {open_corrections[0].id}.")

    source_item = ctx.store.get_item(entry.source_work_item) if entry.source_work_item else None
    agent_id = source_item.requested_by if source_item and source_item.requested_by.startswith("agent:") else None
    current = current_period(ctx)
    closed = not is_open(ctx, entry.period)
    from_label, to_label = account_label(ctx.config, line.account), account_label(ctx.config, new_account)

    if closed:
        audit_denied(
            ctx, actor=actor, action="edit", subject=je_id, rule_id="R30",
            reason=(f"{period_name(entry.period)} is closed, so the gateway refused to change {je_id} in place. "
                    f"Elix will post the fix in {period_name(current)} as a reversal and a repost."),
            evidence={"period": entry.period, "line_index": line_index, "to_account": new_account},
        )
        summary = (f"{period_name(entry.period)} is closed, so the fix lands in {period_name(current)}. Elix will "
                   f"reverse {je_id} and repost line {line_index + 1} to {to_label} instead of {from_label}.")
    else:
        summary = f"Elix will reverse {je_id} and repost line {line_index + 1} to {to_label} instead of {from_label}."

    amount = line.debit_cents or line.credit_cents
    item = WorkItem(
        id="", kind=WorkItemKind.correction, state=State.NEW, period=current, created_at="", updated_at="",
        requested_by=actor, proposed_by=actor, required_approver_role=Role.accountant,
        vendor_id=line.vendor_id, amount_cents=amount, account=new_account, cost_center=line.cost_center,
        title=f"Recode {je_id} line {line_index + 1} from {line.account} to {new_account}", simulated=True,
        links=[{"rel": "corrects", "id": je_id}, {"rel": "reverses", "id": je_id}],
        data={"je_id": je_id, "line_index": line_index, "from_account": line.account, "to_account": new_account,
              "original_period": entry.period, "closed_period": closed, "note": note, "summary": summary,
              "agent_id": agent_id, "source_work_item": entry.source_work_item},
    )
    name = ctx.config.actor_name(actor)
    with ctx.store.tx():
        item = create(
            ctx, item, actor=actor, action="recode",
            reason=(f"{name} asked to recode {money(amount)} on {je_id} from {from_label} to {to_label}"
                    + (f" with the note \"{note.rstrip('.')}.\"" if note else ".")),
            evidence={"rule_id": "R30"} if closed else {},
            refs=[r for r in (je_id, entry.source_work_item) if r],
        )
        ctx.store.insert_recode(at=ctx.clock.now(), actor=actor, je_id=je_id, line_index=line_index,
                                agent_id=agent_id, vendor_id=line.vendor_id, from_account=line.account,
                                to_account=new_account, correction_item=item.id)
    learn_from_corrections(ctx, triggered_by=actor)
    return item
