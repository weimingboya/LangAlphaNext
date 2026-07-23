# LangAlpha Next

This is a clean-room rewrite. Do not import runtime code from the sibling legacy
`LangAlpha` repository.

## Architecture invariants

- `create_deep_agent()` is the only agent harness constructor.
- OpenAI is the only model provider; the default model is `gpt-5.6-luna`.
- Daytona is the only production sandbox.
- LangGraph Agent Server owns thread, run, checkpoint, interrupt, and cancellation state.
- Product APIs expose only versioned DomainEvents, never raw provider chunks.
- Native MCP runs outside Daytona. Large results are materialized as datasets before
  Daytona Python analysis.
- Secrets come only from process/deployment environment variables and never enter
  prompts, sandbox files, DomainEvents, or repository files.

## Commands

```bash
uv sync --all-groups
uv run langgraph dev --no-browser --no-reload --port 2024
uv run uvicorn langalpha.server.main:app --reload --port 8000
uv run pytest -q
uv run ruff check src tests
```
