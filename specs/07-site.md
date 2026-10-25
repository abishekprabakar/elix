# 07 · Marketing site

Directory `site/`. A single page that will be published as a claude.ai Artifact (a hosted HTML page). Read 08 first, and read the Artifact page contract below because it constrains the build.

## Artifact page contract (hard constraints)

- Author `site/src/index.html` as page content without `<!doctype>`, `<html>`, `<head>` or `<body>` tags; the host wraps it. Put `<title>Elix</title>` and `<meta name="claude:viewport-fit-sides" content="cover">` at the top, then one `<style>` block, then content, then one inline `<script>`.
- External stylesheets only from Google Fonts. External scripts only from cdnjs (avoid scripts entirely if possible). Images, video, and everything else must be inline (data URIs). No fetch to any other host.
- Side gutter: `padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px));` on one outer wrapper or each band's inner wrapper, vertical padding with `padding-block`.
- Works at 400px wide with no horizontal scroll. Light and dark using the token pattern in 08, `body` gets an explicit `background: var(--paper)`.
- Forms cannot submit anywhere. The pilot form is handled in script (see below).
- No `window.print`, no `alert/confirm/prompt`, no `<a download>`, no iframes. Links to other sites use normal `<a href>` and open in a new tab.
- Whole page under 8 MB including inlined images. Use WebP or optimized PNG/JPEG for screenshots (target 120 to 350 KB each).
- Respect `prefers-reduced-motion`. The page must be complete at rest: no content hidden at opacity 0 waiting for scroll observers.

## Build

`site/build.py` reads `site/src/index.html`, replaces placeholders like `{{img:today_light}}` with data URIs from `site/assets/` (screenshots copied from `console/screenshots/` and optimized), and `{{eval:automation_precision}}` style placeholders with numbers from `eval/report.json`, and writes `site/dist/index.html`. It fails loudly if a placeholder has no value. Numbers on the site must come from the eval report.

Until console screenshots exist, use clearly temporary placeholder images generated locally (a neutral rectangle with the screen name), and keep the placeholders' names stable so the final build swaps them in.

## Audience and job

Primary viewer is an investor or judge who gives the page 30 seconds, likely on a laptop, sometimes a phone. The page has one job: make them understand that AI agents now spend money, that finance has no system for it, and that Elix is that system, then show the real product doing it.

## Structure and copy

Write all copy following the rules in 00 (no em dashes, no colon constructions, no all caps labels, no single word accents, no arrows on links, plain sentences). The copy below is the starting point. Improve wording if it reads awkwardly, keep the meaning and the rules.

1. Top bar. Wordmark "Elix" (custom lettering is fine: Archivo at a wide width with a small elixir pip as the dot or mark). Links: How it works, Proof, Developers, and a "Request a pilot" button.

2. Hero.
   - Headline: "The controller for agent spend."
   - Lede: "Your agents now buy data, compute and API calls on their own. Elix approves each purchase, pays over x402 or the bank, books it to the right account, and keeps a trail your auditor can read."
   - Buttons: "Request a pilot" and "See it catch a double bill" (anchors to the moment section).
   - The live wire panel (the page's one orchestrated motion moment). A vertical sequence of five steps for a real transaction from the demo world, shown fully at rest, with a single pass of highlighting on load that walks down the steps (about 3 seconds total, skipped under reduced motion):
     1. "Forge asks for 600 H100 spot hours from Lumen Compute for the hail-v7 retrain." (agent avatar pip, task ref)
     2. "HTTP 402 Payment Required. Pay 1,260.00 USDC on Base to 0x4b1d…77c0." (in Plex Mono, styled as a protocol line)
     3. "Nine checks passed. Covered by pol-002, learned from 61 approvals by Dev Okafor." (use the real numbers from the seeded world if available, otherwise keep this copy and mark it as example in the HTML comment)
     4. "Paid with a signed EIP-3009 authorization. Settlement sim-a91c…" with a small "simulated" badge
     5. A journal entry block: Dr 5110 Compute $1,260.00, Cr 1015 USDC wallet $1,260.00
   - Under the hero, a full width real screenshot of the Today screen (light theme on light, dark on dark using `<picture>` with `prefers-color-scheme` sources, or two images toggled by CSS on the theme tokens).

3. Problem. Heading "Agents spend on every rail. Finance sees none of them together." Three short paragraphs with Kestrel details:
   - "Atlas buys satellite scenes a few dollars at a time over x402. Forge rents GPUs from Lumen, partly on x402 and partly on a twice monthly invoice. Scout buys enrichment data and search queries."
   - "Each payment rail only knows its own transactions. The card company never sees the stablecoin payment. The vendor's invoice never mentions what was already paid."
   - "So someone in finance reconciles it by hand at month end, if they catch it at all."
   A simple diagram drawn in inline SVG: three agents on the left, three rails in the middle (x402, card, invoice), one ledger on the right with Elix between rails and ledger. Real labels, no decoration.

4. How it works. This is a real sequence, so numbered steps are appropriate. Six steps, each with a one sentence description and a small cropped screenshot or a drawn UI fragment:
   1. Request. "An agent asks to buy something and has to say why."
   2. Check. "Deterministic rules decide. Caps, budgets, contract prices, duplicates, split orders, payee changes. The model never gets to approve."
   3. Pay. "Only the gateway holds the wallet. It pays over x402 or the bank, and refuses if the vendor's terms changed after approval."
   4. Book. "Every payment becomes a balanced journal entry in the right account and cost center."
   5. Reconcile. "Invoices are matched against usage meters and payments on every rail."
   6. Explain. "Every action is written to a hash chained audit log, including the attempts that were refused."

5. The moment. Heading "Lumen billed 600 hours that Forge had already paid for." Body: "On October 9 Forge paid Lumen 1,260 USDC over x402 for 600 spot hours. Lumen's invoice for October 1 to 15 counted those hours again. Elix matched the invoice against the usage meter and the x402 receipt, proposed paying $12,234.00 instead of $13,494.00, and drafted the dispute. Sam approved it in one click." Then: "No single payment rail could have caught this, because none of them sees the others." Real screenshot of the Reconcile screen.

6. Controls in code. Heading "Limits live in code, not in a prompt." A check list (component 4 from 08) of real rules with their ids: segregation of duties (R31), payee change holds (R03), duplicate retries (R04), split purchases (R09), contract price checks (R06), instructions hidden in vendor text (R11), closed periods (R30). Each with one plain sentence. Beside it a screenshot of the payee hold item ("Dev approved the spend. Elix still held the money until Priya verified the new wallet.").

7. Audit. Heading "Months later, the answer is one query." Short paragraph and a screenshot of the Why report for LUM-2026-10A.

8. Proof. Heading "Tested against planted errors." Numbers from `eval/report.json`: anomalies planted, anomalies auto paid, automation precision, automation rate before and after learning, chain verified. A sentence explaining the method in plain words: "We generate 90 days of synthetic purchases and invoices with planted errors and an answer key the engine never sees, then score what Elix paid without a person." Plus the "what is real and what is simulated" list, short.

9. Developers. Heading "Give any agent a wallet it cannot overspend." A code block with the MCP setup line and a 6 line example of an agent tool call. Mention x402 on Base, the official x402 SDK, and MCP. Link to the GitHub repo placeholder `{{repo_url}}`.

10. Pilot. Heading "Built for finance teams at AI native companies." One sentence on who it is for. Form fields: work email, company, roughly how much your agents spend per month (select), and a submit button "Request a pilot". On submit, if the artifact runtime `window.claude` storage capability is available (the publisher will wire it), store the entry; otherwise show "Thanks. We will reach out from the Cal Hacks booth this weekend." without pretending it was sent anywhere. The publisher will adjust this part.

11. Footer. "Elix was built at Cal Hacks 13.0 by Abishek Prabakar, Georgia Tech." "The demo company, Kestrel Labs, is fictional and its data is synthetic. Settlement in the demo is simulated unless marked live."

## Visual direction

- Light first, safety paper tone, carbon ink, elixir accent used for the pip marks, primary buttons and the hero highlight only.
- The headline uses Archivo at a wide width and large size, left aligned. The hero is not centered. The live wire panel sits to the right on desktop and below on mobile.
- One fine guilloche band (SVG pattern, `--engrave`) behind the hero, very low contrast. Nowhere else on the page except the receipt block.
- Screenshots get a thin `--rule` border and 8px radius, no browser chrome mockup, no tilt, no shadow stack. Let the product carry the page.
- Sections are separated by generous space and a single rule, not by alternating background colors.
- Keep the page under about eight screens on desktop.

Review your own result with headless Chromium screenshots at 1440 wide and 400 wide in light and dark before you finish. Fix anything clipped, overlapping, or misaligned.
