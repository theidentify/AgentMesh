import json
import sqlite3
import shutil
import tempfile
from pathlib import Path
import pytest
import memory_sync as sync
import sqlite_memory as backend

GROUP = '00000000-0000-4000-8000-000000000001'

@pytest.fixture
def peers():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        a, b, ex = root/'a.db', root/'b.db', root/'exchange'
        backend.init_database(a)
        with sqlite3.connect(a) as c:
            c.execute("INSERT INTO memory_items(id,kind,scope,content,metadata) VALUES(1,'fact','global','baseline','{\"n\":1}')")
        with sqlite3.connect(a) as source, sqlite3.connect(b) as target:
            source.backup(target)
        yield a,b,ex

def test_linux_peer_uses_distinct_ids_and_exchanges_with_both_peers(peers):
    a, b, exchange = peers
    linux = a.with_name('linux.db')
    with sqlite3.connect(a) as source, sqlite3.connect(linux) as target:
        source.backup(target)
    sync.initialize(a, 'mac', GROUP)
    sync.initialize(b, 'windows', GROUP)
    sync.initialize(linux, 'linux', GROUP)
    with sqlite3.connect(linux) as c:
        c.execute('BEGIN IMMEDIATE')
        ident = sync.allocate_id(c, 'memory_items')
        assert 3 * 2**40 <= ident < 4 * 2**40
        c.execute("INSERT INTO memory_items(id,kind,scope,content) VALUES(?,'fact','global','linux')", (ident,))
    sync.cycle(linux, exchange)
    assert sync.cycle(a, exchange)['receive']['applied'] == 1
    assert sync.cycle(b, exchange)['receive']['applied'] == 1
    for db in [a, b, linux]:
        with sqlite3.connect(db) as c:
            assert c.execute('SELECT content FROM memory_items WHERE id=?', (ident,)).fetchone()[0] == 'linux'
    with sqlite3.connect(a) as c:
        c.execute("UPDATE memory_items SET content='from mac' WHERE id=?", (ident,))
    sync.cycle(a, exchange)
    assert sync.cycle(linux, exchange)['receive']['applied'] == 1


def test_initialize_idempotence_and_identity(peers):
    a,b,_ = peers
    assert sync.initialize(a,'mac',GROUP)['node'] == 'mac'
    assert sync.initialize(a,'mac',GROUP)['node'] == 'mac'
    sync.initialize(b,'windows',GROUP)
    with pytest.raises(ValueError): sync.initialize(a,'windows',GROUP)
    with pytest.raises(ValueError): sync.initialize(a,'mac','00000000-0000-0000-0000-000000000000')
    assert sync.status(a)['outbox'] == 0
    assert sync.capture(a)['changes'] == 0

def test_offline_publish_receive_typed_metadata_no_echo(peers):
    a,b,ex=peers
    sync.initialize(a,'mac',GROUP); sync.initialize(b,'windows',GROUP)
    with sqlite3.connect(a) as c:
        c.execute("UPDATE memory_items SET content='changed',metadata=? WHERE id=1", (json.dumps({'array':[1,True,None], 'object':{'x':2}}),))
    assert sync.capture(a)['changes']==1
    assert sync.status(a)['outbox']==1
    assert sync.publish(a,ex)['published']==1
    assert sync.receive(b,ex)['applied']==1
    with sqlite3.connect(b) as c:
        r=c.execute('SELECT content,metadata FROM memory_items WHERE id=1').fetchone()
        assert r[0]=='changed' and json.loads(r[1])['array']==[1,True,None]
    assert sync.capture(b)['changes']==0
    assert sync.receive(b,ex)['applied']==0
    assert len(list(ex.rglob('*.json')))==1
    with sqlite3.connect(b) as c: c.execute("UPDATE memory_items SET content='back' WHERE id=1")
    sync.cycle(b,ex)
    assert sync.cycle(a,ex)['receive']['applied']==1


def test_allocations_survive_high_peer_ids_and_guard_defaults(peers):
    a,b,ex=peers
    sync.initialize(a,'mac',GROUP); sync.initialize(b,'windows',GROUP)
    with sqlite3.connect(a) as c:
        c.execute('BEGIN IMMEDIATE')
        mid=sync.allocate_id(c,'memory_items')
        assert mid==2*2**40
        c.execute("INSERT INTO memory_items(id,kind,scope,content) VALUES(?,'fact','global','mac')",(mid,))
    sync.cycle(a,ex); sync.receive(b,ex)
    with sqlite3.connect(b) as c:
        c.execute('BEGIN IMMEDIATE')
        wid=sync.allocate_id(c,'memory_items')
        assert wid==2**40
        c.execute("INSERT INTO memory_items(id,kind,scope,content) VALUES(?,'fact','global','win')",(wid,))
    sync.cycle(b,ex); sync.receive(a,ex)
    with sqlite3.connect(b) as c:
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO memory_items(kind,scope,content) VALUES('fact','global','default forbidden')")
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO memory_items(id,kind,scope,content) VALUES(10,'fact','global','low forbidden')")
        c.execute('BEGIN IMMEDIATE') if not c.in_transaction else None
        assert sync.allocate_id(c,'memory_items')==wid+1
    with sqlite3.connect(a) as c:
        c.execute('BEGIN IMMEDIATE')
        assert sync.allocate_id(c,'memory_items')==mid+1
        c.execute("INSERT INTO memory_items(id,kind,scope,content) VALUES(10,'fact','global','pg mirror')")
        with pytest.raises(sqlite3.IntegrityError): c.execute('UPDATE memory_items SET id=11 WHERE id=10')
    with sqlite3.connect(a) as c:
        with pytest.raises(ValueError): sync.allocate_id(c,'memory_sources')
        with pytest.raises(ValueError): sync.allocate_id(c,'memory_items')


def insert_family(db, suffix):
    with sqlite3.connect(db) as c:
        c.execute('PRAGMA foreign_keys=ON'); c.execute('BEGIN IMMEDIATE')
        ids={t:sync.allocate_id(c,t) for t in sync.TABLES if sync.KEYS[t]==('id',)}
        c.execute("INSERT INTO source_sessions(id,source_path) VALUES(?,?)",(ids['source_sessions'],suffix))
        c.execute("INSERT INTO observation_events(id,event_key,source_path,kind,content,source_hash) VALUES(?,?,?,'message','event','hash')",(ids['observation_events'],suffix,suffix))
        c.execute("INSERT INTO ingestion_errors(id,source_path,line_hash,error_type,error_message) VALUES(?,?,?,'parse','error')",(ids['ingestion_errors'],suffix,suffix))
        c.execute("INSERT INTO memory_summaries(id,scope,scope_key,content) VALUES(?,'session',?,'summary')",(ids['memory_summaries'],suffix))
        c.execute("INSERT INTO memory_items(id,kind,scope,content,supersedes_id) VALUES(?,'fact','global',?,1)",(ids['memory_items'],suffix))
        c.execute('INSERT INTO memory_sources VALUES(?,?)',(ids['memory_items'],ids['observation_events']))
        c.execute('INSERT INTO ingestion_cursors(source_path) VALUES(?)',(suffix,))
        c.execute('INSERT INTO summary_state(consumer) VALUES(?)',(suffix,))
    return ids

def test_all_eight_tables_two_way_insert_update_delete_cascade(peers):
    a,b,ex=peers
    sync.initialize(a,'mac',GROUP); sync.initialize(b,'windows',GROUP)
    for source,target,suffix in ((a,b,'mac-family'),(b,a,'win-family')):
        ids=insert_family(source,suffix)
        assert sync.cycle(source,ex)['capture']['changes']==8
        assert sync.receive(target,ex)['applied']==1
        with sync.connect(source) as c, sync.connect(target) as d:
            assert sync.rows(c)==sync.rows(d)
        with sqlite3.connect(source) as c:
            c.execute('PRAGMA foreign_keys=ON')
            for t in sync.TABLES:
                if t=='memory_sources': continue
                key=ids[t] if t in ids else suffix
                field=sync.KEYS[t][0]
                update='byte_offset=42' if t=='ingestion_cursors' else 'last_event_id=42' if t=='summary_state' else "error_message='updated'" if t=='ingestion_errors' else "workspace='updated'" if t=='source_sessions' else "content='updated'"
                c.execute('UPDATE '+t+' SET '+update+' WHERE '+field+'=?',(key,))
        sync.cycle(source,ex); assert sync.receive(target,ex)['applied']==1
        with sqlite3.connect(source) as c:
            c.execute('PRAGMA foreign_keys=ON')
            c.execute('DELETE FROM memory_items WHERE id=?',(ids['memory_items'],))
            for t in sync.TABLES:
                if t in ('memory_items','memory_sources'): continue
                key=ids[t] if t in ids else suffix
                c.execute('DELETE FROM '+t+' WHERE '+sync.KEYS[t][0]+'=?',(key,))
        sync.cycle(source,ex); assert sync.receive(target,ex)['applied']==1
        assert sync.capture(target)['changes']==0
        with sync.connect(source) as c, sync.connect(target) as d:
            assert sync.rows(c)==sync.rows(d)
            assert not c.execute('PRAGMA foreign_key_check').fetchall()
            assert c.execute('SELECT count(*) FROM _sync_shadow WHERE row IS NULL').fetchone()[0]>=8

def test_concurrent_child_not_silently_cascaded(peers):
    a,b,ex=peers
    sync.initialize(a,'mac',GROUP); sync.initialize(b,'windows',GROUP)
    ids=insert_family(b,'win')
    with sqlite3.connect(b) as c:
        c.execute('UPDATE memory_items SET supersedes_id=NULL WHERE id=?',(ids['memory_items'],))
        c.execute('INSERT INTO memory_sources VALUES(?,?)',(1,ids['observation_events']))
    with sqlite3.connect(a) as c:
        c.execute('PRAGMA foreign_keys=ON'); c.execute('DELETE FROM memory_items WHERE id=1')
    sync.cycle(a,ex)
    assert sync.receive(b,ex)['conflict']==1
    with sqlite3.connect(b) as c:
        assert c.execute('SELECT count(*) FROM memory_items WHERE id=1').fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM _sync_receipts').fetchone()[0]==0


def test_cli_init_once_status_and_watch_unavailable(peers):
    import subprocess
    import sys
    a,b,ex=peers
    script=str(Path(sync.__file__))
    def cli(*args):
        r=subprocess.run([sys.executable,script,*map(str,args)],capture_output=True,text=True)
        assert r.returncode==0,r.stderr
        return json.loads(r.stdout)
    assert cli('init',a,'--node','mac','--group',GROUP)['node']=='mac'
    assert cli('once',a,ex)['capture']['changes']==0
    assert cli('status',a)['received']==0
    # Real watch runs two iterations, retries unavailable path, exits on SIGINT.
    obstacle=ex/'blocked'; obstacle.write_text('not a directory')
    process=subprocess.Popen([sys.executable,script,'watch',str(a),str(obstacle),'--interval','0.05'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    import signal
    import time
    time.sleep(1); process.send_signal(signal.SIGINT)
    stdout,stderr=process.communicate(timeout=5)
    assert process.returncode==0,stderr
    reports=[json.loads(line) for line in stdout.splitlines()]
    assert len(reports)>=2 and all(r['publish']['unavailable'] for r in reports)


def prepare_packet(peers):
    a,b,ex=peers
    sync.initialize(a,'mac',GROUP); sync.initialize(b,'windows',GROUP)
    with sqlite3.connect(a) as c: c.execute("UPDATE memory_items SET content='incoming' WHERE id=1")
    sync.capture(a); sync.publish(a,ex)
    path=next(ex.rglob('*.json'))
    return a,b,ex,path,json.loads(path.read_text())

def test_reversed_chain_pending_then_retry_and_duplicate_files(peers):
    a,b,ex,path,p=prepare_packet(peers)
    first=path.read_bytes(); path.unlink() # Test transport hides predecessor only.
    with sqlite3.connect(a) as c: c.execute("UPDATE memory_items SET content='second' WHERE id=1")
    sync.capture(a); sync.publish(a,ex)
    second=next(ex.rglob('*.json'))
    assert sync.receive(b,ex)['pending']==1
    (path.parent/'z-first.json').write_bytes(first)
    (path.parent/'0-second.json').write_bytes(second.read_bytes())
    report=sync.receive(b,ex)
    assert report['applied']==2 and report['pending']==0
    assert sync.receive(b,ex)['applied']==0
    with sqlite3.connect(b) as c:
        assert c.execute('SELECT content FROM memory_items WHERE id=1').fetchone()[0]=='second'
        assert c.execute('SELECT count(*) FROM _sync_receipts').fetchone()[0]==2
    assert sync.capture(b)['changes']==0

def test_publication_recovers_commit_and_rename_crashes(peers,monkeypatch):
    a,b,ex=peers
    sync.initialize(a,'mac',GROUP); sync.initialize(b,'windows',GROUP)
    with sqlite3.connect(a) as c: c.execute("UPDATE memory_items SET content='durable' WHERE id=1")
    sync.capture(a)
    original=sync.os.replace
    def broken(*args): raise OSError('offline')
    monkeypatch.setattr(sync.os,'replace',broken)
    assert sync.publish(a,ex)['unavailable']
    assert sync.status(a)['outbox']==1 and not list(ex.rglob('*.json'))
    monkeypatch.setattr(sync.os,'replace',original)
    assert sync.publish(a,ex)['published']==1
    # Simulate crash after fsynced rename, before published flag transaction.
    with sqlite3.connect(a) as c: c.execute('UPDATE _sync_outbox SET published=0')
    assert sync.publish(a,ex)['published']==1
    assert len(list(ex.rglob('*.json')))==1
    assert sync.receive(b,ex)['applied']==1

def test_concurrent_edits_rollback_whole_packet_and_continue(peers):
    a,b,ex,path,p=prepare_packet(peers)
    with sqlite3.connect(b) as c: c.execute("UPDATE memory_items SET content='local offline' WHERE id=1")
    with sqlite3.connect(a) as c: c.execute("INSERT INTO summary_state(consumer) VALUES('unrelated')")
    sync.capture(a); sync.publish(a,ex)
    report=sync.receive(b,ex)
    assert report['conflict']==1 and report['applied']==1
    assert sync.status(b)['outbox']==1 # capture before receive kept offline edit
    with sqlite3.connect(b) as c:
        assert c.execute('SELECT content FROM memory_items WHERE id=1').fetchone()[0]=='local offline'
        assert c.execute('SELECT count(*) FROM summary_state').fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM _sync_receipts').fetchone()[0]==1
    assert sync.capture(b)['changes']==0

@pytest.mark.parametrize('damage',['checksum','foreign','extra','key','columns','uuid-type','revision-type','parent-type','metadata-nan','int-type','new-peer-range','duplicate-json'])
def test_invalid_packets_quarantine_without_stopping_good_packet(peers,damage):
    a,b,ex,path,p=prepare_packet(peers)
    if damage=='checksum': p['checksum']='0'*64
    elif damage=='foreign': p['group']='00000000-0000-0000-0000-000000000000'
    elif damage=='extra': p['extra']=True
    elif damage=='key': p['body'][0]['key']=[2]
    elif damage=='columns': p['body'][0]['row']['injected']='evil'
    elif damage=='uuid-type': p['uuid']=123
    elif damage=='revision-type': p['body'][0]['revision']=123
    elif damage=='parent-type': p['body'][0]['parent']={}
    elif damage=='metadata-nan': p['body'][0]['row']['metadata']=float('nan')
    elif damage=='int-type': p['body'][0]['row']['id']=True
    elif damage=='new-peer-range':
        p['node']='windows'; p['body'][0]['parent']=None
        p['body'][0]['key']=[2*2**40]; p['body'][0]['row']['id']=2*2**40
    if damage!='checksum' and damage!='metadata-nan': p['checksum']=sync.digest(p['body'])
    text=json.dumps(p)
    if damage=='duplicate-json': text=text.replace('"format":', '"format":"bad", "format":',1)
    path.write_text(text)
    with sqlite3.connect(a) as c: c.execute("INSERT INTO summary_state(consumer) VALUES('ok')")
    sync.capture(a); sync.publish(a,ex)
    report=sync.receive(b,ex)
    assert report['invalid']==1 and report['applied']==1
    with sqlite3.connect(b) as c:
        assert c.execute('SELECT content FROM memory_items WHERE id=1').fetchone()[0]=='baseline'
        assert c.execute('SELECT count(*) FROM _sync_receipts').fetchone()[0]==1


def test_unique_conflict_is_atomic_and_not_received(peers):
    a,b,ex=peers
    sync.initialize(a,'mac',GROUP);sync.initialize(b,'windows',GROUP)
    insert_family(a,'same-natural-key');insert_family(b,'same-natural-key')
    sync.capture(a);sync.publish(a,ex)
    with sync.connect(b) as c: before=sync.rows(c)
    report=sync.receive(b,ex)
    assert report['conflict']==1 and report['applied']==0
    with sync.connect(b) as c:
        assert sync.rows(c)==before
        assert c.execute('SELECT count(*) FROM _sync_receipts').fetchone()[0]==0
        assert c.execute('SELECT importing FROM _sync_config').fetchone()[0]==0


def test_replays_do_not_rescan_full_memory_per_retained_file(peers,monkeypatch):
    a,b,ex,path,p=prepare_packet(peers)
    assert sync.receive(b,ex)['applied']==1
    for n in range(15): (path.parent/('copy%d.json'%n)).write_bytes(path.read_bytes())
    calls=[]; original=sync.rows
    def counted(c): calls.append(1); return original(c)
    monkeypatch.setattr(sync,'rows',counted)
    assert sync.receive(b,ex)['applied']==0
    assert len(calls)<=2


def test_sender_range_checked_even_for_new_packet(peers):
    a,b,ex,path,p=prepare_packet(peers)
    p['body'][0]['parent']=None
    p['body'][0]['key']=[2**40];p['body'][0]['row']['id']=2**40
    p['checksum']=sync.digest(p['body']);path.write_text(json.dumps(p))
    assert sync.receive(b,ex)['invalid']==1
    with sqlite3.connect(b) as c: assert c.execute('SELECT count(*) FROM memory_items').fetchone()[0]==1

def test_partial_packet_then_complete_retries_without_data_loss(peers):
    a,b,ex,path,p=prepare_packet(peers)
    text=path.read_text();path.write_text(text[:40])
    assert sync.receive(b,ex)['invalid']==1
    assert sync.status(b)['received']==0
    path.write_text(text)
    r=sync.receive(b,ex)
    assert r['applied']==1 and r['invalid']==0

def test_mismatched_baseline_is_conflict_not_unresolvable_pending(peers):
    a,b,ex=peers
    with sqlite3.connect(b) as c: c.execute("UPDATE memory_items SET content='different bootstrap' WHERE id=1")
    sync.initialize(a,'mac',GROUP);sync.initialize(b,'windows',GROUP)
    with sqlite3.connect(a) as c: c.execute("UPDATE memory_items SET content='new' WHERE id=1")
    sync.capture(a);sync.publish(a,ex)
    r=sync.receive(b,ex)
    assert r['conflict']==1 and r['pending']==0


def test_foreign_key_dependency_retries_in_same_receive_pass(peers):
    a,b,ex=peers
    sync.initialize(a,'mac',GROUP);sync.initialize(b,'windows',GROUP)
    with sqlite3.connect(a) as c:
        c.execute('BEGIN IMMEDIATE');eid=sync.allocate_id(c,'observation_events')
        c.execute("INSERT INTO observation_events(id,event_key,source_path,kind,content,source_hash) VALUES(?,'dependency','path','message','event','hash')",(eid,))
    sync.capture(a);sync.publish(a,ex)
    first=next(ex.rglob('*.json'));data=first.read_bytes();first.unlink()
    with sqlite3.connect(a) as c: c.execute('INSERT INTO memory_sources VALUES(?,?)',(1,eid))
    sync.capture(a);sync.publish(a,ex)
    assert sync.receive(b,ex)['pending']==1
    (first.parent/'z-event.json').write_bytes(data)
    report=sync.receive(b,ex)
    assert report['applied']==2 and report['pending']==0 and report['conflict']==0
    with sqlite3.connect(b) as c:
        assert c.execute('SELECT count(*) FROM memory_sources').fetchone()[0]==1
        assert not c.execute('PRAGMA foreign_key_check').fetchall()


def test_missing_database_not_created_and_bad_init_is_atomic(peers):
    a,b,ex=peers
    missing=a.parent/'missing.db'
    with pytest.raises((ValueError,sqlite3.OperationalError)): sync.initialize(missing,'mac',GROUP)
    assert not missing.exists()
    with sqlite3.connect(a) as c: c.execute('UPDATE memory_items SET id=? WHERE id=1',(4*2**40,))
    with pytest.raises(ValueError): sync.initialize(a,'mac',GROUP)
    with sqlite3.connect(a) as c:
        assert not c.execute("SELECT name FROM sqlite_master WHERE name LIKE '_sync_%'").fetchall()

def test_atomic_multichange_conflict_and_self_reference_delete(peers):
    a,b,ex=peers
    sync.initialize(a,'mac',GROUP);sync.initialize(b,'windows',GROUP)
    ids=insert_family(a,'family')
    sync.cycle(a,ex);sync.receive(b,ex)
    with sqlite3.connect(a) as c:
        c.execute("UPDATE memory_items SET content='remote' WHERE id=1")
        c.execute("UPDATE source_sessions SET workspace='remote' WHERE id=?",(ids['source_sessions'],))
    with sqlite3.connect(b) as c: c.execute("UPDATE memory_items SET content='local' WHERE id=1")
    sync.capture(a);sync.publish(a,ex)
    assert sync.receive(b,ex)['conflict']==1
    with sqlite3.connect(b) as c:
        assert c.execute('SELECT workspace FROM source_sessions').fetchone()[0] is None
    # Self-references: child, parent and their link can all disappear atomically.
    with sqlite3.connect(a) as c:
        c.execute('PRAGMA foreign_keys=ON');c.execute('BEGIN IMMEDIATE')
        c.execute('DELETE FROM memory_items WHERE id=1')
        c.execute('DELETE FROM memory_items WHERE id=?',(ids['memory_items'],))
    assert sync.capture(a)['changes']==3
