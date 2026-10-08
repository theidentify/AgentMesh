"""Regression reproductions for the independent setup review findings."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

import pytest
import signed_packets as signed
import security_wizard as wizard
from test_signed_packets import secure_peers
from test_security_wizard import options, paired


def command(opts):
    return [sys.executable, wizard.__file__, '--database', str(opts['database']),
            '--exchange', str(opts['exchange']), '--security-dir', str(opts['security_dir']),
            '--state', str(opts['state_path']), 'resume']


def remove_peer(sa, peer):
    trust = signed.read_trust(sa.directory)
    del trust['peers'][peer['key_id']]
    signed.write_local(sa.directory / 'trust.json', trust)
    return (sa.directory / 'trust.json').read_bytes()


def scope_flags(peer):
    return ['--confirm-fingerprint', peer['key_id'], '--expected-group', peer['group'],
            '--expected-node', peer['node'], '--expected-sender', peer['sender']]


@pytest.mark.parametrize('mode', ['api', 'cli', 'interactive'])
def test_genuine_windows_key_with_forged_linux_sender_rejects_without_trust_write(secure_peers, mode):
    _, _, _, sa, sb = secure_peers
    opts = options(secure_peers, 'mac')
    before = remove_peer(sa, sb.public)
    proposal = Path(opts['database']).with_name('forged-proposal.json')
    forged = {**sb.public, 'node': 'linux', 'sender': str(uuid.uuid4())}
    signed.write_local(proposal, forged)
    if mode == 'api':
        with pytest.raises(ValueError, match='approval mismatch'):
            wizard.resume(**opts, peer_public=proposal, confirm_fingerprint=sb.public['key_id'],
                          expected_group=sb.public['group'], expected_node='windows', expected_sender=sb.public['sender'])
    elif mode == 'cli':
        result = subprocess.run(command(opts) + ['--peer-public', str(proposal)] + scope_flags(sb.public), capture_output=True, text=True)
        assert result.returncode == 1, result.stderr
    else:
        inputs = '\n'.join(['', str(proposal), sb.public['key_id'], sb.public['group'], 'windows', sb.public['sender'], ''])
        result = subprocess.run(command(opts) + ['--interactive'], input=inputs, capture_output=True, text=True)
        assert result.returncode == 1, result.stderr
        assert 'Do NOT copy proposal values' in result.stderr
    assert (sa.directory / 'trust.json').read_bytes() == before
    assert sb.public['key_id'] not in signed.read_trust(sa.directory)['peers']
    assert wizard.load_state(opts['state_path'])['pairing'] == 'pending'


@pytest.mark.parametrize('missing', ['all', 'group', 'node', 'sender'])
def test_cli_fingerprint_alone_or_incomplete_scope_cannot_approve(secure_peers, missing):
    _, _, _, sa, sb = secure_peers
    opts = options(secure_peers, 'mac')
    before = remove_peer(sa, sb.public)
    proposal = Path(opts['database']).with_name('proposal.json'); signed.write_local(proposal, sb.public)
    flags = scope_flags(sb.public)
    if missing == 'all': flags = flags[:2]
    else:
        index = flags.index('--expected-' + missing); del flags[index:index+2]
    result = subprocess.run(command(opts) + ['--peer-public', str(proposal)] + flags, capture_output=True, text=True)
    assert result.returncode == 1, result.stderr
    assert (sa.directory / 'trust.json').read_bytes() == before


def test_interactive_genuine_scope_requires_independent_values_and_accepts(secure_peers):
    _, _, _, sa, sb = secure_peers
    opts = options(secure_peers, 'mac'); remove_peer(sa, sb.public)
    proposal = Path(opts['database']).with_name('proposal.json'); signed.write_local(proposal, sb.public)
    inputs = '\n'.join(['', str(proposal), sb.public['key_id'], sb.public['group'], 'windows', sb.public['sender'], '', ''])
    result = subprocess.run(command(opts) + ['--interactive'], input=inputs, capture_output=True, text=True)
    assert result.returncode == 2, result.stderr
    assert json.loads(result.stdout)['pairing'] == 'approved'
    trust = signed.read_trust(sa.directory)['peers'][sb.public['key_id']]
    assert (trust['node'], trust['sender']) == ('windows', sb.public['sender'])


def receipt_path(first, node='windows'):
    probe = wizard.load_state(first['state_path'])['probe']
    return first['exchange'] / 'pairing' / 'smoke' / probe['uuid'] / 'receipts' / (node + '.json')


@pytest.mark.parametrize('field', ['digest-only', 'signature', 'key_id', 'node', 'sender', 'group',
                                   'packet_uuid', 'origin_sender', 'origin_node', 'result', 'committed_at'])
def test_existing_receipt_is_fully_authenticated_and_can_only_recover_explicitly(secure_peers, field):
    first, second = paired(secure_peers)
    _, _, _, sa, sb = secure_peers
    wizard.resume(**first, send_probe=True)
    assert wizard.receive_probes(sb, first['exchange']) == 1
    path = receipt_path(first)
    good = signed.parse(path.read_bytes())
    forged = {'packet_digest': good['packet_digest']} if field == 'digest-only' else {**good, field: '../../wrong'}
    raw = signed.wire(forged); path.write_bytes(raw)
    with pytest.raises(ValueError, match='explicit quarantine'):
        wizard.receive_probes(sb, first['exchange'])
    assert path.read_bytes() == raw
    assert not (sb.directory / 'receipt-quarantine').exists()
    with pytest.raises(ValueError): wizard.resume(**first)
    assert wizard.receive_probes(sb, first['exchange'], confirm_quarantine_receipts=True) == 1
    quarantine = list((sb.directory / 'receipt-quarantine').glob('*.json'))
    assert len(quarantine) == 1
    preserved = signed.parse(signed.read_local(quarantine[0], private=True))
    assert bytes.fromhex(preserved['receipt_bytes_hex']) == raw
    assert preserved['packet_uuid'] == good['packet_uuid']
    assert signed.canonical_uuid(quarantine[0].stem)
    recovered = path.read_bytes(); evidence = quarantine[0].read_bytes()
    assert wizard.resume(**first)['roundtrip'] == 'verified'
    assert wizard.receive_probes(sb, first['exchange'], confirm_quarantine_receipts=True) == 1
    assert path.read_bytes() == recovered and quarantine[0].read_bytes() == evidence
    assert len(list((sb.directory / 'receipt-quarantine').iterdir())) == 1
    with wizard.sync.connect(sb.directory / 'probes' / good['packet_uuid'] / 'receiver.db') as c:
        assert c.execute('SELECT count(*) FROM _sync_receipts').fetchone()[0] == 1
        assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_real_cli_digest_collision_blocks_then_explicit_quarantine_recovers(secure_peers):
    first, second = paired(secure_peers)
    wizard.resume(**first, send_probe=True)
    probe = wizard.load_state(first['state_path'])['probe']; path = receipt_path(first)
    path.parent.mkdir(); raw = signed.wire({'packet_digest': probe['digest']}); path.write_bytes(raw)
    blocked = subprocess.run(command(second) + ['--accept-probes'], capture_output=True, text=True)
    assert blocked.returncode == 1 and path.read_bytes() == raw
    assert wizard.load_state(second['state_path'])['roundtrip'] is None
    recovered = subprocess.run(command(second) + ['--accept-probes', '--confirm-quarantine-receipts'], capture_output=True, text=True)
    assert recovered.returncode == 2, recovered.stderr
    assert wizard.resume(**first)['roundtrip'] == 'verified'
    quarantine = list((Path(second['security_dir']) / 'receipt-quarantine').glob('*.json'))
    assert len(quarantine) == 1
    assert bytes.fromhex(signed.parse(quarantine[0].read_bytes())['receipt_bytes_hex']) == raw


@pytest.mark.parametrize('confirmation', ['', 'QUARANTINE'])
def test_interactive_collision_recovery_never_defaults_to_approval(secure_peers, confirmation):
    first, second = paired(secure_peers)
    wizard.resume(**first, send_probe=True)
    probe = wizard.load_state(first['state_path'])['probe']; path = receipt_path(first)
    path.parent.mkdir(); raw = signed.wire({'packet_digest': probe['digest']}); path.write_bytes(raw)
    inputs = '\n'.join(['', '', 'PROBE', confirmation, ''])
    result = subprocess.run(command(second) + ['--interactive'], input=inputs, capture_output=True, text=True)
    assert 'Type QUARANTINE' in result.stderr
    if confirmation:
        assert result.returncode == 2, result.stderr
        assert wizard.resume(**first)['roundtrip'] == 'verified'
        assert len(list((second['security_dir'] / 'receipt-quarantine').glob('*.json'))) == 1
    else:
        assert result.returncode == 1
        assert path.read_bytes() == raw
        assert not (second['security_dir'] / 'receipt-quarantine').exists()


@pytest.mark.parametrize('failure', [SystemExit, KeyboardInterrupt, RuntimeError])
def test_interrupted_backup_has_no_final_looking_file_or_wizard_state(secure_peers, monkeypatch, failure):
    opts = options(secure_peers, 'mac')
    real_connect = sqlite3.connect
    handles = []
    class FaultConnection(sqlite3.Connection):
        closed = False
        def backup(self, target, **kwargs):
            target.execute('CREATE TABLE partial(x)'); target.commit()
            raise failure('injected interruption')
        def close(self): self.closed = True; return super().close()
    def connect(*args, **kwargs):
        c = real_connect(*args, **kwargs, factory=FaultConnection); handles.append(c); return c
    monkeypatch.setattr(wizard.sqlite3, 'connect', connect)
    with pytest.raises(failure): wizard.resume(**opts)
    root = opts['state_path'].parent / 'security-backups'
    assert not list(root.iterdir()) and not opts['state_path'].exists()
    assert handles and all(c.closed for c in handles)


def test_backup_no_clobber_after_race_and_closed_handles(secure_peers, monkeypatch):
    opts = options(secure_peers, 'mac')
    destination = opts['state_path'].parent / 'recovery.db'
    real_link = wizard.os.link; real_connect = sqlite3.connect; handles = []
    class TrackingConnection(sqlite3.Connection):
        closed = False
        def close(self): self.closed = True; return super().close()
    def connect(*args, **kwargs):
        c = real_connect(*args, **kwargs, factory=TrackingConnection); handles.append(c); return c
    def link(src, dst):
        assert len(handles) == 2 and all(c.closed for c in handles)
        destination.write_bytes(b'pre-existing recovery evidence')
        return real_link(src, dst)
    monkeypatch.setattr(wizard.sqlite3, 'connect', connect)
    monkeypatch.setattr(wizard.os, 'link', link)
    with pytest.raises(FileExistsError): wizard.backup(opts['database'], destination)
    assert destination.read_bytes() == b'pre-existing recovery evidence'
    assert not list(destination.parent.glob('.recovery.db.*.tmp'))
    with pytest.raises(ValueError, match='already exists'): wizard.backup(opts['database'], destination)
    assert destination.read_bytes() == b'pre-existing recovery evidence'


def test_successful_backup_is_complete_before_state_publication(secure_peers, monkeypatch):
    opts = options(secure_peers, 'mac'); real_save = wizard.save_state
    def save(path, state):
        with closing(sqlite3.connect(state['backup'])) as c:
            assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert c.execute('SELECT content FROM memory_items WHERE id=1').fetchone()[0] == 'baseline'
        assert not list(Path(state['backup']).parent.glob('*.tmp'))
        real_save(path, state)
    monkeypatch.setattr(wizard, 'save_state', save)
    wizard.resume(**opts)
    assert wizard.load_state(opts['state_path'])['backup']
