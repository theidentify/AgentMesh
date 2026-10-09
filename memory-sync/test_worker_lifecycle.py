"""Managed lifecycle exercises real SQLite cycles and real child processes."""
from contextlib import closing
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest
from test_install_adopt import existing


def bound(tmp_path, monkeypatch, *, identity=False):
    from install_adopt import adopt
    args = existing(tmp_path)
    if identity:
        from signed_packets import init_identity
        init_identity(args['security_dir'], '00000000-0000-4000-8000-000000000001', 'mac')
    monkeypatch.setattr('sys.stdin', io.StringIO('BIND\n'))
    adopt(**args)
    return args


def test_managed_cycle_preserves_legacy_despite_pending_identity(tmp_path, monkeypatch):
    import worker_lifecycle
    args = bound(tmp_path, monkeypatch, identity=True)
    runtime_bytes = args['runtime'].read_bytes()
    assert worker_lifecycle.run(args['runtime'], once=True, legacy_drained=True) == 0
    report = json.loads((args['exchange'] / 'status/mac.json').read_text())
    assert report['sync']['invalid'] == 0
    assert report['security']['policy'] == 'legacy'
    assert args['runtime'].read_bytes() == runtime_bytes
    with closing(sqlite3.connect(args['database'])) as c:
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='_sync_security'").fetchone()


def test_start_exclusive_stop_restart_and_readonly_status(tmp_path, monkeypatch):
    import worker_lifecycle as worker
    args = bound(tmp_path, monkeypatch, identity=True)
    runtime = args['runtime']
    before = set(tmp_path.rglob('*'))
    assert worker.status(runtime)['state'] == 'stopped'
    assert set(tmp_path.rglob('*')) == before
    first = worker.start(runtime, interval=0.1, legacy_drained=True, timeout=30)
    try:
        assert first['state'] == 'running'
        assert first['cycles'] >= 1
        with pytest.raises((ValueError, TimeoutError), match='already|drain'):
            worker.start(runtime, interval=0.1, legacy_drained=True, timeout=5)
        stopped = worker.stop(runtime, timeout=30)
        assert stopped['state'] == 'stopped'
        second = worker.start(runtime, interval=0.1, legacy_drained=True, timeout=30)
        assert second['nonce'] != first['nonce']
        assert second['pid'] != first['pid']
    finally:
        worker.stop(runtime, timeout=30)
    assert json.loads(runtime.read_text())['policy'] == 'legacy'


@pytest.mark.parametrize('interval', [0, -1, float('nan'), float('inf')])
def test_invalid_interval_refused_before_cycle(tmp_path, monkeypatch, interval):
    import worker_lifecycle as worker
    args = bound(tmp_path, monkeypatch)
    before = args['database'].read_bytes()
    with pytest.raises(ValueError, match='finite positive'):
        worker.run(args['runtime'], once=True, interval=interval, legacy_drained=True)
    assert args['database'].read_bytes() == before


def test_scope_rechecked_after_cycle_lock_before_any_mutation(tmp_path, monkeypatch):
    import worker_lifecycle as worker
    import sync_worker
    args = bound(tmp_path, monkeypatch)
    original = sync_worker.run_once
    def race(*a, **kw):
        config = json.loads(args['runtime'].read_text())
        config['node'] = 'windows'
        args['runtime'].write_text(json.dumps(config))
        return original(*a, **kw)
    monkeypatch.setattr(sync_worker, 'run_once', race)
    assert worker.run(args['runtime'], once=True, legacy_drained=True) == 1
    assert not (args['exchange'] / 'status/mac.json').exists()


def test_custom_pending_wizard_status_preserved_without_activation(tmp_path, monkeypatch):
    import worker_lifecycle as worker
    import security_wizard
    args = bound(tmp_path, monkeypatch, identity=True)
    security_wizard.resume(args['database'], args['exchange'], args['security_dir'], args['security_state'])
    before = args['security_state'].read_bytes()
    assert worker.run(args['runtime'], once=True, legacy_drained=True) == 0
    security = json.loads((args['exchange'] / 'status/mac.json').read_text())['security']
    assert security['pairing'] == 'pending'
    assert security['wizard_step'] != 'active'
    assert args['security_state'].read_bytes() == before


def strict_bound(tmp_path, monkeypatch):
    from install_adopt import adopt
    from signed_packets import init_identity, Security
    import memory_sync
    args = existing(tmp_path)
    init_identity(args['security_dir'], '00000000-0000-4000-8000-000000000001', 'mac')
    with memory_sync.connect(args['database']) as c, c:
        Security(args['security_dir']).guard(c, args['exchange'])
    monkeypatch.setattr('sys.stdin', io.StringIO('BIND\n'))
    adopt(**args)
    return args


@pytest.mark.parametrize('damage', ['missing', 'revoked', 'binding'])
def test_strict_invalid_identity_fails_before_any_cycle_write(tmp_path, monkeypatch, damage):
    import worker_lifecycle as worker
    from signed_packets import Security, revoke
    args = strict_bound(tmp_path, monkeypatch)
    if damage == 'missing':
        (args['security_dir'] / 'identity.json').unlink()
    elif damage == 'revoked':
        revoke(args['security_dir'], Security(args['security_dir']).public['key_id'])
    else:
        with closing(sqlite3.connect(args['database'])) as c, c:
            c.execute("UPDATE _sync_security SET sender='00000000-0000-4000-8000-000000000002'")
    before = args['database'].read_bytes()
    with pytest.raises((ValueError, FileNotFoundError)):
        worker.run(args['runtime'], once=True)
    assert args['database'].read_bytes() == before
    assert not (args['exchange'] / 'status').exists()
    assert not (args['database'].parent / '.agentmesh-worker').exists()


def test_owned_worker_can_stop_after_key_revocation(tmp_path, monkeypatch):
    import worker_lifecycle as worker
    from signed_packets import Security, revoke
    args = strict_bound(tmp_path, monkeypatch)
    worker.start(args['runtime'], interval=60, timeout=30)
    try:
        revoke(args['security_dir'], Security(args['security_dir']).public['key_id'])
        assert worker.stop(args['runtime'], timeout=30)['state'] == 'stopped'
    finally:
        # A failed test must not leave the owned disposable process behind.
        trust = json.loads((args['security_dir'] / 'trust.json').read_text())
        for peer in trust['peers'].values():
            peer['revoked'] = False
        from signed_packets import write_local
        write_local(args['security_dir'] / 'trust.json', trust)
        worker.stop(args['runtime'], timeout=30)


def test_ota_fence_blocks_unmanaged_runtime_cycle(tmp_path, monkeypatch):
    import worker_lifecycle as worker
    from signed_packets import write_local
    args = bound(tmp_path, monkeypatch)
    write_local(args['app_root'] / 'ota-state.json', {'active': 1})
    assert worker.run(args['runtime'], once=True, legacy_drained=True) == 1
    assert not (args['exchange'] / 'status').exists()
