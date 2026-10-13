"""Gate-2 migration is local, restartable and does not invent ancestry."""
import copy
import importlib
import json
import sqlite3

import pytest
import memory_sync as sync
import resolution_protocol as contract
import sqlite_memory
from test_packet_versions import new_packet, decode, versions, store
from test_memory_sync import GROUP


@pytest.fixture
def db(tmp_path):
    path = tmp_path/'db.sqlite'; sqlite_memory.init_database(path)
    with sync.connect(path) as c, c:
        c.execute("INSERT INTO summary_state(consumer,last_event_id) VALUES('baseline',1)")
    sync.initialize(path,'windows',GROUP)
    return path


TABLES = ('_sync_protocol', '_sync_revision_nodes', '_sync_revision_parents', '_sync_packet_evidence',
          '_sync_packet_dispositions', '_sync_origins', '_sync_resolution_operations', '_sync_packet_paths')


@pytest.mark.parametrize('fail_table', TABLES)
def test_migration_is_idempotent_and_atomic(store, db, fail_table):
    with sync.connect(db) as c:
        before = list(c.execute('SELECT * FROM sqlite_master'))
        c.set_authorizer(lambda action, name, *args: sqlite3.SQLITE_DENY
                         if action == sqlite3.SQLITE_CREATE_TABLE and name == fail_table else sqlite3.SQLITE_OK)
        with pytest.raises(sqlite3.DatabaseError): store.migrate(c)
        c.set_authorizer(None)
        assert list(c.execute('SELECT * FROM sqlite_master')) == before
        store.migrate(c)
        dump = '\n'.join(c.iterdump())
        store.migrate(c)
        assert '\n'.join(c.iterdump()) == dump
        assert c.execute('SELECT schema_version,emission_enabled,activation_manifest_digest FROM _sync_protocol').fetchone()[:] == (store.SCHEMA_VERSION,0,None)
        assert all(c.execute('SELECT 1 FROM sqlite_master WHERE name=?',(t,)).fetchone() for t in TABLES)
    # Durable when called outside a transaction, caller-owned rollback otherwise.
    with sync.connect(db) as c:
        assert c.execute('SELECT emission_enabled FROM _sync_protocol').fetchone()[0] == 0


def test_migration_does_not_commit_caller_transaction(store, db):
    with sync.connect(db) as c:
        c.execute('BEGIN IMMEDIATE'); store.migrate(c)
        c.rollback()
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='_sync_protocol'").fetchone()


def test_missing_baseline_witness_is_not_invented(store, db):
    key = contract.Key('summary_state',('baseline',))
    with sync.connect(db) as c, c:
        c.execute("UPDATE summary_state SET last_event_id=2 WHERE consumer='baseline'")
    sync.capture(db)
    with sync.connect(db) as c, c:
        store.migrate(c)
        with pytest.raises(contract.MissingEvidence): store.load_origins(c,[key])
        assert c.execute('SELECT count(*) FROM _sync_origins').fetchone()[0] == 0


def test_baseline_witness_hash_is_verified(store, db):
    key = contract.Key('summary_state',('baseline',))
    with sync.connect(db) as c, c:
        store.migrate(c)
        origin = store.load_origins(c,[key])[key]
        assert origin.startswith('baseline:')
        c.execute("UPDATE _sync_origins SET row_json='{}'")
        with pytest.raises(ValueError): store.load_origins(c,[key])


def test_reconstruct_edges_from_retained_packets(versions, store, db):
    first = new_packet()
    second = new_packet()
    second['body'][0]['parents'] = [first['body'][0]['revision']]
    second['checksum'] = contract.payload_digest({k:second[k] for k in ('group','node','uuid','kind','body','covers','evidence')})
    with sync.connect(db) as c, c:
        c.execute('BEGIN IMMEDIATE'); store.migrate(c)
        decode(versions,c,second)
        decode(versions,c,first)
        assert c.execute('SELECT revision,parent FROM _sync_revision_parents').fetchone()[:] == (second['body'][0]['revision'],first['body'][0]['revision'])
        k = contract.Key('summary_state',('gate2',))
        assert store.load_origins(c,[k])[k] is None
        assert c.execute('SELECT count(*) FROM _sync_revision_nodes').fetchone()[0] == 2
        assert c.execute('SELECT count(*) FROM _sync_history').fetchone()[0] == 1
        assert c.execute('SELECT count(*) FROM _sync_receipts').fetchone()[0] == 0


def test_reconstruct_legacy_outbox_with_explicit_adapter(versions, store, db):
    with sync.connect(db) as c, c:
        store.migrate(c)
        c.execute("UPDATE summary_state SET last_event_id=2 WHERE consumer='baseline'")
    sync.capture(db)
    with sync.connect(db) as c, c:
        c.execute('BEGIN IMMEDIATE')
        p = json.loads(c.execute('SELECT packet FROM _sync_outbox').fetchone()[0])
        decoded = decode(versions,c,p)
        assert decoded.changes[0].origin == p['body'][0]['parent']
        assert decoded.changes[0].parents == (p['body'][0]['parent'],)
        assert c.execute('SELECT evidence_state FROM _sync_revision_nodes').fetchone()[0] == 'local'


def test_changed_envelope_cannot_replace_evidence(versions, store, db):
    p = new_packet()
    with sync.connect(db) as c, c:
        c.execute('BEGIN IMMEDIATE'); decode(versions,c,p)
        old = '\n'.join(c.iterdump())
        changed = copy.deepcopy(p); changed['body'][0]['row']['last_event_id'] = 999
        changed['checksum'] = contract.payload_digest({k:changed[k] for k in ('group','node','uuid','kind','body','covers','evidence')})
        with pytest.raises(ValueError): decode(versions,c,changed)
        assert '\n'.join(c.iterdump()) == old
        ref = contract.Ref(p['uuid'], versions.envelope_identity(p))
        assert store.load_evidence(c,[ref])[p['uuid']] == p
        with pytest.raises(ValueError): store.load_evidence(c,[contract.Ref(p['uuid'],'f'*64)])


def test_revision_reuse_cross_key_and_cycles_refused(versions, store, db):
    p = new_packet(); q = new_packet()
    with sync.connect(db) as c, c:
        c.execute('BEGIN IMMEDIATE'); decode(versions,c,p)
        q['body'][0]['revision'] = p['body'][0]['revision']
        q['checksum'] = contract.payload_digest({k:q[k] for k in ('group','node','uuid','kind','body','covers','evidence')})
        with pytest.raises(ValueError): decode(versions,c,q)
        assert c.execute('SELECT count(*) FROM _sync_packet_evidence').fetchone()[0] == 1


@pytest.mark.parametrize('kind', ['cross-key', 'cycle'])
def test_cross_key_and_cyclic_delayed_parents_roll_back(versions, store, db, kind):
    p, q = new_packet(), new_packet()
    if kind == 'cross-key':
        q['body'][0]['key'] = ['other']; q['body'][0]['row']['consumer'] = 'other'
    else:
        p['body'][0]['parents'] = [q['body'][0]['revision']]
    q['body'][0]['parents'] = [p['body'][0]['revision']]
    for item in (p,q):
        item['checksum'] = contract.payload_digest({k:item[k] for k in ('group','node','uuid','kind','body','covers','evidence')})
    with sync.connect(db) as c, c:
        c.execute('BEGIN IMMEDIATE'); decode(versions,c,p)
        before = '\n'.join(c.iterdump())
        with pytest.raises(ValueError, match='cross-key|cyclic'): decode(versions,c,q)
        assert '\n'.join(c.iterdump()) == before


def test_legacy_unknown_parent_retained_without_guessing_origin(versions, store, db):
    p = new_packet()
    ch = p['body'][0]
    ch.update(key=['baseline'], row=dict(ch['row'], consumer='baseline'), parent=ch['revision'])
    del ch['parents']; del ch['origin']
    # Unknown predecessor, distinct from this change's revision.
    import uuid
    ch['revision'] = str(uuid.uuid4())
    p = {k:p[k] for k in ('group','node','uuid','body')}
    p.update(format=sync.FORMAT, checksum=sync.digest(p['body']))
    with sync.connect(db) as c, c:
        c.execute('BEGIN IMMEDIATE'); store.migrate(c)
        with pytest.raises(contract.MissingEvidence): decode(versions,c,p)
        assert c.execute('SELECT count(*) FROM _sync_packet_evidence').fetchone()[0] == 1
        assert c.execute('SELECT evidence_state FROM _sync_revision_nodes').fetchone()[0] == 'unresolved'


def test_future_schema_refused_without_mutation(store, db):
    with sync.connect(db) as c, c:
        store.migrate(c); c.execute('UPDATE _sync_protocol SET schema_version=99')
        before = '\n'.join(c.iterdump())
        with pytest.raises(ValueError, match='unsupported'): store.migrate(c)
        assert '\n'.join(c.iterdump()) == before


@pytest.mark.parametrize('action,name', [(sqlite3.SQLITE_CREATE_INDEX,'_sync_revision_parent_lookup'),
                                       (sqlite3.SQLITE_INSERT,'_sync_origins'),
                                       (sqlite3.SQLITE_INSERT,'_sync_protocol')])
def test_migration_final_boundaries_are_atomic(store, db, action, name):
    with sync.connect(db) as c:
        before = '\n'.join(c.iterdump())
        c.set_authorizer(lambda op, obj, *args: sqlite3.SQLITE_DENY if (op,obj)==(action,name) else sqlite3.SQLITE_OK)
        with pytest.raises(sqlite3.DatabaseError): store.migrate(c)
        c.set_authorizer(None)
        assert '\n'.join(c.iterdump()) == before
        store.migrate(c)
        assert c.execute('SELECT schema_version FROM _sync_protocol').fetchone()[0] == store.SCHEMA_VERSION
