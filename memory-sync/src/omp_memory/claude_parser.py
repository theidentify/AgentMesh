"""Parse Claude Code session JSONL transcripts (~/.claude/projects) into observation events.

Each project directory holds ``<session-id>.jsonl`` files; subagent transcripts live in
``<session-id>/subagents/`` and are not ingested. Assistant output is split into one
record per content block, so only the last text block of a turn (the reply the user
actually reads) becomes ``assistant_response``; interim narration is dropped.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable

from .parser import ObservationEvent, _normalize_timestamp, is_excluded, redact_secrets


AGENT = "claude"
_PROJECT_PREFIX = "-" + str(Path("~/Workspaces").expanduser()).strip("/").replace("/", "-") + "-"
_HOME_DIR = "-" + str(Path("~").expanduser()).strip("/").replace("/", "-")
_RESULT_LIMIT = 2000
_INPUT_LIMIT = 500
_SYSTEM_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
_SKIPPED_USER_PREFIXES = ("<local-command-stdout>", "<local-command-caveat>", "<local-command-stderr>")
_RESULT_TOOLS = {"Bash"}


def _key(source_path: str, source_event_id: str, kind: str) -> str:
    return sha256(f"{AGENT}\0{source_path}\0{source_event_id}\0{kind}".encode("utf-8")).hexdigest()


def infer_claude_project(path: Path) -> str:
    name = path.parent.name
    if name.startswith(_PROJECT_PREFIX):
        return name[len(_PROJECT_PREFIX) :]
    if "-scratch-" in name:
        return "claude-scratch"
    if name == _HOME_DIR:
        return "home"
    return name.lstrip("-") or "unknown"


def clean_user_text(text: str) -> str:
    text = _SYSTEM_REMINDER.sub("", text).strip()
    if text.startswith(_SKIPPED_USER_PREFIXES):
        return ""
    return text


def _blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [block for block in content if isinstance(block, dict)]
    return []


def _tool_input_text(name: str, tool_input: Any) -> str:
    if not isinstance(tool_input, dict):
        return ""
    if name == "Bash":
        return str(tool_input.get("command") or "")
    for field in ("file_path", "path", "pattern", "url", "query", "description"):
        if tool_input.get(field):
            return str(tool_input[field])
    return json.dumps(tool_input, ensure_ascii=False)[:_INPUT_LIMIT]


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(
        str(block.get("text") or "")
        for block in _blocks(content)
        if block.get("type") == "text"
    )


def parse_claude_lines(
    lines: Iterable[str],
    source_path: str,
    *,
    project: str | None = None,
) -> list[ObservationEvent]:
    events: list[ObservationEvent] = []
    tool_names: dict[str, str] = {}
    pending_reply: tuple[str, str, Any, str | None] | None = None

    def add(
        record_id: str,
        kind: str,
        role: str,
        content: str,
        ts: Any,
        session_id: str | None,
        metadata: dict | None = None,
    ) -> None:
        content = redact_secrets(content.strip())
        if not content:
            return
        source_event_id = f"{record_id}:{kind}"
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

    def flush_reply() -> None:
        nonlocal pending_reply
        if pending_reply:
            record_id, text, ts, session_id = pending_reply
            add(record_id, "assistant_response", "assistant", text, ts, session_id)
        pending_reply = None

    for line in lines:
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(record, dict) or record.get("type") not in {"user", "assistant"}:
            continue
        if record.get("isSidechain") or record.get("isMeta") or record.get("isCompactSummary"):
            continue
        message = record.get("message") or {}
        blocks = _blocks(message.get("content"))
        record_id = str(record.get("uuid") or sha256(line.encode("utf-8")).hexdigest())
        ts = record.get("timestamp")
        session_id = record.get("sessionId")

        if record["type"] == "user":
            texts = [clean_user_text(str(b.get("text") or "")) for b in blocks if b.get("type") == "text"]
            text = "\n".join(t for t in texts if t)
            if text:
                flush_reply()
                add(record_id, "user_request", "user", text, ts, session_id)
            for block in blocks:
                if block.get("type") != "tool_result":
                    continue
                name = tool_names.get(str(block.get("tool_use_id")), "tool")
                is_error = bool(block.get("is_error"))
                if not is_error and name not in _RESULT_TOOLS:
                    continue
                output = _result_text(block.get("content"))[:_RESULT_LIMIT]
                if not output.strip() and not is_error:
                    continue
                add(
                    f"{record_id}:{block.get('tool_use_id')}",
                    "tool_result",
                    "toolResult",
                    f"{name} — {'error' if is_error else 'success'} — {output}",
                    ts,
                    session_id,
                    {"tool_name": name, "is_error": is_error},
                )
            continue

        for block in blocks:
            block_type = block.get("type")
            if block_type == "text" and str(block.get("text") or "").strip():
                pending_reply = (record_id, str(block["text"]), ts, session_id)
            elif block_type == "tool_use":
                pending_reply = None
                name = str(block.get("name") or "tool")
                tool_names[str(block.get("id"))] = name
                summary = _tool_input_text(name, block.get("input"))
                if is_excluded(summary):
                    continue
                add(
                    f"{record_id}:{block.get('id')}",
                    "tool_call",
                    "assistant",
                    f"{name} — {summary}",
                    ts,
                    session_id,
                    {"tool_name": name},
                )

    flush_reply()
    return events
