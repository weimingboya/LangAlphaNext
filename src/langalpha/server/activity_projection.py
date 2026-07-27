from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from langalpha.domain.models import AgentEvent

_TOOL_LABELS = {
    "ask_user": "Request clarification",
    "check_async_task": "Check research task",
    "edit_file": "Edit file",
    "execute": "Run calculation",
    "fred_get_observations": "Fetch macroeconomic observations",
    "fred_search_series": "Search FRED series",
    "glob": "Find files",
    "grep": "Search files",
    "inspect_asset": "Inspect workspace file",
    "ls": "List files",
    "market_get_bars": "Fetch market price history",
    "market_get_corporate_actions": "Fetch corporate actions",
    "market_get_snapshots": "Fetch market snapshots",
    "market_resolve_instrument": "Resolve market instrument",
    "read_file": "Read file",
    "sec_get_company_facts": "Fetch SEC company facts",
    "sec_get_filing": "Read SEC filing",
    "sec_list_filings": "List SEC filings",
    "sec_resolve_company": "Resolve SEC company",
    "web_search": "Search the web",
    "show_widget": "Build result widget",
    "start_async_task": "Start research task",
    "list_async_tasks": "Review research tasks",
    "cancel_async_task": "Cancel research task",
    "write_file": "Create file",
}

_SUBAGENT_TOOLS = {
    "start_async_task",
    "check_async_task",
    "list_async_tasks",
    "cancel_async_task",
}

_RESULT_SUMMARY_TOOLS = {
    "fred_get_observations",
    "fred_search_series",
    "market_get_bars",
    "market_get_corporate_actions",
    "market_get_snapshots",
    "market_resolve_instrument",
    "sec_get_company_facts",
    "sec_get_filing",
    "sec_list_filings",
    "sec_resolve_company",
    "show_widget",
    *_SUBAGENT_TOOLS,
}

_DETAIL_KEYS = (
    "query",
    "symbol",
    "cik",
    "forms",
    "series_id",
    "start_date",
    "end_date",
    "agent_name",
    "subagent_type",
    "description",
    "task_id",
    "file_path",
    "path",
    "filename",
)


def _record(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _trim(value: str, limit: int = 420) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1].rstrip()}…"


def _reasoning_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n\n".join(text for item in value if (text := _reasoning_text(item)))
    if not isinstance(value, dict):
        return ""
    return "\n\n".join(
        text
        for key in ("reasoning", "summary", "summary_text", "text", "content")
        if key in value
        if (text := _reasoning_text(value.get(key)))
    )


def _message_kind(value: dict[str, Any]) -> str:
    for key in ("type", "role"):
        kind = value.get(key)
        if isinstance(kind, str):
            return kind.lower()
    return ""


def _tool_name(value: dict[str, Any]) -> str:
    direct = value.get("name") or value.get("tool_name")
    if isinstance(direct, str):
        return direct
    if str(value.get("type") or "").lower() == "web_search_call":
        return "web_search"
    function = _record(value.get("function"))
    name = function.get("name") if function else None
    return name if isinstance(name, str) else ""


def _tool_call_id(value: dict[str, Any]) -> str:
    for key in ("tool_call_id", "call_id", "id"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate:
            return candidate
    return ""


def _tool_arguments(value: dict[str, Any]) -> dict[str, Any]:
    candidate: Any = (
        value.get("args") or value.get("arguments") or value.get("input") or value.get("action")
    )
    function = _record(value.get("function"))
    if candidate is None and function:
        candidate = function.get("arguments")
    if isinstance(candidate, str):
        try:
            candidate = json.loads(candidate)
        except json.JSONDecodeError:
            return {}
    return candidate if isinstance(candidate, dict) else {}


def _compact_tool_detail(tool_name: str, arguments: dict[str, Any]) -> str | None:
    file_path = arguments.get("file_path")
    if tool_name == "read_file" and isinstance(file_path, str) and file_path:
        details = [Path(file_path).name]
        offset = arguments.get("offset")
        limit = arguments.get("limit")
        if isinstance(offset, int) and isinstance(limit, int) and limit > 0:
            details.append(f"lines {offset + 1}-{offset + limit}")
        return _trim(" · ".join(details), 180)

    details: list[str] = []
    for key in _DETAIL_KEYS:
        value = arguments.get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            rendered = ", ".join(str(item) for item in value[:3])
        elif key in {"file_path", "path", "filename"}:
            rendered = Path(str(value)).name
        else:
            rendered = str(value)
        details.append(rendered)
        if len(details) == 2:
            break
    return _trim(" · ".join(details), 180) if details else None


def _parse_content(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _count_label(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else plural or f'{singular}s'}"


def _result_summary(tool_name: str, content: Any) -> str | None:
    if tool_name not in _RESULT_SUMMARY_TOOLS:
        return None

    parsed = _parse_content(content)
    if tool_name == "start_async_task":
        return "Task started"

    if isinstance(parsed, list):
        return _count_label(len(parsed), "record")
    if not isinstance(parsed, dict):
        return None

    status = parsed.get("status")
    if tool_name in _SUBAGENT_TOOLS and isinstance(status, str):
        status_labels = {
            "pending": "Task pending",
            "running": "Task running",
            "completed": "Task completed",
            "complete": "Task completed",
            "success": "Task completed",
            "failed": "Task failed",
            "error": "Task failed",
            "cancelled": "Task cancelled",
            "canceled": "Task cancelled",
        }
        if status.lower() in status_labels:
            return status_labels[status.lower()]

    row_count = parsed.get("row_count")
    if isinstance(row_count, int):
        return _count_label(row_count, "row")

    for key, singular in (
        ("records", "record"),
        ("results", "result"),
        ("filings", "filing"),
        ("facts", "fact"),
        ("observations", "observation"),
        ("evidence", "evidence item"),
    ):
        values = parsed.get(key)
        if isinstance(values, list):
            return _count_label(len(values), singular)

    title = parsed.get("title")
    if tool_name == "show_widget" and isinstance(title, str):
        return _trim(title, 120)

    for key in ("filename", "path"):
        value = parsed.get(key)
        if isinstance(value, str) and value:
            return Path(value).name
    return None


def _activity_id(run_id: str, kind: str, identity: str) -> str:
    return f"{kind}:{run_id}:{identity}"


def _subagent_task_id(value: dict[str, Any], content: Any = None) -> str | None:
    task_id = _tool_arguments(value).get("task_id")
    if isinstance(task_id, str) and task_id:
        return task_id
    parsed = _parse_content(content)
    if isinstance(parsed, dict):
        for key in ("task_id", "thread_id"):
            candidate = parsed.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
    if isinstance(parsed, str):
        match = re.search(r"\btask_id:\s*([A-Za-z0-9_-]+)", parsed)
        if match:
            return match.group(1)
    return None


def _tool_candidate(
    *,
    run_id: str,
    value: dict[str, Any],
    status: str,
    fallback_identity: str,
) -> dict[str, Any] | None:
    name = _tool_name(value)
    if name == "write_todos":
        return None
    call_id = _tool_call_id(value) or fallback_identity
    if not name and not call_id:
        return None

    is_subagent = name in _SUBAGENT_TOOLS
    identity = _subagent_task_id(value) if is_subagent else None
    candidate: dict[str, Any] = {
        "id": _activity_id(
            run_id,
            "subagent" if is_subagent else "tool",
            identity or call_id,
        ),
        "kind": "subagent" if is_subagent else "tool",
        "title": _TOOL_LABELS.get(name, name.replace("_", " ").title() or "Research tool"),
        "status": status,
        "tool_name": name,
    }
    detail = _compact_tool_detail(name, _tool_arguments(value))
    if detail:
        candidate["detail"] = detail
    return candidate


def _tool_result_candidate(
    *,
    run_id: str,
    value: dict[str, Any],
    fallback_identity: str,
) -> dict[str, Any] | None:
    name = _tool_name(value)
    if name == "write_todos":
        return None
    call_id = _tool_call_id(value) or fallback_identity
    content = value.get("content") if "content" in value else value.get("result")
    is_subagent = name in _SUBAGENT_TOOLS
    task_id = _subagent_task_id(value, content) if is_subagent else None
    raw_status = value.get("status")
    is_error = bool(value.get("error")) or (
        isinstance(raw_status, str) and raw_status.lower() in {"error", "failed"}
    )
    candidate: dict[str, Any] = {
        "id": _activity_id(
            run_id,
            "subagent" if is_subagent else "tool",
            task_id or call_id,
        ),
        "kind": "subagent" if is_subagent else "tool",
        "title": _TOOL_LABELS.get(name, name.replace("_", " ").title() or "Research tool"),
        "status": "error" if is_error else "complete",
        "tool_name": name,
    }
    if is_subagent and task_id and task_id != call_id:
        candidate["replaces_id"] = _activity_id(run_id, "subagent", call_id)
    if is_error:
        candidate["detail"] = "Tool returned an error"
    else:
        summary = _result_summary(name, content)
        if summary:
            candidate["detail"] = summary
    return candidate


def _reasoning_candidate(
    *,
    run_id: str,
    value: dict[str, Any],
    message_identity: str,
    block_index: int,
    source_type: str,
) -> dict[str, Any] | None:
    text = _reasoning_text(value)
    if not text:
        return None
    identity = str(value.get("id") or f"{message_identity}:{block_index}")
    raw_status = value.get("status")
    is_complete = source_type == "message.completed" or (
        isinstance(raw_status, str) and raw_status.lower() in {"complete", "completed"}
    )
    return {
        "id": _activity_id(run_id, "reasoning", identity),
        "kind": "reasoning",
        "title": "Analysis",
        "detail": text,
        "status": "complete" if is_complete else "running",
    }


def _event_from_candidate(source: AgentEvent, candidate: dict[str, Any]) -> AgentEvent:
    fingerprint = json.dumps(candidate, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(f"{source.id}:{fingerprint}".encode()).hexdigest()[:16]
    return AgentEvent(
        id=f"activity:{source.id}:{digest}",
        type="activity.updated",
        thread_id=source.thread_id,
        run_id=source.run_id,
        payload=candidate,
        created_at=source.created_at,
    )


def project_activity_events(source: AgentEvent) -> list[AgentEvent]:
    """Project provider-shaped messages into compact, user-facing activity events."""
    candidates: dict[str, dict[str, Any]] = {}

    def add(candidate: dict[str, Any] | None) -> None:
        if candidate is not None:
            candidates[str(candidate["id"])] = candidate

    def visit(value: Any, path: str = "payload") -> None:
        if isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}:{index}")
            return
        record = _record(value)
        if record is None:
            return

        kind = _message_kind(record)
        if kind in {"tool", "toolmessage", "tool_message"}:
            add(
                _tool_result_candidate(
                    run_id=source.run_id,
                    value=record,
                    fallback_identity=path,
                )
            )
            return

        if kind in {"ai", "assistant", "aimessage", "ai_message", "aimessagechunk"}:
            message_identity = str(record.get("id") or path)
            for key in ("tool_calls", "tool_call_chunks"):
                calls = record.get(key)
                if isinstance(calls, list):
                    for index, call in enumerate(calls):
                        call_record = _record(call)
                        if call_record:
                            add(
                                _tool_candidate(
                                    run_id=source.run_id,
                                    value=call_record,
                                    status="running",
                                    fallback_identity=f"{message_identity}:{key}:{index}",
                                )
                            )

            content = record.get("content")
            blocks = content if isinstance(content, list) else []
            for index, block in enumerate(blocks):
                block_record = _record(block)
                if not block_record:
                    continue
                block_type = str(block_record.get("type") or "").lower()
                if "reasoning" in block_type:
                    add(
                        _reasoning_candidate(
                            run_id=source.run_id,
                            value=block_record,
                            message_identity=message_identity,
                            block_index=index,
                            source_type=source.type,
                        )
                    )
                elif block_type in {
                    "function_call",
                    "tool_call",
                    "tool_use",
                    "web_search_call",
                    "mcp_call",
                }:
                    raw_status = block_record.get("status")
                    status = (
                        "complete"
                        if isinstance(raw_status, str)
                        and raw_status.lower() in {"complete", "completed", "success"}
                        else "running"
                    )
                    add(
                        _tool_candidate(
                            run_id=source.run_id,
                            value=block_record,
                            status=status,
                            fallback_identity=f"{message_identity}:content:{index}",
                        )
                    )
            return

        for key in ("messages", "message", "value", "data", "output"):
            nested = record.get(key)
            if nested is not None:
                visit(nested, f"{path}:{key}")
        if source.type == "state.updated":
            for key, nested in record.items():
                if key not in {"messages", "message", "value", "data", "output"}:
                    visit(nested, f"{path}:{key}")

    visit(source.payload)
    return [_event_from_candidate(source, candidate) for candidate in candidates.values()]
