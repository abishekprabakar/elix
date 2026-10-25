"""Vendor price table adapter.

The vendor servers must charge exactly what the engine's vendor master says, so
prices are read from ``elix.world.config.VENDORS`` (spec 10 allows this one
import from world into vendors). Until that module exists, a local copy of the
table in specs/03-world.md is used. ``source()`` says which one is active.

Pure module: no FastAPI, no network. The fake rail registry imports it too, so
fast tests and the real vendor servers price a purchase the same way.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# x402 vendors and the URL path each is served under on the vendor app.
VENDOR_PATHS: dict[str, str] = {
    "ven_terrafield": "/terrafield/scenes",
    "ven_stormvault": "/stormvault/archive",
    "ven_lumen": "/lumen/spot",
    "ven_signalboard": "/signalboard/records",
    "ven_parsec": "/parsec/queries",
}

# Short slugs used in /catalog/{vendor} and /admin/payout.
VENDOR_SLUGS: dict[str, str] = {vid: path.split("/")[1] for vid, path in VENDOR_PATHS.items()}
SLUG_TO_VENDOR: dict[str, str] = {slug: vid for vid, slug in VENDOR_SLUGS.items()}


@dataclass
class SkuInfo:
    sku: str
    description: str
    unit: str
    unit_price_cents: int
    tolerance_pct: float = 10.0


@dataclass
class VendorInfo:
    id: str
    name: str
    category: str
    rails: list[str]
    billing: str
    default_account: str
    skus: dict[str, SkuInfo] = field(default_factory=dict)
    notes: str | None = None


# ---------------------------------------------------------------- local copy of specs/03-world.md

def _local_table() -> dict[str, VendorInfo]:
    def v(vid, name, category, rails, billing, account, skus, notes=None):
        return VendorInfo(
            id=vid,
            name=name,
            category=category,
            rails=rails,
            billing=billing,
            default_account=account,
            skus={s.sku: s for s in skus},
            notes=notes,
        )

    return {
        "ven_terrafield": v(
            "ven_terrafield", "Terrafield Imagery", "Satellite imagery", ["x402"], "per_request", "6110",
            [
                SkuInfo("scene-10m", "Satellite scene at 10 m resolution", "scene", 1800),
                SkuInfo("scene-3m", "Satellite scene at 3 m resolution", "scene", 4600),
            ],
        ),
        "ven_stormvault": v(
            "ven_stormvault", "Stormvault", "Radar archive", ["x402"], "per_request", "6110",
            [
                SkuInfo("station-day", "One radar station for one day", "station day", 650),
                SkuInfo("archive-tx-2025", "Full Texas 2025 radar archive, one time", "archive", 480000),
            ],
        ),
        "ven_lumen": v(
            "ven_lumen", "Lumen Compute", "GPU cloud", ["x402", "bank"], "semi_monthly_invoice", "5110",
            [
                SkuInfo("h100-spot-hr", "H100 spot GPU hour", "GPU hour", 210),
                SkuInfo("h100-ondemand-hr", "H100 on demand GPU hour", "GPU hour", 210),
                SkuInfo("h100-reserved-halfmonth", "Reserved H100 cluster for half a month", "half month", 900000),
            ],
        ),
        "ven_signalboard": v(
            "ven_signalboard", "Signalboard", "Insurer firmographics", ["x402"], "per_request", "6110",
            [SkuInfo("firmo-record", "One insurer firmographic record", "record", 40)],
        ),
        "ven_parsec": v(
            "ven_parsec", "Parsec Search", "Search API", ["x402"], "per_request", "6120",
            [SkuInfo("query-1k", "1,000 search API queries", "1,000 queries", 500)],
        ),
    }


_SOURCE = "local"


def _from_world() -> dict[str, VendorInfo] | None:
    try:
        from elix.world.config import VENDORS  # type: ignore[import-not-found]
    except Exception:
        return None
    table: dict[str, VendorInfo] = {}
    for vend in VENDORS:
        get = (lambda o, k, d=None: o.get(k, d)) if isinstance(vend, dict) else (lambda o, k, d=None: getattr(o, k, d))
        skus = {}
        for s in get(vend, "skus", []) or []:
            sget = (lambda o, k, d=None: o.get(k, d)) if isinstance(s, dict) else (lambda o, k, d=None: getattr(o, k, d))
            skus[sget(s, "sku")] = SkuInfo(
                sku=sget(s, "sku"),
                description=sget(s, "description", "") or "",
                unit=sget(s, "unit", "") or "",
                unit_price_cents=int(sget(s, "unit_price_cents")),
                tolerance_pct=float(sget(s, "tolerance_pct", 10.0) or 10.0),
            )
        rails = list(get(vend, "rails", []) or [])
        table[get(vend, "id")] = VendorInfo(
            id=get(vend, "id"),
            name=get(vend, "name"),
            category=get(vend, "category", "") or "",
            rails=rails,
            billing=str(get(vend, "billing", "per_request")),
            default_account=str(get(vend, "default_account", "")),
            skus=skus,
            notes=get(vend, "notes"),
        )
    return table or None


def vendor_table() -> dict[str, VendorInfo]:
    """Every vendor keyed by id. Prefers elix.world.config.VENDORS, falls back to the local copy."""
    global _SOURCE
    world = _from_world()
    if world is not None:
        _SOURCE = "elix.world.config"
        return world
    _SOURCE = "local"
    return _local_table()


def source() -> str:
    vendor_table()
    return _SOURCE


def x402_vendor(vendor_id: str) -> VendorInfo | None:
    """The vendor if it is served by the x402 vendor app, else None."""
    if vendor_id not in VENDOR_PATHS:
        return None
    return vendor_table().get(vendor_id)


class PricingError(ValueError):
    pass


def price_cents(vendor_id: str, sku: str, quantity: float, price_override_cents: int | None = None) -> int:
    """Contract unit price x quantity in integer cents, unless an override is given (offline only)."""
    if price_override_cents is not None:
        if int(price_override_cents) <= 0:
            raise PricingError("price_override_cents must be positive")
        return int(price_override_cents)
    vend = x402_vendor(vendor_id)
    if vend is None:
        raise PricingError(f"Unknown x402 vendor {vendor_id}")
    info = vend.skus.get(sku)
    if info is None:
        raise PricingError(f"{vend.name} does not sell sku {sku}")
    q = float(quantity)
    if not math.isfinite(q) or q <= 0:
        raise PricingError("quantity must be a positive number")
    # Half up rounding to whole cents, matching how the engine computes amount_cents.
    return int(math.floor(info.unit_price_cents * q + 0.5))


def cents_to_atomic(cents: int) -> str:
    """USDC has 6 decimals, so one cent is 10,000 atomic units."""
    return str(int(cents) * 10_000)


def atomic_to_cents(atomic: str | int) -> int:
    """Atomic USDC units to cents, rounded half up when the amount is not a whole cent."""
    a = int(atomic)
    return (a + 5_000) // 10_000


def describe(vendor_id: str) -> dict[str, Any]:
    vend = vendor_table().get(vendor_id)
    if vend is None:
        return {}
    return {
        "id": vend.id,
        "name": vend.name,
        "category": vend.category,
        "skus": [
            {"sku": s.sku, "description": s.description, "unit": s.unit, "unit_price_cents": s.unit_price_cents}
            for s in vend.skus.values()
        ],
    }
