#!/usr/bin/env bash
# API smoke test against a real server. Run from anywhere: bash scripts/smoke.sh
#
# Starts `python -m elix demo` on free ports with a throwaway data folder, so it never touches
# ./data or the ports 8400 and 8402, seeds through the offline x402 rail, then runs
# scripts/smoke.py against it (every endpoint in specs/04-api.md, including the 403 when an
# agent approves its own item).
set -euo pipefail
cd "$(dirname "$0")/.."

DATA="$(mktemp -d)"
API_PORT="$(python -c 'from elix.vendors import free_port; print(free_port())')"
VENDOR_PORT="$(python -c 'from elix.vendors import free_port; print(free_port())')"
export ELIX_DATA_DIR="$DATA" ELIX_EVAL_DIR="$DATA/eval" ELIX_RAIL_MODE=offline ELIX_VENDOR_PORT="$VENDOR_PORT"
export ELIX_VENDOR_BASE_URL="http://127.0.0.1:$VENDOR_PORT"
unset ANTHROPIC_API_KEY || true

python -m elix demo --port "$API_PORT" --vendor-port "$VENDOR_PORT" > "$DATA/server.log" 2>&1 &
PID=$!
cleanup() { kill "$PID" 2>/dev/null || true; wait "$PID" 2>/dev/null || true; rm -rf "$DATA"; }
trap cleanup EXIT

for _ in $(seq 1 180); do
  if curl -fsS "http://127.0.0.1:$API_PORT/healthz" > /dev/null 2>&1; then break; fi
  if ! kill -0 "$PID" 2>/dev/null; then cat "$DATA/server.log"; echo "The server exited early."; exit 1; fi
  sleep 1
done

python scripts/smoke.py --base "http://127.0.0.1:$API_PORT"
