# 08 · Design system (shared by console and site)

The founder finds most AI-built sites sloppy. The bar is a product that looks like a real fintech company built it, benchmarked against Ramp and Scale AI, with its own identity rather than a copy. Read this whole file before writing CSS.

## Identity in one paragraph

Elix is named after the elixir bar in Clash Royale, the resource you spend and cannot overspend because it refills at a fixed rate. The visual identity borrows from two real worlds. From the elixir bar, a segmented pip meter that fills in a saturated elixir violet, used for budgets. From finance paperwork, the look of check safety paper and receipts: a cool pale paper tone, fine engraved line patterns (guilloche) used sparingly, ledger columns, carbon blue-black ink, and tabular numbers that line up. Everything else stays quiet and precise.

## Things we will not do (they read as generated)

- No cream background with a terracotta or clay accent. No black background with a lone acid green or vermilion accent. No purple to blue gradients anywhere. No gradient washes as decoration.
- No Inter, no Space Grotesk, no Poppins, no Instrument Serif, no Fraunces, no Bricolage Grotesque.
- No ALL CAPS eyebrow labels above headings. No "01 / 02 / 03" markers unless the content is a real sequence. No middle dot meta strings like "A · B · C" in UI copy. No arrows appended to link or button text. No emoji as icons or section markers.
- No identical rounded cards with the same soft grey shadow for every block. Card styling is applied by role.
- No fade and slide up on every section. One orchestrated motion moment per page at most, plus motion that responds to user action.
- No lorem ipsum, no "Feature One", no stock phrases. Use Kestrel Labs content.

## Color tokens

Light is the default. Dark is designed with the same care ("night ledger"), not a simple inversion. Define every token on bare `:root`, redefine in `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {...} }` and `:root[data-theme="dark"] {...}`.

| token | light | dark | role |
|---|---|---|---|
| `--paper` | `#F1F4F1` | `#0F1416` | page background (cool safety paper / deep ledger green black) |
| `--surface` | `#FFFFFF` | `#161D20` | raised surfaces: receipts, drawers, table body |
| `--surface-2` | `#E8EEEA` | `#1D2629` | subtle fills: table header, hovered rows, code blocks |
| `--ink` | `#141A24` | `#E9EEF0` | primary text (carbon ink) |
| `--ink-2` | `#4D5866` | `#A6B2B8` | secondary text |
| `--ink-3` | `#7C8794` | `#76838A` | tertiary text, disabled |
| `--rule` | `#CFD8D3` | `#2A3538` | 1px rules and borders |
| `--engrave` | `#C3D3CB` | `#22302F` | guilloche and security pattern lines |
| `--elixir` | `#7B1FD1` | `#C77DFF` | the brand accent: budget pips, primary buttons, focus ring, selected nav |
| `--elixir-ink` | `#FFFFFF` | `#1A0A26` | text on elixir fills |
| `--elixir-soft` | `#EFE3FA` | `#2B1A3A` | elixir tint backgrounds (selected rows, pip track) |
| `--ok` | `#0F7A4E` | `#4CC38A` | approved, posted, verified |
| `--ok-soft` | `#DFF1E8` | `#13291F` | |
| `--hold` | `#9A5A00` | `#E7A93B` | needs approval, held, needs info |
| `--hold-soft` | `#FBEBCF` | `#2E2210` | |
| `--deny` | `#B4231B` | `#FF7A70` | denied, rejected, failed, broken chain |
| `--deny-soft` | `#F8DEDB` | `#33171A` | |
| `--link` | `#1F4FB8` | `#8AB4FF` | inline links in prose only |

Semantic colors are not accents. The only accent is elixir, and it is used for at most one or two things per screen.

## Type

Two families.
- Archivo (variable, with the width axis `wdth` 62 to 125 and weight 100 to 900). The width axis is the personality. Display headlines use a wide cut (`font-stretch: 118%` or `font-variation-settings: "wdth" 118`) at weight 720 to 780 with letter spacing around -0.015em. Body uses normal width at 400. Dense UI labels and table headers use a slightly narrow cut (`wdth` 90) at weight 560.
- IBM Plex Mono for code, hashes, HTTP exchanges, x402 payloads, journal entry account codes inside JE blocks, and tx refs. Not for small labels or numbers in tables.

Numbers in tables, meters and amounts use Archivo with `font-variant-numeric: tabular-nums lining-nums`.

Loading:
- Site (published artifact): Google Fonts `https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@62..125,100..900&family=IBM+Plex+Mono:wght@400;500&display=swap`, with fallback stacks `"Archivo", "Helvetica Neue", Arial, sans-serif` and `"IBM Plex Mono", ui-monospace, Menlo, monospace`.
- Console and local screenshots: npm `@fontsource-variable/archivo` (check that it ships the `wdth` axis; if the full variable file is named differently, find it in the package) and `@fontsource/ibm-plex-mono`. Google Fonts is blocked in the sandbox.

Type scale (rem at 16px root): 0.75, 0.8125, 0.875 (UI default), 1, 1.125, 1.375, 1.75, 2.25, 3, 4.25 (site hero only), with line heights 1.5 for body, 1.2 for headings, 1.05 for the site hero. Headings use `text-wrap: balance`. Body text measure stays under 72 characters.

## Shape, rules, elevation

- Radius by role: 4px for inputs, buttons and chips. 8px for panels and drawers. 999px only for state pills and pips. Tables have no radius on rows.
- Borders are 1px `--rule`. Prefer rules and spacing over boxes.
- Elevation: almost none. Drawers and the command palette get one crisp shadow `0 1px 0 var(--rule), 0 12px 32px -12px rgb(20 26 36 / 0.25)` (dark: stronger alpha). Nothing else has a shadow.
- Spacing scale: 4, 8, 12, 16, 20, 24, 32, 40, 56, 72, 96.

## Signature components

1. Elixir meter. A row of 10 pips (rounded capsules) for an agent's monthly budget. Filled pips in `--elixir` show spent, a hatched pip pattern shows committed (pending approvals), empty pips in `--elixir-soft`. A thin tick marks the per transaction cap scaled to budget if visible. Numbers beside it in tabular figures: "$3,412 of $6,000". When over 100 percent the overflow pips turn `--deny`. Used on Today, Agents, and the site hero product shot.
2. Receipt block. A white surface with a fine guilloche band along the top edge (drawn once in an SVG pattern, 1px lines in `--engrave`), showing the x402 exchange: the 402 challenge (pay to, amount, asset, network), the signed payload hash, verification, settlement ref and a "Simulated settlement" badge when simulated. Used in work item detail and on the site.
3. Journal entry block. Two column debit/credit layout with account codes in Plex Mono and names in Archivo, totals ruled, balanced check mark. Reversals show a link to the original.
4. Check list (decision). Each rule as a row: status glyph (filled circle pass, half circle escalate, ring with dot ask, cross deny), rule name, one line detail, rule id in Plex Mono at the right. Failed rows first.
5. State pill. Shape plus color plus text so state reads at a glance even in grayscale: Posted (ink, solid), Auto approved (ok, solid dot), Needs approval (hold, outline with half dot), Held for payee check (hold, lock glyph), Needs info (hold, question glyph), Denied (deny, cross), Rejected (deny, outline), Failed (deny, solid).
6. Audit row. Seq number, time, actor avatar (initials in a small square, agents get a pip glyph instead of initials), sentence, diff toggle, hash prefix in Plex Mono. Denied attempts get a `--deny-soft` background stripe.

Icons: draw simple inline SVG glyphs (12 to 16px, 1.5px stroke). Do not use emoji. Do not pull an icon font from a CDN in the console; inline SVG is fine.

## Motion

- Console: transitions under 160ms for drawers and pills changing state. When a new event arrives on SSE, the row gets a brief `--elixir-soft` highlight that fades over 1.2s. That is the only ambient motion. Respect `prefers-reduced-motion`.
- Site: one orchestrated hero sequence (see 07). Everything else is still.

## Accessibility

Contrast at least 4.5:1 for body text in both themes. Visible focus ring 2px `--elixir` with 2px offset. Every interactive control reachable by keyboard. Pills include text, not color alone.
