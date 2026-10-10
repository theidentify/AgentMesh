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
    for module in ('brand', 'recall_memory', 'sqlite_memory', 'memory_sync', 'workflow', 'summarize_memory', 'install_inspect', 'security_wizard', 'install_setup'):
        assert module in command
    assert '--distpath' in command and str(tmp_path / 'dist') in command
    assert '--workpath' in command and str(tmp_path / 'work') in command
    assert '--additional-hooks-dir' in command
    assert (root / 'packaging-hooks' / 'hook-workflow.py').is_file()
    for module in ('install_adopt', 'worker_lifecycle', 'mac_replace', 'windows_install', 'windows_task'):
        assert module in command


def test_mac_operator_launcher_is_relocatable_and_has_no_private_defaults(tmp_path):
    standalone_build.operator_launcher(tmp_path)
    launcher = tmp_path / 'Replace-AgentMesh.command'
    text = launcher.read_text()
    assert 'mac-replace' in text
    assert 'agentmesh' in text and '--manifest' in text
    assert '/Users/' not in text
    if __import__('os').name != 'nt':
        assert launcher.stat().st_mode & 0o100


def test_windows_click_helper_plans_before_install_and_has_no_implicit_start(tmp_path):
    helper = standalone_build.windows_launcher(tmp_path)
    content = helper.read_bytes()
    assert b'\r\n' in content and b'\n' not in content.replace(b'\r\n', b'')
    text = content.decode('utf-8')
    assert text.count('"%~dp0agentmesh.exe" windows-install --runtime ') == 2
    assert text.index('--dry-run') < text.index('if errorlevel 1 goto finish')
    assert 'windows-autostart' not in text and 'worker-start' not in text and 'worker-stop' not in text
    assert '%LOCALAPPDATA%\\AgentMesh\\data\\runtime.json' in text


def test_build_rejects_output_inside_source(tmp_path):
    root = Path(__file__).resolve().parent
    with pytest.raises(ValueError, match='outside source'):
        standalone_build.build_command(root, root / 'dist', tmp_path / 'work')


def test_build_rejects_missing_entry_or_schema(tmp_path):
    with pytest.raises(ValueError, match='source'):
        standalone_build.build_command(tmp_path, tmp_path / 'dist', tmp_path / 'work')
