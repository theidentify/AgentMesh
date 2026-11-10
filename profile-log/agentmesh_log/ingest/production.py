"""The scheduled ingest and summary jobs: `agentmesh-log ingest scan|summary` (was memory-sync/jobs/agentmesh_production.py).

The config is AGENTMESH_HOME/jobs/agentmesh-production.json unless --config names another. The broker backend reads
transcripts directly and writes through the summarizer agent; the SQLite backend was removed in retirement R5b and a
config still naming it is refused, never run. memory-sync's `runtime` key (the directory put on sys.path) is accepted
and ignored, so an unmigrated config keeps working; `agentmesh-log ingest migrate-config` drops it.

State is unchanged from memory-sync: the same broker.state_dir files, the same status_dir reports and the same lock
files, so a device can switch between the two without losing a cursor or running a tick twice.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import personal

BROKER = ('cli_argv', 'agent_key', 'mapping', 'state_dir')
ACTIONS = ('ingest', 'summary')
PACKAGE_ROOT = str(Path(__file__).resolve().parents[2])  # the directory holding agentmesh_log


def default_config():
    return personal.home() / 'jobs' / 'agentmesh-production.json'


def boot_id():
    if os.name == 'nt':
        # The kernel's own BootTime (FILETIME) is fixed for the whole boot; now-minus-uptime drifts with clock changes.
        import ctypes
        info = (ctypes.c_byte * 48)()
        size = ctypes.c_ulong()
        if ctypes.windll.ntdll.NtQuerySystemInformation(3, info, 48, ctypes.byref(size)) != 0:
            raise OSError('boot time unavailable')
        filetime = int.from_bytes(bytes(info)[:8], 'little')
        return datetime.fromtimestamp(filetime / 10**7 - 11644473600, timezone.utc).isoformat()
    return subprocess.run(['/usr/sbin/sysctl', '-n', 'kern.boottime'], capture_output=True, text=True, check=True).stdout.strip()


def load(path):
    c = json.loads(Path(path).read_text(encoding='utf-8'))
    if c.get('backend') != 'broker':
        raise ValueError('SQLite backend retired; backend must be broker')
    b = c.get('broker')
    if type(b) is not dict or set(b) != set(BROKER):
        raise ValueError('invalid broker backend')
    if 'runtime' in c and (type(c['runtime']) is not str or not Path(c['runtime']).is_absolute()):
        raise ValueError('invalid broker backend')
    if any(type(v) is not str or not Path(v).is_absolute() for v in (b['agent_key'], b['mapping'], b['state_dir'], c.get('status_dir'))):
        raise ValueError('absolute path required')
    personal.check((b['mapping'], b['state_dir'], c['status_dir']), agent_key=b['agent_key'])
    return c


def save(c, name, value):
    p = Path(c['status_dir']) / (name + '.json')
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    os.replace(tmp, p)


def child_path():
    """Make agentmesh_log importable in the Claude worker this job starts (`python -m agentmesh_log.ingest.claude_worker`),
    whatever working directory or PYTHONPATH the scheduler gave the job."""
    current = [p for p in os.environ.get('PYTHONPATH', '').split(os.pathsep) if p]
    if PACKAGE_ROOT not in current:
        os.environ['PYTHONPATH'] = os.pathsep.join([PACKAGE_ROOT, *current])


def broker(c, config, action, force=False):
    from .digest_io import run_lock
    from . import digest
    b = c['broker']
    with run_lock(str(config) + '.' + action):
        if action == 'ingest':
            # Nothing is stored: report what the next summary tick will read, without moving any cursor.
            state = digest.State(b['state_dir'])
            sources, errors = digest.discover(c.get('roots'))
            _, report = digest.scan(state, sources, start_at=state.summary['start_at'])
            report['errors'] += len(errors)
            report['error_classes'] += [type(e).__name__ for e in errors]
            save(c, 'last-ingest', dict(observed_at=datetime.now(timezone.utc).isoformat(), backend='broker', **report))
            if report['errors']:
                from collections import Counter
                from .protocol import ValidationError
                raise ValidationError('transcript scan errors: ' + ', '.join('%s=%d' % kv for kv in sorted(Counter(report['error_classes']).items())))
            return 0
        child_path()
        backend = digest.CliBackend(b['cli_argv'], b['agent_key'], b['mapping'])
        options = {k: v for k, v in c['summary'].items() if k not in ('consumer', 'primary_node')}
        report = digest.tick(b['state_dir'], backend, roots=c.get('roots'), boot_id=boot_id(), force=force, **options)
        save(c, 'last-summary', dict(observed_at=datetime.now(timezone.utc).isoformat(), backend='broker', **report))
    return 0


def execute(action, *, config=None, force=False):
    """One job run; 0 on success, else 1 with a fixed JSON status line on stderr (job run reads it for alerts)."""
    try:
        if action not in ACTIONS:
            raise ValueError('action unavailable on the broker backend')
        path = Path(config) if config else default_config()
        return broker(load(path), path, action, force=force)
    except Exception as e:
        from .protocol import error_detail
        print(json.dumps({'status': 'blocked', 'action': action, 'error_class': type(e).__name__, 'detail': error_detail(e)}), file=sys.stderr)
        return 1


WORKER_MODULE = 'agentmesh_log.ingest.claude_worker'
LEGACY_WORKER = 'claude_digest_worker.py'
BACKUP_SUFFIX = '.memory-sync.bak'


def migrated(c):
    """The config with memory-sync's `runtime` dropped and a summary.command that ran claude_digest_worker.py from the
    memory-sync tree rewritten to the module form; the interpreter and every worker argument stay as they were."""
    new = json.loads(json.dumps(c))
    new.pop('runtime', None)
    command = (new.get('summary') or {}).get('command')
    if isinstance(command, list) and all(type(a) is str for a in command):
        names = [a.replace('\\', '/').rsplit('/', 1)[-1] for a in command]
        if LEGACY_WORKER in names:
            at = names.index(LEGACY_WORKER)
            python = command[:at] if at else [sys.executable]
            new['summary']['command'] = [*python, '-m', WORKER_MODULE, *command[at + 1:]]
    return new


def migrate_config(config=None, apply=False):
    """Dry run unless apply: the changed keys, then (with apply) the original kept beside it as *.memory-sync.bak."""
    path = Path(config) if config else default_config()
    raw = path.read_bytes()
    c = json.loads(raw.decode('utf-8'))
    new = migrated(c)
    changes = sorted(k for k in set(c) | set(new) if c.get(k) != new.get(k))
    result = dict(config=str(path), changes=changes, command=(new.get('summary') or {}).get('command'), applied=False)
    if not changes or not apply:
        return result
    if new.get('backend') != 'broker':
        raise ValueError('SQLite backend retired; backend must be broker')
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if not backup.exists():
        backup.write_bytes(raw)
        if os.name != 'nt':
            os.chmod(backup, 0o600)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(new, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    if os.name != 'nt':
        os.chmod(tmp, path.stat().st_mode & 0o777)
    os.replace(tmp, path)
    return dict(result, applied=True, backup=str(backup))
