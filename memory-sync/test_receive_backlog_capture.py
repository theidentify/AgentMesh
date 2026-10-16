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
