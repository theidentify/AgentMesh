"""unit.backend: the supervisor moves a unit between launchd and itself, only after its own presence check.

The whole migrate.adopt/release runs on one worker thread, with the CLI's code unchanged. Its confirm() goes through a
Gate that tells the waiting request thread the verdict at once, so the client gets accepted/denied in seconds while the
worker may still wait up to 10 minutes for a launchd job to go idle. One move at a time; at most 3 prompts per 10 min,
denials included, so an agent cannot keep raising sheets and hope for a reflexive approval.
"""
import threading
import time
import uuid
from . import migrate

PROMPTS=3
WINDOW_S=600
DECIDE_S=90  # the helper's 75 s subprocess timeout plus slack
KEEP_S=3600


class MoveError(Exception):
    def __init__(self, code): super().__init__(code); self.code=code


class Gate:
    """Presence for the worker: the real check, then wakes the request thread with the verdict."""
    def __init__(self, presence, on_prompt):
        self.presence,self.on_prompt=presence,on_prompt
        self.decided=threading.Event(); self.approved=False

    def confirm(self, action, summary):
        self.on_prompt()
        try: self.approved=bool(self.presence.confirm(action,summary))
        except Exception: self.approved=False
        self.decided.set()
        return self.approved


class Moves:
    def __init__(self, layout, store, presence, *, launchd=None, agents=None, adopt=migrate.adopt, release=migrate.release,
                 clock=time.monotonic, thread=threading.Thread, decide_s=DECIDE_S):
        self.layout,self.store,self.presence,self.launchd,self.agents=layout,store,presence,launchd,agents
        self.adopt,self.release,self.clock,self.thread,self.decide_s=adopt,release,clock,thread,decide_s
        self.lock=threading.Lock(); self.prompts=[]; self.moves={}; self.active=None

    def prompted(self):
        with self.lock: self.prompts.append(self.clock())

    def request(self, name, to):
        if to not in ('native','launchd') or migrate.native_unit(name) is None: raise MoveError('bad_args')
        with self.lock:
            now=self.clock()
            self.moves={k:v for k,v in self.moves.items() if v['ended_at'] is None or now-v['ended_at']<KEEP_S}
            if self.active is not None: raise MoveError('busy')
            self.prompts=[t for t in self.prompts if now-t<WINDOW_S]
            if len(self.prompts)>=PROMPTS: raise MoveError('rate_limited')
            move_id=uuid.uuid4().hex
            self.active=move_id; self.moves[move_id]=dict(move_id=move_id,unit=name,to=to,state='deciding',
                started_at=now,ended_at=None)
        try:
            if not self.presence.available(): raise MoveError('presence_unavailable')
            gate=Gate(self.presence,self.prompted); finished=threading.Event()
            worker=self.thread(target=self.work,args=(move_id,name,to,gate,finished),daemon=True)
            worker.start()
            if not self.wait(gate,finished): raise MoveError('presence_required')
        except MoveError as error:
            self.finish(move_id,error.code); raise
        with self.lock:
            move=self.moves[move_id]
            if move['ended_at'] is not None and move['state']!='ok': raise MoveError(move['state'])
            if move['state']=='deciding': move['state']='moving'
        return dict(accepted=True,move_id=move_id)

    def wait(self, gate, finished):
        """True once presence approved; False when it denied or never decided. An early worker error also returns
        True here, and request() then reports that error code."""
        deadline=self.clock()+self.decide_s
        while self.clock()<deadline:
            if gate.decided.wait(0.05): return gate.approved
            if finished.is_set(): return True
        return False

    def work(self, move_id, name, to, gate, finished):
        try:
            if to=='native':
                result=self.adopt(self.layout,self.store,name,console=gate,call=lambda *a:{'ok':True},
                    launchd=self.launchd,agents=self.agents)
            else: result=self.release(self.layout,self.store,name,console=gate,launchd=self.launchd,agents=self.agents)
            state='ok' if result.get('ok') else result.get('error','failed')
        except Exception: state='failed'
        self.finish(move_id,state); finished.set()

    def finish(self, move_id, state):
        with self.lock:
            move=self.moves.get(move_id)
            if move is not None and move['ended_at'] is None: move['state'],move['ended_at']=state,self.clock()
            if self.active==move_id: self.active=None

    def get(self, move_id):
        with self.lock:
            move=self.moves.get(move_id) if type(move_id) is str else None
            if move is None: raise MoveError('unknown_move')
            return dict(move_id=move['move_id'],unit=move['unit'],to=move['to'],state=move['state'])

    def current(self, name):
        """The in-flight move of this unit for status rows, else None."""
        with self.lock:
            move=self.moves.get(self.active) if self.active else None
            if move is None or move['unit']!=name: return None
            return dict(move_id=move['move_id'],to=move['to'],state=move['state'])
