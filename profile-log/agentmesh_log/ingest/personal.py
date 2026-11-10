"""Transcript ingestion is personal-only: it never reads from or writes into a team workspace home.

A personal home is AGENTMESH_HOME (default ~/.agentmesh). Each team workspace has its own broker home under
`AGENTMESH_HOME/ws/<id8>/` (one-click join, PR #194), with its own device, broker and agent keys. The ingest
job reads the owner's own Claude and Codex transcripts, so every path it is configured with, and the broker its
summarizer key reaches, must belong to the personal home. Anything else is refused before a transcript is read.

The checks are path and address based and need no broker call:
- AGENTMESH_HOME itself is not a workspace home (its parent directory is not named `ws`).
- No configured path (agent key, mapping, state and status directories) lies under `AGENTMESH_HOME/ws/`.
- The summarizer key's broker address is not inside `AGENTMESH_HOME/ws/` (Unix sockets live in the home) and is
  not the address any workspace agent key names (Windows pipes are named by device, not by path).
"""
import json
import os
from pathlib import Path

from .protocol import ValidationError

WORKSPACES = 'ws'
REFUSED = 'workspace home refused; transcript ingestion is personal-only'


def home():
    return Path(os.path.abspath(os.environ.get('AGENTMESH_HOME', str(Path.home() / '.agentmesh'))))


def _inside(path, parent):
    try:
        Path(os.path.abspath(path)).relative_to(parent)
        return True
    except ValueError:
        return False


def _address(key_path):
    """The broker address an agent key names, or None when the key is unreadable. Only `address` is used."""
    try:
        value = json.loads(Path(key_path).read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError):
        return None
    address = value.get('address') if isinstance(value, dict) else None
    return address if isinstance(address, str) and address else None


def workspace_addresses(root):
    """Broker addresses named by the agent keys of every workspace home under root/ws."""
    found = set()
    base = Path(root) / WORKSPACES
    if not base.is_dir():
        return found
    for key in sorted(base.glob('*/agents/*.key')):
        address = _address(key)
        if address:
            found.add(address)
    return found


def check(paths=(), agent_key=None, root=None):
    """Raise ValidationError unless the home, every path and the agent key's broker are personal."""
    root = Path(os.path.abspath(root)) if root is not None else home()
    if root.parent.name == WORKSPACES:
        raise ValidationError(REFUSED)
    workspaces = root / WORKSPACES
    for path in [*paths, *([agent_key] if agent_key else [])]:
        if path and _inside(path, workspaces):
            raise ValidationError(REFUSED)
    if agent_key:
        address = _address(agent_key)
        if address and (_inside(address, workspaces) or address in workspace_addresses(root)):
            raise ValidationError(REFUSED)
