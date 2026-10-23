"""Send each unit to its current backend: launchd unless state.db marks it native."""


class Router:
    def __init__(self, store, launchd, native):
        self.store,self.launchd,self.native=store,launchd,native

    def pick(self, unit): return self.native if self.store.backend(unit.name)=='native' else self.launchd

    def status(self, unit):
        backend=self.store.backend(unit.name)
        status=dict((self.native if backend=='native' else self.launchd).status(unit),backend=backend)
        status.setdefault('failed',False)
        return status

    def start(self, unit): return self.pick(unit).start(unit)
    def restart(self, unit): return self.pick(unit).restart(unit)
    def stop(self, unit): return self.pick(unit).stop(unit)
    def reset(self, unit): return self.native.reset(unit)
