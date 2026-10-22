"""Reconcile desired settings with protected broker state after console presence."""
from contextlib import contextmanager, nullcontext
from copy import deepcopy
import os
from pathlib import Path
from . import settings
from .admin import create_agent, create_scope, make_grant
from .broker.grants import names
from .broker.keystore import BrokerHome, BrokerState, atomic_write
from .encoding import wire
from .model import Label
from .oskeystore import select_store
from .presence import PresenceManager

FLAGS = ('allow_secret', 'allow_cross_profile', 'allow_import')


def agent_value(value):
    return dict(read=sorted(set(value.get('read', []))), write=sorted(set(value.get('write', []))),
                default_write=value.get('default_write'), **{k: value.get(k, False) for k in FLAGS})


def enforced_agent(state, grant):
    ns = names(state)
    return dict(read=sorted(ns[s] for s in grant.read), write=sorted(ns[s] for s in grant.write),
                default_write=ns.get(grant.default_write), **{k: getattr(grant, k) for k in FLAGS})


def mapping_value(value):
    from .migrate.mapping import Mapping
    if 'migration' not in value: return None
    m = {'format': 'agentmesh-migration-mapping-v1', **value['migration']}
    # All routing decisions must be explicit; absent global/null destinations are not guessed.
    return Mapping(m).value


def bridge_value(value, state, base):
    if 'bridge' not in value: return None
    old = state.control.get('applied_bridge', {})
    old=deepcopy(old)
    if 'profiles' in value['bridge']: old.pop('profile',None)
    if 'profile' in value['bridge']: old.pop('profiles',None)
    b = settings.merge(old, value['bridge'])
    b = settings.merge(dict(enabled=False, recall=True, recall_fallback=False, dual_write=True, authority=False, timeout_seconds=10), b)
    if not b['enabled'] and not old and set(value['bridge']) == {'enabled'}: return b
    if any(k not in b for k in ('database', 'cli_argv', 'recall_agent', 'import_agent')):
        raise ValueError('invalid_settings')
    settings.validate({'bridge': b}, local=True)
    profiles=b.get('profiles',[b['profile']] if 'profile' in b else [])
    if not profiles or not set(profiles)<={t.root.name for t in state.trees}: raise ValueError('invalid_settings')
    from .migrate.source import staging_node
    node=staging_node(b['database']); device=state.device.device_id
    if ('device_id' in b and b['device_id']!=device) or ('staging_node' in b and b['staging_node']!=node):
        raise ValueError('invalid_settings')
    b.update(device_id=device,staging_node=node)
    return b


def render_bridge(base, bridge):
    """Return the bridge's exact field set without opening the database."""
    extra=('recall_fallback','device_id','staging_node')
    if any(k in bridge for k in extra) and not all(k in bridge for k in extra): raise ValueError('invalid_settings')
    if 'profiles' in bridge and not all(k in bridge for k in extra): raise ValueError('invalid_settings')
    return dict(format='agentmesh-profile-log-bridge-v1',
                **{k: bridge[k] for k in ('enabled', 'recall', 'dual_write', 'authority', 'cli_argv', 'timeout_seconds')},
                **{k:bridge[k] for k in extra if k in bridge},
                **({'profiles':bridge['profiles']} if 'profiles' in bridge else {'profile':bridge['profile']}),
                agent_key=str(base / 'agents' / (bridge['recall_agent'] + '.key')),
                import_agent_key=str(base / 'agents' / (bridge['import_agent'] + '.key')),
                mapping=str(base / 'migration-mapping.json'))


def diff(value, state, mapping=None, bridge=None):
    changes = []; ns = names(state); by_name = {v: k for k, v in ns.items()}
    def add(kind, name, **extra): changes.append(dict(kind=kind, name=name, **extra))
    if 'profiles' in value:
        existing = {t.root.name: t for t in state.trees}
        for name in sorted(set(existing) - set(value['profiles'])): add('unsupported', name, reason='profile_remove')
        for profile, desired in sorted(value['profiles'].items()):
            if profile not in existing:
                add('unsupported', profile, reason='profile_missing'); continue
            if 'scopes' not in desired: continue
            declared = {}
            for path, item in desired['scopes'].items():
                full = path if path == profile or path.startswith(profile + '/') else profile + '/' + path
                declared[full] = item.get('label', 'general')
            for full, label in sorted(declared.items(), key=lambda item: (item[0].count('/'), item[0])):
                if full not in by_name: add('scope_create', full, label=label)
                elif existing[profile].scopes[by_name[full]].default_label.name != label:
                    add('unsupported', full, reason='scope_label')
            for sid in existing[profile].scopes:
                if sid != existing[profile].root.scope_id and ns[sid] not in declared:
                    add('unsupported', ns[sid], reason='scope_remove')
    managed = state.control.get('agent_names', {})
    if 'agents' in value:
        for name in sorted(set(managed) - set(value['agents'])): add('agent_remove', name)
        for name, raw in sorted(value['agents'].items()):
            wanted = agent_value(raw); grant = state.grants.get(managed.get(name))
            if grant is None: add('agent_create', name, after=wanted)
            else:
                old = enforced_agent(state, grant)
                if old != wanted:
                    wider = any(set(wanted[k]) - set(old[k]) for k in ('read', 'write')) or any(wanted[k] and not old[k] for k in FLAGS)
                    add('agent_widen' if wider else 'agent_narrow', name, before=old, after=wanted)
    if mapping is not None and mapping != state.control.get('applied_mapping'):
        add('mapping_change', 'migration', before=state.control.get('applied_mapping'), after=mapping)
    if bridge is not None and bridge != state.control.get('applied_bridge'):
        old = state.control.get('applied_bridge', dict(enabled=False,recall=True,recall_fallback=False,dual_write=True,authority=False,timeout_seconds=10))
        kind = 'bridge_enable' if bridge['enabled'] and not old.get('enabled', False) else 'bridge_disable' if old.get('enabled', False) and not bridge['enabled'] else 'bridge_update'
        # Paths are not necessary to describe agent selection or widening flags.
        public = lambda b: {k: v for k, v in b.items() if k not in ('database', 'cli_argv')}
        # Only switching flags off narrows. Any other change (program, database, profile,
        # agents, timeout) redirects which code runs with which agent key, so it widens.
        flags = ('enabled', 'recall', 'dual_write', 'authority')
        changed = {k for k in set(old) | set(bridge) if old.get(k) != bridge.get(k)}
        add(kind, 'bridge', before=public(old), after=public(bridge),
            widening=any(k not in flags or bridge.get(k, False) for k in changed))
    return changes


def widening(change):
    # A mapping change can move rows into a profile or scope other agents read.
    return change['kind'] in ('scope_create', 'agent_create', 'agent_widen', 'bridge_enable', 'mapping_change') or (
        change['kind'] in ('bridge_update', 'bridge_disable') and change.get('widening', False))


def describe(changes):
    if not changes: return 'no changes'
    import json
    return '\n'.join(c['kind'] + ' ' + c['name'] + ' ' + json.dumps({k: v for k, v in c.items() if k not in ('kind', 'name', 'widening')}, ensure_ascii=False, sort_keys=True) for c in changes)


def audit(base, changes, outcome):
    settings.safe(base, directory=True)
    path = base / 'apply.jsonl'; settings.safe(path, missing=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'ab') as stream:
        import stat
        info = os.fstat(stream.fileno())
        settings.check(stat.S_ISREG(info.st_mode))
        if os.name != 'nt': settings.check(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600)
        stream.write(wire(dict(kinds=[c['kind'] for c in changes], names=[c['name'] for c in changes], outcome=outcome)) + b'\n')
        stream.flush(); os.fsync(stream.fileno())


def open_home(base, backend=None):
    # BrokerHome creates directories. Require them first so dry-run is strictly read-only.
    home_path = base / 'profile-log'
    for path in (home_path, *(home_path / n for n in ('agents', 'replica', 'index'))): settings.safe(path, directory=True)
    home = BrokerHome(home_path)
    return home, select_store(home, backend)


@contextmanager
def transaction(home, store, paths, agents_dir):
    """Rollback ordinary I/O failures; no claim of cross-file crash atomicity."""
    replica = home.root / 'replica'
    originals = {p: p.read_bytes() if p.exists() else None for p in paths}
    objects = {}; directories = set()
    for p in replica.rglob('*'):
        settings.safe(p, directory=p.is_dir())
        if p.is_dir(): directories.add(p)
        else: objects[p] = p.read_bytes()
    had_agents = agents_dir.exists()
    try: yield
    except Exception:
        for p in replica.rglob('*'):
            if p.is_file() and p not in objects: p.unlink()
        for p, data in objects.items():
            if not p.exists() or p.read_bytes() != data: atomic_write(p, data)
        for p in sorted((p for p in replica.rglob('*') if p.is_dir() and p not in directories), key=lambda p: len(p.parts), reverse=True): p.rmdir()
        for p, data in originals.items():
            if data is None: p.unlink(missing_ok=True)
            else: atomic_write(p, data)
        if not had_agents and agents_dir.exists(): agents_dir.rmdir()
        raise


def apply_settings(*, dry_run=False, allow_unsupported_report=False, console=None, output=print, backend=None, project_dirs=()):
    base = settings.root(); changes = []; outcome = 'error'; state = None; value = None; mapping = None
    try:
        value = settings.load(); home, store = open_home(base, backend)
        with nullcontext() if dry_run else home.lock():
            state = store.load(); mapping = mapping_value(value)
            if mapping is not None:
                from .projects import effective_mapping
                mapping = effective_mapping(value, state, project_dirs).value
            bridge = bridge_value(value, state, base)
            changes = diff(value, state, mapping, bridge); output(describe(changes))
            if any(c['kind'] == 'unsupported' for c in changes) and not allow_unsupported_report:
                outcome = 'unsupported'; return dict(error=outcome, diff=changes)
            # Preflight all grants/scopes/output paths before presence or mutation.
            preview = BrokerState.from_record(deepcopy(state.record()))
            agents_dir = base / 'agents'; settings.safe(agents_dir, directory=True, missing=True)
            paths = {store.path}; actionable = [c for c in changes if c['kind'] != 'unsupported']
            for c in actionable:
                kind, name = c['kind'], c['name']
                if kind == 'scope_create': preview.add(name, Label[c['label']])
                elif kind.startswith('agent_'):
                    path = agents_dir / (name + '.key'); settings.safe(path, missing=True); paths.add(path)
                    if kind == 'agent_create' and path.exists(): raise ValueError('agent_file_exists')
                    if kind != 'agent_remove':
                        wanted = c['after']; make_grant(preview, '0'*32, '0'*64, wanted['read'], wanted['write'], wanted['default_write'],
                                                       **{k: wanted[k] for k in FLAGS}, declared=True)
            if mapping is not None and not (set(mapping['projects'].values()) | {mapping['global'], mapping['null_project']} | {e['target'] for e in mapping['prefixes']}) - {'skip'} <= set(names(preview).values()):
                raise ValueError('invalid_settings')
            if mapping is not None:
                path = base / 'migration-mapping.json'; settings.safe(path, missing=True); paths.add(path)
            if bridge is not None and 'database' in bridge:
                path = Path(bridge['database'] + '.profile-log.json'); settings.safe(path, missing=True); paths.add(path)
                if not path.parent.is_dir() or path.parent.is_symlink(): raise ValueError('invalid_settings')
                if bridge['enabled']:
                    declared_names = set(value.get('agents', state.control.get('agent_names', {})))
                    if not {bridge['recall_agent'], bridge['import_agent']} <= declared_names: raise ValueError('invalid_settings')
                    if mapping is None and not state.control.get('applied_mapping'): raise ValueError('invalid_settings')
            if dry_run: return dict(ok=True, diff=changes)
            settings.safe(base / 'apply.jsonl', missing=True)
            if any(widening(c) for c in actionable):
                PresenceManager(home, store).require('agent', 'apply', console=console, one_shot=True, summary=describe(changes))
            if actionable:
                with transaction(home, store, paths, agents_dir):
                    managed = state.control.setdefault('agent_names', {})
                    for c in actionable:
                        kind, name = c['kind'], c['name']
                        if kind == 'scope_create': create_scope(home, state, name, Label[c['label']])
                        elif kind == 'agent_remove':
                            state.revoke(managed.pop(name)); (agents_dir / (name + '.key')).unlink(missing_ok=True)
                        elif kind in ('agent_create', 'agent_widen', 'agent_narrow'):
                            wanted = c['after']; kw = {k: wanted[k] for k in FLAGS}
                            if kind == 'agent_create':
                                agents_dir.mkdir(mode=0o700, exist_ok=True)
                                _, grant = create_agent(home, state, name, wanted['read'], wanted['write'], wanted['default_write'],
                                                        **kw, path=agents_dir / (name + '.key'), declared=True)
                                managed[name] = grant.agent_id
                            else:
                                state.remember_import_agents(); old = state.grants[managed[name]]
                                state.grants[old.agent_id] = make_grant(state, old.agent_id, old.agent_public_key, wanted['read'], wanted['write'], wanted['default_write'], **kw, declared=True)
                    if any(c['kind'] == 'mapping_change' for c in actionable):
                        atomic_write(base / 'migration-mapping.json', wire(mapping)); state.control['applied_mapping'] = mapping
                    if any(c['kind'].startswith('bridge_') for c in actionable):
                        if 'database' in bridge: atomic_write(Path(bridge['database'] + '.profile-log.json'), wire(render_bridge(base, bridge)))
                        state.control['applied_bridge'] = bridge
                    store.save(state)
            outcome = 'approved' if actionable else 'no-op'
            return dict(ok=True, diff=changes)
    except Exception as exc:
        outcome = str(exc) if str(exc) in ('presence_required', 'invalid_settings', 'agent_file_exists') else 'apply_failed'
        return dict(error=outcome, diff=changes)
    finally:
        if not dry_run:
            # Unsafe roots/logs fail closed: never repair permissions or follow links to audit.
            try: audit(base, changes, outcome)
            except (OSError, ValueError):
                if outcome in ('approved', 'no-op'): raise ValueError('apply_audit_failed') from None

            try:
                from .status import applied
                if state is not None: state = store.load()
                applied(base,outcome,changes,state,value,mapping)
                if value is not None:
                    path=base/'packet-sync-status.json'; settings.safe(path,missing=True)
                    atomic_write(path,wire(dict(format='agentmesh-packet-sync-status-v1',packet_sync='enabled' if value.get('packet_sync',{}).get('enabled',False) else 'retired')))
            except Exception: pass
