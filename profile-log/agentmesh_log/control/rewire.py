"""`control rewire-ingest`: point the supervised ingest, summary and retention jobs at agentmesh_log instead of the
memory-sync tree (memory-sync's retirement). Owner-run in a terminal, typed presence, dry run unless --apply.

What changes, per unit, in the spec the supervisor runs (control/state.db):
  PY .../job_notify.py OPTS -- PY2 .../agentmesh_ingest.py ARGS
  -> PY -m agentmesh_log.control.jobrun OPTS -- PY2 -m agentmesh_log.ingest scan ARGS
(agentmesh_summary.py becomes `summary`; retention's own job after `--` is not memory-sync's and stays as it is). Each
interpreter is kept: the module forms are standard library only, like the scripts they replace. PYTHONPATH in the spec's
environment names the agentmesh_log this command runs from, so the job imports it whatever its interpreter, and the
child jobs inherit it. Before applying, each interpreter must import the package with that PYTHONPATH.

The supervisor reads a unit's spec at every start and schedules from the last run, so the next due run uses the new
command: none is skipped or run twice, and a run in progress is not touched. On a Mac the parked plist
(control/units/<label>.plist) is rewritten too, so `release` and `reload` keep the new command. On Windows the saved
task XML stays as it was: a later `release` restores the old task, and needs this command again after consolidate.

--back restores the specs saved by the last --apply (control/rewire-ingest.json).
"""
import json
import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

from .units import get

UNITS = ('ingest', 'summary', 'retention')
JOBS = {'agentmesh_ingest.py': 'scan', 'agentmesh_summary.py': 'summary'}
PACKAGE_ROOT = str(Path(__file__).resolve().parents[2])
SAVED = 'rewire-ingest.json'


def basename(arg):
    return arg.replace('\\', '/').rsplit('/', 1)[-1].lower()


def rewired(argv):
    """The new argv for a job_notify.py command line, or None when it does not run memory-sync's wrapper."""
    names = [basename(a) for a in argv]
    if 'job_notify.py' not in names:
        return None
    at = names.index('job_notify.py')
    python, rest = argv[:at], argv[at + 1:]
    if not python:
        return None
    if '--' not in rest:
        return [*python, '-m', 'agentmesh_log.control.jobrun', *rest]
    split = rest.index('--')
    options, job = rest[:split], rest[split + 1:]
    job_names = [basename(a) for a in job]
    for script, action in JOBS.items():
        if script in job_names:
            i = job_names.index(script)
            if i:  # an interpreter runs the script; a bare script path is left alone
                job = [*job[:i], '-m', 'agentmesh_log.ingest', action, *job[i + 1:]]
            break
    return [*python, '-m', 'agentmesh_log.control.jobrun', *options, '--', *job]


def interpreters(argv):
    """Every interpreter in a rewired argv: the one before each `-m agentmesh_log...`."""
    return sorted({argv[i - 1] for i, a in enumerate(argv) if a == '-m' and i and i + 1 < len(argv)
                   and argv[i + 1].startswith('agentmesh_log.')})


def importable(python, root=PACKAGE_ROOT, run=subprocess.run):
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([root, *[p for p in os.environ.get('PYTHONPATH', '').split(os.pathsep) if p]]))
    try:
        result = run([python, '-c', 'import agentmesh_log.control.jobrun, agentmesh_log.ingest.production'],
                     env=env, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def plan(store, root=PACKAGE_ROOT):
    rows = []
    for name in UNITS:
        backend, spec = store.backend(name), store.spec(name)
        if backend != 'native' or spec is None:
            rows.append(dict(unit=name, action='skip', reason='not_native'))
            continue
        argv = rewired(spec['argv'])
        if argv is None:
            rows.append(dict(unit=name, action='skip', reason='not_memory_sync'))
            continue
        env = dict(spec.get('env') or {})
        env['PYTHONPATH'] = root
        rows.append(dict(unit=name, action='rewire', old=spec['argv'], new=argv, spec=dict(spec, argv=argv, env=env)))
    return rows


def update_parked(layout, name, spec):
    """Mac: the parked plist gets the new ProgramArguments and environment, so release and reload keep them."""
    path = layout.units / (get(name).label + '.plist')
    if not path.is_file():
        return False
    with open(path, 'rb') as stream:
        data = plistlib.load(stream)
    data['ProgramArguments'] = spec['argv']
    if spec.get('env'):
        data['EnvironmentVariables'] = dict(spec['env'])
    else:
        data.pop('EnvironmentVariables', None)
    tmp = path.with_name(path.name + '.tmp')
    with open(tmp, 'wb') as stream:
        plistlib.dump(data, stream)
    os.replace(tmp, path)
    return True


def now_ms():
    return time.time_ns() // 1000000


def rewire(layout, store, *, console, apply=False, back=False, windows=os.name == 'nt', check=importable, audit=None):
    if back:
        return restore(layout, store, console=console, windows=windows, audit=audit)
    rows = plan(store)
    work = [r for r in rows if r['action'] == 'rewire']
    shown = [{k: v for k, v in r.items() if k != 'spec'} for r in rows]
    pythons = sorted({p for r in work for p in interpreters(r['new'])})
    imports = {p: check(p) for p in pythons}
    result = dict(ok=all(imports.values()), applied=False, units=shown, imports=imports, pythonpath=PACKAGE_ROOT)
    if not apply or not work or not result['ok']:
        if not result['ok']:
            result['error'] = 'import_failed'
        return result
    text = '\n'.join('%s:\n  %s\n->%s' % (r['unit'], ' '.join(r['old']), ' '.join(r['new'])) for r in work)
    if not console.confirm('migrate', 'Point these jobs at agentmesh_log (PYTHONPATH=%s):\n%s' % (PACKAGE_ROOT, text)):
        if audit: audit(layout, 'unit.rewire', {'units': [r['unit'] for r in work]}, 'presence_denied')
        return {'error': 'presence_required'}
    saved = layout.control / SAVED
    previous = {}
    if saved.is_file():
        previous = json.loads(saved.read_text(encoding='utf-8')).get('specs', {})
    keep = dict(previous)
    for r in work:
        keep.setdefault(r['unit'], store.spec(r['unit']))
    tmp = saved.with_name(saved.name + '.tmp')
    tmp.write_text(json.dumps(dict(saved_at=now_ms(), specs=keep), sort_keys=True), encoding='utf-8')
    os.replace(tmp, saved)
    parked = []
    for r in work:
        store.set_backend(r['unit'], 'native', r['spec'], now_ms())
        if not windows and update_parked(layout, r['unit'], r['spec']):
            parked.append(r['unit'])
    if audit: audit(layout, 'unit.rewire', {'units': [r['unit'] for r in work]}, 'ok')
    return dict(result, applied=True, parked=parked, saved=str(saved))


def restore(layout, store, *, console, windows, audit=None):
    saved = layout.control / SAVED
    if not saved.is_file():
        return {'error': 'nothing_saved'}
    specs = json.loads(saved.read_text(encoding='utf-8')).get('specs', {})
    units = sorted(n for n in specs if n in UNITS and store.backend(n) == 'native')
    if not units:
        return {'error': 'nothing_saved'}
    text = '\n'.join('%s: %s' % (n, ' '.join(specs[n]['argv'])) for n in units)
    if not console.confirm('migrate', 'Put these jobs back on their previous commands:\n%s' % text):
        if audit: audit(layout, 'unit.rewire_back', {'units': units}, 'presence_denied')
        return {'error': 'presence_required'}
    for name in units:
        store.set_backend(name, 'native', specs[name], now_ms())
        if not windows:
            update_parked(layout, name, specs[name])
    saved.replace(saved.with_name(saved.name + '.restored'))
    if audit: audit(layout, 'unit.rewire_back', {'units': units}, 'ok')
    return dict(ok=True, restored=units)
