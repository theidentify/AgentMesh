from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any, Iterable


_SECRET_PATTERNS = (
    re.compile(r"(?i)(Authorization\s*:\s*Bearer\s+)(\S+)"),
    re.compile(r"(?i)\b(token\s*=\s*)([^\s,;]+)"),
    re.compile(r"(?i)\b(api[_-]?key\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(?i)\b(password\s*=\s*)([^\s,;]+)"),
)


@dataclass(frozen=True, slots=True)
class ObservationEvent:
    event_key: str
    source_path: str
    source_event_id: str
    kind: str
    content: str
    role: str
    occurred_at: str | None = None
    project: str | None = None
    source_session_id: str | None = None
    task_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    source_agent: str = "omp"


# Local agent discussion board (pilot). Its bodies are discussion data, not work
# evidence, so tool calls/results that touch it are never ingested.
EXCLUDED_MARKERS = (
    "agent-discussion-pilot",
    "pilot-codex-ingest-summary-handoff",
)


def is_excluded(*texts: object) -> bool:
    return any(marker in str(text) for text in texts if text for marker in EXCLUDED_MARKERS)


def redact_secrets(text: str) -> str:
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", redacted)
    return redacted


def _stable_key(source_path: str, source_event_id: str, kind: str) -> str:
    raw = f"omp\0{source_path}\0{source_event_id}\0{kind}".encode("utf-8")
    return sha256(raw).hexdigest()


def _normalize_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
    return str(value)


def _event(
    *,
    source_path: str,
    record: dict[str, Any],
    source_event_id: str,
    role: str,
    kind: str,
    content: str,
    project: str | None,
    metadata: dict[str, Any] | None = None,
    occurred_at: str | None = None,
) -> ObservationEvent:
    return ObservationEvent(
        event_key=_stable_key(source_path, source_event_id, kind),
        source_path=source_path,
        source_event_id=source_event_id,
        source_session_id=record.get("sessionId") or record.get("session_id"),
        occurred_at=_normalize_timestamp(occurred_at or record.get("timestamp")),
        project=project,
        task_ref=record.get("taskRef") or record.get("task_ref"),
        role=role,
        kind=kind,
        content=redact_secrets(content.strip()),
        metadata=metadata or {},
    )


def _format_tool_call(item: dict[str, Any]) -> str:
    parts = [str(item.get("name") or "unknown")]
    if item.get("intent"):
        parts.append(str(item["intent"]))
    arguments = item.get("arguments")
    if isinstance(arguments, dict):
        parts.extend(f"{key}={value}" for key, value in sorted(arguments.items()))
    return " — ".join(parts)


def parse_omp_lines(
    lines: Iterable[str],
    source_path: str,
    project: str | None = None,
) -> list[ObservationEvent]:
    events: list[ObservationEvent] = []

    for line in lines:
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(record, dict) or record.get("type") != "message":
            continue

        message = record.get("message")
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        if not isinstance(content, list):
            continue

        record_id = str(record.get("id") or _stable_key(source_path, line, "record"))
        if role == "toolResult":
            text_parts = [
                str(item.get("text") or "").strip()
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            result_text = "\n".join(part for part in text_parts if part)[:2000]
            if result_text and not is_excluded(result_text):
                tool_name = str(message.get("toolName") or "unknown")
                is_error = bool(message.get("isError"))
                events.append(
                    _event(
                        source_path=source_path,
                        record=record,
                        source_event_id=f"{record_id}:result:0",
                        role=role,
                        kind="tool_result",
                        content=(
                            f"{tool_name} — {'error' if is_error else 'success'} — "
                            f"{result_text}"
                        ),
                        project=project,
                        metadata={"tool_name": tool_name, "is_error": is_error},
                        occurred_at=message.get("timestamp"),
                    )
                )
            continue
        if role not in {"user", "assistant"}:
            continue

        for index, item in enumerate(content):
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            source_event_id = f"{record_id}:{'tool' if item_type == 'toolCall' else 'text'}:{index}"

            if role == "user" and item_type == "text" and str(item.get("text") or "").strip():
                events.append(
                    _event(
                        source_path=source_path,
                        record=record,
                        source_event_id=source_event_id,
                        role=role,
                        kind="user_request",
                        content=str(item["text"]),
                        project=project,
                    )
                )
            elif role == "assistant" and item_type == "toolCall" and not is_excluded(_format_tool_call(item)):
                events.append(
                    _event(
                        source_path=source_path,
                        record=record,
                        source_event_id=source_event_id,
                        role=role,
                        kind="tool_call",
                        content=_format_tool_call(item),
                        project=project,
                        metadata={"tool_name": item.get("name")},
                    )
                )
            elif (
                role == "assistant"
                and item_type == "text"
                and str(item.get("text") or "").strip()
                and (
                    item.get("phase") == "final_answer"
                    or message.get("stopReason") == "stop"
                )
            ):
                events.append(
                    _event(
                        source_path=source_path,
                        record=record,
                        source_event_id=source_event_id,
                        role=role,
                        kind="assistant_response",
                        content=str(item["text"]),
                        project=project,
                        occurred_at=message.get("timestamp"),
                    )
                )

    return events
