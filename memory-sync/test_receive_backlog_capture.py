"""Disposable backlog reproduction for the receive capture design."""
import uuid

import pytest

import identity_backup
import memory_sync as sync
import resolution_store
import sqlite_memory
from test_memory_sync import GROUP


@pytest.fixture
def backlog(tmp_path):
    db=tmp_path/'receiver.db'
    sqlite_memory.init_database(db)
    with sync.connect(db) as c, c:
        c.executemany('INSERT INTO summary_state VALUES(?,?,?)',
                      [(f'row-{n}',0,'fixture') for n in range(3000)])
    sync.initialize(db,'windows',GROUP)
    directory=tmp_path/'exchange'/'changes'/'mac'
    for path in (directory.parent.parent,directory.parent,directory):
        identity_backup.private_mkdir(path)
    with sync.connect(db) as c:
        parents={r['key']:r['revision'] for r in c.execute(
            "SELECT key,revision FROM _sync_shadow WHERE table_name='summary_state'")}
    packets=[]
    def packet(number, key, value, parent):
        body=[dict(table='summary_state',key=[key],
                   row=dict(consumer=key,last_event_id=value,updated_at='fixture'),
                   revision=str(uuid.UUID(int=1000+number)),parent=parent)]
        p=dict(format=sync.FORMAT,group=GROUP,node='mac',uuid=str(uuid.UUID(int=number)),
               body=body,checksum=sync.digest(body))
        (directory/(p['uuid']+'.json')).write_text(sync.canonical(p),encoding='utf-8')
        packets.append(p)
        return body[0]['revision']
    # Successor sorts before predecessor, forcing a pending retry on both paths.
    parent=packet(2,'row-0',1,parents[sync.canonical(['row-0'])])
    packet(1,'row-0',2,parent)
    for n in range(1,51):
        packet(n+2,f'row-{n}',1,parents[sync.canonical([f'row-{n}'])])
    return db,tmp_path/'exchange',packets


@pytest.mark.parametrize('indexed',[False,True],ids=['legacy','indexed'])
def test_backlog_bounds_full_scans_and_targets_capture(backlog,monkeypatch,indexed):
    db,exchange,packets=backlog
    if indexed:
        with sync.connect(db) as c, c: resolution_store.migrate(c)
    scans=[]; captures=[]; capture_scans=[]; attempts=[]
    original_rows=sync.rows; original_capture=sync._capture; original_apply=sync._apply
    def rows(c):
        scans.append(1)
        return original_rows(c)
    def capture(c, **kwargs):
        captures.append(kwargs.get('keys'))
        before=len(scans)
        try: return original_capture(c,**kwargs)
        finally: capture_scans.append(len(scans)-before)
    def apply(c,p):
        attempts.append(p['uuid'])
        return original_apply(c,p)
    monkeypatch.setattr(sync,'rows',rows)
    monkeypatch.setattr(sync,'_capture',capture)
    monkeypatch.setattr(sync,'_apply',apply)
    report=sync.receive(db,exchange)
    assert report==dict(applied=52,pending=0,conflict=0,invalid=0,unavailable=False)
    successor=str(uuid.UUID(int=1))
    assert attempts.count(successor)==2
    with sync.connect(db) as c:
        assert c.execute("SELECT last_event_id FROM summary_state WHERE consumer='row-0'").fetchone()[0]==2
        assert c.execute('SELECT count(*) FROM _sync_receipts').fetchone()[0]==52
    expected={(ch['table'],sync.canonical(ch['key'])) for p in packets for ch in p['body']}
    observed=set().union(*(keys for keys in captures[1:] if keys is not None))
    assert (len(scans)<=2 and captures[0] is None and
            all(keys is not None for keys in captures[1:]) and observed==expected), {
                'full_rows_calls':len(scans), 'capture_calls':len(captures),
                'capture_scans':sum(capture_scans), 'other_scans':len(scans)-sum(capture_scans),
                'capture_key_sets':captures, 'expected_keys':sorted(expected)}

@pytest.mark.parametrize('indexed',[False,True])
def test_unrelated_edit_waits_for_next_capture(backlog,indexed):
    db,exchange,_=backlog
    with sync.connect(db) as c,c:
        if indexed: resolution_store.migrate(c)
        c.execute("UPDATE summary_state SET last_event_id=9 WHERE consumer='row-2999'")
    assert sync.receive(db,exchange,capture_first=False)['applied']==52
    with sync.connect(db) as c:
        assert c.execute('SELECT count(*) FROM _sync_outbox').fetchone()[0]==0
    assert sync.capture(db)['changes']==1


def test_probe_falls_back_for_entire_local_transaction(backlog):
    db,_,_=backlog
    with sync.connect(db) as c,c:
        c.execute("UPDATE summary_state SET last_event_id=9 WHERE consumer IN ('row-0','row-2999')")
        result=sync._capture(c,keys={('summary_state',sync.canonical(['row-0']))})
        assert result['changes']==2
    with sync.connect(db) as c,c:
        assert sync._capture(c,keys={('summary_state',sync.canonical(['row-0']))})['changes']==0
        with pytest.raises(ValueError): sync._capture(c,keys={('unknown','[]')})


def test_index_local_decodes_once(backlog,monkeypatch):
    import resolution_apply as app
    db,_,_=backlog
    with sync.connect(db) as c,c:
        c.execute("UPDATE summary_state SET last_event_id=9 WHERE consumer='row-0'")
    sync.capture(db)
    calls=[]; original=app._decode
    def decode(*args):
        calls.append(1)
        return original(*args)
    monkeypatch.setattr(app,'_decode',decode)
    with sync.connect(db) as c,c:
        resolution_store.migrate(c)
        app._index_local(c,None)
        app._index_local(c,None)
    assert len(calls)==1


def test_write_preserved_child_and_recursive_setting(tmp_path):
    import resolution_apply as app
    import resolution_protocol as protocol
    db=tmp_path/'tracking.db'; sqlite_memory.init_database(db)
    sync.initialize(db,'mac',GROUP)
    ids=family(db); sync.capture(db)
    with sync.connect(db) as c:
        parent=protocol.Transition(protocol.Key('memory_items',(ids['memory_items'],)),str(uuid.uuid4()),None,'install')
        child_key=protocol.Key('memory_sources',(ids['memory_items'],ids['observation_events']))
        child=protocol.Transition(child_key,str(uuid.uuid4()),None,'preserve')
        c.execute('PRAGMA recursive_triggers=OFF')
        with pytest.raises(protocol.ResolutionConflict,match='relational repair'), c:
            c.execute('BEGIN IMMEDIATE')
            app._write(c,[parent,child])
        assert c.execute('PRAGMA recursive_triggers').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM memory_sources').fetchone()[0]==1


def test_probe_captures_cascade_together(tmp_path):
    db=tmp_path/'cascade.db'; sqlite_memory.init_database(db)
    sync.initialize(db,'mac',GROUP); ids=family(db); sync.capture(db)
    with sync.connect(db) as c,c:
        c.execute('DELETE FROM memory_items WHERE id=?',(ids['memory_items'],))
        assert sync._capture(c,keys={('memory_items',sync.canonical([ids['memory_items']]))})['changes']==2
        body=sync.load_packet(c.execute('SELECT packet FROM _sync_outbox ORDER BY rowid DESC').fetchone()[0])['body']
        assert {ch['table'] for ch in body}=={'memory_items','memory_sources'}


def test_packet_keys_fail_closed():
    for body in (None,{},[],[{'table':'summary_state'}]):
        with pytest.raises(ValueError): sync._packet_keys({'body':body})


def family(db):
    with sync.connect(db) as c,c:
        c.execute("INSERT INTO memory_items(id,kind,scope,content) VALUES(1,'fact','global','fixture')")
        c.execute("INSERT INTO observation_events(id,event_key,source_path,kind,content,source_hash) VALUES(1,'event','fixture','message','fixture','hash')")
        c.execute('INSERT INTO memory_sources VALUES(1,1)')
    return dict(memory_items=1,observation_events=1)


@pytest.mark.parametrize('indexed',[False,True])
def test_receive_interrupt_resumes(backlog,indexed):
    db,exchange,_=backlog
    if indexed:
        with sync.connect(db) as c,c: resolution_store.migrate(c)
    checks=[]
    def interrupt():
        checks.append(1)
        return len(checks)>3
    report=sync.receive(db,exchange,interrupt=interrupt)
    assert report['interrupted'] is True and report['applied']==2
    assert sync.receive(db,exchange)['applied']==50


def test_v2_backlog_bounds_scans_and_local_decodes(backlog,monkeypatch):
    import resolution_apply as app
    import resolution_protocol as protocol
    db,exchange,packets=backlog
    with sync.connect(db) as c,c:
        resolution_store.migrate(c)
        origins={r['key']:r['revision'] for r in c.execute("SELECT key,revision FROM _sync_shadow WHERE table_name='summary_state'")}
    for packet in packets:
        for ch in packet['body']:
            ch['parents']=[ch.pop('parent')]
            ch['origin']=origins[sync.canonical(ch['key'])]
        packet.update(kind='change',covers=[],evidence=[])
        packet['format']='omp-memory-changes-v2'
        packet['checksum']=protocol.payload_digest({k:packet[k] for k in ('group','node','uuid','kind','body','covers','evidence')})
        (exchange/'changes'/'mac'/(packet['uuid']+'.json')).write_text(sync.canonical(packet),encoding='utf-8')
    for n in range(5):
        with sync.connect(db) as c,c:
            c.execute("UPDATE summary_state SET last_event_id=? WHERE consumer='row-2999'",(n+1,))
        sync.capture(db)
    scans=[]; decodes=[]; original_rows=sync.rows; original_decode=app._decode
    def rows(c): scans.append(1); return original_rows(c)
    def decode(c,p,security):
        if p['node']=='windows': decodes.append(p['uuid'])
        return original_decode(c,p,security)
    monkeypatch.setattr(sync,'rows',rows); monkeypatch.setattr(app,'_decode',decode)
    assert sync.receive(db,exchange)['applied']==52
    assert len(scans)<=2 and len(decodes)==5 and len(set(decodes))==5


@pytest.mark.parametrize('mode',['signed','v2'])
def test_targeted_capture_emission_new_and_deleted(tmp_path,monkeypatch,mode):
    import signed_packets as signed
    import packet_versions
    import protocol_activation
    db=tmp_path/'emission.db'; sqlite_memory.init_database(db); sync.initialize(db,'mac',GROUP)
    security=None
    if mode=='signed':
        signed.init_identity(tmp_path/'identity',GROUP,'mac')
        security=signed.Security(tmp_path/'identity')
    else:
        with sync.connect(db) as c,c: resolution_store.migrate(c)
        monkeypatch.setattr(protocol_activation,'emission_enabled',lambda c: True)
    key={('summary_state',sync.canonical(['é']))}
    for delete in (False,True):
        with sync.connect(db) as c,c:
            c.execute('BEGIN IMMEDIATE')
            if delete: c.execute('DELETE FROM summary_state')
            else: c.execute("INSERT INTO summary_state VALUES('é',1,'fixture')")
            assert sync._capture(c,security=security,keys=key)['changes']==1
            packet=sync.load_packet(c.execute('SELECT packet FROM _sync_outbox ORDER BY rowid DESC').fetchone()[0])
            assert packet['format']==(signed.FORMAT if mode=='signed' else 'omp-memory-changes-v2')
            packet_versions.decode_packet(c,packet,security=security,folder='mac',filename=packet['uuid']+'.json')
            assert packet['body'][0]['row'] is None if delete else packet['body'][0]['row']['consumer']=='é'


def test_write_allows_unrelated_edit_and_checks_preserved_shadow(backlog):
    import resolution_apply as app
    import resolution_protocol as protocol
    db,_,_=backlog
    with sync.connect(db) as c,c:
        c.execute("UPDATE summary_state SET last_event_id=99 WHERE consumer='row-2999'")
        install=protocol.Transition(protocol.Key('summary_state',('row-0',)),str(uuid.uuid4()),
            sync.canonical(dict(consumer='row-0',last_event_id=1,updated_at='fixture')),'install')
        preserve=protocol.Transition(protocol.Key('summary_state',('row-1',)),str(uuid.uuid4()),
            sync.canonical(dict(consumer='row-1',last_event_id=77,updated_at='fixture')),'preserve')
        app._write(c,[install,preserve])
        assert c.execute("SELECT last_event_id FROM summary_state WHERE consumer='row-1'").fetchone()[0]==0
    assert sync.capture(db)['changes']==1


def test_write_tracks_import_flag_trigger(backlog):
    import resolution_apply as app
    import resolution_protocol as protocol
    db,_,_=backlog
    with sync.connect(db) as c,c:
        c.execute("CREATE TRIGGER test_import_side_effect AFTER UPDATE ON _sync_config WHEN NEW.importing=1 BEGIN UPDATE summary_state SET last_event_id=8 WHERE consumer='row-2999'; END")
    transition=protocol.Transition(protocol.Key('summary_state',('row-0',)),str(uuid.uuid4()),
        sync.canonical(dict(consumer='row-0',last_event_id=1,updated_at='fixture')),'install')
    with sync.connect(db) as c, pytest.raises(protocol.ResolutionConflict), c:
        app._write(c,[transition])
