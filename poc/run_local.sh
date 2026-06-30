#!/usr/bin/env bash
# Starts backend, mcp_server, and agent, in that order, each in its own
# venv, logging to /tmp/poc-*.log. Ctrl+C stops all three.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDS=()

cleanup() {
  echo
  echo "Stopping services..."
  for pid in "${PIDS[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

prepare_venv() {
  local dir="$1"
  local venv="$dir/.venv"

  if [ ! -f "$dir/.env" ]; then
    echo "ERROR: $dir/.env not found. Copy $dir/.env.example to $dir/.env and fill it in first." >&2
    exit 1
  fi
  if [ ! -d "$venv" ]; then
    echo "[$(basename "$dir")] creating venv..."
    python3 -m venv "$venv"
  fi
  echo "[$(basename "$dir")] installing dependencies..."
  "$venv/bin/pip" install -q -r "$dir/requirements.txt"
}

run_in_background() {
  local name="$1" dir="$2"
  shift 2
  echo "[$name] starting..."
  ( cd "$dir" && set -a && source .env && set +a && exec "$dir/.venv/bin/python" "$@" ) \
    > "/tmp/poc-$name.log" 2>&1 &
  PIDS+=("$!")
  echo "[$name] pid=$! log=/tmp/poc-$name.log"
}

prepare_venv "$ROOT_DIR/backend"
run_in_background "backend" "$ROOT_DIR/backend" -m app.main
sleep 1

prepare_venv "$ROOT_DIR/mcp_server"
run_in_background "mcp_server" "$ROOT_DIR/mcp_server" server.py
sleep 1

prepare_venv "$ROOT_DIR/agent"
run_in_background "agent" "$ROOT_DIR/agent" app.py

echo
echo "All services started:"
echo "  poc-backend    http://127.0.0.1:8000  (log: /tmp/poc-backend.log)"
echo "  poc-mcp        http://127.0.0.1:8001  (log: /tmp/poc-mcp_server.log)"
echo "  poc-agent      http://127.0.0.1:8501  (log: /tmp/poc-agent.log)"
echo
echo "Open http://localhost:8501 to use the chat UI. Press Ctrl+C to stop everything."

wait
