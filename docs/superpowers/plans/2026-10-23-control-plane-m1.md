# Control plane milestone 1 (observe and control, tiers 0–1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Python supervisor, `agentmesh-log control serve`, that observes the Mac's launchd units and serves tier 0–1 control operations over an owner-only Unix socket. Add maintenance mode and a summary quota guard, enforced in the existing launchd job wrapper.

**Architecture:** A new package, `profile-log/agentmesh_log/control/`. It reuses the broker's Unix transport, framing and safe audit append. launchd still starts every unit in this milestone, so the supervisor controls units only through `launchctl`. Maintenance and quota holds are files under `~/.agentmesh/control/`, and `memory-sync/job_notify.py`, which already wraps the ingest, summary and retention jobs, reads them.

**Tech Stack:** Python 3.11 standard library only (no new dependencies), pytest 8.4.2, launchctl and ps on macOS.

**Spec:** `docs/superpowers/specs/2026-10-23-local-control-plane-design.md` (milestone 1).

---

## Plan-time corrections to the spec (read first)

Reading the code during planning changed six things. Task 15 records them in the spec.

1. **Watchdog signal.** The #92 heartbeats are sealed *device* telemetry in the registry (`agentmesh_log/heartbeats.py`), not per-service liveness. In M1 the watchdog is observe-only and uses `launchctl print` (state, pid, last exit code) plus process runtime from `ps`. A job running longer than its `max_runtime_s` is reported `stuck`, and a KeepAlive service that is not running is reported `down`. Service liveness pings come in M2.
2. **`sync` is retired.** Phase 7a unloaded `org.agentmesh.sync` (packet sync). The registry has **8** units, and the spec's `sync.retry` op is dropped. Remote R2 sync belongs to the broker.
3. **Where holds are enforced.** launchd, not the supervisor, starts jobs in M1. So maintenance and quota holds are enforced in `job_notify.py`, which wraps ingest, summary and retention. `catch-up` and the services are not held in M1. They get held in M2 when they become native units.
4. **Quota cap counts LLM calls, not cost.** `status/last-summary.json` reports `llm_api_calls` per run, so the daily cap counts calls. A cost cap and `quota.set` (tier 2) come later. In M1 the policy file is optional, and without it there is no cap. 429 backoff is always on.
5. **No `state.db` yet.** Restart rate-limit counters live in supervisor memory in M1, and a supervisor restart resets them. `state.db` arrives in M2 with native units.
6. **CLI name.** M1 commands are `agentmesh-log control …` (`python -m agentmesh_log.cli control …`), next to `broker`. An `agentmesh control` alias comes later. A failed audit write returns `{"code": "failed", "message": "audit_unavailable"}`.

## Conventions

- **Python for tests:** `~/.cache/agentmesh-logvenv/bin/python` (written `$PY` below).
- **Run from the repository root.** Profile-log tests: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/...`. Memory-sync tests: `$PY -m pytest -q -p no:cacheprovider memory-sync/test_job_notify.py`.
- **Code style:** match the surrounding code, which is terse, uses dict results, and has short docstrings stating the security property.
- **Commits:** every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File structure

| File | Responsibility |
|---|---|
| Modify `profile-log/agentmesh_log/broker/audit.py` | Extract `append_row(path, row)`, the owner-only, no-symlink, fsynced JSON-line append. `Audit.append` uses it. |
| Create `profile-log/agentmesh_log/control/__init__.py` | Package marker (docstring only). |
| Create `.../control/layout.py` | Paths under `AGENTMESH_HOME`, plus `read_json` / `write_json` (owner-only, atomic). |
| Create `.../control/units.py` | The registry of the 8 launchd units. |
| Create `.../control/launchd.py` | `LaunchdBackend`: status, start, restart and stop through `launchctl`, with `ps` for runtime. |
| Create `.../control/maintenance.py` | The maintenance hold file: status, on, off. |
| Create `.../control/quota.py` | A read-only view of the quota state that `job_notify.py` writes. |
| Create `.../control/health.py` | Observe-only health per unit. |
| Create `.../control/peer.py` | Caller pid and executable, for the audit only. |
| Create `.../control/ops.py` | `Control`: op schemas, tiers, rate limits, the fail-closed audit, handlers. |
| Create `.../control/server.py` | `Supervisor`: socket accept loop, one thread and one request per connection. |
| Create `.../control/client.py` | `call(layout, op, args)`. |
| Create `.../control/plist.py` | Renders the `org.agentmesh.control` launchd plist. |
| Create `.../control/cli.py` | `control` subcommand execution. |
| Modify `profile-log/agentmesh_log/cli.py` | Register the `control` parser and dispatch it. |
| Modify `memory-sync/job_notify.py` | Maintenance and quota holds, and quota state updates. |
| Create `profile-log/tests/control/__init__.py`, `support.py`, `test_*.py` | Tests. |
| Modify `memory-sync/test_job_notify.py` | Hold and quota tests. |
| Modify `docs/superpowers/specs/2026-10-23-local-control-plane-design.md` | The corrections above. |

---

### Task 1: Shared safe audit append

**Files:**
- Modify: `profile-log/agentmesh_log/broker/audit.py`
- Test: `profile-log/tests/broker/test_audit.py`

- [ ] **Step 1: Write the failing tests.** Append to `profile-log/tests/broker/test_audit.py`:

```python
import os
import stat
import pytest
from agentmesh_log.broker.audit import append_row


def test_append_row_owner_only(tmp_path):
    append_row(tmp_path/'rows',{'op':'x'}); append_row(tmp_path/'rows',{'op':'y'})
    assert [json.loads(line)['op'] for line in (tmp_path/'rows').read_bytes().splitlines()]==['x','y']
    if os.name!='nt': assert stat.S_IMODE((tmp_path/'rows').stat().st_mode)==0o600


def test_append_row_refuses_symlink(tmp_path):
    if os.name=='nt': return
    (tmp_path/'target').write_bytes(b''); (tmp_path/'rows').symlink_to(tmp_path/'target')
    with pytest.raises(Exception): append_row(tmp_path/'rows',{'op':'x'})
    assert (tmp_path/'target').read_bytes()==b''
```

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/broker/test_audit.py`
Expected: FAIL with `ImportError: cannot import name 'append_row'`.

- [ ] **Step 3: Implement.** Replace the body of `profile-log/agentmesh_log/broker/audit.py` after the imports with:

```python
def append_row(path, row):
    """Append one canonical JSON line to an owner-only regular file (never through a symlink) and fsync it."""
    path=Path(path); data=wire(row)+b'\n'
    _check(path)
    fd = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'ab') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode): raise ValueError('unsafe audit file')
        if os.name!='nt' and (info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600):
            raise ValueError('unsafe audit file')
        stream.write(data); stream.flush(); os.fsync(stream.fileno())


class Audit:
    def __init__(self, path, key):
        self.path, self.key, self.lock = path, key, threading.Lock()

    def append(self, *, agent_id, op, outcome, profiles=None, scopes=None, cross_profile=False,
               query=None, result_ids=None, redactions=None, label=None):
        query = query if type(query) is str else ''
        row = dict(ts=time.time_ns()//1000000,agent_id=agent_id,op=op,outcome=outcome,
            profiles=profiles or [],scopes=scopes or [],cross_profile=cross_profile,
            query_hmac=hmac.new(self.key,query.encode('utf-8'),hashlib.sha256).hexdigest(),
            query_chars=len(query),result_count=len(result_ids or []),result_ids=result_ids or [],
            redactions=redactions or {},label=label)
        with self.lock: append_row(self.path,row)
```

Also add `from pathlib import Path` to the imports.

- [ ] **Step 4: Run the broker tests and confirm they pass.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/broker`
Expected: all pass, including the two new tests.

- [ ] **Step 5: Commit.**

```bash
git add profile-log/agentmesh_log/broker/audit.py profile-log/tests/broker/test_audit.py
git commit -m "refactor(broker): extract the safe audit append for reuse

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Control layout and owner-only JSON files

**Files:**
- Create: `profile-log/agentmesh_log/control/__init__.py`, `profile-log/agentmesh_log/control/layout.py`
- Create: `profile-log/tests/control/__init__.py` (empty), `profile-log/tests/control/test_layout.py`

- [ ] **Step 1: Write the failing tests.** Create `profile-log/tests/control/__init__.py` as an empty file, and create `profile-log/tests/control/test_layout.py`:

```python
import os
import stat
from agentmesh_log.control.layout import Layout, read_json, write_json


def test_layout_defaults_to_agentmesh_home(monkeypatch, tmp_path):
    monkeypatch.setenv('AGENTMESH_HOME', str(tmp_path))
    layout=Layout()
    assert layout.socket==tmp_path/'run'/'control.sock'
    assert layout.maintenance==tmp_path/'control'/'maintenance.json'
    assert layout.audit==tmp_path/'control'/'audit.jsonl'
    assert layout.status==tmp_path/'status'


def test_json_roundtrip_owner_only(tmp_path):
    layout=Layout(tmp_path).ensure()
    write_json(layout.quota,{'calls':3})
    assert read_json(layout.quota)=={'calls':3}
    if os.name!='nt':
        assert stat.S_IMODE(layout.quota.stat().st_mode)==0o600
        assert stat.S_IMODE(layout.control.stat().st_mode)==0o700


def test_read_json_missing_or_invalid_is_none(tmp_path):
    layout=Layout(tmp_path).ensure()
    assert read_json(layout.quota) is None
    layout.quota.write_text('[1]'); assert read_json(layout.quota) is None
    layout.quota.write_text('{'); assert read_json(layout.quota) is None
```

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_layout.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentmesh_log.control'`.

- [ ] **Step 3: Implement.** Create `profile-log/agentmesh_log/control/__init__.py`:

```python
"""Local control plane: a supervisor that observes and controls AgentMesh units on this host."""
```

Create `profile-log/agentmesh_log/control/layout.py`:

```python
"""Control-plane paths under the AgentMesh home; state files are owner-only JSON."""
import json
from pathlib import Path
from .. import settings
from ..broker.keystore import atomic_write
from ..storage.filesystem import _check


class Layout:
    def __init__(self, root=None):
        self.root=Path(root) if root is not None else settings.root()
        self.control=self.root/'control'; self.status=self.root/'status'; self.run=self.root/'run'
        self.socket=self.run/'control.sock'; self.audit=self.control/'audit.jsonl'
        self.maintenance=self.control/'maintenance.json'
        self.quota=self.control/'quota.json'; self.quota_policy=self.control/'quota-policy.json'

    def ensure(self):
        for directory in (self.control,self.run):
            _check(directory); directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        return self


def read_json(path):
    """A JSON object, or None when the file is missing or not an object. Symlinks are refused."""
    path=Path(path); _check(path)
    try: value=json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError,ValueError): return None
    return value if isinstance(value,dict) else None


def write_json(path, value):
    atomic_write(path,json.dumps(value,sort_keys=True,ensure_ascii=False).encode('utf-8'))
```

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_layout.py`
Expected: 3 passed.

- [ ] **Step 5: Commit.**

```bash
git add profile-log/agentmesh_log/control profile-log/tests/control
git commit -m "feat(control): layout and owner-only state files

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Unit registry

**Files:**
- Create: `profile-log/agentmesh_log/control/units.py`
- Test: `profile-log/tests/control/test_units.py`

- [ ] **Step 1: Write the failing tests.** Create `profile-log/tests/control/test_units.py`:

```python
import pytest
from agentmesh_log.control.units import UNITS, get


def test_registry_matches_installed_labels():
    assert [u.label for u in UNITS]==['org.agentmesh.ingest','org.agentmesh.summary','org.agentmesh.retention',
        'org.agentmesh.profile-log.catch-up','org.agentmesh.profile-log.broker','org.agentmesh.profile-log.bridge',
        'org.agentmesh.insight','org.agentmesh.insight-tunnel']
    assert {u.name for u in UNITS if u.wrapped}=={'ingest','summary','retention'}
    assert all(u.max_runtime_s for u in UNITS if u.kind=='job')
    assert not any(u.max_runtime_s or u.wrapped for u in UNITS if u.kind=='service')


def test_get():
    assert get('broker').kind=='service'
    with pytest.raises(KeyError): get('sync')
```

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_units.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement.** Create `profile-log/agentmesh_log/control/units.py`:

```python
"""The launchd units AgentMesh runs on a Mac. Packet sync (org.agentmesh.sync) is retired and absent."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Unit:
    name: str
    label: str
    kind: str                         # 'job' runs on a schedule; 'service' is KeepAlive
    wrapped: bool = False             # runs through job_notify.py, so maintenance and quota holds apply
    max_runtime_s: int | None = None  # a job running longer than this is reported stuck


UNITS=(
    Unit('ingest','org.agentmesh.ingest','job',True,3600),
    Unit('summary','org.agentmesh.summary','job',True,3600),
    Unit('retention','org.agentmesh.retention','job',True,3600),
    Unit('catch-up','org.agentmesh.profile-log.catch-up','job',False,1800),
    Unit('broker','org.agentmesh.profile-log.broker','service'),
    Unit('bridge','org.agentmesh.profile-log.bridge','service'),
    Unit('insight','org.agentmesh.insight','service'),
    Unit('insight-tunnel','org.agentmesh.insight-tunnel','service'),
)


def get(name):
    for unit in UNITS:
        if unit.name==name: return unit
    raise KeyError(name)
```

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_units.py`
Expected: 2 passed.

- [ ] **Step 5: Commit.**

```bash
git add profile-log/agentmesh_log/control/units.py profile-log/tests/control/test_units.py
git commit -m "feat(control): registry of the eight launchd units

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: launchd backend

**Files:**
- Create: `profile-log/agentmesh_log/control/launchd.py`
- Test: `profile-log/tests/control/test_launchd.py`

- [ ] **Step 1: Write the failing tests.** Create `profile-log/tests/control/test_launchd.py`. `SAMPLE` mirrors real `launchctl print` output: top-level keys sit one tab deep, and nested sections such as `event triggers` repeat `state` deeper, so those lines must be ignored.

```python
import pytest
from agentmesh_log.control.launchd import LaunchdBackend, parse_print, parse_etime
from agentmesh_log.control.units import get

SAMPLE=('gui/501/org.agentmesh.summary = {\n\tactive count = 1\n\tpath = /x.plist\n\tstate = running\n\n'
        '\tprogram = /p\n\tpid = 4242\n\tlast exit code = 1\n\tevent triggers = {\n\t\tstate = active\n\t}\n}\n')


def test_parse_print_reads_top_level_only():
    fields=parse_print(SAMPLE)
    assert fields['state']=='running' and fields['pid']=='4242' and fields['last exit code']=='1'


def test_parse_etime():
    assert parse_etime('05:03')==303 and parse_etime('02:00:01')==7201
    assert parse_etime('  1-00:00:05\n')==86405 and parse_etime('junk') is None


def test_status_running_with_runtime():
    calls=[]
    def runner(argv):
        calls.append(argv)
        return (0,SAMPLE) if argv[1]=='print' else (0,'  1-00:00:05\n')
    status=LaunchdBackend(runner,uid=501).status(get('summary'))
    assert status==dict(loaded=True,running=True,pid=4242,last_exit=1,runtime_s=86405)
    assert calls==[['/bin/launchctl','print','gui/501/org.agentmesh.summary'],['/bin/ps','-o','etime=','-p','4242']]


def test_status_not_loaded():
    status=LaunchdBackend(lambda argv:(113,''),uid=501).status(get('summary'))
    assert status==dict(loaded=False,running=False,pid=None,last_exit=None,runtime_s=None)


def test_never_exited_is_none():
    text='x = {\n\tstate = not running\n\tlast exit code = (never exited)\n}\n'
    assert LaunchdBackend(lambda argv:(0,text),uid=501).status(get('ingest'))['last_exit'] is None


def test_controls_use_exact_argv():
    calls=[]; backend=LaunchdBackend(lambda argv:(calls.append(argv) or (0,'')),uid=501); unit=get('ingest')
    backend.start(unit); backend.restart(unit); backend.stop(unit)
    target='gui/501/org.agentmesh.ingest'
    assert calls==[['/bin/launchctl','kickstart',target],['/bin/launchctl','kickstart','-k',target],
                   ['/bin/launchctl','kill','SIGTERM',target]]


def test_control_failure_raises():
    with pytest.raises(RuntimeError): LaunchdBackend(lambda argv:(3,''),uid=501).start(get('ingest'))
```

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_launchd.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement.** Create `profile-log/agentmesh_log/control/launchd.py`:

```python
"""launchd backend: observe and control units through launchctl only, with exact argv and no shell."""
import os
import re
import subprocess

ETIME=re.compile(r'^(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)$')


def run(argv):
    process=subprocess.run(argv,capture_output=True,text=True,timeout=10)
    return process.returncode,process.stdout


def parse_print(text):
    """Top-level `key = value` lines of `launchctl print` (exactly one tab deep)."""
    fields={}
    for line in text.splitlines():
        if line.startswith('\t') and not line.startswith('\t\t') and ' = ' in line:
            key,value=line[1:].split(' = ',1)
            fields.setdefault(key.strip(),value.strip())
    return fields


def parse_etime(text):
    """Seconds from ps etime ([[dd-]hh:]mm:ss), or None."""
    match=ETIME.match(text.strip())
    if not match: return None
    days,hours,minutes,seconds=(int(part) if part else 0 for part in match.groups())
    return ((days*24+hours)*60+minutes)*60+seconds


class LaunchdBackend:
    def __init__(self, runner=run, uid=None):
        self.runner=runner; self.domain='gui/%d'%(os.getuid() if uid is None else uid)

    def target(self, unit): return self.domain+'/'+unit.label

    def status(self, unit):
        code,out=self.runner(['/bin/launchctl','print',self.target(unit)])
        if code!=0: return dict(loaded=False,running=False,pid=None,last_exit=None,runtime_s=None)
        fields=parse_print(out)
        pid=int(fields['pid']) if fields.get('pid','').isdigit() else None
        last=fields.get('last exit code','')
        last_exit=int(last) if last.lstrip('-').isdigit() else None
        runtime=None
        if pid is not None:
            code,out=self.runner(['/bin/ps','-o','etime=','-p',str(pid)])
            runtime=parse_etime(out) if code==0 else None
        return dict(loaded=True,running=fields.get('state')=='running',pid=pid,last_exit=last_exit,runtime_s=runtime)

    def start(self, unit): return self._ok(['/bin/launchctl','kickstart',self.target(unit)])
    def restart(self, unit): return self._ok(['/bin/launchctl','kickstart','-k',self.target(unit)])
    def stop(self, unit): return self._ok(['/bin/launchctl','kill','SIGTERM',self.target(unit)])

    def _ok(self, argv):
        code,_=self.runner(argv)
        if code!=0: raise RuntimeError('launchctl_failed')
        return True
```

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_launchd.py`
Expected: 7 passed.

- [ ] **Step 5: Commit.**

```bash
git add profile-log/agentmesh_log/control/launchd.py profile-log/tests/control/test_launchd.py
git commit -m "feat(control): launchd backend over launchctl

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Maintenance hold file

**Files:**
- Create: `profile-log/agentmesh_log/control/maintenance.py`
- Test: `profile-log/tests/control/test_maintenance.py`

- [ ] **Step 1: Write the failing tests.** Create `profile-log/tests/control/test_maintenance.py`:

```python
import pytest
from agentmesh_log.control import maintenance
from agentmesh_log.control.layout import Layout


def test_off_by_default(tmp_path):
    assert maintenance.status(Layout(tmp_path))==dict(on=False,since=None,reason=None)


def test_on_is_idempotent_then_off(tmp_path):
    layout=Layout(tmp_path)
    first=maintenance.turn_on(layout,'upgrade 0.3.3',now=lambda:'2026-10-23T10:00:00+00:00')
    assert first==dict(on=True,since='2026-10-23T10:00:00+00:00',reason='upgrade 0.3.3')
    assert maintenance.turn_on(layout,'other',now=lambda:'later')==first
    assert maintenance.turn_off(layout)==dict(on=False,since=None,reason=None)


@pytest.mark.parametrize('reason',['',None,'x'*121,'line\nbreak',7])
def test_reason_validated(tmp_path, reason):
    with pytest.raises(ValueError): maintenance.turn_on(Layout(tmp_path),reason)
    assert not Layout(tmp_path).maintenance.exists()
```

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_maintenance.py`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement.** Create `profile-log/agentmesh_log/control/maintenance.py`:

```python
"""Maintenance mode: a hold file job_notify.py reads before every wrapped job. Only `"on": true` holds."""
from datetime import datetime, timezone
from .layout import read_json, write_json

REASON_MAX=120


def now_iso(): return datetime.now(timezone.utc).isoformat(timespec='seconds')


def status(layout):
    value=read_json(layout.maintenance) or {}
    on=value.get('on') is True
    return dict(on=on,since=value.get('since') if on else None,reason=value.get('reason') if on else None)


def turn_on(layout, reason, now=now_iso):
    if type(reason) is not str or not 1<=len(reason)<=REASON_MAX or not reason.isprintable(): raise ValueError('bad_args')
    current=status(layout)
    if current['on']: return current
    layout.ensure(); write_json(layout.maintenance,dict(on=True,since=now(),reason=reason))
    return status(layout)


def turn_off(layout):
    layout.ensure(); write_json(layout.maintenance,dict(on=False))
    return status(layout)
```

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_maintenance.py`
Expected: 7 passed.

- [ ] **Step 5: Commit.**

```bash
git add profile-log/agentmesh_log/control/maintenance.py profile-log/tests/control/test_maintenance.py
git commit -m "feat(control): maintenance hold file

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: job_notify honours maintenance

`job_notify.py` is standard-library only and is deployed on its own under `~/.agentmesh/memory-sync/`, so it must not import `agentmesh_log`. It finds the control directory beside its `--state-dir` (`AGENTMESH_HOME/status` → `AGENTMESH_HOME/control`). Holds and quota apply **only when that directory exists**, so hosts without the control plane, and the existing tests, behave exactly as before.

**Files:**
- Modify: `memory-sync/job_notify.py`
- Test: `memory-sync/test_job_notify.py`

- [ ] **Step 1: Write the failing tests.** Append to `memory-sync/test_job_notify.py`:

```python
NOW = '2026-10-23T10:00:00+00:00'


def control_home(tmp_path):
    (tmp_path / 'status').mkdir(); (tmp_path / 'control').mkdir()
    return tmp_path / 'status', tmp_path / 'control'


def touch(path):
    return [sys.executable, '-c', 'open(%r, "w").close()' % str(path)]


def test_maintenance_holds_wrapped_job(tmp_path, capsys):
    status, control = control_home(tmp_path)
    (control / 'maintenance.json').write_text('{"on": true, "reason": "upgrade"}')
    assert job_notify.run('ingest', touch(tmp_path / 'ran'), status, now=NOW) == 0
    assert not (tmp_path / 'ran').exists() and not (status / 'job-ingest.json').exists()
    assert json.loads(capsys.readouterr().out) == {'at': NOW, 'held': 'maintenance', 'job': 'ingest'}


def test_maintenance_off_runs(tmp_path):
    status, control = control_home(tmp_path)
    (control / 'maintenance.json').write_text('{"on": "yes"}')
    job_notify.run('ingest', touch(tmp_path / 'ran'), status, now=NOW)
    assert (tmp_path / 'ran').exists()


def test_without_control_dir_nothing_changes(tmp_path):
    status = tmp_path / 'status'; status.mkdir()
    assert job_notify.run('summary', job(tmp_path, 0), status, now=NOW) == 0
    assert not (tmp_path / 'control').exists()
```

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `$PY -m pytest -q -p no:cacheprovider memory-sync/test_job_notify.py`
Expected: `test_maintenance_holds_wrapped_job` FAILS (the job runs). The other new tests pass.

- [ ] **Step 3: Implement.** In `memory-sync/job_notify.py`, add these helpers after `SYSTEM_CA = ...`:

```python
def control_dir(state_dir):
    """AGENTMESH_HOME/control beside the job status directory; None when the control plane is absent."""
    path = Path(state_dir).resolve().parent / 'control'
    return path if path.is_dir() else None


def read_object(path):
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def parse_time(text):
    try:
        value = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except (AttributeError, TypeError, ValueError):
        return None
    return value if value.tzinfo else None


def hold_reason(name, control, now):
    """'maintenance' when the run must be skipped, else None."""
    if control is None:
        return None
    maintenance = read_object(control / 'maintenance.json')
    if maintenance and maintenance.get('on') is True:
        return 'maintenance'
    return None
```

At the very start of `run(...)`, before `state_path = ...`, insert:

```python
    current = parse_time(now) or datetime.now(timezone.utc)
    control = control_dir(state_dir)
    held = hold_reason(name, control, current)
    if held:
        print(json.dumps(dict(job=name, at=current.isoformat(timespec='seconds'), held=held), sort_keys=True))
        return 0
```

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `$PY -m pytest -q -p no:cacheprovider memory-sync/test_job_notify.py`
Expected: all pass (12 existing + 3 new).

- [ ] **Step 5: Commit.**

```bash
git add memory-sync/job_notify.py memory-sync/test_job_notify.py
git commit -m "feat(job_notify): hold wrapped jobs during control-plane maintenance

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: job_notify quota guard for summary

The rules:

- A summary failure whose `error_class`, `status` or `detail` matches `429|quota|rate limit` starts or doubles a backoff: 600 s, then 1200 s, and so on, capped at 21600 s.
- While `backoff_until` is in the future, summary runs are held.
- A success resets the backoff.
- Other failures leave the backoff unchanged.
- After each summary run, `llm_api_calls` from `status/last-summary.json` is added to today's (UTC) count, but only if that report was observed at or after the run started.
- If `control/quota-policy.json` has `{"daily_llm_calls": N}` and today's count is at least N, runs are held until the next UTC day.
- The existing failure transition already alerts once per episode. Held runs do not touch `job-summary.json`, so they never re-alert.

**Files:**
- Modify: `memory-sync/job_notify.py`
- Test: `memory-sync/test_job_notify.py`

- [ ] **Step 1: Write the failing tests.** Append to `memory-sync/test_job_notify.py`:

```python
QUOTA_FAIL = '{"status": "blocked", "error_class": "RateLimitError", "detail": "HTTP 429 quota"}'


def quota(control):
    return json.loads((control / 'quota.json').read_text())


def test_quota_failure_backs_off_and_doubles(tmp_path):
    status, control = control_home(tmp_path)
    fail = job(tmp_path, 1, QUOTA_FAIL)
    assert job_notify.run('summary', fail, status, now=NOW) == 1
    assert quota(control) == {'backoff_s': 600, 'backoff_until': '2026-10-23T10:10:00+00:00', 'calls': 0, 'day': '2026-10-23'}
    assert job_notify.run('summary', fail, status, now='2026-10-23T10:05:00+00:00') == 0  # held
    assert quota(control)['backoff_s'] == 600
    assert job_notify.run('summary', fail, status, now='2026-10-23T10:11:00+00:00') == 1
    assert quota(control)['backoff_s'] == 1200


def test_success_resets_backoff(tmp_path):
    status, control = control_home(tmp_path)
    job_notify.run('summary', job(tmp_path, 1, QUOTA_FAIL), status, now=NOW)
    job_notify.run('summary', job(tmp_path, 0), status, now='2026-10-23T10:11:00+00:00')
    assert quota(control)['backoff_s'] == 0 and quota(control)['backoff_until'] is None


def test_other_failures_do_not_back_off(tmp_path):
    status, control = control_home(tmp_path)
    job_notify.run('summary', job(tmp_path, 1, '{"error_class": "ValidationError"}'), status, now=NOW)
    assert quota(control)['backoff_s'] == 0


def test_daily_call_cap(tmp_path):
    status, control = control_home(tmp_path)
    (control / 'quota-policy.json').write_text('{"daily_llm_calls": 5}')
    report = '{"observed_at": "2026-10-23T10:00:30+00:00", "llm_api_calls": 5}'
    writer = [sys.executable, '-c', 'open(%r, "w").write(%r)' % (str(status / 'last-summary.json'), report)]
    assert job_notify.run('summary', writer, status, now=NOW) == 0
    assert quota(control)['calls'] == 5
    assert job_notify.run('summary', job(tmp_path, 1), status, now='2026-10-23T10:05:00+00:00') == 0  # held
    assert job_notify.run('summary', job(tmp_path, 0), status, now='2026-10-24T00:01:00+00:00') == 0  # new day runs
    assert quota(control)['day'] == '2026-10-24' and quota(control)['calls'] == 0  # old report not counted


def test_quota_only_for_summary(tmp_path):
    status, control = control_home(tmp_path)
    job_notify.run('ingest', job(tmp_path, 1, QUOTA_FAIL), status, now=NOW)
    assert not (control / 'quota.json').exists()
```

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `$PY -m pytest -q -p no:cacheprovider memory-sync/test_job_notify.py`
Expected: the new quota tests FAIL (`quota.json` is never written).

- [ ] **Step 3: Implement.** In `memory-sync/job_notify.py`, change `from datetime import datetime, timezone` to `from datetime import datetime, timedelta, timezone`, and add these constants after `SYSTEM_CA`:

```python
QUOTA_JOBS = ('summary',)
QUOTA = re.compile(r'\b429\b|quota|rate.?limit', re.I)
BACKOFF_START, BACKOFF_MAX = 600, 21600
```

Add these helpers after `read_object`:

```python
def write_object(path, value):
    path = Path(path)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, sort_keys=True))
    if os.name != 'nt':
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def update_quota(control, state_dir, ok, fields, started, now):
    """Count today's LLM calls and start, double or clear the quota backoff."""
    quota = read_object(control / 'quota.json') or {}
    day = now.date().isoformat()
    calls = quota['calls'] if quota.get('day') == day and type(quota.get('calls')) is int else 0
    report = read_object(Path(state_dir) / 'last-summary.json') or {}
    observed, used = parse_time(report.get('observed_at')), report.get('llm_api_calls')
    if observed and observed >= started and type(used) is int and used >= 0:
        calls += used
    backoff = quota['backoff_s'] if type(quota.get('backoff_s')) is int else 0
    until = quota.get('backoff_until')
    reason = ' '.join(str(fields.get(key, '')) for key in ('error_class', 'status', 'detail'))
    if ok:
        backoff, until = 0, None
    elif QUOTA.search(reason):
        backoff = min(BACKOFF_MAX, backoff * 2 if backoff else BACKOFF_START)
        until = (now + timedelta(seconds=backoff)).isoformat(timespec='seconds')
    write_object(control / 'quota.json', dict(day=day, calls=calls, backoff_s=backoff, backoff_until=until))
```

Replace `hold_reason` with:

```python
def hold_reason(name, control, now):
    """'maintenance' or 'quota' when the run must be skipped, else None."""
    if control is None:
        return None
    maintenance = read_object(control / 'maintenance.json')
    if maintenance and maintenance.get('on') is True:
        return 'maintenance'
    if name not in QUOTA_JOBS:
        return None
    quota = read_object(control / 'quota.json') or {}
    until = parse_time(quota.get('backoff_until'))
    if until and until > now:
        return 'quota'
    cap = (read_object(control / 'quota-policy.json') or {}).get('daily_llm_calls')
    calls = quota.get('calls')
    if type(cap) is int and cap >= 0 and quota.get('day') == now.date().isoformat() and type(calls) is int and calls >= cap:
        return 'quota'
    return None
```

In `run(...)`, directly after the line `event, since, alerted = transition(previous, ok, now)`, insert:

```python
    if control is not None and name in QUOTA_JOBS:
        try:
            update_quota(control, state_dir, ok, fields, current, parse_time(now) or datetime.now(timezone.utc))
        except OSError as exc:
            print(json.dumps({'job': name, 'quota_error': type(exc).__name__}), file=sys.stderr)
```

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `$PY -m pytest -q -p no:cacheprovider memory-sync/test_job_notify.py`
Expected: all pass (20 tests).

- [ ] **Step 5: Commit.**

```bash
git add memory-sync/job_notify.py memory-sync/test_job_notify.py
git commit -m "feat(job_notify): summary quota backoff and optional daily call cap

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Read-only quota status

**Files:**
- Create: `profile-log/agentmesh_log/control/quota.py`
- Test: `profile-log/tests/control/test_quota.py`

- [ ] **Step 1: Write the failing tests.** Create `profile-log/tests/control/test_quota.py`:

```python
from agentmesh_log.control import quota
from agentmesh_log.control.layout import Layout


def test_defaults_without_files(tmp_path):
    assert quota.status(Layout(tmp_path))==dict(day=None,calls=0,daily_llm_calls=None,backoff_s=0,backoff_until=None)


def test_reads_job_notify_state(tmp_path):
    layout=Layout(tmp_path).ensure()
    layout.quota.write_text('{"day": "2026-10-23", "calls": 4, "backoff_s": 600, "backoff_until": "2026-10-23T10:10:00+00:00"}')
    layout.quota_policy.write_text('{"daily_llm_calls": 50}')
    assert quota.status(layout)==dict(day='2026-10-23',calls=4,daily_llm_calls=50,backoff_s=600,backoff_until='2026-10-23T10:10:00+00:00')


def test_invalid_values_ignored(tmp_path):
    layout=Layout(tmp_path).ensure()
    layout.quota.write_text('{"calls": "4", "backoff_s": true}'); layout.quota_policy.write_text('{"daily_llm_calls": -1}')
    status=quota.status(layout)
    assert status['calls']==0 and status['backoff_s']==0 and status['daily_llm_calls'] is None
```

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_quota.py`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement.** Create `profile-log/agentmesh_log/control/quota.py`:

```python
"""Read-only view of the summary quota guard that job_notify.py maintains (control/quota.json)."""
from .layout import read_json


def count(value): return value if type(value) is int and value>=0 else None


def status(layout):
    state=read_json(layout.quota) or {}; policy=read_json(layout.quota_policy) or {}
    return dict(day=state.get('day') if type(state.get('day')) is str else None,
        calls=count(state.get('calls')) or 0,daily_llm_calls=count(policy.get('daily_llm_calls')),
        backoff_s=count(state.get('backoff_s')) or 0,
        backoff_until=state.get('backoff_until') if type(state.get('backoff_until')) is str else None)
```

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_quota.py`
Expected: 3 passed.

- [ ] **Step 5: Commit.**

```bash
git add profile-log/agentmesh_log/control/quota.py profile-log/tests/control/test_quota.py
git commit -m "feat(control): read-only quota status

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Observe-only health

**Files:**
- Create: `profile-log/agentmesh_log/control/health.py`
- Create: `profile-log/tests/control/support.py`
- Test: `profile-log/tests/control/test_health.py`

- [ ] **Step 1: Write the shared fake and the failing tests.** Create `profile-log/tests/control/support.py`:

```python
IDLE=dict(loaded=True,running=False,pid=None,last_exit=0,runtime_s=None)


class FakeBackend:
    def __init__(self, statuses=None, fail=False):
        self.statuses,self.fail,self.calls=statuses or {},fail,[]

    def status(self, unit): return self.statuses.get(unit.name,IDLE)

    def _act(self, action, unit):
        if self.fail: raise RuntimeError('launchctl_failed')
        self.calls.append((action,unit.name)); return True

    def start(self, unit): return self._act('start',unit)
    def restart(self, unit): return self._act('restart',unit)
    def stop(self, unit): return self._act('stop',unit)
```

Create `profile-log/tests/control/test_health.py`:

```python
from agentmesh_log.control.health import assess, observe
from agentmesh_log.control.layout import Layout
from agentmesh_log.control.units import UNITS, get
from .support import FakeBackend, IDLE


def test_assess():
    job,service=get('ingest'),get('broker')
    assert assess(job,dict(IDLE,loaded=False),None)=='missing'
    assert assess(job,dict(IDLE,running=True,pid=1,runtime_s=3600),None)=='running'
    assert assess(job,dict(IDLE,running=True,pid=1,runtime_s=3601),None)=='stuck'
    assert assess(job,dict(IDLE,running=True,pid=1,runtime_s=None),None)=='running'
    assert assess(service,dict(IDLE,running=True,pid=1,runtime_s=10**7),None)=='running'
    assert assess(service,IDLE,None)=='down'
    assert assess(job,IDLE,dict(ok=False,at='t',since='t'))=='failing'
    assert assess(get('catch-up'),dict(IDLE,last_exit=1),None)=='failing'
    assert assess(job,IDLE,dict(ok=True,at='t',since='t'))=='idle'


def test_observe_reads_job_state(tmp_path):
    layout=Layout(tmp_path); layout.status.mkdir()
    (layout.status/'job-summary.json').write_text('{"ok": false, "at": "a", "since": "s", "alerted": true}')
    rows={row['name']:row for row in observe(layout,FakeBackend(),UNITS)}
    assert len(rows)==8
    assert rows['summary']['health']=='failing' and rows['summary']['job']==dict(ok=False,at='a',since='s')
    assert rows['broker']['health']=='down' and rows['catch-up']['job'] is None
```

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_health.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement.** Create `profile-log/agentmesh_log/control/health.py`:

```python
"""Observe-only health from launchctl and job state files. M1 reports; it never restarts anything."""
from .layout import read_json


def job_state(layout, unit):
    if not unit.wrapped: return None
    value=read_json(layout.status/('job-%s.json'%unit.name))
    if value is None: return None
    return dict(ok=value['ok'] if type(value.get('ok')) is bool else None,at=value.get('at'),since=value.get('since'))


def assess(unit, launchd, job):
    """One of: missing, running, stuck, down, failing, idle."""
    if not launchd['loaded']: return 'missing'
    if launchd['running']:
        limit,runtime=unit.max_runtime_s,launchd['runtime_s']
        return 'stuck' if unit.kind=='job' and limit and runtime is not None and runtime>limit else 'running'
    if unit.kind=='service': return 'down'
    if job is not None: return 'failing' if job['ok'] is False else 'idle'
    return 'failing' if launchd['last_exit'] not in (None,0) else 'idle'


def observe(layout, backend, units):
    rows=[]
    for unit in units:
        launchd=backend.status(unit); job=job_state(layout,unit)
        rows.append(dict(name=unit.name,kind=unit.kind,health=assess(unit,launchd,job),pid=launchd['pid'],
            runtime_s=launchd['runtime_s'],last_exit=launchd['last_exit'],job=job))
    return rows
```

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_health.py`
Expected: 2 passed.

- [ ] **Step 5: Commit.**

```bash
git add profile-log/agentmesh_log/control/health.py profile-log/tests/control/support.py profile-log/tests/control/test_health.py
git commit -m "feat(control): observe-only unit health

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Caller identity for the audit

**Files:**
- Create: `profile-log/agentmesh_log/control/peer.py`
- Test: `profile-log/tests/control/test_peer.py`

- [ ] **Step 1: Write the failing tests.** Create `profile-log/tests/control/test_peer.py`:

```python
import os
import socket
import pytest
from agentmesh_log.control.peer import peer_pid, executable


@pytest.mark.skipif(os.name=='nt',reason='AF_UNIX peer credentials')
def test_peer_pid_of_socketpair():
    a,b=socket.socketpair(socket.AF_UNIX)
    try: assert peer_pid(a)==os.getpid()
    finally: a.close(); b.close()


def test_executable_uses_ps():
    class Done: returncode=0; stdout='/usr/bin/python3\n'
    seen=[]
    assert executable(42,runner=lambda argv,**kw:(seen.append(argv) or Done()))=='/usr/bin/python3'
    assert seen==[['/bin/ps','-o','comm=','-p','42']]
    assert executable(None) is None


def test_executable_failure_is_none():
    class Gone: returncode=1; stdout=''
    assert executable(42,runner=lambda argv,**kw:Gone()) is None
```

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_peer.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement.** Create `profile-log/agentmesh_log/control/peer.py`:

```python
"""Who is calling: peer pid and executable. Audit context only, never authority (same-user code can lie)."""
import socket
import struct
import subprocess
import sys

SOL_LOCAL,LOCAL_PEERPID=0,2  # <sys/un.h> on macOS


def peer_pid(sock):
    try:
        if hasattr(socket,'SO_PEERCRED'):
            return struct.unpack('3i',sock.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[0]
        if sys.platform=='darwin':
            return struct.unpack('i',sock.getsockopt(SOL_LOCAL,LOCAL_PEERPID,4))[0]
    except OSError: pass
    return None


def executable(pid, runner=subprocess.run):
    if pid is None: return None
    try: process=runner(['/bin/ps','-o','comm=','-p',str(pid)],capture_output=True,text=True,timeout=5)
    except (OSError,subprocess.SubprocessError): return None
    if process.returncode!=0: return None
    return process.stdout.strip()[:256] or None
```

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_peer.py`
Expected: 3 passed (the socketpair test is skipped on Windows).

- [ ] **Step 5: Commit.**

```bash
git add profile-log/agentmesh_log/control/peer.py profile-log/tests/control/test_peer.py
git commit -m "feat(control): caller pid and executable for the audit

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Operations, tiers and the fail-closed audit

These tests are the **M1 mutation gates**. After they pass, mutate `ops.py` by hand, one change at a time, and confirm that at least one test fails for each mutant (Step 5).

**Files:**
- Create: `profile-log/agentmesh_log/control/ops.py`
- Test: `profile-log/tests/control/test_ops.py`

- [ ] **Step 1: Write the failing tests.** Create `profile-log/tests/control/test_ops.py`:

```python
import json
import os
import pytest
from agentmesh_log.control.layout import Layout
from agentmesh_log.control.ops import Control
from .support import FakeBackend

CALLER=dict(pid=7,exe='/usr/bin/agent')


def request(op, args=None, rid='r1'): return dict(v=1,id=rid,op=op,args=args or {})


def audit_rows(tmp_path):
    return [json.loads(line) for line in Layout(tmp_path).audit.read_bytes().splitlines()]


def test_status_is_tier0_and_not_audited(tmp_path):
    response=Control(Layout(tmp_path),FakeBackend()).handle(request('status'),CALLER)
    assert response['ok'] and response['id']=='r1' and len(response['result']['units'])==8
    assert response['result']['maintenance']['on'] is False and response['result']['quota']['calls']==0
    assert not Layout(tmp_path).audit.exists()


@pytest.mark.parametrize('bad',[None,[],dict(v=1,id='r',op='status'),dict(v=True,id='r',op='status',args={}),
    dict(v=2,id='r',op='status',args={}),dict(v=1,id='',op='status',args={}),dict(v=1,id='x'*65,op='status',args={}),
    dict(v=1,id='r',op='status',args={},extra=1),dict(v=1,id='r',op='status',args=[])])
def test_envelope_refused(tmp_path, bad):
    assert Control(Layout(tmp_path),FakeBackend()).handle(bad,CALLER)['error']['code']=='bad_args'


def test_unknown_op(tmp_path):
    assert Control(Layout(tmp_path),FakeBackend()).handle(request('unit.delete'),CALLER)['error']['code']=='unknown_op'


def test_exact_arguments(tmp_path):
    control=Control(Layout(tmp_path),FakeBackend())
    assert control.handle(request('status',{'x':1}),CALLER)['error']['code']=='bad_args'
    assert control.handle(request('unit.restart'),CALLER)['error']['code']=='bad_args'
    assert control.handle(request('unit.restart',{'unit':'ingest','force':True}),CALLER)['error']['code']=='bad_args'
    assert control.handle(request('unit.restart',{'unit':'sync'}),CALLER)['error']==dict(code='bad_args',message='unknown_unit')
    assert control.handle(request('unit.restart',{'unit':['ingest']}),CALLER)['error']['code']=='bad_args'


def test_restart_audits_request_and_outcome(tmp_path):
    backend=FakeBackend(); control=Control(Layout(tmp_path),backend)
    assert control.handle(request('unit.restart',{'unit':'ingest'}),CALLER)['result']==dict(unit='ingest',action='restart')
    assert backend.calls==[('restart','ingest')]
    rows=audit_rows(tmp_path)
    assert [row['outcome'] for row in rows]==['requested','ok']
    assert rows[0]['op']=='unit.restart' and rows[0]['args']=={'unit':'ingest'}
    assert rows[0]['caller_pid']==7 and rows[0]['caller_exe']=='/usr/bin/agent'


@pytest.mark.skipif(os.name=='nt',reason='symlink refusal')
def test_audit_failure_fails_closed(tmp_path):
    layout=Layout(tmp_path).ensure()
    (tmp_path/'elsewhere').write_bytes(b''); layout.audit.symlink_to(tmp_path/'elsewhere')
    backend=FakeBackend(); control=Control(layout,backend)
    response=control.handle(request('unit.restart',{'unit':'ingest'}),CALLER)
    assert response['error']==dict(code='failed',message='audit_unavailable') and backend.calls==[]
    assert control.handle(request('maintenance.on',{'reason':'x'}),CALLER)['error']['message']=='audit_unavailable'
    assert not layout.maintenance.exists()
    assert control.handle(request('status'),CALLER)['ok']
    assert (tmp_path/'elsewhere').read_bytes()==b''


def test_rate_limit_per_unit_per_hour(tmp_path):
    now=[0.0]; backend=FakeBackend(); control=Control(Layout(tmp_path),backend,clock=lambda:now[0])
    for _ in range(3): assert control.handle(request('unit.restart',{'unit':'ingest'}),CALLER)['ok']
    assert control.handle(request('unit.start',{'unit':'ingest'}),CALLER)['error']['code']=='rate_limited'
    assert control.handle(request('unit.restart',{'unit':'summary'}),CALLER)['ok']
    now[0]=3599.0
    assert control.handle(request('unit.restart',{'unit':'ingest'}),CALLER)['error']['code']=='rate_limited'
    now[0]=3600.0
    assert control.handle(request('unit.start',{'unit':'ingest'}),CALLER)['ok']
    assert audit_rows(tmp_path)[-3]['outcome']=='rate_limited'


def test_maintenance_blocks_wrapped_starts(tmp_path):
    backend=FakeBackend(); control=Control(Layout(tmp_path),backend)
    assert control.handle(request('maintenance.on',{'reason':'upgrade'}),CALLER)['result']['on'] is True
    for op,unit in (('unit.start','summary'),('unit.restart','ingest'),('unit.start','retention')):
        assert control.handle(request(op,{'unit':unit}),CALLER)['error']['code']=='maintenance'
    assert control.handle(request('unit.restart',{'unit':'broker'}),CALLER)['ok']
    assert control.handle(request('maintenance.off'),CALLER)['result']['on'] is False
    assert control.handle(request('unit.start',{'unit':'summary'}),CALLER)['ok']
    assert backend.calls==[('restart','broker'),('start','summary')]


def test_maintenance_reason_validated(tmp_path):
    assert Control(Layout(tmp_path),FakeBackend()).handle(request('maintenance.on',{'reason':''}),CALLER)['error']['code']=='bad_args'


def test_stop_only_jobs(tmp_path):
    backend=FakeBackend(); control=Control(Layout(tmp_path),backend)
    assert control.handle(request('unit.stop',{'unit':'broker'}),CALLER)['error']==dict(code='bad_args',message='service_keepalive')
    assert control.handle(request('unit.stop',{'unit':'ingest'}),CALLER)['ok'] and backend.calls==[('stop','ingest')]


def test_backend_failure_is_failed_and_audited(tmp_path):
    control=Control(Layout(tmp_path),FakeBackend(fail=True))
    assert control.handle(request('unit.restart',{'unit':'ingest'}),CALLER)['error']==dict(code='failed',message='')
    assert [row['outcome'] for row in audit_rows(tmp_path)]==['requested','failed']


def test_audit_tail(tmp_path):
    control=Control(Layout(tmp_path),FakeBackend())
    assert control.handle(request('audit.tail',{'limit':5}),CALLER)['result']==[]
    control.handle(request('unit.restart',{'unit':'ingest'}),CALLER)
    rows=control.handle(request('audit.tail',{'limit':1}),CALLER)['result']
    assert len(rows)==1 and rows[0]['outcome']=='ok'
    for bad in (0,201,True,'5'):
        assert control.handle(request('audit.tail',{'limit':bad}),CALLER)['error']['code']=='bad_args'


def test_units_and_diagnose(tmp_path):
    control=Control(Layout(tmp_path),FakeBackend())
    units=control.handle(request('units'),CALLER)['result']
    assert [u['name'] for u in units][:2]==['ingest','summary'] and units[0]['wrapped'] is True
    report=control.handle(request('diagnose'),CALLER)['result']
    assert set(report)=={'status','units','python','platform'}
```

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_ops.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement.** Create `profile-log/agentmesh_log/control/ops.py`:

```python
"""Control operations: exact schemas, tiers, rate limits and the fail-closed audit rule.

Tier 0 reads are never audited. Every tier 1 action is audited as `requested` before it runs;
if that write fails the action is refused, so nothing changes without a record.
"""
import json
import platform
import sys
import threading
import time
from ..broker.audit import append_row
from . import health, maintenance, quota
from .units import UNITS

RESTARTS_PER_HOUR=3
AUDIT_TAIL_MAX=200
ENVELOPE=frozenset({'v','id','op','args'})
OPS={  # op: (tier, exact argument names)
    'status':(0,frozenset()),'units':(0,frozenset()),'quota.status':(0,frozenset()),
    'audit.tail':(0,frozenset({'limit'})),
    'unit.start':(1,frozenset({'unit'})),'unit.stop':(1,frozenset({'unit'})),'unit.restart':(1,frozenset({'unit'})),
    'maintenance.on':(1,frozenset({'reason'})),'maintenance.off':(1,frozenset()),'diagnose':(1,frozenset()),
}


class OpError(Exception):
    def __init__(self, code, message=''):
        super().__init__(code); self.code,self.message=code,message


class Control:
    def __init__(self, layout, backend, *, clock=time.monotonic, units=UNITS):
        self.layout,self.backend,self.clock,self.units=layout,backend,clock,units
        self.lock=threading.Lock(); self.recent={}

    def handle(self, request, caller):
        """One request in, one response out; never raises."""
        rid=request.get('id') if isinstance(request,dict) and type(request.get('id')) is str else None
        op=None
        try:
            op,args=self.validate(request)
            if OPS[op][0]>=1: self.record(op,args,caller,'requested')
            result=getattr(self,'op_'+op.replace('.','_'))(**args)
            if OPS[op][0]>=1: self.record(op,args,caller,'ok',strict=False)
            return dict(id=rid,ok=True,result=result)
        except OpError as error:
            if op is not None and OPS[op][0]>=1 and error.message!='audit_unavailable':
                self.record(op,request['args'],caller,error.code,strict=False)
            return dict(id=rid,ok=False,error=dict(code=error.code,message=error.message))
        except Exception:
            if op is not None and OPS[op][0]>=1: self.record(op,request['args'],caller,'failed',strict=False)
            return dict(id=rid,ok=False,error=dict(code='failed',message=''))

    def validate(self, request):
        if (not isinstance(request,dict) or set(request)!=ENVELOPE or type(request['v']) is not int or request['v']!=1
                or type(request['id']) is not str or not 1<=len(request['id'])<=64):
            raise OpError('bad_args')
        op,args=request['op'],request['args']
        if type(op) is not str or op not in OPS: raise OpError('unknown_op')
        if not isinstance(args,dict) or set(args)!=OPS[op][1]: raise OpError('bad_args')
        return op,args

    def record(self, op, args, caller, outcome, strict=True):
        row=dict(ts=time.time_ns()//1000000,op=op,args=args,outcome=outcome,
            caller_pid=caller.get('pid'),caller_exe=caller.get('exe'))
        try: self.layout.ensure(); append_row(self.layout.audit,row)
        except Exception:
            if strict: raise OpError('failed','audit_unavailable') from None

    def unit(self, name):
        for unit in self.units:
            if type(name) is str and unit.name==name: return unit
        raise OpError('bad_args','unknown_unit')

    def runnable(self, unit):
        if unit.wrapped and maintenance.status(self.layout)['on']: raise OpError('maintenance')

    def throttle(self, unit):
        with self.lock:
            now=self.clock(); window=[t for t in self.recent.get(unit.name,()) if now-t<3600]
            if len(window)>=RESTARTS_PER_HOUR: raise OpError('rate_limited')
            window.append(now); self.recent[unit.name]=window

    def op_status(self):
        return dict(units=health.observe(self.layout,self.backend,self.units),
            maintenance=maintenance.status(self.layout),quota=quota.status(self.layout))

    def op_units(self):
        return [dict(name=u.name,label=u.label,kind=u.kind,wrapped=u.wrapped,max_runtime_s=u.max_runtime_s) for u in self.units]

    def op_quota_status(self): return quota.status(self.layout)

    def op_audit_tail(self, limit):
        if type(limit) is not int or not 1<=limit<=AUDIT_TAIL_MAX: raise OpError('bad_args')
        try: lines=self.layout.audit.read_bytes().splitlines()[-limit:]
        except FileNotFoundError: return []
        return [json.loads(line) for line in lines]

    def op_unit_start(self, unit):
        unit=self.unit(unit); self.runnable(unit); self.throttle(unit); self.backend.start(unit)
        return dict(unit=unit.name,action='start')

    def op_unit_restart(self, unit):
        unit=self.unit(unit); self.runnable(unit); self.throttle(unit); self.backend.restart(unit)
        return dict(unit=unit.name,action='restart')

    def op_unit_stop(self, unit):
        unit=self.unit(unit)
        if unit.kind!='job': raise OpError('bad_args','service_keepalive')
        self.backend.stop(unit)
        return dict(unit=unit.name,action='stop')

    def op_maintenance_on(self, reason):
        try: return maintenance.turn_on(self.layout,reason)
        except ValueError: raise OpError('bad_args') from None

    def op_maintenance_off(self): return maintenance.turn_off(self.layout)

    def op_diagnose(self):
        return dict(status=self.op_status(),units=self.op_units(),python=sys.version.split()[0],
            platform=platform.platform(terse=True))
```

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_ops.py`
Expected: all pass (21 tests including the parametrized cases).

- [ ] **Step 5: Mutation gate (manual, record the results in the PR).** Apply each mutant to `ops.py` alone, run `test_ops.py`, confirm at least one FAILURE, then revert:
  1. `if OPS[op][0]>=1: self.record(op,args,caller,'requested')` → `pass` (no pre-audit).
  2. `strict=True` default → `strict=False`.
  3. `set(args)!=OPS[op][1]` → `not set(args)<=OPS[op][1]`.
  4. `request['v']!=1` → `request['v']<1`, and separately remove `type(request['v']) is not int or`.
  5. `len(window)>=RESTARTS_PER_HOUR` → `len(window)>RESTARTS_PER_HOUR`.
  6. `now-t<3600` → `now-t<=3600`.
  7. Delete `self.runnable(unit);` from `op_unit_restart`.
  8. `if unit.kind!='job'` → `if False`.

Expected: every mutant is killed. If one survives, add a test that kills it before you commit.

- [ ] **Step 6: Commit.**

```bash
git add profile-log/agentmesh_log/control/ops.py profile-log/tests/control/test_ops.py
git commit -m "feat(control): tier 0-1 operations with fail-closed audit and rate limits

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Supervisor socket and client

**Files:**
- Create: `profile-log/agentmesh_log/control/server.py`, `profile-log/agentmesh_log/control/client.py`
- Test: `profile-log/tests/control/test_server.py`

- [ ] **Step 1: Write the failing test.** Create `profile-log/tests/control/test_server.py`. The socket lives in a short `/tmp` directory because macOS limits AF_UNIX paths to 104 bytes. This is the same approach as `tests/broker/test_transport.py`.

```python
import json
import os
import shutil
import stat
import tempfile
import threading
import time
import pytest
from agentmesh_log.control.client import call
from agentmesh_log.control.layout import Layout
from agentmesh_log.control.ops import Control
from agentmesh_log.control.server import Supervisor
from .support import FakeBackend


def call_when_ready(layout, op, args):
    for _ in range(100):
        try: return call(layout,op,args)
        except (FileNotFoundError,ConnectionRefusedError): time.sleep(0.02)
    raise AssertionError('supervisor did not start')


@pytest.mark.skipif(os.name=='nt',reason='Unix socket')
def test_supervisor_round_trip():
    root=tempfile.mkdtemp(prefix='amc',dir='/tmp'); layout=Layout(root); backend=FakeBackend()
    supervisor=Supervisor(Control(layout,backend),layout)
    thread=threading.Thread(target=supervisor.serve,daemon=True); thread.start()
    try:
        response=call_when_ready(layout,'unit.restart',{'unit':'ingest'})
        assert response['ok'] and backend.calls==[('restart','ingest')]
        assert call(layout,'status',{})['ok']
        row=json.loads(layout.audit.read_bytes().splitlines()[0])
        assert row['caller_pid']==os.getpid()
        assert stat.S_IMODE(layout.socket.stat().st_mode)==0o600
    finally:
        supervisor.close(); thread.join(5); shutil.rmtree(root)
    assert not thread.is_alive()
```

- [ ] **Step 2: Run the test and confirm it fails.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_server.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement.** Create `profile-log/agentmesh_log/control/server.py`:

```python
"""The supervisor: one owner-only control socket, one thread and one request per connection."""
import threading
from ..broker.transport import UnixServer, UnixClient
from .peer import peer_pid, executable


class Supervisor:
    def __init__(self, control, layout, server_factory=UnixServer):
        self.control,self.layout,self.server_factory=control,layout,server_factory
        self.stopped=threading.Event(); self.server=None

    def serve(self):
        self.layout.ensure()
        self.server=self.server_factory(str(self.layout.socket))
        while not self.stopped.is_set():
            try: conn=self.server.accept()
            except ValueError: continue  # foreign peer UID; the transport already closed it
            except OSError:
                if self.stopped.is_set(): break
                raise
            threading.Thread(target=self.connection,args=(conn,),daemon=True).start()

    def connection(self, conn):
        try:
            sock=getattr(conn,'sock',None); pid=peer_pid(sock) if sock is not None else None
            request=conn.receive()
            conn.send(self.control.handle(request,dict(pid=pid,exe=executable(pid))))
        except (OSError,ValueError,EOFError): pass
        finally: conn.close()

    def close(self):
        self.stopped.set()
        if self.server:
            # Closing a listening socket does not wake a thread blocked in accept() on macOS or Linux.
            try: UnixClient(str(self.layout.socket)).close()
            except OSError: pass
            self.server.close()
```

Create `profile-log/agentmesh_log/control/client.py`:

```python
"""A minimal client for the control socket: one request per connection."""
import uuid
from ..broker.transport import UnixClient


def call(layout, op, args, client_factory=UnixClient):
    conn=client_factory(str(layout.socket))
    try:
        conn.send(dict(v=1,id=uuid.uuid4().hex,op=op,args=args))
        return conn.receive()
    finally: conn.close()
```

- [ ] **Step 4: Run the test and confirm it passes.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_server.py`
Expected: 1 passed. If it fails with `PermissionError` on bind, the sandbox is denying sockets. Rerun outside the sandbox. This is not a code bug.

- [ ] **Step 5: Commit.**

```bash
git add profile-log/agentmesh_log/control/server.py profile-log/agentmesh_log/control/client.py profile-log/tests/control/test_server.py
git commit -m "feat(control): supervisor socket loop and client

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: launchd plist for the supervisor

**Files:**
- Create: `profile-log/agentmesh_log/control/plist.py`
- Test: `profile-log/tests/control/test_plist.py`

- [ ] **Step 1: Write the failing test.** Create `profile-log/tests/control/test_plist.py`:

```python
import plistlib
from agentmesh_log.control.layout import Layout
from agentmesh_log.control.plist import LABEL, render


def test_plist_render(tmp_path):
    data=plistlib.loads(render(Layout(tmp_path),python='/venv/bin/python'))
    assert data['Label']==LABEL=='org.agentmesh.control'
    assert data['ProgramArguments']==['/venv/bin/python','-m','agentmesh_log.cli','control','serve']
    assert data['EnvironmentVariables']=={'AGENTMESH_HOME':str(tmp_path)}
    assert data['KeepAlive'] is True and data['RunAtLoad'] is True and data['Umask']==63
    assert data['StandardErrorPath']==str(tmp_path/'logs'/'control.err.log')
```

- [ ] **Step 2: Run the test and confirm it fails.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_plist.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement.** Create `profile-log/agentmesh_log/control/plist.py`:

```python
"""The launchd plist that keeps the supervisor running. The owner installs it; nothing here loads it."""
import plistlib

LABEL='org.agentmesh.control'


def render(layout, python):
    logs=layout.root/'logs'
    return plistlib.dumps(dict(Label=LABEL,ProgramArguments=[python,'-m','agentmesh_log.cli','control','serve'],
        EnvironmentVariables=dict(AGENTMESH_HOME=str(layout.root)),KeepAlive=True,RunAtLoad=True,
        ProcessType='Background',Umask=63,StandardOutPath=str(logs/'control.out.log'),
        StandardErrorPath=str(logs/'control.err.log')))
```

- [ ] **Step 4: Run the test and confirm it passes.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_plist.py`
Expected: 1 passed.

- [ ] **Step 5: Commit.**

```bash
git add profile-log/agentmesh_log/control/plist.py profile-log/tests/control/test_plist.py
git commit -m "feat(control): render the supervisor launchd plist

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: `agentmesh-log control` CLI

**Files:**
- Create: `profile-log/agentmesh_log/control/cli.py`
- Modify: `profile-log/agentmesh_log/cli.py` (in `parser()`, before `from .connector_cli import add_parsers`; in `execute()`, before `if args.command=='admin':`)
- Test: `profile-log/tests/control/test_cli.py`

- [ ] **Step 1: Write the failing tests.** Create `profile-log/tests/control/test_cli.py`:

```python
import os
import plistlib
import sys
import pytest
from agentmesh_log.cli import parser
from agentmesh_log.control.cli import execute
from agentmesh_log.control.layout import Layout


def fake(response, seen=None):
    def call(layout, op, args):
        if seen is not None: seen.append((op,args))
        return response
    return call


def test_calls_socket_with_exact_op(tmp_path):
    seen=[]
    args=parser().parse_args(['control','restart','ingest'])
    result=execute(args,layout=Layout(tmp_path),call=fake(dict(id='x',ok=True,result={'unit':'ingest'}),seen))
    assert result=={'unit':'ingest'} and seen==[('unit.restart',{'unit':'ingest'})]


def test_maintenance_and_error_mapping(tmp_path):
    seen=[]
    args=parser().parse_args(['control','maintenance','on','--reason','upgrade'])
    result=execute(args,layout=Layout(tmp_path),call=fake(dict(id='x',ok=False,error=dict(code='maintenance',message='')),seen))
    assert result=={'error':'maintenance'} and seen==[('maintenance.on',{'reason':'upgrade'})]


def test_list_results_are_wrapped(tmp_path):
    args=parser().parse_args(['control','audit','--limit','3'])
    assert execute(args,layout=Layout(tmp_path),call=fake(dict(id='x',ok=True,result=[{'op':'a'}])))=={'items':[{'op':'a'}]}


@pytest.mark.skipif(os.name=='nt',reason='Unix socket client')
def test_unreachable_supervisor(tmp_path):
    args=parser().parse_args(['control','status'])
    assert execute(args,layout=Layout(tmp_path))=={'error':'supervisor_unavailable'}


def test_serve_refuses_other_platforms(tmp_path, monkeypatch):
    monkeypatch.setattr(sys,'platform','linux')
    assert execute(parser().parse_args(['control','serve']),layout=Layout(tmp_path))=={'error':'unsupported_platform'}


def test_plist_written_once(tmp_path):
    out=tmp_path/'org.agentmesh.control.plist'
    args=parser().parse_args(['control','plist','--out',str(out),'--python','/venv/bin/python'])
    assert execute(args,layout=Layout(tmp_path))=={'ok':True,'path':str(out)}
    assert plistlib.loads(out.read_bytes())['Label']=='org.agentmesh.control'
    assert execute(args,layout=Layout(tmp_path))=={'error':'exists'}
```

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_cli.py`
Expected: FAIL. `parse_args` exits through `SafeParser.error` (`SystemExit: 2`) because `control` is not registered yet.

- [ ] **Step 3: Implement.** Create `profile-log/agentmesh_log/control/cli.py`:

```python
"""`agentmesh-log control ...`: run the supervisor, write its plist, or call it over the socket."""
import sys
from pathlib import Path
from .layout import Layout

CALLS={
    'status':lambda a:('status',{}),'units':lambda a:('units',{}),'quota':lambda a:('quota.status',{}),
    'diagnose':lambda a:('diagnose',{}),'audit':lambda a:('audit.tail',{'limit':a.limit}),
    'start':lambda a:('unit.start',{'unit':a.unit}),'stop':lambda a:('unit.stop',{'unit':a.unit}),
    'restart':lambda a:('unit.restart',{'unit':a.unit}),
    'maintenance':lambda a:('maintenance.on',{'reason':a.reason}) if a.state=='on' else ('maintenance.off',{}),
}


def add_parsers(commands):
    control=commands.add_parser('control'); sub=control.add_subparsers(dest='action',required=True)
    for action in ('serve','status','units','quota','diagnose'): sub.add_parser(action)
    for action in ('start','stop','restart'): sub.add_parser(action).add_argument('unit')
    maintenance=sub.add_parser('maintenance'); maintenance.add_argument('state',choices=['on','off'])
    maintenance.add_argument('--reason',default='maintenance')
    sub.add_parser('audit').add_argument('--limit',type=int,default=20)
    plist=sub.add_parser('plist'); plist.add_argument('--out',required=True); plist.add_argument('--python',default=sys.executable)


def serve(layout):
    if sys.platform!='darwin': return {'error':'unsupported_platform'}
    from .launchd import LaunchdBackend
    from .ops import Control
    from .server import Supervisor
    supervisor=Supervisor(Control(layout,LaunchdBackend()),layout)
    try: supervisor.serve()
    finally: supervisor.close()
    return {'ok':True}


def write_plist(layout, out, python):
    from .plist import render
    path=Path(out)
    if path.exists() or path.is_symlink(): return {'error':'exists'}
    path.write_bytes(render(layout,python))
    return {'ok':True,'path':str(path)}


def execute(args, *, layout=None, call=None):
    layout=layout or Layout()
    if args.action=='serve': return serve(layout)
    if args.action=='plist': return write_plist(layout,args.out,args.python)
    if call is None: from .client import call
    op,params=CALLS[args.action](args)
    try: response=call(layout,op,params)
    except OSError: return {'error':'supervisor_unavailable'}
    if not response.get('ok'): return {'error':response.get('error',{}).get('code','failed')}
    result=response.get('result')
    return result if isinstance(result,dict) else {'items':result}
```

In `profile-log/agentmesh_log/cli.py` `parser()`, directly before `from .connector_cli import add_parsers`, add:

```python
    from .control.cli import add_parsers as add_control_parsers
    add_control_parsers(commands)
```

In `execute()`, directly before `if args.command=='admin':`, add:

```python
    if args.command=='control':
        from .control.cli import execute as execute_control
        return execute_control(args)
```

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_cli.py profile-log/tests/broker/test_cli.py`
Expected: all pass.

- [ ] **Step 5: Commit.**

```bash
git add profile-log/agentmesh_log/control/cli.py profile-log/agentmesh_log/cli.py profile-log/tests/control/test_cli.py
git commit -m "feat(control): agentmesh-log control CLI

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: Full suites, spec amendments, PR

**Files:**
- Modify: `docs/superpowers/specs/2026-10-23-local-control-plane-design.md`

- [ ] **Step 1: Run the full suites.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests`
Expected: all pass. Gateway cross-implementation tests may skip without `npm ci`, as on main.

Run: `$PY -m pytest -q -p no:cacheprovider memory-sync/test_job_notify.py insight`
Expected: all pass.

- [ ] **Step 2: Amend the spec.** Append this section to the end of the spec:

```markdown
## Amendments from milestone 1 planning (2026-10-23)

- The #92 heartbeats are device telemetry, not service liveness. The M1 watchdog is observe-only and uses `launchctl print` plus `ps` runtime: jobs past `max_runtime_s` are `stuck`, and KeepAlive services not running are `down`. Service liveness pings move to M2.
- `org.agentmesh.sync` was retired in Phase 7a, so the registry has 8 units and `sync.retry` is dropped.
- In M1, launchd still starts jobs, so maintenance and quota holds are enforced in `memory-sync/job_notify.py` (ingest, summary, retention). They apply only when `AGENTMESH_HOME/control` exists.
- The M1 quota cap counts `llm_api_calls` from `status/last-summary.json`. A cost cap and `quota.set` come later.
- In M1, rate-limit counters live in memory. `state.db` arrives with native units in M2.
- The M1 CLI is `agentmesh-log control …`. An audit write failure returns `failed` / `audit_unavailable`.
```

- [ ] **Step 3: Commit, push, and open the PR.**

```bash
git add docs/superpowers/specs/2026-10-23-local-control-plane-design.md
git commit -m "docs(spec): control plane amendments from milestone 1 planning

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -u origin HEAD
gh pr create --title "Control plane milestone 1: observe and control (tiers 0-1)" --body "Implements milestone 1 of docs/superpowers/specs/2026-10-23-local-control-plane-design.md per docs/superpowers/plans/2026-10-23-control-plane-m1.md. Mutation gate results (Task 11 Step 5) are below. Deploy is owner-run (Task 16).

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

---

### Task 16: Owner-run Mac deploy and host checklist (after merge)

Production deploys are owner-run. The deploying agent prepares the commands. It does not run them.

- [ ] **Step 1: Back up and swap code.**
  - Swap the profile-log app to the merge commit with the existing app swap procedure (`swap_profile_log_app.sh`, which keeps `app/profile-log.old-<rev>`).
  - Overlay `memory-sync/job_notify.py` onto `~/.agentmesh/memory-sync/` with the existing overlay procedure. It refuses on drift.
- [ ] **Step 2: Create the control directory and install the plist.**

```bash
~/.agentmesh/venv/bin/python -m agentmesh_log.cli control plist --out ~/Library/LaunchAgents/org.agentmesh.control.plist --python ~/.agentmesh/venv/bin/python
```

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/org.agentmesh.control.plist
```

- [ ] **Step 3: Verify.**
  - `control status` lists 8 units. The broker is `running`, and the jobs are `idle` or `running`.
  - `control audit --limit 5` is empty.
  - `control restart catch-up` succeeds and writes `requested` and `ok` audit rows.
  - A fourth restart within an hour returns `rate_limited`.
- [ ] **Step 4: Maintenance drill.**
  - Run `control maintenance on --reason drill`, then wait for one ingest tick (60 s).
  - Confirm `status/launchd-ingest.log` shows `"held": "maintenance"`.
  - Run `control maintenance off`. The next tick runs.
- [ ] **Step 5: Quota check.** `control quota` shows today's calls. Leave `quota-policy.json` absent (no cap) unless the owner chooses a number.
- [ ] **Step 6: Rollback, if needed.**
  - Run `launchctl bootout gui/$(id -u)/org.agentmesh.control` and delete the plist.
  - Delete `~/.agentmesh/control`. `job_notify` then behaves exactly as before.
  - Swap the app back.

## Self-review notes

- **Spec coverage (M1):**
  - supervisor: Tasks 12 and 14
  - `LaunchdBackend`: Task 4
  - API: Tasks 11 and 12
  - CLI: Task 14
  - observe-only watchdog: Task 9
  - maintenance: Tasks 5, 6 and 11
  - quota guard: Tasks 7 and 8
  - audit: Tasks 1 and 11
  - tier 0–1 op table: Task 11, with `sync.retry` and `unit.reset` deferred (see corrections)
  - Tiers 2–3, native units, `state.db`, approvals and keys are later milestones by design.
- **Placeholders:** none. Deploy steps name the owner's existing procedures, which live outside the repository.
- **Names used across tasks:**
  - `Layout`, `.audit`, `.maintenance`, `.quota`, `.quota_policy`, `.status`, `.socket`, `.ensure()`
  - `read_json`, `write_json`, `get`, `UNITS`, `Unit.wrapped`, `Unit.max_runtime_s`
  - `LaunchdBackend.status` (keys: `loaded`, `running`, `pid`, `last_exit`, `runtime_s`), `start`, `restart`, `stop`
  - `maintenance.status`, `turn_on`, `turn_off`, `quota.status`, `health.observe`, `assess`
  - `peer_pid`, `executable`, `Control.handle`, `Supervisor`, `call`, `render`, `LABEL`, `add_parsers`, `execute`
