"""Shared contracts for every Elix component.

This file is the canonical definition of the data shapes described in
specs/01-domain.md and specs/04-api.md. Field names here are the field names
on the wire. Money is integer cents unless a field says otherwise.

Change a field only together with the spec and every caller.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ---------------------------------------------------------------- enums

class Role(str, Enum):
    agent = "agent"
    budget_owner = "budget_owner"
    accountant = "accountant"
    controller = "controller"
    auditor = "auditor"
    sync = "sync"
    policy = "policy"
    gateway = "gateway"
    learner = "learner"


class ActorKind(str, Enum):
    human = "human"
    agent = "agent"
    system = "system"


class WorkItemKind(str, Enum):
    purchase = "purchase"
    invoice = "invoice"
    accrual = "accrual"
    correction = "correction"
    policy = "policy"


class State(str, Enum):
    NEW = "NEW"
    PROPOSED = "PROPOSED"
    NEEDS_INFO = "NEEDS_INFO"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DENIED = "DENIED"
    PAID = "PAID"
    POSTED = "POSTED"
    FAILED = "FAILED"


TERMINAL_STATES = {State.REJECTED, State.DENIED, State.POSTED, State.FAILED}


class Outcome(str, Enum):
    auto_approve = "auto_approve"
    needs_approval = "needs_approval"
    needs_info = "needs_info"
    deny = "deny"


class Severity(str, Enum):
    info = "info"
    escalate = "escalate"
    ask = "ask"
    deny = "deny"


class FlagCode(str, Enum):
    PERMISSION = "PERMISSION"
    UNKNOWN_VENDOR = "UNKNOWN_VENDOR"
    PAYEE_CHANGED = "PAYEE_CHANGED"
    DUPLICATE_RETRY = "DUPLICATE_RETRY"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    PRICE_ABOVE_CONTRACT = "PRICE_ABOVE_CONTRACT"
    OVER_TXN_CAP = "OVER_TXN_CAP"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    SPLIT_PURCHASE = "SPLIT_PURCHASE"
    MISSING_PURPOSE = "MISSING_PURPOSE"
    MEMO_INSTRUCTIONS = "MEMO_INSTRUCTIONS"
    INVOICE_QTY_OVER_METER = "INVOICE_QTY_OVER_METER"
    INVOICE_RATE_MISMATCH = "INVOICE_RATE_MISMATCH"
    CROSS_RAIL_DOUBLE_BILL = "CROSS_RAIL_DOUBLE_BILL"
    DUPLICATE_INVOICE = "DUPLICATE_INVOICE"
    PERIOD_CLOSED = "PERIOD_CLOSED"
    SEGREGATION = "SEGREGATION"


RULE_FLAGS: dict[str, FlagCode] = {
    "R01": FlagCode.PERMISSION,
    "R02": FlagCode.UNKNOWN_VENDOR,
    "R03": FlagCode.PAYEE_CHANGED,
    "R04": FlagCode.DUPLICATE_RETRY,
    "R05": FlagCode.AMOUNT_MISMATCH,
    "R06": FlagCode.PRICE_ABOVE_CONTRACT,
    "R07": FlagCode.OVER_TXN_CAP,
    "R08": FlagCode.BUDGET_EXCEEDED,
    "R09": FlagCode.SPLIT_PURCHASE,
    "R10": FlagCode.MISSING_PURPOSE,
    "R11": FlagCode.MEMO_INSTRUCTIONS,
    "R20": FlagCode.INVOICE_QTY_OVER_METER,
    "R21": FlagCode.INVOICE_RATE_MISMATCH,
    "R22": FlagCode.CROSS_RAIL_DOUBLE_BILL,
    "R23": FlagCode.DUPLICATE_INVOICE,
    "R30": FlagCode.PERIOD_CLOSED,
    "R31": FlagCode.SEGREGATION,
}

RailName = Literal["x402", "bank"]
CostCenter = Literal["ML", "GTM", "OPS"]


# ---------------------------------------------------------------- world

class Actor(_Model):
    id: str
    kind: ActorKind
    name: str
    role: Role
    title: Optional[str] = None
    owner_id: Optional[str] = None          # agents only
    cost_center: Optional[CostCenter] = None


class AgentConfig(_Model):
    actor_id: str
    name: str
    owner_id: str
    cost_center: CostCenter
    per_txn_cap_cents: int
    monthly_budget_cents: int
    auto_approve_under_cents: int
    description: str


class Sku(_Model):
    sku: str
    description: str
    unit: str
    unit_price_cents: int
    tolerance_pct: float = 10.0


class Vendor(_Model):
    id: str
    name: str
    rails: list[RailName]
    billing: Literal["per_request", "monthly_invoice", "semi_monthly_invoice"]
    category: str
    payout_address: Optional[str] = None
    payout_address_changed_at: Optional[str] = None
    previous_payout_address: Optional[str] = None
    bank_account_last4: Optional[str] = None
    default_account: str
    skus: list[Sku]
    status: Literal["active", "blocked"] = "active"
    notes: Optional[str] = None


class Account(_Model):
    code: str
    name: str
    type: Literal["asset", "liability", "equity", "revenue", "expense"]


class Period(_Model):
    id: str                                   # "2026-10"
    status: Literal["open", "closed"]
    closed_by: Optional[str] = None
    closed_at: Optional[str] = None


class Company(_Model):
    name: str
    description: str
    today: str
    synthetic_notice: str


class World(_Model):
    company: Company
    actors: list[Actor]
    agents: list[AgentConfig]
    vendors: list[Vendor]
    accounts: list[Account]
    periods: list[Period]


# ---------------------------------------------------------------- rails

class PaymentIntent(_Model):
    work_item_id: str
    vendor_id: str
    resource_url: Optional[str] = None
    amount_cents: int
    sku: Optional[str] = None
    quantity: Optional[float] = None
    idempotency_key: Optional[str] = None
    pay_to_override: Optional[str] = None     # demo and eval only: vendor server asks to be paid elsewhere


class PaymentChallenge(_Model):
    scheme: str                     # "exact"
    network: str                    # "eip155:84532" (Base Sepolia)
    asset: str                      # USDC contract address
    pay_to: str
    amount_atomic: str              # USDC has 6 decimals
    amount_cents: int
    resource: str
    description: str = ""
    max_timeout_seconds: int = 60
    raw_b64: str = ""               # the PAYMENT-REQUIRED header (or body) as received
    received_at: str


class Receipt(_Model):
    rail: RailName
    simulated: bool
    network: Optional[str] = None
    tx_ref: str
    payer: str
    payee: str
    amount_cents: int
    amount_atomic: Optional[str] = None
    payload_sha256: Optional[str] = None
    signature_verified: Optional[bool] = None
    settled_at: str
    resource_delivered: bool = False
    resource_sha256: Optional[str] = None


@runtime_checkable
class Rail(Protocol):
    """A way to move money. Implemented in elix/rails. Only elix/engine/gateway.py calls pay()."""

    name: RailName

    def challenge(self, intent: PaymentIntent) -> Optional[PaymentChallenge]:
        """Ask the vendor what it wants to be paid (x402 returns the 402 requirements). Bank rails return None."""
        ...

    def pay(self, intent: PaymentIntent, challenge: Optional[PaymentChallenge]) -> Receipt:
        """Move the money. Raises RailError on failure."""
        ...


class RailError(Exception):
    pass


# ---------------------------------------------------------------- decisions

class Check(_Model):
    rule_id: str
    name: str
    passed: bool
    severity: Severity
    detail: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class Decision(_Model):
    outcome: Outcome
    required_approver_role: Optional[Role] = None
    checks: list[Check]
    summary: str
    policy_version: str
    payee_hold: bool = False


class Question(_Model):
    id: str
    asked_by: str
    to: str
    text: str
    answer: Optional[str] = None
    answered_by: Optional[str] = None
    at: str
    answered_at: Optional[str] = None


class Link(_Model):
    rel: Literal["duplicate_of", "reverses", "reconciles", "accrues", "corrects", "precedent", "policy", "dispute", "related"]
    id: str


class WorkItem(_Model):
    id: str
    kind: WorkItemKind
    state: State
    period: str
    created_at: str
    updated_at: str
    requested_by: str
    proposed_by: str
    approved_by: Optional[str] = None
    approvals: list[dict[str, Any]] = Field(default_factory=list)   # [{actor, role, at, note, kind: "spend"|"payee"|"invoice"|"policy"|"correction"}]
    required_approver_role: Optional[Role] = None
    vendor_id: Optional[str] = None
    sku: Optional[str] = None
    quantity: Optional[float] = None
    unit_price_cents: Optional[int] = None
    amount_cents: int = 0
    currency: Literal["USD"] = "USD"
    account: Optional[str] = None
    cost_center: Optional[CostCenter] = None
    purpose: Optional[str] = None
    task_ref: Optional[str] = None
    idempotency_key: Optional[str] = None
    resource_url: Optional[str] = None
    challenge: Optional[PaymentChallenge] = None
    decision: Optional[Decision] = None
    receipt: Optional[Receipt] = None
    journal_entry_ids: list[str] = Field(default_factory=list)
    links: list[Link] = Field(default_factory=list)
    flags: list[FlagCode] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    memo: Optional[str] = None
    title: str = ""                  # short human label, e.g. "600 H100 spot hours from Lumen Compute"
    simulated: bool = True
    data: dict[str, Any] = Field(default_factory=dict)   # kind specific payload (invoice, accrual assumptions, correction details, policy id)


class PurchaseRequest(_Model):
    vendor_id: str
    sku: str
    quantity: float
    unit_price_cents: Optional[int] = None   # defaults to contract price
    purpose: str = ""
    task_ref: Optional[str] = None
    idempotency_key: Optional[str] = None
    memo: Optional[str] = None
    resource_url: Optional[str] = None
    pay_to_override: Optional[str] = None     # demo and eval only


# ---------------------------------------------------------------- invoices and meters

class UsageWindow(_Model):
    start: str
    end: str


class InvoiceLine(_Model):
    line_id: str
    description: str
    sku: str
    quantity: float
    unit_price_cents: int
    amount_cents: int
    usage_window: Optional[UsageWindow] = None


class VendorInvoice(_Model):
    vendor_id: str
    invoice_number: str
    period: str
    issued_at: str
    due_at: str
    lines: list[InvoiceLine]
    total_cents: int


class VendorCreditNote(_Model):
    """A vendor's credit against one of its invoices, usually the answer to a dispute."""

    vendor_id: str
    credit_number: str
    invoice_number: str
    amount_cents: int
    reason: str = ""
    issued_at: Optional[str] = None


class ExtractedLine(_Model):
    """One invoice line as an extractor read it. Strings as normalized text, "" when the PDF does not show it.

    Numbers are plain decimals without currency symbols or separators ("2140", "2.10", "4494.00") and dates are
    YYYY-MM-DD with an inclusive end. Elix's own code parses and checks every value. Nothing here is trusted.
    """

    description: str = ""
    sku: str = ""
    quantity: str = ""
    unit_price: str = ""
    amount: str = ""
    usage_start: str = ""
    usage_end: str = ""


class ExtractedInvoice(_Model):
    """A proposed vendor invoice read from a PDF, by Claude or by the offline parser. A proposal, never a decision."""

    extractor: Literal["claude", "offline"]
    extractor_label: str
    model: Optional[str] = None
    vendor_name: str = ""
    invoice_number: str = ""
    issue_date: str = ""
    due_date: str = ""
    period_start: str = ""
    period_end: str = ""
    currency: str = ""
    total: str = ""
    lines: list[ExtractedLine] = Field(default_factory=list)
    pages: int = 0
    notes: list[str] = Field(default_factory=list)


class MeterRecord(_Model):
    vendor_id: str
    sku: str
    agent_id: Optional[str] = None
    quantity: float
    window_start: str
    window_end: str
    source: str = "agent_meter"


class ReconciliationLine(_Model):
    line_id: str
    description: str
    invoiced_qty: float
    metered_qty: Optional[float] = None
    paid_elsewhere_qty: float = 0.0
    expected_qty: Optional[float] = None
    contract_rate_cents: Optional[int] = None
    invoiced_rate_cents: int
    rate_ok: bool
    variance_cents: int
    finding: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class ReconciliationProposal(_Model):
    invoice_number: str
    vendor_id: str
    lines: list[ReconciliationLine]
    invoice_total_cents: int
    pay_cents: int
    dispute_cents: int
    summary: str
    dispute_note: Optional[str] = None


# ---------------------------------------------------------------- policies

class Policy(_Model):
    id: str
    version: int = 1
    kind: Literal["auto_approve", "coding", "tolerance"]
    status: Literal["proposed", "active", "rejected", "retired"]
    scope: dict[str, Any]
    params: dict[str, Any]
    rationale: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    proposed_by: str
    approved_by: Optional[str] = None
    created_at: str
    activated_at: Optional[str] = None
    work_item_id: Optional[str] = None


# ---------------------------------------------------------------- ledger

class JournalLine(_Model):
    account: str
    debit_cents: int = 0
    credit_cents: int = 0
    cost_center: Optional[CostCenter] = None
    vendor_id: Optional[str] = None
    memo: Optional[str] = None


class JournalEntry(_Model):
    id: str
    date: str
    period: str
    memo: str
    lines: list[JournalLine]
    source_work_item: Optional[str] = None
    reverses: Optional[str] = None
    reversed_by: Optional[str] = None
    posted_by: str = "system:gateway"
    created_at: str


class AccountBalance(_Model):
    account: str
    name: str
    type: str
    debit_cents: int
    credit_cents: int
    balance_cents: int


# ---------------------------------------------------------------- audit

class AuditEvent(_Model):
    seq: int
    at: str
    actor: str
    action: str
    subject: str
    allowed: bool
    reason: str
    diff: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    prev_hash: str
    hash: str


class ChainCheck(_Model):
    ok: bool
    checked: int
    first_bad_seq: Optional[int] = None
    head_hash: str = ""


class WhyStep(_Model):
    seq: int
    at: str
    actor: str
    actor_name: str
    action: str
    sentence: str
    rule_ids: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


class WhyReport(_Model):
    subject: str
    title: str
    answer: str
    steps: list[WhyStep]
    related: list[str] = Field(default_factory=list)


class NextAction(_Model):
    action: str
    label: str
    allowed: bool
    reason: str = ""


class WorkItemDetail(_Model):
    item: WorkItem
    events: list[AuditEvent]
    journal_entries: list[JournalEntry]
    linked: list[WorkItem]
    explanation: str
    reconciliation: Optional[ReconciliationProposal] = None
    next_actions: list[NextAction] = Field(default_factory=list)


class ClosePreview(_Model):
    period: str
    accruals: list[WorkItem]
    blockers: list[dict[str, Any]]
    can_close: bool


class Denied(Exception):
    """Raised when can() refuses an action. The audit event is already written."""

    def __init__(self, message: str, rule_id: str = "R31", audit_seq: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.rule_id = rule_id
        self.audit_seq = audit_seq
