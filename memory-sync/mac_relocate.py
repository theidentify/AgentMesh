"""Journalled Mac relocation with read-only planning and explicit recovery."""
from contextlib import closing, contextmanager, ExitStack
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
import uuid
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from install_adopt import absolute, load
from mac_replace import Launchd, digest, sqlite_backup, blob, install_binary, wait_health
from signed_packets import parse, read_local, write_local
from worker_lifecycle import positive


DESTINATIONS = ('memory-sync', 'exchange', 'versions', 'jobs', 'status', 'backups/archive')
STEPS = ('preflight', 'drain', 'lock jobs', 'pause syncthing', 'copy db',
         'copy state/identity', 'copy exchange', 'copy app files', 'copy jobs',
         'rebind runtime (adopt)', 'rewrite plists/configs/rules/settings.local',
         'syncthing path + resume', 'start', 'verify', 'archive')


def fields(value, names):
    if type(value) is not dict or set(value) != set(names):
        raise ValueError('invalid relocation manifest fields')


def path(value, *, missing=False, directory=None, private=False):
    if type(value) is not str or not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('invalid relocation path')
    result = absolute(value)
    if not result.exists():
        if not missing:
            raise ValueError('relocation source missing')
        parent = result.parent
        while not parent.exists():
            parent = parent.parent
        path(str(parent), directory=True)
        return result
    info = result.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ValueError('user-owned path not writable by others required')
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise ValueError('regular file or directory required')
    if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
        raise ValueError('hardlinked relocation file refused')
    if directory is not None and result.is_dir() != directory:
        raise ValueError('unexpected relocation path type')
    if private and stat.S_IMODE(info.st_mode) != (0o700 if result.is_dir() else 0o600):
        raise ValueError('private relocation path required')
    return result


def tree(root, *, archive_only=False):
    """Validate each entry without following links, including empty directories."""
    root = path(str(root))
    entries = [root]
    if root.is_dir():
        for directory, dirs, files in os.walk(root, followlinks=False):
            entries.extend(Path(directory) / name for name in sorted(dirs + files))
    for entry in entries:
        if archive_only and entry.is_symlink():
            # Only the link itself is inspected. Its target may be external,
            # a directory, or dangling; none is traversed.
            path(str(entry.parent), directory=True)
            if (entry.lstat().st_uid != os.getuid() or
                    any(ord(c) < 32 or ord(c) == 127 for c in str(entry))):
                raise ValueError('invalid archive symlink')
        else:
            path(str(entry))
    return entries


def archive_allows_links(source, config, app):
    protected = [app, config['from']['exchange'], config['jobs']['status_dir'],
                 config['jobs']['production_config'], *config['jobs']['files'],
                 *config['rules'], config['from']['plist'],
                 *[j['plist'] for j in config['jobs']['plists']]]
    source = Path(source)
    return not any(source.is_relative_to(Path(p)) or Path(p).is_relative_to(source)
                   for p in protected)


def interpreter(value):
    """Interpreter-only exception: inspect the resolved user/root-owned binary."""
    if (type(value) is not str or not Path(value).is_absolute() or '..' in Path(value).parts or
            any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError('invalid job interpreter')
    try:
        target = Path(os.path.realpath(value, strict=True))
        info = target.stat()
    except (OSError, ValueError):
        raise ValueError('job interpreter missing or invalid') from None
    if not stat.S_ISREG(info.st_mode) or not safe_interpreter(target, info) or not os.access(target, os.X_OK):
        raise ValueError('job interpreter must be a safe executable owned by user or root')
    return target


PYTHON_ORG = Path('/Library/Frameworks/Python.framework')


def safe_interpreter(target, info):
    """User- or root-owned and not writable by others. The python.org macOS installer is root-owned
    and admin-group (gid 80) writable; accept exactly that layout, never world-writable."""
    if info.st_mode & 0o002 or info.st_uid not in (0, os.getuid()):
        return False
    if not info.st_mode & 0o020:
        return True
    return (sys.platform == 'darwin' and info.st_uid == 0 and info.st_gid == 80
            and Path(target).is_relative_to(PYTHON_ORG))


def job_command(argv, jobs, app):
    """Parse script commands and the narrowly supported job_notify wrapper."""
    if (not isinstance(argv, list) or len(argv) < 2 or
            not all(type(a) is str and a and not any(ord(c) < 32 or ord(c) == 127 for c in a) for a in argv)):
        raise ValueError('unsupported job command')

    def script_command(command):
        if len(command) < 2:
            raise ValueError('job interpreter and script required')
        resolved = interpreter(command[0])
        index = 1
        while index < len(command) and command[index] in ('-u', '-I', '-B'):
            index += 1
        if index == len(command) or command[index].startswith('-'):
            raise ValueError('unsupported interpreter flags')
        script = path(command[index], directory=False)
        return resolved, script, index

    outer, script, index = script_command(argv)
    resolved = [outer]
    if script == Path(app) / 'job_notify.py':
        tail = argv[index + 1:]
        if '--' not in tail:
            raise ValueError('wrapper command separator required')
        split = tail.index('--')
        options, inner = tail[:split], tail[split + 1:]
        if len(options) % 2:
            raise ValueError('invalid wrapper options')
        values = {}
        for key, value in zip(options[::2], options[1::2]):
            if key not in ('--name', '--state-dir', '--webhook-file', '--timeout') or key in values:
                raise ValueError('invalid wrapper options')
            values[key] = value
        if (not re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}', values.get('--name', '')) or
                values.get('--state-dir') != jobs['status_dir']):
            raise ValueError('invalid wrapper name or status directory')
        if '--webhook-file' in values:
            path(values['--webhook-file'], directory=False, private=True)
        if '--timeout' in values:
            if not values['--timeout'].isdigit() or int(values['--timeout']) <= 0:
                raise ValueError('invalid wrapper timeout')
        inner_python, entry, _ = script_command(inner)
        if str(entry) not in jobs['files']:
            raise ValueError('wrapper job entrypoint absent from manifest files')
        resolved.append(inner_python)
        return dict(interpreters=resolved, python=inner_python, wrapper=True, script=script)
    if str(script) not in jobs['files'] and not script.is_relative_to(Path(jobs['status_dir'])):
        raise ValueError('job entrypoint absent from manifest files or status directory')
    return dict(interpreters=resolved, python=outer, wrapper=False, script=script)


class Syncthing:
    """Loopback REST boundary; transport injection requires no socket in tests."""
    def __init__(self, config, folder_id, *, transport=None, timeout=30):
        self.timeout = positive(timeout)
        if type(folder_id) is not str or not re.fullmatch(r'[A-Za-z0-9_.-]+', folder_id):
            raise ValueError('invalid Syncthing folder ID')
        self.folder_id = folder_id
        config = path(config, directory=False)
        try:
            gui = ET.fromstring(read_local(config)).find('gui')
            address = gui.findtext('address')
            self._key = gui.findtext('apikey')
            if not self._key or any(ord(c) < 32 for c in self._key):
                raise ValueError()
            scheme = 'https' if gui.get('tls') == 'true' else 'http'
            parsed = urllib.parse.urlsplit(address if '://' in address else scheme + '://' + address)
            if (parsed.scheme not in ('http', 'https') or
                    parsed.hostname not in ('127.0.0.1', '::1', 'localhost') or
                    parsed.username or parsed.password or parsed.path not in ('', '/') or
                    parsed.query or parsed.fragment or not parsed.port):
                raise ValueError()
            self.base = parsed.scheme + '://' + parsed.netloc
        except Exception:
            raise ValueError('invalid loopback Syncthing configuration') from None
        self.transport = transport or self._transport

    @staticmethod
    def _transport(method, url, headers, body, timeout):
        # Never forward the API-key header through an HTTP redirect.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        with opener.open(request, timeout=timeout) as response:
            data = response.read(8 * 1024 * 1024 + 1)
        return parse(data) if data else None

    def request(self, method, endpoint, value=None):
        headers = {'X-API-Key': self._key, 'Content-Type': 'application/json'}
        body = None if value is None else json.dumps(value).encode('utf-8')
        try:
            return self.transport(method, self.base + '/rest/' + endpoint, headers, body, self.timeout)
        except Exception:
            raise ValueError('Syncthing unavailable') from None

    def folder(self):
        value = self.request('GET', 'config/folders/' + urllib.parse.quote(self.folder_id, safe=''))
        if type(value) is not dict or value.get('id') != self.folder_id:
            raise ValueError('Syncthing folder mismatch')
        return value

    def preflight(self, exchange):
        if self.request('GET', 'system/ping') != {'ping': 'pong'}:
            raise ValueError('Syncthing unavailable')
        folder = self.folder()
        if folder.get('path') != str(exchange) or folder.get('paused') is not False:
            raise ValueError('Syncthing folder path or pause state mismatch')

    def _patch(self, value):
        self.request('PATCH', 'config/folders/' + urllib.parse.quote(self.folder_id, safe=''), value)
        current = self.folder()
        if any(current.get(k) != v for k, v in value.items()):
            raise ValueError('Syncthing update verification failed')

    def pause(self):
        self._patch({'paused': True})

    def set_path(self, destination):
        self._patch({'path': str(absolute(destination))})

    def resume(self):
        self._patch({'paused': False})

    def wait_idle(self, *, timeout):
        deadline = time.monotonic() + positive(timeout)
        while time.monotonic() < deadline:
            value = self.request('GET', 'db/status?' + urllib.parse.urlencode({'folder': self.folder_id}))
            if (type(value) is dict and value.get('state') == 'idle' and
                    value.get('needFiles') == 0 and value.get('errors', 0) == 0 and
                    value.get('pullErrors', 0) == 0):
                return
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        raise TimeoutError('Syncthing folder did not become idle')


def _read(manifest, supervisor, transport):
    manifest = path(str(manifest), directory=False, private=True)
    config = parse(read_local(manifest, private=True))
    fields(config, ('format', 'from', 'to_root', 'syncthing', 'jobs', 'rules', 'archive', 'settings_local'))
    if config['format'] != 'agentmesh-relocation-v1':
        raise ValueError('unsupported relocation manifest')
    source = config['from']
    fields(source, ('runtime', 'plist', 'binary', 'exchange'))
    for key, value in source.items():
        path(value, directory=key == 'exchange')
    runtime, scope = load(source['runtime'], authoritative=False)
    if scope['node'] != 'mac' or runtime['exchange'] != source['exchange']:
        raise ValueError('current runtime mismatch')
    for key in ('app_root', 'database', 'security_dir', 'security_state', 'workflow_config'):
        path(runtime[key])
    root = path(config['to_root'], missing=True, directory=True, private=True)
    destinations = {name: path(str(root / name), missing=True, directory=True, private=True)
                    for name in DESTINATIONS}
    for target in destinations.values():
        if target.exists() and any(target.iterdir()):
            raise ValueError('destination must be empty')
    exchange = destinations['exchange']
    for target in destinations.values():
        if target != exchange and (target.is_relative_to(exchange) or exchange.is_relative_to(target)):
            raise ValueError('exchange must remain separate from local state')
    protected = [Path(runtime[k]) for k in
                 ('app_root', 'database', 'security_dir', 'security_state', 'workflow_config')]
    protected += [Path(value) for value in source.values()]
    for target in destinations.values():
        if any(target.is_relative_to(old) or old.is_relative_to(target) for old in protected):
            raise ValueError('source and destination overlap')
    service = plistlib.loads(read_local(source['plist']))
    label = service.get('Label')
    argv = service.get('ProgramArguments')
    if (type(label) is not str or not re.fullmatch(r'[A-Za-z0-9_.-]+', label)
            or 'Program' in service or type(argv) is not list or
            not all(type(a) is str and not any(ord(c) < 32 for c in a) for a in argv) or
            argv[:4] != [source['binary'], 'worker-run', '--runtime', source['runtime']]):
        raise ValueError('existing service command mismatch')
    options = argv[4:]
    legacy = scope['policy'] == 'legacy'
    if (len(options) != (3 if legacy else 2) or options[0] != '--interval' or
            (legacy and options[2] != '--legacy-drained') or not os.access(source['binary'], os.X_OK)):
        raise ValueError('unsupported service arguments')
    positive(float(options[1]))
    live = supervisor.inspect(label)
    if not live['loaded'] or live['arguments'] != argv:
        raise ValueError('live service command mismatch')
    jobs = config['jobs']
    fields(jobs, ('plists', 'files', 'production_config', 'status_dir', 'venv_requirements'))
    if type(jobs['plists']) is not list or type(jobs['files']) is not dict:
        raise ValueError('invalid jobs manifest')
    references = [Path(source['plist']), Path(source['runtime']),
                  Path(runtime['security_state']), Path(runtime['workflow_config'])]
    for item in jobs['plists']:
        fields(item, ('plist', 'label'))
        job_plist = path(item['plist'], directory=False)
        job = plistlib.loads(read_local(job_plist))
        if type(item['label']) is not str or job.get('Label') != item['label']:
            raise ValueError('job label mismatch')
        references.append(job_plist)
    names = set()
    for old, name in jobs['files'].items():
        path(old, directory=False)
        if (type(name) is not str or not name or Path(name).is_absolute() or
                '..' in Path(name).parts or '.' == name or
                any(ord(c) < 32 or ord(c) == 127 for c in name)):
            raise ValueError('invalid relative job destination')
        normalized = str(Path(name))
        if normalized in names or normalized.split('/')[0] == '.venv':
            raise ValueError('job destination collision')
        names.add(normalized)
        references.append(Path(old))
    for key in ('production_config', 'venv_requirements', 'status_dir'):
        path(jobs[key], directory=key == 'status_dir')
    references.append(Path(jobs['production_config']))
    if type(config['rules']) is not list or type(config['archive']) is not list:
        raise ValueError('invalid rules or archive')
    for rule in config['rules']:
        references.append(path(rule, directory=False))
    source_parent = Path(runtime['app_root']).parent
    for archived in config['archive']:
        archived = path(archived)
        if archived == source_parent or not archived.is_relative_to(source_parent):
            raise ValueError('archive must be under source memory root')
        tree(archived, archive_only=archive_allows_links(archived, config, runtime['app_root']))
    settings = path(config['settings_local'], directory=False, private=True)
    if settings != root / 'settings.local.json':
        raise ValueError('settings must be under destination root')
    references.append(settings)
    fields(config['syncthing'], ('config', 'folder_id'))
    syncthing = Syncthing(**config['syncthing'], transport=transport)
    files = []
    for source_tree in (runtime['app_root'], source['exchange'], jobs['status_dir']):
        files.extend(p for p in tree(source_tree) if p.is_file())
    free_path = root
    while not free_path.exists():
        free_path = free_path.parent
    required = 2 * Path(runtime['database']).stat().st_size + sum(
        p.stat().st_size for p in files if p.is_relative_to(Path(source['exchange'])))
    if shutil.disk_usage(free_path).free < required:
        raise ValueError('insufficient free disk space')
    syncthing.preflight(source['exchange'])
    report = dict(status='planned', label=label, policy=scope['policy'],
                  destinations={k: str(v) for k, v in destinations.items()},
                  required_free_bytes=required,
                  changes=sorted({str(p) for p in files + references}),
                  database_restore='never_automatic')
    return config, runtime, scope, service, syncthing, report


def plan(manifest, *, supervisor=None, transport=None, release_source=None):
    """Read-only planning: no directory creation, authoritative DB open or drain."""
    return _prepare(manifest, supervisor or Launchd(), transport, release_source)['report']


@contextmanager
def freeze(database, *, timeout):
    """Job locks plus a reserved SQLite write lock, held until cutover or abort.

    The scheduler takes its own lock before bounded_digest's lock; retain that
    ordering. BEGIN IMMEDIATE also excludes writers which ignore advisory locks.
    Backup uses a separate read-only connection and includes committed WAL.
    """
    from bounded_digest import run_lock
    database = absolute(database)
    with run_lock(str(database) + '.scheduler', wait=timeout):
        with run_lock(database, wait=timeout):
            with closing(sqlite3.connect(database.as_uri() + '?mode=rw', uri=True, timeout=timeout)) as guard:
                guard.execute('BEGIN IMMEDIATE')
                try:
                    yield
                finally:
                    guard.rollback()


def table_counts(database):
    with closing(sqlite3.connect(absolute(database).as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('database integrity verification failed')
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {name: db.execute('SELECT count(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
                for name in tables}


def copy_database(source, destination):
    before = table_counts(source)
    sqlite_backup(source, destination)
    if table_counts(destination) != before or table_counts(source) != before:
        raise ValueError('database row counts changed during copy')
    return before


def inventory(root, *, archive_only=False):
    root = absolute(root)
    return {str(entry.relative_to(root)):
            ('link -> ' + os.readlink(entry) if entry.is_symlink() else
             dict(mode=stat.S_IMODE(entry.stat().st_mode),
                  sha256=digest(entry) if entry.is_file() else None))
            for entry in tree(root, archive_only=archive_only)}


def copy_tree(source, destination, *, archive_only=False):
    source, destination = absolute(source), absolute(destination)
    before = inventory(source, archive_only=archive_only)
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError('copy destination must be absent or empty')
    for entry in tree(source, archive_only=archive_only):
        target = destination / entry.relative_to(source)
        if entry.is_symlink():
            os.symlink(os.readlink(entry), target)
            continue
        elif entry.is_dir():
            target.mkdir(mode=0o700, exist_ok=True)
        else:
            shutil.copyfile(entry, target, follow_symlinks=False)
        target.chmod(stat.S_IMODE(entry.stat().st_mode))
    if (inventory(destination, archive_only=archive_only) != before or
            inventory(source, archive_only=archive_only) != before):
        raise ValueError('copy verification failed')


def execute(argv, *, timeout, env=None):
    """Bounded child execution; never echo private child stdout/stderr."""
    result = subprocess.run([str(a) for a in argv], stdin=subprocess.DEVNULL,
                            capture_output=True, timeout=timeout,
                            env={**os.environ, **(env or {})})
    if result.returncode:
        raise ValueError('relocation child command failed')
    if not result.stdout.strip():
        return None
    try:
        return parse(result.stdout)
    except Exception:
        return None


def make_venv(destination, requirements, *, timeout, python=None):
    interpreter = python or sys.executable
    execute([interpreter, '-m', 'venv', '--copies', str(destination)], timeout=timeout)
    if requirement_pins(requirements):
        execute([str(destination / 'bin/python'), '-m', 'pip', 'install', '--no-index',
                 '--disable-pip-version-check', '--no-input', '--no-deps', '--only-binary=:all:', '--find-links',
                 str(requirements.parent / 'wheels'), '-r', str(requirements)], timeout=timeout)
    execute([str(destination / 'bin/python'), '-m', 'pip', 'check'], timeout=timeout)
    harden(destination)
    tree(destination)


def harden(root):
    """python.org venv templates (activate scripts) arrive group-writable; strip group/other
    write from the freshly created venv before validating it. Links are never followed."""
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in [*dirs, *files]:
            entry = Path(directory) / name
            if not entry.is_symlink():
                entry.chmod(stat.S_IMODE(entry.lstat().st_mode) & ~0o022)
    root = Path(root)
    root.chmod(stat.S_IMODE(root.lstat().st_mode) & ~0o022)


def requirement_pins(requirements):
    lines = [s.strip() for s in Path(requirements).read_text(encoding='utf-8').splitlines()
             if s.strip() and not s.lstrip().startswith('#')]
    if any(not re.fullmatch(r'[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?==[A-Za-z0-9_.+!-]+', s)
           for s in lines):
        raise ValueError('requirements must contain exact package==version pins only')
    return lines


def rewrite(value, mapping):
    """Replace whole path prefixes once; do not cascade replacements."""
    if isinstance(value, dict):
        return {k: rewrite(v, mapping) for k, v in value.items()}
    if isinstance(value, list):
        return [rewrite(v, mapping) for v in value]
    if not isinstance(value, str):
        return value
    pattern = '|'.join(re.escape(k) for k in sorted(mapping, key=len, reverse=True))
    if not pattern:
        return value
    return re.sub('(?:' + pattern + r')(?=$|[/\s"\'\\,)\]])',
                  lambda m: mapping[m[0]], value)


def render_bridge(config, runtime):
    """Mirror apply.render_bridge's field set without opening protected keys."""
    root = Path(config['to_root'])
    local = parse(read_local(config['settings_local'], private=True))
    desired = {}
    defaults = root / 'settings.json'
    if defaults.exists():
        desired.update(parse(read_local(path(str(defaults), private=True))).get('bridge', {}))
    desired.update(local.get('bridge', {}))
    # Previously rendered fields supply defaults for partial desired settings.
    old_file = Path(runtime['database'] + '.profile-log.json')
    old = parse(read_local(old_file, private=True)) if old_file.exists() else {}
    result = dict(format='agentmesh-profile-log-bridge-v1',
                  enabled=False, recall=True, dual_write=True, authority=False,
                  timeout_seconds=10)
    keys = ('enabled', 'recall', 'dual_write', 'authority', 'cli_argv', 'profile', 'timeout_seconds')
    for key in keys:
        if key in old:
            result[key] = old[key]
        if key in desired:
            result[key] = desired[key]
    for field, agent in (('agent_key', 'recall_agent'), ('import_agent_key', 'import_agent')):
        if agent in desired:
            name = desired[agent]
            if type(name) is not str or not re.fullmatch('[a-z0-9-]{1,32}', name):
                raise ValueError('invalid bridge agent')
            result[field] = str(root / 'agents' / (name + '.key'))
        elif field in old:
            result[field] = old[field]
    result['mapping'] = str(root / 'migration-mapping.json')
    from profile_log_bridge import FIELDS
    if set(result) != FIELDS or not all(result[k] for k in ('enabled', 'recall', 'dual_write', 'authority')):
        raise ValueError('an active broker bridge is required for relocation verification')
    for name in ('agent_key', 'import_agent_key', 'mapping'):
        path(result[name], directory=False, private=True)
    return result


def release_files(release_source=None):
    default = (Path(sys.executable).absolute().parent / 'memory-sync' if getattr(sys, 'frozen', False)
               else Path(__file__).absolute().parent)
    root = path(str(release_source or default), directory=True)
    # Run from the selected release's source distribution. A frozen onefile
    # executable alone does not contain an installable Python source tree.
    for name in ('agentmesh.py', 'profile_log_bridge.py', 'schema.sql'):
        path(str(root / name), directory=False)
    entries = [p for p in tree(root) if p.is_file() and
               (p.parent == root and (p.suffix == '.py' and not p.name.startswith('test_') or p.name == 'schema.sql')
                or p.is_relative_to(root / 'src') and p.suffix == '.py')]
    return root, entries


def _prepare(manifest, supervisor, transport, release_source):
    config, runtime, scope, service, syncthing, report = _read(manifest, supervisor, transport)
    root = Path(config['to_root'])
    app = Path(runtime['app_root'])
    data = Path(runtime['database']).parent
    if not data.is_relative_to(app) or Path(config['from']['runtime']).parent != data:
        raise ValueError('runtime and database must share the app data directory')
    if not Path(runtime['security_dir']).is_relative_to(app):
        raise ValueError('identity must be inside the source app')
    for name in ('security_state', 'workflow_config'):
        if not Path(runtime[name]).is_relative_to(data):
            raise ValueError('runtime state must be inside the data directory')
    if (app / 'ota-state.json').exists():
        raise ValueError('OTA-owned installation requires its existing consumer')
    jobs = config['jobs']
    roles = {}
    commands = {}
    job_services = []
    for item in jobs['plists']:
        job = plistlib.loads(read_local(item['plist']))
        label = item['label']
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', label) or 'Program' in job:
            raise ValueError('unsupported job service')
        argv = job.get('ProgramArguments')
        commands[label] = job_command(argv, jobs, app)
        live = supervisor.inspect(label)
        if live['loaded'] and live['arguments'] != argv:
            raise ValueError('live job command mismatch')
        job_services.append(dict(plist=item['plist'], service=job, loaded=live['loaded']))
        for role, pattern in (('ingest', 'ingest'), ('summary', r'summary|summar')):
            if re.search(pattern, label, re.I):
                if role in roles:
                    raise ValueError('ambiguous verification job')
                roles[role] = job
    if set(roles) != {'ingest', 'summary'}:
        raise ValueError('ingest and summary job labels required')
    requirements = Path(jobs['venv_requirements'])
    if requirement_pins(requirements):
        tree(path(str(requirements.parent / 'wheels'), directory=True))
    release, files = release_files(release_source)
    if any(c['wrapper'] for c in commands.values()) and release / 'job_notify.py' not in files:
        raise ValueError('release must include job_notify.py')
    bridge = render_bridge(config, runtime)
    recall = []
    for rule in config['rules']:
        if Path(rule).suffix == '.json':
            rule_value = parse(read_local(rule))
            if isinstance(rule_value, dict) and 'recall_argv' in rule_value:
                recall.append(rule_value['recall_argv'])
    if len(recall) != 1 or not isinstance(recall[0], list) or not recall[0]:
        raise ValueError('exactly one rule recall_argv required')
    mapping = {str(app): str(root / 'memory-sync'),
               str(data): str(root / 'memory-sync/data'),
               runtime['security_dir']: str(root / 'memory-sync/identity'),
               config['from']['exchange']: str(root / 'exchange'),
               str(app.parent / '.venv'): str(root / 'jobs/.venv'),
               jobs['production_config']: str(root / 'jobs/agentmesh-production.json'),
               jobs['status_dir']: str(root / 'status')}
    mapping.update({old: str(root / 'jobs' / name) for old, name in jobs['files'].items()})
    targets = {key: rewrite(runtime[key], mapping) for key in
               ('app_root', 'database', 'exchange', 'security_dir', 'security_state', 'workflow_config')}
    targets.update(runtime=str(root / 'memory-sync/data/runtime.json'), node=runtime['node'])
    mapping[config['from']['runtime']] = targets['runtime']
    # No overwrite of the existing protected root through an alias in rules,
    # scripts, archive entries or service descriptors.
    external = [config['from']['plist'], *config['rules'], *jobs['files'],
                jobs['production_config'], *[j['plist'] for j in jobs['plists']]]
    if len(external) != len(set(external)):
        raise ValueError('duplicate relocation write target')
    for item in external:
        if Path(item).is_relative_to(root):
            raise ValueError('existing destination entries are protected')
        if Path(item + '.new').exists() or Path(item + '.new').is_symlink():
            raise ValueError('pending .new file requires review')
    archives = [app, Path(config['from']['exchange']), *map(Path, config['archive'])]
    for item in (Path(jobs['production_config']), Path(jobs['status_dir']), app.parent / '.venv'):
        if item.exists() and item.is_relative_to(app.parent):
            archives.append(item)
    archives = sorted(set(archives), key=lambda p: len(p.parts))
    archives = [p for p in archives if not any(p != a and p.is_relative_to(a) for a in archives)]
    if any(p == app.parent or p.is_relative_to(root) or root.is_relative_to(p) for p in archives):
        raise ValueError('archive overlaps protected root')
    for item in archives:
        tree(item, archive_only=archive_allows_links(item, config, app))
    # Every source-root child must be accounted for, avoiding accidental
    # retention of an active old binary or an unlisted historical tree.
    if any(not any(p == a or p.is_relative_to(a) for a in archives) for p in app.parent.iterdir()):
        raise ValueError('source memory root contains unlisted archive entries')
    if len({p.name for p in archives}) != len(archives):
        raise ValueError('archive destination collision')
    report['changes'] = sorted(set(report['changes']) | set(external) |
        {str(p) for p in files} | {str(p) for a in archives
                                 for p in tree(a, archive_only=archive_allows_links(a, config, app))})
    report['references'] = {old: new for old, new in mapping.items()}
    watched = set(external + [str(manifest), config['from']['runtime'], config['settings_local'],
                             jobs['venv_requirements'], str(root / 'settings.json')])
    watched |= {str(p) for p in files}
    watched |= {str(p) for p in tree(runtime['security_dir']) if p.is_file()}
    watched |= {runtime['security_state'], runtime['workflow_config'], config['from']['binary']}
    watched |= {str(p) for c in commands.values() for p in c['interpreters']}
    watched |= {str(c['script']) for c in commands.values()}
    hashes = {p: digest(Path(p)) for p in watched if Path(p).is_file()}
    return dict(config=config, runtime=runtime, scope=scope, service=service, syncthing=syncthing,
                report=report, jobs=job_services, roles=roles, mapping=mapping, targets=targets,
                bridge=bridge, recall=recall[0], release=release, files=files,
                archives=archives, hashes=hashes,
                venv_python=str(commands[roles['ingest']['Label']]['python']))


def row_versions(database):
    """Only hashes and row identities enter the journal, never row contents."""
    result = {}
    with closing(sqlite3.connect(absolute(database).as_uri() + '?mode=ro', uri=True)) as db:
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            quoted = '"' + name.replace('"', '""') + '"'
            try:
                rows = db.execute('SELECT rowid,* FROM ' + quoted).fetchall()
                result[name] = {str(row[0]): hashlib.sha256(repr(row[1:]).encode('utf-8')).hexdigest()
                                for row in rows}
            except sqlite3.OperationalError:
                rows = db.execute('SELECT * FROM ' + quoted).fetchall()
                result[name] = {hashlib.sha256(repr(row).encode('utf-8')).hexdigest(): 'present' for row in rows}
    return result


class Journal:
    def __init__(self, backup, record):
        self.backup, self.record = backup, record

    def save(self):
        write_local(self.backup / 'relocation.json', self.record)

    def snapshot(self, filename):
        filename = path(str(filename), directory=False)
        key = str(filename)
        if key not in self.record['originals']:
            saved = str(len(self.record['originals'])) + '.original'
            blob(self.backup / saved, filename.read_bytes(), exclusive=True)
            self.record['originals'][key] = dict(file=saved, sha256=digest(filename),
                                               mode=stat.S_IMODE(filename.stat().st_mode))
            self.save()

    def write(self, filename, data):
        filename = absolute(filename)
        self.snapshot(filename)
        original = self.record['originals'][str(filename)]
        expected = self.record['written'].get(str(filename), original['sha256'])
        if digest(filename) != expected:
            raise ValueError('reference changed during relocation')
        # Write-ahead records also cover an exception after publication.
        new_hash = hashlib.sha256(data).hexdigest()
        self.record['written'][str(filename)] = new_hash
        self.save()
        adjacent = filename.with_name(filename.name + '.new')
        blob(adjacent, data, exclusive=True, mode=original['mode'])
        if digest(filename) != expected:
            raise ValueError('reference changed before swap')
        os.replace(adjacent, filename)
        if digest(filename) != new_hash:
            raise ValueError('reference publication failed')


def copy_file(source, target):
    source, target = absolute(source), absolute(target)
    before = digest(source)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    blob(target, source.read_bytes(), exclusive=True, mode=stat.S_IMODE(source.stat().st_mode))
    if digest(target) != before or digest(source) != before:
        raise ValueError('copy verification failed')


def rewrite_file(filename, mapping):
    filename = absolute(filename)
    raw = filename.read_bytes()
    if filename.suffix == '.plist':
        return plistlib.dumps(rewrite(plistlib.loads(raw), mapping))
    if filename.suffix == '.json':
        return json.dumps(rewrite(parse(raw), mapping), ensure_ascii=False, sort_keys=True).encode('utf-8')
    return rewrite(raw.decode('utf-8'), mapping).encode('utf-8')


HOOK_PROBE = """import json,sys
sys.path.insert(0,sys.argv[1])
import profile_log_bridge as bridge
calls=[]
bridge.subprocess.Popen=lambda argv,**kw:calls.append(argv)
bridge.notify_rows(sys.argv[2],[(sys.argv[3],int(sys.argv[4]))])
print(json.dumps({'calls':calls}))
"""


def verify(prepared, *, runner, timeout):
    root = Path(prepared['config']['to_root'])
    mapping = prepared['mapping']
    for role in ('ingest', 'summary'):
        job = prepared['roles'][role]
        runner(rewrite(job['ProgramArguments'], mapping), timeout=timeout,
               env=rewrite(job.get('EnvironmentVariables', {}), mapping))
    db = Path(prepared['targets']['database'])
    refs = []
    with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)) as conn:
        for table in ('memory_items', 'memory_summaries'):
            row = conn.execute('SELECT id FROM ' + table + ' ORDER BY id LIMIT 1').fetchone()
            if row:
                refs = [table, str(row[0])]
                break
    if not refs:
        raise ValueError('bridge verification needs an existing memory row')
    probe = runner([str(root / 'jobs/.venv/bin/python'), '-c', HOOK_PROBE,
                    str(root / 'memory-sync'), str(db), *refs, 'relocation-hook-probe'], timeout=timeout)
    if not isinstance(probe, dict) or len(probe.get('calls', [])) != 1:
        raise ValueError('profile-log bridge hook did not fire')
    # Execute the exact migration command generated by the installed hook,
    # synchronously, so catch-up cannot race an unobserved child.
    runner(probe['calls'][0], timeout=timeout)
    recalled = runner([*rewrite(prepared['recall'], mapping), 'relocation verification'], timeout=timeout)
    if not isinstance(recalled, dict) or recalled.get('backend') != 'broker':
        raise ValueError('rule recall did not use broker')
    bridge = prepared['bridge']
    caught_up = runner([*bridge['cli_argv'], 'migrate', 'catch-up', '--db', str(db),
                       '--mapping', bridge['mapping'], '--agent-key', bridge['import_agent_key']], timeout=timeout)
    if (not isinstance(caught_up, dict) or caught_up.get('errors') or caught_up.get('unmapped') or
            not isinstance(caught_up.get('outcomes'), dict) or
            # The summary tick may legitimately add rows that catch-up then imports.
            any(k not in ('unchanged', 'created', 'revised', 'retired') and v
                for k, v in caught_up['outcomes'].items()) or
            not caught_up['outcomes'].get('unchanged')):
        raise ValueError('migration catch-up was not unchanged')
    # Return only bounded operational metadata, never recall or migration rows.
    return dict(ingest='completed', summary='completed', recall='broker',
                bridge_hook='fired', catch_up={'outcomes': caught_up['outcomes']})


def _archive(prepared, journal):
    root = Path(prepared['config']['to_root']) / 'backups/archive'
    stamp = time.strftime('%Y%m%dT%H%M%S', time.gmtime()) + '-' + uuid.uuid4().hex[:8]
    for source in prepared['archives']:
        target = root / (source.name + '.pre-relocate-' + stamp)
        archive_only = archive_allows_links(source, prepared['config'], prepared['runtime']['app_root'])
        entries = tree(source, archive_only=archive_only)
        modes = {str(p.relative_to(source)): stat.S_IMODE(p.stat().st_mode)
                 for p in entries if not p.is_symlink()}
        links = {str(p.relative_to(source)): 'link -> ' + os.readlink(p)
                 for p in entries if p.is_symlink()}
        operation = dict(source=str(source), target=str(target), modes=modes,
                         archive_only=archive_only, links=links)
        journal.record['archives'].append(operation)
        journal.save()  # Intent precedes rename, so interrupted moves are recoverable.
        source.rename(target)
    for old, name in prepared['config']['jobs']['files'].items():
        journal.write(old, ('# Moved to ' + str(Path(prepared['config']['to_root']) / 'jobs' / name) + '\n').encode('utf-8'))


def _check_archive(operation):
    target = Path(operation['target'])
    entries = tree(target, archive_only=operation.get('archive_only', False))
    links = {str(p.relative_to(target)): 'link -> ' + os.readlink(p)
             for p in entries if p.is_symlink()}
    if links != operation.get('links', {}):
        raise ValueError('archive symlinks changed')


def _readonly_archives(journal):
    for operation in journal.record['archives']:
        target = Path(operation['target'])
        _check_archive(operation)
        for relative, mode in sorted(operation['modes'].items(), key=lambda item: len(Path(item[0]).parts), reverse=True):
            path(str(target / relative)).chmod(mode & ~0o222)


def _restore_modes(operation):
    target = Path(operation['target'])
    if target.exists():
        for relative, mode in sorted(operation['modes'].items(), key=lambda item: len(Path(item[0]).parts)):
            path(str(target / relative)).chmod(mode)


def preserve_packets(source, destination):
    """Union immutable packet namespaces; collisions never overwrite either side."""
    for namespace in ('changes', 'signed-changes'):
        packet_root = Path(source) / namespace
        if not packet_root.exists():
            continue
        for entry in tree(packet_root):
            if not entry.is_file():
                continue
            target = absolute(Path(destination) / entry.relative_to(source))
            if target.exists():
                if digest(target) != digest(entry):
                    raise ValueError('rollback immutable packet collision')
            else:
                copy_file(entry, target)


def _restore(journal, supervisor, syncthing, *, timeout, archived=False):
    record = journal.record
    cfg = record['config']
    # Archives can remain on disk for weeks. Revalidate before stopping a
    # service or chmodding anything; aliases must not reach protected entries.
    archive_root = absolute(Path(cfg['to_root']) / 'backups/archive')
    for operation in record['archives']:
        target = absolute(operation['target'])
        if target.parent != archive_root:
            raise ValueError('invalid archive recovery target')
        for relative in operation['modes']:
            if Path(relative).is_absolute() or '..' in Path(relative).parts:
                raise ValueError('invalid archive recovery entry')
        if target.exists():
            _check_archive(operation)
    record['rollback_archived'] = archived
    record['state'] = 'rolling_back'
    record.setdefault('restored', {})
    journal.save()
    intended_archive_mapping = ({operation['source']: operation['target'] for operation in record['archives']}
                                if archived else {})
    new_db = Path(record['targets']['database'])
    writes, deleted = {}, {}
    baseline = journal.backup / 'database.sqlite'
    if baseline.exists() and record.get('baseline_sha256') and digest(baseline) != record['baseline_sha256']:
        raise ValueError('database baseline changed')
    # Check every externally published file before stopping any service.
    for filename, original in record['originals'].items():
        saved = journal.backup / original['file']
        if digest(saved) != original['sha256']:
            raise ValueError('recovery snapshot changed')
        if Path(filename).exists() and digest(Path(filename)) not in (
                original['sha256'], record['written'].get(filename), record['restored'].get(filename)):
            raise ValueError('reference changed after relocation; manual recovery required')
    services = record['services']
    for item in reversed(services):
        label = item['service']['Label']
        live = supervisor.inspect(label)
        if live['loaded']:
            allowed = [item['service']['ProgramArguments'], rewrite(item['service']['ProgramArguments'], record['mapping'])]
            if archived:
                allowed.append(rewrite(item['service']['ProgramArguments'], intended_archive_mapping))
            if live['arguments'] not in allowed:
                raise ValueError('foreign service command; recovery refused')
            if label in record['drained']:
                supervisor.drain(label, timeout)
    if new_db.exists() and baseline.exists() and record.get('baseline_sha256'):
        current, original_rows = row_versions(new_db), row_versions(baseline)
        for name in set(current) | set(original_rows):
            before, after = original_rows.get(name, {}), current.get(name, {})
            writes[name] = sum(before.get(k) != v for k, v in after.items())
            deleted[name] = len(set(before) - set(after))
    archive_mapping = {}
    active_paths = [*cfg['from'].values(), cfg['jobs']['production_config'], cfg['jobs']['status_dir']]
    active_paths += [arg for item in services for arg in item['service']['ProgramArguments'][:2]
                     if Path(arg).is_absolute()]
    for operation in reversed(record['archives']):
        source, target = Path(operation['source']), Path(operation['target'])
        if not target.exists():
            continue
        if not archived or any(Path(p).is_relative_to(source) for p in active_paths):
            _restore_modes(operation)
        if archived:
            archive_mapping[str(source)] = str(target)
        else:
            if source.exists():
                raise ValueError('archive source reappeared; manual recovery required')
            target.rename(source)
    if record.get('syncthing_touched'):
        syncthing.pause()
        old_exchange = rewrite(cfg['from']['exchange'], archive_mapping)
        preserve_packets(Path(cfg['to_root']) / 'exchange', Path(old_exchange))
        syncthing.set_path(old_exchange)
        syncthing.resume()
        syncthing.wait_idle(timeout=timeout)
    for filename, original in reversed(list(record['originals'].items())):
        target = Path(rewrite(filename, archive_mapping))
        data = (journal.backup / original['file']).read_bytes()
        if archived:
            if target.suffix == '.plist':
                data = plistlib.dumps(rewrite(plistlib.loads(data), archive_mapping))
            elif target.suffix in ('.json', '.py', '.md', '.rules'):
                data = rewrite(data.decode('utf-8'), archive_mapping).encode('utf-8')
        record['restored'][filename] = hashlib.sha256(data).hexdigest()
        journal.save()
        blob(target, data, mode=original['mode'])
        adjacent = target.with_name(target.name + '.new')
        if adjacent.exists():
            if digest(adjacent) != record['written'].get(filename):
                raise ValueError('unrecognized .new file; manual recovery required')
            adjacent.unlink()
    if archived:
        # The archive becomes the active old runtime on explicit rollback.
        # Rebind only live descriptors. Never rewrite immutable packets,
        # historical recovery records, or signing identity/trust bytes.
        original_runtime = parse(read_local(rewrite(cfg['from']['runtime'], archive_mapping), private=True))
        references = [cfg['from']['runtime'], original_runtime['security_state'],
                      original_runtime['workflow_config'], cfg['jobs']['production_config'],
                      original_runtime['database'] + '.profile-log.json',
                      str(Path(original_runtime['database']).parent / '.agentmesh-worker/process.json')]
        for filename in references:
            p = Path(rewrite(filename, archive_mapping))
            if p.exists():
                data = rewrite_file(p, archive_mapping)
                if data != p.read_bytes():
                    blob(p, data, mode=stat.S_IMODE(p.stat().st_mode))
    old_runtime = rewrite(cfg['from']['runtime'], archive_mapping)
    if digest(Path(rewrite(cfg['from']['binary'], archive_mapping))) != record['old_binary_sha256']:
        raise ValueError('old binary changed; recovery refused')
    if load(old_runtime)[1] != record['scope']:
        raise ValueError('old runtime scope changed; recovery refused')
    for item in services:
        label = item['service']['Label']
        if label in record['drained'] and item['loaded']:
            supervisor.bootstrap(rewrite(item['plist'], archive_mapping))
            live = supervisor.inspect(label)
            expected = rewrite(item['service']['ProgramArguments'], archive_mapping)
            if not live['loaded'] or live['arguments'] != expected:
                raise ValueError('old service was not restored')
    record['state'] = 'rolled_back'
    journal.save()
    return dict(status='rolled_back', backup=str(journal.backup),
                post_move_rows=writes, post_move_deleted_rows=deleted,
                database_restore='not_performed')


def relocate(manifest, *, dry_run=False, timeout=300, supervisor=None, transport=None,
             runner=None, venv_builder=None, release_source=None, fault=None):
    positive(timeout)
    supervisor = supervisor or Launchd()
    runner = runner or execute
    prepared = _prepare(manifest, supervisor, transport, release_source)
    if dry_run:
        return prepared['report']
    print(json.dumps(prepared['report'], sort_keys=True), file=sys.stderr)
    print('Move selected runtime, jobs and exchange. Type RELOCATE:', file=sys.stderr)
    answer = sys.stdin.readline()
    if not answer:
        raise EOFError('RELOCATE confirmation required')
    if answer.rstrip('\r\n') != 'RELOCATE':
        return {'status': 'pending'}
    if any(digest(Path(p)) != h for p, h in prepared['hashes'].items()):
        raise ValueError('relocation inputs changed during confirmation')
    # Recheck destination emptiness, live commands and Syncthing after presence.
    fresh = _prepare(manifest, supervisor, transport, release_source)
    if fresh['hashes'] != prepared['hashes'] or fresh['scope'] != prepared['scope']:
        raise ValueError('relocation inputs changed during confirmation')
    prepared = fresh
    cfg, runtime = prepared['config'], prepared['runtime']
    from protocol_activation import require_program_capability
    require_program_capability(runtime['database'], [cfg['from']['binary']])
    root = Path(cfg['to_root'])
    root.mkdir(mode=0o700, exist_ok=True)
    backups = root / 'backups'
    backups.mkdir(mode=0o700, exist_ok=True)
    path(str(backups), directory=True, private=True)
    backup = backups / ('relocation-' + uuid.uuid4().hex)
    backup.mkdir(mode=0o700)
    services = [dict(plist=cfg['from']['plist'], service=prepared['service'], loaded=True), *prepared['jobs']]
    record = dict(format='agentmesh-relocation-journal-v1', state='relocating',
                  config=cfg, scope=prepared['scope'], targets=prepared['targets'],
                  mapping=prepared['mapping'], services=services, steps=[], originals={},
                  written={}, archives=[], drained=[], syncthing_touched=False,
                  old_binary_sha256=digest(Path(cfg['from']['binary'])))
    journal = Journal(backup, record)
    journal.save()
    locks = ExitStack()
    current = None

    def step(name, action):
        nonlocal current
        effects = {
            'preflight': [str(backup / 'relocation.json')],
            'drain': [item['service']['Label'] for item in services],
            'lock jobs': [runtime['database'] + '.scheduler.bounded.lock', runtime['database'] + '.bounded.lock'],
            'pause syncthing': [cfg['syncthing']['folder_id']],
            'copy db': [prepared['targets']['database'], str(backup / 'database.sqlite')],
            'copy state/identity': [str(root / 'memory-sync/data'), str(root / 'memory-sync/identity')],
            'copy exchange': [str(root / 'exchange')],
            'copy app files': [str(root / 'memory-sync' / p.relative_to(prepared['release'])) for p in prepared['files']] + [str(root / 'versions')],
            'copy jobs': [str(root / 'jobs'), str(root / 'status')],
            'rebind runtime (adopt)': [prepared['targets']['runtime']],
            'rewrite plists/configs/rules/settings.local': [item['plist'] for item in services] + cfg['rules'] + [cfg['settings_local']],
            'syncthing path + resume': [cfg['syncthing']['folder_id'], str(root / 'exchange')],
            'start': [prepared['service']['Label']],
            'verify': ['ingest', 'summary', 'bridge hook', 'rule recall', 'migration catch-up'],
            'archive': [str(p) for p in prepared['archives']] + list(cfg['jobs']['files']),
        }
        current = dict(name=name, state='started', changes=effects[name])
        record['steps'].append(current)
        journal.save()
        if fault:
            fault('before:' + name)
        action()
        current['state'] = 'completed'
        journal.save()
        if fault:
            fault(name)

    try:
        step('preflight', lambda: None)

        def drain():
            for item in services:
                if item['loaded']:
                    record['drained'].append(item['service']['Label'])
                    journal.save()
                    supervisor.drain(item['service']['Label'], timeout)
                    if supervisor.inspect(item['service']['Label'])['loaded']:
                        raise ValueError('service failed to drain')
        step('drain', drain)

        def lock_jobs():
            locks.enter_context(freeze(runtime['database'], timeout=timeout))
            if load(cfg['from']['runtime'])[1] != prepared['scope']:
                raise ValueError('scope changed during drain')
            for name in DESTINATIONS:
                (root / name).mkdir(mode=0o700, parents=True, exist_ok=True)
            (root / 'memory-sync/data').mkdir(mode=0o700)
        step('lock jobs', lock_jobs)

        def pause():
            record['syncthing_touched'] = True
            journal.save()
            prepared['syncthing'].pause()
        step('pause syncthing', pause)

        def copy_db():
            record['table_counts'] = copy_database(runtime['database'], prepared['targets']['database'])
            sqlite_backup(prepared['targets']['database'], backup / 'database.sqlite')
            record['baseline_sha256'] = digest(backup / 'database.sqlite')
        step('copy db', copy_db)

        def state_copy():
            old_data = Path(runtime['database']).parent
            excluded = {Path(cfg['from']['runtime']), Path(runtime['database']),
                        Path(runtime['database'] + '-wal'), Path(runtime['database'] + '-shm'),
                        Path(runtime['database'] + '-journal')}
            for p in tree(old_data):
                if p == old_data or p in excluded:
                    continue
                target = root / 'memory-sync/data' / p.relative_to(old_data)
                if p.is_dir():
                    target.mkdir(mode=stat.S_IMODE(p.stat().st_mode), exist_ok=True)
                else:
                    copy_file(p, target)
            copy_tree(runtime['security_dir'], root / 'memory-sync/identity')
        step('copy state/identity', state_copy)
        step('copy exchange', lambda: copy_tree(cfg['from']['exchange'], root / 'exchange'))

        def app_copy():
            for p in prepared['files']:
                copy_file(p, root / 'memory-sync' / p.relative_to(prepared['release']))
            installed = install_binary(Path(cfg['from']['binary']), root / 'versions')
            prepared['mapping'][cfg['from']['binary']] = str(installed)
            journal.save()
        step('copy app files', app_copy)

        def jobs_copy():
            for old, name in cfg['jobs']['files'].items():
                copy_file(old, root / 'jobs' / name)
            copy_file(cfg['jobs']['production_config'], root / 'jobs/agentmesh-production.json')
            copy_tree(cfg['jobs']['status_dir'], root / 'status')
            if venv_builder is not None:
                venv_builder(root / 'jobs/.venv', Path(cfg['jobs']['venv_requirements']), timeout=timeout)
            else:
                make_venv(root / 'jobs/.venv', Path(cfg['jobs']['venv_requirements']), timeout=timeout,
                          python=prepared['venv_python'])
        step('copy jobs', jobs_copy)

        def bind():
            from install_adopt import adopt
            result = adopt(**prepared['targets'])
            if result['status'] != 'bound':
                raise ValueError('runtime binding declined')
            if load(prepared['targets']['runtime'])[1] != prepared['scope']:
                raise ValueError('relocated identity or scope changed')
        step('rebind runtime (adopt)', bind)

        def rebind():
            mapping = prepared['mapping']
            for item in services:
                journal.write(item['plist'], rewrite_file(item['plist'], mapping))
            for rule in cfg['rules']:
                journal.write(rule, rewrite_file(rule, mapping))
            for p in [root / 'jobs' / name for name in cfg['jobs']['files'].values()] + [
                    root / 'jobs/agentmesh-production.json',
                    Path(prepared['targets']['workflow_config']), Path(prepared['targets']['security_state'])]:
                data = rewrite_file(p, mapping)
                blob(p, data, mode=stat.S_IMODE(p.stat().st_mode))
            metadata = root / 'memory-sync/data/.agentmesh-worker/process.json'
            if metadata.exists():
                blob(metadata, rewrite_file(metadata, mapping))
            local = parse(read_local(cfg['settings_local'], private=True))
            local.setdefault('bridge', {})['database'] = prepared['targets']['database']
            journal.write(cfg['settings_local'], json.dumps(local, sort_keys=True).encode('utf-8'))
            write_local(prepared['targets']['database'] + '.profile-log.json', prepared['bridge'])
            changed = [Path(item['plist']) for item in services] + list(map(Path, cfg['rules']))
            changed += [Path(cfg['settings_local']), root / 'jobs/agentmesh-production.json',
                        Path(prepared['targets']['runtime']), Path(prepared['targets']['workflow_config']),
                        Path(prepared['targets']['security_state'])]
            if any(str(runtime['app_root']).encode('utf-8') in p.read_bytes() for p in changed):
                raise ValueError('old app reference remains')
        step('rewrite plists/configs/rules/settings.local', rebind)

        def exchange_start():
            prepared['syncthing'].set_path(root / 'exchange')
            prepared['syncthing'].resume()
            prepared['syncthing'].wait_idle(timeout=timeout)
        step('syncthing path + resume', exchange_start)

        def start():
            from worker_lifecycle import status
            nonce = status(prepared['targets']['runtime']).get('nonce')
            supervisor.bootstrap(cfg['from']['plist'])
            service = plistlib.loads(read_local(cfg['from']['plist']))
            wait_health(supervisor, service, prepared['targets']['runtime'], previous_nonce=nonce, timeout=timeout)
        step('start', start)
        step('verify', lambda: record.update(verification=verify(prepared, runner=runner, timeout=timeout)))
        step('archive', lambda: _archive(prepared, journal))
        # Close all old SQLite handles before removing write permission.
        locks.close()
        _readonly_archives(journal)
        for item in prepared['jobs']:
            if item['loaded']:
                supervisor.bootstrap(item['plist'])
        record['state'] = 'active'
        journal.save()
        return dict(status='relocated', backup=str(backup), runtime=prepared['targets']['runtime'],
                    health='completed_cycle', verification=record['verification'])
    except (Exception, KeyboardInterrupt) as exc:
        locks.close()
        try:
            result = _restore(journal, supervisor, prepared['syncthing'], timeout=timeout)
            return {**result, 'error': type(exc).__name__, 'failed_step': current['name'] if current else None}
        except Exception as recovery:
            record['state'] = 'recovery_required'
            journal.save()
            return dict(status='recovery_required', backup=str(backup),
                        error=type(exc).__name__, recovery_error=type(recovery).__name__,
                        failed_step=current['name'] if current else None)
    finally:
        locks.close()


def rollback(backup, *, supervisor=None, transport=None, timeout=300):
    positive(timeout)
    supervisor = supervisor or Launchd()
    backup = path(str(backup), directory=True, private=True)
    record = parse(read_local(backup / 'relocation.json', private=True))
    if (not isinstance(record, dict) or record.get('format') != 'agentmesh-relocation-journal-v1' or
            record.get('state') not in ('active', 'relocating', 'recovery_required', 'rolling_back')):
        raise ValueError('invalid or already rolled-back relocation journal')
    print('Restore old service; retain new DB writes without merging. Type ROLLBACK:', file=sys.stderr)
    answer = sys.stdin.readline()
    if not answer:
        raise EOFError('ROLLBACK confirmation required')
    if answer.rstrip('\r\n') != 'ROLLBACK':
        return {'status': 'pending'}
    syncthing = Syncthing(**record['config']['syncthing'], transport=transport)
    return _restore(Journal(backup, record), supervisor, syncthing, timeout=timeout,
                    archived=record.get('rollback_archived', record['state'] == 'active'))
