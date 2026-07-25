#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

agent_pid=""
api_pid=""
web_pid=""

cleanup() {
    if [[ -n "$web_pid" ]]; then
        kill "$web_pid" 2>/dev/null || true
    fi
    if [[ -n "$api_pid" ]]; then
        kill "$api_pid" 2>/dev/null || true
    fi
    if [[ -n "$agent_pid" ]]; then
        kill "$agent_pid" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

supabase start

./scripts/with-local-env.sh \
    uv run langgraph dev --no-browser --no-reload --port 2024 &
agent_pid="$!"

for _ in $(seq 1 90); do
    if curl --fail --silent http://127.0.0.1:2024/ok >/dev/null; then
        break
    fi
    if ! kill -0 "$agent_pid" 2>/dev/null; then
        wait "$agent_pid"
    fi
    sleep 1
done

curl --fail --silent http://127.0.0.1:2024/ok >/dev/null

./scripts/with-local-env.sh \
    uv run uvicorn langalpha.server.main:app --host 127.0.0.1 --port 8000 --reload &
api_pid="$!"

npm --prefix web run dev &
web_pid="$!"

echo "LangAlpha Web:   http://127.0.0.1:5173"
echo "FastAPI BFF:     http://127.0.0.1:8000"
echo "LangGraph API:   http://127.0.0.1:2024"
echo "Supabase Studio: http://127.0.0.1:55323"
echo "Local Mail:      http://127.0.0.1:55324"

wait "$web_pid"
