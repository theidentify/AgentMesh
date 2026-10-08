"""SQLite writer outcomes; provider boundary tests are explicitly synthetic."""
import importlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

import memory_sync
import sqlite_memory


class BoundedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'memory.db'
        sqlite_memory.init_database(self.db)
        memory_sync.initialize(self.db, 'mac', '9a95369f-97c5-4b0e-bf41-f110ab1450a9')
        self.event(1, 'Keep deployment manual.')

    def api(self):
        self.assertIsNotNone(importlib.util.find_spec('bounded_digest'), 'bounded SQLite adapter missing')
        return importlib.import_module('bounded_digest')

    def event(self, ident, text, project='demo'):
        with memory_sync.connect(self.db) as c, c:
            c.execute('UPDATE _sync_config SET importing=1')
            c.execute('''INSERT INTO observation_events
                (id,event_key,source_path,source_session_id,source_event_id,project,role,kind,content,source_hash)
                VALUES(?,?,?,?,?,?,'user','user_request',?,?)''',
                (ident, 'event-' + str(ident), 'private/sample.jsonl', 'session', str(ident), project, text, 'hash-' + str(ident)))
            c.execute('UPDATE _sync_config SET importing=0')

    def payload(self, task, status='active', update=False):
        e = task['events'][0]
        item = dict(memory_key='project.demo.constraint.manual-deploy', content=e['content'], status=status,
                    confidence=1.0, source_event_ids=[e['id']], evidence=[dict(event_id=e['id'], quote=e['content'])])
        if not update:
            item.update(kind='constraint', scope='project', scope_key='demo', project='demo')
        return dict(items=[item], reviewed_event_ids=[e['id'] for e in task['events']], conflicts=[])

    def test_stable_key_correction_preserves_identity_sources_and_summary(self):
        api = self.api()
        task = api.prepare(self.db)
        self.assertEqual(api.apply(self.db, task, self.payload(task))['status'], 'committed')
        self.event(2, 'Manual deployment restriction is resolved.')
        task = api.prepare(self.db)
        self.assertEqual(task['related'][0]['memory_key'], 'project.demo.constraint.manual-deploy')
        self.assertEqual(api.apply(self.db, task, self.payload(task, 'resolved', True))['status'], 'committed')
        with memory_sync.connect(self.db) as c:
            rows = c.execute('SELECT id,memory_key,status,content FROM memory_items').fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['status'], 'resolved')
            self.assertEqual(c.execute('SELECT count(*) FROM memory_sources').fetchone()[0], 2)
            self.assertEqual(c.execute("SELECT count(*) FROM summary_state WHERE consumer LIKE '%:event:%'").fetchone()[0], 2)
            self.assertNotIn('- [constraint]', c.execute('SELECT content FROM memory_summaries').fetchone()[0])
            self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(api.prepare(self.db)['events'], [])

    def test_legacy_coverage_freezes_present_rows_not_late_low_or_peer_ids(self):
        api = self.api()
        peer = memory_sync.RANGES['windows'][0]
        self.event(peer, 'Peer is pending.', project=None)
        with memory_sync.connect(self.db) as c, c:
            c.execute("INSERT INTO summary_state(consumer,last_event_id) VALUES('hermes',100)")
        self.assertEqual(api.seed_coverage(self.db)['legacy_events'], 1)
        self.event(2, 'Late low ID is still pending.')
        api.seed_coverage(self.db)
        self.assertEqual([e['id'] for e in api.prepare(self.db)['events']], [2])
        task = api.prepare(self.db)
        api.apply(self.db, task, dict(items=[], reviewed_event_ids=[2], conflicts=[]))
        self.assertEqual([e['id'] for e in api.prepare(self.db)['events']], [peer])
        task = api.prepare(self.db)
        api.apply(self.db, task, dict(items=[], reviewed_event_ids=[peer], conflicts=[]))
        self.event(3, 'Late lower ID after peer highwater.')
        self.assertEqual([e['id'] for e in api.prepare(self.db)['events']], [3])

    def test_tampered_quotes_secrets_conflicts_and_changed_evidence_fail_closed(self):
        api = self.api()
        task = api.prepare(self.db)
        for mode in ('quote', 'secret', 'conflict', 'coverage'):
            payload = self.payload(task)
            if mode == 'quote':
                payload['items'][0]['evidence'][0]['quote'] = 'invented'
            elif mode == 'secret':
                payload['items'][0]['content'] = 'api_key=abcdefghijklmnop1234567890'
            elif mode == 'conflict':
                payload['conflicts'] = ['contradiction']
            else:
                payload['reviewed_event_ids'] = []
            with self.assertRaises(ValueError):
                api.apply(self.db, task, payload)
        with memory_sync.connect(self.db) as c, c:
            self.assertEqual(c.execute('SELECT count(*) FROM summary_state').fetchone()[0], 0)
            self.assertEqual(c.execute('SELECT count(*) FROM memory_sources').fetchone()[0], 0)
            c.execute("UPDATE observation_events SET content='Changed evidence.' WHERE id=1")
        self.assertEqual(api.apply(self.db, task, self.payload(task))['status'], 'stale')

    def test_runner_overlap_is_blocked_and_metrics_account_real_boundary(self):
        api = self.api()
        metrics_path = self.db.with_suffix('.jsonl')
        def extractor(request, *, model):
            with self.assertRaises(ValueError):
                api.run_once(self.db, extractor=extractor)
            task = api.prepare(self.db)
            return dict(payload=self.payload(task), provider='synthetic-test', model=model, api_calls=1,
                        usage=dict(input_total_tokens=100, input_uncached_tokens=80, cache_read_tokens=20,
                                   output_tokens=30, reasoning_tokens=5, actual_cost_usd=None))
        result = api.run_once(self.db, extractor=extractor, metrics_path=metrics_path)
        self.assertEqual(result['status'], 'committed')
        row = json.loads(metrics_path.read_text())
        self.assertEqual(row['llm_api_calls'], 1)
        self.assertEqual(row['input_total_tokens'], 100)
        self.assertEqual(row['context_max_input_tokens'], 100)
        self.assertEqual(row['actual_cost_usd'], None)
        self.assertEqual(row['events_processed'], 1)

    def test_existing_legacy_key_identity_is_preserved_on_correction(self):
        api = self.api()
        old_key = 'agentmesh/sqlite-summary-v1:old-batch:item:0'
        with memory_sync.connect(self.db) as c, c:
            c.execute('BEGIN IMMEDIATE')
            ident = memory_sync.allocate_id(c, 'memory_items')
            c.execute('''INSERT INTO memory_items(id,memory_key,kind,scope,scope_key,project,content,metadata)
                VALUES(?,?,'constraint','project','demo/subproject','demo','Old restriction.',?)''',
                (ident, old_key, json.dumps({'private_metadata': 'preserve'})))
            sid = memory_sync.allocate_id(c, 'memory_summaries')
            c.execute("INSERT INTO memory_summaries(id,scope,scope_key,version,content,metadata) VALUES(?,'project','demo/subproject',1,'Original prose.',?)",
                      (sid, json.dumps({'source_event_ids': [1]})))
        task = api.prepare(self.db)
        payload = self.payload(task, 'resolved', True)
        payload['items'][0]['memory_key'] = old_key
        api.apply(self.db, task, payload)
        with memory_sync.connect(self.db) as c:
            row = c.execute('SELECT * FROM memory_items WHERE id=?', (ident,)).fetchone()
            self.assertEqual(row['scope_key'], 'demo/subproject')
            self.assertEqual(json.loads(row['metadata'])['private_metadata'], 'preserve')
            summary = c.execute('SELECT * FROM memory_summaries WHERE id=?', (sid,)).fetchone()
            self.assertTrue(summary['content'].startswith('Original prose.'))
            self.assertEqual(json.loads(summary['metadata'])['source_event_ids'], [1])

    def test_failed_fallback_usage_is_unknown_not_partial(self):
        api = self.api()
        metrics = self.db.with_suffix('.failed.jsonl')
        def extractor(request, *, model):
            if model == 'fallback':
                raise RuntimeError('simulated boundary failure')
            return dict(payload=dict(items=[], reviewed_event_ids=[1], conflicts=['explicit']),
                        provider='synthetic-test', model=model, api_calls=1,
                        usage=dict(input_total_tokens=100, input_uncached_tokens=80, cache_read_tokens=20, output_tokens=30, reasoning_tokens=5))
        with self.assertRaises(RuntimeError):
            api.run_once(self.db, extractor=extractor, conflict_model='fallback', metrics_path=metrics)
        row = json.loads(metrics.read_text())
        self.assertIsNone(row['llm_api_calls'])
        self.assertIsNone(row['input_total_tokens'])
        self.assertIsNone(row['context_max_input_tokens'])
        self.assertEqual(row['events_processed'], 0)
        self.assertEqual(api.prepare(self.db)['events'][0]['id'], 1)

    def test_atomic_coverage_failure_rolls_back_items_evidence_summary_and_ids(self):
        api = self.api()
        task = api.prepare(self.db)
        with memory_sync.connect(self.db) as c, c:
            c.execute("CREATE TRIGGER reject_receipt BEFORE INSERT ON summary_state BEGIN SELECT RAISE(ABORT,'synthetic receipt failure'); END")
            before = [tuple(r) for r in c.execute('SELECT * FROM _sync_counters')]
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            api.apply(self.db, task, self.payload(task))
        with memory_sync.connect(self.db) as c:
            for table in ('memory_items','memory_sources','memory_summaries','summary_state'):
                self.assertEqual(c.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)
            self.assertEqual([tuple(r) for r in c.execute('SELECT * FROM _sync_counters')], before)

    def test_correction_retains_prior_batch_provenance_if_receipt_is_missing(self):
        api = self.api()
        task = api.prepare(self.db)
        api.apply(self.db, task, self.payload(task))
        self.event(2, 'Deployment restriction resolved.')
        task = api.prepare(self.db)
        api.apply(self.db, task, self.payload(task, 'resolved', True))
        with memory_sync.connect(self.db) as c, c:
            c.execute('DELETE FROM summary_state WHERE consumer=?', (task['consumer'] + ':event:1',))
        self.assertEqual(api.prepare(self.db)['events'], [])

    def test_evidence_boolean_id_is_not_an_integer_reference(self):
        api = self.api()
        task = api.prepare(self.db)
        payload = self.payload(task)
        payload['items'][0]['evidence'][0]['event_id'] = True
        with self.assertRaises(ValueError):
            api.apply(self.db, task, payload)

    def test_new_key_schema_constrains_namespace_before_provider_call(self):
        from bounded_protocol import build_request
        task = self.api().prepare(self.db)
        request = build_request(task['events'], task['related'])
        variants = request['text']['format']['schema']['properties']['items']['items']['anyOf']
        import re
        for variant in variants:
            props = variant['properties']
            scope = props['scope']['enum'][0]
            pattern = props['memory_key'].get('pattern')
            self.assertIsNotNone(pattern)
            key = 'global.constraint.rule' if scope == 'global' else scope + '.demo.constraint.rule'
            self.assertIsNotNone(re.fullmatch(pattern, key))
            self.assertIsNone(re.fullmatch(pattern, 'wrong.rule'))

    def test_invalid_conflict_payload_never_invokes_fallback(self):
        api = self.api()
        calls = []
        def extractor(request, *, model):
            calls.append(model)
            return dict(payload=dict(items=[], reviewed_event_ids=[1], conflicts=[True]),
                        provider='synthetic-test', model=model, api_calls=1, usage={})
        with self.assertRaises(ValueError):
            api.run_once(self.db, extractor=extractor, conflict_model='fallback')
        self.assertEqual(calls, ['gpt-6-luna'])

    def test_bounds_do_not_truncate_event_or_related_context(self):
        api = self.api()
        with self.assertRaises(ValueError):
            api.prepare(self.db, event_chars=1)
        api.apply(self.db, api.prepare(self.db), self.payload(api.prepare(self.db)))
        self.event(2, 'A correction.')
        with self.assertRaises(ValueError):
            api.prepare(self.db, related_chars=1)
