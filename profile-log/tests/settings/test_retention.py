import pytest
from agentmesh_log.settings import validate, merge


def test_retention_local_override():
    base = validate({'staging': {'retention': {'older_than_days': 30}}})
    local = validate({'staging': {'retention': {'older_than_days': 7}}}, local=True)
    assert merge(base, local)['staging']['retention']['older_than_days'] == 7


@pytest.mark.parametrize('value', [6, 0, -1, True, '30', 7.5])
def test_retention_invalid_days(value):
    with pytest.raises(ValueError): validate({'staging': {'retention': {'older_than_days': value}}})


def test_retention_unknown_keys():
    with pytest.raises(ValueError): validate({'staging': {'retention': {'enabled': True}}})
