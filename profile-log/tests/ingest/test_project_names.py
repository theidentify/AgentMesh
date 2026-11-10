import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))

from omp_memory.claude_parser import infer_claude_project
from omp_memory.codex_parser import WORKSPACES_ROOT, infer_codex_project


def test_codex_and_claude_name_the_same_workspace_identically(tmp_path):
    relative = 'AgentMesh/.claude/worktrees/gitbook-agent-mesh-usage-1839e8'
    codex = infer_codex_project({'cwd': WORKSPACES_ROOT + '/' + relative})
    folder = '-' + (WORKSPACES_ROOT + '/' + relative).strip('/').replace('/', '-').replace('.', '-')
    claude = infer_claude_project(tmp_path / folder / 'session.jsonl')
    assert codex == claude == 'AgentMesh--claude-worktrees-gitbook-agent-mesh-usage-1839e8'


def test_plain_workspace_names_are_unchanged():
    assert infer_codex_project({'cwd': WORKSPACES_ROOT + '/localgov'}) == 'localgov'
    assert infer_codex_project({'cwd': WORKSPACES_ROOT + '/AgentMesh/'}) == 'AgentMesh'
