from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import uuid

import pytest
import memory_sync as sync
import signed_packets as signed
import security_wizard as wizard
import sqlite_memory
from test_signed_packets import secure_peers


def options(peers, node):
    a, b, ex, sa, sb = peers
    db, sec = (a, sa) if node == 'mac' else (b, sb)
    return dict(database=db, exchange=ex, security_dir=sec.directory, state_path=db.with_suffix('.wizard.json'))


def paired(peers):
    a, b, ex, sa, sb = peers
    public_a = a.with_name('public-a.json'); signed.write_local(public_a, sa.public, exclusive=True)
    public_b = b.with_name('public-b.json'); signed.write_local(public_b, sb.public, exclusive=True)
    first = options(peers, 'mac'); second = options(peers, 'windows')
    assert wizard.resume(**first, peer_public=public_b, confirm_fingerprint=sb.public['key_id'])['pairing'] == 'approved'
    assert wizard.resume(**second, peer_public=public_a, confirm_fingerprint=sa.public['key_id'])['pairing'] == 'approved'
    return first, second


def verified(peers):
    first, second = paired(peers)
    assert wizard.resume(**first, send_probe=True)['roundtrip'] == 'pending'
    assert wizard.resume(**second, send_probe=True, accept_probes=True)['roundtrip'] == 'pending'
    assert wizard.resume(**first, accept_probes=True)['roundtrip'] == 'verified'
    assert wizard.resume(**second)['roundtrip'] == 'verified'
    return first, second


def test_actual_two_peer_signed_probe_receipts_and_backup_preservation(secure_peers):
    a, b, ex, sa, sb = secure_peers
    with sync.connect(a) as c: before = sync.rows(c)
    first, second = verified(secure_peers)
    for opts in (first, second):
        state = wizard.load_state(opts['state_path'])
        with closing(sqlite3.connect(state['backup'])) as backup, sync.connect(opts['database']) as live:
            backup.row_factory = sqlite3.Row
            assert sync.rows(backup) == sync.rows(live) == before
            assert not live.execute("SELECT 1 FROM sqlite_master WHERE name='_sync_security'").fetchall()
            assert live.execute('SELECT count(*) FROM _sync_outbox').fetchone()[0] == 0
        probe = state['probe']
        receipt_path = ex / 'pairing' / 'smoke' / probe['uuid'] / 'receipts' / (state['peer']['node'] + '.json')
        ack = signed.parse(receipt_path.read_bytes())
        assert ack['packet_uuid'] == probe['uuid'] and ack['packet_digest'] == probe['digest']
        remote = sb if opts == first else sa
        with sync.connect(remote.directory / 'probes' / probe['uuid'] / 'receiver.db') as c:
            assert c.execute('SELECT checksum FROM _sync_receipts WHERE uuid=?', (probe['uuid'],)).fetchone()[0] == ack['packet_digest']
    # Repeated remote processing leaves immutable receipt and idempotent DB.
    state = wizard.load_state(first['state_path']); path = ex / 'pairing' / 'smoke' / state['probe']['uuid'] / 'receipts' / 'windows.json'
    data = path.read_bytes(); wizard.resume(**second, accept_probes=True); assert path.read_bytes() == data


@pytest.mark.parametrize('field', ['packet_uuid', 'packet_digest', 'group', 'node', 'sender', 'origin_sender', 'origin_node', 'signature', 'key_id', 'result', 'format', 'committed_at'])
def test_fake_or_wrong_application_ack_never_verifies(secure_peers, field):
    first, second = paired(secure_peers)
    wizard.resume(**first, send_probe=True); wizard.resume(**second, accept_probes=True)
    state = wizard.load_state(first['state_path'])
    ack_path = first['exchange'] / 'pairing' / 'smoke' / state['probe']['uuid'] / 'receipts' / 'windows.json'
    ack = signed.parse(ack_path.read_bytes())
    ack[field] = str(uuid.uuid4()) if field.endswith('uuid') or field in ('group', 'sender', 'origin_sender') else 'forged'
    ack_path.write_bytes(signed.wire(ack))
    with pytest.raises((ValueError, TypeError)): wizard.resume(**first)
    assert wizard.load_state(first['state_path'])['roundtrip'] is None


def test_transport_presence_status_json_and_unsigned_ack_not_proof(secure_peers):
    first, second = paired(secure_peers)
    result = wizard.resume(**first, send_probe=True)
    assert result['policy'] == 'legacy' and result['roundtrip'] == 'pending'
    ex = first['exchange']; (ex / 'status').mkdir()
    (ex / 'status' / 'windows.json').write_text(json.dumps({'format': 'agentmesh-status-v1', 'node': 'windows', 'signed': True, 'completion': 100, 'received': 999}))
    assert wizard.resume(**first)['roundtrip'] == 'pending'
    state = wizard.load_state(first['state_path']); receipt = ex / 'pairing' / 'smoke' / state['probe']['uuid'] / 'receipts' / 'windows.json'
    receipt.parent.mkdir(); receipt.write_text('{"received":true}')
    with pytest.raises(ValueError): wizard.resume(**first)


@pytest.mark.parametrize('approval', ['neither', 'only-both', 'only-boundary', 'missing-receipt'])
def test_activation_requires_both_operator_confirmations_and_current_receipt(secure_peers, approval):
    first, second = verified(secure_peers)
    if approval == 'missing-receipt':
        state = wizard.load_state(first['state_path'])
        (first['exchange'] / 'pairing' / 'smoke' / state['probe']['uuid'] / 'receipts' / 'windows.json').unlink()
    with pytest.raises(ValueError):
        wizard.resume(**first, activate=True, confirm_both_peers=approval in ('only-both', 'missing-receipt'),
                      confirm_legacy_boundary=approval in ('only-boundary', 'missing-receipt'))
    assert sync.status(first['database'])['node'] == 'mac'
    with sync.connect(first['database']) as c:
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='_sync_security'").fetchall()


def test_coordinated_activation_reuses_identity_and_preserves_application_rows(secure_peers):
    first, second = verified(secure_peers)
    before = signed.read_local(first['security_dir'] / 'identity.json')
    for opts in (first, second):
        assert wizard.resume(**opts, activate=True, confirm_both_peers=True, confirm_legacy_boundary=True)['policy'] == 'required'
        assert sync.cycle(opts['database'], opts['exchange'], security=signed.Security(opts['security_dir']))['receive']['invalid'] == 0
    assert signed.read_local(first['security_dir'] / 'identity.json') == before
    assert wizard.resume(**first)['wizard_step'] == 'active'
    with pytest.raises(ValueError): sync.cycle(first['database'], first['exchange'])


@pytest.mark.parametrize('block', ['outbox', 'diagnostics'])
def test_upgrade_pending_backlog_cannot_cross_boundary(secure_peers, block):
    first, second = verified(secure_peers)
    with sync.connect(first['database']) as c, c:
        if block == 'outbox': c.execute("UPDATE memory_items SET content='pending legacy' WHERE id=1")
        else: c.execute("INSERT INTO _sync_diagnostics VALUES('fixture','pending','fixture')")
    if block == 'outbox': sync.capture(first['database'])
    with pytest.raises(ValueError): wizard.resume(**first, activate=True, confirm_both_peers=True, confirm_legacy_boundary=True)
    with sync.connect(first['database']) as c:
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='_sync_security'").fetchall()


def test_missing_recorded_identity_and_scope_change_halt(secure_peers):
    opts = options(secure_peers, 'mac')
    wizard.resume(**opts)
    moved = opts['security_dir'].with_name('lost-identity'); opts['security_dir'].rename(moved)
    with pytest.raises(ValueError, match='missing'): wizard.resume(**opts, create_identity=True, display_name='Do not replace')
    assert not opts['security_dir'].exists()
    moved.rename(opts['security_dir'])
    opts['exchange'] = opts['exchange'].with_name('other-exchange'); opts['exchange'].mkdir(); (opts['exchange'] / '.stfolder').mkdir()
    with pytest.raises(ValueError, match='scope'): wizard.resume(**opts)


def test_proposals_remain_pending_without_independent_confirmation(secure_peers):
    a, b, ex, sa, sb = secure_peers
    opts = options(secure_peers, 'mac')
    public = a.with_name('unconfirmed.json'); signed.write_local(public, sb.public)
    trust = signed.read_trust(sa.directory); del trust['peers'][sb.public['key_id']]; signed.write_local(sa.directory / 'trust.json', trust)
    result = wizard.resume(**opts, peer_public=public, publish_proposal=True)
    assert result['pairing'] == 'pending' and result['policy'] == 'legacy'
    assert sb.public['key_id'] not in signed.read_trust(sa.directory)['peers']
    assert signed.parse(next((ex / 'pairing' / 'proposals').glob('*.json')).read_bytes()) == sa.public
    with pytest.raises(ValueError): wizard.resume(**opts, send_probe=True)


def test_cli_scripted_stdin_initial_identity_and_resumable_pending(secure_peers):
    a, b, ex, sa, sb = secure_peers
    opts = options(secure_peers, 'mac')
    # Brand-new local identity for an existing legacy DB, not replacing its old identity.
    opts['security_dir'] = sa.directory.with_name('cli-identity')
    command = [sys.executable, wizard.__file__, '--database', str(a), '--exchange', str(ex), '--security-dir', str(opts['security_dir']), '--state', str(opts['state_path'])]
    dry = subprocess.run(command + ['resume', '--dry-run'], capture_output=True, text=True)
    assert dry.returncode == 0 and not opts['state_path'].exists() and not opts['security_dir'].exists()
    initial = subprocess.run(command + ['resume', '--interactive'], input='CREATE\nOffice Mac\nPUBLISH\n\n', capture_output=True, text=True)
    assert initial.returncode == 2, initial.stderr
    assert '[1/6]' in initial.stderr and '[3/6]' in initial.stderr and 'private_key' not in initial.stdout
    result = json.loads(initial.stdout.splitlines()[-1]); assert result['pairing'] == 'pending' and result['display_name'] == 'Office Mac'
    key = signed.read_local(opts['security_dir'] / 'identity.json'); state = wizard.load_state(opts['state_path']); backup = Path(state['backup'])
    resumed = subprocess.run(command + ['resume'], capture_output=True, text=True)
    assert resumed.returncode == 2 and signed.read_local(opts['security_dir'] / 'identity.json') == key
    assert wizard.load_state(opts['state_path'])['backup'] == str(backup)
    status_before = opts['state_path'].read_bytes()
    status = subprocess.run(command + ['status'], capture_output=True, text=True)
    assert status.returncode == 0 and opts['state_path'].read_bytes() == status_before
    for private in ('private_key', 'public_key', 'security_dir', str(backup), sa.public['key_id']):
        assert private not in status.stdout


def test_prerequisites_dry_run_does_not_generate_state(secure_peers):
    opts = options(secure_peers, 'mac'); (opts['exchange'] / '.stfolder').rmdir()
    result = wizard.resume(**opts)
    assert not result['prerequisites']['exchange_ready'] and not opts['state_path'].exists()


def test_explicit_crypto_install_uses_current_python_and_is_bounded(monkeypatch):
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs)); return subprocess.CompletedProcess(args, 0, '', '')
    monkeypatch.setattr(wizard.subprocess, 'run', run)
    wizard.install_crypto()
    args, kwargs = calls[0]
    assert args[:4] == [sys.executable, '-m', 'pip', 'install']
    assert kwargs['timeout'] == 180 and 'shell' not in kwargs


def test_installer_status_is_readonly_and_signed_restart_config_preserved(secure_peers):
    # Existing installer tests cover package extraction; exercise added status path for a real layout.
    a, b, ex, sa, sb = secure_peers
    import bootstrap_windows
    local = ex.parent / 'installed'; (local / 'data').mkdir(parents=True); (local / 'app').mkdir()
    with sync.connect(a) as src, closing(sqlite3.connect(local / 'data' / 'mac.db')) as dst: src.backup(dst)
    # Current source supplies import; status does not require an archive or extract files.
    result = subprocess.run([sys.executable, bootstrap_windows.__file__, '--exchange', str(ex), '--local-dir', str(local), '--node', 'mac', '--wizard-status'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['policy'] == 'legacy'
    assert not (local / 'identity').exists() and not (local / 'data' / 'security-wizard.json').exists()


def test_revoked_peer_blocks_setup_activation(secure_peers):
    first, second = verified(secure_peers)
    _, _, _, sa, sb = secure_peers
    signed.revoke(sa.directory, sb.public['key_id'])
    result = wizard.resume(**first)
    assert result['pairing'] == 'revoked' and result['roundtrip'] == 'pending'
    with pytest.raises(ValueError): wizard.resume(**first, activate=True, confirm_both_peers=True, confirm_legacy_boundary=True)


def test_required_wizard_missing_crypto_is_pending_not_success(secure_peers):
    first, second = verified(secure_peers)
    wizard.resume(**first, activate=True, confirm_both_peers=True, confirm_legacy_boundary=True)
    code = '''import importlib.abc, sys
class Deny(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('cryptography'): raise ImportError('blocked for test')
sys.meta_path.insert(0,Deny())
import security_wizard
raise SystemExit(security_wizard.main(sys.argv[1:]))
'''
    args = [sys.executable, '-c', code, '--database', str(first['database']), '--exchange', str(first['exchange']),
            '--security-dir', str(first['security_dir']), '--state', str(first['state_path']), 'resume']
    result = subprocess.run(args, capture_output=True, text=True, cwd=Path(wizard.__file__).parent)
    assert result.returncode == 2, result.stderr
    assert json.loads(result.stdout)['prerequisites']['crypto_ready'] is False
