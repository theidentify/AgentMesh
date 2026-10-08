"""Real SQLite tests; only the external model command is a stub."""
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

import memory_sync
import sqlite_memory


class SummarizeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'memory.db'
        sqlite_memory.init_database(self.db)
        memory_sync.initialize(self.db, 'mac', '9a95369f-97c5-4b0e-bf41-f110ab1450a9')
        self.add_event(1)

    def add_event(self, ident, project='demo', session='session-1'):
        with memory_sync.connect(self.db) as c, c:
            c.execute('UPDATE _sync_config SET importing=1')
            c.execute('''INSERT INTO observation_events
                (id,event_key,source_path,source_session_id,source_event_id,project,task_ref,role,kind,content,source_hash)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                (ident, 'event-' + str(ident), 'private/transcript.jsonl', session,
                 'original-' + str(ident), project, 'DEMO-1', 'user', 'user_request',
                 'Use SQLite for the portable mirror.', 'hash-' + str(ident)))
            c.execute('UPDATE _sync_config SET importing=0')

    def api(self):
        self.assertIsNotNone(importlib.util.find_spec('summarize_memory'), 'portable summarizer module is missing')
        return importlib.import_module('summarize_memory')

    def snapshot(self):
        with memory_sync.connect(self.db) as c:
            return {name: [tuple(r) for r in c.execute('SELECT * FROM ' + name)]
                    for name in ('memory_items', 'memory_sources', 'memory_summaries', 'summary_state', '_sync_counters')}

    def test_sqlite_summary_version_does_not_block_later_postgres_version(self):
        with memory_sync.connect(self.db) as c, c:
            c.execute("INSERT INTO memory_summaries(id,scope,scope_key,version,content) VALUES(1,'project','demo',1,'legacy')")
        api = self.api()
        task = api.prepare_task(self.db)
        api.commit_response(self.db, task, self.response(task))
        with memory_sync.connect(self.db) as c, c:
            c.execute("INSERT INTO memory_summaries(id,scope,scope_key,version,content) VALUES(2,'project','demo',2,'later legacy')")
            row = c.execute('SELECT version FROM memory_summaries WHERE id>=?', (memory_sync.RANGES['mac'][0],)).fetchone()
            self.assertGreaterEqual(row[0], memory_sync.RANGES['mac'][0])

    def test_initial_legacy_coverage_skips_only_prior_postgres_ids(self):
        self.add_event(memory_sync.RANGES['windows'][0])
        with memory_sync.connect(self.db) as c, c:
            c.execute("INSERT INTO summary_state(consumer,last_event_id) VALUES('hermes',1)")
        api = self.api()
        self.assertEqual(api.seed_legacy_coverage(self.db), 1)
        self.assertEqual([e['id'] for e in api.prepare_task(self.db)['events']], [memory_sync.RANGES['windows'][0]])
        self.add_event(2)
        self.assertEqual([e['id'] for e in api.prepare_task(self.db)['events']], [2, memory_sync.RANGES['windows'][0]])
        with memory_sync.connect(self.db) as c, c:
            c.execute("UPDATE summary_state SET last_event_id=2 WHERE consumer='hermes'")
        self.assertEqual(api.seed_legacy_coverage(self.db), 1)
        self.assertEqual([e['id'] for e in api.prepare_task(self.db)['events']], [2, memory_sync.RANGES['windows'][0]])

    def test_preview_exports_grounded_task_without_database_writes(self):
        before = self.snapshot()
        task = self.api().prepare_task(self.db, max_events=1)
        self.assertEqual(task['format'], 'omp-sqlite-summary-task-v1')
        self.assertEqual(task['checkpoint'], 0)
        self.assertEqual(task['events'][0]['id'], 1)
        self.assertEqual(task['events'][0]['source_event_id'], 'original-1')
        self.assertEqual(task['events'][0]['content'], 'Use SQLite for the portable mirror.')
        self.assertEqual(task['event_range'], {'min': 1, 'max': 1, 'count': 1})
        self.assertEqual(task['primary_node'], 'mac')
        self.assertEqual(before, self.snapshot())
    def response(self, task):
        return {'format': 'omp-sqlite-summary-response-v1', 'batch_id': task['batch_id'],
                'provider': 'test-boundary', 'model': 'test-only',
                'items': [{'kind': 'decision', 'scope': 'project', 'scope_key': 'demo',
                           'project': 'demo', 'content': 'SQLite is the portable mirror.',
                           'confidence': 0.9, 'source_event_ids': [task['events'][0]['id']]}],
                'summaries': [{'scope': 'project', 'scope_key': 'demo',
                               'content': 'The portable mirror uses SQLite.',
                               'source_event_ids': [task['events'][0]['id']]}]}

    def command(self, extra=''):
        code = "import json,sys\ntask=json.load(sys.stdin)\n" + extra + "\n"
        code += "result=" + repr(self.response({'batch_id': 'placeholder', 'events': [{'id': 1}]})) + "\n"
        code += "result['batch_id']=task['batch_id']\n"
        code += "for row in result['items']+result['summaries']: row['source_event_ids']=[task['events'][0]['id']]\n"
        code += "print(json.dumps(result))\n"
        script = self.root / 'provider_boundary.py'
        script.write_text(code, encoding='utf-8')
        return [sys.executable, str(script)]

    def test_external_provider_writes_atomic_grounded_summaries_and_deduplicates(self):
        api = self.api()
        result = api.summarize_once(self.db, command=self.command())
        self.assertEqual(result['status'], 'committed')
        self.assertEqual(result['items'], 2)
        self.assertEqual(result['summaries'], 1)
        with memory_sync.connect(self.db) as c:
            summary = c.execute('SELECT * FROM memory_summaries').fetchone()
            self.assertEqual(summary['version'], summary['id'])
            self.assertEqual(summary['id'], memory_sync.RANGES['mac'][0])
            metadata = json.loads(summary['metadata'])
            self.assertEqual(metadata['provider'], 'test-boundary')
            self.assertEqual(metadata['source_event_ids'], [1])
            self.assertEqual(metadata['sources'][0]['source_event_id'], 'original-1')
            self.assertEqual(metadata['batch_event_ids'], [1])
            self.assertEqual(c.execute('SELECT count(*) FROM memory_sources').fetchone()[0], 2)
            self.assertEqual(c.execute('SELECT last_event_id FROM summary_state WHERE consumer=?',
                                       (api.DEFAULT_CONSUMER,)).fetchone()[0], 1)
            self.assertFalse(c.execute('PRAGMA foreign_key_check').fetchall())
        before = self.snapshot()
        self.assertEqual(api.summarize_once(self.db, command=['does-not-exist'])['status'], 'idle')
        self.assertEqual(before, self.snapshot())
    def test_provider_failure_never_advances_checkpoint_or_allocates_ids(self):
        api = self.api()
        before = self.snapshot()
        commands = [None, ['not-a-real-provider-command'],
                    [sys.executable, '-c', "import sys;print('PRIVATE SOURCE',file=sys.stderr);sys.exit(3)"],
                    [sys.executable, '-c', "print('PRIVATE SOURCE invalid JSON')"],
                    [sys.executable, '-c', 'import time;time.sleep(2)']]
        for command in commands:
            with self.subTest(command=command):
                with self.assertRaises(api.SummaryError) as error:
                    api.summarize_once(self.db, command=command, timeout=0.1)
                self.assertNotIn('PRIVATE SOURCE', str(error.exception))
                self.assertEqual(before, self.snapshot())

    def test_invalid_provider_items_are_rejected_as_an_entire_batch(self):
        api = self.api()
        task = api.prepare_task(self.db)
        before = self.snapshot()
        mutations = [('kind', 'invention'), ('scope', 'other'), ('confidence', True),
                     ('confidence', float('nan')), ('confidence', 1.1),
                     ('content', ''), ('source_event_ids', [999]), ('source_event_ids', [True]),
                     ('source_event_ids', []), ('source_event_ids', [1, 1]),
                     ('scope_key', 'foreign-project'), ('project', 'foreign-project')]
        for key, value in mutations:
            response = self.response(task)
            response['items'].append({**response['items'][0], key: value})
            with self.subTest(key=key, value=value):
                with self.assertRaises(api.SummaryError):
                    api.commit_response(self.db, task, response)
                self.assertEqual(before, self.snapshot())
        for mutation in ('batch', 'duplicate', 'extra', 'scope_summary'):
            response = self.response(task)
            if mutation == 'batch':
                response['batch_id'] = 'wrong-batch'
            elif mutation == 'duplicate':
                response['items'].append(response['items'][0].copy())
            elif mutation == 'extra':
                response['items'][0]['status'] = 'resolved'
            else:
                response['summaries'][0]['scope'] = 'global'
            with self.assertRaises(api.SummaryError):
                api.commit_response(self.db, task, response)
            self.assertEqual(before, self.snapshot())

    def test_later_lower_ids_and_out_of_order_same_lane_are_not_skipped(self):
        api = self.api()
        high = memory_sync.RANGES['linux'][0] + 7
        self.add_event(high)
        api.summarize_once(self.db, command=self.command())
        later_ids = [2, memory_sync.RANGES['windows'][0] + 8,
                     memory_sync.RANGES['mac'][0] + 3]
        for ident in later_ids:
            self.add_event(ident)
        task = api.prepare_task(self.db)
        self.assertEqual(task['checkpoint'], high)
        self.assertEqual([event['id'] for event in task['events']], later_ids)
        api.summarize_once(self.db, command=self.command())
        # A predecessor arrives even later within the SAME windows lane.
        self.add_event(memory_sync.RANGES['windows'][0] + 2)
        self.assertEqual(api.prepare_task(self.db)['event_range']['count'], 1)
        api.summarize_once(self.db, command=self.command())
        self.assertFalse(api.prepare_task(self.db)['events'])

    def test_provider_call_does_not_hold_sqlite_write_transaction(self):
        extra = ("import sqlite3\nc=sqlite3.connect(" + repr(str(self.db)) + ",timeout=0.1)\n"
                 "c.execute('BEGIN IMMEDIATE')\nc.execute(\"INSERT INTO summary_state VALUES('other-job',0,CURRENT_TIMESTAMP)\")\n"
                 "c.commit()\nc.close()")
        self.assertEqual(self.api().summarize_once(self.db, command=self.command(extra))['status'], 'committed')

    def test_checkpoint_race_discards_expensive_provider_result(self):
        api = self.api()
        extra = ("import sqlite3\nc=sqlite3.connect(" + repr(str(self.db)) + ")\n"
                 "c.execute('INSERT INTO summary_state(consumer,last_event_id) VALUES(?,?)',"
                 "(" + repr(api.DEFAULT_CONSUMER) + ",77))\nc.commit()\nc.close()")
        result = api.summarize_once(self.db, command=self.command(extra))
        self.assertEqual(result['status'], 'stale')
        self.assertEqual(len(self.snapshot()['memory_items']), 0)
        self.assertEqual(len(self.snapshot()['memory_summaries']), 0)

    def test_evidence_edit_during_provider_call_discards_result(self):
        api = self.api()
        task = api.prepare_task(self.db)
        with memory_sync.connect(self.db) as c, c:
            c.execute("UPDATE observation_events SET content='Changed evidence' WHERE id=1")
        before = self.snapshot()
        self.assertEqual(api.commit_response(self.db, task, self.response(task))['status'], 'stale')
        self.assertEqual(before, self.snapshot())

    def test_empty_model_result_records_receipts_and_does_not_invent_summaries(self):
        api = self.api()
        task = api.prepare_task(self.db)
        response = {**self.response(task), 'items': [], 'summaries': []}
        result = api.commit_response(self.db, task, response)
        self.assertEqual(result['status'], 'committed')
        self.assertEqual(result['items'], 0)
        self.assertEqual(result['summaries'], 0)
        self.assertFalse(api.prepare_task(self.db)['events'])

    def test_replay_and_tampered_exported_task_do_not_write(self):
        api = self.api()
        task = api.prepare_task(self.db)
        response = self.response(task)
        tampered = json.loads(json.dumps(task))
        tampered['events'][0]['content'] = 'Fabricated replacement'
        before = self.snapshot()
        self.assertEqual(api.commit_response(self.db, tampered, response)['status'], 'stale')
        self.assertEqual(before, self.snapshot())
        api.commit_response(self.db, task, response)
        before = self.snapshot()
        self.assertEqual(api.commit_response(self.db, task, response)['status'], 'stale')
        self.assertEqual(before, self.snapshot())
    def test_primary_enforcement_and_explicit_consistent_override(self):
        api = self.api()
        self.db = self.root / 'windows.db'
        sqlite_memory.init_database(self.db)
        memory_sync.initialize(self.db, 'windows', '9a95369f-97c5-4b0e-bf41-f110ab1450a9')
        self.add_event(1)
        with self.assertRaises(api.SummaryError):
            api.prepare_task(self.db)
        task = api.prepare_task(self.db, primary_node='windows')
        self.assertEqual(task['primary_node'], 'windows')
        api.commit_response(self.db, task, self.response(task))
        with memory_sync.connect(self.db) as c, c:
            c.execute("UPDATE _sync_config SET node='mac'")
        with self.assertRaises(api.SummaryError):
            api.prepare_task(self.db)

    def test_bounded_batches_increment_versions_without_touching_production_cursor(self):
        api = self.api()
        self.add_event(2)
        with memory_sync.connect(self.db) as c, c:
            c.execute("INSERT INTO summary_state(consumer,last_event_id) VALUES('hermes',91)")
            c.execute('''INSERT INTO memory_summaries(id,scope,scope_key,version,content)
                VALUES(7,'project','demo',4,'Existing mirrored summary')''')
        for version in (memory_sync.RANGES['mac'][0], memory_sync.RANGES['mac'][0] + 1):
            result = api.summarize_once(self.db, command=self.command(), max_events=1)
            self.assertEqual(result['events'], 1)
            with memory_sync.connect(self.db) as c:
                self.assertEqual(c.execute('SELECT max(version) FROM memory_summaries').fetchone()[0], version)
                self.assertEqual(c.execute("SELECT last_event_id FROM summary_state WHERE consumer='hermes'").fetchone()[0], 91)
                self.assertEqual(c.execute('SELECT content FROM memory_summaries WHERE id=7').fetchone()[0], 'Existing mirrored summary')
        with self.assertRaises(api.SummaryError):
            api.prepare_task(self.db, consumer='hermes')

    def test_sqlite_failure_rolls_back_items_summaries_receipts_and_allocations(self):
        api = self.api()
        with memory_sync.connect(self.db) as c:
            c.execute('''CREATE TRIGGER reject_summary BEFORE INSERT ON memory_summaries
                BEGIN SELECT RAISE(ABORT,'test write failure'); END''')
        before = self.snapshot()
        with self.assertRaises(sqlite3.IntegrityError):
            api.summarize_once(self.db, command=self.command())
        self.assertEqual(before, self.snapshot())

    def test_explicit_bounds_do_not_truncate_individual_source_events(self):
        api = self.api()
        with self.assertRaises(api.SummaryError):
            api.prepare_task(self.db, max_chars=1)
        with self.assertRaises(api.SummaryError):
            api.prepare_task(self.db, max_events=0)
        self.add_event(2)
        task = api.prepare_task(self.db)
        length = len(memory_sync.canonical(task['events'][0]))
        bounded = api.prepare_task(self.db, max_chars=length)
        self.assertEqual(len(bounded['events']), 1)
        self.assertEqual(bounded['events'][0], task['events'][0])

    def test_strict_json_rejects_duplicate_keys_and_nonfinite_numbers(self):
        api = self.api()
        task = api.prepare_task(self.db)
        for text in ('{"format":1,"format":2}', '{"value":NaN}'):
            with self.assertRaises(api.SummaryError):
                api.invoke_command(task, [sys.executable, '-c', 'print(' + repr(text) + ')'])

    def cli(self, *args):
        import subprocess
        return subprocess.run([sys.executable, str(Path(__file__).with_name('summarize_memory.py')),
                               *map(str, args)], capture_output=True, text=True)

    def test_cli_preview_export_and_missing_command_fail_honestly(self):
        before = self.snapshot()
        preview = self.cli('preview', self.db, '--max-events', '1')
        self.assertEqual(preview.returncode, 0, preview.stderr)
        task = json.loads(preview.stdout)
        self.assertEqual(task['events'][0]['id'], 1)
        export = self.root / 'private-task.json'
        result = self.cli('export', self.db, '--output', export)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(export.read_text())['batch_id'], task['batch_id'] if task['limits']['max_events'] == 100 else self.api().prepare_task(self.db)['batch_id'])
        if os.name != 'nt':
            self.assertEqual(export.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('Use SQLite', result.stdout)
        unavailable = self.cli('once', self.db)
        self.assertNotEqual(unavailable.returncode, 0)
        self.assertIn('no provider command configured', unavailable.stderr)
        self.assertNotIn('Use SQLite', unavailable.stderr + unavailable.stdout)
        self.assertEqual(before, self.snapshot())

    def test_cli_configured_command_runs_real_adapter_boundary(self):
        config_path = self.root / 'private-config.json'
        config_path.write_text(json.dumps({'command': self.command()}))
        result = self.cli('once', self.db, '--config', config_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'committed')
        self.assertNotIn('Use SQLite', result.stdout + result.stderr)
    def test_shared_provenance_recovers_dedup_when_event_receipt_is_missing(self):
        api = self.api()
        self.add_event(2)
        api.summarize_once(self.db, command=self.command())
        with memory_sync.connect(self.db) as c, c:
            c.execute('DELETE FROM summary_state WHERE consumer=?',
                      (api.DEFAULT_CONSUMER + ':event:2',))
        # The provider cited only event 1, but full batch coverage in shared
        # provenance records that event 2 was also reviewed.
        self.assertFalse(api.prepare_task(self.db)['events'])

    def test_every_supported_memory_kind_and_scope_uses_real_schema(self):
        api = self.api()
        task = api.prepare_task(self.db)
        response = self.response(task)
        response['items'] = []
        for scope, key in [('global', None), ('project', 'demo'), ('task', 'DEMO-1'), ('session', 'session-1')]:
            for kind in api.KINDS:
                response['items'].append({'scope': scope, 'scope_key': key, 'project': 'demo',
                                          'kind': kind, 'content': scope + '/' + kind,
                                          'confidence': 0.5, 'source_event_ids': [1]})
        response['summaries'] = [{'scope': scope, 'scope_key': key, 'content': scope + ' overview',
                                  'source_event_ids': [1]}
                                 for scope, key in [('project', 'demo'), ('task', 'DEMO-1'), ('session', 'session-1')]]
        result = api.commit_response(self.db, task, response)
        self.assertEqual(result['items'], 31)
        self.assertEqual(result['summaries'], 3)
        with memory_sync.connect(self.db) as c:
            self.assertFalse(c.execute('PRAGMA foreign_key_check').fetchall())

    def test_synced_peer_receives_all_summary_provenance_and_receipts(self):
        api = self.api()
        peer = self.root / 'peer.db'
        sqlite_memory.init_database(peer)
        memory_sync.initialize(peer, 'windows', '9a95369f-97c5-4b0e-bf41-f110ab1450a9')
        api.summarize_once(self.db, command=self.command())
        exchange = self.root / 'exchange'
        memory_sync.capture(self.db)
        self.assertEqual(memory_sync.publish(self.db, exchange)['published'], 1)
        received = memory_sync.receive(peer, exchange)
        self.assertEqual(received['applied'], 1)
        self.assertEqual(received['conflict'], 0)
        with memory_sync.connect(peer) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM memory_sources').fetchone()[0], 2)
            self.assertTrue(c.execute('SELECT 1 FROM summary_state WHERE consumer=?',
                                      (api.DEFAULT_CONSUMER + ':event:1',)).fetchone())
            provenance = json.loads(c.execute('SELECT metadata FROM memory_summaries').fetchone()[0])
            self.assertEqual(provenance['sources'][0]['source_event_id'], 'original-1')
            self.assertFalse(c.execute('PRAGMA foreign_key_check').fetchall())
        with self.assertRaises(api.SummaryError):
            api.prepare_task(peer)


if __name__ == '__main__':
    unittest.main()
