from pathlib import Path

import pytest

from agentmesh_log.ingest.parsers import claude, codex
from agentmesh_log.ingest.parsers.claude import encode_project_dir, infer_claude_project
from agentmesh_log.ingest.parsers.codex import WORKSPACES_ROOT, infer_codex_project


WORKTREE = 'AgentMesh/.claude/worktrees/gitbook-agent-mesh-usage-1839e8'
WORKTREE_NAME = 'AgentMesh--claude-worktrees-gitbook-agent-mesh-usage-1839e8'


def test_codex_claude_same_name(tmp_path):
    cwd = str(Path(WORKSPACES_ROOT, *WORKTREE.split('/')))
    codex_name = infer_codex_project({'cwd': cwd})
    claude_name = infer_claude_project(tmp_path / encode_project_dir(cwd) / 'session.jsonl')
    assert codex_name == claude_name == WORKTREE_NAME


def test_plain_workspace_names_are_unchanged():
    assert infer_codex_project({'cwd': WORKSPACES_ROOT + '/localgov'}) == 'localgov'
    assert infer_codex_project({'cwd': WORKSPACES_ROOT + '/AgentMesh/'}) == 'AgentMesh'


def test_encoding_matches_claude_code():
    assert encode_project_dir('/Users/me/Workspaces/AgentMesh') == '-Users-me-Workspaces-AgentMesh'
    assert encode_project_dir('/Users/john.doe/Workspaces') == '-Users-john-doe-Workspaces'
    assert encode_project_dir('C:\\Users\\me\\Workspaces\\AgentMesh') == 'C--Users-me-Workspaces-AgentMesh'


@pytest.fixture
def windows_home(monkeypatch):
    """Point both parsers at a Windows-shaped home, whatever OS the test runs on."""
    home = 'C:\\Users\\me'
    monkeypatch.setattr(codex, 'WORKSPACES_ROOT', home + '\\Workspaces')
    monkeypatch.setattr(codex, 'CODEX_SCRATCH_ROOT', home + '\\Documents\\Codex')
    monkeypatch.setattr(claude, '_PROJECT_PREFIX', encode_project_dir(home + '\\Workspaces') + '-')
    monkeypatch.setattr(claude, '_HOME_DIR', encode_project_dir(home))
    return home


def test_windows_codex_claude_same_name(windows_home, tmp_path):
    cwd = windows_home + '\\Workspaces\\' + WORKTREE.replace('/', '\\')
    assert claude._PROJECT_PREFIX == 'C--Users-me-Workspaces-'
    codex_name = infer_codex_project({'cwd': cwd})
    claude_name = infer_claude_project(tmp_path / encode_project_dir(cwd) / 'session.jsonl')
    assert codex_name == claude_name == WORKTREE_NAME


def test_windows_names_match_mac_names(windows_home, tmp_path):
    def claude_name(path):
        return infer_claude_project(tmp_path / encode_project_dir(path) / 'session.jsonl')

    assert infer_codex_project({'cwd': windows_home + '\\Workspaces\\localgov'}) == 'localgov'
    assert infer_codex_project({'cwd': windows_home + '\\Workspaces\\AgentMesh\\'}) == 'AgentMesh'
    assert infer_codex_project({'cwd': windows_home + '/Workspaces/localgov'}) == 'localgov'
    assert claude_name(windows_home + '\\Workspaces\\localgov') == 'localgov'
    assert claude_name(windows_home) == 'home'
    assert infer_codex_project({'cwd': windows_home + '\\Documents\\Codex\\2026-11-10\\x'}) == 'codex-scratch'


def test_windows_sibling_folder_is_not_a_workspace(windows_home):
    assert not codex._under(windows_home + '\\WorkspacesOld\\x', codex.WORKSPACES_ROOT)
