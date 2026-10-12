"""Local conflict review and confirmed recovery. Never publish private reports."""
from dataclasses import asdict
import json
from pathlib import Path

import memory_sync as sync
import packet_versions as versions
import resolution_protocol as protocol
import resolution_store as store
import resolution_apply as apply
import protocol_activation as activation
from signed_packets import no_symlinks,parse,read_local


def _packets(exchange,security=None):
    root=no_symlinks(Path(exchange)/('signed-changes' if security else 'changes'))
    for path in sorted(root.glob('*/*.json')):
        no_symlinks(path)
        p=parse(read_local(path))
        yield path,p


def index_exchange(c,exchange,security):
    for path,p in _packets(exchange,security):
        try: versions.decode_packet(c,p,security=security,folder=path.parent.name,filename=path.name)
        except protocol.MissingEvidence: pass
        c.execute('INSERT OR REPLACE INTO _sync_packet_paths VALUES(?,?,?)',
                  (str(path),p['uuid'],versions.envelope_identity(p)))


def list_conflicts(db,exchange,*,show_content=False):
    """Strictly read-only informational snapshot, never an authorization decision."""
    entries=[]
    with activation.snapshot(db) as c:
        cfg=sync.config(c)
        for diagnostic in c.execute("SELECT path,kind FROM _sync_diagnostics WHERE kind IN ('conflict','pending','invalid') ORDER BY path"):
            path=Path(diagnostic['path'])
            entry=dict(kind=diagnostic['kind'],packet_uuid=None,changes=[],verification='unverified preview')
            # Only read diagnostics within this bound exchange; never follow an
            # arbitrary path planted in the diagnostics table.
            try:
                no_symlinks(path)
                if not path.is_relative_to(no_symlinks(exchange)): raise ValueError('foreign diagnostic path')
                p=parse(read_local(path))
                versions.validate_wire(p,group=cfg['group_id'],folder=path.parent.name)
                if path.name!=p['uuid']+'.json': raise ValueError('filename mismatch')
                if versions.is_new(p): versions._validate_rows(c,p)
                else:
                    legacy={k:p[k] for k in ('group','node','uuid','body')}
                    legacy.update(format=sync.FORMAT,checksum=sync.digest(p['body']));sync._validate(c,legacy)
                entry['packet_uuid']=p['uuid']; entry['node']=p['node']; entry['envelope_digest']=versions.envelope_identity(p)
                for ch in p['body']:
                    current=c.execute('SELECT row,revision FROM _sync_shadow WHERE table_name=? AND key=?',(ch['table'],sync.canonical(ch['key']))).fetchone()
                    item=dict(table=ch['table'],key_digest=sync.digest(ch['key']),
                              mine_revision=current['revision'] if current else None,theirs_revision=ch['revision'],
                              parents=ch.get('parents',[] if ch.get('parent') is None else [ch['parent']]))
                    if show_content: item.update(key=ch['key'],mine=json.loads(current['row']) if current and current['row'] else None,theirs=ch['row'])
                    entry['changes'].append(item)
            except (OSError,ValueError,TypeError,KeyError): entry['verification']='unavailable or invalid preview'
            entries.append(entry)
    return dict(format='agentmesh-conflicts-v1',conflicts=entries,content_shown=show_content)


def _security(bound):
    if bound['scope']['policy']=='required':
        from install_adopt import security_paths
        from signed_packets import Security
        return Security(security_paths(bound['config'],bound['runtime'])[0])
    return None


def preview_resolution(runtime,selection,choices):
    bound=activation.binding(runtime); cfg=bound['config']; security=_security(bound)
    if not selection or len(set(selection))!=len(selection): raise ValueError('select conflict packet UUIDs')
    with activation.snapshot(cfg['database'],writable=True) as c:
        if not activation.emission_enabled(c): raise ValueError('protocol activation required')
        expected={(r['table_name'],r['key']):json.loads(r['row']) for r in c.execute('SELECT * FROM _sync_shadow WHERE row IS NOT NULL')}
        if sync.rows(c)!=expected: raise ValueError('uncaptured local edits')
        index_exchange(c,cfg['exchange'],security); apply._index_local(c,security)
        refs=[]
        for ident in selection:
            row=c.execute('SELECT envelope_digest FROM _sync_packet_evidence WHERE uuid=?',(ident,)).fetchone()
            if not row: raise ValueError('selected packet evidence unavailable')
            refs.append(protocol.Ref(ident,row[0]))
        theirs=apply._graph(c,refs,security)
        # Include local heads and every companion/ancestor they bring.
        while True:
            graph=apply._graph(c,refs,security)
            keys={ch.key for p in graph.values() for ch in p.changes}
            more=[apply._owner(c,head) for head in apply._heads(c,keys).values() if head and not head.startswith('baseline:')]
            new={r.uuid:r for r in [*refs,*more]}
            if set(new)=={r.uuid for r in refs}: break
            refs=list(new.values())
        mode=choices.get('mode')
        if mode not in ('mine','theirs','row','delete'): raise ValueError('invalid resolution choice')
        overrides={protocol.Key(r['table'],tuple(r['key'])):r['row'] for r in choices.get('rows',[])}
        if mode=='row' and not overrides: raise ValueError('explicit rows required')
        by_key={}
        for p in graph.values():
            for ch in p.changes: by_key.setdefault(ch.key,[]).append(ch)
        values={}
        for key,changes in by_key.items():
            parents={parent for ch in changes for parent in ch.parents}
            maxima=[ch for ch in changes if ch.revision not in parents]
            mine=c.execute('SELECT row,revision FROM _sync_shadow WHERE table_name=? AND key=?',(key.table,sync.canonical(list(key.values)))).fetchone()
            if len(maxima)==1: value=maxima[0].row_json  # retain non-conflicting companions
            elif mode=='mine':
                if mine is None: raise ValueError('no local side for selected conflict')
                value=mine['row']
            elif mode=='theirs':
                offered=[ch for ch in maxima if any(ch in p.changes for p in theirs.values())]
                if len(offered)!=1: raise ValueError('multiple remote sides; select explicit row')
                value=offered[0].row_json
            elif mode=='delete': value=None
            else:
                if key not in overrides: raise ValueError('explicit row required for every conflicting key')
                value=sync.canonical(overrides[key]) if overrides[key] is not None else None
            if key in overrides: value=sync.canonical(overrides[key]) if overrides[key] is not None else None
            values[key]=json.loads(value) if value is not None else None
        if not set(overrides)<=set(values): raise ValueError('explicit row outside coverage')
        plan=apply.prepare_resolution(c,refs,values,security=security)
        # Rehearse the full SQL vector on the private snapshot so impossible
        # relational choices are rejected before asking for confirmation.
        import uuid
        apply.commit_resolution(c,plan,security=security,operation_id=str(uuid.uuid4()))
    if activation.binding(runtime)!=bound: raise ValueError('runtime or trust changed')
    value=dict(format='agentmesh-resolution-plan-v1',binding=bound,plan=asdict(plan))
    digest=activation.save_plan(runtime,value)
    return dict(plan_digest=digest,keys=len(values),packets=len(graph),choice=mode)


def resolve(runtime,plan_digest,*,confirm_word,operation_id):
    if confirm_word!='RESOLVE': raise ValueError('RESOLVE confirmation required')
    value=activation.load_plan(runtime,plan_digest,'agentmesh-resolution-plan-v1')
    plan=apply.ResolutionPlan(**value['plan'])
    # Reject an already-stale decision before even creating recovery lock files
    # or stopping a worker. The locked transaction rechecks the same state.
    with activation.snapshot(value['binding']['config']['database']) as c:
        prior=c.execute('SELECT plan_digest FROM _sync_resolution_operations WHERE operation_id=?',(operation_id,)).fetchone()
        if prior:
            if prior[0]!=plan.digest: raise ValueError('operation ID reused')
        elif apply._state(c)!=plan.state_digest or apply._trust(_security(value['binding']))!=plan.trust_digest:
            raise ValueError('stale resolution plan')
    from worker_lifecycle import recovery
    with recovery(runtime) as (cfg,security):
        activation.load_plan(runtime,plan_digest,'agentmesh-resolution-plan-v1')
        with sync.connect(cfg['database']) as c,c:
            c.execute('BEGIN IMMEDIATE')
            index_exchange(c,cfg['exchange'],security)
            plan=apply.ResolutionPlan(**value['plan'])
            packet=apply.commit_resolution(c,plan,security=security,operation_id=operation_id)
            if activation.binding(runtime)!=value['binding']: raise ValueError('runtime or trust changed')
        published=sync.publish(cfg['database'],cfg['exchange'],security=security)
        received=sync.receive(cfg['database'],cfg['exchange'],security=security)
    next_action='retry-publication' if published['unavailable'] else _next_action(received)
    return dict(status='resolved',packet_uuid=packet['uuid'],publish=published,receive=received,next_action=next_action)


def receive_only(runtime):
    from worker_lifecycle import recovery
    with recovery(runtime) as (cfg,security):
        report=sync.receive(cfg['database'],cfg['exchange'],security=security)
    return dict(status='received',receive=report,next_action=_next_action(report))


def _next_action(report):
    return 'review-diagnostics' if any(report[k] for k in ('pending','conflict','invalid','unavailable')) else 'worker-start'
