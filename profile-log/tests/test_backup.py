import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile

import pytest

from agentmesh_log import backup
from agentmesh_log.agekeys import DeviceAgeIdentity, encrypt
from agentmesh_log.oskeystore import initialize, select_store
from agentmesh_log.recovery import RecoveryIdentity, combine_shares
from broker.support import BrokerHome, setup_home

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='backup tool is POSIX-only (macOS host)')


@pytest.fixture(autouse=True)
def file_keystore(monkeypatch):
    monkeypatch.setenv('AGENTMESH_ALLOW_FILE_KEYSTORE', '1')


@pytest.fixture
def vault(tmp_path):
    home = BrokerHome(tmp_path / 'home')
    shares = initialize(home, ['work'], file_keystore=True)
    source = tmp_path / 'source'
    source.mkdir(mode=0o700)
    (source / 'nested').mkdir()
    (source / 'nested' / 'secret').write_bytes(b'SENTINEL-PLAINTEXT-secret\x00\xff')
    (source / 'nested' / 'secret').chmod(0o640)
    (source / 'run').write_bytes(b'run me\n')
    (source / 'run').chmod(0o751)
    outdir = tmp_path / 'backups'
    outdir.mkdir(mode=0o700)
    return home, source, outdir / 'backup.age', RecoveryIdentity(combine_shares(shares[:2]))


def create_args(vault, yes=True):
    home, source, output, _ = vault
    return ['create', '--home', str(home.root), '--out', str(output),
            '--include', str(source)] + (['--yes'] if yes else [])


def restore_args(vault, target, yes=True):
    return ['restore', '--home', str(vault[0].root), '--in', str(vault[2]),
            '--to', str(target)] + (['--yes'] if yes else [])


def snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes(), stat.S_IMODE(p.stat().st_mode))
            for p in root.rglob('*') if p.is_file()}


def test_round_trip_and_recovery(vault, tmp_path, capsys):
    home, source, output, recovery = vault
    before = snapshot(home.root)
    assert backup.main(create_args(vault)) == 0
    result = json.loads(capsys.readouterr().out)
    ciphertext = output.read_bytes()
    assert result == dict(ok=True, files=2, bytes=sum(len(v[0]) for v in snapshot(source).values()),
                          recipients=['device', 'recovery'], sha256=hashlib.sha256(ciphertext).hexdigest())
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert b'SENTINEL-PLAINTEXT' not in ciphertext
    with tarfile.open(fileobj=io.BytesIO(recovery.age.decrypt(ciphertext))) as archive:
        assert len(archive.getmembers()) == 2
        assert all(m.isfile() and m.uid == m.gid == 0 and m.uname == m.gname == ''
                   for m in archive.getmembers())
    target = tmp_path / 'restored'
    assert backup.main(restore_args(vault, target)) == 0
    assert json.loads(capsys.readouterr().out) == dict(ok=True, files=2, bytes=result['bytes'])
    assert snapshot(target / source.name) == snapshot(source)
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    assert snapshot(home.root) == before


@pytest.mark.parametrize('answer', ['n', '', 'YES NO'], ids=['n', 'empty', 'mixed'])
def test_consent_denied(vault, capsys, monkeypatch, answer):
    monkeypatch.setattr(backup, '_answer', lambda: answer)
    assert backup.main(create_args(vault, yes=False)) == 3
    captured = capsys.readouterr()
    assert not vault[2].exists()
    assert 'Create encrypted backup? [y/N]' in captured.err
    assert 'source' not in captured.err and 'SENTINEL' not in captured.err
    assert str(vault[2]) in captured.err
    assert not captured.out


def test_restore_denied(vault, tmp_path, monkeypatch, capsys):
    assert backup.main(create_args(vault)) == 0
    capsys.readouterr()
    monkeypatch.setattr(backup, '_answer', lambda: 'n')
    target = tmp_path / 'restored'
    assert backup.main(restore_args(vault, target, yes=False)) == 3
    assert not target.exists()


@pytest.mark.parametrize('problem', ['existing', 'symlink', 'inside', 'permissions'], ids=['exists', 'link', 'inside', 'mode'])
def test_unsafe_output(vault, capsys, problem):
    _, source, output, _ = vault
    args = create_args(vault)
    if problem == 'existing':
        output.write_bytes(b'unchanged')
    elif problem == 'symlink':
        (source / 'link').symlink_to(source / 'run')
    elif problem == 'inside':
        args[args.index('--out') + 1] = str(source / 'backup.age')
    else:
        output.parent.chmod(0o755)
    assert backup.main(args) == 2
    result = json.loads(capsys.readouterr().out)
    assert result['ok'] is False
    assert str(source) not in result['error']
    assert not (source / 'backup.age').exists()
    if problem == 'existing':
        assert output.read_bytes() == b'unchanged'
    else:
        assert not output.exists()


def test_restore_existing_dir(vault, tmp_path, capsys):
    assert backup.main(create_args(vault)) == 0
    capsys.readouterr()
    target = tmp_path / 'existing'
    target.mkdir(mode=0o700)
    assert backup.main(restore_args(vault, target)) == 2
    assert not list(target.iterdir())


@pytest.mark.parametrize('name,kind', [('../escape', tarfile.REGTYPE), ('/escape', tarfile.REGTYPE),
                                      ('link', tarfile.SYMTYPE), ('hard', tarfile.LNKTYPE),
                                      ('device', tarfile.CHRTYPE), ('fifo', tarfile.FIFOTYPE)],
                         ids=['dotdot', 'abs', 'sym', 'hard', 'dev', 'fifo'])
def test_unsafe_tar_rejected(vault, tmp_path, name, kind):
    state = select_store(vault[0]).load()
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w') as archive:
        member = tarfile.TarInfo(name)
        member.type = kind
        member.linkname = '../escape' if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE) else ''
        archive.addfile(member)
    vault[2].write_bytes(encrypt(data.getvalue(), DeviceAgeIdentity.parse(state.control['age_identity']).recipient))
    target = tmp_path / 'restored'
    assert backup.main(restore_args(vault, target)) == 2
    assert not target.exists()
    assert not (tmp_path / 'escape').exists()


def test_missing_recovery_fails_closed(tmp_path):
    home, _, _, _ = setup_home(tmp_path)
    source = tmp_path / 'secret'
    source.write_bytes(b'secret')
    output = tmp_path / 'backup.age'
    assert backup.main(['create', '--home', str(home.root), '--include', str(source),
                        '--out', str(output), '--yes']) == 2
    assert not output.exists()


def test_module_cli(vault):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run([sys.executable, '-m', 'agentmesh_log.backup', *create_args(vault)],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['ok'] is True
    assert result.stderr == ''


@pytest.mark.parametrize('answer', ['y', 'yes', 'YES'], ids=['y', 'yes', 'upper'])
def test_explicit_consent(vault, monkeypatch, answer):
    monkeypatch.setattr(backup, '_answer', lambda: answer)
    assert backup.main(create_args(vault, yes=False)) == 0
    assert vault[2].exists()


@pytest.mark.parametrize('problem', ['missing_recovery', 'invalid_recovery', 'invalid_device'], ids=['norec', 'badrec', 'baddev'])
def test_invalid_recipients(vault, problem):
    store = select_store(vault[0])
    state = store.load()
    records = state.control['registry_records']
    prefix = 'registry/devices/' if problem == 'invalid_device' else 'registry/recovery/'
    key = next(key for key in records if key.startswith(prefix))
    if problem == 'missing_recovery':
        del records[key]
    else:
        field = 'age_recipient' if problem == 'invalid_device' else 'recovery_age_recipient'
        records[key][field] = 'invalid'
    store.save(state)
    assert backup.main(create_args(vault)) == 2
    assert not vault[2].exists()


def test_current_recovery_recipient(vault):
    from agentmesh_log.recovery import _record
    from agentmesh_log.registry_store import recovery_id

    store = select_store(vault[0])
    state = store.load()
    old = state.registry().current_recovery()
    replacement = RecoveryIdentity(os.urandom(32))
    record = _record(state.account.public_record(), replacement, vault[3]._private, recovery_id(old))
    state.control['registry_records']['registry/recovery/' + recovery_id(record) + '.json'] = record
    store.save(state)
    assert backup.main(create_args(vault)) == 0
    assert replacement.age.decrypt(vault[2].read_bytes())
    with pytest.raises(ValueError):
        vault[3].age.decrypt(vault[2].read_bytes())


@pytest.mark.parametrize('missing', ['home', 'index'], ids=['home', 'index'])
def test_home_is_never_initialized(vault, tmp_path, missing):
    args = create_args(vault)
    if missing == 'home':
        path = tmp_path / 'absent-home'
        args[args.index('--home') + 1] = str(path)
    else:
        path = vault[0].root / 'index'
        path.rmdir()
    assert backup.main(args) == 2
    assert not path.exists()
    assert not vault[2].exists()


def test_multiple_includes(vault, tmp_path):
    other = tmp_path / 'other'
    other.write_bytes(b'another file')
    assert backup.main(create_args(vault) + ['--include', str(other)]) == 0
    target = tmp_path / 'restored'
    assert backup.main(restore_args(vault, target)) == 0
    assert (target / 'other').read_bytes() == other.read_bytes()
    assert snapshot(target / 'source') == snapshot(vault[1])


def test_argument_errors_are_content_free(capsys):
    assert backup.main(['create', '--private-filename-secret']) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out) == dict(ok=False, error='invalid_arguments')
    assert captured.err == ''
