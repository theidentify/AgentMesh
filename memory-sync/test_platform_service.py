import importlib
import plistlib
from pathlib import Path


def module():
    assert Path(__file__).with_name('platform_service.py').exists(), 'service renderer missing'
    return importlib.import_module('platform_service')


def test_launchagent_exact_arguments():
    config = plistlib.loads(module().render_launchagent('/opt/Python 3/bin/python', '/data/Agent Mesh', '/data/local/memory.db', '/data/exchange & peers'))
    assert config['ProgramArguments'] == ['/opt/Python 3/bin/python', '/data/Agent Mesh/sync_worker.py', '/data/local/memory.db', '/data/exchange & peers', '--interval', '60']
    assert config['RunAtLoad'] is True and config['KeepAlive'] is True
    assert config['StandardOutPath'] == '/data/local/agentmesh.stdout.log'
    assert config['StandardErrorPath'] == '/data/local/agentmesh.stderr.log'
    assert config['Umask'] == 0o077
    assert 'EnvironmentVariables' not in config


def test_systemd_exact_arguments():
    import re
    import json
    python = '/opt/Python 3/bin/python'
    app = '/data/mesh "quotes" \\ path'
    database = '/data/local/memory.db'
    exchange = '/data/100%/$HOME; $(touch nope)'
    unit = module().render_systemd(python, app, database, exchange)
    line = next(x for x in unit.splitlines() if x.startswith('ExecStart='))
    assert line.startswith('ExecStart=:')  # Disable systemd environment expansion.
    tokens = re.findall(r'"(?:\\.|[^"\\])*"', line[len('ExecStart=:'):])
    assert ' '.join(tokens) == line[len('ExecStart=:'):]
    assert [json.loads(x).replace('%%', '%') for x in tokens] == [python, app + '/sync_worker.py', database, exchange, '--interval', '60']
    assert 'WantedBy=default.target' in unit
    assert 'UMask=0077' in unit
    assert '/bin/sh' not in unit


def test_rejects_path_controls_and_relative_paths():
    import pytest
    for renderer in [module().render_launchagent, module().render_systemd]:
        for index in range(4):
            for bad in ['relative/path', '/bad\npath', '/bad\x00path', '/bad\tpath', '/bad\x7fpath', '/bad\x85path']:
                args = ['/python', '/app', '/local/db', '/exchange']
                args[index] = bad
                with pytest.raises(ValueError):
                    renderer(*args)
        for interval in [0, -1, float('nan'), float('inf')]:
            with pytest.raises(ValueError):
                renderer('/python', '/app', '/local/db', '/exchange', interval)


def test_cli_private_output_with_environment_mirror(tmp_path, monkeypatch, capsys):
    import os
    secret = 'postgresql://example.invalid/private?password=secret'
    monkeypatch.setenv('OMP_MEMORY_DSN', secret)
    output = tmp_path / 'agentmesh.plist'
    module().main(['generate', '--platform', 'macos', '--python', '/python', '--app-dir', '/app', '--database', '/local/db', '--exchange', '/exchange', '--output', str(output), '--postgres'])
    config = plistlib.loads(output.read_bytes())
    assert config['ProgramArguments'][-1] == '--postgres'
    assert config['EnvironmentVariables'] == {'OMP_MEMORY_DSN': secret}
    assert os.stat(output).st_mode & 0o777 == 0o600
    assert secret not in capsys.readouterr().out


def test_shell_wrappers_forward_exact_paths(tmp_path):
    import os
    import subprocess
    import sys
    import json
    for platform, node, default in [('macos', 'mac', str(tmp_path / 'Library/Application Support/AgentMesh')), ('linux', 'linux', str(tmp_path / '.local/share/AgentMesh'))]:
        wrapper = Path(__file__).with_name('setup_' + platform + '.sh')
        assert wrapper.exists(), 'platform setup wrapper missing'
        tools = tmp_path / platform
        tools.mkdir()
        (tools / 'syncthing').write_text('#!/bin/sh\nexit 0\n')
        (tools / 'syncthing').chmod(0o700)
        proxy = tools / 'python3'
        proxy.write_text('#!' + sys.executable + '\nimport sys, subprocess, json\nif sys.argv[1] == "-":\n sys.exit(subprocess.call([' + repr(sys.executable) + '] + sys.argv[1:]))\nprint(json.dumps(sys.argv[1:]))\n')
        proxy.chmod(0o700)
        env = dict(os.environ, PATH=str(tools) + ':' + os.environ['PATH'], HOME=str(tmp_path))
        env.pop('XDG_DATA_HOME', None)
        result = subprocess.run(['sh', str(wrapper), '/exchange with spaces'], env=env, text=True, capture_output=True, check=True)
        args = json.loads(result.stdout)
        assert args[1:] == ['--node', node, '--exchange', '/exchange with spaces', '--local-dir', default]
        result = subprocess.run(['sh', str(wrapper), '/exchange', '/custom local'], env=env, text=True, capture_output=True, check=True)
        assert json.loads(result.stdout)[-1] == '/custom local'
        assert len(wrapper.read_text().splitlines()) <= 30
