"""Retire the SQLite staging database on this device (SQLite retirement R4).

Owner-run in a terminal, never over the socket: `agentmesh-log control sqlite-retire` is a dry run that changes
nothing; `--apply` needs typed console presence and Touch ID (or the Mac password) through the supervisor's helper.

Apply, in order, with ingest and summary paused (maintenance on, and both job locks held so no run is in flight):
  1. final import (`migrate catch-up` as the importer) and `migrate verify`: any staging memory missing from the
     profile log stops the ceremony before anything changes;
  2. seed the summarizer's JSON state from the database (cursors, waiting events, floor, schedule, quarantine);
  3. save the current production and bridge configs in the archive, then switch: production `backend: broker`,
     bridge `dual_write` and `authority` off (the importer goes quiet; revoke it in settings afterwards);
  4. fold the WAL and move the database, its WAL/SHM, locks, catch-up cache, digest state and the Markdown export
     into backups/archive/sqlite-<node>-<stamp>/ (files 0400, folders 0700, README, manifest), on the same disk,
     so no plaintext copy is left behind;
  5. maintenance off, then one ingest scan and one forced summary through the broker.
A failure before step 4 completes puts every config, state file and moved file back.

`sqlite-rollback` (same presence) moves the archive back and restores both configs; the summarizer's state is set
aside in the archive. `sqlite-archive delete` (same presence) removes an archive, and refuses inside the 30-day
rollback window unless told otherwise.
"""
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time
from ..broker.audit import append_row
from . import maintenance

ROLLBACK_DAYS=30
STATE_FILES=('transcript-cursors.json','summary-state.json')
LOCK_WAIT_S=900


def now(): return datetime.now(timezone.utc)


def audit(layout, op, args, outcome):
    layout.ensure()
    append_row(layout.audit,dict(ts=time.time_ns()//1000000,op=op,args=args,outcome=outcome,caller_pid=os.getpid(),caller_exe=sys.executable))


def read(path):
    value=json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value,dict): raise ValueError('invalid config')
    return value


def write(path, value):
    """Atomic 0600 replace of a JSON config."""
    path=Path(path); tmp=path.with_name(path.name+'.tmp')
    fd=os.open(tmp,os.O_CREAT|os.O_WRONLY|os.O_TRUNC|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as handle:
        handle.write(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=1)); handle.flush(); os.fsync(handle.fileno())
    os.replace(tmp,path)


class Setup:
    """Everything the ceremony touches, read once. Problems are collected, never raised, so the dry run lists all."""
    def __init__(self, root, summarizer_key=None):
        self.root=Path(root); self.problems=[]
        self.production=self.root/'jobs'/'agentmesh-production.json'
        self.jobs=self.root/'jobs'
        self.summarizer_key=Path(summarizer_key) if summarizer_key else self.root/'agents'/'summarizer.key'
        try: self.config=read(self.production)
        except (OSError,ValueError): self.config={}; self.problems.append('production config missing or invalid')
        if self.config.get('backend','sqlite')!='sqlite': self.problems.append('already retired: production backend is not sqlite')
        self.database=Path(self.config['database']) if self.config.get('database') else None
        self.runtime=Path(self.config['runtime']) if self.config.get('runtime') else None
        if self.database is None or not self.database.is_file(): self.problems.append('staging database missing')
        if self.runtime is None or not all((self.runtime/n).is_file() for n in ('broker_digest.py','sqlite_cutover.py','transcripts.py')):
            self.problems.append('deployed runtime predates R4; deploy main first')
        script=self.jobs/'agentmesh_production.py'
        try:
            if 'def broker(' not in script.read_text(encoding='utf-8'): self.problems.append('deployed production job predates R2; deploy main first')
        except OSError: self.problems.append('production job script missing')
        self.bridge_path=Path(os.environ.get('AGENTMESH_PROFILE_LOG_BRIDGE',str(self.database)+'.profile-log.json')) if self.database else None
        try: self.bridge=read(self.bridge_path) if self.bridge_path else {}
        except (OSError,ValueError): self.bridge={}
        if not self.bridge: self.problems.append('bridge config missing or invalid')
        for name in STATE_FILES:
            if (self.jobs/name).exists(): self.problems.append('summarizer state already present: '+name)
        if not self.summarizer_key.is_file(): self.problems.append('summarizer agent key missing (grant an agent named summarizer with allow_summarize)')
        self.export=Path(self.config['export_root']) if self.config.get('export_root') else None

    def memory_sync(self):
        if str(self.runtime) not in sys.path: sys.path.insert(0,str(self.runtime))
        import sqlite_cutover, bounded_digest
        return sqlite_cutover, bounded_digest

    def moves(self):
        """(source, name in the archive) for every staging file that exists; the bridge config stays in place."""
        db=str(self.database)
        candidates=[(Path(db),'staging.db'),(Path(db+'-wal'),'staging.db-wal'),(Path(db+'-shm'),'staging.db-shm'),
                    (Path(db+'.bounded.lock'),'staging.db.bounded.lock'),(Path(db+'.scheduler.bounded.lock'),'staging.db.scheduler.bounded.lock'),
                    (Path(db+'.profile-log.catch-up.json'),'catch-up-cache.json'),(self.jobs/'.bounded_digest_state.json','bounded-digest-state.json')]
        if self.export is not None: candidates.append((self.export,'export'))
        return [(s,n) for s,n in candidates if s.exists() and not s.is_symlink()]


def check_summarizer(setup, mapping, client_factory):
    """The summarizer can write every mapped scope and holds allow_summarize (a heads probe is denied without it)."""
    try:
        client=client_factory(setup.summarizer_key)
        try:
            who=client.request(dict(op='whoami'))
            missing=sorted(mapping.targets-set(who.get('write',[])))
            if missing: return 'summarizer cannot write mapped scopes: '+', '.join(missing)
            for scope in sorted(mapping.targets):
                if 'error' in client.request(dict(op='heads',scope=scope,memory_keys=['preflight'])): return 'summarizer lacks allow_summarize'
        finally: client.close()
    except Exception as exc: return 'summarizer agent unavailable: '+type(exc).__name__
    return None


def plan(setup, *, client_factory=None):
    """The dry run: what would move, what would be seeded, and every problem that blocks --apply."""
    from ..migrate.mapping import Mapping
    from ..broker.client import BrokerClient
    result=dict(problems=list(setup.problems))
    if setup.problems: return result
    try: mapping=Mapping.load(setup.bridge['mapping'])
    except Exception: result['problems'].append('migration mapping invalid'); return result
    problem=check_summarizer(setup,mapping,client_factory or BrokerClient.from_file)
    if problem: result['problems'].append(problem)
    sqlite_cutover,_=setup.memory_sync()
    seeded=sqlite_cutover.plan(setup.database)
    if seeded['unreadable']: result['problems'].append('waiting events in transcripts that are gone or replaced: %d'%len(seeded['unreadable']))
    result.update(seed=seeded['counts'],targets=sorted(mapping.targets),
                  archive=[dict(name=n,bytes=size(s)) for s,n in setup.moves()])
    return result


def size(path):
    path=Path(path)
    if path.is_file(): return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob('*') if p.is_file() and not p.is_symlink())


def summary_text(setup, result):
    seed=result['seed']
    return ('Retire the SQLite staging database on this Mac.\n'
            'Imports anything left, verifies nothing is missing, then switches the summary job to the broker.\n'
            'Transcripts followed: %d (waiting events: %d, read again: %d)\n'
            'Archived (not deleted): %s\n'
            'Undo for %d days: agentmesh-log control sqlite-rollback'
            %(seed['transcripts'],seed['waiting_events'],seed['rekeyed_events'],', '.join(a['name'] for a in result['archive']),ROLLBACK_DAYS))


def lock_down(path):
    """Archive modes: files 0400, folders 0700, deepest first so the walk can finish."""
    path=Path(path)
    if path.is_dir():
        for base,dirs,files in os.walk(path,topdown=False):
            for f in files: os.chmod(os.path.join(base,f),0o400)
            for d in dirs: os.chmod(os.path.join(base,d),0o700)
        os.chmod(path,0o700)
    else: os.chmod(path,0o400)


def unlock(path, mode):
    path=Path(path)
    if path.is_dir():
        os.chmod(path,0o700)
        for base,dirs,files in os.walk(path):
            for d in dirs: os.chmod(os.path.join(base,d),0o700)
            for f in files: os.chmod(os.path.join(base,f),0o600)
    os.chmod(path,mode)


README='''# Archived SQLite staging database

Moved here by `agentmesh-log control sqlite-retire` on {date}. This is plaintext memory and transcript excerpts:
owner-only, never synced and never backed up.

- Undo (within {days} days): `agentmesh-log control sqlite-rollback --archive {name}`
- Delete for good: `agentmesh-log control sqlite-archive delete --archive {name}`

`manifest.json` lists where each file came from; `config/` holds the production and bridge configs from before.
'''


def present(console, helper, action, summary):
    """Tier 3: typed console confirmation and Touch ID (or the Mac password) through the supervisor's helper."""
    if not console.confirm(action,summary): return False
    return helper is None or helper.confirm(action,summary)


def first_run(setup, runner):
    report={}
    for name,args in (('ingest',[]),('summary',['--force'])):
        try:
            done=runner([sys.executable,str(setup.jobs/('agentmesh_%s.py'%name)),*args],capture_output=True,timeout=3600)
            report[name]='ok' if done.returncode==0 else 'failed'
        except (OSError,subprocess.SubprocessError): report[name]='failed'
    return report


def retire(layout, setup, *, console, helper, client_factory=None, runner=subprocess.run, clock=now, first=True):
    from ..migrate.mapping import Mapping
    from ..migrate.run import run as import_rows
    from ..migrate.verify import verify
    from ..broker.client import BrokerClient
    result=plan(setup,client_factory=client_factory)
    if result['problems']: return dict(error='preflight_failed',problems=result['problems'])
    if not present(console,helper,'migrate',summary_text(setup,result)):
        audit(layout,'sqlite.retire',{},'presence_denied'); return dict(error='presence_required')
    audit(layout,'sqlite.retire',{},'requested')
    sqlite_cutover,bounded_digest=setup.memory_sync()
    factory=client_factory or BrokerClient.from_file
    mapping=Mapping.load(setup.bridge['mapping'])
    stamp=clock().strftime('%Y%m%d-%H%M%S')
    node=str(setup.bridge.get('staging_node') or 'node').replace('/','-')[:40]
    archive=setup.root/'backups'/'archive'/('sqlite-%s-%s'%(node,stamp))
    turned_on=not maintenance.status(layout)['on']
    if turned_on: maintenance.turn_on(layout,'sqlite-retire')
    done=dict(seeded=False,configs=False,moved=[])
    try:
        with ExitStack() as locks:
            for action in ('ingest','summary'):
                locks.enter_context(bounded_digest.run_lock(str(setup.production)+'.'+action,wait=LOCK_WAIT_S))
            client=factory(setup.bridge['import_agent_key'])
            try:
                cache=Path(str(setup.database)+'.profile-log.catch-up.json')
                imported=import_rows(setup.database,mapping,client,cache=cache)
                if imported['errors'] or imported['unmapped']: raise Abort('final import incomplete: '+','.join(imported['errors'] or ['unmapped project']))
                gap=verify(setup.database,mapping,client)
                if gap.get('missing') or gap.get('retired_but_present'): raise Abort('verify found %d missing, %d retired but present'%(gap.get('missing',0),gap.get('retired_but_present',0)))
            finally: client.close()
            seeded=sqlite_cutover.seed(setup.database,setup.jobs); done['seeded']=True
            archive.mkdir(mode=0o700,parents=True)
            (archive/'config').mkdir(mode=0o700)
            shutil.copy2(setup.production,archive/'config'/'agentmesh-production.json')
            shutil.copy2(setup.bridge_path,archive/'config'/'bridge.json')
            write(setup.production,{**setup.config,'backend':'broker','broker':dict(cli_argv=setup.bridge['cli_argv'],
                agent_key=str(setup.summarizer_key),mapping=setup.bridge['mapping'],state_dir=str(setup.jobs))})
            write(setup.bridge_path,{**setup.bridge,'dual_write':False,'authority':False}); done['configs']=True
            sqlite_cutover.checkpoint(setup.database)
            manifest=dict(format='agentmesh-sqlite-archive-v1',created_at=clock().isoformat(),node=node,
                          production=str(setup.production),bridge=str(setup.bridge_path),files=[])
            for source,name in setup.moves():
                mode=stat.S_IMODE(source.stat().st_mode)
                os.rename(source,archive/name); done['moved'].append((source,archive/name,mode))
                manifest['files'].append(dict(name=name,source=str(source),mode=mode))
            for _,target,_ in done['moved']: lock_down(target)
            write(archive/'manifest.json',manifest); os.chmod(archive/'manifest.json',0o400)
            (archive/'README.md').write_text(README.format(date=clock().date().isoformat(),days=ROLLBACK_DAYS,name=archive.name),encoding='utf-8')
            os.chmod(archive/'README.md',0o400)
            for path in (archive/'config').iterdir(): os.chmod(path,0o400)
    except Exception as exc:
        undo(setup,archive,done)
        if turned_on: maintenance.turn_off(layout)
        reason=str(exc) if isinstance(exc,Abort) else type(exc).__name__
        audit(layout,'sqlite.retire',{},'aborted')
        return dict(error='aborted',reason=reason,restored=True)
    if turned_on: maintenance.turn_off(layout)
    audit(layout,'sqlite.retire',{'archive':archive.name},'ok')
    return dict(ok=True,archive=archive.name,seed=seeded,moved=[n for n in (m['name'] for m in manifest['files'])],
                first_run=first_run(setup,runner) if first else None,
                next='Revoke the importer agent in settings and apply. Undo within %d days: control sqlite-rollback --archive %s'
                     %(ROLLBACK_DAYS,archive.name))


class Abort(Exception): pass


def undo(setup, archive, done):
    """Put back whatever the failed ceremony changed: files, configs, seeded state, the empty archive."""
    for source,target,mode in reversed(done['moved']):
        if target.exists() and not source.exists(): unlock(target,mode); os.rename(target,source)
    if done['configs']:
        shutil.copy2(archive/'config'/'agentmesh-production.json',setup.production)
        shutil.copy2(archive/'config'/'bridge.json',setup.bridge_path)
        os.chmod(setup.bridge_path,0o600)
    if done['seeded']:
        for name in STATE_FILES: (setup.jobs/name).unlink(missing_ok=True)
    if archive.exists():
        for path in sorted(archive.rglob('*'),reverse=True):
            os.chmod(path,0o700 if path.is_dir() else 0o600)
        shutil.rmtree(archive)


def archives(root):
    base=Path(root)/'backups'/'archive'
    rows=[]
    for path in sorted(base.glob('sqlite-*')) if base.is_dir() else []:
        try: manifest=read(path/'manifest.json')
        except (OSError,ValueError): continue
        created=datetime.fromisoformat(manifest['created_at'])
        rows.append(dict(archive=path.name,created_at=manifest['created_at'],age_days=(now()-created).days,
                         rollback_open=(now()-created).days<ROLLBACK_DAYS,bytes=size(path)))
    return rows


def find(root, name):
    path=Path(root)/'backups'/'archive'/str(name)
    if '/' in str(name) or not str(name).startswith('sqlite-') or not (path/'manifest.json').is_file(): raise Abort('archive not found')
    return path,read(path/'manifest.json')


def rollback(layout, root, name, *, console, helper):
    try: archive,manifest=find(root,name)
    except Abort as exc: return dict(error=str(exc))
    blocked=[f['source'] for f in manifest['files'] if Path(f['source']).exists()]
    if blocked: return dict(error='destination exists',paths=blocked)
    summary=('Roll back the SQLite retirement from %s.\nThe staging database, its export and both configs return; the '
             'summarizer state is set aside in the archive.\nTranscripts since the cutover are summarized again into SQLite.'%archive.name)
    if not present(console,helper,'migrate',summary):
        audit(layout,'sqlite.rollback',{'archive':archive.name},'presence_denied'); return dict(error='presence_required')
    audit(layout,'sqlite.rollback',{'archive':archive.name},'requested')
    turned_on=not maintenance.status(layout)['on']
    if turned_on: maintenance.turn_on(layout,'sqlite-rollback')
    try:
        jobs=Path(manifest['production']).parent
        aside=archive/'rolled-back-state'
        os.chmod(archive,0o700)
        for name_ in STATE_FILES:
            if (jobs/name_).exists():
                aside.mkdir(mode=0o700,exist_ok=True); os.rename(jobs/name_,aside/name_)
        for item in manifest['files']:
            target=archive/item['name']; unlock(target,item['mode']); os.rename(target,item['source'])
        for copy,dest in ((archive/'config'/'agentmesh-production.json',manifest['production']),(archive/'config'/'bridge.json',manifest['bridge'])):
            write(dest,read(copy))
    finally:
        if turned_on: maintenance.turn_off(layout)
    audit(layout,'sqlite.rollback',{'archive':archive.name},'ok')
    return dict(ok=True,archive=archive.name,next='Grant the importer again in settings if it was revoked, then apply.')


def delete(layout, root, name, *, console, helper, early=False):
    try: archive,manifest=find(root,name)
    except Abort as exc: return dict(error=str(exc))
    age=(now()-datetime.fromisoformat(manifest['created_at'])).days
    if age<ROLLBACK_DAYS and not early: return dict(error='rollback_window_open',age_days=age)
    summary='Delete %s for good (%d days old). This ends any rollback of the SQLite retirement.'%(archive.name,age)
    if not present(console,helper,'delete',summary):
        audit(layout,'sqlite.archive.delete',{'archive':archive.name},'presence_denied'); return dict(error='presence_required')
    for path in sorted(archive.rglob('*'),reverse=True):
        os.chmod(path,0o700 if path.is_dir() else 0o600)
    os.chmod(archive,0o700); shutil.rmtree(archive)
    audit(layout,'sqlite.archive.delete',{'archive':archive.name},'ok')
    return dict(ok=True,deleted=archive.name)
