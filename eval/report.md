# Elix eval report

A generator that never imports the engine writes 90 days of purchases, invoices and meters with planted errors and an answer key. The engine sees only the events. Days 1 to 30 are warm up history, then June closes, the learner proposes policies and a simulated controller approves them. Days 31 to 90 are scored on what Elix paid without a person.

Seed 11. Mode offline x402 rail with local vendor servers (real signed EIP-3009 payloads, simulated settlement). Generated 2026-10-24T12:35:39Z in 35.81 seconds. Engine version not committed.

## Headline

| Measure | Value |
|---|---:|
| Anomalies planted | 56 |
| Anomalies caught | 56 |
| Anomalies paid without a person | 0 |
| Categories fully caught | 14 of 14 |
| Automation precision | 100.0% |
| Clean purchases automated before learning (days 1 to 30) | 12.0% |
| Clean purchases automated after learning (days 31 to 90) | 85.3% |
| Clean scored items paid without a person | 325 of 381 |
| Audit chain verified | yes (3924 events) |

## By category

| Category | Planted | Caught | Not auto paid | Auto paid | How it was planted |
|---|---:|---:|---:|---:|---|
| Duplicate retry | 4 | 4 | 4 | 0 | A request resent with a new idempotency key 10 to 300 seconds later. |
| Payee changed | 4 | 4 | 4 | 0 | The vendor master changed 1 to 48 hours earlier and the 402 asks for the new address. |
| Price above contract | 4 | 4 | 4 | 0 | The vendor quotes 15 to 60 percent above the contract price. |
| 402 amount mismatch | 4 | 4 | 4 | 0 | The 402 challenge asks for more than the agent requested. |
| Over per purchase cap | 4 | 4 | 4 | 0 | One purchase above the agent's limit per purchase. |
| Budget exceeded | 4 | 4 | 4 | 0 | A burst of clean spend fills the month, then a request crosses the budget. |
| Split purchase | 4 | 4 | 4 | 0 | Seven small requests in 14 minutes, the seventh crosses the cap. |
| Unknown vendor | 4 | 4 | 4 | 0 | A vendor that is not in the vendor master. |
| Missing purpose | 4 | 4 | 4 | 0 | No usable purpose and no task reference. |
| Instructions in memo | 4 | 4 | 4 | 0 | A memo claims the order is pre-approved, on a request that would otherwise auto approve. |
| Agent approves itself | 4 | 4 | 4 | 0 | The agent calls approve on its own escalated request. |
| Invoice double bills x402 usage | 4 | 4 | 4 | 0 | The invoice bills hours already paid over x402. |
| Invoice over meter | 4 | 4 | 4 | 0 | The invoice bills 5 to 20 percent more usage than the meter shows. |
| Invoice rate mismatch | 4 | 4 | 4 | 0 | The invoice rate differs from the contract rate. |

## Policies learned after warm up

- pol-001. Auto approve Atlas buying scenes at 10 m from Terrafield Imagery up to $40. 98 purchases like this from Jun 1 to Jun 30 were approved by Dev Okafor and none were rejected. The largest was $36.00.
- pol-002. Auto approve Forge buying H100 spot hours from Lumen Compute up to $1,180. 28 purchases like this from Jun 1 to Jun 30 were approved by Dev Okafor and none were rejected. The largest was $1,218.00.
- pol-003. Auto approve Scout buying firmographic records from Signalboard up to $80. 26 purchases like this from Jun 1 to Jun 30 were approved by Maya Chen and none were rejected. The largest was $80.00.

## How to read this

An anomaly counts as caught when Elix did not pay it without a person and flagged at least one of the expected rules. Some categories also check the state (a missing purpose must wait for information), the proposed short pay (within $1 of the double billed amount) or the refused approval in the audit log. The answer key is in answer_key.json next to this file and the full event stream is in events.jsonl.
