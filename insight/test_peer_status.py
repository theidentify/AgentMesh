"""Read-only completed-cycle ACK projection fixtures."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
import adapters


class PeerStatusTests(unittest.TestCase):
    def test_completed_ack_projection_without_private_payloads(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            root = home/'omp-memory/sync/status'
            root.mkdir(parents=True)
            (root/'windows.json').write_text(json.dumps({
                'format':'agentmesh-status-v1','node':'windows','group':'PRIVATE',
                'updated_at':'2026-10-08T11:33:20Z',
                'counts':{'memory_items':119,'observation_events':40574,'summary_state':39863},
                'sync':{'received':78,'pending':0,'invalid':0,'conflict':0,'outbox':0,'group_id':'PRIVATE'},
                'cycle':{'receive':{'applied':2,'unavailable':False},'publish':{'published':3,'unavailable':False}},
                'workflow':{'ingestion':{'error':'PRIVATE/path/password'},'summary':{'status':'blocked','error':'PRIVATE'}},
                'postgres_mirror':None}))
            result = adapters.peer_status(home, now=datetime(2026,10,8,11,34,20,tzinfo=timezone.utc))
            mac, windows = result['rows']
            self.assertEqual(mac['availability'], 'missing')
            self.assertTrue(windows['available'])
            self.assertEqual(windows['freshness'], 'fresh')
            self.assertEqual(windows['age_seconds'], 60)
            self.assertEqual(windows['sync']['received'], 78)
            self.assertEqual(windows['cycle']['applied'], 2)
            self.assertEqual(windows['cycle']['published'], 3)
            self.assertTrue(windows['has_error'])
            self.assertEqual(windows['counts']['summary_state'],39863)
            self.assertNotIn('PRIVATE', json.dumps(result))
            self.assertNotIn('group_id', json.dumps(result))
            self.assertEqual(result['transport'], 'unknown')

    def test_malformed_stale_future_and_bounded_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            root = home/'omp-memory/sync/status'
            root.mkdir(parents=True)
            path = root/'windows.json'
            now = datetime(2026,10,8,12,tzinfo=timezone.utc)
            def read():
                return adapters.peer_status(home, now=now)['rows'][1]
            base = {'format':'agentmesh-status-v1','node':'windows',
                    'updated_at':'2026-10-08T11:00:00Z',
                    'sync':{'received':78,'pending':False,'invalid':-1,'conflict':'PRIVATE','outbox':2**54},
                    'counts':{'memory_items':119,'memory_sources':1.5},
                    'cycle':{'receive':{'applied':'PRIVATE'},'publish':{'published':0}},
                    'workflow':{'summary_sync':{'receive':{'applied':4},'publish':{'published':2}}}}
            path.write_text(json.dumps(base))
            row = read()
            self.assertEqual(row['freshness'],'stale')
            self.assertEqual(row['age_seconds'],3600)
            self.assertIsNone(row['sync']['pending'])
            self.assertIsNone(row['sync']['outbox'])
            self.assertIsNone(row['cycle']['applied'])
            self.assertIsNone(row['counts']['memory_sources'])
            self.assertIsNone(row['has_error'])
            self.assertTrue(row['partial'])
            self.assertEqual(row['cycle']['summary_applied'],4)
            for stamp, expected in [('2026-10-08T12:01:00Z','future timestamp'),
                                    ('PRIVATE','unknown'),('2026-10-08T11:00:00','unknown')]:
                path.write_text(json.dumps(dict(base,updated_at=stamp)))
                self.assertEqual(read()['freshness'], expected)
            for raw in ['{PRIVATE', '[]', json.dumps(dict(base,node='../../PRIVATE')),
                        ' ' * 65537, json.dumps(dict(base,format='unexpected'))]:
                path.write_text(raw)
                self.assertFalse(read()['available'])
                self.assertEqual(read()['availability'],'unavailable or malformed')
                self.assertNotIn('PRIVATE',json.dumps(read()))
            path.unlink()
            outside = home/'outside.json'
            outside.write_text(json.dumps(base))
            path.symlink_to(outside)
            self.assertFalse(read()['available'])

    def test_error_counts_and_malformed_workflow_remain_visible(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            root = home/'omp-memory/sync/status'
            root.mkdir(parents=True)
            path = root/'windows.json'
            report = {'format':'agentmesh-status-v1','node':'windows',
                      'updated_at':'2026-10-08T11:00:00Z',
                      'counts':dict.fromkeys(adapters.PEER_COUNTS,0),
                      'sync':dict.fromkeys(adapters.PEER_SYNC,0),
                      'cycle':{'receive':{'applied':0,'pending':0,'conflict':0,'invalid':0,'unavailable':False},
                               'publish':{'published':0,'unavailable':False}},
                      'workflow':None,'postgres_mirror':None}
            def read():
                path.write_text(json.dumps(report))
                return adapters.peer_status(home, now=datetime(2026,10,8,11,1,tzinfo=timezone.utc))['rows'][1]
            self.assertFalse(read()['has_error'])
            report['counts']['ingestion_errors'] = 3
            self.assertTrue(read()['has_error'])
            report['counts']['ingestion_errors'] = 0
            report['workflow'] = 'PRIVATE malformed value'
            self.assertIsNone(read()['has_error'])
            self.assertTrue(read()['partial'])
            self.assertNotIn('PRIVATE',json.dumps(read()))
            report['workflow'] = None
            report['cycle']['receive']['conflict'] = 1
            self.assertTrue(read()['has_error'])

    def test_peer_status_survives_absent_sqlite(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            result = adapters.operations(home,adapters.MemoryReader(),None)
            self.assertFalse(result['sync']['available'])
            self.assertEqual([r['peer'] for r in result['sync']['peers']['rows']],['Mac','Windows'])


if __name__ == '__main__':
    unittest.main()
