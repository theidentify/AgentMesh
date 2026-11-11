"""One broker per joined workspace home (J3 of the one-click join design).

``workspace join-link`` creates ``<AgentMesh home>/ws/<id8>/`` with its own account, ``remote.json`` and agent. Once
that home's join is ``ready``, the supervisor runs ``broker run --home <it> --sync-interval N`` for it as a native
service unit named ``ws-<id8>``, exactly like the personal broker: kept alive, restarted with backoff, watched and
listed by ``control status``. The remote comes from the home's ``remote.json``, so no credential or bucket appears in
the unit's argv.

The supervisor calls ``attach`` at start and then every SCAN_S, so a join that finishes while it runs gets its broker
within a minute, and a home that is removed loses its unit. Nothing here reads memory or keys: only paths, the
content-free ``joins.json`` state and the presence of a key store.
"""
import json
import re
import sys
import time
from pathlib import Path
from .units import Unit

SCAN_S = 60
SYNC_INTERVAL_S = 120
RETIRED = 'launchd'   # the store allows launchd|native: a cleared spec off native means the supervisor no longer runs it
NAME = re.compile(r'ws-[0-9a-f]{8}')


def homes(layout):
    """Joined workspace homes: ``ws/<id8>/`` with a key store, a ``remote.json`` and a ready join."""
    root = Path(layout.root) / 'ws'
    try: candidates = sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink() and re.fullmatch(r'[0-9a-f]{8}', p.name))
    except FileNotFoundError: return []
    found = []
    for home in candidates:
        if not ((home / 'keystore.sealed').exists() or (home / 'keystore.json').exists()) or not (home / 'remote.json').is_file(): continue
        try: joins = json.loads((home / 'joins.json').read_text(encoding='utf-8'))
        except (FileNotFoundError, ValueError): continue
        if type(joins) is list and joins and all(type(j) is dict and j.get('state') == 'ready' for j in joins): found.append(home)
    return found


def unit_for(home):
    return Unit('ws-' + home.name, 'org.agentmesh.ws.' + home.name + '.broker', 'service', native_ok=True)


def spec_for(layout, home, python=None, interval=SYNC_INTERVAL_S):
    logs = Path(layout.root) / 'logs'
    return dict(argv=[python or sys.executable, '-m', 'agentmesh_log.cli', 'broker', 'run', '--home', str(home),
                      '--sync-interval', str(interval)],
                env={}, cwd=None, stdout=str(logs / ('ws-' + home.name + '.out.log')), stderr=str(logs / ('ws-' + home.name + '.err.log')),
                umask=0o77, interval=None, calendar=None, run_at_load=False, keep_alive=True, throttle=10)


def attach(layout, store, native, units, *, python=None, clock=time.time):
    """Register a native broker unit for every joined workspace home and retire units whose home is gone.

    ``units`` is the supervisor's unit list (shared with Control, so status lists them). Returns the names added and
    retired."""
    (Path(layout.root) / 'logs').mkdir(mode=0o700, parents=True, exist_ok=True)
    at = int(clock() * 1000); added, retired = [], []
    wanted = {}
    for home in homes(layout):
        unit = unit_for(home); wanted[unit.name] = (unit, spec_for(layout, home, python))
    for name, (unit, spec) in wanted.items():
        if name not in native.units: native.units[name] = unit
        if not any(u.name == name for u in units): units.append(unit)
        if store.backend(name) != 'native' or store.spec(name) != spec:
            store.set_backend(name, 'native', spec, at); added.append(name)
    stale = {n for n in list(native.units) + list(store.native_units()) if NAME.fullmatch(n) and n not in wanted}
    for name in sorted(stale):
        if name not in native.units:   # registered by an earlier supervisor; its home is gone
            store.set_backend(name, RETIRED, None, at); retired.append(name); continue
        unit = native.units[name]
        if store.backend(name) == 'native':
            native.stop(unit); store.set_backend(name, RETIRED, None, at); retired.append(name)
        native.units.pop(name, None)
        units[:] = [u for u in units if u.name != name]
    return dict(added=added, retired=retired)


def ticker(layout, store, native, units, *, python=None, clock=time.monotonic, scan_s=SCAN_S):
    """A function the supervisor calls every tick; it scans at most every ``scan_s`` seconds."""
    last = [None]
    def tick():
        now = clock()
        if last[0] is not None and now - last[0] < scan_s: return None
        last[0] = now
        try: return attach(layout, store, native, units, python=python)
        except Exception as exc:   # a broken home must never stop the supervisor
            print(json.dumps({'workspace_scan_error': type(exc).__name__}), file=sys.stderr, flush=True)
            return None
    return tick
