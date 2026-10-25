"""Reading the dispute on an approved invoice. No imports from the rest of the engine, so explain and credits share it."""

from __future__ import annotations

from typing import Any, Optional

from elix.contracts import State, WorkItem, WorkItemKind

STATUS_WORDS = {"open": "open", "partly_credited": "partly credited", "credited": "credited"}


def credit_notes(item: WorkItem) -> list[dict[str, Any]]:
    return list(item.data.get("credit_notes") or [])


def dispute_open_cents(item: WorkItem) -> int:
    """What is still disputed on an approved invoice. Zero before approval and once fully credited."""
    if item.kind != WorkItemKind.invoice or item.state != State.POSTED:
        return 0
    if "dispute_open_cents" in item.data:
        return int(item.data["dispute_open_cents"] or 0)
    disputed = int(item.data.get("dispute_cents") or 0)
    applied = sum(int(n.get("applied_cents") or 0) for n in credit_notes(item) if n.get("je_id"))
    return max(0, disputed - applied)


def dispute_status(item: WorkItem) -> Optional[str]:
    """open, partly_credited or credited for an approved invoice with a dispute, else None."""
    if item.kind != WorkItemKind.invoice or not item.data.get("dispute_cents") or item.state != State.POSTED:
        return None
    if item.data.get("dispute_status"):
        return str(item.data["dispute_status"])
    if dispute_open_cents(item) == 0:
        return "credited"
    return "partly_credited" if any(n.get("je_id") for n in credit_notes(item)) else "open"
