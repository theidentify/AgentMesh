"""Portable fixtures only: never write a configured runtime database."""
import json
import tempfile
import unittest
from test_isolation import setUpModule, tearDownModule
from pathlib import Path
import adapters


class InsightTests(unittest.TestCase):
    def test_redaction_handles_truncated_private_keys(self):
        value = '-----BEGIN PRIVATE KEY-----\nVERY_PRIVATE_BYTES\n'
        self.assertNotIn('VERY_PRIVATE_BYTES', adapters.redact(value))
        self.assertNotIn('sensitive', adapters.redact('{"password":"sensitive"}'))
        self.assertNotIn('secret123', adapters.redact('Authorization: Bearer secret123'))

    def test_metrics_corruption_is_fail_visible(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            (home/'jobs').mkdir()
            (home/'jobs/summary-metrics.jsonl').write_text('{invalid}')
            with self.assertRaises(json.JSONDecodeError):
                adapters.metrics(home)

    def test_metrics_are_standalone_and_sources_stay_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            archive = adapters.cron_archive(home)
            (archive / 'cron').mkdir(parents=True)
            (home / 'jobs').mkdir()
            (archive / 'cron/usage_audit.jsonl').write_text('')
            (archive / 'cron/jobs.json').write_text(json.dumps({'jobs':[{'id':'6fcf5646d34d','enabled':True,'no_agent':True,'last_status':'ok'}]}))
            (home / 'jobs/summary-metrics.jsonl').write_text(json.dumps({'record_type':'run','run_id':'production','status':'no_work','content':'PRIVATE','errors':['secret']})+'\n')
            (home / 'jobs/summary-smoke-test-metrics.jsonl').write_text(json.dumps({'record_type':'run','run_id':'isolated','status':'applied'})+'\n')
            data = adapters.metrics(home)
            self.assertEqual([r['run_id'] for r in data['runs']['bounded']], ['production'])
            self.assertEqual([r['run_id'] for r in data['runs']['isolated']], ['isolated'])
            self.assertIsNone(data['report']['new']['input_total_tokens']['total'])
            self.assertNotIn('PRIVATE', json.dumps(data))
            self.assertNotIn('secret', json.dumps(data))
            self.assertIsNone(data['cron']['window_pending'])
            self.assertFalse(data['cron']['idle_inferred'])
            sqlite_run = dict(record_type='run',run_id='sqlite-committed',status='committed',llm_api_calls=1,
                              input_total_tokens=100,input_uncached_tokens=None,actual_cost_usd=None)
            (home/'jobs/summary-metrics.jsonl').write_text(json.dumps(sqlite_run)+'\n')
            data = adapters.metrics(home)
            self.assertEqual(data['report']['new_successful_llm']['sample_count'], 1)
            self.assertEqual(data['report']['new_successful_llm']['input_total_tokens']['total'], 100)
            self.assertIsNone(data['report']['new_successful_llm']['input_uncached_tokens']['total'])
            self.assertIsNone(data['report']['new_successful_llm']['actual_cost_usd']['total'])


class OperationsTests(unittest.TestCase):
    def test_missing_sources_are_visible_and_never_online(self):
        with tempfile.TemporaryDirectory() as folder:
            data = adapters.operations(Path(folder))
            self.assertEqual(data['pipeline']['jobs'], [])
            self.assertFalse(data['summary']['available'])
            self.assertEqual(data['summary']['tools'], [])
            self.assertEqual(set(data), {'generated_at', 'refresh_seconds', 'pipeline', 'summary'})
            self.assertNotIn('online', json.dumps(data))

    def test_summary_projection_is_content_free(self):
        """The broker summary's own files: counts, states and times leave; paths, keys and project text do not."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            (root/'status').mkdir(); (root/'jobs').mkdir()
            for name in ('ingest','summary'):
                (root/'status'/('job-'+name+'.json')).write_text(json.dumps({'ok':True,'at':'2026-01-01T00:00:00Z','prompt':'secret-token','detail':'private'}))
            (root/'status/last-ingest.json').write_text(json.dumps(dict(backend='broker',found=3,processed=1,skipped=2,errors=0,invalid_json=0,
                error_classes=['secret-class'],observed_at='2026-01-01T00:00:00+00:00')))
            (root/'status/last-summary.json').write_text(json.dumps(dict(backend='broker',status='completed',events=4,batches=1,llm_api_calls=1,
                prompt='secret-prompt',observed_at='2026-01-01T00:00:00+00:00')))
            (root/'jobs/summary-state.json').write_text(json.dumps(dict(format='agentmesh-summary-state-v1',pending=False,start_at=None,
                success=dict(day='2026-01-01',completed_at='2026-01-01T00:00:00+00:00',boot_id='secret-boot'),quarantine=[{'memory_key':'secret-key'}])))
            (root/'jobs/summary-unmapped.json').write_text(json.dumps(dict(format='agentmesh-summary-unmapped-v1',at='t',projects=['learn-x','bad\x00name'])))
            transcript = root/'home/.claude/projects/-secret-project/s.jsonl'; transcript.parent.mkdir(parents=True); transcript.write_text('{}')
            (root/'jobs/transcript-cursors.json').write_text(json.dumps(dict(format='agentmesh-transcript-cursors-v1',files={
                str(transcript):{'offset':2}, str(root/'home/.codex/sessions/gone.jsonl'):{'offset':0}, '/elsewhere/x.jsonl':{}})))
            data = adapters.operations(root)
            s = data['summary']
            self.assertTrue(s['available'])
            self.assertEqual(s['ingest']['found'], 3); self.assertEqual(s['summary']['status'], 'completed')
            self.assertEqual(s['schedule'], dict(pending=False, last_completed_at='2026-01-01T00:00:00+00:00', quarantined=1))
            self.assertEqual(s['held_projects'], ['learn-x'])
            tools = {t['tool']: t for t in s['tools']}
            self.assertEqual((tools['claude']['transcripts'], tools['codex']['transcripts']), (1, 1))
            self.assertIsNotNone(tools['claude']['latest_at']); self.assertIsNone(tools['codex']['latest_at'])
            self.assertEqual(data['pipeline']['jobs'][1]['last_status'], 'ok')
            text = json.dumps(data)
            for secret in ('secret', 'private', str(root)): self.assertNotIn(secret, text)

    def test_unknown_summary_status_is_not_echoed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root/'status').mkdir()
            (root/'status/last-summary.json').write_text(json.dumps(dict(status='<script>', observed_at='not a time')))
            s = adapters.operations(root)['summary']['summary']
            self.assertEqual((s['status'], s['observed_at']), ('unknown', None))


class HTTPTests(unittest.TestCase):
    def test_http_guards_missing_backend_and_navigation(self):
        import server
        import threading
        import urllib.request
        import urllib.error
        with tempfile.TemporaryDirectory() as folder:
            app = server.make_server(0, agentmesh_home=Path(folder))
            thread = threading.Thread(target=app.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(app.server_close)
            self.addCleanup(app.shutdown)
            url = 'http://127.0.0.1:' + str(app.server_port)
            def request(path, method='GET', headers=None):
                try:
                    response = urllib.request.urlopen(urllib.request.Request(url+path, method=method, headers=headers or {}))
                except urllib.error.HTTPError as e:
                    response = e
                return response.status, response.read()
            self.assertEqual(request('/health')[0], 200)
            page = request('/')[1]
            self.assertIn(b'Pipeline', page); self.assertNotIn(b'Memory Browser', page)
            self.assertEqual(request('/metrics.html')[0], 200)
            # The SQLite and PostgreSQL readers are gone (SQLite retirement R5b).
            for retired in ('/api/memory', '/api/memory/items/1', '/api/connections'):
                self.assertEqual(request(retired)[0], 404)
            self.assertEqual(request('/api/operations?backend=sqlite')[0], 400)
            # Hermes cron files are optional now; absent logs give empty, not unavailable, metrics.
            self.assertEqual(request('/api/metrics')[0], 200)
            self.assertNotIn(folder.encode(), request('/api/metrics')[1])
            self.assertEqual(request('/api/operations')[0], 200)
            self.assertEqual(request('/../README.md')[0], 404)
            self.assertEqual(request('/health', headers={'Host':'evil.test'})[0], 403)
            self.assertEqual(request('/health', headers={'Origin':'https://evil.test'})[0], 403)
            for method in ('POST','PUT','PATCH','DELETE'):
                self.assertEqual(request('/api/operations', method=method)[0], 405)

    def test_sqlite_flag_is_accepted_and_ignored(self):
        """Installed plists still pass --sqlite; the server must start anyway."""
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as folder:
            process = subprocess.Popen([sys.executable, '-u', str(Path(__file__).with_name('server.py')), '--port', '0',
                '--agentmesh-home', folder, '--sqlite', str(Path(folder)/'absent.db')], stdout=subprocess.PIPE, text=True)
            try:
                line = json.loads(process.stdout.readline())
                self.assertTrue(line['readonly'] and line['url'].startswith('http://127.0.0.1:'))
            finally:
                process.terminate(); process.wait(10)

    def test_cache_expiry_and_sanitized_failure(self):
        import server
        value = [0]
        def load():
            value[0] += 1
            return {'sample': value[0]}
        cache = server.Cache(load, ttl=0)
        self.assertEqual(cache.get()['sample'], 1)
        self.assertEqual(cache.get()['sample'], 2)
        def fail():
            raise RuntimeError('SECRET /private/path')
        self.assertNotIn('SECRET', json.dumps(server.Cache(fail).get()))


if __name__ == '__main__':
    unittest.main()
