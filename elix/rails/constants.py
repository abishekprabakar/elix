"""Network and asset constants, taken from the x402 SDK's own default asset table."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from x402.mechanisms.evm.default_assets import get_default_asset

NETWORK = "eip155:84532"  # Base Sepolia (CAIP-2)
SCHEME = "exact"

_USDC = get_default_asset(NETWORK)
USDC_ADDRESS: str = _USDC["asset"]          # 0x036CbD53842c5426634e7929541eC2318f3dCF7e
USDC_NAME: str = _USDC["name"]              # "USDC" (EIP-712 domain name on Base Sepolia)
USDC_VERSION: str = _USDC["version"]        # "2"
USDC_DECIMALS: int = _USDC["decimals"]      # 6

DEFAULT_FACILITATOR_URL = "https://x402.org/facilitator"
DEFAULT_VENDOR_BASE_URL = "http://127.0.0.1:8402"

# Largest single payment Elix will ever let the SDK client sign. The agent hard
# ceilings are per transaction caps of $250, $2,500 and $150, but a human can
# approve above a cap (the $4,800 Stormvault archive, the $9,000 Lumen reserved
# half month). Elix is the spend control, so the SDK cap is set to the largest
# single payment the world can legitimately produce rather than disabled.
MAX_SINGLE_PAYMENT_USD = "$10000"
MAX_SINGLE_PAYMENT_CENTS = 1_000_000


def now_iso(clock: Any = None) -> str:
    """ISO 8601 UTC 'Z' timestamp from an optional engine clock, else the wall clock.

    Accepts a callable returning a datetime or string, or an object with now(), iso() or now_iso().
    """
    value: Any = None
    if clock is not None:
        for attr in ("now_iso", "iso", "now"):
            fn = getattr(clock, attr, None)
            if callable(fn):
                try:
                    value = fn()
                    break
                except TypeError:
                    continue
        if value is None and callable(clock):
            value = clock()
    if value is None:
        value = datetime.now(timezone.utc)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)


def data_dir() -> str:
    """ELIX_DATA_DIR (default ./data), created on demand. Resolved at call time so tests can redirect it."""
    import os

    path = os.path.abspath(os.environ.get("ELIX_DATA_DIR") or "./data")
    os.makedirs(path, exist_ok=True)
    return path
