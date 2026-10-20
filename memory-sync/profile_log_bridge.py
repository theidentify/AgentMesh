"""Opt-in subprocess bridge. No profile-log imports or plaintext diagnostics."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

FIELDS={'format','enabled','recall','dual_write','authority','cli_argv','agent_key','import_agent_key','mapping','profile','timeout_seconds'}


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


def load_config(database):
    try:
        path=Path(os.environ.get('AGENTMESH_PROFILE_LOG_BRIDGE',str(database)+'.profile-log.json'))
        if not path.exists() and not path.is_symlink(): return None
        if path.is_symlink() or not path.is_file(): raise ValueError('invalid config')
        if os.name!='nt':
            info=path.stat()
            if info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600: raise ValueError('invalid config')
        value=_json(path.read_text(encoding='utf-8'))
        if type(value) is not dict or set(value)!=FIELDS or value['format']!='agentmesh-profile-log-bridge-v1': raise ValueError('invalid config')
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
        if type(value['profile']) is not str or not value['profile'] or '/' in value['profile'] or any(ord(c)<32 for c in value['profile']): raise ValueError('invalid config')
        if type(value['timeout_seconds']) not in (int,float) or not 1<=value['timeout_seconds']<=30: raise ValueError('invalid config')
        return value
    except Exception as exc:
        warning(exc); return None


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


def broker_recall(plan,pool,config):
    argv=[*config['cli_argv'],'recall',plan.query,'--compat','--agent-key',config['agent_key'],
          '--profile',config['profile'],'--limit',str(min(pool,100))]
    if plan.project is not None: argv+=['--project',plan.project]
    # All references remain in the query; explicit task adds the plan's first ref.
    if plan.task_refs: argv+=['--task',plan.task_refs[0]]
    try:
        response=subprocess.run(argv,stdin=subprocess.DEVNULL,capture_output=True,text=True,encoding='utf-8',timeout=config['timeout_seconds'])
        if response.returncode or len(response.stdout)>4*1024*1024: raise BrokerUnavailable()
        value=_json(response.stdout)
        if type(value) is not dict or set(value)!={'query','query_plan','candidate_counts','results','evidence'}: raise BrokerUnavailable()
        if type(value['results']) is not list or type(value['candidate_counts']) is not dict: raise BrokerUnavailable()
        for row in value['results']:
            required={'id','source_type','source_ref','kind','content','project','task_ref','confidence','scope','profile','label','created_at','updated_at','source','evidence_event_ids'}
            if type(row) is not dict or not required<=set(row): raise BrokerUnavailable()
            if row['source_type'] not in ('durable','summary') or type(row['content']) is not str or type(row['id']) is not str: raise BrokerUnavailable()
            ref=row['source_ref']
            if ref is not None and (type(ref) is not dict or set(ref)!={'table','id'} or ref['table'] not in ('memory_items','memory_summaries') or type(ref['id']) is not int): raise BrokerUnavailable()
            if type(row['evidence_event_ids']) is not list or any(type(v) is not int for v in row['evidence_event_ids']): raise BrokerUnavailable()
        return value
    except Exception: raise BrokerUnavailable() from None
