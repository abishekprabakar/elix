# Backend core notes (engine, world, eval, scenarios, server, CLI)

## What is here

| Path | What it does |
|---|---|
| `elix/engine/` | The engine. `facade.Elix` is the only interface the server, CLI, scenarios, eval and agents use. |
| `engine/store.py` | SQLite schema and transactions. Triggers make the audit log append only and journal lines immutable; a journal entry may change once, when `reversed_by` is set. |
| `engine/permissions.py` | `can(config, actor, action, item)` is the single permission gate, with the four segregation rules. |
| `engine/workflow.py` | `TRANSITIONS` and `transition()`. Permission first, then the state table, then item and audit event in one SQLite transaction. Refused attempts are audited and survive a rollback (`Store.durable`). |
| `engine/audit.py` | Hash chain (`sha256` over canonical JSON of the event and the previous hash), `verify_chain`, event queries. |
| `engine/ledger.py` | Double entry posting, reversals, balances, trial balance, R30 refusal. Only `gateway.py` imports `post_entry`. |
| `engine/policy.py` | R01 to R12. Every rule runs, then the outcome follows deny > needs_approval > needs_info > auto_approve. |
| `engine/gateway.py` | The only code that calls `Rail.pay` and posts. Purchases, invoices (with the 2150 dispute pattern), accruals with scheduled reversals, corrections, opening balances. |
| `engine/reconcile.py` | R20 to R23. Each invoice line is matched against the contract rate, the usage meter for its window, and x402 receipts Elix paid for the same usage (prorated by window overlap). |
| `engine/credits.py`, `disputes.py` | Vendor credit notes against an approved invoice's open dispute (R24, R25, every refusal audited). The gateway books Dr 2010 / Cr 2150 with `post_credit_note`. `python -m elix scenario run vendor-credit` after the double bill is approved. |
| `engine/intake.py`, `elix/intake/` | Invoice PDFs. `elix/intake` proposes the fields (Claude with a strict tool when `ANTHROPIC_API_KEY` is set, else the offline pdfplumber parser, labeled either way). `engine/intake.py` decides with R40 to R46. Samples in `docs/samples/` from `scripts/make_invoice_samples.py`. Try `python -m elix intake docs/samples/aws-AWS-2026-10-arithmetic-error.pdf --as sam`. |
| `engine/accruals.py` | Accrues only time no invoice line covers yet, net of x402 receipts, at contract rates. |
| `engine/learner.py` | Learns auto approve policies from closed periods (25 or more human approvals, no rejections, no payee change, p95 rounded up to $5, capped at the agent cap) and coding policies from 3 or more agreeing recodes. Never activates anything. |
| `engine/corrections.py` | Recodes. Closed period edits are refused (R30, audited) and land as a reversal and repost in the open period. |
| `engine/periods.py` | Close preview, close, blockers, and roll forward to the next open period if the current one was closed. |
| `engine/explain.py`, `fmt.py` | Every sentence a person reads. Templates only, no model. |
| `engine/events.py` | In process pub and sub for SSE. |
| `elix/world/config.py` | Kestrel Labs: actors, agents, vendors (payout addresses from `elix.rails.keys.vendor_address`), chart of accounts, thresholds. |
| `elix/world/seed.py` | Plans July 1 to October 23 and replays it through the facade with the clock moved to each event. |
| `elix/eval/` | `generate.py` (never imports the engine) writes 90 days of events and an answer key; `run.py` scores the engine; `report.py` writes `eval/report.md`. |
| `elix/scenarios/` | The eleven demo moments. They stop where a person decides and return who acts next. |
| `elix/server/` | FastAPI app: every endpoint in spec 04, the two addendum endpoints, SSE at `/api/events`, token auth, the error body, console hosting with SPA fallback, demo reset. |
| `elix/cli.py` | typer CLI. Loads `.env` from the repo root on start (existing variables win). |
| `scripts/smoke.py`, `scripts/smoke.sh` | Every endpoint with the right tokens, including the 403 when an agent approves its own item. |

## How to run (all from the repo root, `/home/claude/elix`)

```
pip install -e .                       # entry point: elix = elix.cli:app
python -m elix demo                    # seed, vendor servers on 8402, API and console on 8400, offline, no keys
python -m elix seed                    # rebuild the world through the offline x402 rail (about 25 to 30 seconds)
python -m elix verify-chain
python -m elix scenario list
python -m elix scenario run cross-rail-double-bill
python -m elix why LUM-2026-10A
python -m elix approve WI-1374 --as human:sam   # play a human step without the console (reject works the same)
python -m elix eval                    # writes eval/report.json, eval/report.md, eval/answer_key.json, eval/events.jsonl
python -m elix eval --fast             # fake rails, about 5 seconds, for iteration only
python -m elix agents run forge --scripted     # against a running API
pytest -q                              # whole repo, about 2 to 3 minutes
bash scripts/smoke.sh                  # starts a demo on free ports with a throwaway data folder and runs the smoke test
```

`demo --keep` keeps the current world. The vendor servers keep payouts in memory, so `demo --keep`, `serve --vendors` and CLI scenario runs re-apply payout addresses from the vendor master before serving.

`seed`, `demo` and the eval always use offline rails, even with `ELIX_RAIL_MODE=live`, because history amounts are far above faucet balances. `serve` and `demo` use `ELIX_RAIL_MODE` for the live API.

## How the decision path works

1. An agent proposes (`POST /api/purchases`). Elix creates the item (NEW), asks the x402 rail for the 402 challenge, and runs R01 to R12 as `system:policy`.
2. The outcome is one transition: auto_approve, escalate, ask or deny. Auto approval needs every check to pass and either an active learned policy or the agent's low default threshold (R12, an info check), and R11 (instructions in vendor text) always downgrades it. When R11 or R12 fail, the spend needs at least the budget owner, even on a payee hold.
3. Spend approval and payee verification are separate. A budget owner can approve spend; only the controller releases a payee hold.
4. When an item is fully approved the gateway pays on its rail, then posts the balanced entry. Pay and post are separate audited transitions; a rail refusal moves the item to FAILED and nothing posts.
5. Every attempt, allowed or refused, is one audit event in the same transaction as the change.

## What the numbers mean

The Proof screen and the site read `eval/report.json`. Nothing is typed by hand. `eval/report.md` explains each measure and the per category table. An anomaly is caught when Elix did not pay it without a person and flagged an expected rule; some categories also check the state, the proposed short pay (within $1) or the refused approval in the audit log.

## Known gaps

- Credit notes are not ingested yet. A disputed amount stays in 2150 Vendor disputes; the posting pattern for the credit note (Dr 2010, Cr 2150) is described in spec 01 but has no endpoint.
- The optional `ELIX_LLM=on` rationale wording is not implemented. Policy rationales are always template text, which keeps the engine free of network calls.
- Live Base Sepolia mode belongs to the rails builder and cannot be tested in this sandbox. The engine passes everything through the `Rail` protocol unchanged in both modes.
- The server serializes engine calls with one lock per Elix instance. That is fine for a demo; production would use a database with row level concurrency.
- The hash chain detects tampering but cannot prevent someone with file access from rewriting the whole chain. Production would anchor the head hash somewhere outside the database.
- Vendor master changes come only from sync (scenario, eval or the facade). There is no screen to edit vendors.
- Agent budgets count the agent's own purchase requests, not invoices.
- The eval world bills Lumen weekly and Anthropic and AWS twice a month so that four of each invoice anomaly can be planted (22 invoices instead of 6). See `specs/DECISIONS.md`.
