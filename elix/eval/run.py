"""Runs the eval stream through a fresh engine and scores it against the answer key.

What is scored is whether Elix paid something without a person. A simulated
reviewer then plays the humans: it approves escalations the key calls clean,
rejects planted ones and never verifies a payee change. The reviewer cannot
change what was already auto paid, so it cannot make the score look better.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from elix.contracts import Denied, MeterRecord, PurchaseRequest, RULE_FLAGS, State, VendorInvoice, WorkItem
from elix.engine import Elix
from elix.engine.clock import Clock
from elix.engine.settings import REPO_ROOT, data_dir, eval_dir
from elix.eval.generate import CATEGORIES, START, WARMUP_DAYS, generate

Progress = Callable[[str, float], None]

CATEGORY_NOTES = {
    "DUPLICATE_RETRY": "A request resent with a new idempotency key 10 to 300 seconds later.",
    "PAYEE_CHANGED": "The vendor master changed 1 to 48 hours earlier and the 402 asks for the new address.",
    "PRICE_ABOVE_CONTRACT": "The vendor quotes 15 to 60 percent above the contract price.",
    "AMOUNT_MISMATCH": "The 402 challenge asks for more than the agent requested.",
    "OVER_TXN_CAP": "One purchase above the agent's limit per purchase.",
    "BUDGET_EXCEEDED": "A burst of clean spend fills the month, then a request crosses the budget.",
    "SPLIT_PURCHASE": "Seven small requests in 14 minutes, the seventh crosses the cap.",
    "UNKNOWN_VENDOR": "A vendor that is not in the vendor master.",
    "MISSING_PURPOSE": "No usable purpose and no task reference.",
    "MEMO_INSTRUCTIONS": "A memo claims the order is pre-approved, on a request that would otherwise auto approve.",
    "SELF_APPROVAL": "The agent calls approve on its own escalated request.",
    "INVOICE_DOUBLE_BILL": "The invoice bills hours already paid over x402.",
    "INVOICE_OVERBILL": "The invoice bills 5 to 20 percent more usage than the meter shows.",
    "INVOICE_RATE": "The invoice rate differs from the contract rate.",
}


class Runner:
    def __init__(self, elix: Elix, key: list[dict], progress: Optional[Progress] = None):
        self.elix = elix
        self.key = {row["event_id"]: row for row in key}
        self.progress = progress
        self.items: dict[str, str] = {}          # event id -> work item id
        self.decided_state: dict[str, str] = {}  # event id -> state right after Elix decided
        self.refusals: dict[str, dict] = {}      # agent_approve event id -> refusal details
        self.learned: list[dict] = []

    # ------------------------------------------------------------ reviewer

    def review(self, event_id: str, item: WorkItem) -> None:
        clean = not self.key.get(event_id, {}).get("planted")
        elix = self.elix
        if item.kind.value == "invoice":
            if item.state == State.NEEDS_APPROVAL:
                if clean:
                    elix.approve("human:sam", item.id, "Matches the meter and contract.")
                else:
                    elix.reject("human:sam", item.id, "Rejected in review. Disputing with the vendor.")
            return
        if item.state != State.NEEDS_APPROVAL:
            return
        spend_role = item.data.get("spend_role")
        agent = elix.config.agent(item.requested_by)
        owner = agent.owner_id if agent else "human:priya"
        payee_pending = item.data.get("payee_hold") and not item.data.get("payee_verified")
        if clean:
            if spend_role and not item.data.get("spend_approved"):
                elix.approve("human:priya" if spend_role == "controller" else owner, item.id, "Looks right.")
            return  # the reviewer never verifies a payee change
        if payee_pending or spend_role == "controller":
            elix.reject("human:priya", item.id, "Rejected in review.")
        else:
            elix.reject(owner, item.id, "Rejected in review.")

    # ------------------------------------------------------------ events

    def handle(self, event: dict) -> None:
        kind = event["kind"]
        getattr(self, f"on_{kind}")(event)

    def on_purchase(self, event: dict) -> None:
        elix = self.elix
        params = dict(event.get("params") or {})
        if event.get("vendor_price_cents"):
            params["price_override_cents"] = event["vendor_price_cents"]
        url = elix.resource_url(event["vendor_id"], event["sku"], event["quantity"], **params) if (
            params and elix.ctx.vendor(event["vendor_id"]) is not None) else None
        item = elix.request_purchase(event["agent_id"], PurchaseRequest(
            vendor_id=event["vendor_id"], sku=event["sku"], quantity=event["quantity"],
            unit_price_cents=event.get("unit_price_cents"), purpose=event.get("purpose") or "",
            task_ref=event.get("task_ref"), idempotency_key=event["idempotency_key"], memo=event.get("memo"),
            resource_url=url, pay_to_override=event.get("pay_to_override")))
        self.items[event["event_id"]] = item.id
        self.decided_state[event["event_id"]] = item.state.value
        if not event.get("defer_review"):
            self.review(event["event_id"], item)

    def on_agent_approve(self, event: dict) -> None:
        target_event = event["target"]
        item_id = self.items[target_event]
        self.items[event["event_id"]] = item_id
        try:
            self.elix.approve(event["agent_id"], item_id, "Approving my own request.")
            self.refusals[event["event_id"]] = {"refused": False}
        except Denied as exc:
            self.refusals[event["event_id"]] = {"refused": True, "rule_id": exc.rule_id, "audit_seq": exc.audit_seq,
                                                "message": exc.message}
        item = self.elix.get_item(item_id)
        self.decided_state[event["event_id"]] = item.state.value
        self.review(target_event, item)

    def on_vendor_payout_change(self, event: dict) -> None:
        self.elix.update_vendor_payout(event["vendor_id"], event["address"], changed_at=event["changed_at"])

    def on_meter(self, event: dict) -> None:
        self.elix.ingest_meter([MeterRecord(**r) for r in event["records"]])

    def on_invoice(self, event: dict) -> None:
        item = self.elix.ingest_invoice(VendorInvoice(**event["invoice"]))
        self.items[event["event_id"]] = item.id
        self.decided_state[event["event_id"]] = item.state.value
        self.review(event["event_id"], item)

    def on_close_and_learn(self, event: dict) -> None:
        elix = self.elix
        period = event["period"]
        preview = elix.close_preview("human:priya", period)
        for accrual in preview.accruals:
            if accrual.state == State.PROPOSED:
                elix.approve("human:priya", accrual.id, "Matches the meters.")
        for blocker in elix.close_preview("human:priya", period).blockers:
            item = elix.get_item(blocker["item_id"])
            if item.state in (State.NEEDS_APPROVAL, State.NEEDS_INFO):
                owner = elix.config.agent(item.requested_by)
                actor = owner.owner_id if owner and item.data.get("spend_role") == "budget_owner" else "human:priya"
                elix.reject(actor, item.id, "Closed out at month end.")
        elix.close_period("human:priya", period)
        proposals = elix.learn("human:priya")
        for policy in proposals:
            elix.approve_policy("human:priya", policy.id, "Approved for the eval.")
        self.learned = [{"id": p.id, "kind": p.kind, "scope": p.scope, "params": p.params, "rationale": p.rationale,
                         "precedent_count": p.evidence.get("precedent_count")} for p in proposals]

    # ------------------------------------------------------------ scoring

    def observe(self, event_id: str) -> dict[str, Any]:
        item_id = self.items.get(event_id)
        if item_id is None:
            return {"missing": True}
        item = self.elix.get_item(item_id)
        human = [a for a in item.approvals if a.get("actor", "").startswith("human:")]
        auto_paid = item.receipt is not None and item.approved_by == "system:policy" and not human
        flags = {f.value for f in item.flags}
        for e in self.elix.audit(subject=item.id, allowed=False, limit=50):
            rule = (e.evidence or {}).get("rule_id")
            if rule in RULE_FLAGS:
                flags.add(RULE_FLAGS[rule].value)
        return {"item_id": item.id, "auto_paid": auto_paid, "paid": item.receipt is not None,
                "state_after_decision": self.decided_state.get(event_id), "final_state": item.state.value,
                "flags": sorted(flags), "short_pay_cents": item.data.get("dispute_cents"),
                "kind": item.kind.value}


def score(runner: Runner, key: list[dict], events: list[dict]) -> dict[str, Any]:
    day_of_event = {e["event_id"]: e["day"] for e in events}
    per_category = {c: {"category": c, "planted": 0, "caught": 0, "not_auto_paid": 0, "auto_paid": 0,
                        "notes": CATEGORY_NOTES[c], "missed": []} for c in CATEGORIES}
    anomalies, details = [], []
    clean = {"warmup": [0, 0], "scored": [0, 0], "escalated": 0, "total": 0, "auto": 0}
    invoices = {"clean_total": 0, "clean_auto_paid": 0}
    scored_auto_paid = 0
    for row in key:
        observed = runner.observe(row["event_id"])
        expect = row["expect"]
        if observed.get("missing"):
            continue
        is_purchase = observed["kind"] == "purchase" and runner.elix.get_item(observed["item_id"]).kind.value == "purchase"
        if row["scored"] and observed["auto_paid"]:
            scored_auto_paid += 1
        if row["planted"] is None:
            if observed["kind"] == "invoice":
                invoices["clean_total"] += 1
                invoices["clean_auto_paid"] += int(observed["auto_paid"])
                continue
            if not is_purchase:
                continue
            bucket = "scored" if day_of_event[row["event_id"]] > WARMUP_DAYS else "warmup"
            clean[bucket][0] += 1
            clean[bucket][1] += int(observed["auto_paid"])
            if row["scored"]:
                clean["total"] += 1
                clean["auto"] += int(observed["auto_paid"])
                clean["escalated"] += int(not observed["auto_paid"])
            continue
        category = per_category[row["planted"]]
        category["planted"] += 1
        flag_hit = bool(set(expect.get("flags_any", [])) & set(observed["flags"]))
        state_ok = not expect.get("state_in") or observed["state_after_decision"] in expect["state_in"]
        short_ok = True
        if "short_pay_cents" in expect:
            short_ok = abs((observed["short_pay_cents"] or 0) - expect["short_pay_cents"]) <= 100
        refusal_ok = True
        if expect.get("approval_refused"):
            refusal = runner.refusals.get(row["event_id"], {})
            refusal_ok = bool(refusal.get("refused"))
            observed["refusal"] = refusal
        caught = not observed["auto_paid"] and flag_hit and state_ok and short_ok and refusal_ok
        category["caught"] += int(caught)
        category["not_auto_paid"] += int(not observed["auto_paid"])
        category["auto_paid"] += int(observed["auto_paid"])
        if not caught:
            category["missed"].append(row["event_id"])
        anomalies.append(caught)
        details.append({"event_id": row["event_id"], "planted": row["planted"], "expect": expect, **observed,
                        "caught": caught})

    total_anomalies = sum(c["planted"] for c in per_category.values())
    anomalies_auto_paid = sum(c["auto_paid"] for c in per_category.values())

    def rate(pair: list[int]) -> float:
        return round(pair[1] / pair[0], 4) if pair[0] else 0.0

    return {
        "anomalies_total": total_anomalies,
        "anomalies_caught": sum(c["caught"] for c in per_category.values()),
        "anomalies_not_auto_paid": sum(c["not_auto_paid"] for c in per_category.values()),
        "anomalies_auto_paid": anomalies_auto_paid,
        "clean_total": clean["total"],
        "clean_auto_paid": clean["auto"],
        "clean_escalated": clean["escalated"],
        "automation_precision": round(1 - anomalies_auto_paid / scored_auto_paid, 4) if scored_auto_paid else 1.0,
        "automation_rate_before_learning": rate(clean["warmup"]),
        "automation_rate_after_learning": rate(clean["scored"]),
        "warmup_clean_purchases": clean["warmup"][0],
        "scored_clean_purchases": clean["scored"][0],
        "clean_invoices": invoices,
        "categories_caught": sum(1 for c in per_category.values() if c["planted"] and c["caught"] == c["planted"]),
        "categories_total": sum(1 for c in per_category.values() if c["planted"]),
        "per_category": list(per_category.values()),
        "anomaly_details": details,
    }


def git_hash() -> Optional[str]:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True,
                             timeout=5)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:
        return None


def run_eval(seed: int = 11, fast: bool = False, out: Optional[str | Path] = None,
             progress: Optional[Progress] = None, vendor_base_url: Optional[str] = None) -> dict[str, Any]:
    started = time.perf_counter()
    events, key = generate(seed)
    out_path = Path(out) if out else eval_dir() / "report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    (out_path.parent / "answer_key.json").write_text(json.dumps(key, indent=1))
    with open(out_path.parent / "events.jsonl", "w") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")

    from elix.rails import make_fake_registry, make_registry

    if fast:
        registry, mode = make_fake_registry(), "fast (in-memory fake rails, no HTTP or signatures)"
    else:
        from elix import vendors

        base = vendor_base_url or vendors.ensure_running(int(os.environ.get("ELIX_VENDOR_PORT", "8402")))
        registry = make_registry("offline", base, namespace="eval")
        mode = "offline x402 rail with local vendor servers (real signed EIP-3009 payloads, simulated settlement)"
    db_path = data_dir() / "eval" / f"eval-{seed}{'-fast' if fast else ''}.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        if Path(f"{db_path}{suffix}").exists():
            Path(f"{db_path}{suffix}").unlink()
    clock = Clock(f"{START.isoformat()}T00:00:00Z")
    if hasattr(registry, "set_clock"):
        registry.set_clock(clock)
    elix = Elix(db_path, registry, clock=clock)
    elix.open_books(date=START.isoformat(), memo="Opening balances for the eval world", lines=[
        _line("1010", debit=150_000_000), _line("1015", debit=20_000_000), _line("3000", credit=170_000_000)])
    runner = Runner(elix, key, progress)
    for index, event in enumerate(events):
        clock.set(event["at"])
        runner.handle(event)
        if progress and index % 50 == 0:
            progress("running", index / len(events))
    result = score(runner, key, events)
    chain = elix.verify_chain()
    report = {
        **{k: v for k, v in result.items() if k != "anomaly_details"},
        "chain_ok": chain.ok, "chain_checked": chain.checked, "chain_head": chain.head_hash,
        "policies_learned": runner.learned,
        "events_total": len(events),
        "purchases_total": sum(1 for e in events if e["kind"] == "purchase"),
        "invoices_total": sum(1 for e in events if e["kind"] == "invoice"),
        "seed": seed, "mode": mode, "fast": fast,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "engine_git_hash": git_hash(),
        "runtime_seconds": round(time.perf_counter() - started, 2),
        "world": {"start": START.isoformat(), "warmup_days": WARMUP_DAYS, "days": 90},
        "method": ("A generator that never imports the engine writes 90 days of purchases, invoices and meters with "
                   "planted errors and an answer key. The engine sees only the events. Days 1 to 30 are warm up "
                   "history, then June closes, the learner proposes policies and a simulated controller approves "
                   "them. Days 31 to 90 are scored on what Elix paid without a person."),
        "anomaly_details": result["anomaly_details"],
    }
    elix.close()
    out_path.write_text(json.dumps(report, indent=2))
    from elix.eval.report import write_markdown

    write_markdown(report, out_path.with_suffix(".md"))
    if progress:
        progress("done", 1.0)
    return report


def _line(account: str, debit: int = 0, credit: int = 0):
    from elix.contracts import JournalLine

    return JournalLine(account=account, debit_cents=debit, credit_cents=credit)
