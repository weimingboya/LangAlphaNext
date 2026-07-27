from __future__ import annotations

import json
from pathlib import Path

from evals.dataset import load_dataset
from evals.evaluators import case_pass
from evals.runner import normalize_trajectory


def test_eval_dataset_has_six_versioned_core_cases() -> None:
    examples = load_dataset()

    assert len(examples) == 6
    assert {example["metadata"]["tier"] for example in examples} == {"core"}
    assert {example["metadata"]["fixture_version"] for example in examples} == {"2026-07-26.v1"}
    assert {example["inputs"]["case_id"] for example in examples} == {
        "simple_financial_concept",
        "single_sec_fact",
        "missing_entity_hitl",
        "async_research_start",
        "complex_async_research",
        "researcher_source_conflict",
    }


def test_normalize_trajectory_keeps_observable_actions_without_reasoning() -> None:
    trajectory = normalize_trajectory(
        [
            {"type": "human", "content": "question"},
            {
                "type": "ai",
                "content": [
                    {"type": "reasoning", "summary": [{"text": "private"}]},
                    {"type": "text", "text": "visible"},
                ],
                "tool_calls": [
                    {
                        "id": "call-1",
                        "name": "sec_resolve_company",
                        "args": {"query": "AAPL"},
                    }
                ],
            },
            {
                "type": "tool",
                "name": "sec_resolve_company",
                "tool_call_id": "call-1",
                "content": '{"records":[]}',
            },
        ]
    )

    assert trajectory == [
        {"role": "user", "content": "question"},
        {
            "role": "assistant",
            "content": "visible",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "sec_resolve_company",
                        "arguments": '{"query": "AAPL"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "name": "sec_resolve_company",
            "content": '{"records":[]}',
            "tool_call_id": "call-1",
        },
    ]
    assert "private" not in json.dumps(trajectory)


def test_case_pass_enforces_async_turn_boundaries_and_evidence() -> None:
    example = next(
        item for item in load_dataset() if item["inputs"]["case_id"] == "complex_async_research"
    )
    output = {
        "answer": (
            "FY2023 383.285; FY2024 391.035; DFF 5.02 and 5.14. "
            "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json "
            "https://fred.stlouisfed.org/series/DFF"
        ),
        "structured_response": {"limitations": ["No causal inference."]},
        "main_tool_calls": [
            {"name": "start_async_task"},
            {"name": "check_async_task"},
        ],
        "researcher_tool_calls": [
            {"name": "sec_resolve_company"},
            {"name": "sec_get_company_facts"},
            {"name": "fred_get_observations"},
        ],
        "turns": [
            {
                "tool_calls": [{"name": "start_async_task"}],
                "tasks": [{"status": "running"}],
                "interrupts": [],
            },
            {
                "tool_calls": [{"name": "check_async_task"}],
                "tasks": [{"status": "success"}],
                "interrupts": [],
            },
        ],
        "tasks": [{"task_id": "task-1"}],
        "tool_results": [
            {"name": "sec_resolve_company", "status": "success"},
            {"name": "sec_get_company_facts", "status": "success"},
            {"name": "fred_get_observations", "status": "success"},
        ],
        "error": None,
    }

    assert case_pass(example["inputs"], output, example["outputs"])["score"] == 1

    output["turns"][0]["tool_calls"].append({"name": "check_async_task"})
    result = case_pass(example["inputs"], output, example["outputs"])
    assert result["score"] == 0
    assert "turn 0 called forbidden tool check_async_task" in result["comment"]


def test_eval_langgraph_config_uses_fixture_graphs() -> None:
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "langgraph.eval.json").read_text())

    assert config["graphs"] == {
        "main": "./evals/graphs.py:main_graph",
        "researcher": "./evals/graphs.py:research_graph",
    }
