"""Insight after `agentmesh-log control consolidate`: the supervisor runs broker, bridge, Insight and the jobs, so
launchd knows none of them. Health comes from <home>/status/supervisor.json and the parked plists instead."""
import json
import plistlib
from types import SimpleNamespace
import adapters
import deploy_insight
import storage


def snapshot(home, units):
    (home/'status').mkdir(parents=True, exist_ok=True)
    (home/'status'/'supervisor.json').write_text(json.dumps({'v':1,'supervisor_pid':7,'units':units}))


def test_services_fall_back_to_supervisor(tmp_path):
    snapshot(tmp_path, {'broker':{'pid':42,'running':True},'bridge':{'pid':None,'running':False},'insight':{'pid':'x'}})
    calls = []
    def runner(argv, **kw):
        calls.append(argv)
        if argv[0] == 'launchctl': return SimpleNamespace(returncode=113, stdout='')  # not loaded in launchd
        if argv[0] == 'pgrep': return SimpleNamespace(returncode=1, stdout='')
        return SimpleNamespace(returncode=0, stdout='42 01:00 2048 02:00:00')
    rows = {r['service']:r for r in storage.services(runner, tmp_path)}
    assert rows['broker']['state'] == 'running' and rows['broker']['rss_mb'] == 2
    assert rows['bridge']['state'] == rows['Insight']['state'] == 'not running'
    assert ['ps','-o','pid=,time=,rss=,etime=','-p','42'] in calls


def test_services_ignore_a_bad_snapshot(tmp_path):
    (tmp_path/'status').mkdir(); (tmp_path/'status'/'supervisor.json').write_text('[1]')
    assert storage.supervised_pids(tmp_path) == {} and storage.supervised_pids(tmp_path/'none') == {}


def test_jobs_read_the_parked_plist(tmp_path):
    (tmp_path/'status').mkdir(); (tmp_path/'control'/'units').mkdir(parents=True); agents = tmp_path/'LaunchAgents'; agents.mkdir()
    (tmp_path/'status/job-ingest.json').write_text(json.dumps({'ok':True,'at':'2026-10-22T09:00:00+00:00'}))
    with open(tmp_path/'control'/'units'/'org.agentmesh.ingest.plist', 'wb') as f:
        plistlib.dump({'Label':'org.agentmesh.ingest','StartInterval':60}, f)
    row, = adapters.launchd_jobs(tmp_path, agents)
    assert (row['enabled'], row['state']) == (True, 'every 1 min')


def test_deploy_finds_and_reloads_the_parked_plist(tmp_path):
    am, agents = tmp_path/'am', tmp_path/'LaunchAgents'
    agents.mkdir(); (am/'control'/'units').mkdir(parents=True)
    parked = am/'control'/'units'/'org.agentmesh.insight.plist'; parked.write_bytes(b'')
    assert deploy_insight.default_plist(am, agents) == parked and deploy_insight.supervised(parked, am)
    (agents/'org.agentmesh.insight.plist').write_bytes(b'')
    assert deploy_insight.default_plist(am, agents) == agents/'org.agentmesh.insight.plist'
    seen = []
    def run(argv, **kw): seen.append((argv, kw.get('env', {}).get('AGENTMESH_HOME'))); raise SystemExit('stop before /health')
    try: deploy_insight.restart(parked, 1, am, run=run)
    except SystemExit: pass
    assert seen == [([str(am/'venv'/'bin'/'python'),'-m','agentmesh_log.cli','control','reload','insight'], str(am))]
