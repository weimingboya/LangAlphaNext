# LangAlpha Next

LangAlpha is a production-oriented Deep Agents application:

- LangGraph Agent Server owns Threads, Runs, checkpoints and native steering;
- Daytona provides isolated, disposable computation;
- Supabase Auth identifies users;
- Supabase Postgres and private Storage persist Assets;
- LangSmith provides tracing and evaluation;
- React and Vite provide the product UI;
- FastAPI serves the product API and built UI on Vercel.

There is no local product database and no custom guidance state machine.

## Local development

Requirements: Python 3.12+, Node.js 24, `uv`, Docker Desktop, the Supabase CLI, and the
non-Supabase values documented in [.env.example](.env.example).

```bash
cp .env.example .env
make sync
make dev
```

`make dev` starts an isolated local Supabase stack, applies the committed
migration, then starts Vite, the local LangGraph Agent Server and FastAPI BFF.
It does not reuse another project's users, database, or Storage.

```text
React Web:       http://127.0.0.1:5173
FastAPI BFF:     http://127.0.0.1:8000
LangGraph API:   http://127.0.0.1:2024
Supabase API:    http://127.0.0.1:55321
Supabase Studio: http://127.0.0.1:55323
Local email:     http://127.0.0.1:55324
```

For separate terminals:

```bash
make local-up
make agent
make api
make web
```

Use `make local-reset` to rebuild the local database from the single baseline
migration and `make local-down` to stop the LangAlpha Supabase containers.

## Supabase setup

Create a dedicated LangAlpha Supabase project and apply:

```text
supabase/migrations/20260725000100_create_assets.sql
```

The migration creates the private `assets` registry and private
`langalpha-assets` bucket. Browser users cannot query the table. The BFF and
Agent host use `SUPABASE_SECRET_KEY`; the UI receives only
`SUPABASE_PUBLISHABLE_KEY`.

For the external production-boundary test, set
`SUPABASE_TEST_ACCESS_TOKEN` to a short-lived token for a disposable test user.

## Vercel deployment

Configure these Production environment variables:

```text
LANGGRAPH_SERVER_URL
LANGGRAPH_API_KEY
LANGGRAPH_ASSISTANT_ID
SUPABASE_URL
SUPABASE_PUBLISHABLE_KEY
SUPABASE_SECRET_KEY
SUPABASE_STORAGE_BUCKET
APP_ID
APP_VERSION
APP_ENVIRONMENT=production
OPENAI_API_KEY
OPENAI_WEB_SEARCH_CONTEXT_SIZE
OPENAI_WEB_SEARCH_MAX_CALLS
MAX_RESEARCHER_MODEL_CALLS
MAX_RESEARCHER_TOOL_CALLS
SEC_USER_AGENT
FRED_API_KEY
MASSIVE_API_KEY
MASSIVE_SNAPSHOTS_ENABLED
DAYTONA_API_KEY
LANGSMITH_API_KEY
LANGSMITH_PROJECT
```

The Vercel BFF uses `LANGGRAPH_API_KEY` to authenticate to the protected Agent
Server. The Agent Server deployment needs the OpenAI, Daytona, Supabase,
LangSmith and application identity values as well. `SEC_USER_AGENT` must identify the
organization and a contact email. FRED and Massive keys stay on the Agent
Server host. Vercel does not run the LangGraph Agent Server.

`vercel.json` builds the React application into Vercel's `public` output,
packages the FastAPI streaming BFF separately, and gives that function the
Hobby-plan maximum 300-second ceiling. Local `.env`, test, design and
service-state files are explicitly excluded from the deployment artifact.
Runs continue on the Agent Server when a client reconnects.
`/health` is the process liveness endpoint; `/ready` additionally rejects
missing production dependencies and loopback Agent Server URLs.

## CI/CD

`.github/workflows/ci.yml` runs lint, unit tests, LangGraph config validation,
and a clean local Supabase migration rebuild on every push and pull request.

`.github/workflows/deploy.yml` runs on `main` and deploys in this order:

1. apply pending migrations to the linked Supabase project;
2. build/update the LangGraph production deployment;
3. build and deploy the prebuilt Vercel artifact;
4. verify `/health` and `/ready`.

Configure the GitHub `production` environment with:

```text
Variables:
SUPABASE_PROJECT_ID
SUPABASE_URL
VERCEL_ORG_ID
VERCEL_PROJECT_ID

Required secrets:
SUPABASE_ACCESS_TOKEN
SUPABASE_DB_PASSWORD
SUPABASE_PUBLISHABLE_KEY
SUPABASE_SECRET_KEY
LANGSMITH_API_KEY
OPENAI_API_KEY
DAYTONA_API_KEY
VERCEL_TOKEN

Optional capability secrets:
SEC_USER_AGENT
FRED_API_KEY
MASSIVE_API_KEY
```

## Runtime behavior

Selected inputs are hydrated from private Storage to:

```text
/workspace/input/assets/{asset_id}/{filename}
```

User-visible outputs belong under `/workspace/artifacts`. After a write, the
Agent host persists the file to Supabase before emitting `asset.ready`.

New messages use LangGraph native `enqueue`. During an active run, an empty
composer exposes cancel on the primary button, while a typed follow-up uses
`interrupt`. The first question becomes the thread title. Threads within one
Project share its workspace and Daytona sandbox; deleting a Thread preserves
shared files, while deleting the Project removes its Threads, Assets and
sandbox. HITL uses checkpoint resume. Daytona stops after one idle hour,
archives after seven days and is deleted explicitly with the Project.

## Quality gates

```bash
make lint
make test
```

Paid external checks:

```bash
make external-test
```

Agent Harness evaluation:

```bash
make eval
```

See [evaluation.md](docs/evaluation.md) for the fixture dataset, metrics, and
experiment workflow.

See [architecture.md](docs/architecture.md) and
[event-contract.md](docs/event-contract.md).
