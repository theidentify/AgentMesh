"""Parity uses generated rows and disposable identities only."""
import json
import os
from pathlib import Path
import sqlite3
import pytest
pytest.importorskip('pyrage')
import profile_log_parity as parity


@pytest.fixture
def corpus(tmp_path):
    db=tmp_path/'synthetic.db'
    c=sqlite3.connect(db)
    c.executescript(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))
    for i in range(300):
        project='SyntheticProject'+str(i%5)
        scope='global' if i%11==0 else ('task' if i%7==0 else 'project')
        key='SYN-'+str(i%10) if scope=='task' else project
        c.execute('INSERT INTO memory_items(id,kind,scope,scope_key,project,content,updated_at) VALUES(?,?,?,?,?,?,?)',
            (i+1,'fact',scope,key,project,'synthetic portability marker'+str(i)+' ฐานข้อมูล ทดสอบ','2020-01-01'))
    for i in range(30):
        scope='project' if i<5 else 'task'; key='SyntheticProject'+str(i) if i<5 else 'SYN-'+str(i-5)
        c.execute('INSERT INTO memory_summaries(scope,scope_key,content,created_at) VALUES(?,?,?,?)',(scope,key,'unrelated summary ทดสอบ','2020-01-01'))
    c.commit(); c.close()
    mapping=tmp_path/'mapping.json'
    mapping.write_text(json.dumps(dict(format='agentmesh-migration-mapping-v1',null_project='work/common',
        prefixes=[dict(prefix='SyntheticProject',target='work/common')],projects={},**{'global':'work/common'})),encoding='utf-8')
    return db,mapping


def test_p5_6_parity(corpus,tmp_path,capsys):
    db,mapping=corpus
    result=parity.run(db,mapping,sample=200,out=tmp_path/'report.json')
    report=(tmp_path/'report.json').read_bytes()
    assert all(marker not in report for marker in (b'synthetic portability',b'SyntheticProject',b'marker'))
    print('overlap_at_12='+str(result['overlap_at_12'])+' top5_hit='+str(result['top5_hit']))
    assert result['overlap_at_12']>=.95 and result['top5_hit']>=.98


def test_snapshot_dir_mode(corpus):
    def inspect(root,snapshot,broker):
        if os.name!='nt': assert root.stat().st_mode&0o777==0o700
    parity.run(*corpus,sample=0,inspect=inspect)


def test_snapshot_file_modes(corpus):
    def inspect(root,snapshot,broker):
        if os.name!='nt':
            assert all(p.stat().st_mode&0o777==0o600 for p in root.rglob('*') if p.is_file())
    parity.run(*corpus,sample=0,inspect=inspect)


def test_snapshot_plaintext(corpus):
    def inspect(root,snapshot,broker):
        allowed={snapshot,*(Path(str(snapshot)+suffix) for suffix in ('-wal','-shm','-journal'))}
        bearing=set()
        for path in root.rglob('*'):
            if path.is_file():
                data=path.read_bytes()
                if any('synthetic portability'.encode(codec) in data for codec in ('utf-8','utf-16-le','utf-16-be')):
                    bearing.add(path)
        assert snapshot in bearing and bearing<=allowed
    parity.run(*corpus,sample=0,inspect=inspect)


def test_snapshot_success(corpus):
    visited=[]
    parity.run(*corpus,sample=0,inspect=lambda root,*_:visited.append(root))
    assert visited and all(not p.exists() for p in visited)


def test_snapshot_error(corpus):
    visited=[]
    def inspect(root,*_):
        visited.append(root)
        raise RuntimeError('injected')
    with pytest.raises(RuntimeError): parity.run(*corpus,sample=0,inspect=inspect)
    assert visited and all(not p.exists() for p in visited)


def test_parity_no_remote(corpus,monkeypatch):
    from agentmesh_log.broker.replica import Replica
    from agentmesh_log.storage.s3 import S3Storage
    def forbidden(*args,**kwargs): pytest.fail('remote operation attempted')
    monkeypatch.setattr(Replica,'sync_once',forbidden)
    monkeypatch.setattr(S3Storage,'__init__',forbidden)
    from agentmesh_log.storage.filesystem import FilesystemStorage
    original=FilesystemStorage.__init__
    def local_only(self,path,*args,**kwargs):
        assert Path(path).name=='replica' and Path(path).parent.name=='broker'
        original(self,path,*args,**kwargs)
    monkeypatch.setattr(FilesystemStorage,'__init__',local_only)
    def inspect(root,snapshot,broker):
        assert broker.replica.outbox()==len(broker.replica.keys())
        assert len(broker.imports.entries)==330
    parity.run(*corpus,sample=0,inspect=inspect)
