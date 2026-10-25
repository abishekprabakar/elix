# Console notes

The Elix console is a Vite, React 18 and TypeScript app with no component library. It is built to `console/dist`, which the FastAPI server serves at `/`.

## Run it

From `console/`:

```
npm ci
npm run build                 # production build into dist, used by python -m elix demo
npm run dev                   # dev server on 5173, proxies /api to 127.0.0.1:8400
npm run dev:fixtures          # same, but with the in-browser fixture API (no backend needed)
npm run build:fixtures        # fixture build into dist-fixtures (never served by the backend)
npm run fixtures              # regenerate src/fixtures/*.json
```

`ELIX_API_TARGET=http://127.0.0.1:8411 npm run dev` points the dev and preview proxies at an API on another port.

## Layout of the code

- `src/api/types.ts` mirrors `elix/contracts.py` and spec 04. `client.ts` adds the bearer token and turns the error body into an `ApiError` with the server's message, rule id and audit seq. `events.ts` holds the EventSource (`/api/events?token=`), reconnects every 3 s, and dispatches to the store.
- `src/lib/store.tsx` keeps the acting person (token, saved in localStorage inside try/catch, overridable with `?as=sam`), the period, the theme (`?theme=dark` works too), the chain check, toasts and a version counter that SSE bursts bump. `useApi` refetches on that counter but keeps the old data on screen, so live updates never flash or shift the layout.
- `src/lib/items.ts` reads payee and spend state from `item.data` (`spend_role`, `spend_approved`, `payee_hold`, `payee_verified`) the way the engine writes it.
- `src/components` holds the signature pieces from spec 08: `Meter.tsx` (elixir pip meter with hatched committed spend, deny overflow pips and a cap marker), `Receipt.tsx` (guilloche band, decoded 402 challenge, payload hash, signature check, settlement, simulated badge, raw view), `JournalEntry.tsx` (codes in Plex Mono, credits indented, ruled and double ruled totals, balanced check), `CheckList.tsx` (failed first, glyph per severity, rule ids), `Pill.tsx` (shape, glyph and text per state), `AuditRow.tsx` (seq, time, avatar, sentence, diff toggle, hash prefix, denied stripe), `SpendChart.tsx` and `ReconTable.tsx`. All icons are inline SVG in `Icons.tsx`.
- `src/screens` has one file per screen in spec 06. `ItemDetail.tsx` is shared by the drawer, the inbox pane and `/items/:id`.
- `src/styles` is one global stylesheet split into tokens, base, shell, components and screens. Tokens are the spec 08 values, redefined for dark under `prefers-color-scheme` and `[data-theme]`.

## Design choices worth knowing

- Archivo comes from `@fontsource-variable/archivo/wdth.css`, whose woff2 carries both axes (wght 100 to 900, wdth 62 to 125). Widths are set with `font-stretch`: 116 to 125 percent for page titles and the wordmark, 104 to 112 for section and figure text, 90 for table headers. IBM Plex Mono is used only for ids, hashes, addresses, account codes in journal entries, rule ids and payloads.
- Elixir violet appears only on budget pips, primary buttons, the selected nav item and focus rings. Semantic colors carry state. Panels get a border only when they are a document (receipt, journal entry, invoice) or a list you act on.
- The work item page splits into two columns on wide screens (decision story and checks left, receipt and journal entries right) using a container query, so the drawer and inbox pane stay single column.
- Disallowed actions show disabled with the API's reason beneath. Approve and verify also offer "Try anyway", which sends the request so the presenter can show the API refusing it and the audit event it wrote. Policy approve buttons stay enabled for every human and show the API's refusal inline.
- After any successful action the store bumps its version so every open view refetches, so the console never depends on SSE timing.
- The world clock comes from the latest audit event, because `company.today` is a bare date.
- Times and dates show in UTC, labeled in the footer, because the seed places the business day in UTC hours.

## Fixtures

`scripts/make-fixtures.mjs` builds a deterministic Kestrel Labs world (seed 7) with the same shapes as the API: July to October history, the open inbox items from spec 03, the Oct 9 Forge spot purchase with an x402 receipt, invoice LUM-2026-10A with its three way match, the Stormvault payee hold with Dev's spend approval, learned policy proposals, a closed period correction, refused attempts, a sha256 hash chained audit log, balanced journal entries and an eval report shape. It reads `src/fixtures/world.base.json` (dumped from the engine's real config) so vendors, skus and payout addresses match. The fixture API in `src/api/mock.ts` is loaded only when `VITE_FIXTURES=1`, and the footer then says "Fixture data". The production bundle does not contain it.

## Checks

- `node scripts/shots.mjs --base http://127.0.0.1:8400` screenshots every screen at 1440x900 in light and dark into `screenshots/` (add `--fixtures` against a fixture build). It prints any console errors.
- `node scripts/flows.mjs --base http://127.0.0.1:8400` drives the presenter's human steps in the UI: Sam approves the Lumen short pay, Dev approves the Stormvault spend and the payment stays held, Priya verifies the payee and the gateway pays, Dev is refused on a policy approval with the API's message, Priya approves the learned policies and the correction, and Jordan gets the why answer. Run the scenarios first.
- `grep -rn "—" src` returns nothing.

## State of the screenshots

`screenshots/` holds the 22 files named in spec 09 (`today`, `inbox`, `item_payee_hold`, `item_x402_receipt`, `reconcile`, `ledger`, `policies`, `agents`, `audit_why`, `proof`, `scenarios`, each `_light` and `_dark`). They were taken against the real server (`python -m elix demo`, offline rail, seed 7) with this state, in this order:

1. Scenarios run from a fresh seed: cross-rail-double-bill, payee-change, retry-double-pay, self-approval, learn-policies, closed-period-fix.
2. Dev Okafor approved the Stormvault spend through the API, so the payee hold shows one approval in and one waiting.
3. A scripted Atlas run with the default task, so Agents has a transcript.
4. The eval ran once through `POST /api/eval/run` (default, non fast mode), so Proof shows a real report.
5. Every screen shot, then `scripts/flows.mjs` ran (8 of 8 passed), then only the ledger was shot again so it shows the dispute, payment, reversal and repost entries those approvals posted.

To retake them, repeat those steps and run `node scripts/shots.mjs --base http://127.0.0.1:8400 --out screenshots`. Wait for `networkidle2` plus no skeletons; SSE keeps one connection open, so `networkidle0` never fires against the real server.

## Known gaps

- Today's spend chart puts bank and x402 on one scale. The x402 only toggle exists because one invoice payment dwarfs a day of agent spend.
- Live decisions hides meter ingest events, which are bookkeeping rather than decisions. The Audit log shows everything.
- Close preview and period close have API routes but no console screen beyond what Scenarios triggers. Spec 06 does not ask for one.
- Agent transcripts poll every 1.5 s while a session is running instead of listening for a session SSE kind.
- Browsers log a console error for any 4xx response. The only ones a presenter will see are the intended refusals (for example a budget owner approving a policy), the eval report before the first eval run, and the Why presets before their scenario has run.
