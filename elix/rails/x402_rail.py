"""X402Rail: pays x402 resources with the official x402 Python SDK (protocol v2).

challenge(intent): GET the resource without payment, expect 402, decode the
PAYMENT-REQUIRED header with the SDK, pick the exact scheme on Base Sepolia and
return a PaymentChallenge.

pay(intent, challenge): fetch the challenge again and refuse with
RailError("challenge changed since approval") if the payee, amount, asset,
network or scheme differ from what the engine evaluated. Otherwise sign an
EIP-3009 authorization for exactly the evaluated challenge with x402ClientSync
and ExactEvmScheme, retry the GET with the PAYMENT-SIGNATURE header, require a
200, decode PAYMENT-RESPONSE and return a Receipt.

The SDK client keeps its spend controls on. max_amount_per_payment is set to
Elix's largest allowed single payment instead of the SDK's $1 default, because
Elix's policy engine is the spend control and the gateway only calls pay() for
approved items.
"""

from __future__ import annotations

import hashlib
import json
import threading
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from x402 import NoMatchingRequirementsError, PaymentError, x402ClientSync
from x402.http import PAYMENT_REQUIRED_HEADER, PAYMENT_RESPONSE_HEADER, x402HTTPClientSync
from x402.mechanisms.evm.exact import ExactEvmClientScheme
from x402.schemas import PaymentPayload, PaymentRequired, PaymentRequirements, SettleResponse

from elix.contracts import PaymentChallenge, PaymentIntent, RailError, Receipt
from elix.rails import artifacts
from elix.rails.constants import (
    MAX_SINGLE_PAYMENT_USD,
    NETWORK,
    SCHEME,
    USDC_ADDRESS,
    now_iso,
)
from elix.rails.keys import treasury_account

PAY_TO_OVERRIDE_HEADER = "X-Elix-Pay-To-Override"   # offline demo and eval only


def _truncate_sig(sig: str | None) -> str | None:
    if not sig or len(sig) < 20:
        return sig
    return f"{sig[:10]}...{sig[-8:]}"


def payload_sha256(payload: PaymentPayload) -> str:
    """sha256 of the payload JSON exactly as the PAYMENT-SIGNATURE header carries it (before base64)."""
    return hashlib.sha256(payload.model_dump_json(by_alias=True, exclude_none=True).encode("utf-8")).hexdigest()


def _with_query(url: str, **params: Any) -> str:
    parts = urlparse(url)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    for k, v in params.items():
        if v is not None:
            q[k] = str(v)
    return urlunparse(parts._replace(query=urlencode(q)))


def _atomic_to_cents(atomic: str) -> int:
    return (int(atomic) + 5_000) // 10_000


class X402Rail:
    name = "x402"

    def __init__(self, mode: str = "offline", vendor_base_url: str | None = None,
                 http_client: httpx.Client | None = None, account: Any = None, timeout: float = 30.0,
                 namespace: str | None = None) -> None:
        self.mode = mode
        # Artifacts are keyed by work item id. A separate engine (the eval) passes a namespace so its
        # WI ids never overwrite the demo's artifacts in rails.db.
        self.namespace = namespace
        self.vendor_base_url = vendor_base_url
        self._registry = None
        self._http = http_client or httpx.Client(timeout=timeout, follow_redirects=False)
        self._account = account
        self._lock = threading.Lock()
        self._client: x402ClientSync | None = None
        self._http_x402: x402HTTPClientSync | None = None

    # ------------------------------------------------------------ SDK setup

    @property
    def account(self):
        if self._account is None:
            self._account = treasury_account(self.mode)
        return self._account

    @property
    def payer_address(self) -> str:
        return self.account.address

    def _sdk(self) -> tuple[x402ClientSync, x402HTTPClientSync]:
        with self._lock:
            if self._client is None:
                client = x402ClientSync()
                # ExactEvmScheme wraps an eth_account LocalAccount (or any BaseAccount, such as a CDP
                # EvmLocalAccount) in the SDK's EthAccountSigner.
                client.register(NETWORK, ExactEvmClientScheme(self.account))
                client.set_spend_controls({"max_amount_per_payment": MAX_SINGLE_PAYMENT_USD})
                self._client = client
                self._http_x402 = x402HTTPClientSync(client)
            return self._client, self._http_x402  # type: ignore[return-value]

    def _clock(self):
        return getattr(self._registry, "clock", None)

    def artifact_key(self, work_item_id: str) -> str:
        return f"{self.namespace}:{work_item_id}" if self.namespace else work_item_id

    def _save(self, work_item_id: str, **fields: Any) -> None:
        artifacts.save(self.artifact_key(work_item_id), **fields)

    # ------------------------------------------------------------ helpers

    def _url(self, intent: PaymentIntent) -> str:
        if intent.resource_url:
            url = intent.resource_url
        else:
            if not intent.sku or intent.quantity is None:
                raise RailError(f"{intent.work_item_id} has no resource_url and no sku and quantity to build one")
            from elix.vendors import resource_url

            url = resource_url(intent.vendor_id, intent.sku, intent.quantity, base_url=self.vendor_base_url)
        if self.mode == "offline" and intent.pay_to_override:
            url = _with_query(url, pay_to_override=intent.pay_to_override)
        return url

    def _get(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        try:
            return self._http.get(url, headers=headers or {})
        except httpx.HTTPError as exc:
            raise RailError(f"The x402 vendor at {url} is not reachable: {exc}") from exc

    def _decode_402(self, resp: httpx.Response) -> tuple[PaymentRequired, str]:
        _client, http = self._sdk()
        header = resp.headers.get(PAYMENT_REQUIRED_HEADER) or ""
        try:
            body = resp.content or None
            pr = http.get_payment_required_response(lambda name: resp.headers.get(name), body)
        except Exception as exc:
            raise RailError(f"The 402 response could not be decoded with the x402 SDK: {exc}") from exc
        if not isinstance(pr, PaymentRequired):
            raise RailError("The vendor answered with x402 protocol v1. Elix pays x402 v2 only.")
        return pr, header

    @staticmethod
    def _select(pr: PaymentRequired) -> PaymentRequirements:
        for req in pr.accepts:
            if req.scheme == SCHEME and req.network == NETWORK:
                return req
        offered = ", ".join(f"{r.scheme} on {r.network}" for r in pr.accepts) or "nothing"
        raise RailError(f"The vendor does not accept the exact scheme on {NETWORK}. It offered {offered}.")

    def _to_challenge(self, pr: PaymentRequired, req: PaymentRequirements, raw_b64: str, url: str) -> PaymentChallenge:
        resource = (pr.resource.url if pr.resource and pr.resource.url else url)
        description = (pr.resource.description if pr.resource and pr.resource.description else "") or ""
        return PaymentChallenge(
            scheme=req.scheme,
            network=req.network,
            asset=req.asset,
            pay_to=req.pay_to,
            amount_atomic=str(req.amount),
            amount_cents=_atomic_to_cents(req.amount),
            resource=resource,
            description=description,
            max_timeout_seconds=int(req.max_timeout_seconds),
            raw_b64=raw_b64,
            received_at=now_iso(self._clock()),
        )

    def _fetch_challenge(self, url: str) -> tuple[PaymentChallenge, PaymentRequired, PaymentRequirements]:
        resp = self._get(url)
        if resp.status_code != 402:
            detail = resp.text[:300]
            raise RailError(f"Expected 402 Payment Required from {url}, got HTTP {resp.status_code}. {detail}")
        pr, raw = self._decode_402(resp)
        req = self._select(pr)
        if req.asset.lower() != USDC_ADDRESS.lower():
            raise RailError(f"The vendor asks for asset {req.asset}, which is not USDC on Base Sepolia.")
        return self._to_challenge(pr, req, raw, url), pr, req

    # ------------------------------------------------------------ Rail protocol

    def challenge(self, intent: PaymentIntent) -> PaymentChallenge:
        url = self._url(intent)
        challenge, pr, _req = self._fetch_challenge(url)
        self._save(
            intent.work_item_id,
            challenge_decoded=pr.model_dump(by_alias=True, exclude_none=True),
            resource_url=url,
            simulated=self.mode != "live",
            mode=self.mode,
        )
        return challenge

    def pay(self, intent: PaymentIntent, challenge: PaymentChallenge | None) -> Receipt:
        if challenge is None:
            raise RailError("An x402 payment needs the challenge the engine evaluated.")
        if not challenge.raw_b64:
            raise RailError("The evaluated challenge has no raw PAYMENT-REQUIRED header, so it cannot be signed.")
        url = self._url(intent)
        client, http = self._sdk()

        # 1. The challenge the engine evaluated, decoded with the SDK.
        try:
            from x402.http.utils import decode_payment_required_header

            evaluated = decode_payment_required_header(challenge.raw_b64)
        except Exception as exc:
            raise RailError(f"The evaluated challenge could not be decoded: {exc}") from exc
        if not isinstance(evaluated, PaymentRequired):
            raise RailError("The evaluated challenge is not x402 protocol v2.")
        evaluated_req = self._select(evaluated)

        # 2. What the vendor asks for right now. Refuse if anything that moves money changed.
        current, current_pr, current_req = self._fetch_challenge(url)
        changed = {
            field: (getattr(challenge, field), getattr(current, field))
            for field in ("pay_to", "amount_atomic", "asset", "network", "scheme")
            if str(getattr(challenge, field)).lower() != str(getattr(current, field)).lower()
        }
        if (evaluated_req.pay_to.lower() != challenge.pay_to.lower()
                or str(evaluated_req.amount) != str(challenge.amount_atomic)):
            changed["raw_b64"] = ("evaluated header", "does not match the parsed challenge")
        if changed:
            self._save(
                intent.work_item_id,
                refused={
                    "reason": "challenge changed since approval",
                    "changed": {k: {"evaluated": v[0], "now": v[1]} for k, v in changed.items()},
                    "challenge_now": current_pr.model_dump(by_alias=True, exclude_none=True),
                    "at": now_iso(self._clock()),
                },
            )
            raise RailError("challenge changed since approval")

        # 3. Sign an EIP-3009 authorization for exactly the evaluated requirement.
        to_sign = evaluated.model_copy(update={"accepts": [evaluated_req]})
        try:
            payload = client.create_payment_payload(to_sign)
        except NoMatchingRequirementsError as exc:
            raise RailError(f"The x402 client refused to sign: {exc}") from exc
        except PaymentError as exc:
            raise RailError(f"The x402 client could not create the payment: {exc}") from exc
        if not isinstance(payload, PaymentPayload):
            raise RailError("The x402 client produced a v1 payload, expected v2.")
        p_sha = payload_sha256(payload)
        payload_view = payload.model_dump(by_alias=True, exclude_none=True)
        if isinstance(payload_view.get("payload"), dict) and "signature" in payload_view["payload"]:
            payload_view["payload"]["signature"] = _truncate_sig(payload_view["payload"]["signature"])

        # 4. Retry the request with the PAYMENT-SIGNATURE header.
        headers = http.encode_payment_signature_header(payload)
        resp = self._get(url, headers=headers)
        settle: SettleResponse | None = None
        if resp.headers.get(PAYMENT_RESPONSE_HEADER):
            try:
                settle = http.get_payment_settle_response(lambda name: resp.headers.get(name))
            except Exception:
                settle = None
        settle_view = settle.model_dump(by_alias=True, exclude_none=True) if settle else None
        self._save(
            intent.work_item_id,
            payload_decoded=payload_view,
            payload_sha256=p_sha,
            payment_response_decoded=settle_view,
            http_status=resp.status_code,
        )

        if resp.status_code != 200:
            reason = None
            if settle is not None:
                reason = settle.error_reason or settle.error_message
            elif resp.headers.get(PAYMENT_REQUIRED_HEADER):
                try:
                    reason = self._decode_402(resp)[0].error
                except RailError:
                    reason = None
            raise RailError(
                f"The vendor did not accept the payment (HTTP {resp.status_code})"
                + (f": {reason}" if reason else ".")
            )
        if settle is None or not settle.success:
            raise RailError("The vendor returned 200 without a successful PAYMENT-RESPONSE settlement.")

        body = resp.content
        resource_sha = hashlib.sha256(body).hexdigest()
        try:
            resource_json: Any = json.loads(body)
        except ValueError:
            resource_json = {"raw": body.decode("utf-8", "replace")}

        extra = settle.extra or {}
        # Offline settlement is always simulated, whatever the response says. In live mode a facilitator
        # could only make it look simulated, never the other way around.
        simulated = True if self.mode != "live" else bool(extra.get("simulated", False))
        if simulated:
            tx_ref = extra.get("tx_ref") or ("sim-" + p_sha[:16])
            signature_verified = bool(extra.get("signature_verified", True))
        else:
            tx_ref = settle.transaction
            # A real facilitator only settles after it verified the signature.
            signature_verified = True
        settled_at = now_iso(self._clock())
        self._save(
            intent.work_item_id,
            resource_json=resource_json,
            resource_sha256=resource_sha,
            simulated=simulated,
            signature_verified=signature_verified,
            tx_ref=tx_ref,
            settled_at=settled_at,
        )
        return Receipt(
            rail="x402",
            simulated=simulated,
            network=settle.network or challenge.network,
            tx_ref=tx_ref,
            payer=settle.payer or self.payer_address,
            payee=evaluated_req.pay_to,
            amount_cents=challenge.amount_cents,
            amount_atomic=str(evaluated_req.amount),
            payload_sha256=p_sha,
            signature_verified=signature_verified,
            settled_at=settled_at,
            resource_delivered=True,
            resource_sha256=resource_sha,
        )
