import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from broker.support import setup_home, remember, recall
from agentmesh_log.broker.service import Broker, Session
from agentmesh_log.encoding import typed
from agentmesh_log.events import EVENT_DOMAIN


def request_body(ident=1, digest='a'*64, device=None):
    value=dict(op='import',source=dict(table='memory_items',id=ident),row_hash=digest,
        created_at=1577836800000,scope='work/a',body=dict(kind='fact',content='migrate database',
        project='alpha',task_ref=None,confidence=1.0,metadata=dict(source=dict(table='memory_items',id=ident,
        scope='global',scope_key=None,memory_key=None,version=None,created_at=None,updated_at=None),
        row_hash=digest,evidence_event_ids=[],retired=False)))
    if device is not None:
        value['source']['device']=device
        value['body']['metadata']['source']['device']=device
    return value


def finish(broker, session, agent, response):
    while response.get('stage') == 'sign':
        response=broker.request(session,dict(op='sign',signature=agent._private.sign(EVENT_DOMAIN+typed(response['unsigned'])).hex()))
    return response


def test_p5_7_authority(tmp_path):
    home,state,store,agents=setup_home(tmp_path)
    broker=Broker(home,store=store); agent=agents[0]; session=Session(agent.certificate())
    assert broker.request(session,request_body(device=state.device.device_id)) == {'error':'denied'}
    assert broker.request(session,dict(op='import_ledger')) == {'error':'denied'}
    for key in ('source','row_hash','evidence_event_ids','retired'):
        assert remember(broker,session,agent,dict(kind='fact',content='safe',metadata={key:None})) == {'error':'invalid_request'}
    broker.close()


def test_p5_3_import_and_retire(tmp_path):
    home,state,store,agents=setup_home(tmp_path); agent=agents[0]
    state.grants[agent.agent_id].allow_import=True; store.save(state)
    broker=Broker(home,store=store); s=Session(agent.certificate())
    result=finish(broker,s,agent,broker.request(s,request_body(device=state.device.device_id)))
    assert result['outcome']=='created'
    assert broker.request(s,request_body(device=state.device.device_id)) == dict(outcome='unchanged',id=result['id'])
    result=finish(broker,s,agent,broker.request(s,request_body(device=state.device.device_id,digest='b'*64)))
    assert result['outcome']=='revised'
    req=request_body(device=state.device.device_id); req['body']=None
    assert finish(broker,s,agent,broker.request(s,req))['outcome']=='retired'
    assert recall(broker,s)['results']==[]
    broker.close()


def test_p5_4_reservation(tmp_path):
    home,state,store,agents=setup_home(tmp_path); agent=agents[0]
    state.grants[agent.agent_id].allow_import=True; store.save(state)
    broker=Broker(home,store=store); a=Session(agent.certificate()); b=Session(agent.certificate())
    first=broker.request(a,request_body(device=state.device.device_id))
    assert first.get('stage')=='sign'
    assert broker.request(b,request_body(device=state.device.device_id))=={'error':'busy'}
    finish(broker,a,agent,first)
    assert broker.request(b,request_body(device=state.device.device_id))['outcome']=='unchanged'
    broker.close()


def test_p5_6_compat_selection(tmp_path):
    home,state,store,agents=setup_home(tmp_path); agent=agents[0]
    state.grants[agent.agent_id].allow_import=True; store.save(state)
    broker=Broker(home,store=store); s=Session(agent.certificate())
    finish(broker,s,agent,broker.request(s,request_body(device=state.device.device_id)))
    req=request_body(2,device=state.device.device_id); req['source']['table']='memory_summaries'
    req['body']['metadata']['source'].update(table='memory_summaries',scope='project',scope_key='alpha',version=1)
    req['body'].update(kind='summary',content='unrelated summary')
    finish(broker,s,agent,broker.request(s,req))
    result=recall(broker,s,compat=True,project='alpha')
    assert result['candidate_counts']==dict(durable=1,summaries=1,events=0)
    assert {r['source_type'] for r in result['results']}=={'durable','summary'}
    assert all(r['source_ref']['id'] in (1,2) for r in result['results'])
    assert len(recall(broker,s,compat=True,project='other')['results'])==1
    broker.close()


def test_p5_3_remap_restart(tmp_path):
    home,state,store,agents=setup_home(tmp_path); agent=agents[0]
    from agentmesh_log.broker.grants import resolve
    grant=state.grants[agent.agent_id]; grant.allow_import=True
    grant.write.append(resolve(state,'work/b')); grant.read.append(resolve(state,'work/b')); store.save(state)
    broker=Broker(home,store=store); s=Session(agent.certificate())
    finish(broker,s,agent,broker.request(s,request_body(device=state.device.device_id)))
    req=request_body(device=state.device.device_id,digest='c'*64); req['scope']='work/b'
    first=broker.request(s,req)
    second=broker.request(s,dict(op='sign',signature=agent._private.sign(EVENT_DOMAIN+typed(first['unsigned'])).hex()))
    assert second.get('stage')=='sign'
    assert finish(broker,s,agent,second)['outcome']=='created'
    assert len(recall(broker,s)['results'])==1
    broker.close(); broker=Broker(home,store=store); s=Session(agent.certificate())
    assert broker.request(s,req)['outcome']=='unchanged'
    assert len(broker.imports.ledger(grant)[0]['chain'])==3
    req=request_body(device=state.device.device_id)
    assert finish(broker,s,agent,broker.request(s,req))['outcome']=='created'
    assert broker.request(s,req)['outcome']=='unchanged'
    broker.close()


def test_p5_4_discard_reservation(tmp_path):
    home,state,store,agents=setup_home(tmp_path); agent=agents[0]
    state.grants[agent.agent_id].allow_import=True; store.save(state)
    broker=Broker(home,store=store); a=Session(agent.certificate()); b=Session(agent.certificate())
    assert broker.request(a,request_body(device=state.device.device_id))['stage']=='sign'
    assert broker.request(a,dict(op='invalid'))=={'error':'invalid_request'}
    assert broker.request(b,request_body(device=state.device.device_id))['stage']=='sign'
    broker.imports.release(b)
    assert broker.request(a,request_body(device=state.device.device_id))['stage']=='sign'
    broker.close()


def test_p5_7_replica_forgery(tmp_path):
    from agentmesh_log.events import seal_event
    from agentmesh_log.model import Label
    home,state,store,agents=setup_home(tmp_path); agent=agents[0]
    broker=Broker(home,store=store)
    sid=state.grants[agent.agent_id].default_write; key=state.trees[0].keys[sid]
    data=seal_event(key.write_key,agent,request_body(device=state.device.device_id)['body'],label=Label.general)
    envelope=broker.replica.put_event(data); broker.ingest(envelope,data)
    assert not broker.imports.entries
    broker.close()


def test_p5_5_secret_and_metadata(tmp_path):
    import secrets
    from agentmesh_log.broker.replica import Replica
    home,state,store,agents=setup_home(tmp_path); agent=agents[0]
    state.grants[agent.agent_id].allow_import=True; store.save(state)
    broker=Broker(home,store=store); s=Session(agent.certificate())
    secret='ghp_'+secrets.token_hex(20)
    req=request_body(device=state.device.device_id); req['body']['content']='synthetic marker '+secret
    before=broker.replica.outbox()
    result=finish(broker,s,agent,broker.request(s,req))
    assert result['label']=='secret' and result['redactions']
    assert broker.replica.outbox()==before+1
    for _,payload in broker.payloads(): assert secret not in str(payload)
    broker.close()
    for path in home.root.rglob('*'):
        if path.is_file():
            data=path.read_bytes()
            for encoding in ('utf-8','utf-16-le','utf-16-be'): assert req['body']['content'].encode(encoding) not in data


def test_no_ordinary_revision(tmp_path):
    home,state,store,agents=setup_home(tmp_path); agent=agents[0]
    state.grants[agent.agent_id].allow_import=True; store.save(state)
    broker=Broker(home,store=store); session=Session(agent.certificate())
    result=finish(broker,session,agent,broker.request(session,request_body(device=state.device.device_id)))
    assert broker.request(session,dict(op='revise',body='ordinary replacement',revises=result['id']))=={'error':'denied'}
    broker.close()


def test_compat_task(tmp_path):
    home,state,store,agents=setup_home(tmp_path); agent=agents[0]
    broker=Broker(home,store=store);session=Session(agent.certificate())
    remember(broker,session,agent,dict(kind='fact',content='unrelated prose',task_ref='SYN-9'))
    assert len(recall(broker,session,compat=True,task='SYN-9')['results'])==1
    broker.close()


def test_import_scope(tmp_path):
    home,state,store,agents=setup_home(tmp_path);agent=agents[0]
    state.grants[agent.agent_id].allow_import=True;store.save(state)
    broker=Broker(home,store=store);session=Session(agent.certificate())
    try:
        req=request_body(device=state.device.device_id);req['scope']='work/b'
        assert broker.request(session,req)=={'error':'denied'}
        result=remember(broker,session,agent,'ordinary memory')
        for key in ('source','row_hash','evidence_event_ids','retired'):
            req=dict(op='revise',revises=result['id'],body=dict(kind='fact',content='synthetic',metadata={key:None}))
            assert broker.request(session,req)=={'error':'invalid_request'}
    finally: broker.close()


def test_retired_summary_hidden(tmp_path):
    # Summaries need no text match, so an empty retired head must still be hidden.
    home,state,store,agents=setup_home(tmp_path); agent=agents[0]
    state.grants[agent.agent_id].allow_import=True; store.save(state)
    broker=Broker(home,store=store); s=Session(agent.certificate())
    req=request_body(2,device=state.device.device_id); req['source']['table']='memory_summaries'
    req['body']['metadata']['source'].update(table='memory_summaries',scope='project',scope_key='alpha',version=1)
    req['body'].update(kind='summary',content='project summary')
    finish(broker,s,agent,broker.request(s,req))
    assert recall(broker,s,compat=True,project='alpha')['candidate_counts']['summaries']==1
    req['body']=None
    assert finish(broker,s,agent,broker.request(s,req))['outcome']=='retired'
    result=recall(broker,s,compat=True,project='alpha')
    assert result['candidate_counts']['summaries']==0 and result['results']==[]
    broker.close()


def test_future_timestamp(tmp_path):
    import time
    home,state,store,agents=setup_home(tmp_path); agent=agents[0]
    state.grants[agent.agent_id].allow_import=True; store.save(state)
    broker=Broker(home,store=store); s=Session(agent.certificate())
    req=request_body(device=state.device.device_id); req['created_at']=int(time.time()*1000)+10*60*1000
    assert broker.request(s,req)=={'error':'invalid_request'}
    req['created_at']=int(time.time()*1000)+60*1000
    assert finish(broker,s,agent,broker.request(s,req))['outcome']=='created'
    broker.close()
