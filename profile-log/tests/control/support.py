IDLE=dict(loaded=True,running=False,pid=None,last_exit=0,runtime_s=None)


class FakeBackend:
    def __init__(self, statuses=None, fail=False):
        self.statuses,self.fail,self.calls=statuses or {},fail,[]

    def status(self, unit): return self.statuses.get(unit.name,IDLE)

    def _act(self, action, unit):
        if self.fail: raise RuntimeError('launchctl_failed')
        self.calls.append((action,unit.name)); return True

    def start(self, unit): return self._act('start',unit)
    def restart(self, unit): return self._act('restart',unit)
    def stop(self, unit): return self._act('stop',unit)
