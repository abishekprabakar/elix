# 02 · Engine

Package `elix/engine/`. Pure Python, stdlib `sqlite3`, pydantic models from `elix/contracts.py`. No network calls inside the engine except through an injected `Rail` (see 05). The engine must run fully offline and deterministically given a seed and a clock.

## Module layout

```
elix/
  contracts.py          # shared models and enums (provided, see 01)
  engine/
    __init__.py         # exports Elix (facade)
    clock.py            # Clock with fixed "now" for the world, advance(seconds)
    store.py            # SQLite schema, connection, transactions, row <-> model mapping
    permissions.py      # ROLES, can(actor, action, item) -> (bool, reason)
    workflow.py         # TRANSITIONS, transition()
    audit.py            # append_event(), verify_chain(), why(subject)
    ledger.py           # post_entry(), reverse_entry(), balances(), trial_balance()
    policy.py           # evaluate_purchase() -> Decision, rule implementations R01..R12
    gateway.py          # the only code path that calls Rail.pay and ledger.post_entry
    reconcile.py        # invoices vs meters vs receipts, rules R20..R23
    accruals.py         # period end accruals and scheduled reversals
    learner.py          # learn_from_history(), learn_from_corrections()
    corrections.py      # recode in open period, reversal and repost for closed periods
    periods.py          # close_preview(), close(), is_open()
    explain.py          # deterministic sentence templates for decisions and why()
    events.py           # in-process pub/sub for the SSE stream
    facade.py           # class Elix: the API the server, CLI, agents and tests use
```

## Facade (the only interface other components use)

```python
class Elix:
    def __init__(self, db_path: str, rail_registry: RailRegistry, clock: Clock | None = None): ...

    # world
    def world(self) -> World                              # company, actors, vendors, accounts, periods, budgets
    def summary(self, period: str) -> Summary

    # purchases (agents)
    def request_purchase(self, actor_id: str, req: PurchaseRequest) -> WorkItem
        # creates WI (NEW), fetches the x402 challenge through the rail (rail.challenge(intent)) for x402 vendors,
        # evaluates policy, transitions to APPROVED / NEEDS_APPROVAL / NEEDS_INFO / DENIED,
        # and if APPROVED immediately executes payment through the gateway (pay then post).
    def answer(self, actor_id: str, item_id: str, question_id: str, text: str) -> WorkItem   # re-evaluates
    def ask(self, actor_id: str, item_id: str, text: str) -> WorkItem

    # humans
    def approve(self, actor_id: str, item_id: str, note: str = "") -> WorkItem    # gateway executes if fully approved
    def reject(self, actor_id: str, item_id: str, note: str) -> WorkItem
    def verify_payee(self, actor_id: str, item_id: str, method: str, note: str) -> WorkItem
    def recode(self, actor_id: str, je_id: str, line_index: int, new_account: str, note: str) -> WorkItem  # creates correction item

    # invoices and reconciliation
    def ingest_invoice(self, invoice: VendorInvoice) -> WorkItem        # sync actor; runs reconcile; R20..R23
    def ingest_meter(self, records: list[MeterRecord]) -> int

    # policies
    def policies(self, status: str | None = None) -> list[Policy]
    def learn(self, actor_id: str = "system:learner") -> list[Policy]    # proposes; never activates
    def approve_policy(self, actor_id: str, policy_id: str, note: str = "") -> Policy
    def reject_policy(self, actor_id: str, policy_id: str, note: str = "") -> Policy

    # periods
    def close_preview(self, actor_id: str, period: str) -> ClosePreview    # creates accrual items (PROPOSED), lists blockers
    def close_period(self, actor_id: str, period: str) -> Period           # refuses if blockers remain

    # reads
    def work_items(self, **filters) -> list[WorkItem]
    def work_item(self, item_id: str) -> WorkItemDetail                  # item + audit events + JEs + linked items
    def ledger(self, period: str) -> list[JournalEntry]
    def balances(self, period: str) -> list[AccountBalance]
    def audit(self, subject: str | None = None, actor: str | None = None, limit: int = 200) -> list[AuditEvent]
    def why(self, subject: str) -> WhyReport                              # readable trail for auditors
    def verify_chain(self) -> ChainCheck
```

Every public method takes the acting actor id and enforces permissions through `transition()` or an explicit `can()` check. Reads check `read` permission (agents only see their own items; everyone else sees all).

## Policy evaluation (R01 to R12)

`evaluate_purchase(item, ctx) -> Decision` runs every rule and collects `Check`s. It never stops at the first failure, because reviewers need the full picture. Then it computes the outcome with the precedence in 01.

Auto approval requires all of:
1. No check failed.
2. Either a matching active `auto_approve` policy covers the item (agent, vendor, optional sku, amount <= max), or the item is under the agent's default auto-approve threshold from the world config (`auto_approve_under_cents`), which is intentionally low.
3. R11 did not detect instructions in memo (if it did, auto approval is downgraded to needs_approval with required role budget_owner and the summary says why).

When auto-approved, the policy engine transitions with actor `system:policy` and action `auto_approve`, then the gateway pays and posts. When a human approves, the approver is recorded and the gateway pays and posts.

Account selection for a purchase: the first active `coding` policy matching (agent, vendor, sku), else vendor `default_account`. Cost center comes from the agent.

## Gateway

`gateway.execute(item)`:
1. Assert state APPROVED and that the payee check passed or was verified by the controller.
2. Build `PaymentIntent`. Call `rail.pay(intent, challenge)`. The rail returns a `Receipt` (and resource bytes for x402). If the rail raises, transition to FAILED with the error text.
3. Transition APPROVED -> PAID with patch `receipt`.
4. Post the journal entry for the posting pattern. Transition PAID -> POSTED with patch `journal_entry_ids`.
All three writes are audited. The gateway is the only module that imports `ledger.post_entry` and calls `rail.pay`; a test asserts this by grepping imports.

Idempotency: the gateway refuses to pay an item that already has a receipt.

## Reconciliation (invoices)

Inputs for a vendor and period:
- `VendorInvoice { vendor_id, invoice_number, period, issued_at, due_at, lines: [ {line_id, description, sku, quantity, unit_price_cents, amount_cents, usage_window: {start, end} | null } ], total_cents }`
- Usage meter records `MeterRecord { vendor_id, sku, agent_id, quantity, window_start, window_end, source: "agent_meter" }`
- Receipts from paid x402 purchases for the same vendor and sku whose usage window overlaps the invoice line window.

Algorithm (`reconcile.match(invoice)`):
1. R23 duplicate invoice number check.
2. For each line: look up contract rate (R21). Sum metered quantity in the line's window (R20). Find x402 receipts in the same window for the same sku and sum their quantities (R22). Expected billable quantity = metered quantity minus quantity already paid on x402. If invoice quantity exceeds expected by more than 2 percent, propose a short pay equal to the excess times contract rate, with a dispute note listing the receipt ids or meter totals as evidence.
3. Produce a `ReconciliationProposal { lines: [ { line_id, invoiced_qty, metered_qty, paid_elsewhere_qty, expected_qty, rate_ok, variance_cents, finding: str, evidence } ], pay_cents, dispute_cents, summary }` attached to the invoice item.
4. Outcome: no variance -> auto approve if under the accountant's auto threshold for invoices (`invoice_auto_approve_under_cents` in world config) else needs_approval (accountant). Any variance -> needs_approval (accountant) with the proposal. Duplicate -> deny.

The cross rail double bill (R22) is the headline moment. It is only detectable because Elix sees both the x402 receipts and the vendor's invoice.

## Invoice PDFs and credit notes

`Elix.intake_invoice(actor, extraction)` takes an `ExtractedInvoice` that Claude or the offline parser in `elix/intake/` proposed from a PDF and runs R40 to R46 in `engine/intake.py` (fields read, vendor master, number format and not seen before, every line mapped to a contract sku, quantity times rate equals the amount, lines add up to the total, dates make sense). All pass, and the VendorInvoice goes to `ingest_invoice` and reconciles like any other. Any fail, and an invoice item waits in NEEDS_INFO for the accountant with the checks as its decision. Nothing is reconciled or posted, and only Reject is offered.

`Elix.record_credit_note(credit, actor)` applies a vendor credit note to an approved invoice's open dispute (R24, R25, in `engine/credits.py`) and the gateway posts Dr 2010 / Cr 2150 (`gateway.post_credit_note`).

## Accruals

`close_preview(period)`:
1. For each vendor with `billing: "monthly_invoice"` and no invoice yet for the period, compute metered quantity for the period × contract rate.
2. Create an `accrual` work item (PROPOSED, requires controller) per vendor with `assumptions` in `evidence`: meter source, quantity, rate, period, and a sentence such as "Accrued 41.2M input tokens and 6.1M output tokens at contract rates because the October invoice has not arrived."
3. Blockers for close: any purchase or invoice item in NEEDS_APPROVAL, NEEDS_INFO, or APPROVED but unpaid; any accrual not yet posted.
4. When an accrual posts on the last day of the period, also create (and post on the first day of the next period, when that period's clock arrives or immediately with the future date if simpler) a reversal entry linked with `reverses`. Decision allowed: post the reversal immediately dated the first of next period, since the next period is open.

## Learning

`learn_from_history()` (onboarding, the "learns the job" requirement):
1. Group posted purchases in closed periods by (agent, vendor, sku-or-any).
2. For groups with at least 25 precedents that were approved by a human, zero rejections, zero flags other than R07 below threshold, propose `auto_approve` with `max_amount_cents` = the 95th percentile approved amount rounded up to the next whole 5 dollars, capped at the agent's per-transaction cap.
3. Evidence: precedent count, sample ids (up to 5), window, max seen, rejections (0).
4. Never propose for a vendor with a payee change in the window, or for groups containing any rejection.
5. Each proposal becomes a `Policy` (status proposed) and a `policy` work item for the controller.
6. Idempotent: running learn twice does not duplicate proposals with identical scope and params.

`learn_from_corrections()` (the "every correction becomes precedent" requirement):
1. Corrections are stored as precedents when an accountant recodes a posted line (`recode`).
2. When there are at least 3 recodes with the same (agent, vendor, from_account, to_account) and no conflicting recodes, propose a `coding` policy routing that (agent, vendor) to `to_account`.

The learner may use Claude only to write the `rationale` sentence when `ELIX_LLM=on` and a key exists. Thresholds, params and evidence are always computed in code. Default is template text.

## Corrections and closed periods

`recode(actor, je_id, line_index, new_account, note)`:
- If the JE's period is open: create a correction item (PROPOSED). It needs approval by a controller or accountant who did not propose it. In the seed world Sam proposes recodes and Priya approves them, or the reverse. On approval the gateway posts a reversal of the original entry and a repost with the corrected account, both dated today.
- If the JE's period is closed: the gateway refuses to edit (R30, audited as denied). Elix creates a correction item that will post a reversal of the original line and a repost with the corrected account, both dated today in the open period, linked to the original with `reverses` and `corrects`. Its summary says the original period is closed so the fix lands in October.
- Every recode is also stored as a precedent for `learn_from_corrections`.

## Explain (deterministic text)

`explain.py` turns checks and audit events into sentences. Examples:
- "Held for payee verification. The 402 challenge asks to pay 0x9f3c…a21e but Stormvault's master record says 0x4b1d…77c0, and that record changed 2 hours ago."
- "Denied as a duplicate of WI-1188. Forge asked for the same 600 H100 hours at the same price 41 seconds after the first request was paid."
- "Auto approved under pol-001 v1. Atlas buys Terrafield scenes under $40 and 214 earlier purchases like this were approved by Dev Okafor with none rejected."
Write sentences in plain English that follow the copy rules (no em dashes).

## Events (SSE)

`events.publish(kind, payload)` on every audit append, work item change, and JE post. The server streams them. Kinds: `audit`, `work_item`, `journal`, `policy`, `period`, `scenario`.

## CLI (`elix/cli.py`, typer, entry point `python -m elix`)

```
python -m elix seed [--db PATH] [--seed 7]        # build the Kestrel world with history Jul 1 to Oct 23
python -m elix serve [--port 8400] [--vendors]    # API + console; --vendors also starts local vendor servers
python -m elix vendors [--port 8402]              # local x402 vendor servers and facilitator
python -m elix scenario list | run <id>           # demo moments from 03
python -m elix eval [--seed 11] [--out eval/report.json]
python -m elix why <subject>                      # prints the trail
python -m elix verify-chain
python -m elix agents run <atlas|forge|scout> --task "..." [--scripted]
python -m elix demo                               # seed + serve + vendors in one process, prints URLs
```

`python -m elix demo` is the one command the founder runs at judging. It must work offline with no keys.
