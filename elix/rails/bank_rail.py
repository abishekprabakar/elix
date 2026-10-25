"""Simulated ACH bank rail. Always simulated, in every mode.

Kestrel pays invoice vendors (Anthropic, AWS, Lumen's semi monthly invoices)
from its Mercury operating account. No bank is contacted. Each payment gets a
deterministic reference "ach-<id>" and is appended to a small settlement log in
ELIX_DATA_DIR/rails.db so the console can show it.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading

from elix.contracts import PaymentChallenge, PaymentIntent, RailError, Receipt
from elix.rails.constants import data_dir, now_iso

PAYER_LABEL = "Kestrel Labs operating account (Mercury, simulated)"


class BankRail:
    name = "bank"

    def __init__(self) -> None:
        self._registry = None
        self._lock = threading.Lock()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(os.path.join(data_dir(), "rails.db"), timeout=30, check_same_thread=False)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS ach_payments (seq INTEGER PRIMARY KEY AUTOINCREMENT, tx_ref TEXT UNIQUE,"
            " work_item_id TEXT, vendor_id TEXT, amount_cents INTEGER, settled_at TEXT)"
        )
        return conn

    def challenge(self, intent: PaymentIntent) -> None:
        return None

    def pay(self, intent: PaymentIntent, challenge: PaymentChallenge | None) -> Receipt:
        if intent.amount_cents <= 0:
            raise RailError("bank payment amount must be positive")
        settled_at = now_iso(getattr(self._registry, "clock", None))
        with self._lock:
            conn = self._conn()
            try:
                n = conn.execute("SELECT COUNT(*) FROM ach_payments").fetchone()[0]
                digest = hashlib.sha256(
                    f"{intent.work_item_id}:{intent.vendor_id}:{intent.amount_cents}:{n}".encode()
                ).hexdigest()
                tx_ref = f"ach-{digest[:12]}"
                conn.execute(
                    "INSERT INTO ach_payments(tx_ref, work_item_id, vendor_id, amount_cents, settled_at)"
                    " VALUES (?,?,?,?,?)",
                    (tx_ref, intent.work_item_id, intent.vendor_id, intent.amount_cents, settled_at),
                )
                conn.commit()
            finally:
                conn.close()
        return Receipt(
            rail="bank",
            simulated=True,
            network=None,
            tx_ref=tx_ref,
            payer=PAYER_LABEL,
            payee=intent.vendor_id,
            amount_cents=intent.amount_cents,
            amount_atomic=None,
            payload_sha256=None,
            signature_verified=None,
            settled_at=settled_at,
            resource_delivered=False,
            resource_sha256=None,
        )


def reset() -> None:
    conn = sqlite3.connect(os.path.join(data_dir(), "rails.db"), timeout=30)
    try:
        conn.execute("DROP TABLE IF EXISTS ach_payments")
        conn.commit()
    finally:
        conn.close()
