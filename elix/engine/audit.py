"""The append only, hash chained audit log.

Each event's hash covers its own fields and the previous event's hash, so
changing any stored event (or removing one) breaks every hash after it.
`verify_chain` recomputes the whole chain from the stored rows.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Optional

from elix.contracts import AuditEvent, ChainCheck
from elix.engine.store import Store

GENESIS_HASH = "0" * 64


def to_jsonable(value: Any) -> Any:
    """Normalize a value to plain JSON types so the stored text hashes the same when re-read."""
    return json.loads(json.dumps(value, default=_default, sort_keys=True))


def _default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, (set, frozenset, tuple)):
        return sorted(value) if isinstance(value, (set, frozenset)) else list(value)
    return str(value)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_hash(seq: int, at: str, actor: str, action: str, subject: str, allowed: bool, reason: str,
                 diff: dict, evidence: dict, prev_hash: str) -> str:
    material = canonical([seq, at, actor, action, subject, bool(allowed), reason, diff, evidence, prev_hash])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def append_event(
    store: Store,
    *,
    at: str,
    actor: str,
    action: str,
    subject: str,
    allowed: bool,
    reason: str,
    diff: Optional[dict] = None,
    evidence: Optional[dict] = None,
    refs: Iterable[str] = (),
) -> AuditEvent:
    """Append one event. Call inside the same transaction as the change it records."""
    diff = to_jsonable(diff or {})
    evidence = to_jsonable(evidence or {})
    last = store.last_audit()
    seq = (last["seq"] + 1) if last else 1
    prev_hash = last["hash"] if last else GENESIS_HASH
    # Timestamps never run backwards in the log, even if the world clock is set back.
    if last and at < last["at"]:
        at = last["at"]
    digest = compute_hash(seq, at, actor, action, subject, allowed, reason, diff, evidence, prev_hash)
    store.insert_audit(
        {
            "seq": seq, "at": at, "actor": actor, "action": action, "subject": subject,
            "allowed": 1 if allowed else 0, "reason": reason, "diff": canonical(diff),
            "evidence": canonical(evidence), "prev_hash": prev_hash, "hash": digest,
        },
        refs,
    )
    store.set_meta("clock", at)
    return AuditEvent(seq=seq, at=at, actor=actor, action=action, subject=subject, allowed=allowed,
                      reason=reason, diff=diff, evidence=evidence, prev_hash=prev_hash, hash=digest)


def row_to_event(row: Any) -> AuditEvent:
    return AuditEvent(
        seq=row["seq"], at=row["at"], actor=row["actor"], action=row["action"], subject=row["subject"],
        allowed=bool(row["allowed"]), reason=row["reason"], diff=json.loads(row["diff"]),
        evidence=json.loads(row["evidence"]), prev_hash=row["prev_hash"], hash=row["hash"],
    )


def verify_chain(store: Store) -> ChainCheck:
    prev_hash = GENESIS_HASH
    checked = 0
    expected_seq = 1
    for row in store.iter_audit():
        if row["seq"] != expected_seq or row["prev_hash"] != prev_hash:
            return ChainCheck(ok=False, checked=checked, first_bad_seq=row["seq"], head_hash=prev_hash)
        digest = compute_hash(
            row["seq"], row["at"], row["actor"], row["action"], row["subject"], bool(row["allowed"]),
            row["reason"], json.loads(row["diff"]), json.loads(row["evidence"]), row["prev_hash"],
        )
        if digest != row["hash"]:
            return ChainCheck(ok=False, checked=checked, first_bad_seq=row["seq"], head_hash=prev_hash)
        prev_hash = row["hash"]
        expected_seq += 1
        checked += 1
    return ChainCheck(ok=True, checked=checked, first_bad_seq=None, head_hash=prev_hash)


def events(store: Store, **filters: Any) -> list[AuditEvent]:
    return [row_to_event(r) for r in store.audit_rows(**filters)]
