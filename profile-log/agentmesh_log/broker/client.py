"""Agent client: pins the device and signs only its own prepared envelopes."""
import os
from pathlib import Path
from ..encoding import parse, typed, fields, hex_bytes
from ..identity import AgentIdentity
from ..events import EVENT_DOMAIN, EVENT_FIELDS
from ..storage.filesystem import _check
from .handshake import hello, check_challenge, authenticate, BrokerIdentityError
from .transport import UnixClient, PipeClient


class BrokerUnreachable(ValueError): pass


class PresenceRequired(ValueError):
    def __init__(self, response):
        super().__init__('presence_required')
        self.presence_class=response.get('class','first_access')


class BrokerClient:
    def __init__(self, agent, pinned, channel):
        self.agent,self.channel=agent,channel
        try:
            h=hello(); channel.send(h); challenge=channel.receive()
            check_challenge(h,challenge,pinned)
            if challenge['device_id']!=agent.device_id: raise BrokerIdentityError('broker identity mismatch')
            channel.send(authenticate(agent,h,challenge)); ready=channel.receive()
            if ready!={'format':'agentmesh-broker-ready-v1','agent_id':agent.agent_id}: raise ValueError('unauthenticated')
        except Exception: channel.close(); raise

    @classmethod
    def from_file(cls, path):
        path=Path(path); _check(path); record=parse(path.read_bytes())
        fields(record,{'format','agent_cert','agent_private_key','device_public_key','address'},'agentmesh-agent-key-v1')
        agent=AgentIdentity.from_private(record['agent_cert'],hex_bytes(record['agent_private_key'],32))
        pinned=hex_bytes(record['device_public_key'],32)
        try: channel=(PipeClient if os.name=='nt' else UnixClient)(record['address'])
        except OSError as exc:
            if isinstance(exc,(FileNotFoundError,ConnectionRefusedError)) or getattr(exc,'winerror',None) in (2,3):
                raise BrokerUnreachable('broker_unreachable') from None
            raise
        return cls(agent,pinned,channel)

    def request(self, request):
        try:
            self.channel.send(request); return self.channel.receive()
        except Exception:
            self.channel.close(); raise

    def write(self, body, *, scope=None, tags=None, task=None, label=None, revises=None, expected_scope=None, outbox_id=None):
        req=dict(op='remember' if revises is None else 'revise',body=body,scope=scope,tags=tags or [],task=task,label=label)
        if revises is not None: req['revises']=revises
        if outbox_id is not None: req['outbox_id']=outbox_id
        response=self.request(req)
        if 'error' in response: return response
        if outbox_id is not None and set(response)=={'id','label','redactions'}: return response
        if set(response)!={'stage','unsigned'} or response['stage']!='sign': raise ValueError('invalid response')
        unsigned=response['unsigned']; fields(unsigned,EVENT_FIELDS-{'signature'},'agentmesh-event-v1')
        if unsigned['agent_cert']!=self.agent.certificate() or (unsigned['account'],unsigned['device'],unsigned['agent'])!=(self.agent.account_id,self.agent.device_id,self.agent.agent_id):
            raise ValueError('invalid author binding')
        if expected_scope is not None and unsigned['scope']!=expected_scope: raise ValueError('invalid scope binding')
        return self.request(dict(op='sign',signature=self.agent._private.sign(EVENT_DOMAIN+typed(unsigned)).hex()))

    def import_row(self, **fields):
        response=self.request(dict(op='import',**fields))
        steps=0
        while response.get('stage')=='sign':
            steps+=1
            if steps>2 or set(response)!={'stage','unsigned'}: raise ValueError('invalid response')
            unsigned=response['unsigned']
            from ..encoding import fields as check_fields
            check_fields(unsigned,EVENT_FIELDS-{'signature'},'agentmesh-event-v1')
            if unsigned['agent_cert']!=self.agent.certificate() or (unsigned['account'],unsigned['device'],unsigned['agent'])!=(self.agent.account_id,self.agent.device_id,self.agent.agent_id):
                raise ValueError('invalid author binding')
            response=self.request(dict(op='sign',signature=self.agent._private.sign(EVENT_DOMAIN+typed(unsigned)).hex()))
        return response

    def close(self): self.channel.close()
