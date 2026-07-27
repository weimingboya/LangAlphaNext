#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

api_url=""
anon_key=""
publishable_key=""
service_role_key=""
secret_key=""

while IFS='=' read -r name raw_value; do
    value="${raw_value#\"}"
    value="${value%\"}"
    case "$name" in
        API_URL) api_url="$value" ;;
        ANON_KEY) anon_key="$value" ;;
        PUBLISHABLE_KEY) publishable_key="$value" ;;
        SERVICE_ROLE_KEY) service_role_key="$value" ;;
        SECRET_KEY) secret_key="$value" ;;
    esac
done <<< "$(supabase status --output env)"

export SUPABASE_URL="$api_url"
export SUPABASE_PUBLISHABLE_KEY="${publishable_key:-$anon_key}"
export SUPABASE_SECRET_KEY="${secret_key:-$service_role_key}"
export SUPABASE_STORAGE_BUCKET="langalpha-assets"
export LANGGRAPH_SERVER_URL="${LANGGRAPH_SERVER_URL:-http://127.0.0.1:2024}"
export LANGGRAPH_ASSISTANT_ID="${LANGGRAPH_ASSISTANT_ID:-main}"
export APP_ID="${APP_ID:-langalpha}"
export APP_VERSION="${APP_VERSION:-local}"
export APP_ENVIRONMENT="${APP_ENVIRONMENT:-development}"
export LANGSMITH_PROJECT="${LANGSMITH_PROJECT:-langalpha-local}"
export LANGSMITH_TRACING="${LANGSMITH_TRACING_LOCAL:-false}"
export LANGGRAPH_CLI_NO_ANALYTICS=1

if [[ -z "$SUPABASE_URL" || -z "$SUPABASE_PUBLISHABLE_KEY" || -z "$SUPABASE_SECRET_KEY" ]]; then
    echo "Local Supabase is missing API credentials. Run 'make local-up' first." >&2
    exit 1
fi

exec "$@"
