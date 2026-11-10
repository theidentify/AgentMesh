"""memory-sync/ holds compatibility shims until every device has run `control rewire-ingest` (PR B removes both).

A device that deploys this tree before migrating still runs the old commands: memory-sync/job_notify.py, the
pre-retirement copies of jobs/agentmesh_*.py in ~/.agentmesh/jobs (whose config puts memory-sync on sys.path as
`runtime`) and summary.command naming claude_digest_worker.py. These run them in a child Python with no PYTHONPATH, so
the shims must find the package beside them, as on a deployed checkout of the repository.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[3]
MEMORY_SYNC = REPO / 'memory-sync'
FIXTURES = Path(__file__).resolve().parent / 'fixtures'

pytestmark = pytest.mark.skipif(not (MEMORY_SYNC / '_compat.py').is_file(), reason='memory-sync shims removed (PR B)')


def clean_env(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME')}
    env['AGENTMESH_HOME'] = str(tmp_path / 'home')
    return env


@pytest.fixture
def deployed(tmp_path):
    """~/.agentmesh/jobs as the R5b deploy left it: the old job scripts beside their config."""
    jobs = tmp_path / 'jobs'
    jobs.mkdir()
    for name in ('agentmesh_production', 'agentmesh_ingest', 'agentmesh_summary'):
        shutil.copy(FIXTURES / ('legacy_%s.py' % name), jobs / (name + '.py'))
    broker = dict(cli_argv=[sys.executable, '-m', 'agentmesh_log.cli'], agent_key=str(tmp_path / 'k'),
                  mapping=str(tmp_path / 'm'), state_dir=str(tmp_path / 'state'))
    (jobs / 'agentmesh-production.json').write_text(json.dumps(dict(
        backend='broker', runtime=str(MEMORY_SYNC), broker=broker, status_dir=str(tmp_path / 'status'),
        roots={'omp': str(tmp_path / 'omp')}, summary=dict(model='m'))))
    return jobs


def test_old_wrapper_and_old_ingest_job_still_run(tmp_path, deployed):
    status = tmp_path / 'status'
    argv = [sys.executable, str(MEMORY_SYNC / 'job_notify.py'), '--name', 'ingest', '--state-dir', str(status), '--',
            sys.executable, str(deployed / 'agentmesh_ingest.py')]
    result = subprocess.run(argv, env=clean_env(tmp_path), cwd=str(deployed), capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['exit'] == 0
    assert json.loads((status / 'job-ingest.json').read_text())['ok'] is True
    report = json.loads((status / 'last-ingest.json').read_text())
    assert (report['backend'], report['errors']) == ('broker', 0)


def test_old_modules_are_the_package_modules(tmp_path):
    code = ('import sys; sys.path.insert(0, %r)\n'
            'import broker_digest, bounded_protocol, digest_io, transcripts, timestamps, claude_digest_worker, job_notify\n'
            'import agentmesh_log.ingest.digest as d, agentmesh_log.ingest.protocol as p, agentmesh_log.control.jobrun as j\n'
            'assert broker_digest is d and bounded_protocol is p and job_notify is j\n'
            'from bounded_protocol import ValidationError\n'
            'assert issubclass(broker_digest.Unmapped, ValidationError)\n'
            'print("ok")\n' % str(MEMORY_SYNC))
    result = subprocess.run([sys.executable, '-c', code], env=clean_env(tmp_path), capture_output=True, text=True, timeout=60)
    assert result.stdout.strip() == 'ok', result.stderr


def test_old_worker_script_runs_the_package_worker(tmp_path):
    """summary.command still names claude_digest_worker.py until migrate-config; a bad request fails the same way."""
    result = subprocess.run([sys.executable, str(MEMORY_SYNC / 'claude_digest_worker.py'), '--model', 'm'], input='{}',
                            env=clean_env(tmp_path), capture_output=True, text=True, timeout=60)
    assert result.returncode != 0
    assert 'Traceback' not in result.stderr


def test_production_shim_copied_into_jobs_still_runs(tmp_path, deployed):
    """Windows R5 runbook step 1 copied memory-sync/jobs/agentmesh_production.py over the deployed one; a deploy of
    this tree followed by the same copy must keep working, finding the package through the config's runtime."""
    shutil.copy(MEMORY_SYNC / 'jobs' / 'agentmesh_production.py', deployed / 'agentmesh_production.py')
    result = subprocess.run([sys.executable, str(deployed / 'agentmesh_summary.py'), '--force'], env=clean_env(tmp_path),
                            cwd=str(tmp_path), capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    summary = json.loads((tmp_path / 'status' / 'last-summary.json').read_text())
    assert (summary['backend'], summary['status'], summary['events']) == ('broker', 'completed', 0)  # no transcripts yet
    result = subprocess.run([sys.executable, str(deployed / 'agentmesh_ingest.py')], env=clean_env(tmp_path),
                            cwd=str(tmp_path), capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert json.loads((tmp_path / 'status' / 'last-ingest.json').read_text())['backend'] == 'broker'
