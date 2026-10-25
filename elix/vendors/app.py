"""One FastAPI app that serves every x402 vendor in the Kestrel world.

Paid routes are protected by the official x402 FastAPI middleware
(x402.http.middleware.fastapi.payment_middleware) with a dynamic price
(contract unit price x qty) and a dynamic payTo (the vendor's current payout
address) per request. The resource server talks to a facilitator:

- offline: elix.rails.local_facilitator.LocalFacilitator (real signature
  recovery, simulated settlement on the local USDC ledger)
- live: the SDK's HTTPFacilitatorClient pointed at ELIX_FACILITATOR_URL
  (default https://x402.org/facilitator) on Base Sepolia

Offline only: price_override_cents and pay_to_override query params (and the
X-Elix-Pay-To-Override header) let the eval and demos simulate a vendor asking
for a different amount or payee. POST /admin/payout changes a vendor's payout
address (payee-change moment). POST /admin/reset restores rotation 0 payouts
and clears used nonces. All of these return 403 in live mode.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from x402 import AbortResult, x402ResourceServer
from x402.http.types import HTTPResponseBody, PaymentOption, RouteConfig
from x402.mechanisms.evm.exact import ExactEvmServerScheme
from x402.mechanisms.evm.types import ExactEIP3009Payload
from x402.schemas import AssetAmount

from elix.rails.constants import (
    DEFAULT_FACILITATOR_URL,
    NETWORK,
    SCHEME,
    USDC_ADDRESS,
    USDC_NAME,
    USDC_VERSION,
)
from elix.rails.keys import vendor_address
from elix.vendors import catalogs, pricing

PAY_TO_OVERRIDE_HEADER = "x-elix-pay-to-override"
MAX_TIMEOUT_SECONDS = 300


def _is_address(value: str) -> bool:
    from eth_utils import is_address

    return bool(value) and is_address(value)


def _checksum(value: str) -> str:
    from eth_utils import to_checksum_address

    return to_checksum_address(value)


class VendorState:
    """Mutable vendor server state: payout addresses, used nonces, delivery log."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.lock = threading.Lock()
        self.payouts: dict[str, str] = {}
        self.payout_changed_at: dict[str, str | None] = {}
        self.used_nonces: set[tuple[str, str]] = set()
        self.deliveries: list[dict[str, Any]] = []
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self.payouts = {vid: vendor_address(vid, 0) for vid in pricing.VENDOR_PATHS}
            self.payout_changed_at = {vid: None for vid in pricing.VENDOR_PATHS}
            self.used_nonces.clear()
            self.deliveries.clear()


def _query(ctx_or_request: Any, name: str) -> str | None:
    adapter = getattr(ctx_or_request, "adapter", None)
    if adapter is not None:
        v = adapter.get_query_param(name)
    else:
        v = ctx_or_request.query_params.get(name)
    if isinstance(v, list):
        v = v[-1] if v else None
    return v


def _header(ctx_or_request: Any, name: str) -> str | None:
    adapter = getattr(ctx_or_request, "adapter", None)
    if adapter is not None:
        return adapter.get_header(name)
    return ctx_or_request.headers.get(name)


def _make_facilitator(mode: str, facilitator: Any = None) -> Any:
    if facilitator is not None:
        return facilitator
    if mode == "offline":
        from elix.rails.local_facilitator import LocalFacilitator

        return LocalFacilitator()
    from x402.http import FacilitatorConfig, HTTPFacilitatorClient

    url = os.environ.get("ELIX_FACILITATOR_URL") or ""
    cdp_keys = os.environ.get("CDP_API_KEY_ID") and os.environ.get("CDP_API_KEY_SECRET")
    if (not url or "api.cdp.coinbase.com" in url) and cdp_keys:
        try:
            from cdp.x402 import create_facilitator_config  # type: ignore[import-not-found]

            cfg = create_facilitator_config()
            return HTTPFacilitatorClient({"url": cfg["url"], "create_headers": cfg["create_headers"]})
        except ImportError:
            pass
    return HTTPFacilitatorClient(FacilitatorConfig(url=url or DEFAULT_FACILITATOR_URL))


def create_app(mode: str | None = None, facilitator: Any = None) -> FastAPI:
    """Build the vendor app. mode defaults to ELIX_RAIL_MODE (offline)."""
    from x402.http.middleware.fastapi import payment_middleware

    mode = (mode or os.environ.get("ELIX_RAIL_MODE") or "offline").strip().lower()
    if mode not in ("offline", "live"):
        mode = "offline"
    state = VendorState(mode)
    fac = _make_facilitator(mode, facilitator)

    server = x402ResourceServer(fac)
    server.register(NETWORK, ExactEvmServerScheme())

    # Vendor-side nonce reuse protection, on top of the facilitator's own check.
    def before_verify(ctx: Any) -> AbortResult | None:
        try:
            auth = ExactEIP3009Payload.from_dict(ctx.payment_payload.payload).authorization
        except Exception:
            return None
        key = (auth.from_address.lower(), auth.nonce.lower())
        with state.lock:
            if key in state.used_nonces:
                return AbortResult(reason="nonce_already_used",
                                   message="This payment authorization was already used at this vendor.")
        return None

    def after_settle(ctx: Any) -> None:
        try:
            auth = ExactEIP3009Payload.from_dict(ctx.payment_payload.payload).authorization
        except Exception:
            return None
        with state.lock:
            state.used_nonces.add((auth.from_address.lower(), auth.nonce.lower()))
        return None

    server.on_before_verify(before_verify)
    server.on_after_settle(after_settle)

    def make_price(vendor_id: str):
        def price(ctx: Any) -> AssetAmount:
            sku = _query(ctx, "sku") or ""
            qty = float(_query(ctx, "qty") or "0")
            override = _query(ctx, "price_override_cents") if mode == "offline" else None
            cents = pricing.price_cents(vendor_id, sku, qty, int(override) if override else None)
            return AssetAmount(
                amount=pricing.cents_to_atomic(cents),
                asset=USDC_ADDRESS,
                extra={"name": USDC_NAME, "version": USDC_VERSION},
            )
        return price

    def make_pay_to(vendor_id: str):
        def pay_to(ctx: Any) -> str:
            if mode == "offline":
                override = _query(ctx, "pay_to_override") or _header(ctx, PAY_TO_OVERRIDE_HEADER)
                if override:
                    return _checksum(override)
            with state.lock:
                return state.payouts[vendor_id]
        return pay_to

    def make_unpaid_body(vendor_id: str):
        name = pricing.vendor_table()[vendor_id].name

        def unpaid(ctx: Any) -> HTTPResponseBody:
            return HTTPResponseBody(
                content_type="application/json",
                body={"error": "Payment required", "vendor": name, "vendor_id": vendor_id,
                      "protocol": "x402 v2", "see_header": "PAYMENT-REQUIRED", "synthetic": True},
            )
        return unpaid

    routes: dict[str, RouteConfig] = {}
    for vendor_id, path in pricing.VENDOR_PATHS.items():
        vend = pricing.vendor_table()[vendor_id]
        routes[f"GET {path}"] = RouteConfig(
            accepts=PaymentOption(
                scheme=SCHEME,
                pay_to=make_pay_to(vendor_id),
                price=make_price(vendor_id),
                network=NETWORK,
                max_timeout_seconds=MAX_TIMEOUT_SECONDS,
            ),
            description=f"{vend.category} from {vend.name} (synthetic demo vendor)",
            mime_type="application/json",
            service_name=vend.name[:32],
            unpaid_response_body=make_unpaid_body(vendor_id),
        )

    app = FastAPI(title="Elix demo x402 vendors", version="1.0")
    app.state.vendor_state = state
    app.state.facilitator = fac
    app.state.resource_server = server
    app.state.mode = mode

    x402_mw = payment_middleware(routes, server)

    @app.middleware("http")
    async def x402_payment(request: Request, call_next):
        try:
            return await x402_mw(request, call_next)
        except Exception as exc:  # facilitator unreachable during lazy initialization (live mode)
            where = getattr(fac, "url", None) or type(fac).__name__
            return JSONResponse(
                {"error": f"The x402 facilitator at {where} is not reachable: {type(exc).__name__}: {exc}"},
                status_code=503,
            )

    path_to_vendor = {p: v for v, p in pricing.VENDOR_PATHS.items()}

    # Added after the x402 middleware, so it runs first: reject bad orders before any 402.
    @app.middleware("http")
    async def validate_order(request: Request, call_next):
        vendor_id = path_to_vendor.get(request.url.path)
        if vendor_id is None or request.method != "GET":
            return await call_next(request)
        q = request.query_params
        sku = q.get("sku") or ""
        try:
            qty = float(q.get("qty") or "")
        except ValueError:
            return JSONResponse({"error": "qty must be a number"}, status_code=400)
        override = q.get("price_override_cents")
        if (override or q.get("pay_to_override") or request.headers.get(PAY_TO_OVERRIDE_HEADER)) and mode != "offline":
            return JSONResponse({"error": "Overrides are only available in offline mode"}, status_code=403)
        try:
            pricing.price_cents(vendor_id, sku, qty, int(override) if override else None)
        except (pricing.PricingError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        pto = q.get("pay_to_override") or request.headers.get(PAY_TO_OVERRIDE_HEADER)
        if pto and not _is_address(pto):
            return JSONResponse({"error": "pay_to_override must be an EVM address"}, status_code=400)
        return await call_next(request)

    def _deliver(request: Request, vendor_id: str) -> JSONResponse:
        reqs = getattr(request.state, "payment_requirements", None)
        payload = getattr(request.state, "payment_payload", None)
        if reqs is None or payload is None:  # cannot happen behind the middleware
            return JSONResponse({"error": "Payment required"}, status_code=402)
        q = request.query_params
        sku = q.get("sku") or ""
        qty = float(q.get("qty") or "0")
        amount_cents = pricing.atomic_to_cents(reqs.amount)
        params = {k: v for k, v in q.items() if k not in ("sku", "qty", "price_override_cents", "pay_to_override")}
        body = catalogs.deliver(vendor_id, sku, qty, params, amount_cents, reqs.pay_to)
        with state.lock:
            state.deliveries.append({
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "vendor_id": vendor_id, "sku": sku, "qty": qty, "amount_cents": amount_cents,
                "pay_to": reqs.pay_to, "order_id": body.get("order_id"),
            })
            del state.deliveries[:-500]
        return JSONResponse(body)

    def _route(vendor_id: str):
        async def handler(request: Request):
            return _deliver(request, vendor_id)
        handler.__name__ = f"buy_{pricing.VENDOR_SLUGS[vendor_id]}"
        return handler

    for vendor_id, path in pricing.VENDOR_PATHS.items():
        app.add_api_route(path, _route(vendor_id), methods=["GET"], name=f"buy_{pricing.VENDOR_SLUGS[vendor_id]}")

    @app.get("/catalog/{vendor}")
    async def get_catalog(vendor: str, request: Request):
        vendor_id = pricing.SLUG_TO_VENDOR.get(vendor, vendor)
        try:
            return catalogs.catalog(vendor_id, base_url=str(request.base_url))
        except KeyError:
            return JSONResponse({"error": f"Unknown vendor {vendor}"}, status_code=404)

    @app.get("/catalog")
    async def list_catalogs(request: Request):
        return {"vendors": [catalogs.catalog(v, base_url=str(request.base_url)) for v in pricing.VENDOR_PATHS]}

    @app.post("/admin/payout")
    async def admin_payout(request: Request):
        if mode != "offline":
            return JSONResponse({"error": "Payout changes are only available in offline mode"}, status_code=403)
        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"error": "Body must be JSON {vendor, address}"}, status_code=400)
        vendor = str(data.get("vendor") or data.get("vendor_id") or "")
        vendor_id = pricing.SLUG_TO_VENDOR.get(vendor, vendor)
        address = str(data.get("address") or "")
        if vendor_id not in pricing.VENDOR_PATHS:
            return JSONResponse({"error": f"Unknown vendor {vendor}"}, status_code=404)
        if not _is_address(address):
            return JSONResponse({"error": "address must be an EVM address"}, status_code=400)
        with state.lock:
            previous = state.payouts[vendor_id]
            state.payouts[vendor_id] = _checksum(address)
            state.payout_changed_at[vendor_id] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return {"vendor_id": vendor_id, "address": state.payouts[vendor_id], "previous_address": previous,
                "simulated": True}

    @app.post("/admin/reset")
    async def admin_reset():
        if mode != "offline":
            return JSONResponse({"error": "Reset is only available in offline mode"}, status_code=403)
        state.reset()
        return {"ok": True, "payouts": state.payouts}

    @app.get("/admin/state")
    async def admin_state():
        with state.lock:
            return {
                "mode": mode,
                "payouts": dict(state.payouts),
                "payout_changed_at": dict(state.payout_changed_at),
                "used_nonces": len(state.used_nonces),
                "deliveries": list(state.deliveries[-50:]),
            }

    @app.get("/healthz")
    async def healthz():
        return {
            "ok": True,
            "service": "elix-vendors",
            "mode": mode,
            "network": NETWORK,
            "facilitator": getattr(fac, "url", None) or getattr(fac, "identifier", None) or type(fac).__name__,
            "settlement": "simulated" if mode == "offline" else "Base Sepolia via facilitator",
            "vendors": list(pricing.VENDOR_PATHS),
            "pricing_source": pricing.source(),
        }

    return app
