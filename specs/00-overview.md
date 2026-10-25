# Elix — product overview and build rules

This folder is the source of truth for the Elix hackathon MVP. Every builder (human or agent) works from these specs. If code and spec disagree, the spec wins. If the spec is silent, choose the simplest behavior that keeps the demo honest, and write the decision into `specs/DECISIONS.md` (append a dated line).

## One line

Elix is the controller for agent spend. It approves, pays, books, and explains every dollar a company's AI agents spend, across x402 stablecoin payments, invoices, and bank rails.

## The seats it fills (Hyphenate asks for this in one line)

Elix fills the accounts payable clerk and spend approver seats for a company whose purchasing is done by AI agents.

## Why it exists

Agents can now buy things on their own: data, compute, API calls, tools. Coinbase's x402 protocol makes that literal, since an HTTP endpoint can answer `402 Payment Required` and an agent can pay in USDC. Finance was built for humans holding corporate cards. Each payment rail only sees its own transactions. Somebody still has to approve the spend, book it to the right account, reconcile it against vendor invoices, accrue it at month end, and explain it to an auditor. Elix is that system of work.

## Core principle (Elix's thesis)

LLMs may propose. Deterministic code authorizes. An agent can propose a purchase and Claude can explain a decision, but only policy code decides, only humans with the right role approve exceptions, and only the gateway can move money or write to the ledger. Every attempt, allowed or denied, is written to an append-only, hash-chained audit log in the same database transaction as the change.

## Who sees this

1. Hackathon judges at Cal Hacks 13.0 (Oct 23 to 25, 2026). Submission closes Sunday Oct 25, 9:00 AM PT. Judging 10:30 AM to 12:30 PM with a live demo and roughly a two minute pitch.
   - Primary track: Hyphenate, "The Hyphenated Enterprise: Autonomous Systems for Finance". Judged on four questions. Is it real enough. Would you hire it (which seats, limited access, clean record, knowing when to ask). Did we see something new. The moment. They want a demo video that opens with the moment, a live demo where they may try their own scenario, one line on the seats, and proof it works (what is real, what is simulated, how we know, e.g. planted errors with an answer key).
   - Coinbase x402 challenge, Payment Agents track. Judged on usefulness, technical design, ease of reuse (someone else can install it and complete the core task from our instructions), and builder feedback. Real money not required. Simulated actions must be labeled clearly. Credentials stay private.
   - Microsoft "Built for the Real World". Production-minded agent in finance. Judged on customer understanding, measurable usefulness, path from prototype to production, responsible design, end-to-end functionality.
2. Investors (a16z speedrun) who see the marketing site and a short demo.
3. Later, Freshworks engineering (the engine must be reusable behind a different front end).

## What we are building (deliverables)

| Deliverable | Path | Spec |
|---|---|---|
| Engine (ledger, roles, state machine, audit log, policies, gateway, reconciliation, accruals, learning) | `elix/engine/` | 01, 02 |
| Synthetic company world and history seed | `elix/world/` | 03 |
| Eval harness with planted errors and answer key | `elix/eval/` | 03 |
| HTTP API + SSE + static console hosting | `elix/server/` | 04 |
| x402 rail, local vendor servers, local facilitator, bank rail | `elix/rails/`, `elix/vendors/` | 05 |
| Claude agents and scripted fallback agents | `elix/agents/` | 05 |
| MCP server so any agent can route spend through Elix | `elix/mcp_server.py` | 05 |
| CLI | `elix/cli.py` | 02 |
| Console web app | `console/` | 06, 08 |
| Marketing site | `site/` | 07, 08 |
| Docs and submission materials | `README.md`, `docs/` | 09 |

Cross component Python interfaces and directory ownership are pinned in `10-interfaces.md`. Shared Python contracts live in `elix/contracts.py`. Do not change a contract's field names or semantics without updating the spec and every caller.

## Fictional company

Kestrel Labs, a 38 person AI company that sells hail and flood forecasts to crop insurers in the Great Plains. Its three agents buy satellite imagery, radar archives, GPU hours, enrichment data and search API calls on their own. All company data is synthetic and must be labeled as such in the console footer and README. Details in `03-world.md`.

## Honesty rules (non-negotiable)

- Never hardcode a result the engine is supposed to compute. Metrics on the Proof screen and the site come from actually running the eval.
- Anything simulated is labeled in data (`simulated: true`) and in UI (a visible "Simulated settlement" badge or similar). In default offline mode, x402 payment payloads are real signed EIP-3009 authorizations created with the official `x402` Python SDK, verified with real signature recovery, and settled into a local simulated USDC ledger. Live mode uses Base Sepolia through a real facilitator when keys are provided.
- Claude agents are real when `ANTHROPIC_API_KEY` is set. Without a key, scripted agents run and are labeled "Scripted agent (offline)".
- Do not invent APIs. Before using any third party library (x402, eth-account, anthropic, mcp, fastapi, cdp-sdk), read its installed source or docs in `site-packages` and use the real signatures. If a library cannot do something, write that down in `specs/DECISIONS.md` and pick a documented fallback.
- No secrets in the repo. Read keys from environment variables. Provide `.env.example`.

## Copy rules (all user-facing text: UI strings, site copy, README prose, Devpost text)

The founder has strict copy preferences. Follow them everywhere.
- No em dashes (—) and no en dashes used as punctuation. Use periods or commas. (This rule applies to user-facing copy, not to code or these specs.)
- No colon-style constructions in prose ("Here's the thing: ..."). Labels in UI like "Amount" are fine. Do not write headlines like "Elix: the controller".
- Plain sentences that read naturally, with varied length. Avoid strings of choppy fragments.
- Avoid heavy bolding in prose.
- Sentence case for UI text and headings. No ALL CAPS labels.
- Do not accent a single word of a headline with italics or a different color.
- No arrows appended to button or link text.
- Write from the user's side. Buttons say exactly what happens ("Approve payment", then a toast "Payment approved").
- No marketing filler ("revolutionize", "seamless", "unlock", "supercharge", "game-changer").

## Process rules for builder agents

1. Read every spec file before writing code. Re-read the sections for your component before you finish.
2. Work only inside the directories assigned to you. Do not edit another component's files. If you need something from another component, note it in `specs/HANDOFF.md` with your component name.
3. Write tests as you go. Your component is not done until its acceptance checks in `09-acceptance.md` pass and you have run them.
4. Keep a short log of what you built and how to run it in your component's `NOTES.md`.
5. Do not ask the user questions. The founder is asleep. Make the call, record it in `specs/DECISIONS.md`, and continue.
6. Do not publish anything, push to git remotes, send messages, or use paid external services.

## Environment facts

- Python 3.13, Node 22, npm 10. Install Python packages with `pip install --break-system-packages`. Core deps are pre-installed: fastapi, uvicorn, pydantic v2, httpx, anthropic, x402 (2.25, extras evm/fastapi/httpx/mcp/requests), eth-account, mcp, pytest, sse-starlette, typer, rich.
- Outbound internet is blocked except package registries (PyPI, npm) and GitHub (`raw.githubusercontent.com`). Base Sepolia RPCs, x402.org, Google Fonts and image CDNs are not reachable from this sandbox. Build so that offline mode works fully, and live mode is implemented carefully against the real SDK but cannot be tested here (say so in NOTES.md).
- Headless Chromium is available for screenshots through `/home/claude/tools/shot` (puppeteer-core plus @sparticuz/chromium, ESM). Example in `/home/claude/tools/shot/test.mjs`.
- Fonts for local rendering must come from npm `@fontsource` or `@fontsource-variable` packages, since Google Fonts is blocked here. The published site may link Google Fonts.
