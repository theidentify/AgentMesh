IDLE=dict(loaded=True,running=False,pid=None,last_exit=0,runtime_s=None)


class FakeBackend:
    def __init__(self, statuses=None, fail=False):
        self.statuses,self.fail,self.calls,self.failed=statuses or {},fail,[],set()

    def status(self, unit): return dict(self.statuses.get(unit.name,IDLE),failed=unit.name in self.failed)

    def reset(self, unit):
        cleared=unit.name in self.failed; self.failed.discard(unit.name); return cleared

    def _act(self, action, unit):
        if self.fail: raise RuntimeError('launchctl_failed')
        self.calls.append((action,unit.name)); return True

    def start(self, unit): return self._act('start',unit)
    def restart(self, unit): return self._act('restart',unit)
    def stop(self, unit): return self._act('stop',unit)


import plistlib
from agentmesh_log.control.units import UNITS

PLISTS={
    'ingest':dict(Label='org.agentmesh.ingest',ProgramArguments=['/opt/am/python','/opt/am/job_notify.py','--name','ingest'],
        StandardOutPath='/opt/am/ingest.log',StandardErrorPath='/opt/am/ingest.log',StartInterval=60,RunAtLoad=True,Umask=63),
    'retention':dict(Label='org.agentmesh.retention',ProgramArguments=['/opt/am/python','/opt/am/job_notify.py'],
        StandardOutPath='/opt/am/r.log',StandardErrorPath='/opt/am/r.log',
        StartCalendarInterval={'Hour':4,'Minute':0,'Weekday':0},RunAtLoad=False,Umask=63),
}


def write_plists(directory):
    for data in PLISTS.values():
        with open(directory/(data['Label']+'.plist'),'wb') as stream: plistlib.dump(data,stream)
    return directory


class FakeLaunchd:
    domain='gui/501'

    def __init__(self, busy=None):
        self.busy,self.loaded,self.calls=dict(busy or {}),{u.name for u in UNITS},[]

    def target(self, unit): return self.domain+'/'+unit.label
    def runner(self, argv): self.calls.append(tuple(argv)); return (0,'')

    def status(self, unit):
        left=self.busy.get(unit.name,0)
        if left: self.busy[unit.name]=left-1
        return dict(IDLE,loaded=unit.name in self.loaded,running=left>0)

    def bootout(self, unit): self.calls.append(('bootout',unit.name)); self.loaded.discard(unit.name); return True

    def bootstrap(self, unit, plist):
        self.calls.append(('bootstrap',unit.name,str(plist))); self.loaded.add(unit.name); return True


class Presence:
    def __init__(self, answer): self.answer,self.seen=answer,[]
    def confirm(self, action, summary): self.seen.append((action,summary)); return self.answer


def ok_call(layout, op, args): return dict(id='x',ok=True,result={})
