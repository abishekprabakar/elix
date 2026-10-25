"""Checks on an invoice read from a PDF, in deterministic code.

An extractor (Claude, or the offline text parser) only proposes what the PDF
says. This module decides whether Elix accepts it. Every check is pass or fail
with a plain reason, and only when all of them pass does the proposal become a
VendorInvoice that goes through the same ingestion and reconciliation as a
structured invoice. Otherwise the PDF becomes a work item waiting for the
accountant, and nothing is reconciled, paid or posted.

    R40  the fields Elix needs could be read
    R41  the vendor is in the vendor master and bills by invoice
    R42  the invoice number fits the vendor's format and was not received before
    R43  every line maps to a product on the vendor's contract
    R44  quantity times rate equals each line amount
    R45  the lines add up to the invoice total
    R46  the dates make sense
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from elix.contracts import (
    Check, Decision, ExtractedInvoice, ExtractedLine, InvoiceLine, Outcome, Role, Severity, Sku, State, UsageWindow,
    Vendor, VendorInvoice, WorkItem, WorkItemKind,
)
from elix.engine.clock import parse_ts
from elix.engine.context import POLICY, SYNC, Ctx
from elix.engine.fmt import cents_of, day, join_words, money, qty
from elix.engine.periods import current_period
from elix.engine.permissions import approver_name
from elix.engine.policy import policy_version
from elix.engine.workflow import create, transition

RULE_NAMES = {
    "R40": "Fields read from the PDF",
    "R41": "Vendor is in the vendor master",
    "R42": "Invoice number fits the vendor and is new",
    "R43": "Every line maps to a contract product",
    "R44": "Quantity times rate equals each line amount",
    "R45": "Lines add up to the total",
    "R46": "Dates make sense",
}
GENERIC_NUMBER = re.compile(r"^(?=.*\d)[A-Z0-9][A-Z0-9\-/_.]{2,39}$", re.IGNORECASE)
MAX_INVOICE_CENTS = 100_000_000_00

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
     "december"], start=1)}
MONTHS.update({name[:3]: i for name, i in list(MONTHS.items())})
MONTHS["sept"] = 9


# ---------------------------------------------------------------- strict parsing of extracted text


def parse_amount(text: str) -> Optional[Decimal]:
    """A plain decimal from text like "$4,494.00", "4494.00 USD" or "(12.00)". None when it is not one number."""
    if text is None:
        return None
    clean = str(text).strip().replace(",", "").replace("$", "").replace("USD", "").replace("usd", "").strip()
    negative = clean.startswith("(") and clean.endswith(")")
    clean = clean.strip("()").strip()
    if not re.fullmatch(r"-?\d+(\.\d+)?", clean):
        return None
    try:
        value = Decimal(clean)
    except InvalidOperation:
        return None
    return -value if negative else value


def parse_cents(text: str) -> Optional[int]:
    """Whole cents, or None when the text is not money or has fractions of a cent."""
    value = parse_amount(text)
    if value is None or value != value.quantize(Decimal("0.01")):
        return None
    return int(value * 100)


def parse_date(text: str) -> Optional[date]:
    """YYYY-MM-DD, "October 24, 2026", "Oct 24 2026", "24 Oct 2026" or US style 10/24/2026."""
    if not text:
        return None
    s = str(text).strip().rstrip(".").replace(",", " ")
    s = re.sub(r"\s+", " ", s)
    try:
        m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ].*)?", s)
        if m:
            return date(int(m[1]), int(m[2]), int(m[3]))
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
        if m:
            return date(int(m[3]), int(m[1]), int(m[2]))
        m = re.fullmatch(r"([A-Za-z]+)\.? (\d{1,2})(?:st|nd|rd|th)? (\d{4})", s)
        if m and m[1].lower() in MONTHS:
            return date(int(m[3]), MONTHS[m[1].lower()], int(m[2]))
        m = re.fullmatch(r"(\d{1,2}) ([A-Za-z]+)\.? (\d{4})", s)
        if m and m[2].lower() in MONTHS:
            return date(int(m[3]), MONTHS[m[2].lower()], int(m[1]))
    except ValueError:
        return None
    return None


def iso_day(value: date) -> str:
    return value.isoformat()


def window_of(start: date, end_inclusive: date) -> UsageWindow:
    """Printed service dates are inclusive. Elix stores windows as [start, day after end) at midnight UTC."""
    return UsageWindow(start=f"{start.isoformat()}T00:00:00Z",
                       end=f"{(end_inclusive + timedelta(days=1)).isoformat()}T00:00:00Z")


# ---------------------------------------------------------------- vendors and products


LEGAL_SUFFIXES = {"inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "company", "plc",
                  "gmbh", "sa", "bv", "the"}


def _name_key(name: str) -> str:
    words = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower()).split()
    return " ".join(w for w in words if w not in LEGAL_SUFFIXES)


def resolve_vendor(ctx: Ctx, name: str) -> Optional[Vendor]:
    """The vendor master entry with exactly this name, ignoring case, punctuation and legal suffixes like Inc.

    "Lumen Compute, Inc." is Lumen Compute. A name with anything else added is not, so text slipped into the vendor
    name never matches a vendor by accident. The vendor id's short form also counts ("AWS" for ven_aws).
    """
    key = _name_key(name)
    if not key:
        return None
    for vendor in ctx.store.vendors():
        if key in (_name_key(vendor.name), vendor.id.removeprefix("ven_")):
            return vendor
    return None


STOP = {"a", "an", "and", "the", "of", "per", "for", "to", "in", "at", "by", "from", "with", "usage", "total", "line",
        *MONTHS}


def _tokens(text: str) -> set[str]:
    text = (text or "").lower().replace("on-demand", "ondemand").replace("on demand", "ondemand")
    out = set()
    for word in re.split(r"[^a-z0-9]+", text):
        if not word or word in STOP or word.isdigit():
            continue
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        out.add(word)
    return out


def map_sku(vendor: Vendor, line: ExtractedLine, rate_cents: Optional[int]) -> tuple[Optional[Sku], str]:
    """The contract product a line bills, and how Elix found it. Printed codes win, then the best description match."""
    printed = (line.sku or "").strip().lower()
    if printed:
        for sku in vendor.skus:
            if sku.sku.lower() == printed:
                return sku, "item code"
    words = _tokens(line.description)
    if not words:
        return None, ""
    scored = []
    for sku in vendor.skus:
        target = _tokens(f"{sku.description} {sku.sku.replace('-', ' ')} {sku.unit}")
        score = 2 * len(words & target) / (len(words) + len(target)) if target else 0.0
        if rate_cents is not None and rate_cents == sku.unit_price_cents:
            score += 0.1
        scored.append((score, sku))
    scored.sort(key=lambda pair: -pair[0])
    best = scored[0] if scored else (0.0, None)
    runner = scored[1][0] if len(scored) > 1 else 0.0
    if best[1] is None or best[0] < 0.4 or best[0] - runner < 0.1:
        return None, ""
    return best[1], "description"


# ---------------------------------------------------------------- the checks


@dataclass
class ParsedLine:
    index: int
    line: ExtractedLine
    quantity: Optional[float] = None
    rate_cents: Optional[int] = None
    amount_cents: Optional[int] = None
    expected_cents: Optional[int] = None
    sku: Optional[Sku] = None
    sku_source: str = ""
    start: Optional[date] = None
    end: Optional[date] = None
    window_source: str = ""

    def label(self) -> str:
        text = (self.line.description or "").strip()
        return f"Line {self.index}" + (f" ({text})" if text else "")

    def view(self) -> dict[str, Any]:
        return {
            "line_id": str(self.index), "description": self.line.description, "printed_sku": self.line.sku or None,
            "sku": self.sku.sku if self.sku else None, "sku_description": self.sku.description if self.sku else None,
            "sku_source": self.sku_source or None, "quantity": self.quantity, "unit_price_cents": self.rate_cents,
            "amount_cents": self.amount_cents, "expected_cents": self.expected_cents,
            "arithmetic_ok": (self.expected_cents == self.amount_cents) if self.expected_cents is not None
            and self.amount_cents is not None else None,
            "usage_start": iso_day(self.start) if self.start else None,
            "usage_end": iso_day(self.end) if self.end else None, "window_source": self.window_source or None,
        }


@dataclass
class IntakeResult:
    checks: list[Check]
    vendor: Optional[Vendor]
    invoice: Optional[VendorInvoice]
    lines: list[ParsedLine] = field(default_factory=list)
    total_cents: Optional[int] = None
    issued: Optional[date] = None
    due: Optional[date] = None

    @property
    def accepted(self) -> bool:
        return all(c.passed for c in self.checks) and self.invoice is not None

    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    def view(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted, "checks": [c.model_dump(mode="json") for c in self.checks],
            "vendor": {"id": self.vendor.id, "name": self.vendor.name} if self.vendor else None,
            "lines": [line.view() for line in self.lines], "total_cents": self.total_cents,
            "proposed_invoice": self.invoice.model_dump(mode="json") if self.invoice else None,
        }


def _check(rule_id: str, passed: bool, detail: str, **evidence: Any) -> Check:
    return Check(rule_id=rule_id, name=RULE_NAMES[rule_id], passed=passed,
                 severity=Severity.info if passed else Severity.escalate, detail=detail, evidence=evidence)


def validate(ctx: Ctx, ex: ExtractedInvoice) -> IntakeResult:
    """Run every check on an extracted invoice. Never stops at the first failure."""
    config = ctx.config
    vendor_name = (ex.vendor_name or "").strip()
    number = (ex.invoice_number or "").strip()
    issued, due = parse_date(ex.issue_date), parse_date(ex.due_date)
    period_start, period_end = parse_date(ex.period_start), parse_date(ex.period_end)
    total_cents = parse_cents(ex.total)

    # R40 the fields Elix needs
    missing: list[str] = []
    if not vendor_name:
        missing.append("the vendor name")
    if not number:
        missing.append("the invoice number")
    if not ex.issue_date:
        missing.append("the invoice date")
    elif issued is None:
        missing.append(f"a readable invoice date (it says \"{ex.issue_date}\")")
    if not ex.due_date:
        missing.append("the due date")
    elif due is None:
        missing.append(f"a readable due date (it says \"{ex.due_date}\")")
    if not ex.total:
        missing.append("the invoice total")
    elif total_cents is None:
        missing.append(f"a readable total (it says \"{ex.total}\")")
    if not ex.lines:
        missing.append("any invoice lines")
    lines: list[ParsedLine] = []
    unreadable: list[str] = []
    for index, raw in enumerate(ex.lines, start=1):
        parsed = ParsedLine(index=index, line=raw)
        amount = parse_amount(raw.quantity)
        parsed.quantity = float(amount) if amount is not None and amount > 0 else None
        parsed.rate_cents = parse_cents(raw.unit_price)
        parsed.amount_cents = parse_cents(raw.amount)
        bad = [name for name, value in (("quantity", parsed.quantity), ("unit price", parsed.rate_cents),
                                        ("amount", parsed.amount_cents)) if value is None]
        if bad:
            unreadable.append(f"the {join_words(bad)} on line {index}")
        if parsed.quantity is not None and parsed.rate_cents is not None:
            parsed.expected_cents = cents_of(parsed.quantity, parsed.rate_cents)
        start, end = parse_date(raw.usage_start), parse_date(raw.usage_end)
        if start and end:
            parsed.start, parsed.end, parsed.window_source = start, end, "line"
        elif period_start and period_end:
            parsed.start, parsed.end, parsed.window_source = period_start, period_end, "billing period"
        lines.append(parsed)
    currency = (ex.currency or "").strip().upper()
    problems = missing + unreadable
    if currency and currency not in ("USD", "US$", "$"):
        problems.append(f"an invoice in USD (it is in {currency})")
    checks = [_check("R40", not problems,
                     f"Elix read the vendor, number, dates, {len(lines)} {'line' if len(lines) == 1 else 'lines'} and "
                     f"the total." if not problems else f"Elix could not find {join_words(problems)} in the PDF.",
                     missing=problems)]

    # R41 the vendor master
    vendor = resolve_vendor(ctx, vendor_name)
    if not vendor_name:
        checks.append(_check("R41", False, "The PDF does not name a vendor, so Elix cannot match it to the vendor "
                                           "master."))
    elif vendor is None:
        controller = approver_name(config, Role.controller, None)
        checks.append(_check("R41", False, f"{vendor_name} is not in Kestrel's vendor master. {controller} has to add "
                                           f"the vendor before Elix will book or pay its invoices.",
                             printed=vendor_name))
    elif vendor.status != "active":
        checks.append(_check("R41", False, f"{vendor.name} is blocked in the vendor master.", vendor_id=vendor.id))
    elif vendor.billing == "per_request":
        checks.append(_check("R41", False, f"{vendor.name} is paid per request over x402 and does not send invoices, "
                                           f"so an invoice from it needs a person to look at it.", vendor_id=vendor.id))
    else:
        printed = "" if vendor_name == vendor.name else f", printed as {vendor_name}"
        sentence = f"Matched {vendor.name} ({vendor.id}) in the vendor master{printed}"
        checks.append(_check("R41", True, sentence if sentence.endswith(".") else sentence + ".",
                             vendor_id=vendor.id, printed=vendor_name))
    known = vendor if checks[-1].passed else None

    # R42 the invoice number
    fmt = config.invoice_number_formats.get(vendor.id) if vendor else None
    who = vendor.name if vendor else (vendor_name or "this vendor")
    if not number:
        checks.append(_check("R42", False, "There is no invoice number to check."))
    else:
        reasons = []
        if fmt is not None:
            pattern, example = fmt
            if not re.fullmatch(pattern, number):
                reasons.append(f"{number} does not look like a {who} invoice number, which reads like {example}.")
        elif not GENERIC_NUMBER.match(number):
            reasons.append(f"{number} does not look like an invoice number.")
        seen = [i for i in ctx.store.query_items(kind="invoice", invoice_number=number, order="created_at ASC")
                if i.state != State.REJECTED and (vendor is None or i.vendor_id == vendor.id)]
        if seen:
            first = seen[0]
            reasons.append(f"{who} invoice {number} was already received as {first.id} on {day(first.created_at)}.")
        fits = (f"{number} matches the format of {who} invoice numbers" if fmt is not None else
                f"{number} looks like an invoice number")
        checks.append(_check("R42", not reasons, " ".join(reasons) if reasons else
                             f"{fits} and has not been received before.",
                             duplicate_of=seen[0].id if seen else None))

    # R43 contract products
    if not lines:
        checks.append(_check("R43", False, "There are no lines to map to the contract."))
    elif known is None:
        checks.append(_check("R43", False, "Elix cannot map the lines to a contract because the vendor is not one it "
                                           "knows."))
    else:
        unmapped = []
        for parsed in lines:
            parsed.sku, parsed.sku_source = map_sku(known, parsed.line, parsed.rate_cents)
            if parsed.sku is None:
                printed_sku = f" (item code {parsed.line.sku})" if parsed.line.sku else ""
                unmapped.append(f"{parsed.label()}{printed_sku} does not match a product on {known.name}'s contract.")
        mapped = [f"line {p.index} to {p.sku.sku}" for p in lines if p.sku]
        checks.append(_check("R43", not unmapped, " ".join(unmapped) if unmapped else
                             f"Mapped {join_words(mapped)}.",
                             skus={str(p.index): p.sku.sku for p in lines if p.sku}))

    # R44 line arithmetic
    wrong = []
    for parsed in lines:
        if parsed.expected_cents is None or parsed.amount_cents is None:
            continue
        if parsed.expected_cents != parsed.amount_cents:
            wrong.append(f"{parsed.label()} says {qty(parsed.quantity)} at {money(parsed.rate_cents)} is "
                         f"{money(parsed.amount_cents)}, but {qty(parsed.quantity)} times {money(parsed.rate_cents)} "
                         f"is {money(parsed.expected_cents)}.")
    checkable = [p for p in lines if p.expected_cents is not None and p.amount_cents is not None]
    if not lines:
        checks.append(_check("R44", False, "There are no lines to check."))
    elif wrong:
        checks.append(_check("R44", False, " ".join(wrong),
                             lines=[p.index for p in lines if p.expected_cents != p.amount_cents]))
    elif len(checkable) < len(lines):
        checks.append(_check("R44", False, "Some lines have a quantity, rate or amount Elix could not read."))
    else:
        checks.append(_check("R44", True, "Quantity times rate equals the amount on every line."))

    # R45 the total
    line_sum = sum(p.amount_cents or 0 for p in lines)
    if total_cents is None or not lines or any(p.amount_cents is None for p in lines):
        checks.append(_check("R45", False, "Elix cannot add up the invoice because the total or a line amount is "
                                           "missing."))
    elif line_sum != total_cents:
        checks.append(_check("R45", False, f"The lines add up to {money(line_sum)} but the invoice total is "
                                           f"{money(total_cents)}.", line_sum_cents=line_sum, total_cents=total_cents))
    elif total_cents <= 0 or total_cents > MAX_INVOICE_CENTS:
        checks.append(_check("R45", False, f"An invoice total of {money(total_cents)} is not something Elix books "
                                           f"from a PDF."))
    else:
        checks.append(_check("R45", True, f"The lines add up to the {money(total_cents)} total."))

    # R46 dates
    today = parse_ts(ctx.clock.now()).date()
    date_problems = []
    if issued and issued > today:
        date_problems.append(f"The invoice date {day(_dt(issued))} is after today, {day(_dt(today))}.")
    if issued and due and due < issued:
        date_problems.append(f"The due date {day(_dt(due))} is before the invoice date {day(_dt(issued))}.")
    if period_start and period_end and period_end < period_start:
        date_problems.append("The billing period ends before it starts.")
    if (ex.period_start or ex.period_end) and not (period_start and period_end):
        date_problems.append("The billing period could not be read.")
    for parsed in lines:
        if parsed.start and parsed.end and parsed.end < parsed.start:
            date_problems.append(f"{parsed.label()} has service dates that end before they start.")
        if (parsed.line.usage_start or parsed.line.usage_end) and parsed.window_source != "line":
            date_problems.append(f"The service dates on line {parsed.index} could not be read.")
        metered = known is not None and parsed.sku is not None and \
            parsed.sku.sku in config.metered_skus.get(known.id, set())
        if metered and parsed.start is None:
            date_problems.append(f"{parsed.label()} bills metered usage but the PDF shows no service dates, so Elix "
                                 f"cannot match it to the usage meter.")
    if not (issued and due):
        date_problems.append("The invoice date and the due date are both needed.")
    checks.append(_check("R46", not date_problems, " ".join(date_problems) if date_problems else
                         f"Issued {day(_dt(issued))}, due {day(_dt(due))}"
                         + (f", for {day(_dt(period_start))} to {day(_dt(period_end))}." if period_start and period_end
                            else ".")))

    result = IntakeResult(checks=checks, vendor=known or vendor, invoice=None, lines=lines, total_cents=total_cents,
                          issued=issued, due=due)
    if all(c.passed for c in checks) and known is not None and issued and due and total_cents is not None:
        starts = [p.start for p in lines if p.start]
        anchor = period_start or (min(starts) if starts else issued)
        result.invoice = VendorInvoice(
            vendor_id=known.id, invoice_number=number, period=anchor.strftime("%Y-%m"),
            issued_at=f"{issued.isoformat()}T00:00:00Z", due_at=f"{due.isoformat()}T00:00:00Z",
            total_cents=total_cents,
            lines=[InvoiceLine(line_id=str(p.index), description=(p.line.description or p.sku.description).strip(),
                               sku=p.sku.sku, quantity=p.quantity, unit_price_cents=p.rate_cents,
                               amount_cents=p.amount_cents,
                               usage_window=window_of(p.start, p.end) if p.start and p.end else None)
                   for p in lines],
        )
    return result


def _dt(value: date) -> datetime:
    return datetime(value.year, value.month, value.day)


# ---------------------------------------------------------------- the work item when a check fails


def hold(ctx: Ctx, ex: ExtractedInvoice, result: IntakeResult, record: dict[str, Any], uploader: str) -> WorkItem:
    """An invoice PDF that failed intake checks becomes an invoice item waiting for the accountant. Nothing posts."""
    failed = result.failed()
    vendor = result.vendor
    printed = (ex.vendor_name or "").strip()
    name = vendor.name if vendor else (printed or "An unknown vendor")
    number = (ex.invoice_number or "").strip()
    accountant = approver_name(ctx.config, Role.accountant, None)
    title = f"{name} invoice {number or 'with no number'} from a PDF"
    total = result.total_cents
    item = WorkItem(
        id="", kind=WorkItemKind.invoice, state=State.NEW, period=current_period(ctx), created_at="", updated_at="",
        requested_by=SYNC, proposed_by=POLICY, vendor_id=vendor.id if vendor else None,
        amount_cents=total if total and total > 0 else 0, title=title, simulated=True,
        data={"intake": record, "triggered_by": uploader},
    )
    filename = record.get("filename") or "an invoice PDF"
    who = ctx.config.actor_name(uploader)
    item = create(ctx, item, actor=SYNC, action="create",
                  reason=(f"Sync received {filename}, which {who} uploaded. {extractor_phrase(record)} read it as "
                          f"{name} invoice {number or 'with no number'}"
                          + (f" for {money(total)}." if total else ".")),
                  evidence={"filename": filename, "sha256": record.get("sha256"), "extractor": record.get("extractor"),
                            "invoice_number": number, "triggered_by": uploader})
    details = " ".join(c.detail for c in failed)
    summary = (f"Elix did not accept this invoice because {len(failed)} of {len(result.checks)} intake checks failed. "
               f"{details} Nothing was reconciled, paid or posted. {accountant} can reject it and ask "
               f"{vendor.name if vendor else 'the vendor'} for a corrected invoice.")
    decision = Decision(outcome=Outcome.needs_info, required_approver_role=Role.accountant, checks=result.checks,
                        summary=summary, policy_version=policy_version(ctx))
    return transition(ctx, item, "intake_check", POLICY, reason=summary,
                      patch={"decision": decision.model_dump(), "required_approver_role": Role.accountant.value},
                      evidence={"rules": [c.rule_id for c in failed], "filename": filename})


def extractor_phrase(record: dict[str, Any]) -> str:
    """Who read the PDF, for sentences. Claude results name the model; offline results say so."""
    if record.get("extractor") == "claude":
        return f"Claude ({record.get('model')})" if record.get("model") else "Claude"
    return "The offline PDF parser"


def failed_intake(item: WorkItem) -> bool:
    intake = item.data.get("intake") or {}
    return item.kind == WorkItemKind.invoice and intake.get("status") == "failed"
