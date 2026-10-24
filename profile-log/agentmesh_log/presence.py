"""Explicit console approval, protected action windows and plaintext-free triggers."""
import os
import stat
import sys
from typing import Protocol
from .encoding import wire
from .registry_store import now
from .storage.filesystem import _check

CLASSES=frozenset(('device','agent','recovery','export','profile','cross_profile','secret_read','first_access'))
ACTIONS=frozenset(('apply','trust','approve','remove','grant','revoke','create','test','use','export','archive','delete','migrate','open','access','recall','read','regrant','recipient'))


class Presence(Protocol):
    def confirm(self, action, summary) -> bool: ...


class ConsolePresence:
    def __init__(self, *, input_fn=None, isatty=None, output=None):
        self.input=input if input_fn is None else input_fn
        # pythonw (a Windows logon task) has sys.stdin None: no console means no presence, not a crash.
        self.isatty=(lambda: sys.stdin is not None and sys.stdin.isatty()) if isatty is None else isatty
        self.output=print if output is None else output

    def confirm(self,action,summary):
        if action not in ACTIONS or not self.isatty(): return False
        self.output('AgentMesh presence: '+action+'\n'+summary)
        try: return self.input('Type '+action+': ') == action
        except (EOFError,KeyboardInterrupt): return False


class PresenceManager:
    def __init__(self,home,store,*,clock=now): self.home,self.store,self.clock=home,store,clock

    def active(self,kind):
        if kind not in CLASSES: raise ValueError('invalid presence class')
        until=self.store.load().control.get('windows',{}).get(kind,0)
        return type(until) is int and self.clock()<until<=self.clock()+900000

    def log(self,kind,action,outcome,used):
        path=self.home.root/'presence.jsonl'; _check(path)
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND|getattr(os,'O_NOFOLLOW',0),0o600)
        with os.fdopen(fd,'ab') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or (os.name!='nt' and (info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600)):
                raise ValueError('unsafe presence log')
            stream.write(wire(dict(ts=self.clock(),action_class=kind,action=action,outcome=outcome,window_used=used))+b'\n')
            stream.flush(); os.fsync(stream.fileno())

    def require(self,kind,action,*,console=None,one_shot=False,summary='Confirm this operation.'):
        if kind not in CLASSES or action not in ACTIONS: raise ValueError('invalid presence action')
        used=not one_shot and self.active(kind)
        approved=used or (console is not None and console.confirm(action,summary))
        self.log(kind,action,'approved' if approved else 'denied',used)
        if not approved: raise ValueError('presence_required')
        if not used and not one_shot:
            state=self.store.load(); state.control.setdefault('windows',{})[kind]=self.clock()+900000; self.store.save(state)

    def open(self,kind,*,minutes=15,console=None):
        if type(minutes) is not int or not 1<=minutes<=15: raise ValueError('invalid presence duration')
        self.require(kind,'open',console=console,one_shot=True)
        state=self.store.load(); state.control.setdefault('windows',{})[kind]=self.clock()+minutes*60000; self.store.save(state)
