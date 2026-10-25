"""Run an agent task: Claude when ANTHROPIC_API_KEY is set, the scripted agent otherwise.

`token` is the agent's own bearer token (for example tok_atlas). The runner
never holds a human token and never calls approve or pay endpoints.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from elix.agents import sessions
from elix.agents.prompts import normalize_agent_id
from elix.agents.tools import ElixAPI, ToolBox

DEFAULT_TASKS: dict[str, str] = {
    "atlas": "Assemble 10 m imagery for the Sept 28 hailstorm footprint in Buffalo County, Nebraska, for the hail-v7 training set.",
    "forge": "Reserve 400 H100 spot hours to retrain hail-v7 on the new Nebraska scenes this weekend.",
    "scout": "Enrich 300 crop insurance carriers in Kansas with firmographics for the November outreach list.",
}

AGENT_TOKENS = {"agent:atlas": "tok_atlas", "agent:forge": "tok_forge", "agent:scout": "tok_scout"}


def claude_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def mode_for(force_scripted: bool = False) -> str:
    return "claude" if (claude_available() and not force_scripted) else "scripted"


def _execute(session_id: str, agent_id: str, task: str, api_base: str, token: str, mode: str,
             client: Any = None) -> str:
    api = ElixAPI(api_base, token)
    toolbox = ToolBox(api, agent_id=agent_id, idempotency_prefix=session_id,
                      on_request=lambda item_id: sessions.add_request(session_id, item_id))
    try:
        if mode == "claude":
            from elix.agents.claude_agent import FirstCallFailed, agent_model, run_claude

            try:
                return run_claude(agent_id, task, toolbox, session_id, client=client, model=agent_model())
            except FirstCallFailed as exc:
                # Honest fallback: say so in the transcript and relabel the session as scripted.
                sessions.append(session_id, "assistant",
                                text=f"The Claude API could not be reached ({exc}). Running the scripted agent instead.")
                sessions.update(session_id, mode="scripted", model=None)
                from elix.agents.scripted import run_scripted

                return run_scripted(agent_id, task, toolbox, session_id)
        from elix.agents.scripted import run_scripted

        return run_scripted(agent_id, task, toolbox, session_id)
    except Exception as exc:  # never leave a session stuck in "running"
        sessions.append(session_id, "assistant", text=f"The agent run stopped with an error ({type(exc).__name__}). {exc}")
        sessions.finish(session_id, "failed", error=f"{type(exc).__name__}: {exc}")
        return "failed"


def _prepare(agent_id: str, task: str | None, force_scripted: bool) -> tuple[str, str, str, str | None]:
    aid = normalize_agent_id(agent_id)
    short = aid.split(":", 1)[1]
    text = (task or "").strip() or DEFAULT_TASKS.get(short, "")
    mode = mode_for(force_scripted)
    model = None
    if mode == "claude":
        from elix.agents.claude_agent import agent_model

        model = agent_model()
    return aid, text, mode, model


def start_run(agent_id: str, task: str, api_base: str, token: str, force_scripted: bool = False) -> str:
    """Starts a background thread, returns session_id immediately."""
    aid, text, mode, model = _prepare(agent_id, task, force_scripted)
    sid = sessions.create_session(aid, text, mode, model)
    t = threading.Thread(target=_execute, args=(sid, aid, text, api_base, token, mode),
                         name=f"elix-agent-{sid}", daemon=True)
    t.start()
    return sid


def run_blocking(agent_id: str, task: str, api_base: str, token: str, force_scripted: bool = False,
                 client: Any = None) -> str:
    """Runs to completion in this thread and returns the session_id."""
    aid, text, mode, model = _prepare(agent_id, task, force_scripted)
    if client is not None and not force_scripted:
        mode = "claude"
        from elix.agents.claude_agent import agent_model

        model = agent_model()
    sid = sessions.create_session(aid, text, mode, model)
    _execute(sid, aid, text, api_base, token, mode, client=client)
    return sid
