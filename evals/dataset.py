from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langsmith import Client

DATASET_NAME = "langalpha-harness-v1"
DATASET_PATH = Path(__file__).with_name("dataset.json")


def load_dataset() -> list[dict[str, Any]]:
    payload = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("eval dataset must be a non-empty JSON array")
    case_ids: set[str] = set()
    for example in payload:
        if not isinstance(example, dict):
            raise TypeError("each eval example must be an object")
        inputs = example.get("inputs")
        outputs = example.get("outputs")
        metadata = example.get("metadata")
        if not all(isinstance(value, dict) for value in (inputs, outputs, metadata)):
            raise TypeError("each eval example requires inputs, outputs, and metadata objects")
        case_id = str(inputs.get("case_id") or "")
        if not case_id or case_id != metadata.get("case_id"):
            raise ValueError("case_id must be present and match metadata.case_id")
        if case_id in case_ids:
            raise ValueError(f"duplicate eval case_id: {case_id}")
        case_ids.add(case_id)
    return payload


def sync_dataset(
    client: Client,
    *,
    dataset_name: str = DATASET_NAME,
) -> str:
    """Create the immutable v1 dataset, or verify an existing copy."""
    local_examples = load_dataset()
    if client.has_dataset(dataset_name=dataset_name):
        dataset = client.read_dataset(dataset_name=dataset_name)
    else:
        dataset = client.create_dataset(
            dataset_name,
            description=(
                "LangAlpha Agent Harness core evaluation cases. "
                "Deterministic fixtures; production traces are not required."
            ),
            metadata={"fixture_version": "2026-07-27.v1"},
        )

    remote_by_case = {
        str((example.metadata or {}).get("case_id")): example
        for example in client.list_examples(dataset_id=dataset.id)
    }
    missing: list[dict[str, Any]] = []
    for local in local_examples:
        case_id = local["inputs"]["case_id"]
        remote = remote_by_case.get(case_id)
        if remote is None:
            missing.append(local)
            continue
        remote_metadata = remote.metadata or {}
        metadata_matches = all(
            remote_metadata.get(key) == value for key, value in local["metadata"].items()
        )
        if (
            remote.inputs != local["inputs"]
            or remote.outputs != local["outputs"]
            or not metadata_matches
        ):
            raise RuntimeError(
                f"remote example {case_id!r} differs from the immutable local v1 dataset; "
                "create a new dataset version instead of mutating it"
            )
    if missing:
        client.create_examples(dataset_id=dataset.id, examples=missing)
    return str(dataset.id)
