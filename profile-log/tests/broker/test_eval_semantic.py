import json
from pathlib import Path
import subprocess
import sys

import pytest

from . import eval_semantic as ev


@pytest.mark.parametrize('values,p,want', [([7], 95, 7), ([4, 1, 3, 2], 50, 2.5),
    ([0, 100], 95, 95), ([2, 9], 0, 2), ([2, 9], 100, 9)],
    ids=['one', 'median', 'p95', 'min', 'max'])
def test_percentile(values, p, want):
    assert ev.percentile(values, p) == want


@pytest.mark.parametrize('values,p', [([], 50), ([1], -1), ([1], 101)],
                         ids=['empty', 'low', 'high'])
def test_bad_pct(values, p):
    with pytest.raises(ValueError): ev.percentile(values, p)


def test_top_ids():
    assert ev.top_ids([]) == []
    rows = [dict(id=str(i), content='private') for i in range(7)]
    assert ev.top_ids(rows) == ['0', '1', '2', '3', '4']
    assert len(rows) == 7


def test_new_ids():
    keyword = [dict(id=str(i)) for i in range(6)]
    hybrid = [dict(id='0'), dict(id='5', ranking=dict(cosine=.8)), dict(id='new'),
              dict(id='1'), dict(id='2'), dict(id='outside')]
    assert ev.new_ids(keyword, hybrid) == [dict(id='5', cosine=.8), dict(id='new', cosine=None)]
    assert ev.new_ids([], []) == []


def test_histogram():
    assert ev.histogram([]) == {}
    assert ev.histogram([-1, -.05, 0, .049, .05, .3, .95, 1, None, float('nan'),
                         float('inf'), 1.1]) == {
        '-1.00..-0.95': 1, '-0.05..0.00': 1, '0.00..0.05': 2,
        '0.05..0.10': 1, '0.30..0.35': 1, '0.95..1.00': 2}


@pytest.mark.parametrize('profile', [None, 'work'], ids=['default', 'profile'])
def test_offline(tmp_path, monkeypatch, capsys, profile):
    queries = tmp_path/'queries'
    queries.write_text('# private comment\n\nลับ ; $(secret)\n  # skip\nsecond secret\n', encoding='utf-8')
    key = tmp_path/'key'
    key.write_text('KEY SECRET', encoding='utf-8')
    calls = []
    ticks = iter(range(40))
    monkeypatch.setattr(ev.time, 'perf_counter', lambda: next(ticks))

    def fake_run(argv, **kwargs):
        calls.append(argv)
        assert kwargs == dict(stdin=subprocess.DEVNULL, capture_output=True,
            text=True, encoding='utf-8', timeout=60, check=True,
            cwd=Path(ev.__file__).resolve().parents[2])
        assert argv[:4] == [sys.executable, '-m', 'agentmesh_log.cli', 'recall']
        assert argv[5:10] == ['--compat', '--agent-key', str(key), '--limit', '12']
        assert argv[10] == '--mode'
        assert argv[12:] == (['--profile', profile] if profile else [])
        mode = argv[11]
        rows = [dict(id='same', content='RESULT SECRET')]
        plan = {}
        if mode == 'hybrid':
            rows += [dict(id=f'h{i}', content='RESULT SECRET',
                          ranking=dict(semantic_rank=i+1, cosine=.8)) for i in range(6)]
            plan['retrieval'] = dict(effective='hybrid', degraded='partial',
                                      coverage=dict(ranked=6, missing=1))
        return subprocess.CompletedProcess(argv, 0, json.dumps(dict(query=argv[4],
            results=rows, query_plan=plan, evidence=[dict(content='EVIDENCE SECRET')])), 'STDERR SECRET')

    monkeypatch.setattr(ev.subprocess, 'run', fake_run)
    args = ['--queries', str(queries), '--agent-key', str(key)]
    if profile: args += ['--profile', profile]
    assert ev.main(args) == 0
    captured = capsys.readouterr()
    for private in ['ลับ', 'secret', 'SECRET', str(key), 'private comment']:
        assert private not in captured.out + captured.err
    reports = [json.loads(line) for line in captured.out.splitlines() if line.startswith('{')]
    assert len(calls) == 20
    assert [report['query_index'] for report in reports[:2]] == [1, 2]
    first, _, summary = reports
    assert first['keyword']['top5_ids'] == ['same']
    assert first['hybrid']['top5_ids'] == ['same', 'h0', 'h1', 'h2', 'h3']
    assert first['new_in_hybrid_top5'] == [dict(id=f'h{i}', cosine=.8) for i in range(4)]
    assert first['keyword']['retrieval'] == [dict(effective='keyword', degraded=None, coverage=None)]*5
    assert first['hybrid']['retrieval'][0]['coverage'] == dict(ranked=6, missing=1)
    assert summary['keyword'] == summary['hybrid'] == dict(p50_ms=1000, p95_ms=1000)
    assert summary['delta_p95_ms'] == 0
    assert summary['degraded_runs'] == 10
    assert summary['cosine_histogram'] == {'0.80..0.85': 60}
    assert 'mode       runs' in captured.out


@pytest.mark.parametrize('failure', ['exit', 'timeout', 'json'], ids=['exit', 'timeout', 'json'])
def test_failure(tmp_path, monkeypatch, capsys, failure):
    queries = tmp_path/'queries'
    queries.write_text('PRIVATE QUERY', encoding='utf-8')

    def fake_run(argv, **kwargs):
        if failure == 'exit':
            raise subprocess.CalledProcessError(1, argv, output='PRIVATE CONTENT', stderr='PRIVATE ERROR')
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(argv, 60, output='PRIVATE CONTENT')
        return subprocess.CompletedProcess(argv, 0, 'PRIVATE INVALID JSON')

    monkeypatch.setattr(ev.subprocess, 'run', fake_run)
    assert ev.main(['--queries', str(queries), '--agent-key', 'PRIVATE KEY',
                    '--python', '/fake/python', '--runs', '1', '--limit', '2']) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert 'PRIVATE' not in captured.err
    assert 'query index 1, mode keyword, run 1' in captured.err
