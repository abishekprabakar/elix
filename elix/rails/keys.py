"""Deterministic offline keys and addresses, plus live key loading.

TEST KEYS ONLY. Every private key derived in this module comes from a fixed,
public string (sha256("elix-offline-...")). Anyone can recompute them. They
exist so offline mode can produce real secp256k1 signatures over real EIP-712
typed data. Never send funds to these addresses on any real network and never
use them outside the local simulation.

Live mode reads the payer key from ELIX_WALLET_PRIVATE_KEY (a Base Sepolia test
wallet). Vendor payout addresses stay deterministic in both modes because a
payout address only receives; nothing here ever needs a vendor's key to pay.
"""

from __future__ import annotations

import hashlib
import os
from functools import lru_cache

from eth_account import Account
from eth_account.signers.local import LocalAccount

# x402 vendors that have payout wallets (see specs/03-world.md).
X402_VENDOR_IDS: tuple[str, ...] = (
    "ven_terrafield",
    "ven_stormvault",
    "ven_lumen",
    "ven_signalboard",
    "ven_parsec",
)

_TREASURY_SEED = "elix-offline-treasury"


def _derive_key(seed: str) -> str:
    """Return a 0x-prefixed 32 byte hex private key from a fixed public string. Test keys only."""
    return "0x" + hashlib.sha256(seed.encode("utf-8")).hexdigest()


@lru_cache(maxsize=None)
def _offline_account(seed: str) -> LocalAccount:
    return Account.from_key(_derive_key(seed))


def rail_mode() -> str:
    mode = (os.environ.get("ELIX_RAIL_MODE") or "offline").strip().lower()
    return "live" if mode == "live" else "offline"


# ---------------------------------------------------------------- vendors

def vendor_seed(vendor_id: str, rotation: int = 0) -> str:
    return f"elix-offline-vendor:{vendor_id}:{int(rotation)}"


def vendor_account(vendor_id: str, rotation: int = 0) -> LocalAccount:
    """Offline test account for a vendor payout wallet. Test key, never fund it."""
    return _offline_account(vendor_seed(vendor_id, rotation))


def vendor_address(vendor_id: str, rotation: int = 0) -> str:
    """Deterministic EVM checksum address for an x402 vendor's payout wallet.

    rotation=0 is the address in the vendor master at seed time.
    rotation=1 is the 'new' address used by the payee-change moment and eval.
    """
    return vendor_account(vendor_id, rotation).address


# ---------------------------------------------------------------- treasury (payer)

def offline_treasury_account() -> LocalAccount:
    """Kestrel's offline treasury signer. Test key derived from a public string."""
    return _offline_account(_TREASURY_SEED)


def live_treasury_account() -> LocalAccount:
    """The live Base Sepolia payer, loaded from ELIX_WALLET_PRIVATE_KEY. Raises if missing."""
    raw = (os.environ.get("ELIX_WALLET_PRIVATE_KEY") or "").strip()
    if not raw:
        raise RuntimeError(
            "ELIX_RAIL_MODE=live needs ELIX_WALLET_PRIVATE_KEY set to a funded Base Sepolia test wallet key."
        )
    if not raw.startswith("0x"):
        raw = "0x" + raw
    return Account.from_key(raw)


def cdp_configured() -> bool:
    return all(os.environ.get(k) for k in ("CDP_API_KEY_ID", "CDP_API_KEY_SECRET", "CDP_WALLET_SECRET"))


def cdp_treasury_account():
    """Live mode alternative: a CDP Server Wallet account wrapped as an eth_account BaseAccount.

    Uses cdp-sdk's CdpClient.evm.get_or_create_account(name=...) and EvmLocalAccount, which the
    x402 SDK's ExactEvmScheme wraps in EthAccountSigner like any LocalAccount. Needs
    `pip install cdp-sdk` and CDP_API_KEY_ID, CDP_API_KEY_SECRET, CDP_WALLET_SECRET. The account
    name comes from ELIX_CDP_ACCOUNT_NAME (default "elix-treasury"). Untested in the sandbox.
    """
    import asyncio
    import concurrent.futures

    try:
        from cdp import CdpClient  # type: ignore[import-not-found]
        from cdp.evm_local_account import EvmLocalAccount  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - cdp-sdk is optional
        raise RuntimeError("CDP wallet signing needs `pip install cdp-sdk`.") from exc

    name = os.environ.get("ELIX_CDP_ACCOUNT_NAME") or "elix-treasury"

    async def _load():
        async with CdpClient() as cdp:
            server_account = await cdp.evm.get_or_create_account(name=name)
            return EvmLocalAccount(server_account)

    # Run in a fresh thread so this works even when called from inside a running event loop.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _load()).result(timeout=60)


_live_account_cache: dict[str, object] = {}


def treasury_account(mode: str | None = None):
    """The account the gateway signs x402 payments with. Agents never see it.

    offline: deterministic test key. live: ELIX_WALLET_PRIVATE_KEY if set, otherwise a CDP
    Server Wallet account when the CDP_* variables are set.
    """
    if (mode or rail_mode()) == "live":
        if os.environ.get("ELIX_WALLET_PRIVATE_KEY"):
            return live_treasury_account()
        if cdp_configured():
            if "cdp" not in _live_account_cache:
                _live_account_cache["cdp"] = cdp_treasury_account()
            return _live_account_cache["cdp"]
        return live_treasury_account()  # raises with a clear message
    return offline_treasury_account()


def treasury_address(mode: str | None = None) -> str:
    """Kestrel's treasury (payer) address in offline mode, or the live wallet address in live mode."""
    m = mode or rail_mode()
    if m == "live":
        try:
            if os.environ.get("ELIX_WALLET_PRIVATE_KEY"):
                return live_treasury_account().address
            if cdp_configured():
                return treasury_account("live").address
        except Exception:
            pass
        # Without a live key we still return a stable address so reads never crash.
        return offline_treasury_account().address
    return offline_treasury_account().address


__all__ = [
    "X402_VENDOR_IDS",
    "rail_mode",
    "vendor_account",
    "vendor_address",
    "vendor_seed",
    "offline_treasury_account",
    "live_treasury_account",
    "treasury_account",
    "treasury_address",
]
