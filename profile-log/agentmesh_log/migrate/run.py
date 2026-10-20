"""Offline, preflighted and resumable broker imports."""
from collections import Counter
import time
from .source import rows, body_for


def checked(response):
    if type(response) is not dict or 'error' in response: raise ValueError('broker unavailable')
    return response


def reference(value):
    table,sep,ident=value.partition(':')
    if not sep or table not in ('memory_items','memory_summaries') or not ident.isdecimal(): raise ValueError('invalid reference')
    return table,int(ident)


def run(database,mapping,client,*,refs=None,take_over=False,sleep=time.sleep):
    values=rows(database)
    if refs is not None:
        if len(refs)>500: raise ValueError('too many references')
        refs={reference(v) if type(v) is str else v for v in refs}
    ledger=checked(client.request(dict(op='import_ledger')))
    if ledger.get('foreign_authority') and not take_over: raise ValueError('foreign migration authority')
    who=checked(client.request(dict(op='whoami')))
    if not mapping.targets <= set(who['write']): raise ValueError('invalid target scopes')
    intents=[]; skipped=Counter(); unmapped=set(); seen=set(); fallbacks=0
    for row in values:
        key=row['table'],row['id']; seen.add(key)
        if refs is not None and key not in refs: continue
        try: target=mapping.target(row)
        except ValueError:
            if row.get('project') is not None: unmapped.add(row['project'])
            continue
        reason=row['reason'] or ('mapping_skip' if target=='skip' else None)
        if reason: skipped[reason]+=1
        old=next((e for e in ledger['entries'] if e['source']==dict(table=key[0],id=key[1])),None)
        if reason and old is None: continue
        if reason: target=old['scope']
        body,stamp,fallback=body_for(row,target); fallbacks+=fallback
        intents.append(dict(source=dict(table=key[0],id=key[1]),scope=target,created_at=stamp,
            row_hash=body['metadata']['row_hash'],body=None if reason else body))
    if unmapped:
        return dict(outcomes={},unmapped=sorted(unmapped),skipped=dict(skipped),timestamp_fallback=0,
                    busy_retries=0,errors=['UnmappedProject'],secret_labelled=0)
    for entry in ledger['entries']:
        key=entry['source']['table'],entry['source']['id']
        if key not in seen and (refs is None or key in refs):
            intents.append(dict(source=entry['source'],scope=entry['scope'],created_at=int(time.time()*1000),row_hash='0'*64,body=None))
    outcomes=Counter(); retries=0; secret=0; errors=[]
    for intent in intents:
        try:
            for attempt in range(100):
                response=client.import_row(**intent)
                if response.get('error')!='busy': break
                retries+=1; sleep(min(.01*(attempt+1),.2))
            checked(response)
            outcomes[response['outcome']]+=1
            secret+=response.get('label')=='secret'
        except Exception as exc:
            errors.append(type(exc).__name__); break
    return dict(outcomes=dict(outcomes),unmapped=[],skipped=dict(skipped),timestamp_fallback=fallbacks,
                busy_retries=retries,errors=errors,secret_labelled=secret)
