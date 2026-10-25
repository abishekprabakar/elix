"""Keys, local facilitator, fake registry, bank rail, vendor admin rules."""

from __future__ import annotations

import time

import pytest
from eth_utils import is_checksum_address

from elix.contracts import PaymentIntent, RailError, Rail
from elix.rails import make_fake_registry, make_registry
from elix.rails.constants import NETWORK, USDC_ADDRESS, USDC_NAME, USDC_VERSION
from elix.rails.keys import offline_treasury_account, treasury_address, vendor_address


def test_keys_are_deterministic_checksummed_and_distinct():
    a0 = vendor_address("ven_stormvault", 0)
    assert a0 == vendor_address("ven_stormvault")
    assert is_checksum_address(a0)
    a1 = vendor_address("ven_stormvault", 1)
    assert a1 != a0 and is_checksum_address(a1)
    assert vendor_address("ven_lumen") != a0
    assert treasury_address("offline") == offline_treasury_account().address
    assert treasury_address("offline") not in {a0, a1}


def test_constants_match_sdk_default_assets():
    from x402.mechanisms.evm.default_assets import get_default_asset

    asset = get_default_asset("eip155:84532")
    assert NETWORK == "eip155:84532"
    assert USDC_ADDRESS == asset["asset"] == "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
    assert (USDC_NAME, USDC_VERSION) == (asset["name"], asset["version"])


def _signed_payload(pay_to: str, amount: str = "15000000"):
    from x402 import x402ClientSync
    from x402.mechanisms.evm.exact import ExactEvmClientScheme
    from x402.schemas import PaymentRequired, PaymentRequirements

    req = PaymentRequirements(scheme="exact", network=NETWORK, asset=USDC_ADDRESS, amount=amount, pay_to=pay_to,
                              max_timeout_seconds=300, extra={"name": USDC_NAME, "version": USDC_VERSION})
    client = x402ClientSync()
    client.register(NETWORK, ExactEvmClientScheme(offline_treasury_account()))
    client.set_spend_controls({"max_amount_per_payment": "$10000"})
    return client.create_payment_payload(PaymentRequired(accepts=[req])), req


def test_local_facilitator_recovers_signer_and_settles(data_dir):
    from elix.rails import sim_chain
    from elix.rails.local_facilitator import LocalFacilitator, recover_eip3009_signer
    from x402.mechanisms.evm.types import ExactEIP3009Payload

    payee = vendor_address("ven_parsec")
    payload, req = _signed_payload(payee)
    evm = ExactEIP3009Payload.from_dict(payload.payload)
    assert recover_eip3009_signer(evm.authorization, evm.signature, network=NETWORK, asset=USDC_ADDRESS,
                                  name=USDC_NAME, version=USDC_VERSION) == offline_treasury_account().address

    fac = LocalFacilitator()
    v = fac.verify_sync(payload, req)
    assert v.is_valid and v.payer == offline_treasury_account().address
    s = fac.settle_sync(payload, req)
    assert s.success and s.extra["simulated"] is True and s.extra["tx_ref"].startswith("sim-")
    assert sim_chain.get_chain().balance_of(payee) == 15_000_000
    # Second settle of the same authorization fails on nonce reuse.
    again = fac.settle_sync(payload, req)
    assert not again.success and "nonce" in (again.error_reason or "")


def test_local_facilitator_rejects_tampering(data_dir):
    from elix.rails.local_facilitator import LocalFacilitator

    fac = LocalFacilitator()
    payee = vendor_address("ven_parsec")
    payload, req = _signed_payload(payee)

    # Payee in the requirements differs from what was signed.
    other = req.model_copy(update={"pay_to": vendor_address("ven_parsec", 1)})
    assert not fac.verify_sync(payload, other).is_valid

    # Amount edited after signing: the signature no longer recovers to the payer.
    forged = payload.model_copy(deep=True)
    forged.payload["authorization"]["value"] = "99000000"
    forged.accepted.amount = "99000000"
    req99 = req.model_copy(update={"amount": "99000000"})
    v = fac.verify_sync(forged, req99)
    assert not v.is_valid and "signature" in (v.invalid_reason or "")

    # Expired authorization.
    stale = payload.model_copy(deep=True)
    stale.payload["authorization"]["validBefore"] = str(int(time.time()) - 10)
    assert not fac.verify_sync(stale, req).is_valid


def test_fake_registry_honors_overrides_and_detects_changes():
    reg = make_fake_registry()
    assert reg.mode == "offline" and reg.kind == "fake"
    x = reg.rail("x402")
    assert isinstance(x, Rail)
    intent = PaymentIntent(work_item_id="WI-1", vendor_id="ven_terrafield", sku="scene-10m", quantity=2,
                           amount_cents=3600)
    ch = x.challenge(intent)
    assert ch.amount_cents == 3600 and ch.pay_to == vendor_address("ven_terrafield")
    r = x.pay(intent, ch)
    assert r.simulated and r.tx_ref.startswith("fake-")

    moved = intent.model_copy(update={"pay_to_override": vendor_address("ven_terrafield", 1)})
    assert x.challenge(moved).pay_to == vendor_address("ven_terrafield", 1)
    with pytest.raises(RailError, match="challenge changed since approval"):
        x.pay(moved, ch)

    # price_override_cents and pay_to_override in resource_url are honored too
    url = "http://127.0.0.1:8402/terrafield/scenes?sku=scene-10m&qty=2&price_override_cents=5000"
    assert x.challenge(intent.model_copy(update={"resource_url": url})).amount_cents == 5000

    b = reg.rail("bank")
    assert b.challenge(intent) is None
    br = b.pay(intent.model_copy(update={"vendor_id": "ven_aws", "amount_cents": 1000}), None)
    assert br.rail == "bank" and br.simulated and br.tx_ref.startswith("ach-")


def test_bank_rail_is_always_simulated(data_dir):
    reg = make_registry("offline", "http://127.0.0.1:1")
    bank = reg.rail("bank")
    r = bank.pay(PaymentIntent(work_item_id="WI-9", vendor_id="ven_anthropic", amount_cents=1234500), None)
    assert r.rail == "bank" and r.simulated is True and r.tx_ref.startswith("ach-")
    with pytest.raises(RailError):
        bank.pay(PaymentIntent(work_item_id="WI-9", vendor_id="ven_anthropic", amount_cents=0), None)


def test_registry_shape_and_clock(data_dir):
    reg = make_registry("offline", "http://127.0.0.1:1")
    assert reg.mode == "offline" and reg.settlement_label == "Offline simulation"
    assert reg.rail("x402").name == "x402" and reg.rail("bank").name == "bank"
    reg.set_clock(lambda: "2026-10-09T12:00:00Z")
    r = reg.rail("bank").pay(PaymentIntent(work_item_id="WI-10", vendor_id="ven_aws", amount_cents=100), None)
    assert r.settled_at == "2026-10-09T12:00:00Z"
    with pytest.raises(KeyError):
        reg.rail("card")  # type: ignore[arg-type]


def test_unreachable_vendor_is_a_rail_error(data_dir):
    x = make_registry("offline", "http://127.0.0.1:9").rail("x402")
    with pytest.raises(RailError, match="not reachable"):
        x.challenge(PaymentIntent(work_item_id="WI-11", vendor_id="ven_parsec", sku="query-1k", quantity=1,
                                  amount_cents=500))


def test_live_mode_vendor_app_refuses_offline_only_controls(data_dir):
    from fastapi.testclient import TestClient

    from elix.rails.local_facilitator import LocalFacilitator
    from elix.vendors.app import create_app

    app = create_app(mode="live", facilitator=LocalFacilitator())
    with TestClient(app) as c:
        assert c.post("/admin/payout", json={"vendor": "stormvault", "address": vendor_address("ven_stormvault", 1)}).status_code == 403
        assert c.get("/parsec/queries?sku=query-1k&qty=1&price_override_cents=1").status_code == 403
        r = c.get("/parsec/queries?sku=query-1k&qty=1")
        assert r.status_code == 402 and r.headers.get("PAYMENT-REQUIRED")


def test_pricing_reads_vendor_master():
    from elix.vendors import pricing

    assert pricing.price_cents("ven_lumen", "h100-spot-hr", 600) == 126000
    assert pricing.price_cents("ven_stormvault", "archive-tx-2025", 1) == 480000
    assert pricing.price_cents("ven_parsec", "query-1k", 3) == 1500
    assert pricing.price_cents("ven_signalboard", "firmo-record", 1200) == 48000
    assert pricing.cents_to_atomic(126000) == "1260000000"
    assert pricing.atomic_to_cents("1260000000") == 126000
    with pytest.raises(pricing.PricingError):
        pricing.price_cents("ven_parsec", "nope", 1)


def test_registry_namespace_keeps_eval_artifacts_apart(fresh_vendor):
    from elix.rails import artifacts

    demo = make_registry("offline", fresh_vendor).rail("x402")
    ev = make_registry("offline", fresh_vendor, namespace="eval").rail("x402")
    intent = PaymentIntent(work_item_id="WI-0001", vendor_id="ven_parsec", sku="query-1k", quantity=1, amount_cents=500)
    demo.pay(intent, demo.challenge(intent))
    ev.pay(intent, ev.challenge(intent))
    a, b = artifacts.get("WI-0001"), artifacts.get("eval:WI-0001")
    assert a and b and a["payload_sha256"] != b["payload_sha256"]


def test_vendor_prices_and_payouts_match_world_config():
    """Spec 10: the vendor app reads VENDORS from elix.world.config, and the master uses vendor_address(v, 0)."""
    world = pytest.importorskip("elix.world.config")
    from elix.vendors import pricing

    assert pricing.source() == "elix.world.config"
    for v in world.VENDORS:
        if "x402" not in v.rails:
            continue
        assert v.payout_address == vendor_address(v.id, 0)
        for s in v.skus:
            assert pricing.price_cents(v.id, s.sku, 1) == s.unit_price_cents
