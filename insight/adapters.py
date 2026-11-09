"""Read-only adapters. Only allowlisted content-free metrics leave this module."""
import json
import os
import stat
import re
from pathlib import Path


def agentmesh_root(agentmesh_home=None):
    return Path(agentmesh_home or os.environ.get('AGENTMESH_HOME', str(Path.home()/'.agentmesh'))).expanduser()


def layout(agentmesh_home=None):
    """AgentMesh-home paths only; filesystem paths stay inside the adapters."""
    base = agentmesh_root(agentmesh_home)
    result = {}
    for key, name in (('coordinator','jobs/.bounded_digest_state.json'),
                      ('summary','jobs/summary-metrics.jsonl'),
                      ('smoke','jobs/summary-smoke-test-metrics.jsonl')):
        path = base/name
        result[key] = dict(path=path, source='agentmesh' if path.exists() or path.is_symlink() else 'missing', home=base)
        if key in ('summary', 'smoke'):
            # Frozen history moved in from older locations, oldest first, then the live log.
            result[key]['paths'] = sorted((base/'backups/archive').glob(path.name+'.pre-relocate-*')) + [path]*path.exists()
    return result


def cron_archive(agentmesh_home=None):
    """Frozen history of the retired cron digest (cron/usage_audit.jsonl, state.db)."""
    return agentmesh_root(agentmesh_home)/'backups/archive/digest-cron'


def redact(value):
    """Best-effort defense, not a guarantee that prose is secret-free."""
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if not isinstance(value, str):
        return value
    value = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|$)', '[REDACTED KEY]', value, flags=re.S)
    value = re.sub(r'(?i)\b(password|passwd|api[_-]?key|access[_-]?token|secret|authorization)\b["\s]*[:=][\s"]*(?:Bearer\s+)?[^\s,;"<>]+', r'\1=[REDACTED]', value)
    value = re.sub(r'\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{15,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b', '[REDACTED TOKEN]', value)
    value = re.sub(r'(?i)(https?://)[^\s/@:]+:[^\s/@]+@', r'\1[REDACTED]@', value)
    return value.replace('\u00b7', ' ')


def observed_state(stamp):
    from datetime import datetime, timezone
    if not stamp:
        return 'unknown'
    try:
        when = datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
        when = when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when
        age = (datetime.now(timezone.utc) - when).total_seconds()
        return 'recent observation' if 0 <= age < 3600 else 'stale'
    except (ValueError, TypeError):
        return 'unknown'


MEMORY_LABELS = ('general', 'internal', 'secret')
STATS_KINDS = ('fact', 'decision', 'constraint', 'preference', 'procedure', 'open_loop', 'summary', 'page', 'note', 'task')


def memory_stats(agentmesh_home=None, agent_key=None, runner=None, executable=None):
    """Counts and metadata of the profile log through the broker, never content (SQLite retirement R3).

    Runs `agentmesh-log stats` as the read-only insight agent (agents/insight.key unless given). The broker answer
    is projected onto a fixed schema here, so a changed or hostile answer cannot put text on the page."""
    import subprocess
    import sys
    key = Path(agent_key) if agent_key else agentmesh_root(agentmesh_home)/'agents'/'insight.key'
    if not key.is_file():
        return {'available':False,'configured':False,'error':'Insight agent not configured',
                'note':'Grant a read-only agent named insight to show profile log counts here.'}
    argv = [executable or sys.executable, '-m', 'agentmesh_log.cli', 'stats', '--agent-key', str(key)]
    try:
        done = (runner or subprocess.run)(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=20)
        value = json.loads(done.stdout.decode('utf-8')) if len(done.stdout) < 1024*1024 else None
    except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired):
        value = None
    if not isinstance(value, dict) or 'error' in value or done.returncode:
        code = value.get('error') if isinstance(value, dict) else None
        code = code if isinstance(code, str) and re.fullmatch(r'[a-z_]{1,40}', code) else 'unavailable'
        return {'available':False,'configured':True,'error':'Profile log unavailable ('+code+')'}
    def count(v): return v if type(v) is int and 0 <= v < 2**53 else None
    def name(v): return v if isinstance(v, str) and 0 < len(v) <= 200 and v.isprintable() else None
    def counts(v, allowed): return {k:count(n) for k, n in (v if isinstance(v, dict) else {}).items() if k in allowed}
    scopes = []
    for row in value.get('scopes') if isinstance(value.get('scopes'), list) else []:
        if not isinstance(row, dict) or name(row.get('scope')) is None: continue
        updated = row.get('updated_at') if isinstance(row.get('updated_at'), str) and parse_instant(row.get('updated_at')) else None
        scopes.append({'scope':name(row['scope']),'profile':name(row.get('profile')),'count':count(row.get('count')),
                       'keyed':count(row.get('keyed')),'kinds':counts(row.get('kinds'), STATS_KINDS),
                       'labels':counts(row.get('labels'), MEMORY_LABELS),'updated_at':updated})
    projects = [{'project':name(r.get('project')),'count':count(r.get('count'))}
                for r in (value.get('projects') if isinstance(value.get('projects'), list) else [])[:50]
                if isinstance(r, dict) and name(r.get('project'))]
    return {'available':True,'configured':True,'total':count(value.get('total')),'scopes':scopes[:100],'projects':projects,
            'more_projects':count(value.get('more_projects')),
            'note':'Counts from the encrypted profile log, read by the insight agent. No memory text leaves the broker.'}


def unavailable(label):
    return {'available':False,'error':label + ' unavailable or not configured'}


def launchd_jobs(agentmesh_home=None, agents=None):
    """Wrapper status for the launchd jobs.

    All jobs, including Retention, omit missing/invalid state files. A missing
    plist keeps the state row with enabled=False, including before installation.
    """
    import plistlib
    from datetime import datetime, timedelta
    base = Path(agentmesh_home or os.environ.get('AGENTMESH_HOME', str(Path.home()/'.agentmesh'))).expanduser()
    agents = Path(agents or Path.home()/'Library/LaunchAgents')
    rows = []
    for label, name in (('Ingest','ingest'),('Summary','summary'),('Retention','retention')):
        try:
            state = json.loads((base/'status'/('job-'+name+'.json')).read_text())
            if not isinstance(state, dict):
                raise ValueError('Invalid job status')
        except (OSError, ValueError):
            continue
        row = {'name':label,'id':'org.agentmesh.'+name,'available':True,'source':'launchd','last_run_at':state.get('at') if isinstance(state.get('at'), str) else None,
               'last_status':'ok' if state.get('ok') is True else 'error' if state.get('ok') is False else None,'has_error':state.get('ok') is False,
               'failing_since':None if state.get('ok') is not False else state.get('since'),'enabled':None,'state':None,'interval_seconds':None,'next_run_at':None}
        try:
            # After `control consolidate` the supervisor runs the job; its plist is parked under control/units/.
            path = agents/('org.agentmesh.'+name+'.plist')
            parked = base/'control'/'units'/path.name
            with open(path if path.exists() or not parked.exists() else parked, 'rb') as f:
                plist = plistlib.load(f)
            row['enabled'] = True
            interval = plist.get('StartInterval')
            if type(interval) is int and interval > 0:
                row['interval_seconds'] = interval
                row['state'] = 'every '+(str(interval//60)+' min' if interval % 60 == 0 else str(interval)+' s')
                if row['last_run_at']:
                    row['next_run_at'] = (datetime.fromisoformat(row['last_run_at'].replace('Z', '+00:00'))+timedelta(seconds=interval)).isoformat()
        except (OSError, ValueError, plistlib.InvalidFileException):
            row['enabled'] = False
        row['observation_state'] = observed_state(row['last_run_at'])
        rows.append(row)
    return rows


JOB_DETAIL = re.compile(r'^[A-Za-z0-9 _.,:;/()=-]{1,200}$')


def job_failures(agentmesh_home=None, now=None, tail=1024*1024):
    """Failed runs from the launchd job wrapper logs (JSON lines of safe fields only), newest first."""
    from datetime import datetime, timedelta, timezone
    base = Path(agentmesh_home or os.environ.get('AGENTMESH_HOME', str(Path.home()/'.agentmesh'))).expanduser()
    now = now or datetime.now(timezone.utc)
    rows, counts = [], {}
    for name in ('ingest', 'summary', 'retention'):
        runs = {'day':0,'day_failed':0,'week_failed':0}
        try:
            with open(base/'status'/('launchd-'+name+'.log'), 'rb') as f:
                f.seek(0, os.SEEK_END)
                offset = max(0, f.tell()-tail)
                f.seek(offset)
                lines = f.read().decode('utf-8', 'replace').splitlines()
                if offset > 0:
                    lines = lines[1:]
        except OSError:
            continue
        for line in lines:
            try:
                value = json.loads(line)
                at = datetime.fromisoformat(value['at'].replace('Z', '+00:00'))
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
            if not isinstance(value, dict) or at.tzinfo is None:
                continue
            failed = type(value.get('exit')) is int and value['exit'] != 0
            age = now-at
            if age <= timedelta(days=1):
                runs['day'] += 1
                runs['day_failed'] += failed
            if failed and age <= timedelta(days=7):
                runs['week_failed'] += 1
                pick = lambda key, pattern: value.get(key) if isinstance(value.get(key), str) and pattern.match(value[key]) else None
                rows.append({'job':name.capitalize(),'at':at.isoformat(),'exit':value['exit'],
                             'error_class':pick('error_class', re.compile(r'^[A-Za-z_][A-Za-z0-9_.]{0,63}$')),
                             'status':pick('status', re.compile(r'^[a-z_]{1,32}$')),'detail':pick('detail', JOB_DETAIL)})
        counts[name] = runs
    rows.sort(key=lambda row: row['at'], reverse=True)
    return {'available':bool(counts),'counts':counts,'rows':rows[:50]}




def parse_instant(value):
    from datetime import datetime, timezone
    try:
        when = datetime.fromisoformat(str(value).replace('Z', '+00:00').replace(' ', 'T'))
        return when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


TOOLS = {'claude': '/.claude/projects/', 'codex': '/.codex/sessions/', 'omp': '/.omp/agent/sessions/'}
SUMMARY_STATUSES = ('scheduled', 'pending', 'completed', 'committed', 'blocked', 'failed')


def bounded_json(path, limit=8*1024*1024):
    """A regular, non-symlinked JSON file of at most `limit` bytes, else None."""
    try:
        if path.is_symlink(): return None
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode): return None
            raw = source.read(limit + 1)
        return json.loads(raw) if len(raw) <= limit else None
    except (OSError, ValueError):
        return None


def summary_status(agentmesh_home=None):
    """The broker summary's own files (SQLite retirement R2): counts, states and times only, never transcript paths
    or content. The ingest scan writes status/last-ingest.json, the summary tick status/last-summary.json; its
    cursors, schedule and held-back projects live in jobs/."""
    base = agentmesh_root(agentmesh_home)
    count = lambda v: v if type(v) is int and 0 <= v < 2**53 else None
    when = lambda v: v if isinstance(v, str) and parse_instant(v) else None
    obj = lambda v: v if isinstance(v, dict) else {}
    ingest = obj(bounded_json(base/'status/last-ingest.json'))
    summary = obj(bounded_json(base/'status/last-summary.json'))
    state = obj(bounded_json(base/'jobs/summary-state.json'))
    held = obj(bounded_json(base/'jobs/summary-unmapped.json'))
    cursors = obj(obj(bounded_json(base/'jobs/transcript-cursors.json')).get('files'))
    tools = {name: dict(tool=name, transcripts=0, latest_at=None) for name in TOOLS}
    from datetime import datetime, timezone
    for path in cursors:
        name = next((n for n, marker in TOOLS.items() if isinstance(path, str) and marker in path.replace('\\', '/')), None)
        if name is None: continue
        row = tools[name]; row['transcripts'] += 1
        try: modified = datetime.fromtimestamp(os.stat(path, follow_symlinks=False).st_mtime, timezone.utc).isoformat()
        except (OSError, ValueError): continue
        if row['latest_at'] is None or modified > row['latest_at']: row['latest_at'] = modified
    for row in tools.values(): row['state'] = observed_state(row['latest_at'])
    success = obj(state.get('success'))
    names = held.get('projects') if held.get('format') == 'agentmesh-summary-unmapped-v1' else []
    return {
        'available': bool(ingest or summary or state),
        'ingest': {k: count(ingest.get(k)) for k in ('found', 'processed', 'skipped', 'errors', 'invalid_json')} | {'observed_at': when(ingest.get('observed_at'))},
        'summary': {k: count(summary.get(k)) for k in ('llm_api_calls', 'events', 'batches')} | {
            'status': summary.get('status') if summary.get('status') in SUMMARY_STATUSES else ('unknown' if summary else None),
            'observed_at': when(summary.get('observed_at'))},
        'schedule': {'pending': state.get('pending') if type(state.get('pending')) is bool else None,
                     'last_completed_at': when(success.get('completed_at')),
                     'quarantined': len(state['quarantine']) if isinstance(state.get('quarantine'), list) else None},
        'held_projects': sorted({n for n in names if isinstance(n, str) and 0 < len(n) <= 200 and n.isprintable()}) if isinstance(names, list) else [],
        'tools': [r for r in tools.values() if r['transcripts']],
        'note': 'From the broker summary\'s own status files. Transcript activity is file modification time, not a live check.'}


def operations(agentmesh_home=None):
    """Pipeline and summary state. The SQLite staging and packet-sync sections went with SQLite retirement R5b."""
    from datetime import datetime, timezone
    result: dict = {'generated_at':datetime.now(timezone.utc).isoformat(),'refresh_seconds':20}
    try:
        selected = launchd_jobs(agentmesh_home)
        note = 'Launchd jobs: last result and time from the job wrapper; next run is last run plus the interval.'
        pipeline = {'available':True,'jobs':selected,'note':note}
        item = layout(agentmesh_home)['coordinator']
        state_path = item['path']
        pipeline['coordinator'] = dict(available=False, source=item['source'], pending=None, phase=None, success_day=None)
        if state_path.exists():
            state = json.loads(state_path.read_text())
            pending = state.get('pending')
            pipeline['coordinator'] = {'available':True,'source':item['source'],'pending':pending is not None,'phase':pending.get('phase') if isinstance(pending, dict) else None,'success_day':(state.get('success') or {}).get('day')}
        pipeline['failures'] = job_failures(agentmesh_home)
        result['pipeline'] = pipeline
    except Exception:
        result['pipeline'] = unavailable('Pipeline cron metadata')
    try:
        result['summary'] = summary_status(agentmesh_home)
    except Exception:
        result['summary'] = unavailable('Broker summary status')
    return redact(result)


METRICS = ('llm_api_calls','input_total_tokens','input_uncached_tokens','cache_read_tokens','output_tokens','reasoning_tokens','actual_cost_usd','duration_seconds','events_processed','context_max_input_tokens','raw_chars','submitted_chars','accepted_items','rejected_items','upserted_items','exporter_duration_seconds','events_pending_before','events_pending_after','cursor_before','cursor_after','batch_count','fallbacks','retries')
LABELS = ('run_id','status','trigger','model','provider','start','end')
def safe_run(row):
    out = {key: row.get(key) for key in LABELS + METRICS}
    out['error_count'] = len(row.get('errors') or [])
    out['error_detail'] = failure_reason(row) if row.get('status') in ('failed', 'error') else None
    return out


def failure_reason(row):
    detail = row.get('error_detail')
    if detail is None or detail == '':
        return 'no detail recorded'
    return detail if isinstance(detail, str) and JOB_DETAIL.match(detail) else 'other (unrecognised detail)'


def failure_reasons(runs, now=None):
    """Failed summary runs per error_detail; quota (429) stays its own visible reason."""
    from datetime import datetime, timedelta, timezone
    now = now or datetime.now(timezone.utc)
    groups = {}
    for r in runs:
        if r.get('status') not in ('failed', 'error'):
            continue
        reason = failure_reason(r)
        g = groups.setdefault(reason, {'reason':reason, 'count':0, 'cost_usd':0.0, 'cost_known':0, 'first_at':None, 'latest_at':None, 'last_24h':0,
                                       'quota':bool(re.search(r'\b429\b|quota|rate.?limit', reason, re.I))})
        g['count'] += 1
        cost = r.get('actual_cost_usd')
        if type(cost) in (int, float):
            g['cost_usd'] += cost; g['cost_known'] += 1
        at = r.get('start') or r.get('end')
        when = parse_instant(at)
        if when is None:
            continue
        if g['first_at'] is None or when < parse_instant(g['first_at']): g['first_at'] = at
        if g['latest_at'] is None or when > parse_instant(g['latest_at']): g['latest_at'] = at
        g['last_24h'] += now - when <= timedelta(days=1)
    rows = list(groups.values())
    for g in rows:
        g['cost_usd'] = round(g['cost_usd'], 6) if g['cost_known'] else None
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    rows.sort(key=lambda g: parse_instant(g['latest_at']) or epoch, reverse=True)
    return {'total':sum(g['count'] for g in rows), 'latest_at':rows[0]['latest_at'] if rows else None, 'rows':rows}

JOB='6fcf5646d34d'

def metrics(agentmesh_home=None, metrics_logs=None, archive=None):
    from datetime import datetime, timezone
    from metrics import report, read_jsonl, aggregate
    paths=layout(agentmesh_home)
    logs=paths['summary']['paths'] if metrics_logs is None else metrics_logs
    archive=Path(archive) if archive else cron_archive(agentmesh_home)
    result=report(archive/'cron/usage_audit.jsonl',archive/'state.db',logs)
    for key in ('new_latest','new_latest_llm','baseline_latest'):
        result[key]=safe_run(result[key]) if result[key] else None
    def raw(path):
        return list({r['run_id']:r for r in read_jsonl(path) if r.get('record_type')=='run'}.values())
    def runs(path):
        return [safe_run(r) for r in raw(path)]
    raw_bounded=raw(logs)
    bounded=[safe_run(r) for r in raw_bounded]
    isolated=runs(paths['smoke']['paths'])
    historical=[]
    audits=list({r.get('fire_id') or str(n):r for n,r in enumerate(read_jsonl(archive/'cron/usage_audit.jsonl')) if r.get('job_id')==JOB}.values())
    for r in audits:
        historical.append(safe_run(dict(run_id=r.get('fire_id'),end=r.get('ts'),start=r.get('ts'),trigger='historical-cron',model=r.get('model'),status='error' if r.get('error') else 'success',input_total_tokens=r.get('prompt_tokens'),output_tokens=r.get('completion_tokens'),duration_seconds=r['duration_ms']/1000 if r.get('duration_ms') is not None else None,errors=[True] if r.get('error') else [])))
    jobs_path=archive/'cron/jobs.json'
    # The retired cron's job record is optional; the frozen archive keeps only its run history.
    jobs=json.loads(jobs_path.read_text())['jobs'] if jobs_path.exists() else []
    job=next((j for j in jobs if j['id']==JOB),{})
    cron={k:job.get(k) for k in ('id','enabled','state','no_agent','last_run_at','last_status','next_run_at')}
    cron['has_error']=bool(job.get('last_error'))
    state_path=paths['coordinator']['path']
    cron['source']=paths['coordinator']['source']
    state=json.loads(state_path.read_text()) if state_path.exists() else {}
    pending=state.get('pending')
    cron['coordinator_available']=state_path.exists()
    cron['window_pending']=(pending is not None) if state_path.exists() else None
    cron['window_phase']=pending.get('phase') if isinstance(pending,dict) else None
    cron['last_success_day']=(state.get('success') or {}).get('day')
    cron['idle_inferred']=bool(state_path.exists() and job.get('no_agent') and job.get('last_status')=='ok' and pending is None)
    cron['job']=next((row for row in launchd_jobs(agentmesh_home) if row['name']=='Summary'),None)
    return {'generated_at':datetime.now(timezone.utc).isoformat(),'refresh_seconds':20,'report':result,'cron':cron,'runs':{'historical':historical,'bounded':bounded,'isolated':isolated},'isolated_aggregate':aggregate(isolated),'failure_reasons':failure_reasons(raw_bounded),'matched_workload_savings_percent':None,'notes':['Dollar cost is not recorded, and these totals do not show savings: the old and new jobs did different amounts of work.','"Waiting events" comes from the last run\'s log, not a live database count.','"Test runs" used a separate copy of the database and never touched real memory.']}


def profile_log_status(agentmesh_home, now=None):
    """One bounded, content-free snapshot; never inspect broker files or settings."""
    from datetime import datetime, timezone
    import math
    import unicodedata
    now = now or datetime.now(timezone.utc)
    result = dict(available=False, availability='missing', truncated=False)
    def text(v):
        return v if isinstance(v, str) and len(v)<=256 and not any(unicodedata.category(c)=='Cc' for c in v) else None
    def count(v): return v if type(v) is int and 0<=v<=2**53-1 else None
    def interval(v):
        try: return v if type(v) in (int,float) and math.isfinite(v) and v>0 else None
        except OverflowError: return None
    def boolean(v): return v if type(v) is bool else None
    def obj(v): return v if isinstance(v, dict) else {}
    def strings(v): return sequence(v, text)
    def sequence(v, project):
        if not isinstance(v, list): return []
        if len(v)>500: result['truncated'] = True
        return [p for item in v[:500] if (p:=project(item)) is not None]
    def fields(v, schema):
        return {k:f(obj(v).get(k)) for k,f in schema.items()}
    def counts(v, allowed): return {k:count(n) for k,n in obj(v).items() if k in allowed}
    def age(v):
        try:
            when = datetime.fromisoformat(v.replace('Z','+00:00'))
            return round((now-when).total_seconds(),1) if when.tzinfo else None
        except (AttributeError, ValueError, OverflowError): return None
    def instant(v): return v if text(v) is not None and 'T' in v and age(v) is not None else None
    def event(v, schema):
        out = fields(v, dict(at=text, outcome=text, **schema)); out['age_seconds'] = age(out['at']); return out
    root = Path(agentmesh_home); path = root/'profile-log-status.json'
    try:
        if any(p.is_symlink() for p in (path, root, *root.parents)): raise ValueError('Invalid status location')
        fd = os.open(path, os.O_RDONLY | getattr(os,'O_NOFOLLOW',0) | getattr(os,'O_NONBLOCK',0))
        with os.fdopen(fd,'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode): raise ValueError('Regular status required')
            raw = stream.read(256*1024+1)
        if len(raw)>256*1024: raise ValueError('Status too large')
        value = json.loads(raw)
        if not isinstance(value,dict) or value.get('format')!='agentmesh-profile-log-status-v1': raise ValueError('Invalid status format')
        result.update(available=True, availability='available', format=value['format'], updated_at=text(value.get('updated_at')))
        result['age_seconds'] = age(result['updated_at'])
        if 'overview' in value:
            result['overview'] = fields(value['overview'], dict(generated_at=text,
                profiles=lambda v:sequence(v,lambda r:fields(r,dict(name=text,scopes=lambda v:sequence(v,lambda r:fields(r,dict(path=text,label=text)))))),
                agents=lambda v:sequence(v,lambda r:fields(r,dict(name=text,read=strings,write=strings,default_write=text,allow_secret=boolean,allow_cross_profile=boolean,allow_import=boolean,allow_pages=boolean,allow_summarize=boolean))),
                devices=lambda v:sequence(v,lambda r:fields(r,dict(device_id=lambda v:(text(v) or '')[:12] or None,revoked=boolean,
                    this_device=boolean,name=lambda v:v if type(v) is str and 1<=len(v)<=64 and v.isprintable() else None,
                    platform=lambda v:v if v in ('macos','windows','linux','ios','android','other') else None,
                    last_seen_at=instant,last_sync_at=instant,
                    last_sync_outcome=lambda v:v if v in ('ok','credentials_missing','credentials_refused','remote_error','error') else None)))))
        if 'workspaces' in value:
            # W4: counts only; the broker never writes titles, accounts or content here.
            result['workspaces'] = fields(value['workspaces'], dict(generated_at=text,
                items=lambda v:sequence(v,lambda r:fields(r,dict(profile=text,
                    members=lambda v:counts(v,('owner','admin','member','contributor')),
                    tasks=lambda v:counts(v,('open','claimed','in_progress','blocked','review','done','abandoned')),
                    **{k:count for k in ('leases_live','leases_expired','conflicts','pending_reviews','over_quota','invite_uses_waiting')})))))
        if 'last_apply' in value:
            result['last_apply'] = event(value['last_apply'],dict(changes=lambda v:counts(v,('unsupported','scope_create','agent_create','agent_remove','agent_widen','agent_narrow','mapping_change','bridge_enable','bridge_disable','bridge_update'))))
        if 'last_sync' in value:
            sync=event(value['last_sync'],dict(**{k:count for k in ('uploaded','downloaded','invalid','errors','outbox','local_objects','remote_objects')},
                interval_s=interval,backend=lambda v:v if v in ('s3','filesystem') else None,last_ok_at=text))
            sync['last_ok_age_seconds']=age(sync['last_ok_at']); seconds=sync['interval_s']
            if sync['age_seconds'] is None: sync['at']=None
            if sync['last_ok_age_seconds'] is None: sync['last_ok_at']=None
            # Unchanged broker outcomes/counts are only rewritten every 600 seconds.
            sync['stale_after_seconds']=max(3*seconds,600+2*seconds) if seconds is not None else None
            sync['stale']=sync['age_seconds']>sync['stale_after_seconds'] if sync['age_seconds'] is not None and seconds is not None else None
            result['last_sync']=sync
    except FileNotFoundError: pass
    except (OSError, ValueError, TypeError, RecursionError): result = dict(available=False, availability='unavailable or malformed', truncated=False)
    return redact(result)
