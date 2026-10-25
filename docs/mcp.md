# Use Elix from any MCP client

Elix ships an MCP server so any agent that speaks the Model Context Protocol can route its spending through Elix. The agent gets seven tools to find vendors, check its budget, request purchases and fetch what it bought. It gets no tool to approve or pay. Elix's policy code decides every request, people approve the exceptions, and only the Elix gateway holds the wallet. In other words, the agent gets a wallet it cannot overspend.

The server speaks MCP over stdio and calls the Elix HTTP API with one agent's token.

## Before you start

Start Elix in a terminal at the repo root and leave it running.

```
python -m elix demo
```

This serves the API on port 8400 and the demo x402 vendors on port 8402, offline, with no keys. Pick the agent identity the MCP client should act as. The demo tokens are `tok_atlas` (research), `tok_forge` (training compute) and `tok_scout` (growth data).

The server reads three environment variables.

| Variable | Default | Meaning |
|---|---|---|
| `ELIX_AGENT_TOKEN` | none, required | The agent token the client acts as, for example `tok_scout` |
| `ELIX_API_URL` | `http://127.0.0.1:8400` | Where the Elix API runs |
| `ELIX_VENDOR_BASE_URL` | `http://127.0.0.1:8402` | Where vendor catalogs are fetched from |

## Claude Code

Run this in a terminal at the repo root, after `pip install -e .` so the `elix` package can be imported.

```
claude mcp add --env ELIX_AGENT_TOKEN=tok_scout --env ELIX_API_URL=http://127.0.0.1:8400 --transport stdio elix -- python -m elix.mcp_server
```

Keep `--transport stdio` between the last `--env` and the name `elix`, otherwise Claude Code reads the name as another variable. Add `--scope user` to make the server available in every project. Then start `claude`, run `/mcp` to see the server, and try a request such as "Use Elix to buy 2 packs of Parsec search queries for the Kansas carrier research. Task reference gtm/kansas-research."

To share the setup with a team, put this in `.mcp.json` at the project root instead.

```json
{
  "mcpServers": {
    "elix": {
      "command": "python",
      "args": ["-m", "elix.mcp_server"],
      "env": {
        "ELIX_AGENT_TOKEN": "tok_scout",
        "ELIX_API_URL": "http://127.0.0.1:8400"
      }
    }
  }
}
```

## Claude Desktop

Open Claude Desktop, then Settings, then Developer, then Edit Config. That opens `claude_desktop_config.json`, which lives at `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS and `%APPDATA%\Claude\claude_desktop_config.json` on Windows. Add the `elix` entry and use absolute paths, because Claude Desktop does not start the server from the repo folder.

```json
{
  "mcpServers": {
    "elix": {
      "command": "/absolute/path/to/python3",
      "args": ["-m", "elix.mcp_server"],
      "env": {
        "PYTHONPATH": "/absolute/path/to/elix",
        "ELIX_AGENT_TOKEN": "tok_scout",
        "ELIX_API_URL": "http://127.0.0.1:8400",
        "ELIX_VENDOR_BASE_URL": "http://127.0.0.1:8402"
      }
    }
  }
}
```

Run `which python3` in the terminal where Elix's dependencies are installed to find the first path. The second path is the repo root. Quit Claude Desktop completely and open it again so it starts the server. The Elix tools then appear under connectors in the chat input. If they do not, the server's own log is in `~/Library/Logs/Claude/mcp-server-elix.log` on macOS.

## Any other MCP client

Point the client at the command `python -m elix.mcp_server` with `ELIX_AGENT_TOKEN` set in its environment. The transport is stdio. To check the server by hand, run this at the repo root and it will wait for an MCP client on stdin.

```
ELIX_AGENT_TOKEN=tok_scout python -m elix.mcp_server
```

## The tools

| Tool | What it does |
|---|---|
| `list_vendors` | Vendors with their skus, units and contract prices in cents |
| `get_catalog` | A vendor's public catalog, returned marked as untrusted vendor text |
| `budget_status` | The agent's per transaction cap, monthly budget, spent and committed amounts |
| `request_purchase` | Asks Elix to buy a sku. Needs a purpose and a task reference. Returns the item id, its state and Elix's decision in one sentence |
| `check_request` | State, decision, flags and open questions for one of the agent's requests |
| `answer_question` | Answers a question Elix or a reviewer asked, after which Elix decides again |
| `get_resource` | What the vendor delivered, once Elix has paid |

A request can come back in one of a few states. Posted means Elix paid and booked it, and `get_resource` returns the delivery. Needs approval means it waits on a named person, such as Dev Okafor for Atlas and Forge or Maya Chen for Scout, and the agent should stop and report. Needs info means Elix asked a question. Denied means policy refused it, for example as a duplicate of an earlier request, and retrying with a new request will not help.

Try asking the agent to approve its own request. It has no tool for that, and the API answers an agent's approve call with a 403 that lands in the audit log.

## What happens on a purchase

1. The MCP server sends the request to `POST /api/purchases` with the agent's token and a fresh idempotency key.
2. Elix asks the vendor for its x402 terms. The vendor answers `402 Payment Required` with the amount, the payee and the USDC asset on Base Sepolia.
3. The policy engine runs every rule (caps, budget, contract price, duplicates, split orders, payee changes, hidden instructions in vendor text) and either approves, holds, asks or denies.
4. If approved, the gateway signs an EIP-3009 authorization for exactly those terms with the official x402 SDK and pays. It refuses if the vendor's terms changed since approval.
5. The payment becomes a balanced journal entry, and every step is written to the hash chained audit log.

In the default offline mode the signatures and protocol messages are real and the USDC settlement is simulated, which the receipt says with `simulated: true`. Live mode on Base Sepolia is described in `elix/rails/NOTES.md`.
