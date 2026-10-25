"""The configuration the engine runs against, and the context passed between modules.

The engine knows nothing about Kestrel Labs. A world (actors, agents, vendors,
chart of accounts, thresholds) is handed to it as a WorldConfig, which is what
lets the same engine sit behind a different company or front end.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from elix.contracts import Account, Actor, AgentConfig, Company, Role, Vendor

if TYPE_CHECKING:  # pragma: no cover
    from elix.engine.clock import Clock
    from elix.engine.events import EventBus
    from elix.engine.store import Store

POLICY = "system:policy"
GATEWAY = "system:gateway"
SYNC = "system:sync"
LEARNER = "system:learner"

ROLE_RANK = {Role.budget_owner: 1, Role.accountant: 2, Role.controller: 3}


@dataclass
class WorldConfig:
    company: Company
    actors: list[Actor]
    agents: list[AgentConfig]
    vendors: list[Vendor]
    accounts: list[Account]
    tokens: dict[str, str] = field(default_factory=dict)
    invoice_auto_approve_under_cents: int = 50_000
    budget_hard_deny_pct: int = 120
    # vendor id -> skus whose invoice lines are checked against usage meters
    metered_skus: dict[str, set[str]] = field(default_factory=dict)
    # (vendor id, sku bought over x402) -> the metered sku that purchase consumes
    sku_equivalents: dict[tuple[str, str], str] = field(default_factory=dict)
    # vendor id -> fixed lines billed every invoice window (sku, quantity)
    recurring_lines: dict[str, list[tuple[str, float]]] = field(default_factory=dict)
    # (vendor id, sku) -> (singular noun, plural noun, quantity multiplier) used in titles and sentences.
    # A singular noun starting with "the " drops the quantity when it is exactly 1.
    sku_nouns: dict[tuple[str, str], tuple[str, str, float]] = field(default_factory=dict)
    # sku -> phrase used in accrual sentences, "{qty}" is filled in
    usage_phrases: dict[str, str] = field(default_factory=dict)
    # vendor id -> (regex an invoice number must fully match, an example) for invoices read from PDFs
    invoice_number_formats: dict[str, tuple[str, str]] = field(default_factory=dict)

    def actor(self, actor_id: str) -> Optional[Actor]:
        return self._actors().get(actor_id)

    def agent(self, actor_id: str) -> Optional[AgentConfig]:
        return self._agents().get(actor_id)

    def account(self, code: str) -> Optional[Account]:
        return self._accounts().get(code)

    def actor_name(self, actor_id: Optional[str]) -> str:
        if not actor_id:
            return "Nobody"
        actor = self.actor(actor_id)
        return actor.name if actor else actor_id

    def humans_with_role(self, role: Role) -> list[Actor]:
        return [a for a in self.actors if a.role == role and a.kind.value == "human"]

    def _actors(self) -> dict[str, Actor]:
        cache = self.__dict__.get("_actor_cache")
        if cache is None:
            cache = {a.id: a for a in self.actors}
            self.__dict__["_actor_cache"] = cache
        return cache

    def _agents(self) -> dict[str, AgentConfig]:
        cache = self.__dict__.get("_agent_cache")
        if cache is None:
            cache = {a.actor_id: a for a in self.agents}
            self.__dict__["_agent_cache"] = cache
        return cache

    def _accounts(self) -> dict[str, Account]:
        cache = self.__dict__.get("_account_cache")
        if cache is None:
            cache = {a.code: a for a in self.accounts}
            self.__dict__["_account_cache"] = cache
        return cache


@dataclass
class Ctx:
    """Everything an engine module needs. Built once by the facade."""

    store: "Store"
    clock: "Clock"
    config: WorldConfig
    registry: Any
    bus: "EventBus"
    vendor_base_url: Optional[str] = None

    def vendor(self, vendor_id: Optional[str]) -> Optional[Vendor]:
        if not vendor_id:
            return None
        return self.store.get_vendor(vendor_id)

    def rail(self, name: str):
        return self.registry.rail(name)
