"""SQLite persistence.

One connection, explicit transactions. Work items are stored as JSON with the
columns the rules query pulled out and indexed. The audit log and journal lines
are protected by triggers so that even a bug elsewhere in Elix cannot update or
delete them. The only permitted change to a journal entry is setting
`reversed_by` once, when its reversal posts.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional

from elix.contracts import JournalEntry, JournalLine, Period, Policy, Vendor, WorkItem

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS vendors (id TEXT PRIMARY KEY, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS periods (
    id TEXT PRIMARY KEY, status TEXT NOT NULL, closed_by TEXT, closed_at TEXT
);
CREATE TABLE IF NOT EXISTS work_items (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    state TEXT NOT NULL,
    period TEXT NOT NULL,
    agent_id TEXT,
    vendor_id TEXT,
    sku TEXT,
    quantity REAL,
    amount_cents INTEGER NOT NULL,
    exposure_cents INTEGER NOT NULL,
    idempotency_key TEXT,
    task_ref TEXT,
    invoice_number TEXT,
    proposed_by TEXT,
    approved_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    paid_at TEXT,
    paid_cents INTEGER,
    json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS wi_agent_vendor ON work_items (agent_id, vendor_id, created_at);
CREATE INDEX IF NOT EXISTS wi_state ON work_items (state, kind);
CREATE INDEX IF NOT EXISTS wi_period ON work_items (period, kind);
CREATE INDEX IF NOT EXISTS wi_idem ON work_items (agent_id, idempotency_key);
CREATE INDEX IF NOT EXISTS wi_invoice ON work_items (vendor_id, invoice_number);
CREATE TABLE IF NOT EXISTS item_links (item_id TEXT NOT NULL, rel TEXT NOT NULL, target TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS links_target ON item_links (target);
CREATE INDEX IF NOT EXISTS links_item ON item_links (item_id);

CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    subject TEXT NOT NULL,
    allowed INTEGER NOT NULL,
    reason TEXT NOT NULL,
    diff TEXT NOT NULL,
    evidence TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_subject ON audit (subject);
CREATE INDEX IF NOT EXISTS audit_actor ON audit (actor);
CREATE TABLE IF NOT EXISTS audit_refs (seq INTEGER NOT NULL, ref TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS audit_refs_ref ON audit_refs (ref);

CREATE TABLE IF NOT EXISTS journal_entries (
    id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    period TEXT NOT NULL,
    memo TEXT NOT NULL,
    source_work_item TEXT,
    reverses TEXT,
    reversed_by TEXT,
    posted_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    ord INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS je_period ON journal_entries (period);
CREATE INDEX IF NOT EXISTS je_source ON journal_entries (source_work_item);
CREATE TABLE IF NOT EXISTS journal_lines (
    je_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    account TEXT NOT NULL,
    debit_cents INTEGER NOT NULL,
    credit_cents INTEGER NOT NULL,
    cost_center TEXT,
    vendor_id TEXT,
    memo TEXT,
    PRIMARY KEY (je_id, idx)
);
CREATE INDEX IF NOT EXISTS jl_account ON journal_lines (account);

CREATE TABLE IF NOT EXISTS policies (id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meters (
    id INTEGER PRIMARY KEY,
    vendor_id TEXT NOT NULL,
    sku TEXT NOT NULL,
    agent_id TEXT,
    quantity REAL NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS meters_vendor ON meters (vendor_id, sku, window_start);
CREATE TABLE IF NOT EXISTS recodes (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    je_id TEXT NOT NULL,
    line_index INTEGER NOT NULL,
    agent_id TEXT,
    vendor_id TEXT,
    from_account TEXT NOT NULL,
    to_account TEXT NOT NULL,
    correction_item TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payee_verifications (
    id INTEGER PRIMARY KEY,
    vendor_id TEXT NOT NULL,
    address TEXT NOT NULL,
    verified_by TEXT NOT NULL,
    at TEXT NOT NULL,
    method TEXT NOT NULL,
    note TEXT NOT NULL,
    work_item_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scenario_runs (id TEXT PRIMARY KEY, at TEXT NOT NULL, result_ids TEXT NOT NULL, message TEXT NOT NULL);

CREATE TRIGGER IF NOT EXISTS audit_append_only_update BEFORE UPDATE ON audit
BEGIN SELECT RAISE(ABORT, 'the audit log is append only'); END;
CREATE TRIGGER IF NOT EXISTS audit_append_only_delete BEFORE DELETE ON audit
BEGIN SELECT RAISE(ABORT, 'the audit log is append only'); END;
CREATE TRIGGER IF NOT EXISTS journal_lines_no_update BEFORE UPDATE ON journal_lines
BEGIN SELECT RAISE(ABORT, 'journal lines are immutable'); END;
CREATE TRIGGER IF NOT EXISTS journal_lines_no_delete BEFORE DELETE ON journal_lines
BEGIN SELECT RAISE(ABORT, 'journal lines are immutable'); END;
CREATE TRIGGER IF NOT EXISTS journal_entries_no_delete BEFORE DELETE ON journal_entries
BEGIN SELECT RAISE(ABORT, 'journal entries are never deleted'); END;
CREATE TRIGGER IF NOT EXISTS journal_entries_reversed_by_only BEFORE UPDATE ON journal_entries
WHEN OLD.reversed_by IS NOT NULL
  OR NEW.id IS NOT OLD.id OR NEW.date IS NOT OLD.date OR NEW.period IS NOT OLD.period
  OR NEW.memo IS NOT OLD.memo OR NEW.source_work_item IS NOT OLD.source_work_item
  OR NEW.reverses IS NOT OLD.reverses OR NEW.posted_by IS NOT OLD.posted_by
  OR NEW.created_at IS NOT OLD.created_at OR NEW.ord IS NOT OLD.ord
BEGIN SELECT RAISE(ABORT, 'a journal entry only changes once, when its reversal posts'); END;
"""

OPEN_STATES = ("NEW", "PROPOSED", "NEEDS_INFO", "NEEDS_APPROVAL", "APPROVED")


def _agent_of(item: WorkItem) -> Optional[str]:
    if item.requested_by.startswith("agent:"):
        return item.requested_by
    agent = item.data.get("agent_id")
    return agent if isinstance(agent, str) else None


def item_number(item_id: str) -> int:
    """WI-1042 -> 1042, so ids compare in creation order past WI-9999."""
    return int(item_id.rsplit("-", 1)[-1])


def exposure_cents(item: WorkItem) -> int:
    """The most this item could cost: the larger of what was asked and what the vendor's 402 asks."""
    challenged = item.challenge.amount_cents if item.challenge else 0
    return max(item.amount_cents, challenged)


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self._depth = 0
        self._on_commit: list[Callable[[], None]] = []
        self._durable: list[Callable[[], None]] = []
        self._thread_lock = threading.RLock()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------ transactions

    @contextmanager
    def tx(self) -> Iterator["Store"]:
        """A transaction. Nested calls join the outer one; the outermost commits."""
        with self._thread_lock:
            outermost = self._depth == 0
            if outermost:
                self.conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self
            except BaseException:
                self._depth -= 1
                if outermost:
                    self.conn.execute("ROLLBACK")
                    self._on_commit.clear()
                    durable, self._durable = self._durable, []
                    for write in durable:
                        with self.tx():
                            write()
                raise
            else:
                self._depth -= 1
                if outermost:
                    self.conn.execute("COMMIT")
                    callbacks, self._on_commit = self._on_commit, []
                    self._durable.clear()
                    for callback in callbacks:
                        callback()

    @property
    def in_tx(self) -> bool:
        return self._depth > 0

    def on_commit(self, callback: Callable[[], None]) -> None:
        if self._depth == 0:
            callback()
        else:
            self._on_commit.append(callback)

    def durable(self, write: Callable[[], None]) -> None:
        """Run a write that must survive even if the surrounding transaction rolls back.

        Used for denied attempts: the caller raises right after, which rolls back
        the outer transaction, and the denial is then written again on its own.
        """
        if self._depth == 0:
            with self.tx():
                write()
        else:
            write()
            self._durable.append(write)

    # ------------------------------------------------------------ meta and counters

    def get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def next_counter(self, name: str, start: int = 1) -> int:
        row = self.conn.execute("SELECT value FROM counters WHERE name = ?", (name,)).fetchone()
        value = start if row is None else row["value"] + 1
        self.conn.execute(
            "INSERT INTO counters (name, value) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET value = excluded.value",
            (name, value),
        )
        return value

    # ------------------------------------------------------------ vendors

    def save_vendor(self, vendor: Vendor) -> None:
        self.conn.execute(
            "INSERT INTO vendors (id, json) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET json = excluded.json",
            (vendor.id, vendor.model_dump_json()),
        )

    def get_vendor(self, vendor_id: str) -> Optional[Vendor]:
        row = self.conn.execute("SELECT json FROM vendors WHERE id = ?", (vendor_id,)).fetchone()
        return Vendor.model_validate_json(row["json"]) if row else None

    def vendors(self) -> list[Vendor]:
        rows = self.conn.execute("SELECT json FROM vendors ORDER BY id").fetchall()
        return [Vendor.model_validate_json(r["json"]) for r in rows]

    # ------------------------------------------------------------ periods

    def get_period(self, period_id: str) -> Optional[Period]:
        row = self.conn.execute("SELECT * FROM periods WHERE id = ?", (period_id,)).fetchone()
        return Period(id=row["id"], status=row["status"], closed_by=row["closed_by"], closed_at=row["closed_at"]) if row else None

    def save_period(self, period: Period) -> None:
        self.conn.execute(
            "INSERT INTO periods (id, status, closed_by, closed_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET status = excluded.status, closed_by = excluded.closed_by, closed_at = excluded.closed_at",
            (period.id, period.status, period.closed_by, period.closed_at),
        )

    def periods(self) -> list[Period]:
        rows = self.conn.execute("SELECT * FROM periods ORDER BY id").fetchall()
        return [Period(id=r["id"], status=r["status"], closed_by=r["closed_by"], closed_at=r["closed_at"]) for r in rows]

    # ------------------------------------------------------------ work items

    def save_item(self, item: WorkItem) -> None:
        paid_at = item.data.get("paid_at") if item.receipt else None
        self.conn.execute(
            """INSERT INTO work_items (id, kind, state, period, agent_id, vendor_id, sku, quantity, amount_cents,
                   exposure_cents, idempotency_key, task_ref, invoice_number, proposed_by, approved_by,
                   created_at, updated_at, paid_at, paid_cents, json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET kind = excluded.kind, state = excluded.state, period = excluded.period,
                   agent_id = excluded.agent_id, vendor_id = excluded.vendor_id, sku = excluded.sku,
                   quantity = excluded.quantity, amount_cents = excluded.amount_cents,
                   exposure_cents = excluded.exposure_cents, idempotency_key = excluded.idempotency_key,
                   task_ref = excluded.task_ref, invoice_number = excluded.invoice_number,
                   proposed_by = excluded.proposed_by, approved_by = excluded.approved_by,
                   created_at = excluded.created_at, updated_at = excluded.updated_at, paid_at = excluded.paid_at,
                   paid_cents = excluded.paid_cents, json = excluded.json""",
            (
                item.id, item.kind.value, item.state.value, item.period, _agent_of(item), item.vendor_id, item.sku,
                item.quantity, item.amount_cents, exposure_cents(item), item.idempotency_key, item.task_ref,
                item.data.get("invoice_number"), item.proposed_by, item.approved_by, item.created_at,
                item.updated_at, paid_at, item.receipt.amount_cents if item.receipt else None,
                item.model_dump_json(),
            ),
        )
        self.conn.execute("DELETE FROM item_links WHERE item_id = ?", (item.id,))
        if item.links:
            self.conn.executemany(
                "INSERT INTO item_links (item_id, rel, target) VALUES (?, ?, ?)",
                [(item.id, link.rel, link.id) for link in item.links],
            )

    def get_item(self, item_id: str) -> Optional[WorkItem]:
        row = self.conn.execute("SELECT json FROM work_items WHERE id = ?", (item_id,)).fetchone()
        return WorkItem.model_validate_json(row["json"]) if row else None

    def query_items(
        self,
        *,
        kind: Optional[str | Iterable[str]] = None,
        state: Optional[str | Iterable[str]] = None,
        not_state: Optional[Iterable[str]] = None,
        agent_id: Optional[str] = None,
        vendor_id: Optional[str] = None,
        sku: Optional[str] = None,
        period: Optional[str] = None,
        created_from: Optional[str] = None,
        created_to: Optional[str] = None,
        paid_period: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        invoice_number: Optional[str] = None,
        exclude_id: Optional[str] = None,
        order: str = "created_at DESC, id DESC",
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[WorkItem]:
        where, args = self._item_filters(
            kind=kind, state=state, not_state=not_state, agent_id=agent_id, vendor_id=vendor_id, sku=sku,
            period=period, created_from=created_from, created_to=created_to, paid_period=paid_period,
            idempotency_key=idempotency_key, invoice_number=invoice_number, exclude_id=exclude_id,
        )
        sql = f"SELECT json FROM work_items {where} ORDER BY {order}"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            args += [limit, offset]
        return [WorkItem.model_validate_json(r["json"]) for r in self.conn.execute(sql, args).fetchall()]

    def sum_items(self, column: str, **filters: Any) -> int:
        assert column in ("exposure_cents", "paid_cents", "amount_cents")
        where, args = self._item_filters(**filters)
        row = self.conn.execute(f"SELECT COALESCE(SUM({column}), 0) AS total FROM work_items {where}", args).fetchone()
        return int(row["total"])

    def _item_filters(self, **f: Any) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        args: list[Any] = []

        def add_in(column: str, value: Any, negate: bool = False) -> None:
            values = [value] if isinstance(value, str) else list(value)
            marks = ",".join("?" for _ in values)
            clauses.append(f"{column} {'NOT IN' if negate else 'IN'} ({marks})")
            args.extend(values)

        if f.get("kind") is not None:
            add_in("kind", f["kind"])
        if f.get("state") is not None:
            add_in("state", f["state"])
        if f.get("not_state") is not None:
            add_in("state", f["not_state"], negate=True)
        for key, column in (("agent_id", "agent_id"), ("vendor_id", "vendor_id"), ("sku", "sku"),
                            ("period", "period"), ("idempotency_key", "idempotency_key"),
                            ("invoice_number", "invoice_number")):
            if f.get(key) is not None:
                clauses.append(f"{column} = ?")
                args.append(f[key])
        if f.get("created_from") is not None:
            clauses.append("created_at >= ?")
            args.append(f["created_from"])
        if f.get("created_to") is not None:
            clauses.append("created_at <= ?")
            args.append(f["created_to"])
        if f.get("paid_period") is not None:
            clauses.append("paid_at LIKE ?")
            args.append(f["paid_period"] + "%")
        if f.get("exclude_id") is not None:
            clauses.append("id != ?")
            args.append(f["exclude_id"])
        return ("WHERE " + " AND ".join(clauses)) if clauses else "", args

    def items_linking_to(self, target: str) -> list[str]:
        rows = self.conn.execute("SELECT DISTINCT item_id FROM item_links WHERE target = ?", (target,)).fetchall()
        return [r["item_id"] for r in rows]

    # ------------------------------------------------------------ audit rows

    def last_audit(self) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT seq, hash, at FROM audit ORDER BY seq DESC LIMIT 1").fetchone()

    def insert_audit(self, row: dict[str, Any], refs: Iterable[str]) -> None:
        self.conn.execute(
            "INSERT INTO audit (seq, at, actor, action, subject, allowed, reason, diff, evidence, prev_hash, hash) "
            "VALUES (:seq, :at, :actor, :action, :subject, :allowed, :reason, :diff, :evidence, :prev_hash, :hash)",
            row,
        )
        unique = sorted({r for r in refs if r and r != row["subject"]})
        if unique:
            self.conn.executemany("INSERT INTO audit_refs (seq, ref) VALUES (?, ?)", [(row["seq"], r) for r in unique])

    def audit_rows(
        self,
        *,
        subjects: Optional[Iterable[str]] = None,
        actor: Optional[str] = None,
        action: Optional[str] = None,
        allowed: Optional[bool] = None,
        before_seq: Optional[int] = None,
        limit: Optional[int] = None,
        ascending: bool = False,
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        args: list[Any] = []
        if subjects is not None:
            ids = list(subjects)
            if not ids:
                return []
            marks = ",".join("?" for _ in ids)
            clauses.append(f"(subject IN ({marks}) OR seq IN (SELECT seq FROM audit_refs WHERE ref IN ({marks})))")
            args.extend(ids + ids)
        if actor is not None:
            clauses.append("actor = ?")
            args.append(actor)
        if action is not None:
            clauses.append("action = ?")
            args.append(action)
        if allowed is not None:
            clauses.append("allowed = ?")
            args.append(1 if allowed else 0)
        if before_seq is not None:
            clauses.append("seq < ?")
            args.append(before_seq)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM audit {where} ORDER BY seq {'ASC' if ascending else 'DESC'}"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        return self.conn.execute(sql, args).fetchall()

    def iter_audit(self) -> Iterator[sqlite3.Row]:
        return iter(self.conn.execute("SELECT * FROM audit ORDER BY seq ASC"))

    def audit_refs_for(self, seqs: list[int]) -> dict[int, list[str]]:
        if not seqs:
            return {}
        marks = ",".join("?" for _ in seqs)
        out: dict[int, list[str]] = {}
        for row in self.conn.execute(f"SELECT seq, ref FROM audit_refs WHERE seq IN ({marks})", seqs):
            out.setdefault(row["seq"], []).append(row["ref"])
        return out

    # ------------------------------------------------------------ journal

    def insert_entry(self, entry: JournalEntry) -> None:
        ord_value = self.next_counter("je_order")
        self.conn.execute(
            "INSERT INTO journal_entries (id, date, period, memo, source_work_item, reverses, reversed_by, posted_by, created_at, ord) "
            "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)",
            (entry.id, entry.date, entry.period, entry.memo, entry.source_work_item, entry.reverses,
             entry.posted_by, entry.created_at, ord_value),
        )
        self.conn.executemany(
            "INSERT INTO journal_lines (je_id, idx, account, debit_cents, credit_cents, cost_center, vendor_id, memo) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(entry.id, i, l.account, l.debit_cents, l.credit_cents, l.cost_center, l.vendor_id, l.memo)
             for i, l in enumerate(entry.lines)],
        )

    def set_reversed_by(self, je_id: str, reversal_id: str) -> None:
        self.conn.execute("UPDATE journal_entries SET reversed_by = ? WHERE id = ?", (reversal_id, je_id))

    def get_entry(self, je_id: str) -> Optional[JournalEntry]:
        row = self.conn.execute("SELECT * FROM journal_entries WHERE id = ?", (je_id,)).fetchone()
        return self._entries_from_rows([row])[0] if row else None

    def entries(
        self,
        *,
        period: Optional[str] = None,
        account: Optional[str] = None,
        ids: Optional[Iterable[str]] = None,
        source_work_item: Optional[str] = None,
        through_period: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        newest_first: bool = True,
    ) -> list[JournalEntry]:
        where, args = self._entry_filters(period, account, ids, source_work_item, through_period)
        sql = f"SELECT * FROM journal_entries {where} ORDER BY ord {'DESC' if newest_first else 'ASC'}"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            args += [limit, offset]
        return self._entries_from_rows(self.conn.execute(sql, args).fetchall())

    def count_entries(self, *, period: Optional[str] = None, account: Optional[str] = None) -> int:
        where, args = self._entry_filters(period, account, None, None, None)
        return int(self.conn.execute(f"SELECT COUNT(*) AS n FROM journal_entries {where}", args).fetchone()["n"])

    def _entry_filters(self, period, account, ids, source_work_item, through_period) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        if period is not None:
            clauses.append("period = ?")
            args.append(period)
        if through_period is not None:
            clauses.append("period <= ?")
            args.append(through_period)
        if account is not None:
            clauses.append("id IN (SELECT je_id FROM journal_lines WHERE account = ?)")
            args.append(account)
        if ids is not None:
            id_list = list(ids)
            if not id_list:
                clauses.append("0")
            else:
                clauses.append(f"id IN ({','.join('?' for _ in id_list)})")
                args.extend(id_list)
        if source_work_item is not None:
            clauses.append("source_work_item = ?")
            args.append(source_work_item)
        return ("WHERE " + " AND ".join(clauses)) if clauses else "", args

    def _entries_from_rows(self, rows: list[sqlite3.Row]) -> list[JournalEntry]:
        if not rows:
            return []
        ids = [r["id"] for r in rows]
        lines: dict[str, list[JournalLine]] = {i: [] for i in ids}
        marks = ",".join("?" for _ in ids)
        for line in self.conn.execute(f"SELECT * FROM journal_lines WHERE je_id IN ({marks}) ORDER BY je_id, idx", ids):
            lines[line["je_id"]].append(JournalLine(
                account=line["account"], debit_cents=line["debit_cents"], credit_cents=line["credit_cents"],
                cost_center=line["cost_center"], vendor_id=line["vendor_id"], memo=line["memo"],
            ))
        return [
            JournalEntry(
                id=r["id"], date=r["date"], period=r["period"], memo=r["memo"], lines=lines[r["id"]],
                source_work_item=r["source_work_item"], reverses=r["reverses"], reversed_by=r["reversed_by"],
                posted_by=r["posted_by"], created_at=r["created_at"],
            )
            for r in rows
        ]

    def account_totals(self, *, period: Optional[str] = None, through_period: Optional[str] = None) -> dict[str, tuple[int, int]]:
        clauses, args = [], []
        if period is not None:
            clauses.append("e.period = ?")
            args.append(period)
        if through_period is not None:
            clauses.append("e.period <= ?")
            args.append(through_period)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.conn.execute(
            f"SELECT l.account, SUM(l.debit_cents) AS d, SUM(l.credit_cents) AS c FROM journal_lines l "
            f"JOIN journal_entries e ON e.id = l.je_id {where} GROUP BY l.account",
            args,
        ).fetchall()
        return {r["account"]: (int(r["d"]), int(r["c"])) for r in rows}

    def daily_line_totals(self, period: str, accounts: Iterable[str]) -> list[sqlite3.Row]:
        accts = list(accounts)
        marks = ",".join("?" for _ in accts)
        return self.conn.execute(
            f"SELECT e.date AS date, l.account AS account, SUM(l.debit_cents) AS d, SUM(l.credit_cents) AS c "
            f"FROM journal_lines l JOIN journal_entries e ON e.id = l.je_id "
            f"WHERE e.period = ? AND l.account IN ({marks}) GROUP BY e.date, l.account ORDER BY e.date",
            [period, *accts],
        ).fetchall()

    # ------------------------------------------------------------ policies

    def save_policy(self, policy: Policy) -> None:
        self.conn.execute(
            "INSERT INTO policies (id, kind, status, json) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET kind = excluded.kind, status = excluded.status, json = excluded.json",
            (policy.id, policy.kind, policy.status, policy.model_dump_json()),
        )

    def get_policy(self, policy_id: str) -> Optional[Policy]:
        row = self.conn.execute("SELECT json FROM policies WHERE id = ?", (policy_id,)).fetchone()
        return Policy.model_validate_json(row["json"]) if row else None

    def policies(self, status: Optional[str] = None, kind: Optional[str] = None) -> list[Policy]:
        clauses, args = [], []
        if status:
            clauses.append("status = ?")
            args.append(status)
        if kind:
            clauses.append("kind = ?")
            args.append(kind)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.conn.execute(f"SELECT json FROM policies {where} ORDER BY id", args).fetchall()
        return [Policy.model_validate_json(r["json"]) for r in rows]

    # ------------------------------------------------------------ meters

    def insert_meters(self, rows: list[tuple]) -> None:
        self.conn.executemany(
            "INSERT INTO meters (vendor_id, sku, agent_id, quantity, window_start, window_end, source) VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )

    def meters_overlapping(self, vendor_id: str, sku: str, start: str, end: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM meters WHERE vendor_id = ? AND sku = ? AND window_start < ? AND window_end > ? ORDER BY window_start",
            (vendor_id, sku, end, start),
        ).fetchall()

    # ------------------------------------------------------------ recodes and payee verifications

    def insert_recode(self, **row: Any) -> None:
        self.conn.execute(
            "INSERT INTO recodes (at, actor, je_id, line_index, agent_id, vendor_id, from_account, to_account, correction_item) "
            "VALUES (:at, :actor, :je_id, :line_index, :agent_id, :vendor_id, :from_account, :to_account, :correction_item)",
            row,
        )

    def recodes(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM recodes ORDER BY id").fetchall()

    def insert_payee_verification(self, **row: Any) -> None:
        self.conn.execute(
            "INSERT INTO payee_verifications (vendor_id, address, verified_by, at, method, note, work_item_id) "
            "VALUES (:vendor_id, :address, :verified_by, :at, :method, :note, :work_item_id)",
            row,
        )

    def payee_verified(self, vendor_id: str, address: str, since: Optional[str]) -> Optional[sqlite3.Row]:
        sql = "SELECT * FROM payee_verifications WHERE vendor_id = ? AND lower(address) = lower(?)"
        args: list[Any] = [vendor_id, address]
        if since:
            sql += " AND at >= ?"
            args.append(since)
        return self.conn.execute(sql + " ORDER BY id DESC LIMIT 1", args).fetchone()

    # ------------------------------------------------------------ scenario runs

    def save_scenario_run(self, scenario_id: str, at: str, result_ids: list[str], message: str) -> None:
        self.conn.execute(
            "INSERT INTO scenario_runs (id, at, result_ids, message) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET at = excluded.at, result_ids = excluded.result_ids, message = excluded.message",
            (scenario_id, at, json.dumps(result_ids), message),
        )

    def scenario_run(self, scenario_id: str) -> Optional[dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM scenario_runs WHERE id = ?", (scenario_id,)).fetchone()
        if not row:
            return None
        return {"at": row["at"], "result_ids": json.loads(row["result_ids"]), "message": row["message"]}
