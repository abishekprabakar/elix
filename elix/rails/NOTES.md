# Rails and vendor servers

Owner: rails builder. Code in `elix/rails/` and `elix/vendors/`. Tests in `tests/rails/`.

## What is real and what is simulated

| Piece | Offline mode (default) | Live mode |
|---|---|---|
| 402 challenge | Real. The vendor app uses the official x402 FastAPI middleware (`x402.http.middleware.fastapi.payment_middleware`) with `x402ResourceServer` and `ExactEvmServerScheme`. The `PAYMENT-REQUIRED` header is built and base64 encoded by the SDK. | Same code. |
| Payment payload | Real. `x402ClientSync` with `ExactEvmScheme` signs an EIP-3009 `TransferWithAuthorization` (EIP-712, Base Sepolia USDC domain `USDC` / `2`, chain 84532) with the treasury key. Sent in `PAYMENT-SIGNATURE`. | Same, signed with `ELIX_WALLET_PRIVATE_KEY` or a CDP Server Wallet account. |
| Signature verification | Real, twice. (1) Elix rebuilds the typed data with the SDK's `build_typed_data_for_signing` and recovers the signer with `eth_account` (`Account.recover_message` over `encode_typed_data`). (2) The SDK's own facilitator scheme `x402.mechanisms.evm.exact.facilitator.ExactEvmScheme.verify` runs against `SimChainSigner`. Both check payee, amount, asset, network, validity window and nonce reuse. | Done by the real facilitator (`ELIX_FACILITATOR_URL`, default `https://x402.org/facilitator`). |
| Settlement | Simulated. `SimChainSigner.write_contract` moves balances in `ELIX_DATA_DIR/sim_chain.db`, marks the nonce used, and returns `0x` + sha256(payload). Receipt `simulated: true`, `tx_ref` = `sim-` + first 16 hex of sha256(payload). The `PAYMENT-RESPONSE` carries `extra.simulated = true`. | Real `transferWithAuthorization` on Base Sepolia submitted by the facilitator. Receipt `simulated: false`, `tx_ref` is the transaction hash. |
| Bank (ACH) | Simulated in every mode. `tx_ref` = `ach-<12 hex>`. | Simulated. |
| Keys | Test keys derived from public strings: `sha256("elix-offline-treasury")`, `sha256("elix-offline-vendor:<vendor_id>:<rotation>")`, `sha256("elix-offline-facilitator")`. Anyone can recompute them. Never fund them on a real network. | Payer key from env. Vendor payout addresses stay the deterministic test addresses (they only receive testnet USDC). |

`SimChainSigner` implements the `FacilitatorEvmSigner` methods the SDK scheme uses for an EOA payer: `get_code` (USDC "exists", every other address is an EOA), `read_contract` for `transferWithAuthorization` simulation with FiatTokenV2 revert messages (nonce used, balance, signature, time window), `balanceOf`, `authorizationState`, `name`, `version`, then `write_contract`, `wait_for_transaction_receipt` (with a real ERC-20 `Transfer` log so the SDK's `verify_eip3009_transfer_event` passes), `get_balance`, `get_chain_id`. Smart wallet paths (ERC-1271, ERC-6492, multicall) are not emulated and fail closed.

The offline treasury starts with 5,000,000 simulated USDC.

## Decisions worth knowing

- SDK spend controls stay on. `x402ClientSync.set_spend_controls({"max_amount_per_payment": "$10000"})`. The SDK default is $1, which would block every real purchase. $10,000 is the largest single payment the Kestrel world legitimately produces (the $9,000 Lumen reserved half month and the $4,800 Stormvault archive are human approved). Elix's policy engine is the spend control; the SDK cap is a backstop. A test proves a $12,000 challenge is refused by the SDK.
- `pay()` fetches the challenge again and raises `RailError("challenge changed since approval")` if payee, amount, asset, network or scheme differ from the challenge the engine evaluated. It then signs exactly the evaluated requirement (decoded from `challenge.raw_b64`), never the fresh one.
- Nonce reuse is refused twice: the vendor app keeps a used-nonce set (before-verify hook), and the facilitator checks EIP-3009 authorization state on the simulated chain.
- Signature recovery results are cached per (authorization, signature) inside the local facilitator, so verify, simulate and settle share one secp256k1 recovery. The SDK scheme still does its own recovery separately. This keeps a purchase near 50 ms so seeding through the real rail stays fast.
- Offline only overrides: `price_override_cents` and `pay_to_override` query params and the `X-Elix-Pay-To-Override` header, plus `POST /admin/payout` and `POST /admin/reset`. All refused with 403 in live mode.

## Interfaces (spec 10)

- `elix.rails.keys`: `vendor_address(vendor_id, rotation=0)`, `treasury_address(mode=None)`.
- `elix.rails`: `RailRegistry` (`rail(name)`, `mode`, `set_clock(clock)`, `settlement_label`, `rail_for(vendor)`), `make_registry(mode=None, vendor_base_url=None)`, `make_fake_registry()`, `reset_offline_state(vendor_base_url=None)`.
- `elix.rails.artifacts`: `save(work_item_id, **fields)`, `get(work_item_id)`, `reset()`, `x402_view(id)` (shape for `GET /api/work-items/{id}/x402`), `resource_view(id)` (shape for `GET /api/work-items/{id}/resource`).
- `elix.vendors`: `ensure_running(port=8402, host="127.0.0.1")` (port 0 picks a free port), `set_payout`, `reset`, `resource_url`, `catalog`, `free_port`, `stop`.
- Prices come from `elix.world.config.VENDORS` when that module exists (`elix.vendors.pricing.source()` says which table is active), else from a local copy of the spec 03 table.

## Vendor routes (port 8402)

`GET /terrafield/scenes`, `GET /stormvault/archive`, `GET /lumen/spot` (optional `start`, `end` for the usage window), `GET /signalboard/records`, `GET /parsec/queries`, each with `sku` and `qty`. `GET /catalog/{vendor}` (slug or id), `GET /catalog`, `POST /admin/payout {vendor, address}`, `POST /admin/reset`, `GET /admin/state`, `GET /healthz`. Bad orders get 400 before any 402. Delivered bodies are synthetic JSON with `"synthetic": true`.

## Run and test

From the repo root:

```
python -m pytest -q tests/rails tests/agents tests/mcp
python -m elix.vendors --port 8402
```

Verified in the sandbox on 2026-10-24:

- `tests/rails` covers the offline round trip (challenge, SDK signed payload, double verification, simulated settlement, balances moved, resource delivered, artifacts), replay of the same payload refused by the vendor and again by the facilitator, payee change between challenge and pay raising `RailError`, tampered amount and expired authorizations rejected, the SDK spend cap still refusing a $12,000 payment, overrides, catalogs, live mode refusing offline only controls, the fake registry, the bank rail, and prices matching `elix.world.config.VENDORS`.
- The backend seed through the real offline rail takes about 24 seconds (373 x402 purchases, every receipt `sim-` with `signature_verified: true`, simulated treasury spend equal to the receipts' total). The eval through the real rail takes about 33 seconds and keeps its artifacts under the `eval:` namespace.
- `python -m elix demo` on free ports with scripted agents: three requests created, Dev's approval of Forge's request paid over the offline x402 rail, Forge fetched the delivered reservation, Scout's self approval got a 403.

## Live mode on Base Sepolia (cannot be tested in the sandbox)

The sandbox cannot reach Base Sepolia or x402.org. The live path is the same code with a different signer and facilitator. A dry run here gets as far as the facilitator `/supported` call and then reports it unreachable.

Saturday morning, in a terminal at the repo root:

1. Create a test wallet and print its key (keep it out of git):
   `python -c "from eth_account import Account; a=Account.create(); print(a.address); print(a.key.hex())"`
2. Fund the address with Base Sepolia USDC from https://faucet.circle.com (pick Base Sepolia). The payer needs no ETH; the facilitator pays gas for `transferWithAuthorization`.
3. Put these in `.env` at the repo root (or export them):
   `ELIX_RAIL_MODE=live`, `ELIX_WALLET_PRIVATE_KEY=0x...`, optionally `ELIX_FACILITATOR_URL=https://x402.org/facilitator`.
4. Dry run (no signature, shows the 402 challenge, proves the facilitator answered `/supported`):
   `ELIX_RAIL_MODE=live ELIX_WALLET_PRIVATE_KEY=0x... python -m elix.rails.live_check --dry-run`
5. Real payment of one Signalboard record ($0.40 testnet USDC):
   `ELIX_RAIL_MODE=live ELIX_WALLET_PRIVATE_KEY=0x... python -m elix.rails.live_check`
   It prints the transaction hash and a `https://sepolia.basescan.org/tx/...` link. The receipt has `simulated: false`.
6. Optional, pay any other x402 v2 endpoint on Base Sepolia: `python -m elix.rails.live_check --url https://...`.

CDP Server Wallet instead of a raw key: `pip install cdp-sdk`, set `CDP_API_KEY_ID`, `CDP_API_KEY_SECRET`, `CDP_WALLET_SECRET` and leave `ELIX_WALLET_PRIVATE_KEY` empty. `keys.treasury_account("live")` then calls `CdpClient().evm.get_or_create_account(name=ELIX_CDP_ACCOUNT_NAME or "elix-treasury")` and wraps it in cdp-sdk's `EvmLocalAccount`, which the x402 SDK signs with like any `eth_account` account (read from cdp-sdk 1.51.0 source, not run). With CDP keys set and no `ELIX_FACILITATOR_URL`, the vendor app uses the CDP facilitator via `cdp.x402.create_facilitator_config()`. Fund the CDP account's address from the faucet the same way.

If the facilitator rejects a payment, the rail raises `RailError` with the facilitator's reason (for example `invalid_exact_evm_insufficient_balance`).

Live mode is meant for small purchases on stage (Signalboard records, a Parsec pack). The seeded history and the eval must always run offline, because the world's amounts are far above faucet balances.

What could not be verified here: the x402.org facilitator's `/supported` list for `eip155:84532` v2, real settlement, CDP signing and the CDP facilitator auth headers.
