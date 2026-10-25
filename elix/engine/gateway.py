"""The gateway is the only code that moves money or writes to the ledger.

It holds the rails (and through them the treasury wallet). Agents and humans
never call it directly. The facade hands it an item only after the state
machine says the item is APPROVED. A test asserts that no other module imports
`post_entry`, `reverse_entry` or calls `.pay(`.
"""

from __future__ import annotations

from elix.contracts import JournalEntry, JournalLine, PaymentIntent, Receipt, State, WorkItem
from elix.engine.clock import next_period, period_end_date
from elix.engine.context import GATEWAY, Ctx
from elix.engine.errors import Conflict
from elix.engine.explain import account_label
from elix.engine.fmt import money
from elix.engine.ledger import post_entry, reverse_entry
from elix.engine.periods import posting_date
from elix.engine.store import exposure_cents
from elix.engine.workflow import merged_data, transition

USDC_WALLET = "1015"
OPERATING_CASH = "1010"
ACCOUNTS_PAYABLE = "2010"
ACCRUED_EXPENSES = "2110"
VENDOR_DISPUTES = "2150"


def _payee_cleared(item: WorkItem) -> bool:
    if not item.data.get("payee_hold"):
        return True
    return bool(item.data.get("payee_verified"))


def _guard(item: WorkItem) -> None:
    if item.state != State.APPROVED:
        raise Conflict(f"The gateway only pays approved items and {item.id} is {item.state.value}.")
    if item.receipt is not None:
        raise Conflict(f"{item.id} already has a receipt, so the gateway will not pay it twice.")


def execute_purchase(ctx: Ctx, item: WorkItem) -> WorkItem:
    """Pay an approved purchase on its rail, then post it. Every step is its own audited transition."""
    _guard(item)
    if not _payee_cleared(item):
        raise Conflict(f"{item.id} still has an unverified payee change, so the gateway will not pay it.")
    vendor = ctx.vendor(item.vendor_id)
    if vendor is None:
        return _fail(ctx, item, f"{item.vendor_id} is not in the vendor master. Add the vendor before paying it.")

    intent = PaymentIntent(
        work_item_id=item.id, vendor_id=item.vendor_id, resource_url=item.resource_url,
        amount_cents=exposure_cents(item), sku=item.sku, quantity=item.quantity,
        idempotency_key=item.idempotency_key, pay_to_override=item.data.get("pay_to_override"),
    )
    rail_name = item.data.get("rail", "x402")
    try:
        receipt = ctx.rail(rail_name).pay(intent, item.challenge)
    except Exception as exc:  # the rail raises RailError; anything else is reported the same way
        return _fail(ctx, item, f"The {rail_name} rail refused the payment. {_error_text(exc)}")

    now = ctx.clock.now()
    item = transition(
        ctx, item, "pay", GATEWAY,
        patch={"receipt": receipt.model_dump(), "data": merged_data(item, paid_at=now)},
        reason=_paid_reason(ctx, item, receipt),
        evidence={"tx_ref": receipt.tx_ref, "payload_sha256": receipt.payload_sha256,
                  "signature_verified": receipt.signature_verified, "simulated": receipt.simulated,
                  "amount_cents": receipt.amount_cents},
    )
    credit = USDC_WALLET if receipt.rail == "x402" else OPERATING_CASH
    agent_name = ctx.config.actor_name(item.requested_by)
    memo = f"{item.title} for {agent_name}" + (f", task {item.task_ref}" if item.task_ref else "")
    with ctx.store.tx():
        entry = post_entry(ctx, date=posting_date(ctx), memo=memo, source_work_item=item.id, lines=[
            JournalLine(account=item.account or vendor.default_account, debit_cents=receipt.amount_cents,
                        cost_center=item.cost_center, vendor_id=item.vendor_id),
            JournalLine(account=credit, credit_cents=receipt.amount_cents, vendor_id=item.vendor_id),
        ])
        item = transition(
            ctx, item, "post", GATEWAY, patch={"journal_entry_ids": [*item.journal_entry_ids, entry.id]},
            reason=f"The gateway booked {money(receipt.amount_cents)} to {account_label(ctx.config, item.account)} "
                   f"in {entry.id}.",
            refs=[entry.id],
        )
    return item


def execute_invoice(ctx: Ctx, item: WorkItem) -> WorkItem:
    """Pay the undisputed part of an approved invoice by bank and book the whole invoice."""
    _guard(item)
    if "invoice" not in item.data or not item.data.get("reconciliation"):
        raise Conflict(f"{item.id} has no reconciled invoice, so the gateway will not pay it.")
    invoice = item.data["invoice"]
    pay_cents = int(item.data.get("pay_cents", item.amount_cents))
    dispute_cents = int(item.data.get("dispute_cents", 0))
    vendor = ctx.vendor(item.vendor_id)
    intent = PaymentIntent(work_item_id=item.id, vendor_id=item.vendor_id, amount_cents=pay_cents,
                           idempotency_key=f"invoice:{item.vendor_id}:{invoice['invoice_number']}")
    try:
        receipt = ctx.rail("bank").pay(intent, None)
    except Exception as exc:
        return _fail(ctx, item, f"The bank rail refused the payment. {_error_text(exc)}")

    now = ctx.clock.now()
    item = transition(
        ctx, item, "pay", GATEWAY,
        patch={"receipt": receipt.model_dump(), "data": merged_data(item, paid_at=now)},
        reason=(f"The gateway paid {money(pay_cents)} to {vendor.name if vendor else item.vendor_id} by bank"
                + (f" and held back {money(dispute_cents)} in dispute" if dispute_cents else "") + "."),
        evidence={"tx_ref": receipt.tx_ref, "simulated": receipt.simulated, "amount_cents": receipt.amount_cents},
    )
    total = int(invoice["total_cents"])
    lines = [JournalLine(account=item.account or (vendor.default_account if vendor else "6990"),
                         debit_cents=total - dispute_cents, cost_center=item.cost_center, vendor_id=item.vendor_id)]
    if dispute_cents:
        lines.append(JournalLine(account=VENDOR_DISPUTES, debit_cents=dispute_cents, vendor_id=item.vendor_id,
                                 memo="Disputed, waiting for a credit note"))
    lines.append(JournalLine(account=ACCOUNTS_PAYABLE, credit_cents=total, vendor_id=item.vendor_id))
    lines = [line for line in lines if line.debit_cents or line.credit_cents]
    with ctx.store.tx():
        bill = post_entry(ctx, date=posting_date(ctx), source_work_item=item.id, lines=lines,
                          memo=f"Invoice {invoice['invoice_number']} from {vendor.name if vendor else item.vendor_id}")
        payment = post_entry(ctx, date=posting_date(ctx), source_work_item=item.id, lines=[
            JournalLine(account=ACCOUNTS_PAYABLE, debit_cents=receipt.amount_cents, vendor_id=item.vendor_id),
            JournalLine(account=OPERATING_CASH, credit_cents=receipt.amount_cents, vendor_id=item.vendor_id),
        ], memo=f"Bank payment {receipt.tx_ref} for invoice {invoice['invoice_number']}")
        patch: dict = {"journal_entry_ids": [*item.journal_entry_ids, bill.id, payment.id]}
        if dispute_cents:
            patch["data"] = merged_data(item, dispute_status="open", dispute_open_cents=dispute_cents,
                                        credited_cents=0, credit_notes=[])
        item = transition(
            ctx, item, "post", GATEWAY, patch=patch,
            reason=(f"The gateway booked the {money(total)} invoice in {bill.id}"
                    + (f" with {money(dispute_cents)} in 2150 Vendor disputes" if dispute_cents else "")
                    + f" and the {money(receipt.amount_cents)} payment in {payment.id}."),
            refs=[bill.id, payment.id],
        )
    return item


def post_credit_note(ctx: Ctx, item: WorkItem, credit_number: str) -> WorkItem:
    """Book a vendor credit note recorded on an approved invoice: Dr 2010 Accounts payable, Cr 2150 Vendor disputes.

    The credit has already been checked and recorded on the item (credits.record). The entry posts today, or on
    the first day of the next open period when today's period is closed, and is linked to the invoice item.
    """
    if item.state != State.POSTED or not item.journal_entry_ids:
        raise Conflict(f"The gateway only books credit notes on approved and posted invoices, and {item.id} is "
                       f"{item.state.value}.")
    notes = list(item.data.get("credit_notes") or [])
    index = next((i for i, n in enumerate(notes) if n.get("credit_number") == credit_number), None)
    if index is None:
        raise Conflict(f"{item.id} has no credit note {credit_number} recorded.")
    note = dict(notes[index])
    if note.get("je_id"):
        raise Conflict(f"Credit note {credit_number} was already booked in {note['je_id']}.")
    applied = int(note["applied_cents"])
    if applied <= 0:
        raise Conflict(f"Credit note {credit_number} has nothing to book.")
    open_before = int(note["open_before_cents"])
    remaining = max(0, open_before - applied)
    vendor = ctx.vendor(item.vendor_id)
    name = vendor.name if vendor else item.vendor_id
    invoice_number = item.data.get("invoice_number")
    cost_center = note.get("cost_center")
    line_memo = f"Credit note {credit_number} for {invoice_number}"
    with ctx.store.tx():
        entry = post_entry(ctx, date=posting_date(ctx), source_work_item=item.id, lines=[
            JournalLine(account=ACCOUNTS_PAYABLE, debit_cents=applied, vendor_id=item.vendor_id,
                        cost_center=cost_center, memo=line_memo),
            JournalLine(account=VENDOR_DISPUTES, credit_cents=applied, vendor_id=item.vendor_id,
                        cost_center=cost_center, memo=line_memo),
        ], memo=f"Credit note {credit_number} from {name} against invoice {invoice_number}")
        note["je_id"] = entry.id
        note["posted_on"] = entry.date
        notes[index] = note
        credited = int(item.data.get("credited_cents") or 0) + applied
        status = "credited" if remaining == 0 else "partly_credited"
        item = transition(
            ctx, item, "post_credit", GATEWAY,
            patch={"journal_entry_ids": [*item.journal_entry_ids, entry.id],
                   "data": merged_data(item, credit_notes=notes, credited_cents=credited,
                                       dispute_open_cents=remaining, dispute_status=status)},
            reason=(f"The gateway booked credit note {credit_number} in {entry.id}, Dr 2010 Accounts payable and "
                    f"Cr 2150 Vendor disputes for {money(applied)}. "
                    + (f"The dispute on {invoice_number} is closed." if remaining == 0 else
                       f"{money(remaining)} of the dispute on {invoice_number} is still open.")),
            evidence={"credit_number": credit_number, "applied_cents": applied, "dispute_open_cents": remaining,
                      "dispute_status": status},
            refs=[entry.id],
        )
    return item


def post_accrual(ctx: Ctx, item: WorkItem) -> WorkItem:
    """Book an approved accrual on the last day of its period and its reversal on the first of the next."""
    _guard_post(item)
    period = item.period
    lines = [JournalLine(account=line["account"], debit_cents=int(line["amount_cents"]), vendor_id=item.vendor_id,
                         cost_center=item.cost_center, memo=line.get("description"))
             for line in item.data["lines"] if int(line["amount_cents"]) > 0]
    total = sum(line.debit_cents for line in lines)
    lines.append(JournalLine(account=ACCRUED_EXPENSES, credit_cents=total, vendor_id=item.vendor_id))
    vendor = ctx.vendor(item.vendor_id)
    name = vendor.name if vendor else item.vendor_id
    with ctx.store.tx():
        accrual = post_entry(ctx, date=period_end_date(period), source_work_item=item.id, lines=lines,
                             memo=f"Accrual for {name} usage in {period}")
        reversal = reverse_entry(ctx, accrual.id, date=f"{next_period(period)}-01", source_work_item=item.id,
                                 memo=f"Scheduled reversal of the {period} accrual for {name}")
        item = transition(
            ctx, item, "post", GATEWAY,
            patch={"journal_entry_ids": [accrual.id, reversal.id],
                   "data": merged_data(item, accrual_je=accrual.id, reversal_je=reversal.id)},
            reason=(f"The gateway posted the {money(total)} accrual for {name} in {accrual.id} and its reversal "
                    f"dated {next_period(period)}-01 in {reversal.id}."),
            refs=[accrual.id, reversal.id],
        )
    return item


def post_correction(ctx: Ctx, item: WorkItem) -> WorkItem:
    """Reverse the original entry and repost it with the corrected account, both dated today."""
    _guard_post(item)
    data = item.data
    original = ctx.store.get_entry(data["je_id"])
    assert original is not None
    corrected = [
        line.model_copy(update={"account": data["to_account"]}) if i == data["line_index"] else line
        for i, line in enumerate(original.lines)
    ]
    today = posting_date(ctx)
    with ctx.store.tx():
        reversal = reverse_entry(ctx, original.id, date=today, source_work_item=item.id,
                                 memo=f"Reversal of {original.id} for a recode")
        repost = post_entry(ctx, date=today, source_work_item=item.id, lines=corrected,
                            memo=f"Repost of {original.id} with line {data['line_index'] + 1} coded to "
                                 f"{data['to_account']} instead of {data['from_account']}")
        item = transition(
            ctx, item, "post", GATEWAY,
            patch={"journal_entry_ids": [reversal.id, repost.id],
                   "data": merged_data(item, reversal_je=reversal.id, repost_je=repost.id)},
            reason=(f"The gateway reversed {original.id} in {reversal.id} and reposted it to "
                    f"{account_label(ctx.config, data['to_account'])} in {repost.id}."),
            refs=[original.id, reversal.id, repost.id],
        )
    return item


def post_opening_balances(ctx: Ctx, *, date: str, lines: list[JournalLine], memo: str) -> JournalEntry:
    return post_entry(ctx, date=date, memo=memo, lines=lines)


def _guard_post(item: WorkItem) -> None:
    if item.state != State.APPROVED:
        raise Conflict(f"The gateway only posts approved items and {item.id} is {item.state.value}.")
    if item.journal_entry_ids:
        raise Conflict(f"{item.id} has already posted.")


def _fail(ctx: Ctx, item: WorkItem, error: str) -> WorkItem:
    return transition(ctx, item, "fail", GATEWAY, patch={"data": merged_data(item, error=error)},
                      reason=f"The payment for {item.id} failed. {error}", evidence={"error": error})


def _error_text(exc: Exception) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    text = text[0].upper() + text[1:]
    return text if text.endswith(".") else text + "."


def _paid_reason(ctx: Ctx, item: WorkItem, receipt: Receipt) -> str:
    vendor = ctx.vendor(item.vendor_id)
    name = vendor.name if vendor else item.vendor_id
    if receipt.rail == "x402":
        text = (f"The gateway paid {money(receipt.amount_cents)} to {name} over x402 with a signed EIP-3009 "
                f"authorization")
        if receipt.signature_verified:
            text += " that the facilitator verified"
        text += f". Settlement {receipt.tx_ref}"
        return text + (" was simulated." if receipt.simulated else ".")
    return (f"The gateway paid {money(receipt.amount_cents)} to {name} by bank, reference {receipt.tx_ref}"
            + (" (simulated)." if receipt.simulated else "."))
