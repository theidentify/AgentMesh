"""Best-effort content-free status for Insight; never an authority source."""
from collections import Counter
from datetime import datetime, timezone
from . import settings
from .broker.keystore import atomic_write
from .encoding import parse, wire

FORMAT = 'agentmesh-profile-log-status-v1'
OUTCOMES = ('created','revised','retired','unchanged','other')
SKIPPED = ('mapping_skip','not_recalled','superseded','resolved','deleted','other')


def folded(value, allowed):
    """Unknown keys count as 'other' so a new reason never invalidates the whole snapshot."""
    out=Counter()
    for k,n in (value or {}).items(): out[k if k in allowed else 'other']+=n
    return dict(out)


def timestamp(): return datetime.now(timezone.utc).isoformat()


def overview(state, *, heartbeats=None, last_sync=None):
    from .apply import enforced_agent
    from .broker.grants import names
    from .heartbeats import local, iso
    ns=names(state); registry=state.registry(); rows=[]
    for d in sorted(registry._devices):
        own=d==state.device.device_id; revoked=d in registry.revocations
        value=local(d,last_sync) if own else (heartbeats or {}).get(d,{}) if not revoked else {}
        rows.append(dict(device_id=d,revoked=revoked,this_device=own,name=value.get('name'),platform=value.get('platform'),
            last_seen_at=timestamp() if own else iso(value.get('issued_at')),last_sync_at=iso(value.get('last_sync_at')),
            last_sync_outcome=value.get('last_sync_outcome')))
        if own and last_sync: rows[-1]['last_sync_at']=last_sync['at']
    return dict(generated_at=timestamp(),
        profiles=[dict(name=t.root.name,scopes=[dict(path=ns[s.scope_id],label=s.default_label.name) for s in t.scopes.values()]) for t in state.trees],
        agents=[dict(name=name,**enforced_agent(state,state.grants[aid])) for name,aid in sorted(state.control.get('agent_names',{}).items()) if aid in state.grants],
        devices=rows)


def migration(database, mapping):
    from .migrate.mapping import plan, project_targets
    from .migrate.source import rows
    value=plan(database,mapping)
    return dict(generated_at=timestamp(),projects=project_targets(value['projects'],rows(database),mapping),unmapped=value['unmapped'])


def write(base, **sections):
    """Preserve other writers' sections only from a valid status document."""
    try:
        settings.safe(base,directory=True,missing=True)
        path=base/'profile-log-status.json'; settings.safe(path,missing=True)
        try:
            old=parse(settings.read_raw(path))
            if not valid(old): old={}
        except Exception: old={}
        value={**old,**sections,'format':FORMAT,'updated_at':timestamp()}
        if not valid(value): return
        atomic_write(path,wire(value))
    except Exception: pass


def valid(value):
    """Strict structural allow-list also rejects injected fields in retained sections."""
    import unicodedata
    from .broker.service import sync_status_valid
    text=lambda v:type(v) is str and len(v)<=256 and not any(unicodedata.category(c)=='Cc' for c in v)
    nullable=lambda v:v is None or text(v)
    count=lambda v:type(v) is int and 0<=v<=2**53-1
    boolean=lambda v:type(v) is bool
    def obj(schema): return lambda v:type(v) is dict and set(v)==set(schema) and all(f(v[k]) for k,f in schema.items())
    def seq(f): return lambda v:type(v) is list and all(f(x) for x in v)
    def counts(allowed): return lambda v:type(v) is dict and set(v)<=set(allowed) and all(count(n) for n in v.values())
    from .heartbeats import name_valid, iso_valid, PLATFORMS, OUTCOMES as SYNC_OUTCOMES
    schemas={
        'last_sync':lambda v:type(v) is dict and 'last_ok_at' in v and nullable(v['last_ok_at']) and sync_status_valid({k:n for k,n in v.items() if k!='last_ok_at'}),
        'overview':obj(dict(generated_at=text,profiles=seq(obj(dict(name=text,scopes=seq(obj(dict(path=text,label=text)))))),
            agents=seq(obj(dict(name=text,read=seq(text),write=seq(text),default_write=nullable,allow_secret=boolean,allow_cross_profile=boolean,allow_import=boolean))),
            # Legacy {device_id,revoked} rows stay valid so an upgrade or rollback never drops other sections.
            devices=seq(lambda v:obj(dict(device_id=text,revoked=boolean))(v) or obj(dict(device_id=text,revoked=boolean,this_device=boolean,
                name=lambda v:v is None or name_valid(v),platform=lambda v:v is None or v in PLATFORMS,
                last_seen_at=lambda v:v is None or iso_valid(v),last_sync_at=lambda v:v is None or iso_valid(v),
                last_sync_outcome=lambda v:v is None or v in SYNC_OUTCOMES))(v)))),
        'migration':obj(dict(generated_at=text,projects=seq(obj(dict(project=nullable,items=count,summaries=count,target=text))),unmapped=seq(text))),
        'last_apply':obj(dict(at=text,outcome=text,changes=counts(('unsupported','scope_create','agent_create','agent_remove','agent_widen','agent_narrow','mapping_change','bridge_enable','bridge_disable','bridge_update')))),
        'last_catch_up':obj(dict(at=text,outcome=text,outcomes=counts(OUTCOMES),skipped=counts(SKIPPED),
            unmapped=seq(text),errors=seq(lambda v:text(v) and v.isidentifier()),busy_retries=count,timestamp_fallback=count,secret_labelled=count))}
    return (type(value) is dict and value.get('format')==FORMAT and text(value.get('updated_at')) and
            not set(value)-{'format','updated_at',*schemas} and all(schemas[k](v) for k,v in value.items() if k in schemas))


def applied(base, outcome, changes, state=None, value=None, mapping=None):
    try:
        sections=dict(last_apply=dict(at=timestamp(),outcome=outcome,changes=dict(Counter(c['kind'] for c in changes))))
        if state is not None: sections['overview']=overview(state)
        database=(value or {}).get('bridge',{}).get('database')
        if database and mapping:
            from .migrate.mapping import Mapping
            try: sections['migration']=migration(database,Mapping(mapping))
            except Exception: pass
        write(base,**sections)
    except Exception: pass


def caught_up(database, mapping, result=None, *, outcome=None, error=None):
    try:
        from .migrate.run import error_code
        result=result or {}
        last=dict(at=timestamp(),outcome=outcome or ('unmapped' if result.get('unmapped') else 'error' if result.get('errors') else 'ok'),
            outcomes=folded(result.get('outcomes'),OUTCOMES),skipped=folded(result.get('skipped'),SKIPPED),unmapped=result.get('unmapped',[]),
            errors=[error_code(error)] if error is not None else result.get('errors',[]),
            **{k:result.get(k,0) for k in ('busy_retries','timestamp_fallback','secret_labelled')})
        sections=dict(last_catch_up=last)
        try: sections['migration']=migration(database,mapping)
        except Exception: pass
        write(settings.root(),**sections)
    except Exception: pass


def synced(base, row):
    try:
        last_ok_at=None
        if row['outcome']=='ok': last_ok_at=row['at']
        else:
            try:
                old=parse(settings.read_raw(base/'profile-log-status.json'))
                if valid(old): last_ok_at=old.get('last_sync',{}).get('last_ok_at')
            except Exception: pass
        write(base,last_sync={**row,'last_ok_at':last_ok_at})
    except Exception: pass
