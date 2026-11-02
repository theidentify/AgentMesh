"""SQLite retirement R3: Insight's profile-log counts come from the broker and carry no memory text."""
import json
from types import SimpleNamespace

import adapters
import remote_access


def run(answer, code=0):
    calls = []
    def runner(argv, **kw):
        calls.append(argv)
        return SimpleNamespace(stdout=json.dumps(answer).encode(), returncode=code)
    return runner, calls


def test_not_configured(tmp_path):
    value = adapters.memory_stats(tmp_path)
    assert (value['available'], value['configured']) == (False, False)


def test_projection_drops_unknown_fields(tmp_path):
    (tmp_path / 'agents').mkdir(); (tmp_path / 'agents/insight.key').write_text('{}')
    answer = dict(total=3, more_projects=0, content='LEAK', scopes=[dict(scope='work/a', profile='work', count=3, keyed=1,
                  kinds={'fact': 2, 'decision': 1, 'evil<script>': 9}, labels={'general': 3, 'x': 1},
                  updated_at='2026-11-02T00:00:00Z', content='LEAK')], projects=[dict(project='demo', count=2, content='LEAK')])
    runner, calls = run(answer)
    value = adapters.memory_stats(tmp_path, runner=runner, executable='/py')
    assert calls == [['/py', '-m', 'agentmesh_log.cli', 'stats', '--agent-key', str(tmp_path / 'agents/insight.key')]]
    assert value['available'] and value['total'] == 3 and 'LEAK' not in json.dumps(value) and 'evil' not in json.dumps(value)
    assert value['scopes'][0]['kinds'] == {'fact': 2, 'decision': 1} and value['scopes'][0]['labels'] == {'general': 3}
    assert value['projects'] == [dict(project='demo', count=2)]


def test_broker_errors_are_fixed_codes(tmp_path):
    key = tmp_path / 'k'; key.write_text('{}')
    runner, _ = run({'error': 'broker_unreachable'}, code=1)
    assert adapters.memory_stats(tmp_path, key, runner=runner)['error'] == 'Profile log unavailable (broker_unreachable)'
    runner, _ = run({'error': '/etc/passwd text'}, code=1)
    assert adapters.memory_stats(tmp_path, key, runner=runner)['error'] == 'Profile log unavailable (unavailable)'


def test_remote_path_allowed():
    assert '/api/log-counts' in remote_access.ALLOWED_PATHS


def test_summarizer_state_counts(tmp_path):
    import storage
    assert storage.summarizer_state(tmp_path)['available'] is False
    jobs = tmp_path / 'jobs'; jobs.mkdir()
    (jobs / 'transcript-cursors.json').write_text(json.dumps(dict(format='x', files={'/a': dict(done=['k1', 'k2']), '/b': dict(done=[])})))
    (jobs / 'summary-state.json').write_text(json.dumps(dict(format='y', pending=False, success=dict(day='2026-11-02'),
                                                            quarantine=[dict(memory_key='k', reason='r')])))
    value = storage.summarizer_state(tmp_path)
    assert (value['tracked_transcripts'], value['pending_events'], value['quarantine'], value['last_completed_day']) == (2, 2, 1, '2026-11-02')
    assert [f['name'] for f in value['files']] == ['transcript-cursors.json', 'summary-state.json'] and '/a' not in json.dumps(value)
