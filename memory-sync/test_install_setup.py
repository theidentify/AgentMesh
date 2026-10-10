"""First-run creation must never become existing-install repair or activation."""
from contextlib import closing
import io
import json
import sqlite3
import uuid

import pytest

import agentmesh
import memory_sync
import signed_packets


def invoke(root, exchange, monkeypatch, answer='NEW\n', node='mac'):
    monkeypatch.setattr('sys.stdin', io.StringIO(answer))
    return agentmesh.main(['setup-new', '--local-dir', str(root), '--exchange', str(exchange), '--node', node])


@pytest.mark.parametrize('answer,code', [('\n', 2), ('no\n', 2), ('new\n', 2), ('', 1)])
def test_without_exact_confirmation_never_writes(paths, monkeypatch, capsys, answer, code):
    root, exchange = paths
    assert invoke(root, exchange, monkeypatch, answer) == code
    output = capsys.readouterr()
    assert 'NEW' in output.err
    if code == 2:
        assert json.loads(output.out)['status'] == 'pending'
    else:
        assert not output.out
    assert not root.exists()
    assert sorted(p.name for p in root.parent.iterdir()) == ['exchange']


@pytest.mark.parametrize('case', ['relative-root', 'relative-exchange', 'dotdot', 'node',
    'missing-parent', 'inside-exchange', 'ancestor-exchange', 'same-exchange',
    'missing-marker', 'file-marker', 'symlink-marker', 'symlink-parent',
    'empty-root', 'file-root', 'database-root', 'runtime-root', 'orphan-identity'])
def test_invalid_or_preexisting_scope_blocks_before_prompt(paths, monkeypatch, capsys, case):
    root, exchange = paths
    monkeypatch.chdir(root.parent)
    node = 'mac'
    if case == 'relative-root':
        root = 'relative-root'
    elif case == 'relative-exchange':
        exchange = 'relative-exchange'
    elif case == 'dotdot':
        root = root / '..' / 'different'
    elif case == 'node':
        node = 'MAC'
    elif case == 'missing-parent':
        root = root / 'missing-child'
    elif case == 'inside-exchange':
        root = exchange / 'local'
    elif case == 'ancestor-exchange':
        root = exchange.parent
    elif case == 'same-exchange':
        root = exchange
    elif case in ('missing-marker', 'file-marker', 'symlink-marker'):
        (exchange / '.stfolder').rmdir()
        if case == 'file-marker':
            (exchange / '.stfolder').write_bytes(b'not accepted')
        elif case == 'symlink-marker':
            (exchange / '.stfolder').symlink_to(exchange, target_is_directory=True)
    elif case == 'symlink-parent':
        link = root.parent / 'link'
        link.symlink_to(exchange, target_is_directory=True)
        root = link / 'local'
    elif case == 'file-root':
        root.write_bytes(b'preserve')
    elif case.endswith('root') or case == 'orphan-identity':
        root.mkdir()
        if case == 'database-root':
            (root / 'memory.db').write_bytes(b'preserve database')
        elif case == 'runtime-root':
            (root / 'runtime.json').write_bytes(b'preserve runtime')
        elif case == 'orphan-identity':
            (root / 'identity').mkdir()
            (root / 'identity' / 'identity.json').write_bytes(b'preserve identity')
    before = snapshot(paths[0].parent)
    assert invoke(root, exchange, monkeypatch, node=node) == 1
    output = capsys.readouterr()
    assert 'Type NEW' not in output.err and not output.out
    assert snapshot(paths[0].parent) == before


def snapshot(root):
    return {str(p.relative_to(root)): ('link', str(p.readlink())) if p.is_symlink()
            else ('dir',) if p.is_dir() else ('file', p.read_bytes())
            for p in root.rglob('*')}


@pytest.mark.parametrize('change', ['marker-removed', 'marker-replaced', 'exchange-replaced', 'parent-replaced'])
def test_prompt_time_path_change_blocks_without_creating_root(paths, monkeypatch, capsys, change):
    root, exchange = paths
    parent = root.parent / 'parent'
    parent.mkdir()
    root = parent / 'local'

    class ChangingInput:
        def readline(self):
            if change == 'marker-removed':
                (exchange / '.stfolder').rmdir()
            elif change == 'marker-replaced':
                (exchange / '.stfolder').rename(exchange / 'old-marker')
                (exchange / '.stfolder').mkdir()
            elif change == 'exchange-replaced':
                exchange.rename(exchange.with_name('old-exchange'))
                (exchange / '.stfolder').mkdir(parents=True)
            else:
                parent.rename(parent.with_name('old-parent'))
                parent.mkdir()
            return 'NEW\n'

    monkeypatch.setattr('sys.stdin', ChangingInput())
    assert agentmesh.main(['setup-new', '--local-dir', str(root), '--exchange', str(exchange), '--node', 'mac']) == 1
    assert not root.exists()
    assert not capsys.readouterr().out


@pytest.mark.parametrize('failure', ['permissions', 'database', 'validation'])
def test_ordinary_failure_rolls_back_only_proven_owned_contents(paths, monkeypatch, capsys, failure):
    import install_setup
    import install_inspect
    import sqlite_memory
    root, exchange = paths

    def fail(*args, **kwargs):
        raise RuntimeError('injected failure')

    if failure == 'permissions':
        monkeypatch.setattr(install_setup, 'windows_private', fail)
    elif failure == 'database':
        original = sqlite_memory.init_database
        def failed_database(db):
            original(db)
            raise RuntimeError('interrupted database initialization')
        monkeypatch.setattr(sqlite_memory, 'init_database', failed_database)
    else:
        monkeypatch.setattr(install_inspect, 'wizard_status', fail)
    assert invoke(root, exchange, monkeypatch) == 1
    assert not capsys.readouterr().out
    if failure == 'database':
        assert (root / '.setup-pending.json').is_file()
        assert (root / 'data' / 'memory.db').is_file()
        before = snapshot(root)
        assert invoke(root, exchange, monkeypatch) == 1
        assert snapshot(root) == before
    else:
        assert not root.exists()
    assert sorted(p.name for p in exchange.iterdir()) == ['.stfolder']


def test_concurrent_foreign_contents_survive_failure(paths, monkeypatch, capsys):
    import install_inspect
    root, exchange = paths
    def fail(*args, **kwargs):
        (root / 'foreign.txt').write_bytes(b'not ours')
        (root / 'data' / 'workflow.json').write_bytes(b'foreign replacement')
        raise RuntimeError('concurrent writer')
    monkeypatch.setattr(install_inspect, 'wizard_status', fail)
    assert invoke(root, exchange, monkeypatch) == 1
    assert (root / 'foreign.txt').read_bytes() == b'not ours'
    assert (root / 'data' / 'workflow.json').read_bytes() == b'foreign replacement'
    assert (root / '.setup-pending.json').exists()
    assert not capsys.readouterr().out


@pytest.mark.parametrize('change', ['exchange', 'runtime', 'foreign-entry', 'identity'])
def test_completion_refuses_changed_scope_or_unowned_entries(paths, monkeypatch, capsys, change):
    import install_inspect
    root, exchange = paths
    original = install_inspect.wizard_status
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if change == 'exchange':
            (exchange / '.stfolder').rmdir()
        elif change == 'runtime':
            (root / 'data' / 'runtime.json').write_bytes(b'foreign config')
        elif change == 'identity':
            (root / 'identity').mkdir()
            (root / 'identity' / 'identity.json').write_bytes(b'foreign key')
        else:
            (root / 'foreign').write_bytes(b'foreign data')
        return result
    monkeypatch.setattr(install_inspect, 'wizard_status', changed)
    assert invoke(root, exchange, monkeypatch) == 1
    assert not capsys.readouterr().out
    if change != 'exchange':
        assert (root / '.setup-pending.json').is_file()
        if change == 'runtime':
            assert (root / 'data' / 'runtime.json').read_bytes() == b'foreign config'
        elif change == 'identity':
            assert (root / 'identity' / 'identity.json').read_bytes() == b'foreign key'
        else:
            assert (root / 'foreign').read_bytes() == b'foreign data'


def test_competing_creator_after_validation_is_never_overwritten(paths, monkeypatch, capsys):
    from pathlib import Path
    root, exchange = paths
    original = Path.mkdir
    def competing(path, *args, **kwargs):
        if path == root and not root.exists():
            original(root, mode=0o700)
            (root / 'identity.json').write_bytes(b'other installer identity')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'mkdir', competing)
    assert invoke(root, exchange, monkeypatch) == 1
    assert snapshot(root) == {'identity.json': ('file', b'other installer identity')}
    assert not capsys.readouterr().out


def test_second_new_installation_gets_distinct_group(paths, monkeypatch, capsys):
    root, exchange = paths
    assert invoke(root, exchange, monkeypatch) == 0
    first = json.loads(capsys.readouterr().out)
    assert invoke(root.with_name('second-local'), exchange, monkeypatch) == 0
    second = json.loads(capsys.readouterr().out)
    assert first['group'] != second['group']
    before = snapshot(root)
    assert invoke(root, exchange, monkeypatch) == 1
    assert snapshot(root) == before


@pytest.mark.parametrize('change', ['group', 'outbox', 'counter'])
def test_initialization_must_read_back_new_empty_scope_before_adopting_database(paths, monkeypatch, capsys, change):
    root, exchange = paths
    original = memory_sync.initialize
    def contaminated(db, node, group):
        result = original(db, node, group)
        with closing(sqlite3.connect(db)) as c, c:
            if change == 'group':
                c.execute('UPDATE _sync_config SET group_id=?', (str(uuid.uuid4()),))
            elif change == 'counter':
                c.execute('UPDATE _sync_counters SET next_id=1')
            else:
                c.execute("INSERT INTO _sync_outbox VALUES('foreign','{}',0)")
        return result
    monkeypatch.setattr(memory_sync, 'initialize', contaminated)
    assert invoke(root, exchange, monkeypatch) == 1
    assert not capsys.readouterr().out
    assert (root / '.setup-pending.json').exists()
    assert (root / 'data' / 'memory.db').exists()


@pytest.mark.parametrize('command', ['inspect-install', 'wizard-status', 'wizard-resume'])
def test_marked_partial_root_blocks_packaged_existing_install_commands(paths, monkeypatch, capsys, command):
    import install_inspect
    root, exchange = paths
    def interrupted(*args, **kwargs):
        (root / 'foreign').write_bytes(b'preserve')
        raise RuntimeError('interrupted')
    original = install_inspect.wizard_status
    monkeypatch.setattr(install_inspect, 'wizard_status', interrupted)
    assert invoke(root, exchange, monkeypatch) == 1
    capsys.readouterr()
    monkeypatch.setattr(install_inspect, 'wizard_status', original)
    monkeypatch.setattr('sys.stdin', io.StringIO('CREATE\nFixture\n\n\n'))
    before = snapshot(root)
    assert agentmesh.main([command, '--runtime', str(root / 'data' / 'runtime.json')]) == 1
    assert not capsys.readouterr().out
    assert snapshot(root) == before


def test_permissions_changed_during_validation_block_completion(paths, monkeypatch, capsys):
    import os
    import install_inspect
    if os.name == 'nt':
        pytest.skip('POSIX permission tampering; native Windows ACL contract covered separately')
    root, exchange = paths
    original = install_inspect.wizard_status
    def insecure(*args, **kwargs):
        result = original(*args, **kwargs)
        root.chmod(0o755)
        return result
    monkeypatch.setattr(install_inspect, 'wizard_status', insecure)
    assert invoke(root, exchange, monkeypatch) == 1
    assert not capsys.readouterr().out
    assert (root / '.setup-pending.json').exists()


@pytest.fixture
def paths(tmp_path):
    exchange = tmp_path / 'exchange'
    (exchange / '.stfolder').mkdir(parents=True)
    return tmp_path / 'new-local', exchange


@pytest.mark.parametrize('node', ['mac', 'windows', 'linux'])
def test_confirmed_setup_creates_empty_isolated_installation(paths, monkeypatch, capsys, node):
    import os
    import subprocess
    root, exchange = paths
    def forbidden(*args, **kwargs):
        raise AssertionError('setup must not run a worker or external process')
    monkeypatch.setattr('sync_worker.run_once', forbidden)
    # Windows ACL provisioning necessarily runs the OS permission adapter.
    if os.name != 'nt':
        monkeypatch.setattr(subprocess, 'Popen', forbidden)
    assert invoke(root, exchange, monkeypatch, node=node) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'created'
    assert str(uuid.UUID(report['group'])) == report['group']
    runtime = root / 'data' / 'runtime.json'
    config = json.loads(runtime.read_text())
    db = root / 'data' / 'memory.db'
    assert config == {'database': str(db), 'exchange': str(exchange), 'node': node,
                      'security_dir': str(root / 'identity'),
                      'security_state': str(root / 'data' / 'security-wizard.json')}
    with closing(sqlite3.connect(db)) as c:
        assert c.execute('SELECT node,group_id FROM _sync_config').fetchone() == (node, report['group'])
        for table in memory_sync.TABLES + ('_sync_outbox', '_sync_history', '_sync_shadow'):
            assert c.execute('SELECT count(*) FROM ' + table).fetchone()[0] == 0
        assert c.execute('SELECT next_id FROM _sync_counters').fetchall()
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='_sync_security'").fetchone()
    assert json.loads((root / 'data' / 'workflow.json').read_text()) == {'ingest': False, 'summarize': False}
    assert not (root / 'identity').exists()
    assert not (root / 'data' / 'security-wizard.json').exists()
    assert sorted(p.name for p in exchange.iterdir()) == ['.stfolder']
    for directory in (root, root / 'data'):
        signed_packets.private_directory(directory)
    for file in (db, runtime, root / 'data' / 'workflow.json'):
        signed_packets.read_local(file, private=True)
    assert agentmesh.main(['inspect-install', '--runtime', str(runtime)]) == 0
    assert json.loads(capsys.readouterr().out)['identity'] == 'absent'
    assert agentmesh.main(['wizard-status', '--runtime', str(runtime)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status['policy'] == 'legacy' and status['wizard_step'] == 'prerequisites'
    assert not (root / 'identity').exists()
