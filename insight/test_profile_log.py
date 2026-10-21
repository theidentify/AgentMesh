"""Strict snapshot projection and the existing HTTP boundary."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error
import adapters
import server
from test_isolation import setUpModule, tearDownModule


def fixture():
    stamp='2026-10-20T00:00:00Z'
    return dict(format='agentmesh-profile-log-status-v1',updated_at=stamp,
        overview=dict(generated_at=stamp,profiles=[dict(name='work',scopes=[dict(path='work/common',label='general')])],
            agents=[dict(name='codex',read=['work/common'],write=['work/common'],default_write='work/common',allow_secret=False,allow_cross_profile=False,allow_import=True)],
            devices=[dict(device_id='123456789012PRIVATE',revoked=False)]),
        migration=dict(generated_at=stamp,projects=[dict(project='AgentMesh',items=3,summaries=1,target='work/common')],unmapped=['unmapped']),
        last_apply=dict(at=stamp,outcome='approved',changes={'agent_widen':1}),
        last_catch_up=dict(at=stamp,outcome='ok',outcomes={'created':2},skipped={'mapping_skip':1},unmapped=[],errors=['ValueError'],busy_retries=0,timestamp_fallback=0,secret_labelled=0))


class ProfileLogTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup); self.root=Path(tmp.name).resolve()
        self.path=self.root/'profile-log-status.json'

    def read(self,value):
        self.path.write_text(json.dumps(value))
        return adapters.profile_log_status(self.root,datetime(2026,10,20,0,1,tzinfo=timezone.utc))

    def test_g10_projection_age_redaction_and_bounds(self):
        value=fixture(); value.update(key='PRIVATE',content='PRIVATE',agent_key='PRIVATE',path='PRIVATE')
        for section in ('overview','migration','last_apply','last_catch_up'): value[section]['key']='PRIVATE'
        value['overview']['agents'][0].update(content='PRIVATE',name='password=sensitive',path='PRIVATE')
        result=self.read(value)
        self.assertTrue(result['available']); self.assertEqual(result['age_seconds'],60)
        self.assertEqual(result['last_apply']['age_seconds'],60); self.assertEqual(result['last_catch_up']['age_seconds'],60)
        self.assertEqual(result['overview']['devices'][0]['device_id'],'123456789012')
        self.assertNotIn('PRIVATE',json.dumps(result)); self.assertNotIn('sensitive',json.dumps(result))
        self.assertEqual(result['overview']['profiles'][0]['scopes'][0]['path'],'work/common')
        for bad in (True,1.5,'3',-1,2**53,None):
            value['migration']['projects'][0]['items']=bad
            self.assertIsNone(self.read(value)['migration']['projects'][0]['items'])
        for bad in ('bad\x00name','bad\x7fname','bad\x85name','x'*257,42,{}):
            value['migration']['projects'][0]['project']=bad
            self.assertIsNone(self.read(value)['migration']['projects'][0]['project'])
        for section,key in [('overview','profiles'),('overview','agents'),('overview','devices'),('migration','projects'),('migration','unmapped'),('last_catch_up','errors')]:
            v=fixture(); v[section][key]*=501; out=self.read(v)
            self.assertTrue(out['truncated']); self.assertEqual(len(out[section][key]),500)
        v=fixture(); v['overview']['agents'][0]['read']=['work']*501
        out=self.read(v); self.assertTrue(out['truncated']); self.assertEqual(len(out['overview']['agents'][0]['read']),500)
        v=fixture(); v['overview']['profiles'][0]['scopes']*=501
        out=self.read(v); self.assertTrue(out['truncated']); self.assertEqual(len(out['overview']['profiles'][0]['scopes']),500)

    def test_remote_sync_projection_and_freshness(self):
        value=fixture(); self.assertNotIn('last_sync',self.read(value))
        row=dict(at='2026-10-20T00:00:00Z',outcome='ok',interval_s=60,backend='s3',last_ok_at='2026-10-19T23:59:00Z',
            uploaded=1,downloaded=0,invalid=0,errors=0,outbox=0,local_objects=2,remote_objects=2)
        value['last_sync']=row
        for outcome in ('ok','remote_error','credentials_missing','credentials_refused','error','future_outcome'):
            row['outcome']=outcome; result=self.read(value)['last_sync']
            self.assertEqual(result['outcome'],outcome); self.assertEqual(result['age_seconds'],60)
            self.assertEqual(result['last_ok_age_seconds'],120); self.assertFalse(result['stale'])
            self.assertEqual(result['stale_after_seconds'],720)
        for interval,age,stale in ((60,720,False),(60,721,True),(300,1000,False),(300,1201,True),(1000,3001,True)):
            from datetime import timedelta
            row.update(interval_s=interval,at=(datetime(2026,10,20,0,1,tzinfo=timezone.utc)-timedelta(seconds=age)).isoformat())
            self.assertEqual(self.read(value)['last_sync']['stale'],stale)
        row.update(credentials='PRIVATE',access_key='PRIVATE',secret_key='PRIVATE',extra='PRIVATE',backend={},last_ok_at=None,at=[],outbox=-1)
        for bad in (True,0,-1,'60',None,float('inf'),float('nan'),{}):
            row['interval_s']=bad; result=self.read(value)['last_sync']
            for key in ('interval_s','stale','stale_after_seconds','age_seconds','last_ok_age_seconds','at','outbox','backend'): self.assertIsNone(result[key])
            self.assertNotIn('PRIVATE',json.dumps(result)); self.assertNotIn('credentials',result)
        row.update(at='not a timestamp',last_ok_at='not a timestamp')
        result=self.read(value)['last_sync']; self.assertIsNone(result['at']); self.assertIsNone(result['last_ok_at'])
        row.update(outcome={},last_ok_at=[])
        result=self.read(value)['last_sync']; self.assertIsNone(result['outcome']); self.assertIsNone(result['last_ok_at'])

    def test_remote_sync_only_opens_snapshot(self):
        self.read(fixture())
        with patch('adapters.os.open',wraps=os.open) as opened:
            self.assertTrue(adapters.profile_log_status(self.root)['available'])
        self.assertEqual([call.args[0] for call in opened.call_args_list],[self.path])

    def test_g10_malformed_symlink_nonregular_oversize(self):
        self.assertEqual(adapters.profile_log_status(self.root)['availability'],'missing')
        for raw in ('{bad','[]','{}','{"format":"wrong"}',' '*(256*1024+1)):
            self.path.write_text(raw)
            self.assertEqual(adapters.profile_log_status(self.root)['availability'],'unavailable or malformed')
        self.path.unlink(); target=self.root/'target'; target.write_text(json.dumps(fixture())); self.path.symlink_to(target)
        self.assertFalse(adapters.profile_log_status(self.root)['available'])
        self.path.unlink(); os.mkfifo(self.path)
        self.assertFalse(adapters.profile_log_status(self.root)['available'])

    def test_g11_endpoint_cache_guards_and_metrics_override(self):
        self.read(fixture()); explicit=self.root/'explicit.jsonl'
        with patch('adapters.metrics',return_value={'fixture':True}) as load_metrics:
            app=server.make_server(self.root,0,agentmesh_home=self.root,metrics_logs=[explicit])
            thread=threading.Thread(target=app.serve_forever,daemon=True); thread.start()
            try:
                url='http://127.0.0.1:'+str(app.server_port)
                def request(path,method='GET',headers=None):
                    try: response=urllib.request.urlopen(urllib.request.Request(url+path,method=method,headers=headers or {}))
                    except urllib.error.HTTPError as e: response=e
                    with response: return response.status,json.load(response)
                code,value=request('/api/profile-log'); self.assertEqual(code,200); self.assertTrue(value['available'])
                self.path.write_text('{bad'); self.assertTrue(request('/api/profile-log')[1]['available'])
                for query in ('?x=1','?backend=sqlite','?x','?x=1&x=2','?path=anything'):
                    self.assertEqual(request('/api/profile-log'+query)[0],400)
                for method in ('POST','PUT','DELETE'): self.assertEqual(request('/api/profile-log',method)[0],405)
                self.assertEqual(request('/api/profile-log',headers={'Host':'evil.test'})[0],403)
                self.assertEqual(request('/api/profile-log',headers={'Origin':'https://evil.test'})[0],403)
                self.assertEqual(request('/api/metrics')[0],200)
                load_metrics.assert_called_once_with(self.root,self.root,[explicit])
            finally: app.shutdown(); app.server_close(); thread.join()

    def test_g13_render_fixture_escaped(self):
        import subprocess
        result=subprocess.run(['node',str(Path(__file__).with_name('profile_log_qa.mjs'))],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('PASS: Profile log',result.stdout)

    def test_g1_g5_server_cli_layout_and_repeatable_override(self):
        import io
        from unittest.mock import MagicMock
        app=MagicMock(); app.server_port=12345
        argv=['server.py','--home',str(self.root/'hermes'),'--agentmesh-home',str(self.root/'am'),'--metrics-log',str(self.root/'one'),'--metrics-log',str(self.root/'two')]
        with patch('sys.argv',argv),patch('server.make_server',return_value=app) as make,patch('sys.stdout',new_callable=io.StringIO):
            server.main()
        self.assertEqual(make.call_args.kwargs['agentmesh_home'],self.root/'am')
        self.assertEqual(make.call_args.kwargs['metrics_logs'],[self.root/'one',self.root/'two'])
        app.serve_forever.assert_called_once(); app.server_close.assert_called_once()
