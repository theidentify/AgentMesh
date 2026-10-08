"""Real isolated consumer/package/worker execution; no production paths."""
from contextlib import closing
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import zipfile

import pytest

import memory_sync
import ota_update as ota
import signed_packets as signed
import sqlite_memory
from runtime_lock import lock

SOURCE = Path(__file__).parent
GROUP = '00000000-0000-4000-8000-000000000001'


@pytest.fixture
def installed(tmp_path):
    local, exchange = tmp_path / 'local', tmp_path / 'exchange'
    (local / 'data').mkdir(parents=True)
    (exchange / '.stfolder').mkdir(parents=True)
    db = local / 'data' / 'windows.db'
    sqlite_memory.init_database(db)
    memory_sync.initialize(db, 'windows', GROUP)
    signed.init_identity(local / 'identity', GROUP, 'windows')
    signed.write_local(local / 'data' / 'runtime.json', {'database': str(db),
        'exchange': str(exchange), 'security_dir': str(local / 'identity')})
    signed.write_local(local / 'data' / 'workflow.json', {'ingest': False, 'summarize': False})
    signed.write_local(local / 'data' / 'security-wizard.json', {'synthetic': 'preserve-bytewise'})
    # A second WAL database with an unconventional extension must be backed up too.
    with closing(sqlite3.connect(local / 'data' / 'auxiliary.state')) as c, c:
        c.execute('CREATE TABLE preserved(value)'); c.execute("INSERT INTO preserved VALUES('synthetic')")
    ota.bootstrap_consumer(local, SOURCE)
    identity = ota.release_key(tmp_path / 'publisher')
    ota.provision_trust(local, identity, identity['key_id'], identity['release_id'])
    return local, exchange, db, tmp_path / 'publisher' / 'release-private.json'


def release(installed, version=1, source=SOURCE):
    local, _, _, private = installed
    output = local.parent / ('release-' + str(version))
    ota.build_release(source, output, private, version)
    return output


def source_copy(tmp_path):
    source = tmp_path / 'source-copy'
    for name in ota.REQUIRED:
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE / name, target)
    return source


def resign(output, private):
    manifest = signed.parse((output / 'release.json').read_bytes())
    manifest.pop('signature')
    manifest['archive_sha256'] = ota.digest((output / 'worker.zip').read_bytes())
    key = signed.crypto()[0].from_private_bytes(bytes.fromhex(ota.read_json(private)['private_key']))
    manifest['signature'] = key.sign(ota.DOMAIN + signed.typed(manifest)).hex()
    signed.write_local(output / 'release.json', manifest)


def test_supervisor_timeout_reaps_child_and_continues_polling(installed, monkeypatch, capsys):
    local, _, _, _ = installed
    worker = local / 'versions' / '0' / 'sync_worker.py'
    worker.write_text('import time\nif __name__ == "__main__":\n    time.sleep(60)\n')
    run = subprocess.run
    calls = []
    def bounded_run(args, **kwargs):
        if len(args) > 1 and args[1].endswith('sync_worker.py'):
            calls.append(args)
            kwargs['timeout'] = 0.2
        return run(args, **kwargs)
    class FinishedPolling(Exception):
        pass
    sleeps = []
    sleep = ota.time.sleep
    def next_cycle(interval):
        if interval != 0:
            return sleep(interval)
        sleeps.append(interval)
        if len(sleeps) == 2:
            raise FinishedPolling()
    before = (local / 'ota-state.json').read_bytes()
    monkeypatch.setattr(ota.subprocess, 'run', bounded_run)
    monkeypatch.setattr(ota.time, 'sleep', next_cycle)
    with pytest.raises(FinishedPolling):
        ota.supervise(local, interval=0, once=False)
    assert len(calls) == 2
    assert (local / 'ota-state.json').read_bytes() == before
    assert capsys.readouterr().err.count('Worker cycle timed out') == 2
    with lock(local / 'data' / 'worker.lock', timeout=0):
        pass


def test_supervisor_once_timeout_returns_failure_without_mutating_state(installed, monkeypatch):
    local, _, _, _ = installed
    worker = local / 'versions' / '0' / 'sync_worker.py'
    worker.write_text('import time\nif __name__ == "__main__":\n    time.sleep(60)\n')
    run = subprocess.run
    def bounded_run(args, **kwargs):
        if len(args) > 1 and args[1].endswith('sync_worker.py'):
            kwargs['timeout'] = 0.2
        return run(args, **kwargs)
    before = (local / 'ota-state.json').read_bytes()
    monkeypatch.setattr(ota.subprocess, 'run', bounded_run)
    assert ota.supervise(local, once=True) == 124
    assert (local / 'ota-state.json').read_bytes() == before


def test_actual_cli_signed_update_preserves_identity_all_databases_and_workflow(installed):
    local, exchange, db, _ = installed
    transcript = local / 'data' / 'synthetic-session.jsonl'
    transcript.write_text(json.dumps({'type': 'message', 'id': 'synthetic', 'message':
        {'role': 'user', 'content': [{'type': 'text', 'text': 'Isolated OTA preservation fixture.'}]}}) + '\n')
    assert sqlite_memory.ingest_file(db, transcript)['inserted'] == 1
    with closing(sqlite3.connect(db)) as connection:
        before_rows = connection.execute('SELECT * FROM observation_events').fetchall()
    before = {p.relative_to(local): p.read_bytes() for directory in ('identity', 'data')
        for p in (local / directory).rglob('*.json')}
    output = release(installed)
    consumer = local / 'consumer' / 'ota_update.py'
    result = subprocess.run([sys.executable, str(consumer), 'apply', '--local', str(local),
        '--release', str(output)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['status'] == 'activated'
    state = ota.state_at(local)
    assert state['active'] == state['highest'] == 1 and state['pending'] is None
    assert json.loads((exchange / 'status' / 'windows.json').read_text())['security']['policy'] == 'required'
    for name, data in before.items():
        assert (local / name).read_bytes() == data
    backup = Path(report['backup'])
    for name, data in before.items():
        assert (backup / name).read_bytes() == data
    with closing(sqlite3.connect(backup / 'data' / 'auxiliary.state')) as c:
        assert c.execute('SELECT value FROM preserved').fetchall() == [('synthetic',)]
    assert (local / 'versions' / '0' / 'sync_worker.py').read_bytes() == (SOURCE / 'sync_worker.py').read_bytes()
    retry = subprocess.run([sys.executable, str(consumer), 'apply', '--local', str(local), '--release', str(output)], capture_output=True)
    assert retry.returncode == 1
    run = subprocess.run([sys.executable, str(consumer), 'run', '--local', str(local), '--once'], capture_output=True)
    assert run.returncode == 0
    assert memory_sync.status(db)['group_id'] == GROUP
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute('SELECT * FROM observation_events').fetchall() == before_rows
    with closing(sqlite3.connect(backup / 'data' / db.name)) as connection:
        assert connection.execute('SELECT * FROM observation_events').fetchall() == before_rows


@pytest.mark.parametrize('failure', ['import', 'cycle'])
def test_real_health_or_worker_failure_rolls_back_code_and_burns_version(installed, tmp_path, failure):
    local, _, db, _ = installed
    source = source_copy(tmp_path)
    worker = source / 'sync_worker.py'
    worker.write_text('raise RuntimeError("synthetic import failure")\n' if failure == 'import' else
        worker.read_text().replace('raise SystemExit(main())', 'raise SystemExit(1)'))
    output = release(installed, source=source)
    result = ota.apply(local, output, db)
    assert result['status'] == 'rolled_back'
    assert ota.state_at(local)['active'] == 0 and ota.state_at(local)['highest'] == 1
    assert ota.worker_cycle(local, ota.runtime_at(local), 0) == 0
    with pytest.raises(ValueError, match='replay'):
        ota.apply(local, output, db)
    assert ota.apply(local, release(installed, 2), db)['status'] == 'activated'


@pytest.mark.parametrize('boundary', ['pending', 'active', 'after_cycle'])
def test_durable_interruption_recovery_is_idempotent(installed, monkeypatch, boundary):
    local, _, db, _ = installed
    output = release(installed)
    write = ota.write_local
    cycle = ota.worker_cycle
    def interrupted_write(path, state, **kwargs):
        write(path, state, **kwargs)
        if (Path(path).name == 'ota-state.json' and state['pending'] == 1 and
            state['active'] == (0 if boundary == 'pending' else 1) and boundary != 'after_cycle'):
            raise SystemExit('simulated power interruption after durable state')
    def interrupted_cycle(*args):
        result = cycle(*args)
        assert result == 0
        raise SystemExit('simulated interruption after real committed cycle')
    with monkeypatch.context() as patch:
        patch.setattr(ota, 'write_local', interrupted_write)
        if boundary == 'after_cycle': patch.setattr(ota, 'worker_cycle', interrupted_cycle)
        with pytest.raises(SystemExit): ota.apply(local, output, db)
    assert ota.state_at(local)['highest'] == 1
    first = ota.recover(local, db)
    assert first['active'] == 0 and first['highest'] == 1 and first['pending'] is None
    assert ota.recover(local, db) == first
    assert ota.worker_cycle(local, ota.runtime_at(local), first['active']) == 0
    with pytest.raises(ValueError, match='replay'): ota.apply(local, output, db)


@pytest.mark.parametrize('tamper', ['manifest', 'archive', 'authority', 'untrusted'])
def test_signature_and_separate_authority_fail_closed(installed, tamper):
    local, _, db, _ = installed
    output = release(installed)
    if tamper == 'archive':
        with (output / 'worker.zip').open('ab') as out: out.write(b'tampered')
    elif tamper == 'untrusted':
        (local / 'release-trust.json').unlink()
    else:
        manifest = signed.parse((output / 'release.json').read_bytes())
        if tamper == 'manifest': manifest['version'] = 8
        else: manifest['key_id'] = signed.Security(local / 'identity').public['key_id']
        signed.write_local(output / 'release.json', manifest)
    with pytest.raises(Exception): ota.apply(local, output, db)
    assert ota.state_at(local)['highest'] == ota.state_at(local)['active'] == 0
    assert not (local / 'versions' / '1').exists()


@pytest.mark.parametrize('member', ['../escape.py', '/escape.py', 'C:\\escape.py', 'src/../escape.py', 'arbitrary-command.cmd', 'SYNC.md'])
def test_authenticated_archive_rejects_unallowlisted_or_duplicate_names(installed, member):
    local, _, db, private = installed
    output = release(installed)
    with zipfile.ZipFile(output / 'worker.zip', 'a') as package:
        package.writestr(member, 'malicious payload')
    resign(output, private)
    with pytest.raises(ValueError, match='unsafe'): ota.apply(local, output, db)
    assert not (local.parent / 'escape.py').exists()
    assert not (local / 'versions' / '1').exists()


@pytest.mark.parametrize('bad', ['symlink', 'bomb', 'hash'])
def test_authenticated_archive_rejects_symlink_bomb_and_file_hash(installed, bad):
    local, _, db, private = installed
    output = release(installed)
    path = output / 'worker.zip'
    entries = {}
    with zipfile.ZipFile(path) as package:
        entries = {name: package.read(name) for name in package.namelist()}
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as package:
        for name, data in entries.items():
            if name == 'SYNC.md':
                if bad == 'symlink':
                    info = zipfile.ZipInfo(name); info.create_system = 3; info.external_attr = 0o120777 << 16
                    package.writestr(info, '../escape.py'); continue
                data = b'X' * (9 * 1024 * 1024) if bad == 'bomb' else b'wrong hash'
            package.writestr(name, data)
    resign(output, private)
    with pytest.raises(ValueError): ota.apply(local, output, db)
    assert not (local / 'versions' / '1').exists()


def test_real_process_worker_waits_on_shared_lock_then_completes(installed):
    local, _, _, _ = installed
    runtime = ota.runtime_at(local)
    app = local / 'versions' / '0'
    args = [sys.executable, str(app / 'sync_worker.py'), runtime['database'], runtime['exchange'],
        '--once', '--ota-version', '0', '--workflow-config', str(local / 'data' / 'workflow.json'), '--security-dir', runtime['security_dir']]
    with lock(local / 'data' / 'worker.lock'):
        child = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            with pytest.raises(subprocess.TimeoutExpired): child.communicate(timeout=0.4)
            assert child.poll() is None
        except BaseException:
            child.kill(); child.wait(); raise
    child.communicate(timeout=30)
    assert child.returncode == 0


def test_second_supervisor_or_updater_refused_before_changes(installed):
    local, _, db, _ = installed
    output = release(installed)
    with lock(local / 'data' / 'supervisor.lock'):
        with pytest.raises(TimeoutError): ota.apply(local, output, db)
        with pytest.raises(TimeoutError): ota.supervise(local, once=True)
    assert ota.state_at(local)['highest'] == 0


def test_bootstrap_interruption_can_resume_without_overwrite(installed, tmp_path):
    local, _, _, _ = installed
    (local / 'ota-state.json').unlink()
    assert ota.bootstrap_consumer(local, SOURCE)['status'] == 'installed'
    (local / 'ota-state.json').unlink()
    (local / 'versions' / '0' / 'SYNC.md').write_text('changed local file')
    with pytest.raises(ValueError, match='mismatch'): ota.bootstrap_consumer(local, SOURCE)


def test_supervisor_automatically_consumes_signed_release_and_reads_back_status(installed):
    local, exchange, _, _ = installed
    output = release(installed)
    delivered = exchange / 'releases' / 'worker'
    shutil.copytree(output, delivered)
    result = subprocess.run([sys.executable, str(local / 'consumer' / 'ota_update.py'),
        'run', '--local', str(local), '--once'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert ota.state_at(local)['active'] == 1
    status = json.loads((exchange / 'status' / 'windows.json').read_text())
    assert status['sync']['invalid'] == status['sync']['conflict'] == 0
    assert status['security']['policy'] == 'required'


def test_manual_updater_waits_for_real_process_lock_drain(installed):
    local, _, _, _ = installed
    output = release(installed)
    with lock(local / 'data' / 'worker.lock'):
        child = subprocess.Popen([sys.executable, str(local / 'consumer' / 'ota_update.py'),
            'apply', '--local', str(local), '--release', str(output)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            with pytest.raises(subprocess.TimeoutExpired): child.communicate(timeout=0.4)
            assert ota.state_at(local)['highest'] == 0
        except BaseException:
            child.kill(); child.wait(); raise
    stdout, stderr = child.communicate(timeout=30)
    assert child.returncode == 0, stderr
    assert json.loads(stdout)['status'] == 'activated'


@pytest.mark.parametrize('node', ['mac', 'windows', 'linux'])
def test_actual_bootstrap_package_installer_to_consumer_to_update(tmp_path, node):
    import build_package
    exchange = tmp_path / 'exchange'
    (exchange / '.stfolder').mkdir(parents=True)
    baseline = tmp_path / 'baseline.jsonl'
    baseline.write_text(json.dumps({'format': 'omp-sqlite-snapshot-v1', 'tables': build_package.TABLES}) + '\n')
    build_package.build(baseline, exchange, GROUP)
    local = tmp_path / 'installed'
    # Real installer extraction/import first, without invoking default ingestion.
    result = subprocess.run([sys.executable, '-c',
        'import sys,json; sys.path.insert(0,sys.argv[1]); import bootstrap_windows; '
        'print(json.dumps(bootstrap_windows.install(sys.argv[2],sys.argv[3],node=sys.argv[4])))',
        str(SOURCE), str(exchange), str(local), node], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    runtime = json.loads(result.stdout)
    signed.write_local(local / 'data' / 'workflow.json', {'ingest': False, 'summarize': False})
    ota.bootstrap_consumer(local, SOURCE)
    identity = ota.release_key(tmp_path / 'publisher')
    ota.provision_trust(local, identity, identity['key_id'], identity['release_id'])
    release_dir = tmp_path / 'release'
    ota.build_release(SOURCE, release_dir, tmp_path / 'publisher' / 'release-private.json', 1)
    assert ota.apply(local, release_dir, Path(runtime['database']))['status'] == 'activated'
    assert memory_sync.status(runtime['database'])['node'] == node
    assert (local / 'app' / 'sync_worker.py').read_bytes() == (SOURCE / 'sync_worker.py').read_bytes()


@pytest.mark.parametrize('failure', ['backup', 'staging', 'preactivation_interrupt'])
def test_authenticated_attempt_burned_before_any_prerequisite_failure(installed, monkeypatch, failure):
    local, _, db, _ = installed
    output = release(installed)
    write = ota.write_local
    def interrupted(path, state, **kwargs):
        write(path, state, **kwargs)
        if Path(path).name == 'ota-state.json' and state['highest'] == 1 and state['pending'] is None:
            raise SystemExit('interrupted after authenticated high water persisted')
    def fail(*args): raise OSError('synthetic mandatory prerequisite failure')
    with monkeypatch.context() as patch:
        if failure == 'preactivation_interrupt': patch.setattr(ota, 'write_local', interrupted)
        else: patch.setattr(ota, 'snapshot' if failure == 'backup' else 'stage', fail)
        with pytest.raises(SystemExit if failure == 'preactivation_interrupt' else OSError):
            ota.apply(local, output, db)
    state = ota.state_at(local)
    assert state['active'] == 0 and state['highest'] == 1 and state['pending'] is None
    assert ota.recover(local, db) == state
    with pytest.raises(ValueError, match='replay'): ota.apply(local, output, db)
    assert ota.apply(local, release(installed, 2), db)['status'] == 'activated'


@pytest.mark.parametrize('name', ['primary-wal', 'primary-shm'])
def test_database_header_precedes_sidecar_name_and_active_backup_is_mandatory(installed, name):
    local, _, db, _ = installed
    renamed = db.with_name(name)
    os.replace(db, renamed)
    runtime = ota.runtime_at(local) if db.exists() else signed.parse((local / 'data' / 'runtime.json').read_bytes())
    runtime['database'] = str(renamed)
    signed.write_local(local / 'data' / 'runtime.json', runtime)
    # Unattached suffix files are user state, not automatically SQLite sidecars.
    unrelated = local / 'data' / 'workflow-notes-wal'
    unrelated.write_bytes(b'synthetic preserved non-SQLite state')
    result = ota.apply(local, release(installed), renamed)
    assert result['status'] == 'activated'
    backup = Path(result['backup'])
    assert (backup / 'data' / name).is_file()
    assert (backup / 'data' / unrelated.name).read_bytes() == unrelated.read_bytes()
    for path in (backup / 'data' / name, backup / 'data' / 'auxiliary.state'):
        with closing(sqlite3.connect(path)) as c:
            assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    with closing(sqlite3.connect(backup / 'data' / name)) as c:
        assert c.execute('SELECT node,group_id FROM _sync_config').fetchone() == ('windows', GROUP)


def test_waiting_stale_worker_fails_version_fence_without_database_write(installed):
    local, _, db, _ = installed
    runtime = ota.runtime_at(local)
    app = local / 'versions' / '0'
    with lock(local / 'data' / 'worker.lock'):
        child = subprocess.Popen([sys.executable, str(app / 'sync_worker.py'), str(db), runtime['exchange'],
            '--once', '--ota-version', '1'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            with pytest.raises(subprocess.TimeoutExpired): child.communicate(timeout=0.4)
        except BaseException:
            child.kill(); child.wait(); raise
    stdout, _ = child.communicate(timeout=30)
    assert child.returncode == 1 and json.loads(stdout)['error'] == 'ValueError'
    assert memory_sync.status(db)['security']['policy'] == 'legacy'


def test_actual_consumer_process_kill_during_active_cycle_recovers(installed, tmp_path):
    import time
    local, _, db, _ = installed
    source = source_copy(tmp_path)
    worker = source / 'sync_worker.py'
    worker.write_text(worker.read_text().replace('    exchange = Path(exchange)',
        "    Path(database).parent.joinpath('cycle-entered').write_text(str(os.getpid()))\n"
        '    time.sleep(1.5)\n    exchange = Path(exchange)', 1))
    output = release(installed, source=source)
    parent = subprocess.Popen([sys.executable, str(local / 'consumer' / 'ota_update.py'),
        'apply', '--local', str(local), '--release', str(output)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    marker = local / 'data' / 'cycle-entered'
    try:
        deadline = time.monotonic() + 20
        while not marker.exists() and time.monotonic() < deadline:
            if parent.poll() is not None: break
            time.sleep(0.05)
        assert marker.exists(), parent.communicate(timeout=1)
        assert ota.state_at(local)['pending'] == 1
        parent.kill(); parent.communicate(timeout=5)
        assert parent.returncode != 0
        # New consumer waits for the orphan cycle's lock, then restores code 0.
        result = subprocess.run([sys.executable, str(local / 'consumer' / 'ota_update.py'),
            'run', '--local', str(local), '--once'], capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr
        state = ota.state_at(local)
        assert state['active'] == 0 and state['highest'] == 1 and state['pending'] is None
        assert ota.worker_cycle(local, ota.runtime_at(local), 0) == 0
        with pytest.raises(ValueError, match='replay'): ota.apply(local, output, db)
    finally:
        if parent.poll() is None: parent.kill(); parent.communicate(timeout=5)


def test_windows_reparse_attribute_is_rejected_without_claiming_native_execution(tmp_path, monkeypatch):
    from types import SimpleNamespace
    directory = tmp_path / 'junction-like'
    directory.mkdir()
    lstat = Path.lstat
    def with_reparse(path):
        info = lstat(path)
        if path == directory:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info
    monkeypatch.setattr(Path, 'lstat', with_reparse)
    with pytest.raises(ValueError, match='reparse'): signed.no_symlinks(directory / 'file.json')


def test_symlinked_state_and_external_security_layout_refused(installed, tmp_path):
    local, _, db, _ = installed
    outside = tmp_path / 'outside.json'
    shutil.copyfile(local / 'ota-state.json', outside)
    (local / 'ota-state.json').unlink(); (local / 'ota-state.json').symlink_to(outside)
    with pytest.raises(ValueError, match='symlink'): ota.apply(local, release(installed), db)
    runtime = ota.read_json(local / 'data' / 'runtime.json')
    runtime['security_dir'] = str(tmp_path / 'external-identity')
    signed.write_local(local / 'data' / 'runtime.json', runtime)
    with pytest.raises(ValueError, match='managed identity'): ota.runtime_at(local)
