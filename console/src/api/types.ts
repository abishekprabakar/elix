// Mirrors elix/contracts.py and specs/04-api.md. Field names are the wire names.
// Money is integer cents unless a field says otherwise.

export type Role =
  | 'agent'
  | 'budget_owner'
  | 'accountant'
  | 'controller'
  | 'auditor'
  | 'sync'
  | 'policy'
  | 'gateway'
  | 'learner';

export type ActorKind = 'human' | 'agent' | 'system';
export type WorkItemKind = 'purchase' | 'invoice' | 'accrual' | 'correction' | 'policy';

export type State =
  | 'NEW'
  | 'PROPOSED'
  | 'NEEDS_INFO'
  | 'NEEDS_APPROVAL'
  | 'APPROVED'
  | 'REJECTED'
  | 'DENIED'
  | 'PAID'
  | 'POSTED'
  | 'FAILED';

export const TERMINAL_STATES: State[] = ['REJECTED', 'DENIED', 'POSTED', 'FAILED'];

export type Outcome = 'auto_approve' | 'needs_approval' | 'needs_info' | 'deny';
export type Severity = 'info' | 'escalate' | 'ask' | 'deny';

export type FlagCode =
  | 'PERMISSION'
  | 'UNKNOWN_VENDOR'
  | 'PAYEE_CHANGED'
  | 'DUPLICATE_RETRY'
  | 'AMOUNT_MISMATCH'
  | 'PRICE_ABOVE_CONTRACT'
  | 'OVER_TXN_CAP'
  | 'BUDGET_EXCEEDED'
  | 'SPLIT_PURCHASE'
  | 'MISSING_PURPOSE'
  | 'MEMO_INSTRUCTIONS'
  | 'INVOICE_QTY_OVER_METER'
  | 'INVOICE_RATE_MISMATCH'
  | 'CROSS_RAIL_DOUBLE_BILL'
  | 'DUPLICATE_INVOICE'
  | 'PERIOD_CLOSED'
  | 'SEGREGATION';

export const RULE_FLAGS: Record<string, FlagCode> = {
  R01: 'PERMISSION',
  R02: 'UNKNOWN_VENDOR',
  R03: 'PAYEE_CHANGED',
  R04: 'DUPLICATE_RETRY',
  R05: 'AMOUNT_MISMATCH',
  R06: 'PRICE_ABOVE_CONTRACT',
  R07: 'OVER_TXN_CAP',
  R08: 'BUDGET_EXCEEDED',
  R09: 'SPLIT_PURCHASE',
  R10: 'MISSING_PURPOSE',
  R11: 'MEMO_INSTRUCTIONS',
  R20: 'INVOICE_QTY_OVER_METER',
  R21: 'INVOICE_RATE_MISMATCH',
  R22: 'CROSS_RAIL_DOUBLE_BILL',
  R23: 'DUPLICATE_INVOICE',
  R30: 'PERIOD_CLOSED',
  R31: 'SEGREGATION',
};

export type RailName = 'x402' | 'bank';
export type CostCenter = 'ML' | 'GTM' | 'OPS';

// ---------------------------------------------------------------- world

export interface Actor {
  id: string;
  kind: ActorKind;
  name: string;
  role: Role;
  title?: string | null;
  owner_id?: string | null;
  cost_center?: CostCenter | null;
}

export interface AgentConfig {
  actor_id: string;
  name: string;
  owner_id: string;
  cost_center: CostCenter;
  per_txn_cap_cents: number;
  monthly_budget_cents: number;
  auto_approve_under_cents: number;
  description: string;
}

export interface Sku {
  sku: string;
  description: string;
  unit: string;
  unit_price_cents: number;
  tolerance_pct: number;
}

export interface Vendor {
  id: string;
  name: string;
  rails: RailName[];
  billing: 'per_request' | 'monthly_invoice' | 'semi_monthly_invoice';
  category: string;
  payout_address?: string | null;
  payout_address_changed_at?: string | null;
  previous_payout_address?: string | null;
  bank_account_last4?: string | null;
  default_account: string;
  skus: Sku[];
  status: 'active' | 'blocked';
  notes?: string | null;
}

export interface Account {
  code: string;
  name: string;
  type: 'asset' | 'liability' | 'equity' | 'revenue' | 'expense';
}

export interface Period {
  id: string;
  status: 'open' | 'closed';
  closed_by?: string | null;
  closed_at?: string | null;
}

export interface Company {
  name: string;
  description: string;
  today: string;
  synthetic_notice: string;
}

export interface World {
  company: Company;
  actors: Actor[];
  agents: AgentConfig[];
  vendors: Vendor[];
  accounts: Account[];
  periods: Period[];
  rail_mode?: 'offline' | 'live';
  rail?: { mode: 'offline' | 'live' | string; kind?: string; settlement_label?: string } | null;
  /** Full ISO timestamp of the world clock, when the server provides it. */
  now?: string | null;
}

// ---------------------------------------------------------------- rails

export interface PaymentChallenge {
  scheme: string;
  network: string;
  asset: string;
  pay_to: string;
  amount_atomic: string;
  amount_cents: number;
  resource: string;
  description: string;
  max_timeout_seconds: number;
  raw_b64: string;
  received_at: string;
}

export interface Receipt {
  rail: RailName;
  simulated: boolean;
  network?: string | null;
  tx_ref: string;
  payer: string;
  payee: string;
  amount_cents: number;
  amount_atomic?: string | null;
  payload_sha256?: string | null;
  signature_verified?: boolean | null;
  settled_at: string;
  resource_delivered: boolean;
  resource_sha256?: string | null;
}

// ---------------------------------------------------------------- decisions

export interface Check {
  rule_id: string;
  name: string;
  passed: boolean;
  severity: Severity;
  detail: string;
  evidence: Record<string, unknown>;
}

export interface Decision {
  outcome: Outcome;
  required_approver_role?: Role | null;
  checks: Check[];
  summary: string;
  policy_version: string;
  payee_hold: boolean;
}

export interface Question {
  id: string;
  asked_by: string;
  to: string;
  text: string;
  answer?: string | null;
  answered_by?: string | null;
  at: string;
  answered_at?: string | null;
}

export type LinkRel =
  | 'duplicate_of'
  | 'reverses'
  | 'reconciles'
  | 'accrues'
  | 'corrects'
  | 'precedent'
  | 'policy'
  | 'dispute'
  | 'related';

export interface Link {
  rel: LinkRel;
  id: string;
}

export interface Approval {
  actor: string;
  role?: string;
  at: string;
  note?: string;
  kind?: 'spend' | 'payee' | 'invoice' | 'policy' | 'correction' | string;
  [k: string]: unknown;
}

export interface WorkItem {
  id: string;
  kind: WorkItemKind;
  state: State;
  period: string;
  created_at: string;
  updated_at: string;
  requested_by: string;
  proposed_by: string;
  approved_by?: string | null;
  approvals: Approval[];
  required_approver_role?: Role | null;
  vendor_id?: string | null;
  sku?: string | null;
  quantity?: number | null;
  unit_price_cents?: number | null;
  amount_cents: number;
  currency: 'USD';
  account?: string | null;
  cost_center?: CostCenter | null;
  purpose?: string | null;
  task_ref?: string | null;
  idempotency_key?: string | null;
  resource_url?: string | null;
  challenge?: PaymentChallenge | null;
  decision?: Decision | null;
  receipt?: Receipt | null;
  journal_entry_ids: string[];
  links: Link[];
  flags: FlagCode[];
  questions: Question[];
  memo?: string | null;
  title: string;
  simulated: boolean;
  data: Record<string, unknown>;
}

export interface PurchaseRequest {
  vendor_id: string;
  sku: string;
  quantity: number;
  unit_price_cents?: number | null;
  purpose: string;
  task_ref?: string | null;
  idempotency_key?: string | null;
  memo?: string | null;
  resource_url?: string | null;
  pay_to_override?: string | null;
}

// ---------------------------------------------------------------- invoices

export interface UsageWindow {
  start: string;
  end: string;
}

export interface InvoiceLine {
  line_id: string;
  description: string;
  sku: string;
  quantity: number;
  unit_price_cents: number;
  amount_cents: number;
  usage_window?: UsageWindow | null;
}

export interface VendorInvoice {
  vendor_id: string;
  invoice_number: string;
  period: string;
  issued_at: string;
  due_at: string;
  lines: InvoiceLine[];
  total_cents: number;
}

export interface ReconciliationLine {
  line_id: string;
  description: string;
  invoiced_qty: number;
  metered_qty?: number | null;
  paid_elsewhere_qty: number;
  expected_qty?: number | null;
  contract_rate_cents?: number | null;
  invoiced_rate_cents: number;
  rate_ok: boolean;
  variance_cents: number;
  finding: string;
  evidence: Record<string, unknown>;
}

export interface ReconciliationProposal {
  invoice_number: string;
  vendor_id: string;
  lines: ReconciliationLine[];
  invoice_total_cents: number;
  pay_cents: number;
  dispute_cents: number;
  summary: string;
  dispute_note?: string | null;
}

// ---------------------------------------------------------------- policies

export interface Policy {
  id: string;
  version: number;
  kind: 'auto_approve' | 'coding' | 'tolerance';
  status: 'proposed' | 'active' | 'rejected' | 'retired';
  scope: { agent_id?: string; vendor_id?: string; sku?: string; [k: string]: unknown };
  params: { max_amount_cents?: number; account?: string; tolerance_pct?: number; [k: string]: unknown };
  rationale: string;
  evidence: {
    precedent_count?: number;
    sample_ids?: string[];
    window?: string;
    max_seen_cents?: number;
    rejections?: number;
    [k: string]: unknown;
  };
  proposed_by: string;
  approved_by?: string | null;
  created_at: string;
  activated_at?: string | null;
  work_item_id?: string | null;
}

// ---------------------------------------------------------------- ledger

export interface JournalLine {
  account: string;
  debit_cents: number;
  credit_cents: number;
  cost_center?: CostCenter | null;
  vendor_id?: string | null;
  memo?: string | null;
}

export interface JournalEntry {
  id: string;
  date: string;
  period: string;
  memo: string;
  lines: JournalLine[];
  source_work_item?: string | null;
  reverses?: string | null;
  reversed_by?: string | null;
  posted_by: string;
  created_at: string;
}

export interface AccountBalance {
  account: string;
  name: string;
  type: string;
  debit_cents: number;
  credit_cents: number;
  balance_cents: number;
}

// ---------------------------------------------------------------- audit

export interface AuditEvent {
  seq: number;
  at: string;
  actor: string;
  action: string;
  subject: string;
  allowed: boolean;
  reason: string;
  diff: Record<string, unknown>;
  evidence: Record<string, unknown>;
  prev_hash: string;
  hash: string;
}

export interface ChainCheck {
  ok: boolean;
  checked: number;
  first_bad_seq?: number | null;
  head_hash: string;
}

export interface WhyStep {
  seq: number;
  at: string;
  actor: string;
  actor_name: string;
  action: string;
  sentence: string;
  rule_ids: string[];
  evidence: Record<string, unknown>;
}

export interface WhyReport {
  subject: string;
  title: string;
  answer: string;
  steps: WhyStep[];
  related: string[];
}

export interface NextAction {
  action: string;
  label: string;
  allowed: boolean;
  reason: string;
}

export interface WorkItemDetail {
  item: WorkItem;
  events: AuditEvent[];
  journal_entries: JournalEntry[];
  linked: WorkItem[];
  explanation: string;
  reconciliation?: ReconciliationProposal | null;
  next_actions: NextAction[];
}

export interface ClosePreview {
  period: string;
  accruals: WorkItem[];
  blockers: { item_id: string; reason: string; [k: string]: unknown }[];
  can_close: boolean;
}

// ---------------------------------------------------------------- API envelopes (04)

export interface SessionInfo {
  actor: Actor;
  role: Role;
  can: {
    approve_purchases: boolean;
    approve_invoices: boolean;
    approve_policies: boolean;
    verify_payee: boolean;
    recode: boolean;
    close_period: boolean;
    run_scenarios: boolean;
    /** Accountant and controller can key in a vendor credit note against an approved invoice. */
    record_credit_notes?: boolean;
    /** Accountant and controller can upload invoice PDFs. */
    upload_invoices?: boolean;
  };
}

// ---------------------------------------------------------------- invoice PDFs

export interface ExtractedLine {
  description: string;
  sku: string;
  quantity: string;
  unit_price: string;
  amount: string;
  usage_start: string;
  usage_end: string;
}

/** What an extractor proposed from a PDF. Never trusted; Elix checks every value in code. */
export interface ExtractedInvoice {
  extractor: 'claude' | 'offline';
  extractor_label: string;
  model?: string | null;
  vendor_name: string;
  invoice_number: string;
  issue_date: string;
  due_date: string;
  period_start: string;
  period_end: string;
  currency: string;
  total: string;
  lines: ExtractedLine[];
  pages: number;
  notes: string[];
}

export interface IntakeLine {
  line_id: string;
  description: string;
  printed_sku: string | null;
  sku: string | null;
  sku_description: string | null;
  sku_source: 'item code' | 'description' | null;
  quantity: number | null;
  unit_price_cents: number | null;
  amount_cents: number | null;
  expected_cents: number | null;
  arithmetic_ok: boolean | null;
  usage_start: string | null;
  usage_end: string | null;
  window_source: 'line' | 'billing period' | null;
}

export interface IntakeResult {
  accepted: boolean;
  checks: Check[];
  vendor: { id: string; name: string } | null;
  lines: IntakeLine[];
  total_cents: number | null;
  proposed_invoice: VendorInvoice | null;
  filename: string;
  sha256: string;
  bytes: number;
  extraction: ExtractedInvoice;
}

export interface IntakeResponse {
  intake: IntakeResult;
  item: WorkItemDetail;
}

export interface IntakeSample {
  name: string;
  title: string;
  description: string;
  bytes: number;
}

// ---------------------------------------------------------------- disputes and credit notes

export type DisputeStatus = 'open' | 'partly_credited' | 'credited';

/** A vendor credit note applied to an approved invoice, as the engine records it in item.data.credit_notes. */
export interface CreditNote {
  credit_number: string;
  amount_cents: number;
  applied_cents: number;
  rounding_cents: number;
  open_before_cents: number;
  disputed_cents: number;
  reason: string;
  issued_at: string;
  recorded_at: string;
  recorded_by: string;
  cost_center?: CostCenter | null;
  je_id: string | null;
  posted_on?: string | null;
}

export interface AgentSpend {
  agent_id: string;
  spent_cents: number;
  committed_cents: number;
  budget_cents: number;
  cap_cents: number;
}

export interface Summary {
  period: string;
  today: string;
  spend_cents: number;
  spend_by_agent: AgentSpend[];
  spend_by_rail: { rail: RailName; cents: number; count: number }[];
  spend_by_account: { account: string; name: string; cents: number }[];
  decisions: { auto_approved: number; human_approved: number; rejected: number; denied: number; pending: number };
  automated_share: number;
  inbox_counts: Record<string, number>;
  flags_recent: { item_id: string; flag: FlagCode; at: string }[];
  daily_spend: { date: string; x402_cents: number; bank_cents: number }[];
  purchases_total?: number;
}

export interface WorkItemList {
  items: WorkItem[];
  total: number;
}

export interface LedgerList {
  entries: JournalEntry[];
  total: number;
}

export interface BalancesResponse {
  balances: AccountBalance[];
  trial_balance_ok: boolean;
}

export interface AgentStatus extends AgentConfig {
  spent_cents: number;
  committed_cents: number;
  last_activity_at?: string | null;
  mode: 'claude' | 'scripted';
}

export interface AgentSession {
  id: string;
  agent_id: string;
  task: string;
  mode: 'claude' | 'scripted';
  model?: string | null;
  started_at: string;
  ended_at?: string | null;
  status: string;
  request_ids: string[];
  mode_label?: string | null;
  summary?: string | null;
  error?: string | null;
}

export interface TranscriptEntry {
  seq?: number;
  at: string;
  role: 'assistant' | 'tool_call' | 'tool_result' | 'user';
  text?: string | null;
  tool?: string | null;
  args?: Record<string, unknown> | null;
  result?: unknown;
}

export interface AgentSessionDetail extends AgentSession {
  transcript: TranscriptEntry[];
}

export interface Scenario {
  id: string;
  title: string;
  description: string;
  prerequisites: string[];
  ready: boolean;
  not_ready_reason?: string | null;
  next_actor?: string | null;
  last_run?: { at: string; result_ids: string[]; message?: string | null } | null;
}

export interface ScenarioResult {
  scenario_id: string;
  result_ids: string[];
  message: string;
}

export interface X402Panel {
  challenge_decoded?: Record<string, unknown> | null;
  payload_decoded?: Record<string, unknown> | null;
  payload_sha256?: string | null;
  payment_response_decoded?: Record<string, unknown> | null;
  signature_verified?: boolean | null;
  simulated?: boolean | null;
  [k: string]: unknown;
}

// The eval report shape is described in prose in 03. The console reads it defensively.
export interface EvalCategoryRow {
  category: string;
  planted: number;
  caught: number;
  not_auto_paid: number;
  auto_paid: number;
  notes?: string | null;
  missed?: unknown[];
  [k: string]: unknown;
}

export interface EvalReport {
  anomalies_total: number;
  anomalies_caught: number;
  anomalies_not_auto_paid: number;
  anomalies_auto_paid: number;
  clean_total: number;
  clean_auto_paid: number;
  clean_escalated: number;
  all_auto_paid?: number;
  automation_precision: number;
  automation_rate_before_learning: number;
  automation_rate_after_learning: number;
  per_category: EvalCategoryRow[] | Record<string, Omit<EvalCategoryRow, 'category'>>;
  chain_ok: boolean;
  runtime_seconds: number;
  seed: number;
  generated_at: string;
  engine_git_hash?: string | null;
  mode?: string;
  learned_policies?: unknown[];
  policies_learned?: Policy[];
  events_total?: number;
  purchases_total?: number;
  invoices_total?: number;
  warmup_clean_purchases?: number;
  scored_clean_purchases?: number;
  categories_caught?: number;
  categories_total?: number;
  chain_checked?: number;
  chain_head?: string;
  method?: string;
  fast?: boolean;
  world?: { start?: string; warmup_days?: number; days?: number };
  [k: string]: unknown;
}

export interface ApiErrorBody {
  error: { code: string; message: string; rule_id?: string | null; audit_seq?: number | null; [k: string]: unknown };
}
