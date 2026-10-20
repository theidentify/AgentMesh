import json
import os
from pathlib import Path
import subprocess
import sys

PACKAGE = str(Path(__file__).resolve().parents[2])


def cli(*args):
    return subprocess.run([sys.executable,'-m','agentmesh_log.cli',*map(str,args)],
        env={**os.environ,'PYTHONPATH':PACKAGE,'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True, encoding='utf-8',timeout=15)


def test_admin(tmp_path):
    home=tmp_path/'home'
    result=cli('broker','init','--home',home,'--profile','work','--profile','personal')
    assert result.returncode==0, result.stderr
    assert cli('broker','scope','add','--home',home,'work/a').returncode==0
    result=cli('broker','grant','--home',home,'--name','fixture','--read','work/a','--write','work/a')
    assert result.returncode==0, result.stderr
    record=json.loads(Path(json.loads(result.stdout)['agent_key']).read_text(encoding='utf-8'))
    assert set(record)=={'format','agent_cert','agent_private_key','device_public_key','address'}
    assert cli('broker','revoke','--home',home,record['agent_cert']['agent_id']).returncode==0
    assert cli('broker','sync','--home',home,'--filesystem',tmp_path/'remote').returncode==0
    assert cli('recall').returncode==2


def test_usage_private():
    marker='syntheticPrivateArgumentMarker'
    result=cli('recall','fixture','--agent-key','missing','--unknown',marker)
    assert result.returncode==2
    assert marker not in result.stdout+result.stderr
    assert json.loads(result.stdout)=={'error':'invalid_request'}
