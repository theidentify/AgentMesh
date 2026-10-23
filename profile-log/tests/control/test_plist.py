import plistlib
from agentmesh_log.control.layout import Layout
from agentmesh_log.control.plist import LABEL, render


def test_plist_render(tmp_path):
    data=plistlib.loads(render(Layout(tmp_path),python='/venv/bin/python'))
    assert data['Label']==LABEL=='org.agentmesh.control'
    assert data['ProgramArguments']==['/venv/bin/python','-m','agentmesh_log.cli','control','serve']
    assert data['EnvironmentVariables']=={'AGENTMESH_HOME':str(tmp_path)}
    assert data['KeepAlive'] is True and data['RunAtLoad'] is True and data['Umask']==63
    assert data['StandardErrorPath']==str(tmp_path/'logs'/'control.err.log')
