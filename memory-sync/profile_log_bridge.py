"""Opt-in subprocess bridge. No profile-log imports or plaintext diagnostics."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import stat
import re
import time
import unicodedata
import subprocess
import sys

FIELDS={'format','enabled','recall','dual_write','authority','cli_argv','agent_key','import_agent_key','mapping','profile','timeout_seconds'}

EXTENDED={'recall_fallback','device_id','staging_node'}


def _device(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{32}',value) is not None


def _profile(value):
    return type(value) is str and bool(value) and value not in ('.','..') and not any(c in '/\\' or unicodedata.category(c)=='Cc' for c in value)


class BrokerUnavailable(Exception):
    pass


def warning(error):
    print('profile-log bridge: '+type(error).__name__,file=sys.stderr)


def _object(pairs):
    result={}
    for key,value in pairs:
        if key in result: raise ValueError('invalid JSON')
        result[key]=value
    return result


def _json(text):
    return json.loads(text,object_pairs_hook=_object,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('invalid JSON')))


def load_config(database, *, strict=False):
    try:
        path=Path(os.environ.get('AGENTMESH_PROFILE_LOG_BRIDGE',str(database)+'.profile-log.json'))
        if not path.exists() and not path.is_symlink(): return None
        if path.is_symlink() or not path.is_file(): raise ValueError('invalid config')
        if os.name!='nt':
            info=path.stat()
            if info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600: raise ValueError('invalid config')
        value=_json(path.read_text(encoding='utf-8'))
        if type(value) is not dict or set(value) not in (FIELDS,FIELDS|EXTENDED,(FIELDS-{'profile'})|{'profiles'}|EXTENDED) or value['format']!='agentmesh-profile-log-bridge-v1': raise ValueError('invalid config')
        if EXTENDED<=set(value):
            if type(value['recall_fallback']) is not bool or not _device(value['device_id']): raise ValueError('invalid config')
            if type(value['staging_node']) is not str or not value['staging_node'] or any(unicodedata.category(c)=='Cc' for c in value['staging_node']): raise ValueError('invalid config')
        if any(type(value[k]) is not bool for k in ('enabled','recall','dual_write','authority')): raise ValueError('invalid config')
        for key in ('agent_key','import_agent_key','mapping'):
            if type(value[key]) is not str or not Path(value[key]).is_absolute() or any(ord(c)<32 for c in value[key]): raise ValueError('invalid config')
        argv=value['cli_argv']
        if type(argv) is not list or not argv or any(type(a) is not str or not a or any(ord(c)<32 for c in a) for a in argv): raise ValueError('invalid config')
        if not Path(argv[0]).is_absolute(): raise ValueError('invalid config')
        # Only the CLI module or an absolute CLI script is an accepted launcher.
        if len(argv)==3 and argv[1:]==['-m','agentmesh_log.cli']: pass
        elif len(argv)==2 and Path(argv[1]).is_absolute(): pass
        else: raise ValueError('invalid config')
        profiles=value.get('profiles',[value.get('profile')])
        if type(profiles) is not list or not profiles or any(not _profile(p) for p in profiles) or len(set(profiles))!=len(profiles): raise ValueError('invalid config')
        if type(value['timeout_seconds']) not in (int,float) or not 1<=value['timeout_seconds']<=30: raise ValueError('invalid config')
        return value
    except Exception as exc:
        warning(exc)
        if strict: raise BrokerUnavailable() from None
        return None


def notify_rows(database,refs):
    try:
        value=load_config(database)
        if not value or not all(value[k] for k in ('enabled','dual_write','authority')): return
        if not refs: return
        if len(refs)>500: raise ValueError('invalid refs')
        argv=[*value['cli_argv'],'migrate','rows','--db',str(Path(database).resolve()),
              '--mapping',value['mapping'],'--agent-key',value['import_agent_key']]
        for table,ident in refs:
            if table not in ('memory_items','memory_summaries') or type(ident) is not int: raise ValueError('invalid refs')
            argv+=['--ref',f'{table}:{ident}']
        options=dict(stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        if os.name=='nt': options['creationflags']=subprocess.CREATE_NO_WINDOW|subprocess.DETACHED_PROCESS
        else: options['start_new_session']=True
        subprocess.Popen(argv,**options)
    except Exception as exc: warning(exc)


def _profile_recall(plan,pool,config,profile,deadline):
    argv=[*config['cli_argv'],'recall',plan.query,'--compat','--agent-key',config['agent_key'],
          '--profile',profile,'--limit',str(min(pool,100))]
    if plan.project is not None: argv+=['--project',plan.project]
    # All references remain in the query; explicit task adds the plan's first ref.
    if plan.task_refs: argv+=['--task',plan.task_refs[0]]
    try:
        response=subprocess.run(argv,stdin=subprocess.DEVNULL,capture_output=True,text=True,encoding='utf-8',timeout=max(0,deadline-time.monotonic()))
        if response.returncode or len(response.stdout)>4*1024*1024: raise BrokerUnavailable()
        value=_json(response.stdout)
        if type(value) is not dict or set(value)!={'query','query_plan','candidate_counts','results','evidence'}: raise BrokerUnavailable()
        if type(value['results']) is not list or type(value['candidate_counts']) is not dict: raise BrokerUnavailable()
        for row in value['results']:
            required={'id','source_type','source_ref','kind','content','project','task_ref','confidence','scope','profile','label','created_at','updated_at','source','evidence_event_ids'}
            if type(row) is not dict or not required<=set(row): raise BrokerUnavailable()
            if row['source_type'] not in ('durable','summary') or type(row['content']) is not str or type(row['id']) is not str: raise BrokerUnavailable()
            ref=row['source_ref']
            if ref is not None and (type(ref) is not dict or set(ref) not in ({'table','id'},{'device','table','id'}) or ('device' in ref and not _device(ref['device'])) or ref['table'] not in ('memory_items','memory_summaries') or type(ref['id']) is not int): raise BrokerUnavailable()
            if row['profile']!=profile or (row.get('author_device') is not None and not _device(row['author_device'])): raise BrokerUnavailable()
            if type(row['evidence_event_ids']) is not list or any(type(v) is not int for v in row['evidence_event_ids']): raise BrokerUnavailable()
        if any(type(v) is not int or v<0 for v in value['candidate_counts'].values()): raise BrokerUnavailable()
        return value
    except Exception: raise BrokerUnavailable() from None


def broker_recall(plan,pool,config):
    """Interleave profile ranks; counts sum before ID deduplication and pool truncation."""
    profiles=config.get('profiles',[config.get('profile')])
    deadline=time.monotonic()+config['timeout_seconds']
    executor=ThreadPoolExecutor(max_workers=len(profiles))
    try:
        futures=[executor.submit(_profile_recall,plan,pool,config,p,deadline) for p in profiles]
        answers=[f.result(timeout=max(0,deadline-time.monotonic())) for f in futures]
        merged=dict(answers[0]); merged['results']=[]; merged['evidence']=[]; merged['candidate_counts']={}
        seen=set()
        for rank in range(max(len(a['results']) for a in answers)):
            for answer in answers:
                if rank<len(answer['results']):
                    row=answer['results'][rank]
                    if row['id'] not in seen:
                        merged['results'].append(row); seen.add(row['id'])
        merged['results']=merged['results'][:pool]
        for answer in answers:
            for key,count in answer['candidate_counts'].items():
                merged['candidate_counts'][key]=merged['candidate_counts'].get(key,0)+count
        return merged
    except Exception: raise BrokerUnavailable() from None
    finally: executor.shutdown(wait=False,cancel_futures=True)
