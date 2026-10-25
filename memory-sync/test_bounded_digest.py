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
        self.db = Path(self.temp.name).resolve() / 'memory.db'  # macOS TMPDIR sits under the /var symlink
        sqlite_memory.init_database(self.db)
        memory_sync.initialize(self.db, 'mac', '9a95369f-97c5-4b0e-bf41-f110ab1450a9')
        self.event(1, 'Keep deployment manual.')

    def api(self):
        self.assertIsNotNone(importlib.util.find_spec('bounded_digest'), 'bounded SQLite adapter missing')
        return importlib.import_module('bounded_digest')

    def event(self, ident, text, project='demo', session='session'):
        with memory_sync.connect(self.db) as c, c:
            c.execute('UPDATE _sync_config SET importing=1')
            c.execute('''INSERT INTO observation_events
                (id,event_key,source_path,source_session_id,source_event_id,project,role,kind,content,source_hash)
                VALUES(?,?,?,?,?,?,'user','user_request',?,?)''',
                (ident, 'event-' + str(ident), 'private/sample.jsonl', session, str(ident), project, text, 'hash-' + str(ident)))
            c.execute('UPDATE _sync_config SET importing=0')

    def payload(self, task, status='active', update=False):
        e = task['events'][0]
        item = dict(memory_key='project.demo.constraint.manual-deploy', content=e['content'], status=status,
                    confidence=1.0, source_event_ids=[e['id']], evidence=[dict(event_id=e['id'], quote=e['content'])])
        if not update:
            item.update(kind='constraint', scope='project', scope_key='demo', project='demo')
        return dict(items=[item], reviewed_event_ids=[e['id'] for e in task['events']], conflicts=[])

    def assert_quarantined(self, task, payload, reason):
        result = self.api().apply(self.db, task, payload)
        self.assertEqual((result['status'], result['items'], result['rejected_items']), ('committed', 0, 1))
        self.assertEqual(self.api().list_quarantine(self.db)[-1]['reason'], reason)
        self.assertEqual(self.api().prepare(self.db)['events'], [])

    # Replays of the 2026-10-22 production failures (F1 and F3).
    def two_event_replay(self):
        self.event(2, 'Deployment must remain manual.')
        task = self.api().prepare(self.db)
        payload = self.payload(task)
        item = payload['items'][0]
        del item['source_event_ids']
        # Repeated quotes and reverse order exercise unique, sorted derivation.
        item['evidence'] = [dict(event_id=e['id'], quote=e['content'])
                            for e in reversed(task['events'])]
        item['evidence'].append(dict(item['evidence'][0]))
        return task, payload

    def test_production_20261022_f1_derived_sources_commit(self):
        from bounded_protocol import validate
        import copy
        task, payload = self.two_event_replay()
        normalized = validate(copy.deepcopy(payload), task['events'], task['related'])
        self.assertEqual(normalized['items'][0]['source_event_ids'], [1, 2])
        self.assertEqual(self.api().apply(self.db, task, payload)['status'], 'committed')
        with memory_sync.connect(self.db) as c:
            self.assertEqual([r[0] for r in c.execute('SELECT event_id FROM memory_sources ORDER BY event_id')], [1, 2])

    def test_production_20261022_f1_legacy_mismatch_rejected(self):
        task, payload = self.two_event_replay()
        payload['items'][0]['source_event_ids'] = [1]
        self.assert_quarantined(task, payload, 'evidence coverage mismatch')

    def test_production_20261022_f1_empty_evidence_rejected(self):
        task, payload = self.two_event_replay()
        payload['items'][0]['evidence'] = []
        self.assert_quarantined(task, payload, 'unsupported evidence ids')

    def test_production_20261022_f1_schema_and_instructions(self):
        from bounded_protocol import build_request, INSTRUCTIONS
        task = self.api().prepare(self.db)
        event = dict(task['events'][0], task_ref='DEMO-1')
        related = [self.payload(task)['items'][0]]
        request = build_request([event], related)
        schema = request['text']['format']['schema']
        variants = schema['properties']['items']['items']['anyOf']
        self.assertEqual(len(variants), 5)  # Update plus all four new scopes.
        self.assertNotIn('source_event_ids', json.dumps(schema))
        self.assertNotIn('source_event_ids', INSTRUCTIONS)

    def identity_replay(self, changes, *, update=False):
        api = self.api()
        task = api.prepare(self.db)
        api.apply(self.db, task, self.payload(task))
        self.event(2, 'Keep deployment manual after review.')
        task = api.prepare(self.db)
        payload = self.payload(task, update=update)
        del payload['items'][0]['source_event_ids']
        payload['items'][0].update(changes)
        self.assertEqual(api.apply(self.db, task, payload)['status'], 'committed')
        with memory_sync.connect(self.db) as c:
            rows = c.execute('SELECT * FROM memory_items').fetchall()
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(tuple(row[f] for f in ('kind', 'scope', 'scope_key', 'project')),
                             ('constraint', 'project', 'demo', 'demo'))
            self.assertEqual(row['content'], 'Keep deployment manual after review.')
            metadata = json.loads(row['metadata'])
            if changes:
                self.assertEqual(metadata['identity_from_existing'], sorted(changes))
            else:
                self.assertNotIn('identity_from_existing', metadata)
        return api

    def test_production_20261022_f3_kind_override(self):
        self.identity_replay({'kind': 'decision'})

    def test_production_20261022_f3_scope_override(self):
        self.identity_replay({'scope': 'session', 'scope_key': 'other-session'})

    def test_production_20261022_f3_project_override(self):
        self.identity_replay({'project': 'other-project'})

    def test_production_20261022_f3_update_shape_has_no_override_metadata(self):
        self.identity_replay({}, update=True)

    def test_production_20261022_f3_matching_identity_has_no_override_metadata(self):
        self.identity_replay({})

    def test_production_20261022_f3_audit_describes_current_update(self):
        api = self.identity_replay({'kind': 'decision'})
        with memory_sync.connect(self.db) as c:
            old = c.execute('SELECT * FROM memory_items').fetchone()
            previous = json.loads(old['metadata'])
            old_updated = old['updated_at']
        self.event(3, 'Deployment is still manual.')
        task = api.prepare(self.db)
        api.apply(self.db, task, self.payload(task, update=True))
        with memory_sync.connect(self.db) as c:
            metadata = json.loads(c.execute('SELECT metadata FROM memory_items').fetchone()[0])
            self.assertNotIn('identity_from_existing', metadata)
            self.assertEqual(metadata['evidence_history'][-1], {
                'evidence': previous['evidence'],
                'batch_id': previous['batch_id'],
                'identity_from_existing': ['kind'],
                'content': 'Keep deployment manual after review.',
                'status': 'active', 'updated_at': old_updated,
            })
            self.assertNotIn('identity_from_existing', metadata['evidence_history'][0])

    def new_key_with_related(self):
        api = self.api()
        task = api.prepare(self.db)
        api.apply(self.db, task, self.payload(task))
        with memory_sync.connect(self.db) as c:
            original = dict(c.execute('SELECT * FROM memory_items').fetchone())
        self.event(2, 'Prefer concise answers.')
        task = api.prepare(self.db)
        self.assertEqual([item['memory_key'] for item in task['related']], [original['memory_key']])
        payload = self.payload(task)
        item = payload['items'][0]
        item.update(memory_key='global.preference.concise-answers', kind='preference',
                    scope='global', scope_key=None, project=None)
        del item['source_event_ids']
        return api, task, payload, original

    def test_new_key_with_related_preserves_own_identity(self):
        api, task, payload, original = self.new_key_with_related()
        self.assertEqual(api.apply(self.db, task, payload)['status'], 'committed')
        with memory_sync.connect(self.db) as c:
            rows = {row['memory_key']: dict(row) for row in c.execute('SELECT * FROM memory_items')}
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[original['memory_key']], original)
            new = rows[payload['items'][0]['memory_key']]
            self.assertEqual(tuple(new[f] for f in ('kind', 'scope', 'scope_key', 'project')),
                             ('preference', 'global', None, None))
            self.assertNotIn('identity_from_existing', json.loads(new['metadata']))
            self.assertEqual([tuple(row) for row in c.execute(
                'SELECT memory_id,event_id FROM memory_sources ORDER BY event_id')],
                [(original['id'], 1), (new['id'], 2)])

    def test_new_key_with_related_rejects_wrong_namespace(self):
        api, task, payload, original = self.new_key_with_related()
        payload['items'][0]['memory_key'] = 'project.demo.preference.concise-answers'
        self.assert_quarantined(task, payload, 'new stable key namespace mismatch')
        with memory_sync.connect(self.db) as c:
            self.assertEqual([dict(row) for row in c.execute('SELECT * FROM memory_items')], [original])

    def test_production_20261022_audit_cannot_be_injected_by_producer(self):
        api = self.api()
        task = api.prepare(self.db)
        for target in ('payload', 'item'):
            payload = self.payload(task)
            obj = payload if target == 'payload' else payload['items'][0]
            obj['identity_from_existing'] = ['kind']
            if target == 'payload':
                with self.assertRaisesRegex(ValueError, 'invalid payload schema'):
                    api.apply(self.db, task, payload)
            else:
                self.assert_quarantined(task, payload, 'invalid item schema')

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

    def test_legacy_coverage_reads_the_renamed_checkpoint(self):
        api = self.api()
        self.event(2, 'Covered by the legacy checkpoint.')
        with memory_sync.connect(self.db) as c, c:
            c.execute("INSERT INTO summary_state(consumer,last_event_id) VALUES('agentmesh:legacy-pg-checkpoint',100)")
        self.assertEqual(api.seed_coverage(self.db)['cutoff'], 100)
        self.assertEqual(api.prepare(self.db)['events'], [])

    def test_tampered_quotes_secrets_conflicts_and_changed_evidence_fail_closed(self):
        api = self.api()
        task = api.prepare(self.db)
        for mode in ('quote', 'secret', 'conflict', 'coverage'):
            self.setUp()
            task = api.prepare(self.db)
            payload = self.payload(task)
            if mode == 'quote':
                payload['items'][0]['evidence'][0]['quote'] = 'invented'
            elif mode == 'secret':
                payload['items'][0]['content'] = 'api_key=abcdefghijklmnop1234567890'
            elif mode == 'conflict':
                payload['conflicts'] = ['contradiction']
            else:
                payload['reviewed_event_ids'] = []
            if mode in ('quote', 'secret'):
                reason = 'unsupported evidence quote' if mode == 'quote' else 'possible secret detected; batch remains pending'
                self.assert_quarantined(task, payload, reason)
            else:
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

    def test_reported_cost_is_summed_across_fallback_and_unknown_if_any_missing(self):
        api = self.api()
        for costs, expected in [((0.002, 0.03), 0.032), ((0.002, None), None)]:
            self.setUp()
            metrics = self.db.with_suffix('.cost.jsonl')
            def extractor(request, *, model):
                task = api.prepare(self.db)
                if model == 'fallback':
                    payload, cost = self.payload(task), costs[1]
                else:
                    payload, cost = dict(items=[], reviewed_event_ids=[1], conflicts=['explicit']), costs[0]
                return dict(payload=payload, provider='synthetic-test', model=model, api_calls=1,
                            usage=dict(input_total_tokens=100, output_tokens=30, actual_cost_usd=cost))
            api.run_once(self.db, extractor=extractor, conflict_model='fallback', metrics_path=metrics)
            row = json.loads(metrics.read_text())
            self.assertEqual(row['fallbacks'], 1)
            self.assertEqual(row['actual_cost_usd'], expected)

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
        self.assert_quarantined(task, payload, 'invalid evidence schema')

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
        # Invalid output gets one same-model retry, never the conflict model.
        self.assertEqual(calls, ['gpt-6-luna', 'gpt-6-luna'])

    def test_invalid_output_is_retried_once_with_same_model(self):
        api = self.api()
        metrics = self.db.with_suffix('.retry.jsonl')
        calls = []
        def extractor(request, *, model):
            calls.append(model)
            payload = self.payload(api.prepare(self.db))
            if len(calls) == 1:
                payload['reviewed_event_ids'] = []  # batch-level coverage error
            return dict(payload=payload, provider='synthetic-test', model=model, api_calls=1,
                        usage=dict(input_total_tokens=10, output_tokens=2, actual_cost_usd=0.001))
        result = api.run_once(self.db, extractor=extractor, conflict_model='fallback', metrics_path=metrics)
        self.assertEqual(result['status'], 'committed')
        self.assertEqual(calls, ['gpt-6-luna', 'gpt-6-luna'])
        row = json.loads(metrics.read_text())
        self.assertEqual((row['retries'], row['fallbacks'], row['llm_api_calls'], row['actual_cost_usd']), (1, 0, 2, 0.002))

    def test_second_invalid_output_fails_without_more_calls(self):
        api = self.api()
        metrics = self.db.with_suffix('.retry-fail.jsonl')
        calls = []
        def extractor(request, *, model):
            calls.append(model)
            payload = self.payload(api.prepare(self.db))
            payload['reviewed_event_ids'] = []
            return dict(payload=payload, provider='synthetic-test', model=model, api_calls=1, usage={})
        with self.assertRaises(ValueError):
            api.run_once(self.db, extractor=extractor, conflict_model='fallback', metrics_path=metrics)
        self.assertEqual(len(calls), 2)
        row = json.loads(metrics.read_text())
        self.assertEqual((row['retries'], row['error_detail']), (1, 'incomplete event coverage'))
        self.assertEqual(api.prepare(self.db)['events'][0]['id'], 1)

    def test_invalid_conflict_model_output_is_retried_once(self):
        api = self.api()
        metrics = self.db.with_suffix('.fallback-retry.jsonl')
        calls = []
        def extractor(request, *, model):
            calls.append(model)
            task = api.prepare(self.db)
            if model != 'fallback':
                payload = dict(items=[], reviewed_event_ids=[1], conflicts=['explicit'])
            else:
                payload = self.payload(task)
                if calls.count('fallback') == 1:
                    payload['reviewed_event_ids'] = []
            return dict(payload=payload, provider='synthetic-test', model=model, api_calls=1, usage={})
        result = api.run_once(self.db, extractor=extractor, conflict_model='fallback', metrics_path=metrics)
        self.assertEqual(result['status'], 'committed')
        self.assertEqual(calls, ['gpt-6-luna', 'fallback', 'fallback'])
        row = json.loads(metrics.read_text())
        self.assertEqual((row['retries'], row['fallbacks']), (1, 1))

    def test_bounds_do_not_truncate_event_or_related_context(self):
        api = self.api()
        with self.assertRaises(ValueError):
            api.prepare(self.db, event_chars=1)
        api.apply(self.db, api.prepare(self.db), self.payload(api.prepare(self.db)))
        self.event(2, 'A correction.')
        with self.assertRaises(ValueError):
            api.prepare(self.db, related_chars=1)

    def session_item(self, task, session):
        e = task['events'][0]
        return dict(items=[dict(memory_key='session.demo.open_loop.deploy-check', kind='open_loop', scope='session',
                                scope_key=session, project='demo', content=e['content'], status='active', confidence=1.0,
                                source_event_ids=[e['id']], evidence=[dict(event_id=e['id'], quote=e['content'])])],
                    reviewed_event_ids=[e['id'] for e in task['events']], conflicts=[])

    def test_session_items_of_other_sessions_are_not_offered_for_update(self):
        # A Codex session in the same worktree project must not be asked to update
        # a Claude session's item: its identity can never hold the new evidence.
        api = self.api()
        with memory_sync.connect(self.db) as c, c:
            c.execute("UPDATE observation_events SET source_session_id='claude-a' WHERE id=1")
        task = api.prepare(self.db)
        self.assertEqual(api.apply(self.db, task, self.session_item(task, 'claude-a'))['status'], 'committed')
        self.event(2, 'Codex checked the deployment.', session='codex-b')
        task = api.prepare(self.db)
        self.assertEqual(task['related'], [])
        anyof = task['request']['text']['format']['schema']['properties']['items']['items']['anyOf']
        self.assertFalse(any('session.demo.open_loop.deploy-check' in json.dumps(v) for v in anyof))
        api.apply(self.db, task, dict(items=[], reviewed_event_ids=[2], conflicts=[]))
        self.event(3, 'The deployment check is done.', session='claude-a')
        task = api.prepare(self.db)
        self.assertEqual([r['memory_key'] for r in task['related']], ['session.demo.open_loop.deploy-check'])
        payload = self.session_item(task, 'claude-a')
        for field in ('kind', 'scope', 'scope_key', 'project'):
            del payload['items'][0][field]
        payload['items'][0]['status'] = 'resolved'
        self.assertEqual(api.apply(self.db, task, payload)['status'], 'committed')

    def test_project_and_global_items_stay_in_context_across_sessions(self):
        api = self.api()
        task = api.prepare(self.db)
        api.apply(self.db, task, self.payload(task))
        self.event(2, 'Deployment is still manual.', session='codex-b')
        self.assertEqual([r['memory_key'] for r in api.prepare(self.db)['related']],
                         ['project.demo.constraint.manual-deploy'])

    def test_new_key_colliding_with_hidden_session_item_is_quarantined(self):
        api = self.api()
        with memory_sync.connect(self.db) as c, c:
            c.execute("UPDATE observation_events SET source_session_id='claude-a' WHERE id=1")
        task = api.prepare(self.db)
        api.apply(self.db, task, self.session_item(task, 'claude-a'))
        self.event(2, 'Codex checked the deployment.', session='codex-b')
        requests = []
        def extractor(request, *, model):
            requests.append(request)
            payload = self.session_item(api.prepare(self.db), 'codex-b')
            if len(requests) == 2:
                payload['items'][0]['memory_key'] = 'session.demo.open_loop.codex-deploy-check'
            return dict(payload=payload, provider='synthetic-test', model=model, api_calls=1, usage={})
        self.assertEqual(api.run_once(self.db, extractor=extractor)['status'], 'committed')
        self.assertEqual(len(requests), 1)
        self.assertEqual(api.list_quarantine(self.db)[0]['reason'], 'stable key exists outside submitted context')

    def test_retry_tells_the_model_which_validation_failed(self):
        api = self.api()
        requests = []
        def extractor(request, *, model):
            requests.append(request)
            payload = self.payload(api.prepare(self.db))
            if len(requests) == 1:
                payload['reviewed_event_ids'] = []
            return dict(payload=payload, provider='synthetic-test', model=model, api_calls=1, usage={})
        result = api.run_once(self.db, extractor=extractor)
        self.assertEqual(result['status'], 'committed')
        first, second = requests
        self.assertNotIn('rejected', first['instructions'])
        self.assertTrue(second['instructions'].startswith(first['instructions']))
        self.assertIn('incomplete event coverage', second['instructions'][len(first['instructions']):])
        self.assertEqual((second['input'], second['text']), (first['input'], first['text']))
        self.assertEqual(result['task']['request'], first)

    # Replays of production failure classes F2/F4; F5 lifecycle regressions.
    def test_replay_f2_session_key_derived_and_matching_not_recorded(self):
        for supplied, derived in [('wrong-session', True), ('session', False)]:
            self.setUp()
            task = self.api().prepare(self.db)
            self.api().apply(self.db, task, self.session_item(task, supplied))
            with memory_sync.connect(self.db) as c:
                row = c.execute('SELECT * FROM memory_items').fetchone()
                self.assertEqual(row['scope_key'], 'session')
                self.assertEqual('scope_key_derived' in json.loads(row['metadata']), derived)

    def test_replay_f2_spans_or_missing_sessions_quarantined(self):
        for session in ('other', None, ''):
            self.setUp()
            self.event(2, 'Keep it manual.', session=session)
            task = self.api().prepare(self.db)
            payload = self.session_item(task, 'session')
            item = payload['items'][0]
            del item['source_event_ids']
            item['evidence'] = [dict(event_id=e['id'], quote=e['content']) for e in task['events']]
            self.assert_quarantined(task, payload, 'session scope spans sessions')

    def test_replay_f4_mixed_batch_commits_without_retry_and_no_secret_stored(self):
        import copy
        api = self.api()
        task = api.prepare(self.db)
        payload = self.payload(task)
        bad = copy.deepcopy(payload['items'][0])
        bad['memory_key'] += '-bad'
        secret = 'sk-' + 'a' * 32
        bad['content'] = secret
        bad['evidence'].append(dict(event_id=999, quote='private rejected quote'))
        bad['source_event_ids'] = [1, 999, True]
        payload['items'].append(bad)
        metrics = self.db.with_suffix('.jsonl')
        calls = []
        def extractor(request, *, model):
            calls.append(model)
            return dict(payload=payload, provider='synthetic-test', model=model, api_calls=1, usage={})
        result = api.run_once(self.db, extractor=extractor, metrics_path=metrics)
        self.assertEqual((result['status'], result['items'], result['rejected_items']), ('committed', 1, 1))
        row = api.list_quarantine(self.db)[0]
        self.assertEqual(row['reason'], 'possible secret detected; batch remains pending')
        self.assertEqual(json.loads(row['event_ids']), [1])
        self.assertEqual((row['consumer'], row['batch_id']), (task['consumer'], task['batch_id']))
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads(metrics.read_text())['rejected_items'], 1)
        self.assertEqual(api.prepare(self.db)['events'], [])
        with memory_sync.connect(self.db) as c:
            dump = '\n'.join(c.iterdump())
            self.assertNotIn(secret, dump)
            self.assertNotIn('private rejected quote', dump)
        self.assertNotIn(secret.encode(), self.db.read_bytes())

    def test_f4_malformed_items_and_duplicates(self):
        import copy
        for bad in (None, {}, dict(memory_key=17), dict(memory_key=[])):
            self.setUp()
            task = self.api().prepare(self.db)
            payload = dict(items=[bad], reviewed_event_ids=[1], conflicts=[])
            self.assert_quarantined(task, payload, 'invalid item schema')
            self.assertIsNone(self.api().list_quarantine(self.db)[0]['memory_key'])
        self.setUp()
        task = self.api().prepare(self.db)
        payload = self.payload(task)
        payload['items'].append(copy.deepcopy(payload['items'][0]))
        payload['items'][0]['content'] = ''
        with self.assertRaisesRegex(ValueError, 'duplicate stable key'):
            self.api().apply(self.db, task, payload)
        self.assertEqual(len(self.api().prepare(self.db)['events']), 1)
        self.assertEqual(self.api().list_quarantine(self.db), [])

    def test_f4_quarantine_and_coverage_roll_back_together(self):
        import sqlite3
        api = self.api()
        task = api.prepare(self.db)
        payload = self.payload(task)
        payload['items'][0]['content'] = ''
        with memory_sync.connect(self.db) as c, c:
            c.execute("CREATE TRIGGER reject_receipt BEFORE INSERT ON summary_state BEGIN SELECT RAISE(ABORT,'synthetic receipt failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            api.apply(self.db, task, payload)
        self.assertEqual(api.list_quarantine(self.db), [])
        self.assertEqual(len(api.prepare(self.db)['events']), 1)

    def test_f4_dry_run_reports_rejections_without_writing(self):
        api = self.api()
        payload = self.payload(api.prepare(self.db))
        payload['items'][0]['content'] = ''
        def extractor(request, *, model):
            return dict(payload=payload, provider='synthetic-test', model=model, api_calls=1, usage={})
        result = api.run_once(self.db, extractor=extractor, apply_result=False)
        self.assertEqual((result['status'], result['items'], result['rejected_items']), ('validated', 0, 1))
        self.assertEqual(api.list_quarantine(self.db), [])
        self.assertEqual(len(api.prepare(self.db)['events']), 1)

    def test_f5_history_without_prior_evidence_is_bounded(self):
        from bounded_digest import revision_history
        old = dict(content='x' * 4000, status='active', updated_at='old-time')
        self.assertEqual(revision_history(old, {}, 'new', 'resolved'),
                         [dict(content='x' * 3000, status='active', updated_at='old-time', batch_id=None)])

    def deletion_proposal(self):
        api = self.api()
        task = api.prepare(self.db)
        api.apply(self.db, task, self.payload(task))
        with memory_sync.connect(self.db) as c:
            old = dict(c.execute('SELECT * FROM memory_items').fetchone())
        self.event(2, 'Remove this restriction.')
        task = api.prepare(self.db)
        self.assert_quarantined(task, self.payload(task, 'deleted', True), 'deletion requires confirmation')
        with memory_sync.connect(self.db) as c:
            self.assertEqual(dict(c.execute('SELECT * FROM memory_items').fetchone()), old)
        return api, old, api.list_quarantine(self.db)[0]

    def test_f5_confirm_deletion_cli_preserves_history_and_notifies(self):
        from unittest.mock import patch
        api, old, quarantine = self.deletion_proposal()
        with patch('profile_log_bridge.notify_rows') as notify:
            self.assertEqual(api.main(['confirm-deletion', '--db', str(self.db), str(quarantine['id'])]), 0)
            notify.assert_called_once_with(str(self.db), [('memory_items', old['id'])])
        with memory_sync.connect(self.db) as c:
            row = c.execute('SELECT * FROM memory_items').fetchone()
            self.assertEqual(row['status'], 'deleted')
            revision = json.loads(row['metadata'])['evidence_history'][-1]
            for field in ('content', 'status', 'updated_at'):
                self.assertEqual(revision[field], old[field])
            q = c.execute('SELECT * FROM memory_quarantine').fetchone()
            self.assertEqual(q['resolution'], 'confirmed')
            self.assertIsNotNone(q['resolved_at'])
        self.assertEqual(api.list_quarantine(self.db), [])
        self.assertEqual(api.main(['confirm-deletion', '--db', str(self.db), str(quarantine['id'])]), 1)

    def test_f5_confirm_deletion_refuses_non_deletion_rows(self):
        from unittest.mock import patch
        api, old, quarantine = self.deletion_proposal()
        with memory_sync.connect(self.db) as c, c:
            c.execute("UPDATE memory_quarantine SET reason='unsupported evidence quote' WHERE id=?", (quarantine['id'],))
        with patch('profile_log_bridge.notify_rows') as notify:
            self.assertEqual(api.main(['confirm-deletion', '--db', str(self.db), str(quarantine['id'])]), 1)
            notify.assert_not_called()
        with memory_sync.connect(self.db) as c:
            self.assertEqual(dict(c.execute('SELECT * FROM memory_items').fetchone()), old)
            self.assertIsNone(c.execute('SELECT resolved_at FROM memory_quarantine').fetchone()[0])

    def test_f5_dismiss_cli_leaves_memory_unchanged(self):
        from unittest.mock import patch
        api, old, quarantine = self.deletion_proposal()
        self.assertEqual(api.main(['quarantine', '--db', str(self.db)]), 0)
        with patch('profile_log_bridge.notify_rows') as notify:
            self.assertEqual(api.main(['dismiss', '--db', str(self.db), str(quarantine['id'])]), 0)
            notify.assert_not_called()
        with memory_sync.connect(self.db) as c:
            self.assertEqual(dict(c.execute('SELECT * FROM memory_items').fetchone()), old)
            self.assertEqual(c.execute('SELECT resolution FROM memory_quarantine').fetchone()[0], 'dismissed')
        self.assertEqual(api.list_quarantine(self.db), [])

    def test_f5_supersede_history_keeps_previous_content_status(self):
        api = self.api()
        task = api.prepare(self.db)
        api.apply(self.db, task, self.payload(task))
        self.event(2, 'Use the new deployment procedure.')
        task = api.prepare(self.db)
        api.apply(self.db, task, self.payload(task, 'superseded', True))
        with memory_sync.connect(self.db) as c:
            row = c.execute('SELECT * FROM memory_items').fetchone()
            self.assertEqual(row['status'], 'superseded')
            history = json.loads(row['metadata'])['evidence_history']
            self.assertEqual(history[-1]['content'], 'Keep deployment manual.')
            self.assertEqual(history[-1]['status'], 'active')
            self.assertIn('updated_at', history[-1])
            self.assertNotIn('kind', history[-1])

    def worker(self, script):
        fake = Path(self.temp.name) / 'worker'
        fake.write_text('#!/bin/sh\n' + script + '\n')
        fake.chmod(0o755)
        return [str(fake)]

    def test_failure_status(self):
        import sys
        import job_notify
        import claude_digest_worker
        from bounded_protocol import ValidationError, error_detail
        api = self.api(); metrics = self.db.with_suffix('.fail.jsonl')
        for marker, reason in [('401', 'claude auth failed'), ('secret source line 42', 'claude exit 7')]:
            fake = self.db.parent / 'claude'
            fake.write_text('#!/bin/sh\ncat >/dev/null\necho "' + marker + '" >&2\nexit 7\n', encoding='utf-8')
            fake.chmod(0o755)
            with self.assertRaises(ValidationError) as caught:
                api.run_once(self.db, model='m', metrics_path=metrics,
                    command=[sys.executable, claude_digest_worker.__file__, '--claude', str(fake)])
            detail = 'model worker failed: ' + reason + '; coverage unchanged'
            self.assertEqual(error_detail(caught.exception), detail)
            row = json.loads(metrics.read_text(encoding='utf-8').splitlines()[-1])
            self.assertEqual(row['error_detail'], detail)
            status = json.dumps(dict(status='blocked', detail=detail))
            code = job_notify.run('summary', [sys.executable, '-c',
                'import sys; print(' + repr(status) + ',file=sys.stderr); sys.exit(1)'], self.db.parent)
            self.assertEqual(code, 1)
            text = (self.db.parent / 'job-summary.json').read_text(encoding='utf-8')
            self.assertEqual(json.loads(text)['fields']['detail'], detail)
            self.assertNotIn('secret source line 42', text + json.dumps(row))

    def test_worker_failure_detail_keeps_only_a_safe_reason(self):
        from bounded_protocol import ValidationError, error_detail
        api = self.api()
        request = api.prepare(self.db)['request']
        cases = [
            ('cat >/dev/null; echo \'{"error_class":"ValueError","reason":"api_error 529"}\'; exit 1',
             'model worker failed: api_error 529; coverage unchanged'),
            ('cat >/dev/null; echo \'{"error_class":"ValueError","reason":"SECRET evidence!"}\'; exit 1',
             'model worker failed; coverage unchanged'),
            ('cat >/dev/null; echo SECRET; exit 1', 'model worker failed; coverage unchanged'),
            ('cat >/dev/null; echo \'{"payload":{}}\'', 'invalid model result envelope'),
            ('cat >/dev/null; echo not-json', 'model worker unavailable or invalid; coverage unchanged'),
        ]
        for script, detail in cases:
            with self.assertRaises(ValidationError) as caught:
                api.extract_command(request, model='m', command=self.worker(script))
            self.assertEqual(error_detail(caught.exception), detail)
        with self.assertRaises(ValidationError) as caught:
            api.extract_command(request, model='m', command=[str(Path(self.temp.name) / 'missing')])
        self.assertEqual(error_detail(caught.exception), 'model worker unavailable; coverage unchanged')
        with self.assertRaises(ValidationError) as caught:
            api.extract_command(request, model='m', command=self.worker('sleep 5'), timeout=0.2)
        self.assertEqual(error_detail(caught.exception), 'model worker timed out; coverage unchanged')


def test_error_detail_exposes_only_protocol_messages():
    from bounded_protocol import ValidationError, ConflictError, error_detail
    assert error_detail(ValidationError('unsupported evidence quote')) == 'unsupported evidence quote'
    assert error_detail(ConflictError('explicit model conflict')) == 'explicit model conflict'
    assert error_detail(RuntimeError('provider said: secret evidence')) is None


def test_run_lock_names_the_lock_and_waits_for_release(tmp_path):
    import threading
    import time
    import pytest
    from bounded_digest import run_lock
    from bounded_protocol import ValidationError
    target = tmp_path / 'root'
    release = threading.Event()
    held = threading.Event()
    def holder():
        with run_lock(target):
            held.set()
            release.wait(5)
    thread = threading.Thread(target=holder)
    thread.start()
    held.wait(5)
    with pytest.raises(ValidationError, match='overlap: export already running'):
        with run_lock(target, name='export'):
            pass
    threading.Timer(0.5, release.set).start()
    start = time.monotonic()
    with run_lock(target, wait=5, name='export'):
        waited = time.monotonic() - start
    thread.join()
    assert 0.3 < waited < 5
