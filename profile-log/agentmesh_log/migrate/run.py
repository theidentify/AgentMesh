"""Offline, preflighted and resumable broker imports."""
from collections import Counter
import hashlib
import json
import time
from pathlib import Path
from ..encoding import wire
from ..broker.client import PresenceRequired, BrokerUnreachable
from .source import rows, body_for, binding_for, check_binding

CACHE_FORMAT='agentmesh-profile-log-catch-up-cache-v2'


def source_key(source):
    return source.get('device'),source['table'],source['id']


def local_entry(entry,device):
    source=entry['source']
    return source.get('device')==device if 'device' in source else entry.get('device')==device


BROKER_CODES=frozenset(('busy','denied','invalid_request','unavailable'))


class BrokerRefused(ValueError):
    """The broker answered with an error. Keeps its code, so catch-up reports `broker_busy` instead of `unavailable`:
    90 failed runs on 2026-10-28 said only "unavailable" while a stale home lock made every request busy."""
    def __init__(self, response):
        super().__init__('broker unavailable')
        code=response.get('error') if type(response) is dict else None
        self.status_code='broker_'+code if code in BROKER_CODES else 'broker_error'


def error_code(exc):
    """A short identifier for status and logs: the broker's own code when it refused, else the exception type."""
    return getattr(exc,'status_code',None) or type(exc).__name__


def checked(response):
    if type(response) is dict and response.get('error')=='presence_required': raise PresenceRequired(response)
    if type(response) is not dict or 'error' in response: raise BrokerRefused(response)
    return response


def reference(value):
    table,sep,ident=value.partition(':')
    if not sep or table not in ('memory_items','memory_summaries') or not ident.isdecimal(): raise ValueError('invalid reference')
    return table,int(ident)


def fingerprint(intent):
    # created_at is advisory to the broker; it never changes an unchanged outcome.
    value=dict(source=intent['source'],scope=intent['scope'],row_hash=intent['row_hash'],retire=intent['body'] is None)
    return hashlib.sha256(wire(value)).hexdigest()


def load_cache(path):
    # Accelerator only: anything unreadable is ignored and rows are re-sent.
    try:
        from ..storage.filesystem import _check
        path=Path(path); _check(path)
        value=json.loads(path.read_bytes().decode('utf-8'))
        if value.get('format')!=CACHE_FORMAT or type(value.get('entries')) is not dict: return {}
        return {k:v for k,v in value['entries'].items() if type(v) is list and len(v)==2 and type(v[0]) is str and (v[1] is None or type(v[1]) is str)}
    except Exception: return {}


def save_cache(path,entries):
    from ..broker.keystore import atomic_write
    # Plain JSON: the cache grows with the source table, beyond the wire packet limit.
    atomic_write(path,json.dumps(dict(format=CACHE_FORMAT,entries=entries),sort_keys=True,separators=(',',':')).encode('utf-8'))


def run(database,mapping,client,*,refs=None,take_over=False,sleep=time.sleep,cache=None,binding=None,hold_unmapped=False):
    """hold_unmapped (catch-up only): import every mapped row and hold back rows of unmapped projects, which are reported
    and neither imported nor retired, instead of refusing the whole run. A new project then waits for the owner's
    scope choice without stopping every other project. Manual runs keep refusing."""
    binding=binding_for(database) if binding is None else binding
    who=checked(client.request(dict(op='whoami')))
    device=who.get('device_id')
    check_binding(database,binding,device)
    values=rows(database,expected_node=binding['staging_node'])
    if refs is not None:
        if len(refs)>500: raise ValueError('too many references')
        refs={reference(v) if type(v) is str else v for v in refs}
    ledger=checked(client.request(dict(op='import_ledger')))
    entries=[e for e in ledger['entries'] if local_entry(e,device)]
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
        source=dict(device=device,table=key[0],id=key[1])
        old=next((e for e in entries if e['source']==source),None)
        if old is None:
            old=next((e for e in entries if e['source']==dict(table=key[0],id=key[1])),None)
            if old is not None: source=old['source']
        if reason and old is None: continue
        if reason: target=old['scope']
        body,stamp,fallback=body_for(row,target); fallbacks+=fallback
        if 'device' in source: body['metadata']['source']['device']=source['device']
        intents.append(dict(source=source,scope=target,created_at=stamp,
            row_hash=body['metadata']['row_hash'],body=None if reason else body))
    if unmapped and not hold_unmapped:
        return dict(outcomes={},unmapped=sorted(unmapped),skipped=dict(skipped),timestamp_fallback=0,
                    busy_retries=0,errors=['UnmappedProject'],secret_labelled=0)
    for entry in entries:
        key=entry['source']['table'],entry['source']['id']
        if key not in seen and (refs is None or key in refs):
            intents.append(dict(source=entry['source'],scope=entry['scope'],created_at=int(time.time()*1000),row_hash='0'*64,body=None))
    outcomes=Counter(); retries=0; secret=0; errors=[]
    # A row whose intent and ledger head both match the last confirmed import is
    # answered unchanged locally, so idle catch-up cycles send no import requests.
    known=load_cache(cache) if cache is not None else {}; confirmed={}
    heads={source_key(e['source']):e['event'] for e in entries}
    for intent in intents:
        key=source_key(intent['source']); name=f'{key[0] or "legacy"}:{key[1]}:{key[2]}'; digest=fingerprint(intent)
        if known.get(name)==[digest,heads.get(key)]:
            outcomes['unchanged']+=1; confirmed[name]=known[name]; continue
        try:
            for attempt in range(100):
                check_binding(database,binding,device)
                response=client.import_row(**intent)
                if response.get('error')!='busy': break
                retries+=1; sleep(min(.01*(attempt+1),.2))
            checked(response)
            outcomes[response['outcome']]+=1
            if type(response.get('id')) is str or (response.get('outcome')=='unchanged' and response.get('id') is None):
                confirmed[name]=[digest,response.get('id')]
            secret+=response.get('label')=='secret'
        except Exception as exc:
            if isinstance(exc,(PresenceRequired,BrokerUnreachable)): raise
            if isinstance(exc,ValueError) and str(exc).startswith(('staging ','invalid staging ')): raise
            errors.append(error_code(exc)); break
    if cache is not None:
        if refs is not None: confirmed={**known,**confirmed}
        try: save_cache(cache,confirmed)
        except Exception: pass
    return dict(outcomes=dict(outcomes),unmapped=sorted(unmapped),skipped=dict(skipped),timestamp_fallback=fallbacks,
                busy_retries=retries,errors=errors,secret_labelled=secret)
