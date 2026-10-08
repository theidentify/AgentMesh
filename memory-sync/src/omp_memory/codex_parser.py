"""Parse Codex rollout JSONL transcripts (~/.codex/sessions) into observation events.

Codex writes one ``session_meta`` record first, then ``event_msg`` records whose
``item_completed`` payloads carry normalized items (UserMessage, AgentMessage,
CommandExecution, McpToolCall, FileChange, ...). Only those normalized items are
used; ``response_item`` duplicates and encrypted reasoning are ignored.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable

from .parser import ObservationEvent, _normalize_timestamp, is_excluded, redact_secrets


AGENT = "codex"
WORKSPACES_ROOT = str(Path("~/Workspaces").expanduser())
CODEX_SCRATCH_ROOT = str(Path("~/Documents/Codex").expanduser())
_RESULT_LIMIT = 2000
_REQUEST_MARKER = "## My request:"
_AMBIENT_BLOCK = re.compile(r"<in-app-browser-context\b.*?</in-app-browser-context>", re.S)
_SKIPPED_THREAD_SOURCES = {"guardian_review"}


def _key(source_path: str, source_event_id: str, kind: str) -> str:
    return sha256(f"{AGENT}\0{source_path}\0{source_event_id}\0{kind}".encode("utf-8")).hexdigest()


def read_session_meta(path: str | Path) -> dict[str, Any] | None:
    with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
        first = handle.readline()
    try:
        record = json.loads(first)
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict) or record.get("type") != "session_meta":
        return None
    payload = record.get("payload")
    return payload if isinstance(payload, dict) else None


def is_reviewer_session(meta: dict[str, Any] | None) -> bool:
    """Guardian/approval-reviewer threads are Codex-internal and not user work."""
    if not meta:
        return False
    if meta.get("thread_source") in _SKIPPED_THREAD_SOURCES:
        return True
    source = meta.get("source")
    return isinstance(source, dict) and "subagent" in source


def infer_codex_project(meta: dict[str, Any] | None) -> str:
    cwd = str((meta or {}).get("cwd") or "").rstrip("/")
    if cwd.startswith(WORKSPACES_ROOT + "/"):
        return cwd[len(WORKSPACES_ROOT) + 1 :].replace("/", "-")
    if cwd.startswith(CODEX_SCRATCH_ROOT + "/"):
        return "codex-scratch"
    return Path(cwd).name or "unknown"


def clean_user_text(text: str) -> str:
    """Drop Codex-injected ambient UI context and keep the user's actual request."""
    if _REQUEST_MARKER in text:
        text = text.rsplit(_REQUEST_MARKER, 1)[1]
    text = _AMBIENT_BLOCK.sub("", text)
    return text.strip()


def _texts(content: Any) -> list[str]:
    if not isinstance(content, list):
        return []
    return [
        str(item.get("text") or "")
        for item in content
        if isinstance(item, dict) and str(item.get("type", "")).lower() == "text"
    ]


def _command_text(command: Any) -> str:
    if isinstance(command, list):
        if len(command) >= 3 and command[1] in {"-lc", "-c"}:
            return str(command[2])
        return " ".join(str(part) for part in command)
    return str(command or "")


def parse_codex_lines(
    lines: Iterable[str],
    source_path: str,
    *,
    project: str | None = None,
    session_id: str | None = None,
) -> list[ObservationEvent]:
    events: list[ObservationEvent] = []

    def add(item_id: str, kind: str, role: str, content: str, ts: Any, metadata: dict | None = None) -> None:
        content = redact_secrets(content.strip())
        if not content:
            return
        source_event_id = f"{item_id}:{kind}"
        events.append(
            ObservationEvent(
                event_key=_key(source_path, source_event_id, kind),
                source_path=source_path,
                source_event_id=source_event_id,
                source_session_id=session_id,
                occurred_at=_normalize_timestamp(ts),
                project=project,
                role=role,
                kind=kind,
                content=content,
                metadata=metadata or {},
                source_agent=AGENT,
            )
        )

    for line in lines:
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(record, dict) or record.get("type") != "event_msg":
            continue
        payload = record.get("payload") or {}
        if payload.get("type") != "item_completed":
            continue
        item = payload.get("item") or {}
        item_type = item.get("type")
        item_id = str(item.get("id") or sha256(line.encode("utf-8")).hexdigest())
        ts = record.get("timestamp")
        turn = {"turn_id": payload.get("turn_id")} if payload.get("turn_id") else {}

        if item_type == "UserMessage":
            text = "\n".join(clean_user_text(part) for part in _texts(item.get("content")))
            add(item_id, "user_request", "user", text, ts, turn)
        elif item_type == "AgentMessage" and item.get("phase") == "final_answer":
            add(item_id, "assistant_response", "assistant", "\n".join(_texts(item.get("content"))), ts, turn)
        elif item_type in {"CommandExecution", "McpToolCall", "FileChange"} and is_excluded(
            json.dumps(item, ensure_ascii=False), item.get("cwd")
        ):
            continue
        elif item_type == "CommandExecution":
            command = _command_text(item.get("command"))
            add(item_id, "tool_call", "assistant", f"exec — {command}", ts, {"tool_name": "exec", **turn})
            exit_code = item.get("exit_code")
            output = str(item.get("aggregated_output") or item.get("stdout") or "")[:_RESULT_LIMIT]
            if output.strip() or exit_code not in (None, 0, "0"):
                is_error = exit_code not in (None, 0, "0")
                add(
                    item_id,
                    "tool_result",
                    "toolResult",
                    f"exec — {'error' if is_error else 'success'} — {output}",
                    ts,
                    {"tool_name": "exec", "is_error": is_error, "exit_code": exit_code, **turn},
                )
        elif item_type == "McpToolCall":
            name = f"{item.get('server')}.{item.get('tool')}"
            arguments = item.get("arguments")
            args = json.dumps(arguments, ensure_ascii=False)[:500] if arguments else ""
            add(item_id, "tool_call", "assistant", f"{name} — {args}", ts, {"tool_name": name, **turn})
        elif item_type == "FileChange":
            changes = item.get("changes") or {}
            summary = ", ".join(
                f"{(change or {}).get('type', 'change')} {path}" for path, change in changes.items()
            )
            add(item_id, "tool_call", "assistant", f"apply_patch — {summary}", ts, {"tool_name": "apply_patch", **turn})

    return events
