"""Synthetic transcript integration tests; never read real user histories."""
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

import sqlite_memory


class IngestSessionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.db = self.home / 'memory.db'
        sqlite_memory.init_database(self.db)

    def write(self, path, agent='omp', ident='one', text='synthetic token=SECRET'):
        path.parent.mkdir(parents=True, exist_ok=True)
        if agent == 'omp':
            record = {'type': 'message', 'id': ident, 'message': {'role': 'user', 'content': [{'type': 'text', 'text': text}]}}
        elif agent == 'codex':
            record = {'type': 'event_msg', 'payload': {'type': 'item_completed', 'item': {'type': 'UserMessage', 'id': ident, 'content': [{'type': 'text', 'text': text}]}}}
        else:
            record = {'type': 'user', 'uuid': ident, 'message': {'role': 'user', 'content': text}}
        with path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(record) + '\n')
        return path

    def ingest(self, **kwargs):
        from ingest_sessions import ingest
        return ingest(self.db, home=self.home, **kwargs)

    def rows(self):
        with sqlite3.connect(self.db) as connection:
            return connection.execute('SELECT source_agent,content,project FROM observation_events').fetchall()

    def test_default_home_discovers_all_three_agents_with_parser_redaction(self):
        for agent, relative in [('omp', '.omp/agent/sessions/nested/a.jsonl'), ('codex', '.codex/sessions/2026/a.jsonl'), ('claude', '.claude/projects/project/a.jsonl')]:
            path = self.write(self.home / relative, agent)
            if agent == 'codex':
                path.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'main'}}) + '\n' + path.read_text())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            report = self.ingest()
        self.assertEqual((report['found'], report['processed'], report['inserted'], report['errors']), (3, 3, 3, 0))
        self.assertEqual({row[0] for row in self.rows()}, {'omp', 'codex', 'claude'})
        self.assertTrue(all('[REDACTED]' in row[1] and 'SECRET' not in row[1] for row in self.rows()))
        self.assertEqual(output.getvalue(), '')
        self.assertNotIn('SECRET', json.dumps(report))

    def test_production_project_inference_and_codex_guards(self):
        root = self.home / 'native'
        good = self.write(root / 'codex/a.jsonl', 'codex')
        meta = {'type': 'session_meta', 'payload': {'id': 'main', 'cwd': str(Path.home() / 'Workspaces/demo/nested')}}
        good.write_text(json.dumps(meta) + '\n' + good.read_text())
        self.write(root / 'codex/missing.jsonl', 'codex', ident='missing')
        review = self.write(root / 'codex/review.jsonl', 'codex', ident='review')
        review.write_text(json.dumps({'type': 'session_meta', 'payload': {'thread_source': 'guardian_review'}}) + '\n' + review.read_text())
        claude_dir = '-' + str(Path.home() / 'Workspaces/demo').strip('/').replace('/', '-')
        self.write(root / 'claude' / claude_dir / 'main.jsonl', 'claude')
        self.write(root / 'omp/-Workspaces-demo/main.jsonl')
        report = self.ingest(roots={a: root / a for a in ('codex', 'claude', 'omp')})
        self.assertEqual(report['inserted'], 3)
        self.assertEqual(report['skipped'], 2)
        self.assertEqual({(r[0], r[2]) for r in self.rows()}, {('codex', 'demo-nested'), ('claude', 'demo'), ('omp', 'demo')})
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM ingestion_cursors').fetchone()[0], 3)

    def test_repeat_skips_cursor_complete_files_and_append_is_incremental(self):
        path = self.write(self.home / 'custom/nested/a.jsonl')
        roots = {'omp': self.home / 'custom'}
        self.assertEqual(self.ingest(roots=roots)['inserted'], 1)
        report = self.ingest(roots=roots)
        self.assertEqual((report['processed'], report['skipped'], report['inserted']), (0, 1, 0))
        self.write(path, ident='two', text='append')
        report = self.ingest(roots=roots)
        self.assertEqual((report['processed'], report['skipped'], report['inserted']), (1, 0, 1))
        self.assertEqual(len(self.rows()), 2)

    def test_symlinks_and_claude_subagents_are_not_ingested(self):
        root = self.home / 'claude'
        self.write(root / 'project/main.jsonl', 'claude')
        self.write(root / 'project/session/subagents/agent-x.jsonl', 'claude', ident='sub')
        outside = self.write(self.home / 'outside/escape.jsonl', 'claude', ident='escape')
        (root / 'project/link.jsonl').symlink_to(outside)
        (root / 'linked-dir').symlink_to(outside.parent, target_is_directory=True)
        report = self.ingest(roots={'claude': root})
        self.assertEqual(report['inserted'], 1)
        self.assertEqual(report['skipped'], 1)
        self.assertEqual(len(self.rows()), 1)

    def test_missing_database_is_rejected_without_creating_a_file(self):
        from ingest_sessions import ingest
        missing = self.home / 'missing.db'
        with self.assertRaises(sqlite3.OperationalError):
            ingest(missing, roots={})
        self.assertFalse(missing.exists())

    def test_missing_roots_are_empty_normal_state(self):
        report = self.ingest()
        self.assertEqual({key: report[key] for key in ('found', 'processed', 'skipped', 'inserted', 'errors')}, dict.fromkeys(('found', 'processed', 'skipped', 'inserted', 'errors'), 0))

    def test_invalid_files_report_only_error_classes_and_continue(self):
        root = self.home / 'custom'
        self.write(root / 'healthy.jsonl')
        meta = json.dumps({'type': 'session_meta', 'payload': {'id': 'main'}}) + '\n'
        (root / 'invalid.jsonl').write_text(meta + 'SECRET not-json\n', encoding='utf-8')
        (root / 'bad-shape.jsonl').write_text(meta + json.dumps({'type': 'event_msg', 'payload': 'SECRET'}) + '\n', encoding='utf-8')
        report = self.ingest(roots={'codex': root})
        self.assertEqual(report['errors'], 2)
        self.assertEqual(sorted(report['error_classes']), ['AttributeError', 'JSONDecodeError'])
        self.assertNotIn('SECRET', json.dumps(report))
        # A separate OMP file still ingests despite independent Codex errors.
        self.assertEqual(self.ingest(roots={'omp': root})['errors'], 0)

    def test_unreadable_file_does_not_abort_other_files(self):
        from unittest.mock import patch
        root = self.home / 'custom'
        blocked = self.write(root / 'blocked.jsonl').resolve()
        self.write(root / 'healthy.jsonl', ident='healthy')
        original = Path.open
        def open_file(path, *args, **kwargs):
            if path == blocked:
                raise PermissionError('SECRET permission failure')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', open_file):
            report = self.ingest(roots={'omp': root})
        self.assertEqual((report['inserted'], report['errors']), (1, 1))
        self.assertEqual(report['error_classes'], ['PermissionError'])
        self.assertNotIn('SECRET', json.dumps(report))

    def test_cli_repeatable_roots_project_and_safe_json_stdout(self):
        omp = self.home / 'custom-omp'
        claude = self.home / 'custom-claude'
        self.write(omp / 'a.jsonl')
        self.write(claude / 'a.jsonl', 'claude')
        command = [sys.executable, str(Path(__file__).with_name('ingest_sessions.py')), str(self.db), '--root', f'omp={omp}', '--root', f'claude={claude}', '--project', 'fixed-project']
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['inserted'], 2)
        self.assertEqual({row[2] for row in self.rows()}, {'fixed-project'})
        self.assertNotIn('SECRET', result.stdout + result.stderr)
        self.assertNotIn('synthetic', result.stdout + result.stderr)
        self.assertEqual(result.stderr, '')


if __name__ == '__main__':
    unittest.main()
