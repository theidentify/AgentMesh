"""Read-only inspection of an existing installation; never creates or updates it."""
import json
from pathlib import Path
import sqlite3

import pytest

import install_inspect
import memory_sync
import sqlite_memory


def fixture_install(tmp_path):
    local = tmp_path / 'local'
    data = local / 'data'
    data.mkdir(parents=True)
    db = data / 'mac.db'
    sqlite_memory.init_database(db)
    memory_sync.initialize(db, 'mac', '00000000-0000-4000-8000-000000000001')
    exchange = tmp_path / 'exchange'
    (exchange / '.stfolder').mkdir(parents=True)
    runtime = data / 'runtime.json'
    runtime.write_text(json.dumps({'database': str(db), 'exchange': str(exchange), 'node': 'mac'}))
    return runtime, db, exchange


def test_inspect_existing_installation_does_not_write_database_or_config(tmp_path):
    runtime, db, exchange = fixture_install(tmp_path)
    before = db.read_bytes(), runtime.read_bytes()
    sidecars_before = [(db.parent / ('mac.db' + suffix)).exists() for suffix in ('-wal', '-shm')]
    report = install_inspect.inspect(runtime)
    assert report == {'format': 'agentmesh-install-inspect-v1', 'status': 'ready',
                      'node': 'mac', 'group': '00000000-0000-4000-8000-000000000001',
                      'policy': 'legacy', 'identity': 'absent', 'database': str(db),
                      'exchange': str(exchange)}
    assert (db.read_bytes(), runtime.read_bytes()) == before
    assert [(db.parent / ('mac.db' + suffix)).exists() for suffix in ('-wal', '-shm')] == sidecars_before


def test_missing_runtime_is_not_a_new_install(tmp_path):
    runtime = tmp_path / 'data' / 'runtime.json'
    assert install_inspect.inspect(runtime)['status'] == 'not_found'
    assert not runtime.parent.exists()


def test_inspect_rejects_scope_mismatch_without_modifying_database(tmp_path):
    runtime, db, _ = fixture_install(tmp_path)
    before = db.read_bytes()
    config = json.loads(runtime.read_text())
    config['node'] = 'windows'
    runtime.write_text(json.dumps(config))
    with pytest.raises(ValueError, match='node mismatch'):
        install_inspect.inspect(runtime)
    assert db.read_bytes() == before


def test_inspect_rejects_symlink_runtime(tmp_path):
    runtime, _, _ = fixture_install(tmp_path)
    alias = tmp_path / 'alias.json'
    alias.symlink_to(runtime)
    with pytest.raises(ValueError, match='symlink'):
        install_inspect.inspect(alias)


def test_inspect_never_opens_missing_database_for_creation(tmp_path):
    runtime, db, _ = fixture_install(tmp_path)
    db.unlink()
    with pytest.raises(ValueError, match='database missing'):
        install_inspect.inspect(runtime)
    assert not db.exists()


def test_strict_database_without_identity_requires_recovery(tmp_path):
    runtime, db, _ = fixture_install(tmp_path)
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE _sync_security(sender TEXT)')
    with pytest.raises(ValueError, match='strict database missing identity'):
        install_inspect.inspect(runtime)
    assert db.exists()


def test_inspect_rejects_symlink_exchange_sidecar(tmp_path):
    runtime, db, exchange = fixture_install(tmp_path)
    alias = tmp_path / 'alias'
    alias.symlink_to(exchange, target_is_directory=True)
    config = json.loads(runtime.read_text())
    config['exchange'] = str(alias)
    runtime.write_text(json.dumps(config))
    with pytest.raises(ValueError, match='symlink'):
        install_inspect.inspect(runtime)
