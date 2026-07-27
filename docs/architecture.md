# LangAlpha Next architecture

## Ownership

LangAlpha has one Deep Agents harness and five explicit infrastructure owners:

| State or capability | Owner |
|---|---|
| Thread, run, checkpoint, message, interrupt, queue | LangGraph Agent Server |
| User identity and access token | Supabase Auth |
| Asset registry and durable file bytes | Supabase Postgres + private Storage |
| Ephemeral computation and working files | Daytona |
| Traces and evaluations | LangSmith |
| Product API, authorization checks and UI | FastAPI on Vercel |

There is no SQLite product database, Thread mirror, runtime binding table, Run
mirror, guidance queue or event outbox.

The public Thread ID is the LangGraph `thread_id`. Thread metadata contains the
server-written `owner_id`, `project_id`, `title`, `schema_version` and optional
`sandbox_id`. Every operation reads that metadata and checks the authenticated
Supabase user before accessing the Thread.

## Repository boundaries

```text
api/                         Vercel FastAPI function entrypoint
src/langalpha/
  agent/                     LangGraph and Deep Agents assembly
  assets/                    durable Asset persistence
  backends/                  Daytona and memory backends
  capabilities/              host-side research tools
  integrations/              external protocol adapters
  security/                  redaction and trust boundaries
  server/
    routes/                  FastAPI system, Thread, Run and Asset APIs
    dependencies.py          request-scoped authorization and services
    main.py                  application assembly only
supabase/                    one reproducible database baseline
web/                         complete React/Vite application root
  src/
    domain/                  pure event and product models
    features/                auth, assets, research and Thread UI
    shared/                  reusable API and UI primitives
    styles/                  application styles
tests/                       Python unit and contract tests
```

The repository remains a single deployable product. Python and web dependency
roots are separate, but there is no workspace tool or monorepo layer because
there is only one frontend and one BFF.

Python tests remain flat while the suite is small. Paid provider and full-stack
gates stay isolated in `tests/external/`; small frontend domain tests remain
co-located with their TypeScript modules.

## Run flow

The BFF creates Agent Server runs with:

- native `multitask_strategy="enqueue"|"interrupt"`;
- resumable `messages`, `updates` and `custom` streams;
- server-written trace metadata (`owner_id`, `project_id`, `thread_id`,
  `turn_id`, version and environment);
- a `RunContext` containing only trusted identity, Thread, turn, input Asset IDs
  and the expected Daytona sandbox.

HITL uses native LangGraph checkpoint interrupts and `Command(resume=...)`.
Cancellation delegates to Agent Server. There is no custom steering middleware.

The UI follows `runs.join_stream` through an authenticated SSE proxy. Reload
builds one snapshot from Agent Server state and run history plus the Supabase
Asset registry.

## Research capabilities

OpenAI Responses web search is the only general-web provider. The main graph
and isolated researcher graph receive it as a provider-hosted tool, so search
actions and URL annotations remain native Responses content. The main graph
also owns fixed host-side tools for:

- SEC company resolution, filing lists, primary filing text and XBRL facts;
- FRED series discovery and observations;
- Massive instrument resolution, snapshots, aggregate bars and corporate actions.

Massive snapshots are entitlement-gated and remain hidden unless
`MASSIVE_SNAPSHOTS_ENABLED=true`. The researcher receives only read-only SEC,
FRED, Massive instrument, historical-bar and corporate-action tools. It loads
the `financial-research` and `sec-filing-analysis` skills from a lightweight
resource backend and does not provision or write to Daytona.

There is no provider fallback in this phase. A failed primary provider remains
visible rather than being silently replaced by a source with different
semantics. The browser renders only validated HTTP(S) citation URLs, and usage
reports web search actions separately from model tokens.

The only asynchronous specialist is `researcher`. Final synthesis and report
writing stay with the main graph so evidence and conclusions share one
authoritative context. The researcher returns structured evidence and
limitations; it does not create user-facing artifacts or persist memory.
Async collection forwards only that structured contract plus task and trace
identifiers, never the researcher's reasoning or message history. SEC monetary
facts include deterministic USD scale conversions, while Massive bars echo the
adjustment flag, market timezone, observed dates and reproducible request URL.

The default synchronous `general-purpose` subagent is disabled through a Deep
Agents Harness Profile. Researcher Threads carry owner, project, parent Thread
and parent turn metadata plus a child `RunContext`; cancelling a parent turn
cancels its active researchers, and deleting a parent Thread deletes its child
Threads.

## Assets and Daytona

The `assets` table is the only product persistence table. Input bytes and
generated artifacts live in the private `langalpha-assets` bucket under:

```text
{owner_id}/{thread_id}/{asset_id}/{sha256}/{filename}
```

Small browser files use signed direct uploads. Files over 6 MiB use Supabase
TUS with 6 MiB chunks. Daytona hydrates selected inputs into
`/workspace/input/assets/{asset_id}/{filename}` when first needed.

User-visible outputs are written to `/workspace/artifacts`. Known `write`,
`edit`, and upload targets sync directly. Arbitrary command execution uses a
metadata-only manifest diff and downloads only changed outputs. The Agent host
uploads an immutable Supabase object, atomically updates the Asset row, and only
then emits `asset.ready`.
Daytona is therefore disposable rather than permanent storage.

Sandbox resolution is deterministic by `thread_id` labels. The Agent host
persists `sandbox_id` directly to LangGraph Thread metadata; it does not depend
on a browser stream being connected. Label mismatches fail closed.

## Deployment

Vercel builds the React + TypeScript + Vite UI into its `public` static output
and packages the FastAPI BFF as a separate function. `LANGGRAPH_SERVER_URL`
points to the deployed LangGraph Agent Server. Supabase migrations provision
the private Asset registry and bucket. Server secrets exist only in Vercel and
the Agent Server host.

Default Daytona lifecycle is stop after 60 idle minutes, archive after seven
days and delete after 30 days. Thread deletion explicitly cancels active runs,
deletes durable Assets, verifies and deletes its Daytona sandbox, then deletes
the LangGraph Thread.
