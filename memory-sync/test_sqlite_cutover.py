"""Seeding the broker summarizer from the staging database (SQLite retirement R4), synthetic transcripts only."""
import json
import os
from pathlib import Path

import pytest

import broker_digest
import bounded_digest
import ingest_sessions
import memory_sync
import sqlite_cutover
import sqlite_memory
from test_broker_digest import Fake, extractor, record


@pytest.fixture
def staging(tmp_path):
    folder = tmp_path / 'omp' / '-Workspaces-demo'
    folder.mkdir(parents=True)
    db = tmp_path / 'staging.db'
    sqlite_memory.init_database(db)
    memory_sync.initialize(db, 'mac', '9a95369f-97c5-4b0e-bf41-f110ab1450a9')
    return tmp_path, folder, db


def summarized(fake):
    return [i['content'] for w in fake.writes for i in w['items']]


def test_waiting_events_carry_over_exactly(staging):
    tmp_path, folder, db = staging
    (folder / 'done.jsonl').write_text(record('d1', 'alpha one') + record('d2', 'beta two'))
    (folder / 'half.jsonl').write_text(record('h1', 'gamma three') + record('h2', 'delta four') + record('h3', 'epsilon five'))
    roots = {'omp': tmp_path / 'omp'}
    assert ingest_sessions.ingest(db, roots=roots)['errors'] == 0
    # The SQLite summary covers three events (done.jsonl and the first of half.jsonl); two are still waiting.
    for _ in range(3):
        bounded_digest.run_once(db, extractor=extractor, model='m', max_events=1)
    with (folder / 'half.jsonl').open('a') as handle: handle.write(record('h4', 'zeta six'))  # after the ingest cursor
    counts = sqlite_cutover.plan(db)['counts']
    assert (counts['transcripts'], counts['reread'], counts['waiting_events'], counts['unreadable']) == (2, 1, 2, 0)
    jobs = tmp_path / 'jobs'
    sqlite_cutover.seed(db, jobs)
    files = json.loads((jobs / broker_digest.CURSORS).read_text())['files']
    done = files[str((folder / 'done.jsonl').resolve())]
    assert done['done'] == [] and done['offset'] == (folder / 'done.jsonl').stat().st_size
    assert files[str((folder / 'half.jsonl').resolve())]['offset'] == 0
    fake = Fake()
    state = broker_digest.State(jobs)
    sources, _ = broker_digest.discover(roots)
    while broker_digest.run_once(state, sources, fake, extractor=extractor, model='m')['status'] != 'idle': pass
    assert sorted(summarized(fake)) == ['delta four', 'epsilon five', 'zeta six']
    with pytest.raises(ValueError, match='already exists'): sqlite_cutover.seed(db, jobs)


def test_floor_schedule_and_quarantine_carry_over(staging):
    tmp_path, folder, db = staging
    sqlite_memory.configure_ingestion(db, '2026-06-01T00:00:00Z')
    with memory_sync.connect(db) as c, c:
        c.execute(bounded_digest.QUARANTINE_SQL)
        c.execute("INSERT INTO memory_quarantine(consumer,batch_id,memory_key,reason,event_ids) VALUES('c','b1','k1','deletion requires confirmation','[]')")
        c.execute("INSERT INTO memory_quarantine(consumer,batch_id,memory_key,reason,event_ids,resolved_at) VALUES('c','b2','k2','r','[]','x')")
        c.execute('CREATE TABLE _agentmesh_digest_windows(consumer TEXT PRIMARY KEY,state TEXT NOT NULL)')
        c.execute('INSERT INTO _agentmesh_digest_windows VALUES(?,?)', (sqlite_cutover.CONSUMER, json.dumps(dict(pending=None, success=dict(day='2026-11-02', boot_id='b', completed_at='t', events=3)))))
    sqlite_cutover.seed(db, tmp_path / 'jobs')
    state = broker_digest.State(tmp_path / 'jobs')
    assert state.summary['start_at'].startswith('2026-06-01')
    assert state.summary['success'] == dict(day='2026-11-02', boot_id='b', completed_at='t')
    assert [q['memory_key'] for q in state.summary['quarantine']] == ['k1']
    if os.name != 'nt':
        assert (tmp_path / 'jobs').stat().st_mode & 0o777 == 0o700


def test_replaced_transcript_with_waiting_events_refuses(staging):
    tmp_path, folder, db = staging
    (folder / 'a.jsonl').write_text(record('a1', 'alpha one'))
    ingest_sessions.ingest(db, roots={'omp': tmp_path / 'omp'})
    (folder / 'new.tmp').write_text(record('n1', 'other'))
    os.replace(folder / 'new.tmp', folder / 'a.jsonl')
    assert sqlite_cutover.plan(db)['counts']['unreadable'] == 1
    with pytest.raises(ValueError, match='gone or replaced'): sqlite_cutover.seed(db, tmp_path / 'jobs')
    assert not (tmp_path / 'jobs' / broker_digest.CURSORS).exists()


def test_checkpoint_folds_the_wal(staging):
    tmp_path, folder, db = staging
    sqlite_cutover.checkpoint(db)
    wal = Path(str(db) + '-wal')
    assert not wal.exists() or wal.stat().st_size == 0


def test_rekeyed_waiting_event_is_summarized_again(staging):
    """The ingest stored a key a whole-file read does not produce: the event is read again, never marked done."""
    tmp_path, folder, db = staging
    (folder / 'a.jsonl').write_text(record('a1', 'alpha one') + record('a2', 'beta two'))
    roots = {'omp': tmp_path / 'omp'}
    ingest_sessions.ingest(db, roots=roots)
    bounded_digest.run_once(db, extractor=extractor, model='m', max_events=1)
    with memory_sync.connect(db) as c, c:
        c.execute('UPDATE _sync_config SET importing=1')
        c.execute("UPDATE observation_events SET event_key='piecewise-key' WHERE content='beta two'")
        c.execute('UPDATE _sync_config SET importing=0')
    assert sqlite_cutover.plan(db)['counts']['rekeyed_events'] == 1
    sqlite_cutover.seed(db, tmp_path / 'jobs')
    fake = Fake()
    state = broker_digest.State(tmp_path / 'jobs')
    while broker_digest.run_once(state, broker_digest.discover(roots)[0], fake, extractor=extractor, model='m')['status'] != 'idle': pass
    assert summarized(fake) == ['beta two']
