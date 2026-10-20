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


def overview(state):
    from .apply import enforced_agent
    from .broker.grants import names
    ns=names(state); registry=state.registry()
    return dict(generated_at=timestamp(),
        profiles=[dict(name=t.root.name,scopes=[dict(path=ns[s.scope_id],label=s.default_label.name) for s in t.scopes.values()]) for t in state.trees],
        agents=[dict(name=name,**enforced_agent(state,state.grants[aid])) for name,aid in sorted(state.control.get('agent_names',{}).items()) if aid in state.grants],
        devices=[dict(device_id=d,revoked=d in registry.revocations) for d in sorted(registry._devices)])


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
    text=lambda v:type(v) is str and len(v)<=256 and not any(unicodedata.category(c)=='Cc' for c in v)
    nullable=lambda v:v is None or text(v)
    count=lambda v:type(v) is int and 0<=v<=2**53-1
    boolean=lambda v:type(v) is bool
    def obj(schema): return lambda v:type(v) is dict and set(v)==set(schema) and all(f(v[k]) for k,f in schema.items())
    def seq(f): return lambda v:type(v) is list and all(f(x) for x in v)
    def counts(allowed): return lambda v:type(v) is dict and set(v)<=set(allowed) and all(count(n) for n in v.values())
    schemas={
        'overview':obj(dict(generated_at=text,profiles=seq(obj(dict(name=text,scopes=seq(obj(dict(path=text,label=text)))))),
            agents=seq(obj(dict(name=text,read=seq(text),write=seq(text),default_write=nullable,allow_secret=boolean,allow_cross_profile=boolean,allow_import=boolean))),
            devices=seq(obj(dict(device_id=text,revoked=boolean))))),
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
        result=result or {}
        last=dict(at=timestamp(),outcome=outcome or ('unmapped' if result.get('unmapped') else 'error' if result.get('errors') else 'ok'),
            outcomes=folded(result.get('outcomes'),OUTCOMES),skipped=folded(result.get('skipped'),SKIPPED),unmapped=result.get('unmapped',[]),
            errors=[type(error).__name__] if error is not None else result.get('errors',[]),
            **{k:result.get(k,0) for k in ('busy_retries','timestamp_fallback','secret_labelled')})
        sections=dict(last_catch_up=last)
        try: sections['migration']=migration(database,mapping)
        except Exception: pass
        write(settings.root(),**sections)
    except Exception: pass
