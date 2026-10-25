"""In-memory rails for fast unit tests (make_fake_registry).

No HTTP and no signatures. The x402 challenge is built from the intent and the
vendor price table exactly the way the real vendor app prices a request, so a
fast eval and a real eval see the same amounts and payees. Receipts are marked
simulated and their tx_ref starts with "fake-" so they can never be mistaken for
a signed offline payment ("sim-") or a live one.
"""

from __future__ import annotations

import hashlib
import json
from urllib.parse import parse_qs, urlparse

from elix.contracts import PaymentChallenge, PaymentIntent, RailError, Receipt
from elix.rails.constants import NETWORK, SCHEME, USDC_ADDRESS, now_iso
from elix.rails.keys import treasury_address, vendor_address
from elix.vendors import pricing


def _query(url: str | None) -> dict[str, str]:
    if not url:
        return {}
    return {k: v[-1] for k, v in parse_qs(urlparse(url).query).items() if v}


class FakeX402Rail:
    name = "x402"

    def __init__(self, registry=None) -> None:
        self._registry = registry
        self.payouts: dict[str, str] = {}
        self.paid: list[Receipt] = []

    # mirrors elix.vendors.set_payout / reset for tests that do not run the vendor app
    def set_payout(self, vendor_id: str, address: str) -> None:
        self.payouts[vendor_id] = address

    def reset(self) -> None:
        self.payouts.clear()
        self.paid.clear()

    def _clock(self):
        return getattr(self._registry, "clock", None)

    def _payee(self, intent: PaymentIntent) -> str:
        q = _query(intent.resource_url)
        override = intent.pay_to_override or q.get("pay_to_override")
        if override:
            return override
        return self.payouts.get(intent.vendor_id) or vendor_address(intent.vendor_id, 0)

    def _amount_cents(self, intent: PaymentIntent) -> int:
        q = _query(intent.resource_url)
        override = q.get("price_override_cents")
        if override:
            return int(override)
        sku = intent.sku or q.get("sku")
        qty = intent.quantity if intent.quantity is not None else q.get("qty")
        if sku and qty is not None and pricing.x402_vendor(intent.vendor_id) is not None:
            try:
                return pricing.price_cents(intent.vendor_id, sku, float(qty))
            except pricing.PricingError:
                pass
        return int(intent.amount_cents)

    def challenge(self, intent: PaymentIntent) -> PaymentChallenge:
        cents = self._amount_cents(intent)
        resource = intent.resource_url or f"fake://{intent.vendor_id}/{intent.sku or ''}"
        raw = {
            "fake": True,
            "scheme": SCHEME,
            "network": NETWORK,
            "asset": USDC_ADDRESS,
            "payTo": self._payee(intent),
            "amount": pricing.cents_to_atomic(cents),
            "resource": resource,
        }
        return PaymentChallenge(
            scheme=SCHEME,
            network=NETWORK,
            asset=USDC_ADDRESS,
            pay_to=raw["payTo"],
            amount_atomic=raw["amount"],
            amount_cents=cents,
            resource=resource,
            description="Fake rail challenge for fast tests",
            max_timeout_seconds=300,
            raw_b64="",
            received_at=now_iso(self._clock()),
        )

    def pay(self, intent: PaymentIntent, challenge: PaymentChallenge | None) -> Receipt:
        if challenge is None:
            raise RailError("x402 payment needs the challenge the engine evaluated")
        current = self.challenge(intent)
        if (
            current.pay_to.lower() != challenge.pay_to.lower()
            or current.amount_atomic != challenge.amount_atomic
            or current.asset.lower() != challenge.asset.lower()
            or current.network != challenge.network
        ):
            raise RailError("challenge changed since approval")
        digest = hashlib.sha256(
            json.dumps(
                {"wi": intent.work_item_id, "to": challenge.pay_to, "amount": challenge.amount_atomic},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        resource = {"synthetic": True, "fake_rail": True, "vendor_id": intent.vendor_id, "sku": intent.sku,
                    "quantity": intent.quantity}
        receipt = Receipt(
            rail="x402",
            simulated=True,
            network=challenge.network,
            tx_ref=f"fake-{digest[:16]}",
            payer=treasury_address("offline"),
            payee=challenge.pay_to,
            amount_cents=challenge.amount_cents,
            amount_atomic=challenge.amount_atomic,
            payload_sha256=None,
            signature_verified=None,
            settled_at=now_iso(self._clock()),
            resource_delivered=True,
            resource_sha256=hashlib.sha256(json.dumps(resource, sort_keys=True).encode()).hexdigest(),
        )
        self.paid.append(receipt)
        return receipt


class FakeBankRail:
    name = "bank"

    def __init__(self, registry=None) -> None:
        self._registry = registry
        self.paid: list[Receipt] = []

    def challenge(self, intent: PaymentIntent) -> None:
        return None

    def pay(self, intent: PaymentIntent, challenge: PaymentChallenge | None) -> Receipt:
        if intent.amount_cents <= 0:
            raise RailError("bank payment amount must be positive")
        digest = hashlib.sha256(f"{intent.work_item_id}:{intent.amount_cents}:{len(self.paid)}".encode()).hexdigest()
        receipt = Receipt(
            rail="bank",
            simulated=True,
            network=None,
            tx_ref=f"ach-fake-{digest[:12]}",
            payer="Kestrel Labs operating account (Mercury)",
            payee=intent.vendor_id,
            amount_cents=intent.amount_cents,
            settled_at=now_iso(getattr(self._registry, "clock", None)),
        )
        self.paid.append(receipt)
        return receipt
