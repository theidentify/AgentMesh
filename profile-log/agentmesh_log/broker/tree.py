"""Read-only memory tree over an agent's visible rows (memory tree T1: derived from metadata, no pages).

Paths are scope names, then optional filters: [projects/<p>/][tasks/<T>/][<kind folder>/][<ULID>].
Every node is built from rows already filtered by the agent's grant, so a scope the agent cannot read
never appears, not even as a name or a count.
"""
import re
from urllib.parse import quote, unquote
from ..ulid import ALPHABET

KIND_FOLDERS = {'decision':'decisions','constraint':'constraints','procedure':'procedures',
    'preference':'preferences','open_loop':'open-loops','fact':'facts','summary':'summaries'}
FOLDER_KINDS = {v:k for k,v in KIND_FOLDERS.items()}
ULID = re.compile('['+ALPHABET+']{26}')
MAX_PATH, MAX_CHILDREN, MAX_NESTED, SUMMARY_CHARS = 512, 40, 10, 120


def split(path):
    if type(path) is not str or len(path)>MAX_PATH: raise ValueError('invalid_request')
    inner=path.strip('/')
    parts=inner.split('/') if inner else []
    if any(p in ('','.','..') for p in parts): raise ValueError('invalid_request')
    return parts


def _under(name, prefix): return not prefix or name==prefix or name.startswith(prefix+'/')


def resolve(parts, scopes):
    """Return (scope, filters) for a readable scope, or (None, prefix) for a synthesized parent."""
    for i in range(len(parts),0,-1):
        name='/'.join(parts[:i])
        if name in scopes: return name,_filters(parts[i:])
    prefix='/'.join(parts)
    if not any(_under(s,prefix) for s in scopes): raise ValueError('denied')
    return None,prefix


def _filters(rest):
    f=dict(project=None,task=None,kind=None,id=None,pending=None); i=0
    while i<len(rest):
        part,last=rest[i],i==len(rest)-1
        if f['id'] is not None or f['pending'] is not None: raise ValueError('denied')
        if ULID.fullmatch(part) and last: f['id']=part
        elif f['kind'] is not None: raise ValueError('denied')
        elif part in FOLDER_KINDS: f['kind']=FOLDER_KINDS[part]
        elif part in ('projects','tasks') and f[part[:-1]] is None:
            if last: f['pending']=part[:-1]
            else: f[part[:-1]]=unquote(rest[i+1]); i+=1
        else: raise ValueError('denied')
        i+=1
    return f


def _match(row, scope, f):
    return (row['scope']==scope and (f['project'] is None or row.get('project')==f['project'])
        and (f['task'] is None or (row.get('task_ref') or '').upper()==f['task'].upper())
        and (f['kind'] is None or row['kind']==f['kind']))


def _newest(rows): return sorted(rows,key=lambda r:(r['updated_at'],r['id']),reverse=True)


def _line(row):
    if row['label']=='secret': return None
    text=next((l.strip() for l in row['content'].splitlines() if l.strip()),'')
    return text if len(text)<=SUMMARY_CHARS else text[:SUMMARY_CHARS-1]+'…'


def _dir(name, rows, kind='dir'):
    rows=_newest(rows)
    return dict(name=name+'/',type=kind,count=len(rows),updated=rows[0]['updated_at'] if rows else None,
        summary=next((s for s in map(_line,rows) if s),None))


def _event(row):
    return dict(name=row['id'],type='event',kind=row['kind'],label=row['label'],updated=row['updated_at'],summary=_line(row))


def _next(prefix, scopes):
    start=len(prefix)+1 if prefix else 0
    return sorted({s[start:].split('/')[0] for s in scopes if s!=prefix and _under(s,prefix)})


def children(parts, rows, scopes):
    scope,f=resolve(parts,scopes)
    if scope is None:
        return [_dir(n,[r for r in rows if _under(r['scope'],'/'.join(parts+[n]))],'scope') for n in _next(f,scopes)]
    mine=[r for r in rows if _match(r,scope,f)]
    if f['id'] is not None: return [_event(r) for r in mine if r['id']==f['id']]
    if f['kind'] is not None: return [_event(r) for r in _newest(mine)]
    if f['pending'] is not None:
        key='project' if f['pending']=='project' else 'task_ref'; groups={}
        for r in mine:
            if r.get(key): groups.setdefault(r[key] if key=='project' else r[key].upper(),[]).append(r)
        return [_dir(quote(k,safe=''),v) for k,v in sorted(groups.items())]
    out=[]
    if f['project'] is None and f['task'] is None:
        out+=[_dir(n,[r for r in rows if _under(r['scope'],scope+'/'+n)],'scope') for n in _next(scope,scopes)]
    for field,folder,key in (('project','projects','project'),('task','tasks','task_ref')):
        tagged=[r for r in mine if r.get(key)]
        if f[field] is None and tagged: out.append(_dir(folder,tagged))
    for kind,folder in KIND_FOLDERS.items():
        same=[r for r in mine if r['kind']==kind]
        if same: out.append(_dir(folder,same))
    return out


def listing(path, rows, scopes, depth=1):
    if depth not in (1,2): raise ValueError('invalid_request')
    parts=split(path); items=children(parts,rows,scopes)
    if depth==2:
        for item in items:
            if item['type']!='event':
                nested=children(parts+[item['name'].rstrip('/')],rows,scopes)
                item['children']=[c['name'] for c in nested[:MAX_NESTED]]
                if len(nested)>MAX_NESTED: item['more']=len(nested)-MAX_NESTED
    return dict(path='/'+'/'.join(parts),children=items[:MAX_CHILDREN],more=max(0,len(items)-MAX_CHILDREN))


def find(path, rows, scopes):
    """The head event a path (or a bare ULID) points at, or ValueError('denied')."""
    parts=split(path)
    if len(parts)==1 and ULID.fullmatch(parts[0]):
        row=next((r for r in rows if r['id']==parts[0]),None)
    else:
        scope,f=resolve(parts,scopes)
        if scope is None or f['id'] is None: raise ValueError('denied')
        row=next((r for r in rows if r['id']==f['id'] and _match(r,scope,f)),None)
    if row is None: raise ValueError('denied')
    return {k:v for k,v in row.items() if not k.startswith('_')}


def canonical(row):
    folder=KIND_FOLDERS.get(row['kind'])
    return '/'+row['scope']+'/'+(folder+'/' if folder else '')+row['id']


def markdown(row):
    head=[('id',row['id']),('kind',row['kind']),('label',row['label']),('scope',row['scope']),
        ('project',row.get('project')),('task',row.get('task_ref')),('updated',row['updated_at'])]
    return '---\n'+''.join(f'{k}: {v}\n' for k,v in head if v)+'---\n'+row['content'].rstrip('\n')+'\n'


def grep_scopes(path, scopes):
    """(profile, scope names, project, task, kind) for a grep under a node; the root means the default profile."""
    parts=split(path)
    if not parts: return None,None,None,None,None
    scope,f=resolve(parts,scopes)
    if scope is None: return parts[0],sorted(s for s in scopes if _under(s,f)),None,None,None
    if f['id'] is not None or f['pending'] is not None: raise ValueError('invalid_request')
    return parts[0],[scope],f['project'],f['task'],f['kind']
