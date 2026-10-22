"""Strict per-device desired settings; never an authorization source."""
from copy import deepcopy
import os
from pathlib import Path
import re
import stat
import unicodedata
from .encoding import parse, wire
from .broker.keystore import atomic_write

SCHEMA = 'agentmesh-settings-v1'
AGENT = re.compile(r'[a-z0-9-]{1,32}\Z')


def root():
    return Path(os.path.abspath(os.environ.get('AGENTMESH_HOME', str(Path.home() / '.agentmesh'))))


def safe(path, *, directory=False, missing=False, checkout=False):
    """checkout=True is for repository files: git writes 0644/0755, so only refuse
    group- or world-writable modes; trust pins their content by hash instead."""
    path = Path(path)
    try:
        if any(parent.is_symlink() for parent in path.parents): raise ValueError()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode): raise ValueError()
        if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)): raise ValueError()
        mode = stat.S_IMODE(info.st_mode)
        if os.name != 'nt' and (info.st_uid != os.getuid() or
                (mode & 0o022 if checkout else mode != (0o700 if directory else 0o600))):
            raise ValueError()
    except FileNotFoundError:
        if not missing: raise ValueError('invalid_settings') from None
    except (OSError, ValueError): raise ValueError('invalid_settings') from None


def scope(value):
    return (type(value) is str and bool(value) and all(p and p not in ('.', '..') for p in value.split('/'))
            and not any(unicodedata.category(c) == 'Cc' or c == '\\' for c in value))


def text(value):
    return type(value) is str and bool(value) and not any(unicodedata.category(c) == 'Cc' for c in value)


def guard(value, key=''):
    if isinstance(value, dict):
        for k, v in value.items():
            guard(k); guard(v, k)
    elif isinstance(value, list):
        for v in value: guard(v, key)
    elif isinstance(value, str):
        if 'AGE-SECRET-KEY-' in value or '-----BEGIN' in value or (key.lower().endswith('key') and re.fullmatch('[0-9a-fA-F]{64}', value)):
            raise ValueError('invalid_settings')


def fields(value, allowed):
    if type(value) is not dict or set(value) - set(allowed): raise ValueError('invalid_settings')


def check(condition):
    if not condition: raise ValueError('invalid_settings')


def validate(value, *, local=False, project=False):
    guard(value)
    if project:
        fields(value, ['project'])
        if 'project' in value:
            fields(value['project'], ['name', 'scope'])
            for k, v in value['project'].items(): check(text(v) if k == 'name' else scope(v))
        return value
    fields(value, ['$schema', 'profiles', 'agents', 'migration', 'routing', 'bridge', 'packet_sync', 'device'])
    if 'device' in value:
        from .heartbeats import name_valid
        fields(value['device'], ['name'])
        if 'name' in value['device']: check(name_valid(value['device']['name']))
    if '$schema' in value: check(value['$schema'] == SCHEMA)
    if 'profiles' in value:
        check(type(value['profiles']) is dict)
        for name, profile in value['profiles'].items():
            check(scope(name) and '/' not in name); fields(profile, ['scopes'])
            if 'scopes' in profile:
                check(type(profile['scopes']) is dict)
                for path, item in profile['scopes'].items():
                    check(scope(path)); fields(item, ['label'])
                    if 'label' in item: check(item['label'] in ('general', 'internal', 'secret'))
    if 'agents' in value:
        check(type(value['agents']) is dict)
        for name, agent in value['agents'].items():
            check(bool(AGENT.fullmatch(name)))
            fields(agent, ['read', 'write', 'default_write', 'allow_secret', 'allow_cross_profile', 'allow_import'])
            for k, v in agent.items():
                if k in ('read', 'write'): check(type(v) is list and all(scope(s) for s in v))
                elif k == 'default_write': check(v is None or scope(v))
                else: check(type(v) is bool)
    if 'migration' in value:
        m = value['migration']; fields(m, ['global', 'null_project', 'projects', 'prefixes'])
        for k in ('global', 'null_project'):
            if k in m: check(scope(m[k]))
        if 'projects' in m:
            check(type(m['projects']) is dict)
            check(all(text(k) and scope(v) for k, v in m['projects'].items()))
        if 'prefixes' in m:
            check(type(m['prefixes']) is list)
            for e in m['prefixes']:
                fields(e, ['prefix', 'target']); check(set(e) == {'prefix', 'target'})
                check(text(e['prefix']) and scope(e['target']))
            check(len({e['prefix'] for e in m['prefixes']}) == len(m['prefixes']))
    if 'routing' in value:
        fields(value['routing'], ['allowed_scopes'])
        if 'allowed_scopes' in value['routing']:
            v = value['routing']['allowed_scopes']; check(type(v) is list and all(scope(s) for s in v))
    if 'packet_sync' in value:
        fields(value['packet_sync'],['enabled'])
        if 'enabled' in value['packet_sync']: check(type(value['packet_sync']['enabled']) is bool)
    if 'bridge' in value:
        b = value['bridge']
        fields(b, ['enabled', 'recall', 'dual_write', 'recall_fallback', 'profile', 'profiles', 'timeout_seconds', 'recall_agent', 'import_agent'] + (['database', 'authority', 'cli_argv', 'device_id', 'staging_node'] if local else []))
        check(not ('profile' in b and 'profiles' in b))
        for k, v in b.items():
            if k in ('enabled', 'recall', 'dual_write', 'authority', 'recall_fallback'): check(type(v) is bool)
            elif k == 'timeout_seconds': check(type(v) in (int, float) and 1 <= v <= 30)
            elif k == 'profiles': check(type(v) is list and bool(v) and all(scope(n) and '/' not in n for n in v) and len(set(v))==len(v))
            elif k == 'device_id': check(type(v) is str and bool(re.fullmatch('[0-9a-f]{32}',v)))
            elif k == 'staging_node': check(text(v))
            elif k == 'profile': check(scope(v) and '/' not in v)
            elif k in ('recall_agent', 'import_agent'): check(type(v) is str and bool(AGENT.fullmatch(v)))
            elif k == 'database': check(text(v) and Path(v).is_absolute())
            elif k == 'cli_argv':
                check(type(v) is list and bool(v) and all(text(a) for a in v) and Path(v[0]).is_absolute())
                check((len(v) == 3 and v[1:] == ['-m', 'agentmesh_log.cli']) or (len(v) == 2 and Path(v[1]).is_absolute()))
    return value


def read_raw(path, *, checkout=False):
    path = Path(path); safe(path, checkout=checkout)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            check(stat.S_ISREG(info.st_mode))
            mode = stat.S_IMODE(info.st_mode)
            if os.name != 'nt': check(info.st_uid == os.getuid() and (not mode & 0o022 if checkout else mode == 0o600))
            return stream.read(1024 * 1024 + 1)
    except Exception: raise ValueError('invalid_settings') from None


def read(path, **options):
    path = Path(path); safe(path, missing=True)
    if not path.exists(): return {}
    try: return validate(parse(read_raw(path)), **options)
    except Exception: raise ValueError('invalid_settings') from None


def merge(low, high):
    result = deepcopy(low)
    for k, v in high.items():
        result[k] = merge(result[k], v) if isinstance(result.get(k), dict) and isinstance(v, dict) else deepcopy(v)
    return result


def load():
    base = root(); safe(base, directory=True)
    return merge({'$schema': SCHEMA}, merge(read(base / 'settings.json'), read(base / 'settings.local.json', local=True)))


def save_user(value):
    validate(value); base = root(); safe(base, directory=True)
    safe(base / 'settings.json', missing=True)
    atomic_write(base / 'settings.json', wire(value))
