"""device.remove from the control app (M3e): the supervisor's own presence, every time, with the device in the prompt."""
import pytest
from agentmesh_log.control.devices import Devices
from agentmesh_log.control.layout import Layout
from agentmesh_log.control.moves import Moves
from agentmesh_log.control.ops import Control
from agentmesh_log.control.store import Store
from control.support import FakeBackend
from phase4.test_lifecycle import pair


class Sheet:
    def __init__(self, answer=True, available=True): self.answer,self._available,self.seen=answer,available,[]
    def available(self): return self._available
    def confirm(self, action, summary): self.seen.append((action,summary)); return self.answer


def fixture(tmp_path, sheet=None):
    home,store,other,other_store,storage,sid=pair(tmp_path,root=True)
    layout=Layout(tmp_path/'am').ensure(); sheet=sheet or Sheet()
    moves=Moves(layout,Store(layout.state),sheet)
    devices=Devices(layout,sheet,moves,context=lambda:(home,store,storage))
    control=Control(layout,FakeBackend(),moves=moves,devices=devices)
    call=lambda op,args:control.handle(dict(v=1,id='1',op=op,args=args),dict(pid=1,exe='app'))
    return call,sheet,store,other_store.load().device.device_id,sid,layout


def test_remove_revokes_and_rotates(tmp_path):
    call,sheet,store,target,sid,layout=fixture(tmp_path)
    old=store.load().trees[0].keys[sid].key_id
    response=call('device.remove',{'device_id':target})
    assert response['ok'] and response['result']=={'device_id':target,'revoked':True}
    assert sheet.seen==[('remove','Revoke device %s. It can no longer read or sync this account, and the keys of every '
                         'scope it could read are rotated.'%target)]
    after=store.load(); assert target in after.registry().revocations and after.trees[0].keys[sid].key_id!=old
    assert '"op":"device.remove"' in layout.audit.read_text().replace('": "','":"')
    again=call('device.remove',{'device_id':target}); assert again['error']['code']=='already_revoked'
    assert len(sheet.seen)==1  # refused before any prompt


def test_remove_asks_every_time(tmp_path):
    """An open `device` presence window from the CLI never stands in for the app's Touch ID."""
    call,sheet,store,target,sid,layout=fixture(tmp_path)
    state=store.load(); state.control.setdefault('windows',{})['device']=2**52; store.save(state)
    assert call('device.remove',{'device_id':target})['ok'] and len(sheet.seen)==1


@pytest.mark.parametrize('case,expected',[('self','current_device'),('unknown','unknown_device'),('bad','bad_args')],
                         ids=['self','unknown','bad'])
def test_remove_refusals(tmp_path,case,expected):
    call,sheet,store,target,sid,layout=fixture(tmp_path)
    device={'self':store.load().device.device_id,'unknown':'ab'*16,'bad':'ABC'}[case]
    assert call('device.remove',{'device_id':device})['error']['code']==expected and sheet.seen==[]


def test_remove_cancelled(tmp_path):
    call,sheet,store,target,sid,layout=fixture(tmp_path,Sheet(answer=False))
    assert call('device.remove',{'device_id':target})['error']['code']=='presence_required'
    assert target not in store.load().registry().revocations


def test_remove_without_helper(tmp_path):
    call,sheet,store,target,sid,layout=fixture(tmp_path,Sheet(available=False))
    assert call('device.remove',{'device_id':target})['error']['code']=='presence_unavailable' and sheet.seen==[]
