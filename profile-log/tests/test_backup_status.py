"""`backup create` records `last_backup` for the control plane's key inventory (M3b): when, how many files, how big."""
import json
import os
import pytest
from agentmesh_log import backup, status
from agentmesh_log.oskeystore import initialize
from broker.support import BrokerHome

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='backup tool is POSIX-only (macOS host)')


@pytest.fixture
def mesh(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENTMESH_ALLOW_FILE_KEYSTORE', '1')
    root = tmp_path / 'mesh'; root.mkdir(mode=0o700); monkeypatch.setenv('AGENTMESH_HOME', str(root))
    home = BrokerHome(root / 'profile-log'); initialize(home, ['work'], file_keystore=True)
    source = tmp_path / 'source'; source.mkdir(mode=0o700); (source / 'a').write_bytes(b'SENTINEL-a'); (source / 'b').write_bytes(b'bb')
    out = tmp_path / 'backups'; out.mkdir(mode=0o700)
    return root, home, source, out / 'backup.age'


def create(home, source, output, *extra):
    return backup.main(['create', '--home', str(home), '--out', str(output), '--include', str(source), *extra])


def last(root):
    path = root / 'profile-log-status.json'
    return json.loads(path.read_text()).get('last_backup') if path.exists() else None


def test_success_recorded(mesh, capsys):
    root, home, source, output = mesh
    assert create(home.root, source, output, '--yes') == 0
    value = json.loads((root / 'profile-log-status.json').read_text())
    assert set(value['last_backup']) == {'at', 'files', 'bytes'}
    assert value['last_backup']['files'] == 2 and value['last_backup']['bytes'] == 12 and status.valid(value)
    raw = (root / 'profile-log-status.json').read_text()
    assert str(output) not in raw and str(source) not in raw and 'backup.age' not in raw and 'SENTINEL' not in raw


def test_declined_not_recorded(mesh, monkeypatch, capsys):
    root, home, source, output = mesh
    monkeypatch.setattr(backup, '_answer', lambda: 'n')
    assert create(home.root, source, output) == 3 and last(root) is None


def test_failure_not_recorded(mesh, capsys):
    root, home, source, output = mesh
    output.write_bytes(b'already here')
    assert create(home.root, source, output, '--yes') == 2 and last(root) is None


def test_restore_not_recorded(mesh, tmp_path, capsys):
    root, home, source, output = mesh
    assert create(home.root, source, output, '--yes') == 0
    first = last(root)
    assert backup.main(['restore', '--home', str(home.root), '--in', str(output), '--to', str(tmp_path / 'r'), '--yes']) == 0
    assert last(root) == first


def test_other_home_not_recorded(mesh, tmp_path, capsys):
    # A backup of some other broker home must never claim to be this AgentMesh home's last backup.
    root, _, source, output = mesh
    other = BrokerHome(tmp_path / 'other'); initialize(other, ['work'], file_keystore=True)
    assert create(other.root, source, output, '--yes') == 0 and last(root) is None


@pytest.mark.parametrize('value', [
    dict(at='yesterday', files=1, bytes=1), dict(at=status.timestamp(), files=-1, bytes=1),
    dict(at=status.timestamp(), files=1, bytes='1'), dict(at=status.timestamp(), files=1, bytes=1, out='/x'),
], ids=['at_text', 'negative', 'bytes_str', 'extra'])
def test_refused(value):
    doc = lambda v: dict(format=status.FORMAT, updated_at=status.timestamp(), last_backup=v)
    assert status.valid(doc(dict(at=status.timestamp(), files=1, bytes=1))) and not status.valid(doc(value))
