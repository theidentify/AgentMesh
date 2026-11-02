"""Summarizer writes through the broker (SQLite retirement R2).

memory-sync's bounded digest calls this through `agentmesh-log summary related|write` with JSON on stdin, so no
transcript text or memory content is ever on a command line. The summarizer agent needs allow_summarize: `heads`
gives the current event per memory key in its own write scopes, and each item becomes a `remember` (new key) or a
`revise` of that head. A status other than active hides the key, like an imported row that is no longer active.
Routing is the migration mapping (project -> scope), so new memories land where the imported ones did.
"""
import hashlib
from .encoding import typed

GENERATOR='agentmesh-bounded-v1'
SUMMARY_PREFIX='summary/'  # '/' is outside the model's key alphabet, so a project summary never collides with an item
MARKER_START='<!-- BOUNDED-CURRENT:START -->'
MARKER_END='<!-- BOUNDED-CURRENT:END -->'
RELATED=('memory_key','kind','scope','scope_key','project','content','status','confidence')
SCOPES=('global','project','task','session')
STATUSES=('active','superseded','resolved','deleted')


class Stale(Exception): pass


def checked(response):
    if 'error' in response:
        from .broker.client import PresenceRequired
        if response['error']=='presence_required': raise PresenceRequired(response)
        raise ValueError(response['error'])
    return response


def target(mapping, scope, project):
    """The profile-log scope for an item, or None when the mapping skips it; an unmapped project raises."""
    value=mapping.target(dict(scope=scope,project=project))
    return None if value=='skip' else value


class Heads:
    """Every keyed head of the scopes touched in one window, read once and refreshed after writes."""
    def __init__(self, client):
        self.client,self.cache=client,{}

    def scope(self, name):
        if name not in self.cache:
            self.cache[name]=checked(self.client.request(dict(op='heads',scope=name)))['heads']
        return self.cache[name]

    def drop(self, name): self.cache.pop(name,None)


def related(client, mapping, project, sessions, heads=None):
    """The SQLite related context, rebuilt from heads: the project's keyed items and every global one.

    Session items count only for the window's own sessions. Project summaries are not items."""
    heads=heads or Heads(client)
    scopes={s for s in (target(mapping,'project',project),target(mapping,'global',None)) if s}
    rows={}
    for name in sorted(scopes):
        for key,head in heads.scope(name).items():
            if key.startswith(SUMMARY_PREFIX) or head['item_scope'] not in SCOPES: continue
            if not (head['project']==project or head['item_scope']=='global'): continue
            if head['item_scope']=='session' and head['scope_key'] not in sessions: continue
            rows[key]=dict(memory_key=key,kind=head['kind'],scope=head['item_scope'],scope_key=head['scope_key'],
                           project=head['project'],content=head['content'],status=head['status'],confidence=head['confidence'])
    return [rows[k] for k in sorted(rows)]


def digest(value):
    return hashlib.sha256(typed(value)).hexdigest()


def body(item, provenance):
    metadata=dict(memory_key=item['memory_key'],status=item['status'],scope=item['scope'],scope_key=item['scope_key'],
                  generator=GENERATOR,**provenance)
    if 'evidence' in item: metadata['evidence']=item['evidence']
    return dict(kind=item['kind'],content=item['content'],project=item['project'],
                task_ref=item['scope_key'] if item['scope']=='task' else None,confidence=item['confidence'],metadata=metadata)


def same(head, item):
    return all(head[a]==item[b] for a,b in (('kind','kind'),('content','content'),('project','project'),
               ('confidence','confidence'),('status','status'),('item_scope','scope'),('scope_key','scope_key')))


def upsert(client, heads, name, item, provenance):
    """remember a new key, revise a changed one, leave an unchanged one alone. Returns (outcome, id)."""
    old=heads.scope(name).get(item['memory_key'])
    if old is not None and same(old,item): return 'unchanged',old['id']
    response=checked(client.write(body(item,provenance),scope=name,revises=None if old is None else old['id'],expected_scope=name))
    heads.drop(name)
    return ('revised' if old else 'created'),response['id']


def summary(heads, mapping, project):
    """The project's summary item: the current active items of the project, in key order, inside the bounded markers."""
    name=target(mapping,'project',project)
    if name is None: return None,None
    current=heads.scope(name)
    old=current.get(SUMMARY_PREFIX+project)
    base=old['content'].split(MARKER_START)[0].rstrip() if old else ''
    lines=[f"- [{h['kind']}] {k}: {h['content']}" for k,h in sorted(current.items())
           if not k.startswith(SUMMARY_PREFIX) and h['project']==project and h['status']=='active']
    content='\n'.join([base,'',MARKER_START,'## Current durable memory',*lines,MARKER_END])
    return name,dict(memory_key=SUMMARY_PREFIX+project,kind='summary',scope='project',scope_key=project,project=project,
                     content=content,status='active',confidence=1.0)


def write(client, mapping, request):
    """Apply one validated window: the same checks as bounded_digest.apply, against heads instead of memory_items.

    request: {project, sessions, related_digest, items, provenance}. The related context is read again first; if it
    changed since the model saw it the window is stale and nothing is written."""
    project,sessions=request['project'],request['sessions']
    heads=Heads(client)
    context=related(client,mapping,project,sessions,heads)
    if digest(context)!=request['related_digest']: raise Stale()
    known={r['memory_key'] for r in context}
    written,rejected,skipped=[],[],0
    for item in request['items']:
        name=target(mapping,item['scope'],item['project'])
        if name is None: skipped+=1; continue
        old=heads.scope(name).get(item['memory_key'])
        if old is not None and item['memory_key'] not in known:
            rejected.append(dict(memory_key=item['memory_key'],reason='stable key exists outside submitted context')); continue
        if old is not None and item['status']=='deleted':
            rejected.append(dict(memory_key=item['memory_key'],reason='deletion requires confirmation')); continue
        outcome,ident=upsert(client,heads,name,item,request['provenance'])
        written.append(dict(memory_key=item['memory_key'],outcome=outcome,id=ident))
    summaries=[]
    applied=[i for i in request['items'] if any(w['memory_key']==i['memory_key'] for w in written)]
    projects=({project}|{i['scope_key'] for i in applied if i['scope']=='project'}) if project and applied else set()
    for name_project in sorted(projects):
        name,item=summary(heads,mapping,name_project)
        if item is None: continue
        outcome,ident=upsert(client,heads,name,item,request['provenance'])
        summaries.append(dict(memory_key=item['memory_key'],outcome=outcome,id=ident))
    return dict(status='committed',written=written,rejected=rejected,skipped=skipped,summaries=summaries)


def valid_request(value, action):
    """Shape check for the stdin request; content rules are the protocol's, already applied by memory-sync."""
    if type(value) is not dict: return False
    fields={'project','sessions'} if action=='related' else {'project','sessions','related_digest','items','provenance'}
    if set(value)!=fields: return False
    if value['project'] is not None and (type(value['project']) is not str or not value['project']): return False
    if type(value['sessions']) is not list or any(type(s) is not str for s in value['sessions']): return False
    if action=='related': return True
    if type(value['related_digest']) is not str or type(value['provenance']) is not dict: return False
    if type(value['items']) is not list or len(value['items'])>200: return False
    for item in value['items']:
        if type(item) is not dict or not set(RELATED)<=set(item) or set(item)-set(RELATED)-{'evidence','source_event_ids'}: return False
        if type(item['memory_key']) is not str or not 1<=len(item['memory_key'])<=512 or item['memory_key'].startswith(SUMMARY_PREFIX): return False
        if item['scope'] not in SCOPES or item['status'] not in STATUSES: return False
        if any(type(item[k]) is not str for k in ('kind','content')): return False
        if any(item[k] is not None and type(item[k]) is not str for k in ('scope_key','project')): return False
        if type(item['confidence']) not in (int,float) or not 0<=item['confidence']<=1: return False
    return len({i['memory_key'] for i in value['items']})==len(value['items'])
