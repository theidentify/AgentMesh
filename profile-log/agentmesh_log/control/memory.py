"""memory.*: the owner's view of the memory tree and the plaintext export, behind the supervisor's own presence.

The supervisor reads through the broker with the agent key in settings.local.json `memory_tree.agent_key`, so
the broker's grant, label and audit rules still apply. Viewing needs one presence check, then stays open while it
is used and locks after IDLE_S without a read. Every export needs its own presence check. Prompts share the tier 2
slot and rate limit with backend moves, so an agent cannot keep raising sheets.
"""
import threading
import time
from .moves import MoveError

IDLE_S=300


class Memory:
    def __init__(self, layout, presence, moves, *, settings=None, client_factory=None, clock=time.monotonic, home=None):
        self.layout,self.presence,self.moves,self.clock=layout,presence,moves,clock
        self.settings,self.client_factory,self.home=settings,client_factory,home
        self.lock=threading.Lock(); self.until=0.0

    def key(self):
        load=self.settings
        if load is None: from ..settings import load
        try: path=load().get('memory_tree',{}).get('agent_key')
        except (OSError,ValueError): path=None
        if not path: raise MoveError('not_configured')
        return path

    def client(self):
        factory=self.client_factory
        if factory is None:
            from ..broker.client import BrokerClient
            factory=BrokerClient.from_file
        try: return factory(self.key())
        except MoveError: raise
        except Exception: raise MoveError('broker_unavailable') from None

    def ask(self, action, summary):
        self.moves.hold()
        try:
            if not self.presence.available(): raise MoveError('presence_unavailable')
            self.moves.prompted()
            if not self.presence.confirm(action,summary): raise MoveError('presence_required')
        finally: self.moves.unhold()

    def unlock(self):
        self.key()
        self.ask('read','View AgentMesh memory in the control app. It locks after %d minutes without use.'%(IDLE_S//60))
        with self.lock: self.until=self.clock()+IDLE_S
        return dict(unlocked=True,idle_s=IDLE_S)

    def relock(self):
        with self.lock: self.until=0.0
        return dict(unlocked=False)

    def touch(self):
        with self.lock:
            now=self.clock()
            if now>=self.until: raise MoveError('locked')
            self.until=now+IDLE_S

    def broker(self, request):
        self.touch(); client=self.client()
        try: result=client.request(request)
        except Exception: raise MoveError('broker_unavailable') from None
        finally: client.close()
        if 'error' in result: raise MoveError(result['error'] if result['error'] in ('denied','presence_required','busy') else 'broker_error')
        return result

    def ls(self, path, depth):
        if type(path) is not str or type(depth) is not int: raise MoveError('bad_args')
        return self.broker(dict(op='ls',path=path,depth=depth))

    def cat(self, path):
        if type(path) is not str: raise MoveError('bad_args')
        return self.broker(dict(op='cat',path=path))

    def export(self, path, folder, include_internal, allow_synced):
        from pathlib import Path
        from .. import export
        if type(path) is not str or type(include_internal) is not bool or type(allow_synced) is not bool: raise MoveError('bad_args')
        try: target=export.check_target(folder,home=self.home or Path.home(),allow_synced=allow_synced)
        except ValueError as error: raise MoveError(str(error)) from None
        labels=('general','internal') if include_internal else ('general',)
        self.key()
        self.ask('export','Write AgentMesh memory under %s as PLAINTEXT files to %s (labels: %s).'%(path,target,', '.join(labels)))
        client=self.client()
        try:
            me=client.request(dict(op='whoami'))
            files,secret=export.collect(client,path,labels)
            count=export.write(target,files,path=path,labels=labels,secret=secret,device=me.get('device_id','unknown'))
        except ValueError as error: raise MoveError(str(error) if str(error) in ('denied','too_many_files','bad_path') else 'export_failed') from None
        except OSError: raise MoveError('export_failed') from None
        finally: client.close()
        return dict(folder=str(target),files=count,secret_skipped=secret,labels=list(labels))
