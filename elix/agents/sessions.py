"""Agent session records and transcripts in a sidecar SQLite file (ELIX_DATA_DIR/sessions.db)."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from typing import Any

_lock = threading.RLock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    task TEXT NOT NULL,
    mode TEXT NOT NULL,
    model TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL,
    request_ids TEXT NOT NULL DEFAULT '[]',
    summary TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS transcript (
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    at TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT,
    tool TEXT,
    args TEXT,
    result TEXT,
    PRIMARY KEY (session_id, seq)
);
"""


def _path() -> str:
    d = os.path.abspath(os.environ.get("ELIX_DATA_DIR") or "./data")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "sessions.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_path(), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


_clock = None


def set_clock(clock) -> None:
    """Optional: stamp transcripts with the server's world clock (an object with now() or a callable)."""
    global _clock
    _clock = clock


def now() -> str:
    if _clock is not None:
        try:
            from elix.rails.constants import now_iso

            return now_iso(_clock)
        except Exception:
            pass
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _norm(agent_id: str) -> str:
    a = agent_id.strip().lower()
    return a if a.startswith("agent:") else f"agent:{a}"


def create_session(agent_id: str, task: str, mode: str, model: str | None = None) -> str:
    sid = "ses-" + uuid.uuid4().hex[:10]
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO sessions(id, agent_id, task, mode, model, started_at, status) VALUES (?,?,?,?,?,?,?)",
                (sid, _norm(agent_id), task, mode, model, now(), "running"),
            )
            conn.commit()
        finally:
            conn.close()
    return sid


def append(session_id: str, role: str, text: str | None = None, tool: str | None = None,
           args: Any = None, result: Any = None) -> None:
    with _lock:
        conn = _connect()
        try:
            row = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM transcript WHERE session_id=?", (session_id,)).fetchone()
            seq = int(row[0]) + 1
            conn.execute(
                "INSERT INTO transcript(session_id, seq, at, role, text, tool, args, result) VALUES (?,?,?,?,?,?,?,?)",
                (session_id, seq, now(), role, text, tool,
                 json.dumps(args, default=str) if args is not None else None,
                 json.dumps(result, default=str) if result is not None else None),
            )
            conn.commit()
        finally:
            conn.close()
    _notify(session_id, role)


def add_request(session_id: str, item_id: str) -> None:
    with _lock:
        conn = _connect()
        try:
            row = conn.execute("SELECT request_ids FROM sessions WHERE id=?", (session_id,)).fetchone()
            ids = json.loads(row["request_ids"]) if row else []
            if item_id not in ids:
                ids.append(item_id)
            conn.execute("UPDATE sessions SET request_ids=? WHERE id=?", (json.dumps(ids), session_id))
            conn.commit()
        finally:
            conn.close()


def update(session_id: str, **fields: Any) -> None:
    allowed = {"mode", "model", "ended_at", "status", "summary", "error"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                f"UPDATE sessions SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?",
                (*sets.values(), session_id),
            )
            conn.commit()
        finally:
            conn.close()
    _notify(session_id, "status")


def finish(session_id: str, status: str, summary: str | None = None, error: str | None = None) -> None:
    update(session_id, status=status, ended_at=now(), summary=summary, error=error)


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "agent_id": row["agent_id"],
        "task": row["task"],
        "mode": row["mode"],
        "mode_label": "Scripted agent (offline)" if row["mode"] == "scripted" else "Claude agent",
        "model": row["model"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "status": row["status"],
        "request_ids": json.loads(row["request_ids"] or "[]"),
        "summary": row["summary"],
        "error": row["error"],
    }


def list_sessions(agent_id: str | None = None) -> list[dict]:
    with _lock:
        conn = _connect()
        try:
            if agent_id:
                rows = conn.execute(
                    "SELECT * FROM sessions WHERE agent_id=? ORDER BY started_at DESC, rowid DESC", (_norm(agent_id),)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM sessions ORDER BY started_at DESC, rowid DESC").fetchall()
        finally:
            conn.close()
    return [_row(r) for r in rows]


def get_session(session_id: str) -> dict | None:
    with _lock:
        conn = _connect()
        try:
            row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if row is None:
                return None
            trows = conn.execute(
                "SELECT * FROM transcript WHERE session_id=? ORDER BY seq", (session_id,)
            ).fetchall()
        finally:
            conn.close()
    out = _row(row)
    transcript = []
    for t in trows:
        entry: dict[str, Any] = {"seq": t["seq"], "at": t["at"], "role": t["role"], "text": t["text"]}
        if t["tool"]:
            entry["tool"] = t["tool"]
        if t["args"] is not None:
            entry["args"] = json.loads(t["args"])
        if t["result"] is not None:
            entry["result"] = json.loads(t["result"])
        transcript.append(entry)
    out["transcript"] = transcript
    return out


def reset() -> None:
    with _lock:
        conn = _connect()
        try:
            conn.execute("DELETE FROM transcript")
            conn.execute("DELETE FROM sessions")
            conn.commit()
        finally:
            conn.close()


# Optional listener so the server can push transcript updates over SSE:
# set_listener(lambda session_id, role: events.publish("agent_session", {...}))
_listener = None


def set_listener(fn) -> None:
    global _listener
    _listener = fn


def _notify(session_id: str, role: str) -> None:
    fn = _listener
    if fn is None:
        return
    try:
        fn(session_id, role)
    except Exception:
        pass
