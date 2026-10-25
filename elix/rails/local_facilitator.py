"""Local x402 facilitator for offline mode.

It implements the facilitator client interface the x402 resource server expects
(async verify, async settle, sync get_supported; see
x402/http/facilitator_client_base.py FacilitatorClient), so the vendor app uses
the official SDK middleware unchanged and only swaps which facilitator it talks to.

Verification is real and runs twice, independently:

1. Elix's own check: the exact EIP-712 TransferWithAuthorization typed data the
   SDK client signs is rebuilt with the SDK's build_typed_data_for_signing, and
   the signer is recovered with eth_account (Account.recover_message over
   encode_typed_data). It must equal authorization.from. Amount, payee, asset,
   network, validity window and nonce reuse are checked against the
   requirements and the simulated USDC ledger.
2. The SDK's own facilitator scheme (x402.mechanisms.evm.exact.facilitator
   ExactEvmScheme.verify) runs against SimChainSigner, an offline
   FacilitatorEvmSigner whose "chain" is elix/rails/sim_chain.py. The SDK does
   its own signature recovery, recipient, amount, timing and asset checks, then
   simulates transferWithAuthorization, which SimChainSigner answers with
   FiatTokenV2 semantics (nonce state, balance, signature).

Settlement is SIMULATED: the SDK scheme's settle() calls
SimChainSigner.write_contract, which moves balances in the SQLite ledger and
returns a simulated transaction hash ("0x" + sha256 of the payment payload).
The SettleResponse carries extra.simulated = true and the receipt tx_ref
"sim-<first 16 hex of sha256(payload)>".
"""

from __future__ import annotations

import contextvars
import functools
import hashlib
import time
from typing import Any

from eth_account import Account
from eth_account.messages import encode_typed_data
from x402.mechanisms.evm.constants import (
    ERR_AUTHORIZATION_VALUE_MISMATCH,
    ERR_INSUFFICIENT_BALANCE,
    ERR_INVALID_SIGNATURE,
    ERR_MISSING_EIP712_DOMAIN,
    ERR_NETWORK_MISMATCH,
    ERR_NONCE_ALREADY_USED,
    ERR_RECIPIENT_MISMATCH,
    ERR_UNSUPPORTED_SCHEME,
    ERR_VALID_AFTER_FUTURE,
    ERR_VALID_BEFORE_EXPIRED,
)
from x402.mechanisms.evm.eip712 import build_typed_data_for_signing
from x402.mechanisms.evm.exact.facilitator import ExactEvmScheme as ExactEvmFacilitatorScheme
from x402.mechanisms.evm.types import (
    TransactionReceipt,
    TypedDataDomain,
    TypedDataField,
    ExactEIP3009Authorization,
    ExactEIP3009Payload,
)
from x402.mechanisms.evm.utils import get_evm_chain_id
from x402.schemas import (
    PaymentPayload,
    PaymentRequirements,
    SettleResponse,
    SupportedKind,
    SupportedResponse,
    VerifyResponse,
)

from elix.rails.constants import NETWORK, SCHEME, USDC_ADDRESS, USDC_NAME, USDC_VERSION
from elix.rails.keys import _offline_account
from elix.rails.sim_chain import SimChain, SimRevert, get_chain

ERC20_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ERR_ASSET_MISMATCH = "invalid_exact_evm_asset_mismatch"
ERR_REQUIREMENTS_MISMATCH = "invalid_exact_evm_accepted_requirements_mismatch"
SIMULATED_LABEL = "Simulated settlement on the Elix offline USDC ledger. No blockchain was used."

# The payload hash of the settle call in progress, so SimChainSigner.write_contract
# can name the simulated transaction after the signed payload.
_current_payload_sha: contextvars.ContextVar[str | None] = contextvars.ContextVar("elix_payload_sha", default=None)


def payload_sha256(payload: PaymentPayload) -> str:
    """sha256 of the payload exactly as the PAYMENT-SIGNATURE header encodes it (before base64)."""
    raw = payload.model_dump_json(by_alias=True, exclude_none=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def sim_tx_ref(payload_hash: str) -> str:
    return "sim-" + payload_hash[:16]


def facilitator_account():
    """Offline facilitator identity. TEST KEY derived from a public string; it never signs anything."""
    return _offline_account("elix-offline-facilitator")


# ---------------------------------------------------------------- EIP-712 recovery (Elix's own check)

def recover_eip3009_signer(
    authorization: ExactEIP3009Authorization,
    signature_hex: str,
    *,
    network: str,
    asset: str,
    name: str,
    version: str,
) -> str:
    """Recover the address that signed an EIP-3009 authorization, using eth_account.

    The typed data comes from the SDK's build_typed_data_for_signing, which is the
    same function ExactEvmScheme (client) uses before calling sign_typed_data.
    """
    return _recover_cached(
        authorization.from_address, authorization.to, str(authorization.value), str(authorization.valid_after),
        str(authorization.valid_before), authorization.nonce.lower(), signature_hex.lower(), network,
        asset, name, version,
    )


@functools.lru_cache(maxsize=4096)
def _recover_cached(frm: str, to: str, value: str, valid_after: str, valid_before: str, nonce: str,
                    signature_hex: str, network: str, asset: str, name: str, version: str) -> str:
    # Pure function of its inputs, so the facilitator's verify, simulate and settle steps share
    # one secp256k1 recovery per payload instead of repeating it.
    authorization = ExactEIP3009Authorization(
        from_address=frm, to=to, value=value, valid_after=valid_after, valid_before=valid_before, nonce=nonce,
    )
    domain, types, _primary, message = build_typed_data_for_signing(
        authorization, get_evm_chain_id(network), asset, name, version
    )
    domain_dict = {
        "name": domain.name,
        "version": domain.version,
        "chainId": domain.chain_id,
        "verifyingContract": domain.verifying_contract,
    }
    signable = encode_typed_data(domain_data=domain_dict, message_types=types, message_data=message)
    return Account.recover_message(signable, signature=bytes.fromhex(signature_hex.removeprefix("0x")))


# ---------------------------------------------------------------- simulated chain signer for the SDK scheme

class SimChainSigner:
    """FacilitatorEvmSigner backed by the simulated USDC ledger. SIMULATED CHAIN.

    Implements the methods x402's exact EVM facilitator scheme calls for EOA
    payers: get_code, read_contract (transferWithAuthorization simulation and a
    few views), write_contract (settlement), wait_for_transaction_receipt,
    get_balance, get_chain_id, get_addresses, verify_typed_data.
    """

    def __init__(self, chain: SimChain, network: str = NETWORK, asset: str = USDC_ADDRESS,
                 token_name: str = USDC_NAME, token_version: str = USDC_VERSION) -> None:
        self.chain = chain
        self.network = network
        self.asset = asset
        self.token_name = token_name
        self.token_version = token_version
        self.address = facilitator_account().address

    def get_addresses(self) -> list[str]:
        return [self.address]

    def get_chain_id(self) -> int:
        return get_evm_chain_id(self.network)

    def get_code(self, address: str) -> bytes:
        # The USDC contract "exists" on the simulated chain; every other address is an EOA.
        if address.lower() == self.asset.lower():
            return bytes.fromhex("6080604052")  # placeholder bytecode marker for the simulated token
        return b""

    def get_balance(self, address: str, token_address: str) -> int:
        return self.chain.balance_of(address, token_address)

    def verify_typed_data(self, address: str, domain: TypedDataDomain, types: dict[str, list[TypedDataField]],
                          primary_type: str, message: dict[str, Any], signature: bytes) -> bool:
        domain_dict = {
            "name": domain.name,
            "version": domain.version,
            "chainId": domain.chain_id,
            "verifyingContract": domain.verifying_contract,
        }
        types_dict = {k: [{"name": f.name, "type": f.type} for f in v] for k, v in types.items()}
        try:
            signable = encode_typed_data(domain_data=domain_dict, message_types=types_dict, message_data=message)
            return Account.recover_message(signable, signature=signature).lower() == address.lower()
        except Exception:
            return False

    # -- FiatTokenV2 semantics for transferWithAuthorization ------------------

    def _check_transfer_call(self, token: str, args: tuple) -> tuple[str, str, int, str]:
        if token.lower() != self.asset.lower():
            raise SimRevert("execution reverted: unknown token on the simulated chain")
        if len(args) == 9:
            frm, to, value, valid_after, valid_before, nonce, v, r, s = args
            sig = bytes(r) + bytes(s) + bytes([int(v)])
        elif len(args) == 7:
            frm, to, value, valid_after, valid_before, nonce, sig = args
            sig = bytes(sig)
        else:
            raise SimRevert("execution reverted: bad transferWithAuthorization arguments")
        now = int(time.time())
        if now <= int(valid_after):
            raise SimRevert("execution reverted: FiatTokenV2: authorization is not yet valid")
        if now >= int(valid_before):
            raise SimRevert("execution reverted: FiatTokenV2: authorization is expired")
        nonce_hex = "0x" + bytes(nonce).hex() if isinstance(nonce, (bytes, bytearray)) else str(nonce)
        auth = ExactEIP3009Authorization(
            from_address=frm, to=to, value=str(int(value)), valid_after=str(int(valid_after)),
            valid_before=str(int(valid_before)), nonce=nonce_hex,
        )
        try:
            recovered = recover_eip3009_signer(
                auth, "0x" + sig.hex(), network=self.network, asset=self.asset,
                name=self.token_name, version=self.token_version,
            )
        except Exception:
            raise SimRevert("execution reverted: FiatTokenV2: invalid signature") from None
        if recovered.lower() != str(frm).lower():
            raise SimRevert("execution reverted: FiatTokenV2: invalid signature")
        self.chain.check_transfer(frm, int(value), nonce_hex, self.asset)
        return frm, to, int(value), nonce_hex

    def read_contract(self, address: str, abi: list[dict[str, Any]], function_name: str, *args: Any) -> Any:
        if function_name == "transferWithAuthorization":
            self._check_transfer_call(address, args)  # eth_call: revert or succeed, no state change
            return None
        if function_name == "balanceOf":
            return self.chain.balance_of(args[0], address)
        if function_name == "authorizationState":
            return self.chain.authorization_state(args[0], args[1], address)
        if function_name == "name" and address.lower() == self.asset.lower():
            return self.token_name
        if function_name == "version" and address.lower() == self.asset.lower():
            return self.token_version
        raise SimRevert(f"execution reverted: {function_name} is not available on the simulated chain")

    def write_contract(self, address: str, abi: list[dict[str, Any]], function_name: str, *args: Any,
                       data_suffix: str | None = None) -> str:
        if function_name != "transferWithAuthorization":
            raise SimRevert(f"execution reverted: {function_name} is not available on the simulated chain")
        frm, to, value, nonce_hex = self._check_transfer_call(address, args)
        p_sha = _current_payload_sha.get()
        if p_sha is None:
            p_sha = hashlib.sha256(f"{frm}:{to}:{value}:{nonce_hex}".encode()).hexdigest()
        tx_hash = "0x" + p_sha
        self.chain.settle_transfer(
            from_address=frm, to_address=to, value=value, nonce=nonce_hex, tx_hash=tx_hash,
            payload_sha256=_current_payload_sha.get(), asset=address,
        )
        return tx_hash

    def send_transaction(self, to: str, data: bytes) -> str:
        raise SimRevert("execution reverted: raw transactions are not supported on the simulated chain")

    def wait_for_transaction_receipt(self, tx_hash: str) -> TransactionReceipt:
        t = self.chain.transfer(tx_hash)
        if t is None:
            raise RuntimeError(f"simulated transaction {tx_hash} not found")

        def topic(addr: str) -> str:
            return "0x" + "0" * 24 + addr.lower().removeprefix("0x")

        log = {
            "address": self.asset,
            "topics": [ERC20_TRANSFER_TOPIC, topic(t.from_address), topic(t.to_address)],
            "data": "0x" + format(t.value, "064x"),
        }
        return TransactionReceipt(status=1, block_number=t.block_number, tx_hash=t.tx_hash, logs=[log])


# ---------------------------------------------------------------- facilitator client

class LocalFacilitator:
    """FacilitatorClient for the x402 resource server (async verify/settle, sync get_supported)."""

    identifier = "elix-local-facilitator"
    url = "local://elix-facilitator"

    def __init__(self, chain: SimChain | None = None) -> None:
        self._chain_override = chain
        self.verifications: list[dict[str, Any]] = []   # last few results, for debugging and tests

    @property
    def chain(self) -> SimChain:
        return self._chain_override or get_chain()

    def _scheme(self) -> ExactEvmFacilitatorScheme:
        return ExactEvmFacilitatorScheme(SimChainSigner(self.chain))

    def get_supported(self) -> SupportedResponse:
        return SupportedResponse(
            kinds=[SupportedKind(x402_version=2, scheme=SCHEME, network=NETWORK)],
            extensions=[],
            signers={"eip155:*": [facilitator_account().address]},
        )

    # -- Elix's independent verification ------------------------------------

    def independent_verify(self, payload: PaymentPayload, requirements: PaymentRequirements) -> VerifyResponse:
        evm = ExactEIP3009Payload.from_dict(payload.payload)
        auth = evm.authorization
        payer = auth.from_address

        def bad(reason: str, message: str | None = None) -> VerifyResponse:
            return VerifyResponse(is_valid=False, invalid_reason=reason, invalid_message=message, payer=payer)

        if payload.accepted.scheme != SCHEME or requirements.scheme != SCHEME:
            return bad(ERR_UNSUPPORTED_SCHEME)
        if payload.accepted.network != requirements.network or requirements.network != NETWORK:
            return bad(ERR_NETWORK_MISMATCH)
        if requirements.asset.lower() != USDC_ADDRESS.lower() or payload.accepted.asset.lower() != requirements.asset.lower():
            return bad(ERR_ASSET_MISMATCH, "asset is not Base Sepolia USDC")
        if (payload.accepted.pay_to.lower() != requirements.pay_to.lower()
                or payload.accepted.amount != requirements.amount):
            return bad(ERR_REQUIREMENTS_MISMATCH, "accepted terms differ from the server's requirements")
        extra = requirements.extra or {}
        if "name" not in extra or "version" not in extra:
            return bad(ERR_MISSING_EIP712_DOMAIN)
        if auth.to.lower() != requirements.pay_to.lower():
            return bad(ERR_RECIPIENT_MISMATCH)
        if int(auth.value) != int(requirements.amount):
            return bad(ERR_AUTHORIZATION_VALUE_MISMATCH)
        now = int(time.time())
        if int(auth.valid_before) < now + 6:
            return bad(ERR_VALID_BEFORE_EXPIRED)
        if int(auth.valid_after) > now:
            return bad(ERR_VALID_AFTER_FUTURE)
        if not evm.signature:
            return bad(ERR_INVALID_SIGNATURE, "missing signature")
        try:
            nonce_bytes = bytes.fromhex(auth.nonce.removeprefix("0x"))
        except ValueError:
            return bad(ERR_INVALID_SIGNATURE, "nonce is not hex")
        if len(nonce_bytes) != 32:
            return bad(ERR_INVALID_SIGNATURE, "nonce must be 32 bytes")
        try:
            recovered = recover_eip3009_signer(
                auth, evm.signature, network=requirements.network, asset=requirements.asset,
                name=extra["name"], version=extra["version"],
            )
        except Exception as exc:  # malformed signature
            return bad(ERR_INVALID_SIGNATURE, f"signature recovery failed: {exc}")
        if recovered.lower() != payer.lower():
            return bad(ERR_INVALID_SIGNATURE, f"signature recovers to {recovered}, not {payer}")
        if self.chain.authorization_state(payer, auth.nonce, requirements.asset):
            return bad(ERR_NONCE_ALREADY_USED, "this EIP-3009 nonce was already used")
        if self.chain.balance_of(payer, requirements.asset) < int(auth.value):
            return bad(ERR_INSUFFICIENT_BALANCE)
        return VerifyResponse(is_valid=True, payer=recovered, extra={"recovered_signer": recovered})

    def _verify_both(self, payload: PaymentPayload, requirements: PaymentRequirements) -> VerifyResponse:
        mine = self.independent_verify(payload, requirements)
        if not mine.is_valid:
            self._log("verify", payload, mine, None)
            return mine
        sdk = self._scheme().verify(payload, requirements)
        self._log("verify", payload, mine, sdk)
        if not sdk.is_valid:
            return VerifyResponse(
                is_valid=False,
                invalid_reason=sdk.invalid_reason,
                invalid_message=sdk.invalid_message or "x402 SDK exact EVM facilitator rejected the payload",
                payer=sdk.payer or mine.payer,
            )
        return VerifyResponse(
            is_valid=True,
            payer=mine.payer,
            extra={
                "recovered_signer": mine.payer,
                "checks": ["eth_account EIP-712 recovery", "x402 SDK ExactEvmScheme.verify"],
                "simulated_chain": True,
            },
        )

    def _log(self, op: str, payload: PaymentPayload, mine: VerifyResponse | None, sdk: Any) -> None:
        self.verifications.append({
            "op": op,
            "payload_sha256": payload_sha256(payload),
            "elix": mine.model_dump() if mine is not None else None,
            "sdk": sdk.model_dump() if hasattr(sdk, "model_dump") else sdk,
        })
        del self.verifications[:-50]

    # -- FacilitatorClient interface ----------------------------------------

    async def verify(self, payload: PaymentPayload, requirements: PaymentRequirements) -> VerifyResponse:
        return self.verify_sync(payload, requirements)

    async def settle(self, payload: PaymentPayload, requirements: PaymentRequirements) -> SettleResponse:
        return self.settle_sync(payload, requirements)

    def verify_sync(self, payload: PaymentPayload, requirements: PaymentRequirements) -> VerifyResponse:
        return self._verify_both(payload, requirements)

    def settle_sync(self, payload: PaymentPayload, requirements: PaymentRequirements) -> SettleResponse:
        p_sha = payload_sha256(payload)
        # The resource server already ran verify() (both checks) before serving the resource.
        # Here Elix re-runs its own check (nonce and balance may have changed since), and the
        # SDK scheme's settle() re-verifies the signature and payee itself before it transfers.
        pre = self.independent_verify(payload, requirements)
        self._log("settle", payload, pre, None)
        if not pre.is_valid:
            return SettleResponse(
                success=False, error_reason=pre.invalid_reason, error_message=pre.invalid_message,
                payer=pre.payer, transaction="", network=requirements.network,
            )
        token = _current_payload_sha.set(p_sha)
        try:
            result = self._scheme().settle(payload, requirements)
        finally:
            _current_payload_sha.reset(token)
        if not result.success:
            return result
        return result.model_copy(update={
            "amount": requirements.amount,
            "extra": {
                **(result.extra or {}),
                "simulated": True,
                "settlement": SIMULATED_LABEL,
                "tx_ref": sim_tx_ref(p_sha),
                "payload_sha256": p_sha,
                "signature_verified": True,
                "recovered_signer": pre.payer,
                "verified_by": ["eth_account EIP-712 recovery", "x402 SDK ExactEvmScheme.verify"],
            },
        })
