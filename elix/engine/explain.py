"""Deterministic sentences for decisions, work items and audit trails.

Everything a person reads about a decision comes from these templates and the
numbers the rules computed. Plain sentences, no em dashes, no colon setups.
"""

from __future__ import annotations

from typing import Optional

from elix.contracts import (
    AuditEvent, Check, Outcome, Policy, Role, Severity, State, Vendor, WorkItem, WorkItemKind,
)
from elix.engine.context import Ctx, WorldConfig
from elix.engine.disputes import credit_notes, dispute_open_cents
from elix.engine.fmt import day, day_long, join_words, money, money_short, period_name, possessive, qty, short_hash
from elix.engine.permissions import approver_name

# ---------------------------------------------------------------- products and titles


def nouns(config: WorldConfig, vendor_id: Optional[str], sku: Optional[str]) -> tuple[str, str, float]:
    found = config.sku_nouns.get((vendor_id or "", sku or ""))
    if found:
        return found
    label = sku or "item"
    return (label, label if label.endswith("s") else label + "s", 1.0)


def quantity_phrase(config: WorldConfig, vendor_id: Optional[str], sku: Optional[str], quantity: Optional[float]) -> str:
    singular, plural_form, multiplier = nouns(config, vendor_id, sku)
    amount = (quantity or 0) * multiplier
    if singular.startswith("the ") and abs((quantity or 0) - 1) < 1e-9:
        return singular
    word = singular.removeprefix("the ") if abs(amount - 1) < 1e-9 else plural_form
    return f"{qty(amount)} {word}"


def product_phrase(config: WorldConfig, vendor: Optional[Vendor], sku: Optional[str]) -> str:
    vendor_id = vendor.id if vendor else None
    _, plural_form, _ = nouns(config, vendor_id, sku)
    name = vendor.name if vendor else (vendor_id or "an unknown vendor")
    return f"{plural_form} from {name}"


def item_title(config: WorldConfig, vendor: Optional[Vendor], vendor_id: Optional[str], sku: Optional[str],
               quantity: Optional[float]) -> str:
    name = vendor.name if vendor else (vendor_id or "an unknown vendor")
    phrase = quantity_phrase(config, vendor.id if vendor else vendor_id, sku, quantity)
    title = f"{phrase} from {name}"
    return title[0].upper() + title[1:]


def account_label(config: WorldConfig, code: Optional[str]) -> str:
    if not code:
        return "no account"
    account = config.account(code)
    return f"{code} {account.name}" if account else code


# ---------------------------------------------------------------- decisions


def decision_summary(ctx: Ctx, item: WorkItem, outcome: Outcome, checks: list[Check], spend_role: Optional[Role],
                     payee_hold: bool, policy: Optional[Policy], original: Optional[WorkItem]) -> str:
    config = ctx.config
    agent_id = item.requested_by
    agent_name = config.actor_name(agent_id)
    failed = [c for c in checks if not c.passed]
    by_rule = {c.rule_id: c for c in checks}

    if outcome == Outcome.deny:
        if original is not None and not by_rule["R04"].passed:
            return f"Denied as a duplicate of {original.id}. {by_rule['R04'].detail}"
        first = next(c for c in failed if c.severity == Severity.deny)
        return f"Denied. {first.detail}"

    if outcome == Outcome.needs_info:
        return f"Waiting on {agent_name} to explain the purchase. {by_rule['R10'].detail}"

    if outcome == Outcome.needs_approval:
        parts: list[str] = []
        if payee_hold:
            parts.append(f"Held for payee verification. {by_rule['R03'].detail}")
        escalations = [c for c in failed if c.severity == Severity.escalate and c.rule_id != "R03"]
        reasons = [c.detail for c in escalations]
        if not reasons and not by_rule["R12"].passed:
            reasons.append(by_rule["R12"].detail)
        if not by_rule["R11"].passed:
            reasons.append(by_rule["R11"].detail)
        if spend_role:
            who = approver_name(config, spend_role, agent_id)
            lead = f"The spend also needs approval from {who}." if payee_hold else f"Needs approval from {who}."
            parts.append(" ".join([lead, *reasons]))
        elif payee_hold:
            controller = approver_name(config, Role.controller, None)
            parts.append(f"{controller} has to verify the new address before Elix pays.")
            if not by_rule["R11"].passed:
                parts.append(by_rule["R11"].detail)
        return " ".join(parts)

    if policy is not None:
        evidence = policy.evidence
        approvers = join_words([config.actor_name(a) for a in evidence.get("approvers", [])]) or "a person"
        vendor = ctx.vendor(item.vendor_id)
        return (f"Auto approved under {policy.id} v{policy.version}. {agent_name} buys "
                f"{product_phrase(config, vendor, policy.scope.get('sku'))} up to "
                f"{money_short(policy.params['max_amount_cents'])} and {evidence.get('precedent_count', 0)} earlier "
                f"purchases like this were approved by {approvers} with none rejected.")
    agent = config.agent(agent_id)
    default = agent.auto_approve_under_cents if agent else 0
    amount = max(item.amount_cents, item.challenge.amount_cents if item.challenge else 0)
    return (f"Auto approved because {money(amount)} is under {possessive(agent_name)} {money(default)} limit for "
            f"purchases without review and every check passed.")


# ---------------------------------------------------------------- work item explanations


def _approval_note(config: WorldConfig, approval: dict) -> str:
    who = config.actor_name(approval.get("actor"))
    when = day(approval["at"]) if approval.get("at") else ""
    kind = approval.get("kind", "spend")
    what = {"spend": "approved the spend", "payee": "verified the new payout address",
            "invoice": "approved the invoice", "policy": "approved the policy",
            "correction": "approved the correction", "accrual": "approved the accrual"}.get(kind, "approved it")
    return f"{who} {what} on {when}."


def rail_phrase(item: WorkItem) -> str:
    if item.receipt is None:
        return ""
    if item.receipt.rail == "x402":
        return "over x402 in USDC"
    return "by bank"


def how_approved(config: WorldConfig, item: WorkItem) -> str:
    human = [a for a in item.approvals if a.get("kind") in ("spend", "payee", "invoice", "correction", "accrual")]
    if human:
        return " ".join(_approval_note(config, a) for a in human)
    if item.approved_by == "system:policy" and item.decision is not None:
        return item.decision.summary
    return ""


def item_explanation(ctx: Ctx, item: WorkItem) -> str:
    config = ctx.config
    state = item.state
    summary = item.decision.summary if item.decision else ""

    if state == State.REJECTED:
        rejection = item.data.get("rejection") or {}
        who = config.actor_name(rejection.get("actor"))
        note = rejection.get("note")
        when = day(rejection["at"]) if rejection.get("at") else ""
        text = f"{who} rejected this on {when}"
        return text + (f" with the note \"{note.rstrip('.')}.\"" if note else ".")
    if state == State.FAILED:
        return f"The payment failed and nothing was paid. {item.data.get('error', '')}".strip()

    if item.kind == WorkItemKind.purchase:
        if state == State.NEEDS_APPROVAL:
            progress = " ".join(_approval_note(config, a) for a in item.approvals)
            remaining = []
            if item.data.get("payee_hold") and not item.data.get("payee_verified"):
                remaining.append(f"{approver_name(config, Role.controller, None)} still has to verify the payee.")
            if item.data.get("spend_role") and not item.data.get("spend_approved"):
                who = approver_name(config, Role(item.data["spend_role"]), item.requested_by)
                remaining.append(f"{who} still has to approve the spend.")
            if progress:
                return " ".join([progress, *remaining])
            return summary
        if state == State.NEEDS_INFO:
            open_q = next((q for q in item.questions if q.answer is None), None)
            return summary if open_q is None or open_q.asked_by == "system:policy" else (
                f"{config.actor_name(open_q.asked_by)} asked {config.actor_name(open_q.to)} a question and is "
                f"waiting for the answer.")
        if state == State.DENIED:
            return summary
        if state in (State.PAID, State.POSTED) and item.receipt is not None:
            vendor = ctx.vendor(item.vendor_id)
            name = vendor.name if vendor else item.vendor_id
            text = (f"Paid {money(item.receipt.amount_cents)} to {name} {rail_phrase(item)} on "
                    f"{day(item.data.get('paid_at') or item.receipt.settled_at)}")
            if state == State.POSTED and item.journal_entry_ids:
                text += f" and booked to {account_label(config, item.account)} in {item.journal_entry_ids[0]}"
            text += "."
            if item.receipt.simulated:
                text += " Settlement was simulated."
            approved = how_approved(config, item)
            return f"{text} {approved}".strip()
        if state == State.APPROVED:
            return "Approved. The gateway is paying it now."
        return summary

    if item.kind == WorkItemKind.invoice:
        recon = item.data.get("reconciliation") or {}
        text = recon.get("summary", summary)
        if state == State.NEEDS_APPROVAL:
            return f"{text} Waiting for an accountant to approve."
        if state == State.POSTED:
            pay = item.data.get("pay_cents", item.amount_cents)
            dispute = item.data.get("dispute_cents", 0)
            approved = how_approved(config, item) or (item.decision.summary if item.decision else "")
            text = f"{approved} The gateway paid {money(pay)} by bank"
            if dispute:
                text += f" and booked the disputed {money(dispute)} to 2150 Vendor disputes"
            text += "."
            credits = credits_paragraph(ctx, item)
            if credits:
                text += " " + credits
            return text
        if state == State.DENIED:
            return summary
        return text

    if item.kind == WorkItemKind.accrual:
        sentence = item.data.get("sentence", "")
        if state == State.POSTED:
            reversal = item.data.get("reversal_je")
            return (f"{sentence} Posted in {item.journal_entry_ids[0] if item.journal_entry_ids else 'the ledger'}"
                    f"{' and reversed on the first of next month in ' + reversal if reversal else ''}.")
        return f"{sentence} Waiting for {approver_name(config, Role.controller, None)} to approve it."

    if item.kind == WorkItemKind.correction:
        text = item.data.get("summary", "")
        if state == State.POSTED:
            data = item.data
            lead = (f"{period_name(data['original_period'])} was closed, so the fix landed in {period_name(item.period)}. "
                    if data.get("closed_period") else "")
            approver = config.actor_name(item.approved_by)
            return (f"{lead}{approver} approved the correction and the gateway reversed {data['je_id']} in "
                    f"{data.get('reversal_je')} and reposted line {data['line_index'] + 1} to "
                    f"{account_label(config, data['to_account'])} in {data.get('repost_je')}.")
        return f"{text} Waiting for approval from an accountant or the controller who did not propose it."

    if item.kind == WorkItemKind.policy:
        status = {State.PROPOSED: "Waiting for the controller to approve it.",
                  State.APPROVED: "The controller approved it, so it is active.",
                  State.REJECTED: "The controller rejected it."}.get(state, "")
        return f"{item.data.get('rationale', '')} {status}".strip()
    return summary


# ---------------------------------------------------------------- audit trail sentences


def event_sentence(config: WorldConfig, event: AuditEvent) -> str:
    if not event.allowed:
        action = event.action.removeprefix("denied:").replace("_", " ")
        who = config.actor_name(event.actor)
        return f"{who} tried to {action} {event.subject} and was refused. {event.reason}"
    return event.reason


def event_rule_ids(event: AuditEvent) -> list[str]:
    ids: list[str] = []
    evidence = event.evidence or {}
    if evidence.get("rule_id") and evidence["rule_id"] != "STATE":
        ids.append(evidence["rule_id"])
    for rule in evidence.get("failed_rules", []) or []:
        if rule not in ids:
            ids.append(rule)
    for rule in evidence.get("rules", []) or []:
        if rule not in ids:
            ids.append(rule)
    return ids


def why_answer(ctx: Ctx, subject: str, item: Optional[WorkItem], events: list[AuditEvent]) -> tuple[str, str]:
    """Title and a plain paragraph that answers why the subject is in its current state."""
    config = ctx.config
    actors = []
    for e in events:
        name = config.actor_name(e.actor)
        if name.startswith("Elix "):
            name = "the " + name
        if name not in actors:
            actors.append(name)
    denied = sum(1 for e in events if not e.allowed)
    trail = (f"The trail has {len(events)} {'event' if len(events) == 1 else 'events'} from {join_words(actors)}"
             f"{f', including {denied} refused attempt' + ('s' if denied != 1 else '') if denied else ''}.")

    if item is None:
        entry = ctx.store.get_entry(subject)
        if entry is not None:
            total = sum(l.debit_cents for l in entry.lines)
            text = (f"{entry.id} is a {money(total)} journal entry dated {day_long(entry.date)} that the gateway posted"
                    f"{' for ' + entry.source_work_item if entry.source_work_item else ''}.")
            if entry.reverses:
                text += f" It reverses {entry.reverses}."
            if entry.reversed_by:
                text += f" It was later reversed by {entry.reversed_by}."
            return f"Journal entry {entry.id}", f"{text} {trail}"
        policy = ctx.store.get_policy(subject)
        if policy is not None:
            status = {"active": f"It is active, approved by {config.actor_name(policy.approved_by)}.",
                      "proposed": "It is waiting for the controller.",
                      "rejected": "The controller rejected it.", "retired": "It was retired."}[policy.status]
            return f"Policy {policy.id}", f"{policy.rationale} {status} {trail}"
        period = ctx.store.get_period(subject)
        if period is not None:
            status = (f"{period_name(period.id)} was closed by {config.actor_name(period.closed_by)} on "
                      f"{day(period.closed_at)}." if period.status == "closed" else f"{period_name(period.id)} is open.")
            return period_name(period.id), f"{status} {trail}"
        return subject, f"Elix has no record called {subject}."

    title = item.title or item.id
    if item.kind == WorkItemKind.invoice and item.state == State.POSTED and item.data.get("dispute_cents"):
        return title, f"{invoice_short_pay_answer(ctx, item)} {trail}"
    return title, f"{title} is {state_words(item)}. {item_explanation(ctx, item)} {trail}"


def state_words(item: WorkItem) -> str:
    if item.state == State.NEEDS_APPROVAL and item.data.get("payee_hold") and not item.data.get("payee_verified"):
        return "held for payee verification"
    return {
        State.NEW: "new", State.PROPOSED: "being evaluated", State.NEEDS_INFO: "waiting for information",
        State.NEEDS_APPROVAL: "waiting for approval", State.APPROVED: "approved", State.REJECTED: "rejected",
        State.DENIED: "denied", State.PAID: "paid", State.POSTED: "paid and posted" if item.receipt else "posted",
        State.FAILED: "failed",
    }[item.state]


def invoice_short_pay_answer(ctx: Ctx, item: WorkItem) -> str:
    config = ctx.config
    invoice = item.data.get("invoice", {})
    recon = item.data.get("reconciliation", {})
    vendor = ctx.vendor(item.vendor_id)
    name = vendor.name if vendor else item.vendor_id
    pay, dispute = item.data.get("pay_cents", 0), item.data.get("dispute_cents", 0)
    text = (f"{name} invoice {invoice.get('invoice_number')} billed {money(invoice.get('total_cents'))} and Kestrel "
            f"paid {money(pay)}, {money(dispute)} short.")
    findings = [line.get("finding") for line in recon.get("lines", []) if line.get("variance_cents")]
    if findings:
        text += " " + " ".join(findings)
    approver = next((a for a in item.approvals if a.get("kind") == "invoice"), None)
    credits = credits_paragraph(ctx, item)
    remaining = dispute_open_cents(item)
    if approver and not credits:
        text += (f" {config.actor_name(approver['actor'])} approved the short pay on {day(approver['at'])}, "
                 f"the gateway paid {money(pay)} by bank, and the disputed {money(dispute)} sits in 2150 Vendor "
                 f"disputes until {name} issues a credit note.")
    elif approver:
        text += (f" {config.actor_name(approver['actor'])} approved the short pay on {day(approver['at'])}, "
                 f"the gateway paid {money(pay)} by bank, and the disputed {money(dispute)} went to 2150 Vendor "
                 f"disputes.")
    if credits:
        text += " " + credits
        if remaining:
            text += f" {money(remaining)} is still in 2150 Vendor disputes until {name} credits the rest."
    return text


def credit_sentence(ctx: Ctx, item: WorkItem, note: dict) -> str:
    """Plain sentences for one vendor credit note applied to an invoice's dispute."""
    vendor = ctx.vendor(item.vendor_id)
    name = vendor.name if vendor else item.vendor_id
    invoice_number = item.data.get("invoice_number")
    applied = int(note.get("applied_cents") or 0)
    open_before = int(note.get("open_before_cents") or applied)
    disputed = int(note.get("disputed_cents") or item.data.get("dispute_cents") or open_before)
    remaining = max(0, open_before - applied)
    text = (f"{name} issued credit note {note.get('credit_number')} for {money(note.get('amount_cents'))} against "
            f"{invoice_number}.")
    booked = "booked Dr 2010 Accounts payable, Cr 2150 Vendor disputes"
    if remaining == 0:
        if note.get("rounding_cents"):
            return text + (f" It was within a cent of the {money(open_before)} still open, so Elix closed the "
                           f"dispute and {booked} for {money(applied)}.")
        if open_before == disputed:
            return text + f" It matched the open dispute, so Elix closed it and {booked}."
        return text + f" It matched the {money(open_before)} still open, so Elix closed the dispute and {booked}."
    return text + (f" It covers part of the {money(open_before)} still open, so Elix {booked} for {money(applied)} "
                   f"and left {money(remaining)} open.")


def credits_paragraph(ctx: Ctx, item: WorkItem) -> str:
    """Every credit note the gateway has booked against this invoice, oldest first."""
    return " ".join(credit_sentence(ctx, item, n) for n in credit_notes(item) if n.get("je_id"))


def receipt_ref(item: WorkItem) -> str:
    if item.receipt is None:
        return item.id
    return f"{item.id}, settlement {short_hash(item.receipt.tx_ref, 12)}"
