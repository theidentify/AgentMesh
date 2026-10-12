"""CI-only frozen OS unlock smoke; build a separate binary bound to a disposable store.

Normal standalone builds have no runtime hook or backend-selection override.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile

import key_protection as kp
import memory_sync
import os_unlock as ou
import signed_packets as signed
import sqlite_memory
import standalone_build as build
from disposable_os_store import disposable_store


def write_hook(path, backend):
    if isinstance(backend, ou.MacKeychainFile):
        factory = 'ou.MacKeychainFile(keychain=%r)' % backend.keychain
    elif isinstance(backend, ou.WindowsCredential) and backend.prefix.startswith('agentmesh-test-'):
        factory = 'ou.WindowsCredential(prefix=%r)' % backend.prefix
    else:
        raise ValueError('disposable backend required')
    path.write_text('import os_unlock as ou\nou.current_backend = lambda: ' + factory + '\n', encoding='utf-8')
    return path


def smoke(root, backend):
    hook = write_hook(root / 'disposable_store_hook.py', backend)
    source = Path(__file__).resolve().parent
    dist, work = root / 'dist', root / 'build'
    if os.name == 'nt':
        _, command = build.windows_build(source, dist, work, runtime_hooks=[hook])
        subprocess.run(command, check=True)
        build.flatten(work / 'collect' / 'agentmesh', dist)
    else:
        subprocess.run(build.build_command(source, dist, work, runtime_hooks=[hook]), check=True)
    binary = dist / ('agentmesh.exe' if os.name == 'nt' else 'agentmesh')
    app = root / 'local'
    data = app / 'data'
    data.mkdir(parents=True, mode=0o700)
    app.chmod(0o700)
    db = data / 'memory.db'
    sqlite_memory.init_database(db)
    group = '00000000-0000-4000-8000-000000000001'
    memory_sync.initialize(db, 'mac', group)
    exchange = root / 'exchange'
    (exchange / '.stfolder').mkdir(parents=True)
    identity = app / 'identity'
    public = signed.init_identity(identity, group, 'mac')
    kp.protect_identity(identity, lambda prompt: 'disposable smoke passphrase', n=2 ** 14)
    runtime = data / 'runtime.json'
    workflow = data / 'workflow.json'
    signed.write_local(workflow, {'ingest': False, 'summarize': False})
    signed.write_local(runtime, {'app_root': str(app), 'database': str(db), 'exchange': str(exchange),
                                 'node': 'mac', 'security_dir': str(identity), 'workflow_config': str(workflow)})
    def run(*args, expected=0):
        result = subprocess.run([str(binary), *map(str, args)], cwd=root, capture_output=True,
                                text=True, timeout=180)
        if result.returncode != expected:
            raise AssertionError('frozen OS unlock command returned unexpected exit status')
        return result.stdout
    assert 'identity-os-unlock' in run('--help')
    assert 'status' in run('identity-os-unlock', '--help')
    before = {p.name: p.read_bytes() for p in identity.iterdir()}
    report = json.loads(run('identity-os-unlock', 'status', '--security-dir', identity))
    assert report['protected'] and report['os_unlock'] == 'absent'
    assert {p.name: p.read_bytes() for p in identity.iterdir()} == before
    if os.name == 'nt':
        try:
            backend.store(public['key_id'], os.urandom(32))
        except ou.OSUnlockError as exc:
            if str(exc) == ou.REFUSED:
                print('Credential Manager unavailable in this CI session (recorded); frozen unlock skipped')
                return
            raise
    try:
        ou.enable(identity, ask=lambda prompt: 'disposable smoke passphrase', backend=backend)
        assert json.loads(run('identity-os-unlock', 'status', '--security-dir', identity))['available']
        memory_sync.cycle(db, exchange, security=signed.Security(identity, unlock=lambda: 'disposable smoke passphrase'))
        run('worker-run', '--once', '--runtime', runtime)
        report = json.loads((exchange / 'status' / 'mac.json').read_text())
        assert report['security']['policy'] == 'required'
        run('identity-os-unlock', 'disable', '--security-dir', identity)
        run('worker-run', '--once', '--runtime', runtime, expected=1)
    finally:
        backend.delete(public['key_id'])
    print('Frozen OS unlock status, strict worker, and disable checks passed')


def main():
    if os.environ.get('AGENTMESH_NATIVE_KEYCHAIN_TESTS') != '1':
        raise RuntimeError('native credential tests require explicit opt-in')
    with tempfile.TemporaryDirectory(prefix='agentmesh-os-unlock-', dir=os.environ['RUNNER_TEMP']) as scratch:
        root = Path(scratch).resolve()
        with disposable_store(root) as backend:
            smoke(root, backend)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
