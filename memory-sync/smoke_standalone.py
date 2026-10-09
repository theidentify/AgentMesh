"""Smoke-test a locally built standalone CLI on a disposable installation.

Run after standalone_build.py on each native OS. Never use a production DB.
"""
import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import sys
import uuid

import memory_sync
import sqlite_memory
import signed_packets
import security_wizard


def main():
    dist = Path(os.environ.get('AGENTMESH_DIST', str(Path(os.environ['RUNNER_TEMP']) / 'agentmesh-dist')))
    binary = dist / ('agentmesh.exe' if os.name == 'nt' else 'agentmesh')
    assert binary.is_file(), f'missing standalone binary: {binary}'
    with tempfile.TemporaryDirectory(prefix='agentmesh-native-smoke-', dir=os.environ['RUNNER_TEMP']) as scratch:
        root = Path(scratch)
        db = root / 'data' / 'mac.db'
        db.parent.mkdir()
        sqlite_memory.init_database(db)
        memory_sync.initialize(db, 'mac', '00000000-0000-4000-8000-000000000001')
        exchange = root / 'exchange'
        (exchange / '.stfolder').mkdir(parents=True)
        runtime = db.parent / 'runtime.json'
        identity = root / 'custom-identity'
        state = root / 'custom-state' / 'wizard.json'
        runtime.write_text(json.dumps({'database': str(db), 'exchange': str(exchange), 'node': 'mac',
                                      'security_dir': str(identity), 'security_state': str(state)}))
        source_before = db.read_bytes(), runtime.read_bytes()
        env = dict(os.environ)
        # os.environ is case-insensitive on Windows; a copied dict is not.
        env['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32') if os.name == 'nt' else '/usr/bin:/bin'
        def run(*args, inputs=None, expected=0):
            command = [str(binary), *map(str, args)]
            result = subprocess.run(command, cwd=root, env=env, input=inputs,
                                    text=True, capture_output=True, timeout=300 if os.name == 'nt' else 90)
            assert result.returncode == expected, (command, result.returncode, result.stderr)
            return result.stdout
        assert 'setup-new' in run('--help')
        # First-run path is frozen-binary-only, separate from two-peer fixtures.
        new_root = root / 'new-install'
        new_exchange = root / 'new-exchange'
        (new_exchange / '.stfolder').mkdir(parents=True)
        node = 'windows' if os.name == 'nt' else 'mac' if sys.platform == 'darwin' else 'linux'
        setup = ('setup-new', '--local-dir', new_root, '--exchange', new_exchange, '--node', node)
        assert json.loads(run(*setup, inputs='\n', expected=2))['status'] == 'pending'
        assert not new_root.exists()
        assert not run(*setup, inputs='', expected=1)
        assert not new_root.exists()
        created_empty = json.loads(run(*setup, inputs='NEW\n'))
        assert created_empty['status'] == 'created'
        assert str(uuid.UUID(created_empty['group'])) == created_empty['group']
        new_runtime = new_root / 'data' / 'runtime.json'
        new_db = new_root / 'data' / 'memory.db'
        assert json.loads(new_runtime.read_text()) == {
            'database': str(new_db), 'exchange': str(new_exchange), 'node': node,
            'security_dir': str(new_root / 'identity'),
            'security_state': str(new_root / 'data' / 'security-wizard.json')}
        assert json.loads((new_root / 'data' / 'workflow.json').read_text()) == {'ingest': False, 'summarize': False}
        assert not (new_root / '.setup-pending.json').exists()
        for directory in (new_root, new_root / 'data'):
            signed_packets.private_directory(directory)
        for file in (new_db, new_runtime, new_root / 'data' / 'workflow.json'):
            signed_packets.read_local(file, private=True)
        with closing(sqlite3.connect(new_db)) as c:
            assert c.execute('SELECT node,group_id FROM _sync_config').fetchone() == (node, created_empty['group'])
            for table in memory_sync.TABLES + ('_sync_history', '_sync_shadow', '_sync_outbox', '_sync_receipts'):
                assert c.execute('SELECT count(*) FROM ' + table).fetchone()[0] == 0
            assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='_sync_security'").fetchone()
        new_bytes = new_db.read_bytes(), new_runtime.read_bytes()
        status_empty = json.loads(run('--database', new_db, 'status'))
        assert status_empty['sync']['node'] == node and not any(status_empty['counts'].values())
        assert json.loads(run('inspect-install', '--runtime', new_runtime))['identity'] == 'absent'
        pending_empty = json.loads(run('wizard-status', '--runtime', new_runtime))
        assert pending_empty['policy'] == 'legacy' and pending_empty['wizard_step'] == 'prerequisites'
        assert not (new_root / 'identity').exists()
        assert not (new_root / 'data' / 'security-wizard.json').exists()
        assert sorted(p.name for p in new_exchange.iterdir()) == ['.stfolder']
        assert not run(*setup, inputs='NEW\n', expected=1)
        assert (new_db.read_bytes(), new_runtime.read_bytes()) == new_bytes
        partial_marker = new_root / '.setup-pending.json'
        signed_packets.write_local(partial_marker, {'format': 'agentmesh-setup-partial-v1', 'status': 'partial'}, exclusive=True)
        for command in ('inspect-install', 'wizard-status', 'wizard-resume'):
            assert not run(command, '--runtime', new_runtime, inputs='CREATE\nForbidden\n', expected=1)
        assert (new_db.read_bytes(), new_runtime.read_bytes()) == new_bytes
        assert not (new_root / 'identity').exists()
        partial_marker.unlink()  # Remove only this smoke fixture's marker.
        print('native setup-new smoke: confirmed empty isolated group, private paths, no identity/packets/worker/activation; decline/EOF/reuse/partial refused')
        # Independent frozen lifecycle, explicitly started after first-run checks.
        signed_packets.init_identity(new_root / 'identity', created_empty['group'], node)
        keys_before = {p.name: p.read_bytes() for p in (new_root / 'identity').iterdir()}
        runtime_before = new_runtime.read_bytes()
        before_status = set(new_root.rglob('*'))
        assert json.loads(run('worker-status', '--runtime', new_runtime))['state'] == 'stopped'
        assert set(new_root.rglob('*')) == before_status
        assert not run('worker-run', '--runtime', new_runtime, '--once', expected=1)
        started = json.loads(run('worker-start', '--runtime', new_runtime, '--legacy-drained', '--interval', '60', '--timeout', '300'))
        try:
            assert started['state'] == 'running' and started['cycles'] >= 1
            assert not run('worker-start', '--runtime', new_runtime, '--legacy-drained', expected=1)
            assert not run('worker-run', '--runtime', new_runtime, '--legacy-drained', '--once', expected=1)
            assert json.loads(run('worker-stop', '--runtime', new_runtime, '--timeout', '300'))['state'] == 'stopped'
            restarted = json.loads(run('worker-start', '--runtime', new_runtime, '--legacy-drained', '--timeout', '300'))
            assert restarted['nonce'] != started['nonce']
        finally:
            run('worker-stop', '--runtime', new_runtime, '--timeout', '300')
        assert new_runtime.read_bytes() == runtime_before
        assert {p.name: p.read_bytes() for p in (new_root / 'identity').iterdir()} == keys_before
        with closing(sqlite3.connect(new_db)) as c:
            assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='_sync_security'").fetchone()
        assert json.loads((new_exchange / 'status' / (node + '.json')).read_text())['security']['policy'] == 'legacy'
        print('native frozen lifecycle: explicit start, healthy cycle, exclusivity, cooperative stop/restart; pending identity stayed legacy')
        # Existing-install adoption is distinct from creating or joining a group.
        adopt_root = root / 'existing-local'
        adopt_data = adopt_root / 'data'
        adopt_data.mkdir(parents=True)
        adopt_db = adopt_data / 'existing.db'
        sqlite_memory.init_database(adopt_db)
        memory_sync.initialize(adopt_db, node, '00000000-0000-4000-8000-000000000003')
        adopt_exchange = root / 'existing-exchange'
        (adopt_exchange / '.stfolder').mkdir(parents=True)
        adopt_identity = root / 'existing-custom-identity'
        adopt_state = root / 'existing-custom-state' / 'wizard.json'
        signed_packets.init_identity(adopt_identity, '00000000-0000-4000-8000-000000000003', node)
        security_wizard.resume(adopt_db, adopt_exchange, adopt_identity, adopt_state)
        adopt_workflow = adopt_data / 'custom-workflow.json'
        signed_packets.write_local(adopt_workflow, {'ingest': False, 'summarize': False}, exclusive=True)
        adopt_runtime = adopt_data / 'runtime.json'
        adopt_args = ('adopt-install', '--runtime', adopt_runtime, '--app-root', adopt_root,
                      '--database', adopt_db, '--exchange', adopt_exchange, '--node', node,
                      '--security-dir', adopt_identity, '--security-state', adopt_state,
                      '--workflow-config', adopt_workflow)
        existing_bytes = adopt_db.read_bytes(), adopt_state.read_bytes(), adopt_workflow.read_bytes()
        existing_keys = {p.name: p.read_bytes() for p in adopt_identity.iterdir()}
        assert json.loads(run(*adopt_args, inputs='\n', expected=2))['status'] == 'pending'
        assert not adopt_runtime.exists()
        assert json.loads(run(*adopt_args, inputs='BIND\n'))['status'] == 'bound'
        assert (adopt_db.read_bytes(), adopt_state.read_bytes(), adopt_workflow.read_bytes()) == existing_bytes
        assert not run(*adopt_args, inputs='BIND\n', expected=1)
        run('worker-run', '--runtime', adopt_runtime, '--legacy-drained', '--once')
        adopted_report = json.loads((adopt_exchange / 'status' / (node + '.json')).read_text())
        assert adopted_report['security']['policy'] == 'legacy' and adopted_report['security']['pairing'] == 'pending'
        assert {p.name: p.read_bytes() for p in adopt_identity.iterdir()} == existing_keys
        assert adopt_state.read_bytes() == existing_bytes[1]
        with closing(sqlite3.connect(adopt_db)) as c:
            assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='_sync_security'").fetchone()
        print('native frozen adoption: BIND-only path binding, no overwrite, custom workflow/wizard/identity preserved; real legacy cycle')
        assert 'inspect-install' in run('--help')
        assert json.loads(run('--database', db, 'status'))['sync']['node'] == 'mac'
        assert json.loads(run('--database', db, 'recall', 'example'))['results'] == []
        assert json.loads(run('inspect-install', '--runtime', runtime))['status'] == 'ready'
        wizard = json.loads(run('wizard-status', '--runtime', runtime))
        assert wizard['policy'] == 'legacy' and wizard['wizard_step'] == 'prerequisites'
        assert (db.read_bytes(), runtime.read_bytes()) == source_before
        assert 'wizard-resume' in run('--help')
        pending = json.loads(run('wizard-resume', '--runtime', runtime, inputs='\n', expected=2))
        assert pending['policy'] == 'legacy' and not identity.exists()
        created = json.loads(run('wizard-resume', '--runtime', runtime,
                                 inputs='CREATE\nDisposable Native Fixture\n\n\n', expected=2))
        assert created['pairing'] == 'pending' and created['policy'] == 'legacy'
        public = signed_packets.Security(identity).public
        identity_before = {p.name: p.read_bytes() for p in identity.iterdir() if p.is_file()}
        state_before = security_wizard.load_state(state)
        stdout = run('wizard-resume', '--runtime', runtime, inputs='\n\n', expected=2)
        assert json.loads(stdout)['policy'] == 'legacy'
        assert signed_packets.Security(identity).public == public
        assert {p.name: p.read_bytes() for p in identity.iterdir() if p.is_file()} == identity_before
        state_after = security_wizard.load_state(state)
        assert state_before is not None and state_after is not None
        assert {k: v for k, v in state_before.items() if k != 'updated_at'} == {k: v for k, v in state_after.items() if k != 'updated_at'}
        assert public['key_id'] not in stdout and 'private_key' not in stdout and 'backup' not in stdout
        assert not (exchange / 'pairing').exists()
        assert (db.read_bytes(), runtime.read_bytes()) == source_before
        assert not run('wizard-resume', '--runtime', root / 'missing.json', inputs='', expected=1)
        assert not run('wizard-resume', '--runtime', runtime, inputs='', expected=1)
        # Prepare real two-peer receipts, then decline ACTIVATE in the binary.
        peer_db = root / 'data' / 'windows.db'
        sqlite_memory.init_database(peer_db)
        memory_sync.initialize(peer_db, 'windows', public['group'])
        peer_identity = root / 'peer-identity'
        signed_packets.init_identity(peer_identity, public['group'], 'windows')
        peer = signed_packets.Security(peer_identity).public
        local_public = root / 'local-public.json'
        peer_public = root / 'peer-public.json'
        signed_packets.write_local(local_public, public, exclusive=True)
        signed_packets.write_local(peer_public, peer, exclusive=True)
        peer_state = root / 'peer-state' / 'wizard.json'
        first = db, exchange, identity, state
        second = peer_db, exchange, peer_identity, peer_state
        security_wizard.resume(*first, peer_public=peer_public, confirm_fingerprint=peer['key_id'],
                               expected_group=peer['group'], expected_node=peer['node'], expected_sender=peer['sender'])
        security_wizard.resume(*second, peer_public=local_public, confirm_fingerprint=public['key_id'],
                               expected_group=public['group'], expected_node=public['node'], expected_sender=public['sender'])
        security_wizard.resume(*first, send_probe=True)
        security_wizard.resume(*second, send_probe=True, accept_probes=True)
        assert security_wizard.resume(*first, accept_probes=True)['roundtrip'] == 'verified'
        ready_pending = json.loads(run('wizard-resume', '--runtime', runtime,
                                       inputs='\n\n\nBOTH\nDRAINED\n\n', expected=2))
        assert ready_pending['roundtrip'] == 'verified' and ready_pending['policy'] == 'legacy'
        assert (db.read_bytes(), runtime.read_bytes()) == source_before
        # A strict DB with missing key material must fail before any writes.
        with closing(sqlite3.connect(db)) as c, c:
            c.execute('CREATE TABLE _sync_security(sender TEXT, group_id TEXT, node TEXT)')
        (identity / 'identity.json').unlink()
        before_block = state.read_bytes()
        assert not run('wizard-resume', '--runtime', runtime, inputs='CREATE\nReplacement\n', expected=1)
        assert state.read_bytes() == before_block
        print('native standalone smoke: help/status/recall/inspect-install/wizard-status/wizard-resume passed; activation pending')


if __name__ == '__main__':
    main()
