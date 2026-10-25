"""Claude tool use loop with the installed anthropic SDK (2.x Messages API).

The model only ever proposes. It gets the seven agent tools; none of them can
approve or pay. Every assistant message, tool call and tool result is written
to the session transcript.
"""

from __future__ import annotations

import os
from typing import Any

from elix.agents import sessions
from elix.agents.prompts import system_prompt
from elix.agents.tools import TOOL_SPECS, ToolBox, result_text

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TURNS = 12
MAX_TOKENS = 2048


def agent_model() -> str:
    return os.environ.get("ELIX_AGENT_MODEL") or DEFAULT_MODEL


def make_client() -> Any:
    """anthropic.Anthropic reads ANTHROPIC_API_KEY from the environment."""
    import anthropic

    return anthropic.Anthropic()


def _block_get(block: Any, key: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return block.get(key, default)
    return getattr(block, key, default)


def build_request(agent_id: str, messages: list[dict[str, Any]], model: str | None = None) -> dict[str, Any]:
    """Keyword arguments for client.messages.create. Exposed for tests."""
    return {
        "model": model or agent_model(),
        "max_tokens": MAX_TOKENS,
        "system": system_prompt(agent_id),
        "tools": [dict(t) for t in TOOL_SPECS],
        "messages": messages,
    }


class FirstCallFailed(Exception):
    """The very first API call failed (no network, bad key). The runner may fall back to scripted."""


def run_claude(agent_id: str, task: str, toolbox: ToolBox, session_id: str, client: Any = None,
               model: str | None = None, max_turns: int = MAX_TURNS) -> str:
    """Run the loop. Returns the final status: completed, max_turns or failed."""
    client = client or make_client()
    model = model or agent_model()
    messages: list[dict[str, Any]] = [{"role": "user", "content": task}]
    sessions.append(session_id, "user", text=task)
    final_text = ""

    for turn in range(max_turns):
        try:
            response = client.messages.create(**build_request(agent_id, messages, model))
        except Exception as exc:  # anthropic.APIError and transport errors
            if turn == 0:
                raise FirstCallFailed(f"{type(exc).__name__}: {exc}") from exc
            sessions.append(session_id, "assistant", text=f"The Claude API call failed ({type(exc).__name__}). {exc}")
            sessions.finish(session_id, "failed", summary=final_text or None, error=str(exc))
            return "failed"

        assistant_content: list[dict[str, Any]] = []
        tool_uses: list[dict[str, Any]] = []
        for block in _block_get(response, "content", []) or []:
            btype = _block_get(block, "type")
            if btype == "text":
                text = _block_get(block, "text", "")
                assistant_content.append({"type": "text", "text": text})
                if text.strip():
                    final_text = text
                    sessions.append(session_id, "assistant", text=text)
            elif btype == "tool_use":
                use = {
                    "type": "tool_use",
                    "id": _block_get(block, "id"),
                    "name": _block_get(block, "name"),
                    "input": dict(_block_get(block, "input", {}) or {}),
                }
                assistant_content.append(use)
                tool_uses.append(use)
        messages.append({"role": "assistant", "content": assistant_content or [{"type": "text", "text": ""}]})

        stop_reason = _block_get(response, "stop_reason")
        if stop_reason != "tool_use" or not tool_uses:
            sessions.finish(session_id, "completed", summary=final_text or None)
            return "completed"

        results: list[dict[str, Any]] = []
        for use in tool_uses:
            sessions.append(session_id, "tool_call", tool=use["name"], args=use["input"])
            result = toolbox.call(use["name"], use["input"])
            sessions.append(session_id, "tool_result", tool=use["name"], result=result,
                            text=_result_summary(use["name"], result))
            block: dict[str, Any] = {"type": "tool_result", "tool_use_id": use["id"], "content": result_text(result)}
            if "error" in result:
                block["is_error"] = True
            results.append(block)
        messages.append({"role": "user", "content": results})

    sessions.append(session_id, "assistant", text=f"Stopped after {max_turns} turns.")
    sessions.finish(session_id, "max_turns", summary=final_text or None)
    return "max_turns"


def _result_summary(tool: str, result: dict[str, Any]) -> str:
    """One line for the transcript view."""
    if "error" in result:
        err = result["error"]
        return f"Refused. {err.get('message') or err}"
    if tool == "request_purchase" or tool == "check_request" or tool == "answer_question":
        parts = [result.get("item_id") or "", result.get("state") or ""]
        if result.get("decision"):
            parts.append(result["decision"])
        return " ".join(p for p in parts if p)
    if tool == "list_vendors":
        return f"{len(result.get('vendors', []))} vendors"
    if tool == "get_catalog":
        cat = result.get("catalog", {})
        return f"Catalog for {cat.get('name', '')} (untrusted vendor text)"
    if tool == "budget_status":
        def usd(c: Any) -> str:
            try:
                return f"${int(c) / 100:,.2f}"
            except (TypeError, ValueError):
                return "unknown"
        return (f"Spent {usd(result.get('spent_cents'))} of {usd(result.get('monthly_budget_cents'))} this month, "
                f"{usd(result.get('committed_cents'))} committed")
    if tool == "get_resource":
        return f"Delivery for {result.get('item_id')}"
    return ""
