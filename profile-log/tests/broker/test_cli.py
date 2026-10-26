import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

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


@pytest.mark.parametrize('mode',[None,'keyword','hybrid'],ids=['default','kw','hy'])
def test_recall_mode(monkeypatch,mode):
    from agentmesh_log import cli as module
    requests=[]; closed=[]
    def request(value):
        requests.append(value)
        return {'results':[]}
    client=SimpleNamespace(request=request,close=lambda:closed.append(True))
    monkeypatch.setattr(module.BrokerClient,'from_file',lambda _:client)
    flags=[] if mode is None else ['--mode',mode]
    result=cli('recall','database','--agent-key','synthetic',*flags)
    assert result.returncode==0, result.stdout
    assert requests==[dict(op='recall',query='database',project=None,task=None,
        limit=12,profiles=None,scopes=None,compat=False,**({} if mode is None else {'mode':mode}))]
    assert closed==[True]


@pytest.mark.parametrize('action',['store','check'],ids=['store','check'])
@pytest.mark.parametrize('kind',[None,'s3','access'],ids=['default','s3','access'])
def test_creds_kind(monkeypatch,action,kind):
    from agentmesh_log.storage import r2creds
    calls=[]
    def credentials(action,ref,**kwargs):
        calls.append((action,ref,kwargs))
        return {'ok':True}
    monkeypatch.setattr(r2creds,'credentials_command',credentials)
    flags=[] if kind is None else ['--kind',kind]
    result=cli('r2-credentials',action,'--ref','wincred:synthetic',*flags)
    assert result.returncode==0, result.stdout
    assert json.loads(result.stdout)=={'ok':True}
    assert calls==[(action,'wincred:synthetic',{'kind':kind or 's3'})]


@pytest.mark.parametrize('args',[
    ['recall','database','--agent-key','synthetic','--mode','vector'],
    ['r2-credentials','store','--ref','wincred:synthetic','--kind','other'],
    ['r2-credentials','check','--ref','wincred:synthetic','--kind','other'],
],ids=['mode','store','check'])
def test_bad_choice(monkeypatch,args):
    from agentmesh_log import cli as module
    from agentmesh_log.storage import r2creds
    monkeypatch.setattr(module.BrokerClient,'from_file',lambda _:pytest.fail('client opened'))
    monkeypatch.setattr(r2creds,'credentials_command',lambda *a,**kw:pytest.fail('credentials called'))
    result=cli(*args)
    assert result.returncode==2
    assert json.loads(result.stdout)=={'error':'invalid_request'}
