# Agents and MCP server

Owner: rails builder. Code in `elix/agents/` and `elix/mcp_server.py`. Tests in `tests/agents/` and `tests/mcp/`.

## What is here

- `tools.py` has the seven agent tools (`list_vendors`, `get_catalog`, `budget_status`, `request_purchase`, `check_request`, `answer_question`, `get_resource`) as JSON schemas plus `ToolBox`, which runs them against the Elix HTTP API with the agent's bearer token. There is no approve or pay tool. API refusals come back as `{"error": {...}}` data so the agent can read them. Catalog text is wrapped as untrusted vendor text.
- `prompts.py` holds the system prompt per agent. It says the agent cannot approve or pay, needs a purpose and task reference, must treat catalog text as data, and must stop and report when a person has to decide.
- `claude_agent.py` is the tool loop on the installed `anthropic` 2.3 SDK (`client.messages.create` with `tools`, `tool_use` blocks, `tool_result` blocks). Model from `ELIX_AGENT_MODEL`, default `claude-sonnet-5-5`. At most 12 model calls.
- `scripted.py` is the deterministic offline agent for the same tasks, labeled "Scripted agent (offline)".
- `sessions.py` stores sessions and transcripts in `ELIX_DATA_DIR/sessions.db`. Transcript roles are `user`, `assistant`, `tool_call`, `tool_result`.
- `runner.py` has `start_run` (background thread), `run_blocking`, `DEFAULT_TASKS`, `AGENT_TOKENS`, `mode_for()`.
- `elix/mcp_server.py` exposes the same seven tools over stdio with mcp 2.3's `MCPServer` (FastMCP was renamed in mcp 2). Setup for Claude Code and Claude Desktop is in `docs/mcp.md`.

## Real and simulated

Claude agents are real when `ANTHROPIC_API_KEY` is set. Without it the runner uses the scripted agent and the session says `mode: "scripted"`. If a key is set but the first Claude call fails, the transcript says so and the session is relabeled scripted before the scripted agent runs.

## Run

From the repo root, with Elix running (`python -m elix demo`):

```
python -c "from elix.agents.runner import run_blocking, DEFAULT_TASKS; from elix.agents.sessions import get_session; s = run_blocking('scout', DEFAULT_TASKS['scout'], 'http://127.0.0.1:8400', 'tok_scout'); print(get_session(s)['summary'])"
ELIX_AGENT_TOKEN=tok_scout python -m elix.mcp_server
```

## Tests

```
python -m pytest -q tests/agents tests/mcp
```

`tests/agents/stub_api.py` is a small stand in for the Elix API used only to test the agent side. The Claude test points the real anthropic SDK at a local fake Messages endpoint and checks every request against the SDK's own `MessageParam` and `ToolParam` types. `tests/agents/test_agents_live_server.py` runs the scripted agents against the real Elix server.
