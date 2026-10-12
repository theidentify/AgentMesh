"""Source reservations and device-bound, empty-baseline replay onboarding."""
import io
import json
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

import agentmesh
import memory_sync as sync
import sqlite_memory
import signed_packets


def setup(tmp_path, monkeypatch, node='mac'):
    exchange = tmp_path / 'exchange'
    (exchange / '.stfolder').mkdir(parents=True)
    root = tmp_path / 'a'
    monkeypatch.setattr('sys.stdin', io.StringIO('NEW\n'))
    assert agentmesh.main(['setup-new', '--local-dir', str(root), '--exchange', str(exchange), '--node', node]) == 0
    return root, exchange


def module():
    import install_join
    return install_join


def prepare(root, exchange, node, output):
    return module().prepare_join(root, exchange, node, output, yes=True, confirm_word='REQUEST')


def issue(root, request, output, **kwargs):
    return module().invite_peer(root / 'data/runtime.json', request, output,
                               yes=True, confirm_word='INVITE', **kwargs)


def join(root, exchange, invitation=None, **kwargs):
    return module().setup_join(root, exchange, invitation, yes=True,
                              confirm_word='RESUME' if kwargs.get('resume') else 'JOIN', **kwargs)


def put(db, content, ident=None):
    with sync.connect(db) as c, c:
        c.execute('BEGIN IMMEDIATE')
        if ident is None:
            ident = sync.allocate_id(c, 'memory_items')
            c.execute("INSERT INTO memory_items(id,kind,scope,content) VALUES(?,'fact','global',?)", (ident, content))
        else:
            c.execute('UPDATE memory_items SET content=? WHERE id=?', (content, ident))
    return ident


def send(db, exchange):
    sync.capture(db)
    assert not sync.publish(db, exchange)['unavailable']


def rows(db):
    with sync.connect(db) as c:
        return sync.rows(c)


def clean(report):
    assert all(report[key] == 0 for key in ('pending', 'conflict', 'invalid')), report
    assert not report['unavailable']


def exchange_files(exchange):
    return {str(p.relative_to(exchange)): p.read_bytes() for p in exchange.rglob('*') if p.is_file()}


@pytest.mark.parametrize('slots', [('mac', 'windows', 'linux'), ('windows', 'linux', 'mac'), ('linux', 'mac', 'windows')])
def test_three_peer_replay_and_uncaptured_writes(tmp_path, monkeypatch, slots):
    a, exchange = setup(tmp_path, monkeypatch, slots[0])
    adb = a / 'data/memory.db'
    x = put(adb, 'first')
    deleted = put(adb, 'deleted later')
    send(adb, exchange)
    put(adb, 'second', x)
    send(adb, exchange)
    with sync.connect(adb) as c, c:
        c.execute('DELETE FROM memory_items WHERE id=?', (deleted,))
    send(adb, exchange)
    uncaptured = put(adb, 'uncaptured insert')
    put(adb, 'uncaptured update', x)
    before = exchange_files(exchange)
    b = tmp_path / 'b'
    request, invitation = tmp_path / 'b-request.json', tmp_path / 'b-invite.json'
    prepare(b, exchange, slots[1], request)
    issued = issue(a, request, invitation)
    assert issued['status'] == 'issued'
    payload = json.loads(invitation.read_text())
    assert payload['baseline'] == 'empty' and payload['policy'] == 'legacy'
    assert payload['node'] == slots[1]
    assert 'uncaptured' not in invitation.read_text()
    result = join(b, exchange, invitation)
    assert result['status'] == 'joined'
    clean(result['receive'])
    assert result['receive']['applied'] == 3
    assert exchange_files(exchange) == before
    bdb = b / 'data/memory.db'
    assert rows(adb) != rows(bdb)
    send(adb, exchange)
    clean(sync.receive(bdb, exchange))
    assert rows(adb) == rows(bdb)
    put(adb, 'A new')
    put(bdb, 'B edit', x)
    send(adb, exchange); send(bdb, exchange)
    for db in (adb, bdb): clean(sync.receive(db, exchange))
    assert rows(adb) == rows(bdb)
    croot = tmp_path / 'c'
    creq, cinvite = tmp_path / 'c-request.json', tmp_path / 'c-invite.json'
    prepare(croot, exchange, slots[2], creq)
    issue(a, creq, cinvite)
    result = join(croot, exchange, cinvite)
    clean(result['receive'])
    cdb = croot / 'data/memory.db'
    assert rows(adb) == rows(bdb) == rows(cdb)
    put(cdb, 'C edits A', uncaptured)
    for db in (adb, bdb, cdb):
        put(db, 'new independent row')
        send(db, exchange)
    for db in (adb, bdb, cdb): clean(sync.receive(db, exchange))
    assert rows(adb) == rows(bdb) == rows(cdb)
    with sync.connect(cdb) as c:
        assert c.execute('SELECT row FROM _sync_shadow WHERE table_name=? AND key=?',
                         ('memory_items', sync.canonical([deleted]))).fetchone()[0] is None
    for root in (a, b, croot):
        signed_packets.private_directory(root)
        signed_packets.private_directory(root / 'data')
        for path in root.rglob('*'):
            if path.is_file():
                signed_packets.windows_private(path)
                if os.name != 'nt': assert path.stat().st_mode & 0o077 == 0
    for path in (request, invitation, creq, cinvite): signed_packets.read_local(path, private=True)
    assert not (b / '.setup-pending.json').exists()
    assert json.loads((b / 'data/workflow.json').read_text()) == {'ingest': False, 'summarize': False}


def test_slot_reservations_are_permanent_and_atomic(tmp_path, monkeypatch):
    a, exchange = setup(tmp_path, monkeypatch)
    requests = [tmp_path / f'request-{i}.json' for i in range(2)]
    for i, request in enumerate(requests): prepare(tmp_path / f'b{i}', exchange, 'windows', request)
    def attempt(i):
        try:
            return issue(a, requests[i], tmp_path / f'invite-{i}.json')['status']
        except ValueError as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert results.count('issued') == 1
    assert sum('slot already used or reserved' in r for r in results) == 1
    winner = next(i for i in range(2) if (tmp_path / f'invite-{i}.json').exists())
    (tmp_path / f'invite-{winner}.json').unlink()  # loss never releases the slot
    with pytest.raises(ValueError, match='slot already used or reserved'):
        issue(a, requests[winner], tmp_path / 'replacement.json')


@pytest.mark.parametrize('kind', ['own', 'observed', 'empty-directory'])
def test_refuses_used_slots(tmp_path, monkeypatch, kind):
    a, exchange = setup(tmp_path, monkeypatch)
    node = 'mac' if kind == 'own' else 'windows'
    if kind == 'observed':
        other = tmp_path / 'other.db'
        sqlite_memory.init_database(other)
        sync.initialize(other, node, sync.status(a / 'data/memory.db')['group_id'])
        put(other, 'already occupied'); send(other, exchange)
    if kind == 'empty-directory': (exchange / 'changes/windows').mkdir(parents=True)
    request = tmp_path / 'request.json'
    prepare(tmp_path / 'b', exchange, node, request)
    with pytest.raises(ValueError, match='slot already used or reserved'):
        issue(a, request, tmp_path / 'invite.json')


@pytest.mark.parametrize('kind', ['expired', 'reused', 'different-device', 'signed'])
def test_invitation_refusals(tmp_path, monkeypatch, kind):
    a, exchange = setup(tmp_path, monkeypatch)
    b, request, invitation = tmp_path / 'b', tmp_path / 'request.json', tmp_path / 'invite.json'
    prepare(b, exchange, 'windows', request)
    issue(a, request, invitation, expires_in=60)
    if kind == 'expired':
        expires = json.loads(invitation.read_text())['expires_at']
        monkeypatch.setattr(module().time, 'time', lambda: expires)
        reason = 'invitation expired'
    elif kind == 'reused':
        join(b, exchange, invitation)
        reason = 'invitation already consumed'
    elif kind == 'different-device':
        b = tmp_path / 'different'
        prepare(b, exchange, 'windows', tmp_path / 'different.json')
        reason = 'invitation belongs to another prepared device'
    else:
        (exchange / 'signed-changes/mac').mkdir(parents=True)
        reason = 'signed groups are not supported'
    before = exchange_files(exchange)
    with pytest.raises(ValueError, match=reason): join(b, exchange, invitation)
    assert exchange_files(exchange) == before
    if kind != 'reused': assert not (b / 'data/memory.db').exists()


@pytest.mark.parametrize('kind', ['nonempty', 'unknown', 'signed', 'joined-peer'])
def test_only_empty_origin_can_issue(tmp_path, monkeypatch, kind):
    a, exchange = setup(tmp_path, monkeypatch)
    db = a / 'data/memory.db'
    if kind == 'joined-peer':
        request, invitation = tmp_path / 'b.json', tmp_path / 'invite-b.json'
        prepare(tmp_path / 'b', exchange, 'windows', request)
        issue(a, request, invitation); join(tmp_path / 'b', exchange, invitation)
        a = tmp_path / 'b'
    else:
        with sync.connect(db) as c, c:
            if kind == 'unknown': c.execute('DROP TABLE _sync_join_origin')
            elif kind == 'nonempty':
                c.execute('INSERT INTO _sync_history VALUES(?,?,?)', ('baseline:' + '0' * 64, 'memory_items', '[1]'))
            else: c.execute('CREATE TABLE _sync_security(sender TEXT, group_id TEXT, node TEXT)')
    request = tmp_path / 'request.json'
    prepare(tmp_path / 'c', exchange, 'linux', request)
    reason = 'signed groups are not supported' if kind == 'signed' else 'original bootstrap|origin provenance'
    with pytest.raises(ValueError, match=reason): issue(a, request, tmp_path / 'invite.json')


def test_missing_predecessor_keeps_installation_partial_until_explicit_resume(tmp_path, monkeypatch):
    a, exchange = setup(tmp_path, monkeypatch)
    db = a / 'data/memory.db'
    x = put(db, 'first'); send(db, exchange)
    first = next((exchange / 'changes/mac').glob('*.json'))
    content = first.read_bytes()
    put(db, 'second', x); send(db, exchange)
    b, request, invitation = tmp_path / 'b', tmp_path / 'request.json', tmp_path / 'invite.json'
    prepare(b, exchange, 'windows', request); issue(a, request, invitation)
    first.unlink()
    result = join(b, exchange, invitation)
    assert result['status'] == 'pending' and result['receive']['pending'] == 1
    assert (b / '.setup-pending.json').exists()
    with pytest.raises(ValueError, match='invitation already consumed'): join(b, exchange, invitation)
    first.write_bytes(content)
    result = join(b, exchange, resume=True)
    assert result['status'] == 'joined'
    clean(result['receive'])
    assert rows(db) == rows(b / 'data/memory.db')


def test_cli_confirmation_and_private_outputs(tmp_path, monkeypatch, capsys):
    a, exchange = setup(tmp_path, monkeypatch)
    b, request, invitation = tmp_path / 'b', tmp_path / 'request.json', tmp_path / 'invite.json'
    command = ['prepare-join', '--local-dir', str(b), '--exchange', str(exchange), '--node', 'windows', '--output', str(request)]
    monkeypatch.setattr('sys.stdin', io.StringIO('NO\n'))
    assert agentmesh.main(command) == 2
    assert not b.exists() and not request.exists()
    assert agentmesh.main(command + ['--yes']) == 1
    assert not b.exists()
    assert agentmesh.main(command + ['--yes', '--confirm', 'REQUEST']) == 0
    assert agentmesh.main(['invite-peer', '--runtime', str(a / 'data/runtime.json'), '--request', str(request), '--output', str(invitation), '--yes', '--confirm', 'INVITE']) == 0
    assert agentmesh.main(['setup-join', '--local-dir', str(b), '--exchange', str(exchange), '--invitation', str(invitation), '--yes', '--confirm', 'JOIN']) == 0
    capsys.readouterr()
    assert agentmesh.main(['setup-join', '--local-dir', str(b), '--exchange', str(exchange), '--invitation', str(invitation), '--yes', '--confirm', 'JOIN']) == 1
    assert 'invitation already consumed' in capsys.readouterr().err


@pytest.mark.parametrize('kind', ['existing-output', 'exchange-output', 'symlink-output', 'wrong-group', 'malformed', 'source-output-failure'])
def test_fail_closed_inputs(tmp_path, monkeypatch, kind):
    a, exchange = setup(tmp_path, monkeypatch)
    b, request, output = tmp_path / 'b', tmp_path / 'request.json', tmp_path / 'invite.json'
    prepare(b, exchange, 'windows', request)
    if kind == 'existing-output': output.write_text('keep')
    elif kind == 'exchange-output': output = exchange / 'invite.json'
    elif kind == 'symlink-output': output.symlink_to(tmp_path / 'absent')
    elif kind == 'wrong-group':
        other = tmp_path / 'other.db'
        sqlite_memory.init_database(other)
        sync.initialize(other, 'linux', '00000000-0000-4000-8000-000000000001')
        put(other, 'foreign'); send(other, exchange)
    elif kind == 'malformed': signed_packets.write_local(request, {'bad': True})
    else:
        original = module().write_local
        def fail(path, value, **kwargs):
            if Path(path) == output: raise OSError('injected publication failure')
            return original(path, value, **kwargs)
        monkeypatch.setattr(module(), 'write_local', fail)
    with pytest.raises((OSError, ValueError)): issue(a, request, output)
    if kind == 'existing-output': assert output.read_text() == 'keep'
    if kind == 'source-output-failure':
        with pytest.raises(ValueError, match='slot already used or reserved'):
            issue(a, request, tmp_path / 'retry.json')


def test_nonempty_baseline_stays_ineligible_after_rows_are_deleted(tmp_path, monkeypatch):
    a, exchange = setup(tmp_path, monkeypatch)
    db = a / 'data/memory.db'
    group = sync.status(db)['group_id']
    db.unlink()
    sqlite_memory.init_database(db)
    with sync.connect(db) as c, c:
        c.execute("INSERT INTO memory_items(id,kind,scope,content) VALUES(1,'fact','global','original baseline')")
    sync.initialize(db, 'mac', group)
    with sync.connect(db) as c, c:
        c.execute('DELETE FROM memory_items')
    send(db, exchange)
    request = tmp_path / 'request.json'
    prepare(tmp_path / 'b', exchange, 'windows', request)
    with pytest.raises(ValueError, match='non-empty baseline group; use the original bootstrap'):
        issue(a, request, tmp_path / 'invite.json')


@pytest.mark.parametrize('failure', ['initialize', 'complete-marker'])
def test_resume_after_interrupted_installation(tmp_path, monkeypatch, failure):
    a, exchange = setup(tmp_path, monkeypatch)
    b, request, invitation = tmp_path / 'b', tmp_path / 'request.json', tmp_path / 'invite.json'
    prepare(b, exchange, 'windows', request); issue(a, request, invitation)
    with monkeypatch.context() as patch:
        if failure == 'initialize':
            def fail(*args, **kwargs): raise OSError('initialization interrupted')
            patch.setattr(sync, 'initialize', fail)
        else:
            original = Path.unlink
            def fail(path, *args, **kwargs):
                if path == b / '.setup-pending.json': raise OSError('interrupted before marker removal')
                return original(path, *args, **kwargs)
            patch.setattr(Path, 'unlink', fail)
        with pytest.raises(OSError): join(b, exchange, invitation)
    assert (b / '.setup-pending.json').exists()
    with pytest.raises(ValueError, match='invitation already consumed'): join(b, exchange, invitation)
    result = join(b, exchange, resume=True)
    assert result['status'] == 'joined'
    assert not (b / '.setup-pending.json').exists()


def test_concurrent_invitation_consumption(tmp_path, monkeypatch):
    a, exchange = setup(tmp_path, monkeypatch)
    b, request, invitation = tmp_path / 'b', tmp_path / 'request.json', tmp_path / 'invite.json'
    prepare(b, exchange, 'windows', request); issue(a, request, invitation)
    def attempt(_):
        try: return join(b, exchange, invitation)['status']
        except ValueError as exc: return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(attempt, range(2)))
    assert sorted(results) == ['invitation already consumed', 'joined']


def test_signed_source_refusal_has_actionable_cli_reason(tmp_path, monkeypatch, capsys):
    a, exchange = setup(tmp_path, monkeypatch)
    request = tmp_path / 'request.json'
    prepare(tmp_path / 'b', exchange, 'windows', request)
    with sync.connect(a / 'data/memory.db') as c, c:
        c.execute('CREATE TABLE _sync_security(sender TEXT, group_id TEXT, node TEXT)')
    capsys.readouterr()
    result = agentmesh.main(['invite-peer', '--runtime', str(a / 'data/runtime.json'),
                            '--request', str(request), '--output', str(tmp_path / 'invite.json'),
                            '--yes', '--confirm', 'INVITE'])
    assert result == 1
    error = json.loads(capsys.readouterr().err)
    assert 'signed groups are not supported' in error['reason']
    assert 'security pairing workflow' in error['reason']


def test_expiry_does_not_release_reservation_or_block_consumed_replay(tmp_path, monkeypatch):
    a, exchange = setup(tmp_path, monkeypatch)
    b, request, invitation = tmp_path / 'b', tmp_path / 'request.json', tmp_path / 'invite.json'
    prepare(b, exchange, 'windows', request); issue(a, request, invitation, expires_in=60)
    with monkeypatch.context() as patch:
        patch.setattr(module(), 'replay', lambda *args: {'status': 'pending'})
        assert join(b, exchange, invitation)['status'] == 'pending'
    expires = json.loads(invitation.read_text())['expires_at']
    monkeypatch.setattr(module().time, 'time', lambda: expires)
    with pytest.raises(ValueError, match='slot already used or reserved'):
        issue(a, request, tmp_path / 'second-invite.json')
    assert join(b, exchange, resume=True)['status'] == 'joined'


def test_source_validates_again_after_confirmation(tmp_path, monkeypatch):
    a, exchange = setup(tmp_path, monkeypatch)
    request, invitation = tmp_path / 'request.json', tmp_path / 'invite.json'
    prepare(tmp_path / 'b', exchange, 'windows', request)
    def occupy(*args, **kwargs):
        (exchange / 'changes/windows').mkdir(parents=True)
        return True
    monkeypatch.setattr(module(), 'confirm', occupy)
    with pytest.raises(ValueError, match='slot already used or reserved'): issue(a, request, invitation)
    assert not invitation.exists()


@pytest.mark.skipif(os.name == 'nt', reason='POSIX modes; native ACL checks cover Windows')
def test_private_state_and_transfer_permissions_are_required(tmp_path, monkeypatch):
    a, exchange = setup(tmp_path, monkeypatch)
    b, request, invitation = tmp_path / 'b', tmp_path / 'request.json', tmp_path / 'invite.json'
    prepare(b, exchange, 'windows', request)
    request.chmod(0o644)
    with pytest.raises(ValueError, match='owner-only'): issue(a, request, invitation)
    request.chmod(0o600)
    issue(a, request, invitation)
    invitation.chmod(0o644)
    with pytest.raises(ValueError, match='owner-only'): join(b, exchange, invitation)
    invitation.chmod(0o600)
    (b / 'data/join-state.json').chmod(0o644)
    with pytest.raises(ValueError, match='owner-only'): join(b, exchange, invitation)


def test_missing_newest_packet_is_silently_incomplete(tmp_path, monkeypatch):
    a, exchange = setup(tmp_path, monkeypatch)
    adb = a / 'data/memory.db'
    put(adb, 'first'); send(adb, exchange)
    original = set((exchange / 'changes/mac').glob('*.json'))
    put(adb, 'newest independent row'); send(adb, exchange)
    newest = (set((exchange / 'changes/mac').glob('*.json')) - original).pop()
    b, request, invitation = tmp_path / 'b', tmp_path / 'request.json', tmp_path / 'invite.json'
    prepare(b, exchange, 'windows', request); issue(a, request, invitation)
    newest.unlink()
    result = join(b, exchange, invitation)
    assert result['status'] == 'joined'
    clean(result['receive'])
    assert rows(adb) != rows(b / 'data/memory.db')  # documented no-pruning precondition


def test_conflicting_history_never_completes_join(tmp_path, monkeypatch):
    import uuid
    a, exchange = setup(tmp_path, monkeypatch)
    adb = a / 'data/memory.db'
    put(adb, 'row'); send(adb, exchange)
    packet = json.loads(next((exchange / 'changes/mac').glob('*.json')).read_text())
    packet['uuid'] = str(uuid.uuid4())
    packet['body'][0]['revision'] = str(uuid.uuid4())
    packet['checksum'] = sync.digest(packet['body'])
    (exchange / 'changes/mac' / (packet['uuid'] + '.json')).write_text(json.dumps(packet))
    b, request, invitation = tmp_path / 'b', tmp_path / 'request.json', tmp_path / 'invite.json'
    prepare(b, exchange, 'windows', request); issue(a, request, invitation)
    result = join(b, exchange, invitation)
    assert result['status'] == 'blocked' and result['failure_kind'] == 'permanent'
    assert result['receive']['conflict'] == 1
    assert (b / '.setup-pending.json').exists()
    assert not (b / 'data/runtime.json').exists()


def test_source_ledger_and_recipient_secret_are_not_transfer_files(tmp_path, monkeypatch):
    a, exchange = setup(tmp_path, monkeypatch)
    b, request, invitation = tmp_path / 'b', tmp_path / 'request.json', tmp_path / 'invite.json'
    prepare(b, exchange, 'windows', request); issue(a, request, invitation)
    state = json.loads((b / 'data/join-state.json').read_text())
    assert len(state['secret']) == 64
    assert state['secret'] not in request.read_text() and state['secret'] not in invitation.read_text()
    payload = json.loads(invitation.read_text())
    assert set(payload) == {'format', 'id', 'group', 'node', 'issuer', 'origin', 'baseline', 'policy',
                            'request_id', 'recipient', 'issued_at', 'expires_at'}
    with sync.connect(a / 'data/memory.db') as c:
        assert json.loads(c.execute('SELECT invitation FROM _sync_join_reservations').fetchone()[0]) == payload
        assert c.execute('SELECT origin FROM _sync_join_origin').fetchone()[0] == payload['origin']
    join(b, exchange, invitation)
    with sync.connect(b / 'data/memory.db') as c:
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='_sync_join_origin'").fetchone()


def test_same_invitation_remains_bound_after_recipient_is_reprepared(tmp_path, monkeypatch):
    import shutil
    a, exchange = setup(tmp_path, monkeypatch)
    b, request, invitation = tmp_path / 'b', tmp_path / 'request.json', tmp_path / 'invite.json'
    prepare(b, exchange, 'windows', request); issue(a, request, invitation)
    join(b, exchange, invitation)
    shutil.rmtree(b)
    prepare(b, exchange, 'windows', tmp_path / 'new-request.json')
    with pytest.raises(ValueError, match='another prepared device'): join(b, exchange, invitation)


def test_join_lock_uses_private_publication_before_acl_validation(tmp_path, monkeypatch):
    # The generic runtime lock's newly created Windows file inherits its DACL;
    # install_join must provision it via write_local before private validation.
    a, exchange = setup(tmp_path, monkeypatch)
    b, request, invitation = tmp_path / 'b', tmp_path / 'request.json', tmp_path / 'invite.json'
    prepare(b, exchange, 'windows', request); issue(a, request, invitation)
    provisioned = set()
    original_write, original_private = module().write_local, module().private_file
    def write(path, value, **kwargs):
        result = original_write(path, value, **kwargs)
        provisioned.add(Path(path))
        return result
    def private(path):
        if Path(path).name == 'join.lock': assert Path(path) in provisioned
        return original_private(path)
    monkeypatch.setattr(module(), 'write_local', write)
    monkeypatch.setattr(module(), 'private_file', private)
    assert join(b, exchange, invitation)['status'] == 'joined'


@pytest.mark.parametrize('suffix', ['-wal', '-shm'])
def test_sqlite_sidecar_disappearing_during_inspection_is_not_a_failed_invite(tmp_path, monkeypatch, suffix):
    a, exchange = setup(tmp_path, monkeypatch)
    request, invitation = tmp_path / 'request.json', tmp_path / 'invite.json'
    prepare(tmp_path / 'b', exchange, 'windows', request)
    sidecar = a / ('data/memory.db' + suffix)
    sidecar.touch(mode=0o600)
    original = module().private_file
    disappeared = []
    def inspect(path, **kwargs):
        if Path(path) == sidecar and not disappeared:
            # Model SQLite's last connection closing between exists() and stat().
            sidecar.unlink()
            disappeared.append(path)
            raise FileNotFoundError(str(path))
        return original(path, **kwargs)
    monkeypatch.setattr(module(), 'private_file', inspect)
    assert issue(a, request, invitation)['status'] == 'issued'
    assert disappeared


@pytest.mark.parametrize('fault', [None, 'principal', 'owner', 'null-dacl', 'inherit-only', 'no-full-control'])
def test_sqlite_sidecar_acl_accepts_only_private_inheritance(fault):
    import copy
    sid = 'S-1-5-21-111111111-222222222-333333333-1001'
    report = {'sid': sid, 'owner': sid, 'directory': False, 'control': 4,
              'rules': [{'sid': sid, 'type': 0, 'flags': 16, 'mask': 0x1f01ff}]}
    if fault == 'principal': report['rules'].append({'sid': 'S-1-1-0', 'type': 0, 'flags': 16, 'mask': 0x120089})
    elif fault == 'owner': report['owner'] = 'S-1-5-18'
    elif fault == 'null-dacl': report['control'] = 0; report['rules'] = []
    elif fault == 'inherit-only': report['rules'][0]['flags'] = 24
    elif fault == 'no-full-control': report['rules'][0]['mask'] = 0x120089
    before = copy.deepcopy(report)
    if fault is None:
        module().sidecar_acl(report)
    else:
        with pytest.raises(ValueError): module().sidecar_acl(report)
    assert report == before  # validation never changes an existing descriptor
