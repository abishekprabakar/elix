# Handoffs between components

Write here when you need something from another component. Format: `[from -> to] request`. The lead resolves these.
[lead -> backend core] CLI must load a .env file from the repo root on startup if present (python-dotenv is fine, or a tiny parser). The morning checklist tells the founder to put ANTHROPIC_API_KEY, ELIX_RAIL_MODE and ELIX_WALLET_PRIVATE_KEY in .env. Lead will add this at integration if not done.

[rails -> backend core] 2026-10-24 ready to import now: `elix/rails/keys.py` (`vendor_address(vendor_id, rotation=0)`, `treasury_address()`), `elix/rails/__init__.py` (`RailRegistry`, `make_registry()`, `make_fake_registry()`). Extras you may use, all optional: `registry.set_clock(clock)` makes rails stamp `received_at`/`settled_at` with world time (accepts a callable or an object with `now()`/`iso()`/`now_iso()` returning a datetime or ISO string); `registry.settlement_label` ("Offline simulation" or "Live Base Sepolia") for the console footer; `registry.rail_for(vendor)`. The fake x402 rail prices challenges exactly like the vendor app (contract price x qty from VENDORS) and honors `pay_to_override` on the intent plus `pay_to_override` and `price_override_cents` query params on `resource_url`. Fake receipts have `tx_ref` "fake-..." so they never look like signed offline payments ("sim-...").

[rails -> backend core] 2026-10-24 for the server and CLI:
- `GET /api/work-items/{id}/x402` can return `elix.rails.artifacts.x402_view(id)` and `GET /api/work-items/{id}/resource` can return `elix.rails.artifacts.resource_view(id)` (None means 404). Only agents should be limited to their own items there.
- `seed` and `demo` should call `elix.rails.reset_offline_state(vendor_base_url)` (clears rails.db, re-funds sim_chain.db, resets vendor payouts and nonces).
- Seed and eval must use offline rails even when ELIX_RAIL_MODE=live (`make_registry("offline", base)`), because history amounts are far above faucet balances. `demo` should call `elix.vendors.ensure_running(8402)` before building the registry.
- Agents: `elix.agents.runner.start_run(agent_id, task, api_base, token)` expects the agent's own token (tok_atlas, tok_forge, tok_scout), never the human token that called `POST /api/agents/{agent}/run`. `runner.AGENT_TOKENS` maps agent id to token and `runner.mode_for()` gives "claude" or "scripted" for `GET /api/agents`. Sessions: `elix.agents.sessions.list_sessions(agent)` and `get_session(id)`. Optional: `sessions.set_clock(clock)` so transcripts use world time, and `sessions.set_listener(fn(session_id, role))` to push SSE updates.
- Agent tools call these endpoints with the agent token, so agents need read access to them: `GET /api/session`, `GET /api/world` (vendors), `GET /api/agents` (falls back to `GET /api/summary` on 403), `POST /api/purchases`, `GET /api/work-items/{id}`, `POST /api/work-items/{id}/answer`, `GET /api/work-items/{id}/resource`.
- Engine: when building the PaymentIntent for x402, please pass `resource_url` (or sku and quantity) and `pay_to_override`; the rail builds the URL with `elix.vendors.resource_url` when resource_url is empty.

[rails -> backend core] 2026-10-24 eval and rail errors:
- The eval can share the demo's vendor servers. Build its registry with `make_registry("offline", base, namespace="eval")` so its WI ids do not overwrite the demo's x402 artifacts in rails.db. For planted anomalies use per request overrides rather than global vendor state: `PaymentIntent.pay_to_override` (payee change) and `resource_url=elix.vendors.resource_url(vendor, sku, qty, price_override_cents=N)` (amount mismatch, price above contract). `vendors.set_payout` changes the payout for everyone and is meant for the payee-change scenario only (call `vendors.reset()` afterwards).
- `X402Rail.challenge()` raises `RailError` when the vendor is unreachable, unknown (404) or the order is invalid (400). Please catch it at proposal time and record it on the item rather than failing the request.
- Optional speedup: adding `coincurve` to the dependencies makes eth_keys signature recovery about 50x faster (a purchase through the real offline rail is about 50 ms today, mostly pure Python secp256k1).

[console -> backend core] 2026-10-24 notes from driving the engine facade directly with the fake registry (server not up yet). None of these block the console, which already adapts.
- `World.company.today` is a bare date ("2026-10-24"). The console needs the world clock to show waiting times, so it uses the latest audit event time as "now". If convenient, add `now` (full ISO timestamp of the world clock) to `/api/world` company or to `/api/session`; the console will prefer it when present.
- After seeding, Atlas has $1,740.50 spent and $54 committed in October. The payee-change scenario's $4,800 archive then totals $6,594.50 against the $6,000 budget, so R08 also fails (needs_approval, under 120 percent). Spec 03 says only R07 and R03 fail. Either lower Atlas's October volume or accept the third flag; the console shows whatever checks come back.
- `summary.spend_by_account` shows 5120 Model inference at 5 cents for October (accrual reversal against a September invoice). Harmless, but it looks odd on the Today screen.
- `GET /api/agents` should include `mode` ("claude" or "scripted") per spec 04; `agents_status()` does not return it, so the server has to add it from `runner.mode_for()`.
- Console reads these item.data keys exactly as the engine writes them: `spend_role`, `spend_approved`, `payee_hold`, `payee_verified`, `invoice`, `invoice_number`, `pay_cents`, `dispute_cents`, `reconciliation`, `policy_id`. Reconciliation receipts come from `lines[].evidence.receipts[].item_id/tx_ref`. Please keep these names.
- For the SSE stream the console listens for the kinds in spec 04 (`audit`, `work_item`, `journal`, `policy`, `period`, `scenario`, `eval`, `ping`) and uses `audit` events to update Today's live list, so each audit event needs the full AuditEvent shape including `seq`.

[rails -> backend core, FYI, fixed on my side] 2026-10-24 `pytest -q` from the root failed to collect test_audit_ledger.py and test_policy_rules.py because `from conftest import buy` resolved to tests/rails/conftest.py (pytest registers every non-package conftest as module "conftest"). I added `tests/rails/__init__.py` and `tests/agents/__init__.py` so my conftests import as `rails.conftest` and `agents.conftest`; yours stays "conftest". tests/mcp has no conftest and no __init__ (a package named mcp would shadow the mcp library). Full suite: 404 passed.

[rails -> lead] 2026-10-24 pyproject pins: my code is written and tested against x402 2.25, mcp 2.3 and anthropic 2.3 (the installed versions). Please consider `x402[evm,fastapi,httpx]>=2.25` and `mcp>=2.0`. The MCP server falls back to mcp 1.x FastMCP if MCPServer is missing, but that path is untested. `coincurve` in the `fast` extra is a good call (signature recovery is the main cost of a purchase).

[console -> backend core] 2026-10-24 `python -m elix demo --keep` restarts the vendor servers with every payout back at rotation 0, while the kept world still has Stormvault's master record on the new address. Verifying the payee on the kept WI-1397 then made the gateway refuse with "challenge changed since approval" and the item went FAILED. That refusal is correct behavior, but a presenter who restarts the demo mid-pitch would hit it. Suggest `serve`/`demo --keep` re-applies each vendor's current master payout to the vendor app on startup (vendors.set_payout from the vendor master). A fresh `demo` run is unaffected.

[backend core -> console] 2026-10-24 the API is live and `node scripts/flows.mjs` passes 8 of 8 against it; `scripts/shots.mjs` loads every screen with no console errors. Extra fields you can use (all optional, nothing removed):
- `GET /api/world` adds `now` (world clock, full ISO) and `rail: {mode, kind, settlement_label}` for the footer. `GET /api/session` adds `now`.
- `GET /api/scenarios` rows add `not_ready_reason` and `next_actor`. `POST /api/scenarios/{id}/run` returns `next_actor` (actor id of who decides next) for "Open in inbox as".
- `GET /api/summary` adds `purchases_total` (purchases in the period) for the status sentence.
- SSE also sends kind `agent` ({session_id, role}) whenever an agent session transcript changes, and `scenario` {id: "reset", status: "done"} after a demo reset.
- `GET /api/eval/latest` omits the long `anomaly_details` list; the full list stays in eval/report.json.
- 409 bodies carry `error.details` (close blockers on `/close`).
- The 5 cent 5120 line on Today is real: the September Anthropic accrual reversed on Oct 1 and the actual invoice posted on Oct 8 for 5 cents more. Accrual true ups look like this in real books.

[backend core -> lead] 2026-10-24
- Done: `.env` loading in `elix/cli.py` (repo root or current folder, existing variables win, empty values ignored).
- pyproject pins now follow the rails builder's request (x402 >= 2.25, mcp >= 2.0, anthropic >= 2.0); `coincurve` is in the `fast` extra, not required.
- The payee change moment trips R08 as well as R03 and R07 (Atlas already spent about $1,740 in October). Left as is and logged in DECISIONS; the narrative still holds.
- eval/report.json, report.md, answer_key.json and events.jsonl in `eval/` come from a default (non fast) run through the offline x402 rail. Rerun with `python -m elix eval` after any engine change.

[console -> backend core] 2026-10-24 status after running the console against the real server (`python -m elix demo`, every screen, both themes, all human steps through the UI). Resolved on the backend side, thanks: `world.now`, `world.rail`, `mode` on `/api/agents`, `payload_decoded` on the x402 panel. Still open, none blocking:
- R08 also fails on the Stormvault archive (Atlas reaches $6,594.50 of $6,000), so the payee-change item shows four failed checks (R03, R07, R08, R12) instead of the two spec 03 describes.
- `summary.spend_by_account` still lists 5120 Model inference at $0.05 for October. The console now shows it as $0.05 instead of rounding to $0.
- `demo --keep` vendor payout reset, described above.
[site -> lead] 2026-10-24 After the last console or engine change, run from the repo root: `python site/capture.py && python site/build.py`. capture.py needs console/dist built and takes about a minute; build.py writes site/dist/index.html. Set ELIX_REPO_URL first if the repo is not https://github.com/abishekprabakar/elix. When publishing, declare `capabilities: {db: {}}` if the pilot form should save entries (collection pilot_requests); without it the form says nothing was sent.

[credit notes and intake -> console, site] 2026-10-24 Console fixture mode (src/fixtures, scripts/make-fixtures.mjs) has no credit notes, PDF intake or vendor-credit scenario; the upload control says fixture mode has no PDF intake. site/capture.py and console/screenshots were not re-taken for these features. Rerun `python site/capture.py && python site/build.py` if the site should show them.
