"""Portable fixtures only: never write a configured runtime database."""
import json
import sqlite3
import tempfile
import unittest
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
            (home/'cron').mkdir()
            (home/'cron/usage_audit.jsonl').write_text('{invalid}')
            with self.assertRaises(json.JSONDecodeError):
                adapters.metrics(home)

    def test_metrics_are_standalone_and_sources_stay_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            (home / 'cron').mkdir()
            (home / 'omp-memory').mkdir()
            with sqlite3.connect(home / 'state.db') as c:
                c.execute('CREATE TABLE sessions(id TEXT, started_at REAL)')
            (home / 'cron/usage_audit.jsonl').write_text('')
            (home / 'cron/jobs.json').write_text(json.dumps({'jobs':[{'id':'6fcf5646d34d','enabled':True,'no_agent':True,'last_status':'ok'}]}))
            (home / 'omp-memory/summary-metrics.jsonl').write_text(json.dumps({'record_type':'run','run_id':'production','status':'no_work','content':'PRIVATE','errors':['secret']})+'\n')
            (home / 'omp-memory/summary-smoke-test-metrics.jsonl').write_text(json.dumps({'record_type':'run','run_id':'isolated','status':'applied'})+'\n')
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
            (home/'omp-memory/summary-metrics.jsonl').write_text(json.dumps(sqlite_run)+'\n')
            data = adapters.metrics(home)
            self.assertEqual(data['report']['new_successful_llm']['sample_count'], 1)
            self.assertEqual(data['report']['new_successful_llm']['input_total_tokens']['total'], 100)
            self.assertIsNone(data['report']['new_successful_llm']['input_uncached_tokens']['total'])
            self.assertIsNone(data['report']['new_successful_llm']['actual_cost_usd']['total'])


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / 'memory.db'
        with sqlite3.connect(self.db) as c:
            c.executescript((Path(__file__).parent.parent / 'memory-sync/schema.sql').read_text())
            for n in range(3):
                c.execute("INSERT INTO memory_items(kind,scope,project,content) VALUES ('fact','project','demo',?)", ('remember api_key=sk-abcdef1234567890 '+str(n),))
            c.execute("INSERT INTO observation_events(event_key,source_path,kind,content,source_hash) VALUES ('ev','/private/path','user','password=hunter22','hash')")
            c.execute('INSERT INTO memory_sources VALUES (1,1)')
            c.execute("INSERT INTO memory_summaries(scope,scope_key,content,metadata) VALUES ('project','demo','summary','{\"source_event_ids\":[1]}')")
        self.addCleanup(self.temp.cleanup)

    def test_memory_pagination_redaction_evidence_and_readonly(self):
        reader = adapters.MemoryReader(sqlite=self.db)
        page = reader.browse('items', {'limit':'2','offset':'0','q':'remember'})
        self.assertEqual(page['total'], 3)
        self.assertEqual(len(page['rows']), 2)
        self.assertNotIn('sk-abcdef', json.dumps(page))
        self.assertNotIn('evidence', page['rows'][0])
        next_page = reader.browse('items', {'limit':'2','offset':'2'})
        self.assertEqual(len(next_page['rows']), 1)
        detail = reader.detail('items', 1)
        self.assertEqual(detail['evidence'][0]['id'], 1)
        self.assertNotIn('hunter22', json.dumps(detail))
        self.assertNotIn('/private/path', json.dumps(detail))
        self.assertEqual(reader.detail('summaries', 1)['evidence'][0]['id'], 1)
        with reader.connect() as c:
            with self.assertRaises(sqlite3.OperationalError):
                c.execute("DELETE FROM memory_items")

    def test_memory_query_allowlist_and_literal_search(self):
        reader = adapters.MemoryReader(sqlite=self.db)
        with self.assertRaises(ValueError):
            reader.browse('observation_events', {})
        with self.assertRaises(ValueError):
            reader.browse('items', {'sql':'DELETE FROM memory_items'})
        with self.assertRaises(ValueError):
            reader.browse('items', {'limit':'101'})
        self.assertEqual(reader.browse('items', {'q':"' OR 1=1 --"})['total'], 0)
        self.assertEqual(reader.browse('items', {'q':'%'})['total'], 0)
        self.assertEqual(reader.browse('summaries', {'project':'demo'})['total'], 1)


class OperationsTests(unittest.TestCase):
    def test_missing_sources_are_visible_and_never_online(self):
        with tempfile.TemporaryDirectory() as folder:
            data = adapters.operations(Path(folder), adapters.MemoryReader(), None)
            self.assertFalse(data['agents']['available'])
            self.assertFalse(data['pipeline']['available'])
            self.assertFalse(data['sync']['available'])
            self.assertEqual(data['sync']['transport'], 'unknown')
            self.assertNotIn('online', json.dumps(data))

    def test_operational_projection_hides_payloads_and_distinguishes_observations(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'cron').mkdir()
            (root/'cron/jobs.json').write_text(json.dumps({'jobs':[{'id':'6fcf5646d34d','enabled':True,'last_status':'ok','last_run_at':'2026-01-01T00:00:00Z','prompt':'secret-token','last_error':'private'}]}))
            db = root/'staged.db'
            with sqlite3.connect(db) as c:
                c.executescript((Path(__file__).parent.parent/'memory-sync/schema.sql').read_text())
                c.execute("INSERT INTO source_sessions(source_agent,source_path,last_seen_at) VALUES ('codex','secret-path','2026-01-01T00:00:00Z')")
                c.executescript("CREATE TABLE _sync_config(node TEXT, group_id TEXT); INSERT INTO _sync_config VALUES ('mac','SECRET'); CREATE TABLE _sync_receipts(uuid TEXT,checksum TEXT); CREATE TABLE _sync_outbox(uuid TEXT,published INTEGER,packet TEXT); CREATE TABLE _sync_diagnostics(path TEXT,kind TEXT,message TEXT); CREATE TABLE _agentmesh_worker_state(consumer TEXT,last_run REAL,lease_until REAL,owner TEXT);")
            data = adapters.operations(root, adapters.MemoryReader(sqlite=db), db)
            self.assertEqual(data['agents']['rows'][0]['state'], 'stale')
            self.assertEqual(data['pipeline']['jobs'][1]['last_status'], 'ok')
            self.assertEqual(data['sync']['node'], 'mac')
            self.assertEqual(data['sync']['transport'], 'unknown')
            self.assertNotIn('SECRET', json.dumps(data))
            self.assertNotIn('secret-', json.dumps(data))
            self.assertNotIn('private', json.dumps(data))


class HTTPTests(unittest.TestCase):
    def test_http_guards_missing_backend_and_navigation(self):
        import server
        import threading
        import urllib.request
        import urllib.error
        with tempfile.TemporaryDirectory() as folder:
            app = server.make_server(Path(folder), 0)
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
            self.assertIn(b'Memory Browser', request('/')[1])
            self.assertEqual(request('/metrics.html')[0], 200)
            self.assertEqual(request('/api/memory?backend=bad')[0], 400)
            self.assertEqual(request('/api/memory?sql=select')[0], 400)
            self.assertEqual(request('/api/memory')[0], 503)
            self.assertEqual(request('/api/metrics')[0], 503)
            self.assertNotIn(folder.encode(), request('/api/metrics')[1])
            self.assertEqual(request('/api/operations')[0], 200)
            self.assertEqual(request('/../README.md')[0], 404)
            self.assertEqual(request('/health', headers={'Host':'evil.test'})[0], 403)
            self.assertEqual(request('/health', headers={'Origin':'https://evil.test'})[0], 403)
            for method in ('POST','PUT','PATCH','DELETE'):
                self.assertEqual(request('/api/memory', method=method)[0], 405)

    def test_explicit_authority_does_not_promote_staging_by_default(self):
        import server
        import threading
        import urllib.request
        with tempfile.TemporaryDirectory() as folder:
            for primary, expected in ((None, 'PostgreSQL authority'), ('sqlite', 'SQLite authority')):
                app = server.make_server(Path(folder), 0, sqlite=Path(folder)/'absent.db', primary=primary)
                thread = threading.Thread(target=app.serve_forever, daemon=True)
                thread.start()
                try:
                    result = json.load(urllib.request.urlopen('http://127.0.0.1:'+str(app.server_port)+'/api/connections'))
                    self.assertEqual(next(r for r in result['rows'] if r['authority'])['label'], expected)
                    self.assertEqual(result['primary'], primary or 'postgres')
                finally:
                    app.shutdown()
                    app.server_close()
            with self.assertRaises(ValueError):
                server.make_server(Path(folder), 0, primary='invalid')

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
