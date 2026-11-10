"""Private production selector and scheduler entrypoint. No PostgreSQL fallback."""
import argparse,json,os,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
HOME=Path(os.environ.get('AGENTMESH_HOME',str(Path.home()/'.agentmesh')))
# Prefer a config beside this script (the deployed ~/.agentmesh/jobs); else AGENTMESH_HOME/jobs.
_LOCAL=Path(__file__).resolve().with_name('agentmesh-production.json')
CONFIG=_LOCAL if _LOCAL.is_file() else HOME/'jobs/agentmesh-production.json'
def boot_id():
 if os.name=='nt':
  # The kernel's own BootTime (FILETIME) is fixed for the whole boot; now-minus-uptime drifts with clock changes.
  import ctypes
  info=(ctypes.c_byte*48)();size=ctypes.c_ulong()
  if ctypes.windll.ntdll.NtQuerySystemInformation(3,info,48,ctypes.byref(size))!=0:raise OSError('boot time unavailable')
  filetime=int.from_bytes(bytes(info)[:8],'little')
  return datetime.fromtimestamp(filetime/10**7-11644473600,timezone.utc).isoformat()
 return subprocess.run(['/usr/sbin/sysctl','-n','kern.boottime'],capture_output=True,text=True,check=True).stdout.strip()
BROKER=('cli_argv','agent_key','mapping','state_dir')
def load():
 c=json.loads(CONFIG.read_text(encoding='utf-8'))
 # The broker backend reads transcripts directly and writes through the summarizer agent. The SQLite backend was
 # removed in retirement R5b; a config still naming it is refused, never run.
 if c.get('backend')!='broker':raise ValueError('SQLite backend retired; backend must be broker')
 b=c.get('broker')
 if type(b) is not dict or set(b)!=set(BROKER) or not Path(c['runtime']).is_absolute():raise ValueError('invalid broker backend')
 if any(not Path(v).is_absolute() for v in (b['agent_key'],b['mapping'],b['state_dir'],c['status_dir'])):raise ValueError('absolute path required')
 sys.path.insert(0,c['runtime']);return c
def save(c,name,value):
 p=Path(c['status_dir'])/(name+'.json');p.parent.mkdir(parents=True,exist_ok=True)
 tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True),encoding='utf-8');os.replace(tmp,p)
def broker(c,action,args):
 if action not in ('ingest','summary'):raise ValueError('action unavailable on the broker backend')
 p=argparse.ArgumentParser();p.add_argument('--force',action='store_true'); a=p.parse_args(args)
 from digest_io import run_lock
 import broker_digest
 b=c['broker']
 with run_lock(str(CONFIG)+'.'+action):
  if action=='ingest':
   # Nothing is stored: report what the next summary tick will read, without moving any cursor.
   state=broker_digest.State(b['state_dir']);sources,errors=broker_digest.discover(c.get('roots'))
   _,report=broker_digest.scan(state,sources,start_at=state.summary['start_at'])
   report['errors']+=len(errors);report['error_classes']+=[type(e).__name__ for e in errors]
   save(c,'last-ingest',dict(observed_at=datetime.now(timezone.utc).isoformat(),backend='broker',**report))
   if report['errors']:
    from collections import Counter
    from bounded_protocol import ValidationError
    raise ValidationError('transcript scan errors: '+', '.join('%s=%d'%kv for kv in sorted(Counter(report['error_classes']).items())))
   return 0
  backend=broker_digest.CliBackend(b['cli_argv'],b['agent_key'],b['mapping'])
  options={k:v for k,v in c['summary'].items() if k not in ('consumer','primary_node')}
  report=broker_digest.tick(b['state_dir'],backend,roots=c.get('roots'),boot_id=boot_id(),force=a.force,**options)
  save(c,'last-summary',dict(observed_at=datetime.now(timezone.utc).isoformat(),backend='broker',**report))
 return 0
def execute(action,argv=None):
 args=list(sys.argv[1:] if argv is None else argv)
 try:
  c=load()
  return broker(c,action,args)
 except Exception as e:
  from bounded_protocol import error_detail
  print(json.dumps({'status':'blocked','action':action,'error_class':type(e).__name__,'detail':error_detail(e)}),file=sys.stderr);return 1
