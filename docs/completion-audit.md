# End-to-end completion audit

Status date: 2026-07-24

This audit keeps the full clean-room rewrite objective intact. A contract test
is not treated as proof of a real provider, managed deployment, or trace.

## Proven in the current local worktree

| Requirement | Authoritative evidence | Status |
|---|---|---|
| New project, no legacy runtime imports | Source scan in `tests/test_factory.py`; `AGENTS.md` boundary | Passed |
| One Deep Agents harness | `create_deep_agent()` appears only in `agent/factory.py`; graph tool snapshot | Passed |
| One user-visible agent mode | Main graph plus internal researcher/reporter graphs; no Flash/PTC product mode | Passed |
| GPT-5.6 Luna Responses profile | Exact `ChatOpenAI` construction plus real Responses API call | Passed |
| Runtime-aware Daytona backend | Lazy lookup/create, strict binding, private/network-blocked lifecycle, writable-workdir path mapping | Passed |
| Empty new workspace | Skills/memory use routed backends; Daytona resolves only on a workspace operation | Passed |
| Host-side MCP | `MultiServerMCPClient`, server-issued context, allowlist, budget, full-result redaction | Passed |
| MCP-to-Python data bridge | Idempotent `DatasetMaterializer`, stable `DatasetRef`, prior ToolMessage lookup | Passed |
| Official QuickJS PTC | Static allowlist and bounded middleware configuration | Passed locally |
| Official async subagent surface | Researcher/reporter graph registration plus real start/check task execution | Passed |
| HITL and steering product contract | Real Ask User checkpoint interrupt/successor resume plus deterministic steering lifecycle | Passed |
| Thin run BFF | SDK create/get/list/join/cancel/resume and server-issued identity | Passed |
| Agent Server-owned streaming and recovery | Native cursor forwarding, state/run snapshot rebuild, terminal reconciliation | Passed |
| No duplicate runtime persistence | SQLite schema contains no runs, events, checkpoints, cursors, terminals, or Outbox | Passed |
| Artifact lifecycle | Upload, manifest projection, stable upsert, list and download | Passed |
| Product UI | Live stream/snapshot reducer, Ask/Plan, guidance, cancel, artifacts, usage and responsive layout | Contract passed |
| Structured widgets | Metric/table plus native SVG bar/line rendering and invalid-contract fallback | Browser passed |
| Limits and usage | Model/tool/subagent/time limits plus snapshot-derived usage/cost | Passed |
| Secret persistence guard | Recursive event/MCP redaction and real external gate scans DB/WAL/logs | Passed |

Current reproducible local result:

```text
make lint
  passed

uv run pytest -q -m "not external"
  30 Python tests passed

node --test tests/*.mjs
  4 JavaScript reducer/chart tests passed
```

## Still required before the full objective can be marked complete

| Required proof | Gate already implemented | Missing external state |
|---|---|---|
| Queryable LangSmith trace tree | Included in the real vertical slice | `LANGSMITH_API_KEY` |
| Managed LangSmith Deployment | `langgraph.json` and exact Agent Server pin are ready | Deployment creation and managed smoke run |
| Provider-backed SLO evidence | Limits, deterministic rejoin gates, and real samples exist | The blueprint's recent-50-run targets are not yet proven |

Run all local paid gates with credentials in the Git-ignored `.env`:

```bash
make external-test
```

No credential value should be committed to this repository, copied into a test
argument, written into Daytona, or recorded in this audit.

## Explicit upstream boundary

Deep Agents 0.6.12 rejects global filesystem allow/deny rules when the same
backend exposes `execute`, because shell execution is not governed by those
rules. LangAlpha uses the supported permission contract to protect routed
read-only resources and relies on Daytona for host isolation, secret exclusion,
and blocked network access. It deliberately does not implement a partial
file-tool-only policy and describe it as complete shell protection.
