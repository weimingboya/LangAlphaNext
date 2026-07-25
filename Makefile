.PHONY: sync local-up local-reset local-down agent api web web-build dev test external-test lint

sync:
	uv sync --all-groups
	npm --prefix web ci

local-up:
	supabase start

local-reset:
	supabase db reset --local

local-down:
	supabase stop

agent:
	./scripts/with-local-env.sh uv run langgraph dev --no-browser --no-reload --port 2024

api:
	npm --prefix web run build
	./scripts/with-local-env.sh uv run uvicorn langalpha.server.main:app --reload --port 8000

web:
	npm --prefix web run dev

web-build:
	npm --prefix web run build

dev:
	./scripts/dev-local.sh

test:
	uv run pytest -q
	npm --prefix web test

external-test:
	RUN_EXTERNAL_E2E=1 uv run --env-file .env pytest -q tests/external

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests
	npm --prefix web run lint
