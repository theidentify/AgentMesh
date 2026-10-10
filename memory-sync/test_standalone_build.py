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
    # One call: the CLI shows its plan once and asks before writing anything.
    assert text.count('"%~dp0agentmesh.exe" windows-install --runtime ') == 1 and '--dry-run' not in text
    assert text.index('if errorlevel 2') < text.index('if errorlevel 1 goto finish')
    assert 'windows-autostart' not in text and 'worker-start' not in text and 'worker-stop' not in text
    assert '%LOCALAPPDATA%\\AgentMesh\\data\\runtime.json' in text


def test_build_rejects_output_inside_source(tmp_path):
    root = Path(__file__).resolve().parent
    with pytest.raises(ValueError, match='outside source'):
        standalone_build.build_command(root, root / 'dist', tmp_path / 'work')


def test_build_rejects_missing_entry_or_schema(tmp_path):
    with pytest.raises(ValueError, match='source'):
        standalone_build.build_command(tmp_path, tmp_path / 'dist', tmp_path / 'work')


def test_windows_onedir_spec_builds_both_entries_into_one_shared_runtime(tmp_path):
    root = Path(__file__).resolve().parent
    spec, command = standalone_build.windows_build(root, tmp_path / 'dist', tmp_path / 'work')
    text = spec.read_text()
    compile(text, str(spec), 'exec')
    assert "name='agentmesh', console=True" in text and "name='agentmeshw', console=False" in text
    assert text.count('exclude_binaries=True') == 2 and "COLLECT(cli_exe" in text and 'launcher_exe' in text
    assert repr(str(root / 'agentmesh_launcher.py')) in text and repr(str(root / 'schema.sql')) in text
    for module in ('windows_install', 'windows_task', 'worker_lifecycle', 'sync_worker'):
        assert repr(module) in text
    assert '--onefile' not in command and command[-1] == str(spec)
    with pytest.raises(ValueError, match='outside source'):
        standalone_build.windows_build(root, root / 'dist', tmp_path / 'work')


def test_flatten_keeps_dist_paths_and_refuses_overwrite(tmp_path):
    collected = tmp_path / 'collect' / 'agentmesh'
    (collected / '_internal').mkdir(parents=True)
    (collected / 'agentmesh.exe').write_bytes(b'cli')
    (collected / '_internal' / 'python311.dll').write_bytes(b'runtime')
    standalone_build.flatten(collected, tmp_path / 'dist')
    assert (tmp_path / 'dist' / 'agentmesh.exe').read_bytes() == b'cli'
    assert (tmp_path / 'dist' / '_internal' / 'python311.dll').is_file() and not collected.exists()
    (collected / 'x').mkdir(parents=True)
    (tmp_path / 'dist' / 'x').mkdir()
    with pytest.raises(ValueError, match='already contains'):
        standalone_build.flatten(collected, tmp_path / 'dist')


def test_windows_upgrade_helper_plans_before_replace_and_keeps_legacy_explicit(tmp_path):
    standalone_build.windows_launcher(tmp_path)
    content = (tmp_path / 'Upgrade-AgentMesh.cmd').read_bytes()
    assert b'\n' not in content.replace(b'\r\n', b'')
    text = content.decode('utf-8')
    assert text.count('"%~dp0agentmesh.exe" windows-upgrade --runtime ') == 1 and '--dry-run' not in text
    assert text.index('if errorlevel 2') < text.index('if errorlevel 1 goto finish')
    assert '--replace --legacy-drained' in text and 'windows-autostart' not in text and 'setup-new' not in text


def test_launcher_refuses_anything_but_worker_start(monkeypatch):
    import agentmesh_launcher
    calls = []
    monkeypatch.setattr(agentmesh_launcher.subprocess, 'run', lambda *a, **k: calls.append((a, k)))
    assert agentmesh_launcher.main([]) == 2
    assert agentmesh_launcher.main(['worker-run', '--runtime', 'x']) == 2
    assert calls == []


def test_launcher_runs_adjacent_cli_hidden_with_fresh_extraction(monkeypatch, tmp_path):
    import agentmesh_launcher
    seen = {}
    class Done:
        returncode = 7
    def run(command, **kwargs):
        seen.update(command=command, **kwargs)
        return Done()
    monkeypatch.setattr(agentmesh_launcher.subprocess, 'run', run)
    monkeypatch.setattr(agentmesh_launcher.sys, 'executable', str(tmp_path / 'agentmeshw.exe'))
    assert agentmesh_launcher.main(['worker-start', '--runtime', 'r']) == 7
    assert seen['command'] == [str(tmp_path / 'agentmesh.exe'), 'worker-start', '--runtime', 'r']
    assert seen['env']['PYINSTALLER_RESET_ENVIRONMENT'] == '1'
    assert seen['stdin'] is seen['stdout'] is seen['stderr'] is agentmesh_launcher.subprocess.DEVNULL


def test_windows_autostart_helper_stops_cooperatively_before_enable(tmp_path):
    standalone_build.windows_launcher(tmp_path)
    text = (tmp_path / 'Enable-AgentMesh-Autostart.cmd').read_bytes().decode('utf-8')
    assert text.index('worker-stop') < text.index('windows-autostart')
    assert text.count('windows-autostart') == 1 and '--dry-run' not in text and '--enable --legacy-drained' in text
    assert 'choice /m' in text and 'Do not close this window' in text
    assert 'programs\\' not in text  # no version-pinned installed path
