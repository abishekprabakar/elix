# Coinbase x402 challenge, Payment Agents

## Summary

Elix is accounts payable for x402. It is for finance teams at companies whose AI agents buy data, compute and API calls on their own. Agents ask Elix to buy something, Elix reads the vendor's x402 payment challenge, checks it against policy and budget, routes exceptions to the right person, pays with a signed USDC authorization, and books the payment to the ledger with an audit trail. It also reconciles x402 payments against the same vendor's invoices, which is how it catches a vendor billing hours an agent already paid for.

Agent platform. Claude agents built with the Anthropic SDK tool use loop, plus an MCP server so any MCP client (Claude Code, Claude Desktop, other agent frameworks) can route payments through Elix.

Coinbase tools. The official `x402` Python SDK (v2 protocol, `exact` scheme on EVM) for both the paying client and the vendors' resource servers, USDC on Base Sepolia, and the x402.org facilitator in live mode. Optional CDP server wallet credentials for live mode.

## Working example

Run these in a terminal from the repo folder. Python 3.11 or newer.

```bash
pip install -e . && python -m elix demo
```

That starts the API and console on http://127.0.0.1:8400 and five x402 vendor servers on port 8402, offline, with no keys needed.

To watch one x402 payment end to end from the command line, first ask the vendor for the resource without paying.

```bash
curl -i "http://127.0.0.1:8402/parsec/queries?sku=query-1k&qty=3"
```

You get `402 Payment Required` with a base64 `PAYMENT-REQUIRED` header. Then ask Elix to buy it as the Scout agent.

```bash
curl -s -X POST http://127.0.0.1:8400/api/purchases \
  -H "Authorization: Bearer tok_scout" -H "Content-Type: application/json" \
  -d '{"vendor_id":"ven_parsec","sku":"query-1k","quantity":3,"purpose":"Search Kansas crop insurance carriers for the November list","task_ref":"GTM-88"}'
```

Three thousand queries at $5 per thousand come to $15, under Scout's $25 auto approve limit, so the response comes back already paid and posted (state `POSTED`) with a receipt. Open the item in the console (Activity, then the item) to see the decoded challenge, the signed payload hash, the facilitator's verify result and the delivered resource. Every offline receipt is labeled simulated settlement.

To route your own agent's spending through Elix over MCP, add the server to Claude Code.

```bash
claude mcp add elix --env ELIX_AGENT_TOKEN=tok_scout -- python -m elix.mcp_server
```

Details in [mcp.md](mcp.md).

### Live mode on Base Sepolia

1. Create a test wallet and fund it with Base Sepolia USDC from the Circle faucet (faucet.circle.com) or the CDP faucet in the Coinbase Developer Platform portal.
2. Put the test wallet's private key in `.env` as `ELIX_WALLET_PRIVATE_KEY` and set `ELIX_RAIL_MODE=live`.
3. Run `python -m elix demo` again. The vendor servers now verify and settle through `https://x402.org/facilitator`, and receipts carry real transaction hashes you can open on sepolia.basescan.org.

The exact commands and what to expect are in `elix/rails/NOTES.md`.

## Architecture

See [architecture.md](architecture.md). The short version is that agents can only propose. A policy engine decides, people approve exceptions, and a gateway module is the only code allowed to call `pay()`. Right before paying, the gateway fetches a fresh 402 and refuses if the payee or amount changed since approval.

## Builder feedback

Biggest blocker. Testing without a chain. The SDK's `exact` EVM facilitator wants an RPC connection for verification and settlement, so in an environment with no network access to Base Sepolia there was no supported way to verify a payload end to end. I could build real payment requirements and real signed payloads, but not check them.

Workaround. I wrote a local facilitator that implements the SDK's facilitator client interface and verifies payloads offline. It rebuilds the EIP-712 typed data for `transferWithAuthorization`, recovers the signer with `eth_account`, and checks amount, payee, asset, network, the validity window and nonce reuse. Settlement goes into a small simulated USDC ledger, and every receipt it produces is labeled simulated.

Second thing worth knowing. The client's spend controls cap each payment at $1 by default. That is a sensible default, but it fails quietly for anything priced like compute. I set `max_amount_per_payment` to the largest single payment Elix allows instead of turning the controls off.

The one capability I most wanted. An official offline or local facilitator in the SDK, something like `x402.testing.LocalFacilitator`, that does full signature and replay verification without a chain and returns clearly marked simulated settlements. It would make x402 apps testable in CI, in classrooms and at hackathons, and it would let teams write tests for payment edge cases (expired authorization, wrong payee, replayed nonce) without a funded wallet.

Smaller requests.
- A first class way to read the decoded requirements before paying, so an approval layer can show a person exactly what will be paid and to whom, then pay that exact challenge.
- A documented way to detect that the payee changed between two challenges for the same resource. Elix compares them itself today.
- Invoice and statement metadata on the 402 (vendor invoice id, usage window), so usage paid over x402 can be matched against a vendor's later invoice without guessing.
