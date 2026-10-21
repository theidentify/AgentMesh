import copy
from pathlib import Path
from dataclasses import replace
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.exceptions import InvalidTag
import pytest
from phase6_support import device
from agentmesh_log.connector import secret_record
from agentmesh_log.registry_store import signed,now
from agentmesh_log.encoding import wire,typed,parse
from agentmesh_log.bridge import request_headers
from agentmesh_log.broker.handshake import b64
from agentmesh_log.keys import SUITE
from agentmesh_log.ulid import generate


def call(b,k,p,pin,tool,args):
    raw=wire({'tool':tool,'arguments':args});return b.handle(raw,request_headers(k,pin,p['app_id'],raw))


def test_p6_2_revoked_bridge(tmp_path):
    home,store,storage,cr,p,k,pin,broker,b=device(tmp_path)
    assert call(b,k,p,pin,'remember',{'body':'migrate database'})['status']=='stored'
    revoke=signed('connector-policy',store.load().device._private,**{a:v for a,v in p.items() if a not in ('format','signature','state','issued_at')},state='revoked',issued_at=now())
    cr.publish(revoke)
    # Test-only Worker-check-disabled path: a correctly gateway-signed request goes straight to bridge.
    assert call(b,k,p,pin,'recall',{'query':'migrate database'})=={'error':'denied'}
    broker.close()


def test_p6_4_window_isolation(tmp_path):
    home,store,storage,cr,p,k,pin,broker,b=device(tmp_path)
    assert call(b,k,p,pin,'remember',{'body':'migrate database public'})['status']=='stored'
    baseline=call(b,k,p,pin,'recall',{'query':'migrate database'})
    assert call(b,k,p,pin,'remember',{'body':'migrate database hidden','label':'secret'})['status']=='stored'
    assert call(b,k,p,pin,'recall',{'query':'migrate database'})==baseline
    other=signed('connector-policy',store.load().device._private,**{a:v for a,v in p.items() if a not in ('format','signature','app_id','issued_at')},app_id='cd'*16,issued_at=now());cr.publish(other)
    start=now();cr.publish(secret_record(store.load().device,p['app_id'],start,start+60000))
    opened=call(b,k,p,pin,'recall',{'query':'migrate database'})
    assert len(opened['data']['results'])==2
    assert call(b,k,other,pin,'recall',{'query':'migrate database'})==baseline
    # Expire via the actual broker clock, not the bridge policy check.
    state=store.load();aid=state.control['connector_agents'][p['app_id']]
    state.grants[aid]=replace(state.grants[aid],secret_until=now()-1);store.save(state)
    from phase6_support import DirectClient
    client=DirectClient(home.root/'agents'/(aid+'.key'),broker)
    assert client.request({'op':'recall','query':'migrate database'})==baseline['data']
    broker.close()


def item(store,p,pin,k,body,*,created=None):
    state=store.load();key=X25519PrivateKey.from_private_bytes(bytes.fromhex(state.control['outbox_private']));oid=generate()
    binding={'outbox_id':oid,'app_id':p['app_id'],'device_id':state.device.device_id}
    ciphertext=SUITE.encrypt(wire({'body':body,'tags':[],'task':None,'label':None}),key.public_key(),b'AgentMesh/outbox/v1\x00'+typed(binding))
    from agentmesh_log.connector import gateway_id
    return signed('outbox-item',k,outbox_id=oid,app_id=p['app_id'],created_at=now() if created is None else created,recipients=[{'device_id':state.device.device_id,'ciphertext':b64(ciphertext)}],gateway_key_id=gateway_id(pin))


def test_p6_6_drain(tmp_path):
    home,store,storage,cr,p,k,pin,broker,b=device(tmp_path)
    good=item(store,p,pin,k,'queued-marker');cr.publish(good)
    assert b'queued-marker' not in wire(good)
    from agentmesh_log.keys import decode_ciphertext
    info=b'AgentMesh/outbox/v1\x00'+typed({'outbox_id':good['outbox_id'],'app_id':p['app_id'],'device_id':store.load().device.device_id})
    with pytest.raises((ValueError,InvalidTag)):SUITE.decrypt(decode_ciphertext(good['recipients'][0]['ciphertext']),X25519PrivateKey.generate(),info)
    assert b.drain()=={'stored':1,'rejected':0,'errors':0}
    state=store.load();state.control['connector_drained']=[];store.save(state)
    assert b.drain()['stored']==1
    assert len(call(b,k,p,pin,'recall',{'query':'queued-marker'})['data']['results'])==1
    for kind in ('signature','ciphertext','key','before_policy'):
        bad=item(store,p,pin,k,'forged-marker',created=0 if kind=='before_policy' else None)
        if kind=='signature':bad['signature']='00'*64
        if kind=='ciphertext':bad['recipients'][0]['ciphertext']=bad['recipients'][0]['ciphertext'][:-2]+'AA'
        if kind=='key':bad['gateway_key_id']='00'*32
        cr.publish(bad)
    assert b.drain()['rejected']==4
    broker.close()


def test_p6_6_two_devices(tmp_path):
    from agentmesh_log.pairing import JoinRequest,Approver
    from agentmesh_log.broker.keystore import BrokerHome,FileKeyStore
    from agentmesh_log.broker.service import Broker
    from agentmesh_log.bridge import Bridge
    from phase6_support import DirectClient
    from agentmesh_log.keyring import sync_control
    home,store,storage,cr,p,k,pin,first,a=device(tmp_path/'mac')
    state=store.load();join=JoinRequest(storage);approver=Approver(storage,state.device,state.registry(),{})
    approval=approver.begin(join.request_id);reveal=join.reveal(approval,state.registry());approver.confirm(reveal,lambda _:True)
    approver.grant(state,[p['read'][0]['profile']])
    other=BrokerHome(tmp_path/'windows');other_store=FileKeyStore(other);join.finish(state.registry(),other,other_store)
    sync_control(home,store,storage)
    second=Broker(other,store=other_store);b=Bridge(other,other_store,storage,client_factory=lambda path:DirectClient(path,second))
    oid=generate();recipients=[]
    for target in (store,other_store):
        st=target.load();private=X25519PrivateKey.from_private_bytes(bytes.fromhex(st.control['outbox_private']))
        info=b'AgentMesh/outbox/v1\x00'+typed({'outbox_id':oid,'app_id':p['app_id'],'device_id':st.device.device_id})
        ciphertext=SUITE.encrypt(wire({'body':'two-device queued marker','tags':[],'task':None,'label':None}),private.public_key(),info)
        recipients.append({'device_id':st.device.device_id,'ciphertext':b64(ciphertext)})
    from agentmesh_log.connector import gateway_id
    cr.publish(signed('outbox-item',k,outbox_id=oid,app_id=p['app_id'],created_at=now(),recipients=recipients,gateway_key_id=gateway_id(pin)))
    assert a.drain()['stored']==1
    assert b.drain()['stored']==1
    # Both wrote while unaware of the other event; synchronization must collapse the two roots.
    first.sync_once(storage);second.sync_once(storage);first.sync_once(storage)
    ids=[]
    for bridge in (a,b):
        result=call(bridge,k,p,pin,'recall',{'query':'two-device queued marker'})
        assert len(result['data']['results'])==1
        ids.append(result['data']['results'][0]['id'])
    assert ids[0]==ids[1]
    # A Tunnel may pick either healthy connector; each bridge serves alone.
    for healthy,offline in ((a,b),(b,a)):
        assert call(healthy,k,p,pin,'recall',{'query':'two-device queued marker'})['data']['results']
    first.close();second.close()


def test_p6_4_bridge_only_window_op(tmp_path):
    home,store,storage,cr,p,k,pin,broker,b=device(tmp_path)
    start=now();cr.publish(secret_record(store.load().device,p['app_id'],start,start+60000))
    assert 'data' in call(b,k,p,pin,'recall',{'query':'absent'})
    from phase6_support import DirectClient
    aid=store.load().control['connector_agents'][p['app_id']]
    client=DirectClient(home.root/'agents'/(aid+'.key'),broker)
    # A valid window exists; only the bridge credential guard can reject this request.
    assert client.request({'op':'connector_window','app_id':p['app_id']})=={'error':'denied'}
    broker.close()


def test_p6_6_policy_recheck(tmp_path):
    home,store,storage,cr,p,k,pin,broker,b=device(tmp_path)
    queued=item(store,p,pin,k,'private-queued-marker');cr.publish(queued)
    target=next(t for t in store.load().trees if t.root.name=='work')
    changed=signed('connector-policy',store.load().device._private,**{a:v for a,v in p.items() if a not in ('format','signature','remember','issued_at')},remember={'profile':target.root.scope_id,'scope':target.root.scope_id},issued_at=now())
    cr.publish(changed)
    assert b.drain()['rejected']==1  # historical target is no longer authorized now
    revoke=signed('connector-policy',store.load().device._private,**{a:v for a,v in changed.items() if a not in ('format','signature','state','issued_at')},state='revoked',issued_at=now());cr.publish(revoke)
    assert b.drain()['rejected']==1
    assert all(b'private-queued-marker' not in storage.get(path) for path in storage.list(''))
    broker.close()
