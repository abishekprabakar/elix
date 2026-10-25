# 06 · Console (the product UI)

Directory `console/`. Vite + React 18 + TypeScript. No component library and no Tailwind. Plain CSS modules or one global stylesheet built from the tokens in 08. Lightweight dependencies only (react-router-dom is fine; a tiny chart is drawn by hand in SVG). Build output `console/dist`, served by the FastAPI server at `/`. Dev server proxies `/api` to `http://127.0.0.1:8400`.

This is what judges and investors will actually look at during the live demo and in screenshots. It must look like a real finance product: dense where it needs to be, calm, precise, every number aligned. Read 08 before writing any CSS.

## Data access

- `console/src/api/types.ts` mirrors `elix/contracts.py` and 04.
- `console/src/api/client.ts` wraps fetch with the current bearer token and parses the error body into a typed error.
- `console/src/api/events.ts` subscribes to `/api/events?token=` with EventSource and dispatches updates to a small store (React context + reducer is fine). Reconnect on error.
- Fixture mode: `VITE_FIXTURES=1` serves data from `console/src/fixtures/*.json` so the UI can be built before the backend is ready. Fixtures must match the real shapes exactly. The final build uses the real API.

## Global layout

```
+--------------------------------------------------------------------------------+
| Elix logo  Kestrel Labs   October 2026 v      [ search work items ]  Acting as v |
+------------+-------------------------------------------------------------------+
| Today      |                                                                   |
| Inbox  (3) |   page content                                                    |
| Activity   |                                                                   |
| Reconcile  |                                                                   |
| Ledger     |                                                                   |
| Policies   |                                                                   |
| Agents     |                                                                   |
| Audit      |                                                                   |
| Proof      |                                                                   |
| ---------- |                                                                   |
| Scenarios  |                                                                   |
+------------+-------------------------------------------------------------------+
| Kestrel Labs is a fictional company. All data is synthetic. Settlement: offline simulation | chain ok |
+--------------------------------------------------------------------------------+
```

- Acting as: a menu of Priya Raman (Controller), Dev Okafor (Head of ML), Maya Chen (Head of Growth), Sam Ortiz (Staff accountant), Jordan Lee (Auditor). Switching changes the token and refetches. Show the person's role under their name. The choice persists in localStorage (wrapped in try/catch).
- Inbox count is for the current person.
- Period selector lists periods with a lock glyph on closed ones.
- The footer shows the synthetic data notice, the rail mode (offline simulation or live Base Sepolia), and the chain verification state from `/api/audit/verify` (ok or broken, with checked count).
- Below 900px width the nav collapses to a top menu. The console is primarily a desktop app but must not break on a phone.
- Keyboard: `g i` inbox, `g t` today, `/` focuses search, `j/k` moves in tables, `Enter` opens, `Esc` closes drawers. Show a small shortcuts sheet on `?`.

## Screens

### Today (`/`)
Summary before detail.
- A one line status sentence built from data: "Elix handled 87 percent of this month's 312 purchases without a person. 4 items need someone today."
- Elixir meters for each agent (spent, committed, budget, cap). Clicking an agent opens Agents filtered.
- Needs you: the current person's inbox items as compact rows with state pill, title, amount, age, and the decision summary sentence. Empty state: "Nothing needs you right now." plus what Elix handled since yesterday.
- Spend this month: a daily bar chart (SVG, stacked x402 and bank) with axis labels from real data and a faint grid, plus totals by rail and by account.
- Live decisions: the latest 15 audit events as sentences, updating over SSE with the highlight from 08.

### Inbox (`/inbox`)
Items needing the current person (`inbox=me`), oldest first. Each row: state pill, title, agent or vendor, amount, flags as small chips, waiting time. Selecting a row opens the work item drawer (same content as the detail page) on the right so approval is fast. Batch actions are not needed.

### Work item detail (`/items/:id` and the drawer)
Order of content:
1. Header: title, amount, state pill, period, vendor, agent, cost center. Buttons from `next_actions`. A disallowed action shows as a disabled button with its reason visible beneath ("You proposed this item, so someone else has to approve it." or "Payee changes need the controller."). Approve and reject open a small inline note field. Verify payee asks for the method (select: "Called vendor at number on contract", "Confirmed in signed vendor portal", "Other") and a note.
2. Explanation sentence (from the API).
3. Decision checks list (component 4 in 08), failed first.
4. For purchases on x402: the receipt block (component 2), including the decoded 402 challenge and, when paid, payload hash, signature verified, settlement ref, and the simulated badge. A "View raw" toggle shows the decoded JSON in Plex Mono.
5. For invoices: the reconciliation table (see Reconcile).
6. Journal entries (component 3).
7. Questions thread (agent questions and answers).
8. Timeline: audit events for this item, each with its diff toggle.
9. Linked items (duplicate_of, reverses, corrects, precedent, policy).

### Activity (`/activity`)
All work items in the period as a dense table. Columns: id, state, title, agent, vendor, amount, account, flags, decided by (Elix policy or a person), updated. Filters: state, kind, agent, vendor, flag. Text search. Click opens the drawer.

### Reconcile (`/reconcile`)
Invoices for the period. Each invoice shows a three way match table: line, invoiced quantity and rate, metered quantity, paid elsewhere (with links to the x402 receipts), expected quantity, variance, finding. Variance cells use `--hold` or `--deny`. The proposal summary sits above the table: "Pay $12,234.00. Dispute $1,260.00 already paid over x402 on Oct 9 (WI-0xxx, tx sim-…)." The dispute note draft is shown in a copyable block. Approve button for the accountant.

### Ledger (`/ledger`)
Journal entries for the period, newest first, each as a compact JE block with account codes and names, debit and credit columns, memo, source item link, and reversal links. A balances panel at the top shows the trial balance with a balanced indicator. Closed periods show a lock and the closer's name and date. Recode action on a line opens a small form (new account, note) and explains when the period is closed that the fix will post in October as a reversal and repost.

### Policies (`/policies`)
Two groups: proposed and active. Each policy shows a plain sentence ("Auto approve Atlas buying Terrafield 10 m scenes up to $40"), the evidence ("214 approvals by Dev Okafor from Jul 1 to Sep 30, none rejected, largest $36"), sample precedent links, who proposed it, who approved it, version. Approve and reject for the controller. A "Learn from history" button runs `/api/policies/learn` and shows what changed. Rejected and retired policies are collapsed below.

### Agents (`/agents`)
Each agent: name, owner, cost center, mode (Claude or scripted), elixir meter, cap, recent requests. "Run agent" with the default task prefilled (editable) starts a run; the session transcript streams in: assistant text, tool calls (name and args in Plex Mono), tool results summarized, with links to created work items. Label scripted sessions clearly as "Scripted agent (offline)".

### Audit (`/audit`)
- A "Why" box: input a work item, JE, invoice number or policy id, and get the WhyReport answer paragraph plus the ordered steps. Preset buttons for the demo: "Why was LUM-2026-10A paid short?" and "Why is the Stormvault archive on hold?".
- The event log with filters (actor, action, allowed) and a "Denied attempts only" toggle. Hash chain status with "Verify chain" button that calls `/api/audit/verify` and shows the result.
- Readable for an auditor (Jordan): no internal jargon in sentences.

### Proof (`/proof`)
Reads `/api/eval/latest`. Shows:
- The headline numbers: anomalies planted, anomalies auto paid (target 0), automation precision, automation rate before learning and after learning, chain ok.
- A per category table: planted, caught, not auto paid, auto paid, notes.
- "What is real and what is simulated" table: real (policy engine, ledger, audit chain, x402 protocol messages and signatures via the official SDK, Claude agents when a key is set), simulated (company and data, USDC settlement in offline mode, bank ACH), how we know (answer key generated separately, seeds, re-run button).
- Run again button (POST `/api/eval/run`) with progress over SSE.
Never hardcode numbers; show an empty state with the command to run if the report is missing.

### Scenarios (`/scenarios`)
The presenter's control panel for the live demo. A list of the scenarios from 03 with title, description, prerequisites, and "Run scenario". After running, show the message and links to the affected items, and offer "Open in inbox as …" which switches the acting person to whoever needs to act next. The custom scenario has a form (agent, vendor, sku, quantity, unit price override, pay to override, memo, purpose). A "Reset demo data" button with an in-page confirmation step (no `confirm()` dialogs).

## Empty, loading, and error states

- Loading uses skeleton rows matching the table layout, not spinners in the middle of the page.
- Errors show what failed and what to do ("The API at 127.0.0.1:8400 is not responding. Start it with python -m elix demo.").
- 403 responses show the server's message inline next to the action and do not crash the page.

## Quality bar

- Tabular numbers everywhere amounts appear. Amounts right aligned. Currency formatting `$12,234.00`.
- No layout shift when SSE updates arrive.
- Works in light and dark (theme follows the OS, plus a toggle in the acting-as menu).
- Take screenshots of every screen with headless Chromium (`/home/claude/tools/shot`) at 1440x900 in light and dark, review them yourself, fix what looks off, and save the final set to `console/screenshots/`. These are used on the marketing site.
