# Agent Harness evaluation

LangAlpha uses one small, fixture-backed LangSmith dataset to compare Agent
Harness changes. It is an optimization aid, not a production release gate.

## What is evaluated

The immutable `langalpha-harness-v1` dataset contains six explicit protocol
cases:

1. simple financial concept: answer directly without tools;
2. one deterministic SEC fact: use the direct SEC tool, not a researcher;
3. missing company identity: interrupt through `ask_user`;
4. async research start: return the complete task ID and do not poll in the
   same turn;
5. complex async research: start in turn one, collect in turn two, and
   synthesize SEC and FRED evidence;
6. researcher source conflict: preserve the structured contract, prefer SEC,
   and disclose the conflicting secondary fixture.

Four policy variants ask for the same representative work without naming tool
functions or prescribing the complete trajectory. They measure whether a
candidate still chooses direct SEC and FRED tools for narrow work, delegates a
multi-source task, and resolves a source conflict without test-specific
instructions.

The fixture graph uses the production `DeepAgentFactory`, prompts, middleware,
budgets, async protocol, and model. Only external research tools are replaced
with deterministic, same-named fixtures.

## Metrics

- `case_pass` is the deterministic comparison filter. It checks required and
  forbidden actions, async task states, HITL, and structured output. Required
  answer facts and URLs must appear in the user-visible answer; raw tool
  results cannot satisfy answer requirements.
- `grounded_complete` is a 0/1/2 LLM judge for the complex synthesis and source
  conflict cases.
- `trace_quality` is a 0/1/2 AgentEvals judge for the single complex two-turn
  trace. It sees observable messages, tool calls, tool results, and fixture
  truth, but never hidden reasoning.
- calls, tokens, task count, first-turn latency, per-turn latency, and total
  latency are logged in each run output. Compare efficiency only among runs
  that pass `case_pass`.

## Run it

Configure `.env`, then run:

```bash
make eval
```

That command:

1. starts an isolated local Agent Server with `langgraph.eval.json`;
2. creates or verifies the immutable `langalpha-harness-v1` LangSmith dataset;
3. runs the ten examples with concurrency one;
4. records the Git SHA, model, reasoning effort, budgets, and fixture version;
5. prints the LangSmith experiment URL.

For a cheap extraction or protocol check without LLM judges:

```bash
uv run --env-file .env python -m evals.run --no-judges
```

For a paired baseline or candidate stability measurement:

```bash
uv run --env-file .env python -m evals.run \
  --experiment-prefix langalpha-harness-baseline \
  --repetitions 3
```

Use one repetition while iterating. Run the same selected cases three times for
both baseline and candidate before comparing pass rate, grounding, tokens,
tool calls, first-turn latency, and total latency.

Run only selected cases while iterating:

```bash
uv run --env-file .env python -m evals.run \
  --case complex_async_research \
  --case researcher_source_conflict
```

## Canary checks

Canaries are release checks, not daily Harness scores:

- Native web search must perform at least one provider search and preserve an
  HTTP citation in the final answer.
- The production boundary test must hydrate an input asset, use Daytona, write
  `/workspace/artifacts/report.md`, persist it, and download it through the BFF.

They remain under the paid `external` pytest marker and are run with:

```bash
make external-test
```

## Design constraints

- Inspect real Agent Server state before changing extraction logic.
- Keep fixture values immutable; create a new dataset version instead of
  silently mutating an existing one.
- Never score an exact full trajectory when multiple valid research paths
  exist. Deterministic trajectory checks cover only protocol invariants.
- Do not treat researcher self-reported confidence as factual accuracy.
