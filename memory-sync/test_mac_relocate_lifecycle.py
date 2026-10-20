"""Full disposable relocation, with fake process and transport boundaries."""
from contextlib import closing
import io
import json
from pathlib import Path
import plistlib
import sqlite3

import pytest

from signed_packets import write_local, Security
from test_mac_relocate import installation, Transport, snapshot


class Services:
    def __init__(self, plists):
        self.services = {plistlib.loads(p.read_bytes())['Label']: plistlib.loads(p.read_bytes()) for p in plists}
        self.calls = []

    def inspect(self, label):
        value = self.services.get(label)
        return dict(loaded=value is not None, pid=123 if value else None,
                    arguments=value['ProgramArguments'] if value else [])

    def drain(self, label, timeout):
        self.calls.append(('drain', label))
        self.services.pop(label, None)

    def bootstrap(self, plist):
        value = plistlib.loads(Path(plist).read_bytes())
        self.services[value['Label']] = value
        self.calls.append(('bootstrap', value['Label']))


class Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, *, timeout, env=None):
        self.calls.append(argv)
        if 'relocation-hook-probe' in argv:
            return {'calls': [['synthetic-bridge', 'migrate', 'rows']]}
        if 'catch-up' in argv:
            return dict(outcomes={'unchanged': 1}, errors=[], unmapped=[])
        if 'recall' in argv:
            return {'backend': 'broker'}
        return {'status': 'completed'}


@pytest.fixture
def full_install(installation, tmp_path, monkeypatch):
    import mac_relocate
    manifest_path, manifest, args, _, transport = installation
    jobs = manifest['jobs']
    jobs['plists'] = []
    old_python = tmp_path / 'old-memory/.venv/bin/python'
    old_python.parent.mkdir(mode=0o700, parents=True)
    old_python.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
    old_python.chmod(0o700)
    for role in ('ingest', 'summary', 'insight'):
        script = tmp_path / (role + '.py')
        script.write_text('# synthetic ' + str(args['database']) + '\n', encoding='utf-8')
        jobs['files'][str(script)] = script.name
        plist = tmp_path / (role + '.plist')
        label = 'org.synthetic.' + role
        plist.write_bytes(plistlib.dumps(dict(Label=label,
            ProgramArguments=[str(tmp_path / 'old-memory' / '.venv' / 'bin' / 'python'),
                              str(script)], StandardOutPath=str(args['database'].parent / (role + '.log')))))
        jobs['plists'].append(dict(plist=str(plist), label=label))
    root = Path(manifest['to_root'])
    bridge = dict(enabled=True, recall=True, dual_write=True, authority=True,
                  profile='work', timeout_seconds=10, recall_agent='recall', import_agent='importer',
                  cli_argv=[str(tmp_path / 'broker-python'), '-m', 'agentmesh_log.cli'],
                  database=str(args['database']))
    write_local(root / 'settings.local.json', {'bridge': bridge})
    (root / 'agents').mkdir(mode=0o700)
    write_local(root / 'migration-mapping.json', {'synthetic': True})
    for name in ('recall', 'importer'):
        (root / 'agents' / (name + '.key')).write_bytes(b'synthetic-not-a-real-key')
        (root / 'agents' / (name + '.key')).chmod(0o600)
    write_local(Path(manifest['rules'][0]), {'recall_argv': [
        str(tmp_path / 'old-memory' / '.venv' / 'bin' / 'python'),
        str(args['app_root'] / 'agentmesh.py'), '--database', str(args['database']), 'recall']})
    requirements = Path(jobs['venv_requirements'])
    (requirements.parent / 'wheels').mkdir(mode=0o700, exist_ok=True)
    release = tmp_path / 'release'
    release.mkdir(mode=0o700)
    for name in ('agentmesh.py', 'profile_log_bridge.py', 'schema.sql'):
        (release / name).write_text('# synthetic release\n', encoding='utf-8')
    write_local(manifest_path, manifest)
    with closing(sqlite3.connect(args['database'])) as db, db:
        db.execute("INSERT INTO memory_items(id,kind,scope,content) VALUES(1,'fact','global','synthetic')")
    supervisor = Services([Path(manifest['from']['plist'])] +
                          [Path(item['plist']) for item in jobs['plists']])
    runner = Runner()
    def venv_builder(destination, requirements, *, timeout):
        (destination / 'bin').mkdir(mode=0o700, parents=True)
        (destination / 'bin' / 'python').write_text('# synthetic interpreter\n', encoding='utf-8')
        (destination / 'bin' / 'python').chmod(0o700)
    def health(supervisor, service, runtime, **kwargs):
        assert supervisor.inspect(service['Label'])['arguments'] == service['ProgramArguments']
        assert Path(runtime).is_file()
    monkeypatch.setattr(mac_relocate, 'wait_health', health, raising=False)
    return dict(manifest=manifest_path, supervisor=supervisor, transport=transport,
                runner=runner, venv_builder=venv_builder, release_source=release), manifest, args


def move(options, monkeypatch, **kwargs):
    import mac_relocate
    monkeypatch.setattr('sys.stdin', io.StringIO('RELOCATE\nBIND\n'))
    return mac_relocate.relocate(**options, **kwargs)


def test_full_move_rewrites_references_archives_and_preserves_identity(full_install, monkeypatch):
    import mac_relocate
    options, manifest, args = full_install
    identities = {p.name: p.read_bytes() for p in args['security_dir'].iterdir()}
    sentinel = Path(manifest['to_root']) / 'profile-log' / 'preserve.json'
    unchanged = sentinel.read_bytes()
    result = move(options, monkeypatch)
    assert result['status'] == 'relocated', result
    root = Path(manifest['to_root'])
    runtime = json.loads((root / 'memory-sync/data/runtime.json').read_text(encoding='utf-8'))
    assert runtime['database'] == str(root / 'memory-sync/data/mac.db')
    assert {p.name: p.read_bytes() for p in (root / 'memory-sync/identity').iterdir()} == identities
    assert sentinel.read_bytes() == unchanged
    assert options['transport'].exchange == str(root / 'exchange')
    for p in [Path(manifest['from']['plist']), Path(manifest['settings_local']),
              *map(Path, manifest['rules']), *[Path(j['plist']) for j in manifest['jobs']['plists']],
              root / 'jobs/agentmesh-production.json']:
        assert str(args['app_root']).encode() not in p.read_bytes()
    journal = json.loads((Path(result['backup']) / 'relocation.json').read_text(encoding='utf-8'))
    assert [s['name'] for s in journal['steps']] == list(mac_relocate.STEPS)
    assert all(s['state'] == 'completed' for s in journal['steps'])
    assert all(not p.stat().st_mode & 0o222 for p in (root / 'backups/archive').rglob('*'))
    assert not args['app_root'].exists()
    assert 'unchanged' in result['verification']['catch_up']['outcomes']


@pytest.mark.parametrize('when', ['', 'before:'])
@pytest.mark.parametrize('step', [
    'preflight', 'drain', 'lock jobs', 'pause syncthing', 'copy db', 'copy state/identity',
    'copy exchange', 'copy app files', 'copy jobs', 'rebind runtime (adopt)',
    'rewrite plists/configs/rules/settings.local', 'syncthing path + resume', 'start', 'verify', 'archive'])
def test_failure_after_every_step_restores_original_service(full_install, monkeypatch, step, when):
    options, manifest, args = full_install
    old_service = Path(manifest['from']['plist']).read_bytes()
    old_runtime = args['runtime'].read_bytes()
    def fail(name):
        if name == when + step:
            raise RuntimeError('synthetic failure')
    result = move(options, monkeypatch, fault=fail)
    assert result['status'] == 'rolled_back', result
    assert Path(manifest['from']['plist']).read_bytes() == old_service
    assert args['runtime'].read_bytes() == old_runtime
    assert options['supervisor'].inspect('org.example.sync')['arguments'] == plistlib.loads(old_service)['ProgramArguments']
    assert options['transport'].exchange == manifest['from']['exchange']
    assert not options['transport'].paused


def test_manual_rollback_retains_new_rows_and_reports_them(full_install, monkeypatch):
    import mac_relocate
    options, manifest, args = full_install
    result = move(options, monkeypatch)
    assert result['status'] == 'relocated', result
    new_db = Path(manifest['to_root']) / 'memory-sync/data/mac.db'
    with closing(sqlite3.connect(new_db)) as db, db:
        db.execute("INSERT INTO memory_items(id,kind,scope,content) VALUES(2,'fact','global','post move')")
    monkeypatch.setattr('sys.stdin', io.StringIO('ROLLBACK\n'))
    restored = mac_relocate.rollback(result['backup'], supervisor=options['supervisor'],
                                    transport=options['transport'])
    assert restored['status'] == 'rolled_back', restored
    assert restored['post_move_rows']['memory_items'] == 1
    assert mac_relocate.table_counts(new_db)['memory_items'] == 2
    assert 'backups/archive' in options['supervisor'].inspect('org.example.sync')['arguments'][3]


def test_end_to_end_dry_run_decline_and_eof_leave_everything_untouched(full_install, tmp_path, monkeypatch):
    import mac_relocate
    options, _, _ = full_install
    before = snapshot(tmp_path)
    planned = mac_relocate.plan(**{k: v for k, v in options.items() if k in
                                  ('manifest', 'supervisor', 'transport', 'release_source')})
    assert mac_relocate.relocate(**options, dry_run=True) == planned
    assert snapshot(tmp_path) == before
    monkeypatch.setattr('sys.stdin', io.StringIO('NO\n'))
    assert mac_relocate.relocate(**options)['status'] == 'pending'
    assert snapshot(tmp_path) == before
    monkeypatch.setattr('sys.stdin', io.StringIO(''))
    with pytest.raises(EOFError):
        mac_relocate.relocate(**options)
    assert snapshot(tmp_path) == before


def test_moved_identity_signs_packets_accepted_by_peer(full_install, tmp_path, monkeypatch):
    import memory_sync
    import sqlite_memory
    import signed_packets
    options, manifest, args = full_install
    old_identity = Security(args['security_dir'])
    with memory_sync.connect(args['database']) as db, db:
        old_identity.guard(db, args['exchange'])
    service_path = Path(manifest['from']['plist'])
    service = plistlib.loads(service_path.read_bytes())
    service['ProgramArguments'].remove('--legacy-drained')
    service_path.write_bytes(plistlib.dumps(service))
    options['supervisor'].services[service['Label']] = service
    peer_db = tmp_path / 'peer.db'
    sqlite_memory.init_database(peer_db)
    group = old_identity.public['group']
    memory_sync.initialize(peer_db, 'windows', group)
    peer_dir = tmp_path / 'peer-identity'
    signed_packets.init_identity(peer_dir, group, 'windows')
    signed_packets.approve(peer_dir, old_identity.public, old_identity.public['key_id'],
                           group, 'mac', old_identity.public['sender'])
    result = move(options, monkeypatch)
    assert result['status'] == 'relocated', {k: result.get(k) for k in ('error', 'failed_step')}
    root = Path(manifest['to_root'])
    moved = Security(root / 'memory-sync/identity')
    assert moved.public['key_id'] == old_identity.public['key_id']
    db_path = root / 'memory-sync/data/mac.db'
    with memory_sync.connect(db_path) as db, db:
        db.execute("INSERT INTO memory_items(id,kind,scope,content) VALUES(2,'fact','global','peer rehearsal')")
    memory_sync.capture(db_path, security=moved)
    assert memory_sync.publish(db_path, root / 'exchange', security=moved)['published'] > 0
    assert memory_sync.receive(peer_db, root / 'exchange', security=Security(peer_dir))['applied'] > 0
    with closing(sqlite3.connect(peer_db)) as db:
        assert db.execute('SELECT count(*) FROM memory_items WHERE id=2').fetchone() == (1,)


@pytest.mark.parametrize('target', ['custom-wizard.json', 'identity.json', 'exchange', 'agentmesh.py', 'ingest.py'])
def test_corrupt_each_file_copy_restores_service(full_install, monkeypatch, target):
    import mac_relocate
    options, manifest, args = full_install
    marker = args['exchange'] / 'exchange'
    marker.write_text('synthetic exchange data', encoding='utf-8')
    real_blob, real_copy = mac_relocate.blob, mac_relocate.shutil.copyfile
    def blob(filename, data, **kwargs):
        if Path(filename).name == target and 'backups' not in Path(filename).parts:
            data = b'corrupt synthetic copy'
        return real_blob(filename, data, **kwargs)
    def copy(source, destination, **kwargs):
        value = real_copy(source, destination, **kwargs)
        if Path(destination).name == target:
            Path(destination).write_bytes(b'corrupt synthetic copy')
        return value
    monkeypatch.setattr(mac_relocate, 'blob', blob)
    monkeypatch.setattr(mac_relocate.shutil, 'copyfile', copy)
    result = move(options, monkeypatch)
    assert result['status'] == 'rolled_back', result.get('failed_step')
    assert options['supervisor'].inspect('org.example.sync')['arguments'][0] == manifest['from']['binary']


def test_verification_failure_restores_and_never_leaks_output(full_install, monkeypatch, capsys):
    options, manifest, _ = full_install
    real = options['runner']
    def fail_recall(argv, **kwargs):
        if 'recall' in argv:
            return {'backend': 'sqlite', 'private': 'SYNTHETIC-API-SECRET'}
        return real(argv, **kwargs)
    options['runner'] = fail_recall
    result = move(options, monkeypatch)
    assert result['status'] == 'rolled_back'
    assert result['failed_step'] == 'verify'
    assert 'SYNTHETIC-API-SECRET' not in json.dumps(result) + str(capsys.readouterr())


def test_rollback_reports_updates_and_keeps_new_exchange_packets(full_install, monkeypatch):
    import mac_relocate
    options, manifest, _ = full_install
    result = move(options, monkeypatch)
    assert result['status'] == 'relocated'
    root = Path(manifest['to_root'])
    with closing(sqlite3.connect(root / 'memory-sync/data/mac.db')) as db, db:
        db.execute("UPDATE memory_items SET content='changed after move' WHERE id=1")
    packet = root / 'exchange/signed-changes/mac/synthetic.json'
    packet.parent.mkdir(mode=0o700, parents=True)
    packet.write_bytes(b'opaque synthetic packet')
    monkeypatch.setattr('sys.stdin', io.StringIO('ROLLBACK\n'))
    restored = mac_relocate.rollback(result['backup'], supervisor=options['supervisor'], transport=options['transport'])
    assert restored['post_move_rows']['memory_items'] == 1
    assert (Path(options['transport'].exchange) / 'signed-changes/mac/synthetic.json').read_bytes() == packet.read_bytes()


def test_rewrite_is_boundary_aware_and_non_cascading():
    import mac_relocate
    mapping = {'/old/app': '/old/app/new', '/old/app/data': '/new/data'}
    assert mac_relocate.rewrite('/old/application /old/app /old/app/data/db', mapping) == '/old/application /old/app/new /new/data/db'


@pytest.mark.parametrize('failed_label', ['org.example.sync', 'org.synthetic.summary'])
def test_explicit_rollback_can_resume_after_bootstrap_failure(full_install, monkeypatch, failed_label):
    import mac_relocate
    options, _, _ = full_install
    moved = move(options, monkeypatch)
    assert moved['status'] == 'relocated'
    real = options['supervisor'].bootstrap
    def bootstrap(plist):
        value = plistlib.loads(Path(plist).read_bytes())
        if value['Label'] == failed_label:
            raise OSError('synthetic rollback bootstrap failure')
        return real(plist)
    monkeypatch.setattr(options['supervisor'], 'bootstrap', bootstrap)
    monkeypatch.setattr('sys.stdin', io.StringIO('ROLLBACK\n'))
    with pytest.raises(OSError):
        mac_relocate.rollback(moved['backup'], supervisor=options['supervisor'], transport=options['transport'])
    monkeypatch.setattr(options['supervisor'], 'bootstrap', real)
    monkeypatch.setattr('sys.stdin', io.StringIO('ROLLBACK\n'))
    result = mac_relocate.rollback(moved['backup'], supervisor=options['supervisor'], transport=options['transport'])
    assert result['status'] == 'rolled_back'
    assert 'backups/archive' in options['supervisor'].inspect('org.example.sync')['arguments'][3]


def test_installed_bridge_hook_executes_real_child_code(full_install, monkeypatch):
    import mac_relocate
    import sys
    options, config, _ = full_install
    source = Path(__file__).with_name('profile_log_bridge.py')
    (options['release_source'] / 'profile_log_bridge.py').write_text(
        source.read_text(encoding='utf-8'), encoding='utf-8')
    fake = options['runner']
    observed = []
    def runner(argv, **kwargs):
        if 'relocation-hook-probe' in argv:
            value = mac_relocate.execute([sys.executable, *argv[1:]], **kwargs)
            observed.extend(value['calls'])
            return value
        return fake(argv, **kwargs)
    options['runner'] = runner
    result = move(options, monkeypatch)
    assert result['status'] == 'relocated'
    assert len(observed) == 1
    assert observed[0][3:5] == ['migrate', 'rows']
    assert '--ref' in observed[0] and 'memory_items:1' in observed[0]
    assert str(Path(config['to_root']) / 'memory-sync/data/mac.db') in observed[0]


@pytest.mark.parametrize('outcomes,ok', [
    ({'unchanged': 3, 'created': 1, 'revised': 1, 'retired': 1}, True),
    ({'unchanged': 3, 'failed': 1}, False),
    ({'created': 1}, False),
])
def test_catch_up_accepts_rows_added_during_verification(full_install, monkeypatch, outcomes, ok):
    # The summary tick may add memory rows that catch-up imports; anything else still fails closed.
    options, manifest, args = full_install
    runner = options['runner']
    original = runner.__call__

    def call(argv, *, timeout, env=None):
        if 'catch-up' in argv:
            runner.calls.append(argv)
            return dict(outcomes=dict(outcomes), errors=[], unmapped=[])
        return original(argv, timeout=timeout, env=env)
    options = dict(options, runner=call)
    result = move(options, monkeypatch)
    assert (result['status'] == 'relocated') is ok, result
