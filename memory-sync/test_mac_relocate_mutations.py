"""Dedicated regressions for the reviewer's six surviving guard removals."""
from pathlib import Path

import pytest

from test_mac_relocate import installation, snapshot
from test_mac_relocate_lifecycle import full_install, move


def assert_restored(options, config, args, original_runtime, result, stage):
    assert result['status'] == 'rolled_back', result
    assert result['failed_step'] == stage
    assert args['runtime'].read_bytes() == original_runtime
    assert options['supervisor'].inspect('org.example.sync')['arguments'][:4] == [
        config['from']['binary'], 'worker-run', '--runtime', config['from']['runtime']]
    assert options['transport'].exchange == config['from']['exchange']
    assert all(options['supervisor'].inspect(job['label'])['loaded']
               for job in config['jobs']['plists'])


def test_destination_integrity_refusal_restores_old_install(full_install, monkeypatch):
    import mac_relocate
    options, config, args = full_install
    original_runtime = args['runtime'].read_bytes()
    destination = Path(config['to_root']) / 'memory-sync/data/mac.db'
    connect = mac_relocate.sqlite3.connect
    checked = []

    class BadIntegrity:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def execute(self, sql, *parameters):
            if sql == 'PRAGMA integrity_check':
                checked.append(True)
                return self.connection.execute("SELECT 'synthetic integrity failure'")
            return self.connection.execute(sql, *parameters)

    def connect_with_bad_destination(database, *a, **kw):
        connection = connect(database, *a, **kw)
        if str(database) == destination.as_uri() + '?mode=ro':
            return BadIntegrity(connection)
        return connection

    monkeypatch.setattr(mac_relocate.sqlite3, 'connect', connect_with_bad_destination)
    result = move(options, monkeypatch)
    assert_restored(options, config, args, original_runtime, result, 'copy db')
    assert checked
    assert result['error'] == 'ValueError'


def test_copy_tree_refuses_nonempty_destination_before_copy(tmp_path):
    import mac_relocate
    source, destination = tmp_path / 'source', tmp_path / 'destination'
    source.mkdir(mode=0o700)
    destination.mkdir(mode=0o700)
    (source / 'new.txt').write_text('synthetic source', encoding='utf-8')
    (destination / 'existing.txt').write_text('must remain untouched', encoding='utf-8')
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match='copy destination must be absent or empty'):
        mac_relocate.copy_tree(source, destination)
    assert snapshot(tmp_path) == before


def test_final_old_app_scan_refuses_stale_rule_and_restores(full_install, monkeypatch):
    import mac_relocate
    options, config, args = full_install
    original_runtime = args['runtime'].read_bytes()
    original_rule = Path(config['rules'][0]).read_bytes()
    prepare = mac_relocate._prepare

    def stale_rule_mapping(*a, **kw):
        prepared = prepare(*a, **kw)
        # Other (longer) data/identity mappings remain correct. Only the old
        # app-prefix reference in the rule's agentmesh.py argv survives.
        prepared['mapping'][str(args['app_root'])] = str(args['app_root'])
        return prepared

    monkeypatch.setattr(mac_relocate, '_prepare', stale_rule_mapping)
    result = move(options, monkeypatch)
    assert_restored(options, config, args, original_runtime, result,
                    'rewrite plists/configs/rules/settings.local')
    assert result['error'] == 'ValueError'
    assert Path(config['rules'][0]).read_bytes() == original_rule


def test_post_adopt_key_id_recheck_refuses_changed_scope_and_restores(full_install, monkeypatch):
    import mac_relocate
    options, config, args = full_install
    original_runtime = args['runtime'].read_bytes()
    new_runtime = Path(config['to_root']) / 'memory-sync/data/runtime.json'
    load = mac_relocate.load
    checked = []

    def changed_scope(runtime, *a, **kw):
        value, scope = load(runtime, *a, **kw)
        if Path(runtime) == new_runtime:
            checked.append(True)
            return value, {**scope, 'key_id': 'synthetic-different-key-id'}
        return value, scope

    monkeypatch.setattr(mac_relocate, 'load', changed_scope)
    result = move(options, monkeypatch)
    assert_restored(options, config, args, original_runtime, result, 'rebind runtime (adopt)')
    assert checked
    assert result['error'] == 'ValueError'


def test_exchange_wait_after_resume_failure_restores_old_folder(full_install, monkeypatch):
    import mac_relocate
    options, config, args = full_install
    original_runtime = args['runtime'].read_bytes()
    new_exchange = str(Path(config['to_root']) / 'exchange')
    events = []
    set_path, resume, wait_idle = (mac_relocate.Syncthing.set_path,
                                  mac_relocate.Syncthing.resume,
                                  mac_relocate.Syncthing.wait_idle)

    def tracked_path(self, destination):
        events.append(('path', str(destination)))
        return set_path(self, destination)

    def tracked_resume(self):
        events.append(('resume', options['transport'].exchange))
        return resume(self)

    def failing_wait(self, **kw):
        current = options['transport'].exchange
        events.append(('wait', current))
        if current == new_exchange:
            raise TimeoutError('synthetic folder did not become idle')
        return wait_idle(self, **kw)

    monkeypatch.setattr(mac_relocate.Syncthing, 'set_path', tracked_path)
    monkeypatch.setattr(mac_relocate.Syncthing, 'resume', tracked_resume)
    monkeypatch.setattr(mac_relocate.Syncthing, 'wait_idle', failing_wait)
    result = move(options, monkeypatch)
    assert_restored(options, config, args, original_runtime, result, 'syncthing path + resume')
    assert result['error'] == 'TimeoutError'
    assert events == [(op, folder) for folder in (new_exchange, config['from']['exchange'])
                      for op in ('path', 'resume', 'wait')]


@pytest.mark.parametrize('changed', ['hashes', 'scope'])
def test_fresh_post_confirmation_prepare_mismatch_refuses_before_drain(
        full_install, monkeypatch, tmp_path, changed):
    import mac_relocate
    options, _, _ = full_install
    before = snapshot(tmp_path)
    prepare = mac_relocate._prepare
    calls = []

    def changed_fresh_prepare(*a, **kw):
        prepared = prepare(*a, **kw)
        calls.append(True)
        if len(calls) == 2:
            prepared[changed] = {**prepared[changed], 'synthetic-change': 'changed-after-confirmation'}
        return prepared

    monkeypatch.setattr(mac_relocate, '_prepare', changed_fresh_prepare)
    with pytest.raises(ValueError, match='relocation inputs changed during confirmation'):
        move(options, monkeypatch)
    assert len(calls) == 2
    assert options['supervisor'].calls == []
    assert snapshot(tmp_path) == before
