from agentmesh_log import ulid
from agentmesh_log.broker import review
from agentmesh_log.workspace import State
from .test_tasks import shared, workspace_of


def fact(agent, text, scope='work/a'):
    return agent.sign(agent.request(dict(op='remember', body=dict(kind='fact', content=text), scope=scope, tags=[], task=None, label=None)))


def recall(agent, query, **kw):
    return agent.request(dict(op='recall', query=query, **kw))['results']


def test_staged_until_a_person_approves(tmp_path):
    b, alice, bob, _ = shared(tmp_path)
    workspace_of(b, **{'review.staged': 1})
    written = fact(alice, 'staged index migration plan')
    assert 'id' in written
    assert recall(bob, 'index migration plan') == []
    [pending] = recall(bob, 'index migration plan', include_pending=True)
    assert pending['review'] == 'pending' and pending['author']['agent'] == alice.identity.agent_id
    assert bob.sign(bob.request(dict(op='review', target=written['id'], verdict='approved')))['id']
    [approved] = recall(alice, 'index migration plan')
    assert approved['review'] == 'approved' and not approved['over_quota']
    # only pending events are reviewed, and a review is never written through remember
    assert bob.request(dict(op='review', target=written['id'], verdict='rejected')) == {'error': 'denied'}
    forged = dict(kind='review', content='approved x', metadata=dict(review=dict(target=written['id'], verdict='approved')))
    assert alice.request(dict(op='remember', body=forged, scope='work/a', tags=[], task=None, label=None)) == {'error': 'denied'}


def test_rejected_and_presence(tmp_path):
    b, alice, bob, _ = shared(tmp_path)
    workspace_of(b, **{'review.staged': 1})
    written = fact(alice, 'risky shortcut for the migration')
    b.state.control['windows'].pop('workspace'); b.store.save(b.state)
    assert bob.request(dict(op='review', target=written['id'], verdict='rejected')) == {'error': 'presence_required'}
    from agentmesh_log.registry_store import now
    b.state.control['windows']['workspace'] = now() + 600000; b.store.save(b.state)
    assert 'id' in bob.sign(bob.request(dict(op='review', target=written['id'], verdict='rejected')))
    assert recall(alice, 'risky shortcut migration') == []
    assert recall(alice, 'risky shortcut migration', include_pending=True)[0]['review'] == 'rejected'


def test_direct_agents_and_exempt_kinds(tmp_path):
    b, alice, bob, _ = shared(tmp_path)
    workspace_of(b, **{'review.staged': 1, 'review.direct_agents': [alice.identity.agent_id]})
    fact(alice, 'trusted writer note about indexes')
    fact(bob, 'untrusted note about indexes')
    seen = [r['content'] for r in recall(bob, 'note about indexes')]
    assert seen == ['trusted writer note about indexes']
    assert 'id' in bob.task('create', 'AM-1', title='Indexes')   # task state is not staged
    assert bob.tasks(task='AM-1')['tasks'][0]['status'] == 'open'


def test_writer_quota(tmp_path):
    b, alice, bob, _ = shared(tmp_path)
    workspace_of(b, **{'quota.agent_events_per_day': 2, 'quota.member_events_per_day': 3})
    assert 'id' in fact(alice, 'one') and 'id' in fact(alice, 'two')
    assert fact(alice, 'three') == {'error': 'quota_exceeded'}
    assert 'id' in fact(bob, 'bob one')                      # same account: member total is now 3
    assert fact(bob, 'bob two') == {'error': 'quota_exceeded'}


class WS:
    def __init__(self, settings, roles=None): self.settings, self.roles = settings, roles or {}
    def at(self, when): return State(settings=self.settings)
    def role(self, account, when): return self.roles.get(account)


def row(ident, kind='fact', agent='A', account='X', scope='s', profile='p', **extra):
    return dict(id=ident, kind=kind, scope=scope, profile=profile, _author=dict(agent=agent, account=account), **extra)


def ids(n): return [ulid.generate() for _ in range(n)]


def test_standing_rules_are_reader_side():
    a, b, c, d, e = sorted(ids(5))
    ws = WS({'review.staged': 1, 'quota.agent_events_per_day': 2}, roles={'X': 'member', 'Y': 'contributor'})
    rows = [row(a), row(b), row(c),
            row(d, kind='review', account='Y', _review=dict(target=a, verdict='approved')),    # contributor: ignored
            row(e, kind='review', account='X', _review=dict(target=b, verdict='rejected'))]
    standing, over = review.standing(rows, {'p': ws}.get)
    assert standing[a] == 'pending' and standing[b] == 'rejected' and standing[c] == 'pending'
    assert over == {c}                                                     # the agent's third event that day
    plain, none = review.standing([row(a, profile='q')], {'p': ws}.get)
    assert plain[a] == 'direct' and none == set()
