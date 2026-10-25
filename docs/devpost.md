# Devpost draft

Copy each section into the matching Devpost field. Replace the numbers in the proof paragraph with the ones on the Proof screen if you rerun the eval.

## Project name

Elix

## Tagline (under 200 characters)

Accounts payable for x402. Elix checks every payment your AI agents make before it is signed, books it when it settles, and catches vendors who bill it again.

## Tracks to tag

- Hyphenate, The Hyphenated Enterprise (primary)
- Coinbase x402 Challenge, Payment Agents
- Microsoft, Built for the Real World
- MLH Commit Fellowship (also fill in the supplemental form)

Best Solo Hack is applied automatically for solo teams.

## The seats (Hyphenate asks for one line)

Elix fills the accounts payable clerk and spend approver seats for a company whose AI agents pay their own vendors over x402.

## How Elix answers the Hyphenate brief

Keep this section for the Hyphenate judges, either in "What it does" or as the first lines of the video description.

- Sources that disagree. The x402 receipt, the vendor's invoice and the usage meter tell three different stories about Lumen's October hours. Elix trusts the signed receipt for what was paid, the meter for what was used, and says why on every invoice line.
- Time. The seed covers July to October. Approvals from July to September become proposed limits for October, Sam's September recodes become a coding rule, and a fix to closed September posts as a reversal and repost in October.
- Working with others. Elix asks the agent for a purpose when it is missing, routes spend to the budget owner who owns that agent, and waits for the controller on a changed payee. When Priya rejects something, the item stops and says why.
- Limits. Agents have no approve or pay tool. Nobody approves their own request, a changed payee needs the controller, and a closed period needs a second person.
- Explainable and accountable. Ask why about any invoice, payment or journal entry and the answer comes from the hash chained audit log, with the rule and the person behind every step.

On the four judging questions. It is real enough because Kestrel Labs has three months of messy history and the eval plants 56 errors with an answer key. You would hire it for the accounts payable clerk and spend approver seats. What is new is that the payer is an agent paying over x402. The moment is the $1,260 double bill.

## Inspiration

AI agents can now buy things on their own. With x402, an API can answer 402 Payment Required and an agent can pay it in USDC a second later. That is great for the agent and a mess for finance. A company ends up with agents paying on a stablecoin wallet, vendors billing the same usage on a monthly invoice, and a bank feed that sees neither. Somebody still has to approve each payment, book it to the right account, and catch the vendor who bills the same usage again. Accounts payable software was built for invoices that people approve one at a time. I wanted to build accounts payable for the payments agents make on their own.

## What it does

Elix is accounts payable for x402. It does three things for every payment an agent makes. Before the payment is signed, Elix reads the vendor's 402 challenge and checks it against the agent's budget, its per purchase cap, the vendor master and the person who owns the budget, then pays it, asks the agent a question, routes it to that person, or refuses. When it settles, Elix books it as a double entry journal entry with the x402 receipt attached. When the vendor's invoice arrives, Elix matches every line against the usage meter and against what agents already paid over x402. Every attempt, allowed or denied, lands in a hash chained audit log.

The moment I built it around is a double bill. Kestrel Labs, the fictional customer in the demo, has a GPU agent that paid Lumen for 600 spot hours over x402 on October 9. A week later Lumen's semi monthly invoice bills those same hours again, because Lumen's meter counts all usage and its invoice system has no idea what the wallet paid. Elix sees both rails, so it proposes paying $12,234 instead of $13,494, drafts the dispute and books the $1,260 difference to vendor disputes. When Lumen answers with a credit note, Elix checks it against the open dispute, refuses duplicates and credits larger than what is open, and books it against accounts payable so the dispute closes in the ledger as well.

It also holds a payment when a vendor's wallet changed two hours ago, even after the budget owner approved the spend. It refuses when an agent tries to approve its own order, and it treats vendor text that says "pre-approved by finance" as data. It catches split purchases, retries, prices above contract and invoices billing more than the meter. Because finance runs for months, it also learns from history. Three months of approvals and recodes become proposed auto approve limits and coding rules, which the controller has to sign off on before they change anything.

Bills arrive in every format, so Elix also reads invoice PDFs. Claude proposes the fields through a strict tool call, or an offline parser does when there is no key, and deterministic code checks the vendor, the invoice number, each line's product and arithmetic, the total and the dates before the invoice is reconciled like any other. The sample AWS invoice with a mistyped line total is held for the accountant and nothing posts.

Any agent can use it. Elix ships an MCP server, so Claude Code, Claude Desktop or any MCP client gets a wallet it cannot overspend.

## Proof

I built a 90 day synthetic company with anomalies planted at known positions and an answer key the engine never reads. In the latest run Elix caught all 56 planted anomalies across 14 categories and paid none of them without a person. Every payment it made on its own was clean, so automation precision was 100.0%. Once the learner's policies were approved, the share of clean purchases paid without a person went from 12.0% to 85.3%. The audit chain verified across 3,924 events. Anyone can reproduce this with `python -m elix eval`.

What is real and what is simulated is labeled everywhere. The policy engine, ledger, audit chain and reconciliation are real code. The x402 messages and payment signatures are real, created with Coinbase's official x402 Python SDK, and verified with real signature recovery. In offline mode settlement goes into a local simulated USDC ledger. Live mode uses Base Sepolia through a facilitator. The company and its data are synthetic.

## How we built it

The engine is Python with FastAPI and SQLite. One facade handles every operation, so the console, CLI, MCP server and tests share a code path. Policy is a table of deterministic rules that each return a pass or fail check with evidence. A gateway module is the only code allowed to call a payment rail or post to the ledger, and a test enforces that. The audit log is append only, with each row hashing the one before it, and is written in the same transaction as the change it describes.

The x402 rail uses Coinbase's `x402` SDK for the client and for the vendor's resource server. Because the build environment could not reach a chain, I wrote a local facilitator that implements the SDK's facilitator interface and verifies the EIP-712 signature with `eth_account`. Agents are Claude tool use loops through the Anthropic SDK, with scripted fallbacks for offline demos. The console is React and TypeScript with no UI kit.

I wrote the specs first, including the domain model, the rule table, the state machines and the acceptance checks, and built every component against them.

## Challenges we ran into

The x402 client ships with a one dollar per payment spend cap by default. That is a sensible safety default, but every GPU reservation in the demo is far above it. I set it to the largest cap Elix allows instead of turning it off, since Elix is the spend control.

Verifying payments without a chain took work. The SDK's facilitator expects to talk to an RPC, so offline verification meant reproducing the exact typed data the client signs and recovering the signer ourselves.

The hardest product problem was the payee change. Approving a purchase and releasing money to a wallet that just changed are different decisions with different owners, and the state machine had to keep them apart without making the common case slower.

## Accomplishments that we're proud of

The double bill catch works on real protocol messages, not a mocked flag. Refused attempts are part of the record, so an auditor can see what an agent tried and why it did not happen. The eval has an answer key the engine never sees, so the proof numbers are earned. And it was built solo in one weekend.

## What we learned

Finance software is mostly about who is allowed to do what, and when. The language model is useful for proposing and explaining, but the product is the controls. I also learned that x402 makes agent payments easy enough that the bookkeeping problem shows up immediately.

## What's next for Elix

Live settlement on Base and real bank feeds. Card and API metering rails, so the same reconciliation covers OpenAI, Anthropic and AWS usage. ERP sync to NetSuite and QuickBooks. A design partner pilot with an AI company whose agents already buy data and compute.

## Built with

Add coinbase-developer-platform and base-sepolia only if you completed the live test in the morning checklist.


python, fastapi, sqlite, pydantic, x402, base, usdc, eip-3009, eth-account, anthropic, claude, mcp, react, typescript, vite
