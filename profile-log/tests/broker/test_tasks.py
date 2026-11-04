import pytest
from agentmesh_log.broker import tasks
from agentmesh_log.broker.service import Broker, Session
from agentmesh_log.broker.grants import Grant, resolve
from agentmesh_log.events import EVENT_DOMAIN
from agentmesh_log.encoding import typed
from agentmesh_log.identity import AgentIdentity
from .support import setup_home

A = dict(account='acct', device='dev', agent='alice')
B = dict(account='acct', device='dev', agent='bob')


def state(status='open', owner=None, lease=None, **kw):
    return dict(dict(status=status, title='Retire RANGES', owner=owner, lease_until=lease, next=None, blocked_on=[],
                     handoff=None), **kw)


def event(ident, author, at, st, revises=None):
    return dict(id=ident, revises=revises, _author=author, _task=dict(state=st, at=at))


def test_first_claim_wins():
    root = event('01A', A, 0, state())
    alice = event('01B', A, 10, state('claimed', A, 1000), '01A')
    bob = event('01C', B, 11, state('claimed', B, 1000), '01A')
    alice_more = event('01D', A, 20, state('in_progress', A, 1000), '01B')
    head, superseded, ignored = tasks.resolve([root, bob, alice, alice_more])
    assert head['id'] == '01D' and superseded == ['01C'] and ignored == []


def test_live_lease_blocks():
    held = event('01B', A, 10, state('claimed', A, 100), '01A')
    rows = [event('01A', A, 0, state()), held]
    assert not tasks.valid(held, event('01C', B, 50, state('claimed', B, 200), '01B'))
    assert not tasks.valid(held, event('01C', B, 50, state('open'), '01B'))
    assert tasks.valid(held, event('01C', B, 150, state('claimed', B, 300), '01B'))
    head, _, ignored = tasks.resolve(rows+[event('01C', B, 50, state('open'), '01B')])
    assert head['id'] == '01B' and ignored == ['01C']


def test_bad_leases():
    root = event('01A', A, 0, state())
    assert not tasks.valid(root, event('01B', A, 10, state('claimed', A, 10+tasks.MAX_LEASE_MS+1), '01A'))
    assert not tasks.valid(root, event('01B', A, 10, state('claimed', B, 100), '01A'))  # owner must be the author
    assert not tasks.valid(None, event('01B', A, 10, state('done')))
    assert not tasks.valid(root, event('01B', A, 10, dict(state(), extra=1), '01A'))
    done = event('01B', A, 10, state('done'), '01A')
    assert not tasks.valid(done, event('01C', A, 20, state('review'), '01B'))
    assert tasks.valid(done, event('01C', B, 20, state('open'), '01B'))


def test_roots():
    head, superseded, ignored = tasks.resolve([event('01B', B, 5, state()), event('01A', A, 9, state('done')),
                                               event('01C', A, 1, state())])
    assert head['id'] == '01B' and superseded == ['01C'] and ignored == ['01A']


def test_successor():
    with pytest.raises(ValueError): tasks.successor('create', state(), dict(title='x'), A, 0)
    created = tasks.successor('create', None, dict(title='x', claim=True), A, 0)
    assert created['status'] == 'claimed' and created['owner'] == A and created['lease_until'] == tasks.DEFAULT_LEASE_MS
    with pytest.raises(ValueError, match='denied'): tasks.successor('claim', created, {}, B, 10)
    taken = tasks.successor('claim', created, {}, B, tasks.DEFAULT_LEASE_MS+1)
    assert taken['owner'] == B
    with pytest.raises(ValueError, match='denied'): tasks.successor('update', state(), dict(status='in_progress'), A, 0)
    released = tasks.successor('update', created, dict(status='open', handoff='done step 1'), A, 10)
    assert released['owner'] is None and released['lease_until'] is None and released['handoff'] == 'done step 1'
    with pytest.raises(ValueError, match='invalid_request'): tasks.successor('claim', state(), dict(lease_ms=1), A, 0)
    with pytest.raises(ValueError): tasks.ref('../x')
    assert tasks.key('am-1') == 'task/AM-1'


class Agent:
    def __init__(self, broker, identity):
        self.broker, self.identity, self.session = broker, identity, Session(identity.certificate())

    def request(self, request): return self.broker.request(self.session, request)

    def sign(self, response):
        if response.get('stage') != 'sign': return response
        return self.request(dict(op='sign', signature=self.identity._private.sign(EVENT_DOMAIN+typed(response['unsigned'])).hex()))

    def task(self, action, ref, **fields): return self.sign(self.request(dict(op='task', action=action, task=ref, scope='work/a', **fields)))

    def tasks(self, **kw): return self.request(dict(op='tasks', **kw))


def shared(tmp_path):
    home, state_, store, agents = setup_home(tmp_path)
    sid = resolve(state_, 'work/a')
    bob = AgentIdentity(state_.device, state_.account.account_id)
    state_.grants[bob.agent_id] = Grant(bob.agent_id, bob.public_bytes.hex(), [sid], [sid], sid, state_.trees[0].root.scope_id)
    store.save(state_)
    b = Broker(home)
    return b, Agent(b, agents[0]), Agent(b, bob), Agent(b, agents[1])


def test_claim_handoff(tmp_path):
    b, alice, bob, outsider = shared(tmp_path)
    created = alice.task('create', 'am-142', title='Retire packet RANGES', project='mesh', claim=True, next='run r5b')
    assert 'id' in created, created
    [mine] = alice.tasks(mine=True)['tasks']
    assert mine['task'] == 'AM-142' and mine['status'] == 'claimed' and mine['lease_live'] and mine['project'] == 'mesh'
    assert bob.tasks(mine=True)['tasks'] == []
    assert bob.task('claim', 'AM-142') == {'error': 'denied'}
    assert bob.task('update', 'AM-142', status='done') == {'error': 'denied'}
    assert alice.task('create', 'AM-142', title='again') == {'error': 'denied'}
    released = alice.task('update', 'AM-142', status='open', handoff='branch feat/r5b, tests green')
    assert 'id' in released
    taken = bob.task('claim', 'AM-142', status='in_progress')
    assert 'id' in taken
    [view] = alice.tasks(task='am-142')['tasks']
    assert view['status'] == 'in_progress' and view['owner']['agent'] == bob.identity.agent_id
    assert view['handoff'] == 'branch feat/r5b, tests green' and view['next'] == 'run r5b' and not view['conflict']
    assert outsider.tasks(scope='work/a') == {'error': 'denied'}
    assert outsider.tasks()['tasks'] == []
    assert alice.tasks(status=['done'])['tasks'] == []


def test_task_op_only(tmp_path):
    b, alice, bob, _ = shared(tmp_path)
    forged = dict(kind='task_state', content='x', metadata=dict(memory_key='task/AM-1', task_state={}))
    assert alice.request(dict(op='remember', body=forged, scope='work/a', tags=[], task=None, label=None)) == {'error': 'denied'}
    sneaky = dict(kind='fact', content='x', metadata=dict(memory_key='task/AM-1'))
    assert alice.request(dict(op='remember', body=sneaky, scope='work/a', tags=[], task=None, label=None)) == {'error': 'denied'}
    created = alice.task('create', 'AM-1', title='First')
    revise = dict(op='revise', body=dict(kind='fact', content='overwrite'), scope='work/a', tags=[], task=None, label=None,
                  revises=created['id'])
    assert alice.request(revise) == {'error': 'denied'}


def test_recall_tree(tmp_path):
    b, alice, _, _ = shared(tmp_path)
    alice.task('create', 'AM-7', title='Migrate database index', next='rebuild snapshots')
    recall = alice.request(dict(op='recall', query='migrate database index'))
    hits = [r for r in recall['results'] if r['kind'] == 'task_state']
    assert hits and all(not k.startswith('_') for r in recall['results'] for k in r)
    listing = alice.request(dict(op='ls', path='/work/a/tasks'))
    [folder] = [c for c in listing['children'] if c['name'] == 'AM-7/']
    assert folder['summary'] == '[open] AM-7: Migrate database index'


def test_concurrent(tmp_path):
    b, alice, bob, _ = shared(tmp_path)
    alice.task('create', 'AM-9', title='Race')
    # Both prepare against the same open head before either signs.
    first = alice.request(dict(op='task', action='claim', task='AM-9', scope='work/a'))
    second = bob.request(dict(op='task', action='claim', task='AM-9', scope='work/a'))
    winner = alice.sign(first); loser = bob.sign(second)
    assert 'id' in winner and 'id' in loser and winner['id'] < loser['id']
    [view] = bob.tasks(task='AM-9')['tasks']
    assert view['owner']['agent'] == alice.identity.agent_id and view['conflict'] and view['superseded'] == [loser['id']]


def workspace_of(b, **settings):
    from agentmesh_log import workspace as ws
    profile = b.state.trees[0].root.scope_id; me = b.state.account.account_id
    genesis = ws.create(b.state.device, profile, 'genesis', subject=me, role='owner')
    ws.publish(b.replica.storage, genesis)
    if settings:
        ws.publish(b.replica.storage, ws.create(b.state.device, profile, 'settings', previous=ws.entry_hash(genesis),
                                                settings=settings))


def test_workspace_lease_limits(tmp_path):
    b, alice, _, _ = shared(tmp_path)
    assert 'id' in alice.task('create', 'AM-2', title='x', claim=True, lease_ms=3*3600*1000)
    workspace_of(b, **{'task_lease.default_ms': 120_000, 'task_lease.max_ms': 600_000})
    assert alice.task('create', 'AM-3', title='y', claim=True, lease_ms=3*3600*1000) == {'error': 'invalid_request'}
    assert 'id' in alice.task('create', 'AM-3', title='y', claim=True)
    [t] = alice.tasks(task='AM-3')['tasks']
    assert t['status'] == 'claimed' and t['lease_until'] <= alice.tasks()['at'] + 600_000
    # the older long lease stays valid: limits apply from their own time
    [old] = alice.tasks(task='AM-2')['tasks']
    assert old['status'] == 'claimed' and old['ignored'] == []


def test_override_needs_role_and_presence(tmp_path):
    b, alice, bob, _ = shared(tmp_path)
    alice.task('create', 'AM-5', title='Stuck', claim=True)
    assert bob.task('override', 'AM-5', status='open') == {'error': 'denied'}   # no workspace, no roles
    workspace_of(b)
    b.state.control['windows'].pop('workspace'); b.store.save(b.state)
    assert bob.task('override', 'AM-5', status='open') == {'error': 'presence_required'}
    from agentmesh_log.registry_store import now
    b.state.control['windows']['workspace'] = now() + 600000; b.store.save(b.state)
    done = bob.task('override', 'AM-5', status='open', handoff='freed by admin')
    assert 'id' in done, done
    [t] = alice.tasks(task='AM-5')['tasks']
    assert t['status'] == 'open' and t['override'] and t['owner'] is None and t['ignored'] == []
