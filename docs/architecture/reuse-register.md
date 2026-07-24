# Official capability reuse register

This register keeps one owner for every core Agent semantic. LangAlpha adapters
may add product identity, artifact metadata, snapshot shaping, and transport,
but do not implement fallback runtimes.

| Capability | Single owner | LangAlpha extension | Contract evidence |
|---|---|---|---|
| Agent harness | Deep Agents `create_deep_agent` | `DeepAgentFactory` selects profile and domain tools | Source scan and graph tool snapshot |
| Todo, filesystem, summarization, skills | Deep Agents middleware | Read-only packaged routes, writable Store memory routes, and Daytona workspace binding | `tests/test_factory.py`, `tests/test_backends.py` |
| Model provider | `ChatOpenAI` Responses API | GPT-5.6 Luna profile and bounded standard retry | Factory/model tests and local graph load |
| Python/Shell workspace | Official `DaytonaSandbox` | Runtime-aware lookup, strict binding, artifact manifest events | `tests/test_backends.py` |
| Long-term memory | Deep Agents `StoreBackend` + LangGraph Store | User/workspace namespace functions | `tests/test_backends.py` |
| MCP runtime | `MultiServerMCPClient` and tool interceptor | Context, allowlist, budget, redaction | `tests/test_mcp.py` |
| Programmatic tool calling | `CodeInterpreterMiddleware` | Static allowlist and resource limits | Factory tool snapshot |
| Async subagents | Deep Agents `AsyncSubAgent` | Researcher/reporter graph registration and start limit | Factory tool snapshot |
| HITL | LangGraph `interrupt` and command resume | Typed Ask User/Plan payloads and product cards | `tests/test_server.py` |
| Runtime state | LangGraph Agent Server | Product-thread mapping and read-only RunView shaping | Repository and server tests |
| Runtime stream | LangGraph SDK resumable `join_stream` | Redacted per-run SSE proxy; Agent Server retains cursor | `tests/test_server.py` |
| Reload/recovery | Agent Server thread state and run history | Snapshot shaping plus Daytona artifact reconciliation | `tests/test_server.py` |
| Concurrency | Agent Server `multitask_strategy="reject"` | HTTP 409 translation only | `tests/test_server.py` |
| Technical trace | LangSmith | No parallel trace or runtime-event database | Deployment configuration |

The repository must continue to satisfy:

```text
create_deep_agent() appears only in agent/factory.py
langchain.agents.create_agent() does not appear in project source
PTCSandbox and legacy LangAlpha runtime are not imported
```
