from agentmesh_log.broker.keystore import BrokerHome, BrokerState, FileKeyStore
from agentmesh_log.broker.grants import Grant, resolve
from agentmesh_log.identity import AgentIdentity
from agentmesh_log.events import EVENT_DOMAIN
from agentmesh_log.encoding import typed


def setup_home(tmp_path):
    home = BrokerHome(tmp_path/'home'); state = BrokerState.create(['work','personal'])
    for name in ('a','b'): state.trees[0].add(state.trees[0].root.scope_id,'project',name)
    state.trees[1].add(state.trees[1].root.scope_id,'project','notes')
    agents = []
    for name in ('work/a','work/b'):
        agent = AgentIdentity(state.device,state.account.account_id); sid = resolve(state,name)
        state.grants[agent.agent_id] = Grant(agent.agent_id,agent.public_bytes.hex(),[sid],[sid],sid,state.trees[0].root.scope_id)
        agents.append(agent)
    # Phase 3 policy tests run inside explicit test-only presence windows.
    from agentmesh_log.registry_store import now
    from agentmesh_log.presence import CLASSES
    state.control['windows']={kind:now()+900000 for kind in CLASSES}
    store = FileKeyStore(home); store.save(state)
    return home,state,store,agents


def remember(broker, session, agent, body='migrate database', **kwargs):
    response = broker.request(session,dict(op='remember',body=body,scope=None,tags=[],task=None,label=None,**kwargs))
    if response.get('stage') != 'sign': return response
    signature = agent._private.sign(EVENT_DOMAIN+typed(response['unsigned'])).hex()
    return broker.request(session,dict(op='sign',signature=signature))


def recall(broker, session, **kwargs):
    return broker.request(session,dict(op='recall',query='migrate database',**kwargs))


class FakeEmbedding:
    """Offline stand-in for OllamaEmbedding: synonym groups share one axis."""
    GROUPS = (('database','db','ฐานข้อมูล'), ('deploy','rollout','ติดตั้ง'), ('launchd','service'))
    def __init__(self, revision='f'*64, fail=False):
        self.space = dict(provider='fake', model='fake', revision=revision, metric='cosine')
        self.calls = []; self.fail = fail
    def embed(self, texts):
        if self.fail: raise RuntimeError('local Ollama transport failed; no fallback')
        self.calls.append(list(texts)); out = []
        for text in texts:
            v = [0.0]*(len(self.GROUPS)+1); low = text.lower()
            for i, group in enumerate(self.GROUPS):
                v[i] = float(sum(low.count(w) for w in group))
            v[-1] = 0.1  # never a zero vector
            norm = sum(x*x for x in v) ** .5; out.append([x/norm for x in v])
        return out
