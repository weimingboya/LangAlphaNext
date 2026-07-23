.PHONY: sync agent api dev test external-test lint

sync:
	uv sync --all-groups

agent:
	uv run langgraph dev --no-browser --no-reload --port 2024

api:
	uv run uvicorn langalpha.server.main:app --reload --port 8000

dev:
	@echo "Run 'make agent' and 'make api' in separate terminals."

test:
	uv run pytest -q
	node --test tests/test_domain_events.mjs

external-test:
	RUN_EXTERNAL_E2E=1 uv run --env-file .env pytest -q tests/external

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests
	node --check src/langalpha/server/static/app.js
	node --check src/langalpha/server/static/domain-events.mjs
