"""Adversarial preflight, atomic publication, WAL and recovery checks."""
from contextlib import closing
import ast
import io
import json
import os
from pathlib import Path
import sqlite3

import pytest

from signed_packets import write_local
from test_mac_relocate import installation, snapshot
from test_mac_relocate_lifecycle import full_install, move


def test_archive_hardlink_refused_before_drain(full_install):
    import mac_relocate
    options, config, args = full_install
    sentinel = Path(config['to_root']) / 'protected.txt'
    sentinel.write_text('synthetic protected entry', encoding='utf-8')
    sentinel.chmod(0o600)
    os.link(sentinel, args['app_root'] / 'alias.txt')
    before = (sentinel.read_bytes(), sentinel.stat().st_mode)
    with pytest.raises(ValueError, match='hardlink'):
        mac_relocate.relocate(**options, dry_run=True)
    assert not options['supervisor'].calls
    assert (sentinel.read_bytes(), sentinel.stat().st_mode) == before


def test_rollback_refuses_archive_symlink_before_chmod(full_install, monkeypatch):
    import mac_relocate
    options, config, args = full_install
    result = move(options, monkeypatch)
    record = json.loads((Path(result['backup']) / 'relocation.json').read_text(encoding='utf-8'))
    archived_app = Path(next(a['target'] for a in record['archives'] if a['source'] == str(args['app_root'])))
    sentinel = Path(config['to_root']) / 'protected.txt'
    sentinel.write_text('synthetic protected entry', encoding='utf-8')
    sentinel.chmod(0o400)
    archived_app.chmod(0o700)
    (archived_app / 'alias.txt').symlink_to(sentinel)
    before = (sentinel.read_bytes(), sentinel.stat().st_mode)
    calls = list(options['supervisor'].calls)
    monkeypatch.setattr('sys.stdin', io.StringIO('ROLLBACK\n'))
    with pytest.raises(ValueError, match='symlink'):
        mac_relocate.rollback(result['backup'], supervisor=options['supervisor'], transport=options['transport'])
    assert options['supervisor'].calls == calls
    assert (sentinel.read_bytes(), sentinel.stat().st_mode) == before


def test_explicit_rollback_keeps_inactive_history_readonly(full_install, monkeypatch):
    import mac_relocate
    options, config, args = full_install
    history = args['app_root'].parent / 'historical-analysis.txt'
    history.write_text('synthetic history', encoding='utf-8')
    history.chmod(0o600)
    config['archive'].append(str(history))
    write_local(options['manifest'], config)
    result = move(options, monkeypatch)
    monkeypatch.setattr('sys.stdin', io.StringIO('ROLLBACK\n'))
    mac_relocate.rollback(result['backup'], supervisor=options['supervisor'], transport=options['transport'])
    archived = next((Path(config['to_root']) / 'backups/archive').glob('historical-analysis.txt.*'))
    assert archived.read_text(encoding='utf-8') == 'synthetic history'
    assert not archived.stat().st_mode & 0o222


def test_wrong_owner_and_symlinked_parent_refused(installation, monkeypatch, tmp_path):
    import mac_relocate
    manifest, config, _, supervisor, transport = installation
    target = Path(config['to_root']) / 'jobs'
    target.mkdir(mode=0o700)
    real = Path.stat
    def stat(path, **kwargs):
        value = real(path, **kwargs)
        if path == target:
            values = list(value)
            values[4] = os.getuid() + 1
            return os.stat_result(values)
        return value
    monkeypatch.setattr(Path, 'stat', stat)
    with pytest.raises(ValueError, match='owned'):
        mac_relocate.plan(manifest, supervisor=supervisor, transport=transport)
    assert supervisor.calls == []
    monkeypatch.setattr(Path, 'stat', real)
    target.rmdir()
    (Path(config['to_root']) / 'backups').symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        mac_relocate.plan(manifest, supervisor=supervisor, transport=transport)


def test_exchange_containing_target_is_refused(installation):
    import mac_relocate
    manifest, config, args, supervisor, transport = installation
    config['to_root'] = str(args['exchange'] / 'nested-target')
    write_local(manifest, config)
    with pytest.raises(ValueError, match='overlap'):
        mac_relocate.plan(manifest, supervisor=supervisor, transport=transport)
    assert not supervisor.calls


def test_wal_copy_includes_committed_uncheckpointed_rows(installation, tmp_path):
    import mac_relocate
    _, _, args, _, _ = installation
    with closing(sqlite3.connect(args['database'])) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute("INSERT INTO memory_items(id,kind,scope,content) VALUES(7,'fact','global','WAL synthetic')")
        writer.commit()
        with mac_relocate.freeze(args['database'], timeout=1):
            mac_relocate.copy_database(args['database'], tmp_path / 'wal-copy.db')
        with closing(sqlite3.connect(tmp_path / 'wal-copy.db')) as db:
            assert db.execute('SELECT count(*) FROM memory_items WHERE id=7').fetchone() == (1,)


@pytest.mark.parametrize('damage', ['identity', 'rule', 'destination'])
def test_confirmation_race_refuses_before_drain(full_install, monkeypatch, damage):
    import mac_relocate
    options, config, args = full_install
    class Input:
        def readline(self):
            if damage == 'destination':
                (Path(config['to_root']) / 'jobs').mkdir(mode=0o700)
                (Path(config['to_root']) / 'jobs/foreign').write_bytes(b'foreign')
            elif damage == 'rule':
                Path(config['rules'][0]).write_bytes(b'changed synthetic rule')
            else:
                (args['security_dir'] / 'trust.json').write_bytes(b'changed synthetic identity')
            return 'RELOCATE\n'
    monkeypatch.setattr('sys.stdin', Input())
    with pytest.raises(ValueError):
        mac_relocate.relocate(**options)
    assert not options['supervisor'].calls


def test_failure_between_new_and_replace_restores_all_files(full_install, monkeypatch):
    import mac_relocate
    options, config, args = full_install
    original = Path(config['from']['plist']).read_bytes()
    real = mac_relocate.os.replace
    hit = []
    def fail(source, target):
        if str(source).endswith('.new') and not hit:
            hit.append(True)
            raise OSError('synthetic swap failure')
        return real(source, target)
    monkeypatch.setattr(mac_relocate.os, 'replace', fail)
    result = move(options, monkeypatch)
    assert result['status'] == 'rolled_back'
    assert Path(config['from']['plist']).read_bytes() == original
    assert not Path(config['from']['plist'] + '.new').exists()


def test_lock_failure_restarts_services_unchanged(full_install, monkeypatch):
    from bounded_digest import run_lock
    options, config, args = full_install
    with run_lock(str(args['database']) + '.scheduler'):
        result = move(options, monkeypatch, timeout=0.01)
    assert result['status'] == 'rolled_back'
    assert result['failed_step'] == 'lock jobs'
    assert options['supervisor'].inspect('org.example.sync')['arguments'][0] == config['from']['binary']


def test_health_failure_restores_old_service(full_install, monkeypatch):
    import mac_relocate
    options, config, _ = full_install
    def unhealthy(*args, **kwargs):
        raise TimeoutError('no completed cycle')
    monkeypatch.setattr(mac_relocate, 'wait_health', unhealthy)
    result = move(options, monkeypatch)
    assert result['status'] == 'rolled_back'
    assert result['failed_step'] == 'start'
    assert options['supervisor'].inspect('org.example.sync')['arguments'][0] == config['from']['binary']


def test_render_matches_apply_field_for_field(full_install):
    import mac_relocate
    options, config, args = full_install
    # Execute the actual pure rendering function, without importing optional
    # broker crypto dependencies or touching a keychain.
    filename = Path(__file__).resolve().parents[1] / 'profile-log/agentmesh_log/apply.py'
    source = ast.parse(filename.read_text(encoding='utf-8'))
    function = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == 'render_bridge')
    namespace = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(filename), 'exec'), namespace)
    bridge = json.loads(Path(config['settings_local']).read_text(encoding='utf-8'))['bridge']
    assert mac_relocate.render_bridge(config, {k: str(v) for k, v in args.items()}) == namespace['render_bridge'](Path(config['to_root']), bridge)


def test_offline_venv_commands_use_pins_and_no_network(tmp_path, monkeypatch):
    import mac_relocate
    calls = []
    target = tmp_path / 'venv'
    target.mkdir(mode=0o700)
    monkeypatch.setattr(mac_relocate, 'execute', lambda argv, **kw: calls.append(argv))
    mac_relocate.make_venv(target, tmp_path / 'requirements.txt', timeout=1, python='/synthetic/python')
    assert calls[0] == ['/synthetic/python', '-m', 'venv', '--copies', str(target)]
    assert '--no-index' in calls[1] and '--no-deps' in calls[1]
    assert calls[1][-2:] == ['-r', str(tmp_path / 'requirements.txt')]
    assert calls[2][-2:] == ['pip', 'check']


@pytest.mark.parametrize('address', ['0.0.0.0:8384', 'https://example.com:8384', 'http://user:password@127.0.0.1:8384'])
def test_syncthing_nonloopback_and_credentials_refused(installation, address):
    import mac_relocate
    manifest, config, _, supervisor, transport = installation
    path = Path(config['syncthing']['config'])
    path.write_text('<configuration><gui><address>' + address + '</address><apikey>synthetic</apikey></gui></configuration>', encoding='utf-8')
    with pytest.raises(ValueError, match='loopback'):
        mac_relocate.plan(manifest, supervisor=supervisor, transport=transport)
    assert not transport.calls
