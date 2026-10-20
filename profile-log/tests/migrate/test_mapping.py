import pytest
from agentmesh_log.migrate.mapping import Mapping


def test_mapping():
    value = dict(format='agentmesh-migration-mapping-v1', null_project='work/common',
                 projects={'exact':'personal/common'}, prefixes=[dict(prefix='proj',target='work/common'),dict(prefix='project',target='skip')])
    value['global']='work/common'
    m=Mapping(value)
    assert m.target(dict(scope='project',project='exact'))=='personal/common'
    assert m.target(dict(scope='task',project='project-a'))=='skip'
    assert m.target(dict(scope='global',project='unknown'))=='work/common'
    with pytest.raises(ValueError): m.target(dict(scope='task',project='unknown'))
    with pytest.raises(ValueError): Mapping({**value,'extra':1})
    with pytest.raises(ValueError): Mapping({**value,'null_project':'../escape'})
