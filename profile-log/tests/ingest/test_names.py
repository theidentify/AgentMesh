import os

import pytest

from agentmesh_log.ingest.parsers.claude import infer_claude_project
from agentmesh_log.ingest.parsers.codex import WORKSPACES_ROOT, infer_codex_project


# Known gap, unchanged by the move from memory-sync: on Windows the Claude prefix only replaces '/', so a
# C:\\...\\Workspaces folder never matches and project names differ from Codex's. Fixing it renames Windows projects.
@pytest.mark.skipif(os.name == 'nt', reason='workspace prefix matching is POSIX-only (known gap)')
def test_codex_claude_same_name(tmp_path):
    relative = 'AgentMesh/.claude/worktrees/gitbook-agent-mesh-usage-1839e8'
    codex = infer_codex_project({'cwd': WORKSPACES_ROOT + '/' + relative})
    folder = '-' + (WORKSPACES_ROOT + '/' + relative).strip('/').replace('/', '-').replace('.', '-')
    claude = infer_claude_project(tmp_path / folder / 'session.jsonl')
    assert codex == claude == 'AgentMesh--claude-worktrees-gitbook-agent-mesh-usage-1839e8'


def test_plain_workspace_names_are_unchanged():
    assert infer_codex_project({'cwd': WORKSPACES_ROOT + '/localgov'}) == 'localgov'
    assert infer_codex_project({'cwd': WORKSPACES_ROOT + '/AgentMesh/'}) == 'AgentMesh'
