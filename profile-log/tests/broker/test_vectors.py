import pytest
from agentmesh_log.broker.vectors import rank_exact, validate_vectors


def test_vectors():
    assert rank_exact(['b','a'],[[1,0],[1,0]],[1,0],2) == [('a',1),('b',1)]
    assert validate_vectors([[3,4]]) == [[.6,.8]]
    for values in ([[0,0]],[[True,1]],[[float('nan'),1]], [[1],[1,2]]):
        with pytest.raises(ValueError): validate_vectors(values)
