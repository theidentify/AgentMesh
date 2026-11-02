import json
import pytest
from . import eval_tree as ev
from .test_pages import setup, page


class FakeCli:
    """The CLI argv eval_tree builds, answered by an in-process broker session."""
    def __init__(self, client): self.client, self.calls = client, []
    def __call__(self, *args):
        self.calls.append(args)
        if args[0] == 'recall':
            request = dict(op='recall', query=args[1], limit=int(args[3]), mode=args[5])
        elif args[1] == 'grep':
            request = dict(op='grep', query=args[2], path=args[3], **({'limit': int(args[5])} if len(args) > 5 else {}))
        else:
            request = dict(op=args[1], path=args[2])
        answer = self.client.request(request)
        return answer, 1.0, len(json.dumps(answer))


def scripted(*steps):
    queue = list(steps)
    def decide(query, history):
        action, path, q = queue.pop(0)
        return dict(action=action, path=path, query=q), 0.01
    return decide


@pytest.fixture
def world(tmp_path):
    home, b, client, agents, ids = setup(tmp_path)
    client.write(page('decisions/INDEX', [ids['d2']]), scope='work/a', tags=['page:decisions/INDEX'])
    return FakeCli(client), ids


def test_load_queries(tmp_path):
    good = tmp_path/'q.jsonl'
    good.write_text('# private\n{"query": "ollama host", "targets": ["01M5HE0TAVV5C8P3Q471SM5DKF"]}\n', encoding='utf-8')
    assert ev.load_queries(good) == [dict(query='ollama host', targets={'01M5HE0TAVV5C8P3Q471SM5DKF'})]
    for line in ('{"query": "x", "targets": []}', '{"query": " ", "targets": ["01M5HE0TAVV5C8P3Q471SM5DKF"]}',
                 '{"query": "x", "targets": ["nope"]}', '{"query": "x"}', '', '[1]'):
        bad = tmp_path/'bad.jsonl'; bad.write_text(line, encoding='utf-8')
        with pytest.raises(ValueError): ev.load_queries(bad)


def test_nav_page_sources(world):
    cli, ids = world
    decide = scripted(('ls', '/work', ''), ('ls', '/work/a', ''), ('cat', '/work/a/decisions/INDEX.md', ''))
    result = ev.navigate(cli, decide, 'where do pages live', {ids['d2']}, 8)
    assert result == dict(hit=True, calls=4, calls_to_hit=4, bytes=result['bytes'], ms=4.0,
                          actions=['ls', 'ls', 'ls', 'cat'], cost_usd=0.03)


def test_nav_limits(world):
    cli, ids = world
    assert ev.navigate(cli, scripted(('grep', '/work/a', 'vectors')), 'sync vectors?', {ids['c1']}, 8)['calls_to_hit'] == 2
    missed = ev.navigate(cli, scripted(('done', '', '')), 'x', {ids['c1']}, 8)
    assert missed['hit'] is False and missed['calls'] == 1
    capped = ev.navigate(cli, scripted(*[('ls', '/work', '')]*10), 'x', {ids['c1']}, 3)
    assert capped['calls'] == 3 and capped['hit'] is False
    def broken(query, history): raise RuntimeError('claude failed')
    assert ev.navigate(cli, broken, 'x', {ids['c1']}, 8)['calls'] == 1
    secret = ev.navigate(cli, scripted(('cat', ids['s1'], '')), 'x', {ids['s1']}, 8)
    assert secret['hit'] is True  # the agent here holds allow_secret inside an open presence window


def test_observe_secret(world):
    cli, ids = world
    answer, _, _ = cli('memory', 'ls', '/work/a/facts')
    text, reached = ev.observe('ls', answer)
    assert 'sk-not-a-real-one' not in text and reached == set()
    text, reached = ev.observe('grep', cli('memory', 'grep', 'vectors', '/work')[0])
    assert ids['c1'] in reached and 'Never sync vectors' in text


def test_main_ids_only(world, tmp_path):
    cli, ids = world
    queries = tmp_path/'q.jsonl'
    queries.write_text('\n'.join(json.dumps(dict(query=q, targets=[t])) for q, t in
                                 (('Never sync vectors', ids['c1']), ('Pages live in their own scope', ids['d2']))), encoding='utf-8')
    lines = []
    decide = scripted(('grep', '/work', 'vectors'), ('cat', '/work/a/decisions/INDEX.md', ''))
    assert ev.main(['--queries', str(queries), '--agent-key', 'k', '--modes', 'keyword,hybrid,grep,tree'],
                   cli=cli, decide=decide, out=lines.append) == 0
    rows = [json.loads(line) for line in lines]
    assert [r.get('query_index') for r in rows[:2]] == [1, 2]
    assert rows[0]['keyword']['rank'] == 1 and rows[0]['hybrid']['effective'] == 'keyword'
    assert rows[0]['tree']['hit'] and rows[1]['tree']['calls_to_hit'] == 2
    summary = rows[-1]
    assert summary['summary'] and summary['keyword']['hit_at_5'] == 2 and summary['tree']['hits'] == 2
    assert summary['only'] == dict(keyword=0, hybrid=0, grep=0, tree=0) and summary['none'] == 0
    text = '\n'.join(lines)
    for private in ('Never sync vectors', 'Pages live', 'Ollama'): assert private not in text


def test_main_rejects_bad_input(tmp_path, capsys):
    assert ev.main(['--queries', str(tmp_path/'missing'), '--agent-key', 'k'], cli=object(), out=lambda s: None) == 1
    assert ev.main(['--queries', 'x', '--agent-key', 'k', '--modes', 'bogus'], cli=object(), out=lambda s: None) == 2
