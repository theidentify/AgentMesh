"""Native frozen-worker lifetime proof; only freshly created disposable state.

AGENTMESH_DIST and RUNNER_TEMP select the binary and fixture parent.
On Windows the starter owns a new console, which exits before observation.
Failed fixtures are retained for diagnosis, never deleted under a live worker.
"""
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import time

import signed_packets
import worker_lifecycle


def main():
    started_at = time.monotonic()
    parent = Path(os.environ.get('RUNNER_TEMP') or tempfile.gettempdir()).resolve()
    binary = (Path(os.environ.get('AGENTMESH_DIST', str(parent / 'agentmesh-dist')))
              / ('agentmesh.exe' if os.name == 'nt' else 'agentmesh')).resolve()
    assert binary.is_file(), f'missing binary: {binary}'
    root = Path(tempfile.mkdtemp(prefix='agentmesh-worker-lifetime-', dir=parent)).resolve()
    extraction = root / 'extraction'
    extraction.mkdir(mode=0o700)
    local = root / 'app'
    exchange = root / 'exchange'
    (exchange / '.stfolder').mkdir(parents=True)
    runtime = local / 'data' / 'runtime.json'
    db = local / 'data' / 'memory.db'
    env = dict(os.environ)
    for name in ('TMPDIR', 'TMP', 'TEMP'):
        for key in list(env):
            if key.upper() == name:
                del env[key]
        env[name] = str(extraction)
    node = 'windows' if os.name == 'nt' else 'mac'

    def invoke(*args, inputs=None, starter=False):
        options = {'creationflags': subprocess.CREATE_NEW_CONSOLE} if os.name == 'nt' and starter else {}
        result = subprocess.run([str(binary), *map(str, args)], cwd=root,
                                env=env, input=inputs, capture_output=True,
                                text=True, timeout=600, **options)
        assert result.returncode == 0, (args, result.returncode, result.stderr)
        assert 'Failed to remove temporary directory' not in result.stderr, result.stderr
        return json.loads(result.stdout)

    try:
        setup = invoke('setup-new', '--local-dir', local, '--exchange', exchange,
                       '--node', node, inputs='NEW\n')
        assert setup['status'] == 'created'
        identity = local / 'identity'
        signed_packets.init_identity(identity, setup['group'], node)
        keys_before = {p.name: p.read_bytes() for p in identity.iterdir()}
        bound = {p: p.read_bytes() for p in (runtime, local / 'data' / 'workflow.json')}
        with closing(sqlite3.connect(db)) as connection:
            scope_before = connection.execute('SELECT node,group_id FROM _sync_config').fetchone()
        state = invoke('worker-start', '--runtime', runtime, '--legacy-drained',
                       '--interval', '0.25', '--timeout', '300', starter=True)
        # subprocess.run has waited for the actual onefile starter to exit.
        assert state['state'] == 'running' and state['cycles'] >= 1 and state['last_error'] is None
        assert any((p / 'base_library.zip').is_file() for p in extraction.glob('_MEI*')), \
            'starter removed the sole extraction directory while the worker was running'
        if os.name == 'nt':
            assert state['console_attached'] is False, state
        bundle = Path(state['bundle_dir']).resolve()
        assert bundle.is_relative_to(extraction), state
        assert (bundle / 'base_library.zip').is_file(), 'worker extraction was removed with starter'
        nonce, pid, initial_cycles = state['nonce'], state['pid'], state['cycles']
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            current = invoke('worker-status', '--runtime', runtime)
            assert (current['nonce'], current['pid']) == (nonce, pid), current
            assert current['state'] == 'running' and current['last_error'] is None, current
            assert Path(current['bundle_dir']).resolve() == bundle
            assert (bundle / 'base_library.zip').is_file()
            if current['cycles'] >= initial_cycles + 2:
                break
            time.sleep(0.25)
        else:
            raise AssertionError('worker did not complete two additional independent cycles')
        cycles = current['cycles']
        diagnostic = invoke('diagnose', '--runtime', runtime, '--json')
        assert diagnostic['status'] == 'ok' and diagnostic['read_only'] is True, diagnostic
        assert diagnostic['program']['mode'] == 'standalone', diagnostic
        assert diagnostic['worker']['state'] == 'running', diagnostic
        assert (diagnostic['worker']['nonce'], diagnostic['worker']['pid']) == (nonce, pid)
        assert diagnostic['worker']['last_error'] is None
        assert str(runtime) not in json.dumps(diagnostic)
        assert all(p.read_bytes() == data for p, data in bound.items())
    finally:
        # Nonce-cooperative stop only; no PID signalling, deletion or forced kill.
        if runtime.exists():
            stopped = invoke('worker-stop', '--runtime', runtime, '--timeout', '300')
            assert stopped['state'] == 'stopped', stopped
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline and (bundle.exists() or worker_lifecycle.alive(pid)):
        time.sleep(0.1)
    assert not worker_lifecycle.alive(pid), 'drained worker Python process did not exit'
    assert not bundle.exists(), 'worker onefile extraction did not clean up'
    assert not list(extraction.glob('_MEI*')), 'starter/worker extraction leaked'
    assert all(p.read_bytes() == data for p, data in bound.items())
    assert {p.name: p.read_bytes() for p in identity.iterdir()} == keys_before
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute('SELECT node,group_id FROM _sync_config').fetchone() == scope_before
        assert connection.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name='_sync_security'").fetchone()
    status = json.loads((exchange / 'status' / (node + '.json')).read_text())
    assert status['security']['policy'] == 'legacy'
    report = {'platform': os.name, 'starter_exited': True, 'cycles_after_starter_exit': cycles - initial_cycles,
              'independent_bundle_cleaned': True, 'nonce_stop_drained': True,
              'runtime_workflow_keys_scope_preserved': True, 'frozen_diagnose_passed': True, 'seconds': round(time.monotonic() - started_at, 3)}
    if os.name == 'nt':
        report['worker_console_attached'] = False
    shutil.rmtree(root)
    print(json.dumps(report))


if __name__ == '__main__':
    main()
