"""Double entry ledger.

`post_entry` is the only way a journal entry is written, and only the gateway
calls it (a test greps for this). Entries must balance, are never updated or
deleted, and are refused when their date falls in a closed period (R30).
"""

from __future__ import annotations

from typing import Optional

from elix.contracts import AccountBalance, JournalEntry, JournalLine
from elix.engine import audit
from elix.engine.clock import period_of
from elix.engine.context import GATEWAY, Ctx
from elix.engine.errors import Invalid, PeriodClosed
from elix.engine.fmt import money, period_name
from elix.engine.periods import ensure_period
from elix.engine.permissions import can
from elix.engine.workflow import audit_denied, deny

NORMAL_DEBIT = {"asset", "expense"}


def validate_lines(ctx: Ctx, lines: list[JournalLine]) -> int:
    if not lines:
        raise Invalid("A journal entry needs at least one line.")
    debits = credits = 0
    for line in lines:
        if ctx.config.account(line.account) is None:
            raise Invalid(f"Account {line.account} is not in the chart of accounts.")
        if line.debit_cents < 0 or line.credit_cents < 0:
            raise Invalid("Journal amounts are never negative.")
        if (line.debit_cents > 0) == (line.credit_cents > 0):
            raise Invalid("Each journal line is either a debit or a credit.")
        debits += line.debit_cents
        credits += line.credit_cents
    if debits != credits:
        raise Invalid(f"The entry does not balance. Debits are {money(debits)} and credits are {money(credits)}.")
    return debits


def post_entry(
    ctx: Ctx,
    *,
    date: str,
    memo: str,
    lines: list[JournalLine],
    source_work_item: Optional[str] = None,
    reverses: Optional[str] = None,
    actor: str = GATEWAY,
) -> JournalEntry:
    verdict = can(ctx.config, actor, "post")
    if not verdict.allowed:
        raise deny(ctx, actor=actor, action="post", subject=source_work_item or "ledger",
                   reason=verdict.reason, rule_id=verdict.rule_id)
    total = validate_lines(ctx, lines)
    period = period_of(date)
    with ctx.store.tx():
        status = ensure_period(ctx, period)
        if status.status == "closed":
            reason = (f"{period_name(period)} is closed, so the gateway refused to post "
                      f"{'a correction' if reverses else 'this entry'} dated {date}.")
            exc = PeriodClosed(reason)
            audit_denied(ctx, actor=actor, action="post", subject=source_work_item or period, reason=reason,
                         rule_id="R30", evidence={"date": date, "period": period}, exc=exc)
            raise exc
        if reverses:
            original = ctx.store.get_entry(reverses)
            if original is None:
                raise Invalid(f"{reverses} does not exist.")
            if original.reversed_by:
                raise Invalid(f"{reverses} was already reversed by {original.reversed_by}.")
        entry = JournalEntry(
            id=f"JE-{period}-{ctx.store.next_counter('je:' + period):04d}", date=date, period=period, memo=memo,
            lines=lines, source_work_item=source_work_item, reverses=reverses, posted_by=GATEWAY,
            created_at=ctx.clock.now(),
        )
        ctx.store.insert_entry(entry)
        event = audit.append_event(
            ctx.store, at=ctx.clock.now(), actor=actor, action="post", subject=entry.id, allowed=True,
            reason=f"The gateway posted {entry.id} for {money(total)}. {memo}".strip(),
            evidence={"date": date, "lines": [line.model_dump(mode="json") for line in lines],
                      "source_work_item": source_work_item, "reverses": reverses},
            refs=[r for r in (source_work_item, reverses) if r],
        )
        events = [event]
        if reverses:
            ctx.store.set_reversed_by(reverses, entry.id)
            events.append(audit.append_event(
                ctx.store, at=ctx.clock.now(), actor=actor, action="mark_reversed", subject=reverses, allowed=True,
                reason=f"{reverses} was reversed by {entry.id}.", diff={"reversed_by": [None, entry.id]},
                refs=[entry.id, *( [source_work_item] if source_work_item else [])],
            ))
        entry_json = entry.model_dump(mode="json")
        event_json = [e.model_dump(mode="json") for e in events]

        def publish() -> None:
            for e in event_json:
                ctx.bus.publish("audit", e)
            ctx.bus.publish("journal", entry_json)

        ctx.store.on_commit(publish)
    return entry


def reversal_lines(entry: JournalEntry) -> list[JournalLine]:
    return [line.model_copy(update={"debit_cents": line.credit_cents, "credit_cents": line.debit_cents})
            for line in entry.lines]


def reverse_entry(ctx: Ctx, je_id: str, *, date: str, memo: str, source_work_item: Optional[str] = None,
                  actor: str = GATEWAY) -> JournalEntry:
    original = ctx.store.get_entry(je_id)
    if original is None:
        raise Invalid(f"{je_id} does not exist.")
    return post_entry(ctx, date=date, memo=memo, lines=reversal_lines(original),
                      source_work_item=source_work_item, reverses=je_id, actor=actor)


def balances(ctx: Ctx, period: str, cumulative: bool = True) -> list[AccountBalance]:
    """Account balances. Cumulative balances run through the end of the period (a trial balance)."""
    totals = ctx.store.account_totals(through_period=period) if cumulative else ctx.store.account_totals(period=period)
    out: list[AccountBalance] = []
    for account in ctx.config.accounts:
        debit, credit = totals.get(account.code, (0, 0))
        balance = debit - credit if account.type in NORMAL_DEBIT else credit - debit
        out.append(AccountBalance(account=account.code, name=account.name, type=account.type,
                                  debit_cents=debit, credit_cents=credit, balance_cents=balance))
    return out


def trial_balance_ok(ctx: Ctx, period: str, cumulative: bool = True) -> bool:
    rows = balances(ctx, period, cumulative)
    return sum(r.debit_cents for r in rows) == sum(r.credit_cents for r in rows)
