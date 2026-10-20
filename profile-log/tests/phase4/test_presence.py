import json
import pytest
from agentmesh_log.presence import ConsolePresence, PresenceManager
from agentmesh_log.broker.keystore import BrokerState, BrokerHome, FileKeyStore
from agentmesh_log.identity import AgentIdentity
from agentmesh_log.broker.grants import Grant
from agentmesh_log.broker.service import Broker, Session


def test_p4_7_console_windows(tmp_path):
    home=BrokerHome(tmp_path/'home'); state=BrokerState.create(['work']); store=FileKeyStore(home); store.save(state)
    clock=[1000]
    p=PresenceManager(home,store,clock=lambda:clock[0])
    no=ConsolePresence(input_fn=lambda _: 'remove',isatty=lambda:False,output=lambda _:None)
    with pytest.raises(ValueError,match='presence_required'): p.require('device','remove',console=no)
    yes=ConsolePresence(input_fn=lambda _: 'remove',isatty=lambda:True,output=lambda _:None)
    p.require('device','remove',console=yes)
    assert p.active('device')
    assert not p.active('recovery')
    p.require('device','remove')
    clock[0]+=900001
    with pytest.raises(ValueError): p.require('device','remove')
    (home.root/'presence-windows.json').write_text('{"device":9999999999999}',encoding='utf-8')
    with pytest.raises(ValueError): p.require('device','remove')
    rows=[json.loads(line) for line in (home.root/'presence.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(rows)==5 and rows[2]['window_used'] and not rows[0]['window_used']
    before=store.path.read_bytes()
    p.require('recovery','test',console=ConsolePresence(input_fn=lambda _:'test',isatty=lambda:True,output=lambda _:None),one_shot=True)
    assert before==store.path.read_bytes()
    assert not p.active('recovery')


def test_p4_7_broker_hooks(tmp_path):
    home=BrokerHome(tmp_path/'home'); state=BrokerState.create(['work','personal']); store=FileKeyStore(home)
    agent=AgentIdentity(state.device,state.account.account_id)
    roots=[t.root.scope_id for t in state.trees]
    state.grants[agent.agent_id]=Grant(agent.agent_id,agent.public_bytes.hex(),roots,roots,roots[0],roots[0],True,True)
    store.save(state); b=Broker(home); session=Session(agent.certificate())
    assert b.request(session,{'op':'whoami'})=={'error':'presence_required'}
    p=PresenceManager(home,store)
    for kind in ('first_access','cross_profile','secret_read'):
        p.open(kind,console=ConsolePresence(input_fn=lambda _:'open',isatty=lambda:True,output=lambda _:None))
    assert 'error' not in b.request(session,{'op':'whoami'})
    assert 'error' not in b.request(session,{'op':'recall','query':'test','profiles':['work','personal']})
    current=store.load(); current.control['windows']['cross_profile']=0; store.save(current)
    assert b.request(session,{'op':'recall','query':'test','profiles':['work','personal']})=={'error':'presence_required'}
    b.close()


def test_p4_7_secret_results(tmp_path):
    from agentmesh_log.events import EVENT_DOMAIN
    from agentmesh_log.encoding import typed
    home=BrokerHome(tmp_path/'home'); state=BrokerState.create(['work']); store=FileKeyStore(home)
    agent=AgentIdentity(state.device,state.account.account_id); sid=state.trees[0].root.scope_id
    state.grants[agent.agent_id]=Grant(agent.agent_id,agent.public_bytes.hex(),[sid],[sid],sid,sid,True)
    store.save(state); p=PresenceManager(home,store)
    yes=ConsolePresence(input_fn=lambda _:'open',isatty=lambda:True,output=lambda _:None)
    p.open('first_access',console=yes)
    b=Broker(home); session=Session(agent.certificate())
    result=b.request(session,dict(op='remember',body='classified example',label='secret'))
    signature=agent._private.sign(EVENT_DOMAIN+typed(result['unsigned'])).hex()
    assert 'id' in b.request(session,dict(op='sign',signature=signature))
    assert b.request(session,dict(op='recall',query='classified example'))=={'error':'presence_required'}
    p.open('secret_read',console=yes)
    assert b.request(session,dict(op='recall',query='classified example'))['results']
    state=store.load(); state.control['windows']['secret_read']=0; store.save(state)
    assert b.request(session,dict(op='recall',query='classified example'))=={'error':'presence_required'}
    b.close()


@pytest.mark.parametrize('minutes',[1,7,15])
def test_window_minutes(tmp_path,minutes):
    home=BrokerHome(tmp_path/'home'); store=FileKeyStore(home); store.save(BrokerState.create(['work']))
    clock=[1000]; p=PresenceManager(home,store,clock=lambda:clock[0])
    yes=ConsolePresence(input_fn=lambda _:'open',isatty=lambda:True,output=lambda _:None)
    p.open('secret_read',minutes=minutes,console=yes)
    assert store.load().control['windows']['secret_read']==1000+minutes*60000
    clock[0]+=minutes*60000-1; assert p.active('secret_read')
    clock[0]+=1; assert not p.active('secret_read')


@pytest.mark.parametrize('minutes',[0,16,-1,True,1.5,15.0,'5'])
def test_bad_minutes(tmp_path,minutes):
    home=BrokerHome(tmp_path/'home'); store=FileKeyStore(home); store.save(BrokerState.create(['work']))
    before=store.path.read_bytes(); p=PresenceManager(home,store)
    with pytest.raises(ValueError,match='invalid presence duration'): p.open('device',minutes=minutes)
    assert store.path.read_bytes()==before
    assert not (home.root/'presence.jsonl').exists()


def test_cli_minutes(tmp_path,monkeypatch):
    from agentmesh_log import cli
    home=BrokerHome(tmp_path/'home'); store=FileKeyStore(home); store.save(BrokerState.create(['work']))
    monkeypatch.setattr(cli,'PresenceManager',lambda home,store:PresenceManager(home,store,clock=lambda:1000))
    yes=ConsolePresence(input_fn=lambda _:'open',isatty=lambda:True,output=lambda _:None)
    args=cli.parser().parse_args(['presence','open','--home',str(home.root),'--class','device','--minutes','3'])
    assert cli.execute(args,console=yes)=={'active':True,'action_class':'device'}
    assert store.load().control['windows']['device']==181000
