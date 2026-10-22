import base64
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import pytest
from agentmesh_log.broker.service import Broker, Session
from agentmesh_log.broker.client import BrokerClient
from agentmesh_log.broker.framing import encode, decode
from agentmesh_log.broker.keystore import atomic_write, FileKeyStore, BrokerHome
from agentmesh_log.broker.grants import resolve
from agentmesh_log.broker.transport import UnixServer, PipeServer
from agentmesh_log.encoding import wire, parse, typed
from agentmesh_log.events import EVENT_DOMAIN
from agentmesh_log.identity import AgentIdentity
from .support import setup_home, remember, recall
from .test_cli import cli, PACKAGE


class Channel:
    def __init__(self, incoming, outgoing, sent): self.incoming,self.outgoing,self.sent=incoming,outgoing,sent
    def send(self,obj):
        data=encode(obj); self.sent.append(data); self.outgoing.put(data)
    def receive(self):
        data=self.incoming.get(timeout=5)
        if data is None: raise EOFError()
        return decode(data)
    def close(self): self.outgoing.put(None)


def connect(broker,agent):
    q1,q2=queue.Queue(),queue.Queue(); sent=[]
    server=Channel(q1,q2,sent); client=Channel(q2,q1,[])
    thread=threading.Thread(target=broker.connection,args=(server,),daemon=True); thread.start()
    return BrokerClient(agent,broker.state.device.public_bytes,client),sent,thread


def keyfile(home,state,agent):
    path=home.root/'agents'/(agent.agent_id+'.key')
    atomic_write(path,wire(dict(format='agentmesh-agent-key-v1',agent_cert=agent.certificate(),
        agent_private_key=agent.private_bytes().hex(),device_public_key=state.device.public_bytes.hex(),
        address=home.address(state.device.device_id))))
    return path


def test_p3_1_processes():
    root=Path(tempfile.mkdtemp(prefix='am',dir=None if os.name=='nt' else '/tmp')).resolve()
    broker=None
    try:
        home,state,store,agents=setup_home(root); broker=Broker(home)
        try: server=(PipeServer if os.name=='nt' else UnixServer)(home.address(state.device.device_id))
        except PermissionError: pytest.fail('P3-1 blocked: sandbox denies socket binding',pytrace=False)
        threading.Thread(target=broker.serve,kwargs={'server':server},daemon=True).start()
        keys=[keyfile(home,state,a) for a in agents]
        for key in keys:
            result=cli('remember','--agent-key',key,'migrate database')
            assert result.returncode==0
        # Each subprocess loads only its own agent key and connects through real IPC.
        responses=[]
        for key in keys:
            p=subprocess.Popen([sys.executable,'-m','agentmesh_log.cli','recall','migrate database','--agent-key',str(key)],
                env={**os.environ,'PYTHONPATH':PACKAGE,'PYTHONDONTWRITEBYTECODE':'1'},stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True, encoding='utf-8')
            responses.append(p)
        assert responses[0].pid!=responses[1].pid
        for process,name in zip(responses,('work/a','work/b')):
            out,err=process.communicate(timeout=15)
            assert process.returncode==0
            assert {r['scope'] for r in json.loads(out)['results']}=={name}
        client=BrokerClient.from_file(keys[0])
        assert client.request({'op':'recall','query':'migrate database','scopes':['work/b']})=={'error':'denied'}
        assert client.write('migrate database',scope='work/b')=={'error':'denied'}
        bid=json.loads(cli('recall','migrate database','--agent-key',keys[1]).stdout)['results'][0]['id']
        assert client.write('migrate database',scope='work/b',revises=bid)=={'error':'denied'}
        grant=state.grants[agents[1].agent_id]; grant.read=[state.trees[0].root.scope_id]
        grant.write=list(state.trees[0].scopes); store.save(state)
        parent=BrokerClient.from_file(keys[1])
        parent.write('migrate database',scope='work'); parent.write('migrate database',scope='work/common')
        assert {r['scope'] for r in parent.request({'op':'recall','query':'migrate database'})['results']}=={'work','work/common','work/a','work/b'}
        assert {r['scope'] for r in client.request({'op':'recall','query':'migrate database'})['results']}=={'work/a'}
        parent.close(); client.close()
    finally:
        if broker: broker.close()
        shutil.rmtree(root)


def test_p3_4_indexes(tmp_path):
    home,state,store,agents=setup_home(tmp_path); broker=Broker(home)
    sessions=[Session(a.certificate()) for a in agents]
    for s,a in zip(sessions,agents): remember(broker,s,a)
    for s in sessions: recall(broker,s)
    a,b=[broker.indexes[x.agent_id] for x in agents]
    assert a is not b and a.connection is not b.connection
    assert {r['scope'] for r in a.rows()}=={'work/a'}
    assert {r['scope'] for r in b.rows()}=={'work/b'}
    paths=[home.root/'index'/(x.agent_id+'.snap') for x in agents]
    assert paths[0]!=paths[1] and all(p.exists() for p in paths)
    broker.close()  # Flush lazy snapshots before swapping the persisted agent views.
    paths[1].write_bytes(paths[0].read_bytes()); broker=Broker(home)
    assert {r['scope'] for r in recall(broker,sessions[1])['results']}=={'work/b'}
    g=state.grants[agents[1].agent_id]; g.read=state.grants[agents[0].agent_id].read[:]; store.save(state)
    for s in sessions: recall(broker,s)
    assert broker.indexes[agents[0].agent_id] is not broker.indexes[agents[1].agent_id]
    assert broker.indexes[agents[0].agent_id].rows()==broker.indexes[agents[1].agent_id].rows()


def test_p3_5_at_rest(tmp_path):
    home,state,store,agents=setup_home(tmp_path); broker=Broker(home)
    client,sent,thread=connect(broker,agents[0])
    markers=['bodyMarkerQZ','tagMarkerQZ','taskMarkerQZ']
    assert 'id' in client.write(markers[0],tags=[markers[1]],task=markers[2])
    assert client.request({'op':'recall','query':markers[0]})['results']
    client.close(); thread.join(5)
    for path in home.root.rglob('*'):
        if not path.is_file(): continue
        data=path.read_bytes()
        for marker in markers:
            for encoding in ('utf-8','utf-16-le','utf-16-be'): assert marker.encode(encoding) not in data


def test_p3_6_no_keys(tmp_path):
    home,state,store,agents=setup_home(tmp_path); broker=Broker(home)
    client,sent,thread=connect(broker,agents[0])
    assert set(client.request({'op':'whoami'}))=={'agent_id','device_id','read','write','default_profile','allow_secret'}
    first=client.write('migrate database'); assert set(first)=={'id','label','redactions'}
    assert set(client.write('migrate database revision',revises=first['id']))=={'id','label','redactions'}
    client.request({'op':'recall','query':'migrate database'}); client.request({'op':'missing'})
    client.close(); thread.join(5)
    keys=[state.index_master_key,state.audit_key,*[k.private_bytes() for t in state.trees for e in t.epochs.values() for k in e]]
    transcript=b''.join(sent)
    for key in keys:
        for value in (key,key.hex().encode(),base64.urlsafe_b64encode(key).rstrip(b'=')): assert value not in transcript
    shapes={'agentmesh-broker-challenge-v1':{'format','device_id','server_nonce','signature'},
            'agentmesh-broker-ready-v1':{'format','agent_id'}}
    for data in sent:
        response=decode(data)
        if 'format' in response: assert set(response)==shapes[response['format']]
        elif 'stage' in response: assert set(response)=={'stage','unsigned'}
        elif 'error' in response: assert set(response)=={'error'}
        elif 'id' in response: assert set(response)=={'id','label','redactions'}
        elif 'results' in response: assert set(response)=={'query','query_plan','candidate_counts','results','evidence'}
        else: assert set(response)=={'agent_id','device_id','read','write','default_profile','allow_secret'}
    record=parse(keyfile(home,state,agents[0]).read_bytes())
    assert set(record)=={'format','agent_cert','agent_private_key','device_public_key','address'}


def test_p3_12_shape(tmp_path):
    home,state,store,agents=setup_home(tmp_path); b=Broker(home); s=Session(agents[0].certificate())
    for kind in ('fact','constraint','decision'):
        remember(b,s,agents[0],body=dict(kind=kind,content='migrate database',confidence=.6))
    result=b.request(s,{'op':'recall','query':'why migrate database'})
    assert set(result)=={'query','query_plan','candidate_counts','results','evidence'}
    assert set(result['query_plan'])=={'query','project','task_refs','intents','terms'}
    assert set(result['candidate_counts'])=={'durable','summaries','events'}
    assert [r['kind'] for r in result['results']]==['decision','constraint','fact']
    for row in result['results']:
        assert set(row)==set('id source_type kind content project task_ref confidence scope profile label tags created_at updated_at matched_terms score conflict alternatives'.split())


def test_p3_7_wire_audit(tmp_path, monkeypatch):
    home,state,store,agents=setup_home(tmp_path); broker=Broker(home)
    q1,q2=queue.Queue(),queue.Queue(); sent=[]
    class Checked(Channel):
        count=0
        def send(self,obj):
            if obj.get('format')!='agentmesh-broker-challenge-v1':
                rows=(home.root/'audit.jsonl').read_text(encoding='utf-8').splitlines()
                self.count+=1
                assert len(rows)==self.count
            super().send(obj)
    server=Checked(q1,q2,sent); channel=Channel(q2,q1,[])
    thread=threading.Thread(target=broker.connection,args=(server,),daemon=True); thread.start()
    client=BrokerClient(agents[0],state.device.public_bytes,channel)
    for req in ({'op':'whoami'},{'op':'bad'},{'op':'recall','query':'marker','scopes':['work/b']}):
        client.request(req)
    state.revoke(agents[0].agent_id); store.save(state)
    assert client.request({'op':'whoami'})=={'error':'denied'}
    client.close(); thread.join(5)
    # Failed proof of possession adds exactly one outcome before rejection.
    q1,q2=queue.Queue(),queue.Queue(); server=Channel(q1,q2,[]); channel=Channel(q2,q1,[])
    thread=threading.Thread(target=broker.connection,args=(server,),daemon=True); thread.start()
    from agentmesh_log.broker.handshake import hello
    channel.send(hello()); channel.receive(); channel.send({'format':'bad'})
    assert channel.receive()=={'error':'unauthenticated'}
    thread.join(5)
    rows=[json.loads(line) for line in (home.root/'audit.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(rows)==6 and rows[-1]['outcome']=='unauthenticated'


def test_p3_13_frames(tmp_path):
    import struct
    home,state,store,agents=setup_home(tmp_path); broker=Broker(home)
    healthy,_,healthy_thread=connect(broker,agents[1])
    for raw in (b'\0'*4,struct.pack('!I',1048577),struct.pack('!I',1)+b'{',struct.pack('!I',13)+b'{"a":1,"a":2}'):
        client,_,thread=connect(broker,agents[0])
        client.channel.outgoing.put(raw); thread.join(5)
        assert not thread.is_alive()
        assert healthy.request({'op':'whoami'})['agent_id']==agents[1].agent_id
    healthy.close(); healthy_thread.join(5)


def test_p3_10_outputs(tmp_path):
    from .test_detect import positives
    home,state,store,agents=setup_home(tmp_path); broker=Broker(home)
    client,sent,thread=connect(broker,agents[0]); value=positives()['aws']
    result=client.write('migrate database '+value)
    assert result['label']=='secret' and result['redactions']==['aws']
    assert value.encode() not in b''.join(sent)
    audit=(home.root/'audit.jsonl').read_text(encoding='utf-8')
    assert value not in audit
    assert json.loads(audit.splitlines()[-1])['redactions']=={'aws':1}
    state.grants[agents[0].agent_id].allow_secret=True; store.save(state)
    result=client.request({'op':'recall','query':'migrate database'})
    assert value not in result['results'][0]['content']
    client.close(); thread.join(5)
