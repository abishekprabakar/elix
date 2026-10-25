"""Local x402 vendor servers (one FastAPI app on port 8402). See specs/05 and specs/10.

Public functions (spec 10): ensure_running, set_payout, reset, resource_url, catalog.
Heavy imports (FastAPI, uvicorn, the x402 server) happen lazily inside them.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from elix.vendors import pricing

DEFAULT_PORT = 8402
_servers: dict[tuple[str, int], dict[str, Any]] = {}
_servers_lock = threading.Lock()


def _default_base_url() -> str:
    return (os.environ.get("ELIX_VENDOR_BASE_URL") or f"http://127.0.0.1:{DEFAULT_PORT}").rstrip("/")


def free_port(host: str = "127.0.0.1") -> int:
    """An ephemeral TCP port that is free right now (for tests)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def _healthy(base_url: str, timeout: float = 0.5) -> bool:
    try:
        r = httpx.get(f"{base_url}/healthz", timeout=timeout)
        return r.status_code == 200 and r.json().get("service") == "elix-vendors"
    except Exception:
        return False


def ensure_running(port: int = DEFAULT_PORT, host: str = "127.0.0.1", mode: str | None = None) -> str:
    """Start the vendor app in a daemon thread with uvicorn if nothing answers /healthz on that port.

    Returns the base URL. port=0 picks a free ephemeral port.
    """
    import uvicorn

    if port == 0:
        port = free_port(host)
    base = f"http://{host}:{port}"
    with _servers_lock:
        if _healthy(base):
            return base
        from elix.vendors.app import create_app

        app = create_app(mode=mode)
        config = uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False, lifespan="off")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, name=f"elix-vendors-{port}", daemon=True)
        thread.start()
        deadline = time.time() + 15
        while time.time() < deadline:
            if _healthy(base, timeout=0.3):
                _servers[(host, port)] = {"server": server, "thread": thread, "app": app}
                return base
            if not thread.is_alive():
                break
            time.sleep(0.05)
        raise RuntimeError(f"The Elix vendor servers did not start on {base}. Is the port taken by another program?")


def stop(port: int, host: str = "127.0.0.1") -> None:
    """Stop a vendor server started by ensure_running in this process (tests)."""
    with _servers_lock:
        entry = _servers.pop((host, port), None)
    if entry:
        entry["server"].should_exit = True
        entry["thread"].join(timeout=5)


def running_app(base_url: str | None = None):
    """The in-process FastAPI app behind base_url, if this process started it."""
    from urllib.parse import urlparse

    u = urlparse(base_url or _default_base_url())
    entry = _servers.get((u.hostname or "127.0.0.1", u.port or DEFAULT_PORT))
    return entry["app"] if entry else None


def set_payout(vendor_id: str, address: str, base_url: str | None = None) -> None:
    """Offline only. Changes the address the vendor's 402 challenges ask to be paid at (POST /admin/payout)."""
    base = (base_url or _default_base_url()).rstrip("/")
    r = httpx.post(f"{base}/admin/payout", json={"vendor": vendor_id, "address": address}, timeout=10)
    if r.status_code != 200:
        raise RuntimeError(f"set_payout failed with HTTP {r.status_code}: {r.text}")


def reset(base_url: str | None = None) -> None:
    """Offline only. Restores every vendor's payout to rotation 0 and clears used nonces.

    Does nothing when no vendor server is running at base_url (its state lives in memory).
    """
    base = (base_url or _default_base_url()).rstrip("/")
    try:
        r = httpx.post(f"{base}/admin/reset", timeout=5)
    except httpx.HTTPError:
        return
    if r.status_code not in (200, 403):
        raise RuntimeError(f"vendor reset failed with HTTP {r.status_code}: {r.text}")


def _fmt_qty(quantity: float) -> str:
    q = float(quantity)
    return str(int(q)) if q.is_integer() else repr(q)


def resource_url(vendor_id: str, sku: str, quantity: float, base_url: str | None = None, **params: Any) -> str:
    """Builds the paid resource URL for a purchase (see 05 routes)."""
    path = pricing.VENDOR_PATHS.get(vendor_id)
    base = (base_url or _default_base_url()).rstrip("/")
    if path is None:
        # Unknown vendors still get a stable URL so the rail reports a clear 404 instead of crashing.
        path = f"/unknown/{vendor_id}"
    query = {"sku": sku, "qty": _fmt_qty(quantity)}
    for k, v in params.items():
        if v is not None:
            query[k] = str(v)
    return f"{base}{path}?{urlencode(query)}"


def catalog(vendor_id: str) -> dict:
    """Static catalog JSON, same as GET /catalog/{vendor}."""
    from elix.vendors import catalogs

    vid = pricing.SLUG_TO_VENDOR.get(vendor_id, vendor_id)
    return catalogs.catalog(vid, base_url=_default_base_url())


__all__ = ["ensure_running", "set_payout", "reset", "resource_url", "catalog", "free_port", "stop"]
