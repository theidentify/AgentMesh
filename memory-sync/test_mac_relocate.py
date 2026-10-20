"""Relocation rehearsals use disposable installations and injected boundaries."""
import json
import os
from pathlib import Path
import plistlib
import stat

import pytest

from signed_packets import init_identity, write_local
from test_install_adopt import existing


class Supervisor:
    def __init__(self, plist):
        self.plist = plist
        self.calls = []

    def inspect(self, label):
        return dict(loaded=True, pid=None,
                    arguments=plistlib.loads(self.plist.read_bytes())['ProgramArguments'])

    def drain(self, label, timeout):
        self.calls.append(('drain', label))


class Transport:
    def __init__(self, exchange):
        self.exchange = str(exchange)
        self.calls = []
        self.paused = False

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, body))
        if url.endswith('/rest/system/ping'):
            return {'ping': 'pong'}
        if '/rest/config/folders/' in url:
            if method == 'PATCH':
                value = json.loads(body)
                self.exchange = value.get('path', self.exchange)
                self.paused = value.get('paused', self.paused)
                return None
            return dict(id='synthetic-folder', path=self.exchange, paused=self.paused)
        if '/rest/db/status?' in url:
            return dict(state='idle', needFiles=0, errors=0, pullErrors=0)
        raise AssertionError('unexpected request')


@pytest.fixture
def installation(tmp_path):
    args = existing(tmp_path / 'old-memory')
    init_identity(args['security_dir'], '00000000-0000-4000-8000-000000000001', 'mac')
    write_local(args['security_state'], {'database': str(args['database'])})
    write_local(args['runtime'], {k: str(v) for k, v in args.items() if k != 'runtime'})
    binary = tmp_path / 'agentmesh'
    binary.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
    binary.chmod(0o700)
    plist = tmp_path / 'sync.plist'
    plist.write_bytes(plistlib.dumps(dict(Label='org.example.sync',
        ProgramArguments=[str(binary), 'worker-run', '--runtime', str(args['runtime']),
                          '--interval', '60', '--legacy-drained'])))
    root = tmp_path / 'destination'
    root.mkdir(mode=0o700)
    write_local(root / 'settings.local.json', {'bridge': {'database': str(args['database'])}})
    (root / 'profile-log').mkdir(mode=0o700)
    write_local(root / 'profile-log' / 'preserve.json', {'synthetic': True})
    script = tmp_path / 'ingest.py'
    script.write_text('# synthetic job\n', encoding='utf-8')
    requirements = tmp_path / 'requirements.txt'
    requirements.write_text('cryptography==44.0.0\n', encoding='utf-8')
    production = tmp_path / 'production.json'
    write_local(production, {'runtime': str(args['runtime'])})
    status = tmp_path / 'production-status'
    status.mkdir(mode=0o700)
    rule = tmp_path / 'recall.command.json'
    write_local(rule, {'argv': [str(binary), str(args['app_root'] / 'recall_memory.py')]})
    xml = tmp_path / 'syncthing.xml'
    xml.write_text('<configuration><gui><address>127.0.0.1:8384</address>'
                   '<apikey>SYNTHETIC-API-SECRET</apikey></gui></configuration>', encoding='utf-8')
    xml.chmod(0o600)
    manifest = dict(format='agentmesh-relocation-v1',
        **{'from': {k: str(v) for k, v in dict(runtime=args['runtime'], plist=plist,
                                              binary=binary, exchange=args['exchange']).items()}},
        to_root=str(root), syncthing=dict(config=str(xml), folder_id='synthetic-folder'),
        jobs=dict(plists=[], files={str(script): 'ingest.py'}, production_config=str(production),
                  status_dir=str(status), venv_requirements=str(requirements)),
        rules=[str(rule)], archive=[], settings_local=str(root / 'settings.local.json'))
    path = tmp_path / 'manifest.json'
    write_local(path, manifest)
    return path, manifest, args, Supervisor(plist), Transport(args['exchange'])


def snapshot(root):
    import hashlib
    return {str(p.relative_to(root)): (stat.S_IMODE(p.stat().st_mode), p.stat().st_mtime_ns,
            hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None)
            for p in root.rglob('*')}


def test_populated_root_and_dry_run_are_readonly(installation, tmp_path):
    import mac_relocate
    path, manifest, args, supervisor, transport = installation
    before = snapshot(tmp_path)
    result = mac_relocate._read(path, supervisor, transport)[-1]
    assert result['status'] == 'planned'
    assert result['destinations']['exchange'] == str(Path(manifest['to_root']) / 'exchange')
    assert result['changes']
    assert snapshot(tmp_path) == before
    assert supervisor.calls == []
    assert 'SYNTHETIC-API-SECRET' not in json.dumps(result)
    assert [call[0] for call in transport.calls] == ['GET', 'GET']


@pytest.mark.parametrize('destination', ['memory-sync', 'exchange', 'versions', 'jobs', 'status', 'backups/archive'])
@pytest.mark.parametrize('damage', ['nonempty', 'symlink', 'mode'])
def test_destination_refusals_before_drain(installation, destination, damage):
    import mac_relocate
    path, manifest, args, supervisor, transport = installation
    target = Path(manifest['to_root']) / destination
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if damage == 'symlink':
        target.symlink_to(args['exchange'], target_is_directory=True)
    else:
        target.mkdir(mode=0o700)
        if damage == 'nonempty':
            (target / 'keep').write_text('preserve', encoding='utf-8')
        else:
            target.chmod(0o770)
    with pytest.raises(ValueError):
        mac_relocate.plan(path, supervisor=supervisor, transport=transport)
    assert supervisor.calls == []


@pytest.mark.parametrize('damage', ['unknown', 'relative', 'control', 'manifest_mode', 'live_args', 'exchange'])
def test_manifest_and_live_binding_refusals(installation, damage):
    import mac_relocate
    path, manifest, args, supervisor, transport = installation
    if damage == 'unknown':
        manifest['unknown'] = True
    elif damage == 'relative':
        manifest['to_root'] = 'relative'
    elif damage == 'control':
        manifest['to_root'] += '\n'
    elif damage == 'live_args':
        supervisor.inspect = lambda _: dict(loaded=True, pid=None, arguments=['foreign'])
    elif damage == 'exchange':
        transport.exchange = '/wrong/exchange'
    write_local(path, manifest)
    if damage == 'manifest_mode':
        path.chmod(0o644)
    with pytest.raises(ValueError):
        mac_relocate.plan(path, supervisor=supervisor, transport=transport)
    assert not supervisor.calls


def test_space_and_unreachable_refuse_without_drain(installation, monkeypatch):
    import mac_relocate
    path, manifest, args, supervisor, transport = installation
    monkeypatch.setattr(mac_relocate.shutil, 'disk_usage', lambda _: type('Usage', (), {'free': 0})())
    with pytest.raises(ValueError, match='space'):
        mac_relocate.plan(path, supervisor=supervisor, transport=transport)
    assert not supervisor.calls


def test_syncthing_unreachable_is_redacted(installation, capsys):
    import mac_relocate
    path, manifest, args, supervisor, _ = installation
    def unreachable(*args):
        raise OSError('SYNTHETIC-API-SECRET')
    with pytest.raises(ValueError, match='Syncthing unavailable') as caught:
        mac_relocate.plan(path, supervisor=supervisor, transport=unreachable)
    assert 'SYNTHETIC-API-SECRET' not in str(caught.value)
    assert 'SYNTHETIC-API-SECRET' not in str(capsys.readouterr())
    assert not supervisor.calls


def test_copy_freeze_blocks_noncooperating_writer_and_preserves_counts(installation, tmp_path):
    import sqlite3
    from contextlib import closing
    import mac_relocate
    _, _, args, _, _ = installation
    target = tmp_path / 'copy.db'
    with mac_relocate.freeze(args['database'], timeout=1):
        with closing(sqlite3.connect(args['database'], timeout=0)) as writer:
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                writer.execute("INSERT INTO memory_items(id,kind,scope,content,metadata) "
                               "VALUES(1,'fact','global','synthetic','{}')")
        counts = mac_relocate.copy_database(args['database'], target)
    assert counts == mac_relocate.table_counts(target)
    with closing(sqlite3.connect(target)) as copy:
        assert copy.execute('PRAGMA integrity_check').fetchall() == [('ok',)]


def test_copy_freeze_takes_both_job_locks(installation):
    import bounded_digest
    import mac_relocate
    from bounded_protocol import ValidationError
    _, _, args, _, _ = installation
    for suffix in ('', '.scheduler'):
        with bounded_digest.run_lock(str(args['database']) + suffix):
            with pytest.raises(ValidationError, match='overlap'):
                with mac_relocate.freeze(args['database'], timeout=0.01):
                    pytest.fail('copy entered with active job')


def test_database_copy_rejects_mutation(installation, tmp_path, monkeypatch):
    import mac_relocate
    import sqlite3
    from contextlib import closing
    _, _, args, _, _ = installation
    original = mac_relocate.sqlite_backup
    def damaged(source, target):
        original(source, target)
        with closing(sqlite3.connect(target)) as db, db:
            db.execute('DELETE FROM _sync_config')
    monkeypatch.setattr(mac_relocate, 'sqlite_backup', damaged)
    with pytest.raises(ValueError, match='counts'):
        mac_relocate.copy_database(args['database'], tmp_path / 'copy.db')


def test_tree_copy_preserves_identity_state_modes_and_empty_directories(installation, tmp_path):
    import mac_relocate
    _, _, args, _, _ = installation
    target = tmp_path / 'identity-copy'
    mac_relocate.copy_tree(args['security_dir'], target)
    assert mac_relocate.inventory(args['security_dir']) == mac_relocate.inventory(target)
    assert target.stat().st_mode & 0o777 == 0o700


def test_tree_copy_detects_corruption(installation, tmp_path, monkeypatch):
    import mac_relocate
    _, _, args, _, _ = installation
    original = mac_relocate.shutil.copyfile
    def corrupt(source, target, **kwargs):
        original(source, target, **kwargs)
        Path(target).write_bytes(b'corrupt synthetic copy')
    monkeypatch.setattr(mac_relocate.shutil, 'copyfile', corrupt)
    with pytest.raises(ValueError, match='copy verification'):
        mac_relocate.copy_tree(args['security_dir'], tmp_path / 'damaged')


def test_syncthing_exact_pause_move_resume_idle_and_restore_calls(installation):
    import mac_relocate
    _, manifest, args, _, transport = installation
    syncthing = mac_relocate.Syncthing(**manifest['syncthing'], transport=transport)
    syncthing.pause()
    new = str(Path(manifest['to_root']) / 'exchange')
    syncthing.set_path(new)
    syncthing.resume()
    syncthing.wait_idle(timeout=1)
    syncthing.pause()
    syncthing.set_path(str(args['exchange']))
    syncthing.resume()
    assert [(method, json.loads(body)) for method, _, _, body in transport.calls if body] == [
        ('PATCH', {'paused': True}), ('PATCH', {'path': new}), ('PATCH', {'paused': False}),
        ('PATCH', {'paused': True}), ('PATCH', {'path': str(args['exchange'])}), ('PATCH', {'paused': False})]
    assert all(headers['X-API-Key'] == 'SYNTHETIC-API-SECRET' for _, _, headers, _ in transport.calls)
    assert all('SYNTHETIC-API-SECRET' not in url for _, url, _, _ in transport.calls)
    assert all('synthetic-folder' in url for _, url, _, _ in transport.calls)


@pytest.mark.parametrize('status', [
    dict(state='syncing', needFiles=0, errors=0, pullErrors=0),
    dict(state='idle', needFiles=1, errors=0, pullErrors=0),
    dict(state='idle', needFiles=0, errors=1, pullErrors=0),
    dict(state='idle', needFiles=0, errors=0, pullErrors=1),
])
def test_syncthing_wait_requires_all_idle_conditions(installation, status):
    import mac_relocate
    _, manifest, _, _, _ = installation
    syncthing = mac_relocate.Syncthing(**manifest['syncthing'], transport=lambda *args: status)
    with pytest.raises(TimeoutError):
        syncthing.wait_idle(timeout=0.001)
