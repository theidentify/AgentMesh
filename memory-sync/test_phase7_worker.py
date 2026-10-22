"""Retired packets preserve local workflow and cooperative worker ownership."""
import json
import sqlite3
import uuid
import pytest
import sync_worker
import worker_lifecycle as worker
from test_phase7_ingest import fixture, write
from test_worker_lifecycle import bound, NATIVE_TIMEOUT


def forbidden(*args,**kwargs):
    raise AssertionError('packet work entered')


def test_retired_ingests_without_exchange(tmp_path,monkeypatch):
    database,root=fixture(tmp_path)
    write(root/'new.jsonl','fresh','2026-10-22T00:00:00Z')
    config=tmp_path/'workflow.json'
    config.write_text(json.dumps(dict(ingest=True,summarize=True,roots={'omp':str(root)})),encoding='utf-8')
    import workflow
    summaries=[]
    monkeypatch.setattr(workflow,'summarize',lambda *a,**kw:summaries.append(1) or {'status':'committed'})
    monkeypatch.setattr(sync_worker.memory_sync,'cycle',forbidden)
    monkeypatch.setattr(sync_worker.memory_sync,'_security_policy',forbidden)
    exchange=tmp_path/'exchange'
    report=sync_worker.run_once(database,exchange,workflow_config=config,security_dir=tmp_path/'missing-key')
    assert report['packet_sync']=='retired' and not sync_worker.failed(report)
    assert summaries==[1] and report['workflow']['ingestion']['inserted']==1
    assert not {'cycle','sync','security','node','group'} & report.keys()
    assert 'summary_sync' not in report['workflow'] and not exchange.exists()
    with sqlite3.connect(database) as c:
        assert c.execute('SELECT COUNT(*) FROM observation_events').fetchone()[0]==1


@pytest.mark.parametrize('configured',[False,True])
def test_retired_no_work(tmp_path,monkeypatch,configured):
    database=tmp_path/'missing.db'
    if configured:
        (tmp_path/'workflow.json').write_text('{"ingest":false,"summarize":false}',encoding='utf-8')
    monkeypatch.setattr(sync_worker,'_run_once',forbidden)
    assert sync_worker.run_once(database,tmp_path/'exchange')['packet_sync']=='retired'
    assert not database.exists() and not (tmp_path/'worker.lock').exists()


def test_retired_cli_idles(tmp_path,monkeypatch,capsys):
    sleeps=[]
    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps)==3: raise KeyboardInterrupt
    monkeypatch.setattr(sync_worker.time,'sleep',sleep)
    monkeypatch.setattr(sync_worker,'run_once',forbidden)
    assert sync_worker.main([str(tmp_path/'absent.db'),str(tmp_path/'exchange'),'--interval','7'])==0
    assert sleeps==[7,7,7] and len(capsys.readouterr().out.splitlines())==1
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('workflow_on',[False,True])
def test_retired_managed_stop(tmp_path,monkeypatch,capsys,workflow_on):
    args=bound(tmp_path,monkeypatch)
    args['workflow_config'].write_text(json.dumps(dict(ingest=workflow_on,summarize=False)),encoding='utf-8')
    capsys.readouterr()
    nonce=str(uuid.uuid4()); ticks=[]; cycles=[]
    def cycle(*a,**kw):
        cycles.append(1)
        return {'packet_sync':'retired','workflow':{'ingestion':{'status':'completed'}}}
    monkeypatch.setattr(sync_worker,'run_once',cycle if workflow_on else forbidden)
    clock=[0.]
    monkeypatch.setattr(worker.time,'monotonic',lambda:clock[0])
    def sleep(seconds):
        clock[0]+=seconds; ticks.append(seconds)
        if clock[0]>=.3:
            worker.write_local(worker.control({'database':str(args['database'])})/'stop.json',{'nonce':nonce})
    monkeypatch.setattr(worker.time,'sleep',sleep)
    assert worker.run(args['runtime'],interval=.1,nonce=nonce,legacy_drained=True)==0
    report=worker.metadata(worker.control({'database':str(args['database'])}),args['runtime'])
    assert report['state']=='stopped' and ticks
    assert report['cycles']==len(cycles) and (len(cycles)>=2 if workflow_on else not cycles)
    if not workflow_on: assert capsys.readouterr().out.splitlines()==['packet sync retired']
    assert not (args['exchange']/'changes').exists()


def test_retired_managed_once(tmp_path,monkeypatch):
    args=bound(tmp_path,monkeypatch)
    monkeypatch.setattr(worker.time,'sleep',forbidden)
    monkeypatch.setattr(sync_worker,'run_once',forbidden)
    assert worker.run(args['runtime'],once=True)==0
    assert not worker.control({'database':str(args['database'])}).exists()


def test_retired_managed_start_acknowledges_idle(tmp_path,monkeypatch):
    args=bound(tmp_path,monkeypatch)
    try:
        record=worker.start(args['runtime'],interval=.1,legacy_drained=True,timeout=NATIVE_TIMEOUT)
        assert record['idle'] is True and record['cycles']==0
    finally:
        worker.stop(args['runtime'],timeout=NATIVE_TIMEOUT)
