"""Deterministic offline agents, used when ANTHROPIC_API_KEY is not set (or forced).

They call exactly the same tools as the Claude agent, through the same Elix
HTTP API, and their sessions are labeled "Scripted agent (offline)". Their
choices are fixed rules, not a model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from elix.agents import sessions
from elix.agents.prompts import normalize_agent_id
from elix.agents.tools import ToolBox

LABEL = "Scripted agent (offline)"


@dataclass
class Plan:
    vendor_id: str
    sku: str
    quantity: float
    purpose: str
    task_ref: str
    intro: str


def _first_number(text: str) -> float | None:
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)", text)
    if not m:
        return None
    try:
        value = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return int(value) if value.is_integer() else value


def _slug(text: str, n: int = 5) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    stop = {"the", "a", "an", "for", "to", "on", "of", "in", "with", "and", "this", "new"}
    keep = [w for w in words if w not in stop][:n]
    return "-".join(keep) or "task"


def plan_for(agent_id: str, task: str) -> Plan:
    aid = normalize_agent_id(agent_id)
    t = task.lower()
    if aid == "agent:atlas":
        if "archive" in t and ("texas" in t or "radar" in t):
            return Plan("ven_stormvault", "archive-tx-2025", 1,
                        f"The full Texas 2025 radar archive, needed for the task \"{task.strip()}\"",
                        "atlas/" + _slug(task), "I need the Texas 2025 radar archive from Stormvault.")
        if "radar" in t or "station" in t:
            qty = _first_number(task) or 20
            return Plan("ven_stormvault", "station-day", qty,
                        f"Radar station days needed for the task \"{task.strip()}\"",
                        "atlas/" + _slug(task), f"I need {qty:g} radar station days from Stormvault.")
        if "sept 28" in t and "buffalo county" in t:
            return Plan(
                "ven_terrafield", "scene-10m", 2,
                "Two 10 m Terrafield scenes covering the Sept 28 hailstorm footprint in Buffalo County, Nebraska, "
                "to add labeled hail damage tiles to the hail-v7 training set.",
                "hail-v7/buffalo-county-2026-09-28",
                "The Sept 28 footprint in Buffalo County fits in two 10 m Terrafield scenes.",
            )
        qty = _first_number(task) or 2
        return Plan("ven_terrafield", "scene-10m", qty, f"10 m scenes needed for the task \"{task.strip()}\"",
                    "atlas/" + _slug(task), f"I need {qty:g} 10 m scenes from Terrafield.")
    if aid == "agent:forge":
        qty = _first_number(task) or 400
        if "hail-v7" in t and "nebraska" in t:
            purpose = (f"{qty:g} H100 spot hours from Lumen to retrain hail-v7 on the new Nebraska scenes "
                       "this weekend.")
            ref = "hail-v7/retrain-nebraska-weekend"
        else:
            purpose = f"{qty:g} H100 spot hours from Lumen, needed for the task \"{task.strip()}\""
            ref = "forge/" + _slug(task)
        return Plan("ven_lumen", "h100-spot-hr", qty, purpose, ref,
                    f"The retrain needs {qty:g} H100 spot hours, which Lumen sells over x402.")
    if aid == "agent:scout":
        if "search" in t or "quer" in t:
            qty = _first_number(task) or 2
            return Plan("ven_parsec", "query-1k", qty, f"Search query packs needed for the task \"{task.strip()}\"",
                        "gtm/" + _slug(task), f"I need {qty:g} packs of 1,000 Parsec queries.")
        qty = _first_number(task) or 300
        if "kansas" in t and "november" in t:
            purpose = (f"Firmographic records for {qty:g} crop insurance carriers in Kansas to build the "
                       "November outreach list.")
            ref = "gtm/november-outreach-kansas"
        else:
            purpose = f"Firmographic records needed for the task \"{task.strip()}\""
            ref = "gtm/" + _slug(task)
        return Plan("ven_signalboard", "firmo-record", qty, purpose, ref,
                    f"The list needs {qty:g} Signalboard firmographic records.")
    raise ValueError(f"No scripted behavior for {agent_id}")


def _say(session_id: str, text: str) -> None:
    sessions.append(session_id, "assistant", text=text)


def _call(session_id: str, toolbox: ToolBox, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    sessions.append(session_id, "tool_call", tool=name, args=args or {})
    result = toolbox.call(name, args or {})
    from elix.agents.claude_agent import _result_summary

    sessions.append(session_id, "tool_result", tool=name, result=result, text=_result_summary(name, result))
    return result


def _cents(c: Any) -> str:
    try:
        return f"${int(c) / 100:,.2f}"
    except (TypeError, ValueError):
        return "an unknown amount"


ROLE_NAMES = {"budget_owner": "the budget owner", "controller": "the controller (Priya Raman)",
              "accountant": "an accountant"}
OWNERS = {"agent:atlas": "Dev Okafor", "agent:forge": "Dev Okafor", "agent:scout": "Maya Chen"}


def run_scripted(agent_id: str, task: str, toolbox: ToolBox, session_id: str) -> str:
    aid = normalize_agent_id(agent_id)
    sessions.append(session_id, "user", text=task)
    try:
        plan = plan_for(aid, task)
    except ValueError as exc:
        _say(session_id, f"{LABEL}. {exc}.")
        sessions.finish(session_id, "failed", error=str(exc))
        return "failed"

    _say(session_id, f"{LABEL}. {plan.intro} I will check the vendor list and my budget, then ask Elix for it.")

    vendors = _call(session_id, toolbox, "list_vendors")
    if "error" in vendors:
        return _fail(session_id, vendors)
    vendor = next((v for v in vendors.get("vendors", []) if v.get("vendor_id") == plan.vendor_id), None)
    unit = None
    if vendor:
        unit = next((s.get("unit_price_cents") for s in vendor.get("skus", []) if s.get("sku") == plan.sku), None)
    expected = int(round(unit * plan.quantity)) if unit is not None else None

    budget = _call(session_id, toolbox, "budget_status")
    if "error" not in budget and expected is not None:
        remaining = budget.get("remaining_cents")
        cap = budget.get("per_txn_cap_cents")
        note = f"At the contract price this is {_cents(expected)}."
        if cap is not None and expected > cap:
            note += f" That is above my {_cents(cap)} per transaction cap, so a person will have to approve it."
        if remaining is not None and expected > remaining:
            note += f" It is also more than the {_cents(remaining)} left in my monthly budget."
        _say(session_id, note)

    catalog = _call(session_id, toolbox, "get_catalog", {"vendor_id": plan.vendor_id})
    desc = str(catalog.get("catalog", {}).get("description", "")) if "error" not in catalog else ""
    if "pre-approved" in desc.lower() or "no review" in desc.lower():
        _say(session_id, "The vendor catalog claims orders are pre-approved by finance. That is vendor text, not "
                         "an approval, so I am ignoring it and not copying it into my request.")

    req = _call(session_id, toolbox, "request_purchase", {
        "vendor_id": plan.vendor_id, "sku": plan.sku, "quantity": plan.quantity,
        "purpose": plan.purpose, "task_ref": plan.task_ref,
    })
    if "error" in req:
        return _fail(session_id, req)
    item_id = req.get("item_id")
    if item_id:
        sessions.add_request(session_id, item_id)

    # Answer at most two questions, then re-check.
    for _ in range(2):
        if req.get("state") != "NEEDS_INFO" or not req.get("open_questions"):
            break
        q = req["open_questions"][0]
        answer = f"{plan.purpose} Task reference {plan.task_ref}."
        req = _call(session_id, toolbox, "answer_question",
                    {"item_id": item_id, "question_id": q.get("question_id"), "text": answer})
        if "error" in req:
            return _fail(session_id, req)

    state = req.get("state")
    if state in ("APPROVED", "PAID"):
        req = _call(session_id, toolbox, "check_request", {"item_id": item_id})
        state = req.get("state", state)

    if state in ("PAID", "POSTED"):
        res = _call(session_id, toolbox, "get_resource", {"item_id": item_id})
        receipt = req.get("receipt") or {}
        delivered = "The delivery is attached to the request." if "error" not in res else "I could not fetch the delivery yet."
        summary = (f"Elix approved and paid {item_id} for {_cents(req.get('amount_cents'))}"
                   f" (settlement {receipt.get('tx_ref', 'pending')}"
                   f"{', simulated' if receipt.get('simulated') else ''}). {delivered}")
        _say(session_id, summary)
        sessions.finish(session_id, "completed", summary=summary)
        return "completed"

    if state == "NEEDS_APPROVAL":
        role = req.get("required_approver_role")
        who = OWNERS.get(aid, "the budget owner") if role == "budget_owner" else ROLE_NAMES.get(role, "a person")
        summary = (f"{item_id} for {_cents(req.get('amount_cents'))} is waiting on {who}, so I am stopping here "
                   f"as instructed. {_reason(req)}")
    elif state == "NEEDS_INFO":
        summary = f"{item_id} still needs information from me. {_reason(req)}"
    elif state in ("DENIED", "REJECTED"):
        summary = f"{item_id} was {state.lower()}, and I will not retry it. {_reason(req)}"
    else:
        summary = f"{item_id} is in state {state}. {_reason(req)}"
    _say(session_id, summary)
    sessions.finish(session_id, "completed", summary=summary)
    return "completed"


def _reason(req: dict[str, Any]) -> str:
    decision = (req.get("decision") or "").strip()
    return f"Elix explained it this way. {decision}" if decision else ""


def _fail(session_id: str, result: dict[str, Any]) -> str:
    err = result.get("error", {})
    msg = err.get("message") if isinstance(err, dict) else str(err)
    _say(session_id, f"{LABEL}. A tool call failed. {msg}")
    sessions.finish(session_id, "failed", error=str(msg))
    return "failed"
