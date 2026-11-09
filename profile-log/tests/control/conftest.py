import pytest
from agentmesh_log.control import cli


@pytest.fixture(autouse=True)
def launchd_paths(monkeypatch):
    """The control tests exercise the launchd paths on every OS; Windows paths are asked for with windows=True."""
    monkeypatch.setattr(cli,'WINDOWS',False)
