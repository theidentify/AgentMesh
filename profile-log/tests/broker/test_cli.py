import json
import os
from pathlib import Path
import subprocess
import sys

PACKAGE = str(Path(__file__).resolve().parents[2])


# Native keychains must never be touched by automated tests. Admin CLI runs use
# its explicit backend/input seams; transport process tests remain unchanged.
class FakeBackend:
    def __init__(self): self.items={}
    def load(self,account): return self.items.get(account)
    def store(self,account,key): self.items[account]=key
    def delete(self,account): self.items.pop(account,None)

_backend=FakeBackend()

def cli(*args):
    import io
    from contextlib import redirect_stdout,redirect_stderr
    from agentmesh_log.cli import main
    from agentmesh_log.presence import ConsolePresence
    out,err=io.BytesIO(),io.StringIO(); stream=io.TextIOWrapper(out,encoding='utf-8')
    console=ConsolePresence(input_fn=lambda prompt:prompt.split()[1].rstrip(':'),isatty=lambda:True,output=lambda _:None)
    with redirect_stdout(stream),redirect_stderr(err):
        try: code=main(list(map(str,args)),backend=_backend,console=console)
        except SystemExit as exc: code=exc.code
    stream.flush(); text=out.getvalue().decode('utf-8')
    return subprocess.CompletedProcess(args,code,text,err.getvalue())


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
