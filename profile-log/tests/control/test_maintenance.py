import pytest
from agentmesh_log.control import maintenance
from agentmesh_log.control.layout import Layout


def test_off_by_default(tmp_path):
    assert maintenance.status(Layout(tmp_path))==dict(on=False,since=None,reason=None)


def test_on_is_idempotent_then_off(tmp_path):
    layout=Layout(tmp_path)
    first=maintenance.turn_on(layout,'upgrade 0.3.3',now=lambda:'2026-10-23T10:00:00+00:00')
    assert first==dict(on=True,since='2026-10-23T10:00:00+00:00',reason='upgrade 0.3.3')
    assert maintenance.turn_on(layout,'other',now=lambda:'later')==first
    assert maintenance.turn_off(layout)==dict(on=False,since=None,reason=None)


@pytest.mark.parametrize('reason',['',None,'x'*121,'line\nbreak',7],ids=['empty','none','too-long','newline','int'])
def test_reason_validated(tmp_path, reason):
    with pytest.raises(ValueError): maintenance.turn_on(Layout(tmp_path),reason)
    assert not Layout(tmp_path).maintenance.exists()
