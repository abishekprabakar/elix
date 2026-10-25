"""Builds Kestrel Labs' history from July 1 to October 23 through the real engine.

Nothing is inserted directly. A deterministic generator plans every event
(purchases, approvals, meters, invoices, recodes, month end closes), sorts them
by time, and replays them through the facade with the world clock moved to each
event's time. The audit log and its hash chain are therefore genuine.
"""

from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from elix.contracts import InvoiceLine, JournalLine, MeterRecord, PurchaseRequest, UsageWindow, VendorInvoice
from elix.engine import Elix
from elix.engine.clock import Clock, iso
from elix.engine.fmt import cents_of
from elix.world.config import HISTORY_START, TODAY, VENDORS

PRICES = {(v.id, s.sku): s.unit_price_cents for v in VENDORS for s in v.skus}
HISTORY_END = date(2026, 10, 23)
OCT9_SPOT_HOURS = 600
OCT_A_METER_TOTAL = 2140  # on-demand hours Forge used Oct 1 to 15, including the 600 spot hours
# Atlas buys fewer scenes in October, so its spend before the $4,800 archive leaves room in its $6,000 budget
# and the payee change moment fails only R03 and R07, as spec 03 describes.
OCTOBER_ATLAS_PER_DAY = 2

COUNTIES = [("Buffalo", "NE"), ("Hall", "NE"), ("Adams", "NE"), ("Sedgwick", "KS"), ("Reno", "KS"),
            ("Finney", "KS"), ("Lancaster", "NE"), ("Custer", "OK"), ("Potter", "TX"), ("Weld", "CO")]
STATIONS = ["KUEX", "KOAX", "KGLD", "KDDC", "KICT", "KTWX", "KAMA", "KLBB", "KFTG", "KTLX"]
SEGMENTS = ["crop-hail carriers", "multi-peril crop insurers", "Kansas mutuals", "Nebraska agencies",
            "reinsurance brokers"]


def _dt(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)


def _days(start: date, end: date):
    cursor = start
    while cursor <= end:
        yield cursor
        cursor += timedelta(days=1)


def business_day(day: date) -> date:
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def nth_business_day(year: int, month: int, n: int) -> date:
    day, count = date(year, month, 1), 0
    while True:
        if day.weekday() < 5:
            count += 1
            if count == n:
                return day
        day += timedelta(days=1)


@dataclass(order=True)
class Event:
    at: datetime
    order: int
    kind: str = field(compare=False)
    data: dict = field(compare=False, default_factory=dict)


class Plan:
    """Collects events. `order` keeps ties stable and deterministic."""

    def __init__(self) -> None:
        self.events: list[Event] = []
        self._n = 0

    def add(self, at: datetime, kind: str, **data: Any) -> None:
        self._n += 1
        self.events.append(Event(at=at, order=self._n, kind=kind, data=data))

    def sorted(self) -> list[Event]:
        return sorted(self.events)


# ---------------------------------------------------------------- planning


def plan_history(seed: int = 7) -> Plan:
    rng = random.Random(seed)
    plan = Plan()
    start = date(2026, 7, 1)
    plan.add(_dt(start, 0, 0), "open_books")

    spot_hours_by_day: dict[date, float] = {}
    _plan_atlas(plan, rng, start)
    _plan_forge(plan, rng, start, spot_hours_by_day)
    _plan_scout(plan, rng, start)
    _plan_meters(plan, rng, start, spot_hours_by_day)
    _plan_recodes(plan)
    _plan_closes(plan)
    return plan


def _purchase(plan: Plan, at: datetime, ref: str, agent: str, vendor_id: str, sku: str, quantity: float,
              purpose: str, task_ref: str, *, approver: Optional[str], delay_min: int = 0,
              reject_note: Optional[str] = None, params: Optional[dict] = None) -> None:
    plan.add(at, "purchase", ref=ref, agent=agent, vendor_id=vendor_id, sku=sku, quantity=quantity,
             purpose=purpose, task_ref=task_ref, params=params or {})
    if approver and reject_note:
        plan.add(at + timedelta(minutes=delay_min), "reject", ref=ref, actor=approver, note=reject_note)
    elif approver:
        plan.add(at + timedelta(minutes=delay_min), "approve", ref=ref, actor=approver)


def _plan_atlas(plan: Plan, rng: random.Random, start: date) -> None:
    slots = [(9, 10), (10, 25), (11, 40), (13, 5), (14, 20), (15, 35), (16, 40)]
    for day in _days(start, HISTORY_END):
        if day.weekday() >= 5:
            continue
        if day == HISTORY_END:
            # Oct 23. The first request is approved; the last two are still waiting for Dev.
            chosen, pending_from = [(9, 10), (14, 20), (15, 35)], 1
        else:
            count = rng.choices([1, 2, 3, 4], weights=[5, 12, 23, 60])[0]
            chosen, pending_from = sorted(rng.sample(slots, count)), 99
        for index, (hour, minute) in enumerate(chosen):
            at = _dt(day, hour, minute + rng.randint(0, 9))
            county, state = rng.choice(COUNTIES)
            quantity = rng.choice([1, 1, 2])
            if day.month == 10 and day != HISTORY_END and index >= OCTOBER_ATLAS_PER_DAY:
                continue  # draws above still happen, so every other seeded value stays the same
            ref = f"atlas-tf-{day.isoformat()}-{index}"
            _purchase(plan, at, ref, "agent:atlas", "ven_terrafield", "scene-10m", quantity,
                      f"Training imagery for hail-v7 over {county} County, {state}",
                      f"hail-v7-{county.lower()}-{day.strftime('%m%d')}-{index}",
                      approver=None if index >= pending_from else "human:dev", delay_min=rng.randint(15, 110),
                      params={"aoi": f"{county.lower()}-{state.lower()}"})
        if day.weekday() == 1:  # Tuesdays, radar archive pulls
            quantity = rng.randint(10, 30)
            stations = ",".join(rng.sample(STATIONS, 3))
            _purchase(plan, _dt(day, 11, 5), f"atlas-sv-{day.isoformat()}", "agent:atlas", "ven_stormvault",
                      "station-day", quantity, f"Radar station days for hail-v7 label checks at {stations}",
                      f"hail-v7-radar-{day.strftime('%m%d')}", approver="human:dev", delay_min=rng.randint(30, 120),
                      params={"stations": stations})
    # The August scene-3m request Dev rejected. It keeps 3 m scenes out of learned auto approval.
    _purchase(plan, _dt(date(2026, 8, 12), 10, 48), "atlas-3m-rejected", "agent:atlas", "ven_terrafield", "scene-3m",
              1, "Sharper imagery to compare hail streaks near Grand Island", "hail-v7-gi-0812",
              approver="human:dev", delay_min=42, reject_note="We only use 10 m scenes for training.",
              params={"aoi": "hall-ne"})


def _forge_days(rng: random.Random, start: date) -> list[date]:
    days: list[date] = []
    week_start = start - timedelta(days=start.weekday())
    while week_start <= date(2026, 9, 30):
        options = [week_start + timedelta(days=d) for d in (0, 2, 4)]
        for day in sorted(rng.sample(options, rng.choice([2, 3]))):
            # Windows run two days; none may spill into October, so October's meter stays exact.
            if start <= day and day + timedelta(days=2) <= date(2026, 10, 1):
                days.append(day)
        week_start += timedelta(days=7)
    # October: only Oct 9 before the 16th, so Lumen's first half invoice has one x402 reservation to double bill.
    days += [date(2026, 10, 9), date(2026, 10, 16), date(2026, 10, 19), date(2026, 10, 21)]
    return days


def _plan_forge(plan: Plan, rng: random.Random, start: date, spot_hours: dict[date, float]) -> None:
    for day in _forge_days(rng, start):
        hours = OCT9_SPOT_HOURS if day == date(2026, 10, 9) else rng.randrange(100, 601, 20)
        window_start, window_end = _dt(day, 0), _dt(day + timedelta(days=2), 0)
        for offset in (0, 1):
            spot_hours[day + timedelta(days=offset)] = spot_hours.get(day + timedelta(days=offset), 0) + hours / 2
        _purchase(plan, _dt(day, 10, 15 + rng.randint(0, 20)), f"forge-spot-{day.isoformat()}", "agent:forge",
                  "ven_lumen", "h100-spot-hr", hours, "Spot GPU hours for the hail-v7 training run",
                  f"hail-v7-train-{day.strftime('%m%d')}", approver="human:dev", delay_min=rng.randint(15, 90),
                  params={"start": iso(window_start), "end": iso(window_end)})
    # Still waiting for Dev on Oct 23: the weekend retrain.
    pending = date(2026, 10, 23)
    _purchase(plan, _dt(pending, 15, 50), "forge-spot-pending", "agent:forge", "ven_lumen", "h100-spot-hr", 480,
              "Spot GPU hours to retrain hail-v7 on the new Nebraska scenes this weekend", "hail-v7-retrain-1024",
              approver=None, params={"start": iso(_dt(date(2026, 10, 24), 0)), "end": iso(_dt(date(2026, 10, 26), 0))})


def _plan_scout(plan: Plan, rng: random.Random, start: date) -> None:
    for day in _days(start, HISTORY_END):
        if day.weekday() in (1, 3):  # Tuesday and Thursday enrichment pulls
            records = rng.randrange(100, 301, 25)
            segment = rng.choice(SEGMENTS)
            pending = day == date(2026, 10, 22)
            _purchase(plan, _dt(day, 13, 30 + rng.randint(0, 20)) if not pending else _dt(day, 15, 5),
                      f"scout-sb-{day.isoformat()}", "agent:scout", "ven_signalboard", "firmo-record", records,
                      f"Firmographics for {segment} on the outreach list", f"gtm-outreach-{day.strftime('%m%d')}",
                      approver=None if pending else "human:maya", delay_min=rng.randint(20, 120),
                      params={"segment": segment.replace(" ", "-")})
        if day.weekday() == 0:  # Mondays, a few search packs, auto approved under Scout's $25 default
            packs = rng.randint(1, 4)
            _purchase(plan, _dt(day, 10, 2), f"scout-px-{day.isoformat()}", "agent:scout", "ven_parsec", "query-1k",
                      packs, "Search queries to find crop insurance carrier contacts",
                      f"gtm-search-{day.strftime('%m%d')}", approver=None)


def _plan_meters(plan: Plan, rng: random.Random, start: date, spot_hours: dict[date, float]) -> None:
    lumen_base: dict[date, int] = {}
    for day in _days(start, HISTORY_END):
        lumen_base[day] = rng.randint(70, 130)
    # Oct 1 to 15 on-demand meter totals exactly 2,140 hours including the 600 spot hours.
    october = [date(2026, 10, d) for d in range(1, 16)]
    target = OCT_A_METER_TOTAL - OCT9_SPOT_HOURS
    base = [rng.randint(85, 120) for _ in october]
    base[-1] += target - sum(base)
    for day, value in zip(october, base):
        lumen_base[day] = value

    storage_tb = 38.0
    for day in _days(start, HISTORY_END + timedelta(days=1)):
        partial = day > HISTORY_END
        end_hour = 15 if partial else 24
        window_start = _dt(day, 0)
        window_end = _dt(day, 15) if partial else _dt(day + timedelta(days=1), 0)
        share = end_hour / 24
        weekend = day.weekday() >= 5
        records: list[MeterRecord] = []
        for agent, (inp, out) in {"agent:atlas": (2.4, 0.32), "agent:forge": (1.1, 0.16),
                                  "agent:scout": (2.6, 0.36)}.items():
            factor = (0.55 if weekend else 1.0) * rng.uniform(0.8, 1.2) * share
            records.append(MeterRecord(vendor_id="ven_anthropic", sku="input-mtok", agent_id=agent,
                                       quantity=round(inp * factor, 3), window_start=iso(window_start),
                                       window_end=iso(window_end)))
            records.append(MeterRecord(vendor_id="ven_anthropic", sku="output-mtok", agent_id=agent,
                                       quantity=round(out * factor, 3), window_start=iso(window_start),
                                       window_end=iso(window_end)))
        storage_tb += rng.uniform(0.03, 0.15)
        days_in_month = ((date(day.year + (day.month == 12), day.month % 12 + 1, 1)) - timedelta(days=1)).day
        records.append(MeterRecord(vendor_id="ven_aws", sku="s3-tb-month", quantity=round(storage_tb / days_in_month * share, 4),
                                   window_start=iso(window_start), window_end=iso(window_end)))
        records.append(MeterRecord(vendor_id="ven_aws", sku="egress-tb", quantity=round(rng.uniform(0.05, 0.15) * share, 4),
                                   window_start=iso(window_start), window_end=iso(window_end)))
        if not partial:
            records.append(MeterRecord(vendor_id="ven_lumen", sku="h100-ondemand-hr", agent_id="agent:forge",
                                       quantity=float(lumen_base[day]), window_start=iso(window_start),
                                       window_end=iso(window_end)))
            if spot_hours.get(day):
                records.append(MeterRecord(vendor_id="ven_lumen", sku="h100-ondemand-hr", agent_id="agent:forge",
                                           quantity=float(spot_hours[day]), window_start=iso(window_start),
                                           window_end=iso(window_end)))
        plan.add(window_end - timedelta(minutes=5) if partial else _dt(day, 23, 50), "meter", records=records)

    _plan_invoices(plan, rng, lumen_base, spot_hours)


def _plan_invoices(plan: Plan, rng: random.Random, lumen_base: dict[date, int], spot_hours: dict[date, float]) -> None:
    for month in (7, 8, 9):
        first = date(2026, month, 1)
        next_first = date(2026 + (month == 12), month % 12 + 1, 1)
        middle = date(2026, month, 16)
        halves = [("A", first, middle, business_day(date(2026, month, 17))),
                  ("B", middle, next_first, business_day(next_first + timedelta(days=1)))]
        for label, window_start, window_end, arrives in halves:
            used = sum(lumen_base[d] for d in _days(window_start, window_end - timedelta(days=1)))
            # Lumen billed history correctly: on-demand hours exclude spot hours already paid over x402.
            lines = [
                _line("1", "Reserved H100 cluster, half month", "ven_lumen", "h100-reserved-halfmonth", 1,
                      window_start, window_end),
                _line("2", "On demand H100 hours", "ven_lumen", "h100-ondemand-hr", float(used), window_start,
                      window_end),
            ]
            _invoice(plan, rng, "ven_lumen", f"LUM-2026-{month:02d}{label}", f"2026-{month:02d}", arrives, lines)
        # AWS and Anthropic bill monthly from the same meters Elix sees.
        _invoice(plan, rng, "ven_aws", f"AWS-2026-{month:02d}", f"2026-{month:02d}",
                 business_day(next_first + timedelta(days=2)), [], meter_skus=["s3-tb-month", "egress-tb"],
                 window=(first, next_first))
        _invoice(plan, rng, "ven_anthropic", f"ANT-2026-{month:02d}", f"2026-{month:02d}",
                 business_day(next_first + timedelta(days=7)), [], meter_skus=["input-mtok", "output-mtok"],
                 window=(first, next_first))


def _line(line_id: str, description: str, vendor_id: str, sku: str, quantity: float, start: date, end: date) -> InvoiceLine:
    rate = PRICES[(vendor_id, sku)]
    return InvoiceLine(line_id=line_id, description=description, sku=sku, quantity=quantity, unit_price_cents=rate,
                       amount_cents=cents_of(quantity, rate),
                       usage_window=UsageWindow(start=iso(_dt(start, 0)), end=iso(_dt(end, 0))))


def _invoice(plan: Plan, rng: random.Random, vendor_id: str, number: str, period: str, arrives: date,
             lines: list[InvoiceLine], meter_skus: Optional[list[str]] = None,
             window: Optional[tuple[date, date]] = None) -> None:
    at = _dt(arrives, 9, 30)
    plan.add(at, "invoice", ref=number, vendor_id=vendor_id, number=number, period=period, lines=lines,
             meter_skus=meter_skus or [], window=window, arrives=arrives)
    plan.add(at + timedelta(minutes=rng.randint(60, 240)), "approve_invoice", ref=number, actor="human:sam")


def _plan_recodes(plan: Plan) -> None:
    # In September Sam recoded two Signalboard entries from 6110 to 6210 and Priya approved both.
    for purchase_day, recode_day in ((date(2026, 9, 1), date(2026, 9, 24)), (date(2026, 9, 8), date(2026, 9, 29))):
        ref = f"scout-sb-{purchase_day.isoformat()}"
        plan.add(_dt(recode_day, 11, 0), "recode", ref=ref, actor="human:sam", to_account="6210",
                 note="Signalboard firmographics are marketing data, not research data")
        plan.add(_dt(recode_day, 14, 30), "approve_correction", ref=ref, actor="human:priya")


def _plan_closes(plan: Plan) -> None:
    for month in (7, 8, 9):
        close_day = nth_business_day(2026, month + 1, 4)
        plan.add(_dt(close_day, 16, 0), "close", period=f"2026-{month:02d}", actor="human:priya")


# ---------------------------------------------------------------- replay


@dataclass
class SeedResult:
    elix: Elix
    seconds: float
    counts: dict[str, int]


def remove_db(db_path: str | Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        path = Path(f"{db_path}{suffix}")
        if path.exists():
            path.unlink()


def seed_world(db_path: str | Path, registry: Any, seed: int = 7,
               progress: Optional[Callable[[str], None]] = None, bus: Any = None) -> SeedResult:
    """Recreate the database at db_path and replay Kestrel's history into it."""
    started = time.perf_counter()
    remove_db(db_path)
    clock = Clock(HISTORY_START)
    if hasattr(registry, "set_clock"):
        registry.set_clock(clock)
    elix = Elix(db_path, registry, clock=clock, bus=bus)
    refs: dict[str, str] = {}
    counts: dict[str, int] = {}
    events = plan_history(seed).sorted()
    for index, event in enumerate(events):
        clock.set(event.at)
        handler = HANDLERS[event.kind]
        handler(elix, event, refs)
        counts[event.kind] = counts.get(event.kind, 0) + 1
        if progress and index % 250 == 0:
            progress(f"{index} of {len(events)} events, now {event.at:%b %d}")
    clock.set(TODAY)
    with elix.store.tx():
        elix.store.set_meta("clock", TODAY)
        elix.store.set_meta("seed", str(seed))
    return SeedResult(elix=elix, seconds=time.perf_counter() - started, counts=counts)


def _open_books(elix: Elix, event: Event, refs: dict) -> None:
    elix.open_books(date=event.at.strftime("%Y-%m-%d"), memo="Opening balances for Kestrel Labs on July 1", lines=[
        JournalLine(account="1010", debit_cents=150_000_000),
        JournalLine(account="1015", debit_cents=7_500_000),
        JournalLine(account="3000", credit_cents=157_500_000),
    ])


def _purchase_handler(elix: Elix, event: Event, refs: dict) -> None:
    d = event.data
    url = elix.resource_url(d["vendor_id"], d["sku"], d["quantity"], **d["params"]) if d["params"] else None
    item = elix.request_purchase(d["agent"], PurchaseRequest(
        vendor_id=d["vendor_id"], sku=d["sku"], quantity=d["quantity"], purpose=d["purpose"],
        task_ref=d["task_ref"], idempotency_key=f"seed-{d['ref']}", resource_url=url))
    refs[d["ref"]] = item.id


def _approve_handler(elix: Elix, event: Event, refs: dict) -> None:
    elix.approve(event.data["actor"], refs[event.data["ref"]])


def _reject_handler(elix: Elix, event: Event, refs: dict) -> None:
    elix.reject(event.data["actor"], refs[event.data["ref"]], event.data["note"])


def _meter_handler(elix: Elix, event: Event, refs: dict) -> None:
    elix.ingest_meter(event.data["records"])


def _invoice_handler(elix: Elix, event: Event, refs: dict) -> None:
    d = event.data
    lines = list(d["lines"])
    if d["meter_skus"]:
        start, end = d["window"]
        for index, sku in enumerate(d["meter_skus"], start=1):
            from elix.engine.reconcile import metered_quantity

            used, _ = metered_quantity(elix.ctx, d["vendor_id"], sku, iso(_dt(start, 0)), iso(_dt(end, 0)))
            description = next(s.description for v in VENDORS if v.id == d["vendor_id"] for s in v.skus if s.sku == sku)
            lines.append(_line(str(index), description, d["vendor_id"], sku, round(used, 2), start, end))
    invoice = VendorInvoice(vendor_id=d["vendor_id"], invoice_number=d["number"], period=d["period"],
                            issued_at=iso(event.at), due_at=iso(event.at + timedelta(days=30)), lines=lines,
                            total_cents=sum(line.amount_cents for line in lines))
    item = elix.ingest_invoice(invoice)
    refs[d["ref"]] = item.id


def _approve_invoice_handler(elix: Elix, event: Event, refs: dict) -> None:
    item = elix.get_item(refs[event.data["ref"]])
    if item.state.value == "NEEDS_APPROVAL":
        elix.approve(event.data["actor"], item.id)


def _recode_handler(elix: Elix, event: Event, refs: dict) -> None:
    purchase = elix.get_item(refs[event.data["ref"]])
    correction = elix.recode(event.data["actor"], purchase.journal_entry_ids[0], 0, event.data["to_account"],
                             event.data["note"])
    refs[f"correction:{event.data['ref']}"] = correction.id


def _approve_correction_handler(elix: Elix, event: Event, refs: dict) -> None:
    elix.approve(event.data["actor"], refs[f"correction:{event.data['ref']}"])


def _close_handler(elix: Elix, event: Event, refs: dict) -> None:
    actor, period = event.data["actor"], event.data["period"]
    preview = elix.close_preview(actor, period)
    for accrual in preview.accruals:
        if accrual.state.value == "PROPOSED":
            elix.clock.advance(300)
            elix.approve(actor, accrual.id, "Matches the usage meters at contract rates.")
    elix.clock.advance(600)
    elix.close_period(actor, period)


HANDLERS: dict[str, Callable[[Elix, Event, dict], None]] = {
    "open_books": _open_books, "purchase": _purchase_handler, "approve": _approve_handler,
    "reject": _reject_handler, "meter": _meter_handler, "invoice": _invoice_handler,
    "approve_invoice": _approve_invoice_handler, "recode": _recode_handler,
    "approve_correction": _approve_correction_handler, "close": _close_handler,
}


def restore_vendor_payouts(db_path: str | Path, vendor_base_url: str) -> list[str]:
    """Offline only. Make the local vendor servers ask for the payout addresses in this world's vendor master.

    The vendor servers keep payouts in memory and start at rotation 0, so a kept world (for example after the
    payee change moment) would otherwise see 'challenge changed since approval' when Priya releases the hold.
    Returns the vendor ids that were changed.
    """
    from elix import vendors
    from elix.engine.store import Store
    from elix.rails.keys import X402_VENDOR_IDS, vendor_address

    if not Path(db_path).exists():
        return []
    store = Store(db_path)
    try:
        changed = []
        for vendor in store.vendors():
            if vendor.id in X402_VENDOR_IDS and vendor.payout_address and \
                    vendor.payout_address.lower() != vendor_address(vendor.id, 0).lower():
                vendors.set_payout(vendor.id, vendor.payout_address, vendor_base_url)
                changed.append(vendor.id)
        return changed
    finally:
        store.close()


def default_registry(vendor_base_url: Optional[str] = None):
    """Seed always uses offline rails, even when ELIX_RAIL_MODE=live, because history amounts exceed faucet funds."""
    from elix.rails import make_registry

    return make_registry("offline", vendor_base_url)


def env_seed() -> int:
    return int(os.environ.get("ELIX_SEED", "7"))
