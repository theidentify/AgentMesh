"""Diagnostics exercise real private bindings and exited PIDs, without repairs."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid

import pytest
from test_worker_lifecycle import bound
from signed_packets import write_local
import worker_diagnostics as diagnostics
import worker_lifecycle as worker


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            if p.is_file() else 'directory' for p in root.rglob('*')}


def stale_record(args, *, last_error=None):
    config, _ = worker.load(args['runtime'], authoritative=False)
    directory = worker.control(config, create=True)
    process = subprocess.Popen([sys.executable, '-c', 'pass'])
    assert process.wait(timeout=60) == 0
    record = {'format': 'agentmesh-worker-v1', 'runtime': str(args['runtime']),
              'nonce': str(uuid.uuid4()), 'state': 'running', 'pid': process.pid,
              'cycles': 2, 'last_error': last_error, 'updated_at': time.time() - 10,
              'bundle_dir': '/private-path-must-not-be-reported', 'console_attached': False,
              'private_token': 'credential-must-not-be-reported'}
    write_local(directory / 'process.json', record)
    return record


def test_stale_pid_is_explained_without_mutation_or_private_values(tmp_path, monkeypatch):
    args = bound(tmp_path, monkeypatch)
    record = stale_record(args)
    before = snapshot(tmp_path)
    observed = []
    report = diagnostics.diagnose(args['runtime'], progress=observed.append)
    assert report['status'] == 'attention'
    assert report['worker']['state'] == 'stale'
    assert report['worker']['process_observation'] == 'absent'
    assert report['worker']['nonce'] == record['nonce']
    assert report['worker']['status_age_seconds'] >= 10
    assert report['checks'][-1]['error']['code'] == 'WORKER_STALE'
    assert observed == ['program', 'runtime', 'installation', 'worker-control', 'worker']
    output = json.dumps(report) + diagnostics.render(report)
    assert 'credential-must-not-be-reported' not in output
    assert '/private-path-must-not-be-reported' not in output
    assert str(args['runtime']) not in output
    assert snapshot(tmp_path) == before
    with pytest.raises(ValueError) as failure:
        worker.stop(args['runtime'])
    from cli_errors import report as error_report
    error = error_report(failure.value, 'worker-stop')
    assert error['code'] == 'WORKER_STALE'
    assert error['stage'] == 'worker_lifecycle.stop'
    assert snapshot(tmp_path) == before


def test_diagnose_never_creates_missing_shm_or_worker_control(tmp_path, monkeypatch):
    args = bound(tmp_path, monkeypatch)
    database = args['database']
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('CREATE TABLE diagnostic_read_only_probe(value TEXT)')
        wal_bytes = database.with_name(database.name + '-wal').read_bytes()
    database.with_name(database.name + '-wal').write_bytes(wal_bytes)
    assert not database.with_name(database.name + '-shm').exists()
    before = snapshot(tmp_path)
    report = diagnostics.diagnose(args['runtime'])
    assert report['status'] == 'ok'
    assert report['worker']['state'] == 'stopped'
    assert snapshot(tmp_path) == before
    assert not (database.parent / '.agentmesh-worker').exists()


def test_failed_runtime_stops_dependent_checks_without_disclosure(tmp_path):
    runtime = tmp_path / 'runtime.json'
    write_local(runtime, {'database': ['credential-do-not-share'], 'node': 'mac'})
    before = snapshot(tmp_path)
    report = diagnostics.diagnose(runtime)
    assert report['status'] == 'attention'
    runtime_check = report['checks'][1]
    assert runtime_check['stage'] == 'runtime'
    assert runtime_check['error']['code'] == 'RUNTIME_INVALID'
    assert all(item['status'] == 'skipped' for item in report['checks'][2:])
    assert 'credential-do-not-share' not in json.dumps(report) + diagnostics.render(report)
    assert snapshot(tmp_path) == before


def test_diagnose_redacts_recorded_error_payload(tmp_path, monkeypatch):
    args = bound(tmp_path, monkeypatch)
    stale_record(args, last_error='provider-token-source-transcript-must-not-leak')
    report = diagnostics.diagnose(args['runtime'])
    assert report['worker']['last_error'] == 'RecordedWorkerError'
    assert 'provider-token-source-transcript-must-not-leak' not in json.dumps(report)


def test_cli_outputs_human_and_json_reports_with_meaningful_exit(tmp_path, monkeypatch, capsys):
    import agentmesh
    args = bound(tmp_path, monkeypatch)
    capsys.readouterr()
    assert agentmesh.main(['diagnose', '--runtime', str(args['runtime'])]) == 0
    output = capsys.readouterr()
    assert '[OK] runtime' in output.out and 'READ ONLY' in output.out
    assert 'Checking runtime...' in output.err
    assert agentmesh.main(['diagnose', '--runtime', str(args['runtime']), '--json']) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)['worker']['state'] == 'stopped'
    assert output.err == ''
    stale_record(args)
    assert agentmesh.main(['diagnose', '--runtime', str(args['runtime']), '--json']) == 1
    assert json.loads(capsys.readouterr().out)['checks'][-1]['error']['code'] == 'WORKER_STALE'


def test_unknown_errors_and_os_paths_remain_redacted():
    from cli_errors import report
    output = report(PermissionError(13, 'secret-provider-token', '/secret-runtime-location'), 'worker-status')
    assert output['code'] == 'LOCAL_ACCESS_DENIED' and output['errno'] == 13
    assert 'secret' not in json.dumps(output)
    output = report(ValueError('private-key-source-transcript'), 'worker-start')
    assert output['code'] == 'UNCLASSIFIED_ERROR'
    assert 'private-key-source-transcript' not in json.dumps(output)


def test_child_failure_persists_safe_reason_for_next_diagnosis(tmp_path, monkeypatch):
    args = bound(tmp_path, monkeypatch)
    def fail(*args, **kwargs):
        raise ValueError('runtime configuration changed; restart after review')
    monkeypatch.setattr('sync_worker.run_once', fail)
    assert worker.run(args['runtime'], once=True, legacy_drained=True) == 1
    report = diagnostics.diagnose(args['runtime'])
    assert report['worker']['state'] == 'failed'
    detail = report['worker']['last_error_detail']
    assert detail['code'] == 'RUNTIME_CHANGED'
    assert detail['stage'] == 'worker_lifecycle.run'
    assert 'RUNTIME_CHANGED' in diagnostics.render(report)


def test_forged_recorded_error_fields_are_not_echoed(tmp_path, monkeypatch):
    args = bound(tmp_path, monkeypatch)
    record = stale_record(args, last_error='ValueError')
    record['last_error_detail'] = {key: 'secret-record-payload' for key in
                                 ('reason', 'code', 'stage', 'next_action', 'error')}
    write_local(args['database'].parent / '.agentmesh-worker' / 'process.json', record)
    report = diagnostics.diagnose(args['runtime'])
    assert 'secret-record-payload' not in json.dumps(report) + diagnostics.render(report)
    assert report['worker']['last_error_detail']['code'] == 'UNCLASSIFIED_ERROR'


def test_package_manifest_identity_is_checked_without_echoing_untrusted_values(tmp_path, monkeypatch):
    executable = tmp_path / ('agentmesh.exe' if os.name == 'nt' else 'agentmesh')
    executable.write_bytes(b'disposable-program')
    manifest = tmp_path / 'BUILD.json'
    data = {'version': '0.2.0-rc.4', 'source_sha': 'a' * 40,
            'checksums': {executable.name: hashlib.sha256(executable.read_bytes()).hexdigest()}}
    write_local(manifest, data)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(executable))
    info = diagnostics.program_info()
    assert info['manifest_binary_match'] is True and info['version'] == '0.2.0-rc.4'
    executable.write_bytes(b'changed-program')
    with pytest.raises(ValueError, match='package manifest does not match executable'):
        diagnostics.program_info()
    data['version'] = 'secret-unknown-metadata-do-not-share'
    write_local(manifest, data)
    with pytest.raises(ValueError, match='invalid package metadata'):
        diagnostics.program_info()
