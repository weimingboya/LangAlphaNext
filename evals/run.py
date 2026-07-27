from __future__ import annotations

import argparse
import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

import httpx
from langsmith import Client

from evals.dataset import DATASET_NAME, sync_dataset
from evals.evaluators import case_pass, grounded_complete, trace_quality
from evals.fixtures import FIXTURE_VERSION
from evals.runner import AgentServerEvalTarget, EvalRunnerConfig

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server(url: str, process: subprocess.Popen[bytes], log: BinaryIO) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            log.seek(0)
            output = log.read().decode(errors="replace")
            raise RuntimeError(f"eval Agent Server exited during startup:\n{output[-8_000:]}")
        try:
            if httpx.get(f"{url}/ok", timeout=1).is_success:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise TimeoutError(f"eval Agent Server did not become ready: {url}")


@contextmanager
def _local_eval_server() -> Iterator[str]:
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    environment = {
        **os.environ,
        "EVAL_AGENT_SERVER_URL": url,
        "LANGGRAPH_SERVER_URL": url,
        "LANGSMITH_PROJECT": os.getenv(
            "EVAL_LANGSMITH_PROJECT",
            "langalpha-evals",
        ),
        "LANGSMITH_TRACING": "true",
        "LANGCHAIN_CALLBACKS_BACKGROUND": "false",
        "LANGGRAPH_CLI_NO_ANALYTICS": "1",
    }
    executable = Path(sys.executable).parent / "langgraph"
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(
            [
                str(executable),
                "dev",
                "--config",
                "langgraph.eval.json",
                "--no-browser",
                "--no-reload",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            _wait_for_server(url, process, log)
            yield url
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def _git_sha() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _metadata() -> dict[str, object]:
    return {
        "git_sha": _git_sha(),
        "fixture_version": FIXTURE_VERSION,
        "agent_model": os.getenv("OPENAI_MODEL", "gpt-5.6-luna"),
        "reasoning_effort": os.getenv("OPENAI_REASONING_EFFORT", "medium"),
        "judge_model": os.getenv("EVAL_JUDGE_MODEL", "openai:gpt-5.4-mini"),
        "max_model_calls": os.getenv("MAX_MODEL_CALLS", "20"),
        "max_tool_calls": os.getenv("MAX_TOOL_CALLS", "80"),
        "max_researcher_model_calls": os.getenv(
            "MAX_RESEARCHER_MODEL_CALLS",
            "16",
        ),
        "max_researcher_tool_calls": os.getenv(
            "MAX_RESEARCHER_TOOL_CALLS",
            "40",
        ),
    }


async def _evaluate(args: argparse.Namespace, server_url: str) -> None:
    client = Client()
    sync_dataset(client, dataset_name=args.dataset)
    data: object = args.dataset
    if args.case:
        requested = set(args.case)
        selected = [
            example
            for example in client.list_examples(dataset_name=args.dataset)
            if str((example.metadata or {}).get("case_id")) in requested
        ]
        found = {str((example.metadata or {}).get("case_id")) for example in selected}
        if missing := requested - found:
            raise ValueError(f"unknown case_id(s): {', '.join(sorted(missing))}")
        data = selected
    evaluators = [case_pass]
    if not args.no_judges:
        evaluators.extend([grounded_complete, trace_quality])
    target = AgentServerEvalTarget(
        EvalRunnerConfig(
            server_url=server_url,
            api_key=os.getenv("LANGGRAPH_API_KEY") or None,
            timeout_seconds=args.timeout,
            keep_threads=args.keep_threads,
        )
    )
    results = await client.aevaluate(
        target.run,
        data=data,
        evaluators=evaluators,
        experiment_prefix=args.experiment_prefix,
        description="LangAlpha Agent Harness offline evaluation",
        metadata=_metadata(),
        max_concurrency=args.concurrency,
        num_repetitions=args.repetitions,
        blocking=True,
    )
    await results.wait()
    print(f"Experiment: {results.experiment_name}")
    print(f"Results: {results.url}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the LangAlpha v1 Agent Harness evaluation.",
    )
    parser.add_argument("--dataset", default=DATASET_NAME)
    parser.add_argument("--experiment-prefix", default="langalpha-harness-v1")
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument(
        "--case",
        action="append",
        help="Run one case_id; repeat the option to select multiple cases.",
    )
    parser.add_argument(
        "--server-url",
        help="Use an existing eval Agent Server instead of starting one.",
    )
    parser.add_argument(
        "--no-judges",
        action="store_true",
        help="Run only deterministic case_pass checks.",
    )
    parser.add_argument(
        "--keep-threads",
        action="store_true",
        help="Keep eval threads on the Agent Server for manual inspection.",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.repetitions < 1:
        raise SystemExit("--repetitions must be at least 1")
    if args.concurrency < 1:
        raise SystemExit("--concurrency must be at least 1")
    if args.server_url:
        asyncio.run(_evaluate(args, args.server_url))
        return
    with _local_eval_server() as server_url:
        asyncio.run(_evaluate(args, server_url))


if __name__ == "__main__":
    main()
