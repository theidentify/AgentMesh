"""Relocation gates: disposable homes, never configured runtime data."""
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import adapters
import metrics
from test_isolation import setUpModule, tearDownModule


class RelocationTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name).resolve(); self.home=self.root/'hermes'; self.am=self.root/'agentmesh'
        (self.home/'omp-memory').mkdir(parents=True); self.am.mkdir()
        self.put(self.am/'packet-sync-status.json',dict(format='agentmesh-packet-sync-status-v1',packet_sync='enabled'))
        (self.home/'cron').mkdir()
        self.put(self.home/'cron/jobs.json',{'jobs':[{'id':adapters.JOB,'last_status':'ok'}]})
        (self.home/'cron/usage_audit.jsonl').write_text('')
        with sqlite3.connect(self.home/'state.db') as c:
            c.execute('CREATE TABLE sessions(id TEXT, started_at REAL, source TEXT,last_activity_at REAL,ended_at REAL)')
            c.execute("INSERT INTO sessions VALUES ('fixture',0,'fixture',123,124)")
        c.close()

    def put(self,path,value):
        path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(value))

    def install(self,new=True):
        status=self.am/'exchange/status' if new else self.home/'omp-memory/sync/status'
        coordinator=self.am/'jobs/.bounded_digest_state.json' if new else self.home/'omp-memory/.bounded_digest_state.json'
        for node in ('mac','windows'): self.put(status/(node+'.json'),dict(format='agentmesh-status-v1',node=node))
        self.put(coordinator,dict(pending={'phase':'fixture'},success={'day':'2026-10-20'}))
        return status,coordinator

    def operations(self):
        return adapters.operations(self.home,adapters.MemoryReader(),None,{'status':'unknown'},self.am)

    def check_layout(self,source):
        peers=adapters.peer_status(self.home,agentmesh_home=self.am)['rows']
        self.assertTrue(all(p['available'] and p['source']==source for p in peers))
        coordinator=self.operations()['pipeline']['coordinator']
        self.assertEqual((coordinator['available'],coordinator['source'],coordinator['phase']),(True,source,'fixture'))
        cron=adapters.metrics(self.home,self.am)['cron']
        self.assertEqual((cron['coordinator_available'],cron['source'],cron['window_phase']),(True,source,'fixture'))
        self.assertNotIn(str(self.root),json.dumps(self.operations()))

    def test_g1_new_layout(self):
        self.install(); self.check_layout('agentmesh')

    def test_g2_legacy_layout(self):
        self.install(False); self.check_layout('legacy')

    def test_g3_mixed_missing(self):
        _,legacy=self.install(False); self.install()[1].unlink()
        self.assertEqual(adapters.peer_status(self.home,agentmesh_home=self.am)['rows'][0]['source'],'agentmesh')
        self.assertEqual(self.operations()['pipeline']['coordinator']['source'],'legacy')
        legacy.unlink()
        coordinator=self.operations()['pipeline']['coordinator']
        self.assertEqual(coordinator['source'],'missing'); self.assertFalse(coordinator['available']); self.assertIsNone(coordinator['pending'])
        cron=adapters.metrics(self.home,self.am)['cron']
        self.assertFalse(cron['coordinator_available']); self.assertEqual(cron['source'],'missing')
        self.assertEqual(adapters.layout(self.home,self.am)['coordinator']['path'],self.am/'jobs/.bounded_digest_state.json')

    def test_g4_cron_home(self):
        self.install()
        self.put(self.am/'cron/jobs.json',{'jobs':[{'id':adapters.JOB,'last_status':'WRONG'}]})
        (self.am/'cron/usage_audit.jsonl').write_text('{invalid}')
        (self.am/'state.db').write_text('invalid database')
        self.put(self.home/'cron/usage_audit.jsonl',dict(job_id=adapters.JOB,ts='2026-10-20T00:00:00Z',prompt_tokens=42))
        data=adapters.metrics(self.home,self.am)
        self.assertEqual(data['cron']['last_status'],'ok'); self.assertEqual(data['report']['baseline']['input_total_tokens']['total'],42)
        ops=self.operations(); self.assertEqual(ops['pipeline']['jobs'][1]['last_status'],'ok')
        self.assertEqual(ops['hermes_sessions']['rows'][0]['source'],'fixture')

    def test_g5_merge_override_corruption_readonly(self):
        for filename,group in [('summary-metrics.jsonl','bounded'),('summary-smoke-test-metrics.jsonl','isolated')]:
            paths=[self.am/'backups/archive'/(filename+'.pre-relocate-01'),self.am/'backups/archive'/(filename+'.pre-relocate-02'),self.home/'omp-memory'/filename,self.am/'jobs'/filename]
            for n,path in enumerate(paths):
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text('\n'.join(json.dumps(dict(record_type='run',run_id=ident,status='applied',events_processed=n)) for ident in (str(n),'duplicate')))
            paths[0].chmod(0o400); before=paths[0].stat()
            result=adapters.metrics(self.home,self.am)
            runs=result['runs'][group]; self.assertEqual(len(runs),5)
            self.assertEqual(next(r for r in runs if r['run_id']=='duplicate')['events_processed'],3)
            after=paths[0].stat(); self.assertEqual((before.st_mode,before.st_mtime_ns),(after.st_mode,after.st_mtime_ns))
        override=self.root/'override.jsonl'; self.put(override,dict(record_type='run',run_id='override',status='applied'))
        self.assertEqual([r['run_id'] for r in adapters.metrics(self.home,self.am,[override])['runs']['bounded']],['override'])
        self.assertEqual(metrics.report(self.home/'cron/usage_audit.jsonl',self.home/'state.db',override)['new']['sample_count'],1)
        with patch('sys.stdout',new_callable=io.StringIO) as out:
            metrics.main(['--audit',str(self.home/'cron/usage_audit.jsonl'),'--state',str(self.home/'state.db'),'--metrics-log',str(override),'--metrics-log',str(override),'--json'])
            self.assertEqual(json.loads(out.getvalue())['new']['sample_count'],1)
        override.write_text('{invalid}')
        with self.assertRaises(json.JSONDecodeError): adapters.metrics(self.home,self.am,[override])

    def test_g6_both_layout_containment(self):
        for new in (False,True):
            with self.subTest(new=new):
                root,_=self.install(new); path=root/'mac.json'; path.unlink(); path.symlink_to(root/'windows.json')
                self.assertEqual(adapters.peer_status(self.home,agentmesh_home=self.am)['rows'][0]['availability'],'unavailable or malformed')
                path.unlink(); (root/'windows.json').unlink(); root.rmdir()
                outside=self.root/('outside-'+str(new)); outside.mkdir(); root.symlink_to(outside,target_is_directory=True)
                self.assertTrue(all(r['availability']=='unavailable or malformed' for r in adapters.peer_status(self.home,agentmesh_home=self.am)['rows']))
                root.unlink()

    def test_g12_adapters_never_open_secrets(self):
        self.install()
        for name in ('profile-log/keystore.sealed','agents/x.key','settings.json','settings.local.json','apply.jsonl'):
            path=self.am/name; path.parent.mkdir(exist_ok=True); path.write_text('SENTINEL'); path.chmod(0)
        original=os.open; original_io=io.open; opened=[]
        def check(path):
            if isinstance(path,(str,bytes,os.PathLike)):
                path=Path(os.fsdecode(path)); opened.append(path)
                self.assertNotIn(path.name,('keystore.sealed','x.key','settings.json','settings.local.json','apply.jsonl'))
        def os_open(path,*args,**kwargs): check(path); return original(path,*args,**kwargs)
        def io_open(path,*args,**kwargs): check(path); return original_io(path,*args,**kwargs)
        with patch('os.open',os_open),patch('io.open',io_open),patch('builtins.open',io_open):
            adapters.profile_log_status(self.am); adapters.peer_status(self.home,agentmesh_home=self.am)
            self.operations(); adapters.metrics(self.home,self.am)
            import syncthing_transport
            config=self.root/'missing-config.xml'
            with patch.object(syncthing_transport,'config_path',return_value=config):
                syncthing_transport.observe(self.home,adapters.layout(self.home,self.am)['exchange']['path'])
        self.assertIn(self.am/'profile-log-status.json',opened)
        self.assertIn(self.am/'exchange/status/mac.json',opened)
