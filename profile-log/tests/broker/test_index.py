from agentmesh_log.broker.index import AgentIndex
from agentmesh_log.broker.query import analyze_query


def row(identifier='a'):
    return dict(id=identifier, content='migrate database', kind='fact', scope='work/a',
        profile='work', project=None, task_ref=None, confidence=.6, label='general', tags=[],
        created_at='2000-01-01T00:00:00Z', updated_at='2000-01-01T00:00:00Z',
        conflict=False, alternatives=[], source_type='durable')


def test_snapshot(tmp_path):
    a = AgentIndex('a', b'a'*32); b = AgentIndex('b', b'a'*32)
    a.replace([row()]); path = tmp_path/'a.snap'; a.save(path,'grant','replica')
    assert b'migrate' not in path.read_bytes()
    assert not b.load(path,'grant','replica') and b.rows() == []
    assert a.load(path,'grant','replica')
    assert not a.load(path,'changed','replica')
    assert a.connection is not b.connection
    assert a.connection.execute('PRAGMA temp_store').fetchone()[0] == 2


def test_selection():
    a = AgentIndex('a', b'a'*32); a.replace([row(), {**row('b'),'content':'database'}])
    response = a.recall(analyze_query('migrate database'),12,{'work/a'},{'work'})
    assert [r['id'] for r in response['results']] == ['a']
    assert a.recall(analyze_query('" OR *'),12,{'work/a'},{'work'})['results'] == []


def test_missing_fts(monkeypatch):
    import sqlite3
    import pytest
    from agentmesh_log.broker import index
    class Unavailable:
        def execute(self, sql):
            if 'VIRTUAL' in sql: raise sqlite3.OperationalError('missing FTS5')
        def close(self): pass
    monkeypatch.setattr(index.sqlite3,'connect',lambda *a,**kw:Unavailable())
    with pytest.raises(ValueError,match='unavailable'): AgentIndex('fixture',b'a'*32)


def test_recall_output_unchanged_by_refactor():
    a = AgentIndex('a', b'a'*32)
    a.replace([row(), {**row('b'),'content':'database only'}, {**row('c'),'content':'unrelated'}])
    plan = analyze_query('migrate database')
    for compat in (False, True):
        out = a.recall(plan, 12, {'work/a'}, {'work'}, compat=compat)
        assert set(out) == {'query','query_plan','candidate_counts','results','evidence'}
        assert 'retrieval' not in out['query_plan']
        assert [r['id'] for r in out['results']] == ['a']
