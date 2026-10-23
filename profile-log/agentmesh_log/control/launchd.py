"""launchd backend: observe and control units through launchctl only, with exact argv and no shell."""
import os
import re
import subprocess

ETIME=re.compile(r'^(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)$')


def run(argv):
    process=subprocess.run(argv,capture_output=True,text=True,timeout=10)
    return process.returncode,process.stdout


def parse_print(text):
    """Top-level `key = value` lines of `launchctl print` (exactly one tab deep)."""
    fields={}
    for line in text.splitlines():
        if line.startswith('\t') and not line.startswith('\t\t') and ' = ' in line:
            key,value=line[1:].split(' = ',1)
            fields.setdefault(key.strip(),value.strip())
    return fields


def parse_etime(text):
    """Seconds from ps etime ([[dd-]hh:]mm:ss), or None."""
    match=ETIME.match(text.strip())
    if not match: return None
    days,hours,minutes,seconds=(int(part) if part else 0 for part in match.groups())
    return ((days*24+hours)*60+minutes)*60+seconds


class LaunchdBackend:
    def __init__(self, runner=run, uid=None):
        self.runner=runner; self.domain='gui/%d'%(os.getuid() if uid is None else uid)

    def target(self, unit): return self.domain+'/'+unit.label

    def status(self, unit):
        code,out=self.runner(['/bin/launchctl','print',self.target(unit)])
        if code!=0: return dict(loaded=False,running=False,pid=None,last_exit=None,runtime_s=None)
        fields=parse_print(out)
        pid=int(fields['pid']) if fields.get('pid','').isdigit() else None
        last=fields.get('last exit code','')
        last_exit=int(last) if last.lstrip('-').isdigit() else None
        runtime=None
        if pid is not None:
            code,out=self.runner(['/bin/ps','-o','etime=','-p',str(pid)])
            runtime=parse_etime(out) if code==0 else None
        return dict(loaded=True,running=fields.get('state')=='running',pid=pid,last_exit=last_exit,runtime_s=runtime)

    def start(self, unit): return self._ok(['/bin/launchctl','kickstart',self.target(unit)])
    def restart(self, unit): return self._ok(['/bin/launchctl','kickstart','-k',self.target(unit)])
    def stop(self, unit): return self._ok(['/bin/launchctl','kill','SIGTERM',self.target(unit)])
    def bootout(self, unit): return self._ok(['/bin/launchctl','bootout',self.target(unit)])
    def bootstrap(self, unit, plist): return self._ok(['/bin/launchctl','bootstrap',self.domain,str(plist)])

    def _ok(self, argv):
        code,_=self.runner(argv)
        if code!=0: raise RuntimeError('launchctl_failed')
        return True
