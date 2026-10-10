"""Read-only, redacted observations; never authorize a worker/identity repair."""
import hashlib
import math
from pathlib import Path
import platform
import re
import sys
import time

from cli_errors import report as error_report, SAFE_REASONS
from install_adopt import absolute, validate
from signed_packets import parse, read_local
import worker_lifecycle as worker


def program_info():
    frozen = bool(getattr(sys, 'frozen', False))
    result = {'mode': 'standalone' if frozen else 'source', 'platform': platform.system(),
              'python': platform.python_version(), 'version': None, 'source_sha': None,
              'manifest_binary_match': None}
    if frozen:
        executable = Path(sys.executable)
        manifest = executable.parent / 'BUILD.json'
        if manifest.exists():
            data = parse(read_local(manifest))
            if (not isinstance(data, dict)
                    or not isinstance(data.get('version'), str)
                    or not re.fullmatch(r'\d+\.\d+\.\d+(?:-rc\.\d+)?', data['version'])
                    or not isinstance(data.get('source_sha'), str)
                    or not re.fullmatch(r'[0-9a-f]{40}', data['source_sha'])
                    or not isinstance(data.get('checksums'), dict)):
                raise ValueError('invalid package metadata')
            with executable.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if data['checksums'].get(executable.name) != digest:
                raise ValueError('package manifest does not match executable')
            result.update(version=data['version'], source_sha=data['source_sha'],
                          manifest_binary_match=True)
    return result


def read_runtime(runtime):
    config = parse(read_local(absolute(runtime), private=True))
    if (not isinstance(config, dict)
            or any(not isinstance(config.get(key), str) or not config[key]
                   for key in ('database', 'exchange', 'node'))):
        raise ValueError('invalid runtime')
    return config


def recorded_error(value):
    if not isinstance(value, dict):
        return None
    # Rebuild public messages/actions; never echo fields just because they are
    # called 'code', 'reason', 'stage' or 'next_action' in a local record.
    reason = value.get('reason')
    reason = reason if isinstance(reason, str) and reason in SAFE_REASONS else 'recorded error details withheld'
    kind = value.get('error')
    import builtins
    kinds = {'ValueError', 'TimeoutError', 'PermissionError', 'FileNotFoundError',
             'RuntimeError', 'OSError', 'TypeError', 'KeyError'}
    exception = getattr(builtins, kind, ValueError) if isinstance(kind, str) and kind in kinds else ValueError
    public = error_report(exception(reason), 'worker-run')
    public['error'] = kind if isinstance(kind, str) and kind in kinds | {'CycleFailed'} else 'RecordedWorkerError'
    stages = {'worker_lifecycle.run', 'worker_lifecycle.preflight', 'worker_lifecycle.control',
              'install_adopt.load', 'install_adopt.validate', 'install_adopt.absolute',
              'signed_packets.read_local', 'signed_packets.parse', 'signed_packets.guard',
              'signed_packets.private_directory', 'windows_acl.apply', 'windows_acl.validate',
              'runtime_lock.lock', 'sync_worker.run_once'}
    if isinstance(value.get('stage'), str) and value['stage'] in stages:
        public['stage'] = value['stage']
    for key in ('errno', 'winerror', 'sqlite_errorcode'):
        if type(value.get(key)) is int:
            public[key] = value[key]
    return public


def observe_worker(directory, runtime):
    record = worker.metadata(directory, runtime)
    if record is None:
        return {'state': 'stopped', 'cycles': 0, 'process_observation': 'no_record'}
    # Observing a PID is NOT a proof of ownership. Access denied is deliberately
    # conservative in alive(); do not label that as verified identity/liveness.
    observation = None
    state = record['state']
    if state == 'running':
        observation = worker.alive(record['pid'])
        if not observation:
            state = 'stale'
    last_error = record.get('last_error')
    if last_error is not None and last_error not in (
            'CycleFailed', 'ValueError', 'TimeoutError', 'PermissionError',
            'RuntimeError', 'FileNotFoundError', 'OperationalError'):
        last_error = 'RecordedWorkerError'
    timestamp = record.get('updated_at')
    age = (round(max(0, time.time() - timestamp), 1)
           if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool)
           and abs(timestamp) <= 1e12 and math.isfinite(timestamp) else None)
    return {'state': state, 'reported_state': record['state'], 'pid': record['pid'],
            'nonce': record['nonce'], 'cycles': record['cycles'], 'last_error': last_error,
            'status_age_seconds': age,
            'process_observation': ('absent' if observation is False else
                                    'present_or_access_denied' if observation else 'not_probed'),
            'last_error_detail': recorded_error(record.get('last_error_detail')) if last_error is not None else None,
            'console_attached': record.get('console_attached')
                if type(record.get('console_attached')) is bool else None}


def diagnose(runtime, *, progress=None):
    result = {'format': 'agentmesh-diagnostics-v1', 'read_only': True,
              'status': 'ok', 'checks': [], 'program': None, 'installation': None,
              'worker': None,
              'limitations': ['Installation scope uses an immutable DB snapshot; uncheckpointed WAL is not included.',
                              'PID observations do not prove instance ownership; access denied is not proof of exit.',
                              'Package checksum consistency is not a trusted publisher signature.',
                              'No repair, stop, restart, identity activation or legacy drain is performed.']}

    def check(stage, fn):
        if progress is not None:
            progress(stage)
        started = time.monotonic()
        item = {'stage': stage, 'status': 'ok'}
        try:
            value = fn()
        except Exception as exc:
            item.update(status='error', error=error_report(exc, 'diagnose'))
            value = None
            result['status'] = 'attention'
        item['duration_ms'] = round((time.monotonic() - started) * 1000, 1)
        result['checks'].append(item)
        return value

    def skipped(*stages):
        for stage in stages:
            result['checks'].append({'stage': stage, 'status': 'skipped',
                                     'reason': 'dependency failed'})

    # Program and runtime observations are independent: a package mismatch must
    # not hide the existing worker's state.
    result['program'] = check('program', program_info)
    config = check('runtime', lambda: read_runtime(runtime))
    if config is None:
        skipped('installation', 'worker-control', 'worker')
        return result
    baseline = check('installation', lambda: validate(config, runtime, authoritative=False))
    if baseline is None:
        skipped('worker-control', 'worker')
        return result
    result['installation'] = {key: baseline[key] for key in ('node', 'policy')}
    directory = check('worker-control', lambda: worker.control(config))
    if directory is None:
        skipped('worker')
        return result
    observation = check('worker', lambda: observe_worker(directory, runtime))
    result['worker'] = observation
    if observation is not None:
        if observation['state'] == 'stale':
            result['checks'][-1].update(status='warning', error=error_report(
                ValueError('stale worker metadata; no process was signalled'), 'diagnose'))
            result['status'] = 'attention'
        elif observation['state'] == 'failed' or observation.get('last_error') is not None:
            result['checks'][-1].update(status='warning', error={
                'code': 'WORKER_CYCLE_FAILED', 'reason': 'worker recorded a cycle failure',
                'stage': 'worker', 'next_action': 'Inspect the existing instance and cycle diagnostics; do not start a duplicate.'})
            result['status'] = 'attention'
    return result


def render(result):
    lines = ['AgentMesh diagnostics | READ ONLY (no repairs or worker changes)']
    info = result.get('program')
    if info:
        lines.append(f"Program: {info['mode']} | {info['platform']} | Python {info['python']}")
        lines.append('Package: ' + (info['version'] or 'version unavailable (no adjacent BUILD.json)'))
        if info['source_sha']:
            lines.append('Source: ' + info['source_sha'] + ' | binary matches adjacent manifest (not publisher signed)')
    for item in result['checks']:
        duration = f" ({item['duration_ms']} ms)" if 'duration_ms' in item else ''
        lines.append(f"[{item['status'].upper()}] {item['stage']}{duration}")
        error = item.get('error')
        if error:
            lines.append(f"  {error['code']}: {error['reason']}")
            lines.append(f"  Origin: {error['stage']}")
            for key in ('errno', 'winerror', 'sqlite_errorcode'):
                if key in error:
                    lines.append(f"  {key}: {error[key]}")
            lines.append('  Next: ' + error['next_action'])
    if result.get('installation'):
        info = result['installation']
        lines.append(f"Installation: node={info['node']} policy={info['policy']} (immutable snapshot)")
    if result.get('worker'):
        info = result['worker']
        for key in ('state', 'pid', 'nonce', 'cycles', 'last_error', 'status_age_seconds',
                    'process_observation', 'console_attached'):
            if key in info:
                lines.append(f"Worker {key}: {info[key]}")
        detail = info.get('last_error_detail')
        if detail:
            lines.append(f"Recorded failure: {detail['code']}: {detail['reason']}")
            lines.append('  Origin: ' + detail['stage'])
            lines.append('  Next: ' + detail['next_action'])
    lines.extend(['Result: ' + result['status'].upper(), 'Limits:'] +
                 ['  - ' + line for line in result['limitations']])
    return '\n'.join(lines)
