# Microsoft, Built for the Real World

Elix is a production minded agent system for finance. This page maps it to the five judging criteria.

## Customer understanding

The customer is the controller or head of finance at a 20 to 200 person AI company. Their agents already buy data, GPU time and API calls, and the volume is growing faster than headcount. Today that spend shows up in three places that never agree. A USDC wallet or card feed shows agent payments one at a time. Vendors send monthly or semi monthly invoices built from their own meters. The bank feed shows the invoice payments. The controller's real jobs are approving the right things quickly, booking spend to the right account and cost center, catching double billing and overages, closing the month on time, and answering the auditor.

Elix is built around those jobs rather than around the agent. The people in the demo company map to real finance roles. Priya Raman is the controller, Dev Okafor and Maya Chen are budget owners, Sam Ortiz is the accountant, and Jordan Lee is an outside auditor with read only access. Each sees a different inbox and can take different actions.

## Measurable usefulness

The eval generates 90 days of purchases and invoices for the demo company with anomalies planted at known positions, and scores Elix against an answer key the engine never reads. It measures
- how many planted anomalies were caught, by category (14 categories, from changed payees to cross rail double bills),
- how many anomalies were auto paid, where the target is zero,
- automation precision, meaning the share of auto approved items that were actually clean,
- the automation rate before and after the learner's policies were approved, which is the time a finance team gets back,
- whether the audit chain verifies end to end.

The latest numbers are in `eval/report.md` and on the console's Proof screen. Run `python -m elix eval` to reproduce them.

The demo also has one concrete dollar outcome. On the Lumen invoice for October 1 to 15, Elix pays $12,234 instead of $13,494 and disputes $1,260 that had already been paid over x402.

## Path from prototype to production

The engine is one Python package with a single facade, so the console, CLI, HTTP API, MCP server and tests already share a code path. Moving to production is mostly swapping infrastructure under stable interfaces.

1. Storage. Replace SQLite with Postgres. Keep the audit log in its own append only table with the hash chain, and anchor the head hash externally each day.
2. Rails. The `Rail` protocol already separates x402 and bank. Add a live bank rail (Mercury or a Plaid feed plus bank API), a card rail, and API usage feeds from model providers.
3. Identity. Replace static bearer tokens with SSO for people and per agent credentials issued by the company's identity provider, with the same roles map.
4. ERP. Push posted journal entries to NetSuite or QuickBooks, and pull the chart of accounts and vendor master from there instead of seeding it.
5. Deployment. Containerize the API, run it next to the customer's agents, and keep payment keys in a managed signer such as a CDP server wallet or a cloud KMS.

A pilot needs steps 1, 3 and 4 and live x402 settlement. The rest can follow.

## Responsible design

- Language models propose and explain. They never authorize. Policy is deterministic code with a pass or fail check per rule and the evidence behind it.
- Agents have no approve or pay tool. Direct calls to those endpoints are refused and logged.
- Segregation of duties is enforced in code. Nobody approves their own request, payee changes go to the controller, and corrections to a closed period need a second person.
- Vendor and memo text is treated as untrusted data. Text claiming an order is pre-approved turns off learned auto approval for that item and is shown to the approver.
- Every attempt, allowed or denied, is in a hash chained audit log written in the same transaction as the change.
- Learned policies are proposals with evidence. A controller must approve each one, and they can be retired.
- Precision is chosen over coverage. When a rule is unsure, the item goes to a person.
- Everything simulated is labeled in the data and in the interface.

## End to end functionality

`python -m elix demo` starts the whole system offline with no keys. The x402 vendor servers issue real payment challenges, the agents request purchases, Elix decides, people approve in the console, the gateway pays with real signed authorizations, the ledger posts, invoices reconcile, month end accrues, and the audit screen answers questions about any item. With an Anthropic key the agents are real Claude tool use agents. With a funded Base Sepolia wallet, settlement goes on chain.
