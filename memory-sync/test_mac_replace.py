"""Mac replacement rehearsals inject only launchd; DB and binary are real."""
from contextlib import closing
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
import sqlite3
import subprocess
import sys

import pytest
from test_install_adopt import existing


@pytest.mark.parametrize('arguments', ['\targuments = {\n\t\t/opt/agentmesh\n\t\tworker-run\n\t}\n', '\targuments = { /opt/agentmesh worker-run }\n'])
def test_launchd_inspect_requires_verifiable_argument_boundaries(monkeypatch, arguments):
    import mac_replace
    supervisor = mac_replace.Launchd.__new__(mac_replace.Launchd)
    supervisor.domain = 'gui/123'
    monkeypatch.setattr(mac_replace.subprocess, 'run', lambda *a, **k:
        subprocess.CompletedProcess(a, 0, stdout='service = {\n\tpid = 123\n' + arguments + '}\n'))
    if '\n\t\t' in arguments:
        assert supervisor.inspect('org.example.agentmesh') == {
            'loaded': True, 'pid': 123, 'arguments': ['/opt/agentmesh', 'worker-run']}
    else:
        with pytest.raises(ValueError, match='verify launchd'):
            supervisor.inspect('org.example.agentmesh')


def fixture_manifest(tmp_path):
    args = existing(tmp_path)
    script = args['app_root'] / 'sync_worker.py'
    script.write_text("import sqlite3,sys,time\nc=sqlite3.connect(sys.argv[1])\nc.execute('BEGIN IMMEDIATE')\nprint('cycle-open',flush=True)\ntime.sleep(120)\nc.close()\n")
    legacy = [sys.executable, str(script), str(args['database']), str(args['exchange']), '--interval', '60.0']
    plist = tmp_path / 'agentmesh.plist'
    plist.write_bytes(plistlib.dumps(dict(Label='org.example.agentmesh', ProgramArguments=legacy,
        RunAtLoad=True, KeepAlive=True, EnvironmentVariables={'SYNTHETIC_SETTING': 'preserve'},
        StandardOutPath=str(args['database'].parent / 'worker.stdout.log'),
        StandardErrorPath=str(args['database'].parent / 'worker.stderr.log'))))
    manifest = {k: str(v) for k, v in args.items()}
    manifest.update(plist=str(plist), expected_args=legacy,
        install_root=str(tmp_path / 'versions'), backup_root=str(tmp_path / 'backups'))
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(manifest))
    path.chmod(0o600)
    return path, args, plist


class FakeSupervisor:
    def __init__(self, plist):
        self.plist = plist
        self.loaded = True
        self.process = None
        self.calls = []
    def inspect(self, label):
        config = plistlib.loads(self.plist.read_bytes())
        return {'loaded': self.loaded, 'pid': self.process.pid if self.process else None,
                'arguments': config['ProgramArguments'] if self.loaded else []}
    def drain(self, label, timeout):
        self.calls.append('drain')
        self.loaded = False
        if self.process:
            self.process.terminate()
            self.process.wait(timeout=timeout)
            self.process = None
    def bootstrap(self, plist):
        self.calls.append('bootstrap')
        self.loaded = True
        config = plistlib.loads(Path(plist).read_bytes())
        self.process = subprocess.Popen(config['ProgramArguments'], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, env={**os.environ, **config.get('EnvironmentVariables', {})})


def test_scope_change_during_drain_leaves_old_service_stopped(tmp_path, monkeypatch):
    import mac_replace
    manifest, args, plist = fixture_manifest(tmp_path)
    supervisor = FakeSupervisor(plist)
    original_drain = supervisor.drain
    def drain_and_change_scope(label, timeout):
        original_drain(label, timeout)
        with closing(sqlite3.connect(args['database'])) as c, c:
            c.execute("UPDATE _sync_config SET group_id='00000000-0000-4000-8000-000000000002'")
    supervisor.drain = drain_and_change_scope
    monkeypatch.setattr('sys.stdin', io.StringIO('REPLACE\n'))
    try:
        result = mac_replace.replace(manifest, Path(sys.executable).resolve(), supervisor=supervisor)
        assert result['status'] == 'recovery_required'
        assert supervisor.calls == ['drain']
        assert not supervisor.loaded
    finally:
        if supervisor.process:
            original_drain('org.example.agentmesh', 30)


def test_plan_is_readonly_and_rejects_unexpected_service(tmp_path):
    import mac_replace
    manifest, args, plist = fixture_manifest(tmp_path)
    supervisor = FakeSupervisor(plist)
    binary = Path(sys.executable).resolve()  # Read-only planning never executes it.
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    report = mac_replace.plan(manifest, binary, supervisor=supervisor)
    assert report['status'] == 'planned'
    assert report['policy'] == 'legacy'
    assert before == {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert not supervisor.calls
    config = plistlib.loads(plist.read_bytes())
    config['ProgramArguments'][-1] = '30'
    plist.write_bytes(plistlib.dumps(config))
    with pytest.raises(ValueError, match='expected service'):
        mac_replace.plan(manifest, binary, supervisor=supervisor)


@pytest.mark.parametrize('unsafe', ['relative_interpreter', 'ota'])
def test_plan_refuses_unbounded_or_ota_owned_service(tmp_path, unsafe):
    import mac_replace
    from signed_packets import write_local
    manifest, args, plist = fixture_manifest(tmp_path)
    if unsafe == 'ota':
        write_local(args['database'].parent.parent / 'ota-state.json', {'active': 1})
    else:
        config = json.loads(manifest.read_text())
        service = plistlib.loads(plist.read_bytes())
        service['ProgramArguments'][0] = 'python'
        config['expected_args'] = service['ProgramArguments']
        manifest.write_text(json.dumps(config))
        plist.write_bytes(plistlib.dumps(service))
    with pytest.raises(ValueError, match='absolute|OTA'):
        mac_replace.plan(manifest, Path(sys.executable).resolve(), supervisor=FakeSupervisor(plist))


@pytest.mark.skipif(sys.platform != 'darwin', reason='Mac operator rehearsal')
def test_replace_real_binary_wal_backup_then_rollback_never_restores_db(tmp_path, monkeypatch):
    import mac_replace
    binary_path = os.environ.get('AGENTMESH_REPLACEMENT_BINARY')
    if not binary_path:
        pytest.skip('compiled rehearsal requires AGENTMESH_REPLACEMENT_BINARY')
    manifest, args, plist = fixture_manifest(tmp_path)
    supervisor = FakeSupervisor(plist)
    old_plist, workflow = plist.read_bytes(), args['workflow_config'].read_bytes()
    monkeypatch.setattr('sys.stdin', io.StringIO('REPLACE\nBIND\n'))
    with closing(sqlite3.connect(args['database'])) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('CREATE TABLE trial_probe(value TEXT)')
        writer.execute("INSERT INTO trial_probe VALUES('before')")
        writer.commit()
        old_process = subprocess.Popen(plistlib.loads(plist.read_bytes())['ProgramArguments'], stdout=subprocess.PIPE, text=True)
        supervisor.process = old_process
        assert old_process.stdout.readline() == 'cycle-open\n'
        report = mac_replace.replace(manifest, binary_path, supervisor=supervisor, timeout=30)
        try:
            assert report['status'] == 'replaced', report
            assert old_process.poll() is not None
            old_process.stdout.close()
            assert supervisor.calls == ['drain', 'bootstrap']
            assert args['workflow_config'].read_bytes() == workflow
            new_plist = plistlib.loads(plist.read_bytes())
            assert new_plist['EnvironmentVariables'] == {'SYNTHETIC_SETTING': 'preserve'}
            assert new_plist['ProgramArguments'][1:4] == ['worker-run', '--runtime', str(args['runtime'])]
            backup = Path(report['backup'])
            with closing(sqlite3.connect(backup / 'database.sqlite')) as saved:
                assert saved.execute('PRAGMA integrity_check').fetchone() == ('ok',)
                assert saved.execute('PRAGMA foreign_key_check').fetchall() == []
                assert saved.execute('SELECT value FROM trial_probe').fetchall() == [('before',)]
            writer.execute("INSERT INTO trial_probe VALUES('valid-new-data')")
            writer.commit()
            monkeypatch.setattr('sys.stdin', io.StringIO('ROLLBACK\n'))
            rollback = mac_replace.rollback(backup, supervisor=supervisor, timeout=30)
            assert rollback['status'] == 'rolled_back'
            assert plist.read_bytes() == old_plist
            assert writer.execute('SELECT value FROM trial_probe').fetchall() == [('before',), ('valid-new-data',)]
            assert args['workflow_config'].read_bytes() == workflow
            assert not args['runtime'].exists()
        finally:
            supervisor.drain('org.example.agentmesh', 30)


def test_plan_rejects_existing_runtime_mismatch_before_drain(tmp_path):
    import mac_replace
    from signed_packets import write_local
    manifest, args, plist = fixture_manifest(tmp_path)
    wrong = json.loads(manifest.read_text())
    wrong['node'] = 'windows'
    write_local(args['runtime'], wrong)
    supervisor = FakeSupervisor(plist)
    with pytest.raises(ValueError, match='runtime|scope'):
        mac_replace.plan(manifest, Path(sys.executable).resolve(), supervisor=supervisor)
    assert supervisor.calls == []


def test_launchd_drain_waits_for_bootloader_descendants_without_signalling_pids(monkeypatch):
    import mac_replace
    import worker_lifecycle
    supervisor = mac_replace.Launchd()
    snapshots = iter([{'loaded': True, 'pid': 100, 'arguments': []},
                      {'loaded': False, 'pid': None, 'arguments': []}])
    monkeypatch.setattr(supervisor, 'inspect', lambda label: next(snapshots))
    commands = []
    def command(argv, **kw):
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout='100 1\n101 100\n102 101\n', stderr='')
    monkeypatch.setattr(mac_replace.subprocess, 'run', command)
    observed = []
    monkeypatch.setattr(worker_lifecycle, 'alive', lambda pid: observed.append(pid) or False)
    supervisor.drain('org.example.agentmesh', 1)
    assert set(observed) == {100, 101, 102}
    assert not any('kill' in cmd for cmd in commands)


def test_declined_replace_and_failed_drain_make_no_installation(tmp_path, monkeypatch):
    import mac_replace
    manifest, args, plist = fixture_manifest(tmp_path)
    binary = Path(sys.executable).resolve()
    supervisor = FakeSupervisor(plist)
    monkeypatch.setattr('sys.stdin', io.StringIO('\n'))
    assert mac_replace.replace(manifest, binary, supervisor=supervisor)['status'] == 'pending'
    assert not supervisor.calls
    def failed_drain(label, timeout):
        raise TimeoutError('synthetic no drain')
    supervisor.drain = failed_drain
    monkeypatch.setattr('sys.stdin', io.StringIO('REPLACE\n'))
    assert mac_replace.replace(manifest, binary, supervisor=supervisor)['status'] == 'recovery_required'
    assert not args['runtime'].exists()
    assert not (tmp_path / 'backups').exists()
    assert not (tmp_path / 'versions').exists()


def test_managed_upgrade_plan_accepts_preserved_float_interval(tmp_path, monkeypatch):
    import mac_replace
    from install_adopt import adopt
    manifest, args, plist = fixture_manifest(tmp_path)
    monkeypatch.setattr('sys.stdin', io.StringIO('BIND\n'))
    adopt(**args)
    config = json.loads(manifest.read_text())
    service = plistlib.loads(plist.read_bytes())
    binary = Path(sys.executable).resolve()
    service['ProgramArguments'] = [str(binary), 'worker-run', '--runtime', str(args['runtime']), '--interval', '60.0', '--legacy-drained']
    plist.write_bytes(plistlib.dumps(service))
    config['expected_args'] = service['ProgramArguments']
    manifest.write_text(json.dumps(config))
    assert mac_replace.plan(manifest, binary, supervisor=FakeSupervisor(plist))['interval'] == 60


@pytest.mark.parametrize('kind', ['identity', 'overlap'])
def test_private_roots_cannot_overlap_identity_or_each_other(tmp_path, kind):
    import mac_replace
    manifest, args, plist = fixture_manifest(tmp_path)
    config = json.loads(manifest.read_text())
    if kind == 'identity':
        args['security_dir'].mkdir(mode=0o700)
        config['install_root'] = str(args['security_dir'])
    else:
        config['backup_root'] = config['install_root'] + '/backups'
        Path(config['install_root']).mkdir(mode=0o700)
    manifest.write_text(json.dumps(config))
    with pytest.raises(ValueError, match='separate|overlap'):
        mac_replace.plan(manifest, Path(sys.executable).resolve(), supervisor=FakeSupervisor(plist))


def test_cli_mac_dry_run_has_no_supervisor_mutations(tmp_path, monkeypatch, capsys):
    import agentmesh
    import mac_replace
    manifest, args, plist = fixture_manifest(tmp_path)
    supervisor = FakeSupervisor(plist)
    monkeypatch.setattr(mac_replace, 'Launchd', lambda: supervisor)
    assert agentmesh.main(['mac-replace', '--manifest', str(manifest), '--binary', str(Path(sys.executable).resolve()), '--dry-run']) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'planned'
    assert not supervisor.calls


@pytest.mark.skipif(sys.platform != 'darwin', reason='Mac operator rehearsal')
@pytest.mark.parametrize('strict,fail_bootstrap', [(False, True), (True, False)])
def test_compiled_strict_preservation_or_failed_bootstrap_rollback(tmp_path, monkeypatch, strict, fail_bootstrap):
    import mac_replace
    from signed_packets import init_identity, Security
    import memory_sync
    binary = os.environ.get('AGENTMESH_REPLACEMENT_BINARY')
    if not binary:
        pytest.skip('compiled rehearsal requires AGENTMESH_REPLACEMENT_BINARY')
    manifest, args, plist = fixture_manifest(tmp_path)
    init_identity(args['security_dir'], '00000000-0000-4000-8000-000000000001', 'mac')
    keys = {p: p.read_bytes() for p in args['security_dir'].iterdir()}
    if strict:
        with memory_sync.connect(args['database']) as c, c:
            c.execute("INSERT INTO memory_items(id,kind,scope,content,metadata) VALUES(1,'fact','global','disposable strict fact','{}')")
            Security(args['security_dir']).guard(c, args['exchange'])
        service = plistlib.loads(plist.read_bytes())
        service['ProgramArguments'] += ['--security-dir', str(args['security_dir'])]
        plist.write_bytes(plistlib.dumps(service))
        config = json.loads(manifest.read_text())
        config['expected_args'] = service['ProgramArguments']
        manifest.write_text(json.dumps(config))
    old_plist = plist.read_bytes()
    supervisor = FakeSupervisor(plist)
    if fail_bootstrap:
        original = supervisor.bootstrap
        def fail_selected(path):
            if 'worker-run' in plistlib.loads(Path(path).read_bytes())['ProgramArguments']:
                raise OSError('synthetic service refusal')
            original(path)
        supervisor.bootstrap = fail_selected
    monkeypatch.setattr('sys.stdin', io.StringIO('REPLACE\nBIND\n'))
    report = mac_replace.replace(manifest, binary, supervisor=supervisor, timeout=30)
    try:
        assert report['status'] == ('rolled_back' if fail_bootstrap else 'replaced'), report
        assert {p: p.read_bytes() for p in args['security_dir'].iterdir()} == keys
        if fail_bootstrap:
            assert plist.read_bytes() == old_plist
            assert not args['runtime'].exists()
            assert report['error'] == 'OSError'
        else:
            assert report['policy'] == 'required'
            assert json.loads((args['exchange'] / 'status/mac.json').read_text())['security']['policy'] == 'required'
            packets = list((args['exchange'] / 'signed-changes/mac').glob('*.json'))
            assert len(packets) == 1
            packet = json.loads(packets[0].read_text())
            Security(args['security_dir']).verify(packet, '00000000-0000-4000-8000-000000000001', 'mac')
            import agentmesh
            monkeypatch.setattr(mac_replace, 'Launchd', lambda: supervisor)
            monkeypatch.setattr('sys.stdin', io.StringIO('ROLLBACK\n'))
            assert agentmesh.main(['mac-rollback', '--backup', report['backup']]) == 0
            assert plist.read_bytes() == old_plist
    finally:
        supervisor.drain('org.example.agentmesh', 30)


@pytest.mark.skipif(sys.platform != 'darwin', reason='Mac operator rehearsal')
def test_rollback_refuses_changed_old_code_before_stopping_new_service(tmp_path, monkeypatch):
    import mac_replace
    binary = os.environ.get('AGENTMESH_REPLACEMENT_BINARY')
    if not binary:
        pytest.skip('compiled rehearsal requires AGENTMESH_REPLACEMENT_BINARY')
    manifest, args, plist = fixture_manifest(tmp_path)
    supervisor = FakeSupervisor(plist)
    monkeypatch.setattr('sys.stdin', io.StringIO('REPLACE\nBIND\n'))
    report = mac_replace.replace(manifest, binary, supervisor=supervisor, timeout=30)
    try:
        assert report['status'] == 'replaced'
        calls = list(supervisor.calls)
        (args['app_root'] / 'sync_worker.py').write_text('# changed legacy code\n')
        monkeypatch.setattr('sys.stdin', io.StringIO('ROLLBACK\n'))
        with pytest.raises(ValueError, match='old code'):
            mac_replace.rollback(report['backup'], supervisor=supervisor, timeout=30)
        assert supervisor.calls == calls
    finally:
        supervisor.drain('org.example.agentmesh', 30)
