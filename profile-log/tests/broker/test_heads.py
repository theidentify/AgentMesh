from agentmesh_log.broker.heads import heads


def test_conflicts():
    rows = [dict(id=i, scope=s, revises=r, created_at=t) for i,s,r,t in
            [('a','x',None,1),('b','x','a',2),('c','x','a',3),('d','y','a',4)]]
    result = {r['id']:r for r in heads(rows)}
    assert set(result) == {'c','d'}
    assert result['c']['conflict'] and result['c']['alternatives'] == ['b']
    rows.append(dict(id='e',scope='x',revises='c',created_at=5))
    assert {r['id']:r for r in heads(rows)}['e']['alternatives'] == ['b']
