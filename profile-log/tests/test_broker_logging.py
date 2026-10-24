import json
import sys
import pytest
from agentmesh_log import cli


def fake_broker(monkeypatch):
    calls=[]
    monkeypatch.setattr(cli,'BrokerHome',lambda path:calls.append('home'))
    monkeypatch.setattr(cli,'select_store',lambda *a:None)
    class Broker:
        def __init__(self,*a,**kw): pass
        def serve(self,**kw):
            cli.emit('output')
            if sys.stderr is not None: sys.stderr.write('error\n')
            calls.append('serve')
        def close(self): calls.append('close')
    monkeypatch.setattr(cli,'Broker',Broker)
    return calls


def test_log_dir_append_utf8_before_broker(tmp_path,monkeypatch):
    calls=fake_broker(monkeypatch)
    for name in ('broker.out.log','broker.err.log'): (tmp_path/name).write_text('before\n',encoding='utf-8')
    def home(path):
        assert sys.stdout.name==str(tmp_path/'broker.out.log')
        assert sys.stderr.name==str(tmp_path/'broker.err.log')
        calls.append('home')
    monkeypatch.setattr(cli,'BrokerHome',home)
    stdout,stderr=sys.stdout,sys.stderr
    try:
        assert cli.main(['broker','run','--home',str(tmp_path),'--log-dir',str(tmp_path)])==0
        assert sys.stdout.line_buffering and sys.stderr.line_buffering
        assert sys.stdout.encoding==sys.stderr.encoding=='utf-8'
        assert sys.stdout.errors==sys.stderr.errors=='backslashreplace'
        sys.stdout.write('ไทย\ud800\n'); sys.stderr.write('ไทย\ud800\n')
        assert (tmp_path/'broker.out.log').read_text(encoding='utf-8')=='before\noutput\n{"ok": true}\nไทย\\ud800\n'
        assert (tmp_path/'broker.err.log').read_text(encoding='utf-8')=='before\nerror\nไทย\\ud800\n'
        assert calls==['home','serve','close']
    finally:
        if sys.stdout is not stdout: sys.stdout.close()
        if sys.stderr is not stderr: sys.stderr.close()
        sys.stdout,sys.stderr=stdout,stderr


@pytest.mark.parametrize('kind',['relative','missing','file'],ids=['relative','missing','file'])
def test_invalid_log_dir(tmp_path,monkeypatch,capsys,kind):
    calls=fake_broker(monkeypatch); file=tmp_path/'file'; file.write_text('',encoding='utf-8')
    (tmp_path/'relative').mkdir(); monkeypatch.chdir(tmp_path)
    directory={'relative':'relative','missing':str(tmp_path/'missing'),'file':str(file)}[kind]
    assert cli.main(['broker','run','--home',str(tmp_path),'--log-dir',directory])==1
    assert json.loads(capsys.readouterr().out)=={'error':'invalid_request'}
    assert calls==[]


def test_pythonw_none_streams(tmp_path,monkeypatch):
    calls=fake_broker(monkeypatch)
    with monkeypatch.context() as patch:
        patch.setattr(sys,'stdout',None); patch.setattr(sys,'stderr',None)
        assert cli.main(['broker','run','--home',str(tmp_path)])==0
    assert calls==['home','serve','close']
