import pytest
from agentmesh_log.broker.keystore import BrokerState
from agentmesh_log.broker.grants import Grant, resolve, names, readable


def test_descendants():
    s = BrokerState.create(['work', 'personal']); tree = s.trees[0]
    a = tree.add(tree.root.scope_id, 'project', 'a')
    b = tree.add(tree.root.scope_id, 'project', 'b')
    g = Grant('1'*32, '2'*64, [a.scope_id], [a.scope_id], a.scope_id, tree.root.scope_id)
    assert readable(s, g) == {a.scope_id}
    assert resolve(s, 'work/a') == a.scope_id
    assert names(s)[a.scope_id] == 'work/a'
    old = g.digest(); g.read = [tree.root.scope_id]
    assert set(tree.scopes) == readable(s, g)
    assert b.scope_id not in g.write and g.digest() != old
    with pytest.raises(ValueError, match='denied'): resolve(s, 'work/missing')
    s.grants[g.agent_id] = g; s.revoke(g.agent_id)
    assert g.agent_id not in s.grants
