"""Hash chain, append only protection, tamper detection, ledger invariants and R30."""

from __future__ import annotations

import sqlite3

import pytest

from elix.contracts import Denied, JournalLine
from elix.engine import audit
from elix.engine.errors import Invalid, PeriodClosed
from elix.engine.ledger import post_entry, reverse_entry

from conftest import buy


def test_chain_verifies_and_detects_a_tampered_row(seeded):
    check = seeded.verify_chain()
    assert check.ok and check.checked > 2000 and check.first_bad_seq is None
    conn = seeded.store.conn
    # An attacker with raw file access drops the guard trigger and rewrites history.
    conn.execute("DROP TRIGGER audit_append_only_update")
    conn.execute("UPDATE audit SET reason = 'Dev Okafor approved nothing.' WHERE seq = 500")
    broken = seeded.verify_chain()
    assert not broken.ok and broken.first_bad_seq == 500 and broken.checked == 499


def test_chain_detects_a_deleted_row(seeded):
    conn = seeded.store.conn
    conn.execute("DROP TRIGGER audit_append_only_delete")
    conn.execute("DELETE FROM audit WHERE seq = 42")
    broken = seeded.verify_chain()
    assert not broken.ok and broken.first_bad_seq == 43


def test_audit_log_is_append_only(seeded):
    with pytest.raises(sqlite3.DatabaseError, match="append only"):
        seeded.store.conn.execute("UPDATE audit SET allowed = 1 WHERE seq = 1")
    with pytest.raises(sqlite3.DatabaseError, match="append only"):
        seeded.store.conn.execute("DELETE FROM audit WHERE seq = 1")


def test_hash_covers_every_field(elix):
    event = audit.append_event(elix.store, at="2026-10-24T16:00:00Z", actor="system:sync", action="x",
                               subject="s", allowed=True, reason="r", diff={"a": [1, 2]}, evidence={"e": 1})
    base = dict(seq=event.seq, at=event.at, actor=event.actor, action=event.action, subject=event.subject,
                allowed=event.allowed, reason=event.reason, diff=event.diff, evidence=event.evidence,
                prev_hash=event.prev_hash)
    assert audit.compute_hash(**base) == event.hash
    for field, value in (("actor", "human:dev"), ("allowed", False), ("reason", "other"), ("diff", {}),
                         ("evidence", {"e": 2}), ("prev_hash", "f" * 64)):
        assert audit.compute_hash(**{**base, field: value}) != event.hash


def test_why_answers_with_events_actors_and_sentences(seeded):
    item = buy(seeded, "agent:atlas", "ven_terrafield", "scene-10m", 2)
    seeded.approve("human:dev", item.id, "Fine for training")
    report = seeded.why(item.id)
    actions = [s.action for s in report.steps]
    assert actions[:4] == ["propose", "evaluate", "escalate", "approve"]
    assert "pay" in actions and "post" in actions
    assert any(s.actor_name == "Dev Okafor" for s in report.steps)
    assert report.answer.startswith("2 scenes at 10 m from Terrafield Imagery is paid and posted.")
    assert "—" not in report.answer and all("—" not in s.sentence for s in report.steps)


# --------------------------------------------------------------- ledger


def test_every_entry_balances_and_is_positive(seeded):
    for entry in seeded.store.entries():
        debits = sum(l.debit_cents for l in entry.lines)
        credits = sum(l.credit_cents for l in entry.lines)
        assert debits == credits > 0, entry.id


def test_unbalanced_entry_is_refused(elix):
    with pytest.raises(Invalid, match="does not balance"):
        post_entry(elix.ctx, date="2026-10-24", memo="bad", lines=[
            JournalLine(account="6110", debit_cents=100), JournalLine(account="1015", credit_cents=99)])
    with pytest.raises(Invalid):
        post_entry(elix.ctx, date="2026-10-24", memo="bad", lines=[JournalLine(account="9999", debit_cents=1),
                                                                   JournalLine(account="1015", credit_cents=1)])


def test_only_the_gateway_can_post(elix):
    with pytest.raises(Denied) as err:
        post_entry(elix.ctx, date="2026-10-24", memo="sneaky", actor="human:priya", lines=[
            JournalLine(account="6110", debit_cents=100), JournalLine(account="1015", credit_cents=100)])
    assert err.value.rule_id == "R31"
    assert audit.events(elix.store, limit=1)[0].action == "denied:post"


def test_journal_lines_never_change(seeded):
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        seeded.store.conn.execute("UPDATE journal_lines SET debit_cents = 1")
    with pytest.raises(sqlite3.DatabaseError):
        seeded.store.conn.execute("DELETE FROM journal_entries")
    with pytest.raises(sqlite3.DatabaseError):
        seeded.store.conn.execute("UPDATE journal_entries SET memo = 'changed'")


def test_reversal_links_both_ways_and_only_once(elix):
    entry = post_entry(elix.ctx, date="2026-10-24", memo="test", lines=[
        JournalLine(account="6110", debit_cents=500), JournalLine(account="1015", credit_cents=500)])
    reversal = reverse_entry(elix.ctx, entry.id, date="2026-10-24", memo="undo")
    assert reversal.reverses == entry.id
    assert elix.store.get_entry(entry.id).reversed_by == reversal.id
    assert reversal.lines[0].credit_cents == 500 and reversal.lines[1].debit_cents == 500
    with pytest.raises(Invalid, match="already reversed"):
        reverse_entry(elix.ctx, entry.id, date="2026-10-24", memo="again")
    with pytest.raises(sqlite3.DatabaseError):
        elix.store.conn.execute("UPDATE journal_entries SET reversed_by = 'JE-x' WHERE id = ?", (entry.id,))


def test_r30_posting_into_a_closed_period_is_refused(seeded):
    with pytest.raises(PeriodClosed) as err:
        post_entry(seeded.ctx, date="2026-09-15", memo="late", lines=[
            JournalLine(account="6110", debit_cents=100), JournalLine(account="1015", credit_cents=100)])
    assert err.value.rule_id == "R30" and err.value.audit_seq
    event = audit.events(seeded.store, limit=1)[0]
    assert event.action == "denied:post" and event.evidence["rule_id"] == "R30" and not event.allowed
    assert seeded.verify_chain().ok


def test_trial_balance_balances_for_every_period(seeded):
    for period in ("2026-07", "2026-08", "2026-09", "2026-10"):
        assert seeded.trial_balance_ok(period)
    balances = {b.account: b for b in seeded.balances("2026-10")}
    assert balances["1015"].balance_cents > 0  # the USDC wallet never went negative


def test_closing_the_current_month_rolls_new_postings_forward(seeded):
    """If someone closes October on October 24, today's payments post to November instead of failing R30."""
    for item in seeded.inbox("human:dev") + seeded.inbox("human:maya"):
        owner = seeded.config.agent(item.requested_by).owner_id
        seeded.reject(owner, item.id, "Clearing the inbox before close")
    for accrual in seeded.close_preview("human:priya", "2026-10").accruals:
        seeded.approve("human:priya", accrual.id)
    assert seeded.close_period("human:priya", "2026-10").status == "closed"
    item = buy(seeded, "agent:scout", "ven_parsec", "query-1k", 1, task_ref="after-close")
    entry = seeded.store.get_entry(item.journal_entry_ids[0])
    assert item.state.value == "POSTED" and item.period == "2026-11"
    assert entry.period == "2026-11" and entry.date == "2026-11-01"
    assert seeded.trial_balance_ok("2026-11")
