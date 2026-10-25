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
def load():
 c=json.loads(CONFIG.read_text(encoding='utf-8'))
 if c['primary_backend']!='sqlite' or c['retention']!='disabled':raise ValueError('inactive SQLite authority or unsafe retention')
 for k in ('database','runtime','export_root'):
  if not Path(c[k]).is_absolute():raise ValueError('absolute path required')
 if not Path(c['database']).is_file():raise ValueError('database unavailable')
 sys.path.insert(0,c['runtime']);return c
def save(c,name,value):
 p=Path(c['status_dir'])/(name+'.json');p.parent.mkdir(parents=True,exist_ok=True)
 tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True),encoding='utf-8');os.replace(tmp,p)
def execute(action,argv=None):
 args=list(sys.argv[1:] if argv is None else argv)
 try:
  c=load()
  if action=='context':
   from shared_memory_context import main
   return main(['--database',c['database'],*args]) or 0
  if action=='recall':
   from recall_memory import main
   return main([c['database'],*args]) or 0
  if action=='retention':
   from retention_audit import audit
   print(json.dumps(audit(c['database'])));return 0
  if action=='export':
   from sqlite_export import main
   return main([c['database'],*args] if args else [c['database'],'--root',c['export_root']]) or 0
  if action=='blocked':raise ValueError('legacy PostgreSQL writer disabled after SQLite cutover')
  p=argparse.ArgumentParser();p.add_argument('--force',action='store_true'); a=p.parse_args(args)
  from datetime import datetime,timezone
  from bounded_digest import run_lock
  with run_lock(str(CONFIG)+'.'+action):
   if action=='ingest':
    from ingest_sessions import ingest,raise_for_errors
    from sqlite_export import publish
    report=ingest(c['database'],roots=c['roots']);save(c,'last-ingest',dict(observed_at=datetime.now(timezone.utc).isoformat(),**report))
    raise_for_errors(report)
    exported=publish(c['database'],c['export_root']);save(c,'last-export',dict(observed_at=datetime.now(timezone.utc).isoformat(),**exported))
   elif action=='summary':
    from digest_scheduler import tick
    boot=boot_id()
    try:
     report=tick(c['database'],root=c['export_root'],boot_id=boot,force=a.force,**c['summary']);save(c,'last-summary',dict(observed_at=datetime.now(timezone.utc).isoformat(),**report))
    finally:
     from digest_scheduler import read_state
     state=read_state(c['database']);state['source_of_truth']='SQLite';state['observed_at']=datetime.now(timezone.utc).isoformat()
     target=CONFIG.parent/'.bounded_digest_state.json';tmp=target.with_suffix('.tmp');tmp.write_text(json.dumps(state,sort_keys=True),encoding='utf-8');os.replace(tmp,target)
   else:raise ValueError('unknown operation')
  return 0
 except Exception as e:
  from bounded_protocol import error_detail
  print(json.dumps({'status':'blocked','action':action,'error_class':type(e).__name__,'detail':error_detail(e)}),file=sys.stderr);return 1
