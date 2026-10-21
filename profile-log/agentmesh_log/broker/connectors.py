"""Connector-only broker capabilities; ordinary agents cannot open presence windows."""
from dataclasses import replace
from ..registry_store import now
from ..connector import ConnectorRegistry
from ..identity import AgentIdentity
from ..encoding import wire, parse, hex_bytes
from .grants import Grant
from .keystore import atomic_write


def controls(broker):
    return ConnectorRegistry(broker.replica.storage,broker.state.account.public_record(),
        [s for t in broker.state.trees for s in t.scopes.values()])


def effective(grant,clock=now):
    if grant.connector_app_id is None: return grant
    active=grant.allow_secret and clock()<grant.secret_until
    return replace(grant,allow_secret=active,max_label='secret' if active else grant.max_label)


def window(broker,session,req):
    if set(req)!={'op','app_id'}: raise ValueError('invalid_request')
    if session.agent_id!=broker.state.control.get('connector_bridge_agent'): raise ValueError('denied')
    hex_bytes(req['app_id'],16)
    record=controls(broker).window(req['app_id'])
    if record is None: raise ValueError('denied')
    policy=controls(broker).policy(req['app_id'])
    if policy is None or policy['state']!='active': raise ValueError('denied')
    for aid,g in broker.state.grants.items():
        if g.connector_app_id==req['app_id']:
            broker.state.grants[aid]=replace(g,allow_secret=True,secret_until=record['not_after'])
    broker.store.save(broker.state); broker.fingerprint=broker.store.fingerprint()
    return {'status':'ok'}


def provision(home,store,policy=None):
    """Protected local administration. One signing identity per app per device."""
    with home.lock():
        state=store.load(); app=policy['app_id'] if policy else None
        key='connector_bridge_agent' if app is None else 'connector_agents'
        aid=state.control.get(key) if app is None else state.control.get(key,{}).get(app)
        if aid:
            path=home.root/'agents'/(aid+'.key')
            record=parse(path.read_bytes()); agent=AgentIdentity.from_private(record['agent_cert'],bytes.fromhex(record['agent_private_key']))
        else:
            agent=AgentIdentity(state.device,state.account.account_id); aid=agent.agent_id
            path=home.root/'agents'/(aid+'.key')
            atomic_write(path,wire(dict(format='agentmesh-agent-key-v1',agent_cert=agent.certificate(),agent_private_key=agent.private_bytes().hex(),device_public_key=state.device.public_bytes.hex(),address=home.address(state.device.device_id))))
            if app is None: state.control[key]=aid
            else: state.control.setdefault(key,{})[app]=aid
        if policy:
            reads=[sid for e in policy['read'] for sid in (e['scopes'] or [e['profile']])]
            target=policy['remember']; writes=[target['scope']] if target else []
            default=policy['read'][0]['profile'] if policy['read'] else (target['profile'] if target else state.trees[0].root.scope_id)
            previous=state.grants.get(aid)
            state.grants[aid]=Grant(aid,agent.public_bytes.hex(),reads,writes,writes[0] if writes else None,default,
                allow_secret=False,connector_app_id=app,max_label=policy['max_label'],secret_until=0)
        else: state.grants[aid]=Grant(aid,agent.public_bytes.hex(),[],[],None,state.trees[0].root.scope_id)
        if aid not in state.control.setdefault('seen_agents',[]): state.control['seen_agents'].append(aid)
        store.save(state)
        return path


def outbox_body(broker,grant,req):
    body=req['body']
    if type(body) is dict and {'outbox_id','connector_app_id'} & set(body.get('metadata',{})): raise ValueError('invalid_request')
    oid=req.get('outbox_id')
    if oid is None: return body,None
    if grant.connector_app_id is None: raise ValueError('denied')
    from ..ulid import parse as check_ulid
    check_ulid(oid)
    existing=[]
    for e,p in broker.payloads():
        metadata=p['body'].get('metadata',{}) if type(p['body']) is dict else {}
        if metadata.get('outbox_id')==oid and metadata.get('connector_app_id')==grant.connector_app_id:
            existing.append(e['id'])
    if existing: return body,{'id':min(existing),'label':'general','redactions':[]}
    if type(body) is str: body={'kind':'fact','content':body}
    body={**body,'metadata':{**body.get('metadata',{}),'outbox_id':oid,'connector_app_id':grant.connector_app_id}}
    return body,None
