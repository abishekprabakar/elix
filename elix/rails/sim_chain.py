"""Simulated USDC ledger for offline mode. SIMULATED SETTLEMENT.

Nothing in this module touches a blockchain. It is a small SQLite file
(ELIX_DATA_DIR/sim_chain.db) that plays the part of the Base Sepolia USDC
contract for the local facilitator:

- balances per address (atomic units, 6 decimals)
- EIP-3009 authorization state (authorizer, nonce) so a nonce can be used once
- a settlement log with a simulated transaction hash per transfer

The payment payloads it settles are real: they are signed EIP-3009
authorizations created by the x402 SDK and verified by signature recovery
before anything moves here. Only the movement of balances is simulated.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any

from elix.rails.constants import USDC_ADDRESS, data_dir

# Opening simulated balance for Kestrel's treasury: 5,000,000 test USDC.
TREASURY_OPENING_ATOMIC = 5_000_000 * 1_000_000


class SimRevert(Exception):
    """Raised the way an eth_call or transaction revert surfaces from an RPC node.

    The message always contains "execution reverted" so the x402 SDK's
    is_contract_revert() and parse_eip3009_transfer_error() classify it the same
    way they classify a real FiatTokenV2 revert.
    """


@dataclass
class SimTransfer:
    tx_hash: str
    block_number: int
    asset: str
    from_address: str
    to_address: str
    value: int
    nonce: str
    payload_sha256: str | None
    at: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS balances (
    asset TEXT NOT NULL,
    address TEXT NOT NULL,
    atomic INTEGER NOT NULL,
    PRIMARY KEY (asset, address)
);
CREATE TABLE IF NOT EXISTS authorizations (
    asset TEXT NOT NULL,
    authorizer TEXT NOT NULL,
    nonce TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    used_at TEXT NOT NULL,
    PRIMARY KEY (asset, authorizer, nonce)
);
CREATE TABLE IF NOT EXISTS transfers (
    tx_hash TEXT PRIMARY KEY,
    block_number INTEGER NOT NULL,
    asset TEXT NOT NULL,
    from_address TEXT NOT NULL,
    to_address TEXT NOT NULL,
    value INTEGER NOT NULL,
    nonce TEXT NOT NULL,
    payload_sha256 TEXT,
    at TEXT NOT NULL,
    simulated INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class SimChain:
    """One simulated USDC contract state per SQLite file. Thread safe."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or os.path.join(data_dir(), "sim_chain.db")
        self._lock = threading.RLock()
        self._ensure()

    # -------------------------------------------------------------- plumbing

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _ensure(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with self._lock, self._connect() as conn:
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:
                pass
            conn.executescript(_SCHEMA)
            row = conn.execute("SELECT v FROM meta WHERE k='funded'").fetchone()
            if row is None:
                self._fund_treasury(conn)

    def _fund_treasury(self, conn: sqlite3.Connection) -> None:
        from elix.rails.keys import offline_treasury_account

        addr = offline_treasury_account().address.lower()
        conn.execute(
            "INSERT OR REPLACE INTO balances(asset, address, atomic) VALUES (?,?,?)",
            (USDC_ADDRESS.lower(), addr, TREASURY_OPENING_ATOMIC),
        )
        conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES ('funded', ?)", (_now(),))
        conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES ('block', '1')")

    def reset(self) -> None:
        """Wipe every balance, nonce and transfer, then re-fund the offline treasury."""
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM balances")
            conn.execute("DELETE FROM authorizations")
            conn.execute("DELETE FROM transfers")
            conn.execute("DELETE FROM meta")
            self._fund_treasury(conn)

    # -------------------------------------------------------------- reads

    def balance_of(self, address: str, asset: str = USDC_ADDRESS) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT atomic FROM balances WHERE asset=? AND address=?",
                (asset.lower(), address.lower()),
            ).fetchone()
        return int(row["atomic"]) if row else 0

    def authorization_state(self, authorizer: str, nonce: str, asset: str = USDC_ADDRESS) -> bool:
        """True when the (authorizer, nonce) pair was already used, like FiatTokenV2.authorizationState."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM authorizations WHERE asset=? AND authorizer=? AND nonce=?",
                (asset.lower(), authorizer.lower(), _norm_nonce(nonce)),
            ).fetchone()
        return row is not None

    def transfer(self, tx_hash: str) -> SimTransfer | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM transfers WHERE tx_hash=?", (tx_hash.lower(),)).fetchone()
        return _row_to_transfer(row) if row else None

    def transfers(self, limit: int = 100) -> list[SimTransfer]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM transfers ORDER BY block_number DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [_row_to_transfer(r) for r in rows]

    def balances(self) -> dict[str, int]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT address, atomic FROM balances WHERE asset=?", (USDC_ADDRESS.lower(),)).fetchall()
        return {r["address"]: int(r["atomic"]) for r in rows}

    # -------------------------------------------------------------- writes

    def check_transfer(self, from_address: str, value: int, nonce: str, asset: str = USDC_ADDRESS) -> None:
        """Raise SimRevert with FiatTokenV2 style messages when a transfer would revert."""
        if self.authorization_state(from_address, nonce, asset):
            raise SimRevert("execution reverted: FiatTokenV2: authorization is used or canceled")
        if self.balance_of(from_address, asset) < int(value):
            raise SimRevert("execution reverted: ERC20: transfer amount exceeds balance")

    def settle_transfer(
        self,
        *,
        from_address: str,
        to_address: str,
        value: int,
        nonce: str,
        tx_hash: str,
        payload_sha256: str | None = None,
        asset: str = USDC_ADDRESS,
    ) -> SimTransfer:
        """Atomically mark the nonce used and move the balance. SIMULATED SETTLEMENT."""
        asset_l = asset.lower()
        frm, to = from_address.lower(), to_address.lower()
        n = _norm_nonce(nonce)
        value = int(value)
        if value <= 0:
            raise SimRevert("execution reverted: transfer value must be positive")
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                used = conn.execute(
                    "SELECT 1 FROM authorizations WHERE asset=? AND authorizer=? AND nonce=?", (asset_l, frm, n)
                ).fetchone()
                if used:
                    raise SimRevert("execution reverted: FiatTokenV2: authorization is used or canceled")
                if conn.execute("SELECT 1 FROM transfers WHERE tx_hash=?", (tx_hash.lower(),)).fetchone():
                    raise SimRevert("execution reverted: duplicate simulated transaction")
                row = conn.execute("SELECT atomic FROM balances WHERE asset=? AND address=?", (asset_l, frm)).fetchone()
                bal = int(row["atomic"]) if row else 0
                if bal < value:
                    raise SimRevert("execution reverted: ERC20: transfer amount exceeds balance")
                conn.execute(
                    "INSERT OR REPLACE INTO balances(asset, address, atomic) VALUES (?,?,?)", (asset_l, frm, bal - value)
                )
                row = conn.execute("SELECT atomic FROM balances WHERE asset=? AND address=?", (asset_l, to)).fetchone()
                conn.execute(
                    "INSERT OR REPLACE INTO balances(asset, address, atomic) VALUES (?,?,?)",
                    (asset_l, to, (int(row["atomic"]) if row else 0) + value),
                )
                at = _now()
                conn.execute(
                    "INSERT INTO authorizations(asset, authorizer, nonce, tx_hash, used_at) VALUES (?,?,?,?,?)",
                    (asset_l, frm, n, tx_hash.lower(), at),
                )
                brow = conn.execute("SELECT v FROM meta WHERE k='block'").fetchone()
                block = int(brow["v"]) + 1 if brow else 2
                conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES ('block', ?)", (str(block),))
                conn.execute(
                    "INSERT INTO transfers(tx_hash, block_number, asset, from_address, to_address, value, nonce,"
                    " payload_sha256, at, simulated) VALUES (?,?,?,?,?,?,?,?,?,1)",
                    (tx_hash.lower(), block, asset_l, frm, to, value, n, payload_sha256, at),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        return SimTransfer(tx_hash.lower(), block, asset_l, frm, to, value, n, payload_sha256, at)


def _norm_nonce(nonce: Any) -> str:
    if isinstance(nonce, (bytes, bytearray)):
        return "0x" + bytes(nonce).hex()
    s = str(nonce).lower()
    return s if s.startswith("0x") else "0x" + s


def _row_to_transfer(row: sqlite3.Row) -> SimTransfer:
    return SimTransfer(
        tx_hash=row["tx_hash"],
        block_number=int(row["block_number"]),
        asset=row["asset"],
        from_address=row["from_address"],
        to_address=row["to_address"],
        value=int(row["value"]),
        nonce=row["nonce"],
        payload_sha256=row["payload_sha256"],
        at=row["at"],
    )


_chains: dict[str, SimChain] = {}
_chains_lock = threading.Lock()


def get_chain(path: str | None = None) -> SimChain:
    """The SimChain for a path (default ELIX_DATA_DIR/sim_chain.db), cached per path."""
    p = os.path.abspath(path or os.path.join(data_dir(), "sim_chain.db"))
    with _chains_lock:
        chain = _chains.get(p)
        if chain is None or not os.path.exists(p):
            chain = SimChain(p)
            _chains[p] = chain
        return chain


def reset(path: str | None = None) -> None:
    get_chain(path).reset()
