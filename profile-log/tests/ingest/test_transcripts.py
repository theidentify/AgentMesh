"""Pure transcript reader (SQLite retirement R0); synthetic transcripts only."""
import json
from pathlib import Path
import tempfile
import unittest

from agentmesh_log.ingest import transcripts


def record(ident, text='synthetic note'):
    return json.dumps({'type': 'message', 'id': ident, 'message': {'role': 'user', 'content': [{'type': 'text', 'text': text}]}}) + '\n'


class TranscriptReaderTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / '-Workspaces-demo' / 'a.jsonl'
        self.path.parent.mkdir()

    def test_resolve_project_and_agent(self):
        self.path.write_text(record('one'), encoding='utf-8')
        path, project, skipped = transcripts.resolve(self.path, 'omp')
        self.assertEqual((path, project, skipped), (self.path.resolve(), 'demo', False))
        self.assertEqual(transcripts.resolve(self.path, 'omp', 'given')[1], 'given')
        with self.assertRaises(ValueError): transcripts.resolve(self.path, 'other')

    def test_only_complete_records_and_cursor_advance(self):
        self.path.write_text(record('one') + record('two')[:-5], encoding='utf-8')
        first = transcripts.read(self.path, 'omp', 'demo')
        self.assertEqual((len(first['events']), first['lines'], first['offset']), (1, 1, 0))
        with self.path.open('a', encoding='utf-8') as handle: handle.write(record('two')[-5:] + record('three'))
        second = transcripts.read(self.path, 'omp', 'demo', first['cursor'])
        self.assertEqual([e.source_event_id.split(':')[0] for e in second['events']], ['two', 'three'])
        self.assertEqual(second['offset'], first['cursor'][1])
        self.assertEqual(second['cursor'][2], 3)
        idle = transcripts.read(self.path, 'omp', 'demo', second['cursor'])
        self.assertFalse(idle['complete']); self.assertEqual(idle['cursor'], second['cursor'])

    def test_identity_change_or_truncation_restarts(self):
        self.path.write_text(record('one') + record('two'), encoding='utf-8')
        done = transcripts.read(self.path, 'omp', 'demo')
        moved = ('0:0',) + done['cursor'][1:]
        self.assertEqual(transcripts.read(self.path, 'omp', 'demo', moved)['offset'], 0)
        self.path.write_text(record('new'), encoding='utf-8')
        again = transcripts.read(self.path, 'omp', 'demo', done['cursor'])
        self.assertEqual((again['offset'], [e.source_event_id.split(':')[0] for e in again['events']]), (0, ['new']))

    def test_renumbered_device_keeps_the_cursor(self):
        """macOS renumbers APFS st_dev across reboots; the same inode is the same file and reads on from the cursor."""
        self.path.write_text(record('one'), encoding='utf-8')
        done = transcripts.read(self.path, 'omp', 'demo')
        with self.path.open('a', encoding='utf-8') as handle: handle.write(record('two'))
        stale = ('1:' + done['cursor'][0].rpartition(':')[2],) + done['cursor'][1:]
        after = transcripts.read(self.path, 'omp', 'demo', stale)
        self.assertEqual([e.source_event_id.split(':')[0] for e in after['events']], ['two'])
        self.assertEqual(after['cursor'][0], transcripts.identity(self.path)[0])
        self.assertFalse(transcripts.same_file(None, after['cursor'][0]))

    def test_invalid_json_is_reported_not_fatal(self):
        self.path.write_text('{not json\n' + record('ok'), encoding='utf-8')
        window = transcripts.read(self.path, 'omp', 'demo')
        self.assertEqual(len(window['errors']), 1)
        self.assertEqual(window['errors'][0][1:3] + window['errors'][0][4:5], (0, 1, 'invalid_json'))
        self.assertEqual(window['lines'], 2)


class SqliteInventoryTests(unittest.TestCase):
    """Shrink-only list of production modules that still touch SQLite, until retirement R5 deletes them."""
    # In-memory indexes and the supervisor's own state.db, not the staging database; they stay.
    KEEP = {'profile-log/agentmesh_log/broker/index.py', 'profile-log/agentmesh_log/broker/vector_store.py',
            'profile-log/agentmesh_log/control/store.py'}
    # SQLite retirement R5b removed every entry. Nothing outside KEEP may touch SQLite again.
    RETIRE = set()
    MARKERS = ('import sqlite3', 'sqlite3.connect', 'open_readonly', 'sqlite_memory')

    def test_no_new_sqlite_users(self):
        root = Path(__file__).resolve().parents[3]
        found = set()
        for base in ('insight', 'profile-log/agentmesh_log'):
            for path in (root / base).rglob('*.py'):
                rel = path.relative_to(root).as_posix()
                if path.name.startswith('test_') or path.name == 'conftest.py' or '/tests/' in rel or '/src/' in rel: continue
                if any(m in path.read_text(encoding='utf-8', errors='replace') for m in self.MARKERS): found.add(rel)
        self.assertLessEqual(found, self.KEEP | self.RETIRE, 'new SQLite user; the staging database is being retired')
        self.assertNotIn('profile-log/agentmesh_log/ingest/transcripts.py', found)


if __name__ == '__main__':
    unittest.main()
