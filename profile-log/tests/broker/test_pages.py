import json
import pytest
from agentmesh_log import pages
from agentmesh_log.broker.service import Broker, Session
from agentmesh_log.events import EVENT_DOMAIN
from agentmesh_log.encoding import typed
from agentmesh_log.settings import validate
from .support import setup_home, remember


class Local:
    """BrokerClient stand-in over an in-process broker session."""
    def __init__(self, broker, agent):
        self.broker, self.agent, self.session = broker, agent, Session(agent.certificate())
    def request(self, request): return self.broker.request(self.session, request)
    def write(self, body, *, scope=None, tags=None, revises=None, **_):
        req = dict(op='remember' if revises is None else 'revise', body=body, scope=scope, tags=tags or [], task=None, label=None)
        if revises is not None: req['revises'] = revises
        response = self.request(req)
        if response.get('stage') != 'sign': return response
        return self.request(dict(op='sign', signature=self.agent._private.sign(EVENT_DOMAIN+typed(response['unsigned'])).hex()))


def setup(tmp_path, pages_flag=True):
    home, state, store, agents = setup_home(tmp_path)
    grant = state.grants[agents[0].agent_id]; grant.allow_pages = pages_flag; grant.allow_secret = True; store.save(state)
    b = Broker(home); client = Local(b, agents[0])
    ids = {}
    for key, body, label in (('d1', dict(kind='decision', content='Use central Ollama on the Air'), 'internal'),
                             ('d2', dict(kind='decision', content='Pages live in their own scope', project='mesh'), None),
                             ('c1', dict(kind='constraint', content='Never sync vectors'), None),
                             ('s1', dict(kind='fact', content='token sk-not-a-real-one'), 'secret')):
        response = client.write(body, scope='work/a') if label is None else client.request(dict(op='remember', body=body, scope='work/a', tags=[], task=None, label=label))
        if response.get('stage') == 'sign':
            response = client.request(dict(op='sign', signature=agents[0]._private.sign(EVENT_DOMAIN+typed(response['unsigned'])).hex()))
        ids[key] = response['id']
    return home, b, client, agents, ids


def page(rel, sources, summary='Decisions so far'):
    return dict(kind='page', content='---\npath: %s\nsummary: %s\nsources: [%s]\nupdated: 2026-11-02T00:00:00Z\n---\n# x\n\n- body\n'
        % (rel, summary, ', '.join(sources)))


def test_page_write_rules(tmp_path):
    home, b, client, agents, ids = setup(tmp_path)
    write = lambda rel, sources, **kw: client.write(page(rel, sources), scope='work/a', tags=['page:'+rel], **kw)
    assert write('decisions/INDEX', [ids['s1']]) == {'error': 'denied'}
    assert write('decisions/INDEX', ['01M5HE0TAVV5C8P3Q471SM5DKF']) == {'error': 'denied'}
    assert client.write(page('decisions/INDEX', [ids['d1']]), scope='work/a', tags=['page:constraints/INDEX']) == {'error': 'invalid_request'}
    assert write('decisions/../INDEX', [ids['d1']]) == {'error': 'invalid_request'}
    assert client.write(dict(kind='fact', content='x'), scope='work/a', tags=['page:INDEX']) == {'error': 'invalid_request'}
    first = write('decisions/INDEX', [ids['d1'], ids['d2']])
    assert first['label'] == 'internal'
    assert write('decisions/INDEX', [ids['d1']]) == {'error': 'denied'}
    assert write('decisions/INDEX', [ids['d1']], revises=ids['d2']) == {'error': 'denied'}
    second = write('decisions/INDEX', [ids['d2']], revises=first['id'])
    assert second['label'] == 'internal'
    assert client.write(page('INDEX', [ids['d1']]), scope='work/a', tags=['page:INDEX'], revises=ids['c1']) == {'error': 'denied'}


def test_pages_need_grant(tmp_path):
    home, b, client, agents, ids = setup(tmp_path, pages_flag=False)
    assert client.write(page('INDEX', [ids['c1']]), scope='work/a', tags=['page:INDEX']) == {'error': 'denied'}


def test_pages_in_tree(tmp_path):
    home, b, client, agents, ids = setup(tmp_path)
    client.write(page('decisions/INDEX', [ids['d1']]), scope='work/a', tags=['page:decisions/INDEX'])
    client.write(page('INDEX', [ids['c1']], 'Mesh memory work'), scope='work/a', tags=['page:INDEX'])
    top = client.request(dict(op='ls', path='/work/a'))['children']
    assert top[0] == dict(name='INDEX.md', type='page', label='general', updated=top[0]['updated'], summary='Mesh memory work', stale=False)
    folder = next(e for e in top if e['name'] == 'decisions/')
    assert folder['summary'] == 'Decisions so far' and folder['stale'] is False and folder['count'] == 2
    assert 'stale' not in next(e for e in top if e['name'] == 'constraints/')
    assert client.request(dict(op='ls', path='/work'))['children'][0]['summary'] == 'Mesh memory work'
    assert client.request(dict(op='ls', path='/work/a/decisions'))['children'][0]['name'] == 'INDEX.md'
    cat = client.request(dict(op='cat', path='/work/a/decisions/INDEX.md'))
    assert cat['markdown'].startswith('---\npath: decisions/INDEX\n') and cat['stale'] is False and cat['path'] == '/work/a/decisions/INDEX.md'
    client.write(dict(kind='decision', content='A newer decision'), scope='work/a')
    assert client.request(dict(op='cat', path='/work/a/decisions/INDEX.md'))['stale'] is True
    assert next(e for e in client.request(dict(op='ls', path='/work/a'))['children'] if e['name'] == 'decisions/')['stale'] is True
    other = Local(b, agents[1])
    assert other.request(dict(op='ls', path='/work'))['children'][0]['name'] == 'b/'
    assert other.request(dict(op='cat', path='/work/a/INDEX.md')) == {'error': 'denied'}


class Fake:
    def __init__(self): self.calls = []
    def __call__(self, material):
        self.calls.append(material); return dict(summary='Summary %d' % len(self.calls), body='- point')


def test_build(tmp_path):
    home, b, client, agents, ids = setup(tmp_path)
    fake = Fake()
    report = pages.build(client, summarize=fake, now='2026-11-02T00:00:00Z')
    assert report == dict(scopes=1, planned=4, written=4, skipped=1, failed=0)
    assert not any('sk-not-a-real-one' in m or ids['s1'] in m for m in fake.calls)
    rels = [c.splitlines()[0] for c in fake.calls]
    assert rels == ['Node: /work/a/decisions', 'Node: /work/a/constraints', 'Node: /work/a/projects/mesh', 'Node: /work/a']
    assert 'Child index summaries:' in fake.calls[-1]
    index = client.request(dict(op='cat', path='/work/a/INDEX.md'))
    assert 'sources: [' in index['markdown'] and ids['s1'] not in index['markdown'] and index['event']['label'] == 'internal'
    assert pages.build(client, summarize=fake)['written'] == 0
    client.write(dict(kind='constraint', content='Pages are data'), scope='work/a')
    again = pages.build(client, summarize=fake)
    assert again['written'] == 2 and fake.calls[-2].startswith('Node: /work/a/constraints')
    assert len(client.request(dict(op='ls', path='/work/a/constraints'))['children']) == 3


def test_build_limits(tmp_path):
    home, b, client, agents, ids = setup(tmp_path)
    fake = Fake()
    assert pages.build(client, summarize=fake, dry_run=True) == dict(scopes=1, planned=4, written=0, skipped=1, failed=0)
    assert pages.build(client, summarize=fake, max_pages=1)['written'] == 1
    pages.build(client, summarize=fake, labels=('general',))
    assert ids['d1'] not in ''.join(fake.calls[1:])
    with pytest.raises(ValueError): pages.build(client, summarize=fake, labels=('general', 'secret'))
    def broken(material): raise RuntimeError('summarizer failed')
    client.write(dict(kind='decision', content='Another'), scope='work/a')
    assert pages.build(client, summarize=broken)['failed'] >= 1


def test_settings_memory_tree():
    assert validate({'memory_tree': {'writer_device': 'a'*32, 'summarize_labels': ['general'], 'model': 'claude-sonnet-5-5', 'max_pages': 5, 'budget_usd': 0.5}})
    for bad in ({'summarize_labels': ['secret']}, {'max_pages': 0}, {'budget_usd': 9}, {'writer_device': 'x'}, {'extra': 1}):
        with pytest.raises(ValueError): validate({'memory_tree': bad})


def test_cli_build_guard(monkeypatch, capsys):
    from agentmesh_log.cli import main
    class Client:
        def request(self, request): return dict(device_id='b'*32, read=[], write=[])
        def close(self): pass
    monkeypatch.setattr('agentmesh_log.cli.BrokerClient.from_file', lambda path: Client())
    monkeypatch.setattr('agentmesh_log.settings.load', lambda: {'memory_tree': {'writer_device': 'a'*32}})
    assert main(['memory', 'build', '--agent-key', 'k']) == 1
    assert json.loads(capsys.readouterr().out) == {'error': 'not_writer_device'}
    monkeypatch.setattr('agentmesh_log.settings.load', lambda: {})
    assert main(['memory', 'build', '--agent-key', 'k', '--dry-run']) == 0
    assert json.loads(capsys.readouterr().out) == dict(scopes=0, planned=0, written=0, skipped=0, failed=0)
