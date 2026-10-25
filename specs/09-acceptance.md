# 09 · Acceptance criteria and submission materials

A component is done only when every check in its section passes and the builder has run them. The final verifier re-runs all of them from a clean checkout of the folder.

## Backend: engine, world, eval, server

- [x] `pip install -e .` works from the repo root (`pyproject.toml` with package `elix`, entry point `elix = elix.cli:app`).
- [x] `pytest -q` passes. Tests cover at least: every transition in the state machine table, `can()` for every role and the four segregation rules, every rule R01 to R12 and R20 to R23 with a passing and failing case, R30 closed period refusal plus correction flow, ledger invariants (balanced entries, no updates, reversal links), audit chain verification including detection of a tampered row, idempotent replay, learner thresholds and the scene-3m exclusion, correction based learning, accrual and reversal, the gateway being the only module that imports `ledger.post_entry` or calls `pay`.
- [x] `python -m elix seed` builds the world in under 60 seconds and `python -m elix verify-chain` reports ok.
- [x] After seeding: July to September are closed, October is open, the trial balance balances for every period, the October inbox has the open items described in 03, the Oct 9 Forge spot purchase exists with an x402 receipt.
- [x] Every scenario in 03 runs from a fresh seed via `python -m elix scenario run <id>` and produces the described outcome. Write a test per scenario.
- [x] `python -m elix eval` writes `eval/report.json` and `eval/report.md`. `anomalies_auto_paid` is 0. Every category is caught. Automation rate after learning is higher than before learning. If not, fix the engine and record the change.
- [x] `python -m elix demo` starts the API on 8400 with the console at `/` and vendor servers on 8402, offline, with no keys, and prints the URLs.
- [x] API smoke test script `scripts/smoke.sh` (or a pytest module) exercises every endpoint in 04 with the right tokens and checks status codes, including 403 for an agent approving its own item.

Backend core builder ran these on 2026-10-24 from the repo root: `pip install -e .` (ok, `elix` entry point), `pytest -q` (481 passed, whole repo), `python -m elix seed` (20.8 s through the offline x402 rail) and `python -m elix verify-chain` (ok), all ten scenarios with `python -m elix scenario run <id>` on a fresh seed (human steps with `python -m elix approve`), `python -m elix eval` (default mode, writes eval/report.json and eval/report.md, see the report for the numbers), `python -m elix demo` (API and console on 8400, vendors on 8402, offline, no keys, URLs printed, and `--keep` restores payouts), and `bash scripts/smoke.sh` (104 checks against a live demo on free ports, including the 403 when Scout approves its own item). The console's `scripts/flows.mjs` passed 8 of 8 against the live API.

## Rails, vendors, agents, MCP

- [x] Offline x402 round trip test passes (challenge, signed payload, verify, simulated settle, resource delivered, replay rejected).
- [x] Payee change between challenge and pay raises `RailError`.
- [x] Scripted agents for atlas, forge and scout complete their default tasks against a running server and create work items. Transcripts are stored and returned by the sessions API.
- [x] Claude agent code path constructs valid Anthropic tool use requests (unit test with a fake client that returns a scripted tool_use then end_turn). Live call is attempted only when `ANTHROPIC_API_KEY` is set.
- [x] `python -m elix.mcp_server` starts over stdio and lists exactly the seven agent tools.
- [x] `elix/rails/NOTES.md` states clearly what is real (SDK messages, signatures) and what is simulated (settlement) and the exact steps to run live on Base Sepolia.

Rails builder ran these on 2026-10-24 with `python -m pytest -q tests/rails tests/agents tests/mcp` (36 passed) and against a live `python -m elix demo` on free ports (scripted Atlas, Forge and Scout created WI-1396, WI-1397 and WI-1398; Dev approved WI-1397 and it paid over the offline x402 rail with tx sim-dc20148c5b4ce052).

## Console

- [ ] `npm ci && npm run build` in `console/` succeeds with no TypeScript errors.
- [ ] Served by `python -m elix demo` at `http://127.0.0.1:8400/`, every screen in 06 loads real data with no console errors.
- [ ] Acting as switch changes the inbox and the available actions. An agent cannot approve through the UI (there is no agent identity in the menu) and the API refusal message appears when a human tries an action they are not allowed to take.
- [ ] Running each scenario from the Scenarios screen produces visible results, and the presenter can complete the human steps (Sam approves the Lumen invoice, Priya verifies or rejects the Stormvault payee, Priya approves learned policies, Priya approves the correction).
- [ ] Screenshots of every screen at 1440x900 in light and dark are in `console/screenshots/` with stable names: `today`, `inbox`, `item_payee_hold`, `item_x402_receipt`, `reconcile`, `ledger`, `policies`, `agents`, `audit_why`, `proof`, `scenarios`, each with `_light` and `_dark` suffixes.
- [ ] Copy rules from 00 hold in every UI string (grep for "—" returns nothing in `console/src`).

## Site

- [ ] `python site/build.py` produces `site/dist/index.html` with no unresolved placeholders, under 8 MB.
- [ ] Renders correctly at 1440 and 400 wide in light and dark (screenshots in `site/screenshots/`).
- [ ] No external requests other than Google Fonts.
- [ ] Copy rules hold (no em dashes, no colon constructions in prose, no all caps labels, no arrows on links).

## Submission materials (`docs/` and root)

- [ ] `README.md`: what Elix is in two sentences, the one line on the seats, a screenshot, quick start (one command), how to run the eval, what is real versus simulated, architecture overview with a diagram (Mermaid in markdown is fine), repo map, and credits. Follows the copy rules.
- [ ] `docs/architecture.md` with a component diagram and the gateway, policy and audit flow, including controls and known limitations (Coinbase asks for "components, controls, and known limitations").
- [ ] `docs/demo-script.md`: a 2 minute live pitch script and the click path, opening with the cross rail double bill moment, then payee hold, then the audit question, then proof numbers. Also a 90 second version for the demo video, shot list included.
- [ ] `docs/devpost.md`: draft text for every Devpost field (name, tagline, inspiration, what it does, how we built it, challenges, accomplishments, what we learned, what's next, built with), plus which tracks to tag and the one line on seats for Hyphenate.
- [ ] `docs/coinbase.md`: project summary (who it's for, what it does, which agent platform and Coinbase tools it uses), working example steps, architecture link, and a builder feedback section with the biggest blocker, the workaround, and the one missing Coinbase capability most wanted. Write the feedback honestly from what was actually hard while building (for example the default $1 client spend cap, facilitator verification needing a chain, testing offline).
- [ ] `docs/microsoft.md`: how the project maps to customer understanding, measurable usefulness, path to production, responsible design, end to end functionality.
- [ ] `docs/mcp.md`: MCP setup for Claude Code and Claude Desktop.
- [ ] `docs/morning-checklist.md`: what the founder must do Saturday morning, in order, with exact commands and where to run them (create Coinbase account and CDP API key, fund a Base Sepolia wallet, set env vars, run live mode test, claim Anthropic credits and set the key, run a real Claude agent, record the video, create the Devpost draft, push to GitHub with the exact commands, booth conversations). Keep it short and ordered.
- [ ] `.env.example` with every variable and a comment.
- [ ] `LICENSE` (MIT) and `.gitignore`.
