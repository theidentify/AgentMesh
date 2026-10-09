"""Smoke-test a locally built standalone CLI on a disposable installation.

Run after standalone_build.py on each native OS. Never use a production DB.
"""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile

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
        env['PATH'] = str(Path(env['SystemRoot']) / 'System32') if os.name == 'nt' else '/usr/bin:/bin'
        def run(*args, inputs=None, expected=0):
            command = [str(binary), *map(str, args)]
            result = subprocess.run(command, cwd=root, env=env, input=inputs,
                                    text=True, capture_output=True, timeout=90)
            assert result.returncode == expected, (command, result.returncode, result.stderr)
            return result.stdout
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
        with sqlite3.connect(db) as c:
            c.execute('CREATE TABLE _sync_security(sender TEXT, group_id TEXT, node TEXT)')
        (identity / 'identity.json').unlink()
        before_block = state.read_bytes()
        assert not run('wizard-resume', '--runtime', runtime, inputs='CREATE\nReplacement\n', expected=1)
        assert state.read_bytes() == before_block
        print('native standalone smoke: help/status/recall/inspect-install/wizard-status/wizard-resume passed; activation pending')


if __name__ == '__main__':
    main()
