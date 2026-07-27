from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from langalpha.domain.models import AgentEvent

_PASSTHROUGH_EVENTS = {
    "sandbox.bound",
    "asset.ready",
    "asset.failed",
    "widget.ready",
    "interrupt.requested",
}


def _record(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _message_role(value: dict[str, Any]) -> str:
    raw = str(value.get("role") or value.get("type") or "").lower()
    if raw in {"assistant", "ai"} or "aimessage" in raw:
        return "assistant"
    if raw in {"user", "human"} or "humanmessage" in raw:
        return "user"
    if raw == "tool" or "toolmessage" in raw:
        return "tool"
    return ""


def _public_annotation(value: Any) -> dict[str, Any] | None:
    annotation = _record(value)
    if annotation is None:
        return None
    nested = _record(annotation.get("url_citation")) or annotation
    if str(annotation.get("type") or nested.get("type") or "").lower() != "url_citation":
        return None
    url = nested.get("url")
    if not isinstance(url, str) or urlparse(url).scheme not in {"http", "https"}:
        return None
    result: dict[str, Any] = {
        "type": "url_citation",
        "url": url,
        "title": str(nested.get("title") or url),
    }
    for key in ("start_index", "end_index"):
        index = nested.get(key)
        if isinstance(index, int):
            result[key] = index
    return result


def _public_content(value: Any) -> str | list[dict[str, Any]]:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    blocks: list[dict[str, Any]] = []
    for value_block in value:
        block = _record(value_block)
        if block is None:
            continue
        block_type = str(block.get("type") or "").lower()
        if block_type not in {"text", "input_text", "output_text"}:
            continue
        text = block.get("text")
        if not isinstance(text, str):
            continue
        public_block: dict[str, Any] = {"type": block_type, "text": text}
        raw_annotations = block.get("annotations")
        raw_annotations = raw_annotations if isinstance(raw_annotations, list) else []
        annotations = [
            annotation
            for item in raw_annotations
            if (annotation := _public_annotation(item)) is not None
        ]
        if annotations:
            public_block["annotations"] = annotations
        blocks.append(public_block)
    return blocks


def public_message(value: Any, *, fallback_id: str) -> dict[str, Any] | None:
    message = _record(value)
    if message is None:
        return None
    role = _message_role(message)
    if role not in {"assistant", "user"}:
        return None
    content = _public_content(message.get("content"))
    if not content:
        return None
    return {
        "id": str(message.get("id") or fallback_id),
        "role": role,
        "content": content,
    }


def public_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        projected
        for index, message in enumerate(messages)
        if (projected := public_message(message, fallback_id=f"message-{index}")) is not None
    ]


def _event_messages(value: Any) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []

    def visit(current: Any, path: str = "message") -> None:
        if isinstance(current, list):
            for index, item in enumerate(current):
                visit(item, f"{path}-{index}")
            return
        record = _record(current)
        if record is None:
            return
        if _message_role(record):
            projected = public_message(record, fallback_id=path)
            if projected is not None:
                messages.append(projected)
            return
        for key in ("messages", "message", "value", "data", "output"):
            nested = record.get(key)
            if nested is not None:
                visit(nested, f"{path}-{key}")

    visit(value)
    return messages


def project_public_events(source: AgentEvent) -> list[AgentEvent]:
    """Return only stable, browser-safe product events for one upstream frame."""
    if source.type in {"message.delta", "message.completed"}:
        messages = _event_messages(source.payload)
        if not messages:
            return []
        return [
            source.model_copy(
                update={
                    "payload": {"messages": messages},
                }
            )
        ]
    if source.type in _PASSTHROUGH_EVENTS:
        return [source]
    if source.type == "run.error":
        message = source.payload.get("message") or source.payload.get("error")
        return [
            source.model_copy(
                update={
                    "payload": {
                        "message": str(message or "Agent run failed"),
                    }
                }
            )
        ]
    return []
