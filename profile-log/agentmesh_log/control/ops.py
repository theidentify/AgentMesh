"""Control operations: exact schemas, tiers, rate limits and the fail-closed audit rule.

Tier 0 reads are never audited. Every tier 1 and 2 action is audited as `requested` before it runs;
if that write fails the action is refused, so nothing changes without a record.
"""
import json
import platform
import sys
import threading
import time
from ..broker.audit import append_row
from . import health, maintenance, projects, quota
from .moves import MoveError
from .units import UNITS

RESTARTS_PER_HOUR=3
AUDIT_TAIL_MAX=200
ENVELOPE=frozenset({'v','id','op','args'})
OPS={  # op: (tier, exact argument names)
    'status':(0,frozenset()),'units':(0,frozenset()),'quota.status':(0,frozenset()),
    'audit.tail':(0,frozenset({'limit'})),
    'unit.start':(1,frozenset({'unit'})),'unit.stop':(1,frozenset({'unit'})),'unit.restart':(1,frozenset({'unit'})),
    'unit.reset':(1,frozenset({'unit'})),
    'maintenance.on':(1,frozenset({'reason'})),'maintenance.off':(1,frozenset()),'diagnose':(1,frozenset()),
    'unit.backend':(2,frozenset({'unit','to'})),'moves.get':(0,frozenset({'move_id'})),
    'mapping.set':(2,frozenset({'project','target'})),
}


class OpError(Exception):
    def __init__(self, code, message=''):
        super().__init__(code); self.code,self.message=code,message


class Control:
    def __init__(self, layout, backend, *, clock=time.monotonic, units=UNITS, moves=None, mappings=None):
        self.layout,self.backend,self.clock,self.units,self.moves=layout,backend,clock,units,moves
        self.mappings=mappings
        self.lock=threading.Lock(); self.recent={}

    def handle(self, request, caller):
        """One request in, one response out; never raises."""
        rid=request.get('id') if isinstance(request,dict) and type(request.get('id')) is str else None
        op=None
        try:
            op,args=self.validate(request)
            if OPS[op][0]>=1: self.record(op,args,caller,'requested')
            result=getattr(self,'op_'+op.replace('.','_'))(**args)
            if OPS[op][0]>=1: self.record(op,args,caller,'ok',strict=False)
            return dict(id=rid,ok=True,result=result)
        except OpError as error:
            if op is not None and OPS[op][0]>=1 and error.message!='audit_unavailable':
                self.record(op,request['args'],caller,error.code,strict=False)
            return dict(id=rid,ok=False,error=dict(code=error.code,message=error.message))
        except Exception:
            if op is not None and OPS[op][0]>=1: self.record(op,request['args'],caller,'failed',strict=False)
            return dict(id=rid,ok=False,error=dict(code='failed',message=''))

    def validate(self, request):
        if (not isinstance(request,dict) or set(request)!=ENVELOPE or type(request['v']) is not int or request['v']!=1
                or type(request['id']) is not str or not 1<=len(request['id'])<=64):
            raise OpError('bad_args')
        op,args=request['op'],request['args']
        if type(op) is not str or op not in OPS: raise OpError('unknown_op')
        if not isinstance(args,dict) or set(args)!=OPS[op][1]: raise OpError('bad_args')
        return op,args

    def record(self, op, args, caller, outcome, strict=True):
        row=dict(ts=time.time_ns()//1000000,op=op,args=args,outcome=outcome,
            caller_pid=caller.get('pid'),caller_exe=caller.get('exe'))
        try: self.layout.ensure(); append_row(self.layout.audit,row)
        except Exception:
            if strict: raise OpError('failed','audit_unavailable') from None

    def unit(self, name):
        for unit in self.units:
            if type(name) is str and unit.name==name: return unit
        raise OpError('bad_args','unknown_unit')

    def runnable(self, unit):
        if unit.wrapped and maintenance.status(self.layout)['on']: raise OpError('maintenance')
        if self.backend.status(unit).get('failed'): raise OpError('failed','unit_failed')

    def throttle(self, unit):
        with self.lock:
            now=self.clock(); window=[t for t in self.recent.get(unit.name,()) if now-t<3600]
            if len(window)>=RESTARTS_PER_HOUR: raise OpError('rate_limited')
            window.append(now); self.recent[unit.name]=window

    def op_status(self):
        units=health.observe(self.layout,self.backend,self.units)
        for row in units: row['move']=self.moves.current(row['name']) if self.moves is not None else None
        return dict(units=units,projects=dict(pending=projects.pending(self.layout),scopes=projects.scopes(self.layout)),
            maintenance=maintenance.status(self.layout),quota=quota.status(self.layout))

    def op_units(self):
        return [dict(name=u.name,label=u.label,kind=u.kind,wrapped=u.wrapped,max_runtime_s=u.max_runtime_s,native_ok=u.native_ok) for u in self.units]

    def op_quota_status(self): return quota.status(self.layout)

    def op_audit_tail(self, limit):
        if type(limit) is not int or not 1<=limit<=AUDIT_TAIL_MAX: raise OpError('bad_args')
        try: lines=self.layout.audit.read_bytes().splitlines()[-limit:]
        except FileNotFoundError: return []
        return [json.loads(line) for line in lines]

    def op_unit_start(self, unit):
        unit=self.unit(unit); self.runnable(unit); self.throttle(unit); self.backend.start(unit)
        return dict(unit=unit.name,action='start')

    def op_unit_restart(self, unit):
        unit=self.unit(unit); self.runnable(unit); self.throttle(unit); self.backend.restart(unit)
        return dict(unit=unit.name,action='restart')

    def op_unit_stop(self, unit):
        unit=self.unit(unit)
        if unit.kind!='job': raise OpError('bad_args','service_keepalive')
        self.backend.stop(unit)
        return dict(unit=unit.name,action='stop')

    def op_unit_reset(self, unit):
        unit=self.unit(unit); reset=getattr(self.backend,'reset',None)
        return dict(unit=unit.name,cleared=bool(reset(unit)) if reset else False)

    def op_maintenance_on(self, reason):
        try: return maintenance.turn_on(self.layout,reason)
        except ValueError: raise OpError('bad_args') from None

    def op_maintenance_off(self): return maintenance.turn_off(self.layout)

    def op_unit_backend(self, unit, to):
        """Tier 2: the supervisor's own presence check (Touch ID or the Mac password), then the move on a worker."""
        if self.moves is None: raise OpError('presence_unavailable')
        self.unit(unit)
        try: return self.moves.request(unit,to)
        except MoveError as error: raise OpError(error.code) from None

    def op_mapping_set(self, project, target):
        """Tier 2: add a scope for a new project catch-up is holding back, after the supervisor's presence check."""
        if self.mappings is None: raise OpError('presence_unavailable')
        try: return self.mappings.set(project,target)
        except MoveError as error: raise OpError(error.code) from None

    def op_moves_get(self, move_id):
        if self.moves is None: raise OpError('unknown_move')
        try: return self.moves.get(move_id)
        except MoveError as error: raise OpError(error.code) from None

    def op_diagnose(self):
        return dict(status=self.op_status(),units=self.op_units(),python=sys.version.split()[0],
            platform=platform.platform(terse=True))
