"""Kestrel Labs, the fictional company the demo runs on. All data is synthetic.

Kestrel sells hail and flood forecasts to crop insurers in the Great Plains. Its
three agents buy satellite imagery, radar archives, GPU hours, enrichment data
and search API calls on their own. See specs/03-world.md.

The vendor servers read prices from VENDORS here, so the engine and the vendors
can never disagree about a price.
"""

from __future__ import annotations

from elix.contracts import Account, Actor, AgentConfig, Company, Sku, Vendor
from elix.engine.context import WorldConfig
from elix.rails.keys import vendor_address

TODAY = "2026-10-24T16:00:00Z"
HISTORY_START = "2026-07-01T00:00:00Z"
SYNTHETIC_NOTICE = "Kestrel Labs is a fictional company. All data is synthetic."

COMPANY = Company(
    name="Kestrel Labs",
    description="Kestrel Labs sells hail and flood forecasts to crop insurers in the Great Plains. 38 people.",
    today=TODAY[:10],
    synthetic_notice=SYNTHETIC_NOTICE,
)

ACTORS: list[Actor] = [
    Actor(id="human:priya", kind="human", name="Priya Raman", role="controller", title="Controller"),
    Actor(id="human:dev", kind="human", name="Dev Okafor", role="budget_owner", title="Head of ML"),
    Actor(id="human:maya", kind="human", name="Maya Chen", role="budget_owner", title="Head of Growth"),
    Actor(id="human:sam", kind="human", name="Sam Ortiz", role="accountant", title="Staff accountant"),
    Actor(id="human:jordan", kind="human", name="Jordan Lee", role="auditor",
          title="External auditor, Rivera & Cole LLP"),
    Actor(id="agent:atlas", kind="agent", name="Atlas", role="agent", title="Research agent",
          owner_id="human:dev", cost_center="ML"),
    Actor(id="agent:forge", kind="agent", name="Forge", role="agent", title="Training ops agent",
          owner_id="human:dev", cost_center="ML"),
    Actor(id="agent:scout", kind="agent", name="Scout", role="agent", title="Growth agent",
          owner_id="human:maya", cost_center="GTM"),
    Actor(id="system:sync", kind="system", name="Sync", role="sync",
          title="Ingests invoices, usage meters and receipts"),
    Actor(id="system:policy", kind="system", name="Elix policy engine", role="policy"),
    Actor(id="system:gateway", kind="system", name="Elix gateway", role="gateway"),
    Actor(id="system:learner", kind="system", name="Elix learner", role="learner"),
]

AGENTS: list[AgentConfig] = [
    AgentConfig(actor_id="agent:atlas", name="Atlas", owner_id="human:dev", cost_center="ML",
                per_txn_cap_cents=25_000, monthly_budget_cents=600_000, auto_approve_under_cents=1_500,
                description="Research agent. Buys satellite scenes and radar archives for model training."),
    AgentConfig(actor_id="agent:forge", name="Forge", owner_id="human:dev", cost_center="ML",
                per_txn_cap_cents=250_000, monthly_budget_cents=4_000_000, auto_approve_under_cents=20_000,
                description="Training ops agent. Reserves GPU hours for training runs."),
    AgentConfig(actor_id="agent:scout", name="Scout", owner_id="human:maya", cost_center="GTM",
                per_txn_cap_cents=15_000, monthly_budget_cents=250_000, auto_approve_under_cents=2_500,
                description="Growth agent. Buys insurer firmographics and search queries for outreach."),
]

TOKENS: dict[str, str] = {
    "tok_priya": "human:priya", "tok_dev": "human:dev", "tok_maya": "human:maya", "tok_sam": "human:sam",
    "tok_jordan": "human:jordan", "tok_atlas": "agent:atlas", "tok_forge": "agent:forge", "tok_scout": "agent:scout",
}

ACCOUNTS: list[Account] = [
    Account(code="1010", name="Operating cash (Mercury)", type="asset"),
    Account(code="1015", name="USDC wallet (Base)", type="asset"),
    Account(code="1310", name="Prepaid expenses", type="asset"),
    Account(code="2010", name="Accounts payable", type="liability"),
    Account(code="2110", name="Accrued expenses", type="liability"),
    Account(code="2150", name="Vendor disputes", type="liability"),
    Account(code="3000", name="Opening balance equity", type="equity"),
    Account(code="5110", name="Compute, training and storage", type="expense"),
    Account(code="5120", name="Model inference", type="expense"),
    Account(code="6110", name="Data and research", type="expense"),
    Account(code="6120", name="Software and tools", type="expense"),
    Account(code="6210", name="Marketing data", type="expense"),
    Account(code="6850", name="Bank and network fees", type="expense"),
    Account(code="6990", name="Suspense", type="expense"),
]


def _x402(vendor_id: str) -> str:
    return vendor_address(vendor_id, 0)


VENDORS: list[Vendor] = [
    Vendor(id="ven_terrafield", name="Terrafield Imagery", rails=["x402"], billing="per_request",
           category="Satellite imagery", payout_address=_x402("ven_terrafield"), default_account="6110",
           notes="Satellite imagery tiles.",
           skus=[Sku(sku="scene-10m", description="Satellite scene at 10 m resolution", unit="scene",
                     unit_price_cents=1_800),
                 Sku(sku="scene-3m", description="Satellite scene at 3 m resolution", unit="scene",
                     unit_price_cents=4_600)]),
    Vendor(id="ven_stormvault", name="Stormvault", rails=["x402"], billing="per_request", category="Radar archive",
           payout_address=_x402("ven_stormvault"), default_account="6110", notes="Radar archive.",
           skus=[Sku(sku="station-day", description="One radar station for one day", unit="station day",
                     unit_price_cents=650),
                 Sku(sku="archive-tx-2025", description="Full Texas 2025 radar archive, one time", unit="archive",
                     unit_price_cents=480_000)]),
    Vendor(id="ven_lumen", name="Lumen Compute", rails=["x402", "bank"], billing="semi_monthly_invoice",
           category="GPU cloud", payout_address=_x402("ven_lumen"), bank_account_last4="4417", default_account="5110",
           notes="Spot hours are bought over x402. On demand hours and the reserved cluster are invoiced twice a month.",
           skus=[Sku(sku="h100-spot-hr", description="H100 spot GPU hour", unit="GPU hour", unit_price_cents=210),
                 Sku(sku="h100-ondemand-hr", description="H100 on demand GPU hour", unit="GPU hour",
                     unit_price_cents=210),
                 Sku(sku="h100-reserved-halfmonth", description="Reserved H100 cluster for half a month",
                     unit="half month", unit_price_cents=900_000)]),
    Vendor(id="ven_signalboard", name="Signalboard", rails=["x402"], billing="per_request",
           category="Insurer firmographics", payout_address=_x402("ven_signalboard"), default_account="6110",
           notes="Defaults to 6110 Data and research, which is wrong for marketing data. Sam recodes to 6210.",
           skus=[Sku(sku="firmo-record", description="One insurer firmographic record", unit="record",
                     unit_price_cents=40)]),
    Vendor(id="ven_parsec", name="Parsec Search", rails=["x402"], billing="per_request", category="Search API",
           payout_address=_x402("ven_parsec"), default_account="6120",
           skus=[Sku(sku="query-1k", description="1,000 search API queries", unit="1,000 queries",
                     unit_price_cents=500)]),
    Vendor(id="ven_anthropic", name="Anthropic", rails=["bank"], billing="monthly_invoice", category="Model inference",
           bank_account_last4="0921", default_account="5120",
           notes="Model inference for all three agents. Illustrative rates.",
           skus=[Sku(sku="input-mtok", description="Input tokens, per million", unit="million tokens",
                     unit_price_cents=300),
                 Sku(sku="output-mtok", description="Output tokens, per million", unit="million tokens",
                     unit_price_cents=1_500)]),
    Vendor(id="ven_aws", name="Amazon Web Services", rails=["bank"], billing="monthly_invoice", category="Storage",
           bank_account_last4="7730", default_account="5110", notes="Storage and egress. Illustrative rates.",
           skus=[Sku(sku="s3-tb-month", description="S3 storage, per TB month", unit="TB month",
                     unit_price_cents=2_300),
                 Sku(sku="egress-tb", description="Data egress, per TB", unit="TB", unit_price_cents=9_000)]),
]

INVOICE_AUTO_APPROVE_UNDER_CENTS = 50_000
BUDGET_HARD_DENY_PCT = 120

METERED_SKUS = {
    "ven_lumen": {"h100-ondemand-hr"},
    "ven_anthropic": {"input-mtok", "output-mtok"},
    "ven_aws": {"s3-tb-month", "egress-tb"},
}

# Spot hours bought over x402 run on the same H100 fleet the on demand meter counts.
SKU_EQUIVALENTS = {("ven_lumen", "h100-spot-hr"): "h100-ondemand-hr"}

RECURRING_LINES = {"ven_lumen": [("h100-reserved-halfmonth", 1.0)]}

SKU_NOUNS = {
    ("ven_terrafield", "scene-10m"): ("scene at 10 m", "scenes at 10 m", 1.0),
    ("ven_terrafield", "scene-3m"): ("scene at 3 m", "scenes at 3 m", 1.0),
    ("ven_stormvault", "station-day"): ("radar station day", "radar station days", 1.0),
    ("ven_stormvault", "archive-tx-2025"): ("the full Texas 2025 radar archive", "full Texas 2025 radar archives", 1.0),
    ("ven_lumen", "h100-spot-hr"): ("H100 spot hour", "H100 spot hours", 1.0),
    ("ven_lumen", "h100-ondemand-hr"): ("on-demand H100 hour", "on-demand H100 hours", 1.0),
    ("ven_lumen", "h100-reserved-halfmonth"): ("the reserved H100 cluster for half a month",
                                               "reserved H100 cluster half months", 1.0),
    ("ven_signalboard", "firmo-record"): ("firmographic record", "firmographic records", 1.0),
    ("ven_parsec", "query-1k"): ("search query", "search queries", 1000.0),
    ("ven_anthropic", "input-mtok"): ("million input tokens", "million input tokens", 1.0),
    ("ven_anthropic", "output-mtok"): ("million output tokens", "million output tokens", 1.0),
    ("ven_aws", "s3-tb-month"): ("TB month of storage", "TB months of storage", 1.0),
    ("ven_aws", "egress-tb"): ("TB of egress", "TB of egress", 1.0),
}

USAGE_PHRASES = {
    "input-mtok": "{qty}M input tokens",
    "output-mtok": "{qty}M output tokens",
    "s3-tb-month": "{qty} TB months of storage",
    "egress-tb": "{qty} TB of egress",
    "h100-ondemand-hr": "{qty} on-demand H100 hours",
    "h100-reserved-halfmonth": "the reserved H100 cluster fee",
}

# What each invoicing vendor's invoice numbers look like, checked when an invoice arrives as a PDF.
INVOICE_NUMBER_FORMATS = {
    "ven_lumen": (r"LUM-\d{4}-\d{2}[A-Z]", "LUM-2026-10A"),
    "ven_aws": (r"AWS-\d{4}-\d{2}", "AWS-2026-10"),
    "ven_anthropic": (r"ANT-\d{4}-\d{2}", "ANT-2026-10"),
}

KESTREL = WorldConfig(
    company=COMPANY, actors=ACTORS, agents=AGENTS, vendors=VENDORS, accounts=ACCOUNTS, tokens=TOKENS,
    invoice_auto_approve_under_cents=INVOICE_AUTO_APPROVE_UNDER_CENTS, budget_hard_deny_pct=BUDGET_HARD_DENY_PCT,
    metered_skus=METERED_SKUS, sku_equivalents=SKU_EQUIVALENTS, recurring_lines=RECURRING_LINES,
    sku_nouns=SKU_NOUNS, usage_phrases=USAGE_PHRASES, invoice_number_formats=INVOICE_NUMBER_FORMATS,
)


def agent_for_token(token: str) -> str | None:
    return TOKENS.get(token)
