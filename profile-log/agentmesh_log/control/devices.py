"""device.*: revoke another device of this account from the control app (M3e), behind the supervisor's presence check.

The supervisor runs the same `remove_device` as `agentmesh-log device remove`: it publishes a signed revocation and
rotates the keys of every scope the device could read. Touch ID is asked every time (no presence window), with the
device's name in the prompt, and the prompt shares the tier 2 slot and rate limit with backend moves and approvals.
A device never revokes itself; that is refused before any prompt.
"""
import re
from .connector import broker_context
from .moves import MoveError

PASSTHROUGH=frozenset({'presence_required'})
CODES={'cannot remove current device':'current_device','unknown device':'unknown_device','revoked device':'already_revoked',
       'insufficient rotation authority':'insufficient_authority'}


class Devices:
    def __init__(self, layout, presence, moves, *, context=None):
        self.layout,self.presence,self.moves=layout,presence,moves
        self.context=context or (lambda: broker_context(layout))

    @staticmethod
    def code(error):
        text=str(error)
        return text if text in PASSTHROUGH else CODES.get(text,'failed')

    def remove(self, device_id):
        from ..lifecycle import remove_device
        if type(device_id) is not str or not re.fullmatch('[0-9a-f]{32}',device_id): raise MoveError('bad_args')
        self.moves.hold()
        try:
            if not self.presence.available(): raise MoveError('presence_unavailable')
            home,store,storage=self.context()
            state=store.load()
            if device_id==state.device.device_id: raise MoveError('current_device')
            registry=state.registry(); registry=getattr(registry,'own',registry)   # this account's devices only
            if device_id not in registry.certificates: raise MoveError('unknown_device')
            if device_id in registry.revocations: raise MoveError('already_revoked')
            summary=('Revoke device %s. It can no longer read or sync this account, and the keys of every scope it '
                     'could read are rotated.'%device_id)
            prompted=self.moves.prompted
            presence=self.presence
            class Sheet:
                """The supervisor's helper is the only presence: counted against the prompt rate limit when shown."""
                def confirm(self, action, text): prompted(); return bool(presence.confirm(action,text))
            with home.lock(wait=10):
                remove_device(home,store,storage,device_id,console=Sheet(),one_shot=True,summary=summary)
        except MoveError: raise
        except FileExistsError: raise MoveError('busy') from None
        except Exception as error: raise MoveError(self.code(error)) from None
        finally: self.moves.unhold()
        return {'device_id':device_id,'revoked':True}
