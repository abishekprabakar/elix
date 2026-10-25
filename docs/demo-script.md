# Demo script

Two versions. The live pitch is for the judging table (about two minutes, then questions). The video is for Devpost and Hyphenate, who asked for a video that opens with the moment.

Before you start, run `python -m elix demo` in a terminal inside the repo folder, open http://127.0.0.1:8400 in a browser, and set the console to light mode. Keep the Scenarios screen open in a second tab. If you have run scenarios already, press Reset demo data on the Scenarios screen first.

## Live pitch, about two minutes

Open on the Reconcile screen with nothing run yet, acting as Sam Ortiz.

1. The setup (15 seconds). "Elix is accounts payable for x402. This is Kestrel Labs, a fictional 38 person AI company. Its three agents pay for GPU hours, satellite imagery and data over x402, and some of the same vendors also send invoices. Elix fills the accounts payable clerk and spend approver seats."

2. The moment (40 seconds). On Scenarios, run "Lumen bills hours Forge already paid for". Open the invoice from the result link. "Lumen sent its invoice for October 1 to 15. It bills 2,140 GPU hours. The meter agrees. But on October 9 Forge already paid for 600 of those hours over x402, and here is the receipt. Lumen's invoice has no way of knowing that, and our bank has no way of knowing either. Elix sees both rails, so it proposes paying $12,234 instead of $13,494 and drafts the dispute." Approve as Sam. Show the journal entry with the disputed $1,260 parked in vendor disputes.

   Optional, if a judge asks what happens next (15 seconds). On Scenarios, run "Lumen credits the double billed hours". Lumen's credit note LUM-CN-2026-10A for $1,260.00 arrives, Elix matches it to the open dispute, and the gateway books Dr 2010 Accounts payable, Cr 2150 Vendor disputes. Open the invoice again and the dispute reads Credited, with the credit note and its journal entry. "The dispute closes in the books too, not just in someone's inbox." Sam can also key in a credit note by hand with Record credit note on the same panel. Skip this step if you plan to show the auditor's question, or show it after, since the why answer then includes the credit.

3. Payee hold (30 seconds). Run "A changed payee is held for the controller". Switch to Dev Okafor, approve the spend. "Dev approved the purchase, but the money still does not move. The vendor's 402 response asks us to pay a wallet that changed two hours ago. Only the controller can release that." Switch to Priya and show the hold reason. Reject it or verify it, your choice.

4. Limits in code (15 seconds). Run "An agent tries to approve its own purchase". Show the 403 and the denied attempt in the audit log. "The agent has no approve tool, and when it calls the API directly Elix refuses and logs the attempt. Vendor text that claims the order is pre-approved is treated as data."

5. Audit and proof (20 seconds). Audit screen, press "Why was LUM-2026-10A paid short?". "Months later the answer is one query." Then Proof. Read the numbers from the screen: anomalies planted, anomalies auto paid, automation rate before and after learning. "These come from 90 days of synthetic transactions with planted errors and an answer key the engine never sees."

If judges want their own scenario, open Scenarios, use the custom form, and let them pick the agent, vendor, quantity, price, payee and memo.

If a judge asks about invoices that arrive as PDFs, open Reconcile as Sam, press Upload an invoice PDF and upload the sample "AWS-2026-10 with an arithmetic error". Elix shows what it read, the one check that failed (41.2 TB months at $23.00 is $947.60, not $974.60) and that nothing posted. On a fresh world the Lumen sample reconciles into the same double bill as the scenario.

Things to avoid during the pitch. Do not press Run the eval again on Proof, since it rewrites the report that the README and Devpost quote. Do not run `seed` or `eval` in another terminal while the demo is up, since they share the vendor servers. Ask the Why question only after the double bill scenario has run.

Likely questions, with short answers.
- What is real? The policy engine, ledger, audit chain, and the x402 protocol messages and signatures through Coinbase's official SDK. Settlement is simulated offline here and runs on Base Sepolia in live mode. The company and its data are synthetic.
- Why not let Claude approve? Precision matters more than coverage in finance. Claude can propose and explain. Code decides, people approve exceptions, and only the gateway can move money.
- Why would a company buy this instead of Ramp? Ramp sees its own cards. Agents pay on x402, cards, API metering and invoices. A controller needs one system that sees every rail, and card companies are not neutral across rails.

## Video, about 90 seconds

Record the browser at 1440 by 900 with QuickTime (File, New Screen Recording). Speak over it or add captions.

| time | screen | say or caption |
|---|---|---|
| 0 to 25s | Reconcile, run the double bill scenario, open the invoice | "Lumen billed 600 GPU hours that Forge had already paid for over x402. Elix caught it because it sees every payment rail." |
| 25 to 35s | Approve as Sam, show the journal entry | "Pay $12,234, dispute $1,260, booked to the right accounts." |
| 35 to 50s | Title card or Today screen | "Elix is accounts payable for x402. It fills the accounts payable clerk and spend approver seats for agents." |
| 50 to 65s | Payee hold, Dev approves, money still held | "Approving spend and releasing money to a changed wallet are different decisions." |
| 65 to 75s | Self approval refused, audit log | "Limits live in code. Refused attempts are logged too." |
| 75 to 90s | Proof screen | Read the eval numbers. "Tested on planted errors with an answer key." |
