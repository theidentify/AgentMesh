"""Frozen Windows existing-install smoke: isolated disabled task, no worker start."""
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import uuid


@contextmanager
def retained_fixture(parent):
    root = Path(tempfile.mkdtemp(prefix='agentmesh-install-fixture-', dir=parent))
    try:
        yield root
    except BaseException:
        print('Failed disposable fixture retained: ' + str(root))
        raise
    else:
        shutil.rmtree(root)  # Exclusively created disposable harness root only.


def main():
    if os.name != 'nt':
        print('SKIP: frozen Windows installation requires native Windows')
        return 0
    fixture_root = os.environ.get('AGENTMESH_NATIVE_TASK_FIXTURE_ROOT')
    if not fixture_root:
        raise ValueError('explicit disposable native fixture root required')
    fixture_root = Path(fixture_root).resolve(strict=True)
    executable = Path(os.environ['RUNNER_TEMP']) / 'agentmesh-dist/agentmesh.exe'
    if not executable.is_file():
        raise ValueError('native frozen executable required')
    import memory_sync
    import sqlite_memory
    from signed_packets import init_identity, write_local
    from windows_install import DEVELOPMENT, protection
    from windows_task import TaskAdapter
    # Python is used to create the fixture, not by the frozen program under test.
    with retained_fixture(fixture_root) as root:
        app = root / 'existing'
        protection(app); protection(app / 'data')
        db = app / 'data/windows.db'
        sqlite_memory.init_database(db)
        group = str(uuid.uuid4())
        memory_sync.initialize(db, 'windows', group)
        with closing(sqlite3.connect(db)) as c:
            c.execute('PRAGMA journal_mode=DELETE')
        init_identity(app / 'identity', group, 'windows')
        write_local(app / 'data/workflow.json', {'ingest': False, 'summarize': False})
        exchange = root / 'exchange'
        (exchange / '.stfolder').mkdir(parents=True)
        runtime = app / 'data/runtime.json'
        write_local(runtime, dict(node='windows', app_root=str(app), database=str(db), exchange=str(exchange),
                                 security_dir=str(app / 'identity'), workflow_config=str(app / 'data/workflow.json')))
        preserved = {p: (p.stat().st_dev, p.stat().st_ino, p.read_bytes()) for p in app.rglob('*') if p.is_file()}
        source_sha = os.environ['GITHUB_SHA']
        def bundle(version):
            dest = root / version
            dest.mkdir()
            binary = dest / 'agentmesh.exe'
            shutil.copyfile(executable, binary)
            metadata = dict(version=version, source_sha=source_sha, system='windows', architecture='amd64',
                            checksums={'agentmesh.exe': hashlib.sha256(binary.read_bytes()).hexdigest()},
                            distribution=DEVELOPMENT, verification='disposable fixture of the current CI executable',
                            scope='program files only; no database, keys, runtime, snapshots or deployment secrets')
            (dest / 'BUILD.json').write_text(json.dumps(metadata))
            return binary
        binary = bundle('0.2.0-rc.5')
        name = 'AgentMesh-Fixture-' + str(uuid.uuid4())
        programs = root / 'programs'
        adapter = TaskAdapter()
        assert adapter.read(name)['task'] is None
        env = dict(os.environ)
        env['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
        common = ['--runtime', str(runtime), '--program-root', str(programs), '--task-name', name]
        def call(action, extra=(), answer='', expected=0):
            result = subprocess.run([str(binary), 'windows-' + action, *common, *extra], input=answer,
                                    capture_output=True, text=True, env=env, timeout=300)
            if result.returncode != expected:
                raise AssertionError('frozen fixture ' + action + ' failed: ' + result.stderr)
            return json.loads(result.stdout) if result.stdout.strip() else None
        try:
            assert call('install', ['--dry-run'])['status'] == 'planned'
            assert call('install', answer='NO\n', expected=2)['status'] == 'pending'
            call('install', expected=1)  # EOF
            assert not programs.exists()
            assert all((p.stat().st_dev, p.stat().st_ino, p.read_bytes()) == value for p, value in preserved.items())
            assert call('install', answer='INSTALL\n')['status'] == 'installed'
            # Disabled registration exercises actual COM readback without login
            # activation or a real worker. Task Running is never worker health.
            assert call('autostart', ['--disable'], 'DISABLE\n')['status'] == 'disabled'
            task = adapter.read(name)['task']
            assert task is not None and not task['binding']['enabled']
            next_binary = bundle('0.2.0-rc.6')
            assert call('upgrade', ['--binary', str(next_binary), '--legacy-drained'], 'UPGRADE\nBIND\n')['status'] == 'selected'
            assert call('rollback', ['--legacy-drained'], 'ROLLBACK\n')['status'] == 'selected'
            assert call('uninstall', ['--legacy-drained'], 'UNINSTALL\n')['status'] == 'uninstalled'
            assert adapter.read(name)['task'] is None
            assert all((p.stat().st_dev, p.stat().st_ino, p.read_bytes()) == value for p, value in preserved.items())
            with closing(sqlite3.connect(db)) as c:
                assert c.execute('PRAGMA integrity_check').fetchone() == ('ok',)
                assert c.execute('SELECT node,group_id FROM _sync_config').fetchone() == ('windows', group)
                assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='_sync_security'").fetchone()
        finally:
            task = adapter.read(name)['task']
            if task is not None:
                state_file = programs / 'installed.json'
                state = json.loads(state_file.read_bytes()) if state_file.exists() else None
                if state is None or task['binding']['marker'] != state['marker']:
                    raise RuntimeError('uncertain fixture task ownership; cleanup refused')
                adapter.remove(name, task['sid'], task['xml'])
                assert adapter.read(name)['task'] is None
    print('Frozen Windows installation, disabled task readback/removal, rollback, preservation and cleanup passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
