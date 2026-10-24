"""Read-only remote verification, including content-free failure output."""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from broker.support import setup_home, remember
from agentmesh_log.broker.service import Broker, Session, PREFIXES
from agentmesh_log.storage.filesystem import FilesystemStorage
from agentmesh_log import verify_remote


@pytest.fixture(autouse=True)
def file_keystore(monkeypatch):
    monkeypatch.setenv('AGENTMESH_ALLOW_FILE_KEYSTORE', '1')


@pytest.fixture
def synced(tmp_path):
    home, state, store, agents = setup_home(tmp_path)
    state.control['agent_names'] = {'private-assistant': agents[0].agent_id}
    store.save(state)
    broker = Broker(home)
    remember(broker, Session(agents[0].certificate()), agents[0], body='SECRET memory body')
    remote = FilesystemStorage(tmp_path / 'remote')
    assert broker.sync_once(remote)['errors'] == 0
    markers = tmp_path / 'markers.txt'
    markers.write_text('SECRET plaintext probe\n\nabc\n', encoding='utf-8')
    for index in broker.indexes.values():
        index.close()
    return home, state, remote, markers


def args_for(synced):
    home, _, remote, markers = synced
    return ['--home', str(home.root), '--filesystem', str(remote.root), '--markers', str(markers)]


def run(args, capsys, state=None):
    code = verify_remote.main(args)
    captured = capsys.readouterr()
    assert captured.err == ''
    assert len(captured.out.splitlines()) == 1
    assert 'SECRET' not in captured.out
    assert 'private-assistant' not in captured.out
    assert 'a' * 32 not in captured.out and 'b' * 64 not in captured.out
    if state:
        for tree in state.trees:
            for epochs in tree.epochs.values():
                for key in epochs:
                    assert key.private_bytes().hex() not in captured.out
        assert state.device.private_bytes().hex() not in captured.out
    return code, json.loads(captured.out)


def snapshot(root):
    return {str(p.relative_to(root)): (p.stat().st_mtime_ns,
            hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None)
            for p in [root, *root.rglob('*')]}


def test_good(synced, capsys):
    home, state, remote, _ = synced
    before = snapshot(home.root), snapshot(remote.root)
    code, result = run(args_for(synced), capsys, state)
    count = sum(len(list(remote.list(prefix))) for prefix in PREFIXES)
    assert code == 0
    assert result == dict(ok=True, local_objects=count, remote_objects=count,
        missing_remote=0, missing_local=0, unknown_remote=0, hash_mismatch=0, unparsable=0,
        probes=8, marker_hits=0, restored_events=1, opened_events=1)
    assert (snapshot(home.root), snapshot(remote.root)) == before


def test_unknown_remote(synced, capsys, monkeypatch):
    remote = synced[2]
    count = len(list(remote.list('')))
    for prefix in ('future-tier/', 'outbox/', 'archive/'):
        remote.put(prefix + 'extra', b'not JSON')
    calls = []
    original = remote.list
    def listing(prefix):
        calls.append(prefix)
        return original(prefix)
    monkeypatch.setattr(remote, 'list', listing)
    monkeypatch.setattr(verify_remote.cli, 'remote_storage', lambda *args, **kwargs: remote)
    code, result = run(args_for(synced), capsys)
    assert code == 0
    assert result == dict(ok=True, local_objects=count, remote_objects=count + 3,
        missing_remote=0, missing_local=0, unknown_remote=3, hash_mismatch=0, unparsable=0,
        probes=8, marker_hits=0, restored_events=1, opened_events=1)
    assert calls == ['']


@pytest.mark.parametrize('prefix', ['outbox/', 'future-tier/'], ids=['outbox', 'future'])
def test_unknown_remote_marker(synced, capsys, prefix):
    synced[2].put(prefix + 'extra', b'SECRET plaintext probe')
    code, result = run(args_for(synced), capsys)
    assert code == 1 and not result['ok']
    assert result['marker_hits'] == result['unknown_remote'] == 1
    assert result['missing_remote'] == result['missing_local'] == result['hash_mismatch'] == 0
    assert result['unparsable'] == 0
    assert result['restored_events'] == result['opened_events'] == 1


def test_missing(synced, capsys):
    remote = synced[2]
    (remote.root / next(remote.list('events/'))).unlink()
    code, result = run(args_for(synced), capsys)
    assert code == 1 and not result['ok']
    assert result['missing_remote'] == 1
    assert result['restored_events'] == result['opened_events'] == 0


def test_tampered(synced, capsys):
    remote = synced[2]
    (remote.root / next(remote.list('events/'))).write_bytes(b'SECRET plaintext probe')
    code, result = run(args_for(synced), capsys)
    assert code == 1
    assert result['hash_mismatch'] == result['unparsable'] == result['marker_hits'] == 1
    assert result['opened_events'] == 0


@pytest.mark.parametrize('probe', ['SECRET plaintext probe', 'scope', 'private-assistant', 'personal'],
                         ids=['marker', 'scope', 'agent', 'profile'])
def test_markers(synced, capsys, probe):
    from agentmesh_log.broker.grants import names
    if probe == 'scope': probe = max(names(synced[1]).values(), key=len)
    synced[2].put('alerts/extra.json', json.dumps({'value': probe}).encode())
    code, result = run(args_for(synced), capsys)
    assert code == 1 and result['marker_hits'] == result['missing_local'] == 1


@pytest.mark.parametrize('probe', ['secret PLAINTEXT probe', 'PERSONAL', 'work'], ids=['case', 'profile-case', 'short'])
def test_markers_not_folded(synced, capsys, probe):
    # Case-folded or < 6-char probes would match random base64 ciphertext by chance.
    synced[2].put('alerts/extra.json', json.dumps({'value': probe}).encode())
    code, result = run(args_for(synced), capsys)
    assert result['marker_hits'] == 0 and result['missing_local'] == 1


def test_sample(synced, tmp_path, capsys, monkeypatch):
    # URI-special characters in the path; '?' is not a valid Windows filename character.
    db = tmp_path / ('source ?#%.db' if os.name == 'posix' else 'source #%.db')
    content = 'SECRET sampled content has at least six words in this sentence'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE memory_items (content TEXT, status TEXT)')
        conn.executemany('INSERT INTO memory_items VALUES (?, ?)',
            [(content, 'active'), ('SECRET inactive has at least six words also', 'deleted'),
             ('', 'active'), ('few words', 'active'), (None, 'active')])
    synced[2].put('alerts/sample.json', json.dumps({'value': content}).encode())
    before = snapshot(synced[0].root), snapshot(synced[2].root), snapshot(db)
    original = sqlite3.connect
    def readonly(database, **kwargs):
        assert database.endswith('?mode=ro') and kwargs['uri'] is True
        conn = original(database, **kwargs)
        with pytest.raises(sqlite3.OperationalError):
            conn.execute('DELETE FROM memory_items')
        conn.rollback()
        return conn
    monkeypatch.setattr(sqlite3, 'connect', readonly)
    code, result = run(args_for(synced) + ['--sample-db', str(db), '--sample', '25'], capsys)
    assert code == 1 and result['marker_hits'] == 1 and result['probes'] == 9
    assert (snapshot(synced[0].root), snapshot(synced[2].root), snapshot(db)) == before


def test_symlink(synced, tmp_path, capsys):
    link = tmp_path / 'SECRET-link'
    link.symlink_to(synced[3])
    args = args_for(synced)
    args[-1] = str(link)
    code, result = run(args, capsys)
    assert code == 2 and result == {'ok': False, 'error': 'verification_error'}


@pytest.mark.parametrize('extra', [['--SECRET'], ['--sample', '-1'], ['--sample', '2'], ['--sample-db', 'SECRET']],
                         ids=['unknown', 'negative', 'no-db', 'no-count'])
def test_bad_args(synced, capsys, extra):
    code, result = run(args_for(synced) + extra, capsys)
    assert code == 2 and result == {'ok': False, 'error': 'verification_error'}


def test_missing_root(synced, tmp_path, capsys):
    args = args_for(synced)
    missing = tmp_path / 'SECRET-absent'
    for index in (1, 3):
        bad = list(args)
        bad[index] = str(missing)
        code, _ = run(bad, capsys)
        assert code == 2 and not missing.exists()


def test_s3(synced, monkeypatch, capsys):
    boto3 = pytest.importorskip('boto3')
    moto = pytest.importorskip('moto')
    from agentmesh_log.storage import r2creds
    from agentmesh_log.cli import remote_storage
    from argparse import Namespace
    calls = []
    def credentials(service):
        calls.append(service)
        return 'a' * 32, 'b' * 64
    monkeypatch.setattr(r2creds, 'load_credentials', credentials)
    with moto.mock_aws():
        client = boto3.client('s3', region_name='us-east-1')
        client.create_bucket(Bucket='verify-test')
        flags = dict(filesystem=None, s3_bucket='verify-test', s3_prefix='v1/',
                     s3_endpoint='https://s3.amazonaws.com', s3_credentials='keychain:test')
        remote = remote_storage(Namespace(**flags))
        broker = Broker(synced[0])
        assert broker.sync_once(remote)['errors'] == 0
        for index in broker.indexes.values(): index.close()
        before = client.list_objects_v2(Bucket='verify-test')['Contents']
        code, result = run(['--home', str(synced[0].root), '--markers', str(synced[3]),
            '--s3-bucket', 'verify-test', '--s3-prefix', 'v1/', '--s3-endpoint',
            'https://s3.amazonaws.com', '--s3-credentials', 'keychain:test'], capsys, synced[1])
        assert code == 0 and result['ok'] and calls == ['keychain:test', 'keychain:test']
        assert client.list_objects_v2(Bucket='verify-test')['Contents'] == before


def test_module(synced):
    result = subprocess.run([sys.executable, '-m', 'agentmesh_log.verify_remote', *args_for(synced)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0 and json.loads(result.stdout)['ok']
    assert len(result.stdout.splitlines()) == 1 and result.stderr == ''


def test_restore(synced, capsys, monkeypatch):
    home, state, remote, _ = synced
    # Retain only profile roots locally; child epochs must come from restored wraps.
    from agentmesh_log.broker.keystore import FileKeyStore
    for tree in state.trees:
        tree.epochs = {tree.root.scope_id: tree.epochs[tree.root.scope_id]}
    FileKeyStore(home).save(state)
    before = snapshot(home.root)
    directories = []
    original = verify_remote.tempfile.TemporaryDirectory
    def temporary(**kwargs):
        temp = original(**kwargs)
        directories.append(Path(temp.name))
        return temp
    monkeypatch.setattr(verify_remote.tempfile, 'TemporaryDirectory', temporary)
    code, result = run(args_for(synced), capsys)
    assert code == 0 and result['opened_events'] == 1
    assert directories and all(not p.exists() for p in directories)
    assert snapshot(home.root) == before


def test_wrong_path(synced, capsys):
    remote = synced[2]
    key = next(remote.list('events/'))
    remote.put(key.replace('.json', '0.json'), remote.get(key))
    code, result = run(args_for(synced), capsys)
    assert code == 1 and result['unparsable'] == 1
    assert result['restored_events'] == 2 and result['opened_events'] == 1


def test_read_error(synced, capsys, monkeypatch):
    def refused(*args, **kwargs):
        raise RuntimeError('SECRET credentials and body')
    monkeypatch.setattr(verify_remote.cli, 'remote_storage', refused)
    code, result = run(args_for(synced), capsys)
    assert code == 2 and result == {'ok': False, 'error': 'verification_error'}


def test_connector_inventory(synced, capsys):
    home, _, remote, _ = synced
    local = FilesystemStorage(home.root/'replica')
    local.put('connectors/policies/device.json', b'{}')
    remote.put('connectors/policies/device.json', b'{}')
    remote.put('connectors/gateway-state/remote.json', b'{}')
    local.put('connectors/gateway-state/local.json', b'{}')
    code, result = run(args_for(synced), capsys)
    assert code == 0
    assert result['missing_local'] == result['missing_remote'] == 0
    assert result['unknown_remote'] == 1
