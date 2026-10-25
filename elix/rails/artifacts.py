"""Protocol artifacts captured by X402Rail, keyed by work item id.

Fields: challenge_decoded, payload_decoded (signature truncated), payment_response_decoded,
resource_json, resource_sha256, simulated, plus a few extras (payload_sha256,
signature_verified, tx_ref, resource_url, refused). Stored in ELIX_DATA_DIR/rails.db.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any

from elix.rails.constants import data_dir

_lock = threading.RLock()
_initialized: set[str] = set()


def _path() -> str:
    return os.path.join(data_dir(), "rails.db")


def _connect() -> sqlite3.Connection:
    path = _path()
    existed = os.path.exists(path)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.execute("PRAGMA synchronous=NORMAL")
    if path not in _initialized or not existed:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        conn.execute(
            "CREATE TABLE IF NOT EXISTS artifacts (work_item_id TEXT PRIMARY KEY, data TEXT NOT NULL,"
            " updated_at TEXT NOT NULL)"
        )
        conn.commit()
        _initialized.add(path)
    return conn


def save(work_item_id: str, **fields: Any) -> None:
    """Merge fields into the artifact record for a work item."""
    if not work_item_id:
        return
    with _lock:
        conn = _connect()
        try:
            row = conn.execute("SELECT data FROM artifacts WHERE work_item_id=?", (work_item_id,)).fetchone()
            data: dict[str, Any] = json.loads(row[0]) if row else {}
            data.update(fields)
            conn.execute(
                "INSERT OR REPLACE INTO artifacts(work_item_id, data, updated_at) VALUES (?,?,?)",
                (work_item_id, json.dumps(data, default=str), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
            )
            conn.commit()
        finally:
            conn.close()


def get(work_item_id: str) -> dict | None:
    with _lock:
        conn = _connect()
        try:
            row = conn.execute("SELECT data FROM artifacts WHERE work_item_id=?", (work_item_id,)).fetchone()
        finally:
            conn.close()
    return json.loads(row[0]) if row else None


def reset() -> None:
    """Delete every stored artifact (called by seed and demo reset)."""
    with _lock:
        conn = _connect()
        try:
            conn.execute("DELETE FROM artifacts")
            conn.commit()
        finally:
            conn.close()


def x402_view(work_item_id: str) -> dict | None:
    """Shape used by GET /api/work-items/{id}/x402."""
    a = get(work_item_id)
    if a is None:
        return None
    return {
        "challenge_decoded": a.get("challenge_decoded"),
        "payload_decoded": a.get("payload_decoded"),
        "payload_sha256": a.get("payload_sha256"),
        "payment_response_decoded": a.get("payment_response_decoded"),
        "signature_verified": a.get("signature_verified"),
        "simulated": a.get("simulated", True),
        "tx_ref": a.get("tx_ref"),
        "resource_url": a.get("resource_url"),
        "refused": a.get("refused"),
    }


def resource_view(work_item_id: str) -> dict | None:
    """Shape used by GET /api/work-items/{id}/resource. None when nothing was delivered."""
    a = get(work_item_id)
    if not a or a.get("resource_json") is None:
        return None
    return {"resource": a.get("resource_json"), "sha256": a.get("resource_sha256"), "simulated": a.get("simulated", True)}
