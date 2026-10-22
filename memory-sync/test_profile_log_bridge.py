import json
import os
import subprocess
from types import SimpleNamespace
import pytest
import profile_log_bridge as bridge


def config(tmp_path):
    value=dict(format='agentmesh-profile-log-bridge-v1',enabled=True,recall=True,dual_write=True,authority=True,
        cli_argv=[str(tmp_path/'python'),'-m','agentmesh_log.cli'],agent_key=str(tmp_path/'recall.key'),
        import_agent_key=str(tmp_path/'import.key'),mapping=str(tmp_path/'mapping.json'),profile='work',timeout_seconds=1,recall_fallback=True,device_id='a'*32,staging_node='synthetic')
    db=tmp_path/'source.db'; path=tmp_path/'source.db.profile-log.json'
    path.write_text(json.dumps(value),encoding='utf-8'); path.chmod(0o600)
    return db,path,value


def test_p5_8_detached(tmp_path,monkeypatch):
    db,path,value=config(tmp_path); calls=[]
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**k:calls.append((a,k)))
    bridge.notify_rows(db,[('memory_items',1)])
    assert len(calls)==1 and calls[0][0][0][-2:]==['--ref','memory_items:1']
    assert calls[0][1]['stdout']==subprocess.DEVNULL
    if os.name!='nt': assert calls[0][1]['start_new_session']
    value['authority']=False; path.write_text(json.dumps(value),encoding='utf-8')
    bridge.notify_rows(db,[('memory_items',1)])
    assert len(calls)==1


@pytest.mark.parametrize('change',['mode','extra','relative','symlink'])
def test_p5_9_bad_config(tmp_path,capsys,change):
    db,path,value=config(tmp_path)
    if change=='mode':
        path.chmod(0o644)
        if os.name=='nt':
            assert bridge.load_config(db)==value
            assert capsys.readouterr().err==''
            return
    elif change=='extra':
        value['extra']=True; path.write_text(json.dumps(value),encoding='utf-8')
    elif change=='relative':
        value['mapping']='relative'; path.write_text(json.dumps(value),encoding='utf-8')
    else:
        other=path.with_suffix('.target'); path.rename(other); path.symlink_to(other)
    assert bridge.load_config(db) is None
    assert len(capsys.readouterr().err.splitlines())==1


@pytest.mark.parametrize('writer',['bounded','summarize'])
def test_p5_8_after_commit(monkeypatch,writer):
    from test_bounded_digest import BoundedTests
    from test_summarize_memory import SummarizeTests
    import sqlite3
    test=(BoundedTests if writer=='bounded' else SummarizeTests)(); test.setUp()
    calls=[]
    def notify(database,refs):
        with sqlite3.connect(database) as c:
            for table,ident in refs:
                assert c.execute('SELECT 1 FROM '+table+' WHERE id=?',(ident,)).fetchone()
        calls.append(refs)
        raise RuntimeError('synthetic failure')
    monkeypatch.setattr(bridge,'notify_rows',notify)
    try:
        api=test.api()
        task=api.prepare(test.db) if writer=='bounded' else api.prepare_task(test.db)
        response=test.payload(task) if writer=='bounded' else test.response(task)
        result=api.apply(test.db,task,response) if writer=='bounded' else api.commit_response(test.db,task,response)
        assert result['status']=='committed' and len(calls)==1
        assert {r[0] for r in calls[0]}=={'memory_items','memory_summaries'}
        result=api.apply(test.db,task,response) if writer=='bounded' else api.commit_response(test.db,task,response)
        assert result['status']=='stale' and len(calls)==1
    finally: test.doCleanups()


def test_p5_9_hybrid_and_rollback(tmp_path,monkeypatch,capsys):
    import sqlite_memory
    import sqlite3
    import recall_memory
    db,path,value=config(tmp_path); sqlite_memory.init_database(db)
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO memory_items(id,kind,scope,content,created_at,updated_at) VALUES(1,'fact','global','synthetic sqlite memory','2020-01-01','2020-01-01')")
    value['enabled']=False; path.write_text(json.dumps(value),encoding='utf-8')
    golden=recall_memory.recall(db,'synthetic memory')
    value['enabled']=True; path.write_text(json.dumps(value),encoding='utf-8')
    row=dict(id='synthetic-ulid',author_device='a'*32,source_ref=dict(table='memory_items',id=1),source_type='durable',kind='fact',content='synthetic broker memory',
        project=None,task_ref=None,confidence=1.,scope='work/common',profile='work',label='general',created_at='2020-01-01',updated_at='2020-01-01',
        source=dict(table='memory_items',id=1,scope='global',scope_key=None,memory_key=None,version=None,created_at='2020-01-01',updated_at='2020-01-01'),evidence_event_ids=[])
    monkeypatch.setattr(bridge,'broker_recall',lambda *args:dict(results=[row]))
    result=recall_memory.recall(db,'synthetic memory')
    assert result['backend']=='broker'
    assert result['results'][0]['content']==row['content']
    assert set(result['results'][0])==set(golden['results'][0])|{'profile','source_ref'}
    def unavailable(*args): raise bridge.BrokerUnavailable()
    monkeypatch.setattr(bridge,'broker_recall',unavailable)
    fallback=recall_memory.recall(db,'synthetic memory')
    assert fallback.pop('backend')=='sqlite-fallback' and fallback==golden
    assert len(capsys.readouterr().err.splitlines())==1
    value['enabled']=False; path.write_text(json.dumps(value),encoding='utf-8')
    assert json.dumps(recall_memory.recall(db,'synthetic memory'))==json.dumps(golden)


@pytest.mark.parametrize('failure',['exit','timeout','json','shape'])
def test_p5_9_cli_failures(tmp_path,monkeypatch,failure):
    from recall_memory import analyze_query
    _,_,value=config(tmp_path)
    def execute(*args,**kwargs):
        assert kwargs['encoding']=='utf-8' and 0<kwargs['timeout']<=1
        if failure=='timeout': raise subprocess.TimeoutExpired('synthetic',1)
        return SimpleNamespace(returncode=1 if failure=='exit' else 0,stdout='bad' if failure=='json' else '{}')
    monkeypatch.setattr(subprocess,'run',execute)
    with pytest.raises(bridge.BrokerUnavailable): bridge.broker_recall(analyze_query('synthetic'),40,value)


def test_p5_8_hanging_child_does_not_block(tmp_path,monkeypatch):
    import sys
    import time
    db,path,value=config(tmp_path)
    stub=tmp_path/'stub.py'
    stub.write_text('import time\ntime.sleep(2)\n',encoding='utf-8')
    value['cli_argv']=[sys.executable,str(stub)]; path.write_text(json.dumps(value),encoding='utf-8')
    real=subprocess.Popen; children=[]
    def start(*args,**kwargs):
        child=real(*args,**kwargs); children.append(child); return child
    monkeypatch.setattr(subprocess,'Popen',start)
    try:
        started=time.monotonic(); bridge.notify_rows(db,[('memory_items',1)])
        assert time.monotonic()-started<1 and len(children)==1
    finally:
        for child in children: child.terminate(); child.wait(timeout=5)


def test_p5_9_stored_golden(tmp_path):
    import sqlite3
    from pathlib import Path
    import recall_memory
    db=tmp_path/'synthetic.db'
    with sqlite3.connect(db) as c:
        c.executescript(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))
        c.execute("INSERT INTO memory_items(id,kind,scope,content,created_at,updated_at) VALUES(1,'fact','global','synthetic sqlite memory','2020-01-01','2020-01-01')")
    expected=(Path(__file__).parent/'tests/fixtures/profile_log_golden.json').read_text(encoding='utf-8')
    assert json.dumps(recall_memory.recall(db,'synthetic memory'),ensure_ascii=False)==expected


@pytest.mark.parametrize('writer',['bounded','summarize'])
def test_failed_commit(monkeypatch,writer):
    from test_bounded_digest import BoundedTests
    from test_summarize_memory import SummarizeTests
    import sqlite3
    test=(BoundedTests if writer=='bounded' else SummarizeTests)(); test.setUp()
    calls=[]
    monkeypatch.setattr(bridge,'notify_rows',lambda *args:calls.append(args))
    try:
        api=test.api()
        task=api.prepare(test.db) if writer=='bounded' else api.prepare_task(test.db)
        response=test.payload(task) if writer=='bounded' else test.response(task)
        connect=api.connect
        class FailedCommit:
            def __init__(self,c): self.c=c
            def __enter__(self): self.c.__enter__(); return self
            def __exit__(self,*args): return self.c.__exit__(*args)
            def __getattr__(self,name): return getattr(self.c,name)
            def commit(self): raise sqlite3.IntegrityError('injected commit failure')
        from contextlib import contextmanager
        @contextmanager
        def failing_connect(*args,**kwargs):
            with connect(*args,**kwargs) as c: yield FailedCommit(c)
        monkeypatch.setattr(api,'connect',failing_connect)
        with pytest.raises(sqlite3.IntegrityError):
            if writer=='bounded': api.apply(test.db,task,response)
            else: api.commit_response(test.db,task,response)
        assert calls==[]
    finally: test.doCleanups()


@pytest.mark.parametrize('mode',['ok','exit','timeout','json','shape'])
def test_subprocess_recall(tmp_path,capsys,mode):
    import sys
    import sqlite3
    import sqlite_memory
    import recall_memory
    db,path,value=config(tmp_path); sqlite_memory.init_database(db)
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO memory_items(id,kind,scope,content,created_at,updated_at) VALUES(1,'fact','global','synthetic sqlite memory','2020-01-01','2020-01-01')")
        from test_recall_memory import event
        eid=event(c,'unrelated synthetic evidence')
        c.execute('INSERT INTO memory_sources VALUES(?,?)',(1,eid))
    golden=recall_memory._recall(db,'synthetic memory')
    row=dict(id='synthetic-ulid',author_device='a'*32,source_ref=dict(table='memory_items',id=1),source_type='durable',kind='fact',content='synthetic broker memory',project=None,task_ref=None,confidence=1.,scope='work/common',profile='work',label='general',created_at='2020-01-01',updated_at='2020-01-01',source=dict(table='memory_items',id=1,scope='global',scope_key=None,memory_key=None,version=None,created_at='2020-01-01',updated_at='2020-01-01'),evidence_event_ids=[])
    stub=tmp_path/'stub.py'
    programs={'ok':'print('+repr(json.dumps(dict(query='synthetic memory',query_plan={},candidate_counts={},results=[row],evidence=[])))+')','exit':'raise SystemExit(2)','timeout':'import time; time.sleep(2)','json':"print('invalid')",'shape':"print('{}')"}
    stub.write_text(programs[mode],encoding='utf-8')
    value['cli_argv']=[sys.executable,str(stub)];path.write_text(json.dumps(value),encoding='utf-8')
    result=recall_memory.recall(db,'synthetic memory')
    if mode=='ok':
        assert result.pop('backend')=='broker'
        assert result['results'][0]['content']==row['content']
        assert set(result['results'][0])==set(golden['results'][0])|{'profile','source_ref'}
        expected=[dict(e,source_id=row['id']) for e in golden['evidence']]
        assert result['evidence']==expected and result['evidence']
        assert capsys.readouterr().err==''
    else:
        assert result.pop('backend')=='sqlite-fallback'
        assert result==golden
        assert len(capsys.readouterr().err.splitlines())==1


def test_rollback_state(tmp_path,monkeypatch):
    import sqlite3
    import sqlite_memory
    import recall_memory
    db,path,value=config(tmp_path); sqlite_memory.init_database(db)
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO memory_items(id,kind,scope,content,created_at,updated_at) VALUES(1,'fact','global','synthetic sqlite memory','2020-01-01','2020-01-01')")
    golden=recall_memory._recall(db,'synthetic memory')
    value['enabled']=False;path.write_text(json.dumps(value),encoding='utf-8')
    def tree(): return {str(p.relative_to(tmp_path)):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    before=tree()
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**k:pytest.fail('disabled hook started'))
    bridge.notify_rows(db,[('memory_items',1)])
    assert json.dumps(recall_memory.recall(db,'synthetic memory'))==json.dumps(golden)
    assert tree()==before


@pytest.mark.parametrize('options',[{},dict(project='Synthetic'),dict(task='SYN-1')],ids=['all','project','task'])
def test_sql_golden(tmp_path,monkeypatch,options):
    import hashlib
    import sqlite3
    from pathlib import Path
    import recall_memory
    db=tmp_path/'synthetic.db'
    with sqlite3.connect(db) as c:
        c.executescript(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))
        c.execute("INSERT INTO memory_items(id,kind,scope,content,created_at,updated_at) VALUES(1,'fact','global','synthetic sqlite memory','2020-01-01','2020-01-01')")
    statements=[];connect=sqlite3.connect
    def traced(*args,**kwargs):
        c=connect(*args,**kwargs);c.set_trace_callback(statements.append);return c
    monkeypatch.setattr(sqlite3,'connect',traced)
    recall_memory.recall(db,'synthetic memory',**options)
    digest=hashlib.sha256(json.dumps(statements,ensure_ascii=False).encode('utf-8')).hexdigest()
    expected=json.loads((Path(__file__).parent/'tests/fixtures/profile_log_sql.json').read_text(encoding='utf-8'))
    assert digest==expected[next(iter(options),'all')]


@pytest.mark.parametrize('flag',['absent','enabled','authority','dual_write'])
def test_hook_disabled(tmp_path,monkeypatch,flag):
    db,path,value=config(tmp_path)
    if flag=='absent': path.unlink()
    else:
        value[flag]=False;path.write_text(json.dumps(value),encoding='utf-8')
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**kw:pytest.fail('inactive hook started'))
    bridge.notify_rows(db,[('memory_items',1)])
