"""Existing-install adoption must bind paths, never initialize or activate."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest
import memory_sync
import sqlite_memory


def existing(tmp_path):
    root = tmp_path / 'app'
    data = root / 'data'
    data.mkdir(parents=True)
    db = data / 'mac.db'
    sqlite_memory.init_database(db)
    memory_sync.initialize(db, 'mac', '00000000-0000-4000-8000-000000000001')
    exchange = tmp_path / 'exchange'
    (exchange / '.stfolder').mkdir(parents=True)
    workflow = data / 'custom-workflow.json'
    workflow.write_text(json.dumps({'ingest': False, 'summarize': False}))
    return dict(runtime=data / 'runtime.json', app_root=root, database=db,
                exchange=exchange, node='mac', security_dir=root / 'identity',
                security_state=data / 'custom-wizard.json', workflow_config=workflow)


def test_bind_only_creates_runtime_and_preserves_pending_identity(tmp_path, monkeypatch):
    import install_adopt
    from signed_packets import init_identity
    args = existing(tmp_path)
    init_identity(args['security_dir'], '00000000-0000-4000-8000-000000000001', 'mac')
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    monkeypatch.setattr('sys.stdin', __import__('io').StringIO('BIND\n'))
    report = install_adopt.adopt(**args)
    assert report['status'] == 'bound'
    assert all(p.read_bytes() == value for p, value in before.items())
    config = json.loads(args['runtime'].read_text())
    assert config['policy'] == 'legacy'
    assert config['workflow_config'] == str(args['workflow_config'])
    with closing(sqlite3.connect(args['database'])) as c:
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='_sync_security'").fetchone()


def test_bind_refuses_exchange_replaced_during_prompt(tmp_path, monkeypatch):
    import install_adopt
    args = existing(tmp_path)
    class Input:
        def readline(self):
            args['exchange'].rename(tmp_path / 'old-exchange')
            (args['exchange'] / '.stfolder').mkdir(parents=True)
            return 'BIND\n'
    monkeypatch.setattr('sys.stdin', Input())
    with pytest.raises(ValueError, match='paths changed'):
        install_adopt.adopt(**args)
    assert not args['runtime'].exists()


def test_cli_adopts_explicit_existing_paths(tmp_path, monkeypatch, capsys):
    import agentmesh
    args = existing(tmp_path)
    monkeypatch.setattr('sys.stdin', __import__('io').StringIO('BIND\n'))
    argv = ['adopt-install']
    for key, value in args.items():
        argv += ['--' + key.replace('_', '-'), str(value)]
    assert agentmesh.main(argv) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'bound'
