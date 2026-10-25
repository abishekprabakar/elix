"""Demo moments. `list_scenarios` and `run_scenario` are what the server and CLI call."""

from __future__ import annotations

from typing import Any, Optional

from elix.engine import Elix
from elix.engine.errors import Conflict, NotFound
from elix.scenarios.catalog import BY_ID, SCENARIOS, Scenario, ScenarioResult

__all__ = ["SCENARIOS", "Scenario", "ScenarioResult", "list_scenarios", "run_scenario"]


def list_scenarios(elix: Elix) -> list[dict[str, Any]]:
    out = []
    for scenario in SCENARIOS:
        ready, reason = scenario.ready(elix)
        out.append({
            "id": scenario.id, "title": scenario.title, "description": scenario.description,
            "prerequisites": scenario.prerequisites, "ready": ready, "not_ready_reason": reason or None,
            "next_actor": scenario.next_actor, "last_run": elix.store.scenario_run(scenario.id),
        })
    return out


def run_scenario(elix: Elix, scenario_id: str, actor: str = "system:sync",
                 params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    scenario = BY_ID.get(scenario_id)
    if scenario is None:
        raise NotFound(f"There is no scenario called {scenario_id}.")
    ready, reason = scenario.ready(elix)
    if not ready:
        raise Conflict(reason)
    who = elix.config.actor_name(actor) if actor.startswith("human:") else "The presenter"
    elix.record(actor if actor.startswith("human:") else "system:sync", "run_scenario", f"scenario:{scenario_id}",
                f"{who} ran the demo scenario \"{scenario.title}\".", evidence={"params": params or {}})
    result: ScenarioResult = scenario.run(elix, actor=actor, **(params or {}))
    with elix.store.tx():
        elix.store.save_scenario_run(scenario_id, elix.clock.now(), result.result_ids, result.message)
    return {"scenario_id": scenario_id, "result_ids": result.result_ids, "message": " ".join(result.message.split()),
            "next_actor": result.next_actor}
