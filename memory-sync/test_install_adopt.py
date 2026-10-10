"""Existing-install adoption must bind paths, never initialize or activate."""
from contextlib import closing
import io
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


@pytest.mark.skipif(__import__('os').name == 'nt', reason='POSIX ownership mode; Windows uses native ACLs')
def test_adoption_requires_user_owned_existing_root(tmp_path, monkeypatch):
    import install_adopt
    args = existing(tmp_path)
    args['app_root'].chmod(0o777)
    monkeypatch.setattr('sys.stdin', io.StringIO('BIND\n'))
    with pytest.raises(ValueError, match='owner|writable'):
        install_adopt.adopt(**args)
    assert not args['runtime'].exists()


@pytest.mark.parametrize('damage', ['explicit_root_partial', 'invalid_group', 'invalid_node'])
def test_adoption_rejects_partial_or_invalid_authoritative_scope(tmp_path, monkeypatch, damage):
    import install_adopt
    from signed_packets import write_local
    args = existing(tmp_path)
    if damage == 'explicit_root_partial':
        args['runtime'] = args['app_root'] / 'nested/data/runtime.json'
        args['runtime'].parent.mkdir(parents=True)
        write_local(args['app_root'] / '.setup-pending.json', {'status': 'partial'})
    else:
        with closing(sqlite3.connect(args['database'])) as c, c:
            if damage == 'invalid_group':
                c.execute("UPDATE _sync_config SET group_id='not-a-uuid'")
            else:
                c.execute("UPDATE _sync_config SET node='unknown'")
                args['node'] = 'unknown'
    monkeypatch.setattr('sys.stdin', io.StringIO('BIND\n'))
    with pytest.raises(ValueError, match='partial|invalid database scope'):
        install_adopt.adopt(**args)
    assert not args['runtime'].exists()
