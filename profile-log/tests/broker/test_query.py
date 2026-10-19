from agentmesh_log.broker.query import analyze_query, _score


def test_port():
    plan = analyze_query('why migrate database AM-42', project='fixture')
    assert plan.task_refs == ('AM-42',) and plan.intents == ('decision',)
    assert analyze_query('ควรทำแผนงานอย่างไร').terms == ('แผน',)
    rows = [dict(id=k, kind=k, content='migrate database', confidence=.6,
                 project='fixture', task_ref='AM-42', updated_at='2000-01-01T00:00:00Z')
            for k in ('fact', 'constraint', 'decision')]
    for row in rows: _score(row, plan)
    assert [r['id'] for r in sorted(rows, key=lambda r:r['score'], reverse=True)] == ['decision','constraint','fact']
