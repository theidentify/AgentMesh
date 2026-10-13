"""Passphrase-protected identity storage. Disposable keys only; never real ones."""
from contextlib import closing
import io
import json
from pathlib import Path
import uuid

import pytest
import key_protection as kp
import signed_packets as signed

GROUP = '00000000-0000-4000-8000-000000000001'
PASS = 'correct horse battery staple'
FAST = {'n': 2 ** 14}  # test-only KDF cost; production uses kp.SCRYPT_N


def identity(tmp_path):
    directory = tmp_path / 'identity'
    public = signed.init_identity(directory, GROUP, 'mac')
    return directory, public


def raw(directory):
    return json.loads((directory / 'identity.json').read_bytes())


def answers(*values):
    values = list(values)
    return lambda prompt: values.pop(0)


def test_protect_keeps_public_identity_and_unlocks_to_the_same_key(tmp_path):
    directory, public = identity(tmp_path)
    before = raw(directory)
    report = kp.protect_identity(directory, answers(PASS, PASS), **FAST)
    assert report == {'status': 'protected', 'key_id': public['key_id']}
    after = raw(directory)
    assert 'private_key' not in after and set(after) == signed.PUBLIC_FIELDS | {'protection'}
    assert {k: after[k] for k in signed.PUBLIC_FIELDS} == {k: before[k] for k in signed.PUBLIC_FIELDS}
    assert before['private_key'] not in (directory / 'identity.json').read_text()
    security = signed.Security(directory, unlock=lambda: PASS)
    assert not security.locked and security.public == public
    assert security.key.private_bytes_raw().hex() == before['private_key']


def test_locked_identity_serves_public_checks_but_never_signs(tmp_path):
    directory, public = identity(tmp_path)
    kp.protect_identity(directory, answers(PASS, PASS), **FAST)
    security = signed.Security(directory)
    assert security.locked and security.key is None and security.public == public
    with pytest.raises(ValueError, match='passphrase-protected identity is locked'):
        security.sign({'group': GROUP, 'node': 'mac', 'uuid': str(uuid.uuid4()), 'body': [1]})
    with pytest.raises(ValueError, match='passphrase-protected identity is locked'):
        security.require_unlocked()


@pytest.mark.parametrize('damage', ['passphrase', 'ciphertext', 'nonce', 'aad-sender', 'aad-key-id'])
def test_wrong_passphrase_or_tampering_fails_closed(tmp_path, damage):
    directory, _ = identity(tmp_path)
    kp.protect_identity(directory, answers(PASS, PASS), **FAST)
    data = raw(directory)
    if damage == 'ciphertext':
        c = data['protection']['ciphertext']
        data['protection']['ciphertext'] = ('0' if c[0] != '0' else '1') + c[1:]
    elif damage == 'nonce':
        data['protection']['nonce'] = '00' * 12
    elif damage == 'aad-sender':
        data['sender'] = str(uuid.uuid4())
    elif damage == 'aad-key-id':
        data['key_id'] = 'f' * 64
    signed.write_local(directory / 'identity.json', data)
    with pytest.raises(ValueError):
        signed.Security(directory, unlock=lambda: 'wrong passphrase!!' if damage == 'passphrase' else PASS)


@pytest.mark.parametrize('field,value', [('n', 2 ** 30), ('n', 1000), ('r', 1), ('p', 64), ('salt', 'ab'),
                                         ('cipher', 'aes-128-ecb'), ('kdf', 'md5'), ('format', 'v0')])
def test_corrupted_parameters_are_rejected_before_any_expensive_work(tmp_path, field, value):
    directory, _ = identity(tmp_path)
    kp.protect_identity(directory, answers(PASS, PASS), **FAST)
    data = raw(directory)
    data['protection'][field] = value
    signed.write_local(directory / 'identity.json', data)
    with pytest.raises(ValueError, match='protected identity'):
        signed.Security(directory, unlock=lambda: PASS)


def test_migration_refuses_mismatch_short_reuse_and_already_protected(tmp_path):
    directory, _ = identity(tmp_path)
    before = (directory / 'identity.json').read_bytes()
    with pytest.raises(ValueError, match='do not match'):
        kp.protect_identity(directory, answers(PASS, PASS + 'x'), **FAST)
    with pytest.raises(ValueError, match='at least'):
        kp.protect_identity(directory, answers('short', 'short'), **FAST)
    assert (directory / 'identity.json').read_bytes() == before  # untouched on refusal
    kp.protect_identity(directory, answers(PASS, PASS), **FAST)
    with pytest.raises(ValueError, match='already passphrase-protected'):
        kp.protect_identity(directory, answers(PASS, PASS), **FAST)


def test_interrupted_migration_leaves_the_original_or_the_protected_file(tmp_path, monkeypatch):
    directory, public = identity(tmp_path)
    before = (directory / 'identity.json').read_bytes()
    def crash(source, target, **kwargs):
        raise OSError('fixture power loss before replace')
    monkeypatch.setattr(signed, 'replace', crash)
    with pytest.raises(OSError):
        kp.protect_identity(directory, answers(PASS, PASS), **FAST)
    assert (directory / 'identity.json').read_bytes() == before
    assert sorted(p.name for p in directory.iterdir()) == ['identity.json', 'trust.json']  # no temp left
    assert signed.Security(directory).public == public and not signed.Security(directory).locked


def test_unattended_unlock_is_refused_without_a_console(monkeypatch):
    monkeypatch.setattr('sys.stdin', io.StringIO(PASS + '\n'))
    with pytest.raises(ValueError, match='interactive console'):
        kp.console_passphrase('Passphrase: ')


def test_secrets_never_appear_in_the_stored_file_or_reports(tmp_path):
    directory, public = identity(tmp_path)
    secret = raw(directory)['private_key']
    report = kp.protect_identity(directory, answers(PASS, PASS), **FAST)
    stored = (directory / 'identity.json').read_text()
    assert PASS not in stored and secret not in stored
    assert PASS not in json.dumps(report) and secret not in json.dumps(report)


def test_worker_refuses_a_locked_identity_before_any_write(tmp_path, monkeypatch):
    import sqlite3
    import memory_sync
    import sqlite_memory
    import sync_worker
    directory, _ = identity(tmp_path)
    kp.protect_identity(directory, answers(PASS, PASS), **FAST)
    db = tmp_path / 'mac.db'
    sqlite_memory.init_database(db)
    memory_sync.initialize(db, 'mac', GROUP)
    exchange = tmp_path / 'exchange'
    (exchange / '.stfolder').mkdir(parents=True)
    before = db.read_bytes()
    with pytest.raises(ValueError, match='passphrase-protected identity is locked'):
        sync_worker.run_once(db, exchange, security_dir=directory)
    assert db.read_bytes() == before and not list((exchange).glob('*/*'))


def test_cli_protect_requires_a_console_and_reports_without_secrets(tmp_path, monkeypatch, capsys):
    import agentmesh
    directory, _ = identity(tmp_path)
    monkeypatch.setattr('sys.stdin', io.StringIO(PASS + '\n' + PASS + '\n'))
    assert agentmesh.main(['identity-protect', '--security-dir', str(directory)]) == 1
    err = capsys.readouterr().err
    assert 'interactive console' in err or 'details withheld' in err
    assert 'private_key' in raw(directory)  # unchanged without a console


def test_protected_identity_stays_locked_across_restarts_and_the_restarted_worker_refuses(tmp_path, monkeypatch):
    import sqlite3
    import subprocess
    import sys
    import memory_sync
    import worker_lifecycle as worker
    from install_adopt import adopt
    from test_install_adopt import existing
    from test_worker_lifecycle import NATIVE_TIMEOUT
    args = existing(tmp_path)
    public = signed.init_identity(args['security_dir'], GROUP, 'mac')
    with memory_sync.connect(args['database']) as c, c:
        signed.Security(args['security_dir']).guard(c, args['exchange'])  # strict policy: the worker must sign
    monkeypatch.setattr('sys.stdin', io.StringIO('BIND\n'))
    adopt(**args)
    kp.protect_identity(args['security_dir'], answers(PASS, PASS), **FAST)
    identity_bytes = (args['security_dir'] / 'identity.json').read_bytes()
    def child(code, *extra):
        return subprocess.run([sys.executable, '-c', code, str(args['security_dir']), *extra],
                              capture_output=True, text=True, timeout=NATIVE_TIMEOUT,
                              cwd=Path(__file__).resolve().parent)
    # An interactive unlock lives only in its own process.
    unlocked = child('import sys, signed_packets as s\n'
                     's.Security(sys.argv[1], unlock=lambda: sys.argv[2]).sign_bytes(b"probe")\n'
                     'print("signed")', PASS)
    assert unlocked.returncode == 0 and unlocked.stdout.strip() == 'signed', unlocked.stderr
    restarted = child('import sys, signed_packets as s\n'
                      'x = s.Security(sys.argv[1])\n'
                      'print(x.locked, x.key is None, x.public["key_id"])\n'
                      'x.sign_bytes(b"probe")')
    assert restarted.returncode == 1 and restarted.stdout.split() == ['True', 'True', public['key_id']]
    assert kp.LOCKED in restarted.stderr
    database = args['database'].read_bytes()
    record = args['database'].parent / '.agentmesh-worker' / 'process.json'
    for _ in range(2):  # the first start and a restart both refuse
        run = subprocess.run(worker.command(args['runtime'], once=True), capture_output=True, text=True,
                             timeout=NATIVE_TIMEOUT)
        assert run.returncode == 1, run.stderr
        detail = json.loads(record.read_text())['last_error_detail']
        assert detail['reason'] == kp.LOCKED and detail['code'] == 'GUARD_REFUSED'
        assert args['database'].read_bytes() == database
        assert not (args['exchange'] / 'signed-changes').exists() and not (args['exchange'] / 'changes').exists()
    assert (args['security_dir'] / 'identity.json').read_bytes() == identity_bytes
    with closing(sqlite3.connect(args['database'])) as c:
        assert c.execute('SELECT COUNT(*) FROM _sync_security').fetchone()[0] == 1
