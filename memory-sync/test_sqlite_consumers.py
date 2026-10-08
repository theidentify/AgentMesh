"""Portable consumer outcomes on disposable databases, never production."""
import importlib.util
import json
from pathlib import Path

import unittest
import test_bounded_digest as fixtures


class ConsumerTests(unittest.TestCase):
    db: Path
    setUp = fixtures.BoundedTests.setUp
    event = fixtures.BoundedTests.event
    api = fixtures.BoundedTests.api
    payload = fixtures.BoundedTests.payload
    def test_export_infers_transcript_tasks_even_without_structured_task_ref(self):
        from sqlite_export import publish
        self.event(2, 'DEMO-42 requires verified deployment.')
        root = self.db.parent / 'tasks-export'
        result = publish(self.db, root)
        self.assertEqual(result['tasks'], 1)
        self.assertIn('DEMO-42', next(root.glob('Tasks/*.md')).read_text())

    def test_export_resolution_of_last_active_item_clears_current_memory(self):
        from sqlite_export import publish, check_links, name
        api = self.api()
        task = api.prepare(self.db)
        api.apply(self.db, task, self.payload(task))
        root = self.db.parent / 'resolved-export'
        root.mkdir()
        manual = root / 'Manual.md'
        manual.write_text('# Manual note\nPreserve me.')
        publish(self.db, root)
        current = root / 'Memory' / (name('demo') + '.md')
        self.assertIn('Keep deployment manual.', current.read_text())
        self.event(2, 'Manual deployment restriction is resolved.')
        task = api.prepare(self.db)
        api.apply(self.db, task, self.payload(task, 'resolved', True))
        for _ in range(2):
            result = publish(self.db, root)
            self.assertEqual(result['items'], 0)
            self.assertNotIn('Keep deployment manual.', current.read_text())
            self.assertNotIn('## constraint', current.read_text())
            self.assertIn('demo Durable Memory', current.read_text())
            self.assertIn('OMP Memory/Memory/' + name('demo'), (root / '_Index.md').read_text())
            self.assertEqual(manual.read_text(), '# Manual note\nPreserve me.')
            self.assertEqual(check_links(root)['broken_links'], 0)
        current.write_text('# Manual collision\nDo not overwrite.')
        before = {str(p): p.read_bytes() for p in root.rglob('*.md')}
        with self.assertRaisesRegex(ValueError, 'manual projection note collision'):
            publish(self.db, root)
        self.assertEqual(before, {str(p): p.read_bytes() for p in root.rglob('*.md')})
        current.unlink()
        victim = self.db.parent / 'outside.md'
        victim.write_text('# Outside note\nDo not overwrite.')
        current.symlink_to(victim)
        with self.assertRaisesRegex(ValueError, 'symlink export target refused'):
            publish(self.db, root)
        self.assertEqual(victim.read_text(), '# Outside note\nDo not overwrite.')
        self.assertTrue(current.is_symlink())
        self.assertEqual(manual.read_text(), '# Manual note\nPreserve me.')

    def test_invalid_scheduler_configuration_cannot_persist_a_dead_window(self):
        from digest_scheduler import tick, read_state
        for options in ({'max_events':0}, {'related_chars':0}, {}):
            with self.assertRaises(ValueError):
                tick(self.db, root=self.db.parent/'export', **options)
            self.assertIsNone(read_state(self.db)['pending'])

    def test_scheduler_default_tick_cap_and_boot_idle(self):
        from digest_scheduler import tick
        for ident in range(2, 11):
            self.event(ident, 'Keep deployments manual.')
        calls = []
        def extractor(request, *, model):
            ids = [e['id'] for e in json.loads(request['input'][0]['content'])['events']]
            calls.append(ids)
            return dict(payload=dict(items=[],reviewed_event_ids=ids,conflicts=[]), provider='fixture', model=model, api_calls=1, usage={})
        options: dict = dict(root=self.db.parent/'export', extractor=extractor, exporter=lambda *a: {},
                       max_events=1, boot_id='boot-1', now='2026-10-08T10:00:00+00:00')
        self.assertEqual(tick(self.db, **options)['status'], 'pending')
        self.assertEqual(len(calls), 8)
        self.assertEqual(tick(self.db, **options)['status'], 'completed')
        self.assertEqual(len(calls), 10)
        options['boot_id'] = 'boot-2'
        self.assertEqual(tick(self.db, **options)['status'], 'completed')
        self.assertEqual(len(calls), 10)

    def test_reason_uses_fts_normalization_with_literal_query_safety(self):
        from recall_memory import recall
        self.event(2, 'The café has a constraint.')
        self.assertIn(2, [r['id'] for r in recall(self.db, 'cafe')['results']])
        self.assertEqual(recall(self.db, '" OR * NOT (')['results'], [])

    def test_reason_task_argument_and_bounded_summary_evidence(self):
        from recall_memory import recall
        api = self.api()
        task = api.prepare(self.db)
        api.apply(self.db, task, self.payload(task))
        result = recall(self.db, 'deployment', project='demo', task='DEMO-1')
        self.assertEqual(result['query_plan']['task_refs'], ('DEMO-1',))
        self.assertTrue(any(e.get('summary_id') and e['event_id']==1 for e in result['evidence']))

    def test_retention_is_non_destructive_and_counts_explicit_coverage(self):
        self.assertIsNotNone(importlib.util.find_spec('retention_audit'))
        from retention_audit import audit
        result = audit(self.db)
        self.assertFalse(result['retention_enabled'])
        self.assertEqual(result['uncovered_events'], 1)
        self.assertEqual(result['deletions'], 0)

    def test_workflow_bounded_selector_never_runs_legacy(self):
        import workflow
        with self.assertRaisesRegex(ValueError, 'export root'):
            workflow.summarize(self.db, dict(summarize=True, summary_backend='bounded', summary={}))

    def test_context_scope_coverage_and_readonly(self):
        self.assertIsNotNone(importlib.util.find_spec('shared_memory_context'), 'SQLite context entrypoint missing')
        from shared_memory_context import context
        self.event(2, 'Other project secret', project='other')
        with __import__('memory_sync').connect(self.db) as c, c:
            c.execute("INSERT INTO summary_state(consumer,last_event_id) VALUES('hermes',100)")
        result = context(self.db, project='demo')
        self.assertEqual([e['id'] for e in result['recent_unsummarized']], [1])
        self.assertEqual(result['items'], [])
        self.assertIn('# Shared Memory Context', result['markdown'])
        self.assertNotIn('Other project secret', result['markdown'])
        with __import__('recall_memory').open_readonly(self.db) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM summary_state').fetchone()[0], 1)

    def test_export_complete_collision_safe_manual_notes_and_source_anchors(self):
        self.assertIsNotNone(importlib.util.find_spec('sqlite_export'), 'SQLite deterministic exporter missing')
        from sqlite_export import publish, check_links
        api = self.api()
        task = api.prepare(self.db)
        api.apply(self.db, task, self.payload(task))
        self.event(2, 'Second source path.')
        with __import__('memory_sync').connect(self.db) as c, c:
            c.execute("UPDATE observation_events SET source_path='private/other/sample.jsonl' WHERE id=2")
        root = self.db.parent / 'projection'
        root.mkdir()
        manual = root / 'Manual.md'
        manual.write_text('# Manual note\nDo not change.')
        result = publish(self.db, root)
        self.assertEqual(result['events'], 2)
        self.assertEqual(result['sessions'], 2)
        self.assertEqual(check_links(root)['broken_links'], 0)
        files = {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*.md')}
        publish(self.db, root)
        self.assertEqual(files, {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*.md')})
        self.assertEqual(manual.read_text(), '# Manual note\nDo not change.')
        self.assertTrue(any('#^event-1' in b.decode() for b in files.values()))
        target = next(root.glob('Memory/*.md'))
        target.write_text('# Manual collision')
        before = {str(p):p.read_bytes() for p in root.rglob('*.md')}
        with self.assertRaises(ValueError):
            publish(self.db, root)
        self.assertEqual(before, {str(p):p.read_bytes() for p in root.rglob('*.md')})

    def test_fixed_window_backlog_restart_export_retry_and_late_arrivals(self):
        self.assertIsNotNone(importlib.util.find_spec('digest_scheduler'), 'fixed window coordinator missing')
        from digest_scheduler import tick, read_state
        self.event(1000000000000001, 'High peer ID in initial window.')
        calls = []
        fail = [True]
        def extractor(request, *, model):
            data = json.loads(request['input'][0]['content'])
            ids = [e['id'] for e in data['events']]
            calls.append(ids)
            return dict(payload=dict(items=[], reviewed_event_ids=ids, conflicts=[]),
                        provider='synthetic-test', model=model, api_calls=1, usage={})
        def export(db, root):
            if fail[0]:
                raise ValueError('export fixture failure')
            return dict(broken_links=0)
        options: dict = dict(extractor=extractor, exporter=export, root=self.db.parent / 'export',
                       now='2026-10-08T10:00:00+00:00', boot_id='boot-1', max_batches=1, max_events=1)
        self.assertEqual(tick(self.db, **options)['status'], 'pending')
        self.event(2, 'Late low ID must wait for the next window.')
        with self.assertRaises(ValueError):
            tick(self.db, **options)
        state = read_state(self.db)
        self.assertEqual(state['pending']['phase'], 'export')
        self.assertIsNone(state['success'])
        self.assertEqual(calls, [[1], [1000000000000001]])
        fail[0] = False
        self.assertEqual(tick(self.db, **options)['status'], 'completed')
        self.assertEqual(calls, [[1], [1000000000000001]])
        self.assertEqual(tick(self.db, **options)['status'], 'scheduled')
        options['now'] = '2026-10-09T10:00:00+00:00'
        self.assertEqual(tick(self.db, **options)['status'], 'completed')
        self.assertEqual(calls[-1], [2])

    def test_scheduler_failure_preserves_prior_batch_and_overlap_fencing(self):
        self.assertIsNotNone(importlib.util.find_spec('digest_scheduler'))
        from digest_scheduler import tick, read_state
        self.event(2, 'Next batch.')
        calls = []
        def extractor(request, *, model):
            with self.assertRaises(ValueError):
                tick(self.db, root=self.db.parent / 'export', force=True)
            ids = [e['id'] for e in json.loads(request['input'][0]['content'])['events']]
            calls.append(ids)
            if len(calls) == 2:
                raise ValueError('provider failure')
            return dict(payload=dict(items=[], reviewed_event_ids=ids, conflicts=[]), provider='fixture', model=model, api_calls=1, usage={})
        options: dict = dict(extractor=extractor, exporter=lambda *a: {}, root=self.db.parent / 'export',
                       now='2026-10-08T10:00:00+00:00', max_events=1, max_batches=2)
        with self.assertRaises(ValueError):
            tick(self.db, **options)
        self.assertIsNone(read_state(self.db)['success'])
        self.assertEqual([e['id'] for e in self.api().prepare(self.db)['events']], [2])
        self.assertEqual(tick(self.db, **options)['status'], 'completed')
        self.assertEqual(calls, [[1], [2], [2]])
