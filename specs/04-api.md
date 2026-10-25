# 04 · HTTP API

Package `elix/server/` (FastAPI). Default port 8400. All JSON uses snake_case and the field names from `elix/contracts.py`. Money is integer cents. The server also serves the built console from `console/dist` at `/` (SPA fallback to `index.html` for non-API paths) and exposes `/healthz`.

CORS: allow `http://localhost:5173` (Vite dev) and same origin.

## Auth

`Authorization: Bearer <token>` with the demo tokens from 01. Missing or unknown token returns 401. The console keeps the current token in memory (acting-as switcher). Agents and the MCP server use agent tokens. A permission failure returns 403 with the error body below, and the denial is written to the audit log by the engine.

## Error body

```json
{ "error": { "code": "denied", "message": "Scout cannot approve its own request. An approval has to come from Maya Chen.", "rule_id": "R31", "audit_seq": 5123 } }
```
Codes: `unauthorized`, `denied`, `not_found`, `invalid`, `conflict`, `rail_error`.

## Endpoints

### Session and world
- `GET /api/session` → `{ actor: Actor, role: str, can: { approve_purchases: bool, approve_invoices: bool, approve_policies: bool, verify_payee: bool, recode: bool, close_period: bool, run_scenarios: bool } }`. `run_scenarios` is true for humans (demo convenience, scenarios act as the right actors internally).
- `GET /api/world` → `{ company: {name, description, today, synthetic_notice}, actors: Actor[], agents: AgentConfig[], vendors: Vendor[], accounts: Account[], periods: Period[] }`
  - `AgentConfig { actor_id, name, owner_id, cost_center, per_txn_cap_cents, monthly_budget_cents, auto_approve_under_cents, description }`
  - `Account { code, name, type }`
- `GET /api/summary?period=2026-10` →
```json
{
  "period": "2026-10",
  "today": "2026-10-24",
  "spend_cents": 0,
  "spend_by_agent": [ { "agent_id": "agent:atlas", "spent_cents": 0, "committed_cents": 0, "budget_cents": 600000, "cap_cents": 25000 } ],
  "spend_by_rail": [ { "rail": "x402", "cents": 0, "count": 0 }, { "rail": "bank", "cents": 0, "count": 0 } ],
  "spend_by_account": [ { "account": "6110", "name": "Data and research", "cents": 0 } ],
  "decisions": { "auto_approved": 0, "human_approved": 0, "rejected": 0, "denied": 0, "pending": 0 },
  "automated_share": 0.0,
  "inbox_counts": { "human:priya": 0, "human:dev": 0, "human:maya": 0, "human:sam": 0 },
  "flags_recent": [ { "item_id": "WI-1042", "flag": "PAYEE_CHANGED", "at": "..." } ],
  "daily_spend": [ { "date": "2026-10-01", "x402_cents": 0, "bank_cents": 0 } ]
}
```

### Work items
- `GET /api/work-items?state=&kind=&agent=&vendor=&flag=&period=&inbox=me&limit=100&offset=0` → `{ items: WorkItem[], total: int }`. `inbox=me` returns items whose required approver role and ownership match the current human (budget owners only see their own agents' items).
- `GET /api/work-items/{id}` → `WorkItemDetail { item: WorkItem, events: AuditEvent[], journal_entries: JournalEntry[], linked: WorkItem[], explanation: str, reconciliation: ReconciliationProposal | null, next_actions: [ { action, label, allowed, reason } ] }`. `next_actions` tells the console which buttons to show for the current actor, with `allowed=false` and a reason when segregation blocks it (the console shows the reason instead of hiding the button).
- `POST /api/purchases` (agent token) body `PurchaseRequest { vendor_id, sku, quantity, unit_price_cents, purpose, task_ref, idempotency_key, memo?, resource_url? }` → `WorkItemDetail` (201 for new, 200 for idempotent replay).
- `POST /api/work-items/{id}/approve` `{ note }` → `WorkItemDetail`
- `POST /api/work-items/{id}/reject` `{ note }` → `WorkItemDetail`
- `POST /api/work-items/{id}/ask` `{ text }` → `WorkItemDetail`
- `POST /api/work-items/{id}/answer` `{ question_id, text }` (agent) → `WorkItemDetail`
- `POST /api/work-items/{id}/verify-payee` `{ method, note }` (controller) → `WorkItemDetail`

### Invoices and meters (sync, used by scenarios and seed through the facade; also exposed for completeness)
- `POST /api/invoices` (human token allowed for demo, acts as `system:sync`) body `VendorInvoice` → `WorkItemDetail`
- `GET /api/reconciliations?period=` → `{ items: [ { item: WorkItem, reconciliation: ReconciliationProposal } ] }`
- `POST /api/invoices/{invoice_number}/credit-notes` (accountant or controller token; agents, budget owners and the auditor get 403) body `{ vendor_id, credit_number, amount_cents, reason?, issued_at? }` → `WorkItemDetail` of the invoice, 201. Refusals are audited and answer 404 (no such invoice), 400 (wrong vendor or bad amount) or 409 (not approved, no open dispute, duplicate credit number, more than the open dispute). `GET /api/session` adds `can.record_credit_notes`.
- `POST /api/invoices/pdf?offline=` (accountant or controller token, multipart field `file`, PDF up to 10 MB) → 201 `{ intake: { accepted, checks: Check[] (R40 to R46), vendor, lines, total_cents, proposed_invoice, filename, sha256, bytes, extraction: ExtractedInvoice }, item: WorkItemDetail }`. Claude reads the PDF when `ANTHROPIC_API_KEY` is set, else the offline parser (`offline=true` forces it). Accepted invoices go through ingestion and reconciliation; a failed check leaves an invoice item in NEEDS_INFO for the accountant with nothing posted. Not a PDF is 400. `GET /api/session` adds `can.upload_invoices`.
- `GET /api/intake/samples` (human token) → `{ samples: [ { name, title, description, bytes } ], extractor: "claude" | "offline" }` and `GET /api/intake/samples/{name}` → the synthetic sample PDF.

### Ledger
- `GET /api/ledger?period=2026-10&account=&limit=&offset=` → `{ entries: JournalEntry[], total }`
- `GET /api/balances?period=2026-10` → `{ balances: [ { account, name, type, debit_cents, credit_cents, balance_cents } ], trial_balance_ok: bool }`
- `POST /api/ledger/{je_id}/recode` `{ line_index, new_account, note }` → `WorkItemDetail` (correction item)

### Policies
- `GET /api/policies?status=` → `{ policies: Policy[] }`
- `POST /api/policies/learn` → `{ proposed: Policy[] }` (any human can trigger; actor recorded as `system:learner`, triggered_by in evidence)
- `POST /api/policies/{id}/approve` `{ note }` → `Policy`
- `POST /api/policies/{id}/reject` `{ note }` → `Policy`

### Periods
- `GET /api/periods` → `{ periods: Period[] }`
- `POST /api/periods/{period}/close-preview` → `ClosePreview { period, accruals: WorkItem[], blockers: [ { item_id, reason } ], can_close: bool }`
- `POST /api/periods/{period}/close` → `Period` (409 with blockers if not closable)

### Audit
- `GET /api/audit?subject=&actor=&action=&allowed=&limit=200&before_seq=` → `{ events: AuditEvent[] }`
- `GET /api/audit/verify` → `{ ok: bool, checked: int, first_bad_seq: int | null, head_hash: str }`
- `GET /api/why/{subject}` → `WhyReport { subject, title, answer: str, steps: [ { seq, at, actor, actor_name, action, sentence, rule_ids, evidence } ], related: [str] }`. `answer` is a plain paragraph that directly answers "why is this item in this state".

### Agents
- `GET /api/agents` → `{ agents: [ AgentConfig & { spent_cents, committed_cents, last_activity_at, mode: "claude" | "scripted" } ] }`
- `GET /api/agents/sessions?agent=` → `{ sessions: [ { id, agent_id, task, mode, model, started_at, ended_at, status, request_ids } ] }`
- `GET /api/agents/sessions/{id}` → session plus `transcript: [ { at, role: "assistant"|"tool_call"|"tool_result"|"user", text, tool?, args?, result? } ]`
- `POST /api/agents/{agent}/run` `{ task }` → `{ session_id }` starts a background run (Claude if key present, else scripted). Human token required.

### Proof
- `GET /api/eval/latest` → the contents of `eval/report.json` or 404 with a message to run `python -m elix eval`.
- `POST /api/eval/run` → runs the eval in the background, returns `{ started: true }`; progress over SSE kind `eval`.

### Scenarios
- `GET /api/scenarios` → `{ scenarios: [ { id, title, description, prerequisites: [str], ready: bool, last_run: {at, result_ids} | null } ] }`
- `POST /api/scenarios/{id}/run` body for `custom`: `{ agent_id, vendor_id, sku, quantity, unit_price_cents?, pay_to_override?, memo?, purpose, task_ref? }` → `{ scenario_id, result_ids: [str], message: str }`
- `POST /api/demo/reset` → reseeds the world (takes a few seconds) and returns `{ ok: true }`. Requires a human token.

### Events
- `GET /api/events` → Server-Sent Events. Each event `event: <kind>` and `data: <json>`. Kinds: `audit` (AuditEvent), `work_item` (WorkItem), `journal` (JournalEntry), `policy` (Policy), `period` (Period), `scenario` ({id, status, message}), `eval` ({status, progress}). Send a `ping` every 15 seconds. Auth for SSE: token in query `?token=` since EventSource cannot set headers.

## Model shapes

Use `elix/contracts.py` as the canonical definitions. The console agent should generate or hand write TypeScript types from these shapes in `console/src/api/types.ts`. Optional fields may be `null`.
