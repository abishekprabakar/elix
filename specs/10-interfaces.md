# 10 · Cross component Python interfaces

Two backend builders work in parallel. The backend core builder owns `elix/engine`, `elix/world`, `elix/eval`, `elix/scenarios`, `elix/server`, `elix/cli.py`, `elix/__main__.py`, `pyproject.toml`, `tests/` (except `tests/rails`, `tests/agents`, `tests/mcp`) and `scripts/`. The rails builder owns `elix/rails`, `elix/vendors`, `elix/agents`, `elix/mcp_server.py`, `tests/rails`, `tests/agents`, `tests/mcp`, `docs/mcp.md`. These are the only functions each side may call on the other. Implement them exactly. If you must change one, write it in HANDOFF.md first and keep the old signature working.

## Provided by the rails builder

```python
# elix/rails/keys.py
def vendor_address(vendor_id: str, rotation: int = 0) -> str
    """Deterministic EVM checksum address for an x402 vendor's payout wallet.
    rotation=0 is the address in the vendor master at seed time.
    rotation=1 is the 'new' address used by the payee-change moment and eval."""
def treasury_address() -> str
    """Kestrel's treasury (payer) address in offline mode, or the live wallet address in live mode."""

# elix/rails/__init__.py
class RailRegistry:
    def rail(self, name: Literal["x402", "bank"]) -> Rail: ...
    mode: Literal["offline", "live"]
def make_registry(mode: str | None = None, vendor_base_url: str | None = None) -> RailRegistry
    """Reads ELIX_RAIL_MODE and ELIX_VENDOR_BASE_URL when args are None."""
def make_fake_registry() -> RailRegistry
    """In-memory rails for fast unit tests: x402 challenge built from the intent and vendor master, pay returns a
    simulated receipt without HTTP. Must honor PaymentIntent.pay_to_override so payee tests work."""

# elix/rails/artifacts.py
def save(work_item_id: str, **fields) -> None
def get(work_item_id: str) -> dict | None
    """Protocol artifacts captured by X402Rail: challenge_decoded, payload_decoded (signature truncated),
    payment_response_decoded, resource_json, resource_sha256, simulated. Stored in a small SQLite file at
    ELIX_DATA_DIR/rails.db (default ./data)."""

# elix/vendors/__init__.py
def ensure_running(port: int = 8402, host: str = "127.0.0.1") -> str
    """Start the vendor app in a daemon thread with uvicorn if nothing answers /healthz on that port. Return base URL."""
def set_payout(vendor_id: str, address: str, base_url: str | None = None) -> None
    """Offline only. Changes the address the vendor's 402 challenges ask to be paid at (POST /admin/payout)."""
def reset(base_url: str | None = None) -> None
    """Offline only. Restores every vendor's payout to rotation 0 and clears used nonces."""
def resource_url(vendor_id: str, sku: str, quantity: float, base_url: str | None = None, **params) -> str
    """Builds the paid resource URL for a purchase (see 05 routes)."""
def catalog(vendor_id: str) -> dict
    """Static catalog JSON, same as GET /catalog/{vendor}."""

# elix/agents/runner.py
def start_run(agent_id: str, task: str, api_base: str, token: str, force_scripted: bool = False) -> str
    """Starts a background thread, returns session_id immediately."""
def run_blocking(agent_id: str, task: str, api_base: str, token: str, force_scripted: bool = False) -> str
DEFAULT_TASKS: dict[str, str]

# elix/agents/sessions.py
def list_sessions(agent_id: str | None = None) -> list[dict]
def get_session(session_id: str) -> dict | None   # includes "transcript"
```

## Provided by the backend core builder

```python
# elix/engine/__init__.py
from elix.engine.facade import Elix
def open_elix(db_path: str | None = None, registry: RailRegistry | None = None) -> Elix
    """Default db at ELIX_DATA_DIR/elix.db, default registry from make_registry()."""

# elix/world/config.py
AGENTS: list[AgentConfig]; VENDORS: list[Vendor]; ACTORS: list[Actor]; ACCOUNTS: list[Account]
TOKENS: dict[str, str]          # token -> actor id
TODAY: str                      # "2026-10-24T16:00:00Z"
```

The vendor master in `elix/world/config.py` must use `vendor_address(vendor_id, 0)` for x402 vendors. The vendor app must read prices and skus from `elix/world/config.py` VENDORS so prices never drift between the engine and the vendors. That one import from world into vendors is allowed.

## Data directory

Everything runtime goes under `ELIX_DATA_DIR` (default `./data`, gitignored): `elix.db`, `rails.db`, `sim_chain.db`, `sessions.db`, `eval/` working DBs. `python -m elix demo` and `seed` recreate `elix.db` and reset `rails.db`, `sim_chain.db` and the vendor payouts.

## Ports

API 8400, vendors 8402, console dev 5173. Tests must use free ephemeral ports, never the fixed ones, so builders do not collide.
