# Control plane milestone 2 (native units) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the supervisor run AgentMesh's scheduled jobs itself, exactly as their launchd plists did, so that it can enforce a watchdog with a restart-storm guard, survive its own restarts, and be reverted to launchd in one command. Then move retention, catch-up, ingest and summary over, one at a time.

**Architecture:**
- `control/state.db` (SQLite) records each unit's backend and spec, every native run, every watchdog kill, and failed units.
- `NativeBackend` spawns jobs in their own process group with the plist's argv, environment, cwd, logs and umask.
- The supervisor ticks it once a second to reap finished runs, enforce the watchdog and start due jobs.
- A `Router` sends each unit to launchd or to the native backend, according to `state.db`.
- Units move with owner-run `control adopt` and `control release`, which need typed console presence. `control panic-revert` returns everything to launchd without the supervisor.

**Tech Stack:** Python 3.11 standard library (`sqlite3`, `subprocess`, `signal`, `plistlib`), pytest 8.4.2, launchctl and ps on macOS. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-23-local-control-plane-design.md` (milestone 2 and the M1 amendments). **Previous plan:** `docs/superpowers/plans/2026-10-23-control-plane-m1.md` (merged as #107 and live on the Mac).

---

## Design decisions made for M2 (read first)

1. **Backend moves are local CLI commands, not socket ops.**
   - The spec lists `unit.backend` as tier 2, but Touch ID only arrives in M3.
   - So M2 adds `agentmesh-log control adopt <unit>` and `release <unit>`. Both run in the owner's terminal and require the same typed presence `apply` uses: `ConsolePresence.confirm('migrate', …)`, which refuses non-TTY shells such as an agent's.
   - The supervisor never changes a backend itself. It reads `state.db`, which the CLI writes.
2. **Only scheduled jobs go native in M2.** These are the 4 units in migration steps 1–4: retention, catch-up, ingest and summary (`Unit.native_ok`). KeepAlive services move in M5.
3. **A native run reproduces the plist.**
   - argv, cwd, `StandardOutPath`/`StandardErrorPath` (append), `Umask`, and `StartInterval` or `StartCalendarInterval` (`Minute`/`Hour`/`Weekday` only), plus `RunAtLoad`.
   - The environment is launchd's base keys taken from the supervisor (`HOME`, `USER`, `LOGNAME`, `SHELL`, `TMPDIR`), then `PATH=/usr/bin:/bin:/usr/sbin:/sbin`, then the plist's `EnvironmentVariables`. The supervisor's own `AGENTMESH_HOME` never leaks into children.
   - `ProcessType=Background` is not reproduced.
4. **Missed slots after sleep cause one run, never a burst.**
   - An interval job is due when `now - last_start >= interval`.
   - A calendar job is due when its most recent slot is later than the last start.
   - Before a unit's first native run, the reference time is "now" when `RunAtLoad` is true, and otherwise the adoption time. So retention waits for Sunday 04:00.
5. **Enforcing watchdog and storm guard (native units only).**
   - A run longer than `max_runtime_s` gets SIGTERM to its process group, then SIGKILL 10 s later.
   - Each such kill is recorded. More than 3 kills within 1 h puts the unit in `failed`: it is not started again, by the schedule or by `unit.start`/`unit.restart`, until `control reset <unit>`.
   - One Discord alert is sent through the same webhook file and rules as `job_notify.py`.
   - launchd units stay observe-only, as in M1.
6. **Orphans.** At start the supervisor closes runs a previous supervisor left open. It sends SIGTERM to the process group only if that pid still has the same `ps -o lstart` recorded at spawn, so a reused pid is never signalled. The job wrappers' own `run_lock` already prevents overlapping job bodies.
7. **`panic-revert`** needs no presence because it only returns units to their original plists. Its steps:
   1. Boot out `org.agentmesh.control`.
   2. Send SIGTERM to still-identical native runs.
   3. Mark every native unit as `launchd`.
   4. Bootstrap its plist if it is not loaded.
   5. Audit, best effort.

   To re-enable the supervisor later, bootstrap its plist again.
8. **Restart rate limits stay in memory (M1).** `state.db` holds what must survive a supervisor crash loop: kills, failed units and runs.

## Conventions

Same as M1:
- `$PY` is `~/.cache/agentmesh-logvenv/bin/python`.
- Run from the repository root with `-q -p no:cacheprovider`.
- Use terse code and short docstrings that state the safety property.
- Every `parametrize` needs explicit `ids`, and every collected node ID must be **80 characters or fewer** (`tests/test_collection.py`). Keep test names short.
- POSIX-only tests use `pytestmark = pytest.mark.skipif(os.name=='nt', reason=...)`.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File structure

| File | Responsibility |
|---|---|
| Modify `profile-log/agentmesh_log/control/units.py` | Add `Unit.native_ok`. |
| Modify `.../control/layout.py` | Add `state` (`control/state.db`) and `webhook` paths. |
| Create `.../control/store.py` | `Store` for `state.db`. |
| Create `.../control/schedule.py` | Plist → spec, child environment, and when a job is due (pure functions). |
| Create `.../control/notify.py` | Discord alert with `job_notify.py`'s webhook rules. |
| Create `.../control/native.py` | `NativeBackend`: spawn, tick, watchdog, storm guard, reconcile, shutdown. |
| Create `.../control/router.py` | `Router`: per-unit backend. |
| Modify `.../control/health.py` | Add `failed` health and a `backend` column. |
| Modify `.../control/ops.py` | Add `unit.reset`, block failed units, report `native_ok`. |
| Modify `.../control/server.py` | Ticker thread, close hooks, idempotent close. |
| Modify `.../control/launchd.py` | Add `bootout` and `bootstrap`. |
| Create `.../control/migrate.py` | `adopt`, `release`, `panic_revert`. |
| Modify `.../control/cli.py`, `profile-log/agentmesh_log/cli.py` | Add `adopt`/`release`/`reset`/`panic-revert`, serve wiring, pass the console. |
| Modify `profile-log/tests/control/support.py` | `FakeBackend.failed` and `reset`; `FakeLaunchd`, `Presence`, `write_plists`. |
| Create or modify `profile-log/tests/control/test_*.py` | Tests. |
| Modify the spec | M2 amendments. |

---

### Task 1: `native_ok` and new layout paths

**Files:**
- Modify: `profile-log/agentmesh_log/control/units.py`, `profile-log/agentmesh_log/control/layout.py`
- Test: `profile-log/tests/control/test_units.py`, `profile-log/tests/control/test_layout.py`

- [ ] **Step 1: Write the failing tests.** Append to `profile-log/tests/control/test_units.py`:

```python
def test_native_capable_units():
    assert {u.name for u in UNITS if u.native_ok}=={'ingest','summary','retention','catch-up'}
```

In `profile-log/tests/control/test_layout.py`, add these two lines at the end of `test_layout_defaults_to_agentmesh_home`:

```python
    assert layout.state==tmp_path/'control'/'state.db'
    assert layout.webhook.name=='discord-webhook' and layout.webhook.parent.name=='agentmesh'
```

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_units.py profile-log/tests/control/test_layout.py`
Expected: FAIL with `AttributeError` (`native_ok`, `state`).

- [ ] **Step 3: Implement.** In `units.py`, add this last field to `Unit`:

```python
    native_ok: bool = False           # may move to the supervisor's native backend (M2: scheduled jobs only)
```

Then change the four job entries to:

```python
    Unit('ingest','org.agentmesh.ingest','job',True,3600,True),
    Unit('summary','org.agentmesh.summary','job',True,3600,True),
    Unit('retention','org.agentmesh.retention','job',True,3600,True),
    Unit('catch-up','org.agentmesh.profile-log.catch-up','job',False,1800,True),
```

In `layout.py` `Layout.__init__`, after the `self.quota=...` line, add:

```python
        self.state=self.control/'state.db'
        self.webhook=Path.home()/'.config'/'agentmesh'/'discord-webhook'
```

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control`
Expected: all pass.

- [ ] **Step 5: Commit.**

```bash
git add profile-log/agentmesh_log/control/units.py profile-log/agentmesh_log/control/layout.py profile-log/tests/control/test_units.py profile-log/tests/control/test_layout.py
git commit -m "feat(control): mark native-capable units; state.db and webhook paths

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `state.db` store

**Files:**
- Create: `profile-log/agentmesh_log/control/store.py`
- Test: `profile-log/tests/control/test_store.py`

- [ ] **Step 1: Write the failing tests.** Create `profile-log/tests/control/test_store.py`:

```python
import os
import sqlite3
import stat
import pytest
from agentmesh_log.control.store import Store


def test_backend_switch(tmp_path):
    store=Store(tmp_path/'state.db')
    assert store.backend('ingest')=='launchd' and store.spec('ingest') is None and store.native_units()=={}
    store.set_backend('ingest','native',{'interval':60},1000)
    assert store.backend('ingest')=='native' and store.spec('ingest')=={'interval':60}
    assert store.changed_at('ingest')==1000 and store.native_units()=={'ingest':{'interval':60}}
    store.set_backend('ingest','launchd',{'interval':60},2000)
    assert store.native_units()=={} and store.spec('ingest')=={'interval':60} and store.changed_at('ingest')==2000
    with pytest.raises(sqlite3.IntegrityError): store.set_backend('ingest','cron',None,3000)


def test_owner_only_and_reopen(tmp_path):
    Store(tmp_path/'state.db').set_backend('summary','native',{},1)
    assert Store(tmp_path/'state.db').backend('summary')=='native'
    if os.name!='nt': assert stat.S_IMODE((tmp_path/'state.db').stat().st_mode)==0o600


@pytest.mark.skipif(os.name=='nt',reason='POSIX modes')
def test_refuses_loose_mode_and_symlink(tmp_path):
    (tmp_path/'state.db').write_bytes(b''); (tmp_path/'state.db').chmod(0o644)
    with pytest.raises(ValueError): Store(tmp_path/'state.db')
    (tmp_path/'real').write_bytes(b''); (tmp_path/'link').symlink_to(tmp_path/'real')
    with pytest.raises(Exception): Store(tmp_path/'link')


def test_runs(tmp_path):
    store=Store(tmp_path/'state.db')
    assert store.last_start('ingest') is None and store.last_exit('ingest') is None
    first=store.start_run('ingest',41,'Sat Oct 24 10:00:00 2026',1000)
    second=store.start_run('summary',42,None,2000)
    assert [r['id'] for r in store.open_runs()]==[first,second] and [r['unit'] for r in store.open_runs('ingest')]==['ingest']
    store.end_run(first,3,'failed',1500); store.end_run(first,0,'ok',9999)
    assert store.last_exit('ingest')==3 and store.last_start('ingest')==1000
    assert [r['id'] for r in store.open_runs()]==[second]


def test_kills_and_failed(tmp_path):
    store=Store(tmp_path/'state.db')
    for at in (100,200,300): store.add_kill('ingest',at)
    assert store.kills_since('ingest',150)==2 and store.kills_since('summary',0)==0
    assert store.failed('ingest') is None and store.clear_failed('ingest') is False
    store.set_failed('ingest','restart_storm',400); store.set_failed('ingest','again',500)
    assert store.failed('ingest')=={'since':400,'reason':'restart_storm'}
    assert store.clear_failed('ingest') is True and store.failed('ingest') is None
```

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_store.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement.** Create `profile-log/agentmesh_log/control/store.py`:

```python
"""control/state.db: unit backends and specs, native runs, watchdog kills and failed units (owner-only SQLite).

The supervisor and the owner's CLI (adopt, release, panic-revert) share it; every write is one autocommit statement.
"""
import json
import os
import sqlite3
import stat
import threading
from pathlib import Path
from ..storage.filesystem import _check

SCHEMA='''
CREATE TABLE IF NOT EXISTS units(name TEXT PRIMARY KEY, backend TEXT NOT NULL CHECK(backend IN ('launchd','native')),
    spec TEXT, changed_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, unit TEXT NOT NULL, pid INTEGER NOT NULL, pid_started TEXT,
    started_at INTEGER NOT NULL, ended_at INTEGER, exit INTEGER, outcome TEXT);
CREATE INDEX IF NOT EXISTS runs_unit ON runs(unit, started_at);
CREATE TABLE IF NOT EXISTS kills(unit TEXT NOT NULL, at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS failed(unit TEXT PRIMARY KEY, since INTEGER NOT NULL, reason TEXT NOT NULL);
'''


class Store:
    def __init__(self, path):
        path=Path(path); _check(path)
        try: os.close(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600))
        except FileExistsError: pass
        info=path.lstat()
        if not stat.S_ISREG(info.st_mode) or (os.name!='nt' and (info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600)):
            raise ValueError('unsafe state db')
        self.lock=threading.Lock()
        self.db=sqlite3.connect(str(path),check_same_thread=False,isolation_level=None,timeout=10)
        self.db.executescript(SCHEMA)

    def _one(self, sql, args=()):
        with self.lock: return self.db.execute(sql,args).fetchone()

    def _all(self, sql, args=()):
        with self.lock: return self.db.execute(sql,args).fetchall()

    def _run(self, sql, args=()):
        with self.lock: return self.db.execute(sql,args)

    def backend(self, name):
        row=self._one('SELECT backend FROM units WHERE name=?',(name,))
        return row[0] if row else 'launchd'

    def spec(self, name):
        row=self._one('SELECT spec FROM units WHERE name=?',(name,))
        return json.loads(row[0]) if row and row[0] else None

    def changed_at(self, name):
        row=self._one('SELECT changed_at FROM units WHERE name=?',(name,))
        return row[0] if row else None

    def set_backend(self, name, backend, spec, at):
        self._run('INSERT INTO units(name,backend,spec,changed_at) VALUES(?,?,?,?) ON CONFLICT(name) DO UPDATE SET '
            'backend=excluded.backend,spec=excluded.spec,changed_at=excluded.changed_at',
            (name,backend,None if spec is None else json.dumps(spec,sort_keys=True),at))

    def native_units(self):
        return {name:json.loads(spec) for name,spec in self._all("SELECT name,spec FROM units WHERE backend='native'")}

    def start_run(self, unit, pid, pid_started, at):
        return self._run('INSERT INTO runs(unit,pid,pid_started,started_at) VALUES(?,?,?,?)',(unit,pid,pid_started,at)).lastrowid

    def end_run(self, run_id, exit, outcome, at):
        self._run('UPDATE runs SET ended_at=?,exit=?,outcome=? WHERE id=? AND ended_at IS NULL',(at,exit,outcome,run_id))

    def open_runs(self, unit=None):
        sql='SELECT id,unit,pid,pid_started,started_at FROM runs WHERE ended_at IS NULL'
        rows=self._all(sql+' AND unit=? ORDER BY id',(unit,)) if unit else self._all(sql+' ORDER BY id')
        return [dict(id=r[0],unit=r[1],pid=r[2],pid_started=r[3],started_at=r[4]) for r in rows]

    def last_start(self, unit):
        return self._one('SELECT MAX(started_at) FROM runs WHERE unit=?',(unit,))[0]

    def last_exit(self, unit):
        row=self._one('SELECT exit FROM runs WHERE unit=? AND ended_at IS NOT NULL ORDER BY id DESC LIMIT 1',(unit,))
        return row[0] if row else None

    def add_kill(self, unit, at): self._run('INSERT INTO kills(unit,at) VALUES(?,?)',(unit,at))

    def kills_since(self, unit, since):
        return self._one('SELECT COUNT(*) FROM kills WHERE unit=? AND at>=?',(unit,since))[0]

    def set_failed(self, unit, reason, at):
        self._run('INSERT OR IGNORE INTO failed(unit,since,reason) VALUES(?,?,?)',(unit,at,reason))

    def failed(self, unit):
        row=self._one('SELECT since,reason FROM failed WHERE unit=?',(unit,))
        return dict(since=row[0],reason=row[1]) if row else None

    def clear_failed(self, unit):
        return self._run('DELETE FROM failed WHERE unit=?',(unit,)).rowcount>0

    def close(self):
        with self.lock: self.db.close()
```

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_store.py`
Expected: 5 passed (the symlink test is skipped on Windows).

- [ ] **Step 5: Commit.**

```bash
git add profile-log/agentmesh_log/control/store.py profile-log/tests/control/test_store.py
git commit -m "feat(control): state.db store for backends, runs, kills and failed units

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Plist specs, child environment, due times

**Files:**
- Create: `profile-log/agentmesh_log/control/schedule.py`
- Test: `profile-log/tests/control/test_schedule.py`

- [ ] **Step 1: Write the failing tests.** Create `profile-log/tests/control/test_schedule.py`:

```python
import plistlib
from datetime import datetime
import pytest
from agentmesh_log.control.schedule import DEFAULT_PATH, child_env, due, read_spec, spec_from_plist
from agentmesh_log.control.units import get

INGEST=dict(Label='org.agentmesh.ingest',ProgramArguments=['/opt/am/python','/opt/am/job_notify.py','--name','ingest'],
    EnvironmentVariables={'PATH':'/opt/am/bin:/usr/bin:/bin'},WorkingDirectory='/opt/am/jobs',
    StandardOutPath='/opt/am/status/launchd-ingest.log',StandardErrorPath='/opt/am/status/launchd-ingest.log',
    StartInterval=60,RunAtLoad=True,Umask=63,ProcessType='Background')
RETENTION=dict(Label='org.agentmesh.retention',ProgramArguments=['/opt/am/python','/opt/am/job_notify.py'],
    StandardOutPath='/opt/am/r.log',StandardErrorPath='/opt/am/r.log',StartCalendarInterval={'Hour':4,'Minute':0,'Weekday':0},
    RunAtLoad=False,Umask=63)


def ts(*parts): return datetime(*parts).timestamp()


def test_spec_from_plist():
    assert spec_from_plist(INGEST)==dict(argv=INGEST['ProgramArguments'],env={'PATH':'/opt/am/bin:/usr/bin:/bin'},
        cwd='/opt/am/jobs',stdout='/opt/am/status/launchd-ingest.log',stderr='/opt/am/status/launchd-ingest.log',
        umask=63,interval=60,calendar=None,run_at_load=True)
    spec=spec_from_plist(RETENTION)
    assert spec['interval'] is None and spec['calendar']=={'Hour':4,'Minute':0,'Weekday':0} and spec['run_at_load'] is False
    assert spec['cwd'] is None and spec['env']=={}


BAD=[('no-argv',dict(INGEST,ProgramArguments=[])),('relative',dict(INGEST,ProgramArguments=['python'])),
    ('both',dict(INGEST,StartCalendarInterval={'Hour':1})),('neither',{k:v for k,v in INGEST.items() if k!='StartInterval'}),
    ('zero',dict(INGEST,StartInterval=0)),('day-key',dict(RETENTION,StartCalendarInterval={'Day':1})),
    ('hour-24',dict(RETENTION,StartCalendarInterval={'Hour':24})),('keepalive',dict(INGEST,KeepAlive=True)),
    ('no-stdout',{k:v for k,v in INGEST.items() if k!='StandardOutPath'}),('env',dict(INGEST,EnvironmentVariables={'A':1})),
    ('cwd',dict(INGEST,WorkingDirectory='jobs'))]


@pytest.mark.parametrize('data',[d for _,d in BAD],ids=[i for i,_ in BAD])
def test_unsupported(data):
    with pytest.raises(ValueError): spec_from_plist(data)


def test_read_spec(tmp_path):
    with open(tmp_path/'org.agentmesh.ingest.plist','wb') as f: plistlib.dump(INGEST,f)
    assert read_spec(get('ingest'),tmp_path)['interval']==60


def test_child_env():
    parent={'HOME':'/h','USER':'u','TMPDIR':'/t/','AGENTMESH_HOME':'/x','PATH':'/evil','SECRET':'s'}
    assert child_env(dict(env={'PATH':'/p'}),parent)=={'HOME':'/h','USER':'u','TMPDIR':'/t/','PATH':'/p'}
    assert child_env(dict(env={}),parent)['PATH']==DEFAULT_PATH


def test_due_interval():
    spec=dict(interval=60,calendar=None)
    assert due(spec,None,100) and not due(spec,100,159) and due(spec,100,160) and due(spec,100,100000)


def test_due_calendar():
    spec=dict(interval=None,calendar={'Hour':4,'Minute':0,'Weekday':0})
    saturday=ts(2026,10,24,12,0)
    assert not due(spec,saturday,ts(2026,10,25,3,59)) and due(spec,saturday,ts(2026,10,25,4,0))
    assert not due(spec,ts(2026,10,25,4,0,30),ts(2026,10,25,5,0))
    assert not due(spec,ts(2026,10,25,4,0),ts(2026,10,25,4,30))
    assert due(spec,saturday,ts(2026,10,26,9,0))
    assert due(dict(spec,calendar={'Hour':4,'Minute':0,'Weekday':7}),saturday,ts(2026,10,25,4,0))
    assert due(spec,None,saturday)
```

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_schedule.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement.** Create `profile-log/agentmesh_log/control/schedule.py`:

```python
"""What a native job runs (read from its launchd plist) and when it is due. No side effects beyond reading the plist.

Missed slots after sleep give exactly one run: an interval job is due once `interval` has passed since its last start;
a calendar job is due when its most recent matching minute is later than its last start.
"""
import plistlib
from datetime import datetime, timedelta
from pathlib import Path

ENV_BASE=('HOME','USER','LOGNAME','SHELL','TMPDIR')
DEFAULT_PATH='/usr/bin:/bin:/usr/sbin:/sbin'
RANGES={'Minute':(0,59),'Hour':(0,23),'Weekday':(0,7)}
LOOKBACK_MINUTES=8*24*60


def plist_path(unit, agents=None):
    return Path(agents or Path.home()/'Library'/'LaunchAgents')/(unit.label+'.plist')


def spec_from_plist(data):
    """The launchd keys the native backend reproduces; ValueError for anything it cannot reproduce exactly."""
    argv,env=data.get('ProgramArguments'),data.get('EnvironmentVariables',{})
    if type(argv) is not list or not argv or not all(type(a) is str and a for a in argv) or not argv[0].startswith('/'):
        raise ValueError('unsupported_plist')
    if type(env) is not dict or not all(type(k) is str and type(v) is str for k,v in env.items()): raise ValueError('unsupported_plist')
    if 'StandardOutPath' not in data or 'StandardErrorPath' not in data: raise ValueError('unsupported_plist')
    for key in ('WorkingDirectory','StandardOutPath','StandardErrorPath'):
        if key in data and (type(data[key]) is not str or not data[key].startswith('/')): raise ValueError('unsupported_plist')
    if data.get('KeepAlive') not in (None,False): raise ValueError('unsupported_plist')
    umask=data.get('Umask',0o22)
    if type(umask) is not int or not 0<=umask<=0o777: raise ValueError('unsupported_plist')
    interval,calendar=data.get('StartInterval'),data.get('StartCalendarInterval')
    if (interval is None)==(calendar is None): raise ValueError('unsupported_schedule')
    if interval is not None and (type(interval) is not int or interval<=0): raise ValueError('unsupported_schedule')
    if calendar is not None and (type(calendar) is not dict or not calendar or not set(calendar)<=set(RANGES)
            or not all(type(v) is int and RANGES[k][0]<=v<=RANGES[k][1] for k,v in calendar.items())):
        raise ValueError('unsupported_schedule')
    return dict(argv=argv,env=env,cwd=data.get('WorkingDirectory'),stdout=data['StandardOutPath'],
        stderr=data['StandardErrorPath'],umask=umask,interval=interval,calendar=calendar,run_at_load=data.get('RunAtLoad') is True)


def read_spec(unit, agents=None):
    with open(plist_path(unit,agents),'rb') as stream: return spec_from_plist(plistlib.load(stream))


def child_env(spec, parent):
    """launchd's base environment, then the plist's EnvironmentVariables. Nothing else from the supervisor leaks."""
    env={key:parent[key] for key in ENV_BASE if key in parent}
    env['PATH']=DEFAULT_PATH
    env.update(spec['env'])
    return env


def matches(calendar, moment):
    weekday=(moment.weekday()+1)%7  # launchd: 0 and 7 are Sunday
    return (calendar.get('Minute',moment.minute)==moment.minute and calendar.get('Hour',moment.hour)==moment.hour
        and calendar.get('Weekday',weekday)%7==weekday)


def last_fire(calendar, now):
    moment=now.replace(second=0,microsecond=0)
    for _ in range(LOOKBACK_MINUTES):
        if matches(calendar,moment): return moment
        moment-=timedelta(minutes=1)
    return None


def due(spec, reference, now):
    """reference: epoch seconds of the last start, or of adoption when RunAtLoad is false; None means run now."""
    if reference is None: return True
    if spec['interval'] is not None: return now-reference>=spec['interval']
    fire=last_fire(spec['calendar'],datetime.fromtimestamp(now))
    return fire is not None and fire.timestamp()>reference
```

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_schedule.py`
Expected: 16 passed.

- [ ] **Step 5: Commit.**

```bash
git add profile-log/agentmesh_log/control/schedule.py profile-log/tests/control/test_schedule.py
git commit -m "feat(control): plist specs, launchd-like child env and due times

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Discord alerts

**Files:**
- Create: `profile-log/agentmesh_log/control/notify.py`
- Test: `profile-log/tests/control/test_notify.py`

- [ ] **Step 1: Write the failing tests.** Create `profile-log/tests/control/test_notify.py`:

```python
import os
import pytest
from agentmesh_log.control.layout import Layout
from agentmesh_log.control.notify import notifier, read_webhook

URL='https://discord.com/api/webhooks/123/abc-DEF_9'


def hook(tmp_path, text=URL):
    path=tmp_path/'hook'; path.write_text(text+'\n'); path.chmod(0o600); return path


def test_read_webhook_rules(tmp_path):
    assert read_webhook(tmp_path/'missing') is None
    path=hook(tmp_path); assert read_webhook(path)==URL
    if os.name!='nt':
        path.chmod(0o644)
        with pytest.raises(PermissionError): read_webhook(path)
    path=hook(tmp_path,'https://example.com/x')
    with pytest.raises(ValueError): read_webhook(path)


def test_notifier_sends(tmp_path):
    layout=Layout(tmp_path); layout.webhook=hook(tmp_path); sent=[]
    notifier(layout,sender=lambda url,text:sent.append((url,text)))('hello')
    assert sent==[(URL,'hello')]


def test_notifier_swallows_errors(tmp_path, capsys):
    layout=Layout(tmp_path); layout.webhook=hook(tmp_path)
    def down(url, text): raise OSError('down')
    notifier(layout,sender=down)('hello')
    assert '"notify_error": "OSError"' in capsys.readouterr().err
    layout.webhook=tmp_path/'missing'; sent=[]
    notifier(layout,sender=lambda url,text:sent.append(text))('hello')
    assert sent==[]
```

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_notify.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement.** Create `profile-log/agentmesh_log/control/notify.py`:

```python
"""One-line Discord alerts from the supervisor, under the same webhook rules as memory-sync/job_notify.py.

Owner-only webhook file, Discord webhook URLs only, no mentions, verified TLS. Failures never stop the supervisor.
"""
import json
import os
from pathlib import Path
import re
import ssl
import sys
import urllib.request

WEBHOOK=re.compile(r'^https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+$')
SYSTEM_CA=('/etc/ssl/cert.pem','/etc/ssl/certs/ca-certificates.crt','/etc/pki/tls/certs/ca-bundle.crt')


def read_webhook(path):
    path=Path(path)
    if not path.is_file(): return None
    if os.name!='nt' and path.stat().st_mode&0o077: raise PermissionError('webhook file must not be group/world readable')
    url=path.read_text(encoding='utf-8').strip()
    if not WEBHOOK.match(url): raise ValueError('not a Discord webhook URL')
    return url


def tls_context():
    """Verified TLS; python.org builds may lack a CA bundle, so fall back to the OS one (as job_notify.py does)."""
    context=ssl.create_default_context()
    if not context.get_ca_certs() and not os.path.exists(ssl.get_default_verify_paths().cafile or ''):
        for bundle in SYSTEM_CA:
            if os.path.exists(bundle): context.load_verify_locations(bundle); break
    return context


def post(url, content, timeout=15):
    data=json.dumps({'content':content,'allowed_mentions':{'parse':[]}}).encode('utf-8')
    request=urllib.request.Request(url,data=data,method='POST',
        headers={'Content-Type':'application/json','User-Agent':'AgentMesh-control/1'})
    with urllib.request.urlopen(request,timeout=timeout,context=tls_context()) as response: return response.status


def notifier(layout, sender=post):
    def notify(text):
        try:
            url=read_webhook(layout.webhook)
            if url: sender(url,text)
        except Exception as exc:
            print(json.dumps({'notify_error':type(exc).__name__}),file=sys.stderr,flush=True)
    return notify
```

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_notify.py`
Expected: 3 passed.

- [ ] **Step 5: Commit.**

```bash
git add profile-log/agentmesh_log/control/notify.py profile-log/tests/control/test_notify.py
git commit -m "feat(control): Discord alerts with job_notify's webhook rules

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Native backend

These tests start real child processes. They run on macOS and Linux and are skipped on Windows. The clock is fake, but processes and signals are real.

**Files:**
- Create: `profile-log/agentmesh_log/control/native.py`
- Test: `profile-log/tests/control/test_native.py`

- [ ] **Step 1: Write the failing tests.** Create `profile-log/tests/control/test_native.py`:

```python
import os
import subprocess
import sys
import time
import pytest
from agentmesh_log.control.native import NativeBackend, started_token
from agentmesh_log.control.store import Store
from agentmesh_log.control.units import UNITS, Unit, get

pytestmark=pytest.mark.skipif(os.name=='nt',reason='POSIX process groups')
SLOW=Unit('slow','org.agentmesh.test.slow','job',False,1,True)


def make(tmp_path, units=UNITS, **kw):
    store=Store(tmp_path/'state.db'); clock=[1000.0]
    native=NativeBackend(store,units,clock=lambda:clock[0],
        environ={'HOME':str(tmp_path),'AGENTMESH_HOME':'/leak'},**kw)
    return store,native,clock


def spec(tmp_path, code, **kw):
    return dict(dict(argv=[sys.executable,'-c',code],env={},cwd=str(tmp_path),stdout=str(tmp_path/'out.log'),
        stderr=str(tmp_path/'out.log'),umask=0o77,interval=60,calendar=None,run_at_load=True),**kw)


def adopt(store, name, value, at=900000): store.set_backend(name,'native',value,at)


def settle(native, name):
    """Wait for the run (if a tick has not reaped it already), then reap it."""
    run=native.running.get(name)
    if run is not None: run['proc'].wait(10)
    native.tick()


def outcomes(store, unit):
    return [row[0] for row in store._all('SELECT outcome FROM runs WHERE unit=? ORDER BY id',(unit,))]


def test_runs_like_launchd(tmp_path):
    store,native,_=make(tmp_path)
    code='import os,sys; print("AGENTMESH_HOME" in os.environ, os.environ["PATH"], os.getcwd()); open("made","w").close(); sys.exit(3)'
    adopt(store,'ingest',spec(tmp_path,code)); native.tick()
    assert native.status(get('ingest'))['running'] is True
    settle(native,'ingest')
    assert (tmp_path/'out.log').read_text().split()==['False','/usr/bin:/bin:/usr/sbin:/sbin',os.path.realpath(tmp_path)]
    assert oct((tmp_path/'made').stat().st_mode&0o777)=='0o600' and oct((tmp_path/'out.log').stat().st_mode&0o777)=='0o600'
    assert store.last_exit('ingest')==3 and outcomes(store,'ingest')==['failed']
    assert native.status(get('ingest'))==dict(loaded=True,running=False,pid=None,last_exit=3,runtime_s=None,failed=False)


def test_schedule_no_overlap(tmp_path):
    store,native,clock=make(tmp_path)
    adopt(store,'ingest',spec(tmp_path,'pass')); native.tick(); native.tick()
    settle(native,'ingest'); assert len(outcomes(store,'ingest'))==1
    clock[0]=1059.0; native.tick(); assert 'ingest' not in native.running
    clock[0]=1060.0; native.tick(); assert 'ingest' in native.running
    settle(native,'ingest'); clock[0]=100000.0; native.tick(); native.tick()
    settle(native,'ingest'); assert outcomes(store,'ingest')==['ok','ok','ok']


def test_waits_from_adoption(tmp_path):
    store,native,clock=make(tmp_path)
    adopt(store,'ingest',spec(tmp_path,'pass',run_at_load=False),at=1000000); native.tick()
    assert 'ingest' not in native.running
    clock[0]=1060.0; native.tick(); assert 'ingest' in native.running
    settle(native,'ingest')


def test_watchdog_kills_stuck_run(tmp_path):
    store,native,clock=make(tmp_path,units=UNITS+(SLOW,))
    adopt(store,'slow',spec(tmp_path,'import time; time.sleep(30)')); native.tick()
    clock[0]=1001.0; native.tick(); assert native.running['slow']['term_at'] is None
    clock[0]=1002.0; native.tick(); proc=native.running['slow']['proc']
    assert proc.wait(5)==-15
    native.tick(); assert outcomes(store,'slow')==['killed_stuck'] and store.kills_since('slow',0)==1
    assert store.failed('slow') is None


def test_watchdog_escalates(tmp_path):
    store,native,clock=make(tmp_path,units=UNITS+(SLOW,))
    code='import signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); pathlib.Path("ready").touch(); time.sleep(30)'
    adopt(store,'slow',spec(tmp_path,code)); native.tick()
    for _ in range(100):
        if (tmp_path/'ready').exists(): break
        time.sleep(0.05)
    clock[0]=1002.0; native.tick(); proc=native.running['slow']['proc']
    time.sleep(0.3); assert proc.poll() is None
    clock[0]=1012.0; native.tick(); assert proc.wait(5)==-9
    native.tick(); assert outcomes(store,'slow')==['killed_stuck']


@pytest.mark.parametrize('before,failed',[(2,False),(3,True)],ids=['three','four'])
def test_storm_guard(tmp_path, before, failed):
    alerts=[]
    store,native,clock=make(tmp_path,units=UNITS+(SLOW,),notify=alerts.append)
    for _ in range(before): store.add_kill('slow',999000)
    adopt(store,'slow',spec(tmp_path,'import time; time.sleep(30)')); native.tick()
    clock[0]=1002.0; native.tick(); native.running['slow']['proc'].wait(5); native.tick()
    for _ in range(100):
        if alerts or not failed: break
        time.sleep(0.02)
    assert (store.failed('slow') is not None)==failed and bool(alerts)==failed
    clock[0]=5000.0; native.tick()
    assert ('slow' in native.running)!=failed
    if failed:
        assert '`slow`' in alerts[0] and 'control reset slow' in alerts[0]
        with pytest.raises(RuntimeError): native.start(SLOW)
        assert native.reset(SLOW) is True; native.tick(); assert 'slow' in native.running
    native.stop(SLOW); native.running['slow']['proc'].wait(5); native.tick()


def test_stop_and_restart(tmp_path):
    store,native,_=make(tmp_path); unit=get('ingest')
    adopt(store,'ingest',spec(tmp_path,'import time; time.sleep(30)')); native.tick()
    first=native.running['ingest']['proc']
    native.restart(unit); first.wait(5); native.tick()
    second=native.running['ingest']['proc']; assert second.pid!=first.pid
    native.stop(unit); second.wait(5); native.tick()
    assert outcomes(store,'ingest')==['stopped','stopped'] and 'ingest' not in native.running


def test_launch_error_recorded(tmp_path):
    store,native,_=make(tmp_path)
    adopt(store,'ingest',dict(spec(tmp_path,'pass'),argv=['/nonexistent/agentmesh-job'])); native.tick(); native.tick()
    assert 'ingest' not in native.running and outcomes(store,'ingest')==['launch_error'] and store.last_exit('ingest')==127


def test_reconcile_same_process_only(tmp_path):
    store,native,_=make(tmp_path)
    same=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True)
    other=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True)
    try:
        time.sleep(0.2)
        store.start_run('ingest',same.pid,started_token(same.pid),1)
        store.start_run('summary',other.pid,'Thu Jan  1 00:00:00 1970',1)
        native.reconcile()
        assert same.wait(5)==-15 and other.poll() is None
        assert store.open_runs()==[] and outcomes(store,'ingest')==['orphaned']==outcomes(store,'summary')
    finally:
        for proc in (same,other):
            if proc.poll() is None: proc.kill(); proc.wait(5)


def test_shutdown_stops_runs(tmp_path):
    store,native,_=make(tmp_path)
    adopt(store,'ingest',spec(tmp_path,'import time; time.sleep(30)')); native.tick()
    proc=native.running['ingest']['proc']
    native.shutdown()
    assert proc.returncode==-15 and native.running=={} and outcomes(store,'ingest')==['stopped']
```

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_native.py`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement.** Create `profile-log/agentmesh_log/control/native.py`:

```python
"""Native backend: the supervisor runs scheduled jobs itself, exactly as their launchd plists did.

Enforcing watchdog: a run past its unit's max_runtime_s gets SIGTERM to its process group, then SIGKILL after
KILL_GRACE_S. More than STORM_KILLS watchdog kills within STORM_WINDOW_S puts the unit in `failed`; a failed unit is
never started again (by schedule, start or restart) until `control reset`.
"""
import os
import signal
import subprocess
import threading
import time
from . import schedule

KILL_GRACE_S=10
STORM_KILLS=3
STORM_WINDOW_S=3600


def started_token(pid, runner=subprocess.run):
    """`ps -o lstart` of pid. With the pid it names one process, so a reused pid is never signalled."""
    try: process=runner(['/bin/ps','-o','lstart=','-p',str(pid)],capture_output=True,text=True,timeout=5)
    except (OSError,subprocess.SubprocessError): return None
    if process.returncode!=0: return None
    return process.stdout.strip() or None


def open_log(path):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND|getattr(os,'O_NOFOLLOW',0),0o600)
    return os.fdopen(fd,'ab')


def signal_group(pid, sig):
    try: os.killpg(pid,sig)
    except (ProcessLookupError,PermissionError): pass


class NativeBackend:
    def __init__(self, store, units, *, clock=time.time, notify=None, environ=None, token=started_token):
        self.store,self.units,self.clock,self.token=store,{u.name:u for u in units},clock,token
        self.notify=notify or (lambda text:None)
        self.environ=dict(os.environ) if environ is None else environ
        self.lock=threading.RLock(); self.running={}

    def now_ms(self): return int(self.clock()*1000)

    def status(self, unit):
        with self.lock: run=self.running.get(unit.name)
        last,failed=self.store.last_exit(unit.name),self.store.failed(unit.name) is not None
        if run is None: return dict(loaded=True,running=False,pid=None,last_exit=last,runtime_s=None,failed=failed)
        return dict(loaded=True,running=True,pid=run['proc'].pid,last_exit=last,runtime_s=int(self.clock()-run['started']),failed=failed)

    def start(self, unit):
        with self.lock:
            if self.store.failed(unit.name): raise RuntimeError('unit_failed')
            if unit.name not in self.running: self.spawn(unit,self.store.spec(unit.name))
        return True

    def restart(self, unit):
        with self.lock:
            run=self.running.get(unit.name)
            if run is None: return self.start(unit)
            run['restart']=True; self.terminate(run,'stop')
        return True

    def stop(self, unit):
        with self.lock:
            run=self.running.get(unit.name)
            if run is not None: self.terminate(run,'stop')
        return True

    def reset(self, unit): return self.store.clear_failed(unit.name)

    def spawn(self, unit, spec):
        at=self.now_ms(); out=err=None
        try:
            out=open_log(spec['stdout']); err=out if spec['stderr']==spec['stdout'] else open_log(spec['stderr'])
            proc=subprocess.Popen(spec['argv'],cwd=spec['cwd'] or self.environ.get('HOME') or '/',
                env=schedule.child_env(spec,self.environ),stdin=subprocess.DEVNULL,stdout=out,stderr=err,
                start_new_session=True,umask=spec['umask'],close_fds=True)
        except OSError:
            self.store.end_run(self.store.start_run(unit.name,-1,None,at),127,'launch_error',at)
            return False
        finally:
            for stream in {id(s):s for s in (out,err) if s is not None}.values(): stream.close()
        run_id=self.store.start_run(unit.name,proc.pid,self.token(proc.pid),at)
        self.running[unit.name]=dict(proc=proc,run_id=run_id,started=self.clock(),term_at=None,why=None,restart=False)
        return True

    def terminate(self, run, why):
        if run['term_at'] is None:
            run['term_at'],run['why']=self.clock(),why
            signal_group(run['proc'].pid,signal.SIGTERM)

    def tick(self):
        """One supervisor second: reap finished runs, enforce the watchdog, then start due jobs."""
        alerts=[]
        with self.lock:
            now=self.clock()
            for name,run in list(self.running.items()):
                code=run['proc'].poll()
                if code is not None:
                    alerts+=self.finish(name,run,code); continue
                limit=self.units[name].max_runtime_s
                if run['term_at'] is None and limit and now-run['started']>limit: self.terminate(run,'watchdog')
                elif run['term_at'] is not None and now-run['term_at']>=KILL_GRACE_S: signal_group(run['proc'].pid,signal.SIGKILL)
            for name,spec in self.store.native_units().items():
                unit=self.units.get(name)
                if unit is None or name in self.running or self.store.failed(name): continue
                last=self.store.last_start(name)
                if last is not None: reference=last/1000
                elif spec['run_at_load']: reference=None
                else: reference=(self.store.changed_at(name) or 0)/1000
                if schedule.due(spec,reference,now): self.spawn(unit,spec)
        for text in alerts: threading.Thread(target=self.notify,args=(text,),daemon=True).start()

    def finish(self, name, run, code):
        del self.running[name]
        at=self.now_ms()
        outcome={'watchdog':'killed_stuck','stop':'stopped'}.get(run['why']) or ('ok' if code==0 else 'failed')
        self.store.end_run(run['run_id'],code,outcome,at)
        alerts=[]
        if run['why']=='watchdog':
            self.store.add_kill(name,at)
            if self.store.kills_since(name,at-STORM_WINDOW_S*1000)>STORM_KILLS:
                self.store.set_failed(name,'restart_storm',at)
                alerts.append('🔴 AgentMesh control: `%s` stopped after more than %d watchdog kills in %d min. '
                    'Fix it, then run `agentmesh-log control reset %s`.'%(name,STORM_KILLS,STORM_WINDOW_S//60,name))
        if run['restart'] and not self.store.failed(name) and name in self.store.native_units():
            self.spawn(self.units[name],self.store.spec(name))
        return alerts

    def reconcile(self):
        """Runs a previous supervisor left open: SIGTERM the orphan only if it is still that same process."""
        for run in self.store.open_runs():
            if run['pid']>0 and run['pid_started'] is not None and self.token(run['pid'])==run['pid_started']:
                signal_group(run['pid'],signal.SIGTERM)
            self.store.end_run(run['id'],None,'orphaned',self.now_ms())

    def shutdown(self):
        """Supervisor exit: stop every native run, SIGKILL what ignores SIGTERM, and close the records."""
        with self.lock:
            runs=list(self.running.items())
            for _,run in runs: run['restart']=False; self.terminate(run,'stop')
        deadline=time.monotonic()+KILL_GRACE_S
        for _,run in runs:
            try: run['proc'].wait(max(0.0,deadline-time.monotonic()))
            except subprocess.TimeoutExpired:
                signal_group(run['proc'].pid,signal.SIGKILL); run['proc'].wait(5)
        with self.lock:
            for name,run in runs:
                if self.running.get(name) is run: self.finish(name,run,run['proc'].returncode)
```

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_native.py`
Expected: 11 passed on macOS and Linux.

- [ ] **Step 5: Commit.**

```bash
git add profile-log/agentmesh_log/control/native.py profile-log/tests/control/test_native.py
git commit -m "feat(control): native backend with enforcing watchdog and restart-storm guard

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Router, `failed` health, `unit.reset`

**Files:**
- Create: `profile-log/agentmesh_log/control/router.py`
- Modify: `profile-log/agentmesh_log/control/health.py`, `profile-log/agentmesh_log/control/ops.py`, `profile-log/tests/control/support.py`
- Test: `profile-log/tests/control/test_router.py`, `profile-log/tests/control/test_health.py`, `profile-log/tests/control/test_ops.py`

- [ ] **Step 1: Update the fake and write the failing tests.** In `profile-log/tests/control/support.py`, replace `FakeBackend.__init__` and `status`, and add `reset`:

```python
    def __init__(self, statuses=None, fail=False):
        self.statuses,self.fail,self.calls,self.failed=statuses or {},fail,[],set()

    def status(self, unit): return dict(self.statuses.get(unit.name,IDLE),failed=unit.name in self.failed)

    def reset(self, unit):
        cleared=unit.name in self.failed; self.failed.discard(unit.name); return cleared
```

Create `profile-log/tests/control/test_router.py`:

```python
from agentmesh_log.control.router import Router
from agentmesh_log.control.store import Store
from agentmesh_log.control.units import get
from .support import FakeBackend


def test_routes_by_store(tmp_path):
    store=Store(tmp_path/'state.db'); launchd,native=FakeBackend(),FakeBackend()
    router=Router(store,launchd,native); unit=get('ingest')
    router.restart(unit); assert launchd.calls==[('restart','ingest')] and native.calls==[]
    assert router.status(unit)['backend']=='launchd'
    store.set_backend('ingest','native',{},1)
    router.start(unit); router.stop(unit)
    assert native.calls==[('start','ingest'),('stop','ingest')] and router.status(unit)['backend']=='native'
    native.failed.add('ingest')
    assert router.status(unit)['failed'] is True and router.reset(unit) is True and router.reset(unit) is False
```

Append to `profile-log/tests/control/test_health.py`:

```python
def test_failed_wins():
    job=get('ingest')
    assert assess(job,dict(IDLE,failed=True),None)=='failed'
    assert assess(job,dict(IDLE,running=True,pid=1,runtime_s=1,failed=True),None)=='failed'
    assert assess(job,dict(IDLE,loaded=False,failed=True),None)=='missing'
```

Append to `profile-log/tests/control/test_ops.py`:

```python
def test_failed_blocks_until_reset(tmp_path):
    backend=FakeBackend(); backend.failed.add('ingest'); control=Control(Layout(tmp_path),backend)
    for op in ('unit.start','unit.restart'):
        assert control.handle(request(op,{'unit':'ingest'}),CALLER)['error']==dict(code='failed',message='unit_failed')
    assert control.handle(request('unit.reset',{'unit':'ingest'}),CALLER)['result']==dict(unit='ingest',cleared=True)
    assert control.handle(request('unit.start',{'unit':'ingest'}),CALLER)['ok']
    assert control.handle(request('unit.reset',{'unit':'ingest'}),CALLER)['result']['cleared'] is False
    assert [u['native_ok'] for u in control.handle(request('units'),CALLER)['result']][:4]==[True]*4
```

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control`
Expected: `test_router` fails with `ModuleNotFoundError`. `test_failed_wins` and `test_failed_blocks_until_reset` FAIL.

- [ ] **Step 3: Implement.** Create `profile-log/agentmesh_log/control/router.py`:

```python
"""Send each unit to its current backend: launchd unless state.db marks it native."""


class Router:
    def __init__(self, store, launchd, native):
        self.store,self.launchd,self.native=store,launchd,native

    def pick(self, unit): return self.native if self.store.backend(unit.name)=='native' else self.launchd

    def status(self, unit):
        backend=self.store.backend(unit.name)
        status=dict((self.native if backend=='native' else self.launchd).status(unit),backend=backend)
        status.setdefault('failed',False)
        return status

    def start(self, unit): return self.pick(unit).start(unit)
    def restart(self, unit): return self.pick(unit).restart(unit)
    def stop(self, unit): return self.pick(unit).stop(unit)
    def reset(self, unit): return self.native.reset(unit)
```

In `health.py` `assess`, change the docstring to `"""One of: missing, failed, running, stuck, down, failing, idle."""`, and directly after `if not launchd['loaded']: return 'missing'` add:

```python
    if launchd.get('failed'): return 'failed'
```

In `observe`, add `backend=launchd.get('backend','launchd')` to the row dict, after `kind=unit.kind`.

In `ops.py`:
1. Add `'unit.reset':(1,frozenset({'unit'})),` to `OPS` after `'unit.restart'`.
2. Replace `runnable` with:

```python
    def runnable(self, unit):
        if unit.wrapped and maintenance.status(self.layout)['on']: raise OpError('maintenance')
        if self.backend.status(unit).get('failed'): raise OpError('failed','unit_failed')
```

3. Add this method after `op_unit_stop`:

```python
    def op_unit_reset(self, unit):
        unit=self.unit(unit); reset=getattr(self.backend,'reset',None)
        return dict(unit=unit.name,cleared=bool(reset(unit)) if reset else False)
```

4. In `op_units`, add `native_ok=u.native_ok` to each dict.

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control`
Expected: all pass.

- [ ] **Step 5: Commit.**

```bash
git add profile-log/agentmesh_log/control/router.py profile-log/agentmesh_log/control/health.py profile-log/agentmesh_log/control/ops.py profile-log/tests/control/support.py profile-log/tests/control/test_router.py profile-log/tests/control/test_health.py profile-log/tests/control/test_ops.py
git commit -m "feat(control): per-unit backend router, failed health and unit.reset

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Supervisor ticker, close hooks, serve wiring

**Files:**
- Modify: `profile-log/agentmesh_log/control/server.py`, `profile-log/agentmesh_log/control/cli.py`
- Test: `profile-log/tests/control/test_server.py`

- [ ] **Step 1: Write the failing test.** Append to `profile-log/tests/control/test_server.py`:

```python
@pytest.mark.skipif(os.name=='nt',reason='Unix socket')
def test_ticker_and_close_hooks():
    root=tempfile.mkdtemp(prefix='amc',dir='/tmp'); layout=Layout(root); ticks,closed=[],[]
    def ticker():
        ticks.append(1)
        if len(ticks)==1: raise RuntimeError('first tick fails')
    supervisor=Supervisor(Control(layout,FakeBackend()),layout,ticker=ticker,on_close=lambda:closed.append(1),tick_s=0.01)
    thread=threading.Thread(target=supervisor.serve,daemon=True); thread.start()
    try:
        assert call_when_ready(layout,'status',{})['ok']
        for _ in range(200):
            if len(ticks)>=3: break
            time.sleep(0.01)
        assert len(ticks)>=3
    finally:
        supervisor.close(); supervisor.close(); thread.join(5); shutil.rmtree(root)
    assert closed==[1] and not thread.is_alive()
```

- [ ] **Step 2: Run the test and confirm it fails.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_server.py`
Expected: FAIL with `TypeError: ... unexpected keyword argument 'ticker'`.

- [ ] **Step 3: Implement.** Replace `profile-log/agentmesh_log/control/server.py` with:

```python
"""The supervisor: one owner-only control socket, one thread and one request per connection, and a ticker
thread (the native backend's once-a-second tick). close() is idempotent and safe from a SIGTERM handler."""
import json
import sys
import threading
from ..broker.transport import UnixServer, UnixClient
from .peer import peer_pid, executable


class Supervisor:
    def __init__(self, control, layout, server_factory=UnixServer, *, ticker=None, on_close=None, tick_s=1.0):
        self.control,self.layout,self.server_factory=control,layout,server_factory
        self.ticker,self.on_close,self.tick_s=ticker,on_close,tick_s
        self.stopped=threading.Event(); self.server=None; self.tick_thread=None
        self.closed=False; self.close_lock=threading.Lock()

    def serve(self):
        self.layout.ensure()
        self.server=self.server_factory(str(self.layout.socket))
        if self.ticker is not None:
            self.tick_thread=threading.Thread(target=self.tick_loop,daemon=True); self.tick_thread.start()
        while not self.stopped.is_set():
            try: conn=self.server.accept()
            except ValueError: continue  # foreign peer UID; the transport already closed it
            except OSError:
                if self.stopped.is_set(): break
                raise
            threading.Thread(target=self.connection,args=(conn,),daemon=True).start()

    def tick_loop(self):
        while not self.stopped.wait(self.tick_s):
            try: self.ticker()
            except Exception as exc: print(json.dumps({'tick_error':type(exc).__name__}),file=sys.stderr,flush=True)

    def connection(self, conn):
        try:
            sock=getattr(conn,'sock',None); pid=peer_pid(sock) if sock is not None else None
            request=conn.receive()
            conn.send(self.control.handle(request,dict(pid=pid,exe=executable(pid))))
        except (OSError,ValueError,EOFError): pass
        finally: conn.close()

    def close(self):
        with self.close_lock:
            if self.closed: return
            self.closed=True
        self.stopped.set()
        if self.tick_thread is not None and self.tick_thread is not threading.current_thread(): self.tick_thread.join(30)
        if self.on_close is not None: self.on_close()
        if self.server:
            # Closing a listening socket does not wake a thread blocked in accept() on macOS or Linux.
            try: UnixClient(str(self.layout.socket)).close()
            except OSError: pass
            self.server.close()
```

In `profile-log/agentmesh_log/control/cli.py`, replace `serve` with:

```python
def serve(layout):
    if sys.platform!='darwin': return {'error':'unsupported_platform'}
    import signal
    from .launchd import LaunchdBackend
    from .native import NativeBackend
    from .notify import notifier
    from .ops import Control
    from .router import Router
    from .server import Supervisor
    from .store import Store
    from .units import UNITS
    layout.ensure(); store=Store(layout.state)
    native=NativeBackend(store,UNITS,notify=notifier(layout))
    native.reconcile()
    supervisor=Supervisor(Control(layout,Router(store,LaunchdBackend(),native)),layout,ticker=native.tick,on_close=native.shutdown)
    signal.signal(signal.SIGTERM,lambda signum,frame:supervisor.close())
    try: supervisor.serve()
    finally: supervisor.close(); store.close()
    return {'ok':True}
```

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control`
Expected: all pass.

- [ ] **Step 5: Commit.**

```bash
git add profile-log/agentmesh_log/control/server.py profile-log/agentmesh_log/control/cli.py profile-log/tests/control/test_server.py
git commit -m "feat(control): supervisor ticker, close hooks and native wiring

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: `adopt`, `release`, `panic-revert`

**Files:**
- Modify: `profile-log/agentmesh_log/control/launchd.py`, `profile-log/tests/control/support.py`, `profile-log/tests/control/test_launchd.py`
- Create: `profile-log/agentmesh_log/control/migrate.py`
- Test: `profile-log/tests/control/test_migrate.py`

- [ ] **Step 1: Add the shared fakes and write the failing tests.** Append to `profile-log/tests/control/support.py`:

```python
import plistlib
from agentmesh_log.control.units import UNITS

PLISTS={
    'ingest':dict(Label='org.agentmesh.ingest',ProgramArguments=['/opt/am/python','/opt/am/job_notify.py','--name','ingest'],
        StandardOutPath='/opt/am/ingest.log',StandardErrorPath='/opt/am/ingest.log',StartInterval=60,RunAtLoad=True,Umask=63),
    'retention':dict(Label='org.agentmesh.retention',ProgramArguments=['/opt/am/python','/opt/am/job_notify.py'],
        StandardOutPath='/opt/am/r.log',StandardErrorPath='/opt/am/r.log',
        StartCalendarInterval={'Hour':4,'Minute':0,'Weekday':0},RunAtLoad=False,Umask=63),
}


def write_plists(directory):
    for data in PLISTS.values():
        with open(directory/(data['Label']+'.plist'),'wb') as stream: plistlib.dump(data,stream)
    return directory


class FakeLaunchd:
    domain='gui/501'

    def __init__(self, busy=None):
        self.busy,self.loaded,self.calls=dict(busy or {}),{u.name for u in UNITS},[]

    def target(self, unit): return self.domain+'/'+unit.label
    def runner(self, argv): self.calls.append(tuple(argv)); return (0,'')

    def status(self, unit):
        left=self.busy.get(unit.name,0)
        if left: self.busy[unit.name]=left-1
        return dict(IDLE,loaded=unit.name in self.loaded,running=left>0)

    def bootout(self, unit): self.calls.append(('bootout',unit.name)); self.loaded.discard(unit.name); return True

    def bootstrap(self, unit, plist):
        self.calls.append(('bootstrap',unit.name,str(plist))); self.loaded.add(unit.name); return True


class Presence:
    def __init__(self, answer): self.answer,self.seen=answer,[]
    def confirm(self, action, summary): self.seen.append((action,summary)); return self.answer


def ok_call(layout, op, args): return dict(id='x',ok=True,result={})
```

Append to `profile-log/tests/control/test_launchd.py`:

```python
def test_bootout_and_bootstrap_argv():
    calls=[]; backend=LaunchdBackend(lambda argv:(calls.append(argv) or (0,'')),uid=501); unit=get('ingest')
    backend.bootout(unit); backend.bootstrap(unit,'/x/org.agentmesh.ingest.plist')
    assert calls==[['/bin/launchctl','bootout','gui/501/org.agentmesh.ingest'],
                   ['/bin/launchctl','bootstrap','gui/501','/x/org.agentmesh.ingest.plist']]
```

Create `profile-log/tests/control/test_migrate.py`:

```python
import json
import os
import subprocess
import sys
import time
import pytest
from agentmesh_log.control import migrate
from agentmesh_log.control.layout import Layout
from agentmesh_log.control.native import started_token
from agentmesh_log.control.store import Store
from .support import FakeLaunchd, PLISTS, Presence, ok_call, write_plists

NOSLEEP=lambda seconds:None


def setup(tmp_path, **kw):
    layout=Layout(tmp_path).ensure(); agents=tmp_path/'agents'; agents.mkdir(); write_plists(agents)
    return layout,Store(layout.state),FakeLaunchd(**kw),agents


def audit(layout): return [json.loads(line)['outcome'] for line in layout.audit.read_bytes().splitlines()]


def adopt(layout, store, launchd, agents, name='ingest', answer=True, call=ok_call, **kw):
    return migrate.adopt(layout,store,name,console=Presence(answer),call=call,launchd=launchd,agents=agents,sleep=NOSLEEP,**kw)


def test_adopt(tmp_path):
    layout,store,launchd,agents=setup(tmp_path); console=Presence(True)
    result=migrate.adopt(layout,store,'ingest',console=console,call=ok_call,launchd=launchd,agents=agents,sleep=NOSLEEP)
    assert result=={'ok':True,'unit':'ingest','backend':'native'}
    assert launchd.calls==[('bootout','ingest')] and store.backend('ingest')=='native'
    assert store.spec('ingest')['argv']==PLISTS['ingest']['ProgramArguments']
    assert console.seen[0][0]=='migrate' and 'Undo: agentmesh-log control release ingest' in console.seen[0][1]
    assert audit(layout)==['requested','ok']


def down(layout, op, args): raise ConnectionRefusedError()


REFUSALS=[('broker','broker',True,ok_call,'not_native_capable'),('unknown','nope',True,ok_call,'not_native_capable'),
    ('no-plist','summary',True,ok_call,'unsupported_plist'),('down','ingest',True,down,'supervisor_unavailable'),
    ('not-ok','ingest',True,lambda l,o,a:dict(ok=False),'supervisor_unavailable'),('denied','ingest',False,ok_call,'presence_required')]


@pytest.mark.parametrize('name,answer,call,error',[r[1:] for r in REFUSALS],ids=[r[0] for r in REFUSALS])
def test_adopt_refusals(tmp_path, name, answer, call, error):
    layout,store,launchd,agents=setup(tmp_path)
    assert adopt(layout,store,launchd,agents,name=name,answer=answer,call=call)=={'error':error}
    assert launchd.calls==[] and store.native_units()=={}


def test_adopt_twice(tmp_path):
    layout,store,launchd,agents=setup(tmp_path); adopt(layout,store,launchd,agents)
    assert adopt(layout,store,launchd,agents)=={'error':'already_native'}


def test_adopt_waits_for_idle(tmp_path):
    layout,store,launchd,agents=setup(tmp_path,busy={'ingest':2})
    assert adopt(layout,store,launchd,agents)['ok'] is True
    layout,store,launchd,agents=setup(tmp_path/'b',busy={'ingest':1000})
    assert adopt(layout,store,launchd,agents,wait_s=4)=={'error':'busy'}
    assert launchd.calls==[] and store.backend('ingest')=='launchd' and audit(layout)==['requested','busy']


def test_release(tmp_path):
    layout,store,launchd,agents=setup(tmp_path); adopt(layout,store,launchd,agents)
    run=store.start_run('ingest',123,'token',1)
    result=migrate.release(layout,store,'ingest',console=Presence(True),launchd=launchd,agents=agents,
        sleep=lambda s:store.end_run(run,0,'ok',2))
    assert result=={'ok':True,'unit':'ingest','backend':'launchd','waited_s':2}
    assert launchd.calls[-1]==('bootstrap','ingest',str(agents/'org.agentmesh.ingest.plist'))
    assert store.backend('ingest')=='launchd' and store.spec('ingest') is not None


def test_release_refusals(tmp_path):
    layout,store,launchd,agents=setup(tmp_path)
    assert migrate.release(layout,store,'ingest',console=Presence(True),launchd=launchd,agents=agents)=={'error':'not_native'}
    adopt(layout,store,launchd,agents)
    denied=migrate.release(layout,store,'ingest',console=Presence(False),launchd=launchd,agents=agents)
    assert denied=={'error':'presence_required'} and store.backend('ingest')=='native'
    assert launchd.calls==[('bootout','ingest')]


@pytest.mark.skipif(os.name=='nt',reason='POSIX process groups')
def test_panic_revert(tmp_path):
    layout,store,launchd,agents=setup(tmp_path)
    adopt(layout,store,launchd,agents); adopt(layout,store,launchd,agents,name='retention')
    same=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True)
    other=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True)
    try:
        time.sleep(0.2)
        store.start_run('ingest',same.pid,started_token(same.pid),1)
        store.start_run('retention',other.pid,'Thu Jan  1 00:00:00 1970',1)
        result=migrate.panic_revert(layout,store,launchd=launchd,agents=agents,sleep=NOSLEEP)
        assert result=={'ok':True,'reverted':['ingest','retention'],'stopped':['ingest'],'errors':[]}
        assert same.wait(5)==-15 and other.poll() is None
        assert launchd.calls[2]==('/bin/launchctl','bootout','gui/501/org.agentmesh.control')
        assert ('bootstrap','retention',str(agents/'org.agentmesh.retention.plist')) in launchd.calls
        assert store.native_units()=={} and store.open_runs()==[] and audit(layout)[-1]=='ok'
    finally:
        for proc in (same,other):
            if proc.poll() is None: proc.kill(); proc.wait(5)


def test_panic_revert_with_nothing_native(tmp_path):
    layout,store,launchd,agents=setup(tmp_path)
    assert migrate.panic_revert(layout,store,launchd=launchd,agents=agents,sleep=NOSLEEP)=={'ok':True,'reverted':[],'stopped':[],'errors':[]}
    assert launchd.calls==[('/bin/launchctl','bootout','gui/501/org.agentmesh.control')]
```

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_migrate.py profile-log/tests/control/test_launchd.py`
Expected: FAIL with `ImportError` (`migrate`) and `AttributeError` (`bootout`).

- [ ] **Step 3: Implement.** In `launchd.py`, add these after `stop`:

```python
    def bootout(self, unit): return self._ok(['/bin/launchctl','bootout',self.target(unit)])
    def bootstrap(self, unit, plist): return self._ok(['/bin/launchctl','bootstrap',self.domain,str(plist)])
```

Create `profile-log/agentmesh_log/control/migrate.py`:

```python
"""Owner-run moves of a unit between launchd and the supervisor, and the panic revert.

These run in the owner's terminal, never over the socket. adopt and release need typed console presence (the same
`Type migrate:` confirmation `apply` uses), so an agent's non-interactive shell cannot move units. Plists stay on disk.
panic-revert needs no presence: it only returns units to their original launchd plists.
"""
import os
import signal
import sys
import time
from ..broker.audit import append_row
from . import schedule
from .launchd import LaunchdBackend
from .native import signal_group, started_token
from .plist import LABEL
from .units import get

WAIT_S=600
POLL_S=2


def now_ms(): return time.time_ns()//1000000


def audit(layout, op, args, outcome):
    layout.ensure()
    append_row(layout.audit,dict(ts=now_ms(),op=op,args=args,outcome=outcome,caller_pid=os.getpid(),caller_exe=sys.executable))


def native_unit(name):
    try: unit=get(name)
    except KeyError: return None
    return unit if unit.native_ok else None


def adopt(layout, store, name, *, console, call, launchd=None, agents=None, sleep=time.sleep, wait_s=WAIT_S):
    """launchd -> supervisor: wait for the launchd job to be idle, boot it out, record the plist's spec as native."""
    unit=native_unit(name)
    if unit is None: return {'error':'not_native_capable'}
    if store.backend(name)=='native': return {'error':'already_native'}
    try: spec=schedule.read_spec(unit,agents)
    except Exception: return {'error':'unsupported_plist'}
    try: alive=call(layout,'status',{}).get('ok') is True
    except OSError: alive=False
    if not alive: return {'error':'supervisor_unavailable'}
    summary=('Move %s (%s) from launchd to the AgentMesh supervisor.\nIt will run: %s\nUndo: agentmesh-log control release %s'
        %(name,unit.label,' '.join(spec['argv']),name))
    if not console.confirm('migrate',summary):
        audit(layout,'unit.adopt',{'unit':name},'presence_denied'); return {'error':'presence_required'}
    audit(layout,'unit.adopt',{'unit':name},'requested')
    launchd=launchd or LaunchdBackend(); waited=0
    while launchd.status(unit)['running']:
        if waited>=wait_s:
            audit(layout,'unit.adopt',{'unit':name},'busy'); return {'error':'busy'}
        sleep(POLL_S); waited+=POLL_S
    try: launchd.bootout(unit)
    except RuntimeError:
        if launchd.status(unit)['loaded']:
            audit(layout,'unit.adopt',{'unit':name},'launchctl_failed'); return {'error':'launchctl_failed'}
    store.set_backend(name,'native',spec,now_ms())
    audit(layout,'unit.adopt',{'unit':name},'ok')
    return {'ok':True,'unit':name,'backend':'native'}


def release(layout, store, name, *, console, launchd=None, agents=None, sleep=time.sleep, wait_s=WAIT_S):
    """supervisor -> launchd: no new native runs at once; a run in progress may finish (up to wait_s) first."""
    unit=native_unit(name)
    if unit is None or store.backend(name)!='native': return {'error':'not_native'}
    summary='Move %s back from the AgentMesh supervisor to launchd (%s).'%(name,unit.label)
    if not console.confirm('migrate',summary):
        audit(layout,'unit.release',{'unit':name},'presence_denied'); return {'error':'presence_required'}
    audit(layout,'unit.release',{'unit':name},'requested')
    store.set_backend(name,'launchd',store.spec(name),now_ms())
    waited=0
    while store.open_runs(name) and waited<wait_s: sleep(POLL_S); waited+=POLL_S
    launchd=launchd or LaunchdBackend()
    if not launchd.status(unit)['loaded']:
        try: launchd.bootstrap(unit,schedule.plist_path(unit,agents))
        except RuntimeError:
            audit(layout,'unit.release',{'unit':name},'launchctl_failed'); return {'error':'launchctl_failed'}
    audit(layout,'unit.release',{'unit':name},'ok')
    return {'ok':True,'unit':name,'backend':'launchd','waited_s':waited}


def panic_revert(layout, store, *, launchd=None, agents=None, token=started_token, sleep=time.sleep):
    """Emergency return to launchd without the supervisor: stop it, stop its runs, re-bootstrap the original plists."""
    launchd=launchd or LaunchdBackend()
    launchd.runner(['/bin/launchctl','bootout',launchd.domain+'/'+LABEL])
    sleep(1)
    stopped=[]
    for run in store.open_runs():
        if run['pid']>0 and run['pid_started'] is not None and token(run['pid'])==run['pid_started']:
            signal_group(run['pid'],signal.SIGTERM); stopped.append(run['unit'])
        store.end_run(run['id'],None,'panic',now_ms())
    reverted,errors=[],[]
    for name in sorted(store.native_units()):
        store.set_backend(name,'launchd',store.spec(name),now_ms())
        unit=get(name)
        try:
            if not launchd.status(unit)['loaded']: launchd.bootstrap(unit,schedule.plist_path(unit,agents))
            reverted.append(name)
        except RuntimeError: errors.append(name)
    try: audit(layout,'panic_revert',{},'ok' if not errors else 'partial')
    except Exception: pass
    return {'ok':not errors,'reverted':reverted,'stopped':stopped,'errors':errors}
```

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_migrate.py profile-log/tests/control/test_launchd.py`
Expected: all pass. The panic test is skipped on Windows.

- [ ] **Step 5: Commit.**

```bash
git add profile-log/agentmesh_log/control/launchd.py profile-log/agentmesh_log/control/migrate.py profile-log/tests/control/support.py profile-log/tests/control/test_launchd.py profile-log/tests/control/test_migrate.py
git commit -m "feat(control): presence-gated adopt and release, and panic-revert

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: CLI commands

**Files:**
- Modify: `profile-log/agentmesh_log/control/cli.py`, `profile-log/agentmesh_log/cli.py` (the `control` dispatch in `execute()`)
- Test: `profile-log/tests/control/test_cli.py`

- [ ] **Step 1: Write the failing tests.** Append to `profile-log/tests/control/test_cli.py`:

```python
from .support import FakeLaunchd, Presence, ok_call, write_plists


def test_reset_over_socket(tmp_path):
    seen=[]
    result=execute(parser().parse_args(['control','reset','ingest']),layout=Layout(tmp_path),
        call=fake(dict(id='x',ok=True,result={'unit':'ingest','cleared':True}),seen))
    assert result=={'unit':'ingest','cleared':True} and seen==[('unit.reset',{'unit':'ingest'})]


def test_adopt_release_panic_locally(tmp_path):
    agents=tmp_path/'agents'; agents.mkdir(); write_plists(agents); launchd=FakeLaunchd(); layout=Layout(tmp_path)
    run=lambda *argv,answer=True: execute(parser().parse_args(['control',*argv]),layout=layout,call=ok_call,
        console=Presence(answer),launchd=launchd,agents=agents)
    assert run('adopt','ingest')=={'ok':True,'unit':'ingest','backend':'native'}
    assert run('release','ingest',answer=False)=={'error':'presence_required'}
    assert run('panic-revert')=={'ok':True,'reverted':['ingest'],'stopped':[],'errors':[]}
    assert run('release','ingest')=={'error':'not_native'}
```

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control/test_cli.py`
Expected: FAIL (`SystemExit: 2`, because `reset`, `adopt` and the other commands are not registered).

- [ ] **Step 3: Implement.** In `control/cli.py`:
1. Add `'reset':lambda a:('unit.reset',{'unit':a.unit}),` to `CALLS`.
2. In `add_parsers`, change `for action in ('start','stop','restart'):` to `for action in ('start','stop','restart','reset','adopt','release'):`, and add `sub.add_parser('panic-revert')` after the `audit` parser.
3. Replace `execute` with:

```python
def execute(args, *, layout=None, call=None, console=None, launchd=None, agents=None):
    layout=layout or Layout()
    if args.action=='serve': return serve(layout)
    if args.action=='plist': return write_plist(layout,args.out,args.python)
    if call is None: from .client import call
    if args.action in ('adopt','release','panic-revert'): return local(args,layout,call,console,launchd,agents)
    op,params=CALLS[args.action](args)
    try: response=call(layout,op,params)
    except OSError: return {'error':'supervisor_unavailable'}
    if not response.get('ok'): return {'error':response.get('error',{}).get('code','failed')}
    result=response.get('result')
    return result if isinstance(result,dict) else {'items':result}


def local(args, layout, call, console, launchd, agents):
    """Backend moves run here, in the owner's terminal, against state.db; never over the socket."""
    from . import migrate
    from .store import Store
    if console is None:
        from ..presence import ConsolePresence
        console=ConsolePresence(output=lambda text:print(text,file=sys.stderr))
    layout.ensure(); store=Store(layout.state)
    try:
        if args.action=='panic-revert': return migrate.panic_revert(layout,store,launchd=launchd,agents=agents)
        if args.action=='adopt':
            return migrate.adopt(layout,store,args.unit,console=console,call=call,launchd=launchd,agents=agents)
        return migrate.release(layout,store,args.unit,console=console,launchd=launchd,agents=agents)
    finally: store.close()
```

In `profile-log/agentmesh_log/cli.py` `execute()`, change `return execute_control(args)` to `return execute_control(args,console=console)`.

Note: `panic_revert` sleeps 1 s by default. `test_adopt_release_panic_locally` accepts that one second.

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests/control profile-log/tests/broker/test_cli.py`
Expected: all pass.

- [ ] **Step 5: Commit.**

```bash
git add profile-log/agentmesh_log/control/cli.py profile-log/agentmesh_log/cli.py profile-log/tests/control/test_cli.py
git commit -m "feat(control): adopt, release, reset and panic-revert commands

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Mutation gates (reviewer runs; record the results in the PR)

Apply each mutant alone, run the named test file, confirm at least one FAILURE, then revert. If a mutant survives, add a killing test before you merge.

| # | File | Mutation | Test file |
|---|---|---|---|
| N1 | `native.py` | `>STORM_KILLS` → `>=STORM_KILLS` | `test_native.py` (`three`) |
| N2 | `native.py` | remove ` or self.store.failed(name)` from the tick skip | `test_native.py` (`four`) |
| N3 | `native.py` | `now-run['started']>limit` → `now-run['started']>=limit` | `test_native.py` |
| N4 | `native.py` | tick's `signal.SIGKILL` → `signal.SIGTERM` | `test_native.py` |
| N5 | `native.py` | reconcile `self.token(run['pid'])==run['pid_started']` → `True` | `test_native.py` |
| N6 | `native.py` | remove `if self.store.failed(unit.name): raise RuntimeError('unit_failed')` | `test_native.py` |
| N7 | `schedule.py` | `env={key:parent[key] for key in ENV_BASE if key in parent}` → `env=dict(parent)` | `test_schedule.py`, `test_native.py` |
| N8 | `schedule.py` | calendar `fire.timestamp()>reference` → `>=reference` | `test_schedule.py` |
| G1 | `migrate.py` | adopt `if not console.confirm(` → `if False and not console.confirm(` | `test_migrate.py` |
| G2 | `migrate.py` | release, the same | `test_migrate.py` |
| G3 | `migrate.py` | adopt `while launchd.status(unit)['running']:` → `while False:` | `test_migrate.py` |
| G4 | `migrate.py` | panic `token(run['pid'])==run['pid_started']` → `True` | `test_migrate.py` |
| O1 | `ops.py` | remove the `unit_failed` line from `runnable` | `test_ops.py` |

- [ ] **Step 1:** Run all 13 mutants with a script (the same pattern as M1), and paste the KILLED or SURVIVED table into the PR.

---

### Task 11: Full suites, spec amendments, PR

- [ ] **Step 1: Run the full suites.**

Run: `$PY -m pytest -q -p no:cacheprovider profile-log/tests`
Expected: all pass, except `test_p6_s12_close_cross_verifier`, which fails locally without `gateway/node_modules` (a known baseline failure).

Run: `$PY -m pytest -q -p no:cacheprovider memory-sync/test_job_notify.py insight`
Expected: all pass.

- [ ] **Step 2: Amend the spec.** Append to `docs/superpowers/specs/2026-10-23-local-control-plane-design.md`:

```markdown
## Amendments from milestone 2 planning (2026-10-24)

- Backend moves are owner-run CLI commands, `agentmesh-log control adopt|release <unit>`, gated by typed console presence (`Type migrate:`), until Touch ID arrives in M3. They are not socket ops. The supervisor only reads `state.db`.
- M2's native backend runs scheduled jobs only (retention, catch-up, ingest, summary). KeepAlive services move in M5.
- A native run reproduces the plist's argv, cwd, logs, umask and schedule (`StartInterval`, or `StartCalendarInterval` with Minute/Hour/Weekday). Its environment is launchd's base keys plus the plist's `EnvironmentVariables`, without the supervisor's `AGENTMESH_HOME`. `ProcessType=Background` is not reproduced.
- The storm guard counts watchdog kills: more than 3 within 1 h puts the unit in `failed` until `control reset`. It alerts once through job_notify's Discord webhook.
- At start, orphans are sent SIGTERM only when pid plus `ps lstart` match, and their runs are marked `orphaned`.
- `panic-revert` needs no presence.
```

- [ ] **Step 3: Commit, push, and open the PR** with the mutation table. The body ends with the Claude Code attribution line.

---

### Task 12: Owner-run deploy and migration runbook (after merge)

Production deploys and backend moves are owner-run. Each `adopt` asks you to type `migrate`.

- [ ] **Step 1: Deploy the code.**
  - Swap the app to the merge commit with the M1 deploy script pattern: stage `profile-log/agentmesh_log` and the docs and requirements files, write `REVISION`, rename, and keep the old directory. Change `REV` and the drift guard. `job_notify.py` does not change in M2.
  - Restart the supervisor so it loads the new code:

```bash
launchctl bootout gui/$(id -u)/org.agentmesh.control
```

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/org.agentmesh.control.plist
```

  - Verify: `control status` shows `"backend": "launchd"` for all 8 units, and `control units` shows `native_ok` for 4.

- [ ] **Step 2: Migration step 1, retention.**
  - Run `~/.agentmesh/venv/bin/python -m agentmesh_log.cli control adopt retention` and type `migrate`.
  - Verify:
    - `control status` shows retention with `backend: native` and health `idle`.
    - `launchctl print gui/$(id -u)/org.agentmesh.retention` reports not found.
  - **Do not start retention by hand.** It runs `staging retention --apply`. Its first native run is the next Sunday at 04:00, after which `status/launchd-retention.log` gets a job_notify JSON line.
- [ ] **Step 3: Panic drill (low risk while only retention is native).**
  - Run `control panic-revert`. Expect `reverted: ["retention"]` and a loaded launchd retention.
  - Bootstrap the supervisor again (the Step 1 command), then adopt retention again.
- [ ] **Step 4: Migration step 2, catch-up**, at least 3 days after step 1 and after one native retention run.
  - Adopt it.
  - Within 5 minutes, `logs/catch-up.out.log` gets a new line.
  - Recall still works: `agentmesh-log recall` through the broker returns work and personal results.
- [ ] **Step 5: Migration step 3, ingest**, at least 3 days later.
  - Adopt it.
  - `status/launchd-ingest.log` gains a line every minute.
  - Repeat the M1 maintenance drill: one tick held, then the next tick runs.
- [ ] **Step 6: Migration step 4, summary**, at least 3 days later.
  - Adopt it.
  - `status/launchd-summary.log` gains a line every 5 minutes.
  - `control quota` updates.
- [ ] **Rollback at any step:** `control release <unit>` (type `migrate`). In an emergency, `control panic-revert`.

## Self-review notes

- **Spec coverage (M2):**
  - `NativeBackend`: Task 5
  - `state.db`: Task 2
  - scheduler with sleep catch-up and no overlap: Tasks 3 and 5
  - enforcing watchdog and restart-storm guard with alert: Tasks 4 and 5
  - `panic-revert`: Task 8
  - migration steps 1–4: Task 12
  - reconcile of orphans: Task 5
  - routing and status: Task 6
  - supervisor lifecycle: Task 7
  - The M1 deferrals now covered: `unit.reset` and `state.db`.
- **Placeholders:** none. The deploy step reuses the committed M1 script pattern. Every code step has full code.
- **Names used across tasks:**
  - `Store`: `backend`, `spec`, `changed_at`, `set_backend(name,backend,spec,at)`, `native_units`, `start_run`, `end_run`, `open_runs(unit=None)`, `last_start`, `last_exit`, `add_kill`, `kills_since`, `set_failed`, `failed`, `clear_failed`, `close`
  - `schedule`: `plist_path`, `spec_from_plist`, `read_spec`, `child_env`, `due`, `DEFAULT_PATH`
  - `NativeBackend`: `status`, `start`, `restart`, `stop`, `reset`, `tick`, `reconcile`, `shutdown`, `running`
  - `native` module: `started_token`, `signal_group`
  - `Router`, and `migrate.adopt`, `release`, `panic_revert`
  - `Layout.state`, `Layout.webhook`, `Unit.native_ok`
  - support: `FakeLaunchd`, `Presence`, `ok_call`, `write_plists`, `PLISTS`
