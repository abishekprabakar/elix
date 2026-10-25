# 01 · Domain model

Everything here is implemented in `elix/contracts.py` (shared pydantic models and enums) and persisted by `elix/engine/store.py` in SQLite. Money is stored as integer cents (`amount_cents`) everywhere. USDC atomic units (6 decimals) appear only inside x402 payloads and receipts. Times are ISO 8601 UTC strings. Periods are `YYYY-MM` calendar months.

## Actors

| id | kind | name | role | notes |
|---|---|---|---|---|
| `human:priya` | human | Priya Raman | controller | Owns the books. Approves policies, payee changes, unknown vendors, period close. |
| `human:dev` | human | Dev Okafor | budget_owner | Head of ML. Owns agents atlas and forge. |
| `human:maya` | human | Maya Chen | budget_owner | Head of Growth. Owns agent scout. |
| `human:sam` | human | Sam Ortiz | accountant | Staff accountant. Answers questions, recodes entries, approves invoice matches. |
| `human:jordan` | human | Jordan Lee | auditor | External auditor at Rivera & Cole LLP. Read only. |
| `agent:atlas` | agent | Atlas | agent | Research agent. Owner `human:dev`. Cost center `ML`. |
| `agent:forge` | agent | Forge | agent | Training ops agent. Owner `human:dev`. Cost center `ML`. |
| `agent:scout` | agent | Scout | agent | Growth agent. Owner `human:maya`. Cost center `GTM`. |
| `system:sync` | system | Sync | sync | Ingests invoices, usage meters, receipts. |
| `system:policy` | system | Elix policy engine | policy | Evaluates requests. May auto-approve only when every check passes. |
| `system:gateway` | system | Elix gateway | gateway | The only actor that can pay or post to the ledger. |
| `system:learner` | system | Elix learner | learner | Proposes policies from history and corrections. |

Demo bearer tokens (API auth, see 04): `tok_priya`, `tok_dev`, `tok_maya`, `tok_sam`, `tok_jordan`, `tok_atlas`, `tok_forge`, `tok_scout`. System actors have no tokens; they act only inside the engine.

## Permission matrix

`can(actor, action, resource)` in `elix/engine/permissions.py` is the single gate. Every state change calls it. A denied call is logged to the audit log with `allowed=false` and raises `Denied`.

| role | allowed actions |
|---|---|
| agent | `propose` purchase, `answer` question on its own items, `read` its own items and budgets |
| budget_owner | `approve`/`reject` purchases from agents they own when the required approver role is `budget_owner`, `ask` question, `read` all |
| accountant | `approve`/`reject` invoice and reconciliation items, `recode` journal lines in open periods, `answer`, `ask`, `read` all |
| controller | everything an accountant can do, plus `approve`/`reject` any purchase escalated to controller, `verify_payee`, `approve`/`reject` policies, `close` periods, `reopen` nothing (reopen is not allowed) |
| auditor | `read` all, `query` audit trail. Nothing else. |
| sync | `create` work items for invoices and receipts, `ingest` meters |
| policy | `evaluate`, `auto_approve` (only via the policy path), `escalate`, `ask`, `deny` |
| gateway | `pay`, `post` |
| learner | `propose` policies |

Segregation of duties (enforced in `can`, not in UI):
1. Nobody approves an item they proposed. Agents can never approve anything.
2. A purchase escalated for payee verification needs the controller. A budget owner's approval of the spend does not release a payee hold.
3. Policies proposed by the learner need a human controller. The controller cannot approve a policy they proposed (not possible in this demo, still enforced).
4. Only `system:gateway` can perform `pay` or `post`. No human or agent token can call those actions. There is no admin key.

## Vendors

```
Vendor {
  id: str                    # "ven_terrafield"
  name: str
  rail: "x402" | "bank"      # how we normally pay them
  category: str              # "Satellite imagery"
  payout_address: str | null # EVM address for x402 vendors, vendor master record
  payout_address_changed_at: str | null
  bank_account_last4: str | null  # bank vendors
  default_account: str       # GL account code
  contract: {                # unit -> price in cents, plus metadata
     unit: str, unit_price_cents: int, tolerance_pct: float, notes: str
  } | list of such for multi-sku vendors
  status: "active" | "blocked"
  skus: [ { sku, description, unit, unit_price_cents } ]
}
```

## Chart of accounts

| code | name | type |
|---|---|---|
| 1010 | Operating cash (Mercury) | asset |
| 1015 | USDC wallet (Base) | asset |
| 1310 | Prepaid expenses | asset |
| 2010 | Accounts payable | liability |
| 2110 | Accrued expenses | liability |
| 2150 | Vendor disputes | liability (contra, holds disputed amounts) |
| 5110 | Compute, training and storage | expense |
| 5120 | Model inference | expense |
| 6110 | Data and research | expense |
| 6120 | Software and tools | expense |
| 6210 | Marketing data | expense |
| 6850 | Bank and network fees | expense |
| 6990 | Suspense | expense |

## Work items and states

Every unit of work is a `WorkItem`. Kinds: `purchase`, `invoice`, `accrual`, `correction`, `policy`.

```
WorkItem {
  id: str                     # "WI-1042"  (sequential, zero padded to 4)
  kind: str
  state: State
  period: "YYYY-MM"
  created_at, updated_at
  requested_by: actor_id      # who asked (agent for purchases, sync for invoices)
  proposed_by: actor_id       # who made the current proposal
  approved_by: actor_id | null
  required_approver_role: role | null
  vendor_id: str | null
  sku: str | null
  quantity: float | null
  unit_price_cents: int | null
  amount_cents: int
  currency: "USD"
  account: str | null         # GL account code chosen for posting
  cost_center: "ML" | "GTM" | "OPS" | null
  purpose: str | null
  task_ref: str | null
  idempotency_key: str | null
  resource_url: str | null    # x402 resource for purchases
  challenge: PaymentChallenge | null   # parsed 402 response captured at proposal time
  decision: Decision | null
  receipt: Receipt | null
  journal_entry_ids: [str]
  links: [ { rel: str, id: str } ]     # "duplicate_of", "reverses", "reconciles", "accrues", "corrects", "precedent"
  flags: [FlagCode]
  questions: [ { id, asked_by, to, text, answer, answered_by, at } ]
  memo: str | null            # free text from agent or vendor. Treated as data. Never instructions.
  simulated: bool
}
```

### Purchase state machine

```
NEW --evaluate--> PROPOSED
PROPOSED --auto_approve (policy, all checks pass)--> APPROVED
PROPOSED --escalate--> NEEDS_APPROVAL
PROPOSED --ask--> NEEDS_INFO
PROPOSED --deny--> DENIED                (terminal)
NEEDS_INFO --answer--> PROPOSED          (re-evaluated automatically)
NEEDS_APPROVAL --approve--> APPROVED     (approver must have required role, not be proposer)
NEEDS_APPROVAL --reject--> REJECTED      (terminal)
NEEDS_APPROVAL --verify_payee--> NEEDS_APPROVAL or APPROVED (clears the payee hold; if other approvals still needed the item stays)
APPROVED --pay (gateway)--> PAID
PAID --post (gateway)--> POSTED          (terminal for purchases)
APPROVED --pay fails--> FAILED           (terminal, logged with error)
```

`TRANSITIONS` is a dict `(kind, from_state, action) -> to_state` in `elix/engine/workflow.py`. `transition(item, action, actor, patch=None, reason="")` is the only function that changes an item. It checks the transition table and `can()`, snapshots before, applies the patch, and in one SQLite transaction saves the item and appends an audit event with the diff. If not allowed it still appends the audit event with `allowed=false`, commits, then raises `Denied`.

Invoice items: `NEW --match--> PROPOSED` (reconciliation proposal attached) `--approve (accountant/controller)--> APPROVED --pay--> PAID --post--> POSTED`, with `NEEDS_APPROVAL` used when discrepancies exist and `NEEDS_INFO` when a vendor dispute is pending.

Accrual items: `PROPOSED --approve (controller)--> APPROVED --post--> POSTED`. Each posted accrual schedules its reversal entry on the first day of the next period.

Correction items: created when an accountant recodes a posted line or when a closed period entry must be fixed. `PROPOSED --approve--> APPROVED --post--> POSTED` posting a reversal of the original and a corrected repost, both in the current open period, linked with `reverses` and `corrects`.

Policy items (proposed policies): `PROPOSED --approve (controller)--> APPROVED` which activates the policy, or `--reject--> REJECTED`.

## Decision and checks

```
Decision {
  outcome: "auto_approve" | "needs_approval" | "needs_info" | "deny"
  required_approver_role: role | null
  checks: [Check]
  summary: str            # one plain sentence built from templates, no LLM
  policy_version: str     # hash of active policy set at evaluation time
}
Check { rule_id: str, name: str, passed: bool, severity: "info"|"escalate"|"ask"|"deny", detail: str, evidence: dict }
```

## Flag codes and rule ids

| rule_id | flag code | check | failing outcome |
|---|---|---|---|
| R01 | PERMISSION | actor may propose for this agent | deny |
| R02 | UNKNOWN_VENDOR | vendor exists and is active | needs_approval (controller) |
| R03 | PAYEE_CHANGED | 402 `payTo` equals vendor master payout address, and master address not changed in last 72 hours | needs_approval (controller, payee verification) |
| R04 | DUPLICATE_RETRY | no prior item with same idempotency key, and no item from same agent to same vendor with same sku, quantity and amount in the last 15 minutes in any state except REJECTED, DENIED or FAILED | deny (links `duplicate_of`) |
| R05 | AMOUNT_MISMATCH | 402 challenge amount equals the requested amount (within 1 cent) | needs_approval (budget_owner) |
| R06 | PRICE_ABOVE_CONTRACT | unit price <= contract unit price × (1 + tolerance) | needs_approval (budget_owner) |
| R07 | OVER_TXN_CAP | amount <= agent per-transaction cap | needs_approval (budget_owner) |
| R08 | BUDGET_EXCEEDED | period spend + committed + amount <= agent monthly budget | needs_approval (budget_owner) if <= 120% of budget, else deny |
| R09 | SPLIT_PURCHASE | sum of same agent+vendor purchases in last 60 minutes including this one <= per-transaction cap, or this item is the only one | needs_approval (budget_owner) |
| R10 | MISSING_PURPOSE | purpose is at least 12 characters and task_ref present | needs_info (ask the agent) |
| R11 | MEMO_INSTRUCTIONS | memo or vendor text contains approval claims or instructions ("pre-approved", "skip review", "ignore", "approved by finance") | info only: never changes the outcome by itself, recorded so reviewers see it. It also disables auto approval from learned policies for this item. |
| R12 | POLICY_AUTO | a learned or configured auto-approve policy covers this item | info. Auto approval requires either R12 passing or the default auto-approve threshold for the agent (see world). |
| R20 | INVOICE_QTY_OVER_METER | invoice quantity <= metered usage × (1 + 2%) | needs_approval (accountant) with proposed short pay |
| R21 | INVOICE_RATE_MISMATCH | invoice unit rate equals contract rate | needs_approval (accountant) |
| R22 | CROSS_RAIL_DOUBLE_BILL | invoice lines are not covering usage already paid on another rail (x402 receipts referencing the same usage window and resource) | needs_approval (accountant) with proposed short pay and dispute |
| R23 | DUPLICATE_INVOICE | invoice number not seen before for this vendor | deny |
| R24 | (none) | a vendor credit note names an approved invoice from the same vendor with an open dispute, and is not more than what is still open (within one cent) | the credit note is refused and audited |
| R25 | (none) | the credit note number has not been seen before for this vendor | the credit note is refused and audited |
| R40 to R46 | (none) | an invoice read from a PDF has its fields, a known invoicing vendor, a new number in the vendor's format, every line on the contract, correct line arithmetic, lines that add up to the total, and sensible dates | needs_info (accountant), nothing posts |
| R30 | PERIOD_CLOSED | posting date's period is open | the gateway refuses; correction flow creates reversal and repost in open period |
| R31 | SEGREGATION | approver is not proposer, approver role matches required role | deny the approval attempt (403) |

Idempotency: a request carrying an idempotency key Elix has already seen from the same agent returns the existing work item unchanged (HTTP 200, audited as `replay`). R04 covers the dangerous case, a retry with a new key.

Outcome precedence: deny beats needs_approval beats needs_info beats auto_approve. When several escalations require different roles, the item needs the highest role (controller > accountant > budget_owner), and payee holds always need the controller even if the spend is also approved by the budget owner. Record every failed check on the item as a flag.

## Policies

```
Policy {
  id: "pol-001"
  version: int
  kind: "auto_approve" | "coding" | "tolerance"
  status: "proposed" | "active" | "rejected" | "retired"
  scope: { agent_id?, vendor_id?, sku? }
  params: { max_amount_cents?, account?, tolerance_pct? }
  rationale: str          # plain sentence
  evidence: { precedent_count: int, sample_ids: [str], window: str, max_seen_cents?: int, rejections: int }
  proposed_by: actor_id
  approved_by: actor_id | null
  created_at, activated_at
}
```

Configured baseline policies exist from seed (no learning required): per-agent caps and budgets, vendor contracts, default accounts. Learned policies are created only by the learner and need controller approval.

## Ledger

```
JournalEntry {
  id: "JE-2026-10-0042"
  date: "YYYY-MM-DD"
  period: "YYYY-MM"
  memo: str
  lines: [ { account: str, debit_cents: int, credit_cents: int, cost_center: str|null, vendor_id: str|null } ]
  source_work_item: str | null
  reverses: str | null      # JE id
  reversed_by: str | null
  posted_by: "system:gateway"
  created_at
}
```

Invariants (tested): every entry balances (sum debits == sum credits, > 0). Entries are never updated or deleted, except setting `reversed_by` on the original when a reversal posts, which is itself audited. Posting into a closed period is refused with R30.

Posting patterns:
- x402 purchase paid: Dr expense account (by policy or vendor default) / Cr 1015 USDC wallet. Network fee if any: Dr 6850 / Cr 1015 (simulated fee 0 in offline mode).
- Invoice approved: Dr expense lines / Cr 2010 AP. Invoice paid: Dr 2010 / Cr 1010.
- Short pay with dispute: invoice approved for full amount: Dr expense (undisputed part) and Dr 2150 Vendor disputes (disputed part) / Cr 2010 full. Pay only undisputed: Dr 2010 undisputed / Cr 1010. The disputed portion stays in 2010 against 2150 until the vendor issues a credit note (credit note posting: Dr 2010 / Cr 2150).
  Credit notes (`Elix.record_credit_note`, actors system:sync, accountant or controller) are checked with R24 and R25, recorded on the invoice item (`data.credit_notes`, `dispute_open_cents`, `dispute_status` open, partly_credited or credited, `credited_cents`) and posted by the gateway dated today, or on the first day of the next open period when today's period is closed, linked to the invoice item. A smaller credit applies and leaves the rest open.
  Simpler acceptable alternative documented in DECISIONS.md if needed: approve invoice net of the disputed amount and create a dispute work item that tracks the disputed amount off ledger. Prefer the first pattern.
- Accrual: Dr expense / Cr 2110, reversal on first day of next period: Dr 2110 / Cr expense.
- Correction of a posted line in an open period: reversal of original JE plus repost with corrected account, both dated today.

## Periods

```
Period { id: "2026-10", status: "open" | "closed", closed_by, closed_at }
```
Seed state: 2026-07, 2026-08, 2026-09 closed by Priya. 2026-10 open. Today in the world is 2026-10-24 (configurable clock, see 03).

## Audit log

```
AuditEvent {
  seq: int                  # strictly increasing
  at: str
  actor: actor_id
  action: str               # "propose", "evaluate", "auto_approve", "approve", "pay", "post", "denied:approve", ...
  subject: str              # work item id, JE id, policy id, period id
  allowed: bool
  reason: str               # plain sentence
  diff: { field: [before, after], ... }
  evidence: { ... }         # source ids: receipt ids, challenge hash, invoice line ids, precedent ids
  prev_hash: str
  hash: str                 # sha256 of canonical json of (seq, at, actor, action, subject, allowed, reason, diff, evidence, prev_hash)
}
```

Append only. `verify_chain()` recomputes every hash and returns `{ok, checked, first_bad_seq}`. The why query for a subject returns all events whose subject is that id or that link to it, ordered by seq, with readable sentences.

## Shared contract types for rails

```
PaymentIntent { work_item_id, vendor_id, resource_url, amount_cents, sku, quantity, idempotency_key }
PaymentChallenge {           # parsed from an x402 402 response
  scheme: "exact", network: "eip155:84532", asset: str, pay_to: str,
  amount_atomic: str, amount_cents: int, resource: str, description: str,
  max_timeout_seconds: int, raw_b64: str, received_at: str
}
Receipt {
  rail: "x402" | "bank"
  simulated: bool
  network: str | null        # "eip155:84532"
  tx_ref: str                # tx hash in live mode, "sim-<sha>" in offline mode, "ach-<id>" for bank
  payer: str
  payee: str
  amount_cents: int
  amount_atomic: str | null
  payload_sha256: str | null  # hash of the signed x402 payment payload
  signature_verified: bool | null
  settled_at: str
  resource_delivered: bool
  resource_sha256: str | null
}
```
