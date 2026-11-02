import json
import pytest
from agentmesh_log.broker import tree
from agentmesh_log.broker.service import Broker, Session
from agentmesh_log.cli import main
from .support import setup_home, remember


def row(i, scope='work/a', kind='decision', content='use central Ollama', label='internal', **kw):
    return dict(id='01M5HE0TAVV5C8P3Q471SM5D'+'%02d'%i, scope=scope, kind=kind, content=content, label=label,
        project=kw.get('project'), task_ref=kw.get('task_ref'), updated_at='2026-10-%02dT00:00:00Z'%(i+1))


ROWS = [row(1), row(2, kind='constraint', content='never sync vectors\nsecond line', project='mesh'),
    row(3, task_ref='m3c-1', project='mesh'), row(4, kind='fact', content='secret token', label='secret'),
    row(5, scope='work/b', kind='procedure'), row(6, kind='tool_call')]
SCOPES = {'work/a', 'work/b'}


def names(listing): return [c['name'] for c in listing['children']]


def test_synthesized_parents():
    assert names(tree.listing('/', ROWS, SCOPES)) == ['work/']
    work = tree.listing('/work', ROWS, SCOPES)['children']
    assert [(c['name'], c['type'], c['count']) for c in work] == [('a/','scope',5), ('b/','scope',1)]


def test_scope_node():
    listing = tree.listing('/work/a/', ROWS, SCOPES)
    assert names(listing) == ['projects/', 'tasks/', 'decisions/', 'constraints/', 'facts/']
    decisions = tree.listing('work/a/decisions', ROWS, SCOPES)['children']
    assert [c['name'] for c in decisions] == [ROWS[2]['id'], ROWS[0]['id']] and decisions[0]['type'] == 'event'
    assert tree.listing('/work/a/constraints', ROWS, SCOPES)['children'][0]['summary'] == 'never sync vectors'


def test_filters():
    assert names(tree.listing('/work/a/projects', ROWS, SCOPES)) == ['mesh/']
    assert names(tree.listing('/work/a/projects/mesh', ROWS, SCOPES)) == ['tasks/', 'decisions/', 'constraints/']
    assert names(tree.listing('/work/a/tasks', ROWS, SCOPES)) == ['M3C-1/']
    assert names(tree.listing('/work/a/projects/mesh/tasks/M3C-1/decisions', ROWS, SCOPES)) == [ROWS[2]['id']]


def test_secret_summary_hidden():
    facts = tree.listing('/work/a/facts', ROWS, SCOPES)['children']
    assert facts[0]['summary'] is None and facts[0]['label'] == 'secret'
    assert tree.listing('/work/a', ROWS, SCOPES)['children'][-1]['summary'] is None


@pytest.mark.parametrize('path', ['/personal', '/work/c', '/work/a/nope', '/work/a/decisions/facts', '/work/a/decisions/projects'], ids=['profile','scope','folder','kind2','filter'])
def test_unknown_denied(path):
    with pytest.raises(ValueError, match='denied'): tree.listing(path, ROWS, SCOPES)


@pytest.mark.parametrize('path', ['/work/../a', '/work//a', 'x'*513, None], ids=['dotdot','empty','long','none'])
def test_bad_path(path):
    with pytest.raises(ValueError, match='invalid_request'): tree.listing(path, ROWS, SCOPES)


def test_depth_and_caps(monkeypatch):
    nested = tree.listing('/work', ROWS, SCOPES, depth=2)['children'][0]
    assert nested['children'][:2] == ['projects/', 'tasks/']
    monkeypatch.setattr(tree, 'MAX_CHILDREN', 2)
    capped = tree.listing('/work/a', ROWS, SCOPES)
    assert len(capped['children']) == 2 and capped['more'] == 3
    with pytest.raises(ValueError): tree.listing('/', ROWS, SCOPES, depth=3)


def test_find_and_markdown():
    assert tree.find('/work/a/decisions/'+ROWS[0]['id'], ROWS, SCOPES)['id'] == ROWS[0]['id']
    assert tree.find(ROWS[4]['id'], ROWS, SCOPES)['scope'] == 'work/b'
    with pytest.raises(ValueError, match='denied'): tree.find('/work/a/facts/'+ROWS[0]['id'], ROWS, SCOPES)
    text = tree.markdown(ROWS[1])
    assert text.startswith('---\nid: ') and 'project: mesh\n' in text and text.endswith('never sync vectors\nsecond line\n')
    assert tree.canonical(ROWS[5]) == '/work/a/'+ROWS[5]['id']


def broker(tmp_path):
    home,state,store,agents = setup_home(tmp_path); b = Broker(home)
    a = Session(agents[0].certificate())
    for body in (dict(kind='decision', content='migrate database with Ollama', project='mesh'),
                 dict(kind='constraint', content='migrate database never on Fridays', task_ref='DB-1')):
        assert 'id' in remember(b, a, agents[0], body=body)
    return home, b, a, Session(agents[1].certificate())


def test_service_ls_cat(tmp_path):
    home, b, a, other = broker(tmp_path)
    assert names(b.request(a, dict(op='ls', path='/'))) == ['work/']
    assert names(b.request(a, dict(op='ls', path='/work'))) == ['a/']
    assert names(b.request(other, dict(op='ls', path='/work'))) == ['b/']
    assert b.request(other, dict(op='ls', path='/work/a')) == {'error': 'denied'}
    decision = b.request(a, dict(op='ls', path='/work/a/decisions'))['children'][0]
    page = b.request(a, dict(op='cat', path='/work/a/decisions/'+decision['name']))
    assert page['path'] == '/work/a/decisions/'+decision['name'] and 'migrate database with Ollama' in page['markdown']
    assert b.request(other, dict(op='cat', path=decision['name'])) == {'error': 'denied'}
    assert b.request(a, dict(op='ls', path='/', depth=True)) == {'error': 'invalid_request'}
    assert b.request(a, dict(op='ls', path='/', extra=1)) == {'error': 'invalid_request'}
    audit = [json.loads(l) for l in (home.root/'audit.jsonl').read_text(encoding='utf-8').splitlines()]
    assert [r['op'] for r in audit[-4:]] == ['cat', 'cat', 'ls', 'ls'] and audit[-4]['result_ids'] == [decision['name']]
    assert audit[-3]['outcome'] == 'denied'
    assert '/work/a/decisions' not in (home.root/'audit.jsonl').read_text(encoding='utf-8')


def test_service_grep(tmp_path):
    home, b, a, other = broker(tmp_path)
    everything = b.request(a, dict(op='grep', query='migrate database', path='/work/a'))
    assert len(everything['results']) == 2 and everything['path'] == '/work/a'
    assert [r['kind'] for r in b.request(a, dict(op='grep', query='migrate database', path='/work/a/constraints'))['results']] == ['constraint']
    assert [r['task_ref'] for r in b.request(a, dict(op='grep', query='migrate database', path='/work/a/tasks/db-1'))['results']] == ['DB-1']
    assert len(b.request(a, dict(op='grep', query='migrate database', path='/'))['results']) == 2
    assert b.request(other, dict(op='grep', query='migrate database', path='/work/a')) == {'error': 'denied'}
    audit = [json.loads(l) for l in (home.root/'audit.jsonl').read_text(encoding='utf-8').splitlines()]
    assert audit[-1]['op'] == 'grep' and audit[-1]['outcome'] == 'denied'


def test_cli_cat_prints_markdown(monkeypatch, capsys):
    class Client:
        def request(self, request):
            self.sent = request; return dict(path='/work/a/x', markdown='---\nid: x\n---\nbody\n', event={})
        def close(self): pass
    client = Client()
    monkeypatch.setattr('agentmesh_log.cli.BrokerClient.from_file', lambda path: client)
    assert main(['memory', 'cat', '--agent-key', 'k', '/work/a/x']) == 0
    assert capsys.readouterr().out == '---\nid: x\n---\nbody\n' and client.sent == dict(op='cat', path='/work/a/x')
    assert main(['memory', 'ls', '--agent-key', 'k', '--depth', '2']) == 0
    assert client.sent == dict(op='ls', path='/', depth=2)
    assert main(['memory', 'grep', '--agent-key', 'k', 'ollama', '/work', '--mode', 'hybrid']) == 0
    assert client.sent == dict(op='grep', path='/work', query='ollama', limit=12, mode='hybrid')
