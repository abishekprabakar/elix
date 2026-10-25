"""Live Base Sepolia check for the x402 rail. Run it Saturday morning; it cannot run in the sandbox.

    ELIX_RAIL_MODE=live ELIX_WALLET_PRIVATE_KEY=0x... python -m elix.rails.live_check

It starts the vendor app in live mode on a free local port (its resource server
talks to ELIX_FACILITATOR_URL, default https://x402.org/facilitator), asks for one
Signalboard record ($0.40), shows the 402 challenge, then pays it with a real
signed EIP-3009 authorization. The facilitator verifies and settles on Base
Sepolia and returns a real transaction hash.

Flags: --dry-run (challenge only, nothing is signed), --vendor, --sku, --qty, --url
(pay any external x402 v2 resource on Base Sepolia instead of the local vendor).
"""

from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m elix.rails.live_check")
    p.add_argument("--vendor", default="ven_signalboard")
    p.add_argument("--sku", default="firmo-record")
    p.add_argument("--qty", type=float, default=1)
    p.add_argument("--url", default=None, help="an external x402 v2 resource URL to pay instead")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    from elix.cli import load_dotenv

    load_dotenv()
    if (os.environ.get("ELIX_RAIL_MODE") or "").lower() != "live":
        print("Set ELIX_RAIL_MODE=live to run the live check.", file=sys.stderr)
        return 2
    from elix.rails.keys import cdp_configured, treasury_account

    if not os.environ.get("ELIX_WALLET_PRIVATE_KEY") and not cdp_configured():
        print("Set ELIX_WALLET_PRIVATE_KEY to a funded Base Sepolia test wallet key (or the CDP_* variables).",
              file=sys.stderr)
        return 2

    from elix import vendors
    from elix.contracts import PaymentIntent, RailError
    from elix.rails import make_registry

    account = treasury_account("live")
    print(f"Payer (live treasury): {account.address}")
    print(f"Facilitator: {os.environ.get('ELIX_FACILITATOR_URL') or 'https://x402.org/facilitator'}")

    if args.url:
        base = None
        url = args.url
    else:
        base = vendors.ensure_running(port=0, mode="live")
        url = vendors.resource_url(args.vendor, args.sku, args.qty, base_url=base)
    print(f"Resource: {url}")

    rail = make_registry("live", base).rail("x402")
    intent = PaymentIntent(work_item_id="LIVE-CHECK", vendor_id=args.vendor, sku=args.sku, quantity=args.qty,
                           amount_cents=0, resource_url=url)
    try:
        ch = rail.challenge(intent)
    except RailError as exc:
        print(f"Challenge failed: {exc}", file=sys.stderr)
        return 1
    print(f"402 challenge: pay {ch.amount_cents / 100:.2f} USDC ({ch.amount_atomic} atomic) to {ch.pay_to} "
          f"on {ch.network}, asset {ch.asset}")
    if args.dry_run:
        print("Dry run: nothing was signed.")
        return 0
    try:
        receipt = rail.pay(intent, ch)
    except RailError as exc:
        print(f"Payment failed: {exc}", file=sys.stderr)
        return 1
    print(f"Paid. simulated={receipt.simulated} tx={receipt.tx_ref}")
    print(f"https://sepolia.basescan.org/tx/{receipt.tx_ref}")
    print(f"payload sha256 {receipt.payload_sha256}, resource sha256 {receipt.resource_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
