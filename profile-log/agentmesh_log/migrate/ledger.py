"""Content-free migration mapping reports."""
from pathlib import Path
import os
from ..encoding import wire
from .source import rows
from .run import checked


def report(client,database=None,mapping=None):
    result={}
    for entry in checked(client.request(dict(op='import_ledger')))['entries']:
        src=entry['source']; result[f"{src['table']}:{src['id']}"]={k:v for k,v in entry.items() if k!='source'}
    if database is not None:
        if mapping is None: raise ValueError('mapping required')
        for row in rows(database):
            key=f"{row['table']}:{row['id']}"
            reason=row['reason']
            try:
                target=mapping.target(row)
                if not reason and target=='skip': reason='mapping_skip'
            except ValueError: target=None; reason=reason or 'unmapped'
            if key not in result:
                result[key]=dict(event=None,chain=[],scope=target,status='skipped',reason=reason or 'not_migrated')
            elif reason: result[key]['reason']=reason
    return result


def write_report(result,path,replica):
    path=Path(path).absolute(); replica=Path(replica).resolve()
    if any(p.is_symlink() for p in (path,*path.parents)) or 'replica' in path.parts or path.resolve().is_relative_to(replica): raise ValueError('unsafe report destination')
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as stream:
        stream.write(wire(result).decode('utf-8')+'\n')
