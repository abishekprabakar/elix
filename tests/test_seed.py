"""Acceptance checks for the seeded Kestrel world (specs/09-acceptance.md)."""

from __future__ import annotations

import time

from elix.contracts import State
from elix.world.config import TODAY


def test_periods(seeded):
    status = {p.id: (p.status, p.closed_by) for p in seeded.periods()}
    assert status["2026-07"] == ("closed", "human:priya")
    assert status["2026-08"] == ("closed", "human:priya")
    assert status["2026-09"] == ("closed", "human:priya")
    assert status["2026-10"][0] == "open"
    closed_at = {p.id: p.closed_at[:10] for p in seeded.periods() if p.closed_at}
    assert closed_at == {"2026-07": "2026-08-06", "2026-08": "2026-09-04", "2026-09": "2026-10-06"}


def test_clock_and_chain(seeded):
    assert seeded.clock.now() == TODAY
    check = seeded.verify_chain()
    assert check.ok and check.checked > 2000


def test_trial_balance_every_period(seeded):
    for period in ("2026-07", "2026-08", "2026-09", "2026-10"):
        assert seeded.trial_balance_ok(period), period


def test_october_inbox(seeded):
    dev = seeded.inbox("human:dev")
    maya = seeded.inbox("human:maya")
    atlas_tf = [i for i in dev if i.requested_by == "agent:atlas" and i.vendor_id == "ven_terrafield"]
    forge = [i for i in dev if i.requested_by == "agent:forge" and i.sku == "h100-spot-hr"]
    assert len(atlas_tf) == 2 and len(forge) == 1 and len(dev) == 3
    assert len(maya) == 1 and maya[0].vendor_id == "ven_signalboard"
    assert seeded.inbox("human:priya") == [] and seeded.inbox("human:sam") == []
    assert all(i.period == "2026-10" for i in dev + maya)


def test_oct9_forge_spot_purchase_has_an_x402_receipt(seeded):
    oct9 = [i for i in seeded.store.query_items(kind="purchase", agent_id="agent:forge", sku="h100-spot-hr")
            if i.created_at.startswith("2026-10-09")]
    assert len(oct9) == 1
    item = oct9[0]
    assert item.state == State.POSTED and item.quantity == 600 and item.amount_cents == 126_000
    assert item.receipt.rail == "x402" and item.receipt.amount_cents == 126_000
    assert item.data["usage_window"] == {"start": "2026-10-09T00:00:00Z", "end": "2026-10-11T00:00:00Z"}
    assert item.approved_by == "human:dev"


def test_history_volumes_and_patterns(seeded):
    purchases = seeded.store.query_items(kind="purchase")
    closed = ("2026-07", "2026-08", "2026-09")
    terrafield = [i for i in purchases if i.sku == "scene-10m" and i.period in closed and i.state == State.POSTED]
    assert 200 <= len(terrafield) <= 240
    assert all(i.approved_by == "human:dev" for i in terrafield)
    rejected = [i for i in purchases if i.state == State.REJECTED]
    assert len(rejected) == 1 and rejected[0].sku == "scene-3m" and rejected[0].period == "2026-08"
    assert rejected[0].data["rejection"]["note"] == "We only use 10 m scenes for training."
    parsec = [i for i in purchases if i.vendor_id == "ven_parsec"]
    assert parsec and all(i.approved_by == "system:policy" for i in parsec)
    signalboard = [i for i in purchases if i.vendor_id == "ven_signalboard" and i.state == State.POSTED]
    assert all(i.account == "6110" for i in signalboard)


def test_invoices_reconcile_cleanly_and_were_approved_by_sam(seeded):
    invoices = seeded.store.query_items(kind="invoice")
    numbers = {i.data["invoice_number"] for i in invoices}
    for month in ("07", "08", "09"):
        assert {f"LUM-2026-{month}A", f"LUM-2026-{month}B", f"AWS-2026-{month}", f"ANT-2026-{month}"} <= numbers
    assert all(i.state == State.POSTED and not i.flags and i.approved_by == "human:sam" for i in invoices)
    assert all(i.receipt.rail == "bank" for i in invoices)


def test_accruals_at_each_close_with_reversals(seeded):
    accruals = seeded.store.query_items(kind="accrual", order="created_at ASC")
    assert [a.period for a in accruals] == ["2026-07", "2026-08", "2026-09"]
    for accrual in accruals:
        assert accrual.state == State.POSTED and accrual.vendor_id == "ven_anthropic"
        original, reversal = (seeded.store.get_entry(j) for j in accrual.journal_entry_ids)
        assert original.date.endswith(("-31", "-30")) and reversal.reverses == original.id
        assert reversal.date.endswith("-01") and original.reversed_by == reversal.id


def test_september_recodes(seeded):
    corrections = seeded.store.query_items(kind="correction")
    assert len(corrections) == 2
    assert all(c.state == State.POSTED and c.proposed_by == "human:sam" and c.approved_by == "human:priya"
               and c.data["to_account"] == "6210" for c in corrections)


def test_seed_is_deterministic_and_fast(tmp_path):
    from elix.rails import make_fake_registry
    from elix.world.seed import seed_world

    started = time.perf_counter()
    a = seed_world(tmp_path / "a.db", make_fake_registry(), seed=7)
    b = seed_world(tmp_path / "b.db", make_fake_registry(), seed=7)
    assert time.perf_counter() - started < 60
    assert a.elix.verify_chain().head_hash == b.elix.verify_chain().head_hash
    a.elix.close()
    b.elix.close()


def test_seed_through_the_real_offline_x402_rail(tmp_path, vendor_server):
    from elix.rails import make_registry, reset_offline_state
    from elix.world.seed import seed_world

    reset_offline_state(vendor_server)
    result = seed_world(tmp_path / "real.db", make_registry("offline", vendor_server))
    assert result.seconds < 60
    elix = result.elix
    oct9 = [i for i in elix.store.query_items(kind="purchase", agent_id="agent:forge", sku="h100-spot-hr")
            if i.created_at.startswith("2026-10-09")][0]
    assert oct9.receipt.tx_ref.startswith("sim-") and oct9.receipt.signature_verified is True
    assert oct9.receipt.payload_sha256 and oct9.receipt.settled_at.startswith("2026-10-09")
    assert not elix.store.query_items(kind="purchase", state="FAILED")
    assert elix.verify_chain().ok
    elix.close()


def test_atlas_has_room_for_the_archive_in_october(seeded):
    atlas = next(a for a in seeded.agents_status() if a["actor_id"] == "agent:atlas")
    assert 100_000 <= atlas["spent_cents"] <= 120_000
    assert atlas["spent_cents"] + atlas["committed_cents"] + 480_000 <= atlas["monthly_budget_cents"]


def test_accruals_match_their_invoices_so_inference_nets_to_zero(seeded):
    accruals = {a.period: a.amount_cents for a in seeded.store.query_items(kind="accrual")}
    invoices = {i.data["invoice"]["period"]: i.amount_cents
                for i in seeded.store.query_items(kind="invoice", vendor_id="ven_anthropic")}
    assert accruals == invoices
    october = {row["account"]: row["cents"] for row in seeded.summary("2026-10")["spend_by_account"]}
    assert "5120" not in october


def test_kept_world_restores_vendor_payouts(seeded, vendor_server):
    from elix import vendors
    from elix.contracts import PaymentIntent
    from elix.rails import make_registry
    from elix.rails.keys import vendor_address
    from elix.world.seed import restore_vendor_payouts

    new = vendor_address("ven_stormvault", 1)
    seeded.update_vendor_payout("ven_stormvault", new)
    rail = make_registry("offline", vendor_server).rail("x402")
    intent = PaymentIntent(work_item_id="WI-test", vendor_id="ven_stormvault", amount_cents=1300, sku="station-day",
                           quantity=2)
    try:
        assert rail.challenge(intent).pay_to == vendor_address("ven_stormvault", 0)
        assert restore_vendor_payouts(seeded.db_path, vendor_server) == ["ven_stormvault"]
        assert rail.challenge(intent).pay_to == new
    finally:
        vendors.reset(vendor_server)
