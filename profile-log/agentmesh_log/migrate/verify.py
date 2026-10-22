"""Read-only presence counts from the live broker's content-free import ledger."""
from .source import binding_for, check_binding, rows
from .run import checked, local_entry, source_key


def verify(database,mapping,client,*,binding=None):
    binding=binding_for(database) if binding is None else binding
    who=checked(client.request(dict(op='whoami'))); device=who.get('device_id')
    check_binding(database,binding,device)
    values=rows(database,expected_node=binding['staging_node'])
    if not mapping.targets <= set(who['write']): raise ValueError('invalid target scopes')
    entries={source_key(e['source']):e for e in checked(client.request(dict(op='import_ledger')))['entries'] if local_entry(e,device)}
    result=dict(missing=0,retired_but_present=0)
    for row in values:
        try: target=mapping.target(row)
        except ValueError: raise ValueError('unmapped staging rows') from None
        if row['reason'] or target=='skip': continue
        key=row['table'],row['id']
        old=entries.get((device,*key)) or entries.get((None,*key))
        if old is None: result['missing']+=1
        elif old['status']=='retired': result['retired_but_present']+=1
    return result
