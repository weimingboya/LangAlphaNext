from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any

from agentevals.trajectory.llm import create_async_trajectory_llm_as_judge
from openevals.llm import create_async_llm_as_judge

_GROUNDED_PROMPT = """\
You evaluate a financial research agent using supplied deterministic fixture truth.

Score the answer and structured research result:
- 1.0: all material claims are supported by successful tool results, all required
  reference facts and sources are present, conflicts and limitations are handled,
  and no material claim is fabricated.
- 0.5: the answer is substantially correct and grounded but has a minor omission
  that does not change the conclusion.
- 0.0: any material factual error, unsupported causal claim, missing authoritative
  source, concealed source conflict, or fabricated evidence.

<user_inputs>
{inputs}
</user_inputs>
<fixture_truth>
{reference_outputs}
</fixture_truth>
<agent_output_and_evidence>
{outputs}
</agent_output_and_evidence>
"""

_TRACE_PROMPT = """\
You evaluate the observable trajectory of a two-turn financial research agent.
Hidden chain-of-thought is intentionally absent and must not be required.

Score the trajectory:
- 1.0: correct two-turn async lifecycle, appropriate delegation, sufficient
  evidence retrieval, successful researcher result used in the final synthesis,
  no material duplicated or unused work, and the final response satisfies the goal.
- 0.5: the trajectory reaches a correct grounded result but contains one minor
  inefficiency or non-critical omission.
- 0.0: broken async lifecycle, wrong or missing evidence, researcher output not
  used, material unsupported synthesis, repeated wasteful calls, or failure to
  complete the task.

The reference context contains fixture truth and required behavior:
<reference_context>
{reference_outputs}
</reference_context>

Grade this observable trajectory:
<trajectory>
{outputs}
</trajectory>
"""


def _searchable(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def case_pass(
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    reference_outputs: dict[str, Any],
) -> dict[str, Any]:
    """Deterministic gate for facts, protocol invariants, and required behavior."""
    del inputs
    expected = reference_outputs.get("expectations") or {}
    failures: list[str] = []
    if outputs.get("error"):
        failures.append(f"runner error: {outputs['error']}")

    main_calls = outputs.get("main_tool_calls") or []
    researcher_calls = outputs.get("researcher_tool_calls") or []
    main_names = [str(call.get("name") or "") for call in main_calls]
    researcher_names = [str(call.get("name") or "") for call in researcher_calls]
    all_names = [*main_names, *researcher_names]

    for name in expected.get("required_main_tools") or []:
        if name not in main_names:
            failures.append(f"missing main tool {name}")
    for name in expected.get("required_researcher_tools") or []:
        if name not in researcher_names:
            failures.append(f"missing researcher tool {name}")
    for name in expected.get("forbidden_tools") or []:
        if name in all_names:
            failures.append(f"forbidden tool called: {name}")

    control_tools = {"ask_user", "check_async_task", "start_async_task"}
    required_business_tools = {
        *expected.get("required_main_tools", []),
        *expected.get("required_researcher_tools", []),
    } - control_tools
    successful_result_names = {
        str(result.get("name") or "")
        for result in outputs.get("tool_results") or []
        if result.get("status") != "error"
    }
    for name in sorted(required_business_tools):
        if name not in successful_result_names:
            failures.append(f"missing successful result for tool {name}")

    max_main_calls = expected.get("max_main_tool_calls")
    if isinstance(max_main_calls, int) and len(main_calls) > max_main_calls:
        failures.append(f"main tool calls {len(main_calls)} exceed maximum {max_main_calls}")

    tasks = outputs.get("tasks") or []
    expected_task_count = expected.get("expected_task_count")
    if isinstance(expected_task_count, int) and len(tasks) != expected_task_count:
        failures.append(f"expected {expected_task_count} task(s), got {len(tasks)}")
    max_tasks = expected.get("max_tasks")
    if isinstance(max_tasks, int) and len(tasks) > max_tasks:
        failures.append(f"task count {len(tasks)} exceeds maximum {max_tasks}")

    turns = outputs.get("turns") or []
    turn_required = expected.get("turn_required_tools") or {}
    turn_forbidden = expected.get("turn_forbidden_tools") or {}
    expected_statuses = expected.get("expected_task_status_after_turn") or {}
    for index, turn in enumerate(turns):
        names = [str(call.get("name") or "") for call in turn.get("tool_calls") or []]
        for name in turn_required.get(str(index), []):
            if name not in names:
                failures.append(f"turn {index} missing tool {name}")
        for name in turn_forbidden.get(str(index), []):
            if name in names:
                failures.append(f"turn {index} called forbidden tool {name}")
        expected_status = expected_statuses.get(str(index))
        if expected_status:
            statuses = {str(task.get("status") or "") for task in turn.get("tasks") or []}
            if statuses != {expected_status}:
                failures.append(
                    f"turn {index} task statuses {sorted(statuses)} != {expected_status}"
                )
        expected_run_status = (expected.get("expected_run_status_after_turn") or {}).get(str(index))
        if expected_run_status and turn.get("status") != expected_run_status:
            failures.append(
                f"turn {index} run status {turn.get('status')!r} != {expected_run_status!r}"
            )

    interrupt_kind = expected.get("expected_interrupt_kind")
    if interrupt_kind:
        kinds = {
            str(interrupt.get("kind") or "")
            for turn in turns
            for interrupt in turn.get("interrupts") or []
        }
        if interrupt_kind not in kinds:
            failures.append(f"missing interrupt kind {interrupt_kind}")

    if expected.get("answer_contains_task_id"):
        answer = str(outputs.get("answer") or "")
        if len(tasks) != 1 or str(tasks[0].get("task_id") or "") not in answer:
            failures.append("final answer does not contain the complete task_id")

    structured = outputs.get("structured_response")
    if expected.get("structured_response_required") and not isinstance(structured, dict):
        failures.append("missing structured_response")
    minimum_limitations = expected.get("minimum_limitations")
    if isinstance(minimum_limitations, int):
        limitations = structured.get("limitations") if isinstance(structured, dict) else []
        if not isinstance(limitations, list) or len(limitations) < minimum_limitations:
            failures.append(f"expected at least {minimum_limitations} limitation(s)")

    answer = _searchable(outputs.get("answer") or "")
    structured_output = _searchable(structured or {})
    for value in expected.get("required_answer_text") or []:
        if str(value).casefold() not in answer.casefold():
            failures.append(f"answer missing required text {value!r}")
    for url in expected.get("required_answer_urls") or []:
        if url not in answer:
            failures.append(f"answer missing required source {url}")
    for value in expected.get("required_structured_text") or []:
        if str(value).casefold() not in structured_output.casefold():
            failures.append(f"structured response missing required text {value!r}")
    for url in expected.get("required_structured_urls") or []:
        if url not in structured_output:
            failures.append(f"structured response missing required source {url}")

    return {
        "score": 0 if failures else 1,
        "comment": "; ".join(failures) if failures else "all deterministic checks passed",
    }


@lru_cache(maxsize=1)
def _grounded_judge():
    return create_async_llm_as_judge(
        prompt=_GROUNDED_PROMPT,
        model=os.getenv("EVAL_JUDGE_MODEL", "openai:gpt-5.4-mini"),
        feedback_key="grounded_complete",
        continuous=True,
        choices=[0.0, 0.5, 1.0],
        use_reasoning=True,
    )


@lru_cache(maxsize=1)
def _trajectory_judge():
    return create_async_trajectory_llm_as_judge(
        prompt=_TRACE_PROMPT,
        model=os.getenv("EVAL_JUDGE_MODEL", "openai:gpt-5.4-mini"),
        feedback_key="trace_quality",
        continuous=True,
        choices=[0.0, 0.5, 1.0],
        use_reasoning=True,
    )


def _scaled_result(result: dict[str, Any]) -> dict[str, Any]:
    score = result.get("score")
    scaled = round(float(score) * 2) if isinstance(score, int | float) else None
    return {
        "score": scaled,
        "comment": str(
            result.get("comment") or result.get("reasoning") or "judge returned no explanation"
        ),
    }


async def grounded_complete(
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    reference_outputs: dict[str, Any],
) -> dict[str, Any]:
    if not (reference_outputs.get("judge") or {}).get("grounded_complete"):
        return {"score": None, "comment": "not applicable to this case"}
    result = await _grounded_judge()(
        inputs=inputs,
        outputs={
            "answer": outputs.get("answer"),
            "structured_response": outputs.get("structured_response"),
            "successful_tool_results": [
                result
                for result in outputs.get("tool_results") or []
                if result.get("status") != "error"
            ],
        },
        reference_outputs=reference_outputs.get("reference") or {},
    )
    return _scaled_result(result)


async def trace_quality(
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    reference_outputs: dict[str, Any],
) -> dict[str, Any]:
    del inputs
    if not (reference_outputs.get("judge") or {}).get("trace_quality"):
        return {"score": None, "comment": "not applicable to this case"}
    reference_context = {
        "expectations": reference_outputs.get("expectations") or {},
        "fixture_truth": reference_outputs.get("reference") or {},
    }
    result = await _trajectory_judge()(
        outputs=outputs.get("trajectory") or [],
        reference_outputs=[
            {
                "role": "system",
                "content": json.dumps(
                    reference_context,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            }
        ],
    )
    return _scaled_result(result)
