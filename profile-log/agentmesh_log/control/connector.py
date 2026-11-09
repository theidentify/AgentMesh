"""connector.*: approve a Claude connector from the control app, behind the supervisor's own presence check.

The app lists live gateway requests (connector.pending) and pops a window; the owner types the 6-digit code shown in
the browser and the supervisor runs the same approve as `agentmesh-log connector approve`, with its own Touch ID / Mac
password helper as the presence check. The typed code must match the request the window showed, so a look-alike
request cannot be approved by picking the wrong row. Prompts share the tier 2 slot and rate limit with backend moves.

The broker home and R2 remote come from the broker's own launchd definition, so nothing is configured twice.
"""
import argparse
import threading
import time
from .moves import MoveError

CACHE_S=20
PASSTHROUGH=frozenset({'denied','presence_required'})


def broker_context(layout, config=None):
    """(home, store, storage) for the broker this supervisor runs."""
    from ..broker.keystore import BrokerHome
    from ..connector_cli import broker_config, fill_from_broker, storage_for
    from ..oskeystore import select_store
    config=broker_config() if config is None else config
    args=argparse.Namespace(home=config.get('home') or str(layout.root/'profile-log'),filesystem=None,s3_bucket=None,
        s3_prefix=None,s3_endpoint=None,s3_credentials=None)
    fill_from_broker(args,config)
    home=BrokerHome(args.home)
    return home,select_store(home),storage_for(args,home)


class Connector:
    def __init__(self, layout, presence, moves, *, context=None, clock=time.monotonic, cache_s=CACHE_S):
        self.layout,self.presence,self.moves,self.clock,self.cache_s=layout,presence,moves,clock,cache_s
        self.context=context or (lambda: broker_context(layout))
        self.lock=threading.Lock(); self.cached=None; self.cached_at=0.0

    @staticmethod
    def code(error):
        from ..connector_cli import CODES
        text=str(error)
        return text if text in CODES or text in PASSTHROUGH else 'failed'

    def pending(self):
        """Live requests (client name, created, expires, code hash), cached briefly so the app may poll."""
        from ..connector_cli import pending_requests
        with self.lock:
            if self.cached is not None and self.clock()-self.cached_at<self.cache_s: return self.cached
        try:
            home,store,storage=self.context()
            result=pending_requests(store.load(),storage)
        except MoveError: raise
        except Exception as error: raise MoveError(self.code(error)) from None
        result=dict(result,now_ms=int(time.time()*1000))
        with self.lock: self.cached,self.cached_at=result,self.clock()
        return result

    def approve(self, code, code_hash):
        from ..connector_cli import approve
        if type(code) is not str or (code_hash is not None and type(code_hash) is not str): raise MoveError('bad_args')
        self.moves.hold()
        try:
            if not self.presence.available(): raise MoveError('presence_unavailable')
            home,store,storage=self.context()
            prompted=self.moves.prompted
            class Sheet:
                """The supervisor's helper is the only presence: counted against the prompt rate limit when shown."""
                def __init__(self, presence): self.presence=presence
                def confirm(self, action, summary): prompted(); return bool(self.presence.confirm(action,summary))
            with home.lock(wait=10):
                result=approve(home,store,storage,code,None,None,Sheet(self.presence),code_hash=code_hash)
        except MoveError: raise
        except FileExistsError: raise MoveError('busy') from None
        except Exception as error: raise MoveError(self.code(error)) from None
        finally: self.moves.unhold()
        with self.lock: self.cached=None
        return result
