"""Exact Mac argv layouts inside a disposable home and synthetic system root."""
import io
import json
import os
from pathlib import Path
import plistlib
import stat

import pytest

from signed_packets import write_local
from test_mac_relocate import installation
from test_mac_relocate_lifecycle import full_install, Services, move


def nofollow_snapshot(root):
    result = {'.': (root.lstat().st_mode, root.lstat().st_mtime_ns)}
    for p in root.rglob('*'):
        mode = p.lstat().st_mode
        result[str(p.relative_to(root))] = (
            mode, p.lstat().st_mtime_ns,
            os.readlink(p) if stat.S_ISLNK(mode) else p.read_bytes() if p.is_file() else None)
    return result


@pytest.fixture
def real_jobs(full_install, tmp_path, monkeypatch):
    options, config, args = full_install
    home = tmp_path / 'H'
    memory = home / '.hermes/omp-memory'
    memory.parent.mkdir(mode=0o700, parents=True)
    old_memory, old_app = args['app_root'].parent, args['app_root']
    old_memory.rename(memory)
    (memory / old_app.name).rename(memory / 'sqlite-sync')
    mapping = {str(old_app): str(memory / 'sqlite-sync'), str(old_memory): str(memory)}

    def renamed(value):
        for old, new in mapping.items():
            value = value.replace(old, new)
        return value

    # Only synthetic descriptors; no real host or private credential is read.
    for p in tmp_path.rglob('*'):
        if p.is_file() and p.suffix in ('.json', '.plist', '.py'):
            content = p.read_text(encoding='utf-8')
            updated = renamed(content)
            if updated != content:
                p.write_text(updated, encoding='utf-8')
    args = {k: Path(renamed(str(v))) for k, v in args.items()}
    config = json.loads(renamed(json.dumps(config)))
    options['transport'].exchange = config['from']['exchange']
    scripts = home / '.hermes/scripts'
    scripts.mkdir(mode=0o700)
    jobs = config['jobs']
    jobs['files'] = {}
    for name in ('omp_memory_ingest.py', 'omp_memory_summary_cron.py', 'agentmesh_production.py'):
        p = scripts / name
        p.write_text('# synthetic standard-library job\n', encoding='utf-8')
        jobs['files'][str(p)] = name
    Path(jobs['production_config']).rename(memory / 'agentmesh-production.json')
    jobs['production_config'] = str(memory / 'agentmesh-production.json')
    Path(jobs['status_dir']).rename(memory / 'production-status')
    jobs['status_dir'] = str(memory / 'production-status')
    insight = Path(jobs['status_dir']) / 'insight-release-a1b2c3/server.py'
    insight.parent.mkdir(mode=0o700)
    insight.write_text('# synthetic Insight server\n', encoding='utf-8')
    wrapper = args['app_root'] / 'job_notify.py'
    wrapper.write_text('# synthetic standard-library wrapper\n', encoding='utf-8')
    (options['release_source'] / 'job_notify.py').write_bytes(wrapper.read_bytes())
    webhook = home / '.config/agentmesh/discord-webhook'
    webhook.parent.mkdir(mode=0o700, parents=True)
    webhook.write_text('synthetic placeholder', encoding='utf-8')
    webhook.chmod(0o600)
    system = tmp_path / 'system'
    python = system / 'Library/Frameworks/Python.framework/Versions/3.10/bin/python3.10'
    python.parent.mkdir(mode=0o700, parents=True)
    python.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
    python.chmod(0o755)
    middle = system / 'usr/local/bin/python3'
    middle.parent.mkdir(mode=0o700, parents=True)
    middle.symlink_to(python)
    venv_python = memory / '.venv/bin/python'
    venv_python.unlink()
    venv_python.symlink_to(middle)
    homebrew_real = system / 'opt/homebrew/Cellar/python/3.14/bin/python3.14'
    homebrew_real.parent.mkdir(mode=0o700, parents=True)
    homebrew_real.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
    homebrew_real.chmod(0o700)
    homebrew = system / 'opt/homebrew/bin/python3'
    homebrew.parent.mkdir(mode=0o700)
    homebrew.symlink_to(homebrew_real)
    for job in jobs['plists']:
        role = job['label'].rsplit('.', 1)[-1]
        if role == 'insight':
            argv = [str(homebrew), '-u', str(insight), '--sqlite', str(args['database']), '--port', '51877']
        else:
            script = scripts / ('omp_memory_ingest.py' if role == 'ingest' else 'omp_memory_summary_cron.py')
            argv = [str(venv_python), str(wrapper), '--name', role,
                    '--state-dir', jobs['status_dir'], '--webhook-file', str(webhook),
                    '--', str(venv_python), str(script)]
        Path(job['plist']).write_bytes(plistlib.dumps(dict(Label=job['label'], ProgramArguments=argv)))
    Path(jobs['venv_requirements']).write_text('', encoding='utf-8')
    (Path(jobs['venv_requirements']).parent / 'wheels').rmdir()
    config['archive'].append(str(memory / '.venv'))
    write_local(options['manifest'], config)
    options['supervisor'] = Services([Path(config['from']['plist']),
                                     *[Path(j['plist']) for j in jobs['plists']]])
    real_stat = Path.stat

    def root_owned(p, **kw):
        result = real_stat(p, **kw)
        if p == python:
            values = list(result)
            values[4] = 0
            return os.stat_result(values)
        return result

    monkeypatch.setattr(Path, 'stat', root_owned)
    return options, config, args, dict(python=python, homebrew=homebrew, webhook=webhook)


def test_exact_real_layout_dry_run_is_readonly(real_jobs, tmp_path):
    import mac_relocate
    options, _, _, _ = real_jobs
    before = nofollow_snapshot(tmp_path)
    assert mac_relocate.relocate(**options, dry_run=True)['status'] == 'planned'
    assert nofollow_snapshot(tmp_path) == before
    assert not options['supervisor'].calls


def test_wrapper_flags_status_and_both_interpreters_rewritten(real_jobs, monkeypatch):
    options, config, args, paths = real_jobs
    result = move(options, monkeypatch)
    assert result['status'] == 'relocated', result
    root = Path(config['to_root'])
    for job in config['jobs']['plists']:
        argv = plistlib.loads(Path(job['plist']).read_bytes())['ProgramArguments']
        if job['label'].endswith('insight'):
            assert argv == [str(paths['homebrew']), '-u',
                            str(root / 'status/insight-release-a1b2c3/server.py'),
                            '--sqlite', str(root / 'memory-sync/data/mac.db'), '--port', '51877']
        else:
            role = job['label'].rsplit('.', 1)[-1]
            name = 'omp_memory_ingest.py' if role == 'ingest' else 'omp_memory_summary_cron.py'
            assert argv == [str(root / 'jobs/.venv/bin/python'), str(root / 'memory-sync/job_notify.py'),
                            '--name', role, '--state-dir', str(root / 'status'),
                            '--webhook-file', str(paths['webhook']), '--',
                            str(root / 'jobs/.venv/bin/python'), str(root / 'jobs' / name)]
            assert argv in options['runner'].calls
    assert paths['webhook'].read_text(encoding='utf-8') == 'synthetic placeholder'


@pytest.mark.parametrize('flags', [['-u'], ['-I', '-B']])
def test_interpreter_flags_before_script(real_jobs, flags):
    import mac_relocate
    options, config, _, _ = real_jobs
    job = config['jobs']['plists'][2]
    value = plistlib.loads(Path(job['plist']).read_bytes())
    value['ProgramArguments'][1:2] = flags
    Path(job['plist']).write_bytes(plistlib.dumps(value))
    options['supervisor'].services[job['label']] = value
    assert mac_relocate.relocate(**options, dry_run=True)['status'] == 'planned'


def test_resolved_root_interpreter_used_for_empty_venv(real_jobs, monkeypatch):
    import mac_relocate
    options, config, _, paths = real_jobs
    # Distinguish inner from outer: wrapper runs with Homebrew, while its
    # actual job still runs with the root-owned framework interpreter.
    job = config['jobs']['plists'][0]
    value = plistlib.loads(Path(job['plist']).read_bytes())
    value['ProgramArguments'][0] = str(paths['homebrew'])
    Path(job['plist']).write_bytes(plistlib.dumps(value))
    options['supervisor'].services[job['label']] = value
    options.pop('venv_builder')
    calls = []

    def execute(argv, **kw):
        calls.append(argv)
        if '-m' in argv and 'venv' in argv:
            dest = Path(argv[-1])
            (dest / 'bin').mkdir(mode=0o700, parents=True)
            (dest / 'bin/python').write_text('# synthetic copied interpreter\n', encoding='utf-8')
            (dest / 'bin/python').chmod(0o700)
    monkeypatch.setattr(mac_relocate, 'execute', execute)
    assert move(options, monkeypatch)['status'] == 'relocated'
    assert calls[0][:4] == [str(paths['python']), '-m', 'venv', '--copies']
    assert not any('install' in call for call in calls)


def test_archive_copy_inventory_preserves_links_without_dereferencing(tmp_path, monkeypatch):
    import mac_relocate
    source, dest, outside = (tmp_path / name for name in ('archive-source', 'archive-copy', 'outside'))
    source.mkdir(mode=0o700)
    outside.mkdir(mode=0o700)
    sentinel = outside / 'sentinel'
    sentinel.write_text('do not hash or chmod', encoding='utf-8')
    sentinel.chmod(0o600)
    (source / 'file-link').symlink_to(sentinel)
    (source / 'dir-link').symlink_to(outside, target_is_directory=True)
    (source / 'dangling').symlink_to('../missing')
    before = nofollow_snapshot(outside)
    def no_digest(p):
        pytest.fail('archive-only link target must not be hashed')
    monkeypatch.setattr(mac_relocate, 'digest', no_digest)
    expected = mac_relocate.inventory(source, archive_only=True)
    assert expected['file-link'] == 'link -> ' + str(sentinel)
    mac_relocate.copy_tree(source, dest, archive_only=True)
    assert mac_relocate.inventory(dest, archive_only=True) == expected
    assert os.readlink(dest / 'file-link') == str(sentinel)
    assert os.readlink(dest / 'dir-link') == str(outside)
    assert os.readlink(dest / 'dangling') == '../missing'
    assert nofollow_snapshot(outside) == before


def test_group_writable_resolved_interpreter_refused(real_jobs):
    import mac_relocate
    options, _, _, paths = real_jobs
    paths['python'].chmod(0o775)
    with pytest.raises(ValueError, match='interpreter'):
        mac_relocate.relocate(**options, dry_run=True)
    assert not options['supervisor'].calls


@pytest.mark.parametrize('damage', ['missing_separator', 'unknown_option', 'wrong_state', 'unlisted_inner', 'module_flag'])
def test_wrapper_options_and_inner_command_validated(real_jobs, damage):
    import mac_relocate
    options, config, _, _ = real_jobs
    job = config['jobs']['plists'][0]
    value = plistlib.loads(Path(job['plist']).read_bytes())
    argv = value['ProgramArguments']
    if damage == 'missing_separator':
        argv.remove('--')
    elif damage == 'unknown_option':
        argv[2] = '--unsupported'
    elif damage == 'wrong_state':
        argv[5] = config['to_root']
    elif damage == 'unlisted_inner':
        argv[-1] = argv[1]
    else:
        argv.insert(1, '-m')
    Path(job['plist']).write_bytes(plistlib.dumps(value))
    options['supervisor'].services[job['label']] = value
    with pytest.raises(ValueError):
        mac_relocate.relocate(**options, dry_run=True)
    assert not options['supervisor'].calls


def test_archive_links_preserved_without_hashing_or_chmod_target(real_jobs, tmp_path, monkeypatch):
    import mac_relocate
    options, config, args, _ = real_jobs
    venv = args['app_root'].parent / '.venv'
    protected = tmp_path / 'outside'
    protected.mkdir(mode=0o700)
    (protected / 'sentinel').write_text('unchanged', encoding='utf-8')
    (venv / 'directory-link').symlink_to(protected, target_is_directory=True)
    (venv / 'dangling').symlink_to('does-not-exist')
    before = nofollow_snapshot(protected)
    real_digest = mac_relocate.digest

    def no_link_digest(p):
        assert not Path(p).is_symlink(), 'archive link was dereferenced'
        return real_digest(p)
    monkeypatch.setattr(mac_relocate, 'digest', no_link_digest)
    result = move(options, monkeypatch)
    assert result['status'] == 'relocated', result
    archived = next((Path(config['to_root']) / 'backups/archive').glob('.venv.*'))
    assert os.readlink(archived / 'directory-link') == str(protected)
    assert os.readlink(archived / 'dangling') == 'does-not-exist'
    assert nofollow_snapshot(protected) == before
    journal = json.loads((Path(result['backup']) / 'relocation.json').read_text(encoding='utf-8'))
    operation = next(o for o in journal['archives'] if o['source'] == str(venv))
    assert operation['links']['directory-link'] == 'link -> ' + str(protected)
    monkeypatch.setattr('sys.stdin', io.StringIO('ROLLBACK\n'))
    assert mac_relocate.rollback(result['backup'], supervisor=options['supervisor'],
                                 transport=options['transport'])['status'] == 'rolled_back'
    assert nofollow_snapshot(protected) == before


@pytest.mark.parametrize('area', ['data', 'identity', 'exchange', 'status', 'job'])
def test_live_trees_still_refuse_symlinks(real_jobs, tmp_path, area):
    import mac_relocate
    options, config, args, _ = real_jobs
    roots = dict(data=args['database'].parent, identity=args['security_dir'],
                 exchange=args['exchange'], status=Path(config['jobs']['status_dir']))
    if area == 'job':
        p = Path(next(iter(config['jobs']['files'])))
        p.unlink()
    else:
        p = roots[area] / 'forbidden'
    p.symlink_to(tmp_path / 'missing')
    with pytest.raises(ValueError, match='symlink'):
        mac_relocate.relocate(**options, dry_run=True)
    assert not options['supervisor'].calls



@pytest.mark.parametrize('target,uid,gid,mode,platform,ok', [
    ('/Library/Frameworks/Python.framework/Versions/3.10/bin/python3.10', 0, 80, 0o775, 'darwin', True),
    ('/Library/Frameworks/Python.framework/Versions/3.10/bin/python3.10', 0, 80, 0o777, 'darwin', False),
    ('/Library/Frameworks/Python.framework/Versions/3.10/bin/python3.10', 0, 20, 0o775, 'darwin', False),
    ('/Library/Frameworks/Python.framework/Versions/3.10/bin/python3.10', 0, 80, 0o775, 'linux', False),
    ('/usr/local/bin/python3', 0, 80, 0o775, 'darwin', False),
    ('/usr/bin/python3', 0, 0, 0o755, 'darwin', True),
    ('/usr/bin/python3', 12345, 0, 0o755, 'darwin', False),
])
def test_safe_interpreter_python_org_only(monkeypatch, target, uid, gid, mode, platform, ok):
    import types
    import mac_relocate
    monkeypatch.setattr(mac_relocate.sys, 'platform', platform)
    info = types.SimpleNamespace(st_mode=0o100000 | mode, st_uid=uid, st_gid=gid)
    assert mac_relocate.safe_interpreter(target, info) is ok



def test_make_venv_strips_group_write_from_templates(tmp_path, monkeypatch):
    # python.org venv --copies writes group-writable activate scripts; the new venv must still validate.
    import mac_relocate
    destination = tmp_path / 'venv'
    requirements = tmp_path / 'inputs' / 'requirements.txt'
    requirements.parent.mkdir(mode=0o700); (requirements.parent / 'wheels').mkdir(mode=0o700)
    requirements.write_text('', encoding='utf-8')

    def fake_execute(argv, *, timeout, env=None):
        if argv[1:3] == ['-m', 'venv']:
            (destination / 'bin').mkdir(parents=True, mode=0o755)
            for name, mode in (('python', 0o755), ('activate', 0o664), ('Activate.ps1', 0o664)):
                (destination / 'bin' / name).write_text('x', encoding='utf-8')
                (destination / 'bin' / name).chmod(mode)
    monkeypatch.setattr(mac_relocate, 'execute', fake_execute)
    mac_relocate.make_venv(destination, requirements, timeout=5, python='/usr/bin/python3')
    assert all(not p.stat().st_mode & 0o022 for p in destination.rglob('*'))
