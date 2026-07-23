# Official capability reuse register

This register keeps one owner for every core Agent semantic. LangAlpha adapters
may add product identity, durable event shapes, artifact metadata, and transport,
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
| Runtime state | LangGraph Agent Server | Product/runtime ID binding only | Repository and server tests |
| Runtime stream | LangGraph SDK resumable `join_stream` | Stable DomainEvent projection, cursor, reconciliation | Rejoin test in `tests/test_server.py` |
| Event fan-out | `redis.asyncio` Pub/Sub | Atomic Outbox projection and idempotent event envelope | `tests/test_outbox.py` |
| Technical trace | LangSmith | Product stores only source, cost, artifact, and event facts | Deployment configuration |

The repository must continue to satisfy:

```text
create_deep_agent() appears only in agent/factory.py
langchain.agents.create_agent() does not appear in project source
PTCSandbox and legacy LangAlpha runtime are not imported
```
