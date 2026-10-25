"""Payment rails for Elix (x402 and bank). See specs/05-rails-agents.md and specs/10-interfaces.md.

The engine only ever sees a RailRegistry and the Rail protocol from elix.contracts.
Only elix/engine/gateway.py may call Rail.pay().
"""

from __future__ import annotations

import os
from typing import Any, Literal

from elix.contracts import Rail
from elix.rails.keys import treasury_address, vendor_address  # noqa: F401  (re-exported)

Mode = Literal["offline", "live"]


class RailRegistry:
    """Holds one rail per name. ``mode`` is "offline" or "live".

    ``clock`` is optional. When the engine sets it (registry.set_clock(clock)), rails stamp
    challenge.received_at and receipt.settled_at with world time instead of the wall clock.
    Signature validity windows always use the wall clock, because that is what the
    facilitator checks.
    """

    def __init__(self, rails: dict[str, Rail], mode: Mode, vendor_base_url: str | None = None,
                 kind: str = "real") -> None:
        self._rails = dict(rails)
        self.mode: Mode = mode
        self.vendor_base_url = vendor_base_url
        self.kind = kind            # "real" or "fake"
        self.clock: Any = None
        for r in self._rails.values():
            if hasattr(r, "_registry"):
                r._registry = self

    def rail(self, name: Literal["x402", "bank"]) -> Rail:
        try:
            return self._rails[name]
        except KeyError:
            raise KeyError(f"No rail named {name!r}. Known rails: {sorted(self._rails)}") from None

    def rail_for(self, vendor: Any) -> Rail:
        """The rail a vendor is normally paid on (x402 first when a vendor supports both)."""
        rails = getattr(vendor, "rails", None)
        if rails is None and isinstance(vendor, dict):
            rails = vendor.get("rails")
        rails = list(rails or [])
        if "x402" in rails:
            return self.rail("x402")
        return self.rail("bank")

    def set_clock(self, clock: Any) -> None:
        self.clock = clock

    @property
    def settlement_label(self) -> str:
        if self.kind == "fake":
            return "Fake rail (unit tests)"
        return "Live Base Sepolia" if self.mode == "live" else "Offline simulation"

    def __repr__(self) -> str:  # pragma: no cover
        return f"RailRegistry(mode={self.mode!r}, kind={self.kind!r}, rails={sorted(self._rails)})"


def _env_mode(mode: str | None) -> Mode:
    m = (mode or os.environ.get("ELIX_RAIL_MODE") or "offline").strip().lower()
    if m not in ("offline", "live"):
        raise ValueError(f"ELIX_RAIL_MODE must be offline or live, not {m!r}")
    return m  # type: ignore[return-value]


def make_registry(mode: str | None = None, vendor_base_url: str | None = None,
                  namespace: str | None = None) -> RailRegistry:
    """Real rails. Reads ELIX_RAIL_MODE and ELIX_VENDOR_BASE_URL when args are None.

    offline: X402Rail against the local vendor app (real SDK payloads, local facilitator,
    simulated settlement) plus the simulated ACH BankRail.
    live: X402Rail signing with ELIX_WALLET_PRIVATE_KEY against vendors that settle through
    ELIX_FACILITATOR_URL on Base Sepolia. The bank rail is always simulated.

    namespace (optional) prefixes artifact keys in rails.db, so a second engine such as the eval
    can share the vendor servers without overwriting the demo's artifacts (read them back with
    artifacts.get(f"{namespace}:{work_item_id}")).
    """
    from elix.rails.bank_rail import BankRail
    from elix.rails.constants import DEFAULT_VENDOR_BASE_URL
    from elix.rails.x402_rail import X402Rail

    m = _env_mode(mode)
    base = (vendor_base_url or os.environ.get("ELIX_VENDOR_BASE_URL") or DEFAULT_VENDOR_BASE_URL).rstrip("/")
    x402 = X402Rail(mode=m, vendor_base_url=base, namespace=namespace)
    bank = BankRail()
    return RailRegistry({"x402": x402, "bank": bank}, mode=m, vendor_base_url=base, kind="real")


def make_fake_registry() -> RailRegistry:
    """In-memory rails for fast unit tests: x402 challenge built from the intent and vendor master,
    pay returns a simulated receipt without HTTP. Honors PaymentIntent.pay_to_override (and the
    pay_to_override / price_override_cents query params of resource_url) so payee tests work."""
    from elix.rails.fake import FakeBankRail, FakeX402Rail

    return RailRegistry({"x402": FakeX402Rail(), "bank": FakeBankRail()}, mode="offline", kind="fake")


def reset_offline_state(vendor_base_url: str | None = None) -> None:
    """Clear rails.db (artifacts and ACH log), re-fund the simulated USDC ledger, and reset the
    vendor servers' payouts and used nonces. Called by `seed` and `demo` (spec 10, data directory)."""
    from elix import vendors
    from elix.rails import artifacts, bank_rail, sim_chain

    artifacts.reset()
    bank_rail.reset()
    sim_chain.reset()
    vendors.reset(vendor_base_url)


__all__ = [
    "RailRegistry",
    "make_registry",
    "make_fake_registry",
    "reset_offline_state",
    "vendor_address",
    "treasury_address",
]
