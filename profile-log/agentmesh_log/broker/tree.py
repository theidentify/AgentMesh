"""Read-only memory tree over an agent's visible rows, plus the rules for page events (memory tree T1/T2).

Paths are scope names, then optional filters: [projects/<p>/][tasks/<T>/][<kind folder>/][<ULID>|<page>.md].
Every node is built from rows already filtered by the agent's grant, so a scope the agent cannot read
never appears, not even as a name or a count.

A page is an event with kind "page" and one tag "page:<rel>", where <rel> is the canonical node prefix plus
"INDEX" or a slug, for example "decisions/INDEX" or "projects/mesh/INDEX". Its content is markdown with a
frontmatter block (path, summary, sources, ...). Pages live in the scope they describe and summarize only it.
"""
import re
from datetime import datetime
from urllib.parse import quote, unquote
from ..ulid import ALPHABET

KIND_FOLDERS = {'decision':'decisions','constraint':'constraints','procedure':'procedures',
    'preference':'preferences','open_loop':'open-loops','fact':'facts','summary':'summaries'}
FOLDER_KINDS = {v:k for k,v in KIND_FOLDERS.items()}
ULID = re.compile('['+ALPHABET+']{26}')
SLUG = re.compile('[a-z0-9][a-z0-9-]{0,63}')
PAGE_TAG = 'page:'
MAX_PATH, MAX_CHILDREN, MAX_NESTED, SUMMARY_CHARS, MAX_SOURCES = 512, 40, 10, 120, 200


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
    f=dict(project=None,task=None,kind=None,id=None,page=None,pending=None); i=0
    while i<len(rest):
        part,last=rest[i],i==len(rest)-1
        if f['id'] is not None or f['pending'] is not None: raise ValueError('denied')
        if ULID.fullmatch(part) and last: f['id']=part
        elif last and part.endswith('.md') and (part=='INDEX.md' or SLUG.fullmatch(part[:-3])): f['page']=part[:-3]
        elif f['kind'] is not None: raise ValueError('denied')
        elif part in FOLDER_KINDS: f['kind']=FOLDER_KINDS[part]
        elif part in ('projects','tasks') and f[part[:-1]] is None:
            if last: f['pending']=part[:-1]
            else: f[part[:-1]]=unquote(rest[i+1]); i+=1
        else: raise ValueError('denied')
        i+=1
    return f


def prefix(f):
    """The canonical page-path prefix of a node: projects, then tasks, then the kind folder."""
    out=''
    if f['project'] is not None: out+='projects/'+quote(f['project'],safe='')+'/'
    if f['task'] is not None: out+='tasks/'+quote(f['task'].upper(),safe='')+'/'
    if f['kind'] is not None: out+=KIND_FOLDERS[f['kind']]+'/'
    return out


def _match(row, scope, f):
    return (row['scope']==scope and (f['project'] is None or row.get('project')==f['project'])
        and (f['task'] is None or (row.get('task_ref') or '').upper()==f['task'].upper())
        and (f['kind'] is None or row['kind']==f['kind']))


def _at(value):
    # Row times are ISO strings with or without fractional seconds, so compare them as instants.
    return datetime.fromisoformat(value.replace('Z','+00:00'))


def _newest(rows): return sorted(rows,key=lambda r:(_at(r['updated_at']),r['id']),reverse=True)


def page_rel(row):
    if row['kind']!='page': return None
    return next((t[len(PAGE_TAG):] for t in row.get('tags') or [] if t.startswith(PAGE_TAG)),None)


def _split_rows(rows):
    """Pages keyed by (scope, rel), newest head first on a conflict, and the ordinary events."""
    pages={}; events=[]
    for r in _newest(rows):
        rel=page_rel(r)
        if rel is not None: pages.setdefault((r['scope'],rel),r)
        elif r['kind']!='page': events.append(r)
    return pages,events


def frontmatter(text):
    if type(text) is not str or not text.startswith('---\n'): raise ValueError('invalid_request')
    end=text.find('\n---\n',3)
    if end<0: raise ValueError('invalid_request')
    meta={}
    for line in text[4:end].splitlines():
        key,sep,value=line.partition(': ')
        if not sep or key in meta: raise ValueError('invalid_request')
        value=value.strip()
        if value.startswith('[') and value.endswith(']'): value=[v.strip() for v in value[1:-1].split(',') if v.strip()]
        meta[key]=value
    return meta


def _line(row):
    if row['label']=='secret': return None
    if row['kind']=='page':
        try: text=frontmatter(row['content']).get('summary')
        except ValueError: text=None
        text=text if type(text) is str else ''
    else: text=next((l.strip() for l in row['content'].splitlines() if l.strip()),'')
    return text if len(text)<=SUMMARY_CHARS else text[:SUMMARY_CHARS-1]+'…'


def _stale(page, scope, f, events):
    at=_at(page['updated_at'])
    return any(_at(e['updated_at'])>at for e in events if _match(e,scope,f))


def _page(page, scope, f, events):
    return dict(name=page_rel(page).split('/')[-1]+'.md',type='page',label=page['label'],
        updated=page['updated_at'],summary=_line(page),stale=_stale(page,scope,f,events))


def _dir(name, rows, kind='dir', page=None, stale=False):
    rows=_newest(rows)
    entry=dict(name=name+'/',type=kind,count=len(rows),updated=rows[0]['updated_at'] if rows else None,
        summary=next((s for s in map(_line,rows) if s),None))
    if page is not None: entry.update(summary=_line(page),stale=stale)
    return entry


def _event(row):
    return dict(name=row['id'],type='event',kind=row['kind'],label=row['label'],updated=row['updated_at'],summary=_line(row))


def _next(prefix_, scopes):
    start=len(prefix_)+1 if prefix_ else 0
    return sorted({s[start:].split('/')[0] for s in scopes if s!=prefix_ and _under(s,prefix_)})


def _scope_dir(name, full, scopes, pages, events):
    page=pages.get((full,'INDEX')) if full in scopes else None
    empty=dict(project=None,task=None,kind=None)
    return _dir(name,[r for r in events if _under(r['scope'],full)],'scope',page,
        page is not None and _stale(page,full,empty,events))


def _child(scope, f, pages, events, name, rows, **change):
    g={**f,**change}; page=pages.get((scope,prefix(g)+'INDEX'))
    return _dir(name,rows,'dir',page,page is not None and _stale(page,scope,g,events))


def children(parts, rows, scopes):
    pages,events=_split_rows(rows)
    scope,f=resolve(parts,scopes)
    if scope is None:
        return [_scope_dir(n,'/'.join(parts+[n]),scopes,pages,events) for n in _next(f,scopes)]
    mine=[r for r in events if _match(r,scope,f)]
    if f['id'] is not None: return [_event(r) for r in mine if r['id']==f['id']]
    if f['page'] is not None:
        page=pages.get((scope,prefix(f)+f['page']))
        if page is None: raise ValueError('denied')
        return [_page(page,scope,f,events)]
    if f['pending'] is not None:
        key='project' if f['pending']=='project' else 'task_ref'; groups={}
        for r in mine:
            if r.get(key): groups.setdefault(r[key] if key=='project' else r[key].upper(),[]).append(r)
        return [_child(scope,f,pages,events,quote(k,safe=''),v,pending=None,**{f['pending']:k}) for k,v in sorted(groups.items())]
    base=prefix(f)
    out=[_page(p,scope,f,events) for (s,rel),p in sorted(pages.items(),key=lambda i:(i[0][1]!=base+'INDEX',i[0][1]))
        if s==scope and rel.startswith(base) and '/' not in rel[len(base):]]
    if f['kind'] is not None: return out+[_event(r) for r in _newest(mine)]
    if f['project'] is None and f['task'] is None:
        out+=[_scope_dir(n,scope+'/'+n,scopes,pages,events) for n in _next(scope,scopes)]
    for field,folder,key in (('project','projects','project'),('task','tasks','task_ref')):
        tagged=[r for r in mine if r.get(key)]
        if f[field] is None and tagged: out.append(_dir(folder,tagged))
    for kind,folder in KIND_FOLDERS.items():
        same=[r for r in mine if r['kind']==kind]
        if same: out.append(_child(scope,f,pages,events,folder,same,kind=kind))
    return out


def listing(path, rows, scopes, depth=1):
    if depth not in (1,2): raise ValueError('invalid_request')
    parts=split(path); items=children(parts,rows,scopes)
    if depth==2:
        for item in items:
            if item['type'] not in ('event','page'):
                nested=children(parts+[item['name'].rstrip('/')],rows,scopes)
                item['children']=[c['name'] for c in nested[:MAX_NESTED]]
                if len(nested)>MAX_NESTED: item['more']=len(nested)-MAX_NESTED
    return dict(path='/'+'/'.join(parts),children=items[:MAX_CHILDREN],more=max(0,len(items)-MAX_CHILDREN))


def find(path, rows, scopes):
    """The head event or page a path (or a bare ULID) points at, or ValueError('denied')."""
    parts=split(path)
    if len(parts)==1 and ULID.fullmatch(parts[0]):
        row=next((r for r in rows if r['id']==parts[0]),None)
    else:
        scope,f=resolve(parts,scopes)
        if scope is None: raise ValueError('denied')
        if f['page'] is not None: row=_split_rows(rows)[0].get((scope,prefix(f)+f['page']))
        elif f['id'] is not None: row=next((r for r in rows if r['id']==f['id'] and _match(r,scope,f)),None)
        else: raise ValueError('denied')
    if row is None: raise ValueError('denied')
    return {k:v for k,v in row.items() if not k.startswith('_')}


def stale(row, rows):
    """Whether a page's node has events newer than the page; None for an ordinary event."""
    rel=page_rel(row)
    if rel is None: return None
    f=_filters(rel.split('/')[:-1]) if '/' in rel else _filters([])
    return _stale(row,row['scope'],f,_split_rows(rows)[1])


def canonical(row):
    rel=page_rel(row)
    if rel is not None: return '/'+row['scope']+'/'+rel+'.md'
    folder=KIND_FOLDERS.get(row['kind'])
    return '/'+row['scope']+'/'+(folder+'/' if folder else '')+row['id']


def markdown(row):
    if page_rel(row) is not None: return row['content'].rstrip('\n')+'\n'
    head=[('id',row['id']),('kind',row['kind']),('label',row['label']),('scope',row['scope']),
        ('project',row.get('project')),('task',row.get('task_ref')),('updated',row['updated_at'])]
    return '---\n'+''.join(f'{k}: {v}\n' for k,v in head if v)+'---\n'+row['content'].rstrip('\n')+'\n'


def grep_scopes(path, scopes):
    """(profile, scope names, project, task, kind) for a grep under a node; the root means the default profile."""
    parts=split(path)
    if not parts: return None,None,None,None,None
    scope,f=resolve(parts,scopes)
    if scope is None: return parts[0],sorted(s for s in scopes if _under(s,f)),None,None,None
    if f['id'] is not None or f['pending'] is not None or f['page'] is not None: raise ValueError('invalid_request')
    return parts[0],[scope],f['project'],f['task'],f['kind']


def page_path_valid(rel):
    if type(rel) is not str or not 1<=len(rel)<=MAX_PATH: return False
    parts=rel.split('/'); last=parts[-1]
    if last!='INDEX' and not SLUG.fullmatch(last): return False
    try: f=_filters(parts[:-1])
    except ValueError: return False
    if any(f[k] is not None for k in ('id','page','pending')): return False
    return prefix(f)+last==rel


def page_request(body, tags):
    """(rel, sources) for a page write, None for an ordinary memory, ValueError for a malformed page."""
    kind=body.get('kind') if type(body) is dict else None
    paths=[t[len(PAGE_TAG):] for t in tags if t.startswith(PAGE_TAG)]
    if kind!='page' and not paths: return None
    if kind!='page' or len(paths)!=1 or not page_path_valid(paths[0]): raise ValueError('invalid_request')
    if body.get('project') is not None or body.get('task_ref') is not None: raise ValueError('invalid_request')
    meta=frontmatter(body['content'])
    sources=meta.get('sources'); summary=meta.get('summary')
    if meta.get('path')!=paths[0] or type(summary) is not str or not 1<=len(summary)<=200: raise ValueError('invalid_request')
    if type(sources) is not list or not 1<=len(sources)<=MAX_SOURCES or len(set(sources))!=len(sources) \
            or not all(ULID.fullmatch(s) for s in sources): raise ValueError('invalid_request')
    return paths[0],sources
