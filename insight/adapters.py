"""Read-only adapters. Only allowlisted content-free metrics leave this module."""
import json
import os
import stat
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path


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


@contextmanager
def sqlite_ro(path):
    c = sqlite3.connect(Path(path).expanduser().resolve().as_uri() + '?mode=ro', uri=True, timeout=3)
    c.row_factory = sqlite3.Row
    try:
        c.execute('PRAGMA query_only=ON')
        c.execute('BEGIN')
        yield c
    finally:
        c.close()


class MemoryReader:
    def __init__(self, sqlite=None, dsn=None, backend=None):
        self.sqlite = sqlite
        self.dsn = dsn
        self.is_postgres = backend == 'postgres' or bool(dsn)
        self.prefix = 'memory.' if self.is_postgres else ''
        self.label = 'PostgreSQL authority' if self.is_postgres else 'SQLite staging'

    def connect(self):
        if self.is_postgres:
            if not self.dsn:
                raise RuntimeError('PostgreSQL not configured')
            from libpq_reader import Postgres
            return Postgres(self.dsn)
        if not self.sqlite:
            raise RuntimeError('Memory backend not configured')
        return sqlite_ro(self.sqlite)

    def query(self, c, sql, params=()):
        return c.query(sql, params) if self.dsn else [dict(r) for r in c.execute(sql, params)]

    def table(self, kind):
        if kind not in ('items', 'summaries'):
            raise ValueError('Unsupported collection')
        return self.prefix + ('memory_items' if kind == 'items' else 'memory_summaries')

    def fields(self, kind):
        return 'id,kind,scope,scope_key,project,status,confidence,created_at,updated_at' if kind == 'items' else 'id,scope,scope_key,version,created_at'

    def browse(self, kind, args):
        table = self.table(kind)
        if set(args) - {'q','project','kind','status','scope','limit','offset'}:
            raise ValueError('Unknown filter')
        limit, offset = int(args.get('limit', 25)), int(args.get('offset', 0))
        if not 1 <= limit <= 100 or not 0 <= offset <= 1000000:
            raise ValueError('Pagination outside bounds')
        if any(len(str(v)) > 200 for v in args.values()):
            raise ValueError('Filter too long')
        where, params = [], []
        for name in ('project','kind','status','scope'):
            if args.get(name):
                if kind == 'summaries' and name in ('kind','status'):
                    raise ValueError('Unsupported summary filter')
                col = 'scope_key' if kind == 'summaries' and name == 'project' else name
                where.append(col + ' = ?')
                params.append(args[name])
        if args.get('q'):
            where.append("LOWER(content) LIKE LOWER(?) ESCAPE '\\'")
            params.append('%' + args['q'].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
        clause = ' WHERE ' + ' AND '.join(where) if where else ''
        with self.connect() as c:
            total = self.query(c, 'SELECT COUNT(*) AS total FROM ' + table + clause, params)[0]['total']
            rows = self.query(c, 'SELECT ' + self.fields(kind) + ', substr(content,1,2000) AS content FROM ' + table + clause + ' ORDER BY id DESC LIMIT ? OFFSET ?', params + [limit, offset])
        return redact({'available':True,'backend':self.label,'total':total,'limit':limit,'offset':offset,'rows':rows})

    def detail(self, kind, item_id):
        table = self.table(kind)
        if not 0 < item_id < 2**63:
            raise ValueError('Invalid ID')
        with self.connect() as c:
            rows = self.query(c, 'SELECT ' + self.fields(kind) + ', substr(content,1,20000) AS content,metadata FROM ' + table + ' WHERE id=?', [item_id])
            if not rows:
                return {'available':True,'backend':self.label,'row':None,'evidence':[]}
            row = rows[0]
            metadata = row.pop('metadata')
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            if kind == 'items':
                refs = self.query(c, 'SELECT event_id FROM ' + self.prefix + 'memory_sources WHERE memory_id=? ORDER BY event_id LIMIT 21', [item_id])
                ids = [r['event_id'] for r in refs]
            else:
                ids = [*((metadata or {}).get('source_event_ids') or []), *((metadata or {}).get('event_ids') or []), *((metadata or {}).get('batch_event_ids') or [])]
                # Some summaries store evidence references under source_events.
                if not ids:
                    ids = [r.get('event_id', r.get('id')) if isinstance(r, dict) else r for r in (metadata or {}).get('source_events', [])]
            ids = [int(str(v)) for v in ids if str(v).isdigit() and 0 < int(str(v)) < 2**63]
            evidence = []
            if ids:
                evidence = self.query(c, 'SELECT id,source_agent,occurred_at,project,kind,substr(content,1,4000) AS content FROM ' + self.prefix + 'observation_events WHERE id IN (' + ','.join('?' for _ in ids[:20]) + ') ORDER BY id', ids[:20])
        return redact({'available':True,'backend':self.label,'row':row,'evidence':evidence,'evidence_truncated':len(ids)>20,'note':'Explicit evidence excerpts, maximum 20 references. Redaction is best-effort; treat text as untrusted.'})

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


def unavailable(label):
    return {'available':False,'error':label + ' unavailable or not configured'}


PEER_COUNTS = ('memory_items', 'memory_summaries', 'memory_sources', 'observation_events',
               'source_sessions', 'ingestion_cursors', 'ingestion_errors', 'summary_state')
PEER_SYNC = ('received', 'outbox', 'pending', 'conflict', 'invalid')


def security_projection(value, *, legacy=False):
    """Allowlisted telemetry only; status JSON itself is not authenticated proof."""
    from datetime import datetime, timezone
    import uuid
    value = value if isinstance(value, dict) and value.get('format') == 'agentmesh-security-status-v1' else {}
    def choice(key, allowed, default='unknown'):
        v = value.get(key)
        return v if isinstance(v, str) and v in allowed else default
    def timestamp(v):
        try:
            if not isinstance(v, str) or len(v) > 64: return None
            when = datetime.fromisoformat(v.replace('Z', '+00:00'))
            return when.astimezone(timezone.utc).isoformat() if when.tzinfo else None
        except (ValueError, OverflowError): return None
    def count(v):
        return v if type(v) is int and 0 <= v <= 2**53-1 else None
    label = value.get('display_name')
    label = label if isinstance(label, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}', label) else None
    verification = value.get('verification')
    verification = verification if isinstance(verification, dict) else {}
    packet = value.get('roundtrip_packet_uuid')
    try: packet = packet if isinstance(packet, str) and str(uuid.UUID(packet)) == packet else None
    except ValueError: packet = None
    verified_at = timestamp(value.get('roundtrip_verified_at'))
    roundtrip = 'reported verified' if value.get('roundtrip') == 'verified' and packet and verified_at else 'pending' if value.get('roundtrip') == 'pending' else 'unknown'
    return {'policy': choice('policy', ('legacy', 'required'), 'legacy' if legacy else 'unknown'),
            'display_name': label, 'pairing': choice('pairing', ('pending', 'approved', 'revoked', 'unknown')),
            'wizard_step': choice('wizard_step', ('prerequisites', 'identity', 'pairing', 'roundtrip', 'activation', 'active')),
            'next_action': choice('next_action', ('check_prerequisites', 'create_or_reuse_local_identity', 'confirm_peer_fingerprint_out_of_band', 'send_and_receive_signed_probe', 'confirm_coordinated_legacy_boundary', 'restart_worker_with_same_security_directory', 'recover_identity_or_receipt')),
            'roundtrip': roundtrip, 'roundtrip_packet_uuid': packet if roundtrip == 'reported verified' else None,
            'roundtrip_verified_at': verified_at if roundtrip == 'reported verified' else None,
            'verification': {'attempts':count(verification.get('attempts')), 'failed_attempts':count(verification.get('failed_attempts')),
                             'last_success_at':timestamp(verification.get('last_success_at')), 'last_failure_at':timestamp(verification.get('last_failure_at'))},
            'source': 'reported by unsigned status telemetry'}


def local_security(c):
    required = bool(c.execute("SELECT 1 FROM sqlite_master WHERE name='_sync_security'").fetchone())
    value: dict = {'format':'agentmesh-security-status-v1','policy':'required' if required else 'legacy'}
    if c.execute("SELECT 1 FROM sqlite_master WHERE name='_sync_verification'").fetchone():
        record = c.execute('SELECT attempts,failed_attempts,last_success_at,last_failure_at FROM _sync_verification WHERE id=1').fetchone()
        if record: value['verification'] = dict(record)
    result = security_projection(value)
    result['source'] = 'local read-only SQLite policy and verification counters'
    return result


def peer_status(home, now=None):
    """Fixed peer files only. ACKs are completed cycles, never live connectivity."""
    from datetime import datetime, timezone
    now = now or datetime.now(timezone.utc)
    root = Path(home)/'omp-memory/sync/status'
    rows = []
    for node, label in (('mac', 'Mac'), ('windows', 'Windows')):
        row = {'peer':label, 'available':False, 'availability':'missing',
               'ack_at':None, 'age_seconds':None, 'freshness':'unknown', 'has_error':None, 'partial':False,
               'counts':dict.fromkeys(PEER_COUNTS), 'sync':dict.fromkeys(PEER_SYNC),
               'security':security_projection(None),
               'cycle':dict.fromkeys(('applied', 'published', 'pending', 'conflict', 'invalid',
                                      'summary_applied', 'summary_published'))}
        rows.append(row)
        path = root/(node+'.json')
        try:
            # No symlinks or files outside the configured home; bound read before JSON parsing.
            if path.is_symlink() or path.resolve().parent != root.resolve() or not path.resolve().is_relative_to(Path(home).resolve()):
                raise ValueError('Invalid status location')
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(fd, 'rb') as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise ValueError('Regular status file required')
                raw = source.read(65537)
            if len(raw) > 65536:
                raise ValueError('Status too large')
            report = json.loads(raw)
            if not isinstance(report, dict) or report.get('format') != 'agentmesh-status-v1' or report.get('node') != node:
                raise ValueError('Invalid status format')
            row.update(available=True, availability='available')
            row['security'] = security_projection(report.get('security'), legacy='security' not in report)
            value = report.get('updated_at')
            try:
                if not isinstance(value, str) or len(value) > 64:
                    raise ValueError('Invalid timestamp')
                when = datetime.fromisoformat(value.replace('Z', '+00:00'))
                if when.tzinfo is None:
                    raise ValueError('Offset required')
                row['ack_at'] = when.astimezone(timezone.utc).isoformat()
                row['age_seconds'] = round((now - when).total_seconds(), 1)
                row['freshness'] = ('future timestamp' if row['age_seconds'] < 0 else
                                    'fresh' if row['age_seconds'] <= 300 else 'stale')
            except (TypeError, ValueError, OverflowError):
                pass
            def obj(value):
                return value if isinstance(value, dict) else {}
            def counter(value):
                return value if type(value) is int and 0 <= value <= 2**53-1 else None
            for key, fields in (('counts', PEER_COUNTS), ('sync', PEER_SYNC)):
                row[key] = {field:counter(obj(report.get(key)).get(field)) for field in fields}
            cycle = obj(report.get('cycle'))
            receive, publish = obj(cycle.get('receive')), obj(cycle.get('publish'))
            row['cycle'].update({key:counter(receive.get(key)) for key in ('applied', 'pending', 'conflict', 'invalid')})
            row['cycle']['published'] = counter(publish.get('published'))
            workflow = obj(report.get('workflow'))
            summary_cycle = obj(workflow.get('summary_sync'))
            row['cycle']['summary_applied'] = counter(obj(summary_cycle.get('receive')).get('applied'))
            row['cycle']['summary_published'] = counter(obj(summary_cycle.get('publish')).get('published'))
            # Project presence only: never send workflow statuses, error messages or payloads.
            ingestion, summary = obj(workflow.get('ingestion')), obj(workflow.get('summary'))
            row['has_error'] = bool(report.get('error') or obj(report.get('postgres_mirror')).get('error') or
                                    ingestion.get('error') or ingestion.get('errors') or summary.get('error') or
                                    summary.get('status') in ('stale', 'blocked', 'failed') or
                                    receive.get('unavailable') or publish.get('unavailable') or
                                    obj(summary_cycle.get('receive')).get('unavailable') or
                                    obj(summary_cycle.get('publish')).get('unavailable') or
                                    row['sync']['invalid'] or row['sync']['conflict'] or
                                    row['counts']['ingestion_errors'] or row['cycle']['invalid'] or row['cycle']['conflict'])
            row['partial'] = row['ack_at'] is None or any(
                report.get(key) is not None and not isinstance(report.get(key), dict)
                for key in ('workflow', 'postgres_mirror')) or any(
                workflow.get(key) is not None and not isinstance(workflow.get(key), dict)
                for key in ('ingestion', 'summary', 'summary_sync')) or any(
                value is None for group in ('counts', 'sync') for value in row[group].values()) or any(
                row['cycle'][key] is None for key in ('applied', 'published', 'pending', 'conflict', 'invalid'))
            if row['partial'] and not row['has_error']:
                row['has_error'] = None
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, RecursionError):
            row['availability'] = 'unavailable or malformed'
    return {'rows':rows, 'transport':'unknown', 'freshness_threshold_seconds':300,
            'note':'Completed-cycle ACKs only, not a live process, Syncthing connection or proof of full convergence. Fresh means age at most 5 minutes; stale does not mean offline. Missing or invalid values remain unknown.'}


def operations(home, reader, sync_db, transport=None):
    from datetime import datetime, timezone
    result: dict = {'generated_at':datetime.now(timezone.utc).isoformat(),'refresh_seconds':20,'memory_backend':reader.label}
    try:
        with reader.connect() as c:
            rows = reader.query(c, 'SELECT source_agent,SUM(sessions) AS sessions,SUM(events) AS events,MAX(observed_at) AS observed_at FROM (SELECT source_agent,COUNT(*) AS sessions,0 AS events,MAX(last_seen_at) AS observed_at FROM ' + reader.prefix + 'source_sessions GROUP BY source_agent UNION ALL SELECT source_agent,0 AS sessions,COUNT(*) AS events,MAX(occurred_at) AS observed_at FROM ' + reader.prefix + 'observation_events GROUP BY source_agent) observed GROUP BY source_agent ORDER BY source_agent')
            for row in rows:
                row['state'] = observed_state(row['observed_at'])
        result['agents'] = {'available':True,'backend':reader.label,'rows':rows,'note':'Ingested session and event metadata. Recent observation is not proof of a running process. No heartbeat probe configured.'}
    except Exception:
        result['agents'] = unavailable('Agent source metadata')
    try:
        with sqlite_ro(home/'state.db') as c:
            rows = [dict(r) for r in c.execute('SELECT source,COUNT(*) AS sessions,MAX(last_activity_at) AS observed_epoch,MAX(ended_at) AS latest_ended_epoch FROM sessions GROUP BY source ORDER BY source')]
        # Do not label open-ended historical sessions as live agents.
        result['hermes_sessions'] = {'available':True,'rows':rows,'note':'Session observations only, not process liveness.'}
    except Exception:
        result['hermes_sessions'] = unavailable('Hermes session metadata')
    try:
        jobs = json.loads((home/'cron/jobs.json').read_text())['jobs']
        selected = []
        for label, job_id in (('Ingest','eabd6e41c22d'),('Summary','6fcf5646d34d')):
            job = next((j for j in jobs if j.get('id') == job_id), None)
            row = {'name':label,'id':job_id,'available':job is not None}
            if job:
                row.update({k:job.get(k) for k in ('enabled','state','no_agent','last_status','last_run_at','next_run_at')})
                row['has_error'] = bool(job.get('last_error'))
                row['observation_state'] = observed_state(job.get('last_run_at'))
            selected.append(row)
        pipeline = {'available':True,'jobs':selected,'note':'Exact known cron jobs, observed configuration and last execution. No prompts, commands or error payloads exposed.'}
        state_path = home/'omp-memory/.bounded_digest_state.json'
        if state_path.exists():
            state = json.loads(state_path.read_text())
            pending = state.get('pending')
            pipeline['coordinator'] = {'pending':pending is not None,'phase':pending.get('phase') if isinstance(pending, dict) else None,'success_day':(state.get('success') or {}).get('day')}
        result['pipeline'] = pipeline
    except Exception:
        result['pipeline'] = unavailable('Pipeline cron metadata')
    try:
        with reader.connect() as c:
            result['ingest'] = {'available':True,'backend':reader.label,'rows':reader.query(c, 'SELECT source_agent,COUNT(*) AS events,MAX(occurred_at) AS latest_event_at FROM ' + reader.prefix + 'observation_events GROUP BY source_agent ORDER BY source_agent'),'cursors':reader.query(c, 'SELECT COUNT(*) AS count,MAX(updated_at) AS updated_at FROM ' + reader.prefix + 'ingestion_cursors')[0],'errors':reader.query(c, 'SELECT error_type,COUNT(*) AS count,MAX(created_at) AS latest_at FROM ' + reader.prefix + 'ingestion_errors GROUP BY error_type'),'summary_state':reader.query(c, 'SELECT consumer,last_event_id,updated_at FROM ' + reader.prefix + 'summary_state ORDER BY consumer LIMIT 30')}
    except Exception:
        result['ingest'] = unavailable('Ingest counters')
    try:
        if not sync_db:
            raise RuntimeError('No staging database')
        with sqlite_ro(sync_db) as c:
            config = c.execute('SELECT node FROM _sync_config LIMIT 1').fetchone()
            security = local_security(c)
            sync = {'available':True,'backend':('SQLite authority' if reader.label == 'SQLite authority' else 'SQLite staging'),'node':config['node'] if config else None,'transport':'unknown','receipts':c.execute('SELECT COUNT(*) FROM _sync_receipts').fetchone()[0],'outbox': [dict(r) for r in c.execute('SELECT published,COUNT(*) AS packets FROM _sync_outbox GROUP BY published')],'diagnostics':[dict(r) for r in c.execute('SELECT kind,COUNT(*) AS count FROM _sync_diagnostics GROUP BY kind')],'workers':[],'note':'Receipts confirm local packet import, not peer convergence. Worker timestamps do not prove transport connectivity.'}
            try:
                sync['workers'] = [dict(r) for r in c.execute('SELECT consumer,last_run,lease_until FROM _agentmesh_worker_state ORDER BY consumer LIMIT 30')]
                sync['worker_metadata_available'] = True
            except sqlite3.OperationalError:
                sync['worker_metadata_available'] = False
        result['sync'] = sync
        result['sync']['security'] = security
    except Exception:
        result['sync'] = dict(unavailable('SQLite sync metadata'), transport='unknown')
    result['sync']['peers'] = peer_status(home)
    if 'security' not in result['sync']: result['sync']['security'] = security_projection(None)
    if transport is None:
        from syncthing_transport import observe
        transport = observe(home)
    result['sync']['transport_status'] = transport
    result['sync']['transport'] = transport['status']
    result['sync']['peers']['transport'] = transport['status']
    return redact(result)


METRICS = ('llm_api_calls','input_total_tokens','input_uncached_tokens','cache_read_tokens','output_tokens','reasoning_tokens','actual_cost_usd','duration_seconds','events_processed','context_max_input_tokens','raw_chars','submitted_chars','accepted_items','rejected_items','upserted_items','exporter_duration_seconds','events_pending_before','events_pending_after','cursor_before','cursor_after','batch_count','fallbacks','retries')
LABELS = ('run_id','status','trigger','model','provider','start','end')
def safe_run(row):
    out = {key: row.get(key) for key in LABELS + METRICS}
    out['error_count'] = len(row.get('errors') or [])
    return out

JOB='6fcf5646d34d'

def metrics(home):
    from datetime import datetime, timezone
    from metrics import report, read_jsonl, aggregate
    root=home/'omp-memory'
    result=report(home/'cron/usage_audit.jsonl',home/'state.db',root/'summary-metrics.jsonl')
    for key in ('new_latest','new_latest_llm','baseline_latest'):
        result[key]=safe_run(result[key]) if result[key] else None
    def runs(path):
        return list({r['run_id']:safe_run(r) for r in read_jsonl(path) if r.get('record_type')=='run'}.values())
    bounded=runs(root/'summary-metrics.jsonl')
    isolated=runs(root/'summary-smoke-test-metrics.jsonl')
    historical=[]
    audits=list({r.get('fire_id') or str(n):r for n,r in enumerate(read_jsonl(home/'cron/usage_audit.jsonl')) if r.get('job_id')==JOB}.values())
    for r in audits:
        historical.append(safe_run(dict(run_id=r.get('fire_id'),end=r.get('ts'),start=r.get('ts'),trigger='historical-cron',model=r.get('model'),status='error' if r.get('error') else 'success',input_total_tokens=r.get('prompt_tokens'),output_tokens=r.get('completion_tokens'),duration_seconds=r['duration_ms']/1000 if r.get('duration_ms') is not None else None,errors=[True] if r.get('error') else [])))
    jobs=json.loads((home/'cron/jobs.json').read_text())['jobs']
    job=next((j for j in jobs if j['id']==JOB),{})
    cron={k:job.get(k) for k in ('id','enabled','state','no_agent','last_run_at','last_status','next_run_at')}
    cron['has_error']=bool(job.get('last_error'))
    state_path=root/'.bounded_digest_state.json'
    state=json.loads(state_path.read_text()) if state_path.exists() else {}
    pending=state.get('pending')
    cron['coordinator_available']=state_path.exists()
    cron['window_pending']=(pending is not None) if state_path.exists() else None
    cron['window_phase']=pending.get('phase') if isinstance(pending,dict) else None
    cron['last_success_day']=(state.get('success') or {}).get('day')
    cron['idle_inferred']=bool(state_path.exists() and job.get('no_agent') and job.get('last_status')=='ok' and pending is None)
    return {'generated_at':datetime.now(timezone.utc).isoformat(),'refresh_seconds':20,'report':result,'cron':cron,'runs':{'historical':historical,'bounded':bounded,'isolated':isolated},'isolated_aggregate':aggregate(isolated),'matched_workload_savings_percent':None,'notes':['No matched-workload savings or quality claim. Actual dollar cost unknown.','Backlog is last observed in run metrics, not a live database count.','Idle inferred from completed window + successful no-agent tick; no new LLM calls are made.','Isolated DB smoke is separate from production-source logs. Historical per-run uncached/cache/calls are unknown in chart; matched session totals remain in coverage table.']}
