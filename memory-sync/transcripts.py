"""Pure transcript reading for OMP, Codex and Claude JSONL (SQLite retirement R0).

No database: the caller owns the cursor (file identity, byte offset, line number) and decides where events go.
The staging ingest stores them in its database today; the broker writer will use the same reader later.
Only complete newline-terminated records are consumed, so a line still being written is read next time.
"""
from hashlib import sha256
import json
from pathlib import Path
import sys


def parsers():
    """The pure parsers under src/ (never ingest.py/db.py, which open PostgreSQL)."""
    source = Path(__file__).resolve().parent / 'src'
    if source.is_dir() and str(source) not in sys.path:
        sys.path.insert(0, str(source))
    from omp_memory import parser, codex_parser, claude_parser
    return parser, codex_parser, claude_parser


def resolve(transcript, agent, project=None):
    """(path, project, skipped) for one transcript; Codex reviewer sessions and files without meta are skipped."""
    omp, codex, claude = parsers()
    transcript = Path(transcript).expanduser().resolve()
    if agent not in ('omp', 'codex', 'claude'):
        raise ValueError('unsupported agent')
    if agent == 'codex':
        meta = codex.read_session_meta(transcript)
        if meta is None or codex.is_reviewer_session(meta):
            return transcript, project, True
        return transcript, project or codex.infer_codex_project(meta), False
    if agent == 'claude':
        return transcript, project or claude.infer_claude_project(transcript), False
    workspace = transcript.parent.name
    return transcript, project or (workspace[len('-Workspaces-'):] if workspace.startswith('-Workspaces-')
                                   else workspace.lstrip('-') or 'unknown'), False


def identity(transcript):
    stat = Path(transcript).stat()
    return f'{stat.st_dev}:{stat.st_ino}', stat.st_size


def same_file(stored, current):
    """Whether a stored identity names the file `current` names. Compares the inode only: macOS renumbers APFS st_dev
    across reboots, so a stored device number goes stale while the file is unchanged."""
    return isinstance(stored, str) and stored.rpartition(':')[2] == current.rpartition(':')[2]


def read(transcript, agent, project, cursor=None):
    """Parse the complete records after a cursor.

    cursor is (file identity, byte offset, line number) or None. A different identity or an offset past the end
    restarts the file. Returns None when no complete record is pending, else a dict with the events, the
    invalid-JSON errors (path, offset, line, hash, type, message), the start offset and the next cursor.
    """
    omp, codex, claude = parsers()
    current, size = identity(transcript)
    old_identity, offset, line_number = cursor or (None, 0, 0)
    if not same_file(old_identity, current) or offset > size:
        offset, line_number = 0, 0
    with Path(transcript).open('rb') as handle:
        handle.seek(offset)
        tail = handle.read()
    final_newline = tail.rfind(b'\n')
    if final_newline < 0:
        return dict(events=[], errors=[], lines=0, offset=offset, complete=False,
                    cursor=(current, offset, line_number))
    complete = tail[:final_newline + 1]
    lines, errors, position = [], [], offset
    for index, raw in enumerate(complete.splitlines(keepends=True), 1):
        line = raw.decode('utf-8', errors='replace').rstrip('\r\n')
        lines.append(line)
        try:
            json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append((str(transcript), position, line_number + index, sha256(raw).hexdigest(), 'invalid_json', str(exc)))
        position += len(raw)
    if agent == 'codex':
        meta = codex.read_session_meta(transcript) or {}
        events = codex.parse_codex_lines(lines, str(transcript), project=project,
                                         session_id=meta.get('id') or meta.get('session_id'))
    elif agent == 'claude':
        events = claude.parse_claude_lines(lines, str(transcript), project=project)
    else:
        events = omp.parse_omp_lines(lines, str(transcript), project=project)
    return dict(events=events, errors=errors, lines=len(lines), offset=offset, complete=True,
                cursor=(current, offset + len(complete), line_number + len(lines)))
