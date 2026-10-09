"""Bounded build contract for the standalone AgentMesh CLI."""
from pathlib import Path

import pytest

import standalone_build


def test_build_command_bundles_runtime_data_and_isolates_output(tmp_path):
    root = Path(__file__).resolve().parent
    command = standalone_build.build_command(root, tmp_path / 'dist', tmp_path / 'work')
    assert command[:3] == [standalone_build.sys.executable, '-m', 'PyInstaller']
    assert '--onefile' in command
    assert '--clean' in command
    assert str(root / 'agentmesh.py') == command[-1]
    assert str(root / 'schema.sql') + standalone_build.os.pathsep + '.' in command
    assert str(root / 'src') in command
    assert 'omp_memory.parser' in command
    assert 'omp_memory.codex_parser' in command
    assert 'omp_memory.claude_parser' in command
    for module in ('brand', 'recall_memory', 'sqlite_memory', 'memory_sync', 'workflow', 'summarize_memory'):
        assert module in command
    assert '--distpath' in command and str(tmp_path / 'dist') in command
    assert '--workpath' in command and str(tmp_path / 'work') in command
    assert '--additional-hooks-dir' in command
    assert (root / 'packaging-hooks' / 'hook-workflow.py').is_file()


def test_build_rejects_output_inside_source(tmp_path):
    root = Path(__file__).resolve().parent
    with pytest.raises(ValueError, match='outside source'):
        standalone_build.build_command(root, root / 'dist', tmp_path / 'work')


def test_build_rejects_missing_entry_or_schema(tmp_path):
    with pytest.raises(ValueError, match='source'):
        standalone_build.build_command(tmp_path, tmp_path / 'dist', tmp_path / 'work')
