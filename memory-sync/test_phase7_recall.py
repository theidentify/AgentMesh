import json
import sqlite3
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest
import profile_log_bridge as bridge
import recall_memory as recall
import sqlite_memory

D='a'*32
OTHER='b'*32


def config(tmp_path,profiles=('work','personal')):
    return dict(format='agentmesh-profile-log-bridge-v1',enabled=True,recall=True,dual_write=True,authority=True,
        cli_argv=[sys.executable,str(tmp_path/'stub.py')],agent_key=str(tmp_path/'recall.key'),
        import_agent_key=str(tmp_path/'import.key'),mapping=str(tmp_path/'mapping.json'),profiles=list(profiles),
        timeout_seconds=1,recall_fallback=False,device_id=D,staging_node='synthetic')


def row(ident='one',profile='work',ref=None,author=None,summary=False):
    value=dict(id=ident,source_ref=ref,source_type='summary' if summary else 'durable',kind='summary' if summary else 'fact',
        content='synthetic broker memory',project=None,task_ref=None,confidence=1.,scope=profile+'/common',profile=profile,
        label='general',created_at='2020-01-01',updated_at='2020-01-01',source={},evidence_event_ids=[])
    if author: value['author_device']=author
    return value


def answer(rows):
    return dict(query='synthetic',query_plan={},candidate_counts={'durable':len(rows),'summaries':0,'events':0},results=rows,evidence=[])


def save(db,value):
    path=db.with_name(db.name+'.profile-log.json');path.write_text(json.dumps(value),encoding='utf-8');path.chmod(0o600)


def staging(tmp_path):
    from test_recall_memory import event
    db=tmp_path/'staging.db';sqlite_memory.init_database(db)
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO memory_items(id,kind,scope,content,created_at,updated_at) VALUES(1,'fact','global','synthetic SQLITE ONLY memory','2020-01-01','2020-01-01')")
        eid=event(c,'synthetic SQLITE EVIDENCE memory')
        c.execute('INSERT INTO memory_sources VALUES(?,?)',(1,eid))
    return db,eid


def test_p7_1_broker_only(tmp_path,monkeypatch):
    db,_=staging(tmp_path);value=config(tmp_path);save(db,value)
    monkeypatch.setattr(bridge,'broker_recall',lambda *a:answer([row()]))
    monkeypatch.setattr(recall,'open_readonly',lambda *a:pytest.fail('SQLite candidate lookup'))
    result=recall.recall(db,'synthetic memory')
    assert result['backend']=='broker' and [r['id'] for r in result['results']]==['one']
    assert 'SQLITE' not in json.dumps(result)


def test_p7_1_down_cli_no_sqlite(tmp_path,monkeypatch,capsys):
    db,_=staging(tmp_path);save(db,config(tmp_path))
    def fail(*a): raise bridge.BrokerUnavailable()
    monkeypatch.setattr(bridge,'broker_recall',fail)
    assert recall.main([str(db),'synthetic memory'])==1
    output=capsys.readouterr()
    assert json.loads(output.out)=={'error':'profile_log_unavailable'} and 'SQLITE' not in output.out


def test_p7_1_explicit_fallback(tmp_path,monkeypatch):
    db,_=staging(tmp_path);value=config(tmp_path);value['recall_fallback']=True;save(db,value)
    def fail(*a): raise bridge.BrokerUnavailable()
    monkeypatch.setattr(bridge,'broker_recall',fail)
    result=recall.recall(db,'synthetic memory')
    assert result['backend']=='sqlite-fallback'
    assert 'SQLITE ONLY' in json.dumps(result)


@pytest.mark.parametrize('device,author,local',[(D,None,True),(OTHER,None,False),(None,D,True),(None,OTHER,False),(None,None,False)])
@pytest.mark.parametrize('summary',[False,True])
def test_p7_local_evidence_ownership(tmp_path,monkeypatch,device,author,local,summary):
    db,eid=staging(tmp_path);value=config(tmp_path);save(db,value)
    ref=dict(table='memory_summaries' if summary else 'memory_items',id=1)
    if device: ref['device']=device
    result=row(ref=ref,author=author,summary=summary);result['evidence_event_ids']=[eid]
    monkeypatch.setattr(bridge,'broker_recall',lambda *a:answer([result]))
    if not local: monkeypatch.setattr(recall,'open_readonly',lambda *a:pytest.fail('foreign source opened SQLite'))
    output=recall.recall(db,'synthetic memory')
    assert bool(output['evidence']) is local
    assert output['results'][0]['id']=='one'


def test_p7_profiles_parallel_interleave_and_pool(tmp_path,monkeypatch):
    barrier=threading.Barrier(2);calls=[]
    rows={'work':[row('w1'),row('shared'),row('w3')],
        'personal':[row('p1','personal'),row('shared','personal'),row('p3','personal')]}
    def run(argv,**kwargs):
        calls.append(argv);barrier.wait(timeout=1)
        profile=argv[argv.index('--profile')+1]
        return SimpleNamespace(returncode=0,stdout=json.dumps(answer(rows[profile])))
    monkeypatch.setattr(subprocess,'run',run)
    result=bridge.broker_recall(recall.analyze_query('synthetic'),4,config(tmp_path))
    assert [r['id'] for r in result['results']]==['w1','p1','shared','w3']
    assert [r['profile'] for r in result['results']]==['work','personal','work','work']
    assert result['candidate_counts']['durable']==6
    assert len(calls)==2 and all(a.count('--profile')==1 for a in calls)
    assert not any('cross-profile' in arg or 'presence' in arg for a in calls for arg in a)


def test_p7_profiles_failure_no_partial(tmp_path,monkeypatch):
    def run(argv,**kwargs):
        profile=argv[argv.index('--profile')+1]
        return SimpleNamespace(returncode=int(profile=='personal'),stdout=json.dumps(answer([row()])))
    monkeypatch.setattr(subprocess,'run',run)
    with pytest.raises(bridge.BrokerUnavailable): bridge.broker_recall(recall.analyze_query('synthetic'),40,config(tmp_path))


@pytest.mark.parametrize('legacy',[False,True])
def test_p7_single_profile_compatibility(tmp_path,monkeypatch,legacy):
    db=tmp_path/'staging.db';value=config(tmp_path,('work',))
    if legacy:
        value['profile']=value.pop('profiles')[0]
        for key in bridge.EXTENDED: value.pop(key)
    save(db,value);loaded=bridge.load_config(db);assert loaded==value
    calls=[]
    def run(argv,**kwargs):
        calls.append(argv);return SimpleNamespace(returncode=0,stdout=json.dumps(answer([row()])))
    monkeypatch.setattr(subprocess,'run',run)
    assert bridge.broker_recall(recall.analyze_query('synthetic'),40,loaded)['results'][0]['profile']=='work'
    assert len(calls)==1 and 'personal' not in calls[0]


def test_p7_profiles_total_timeout(tmp_path):
    value=config(tmp_path);value['timeout_seconds']=0.3
    (tmp_path/'stub.py').write_text('import time\ntime.sleep(1)\n',encoding='utf-8')
    start=time.monotonic()
    with pytest.raises(bridge.BrokerUnavailable): bridge.broker_recall(recall.analyze_query('synthetic'),40,value)
    assert time.monotonic()-start<0.55


@pytest.mark.parametrize('change',['empty','duplicate','invalid','both','extra','missing_binding','bad_device'])
def test_p7_strict_config(tmp_path,change):
    db=tmp_path/'staging.db';value=config(tmp_path)
    if change=='empty': value['profiles']=[]
    elif change=='duplicate': value['profiles']=['work','work']
    elif change=='invalid': value['profiles']=['../personal']
    elif change=='both': value['profile']='work'
    elif change=='extra': value['extra']=True
    elif change=='missing_binding': del value['device_id']
    else: value['device_id']='invalid'
    save(db,value);assert bridge.load_config(db) is None


def test_p7_unbound_legacy_has_no_local_evidence(tmp_path,monkeypatch):
    db,eid=staging(tmp_path);value=config(tmp_path,('work',));value['profile']=value.pop('profiles')[0]
    for key in bridge.EXTENDED: value.pop(key)
    save(db,value);result=row(ref=dict(table='memory_items',id=1),author=D)
    monkeypatch.setattr(bridge,'broker_recall',lambda *a:answer([result]))
    monkeypatch.setattr(recall,'open_readonly',lambda *a:pytest.fail('unbound SQLite lookup'))
    assert recall.recall(db,'synthetic memory')['evidence']==[]


@pytest.mark.parametrize('source_device',[D,OTHER,None])
def test_p7_source_ref_device_validation(tmp_path,monkeypatch,source_device):
    ref=dict(table='memory_items',id=1)
    if source_device: ref['device']=source_device
    result=row(ref=ref,author=D)
    monkeypatch.setattr(subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0,stdout=json.dumps(answer([result]))))
    output=bridge.broker_recall(recall.analyze_query('synthetic'),40,config(tmp_path,('work',)))
    assert output['results'][0]['source_ref']==ref


def test_p7_wrong_profile_reply_fails(tmp_path,monkeypatch):
    monkeypatch.setattr(subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0,stdout=json.dumps(answer([row(profile='personal')]))))
    with pytest.raises(bridge.BrokerUnavailable): bridge.broker_recall(recall.analyze_query('synthetic'),40,config(tmp_path,('work',)))


@pytest.mark.parametrize('failure',['extra','invalid_json','partial_binding'])
def test_p7_invalid_config_fails_closed(tmp_path,monkeypatch,capsys,failure):
    db,_=staging(tmp_path);value=config(tmp_path)
    if failure=='extra': value['extra']=True
    elif failure=='partial_binding': del value['device_id']
    save(db,value)
    if failure=='invalid_json': db.with_name(db.name+'.profile-log.json').write_text('{',encoding='utf-8')
    monkeypatch.setattr(recall,'open_readonly',lambda *a:pytest.fail('malformed bridge downgraded to SQLite'))
    assert recall.main([str(db),'synthetic memory'])==1
    assert json.loads(capsys.readouterr().out)=={'error':'profile_log_unavailable'}
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**kw:pytest.fail('malformed bridge started import'))
    bridge.notify_rows(db,[('memory_items',1)])


def test_p7_explicit_sqlite_cannot_bypass_broker(tmp_path,monkeypatch,capsys):
    db,_=staging(tmp_path);save(db,config(tmp_path))
    monkeypatch.setattr(bridge,'broker_recall',lambda *a:pytest.fail('explicit SQLite silently queried broker'))
    monkeypatch.setattr(recall,'open_readonly',lambda *a:pytest.fail('explicit SQLite bypassed gate'))
    assert recall.recall(db,'synthetic memory',backend='sqlite')=={'error':'sqlite_recall_disabled'}
    assert recall.main([str(db),'synthetic memory','--backend','sqlite'])==1
    assert json.loads(capsys.readouterr().out)=={'error':'sqlite_recall_disabled'}


def test_p7_explicit_sqlite_requires_fallback(tmp_path,monkeypatch):
    db,_=staging(tmp_path);value=config(tmp_path);value['recall_fallback']=True;save(db,value)
    monkeypatch.setattr(bridge,'broker_recall',lambda *a:pytest.fail('explicit permitted fallback queried broker'))
    result=recall.recall(db,'synthetic memory',backend='sqlite')
    assert result['backend']=='sqlite-fallback' and 'SQLITE ONLY' in json.dumps(result)


def test_p7_ordinary_row_null_author(tmp_path,monkeypatch):
    result=row();result['author_device']=None
    monkeypatch.setattr(subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0,stdout=json.dumps(answer([result]))))
    monkeypatch.setattr(recall,'open_readonly',lambda *a:pytest.fail('ordinary broker row opened SQLite'))
    db=tmp_path/'staging.db';save(db,config(tmp_path,('work',)))
    output=recall.recall(db,'synthetic memory')
    assert output['backend']=='broker' and output['results'][0]['id']=='one'
    assert output['evidence']==[]


def test_p7_context_preserves_log_ids_and_profiles(tmp_path,monkeypatch):
    import shared_memory_context
    db=tmp_path/'staging.db';save(db,config(tmp_path))
    refs=[row('01ARZ3NDEKTSV4RRFFQ69G5FAV',ref=dict(device=D,table='memory_items',id=1)),
        row('01ARZ3NDEKTSV4RRFFQ69G5FAW','personal',ref=dict(device=OTHER,table='memory_items',id=1)),
        row('01ARZ3NDEKTSV4RRFFQ69G5FAX','personal',summary=True)]
    monkeypatch.setattr(bridge,'broker_recall',lambda *a:answer(refs))
    output=shared_memory_context.context(db,query='synthetic')
    assert [r['id'] for r in output['items']]==[r['id'] for r in refs[:2]]
    assert output['summaries'][0]['id']==refs[2]['id']
    assert all(r['id'] in output['markdown'] for r in refs)
    assert 'personal' in output['markdown'] and output['recent_unsummarized']==[]


def test_p7_agentmesh_preserves_log_ids(tmp_path,monkeypatch,capsys):
    import agentmesh
    db=tmp_path/'staging.db';save(db,config(tmp_path))
    result=row('01ARZ3NDEKTSV4RRFFQ69G5FAV')
    monkeypatch.setattr(bridge,'broker_recall',lambda *a:answer([result]))
    assert agentmesh.main(['--database',str(db),'recall','synthetic'])==0
    output=json.loads(capsys.readouterr().out)
    assert output['results'][0]['id']==result['id'] and output['backend']=='broker'
