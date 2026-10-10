"""Owned managed lifecycle, separate lifetime/cycle locks and cooperative stop."""
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from install_adopt import absolute, load
from runtime_lock import lock
from signed_packets import parse, private_directory, read_local, windows_private, write_local

_CHILDREN = {}


def positive(value):
    if not math.isfinite(value) or value <= 0:
        raise ValueError('finite positive interval/timeout required')
    return value


def control(config, *, create=False):
    directory = absolute(Path(config['database']).parent / '.agentmesh-worker')
    if create and not directory.exists():
        directory.mkdir(mode=0o700)
        windows_private(directory, provision=True)
    if directory.exists():
        private_directory(directory)
    return directory


def metadata(directory, runtime):
    path = directory / 'process.json'
    if not path.exists():
        return None
    record = parse(read_local(path, private=True))
    if (not isinstance(record, dict) or record.get('format') != 'agentmesh-worker-v1'
            or record.get('runtime') != str(absolute(runtime))
            or record.get('state') not in ('running', 'stopped', 'failed')
            or type(record.get('pid')) is not int or record['pid'] <= 0
            or type(record.get('cycles')) is not int or record['cycles'] < 0):
        raise ValueError('corrupt or foreign worker metadata; manual review required')
    try:
        if str(uuid.UUID(record['nonce'])) != record['nonce']:
            raise ValueError('nonce mismatch')
    except (KeyError, TypeError, ValueError):
        raise ValueError('corrupt worker nonce') from None
    return record


def windows_alive(pid):
    """Query process state without Windows console events or termination."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER: PID no longer exists.
            return False
        if error == 5:  # Access denied is not evidence that the process exited.
            return True
        raise ctypes.WinError(error)
    try:
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel.CloseHandle(handle)


def alive(pid):
    if os.name == 'nt':
        return windows_alive(pid)
    try:
        os.kill(pid, 0)  # POSIX observation only. Never signal a metadata PID.
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def status(runtime):
    config, _ = load(runtime, authoritative=False)
    directory = control(config)
    record = metadata(directory, runtime)
    if record is None:
        return {'state': 'stopped', 'cycles': 0}
    if record['state'] == 'running' and not alive(record['pid']):
        return {**record, 'state': 'stale'}
    return record


def stop_requested(directory, nonce):
    path = directory / 'stop.json'
    if not path.exists():
        return False
    value = parse(read_local(path, private=True))
    if not isinstance(value, dict) or set(value) != {'nonce'}:
        raise ValueError('corrupt stop request')
    return value['nonce'] == nonce


def run(runtime, *, once=False, interval=60, legacy_drained=False, nonce=None, startup_deadline=None):
    import sync_worker
    positive(interval)
    runtime = absolute(runtime)
    runtime_bytes = read_local(runtime, private=True)
    config, scope = load(runtime)
    if scope['policy'] == 'legacy' and not legacy_drained:
        raise ValueError('legacy worker must be drained explicitly; acknowledge unsigned policy')
    directory = control(config, create=True)
    nonce = nonce or str(uuid.uuid4())
    if str(uuid.UUID(nonce)) != nonce:
        raise ValueError('invalid worker nonce')
    # Shared across runtimes pointing at this DB directory, unlike PID files.
    with lock(directory / 'lifetime.lock', timeout=0):
        if startup_deadline is not None and time.monotonic() > startup_deadline:
            raise TimeoutError('startup deadline expired')
        record = {'format': 'agentmesh-worker-v1', 'runtime': str(runtime),
                  'pid': os.getpid(), 'nonce': nonce, 'state': 'running',
                  'cycles': 0, 'last_error': None, 'updated_at': time.time()}
        if getattr(sys, 'frozen', False):
            # Local diagnostics only; never projected into exchange peer status.
            record['bundle_dir'] = getattr(sys, '_MEIPASS')
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            console = ctypes.WinDLL('kernel32', use_last_error=True).GetConsoleWindow
            console.restype = wintypes.HWND
            record['console_attached'] = bool(console())
        def save():
            record['updated_at'] = time.time()
            write_local(directory / 'process.json', record)
        def preflight():
            if read_local(runtime, private=True) != runtime_bytes:
                raise ValueError('runtime configuration changed; restart after review')
            if load(runtime) != (config, scope):
                raise ValueError('database scope or signing key changed')
        save()
        result = 0
        try:
            while not stop_requested(directory, nonce):
                if read_local(runtime, private=True) != runtime_bytes:
                    raise ValueError('runtime configuration changed; restart after review')
                current, current_scope = load(runtime)
                if current != config or current_scope != scope:
                    raise ValueError('database scope or signing key changed')
                report = sync_worker.run_once(config['database'], config['exchange'],
                    workflow_config=config.get('workflow_config'), force_summary=False,
                    security_dir=config.get('security_dir') if scope['policy'] == 'required' else None,
                    security_state=config.get('security_state'), preflight=preflight)
                result = int(sync_worker.failed(report))
                record['cycles'] += 1
                record['last_error'] = 'CycleFailed' if result else None
                save()
                if once or result:
                    break
                deadline = time.monotonic() + interval
                while time.monotonic() < deadline and not stop_requested(directory, nonce):
                    time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        except Exception as exc:
            result = 1
            record['last_error'] = type(exc).__name__
        finally:
            record['state'] = 'failed' if result else 'stopped'
            save()
        return result


def command(runtime, *, interval=60, legacy_drained=False, once=False, nonce=None, startup_deadline=None):
    positive(interval)
    prefix = [sys.executable] if getattr(sys, 'frozen', False) else [sys.executable, str(Path(__file__).with_name('agentmesh.py'))]
    args = prefix + ['worker-run', '--runtime', str(absolute(runtime)), '--interval', str(interval)]
    if legacy_drained:
        args += ['--legacy-drained']
    if once:
        args += ['--once']
    if nonce:
        args += ['--nonce', nonce]
    if startup_deadline is not None:
        args += ['--startup-deadline', str(startup_deadline)]
    return args


def start(runtime, *, interval=60, legacy_drained=False, timeout=60):
    positive(interval)
    positive(timeout)
    config, scope = load(runtime)
    if scope['policy'] == 'legacy' and not legacy_drained:
        raise ValueError('legacy worker must be drained explicitly')
    if status(runtime)['state'] == 'running':
        raise ValueError('worker already running; stop it first')
    # Protect shared control storage before the child can expose it to status.
    # Windows native provisioning takes time; an unprotected mkdir is not ready.
    control(config, create=True)
    nonce = str(uuid.uuid4())
    deadline = time.monotonic() + timeout
    env = dict(os.environ)
    if getattr(sys, 'frozen', False):
        # This worker outlives the launcher: give it its own onefile extraction.
        env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == 'nt' else 0
    process = subprocess.Popen(command(runtime, interval=interval, legacy_drained=legacy_drained,
        nonce=nonce, startup_deadline=deadline), stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
        creationflags=flags, start_new_session=os.name != 'nt')
    _CHILDREN[nonce] = process
    while time.monotonic() < deadline:
        record = status(runtime)
        if record.get('nonce') == nonce and record['state'] == 'running' and record['cycles'] > 0 and not record['last_error']:
            return record
        if process.poll() is not None:
            _CHILDREN.pop(nonce, None)
            raise ValueError('worker failed before a healthy cycle')
        time.sleep(0.1)
    directory = control(config)
    write_local(directory / 'stop.json', {'nonce': nonce})
    raise TimeoutError('worker startup did not finish; nonce stop requested')


def drained(directory, record, deadline):
    with lock(directory / 'lifetime.lock', timeout=max(0, deadline - time.monotonic())):
        pass
    child = _CHILDREN.pop(record['nonce'], None)
    if child is not None:
        child.wait(timeout=max(0.1, deadline - time.monotonic()))
    return record


def stop(runtime, *, timeout=60):
    positive(timeout)
    # Stopping owned execution must remain possible after key revocation.
    # This path grants no ingestion/signing authority and never signals a PID.
    config = parse(read_local(absolute(runtime), private=True))
    if not isinstance(config, dict):
        raise ValueError('invalid runtime')
    db, exchange = (absolute(config[k]) for k in ('database', 'exchange'))
    if db.is_relative_to(exchange):
        raise ValueError('database must remain outside exchange')
    directory = control(config)
    record = metadata(directory, runtime)
    deadline = time.monotonic() + timeout
    if record is None:
        return {'state': 'stopped', 'cycles': 0}
    if record['state'] in ('stopped', 'failed'):
        return drained(directory, record, deadline)
    if not alive(record['pid']):
        raise ValueError('stale worker metadata; no process was signalled')
    nonce = record['nonce']
    write_local(directory / 'stop.json', {'nonce': nonce})
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = metadata(directory, runtime)
        if current is None or current['nonce'] != nonce:
            raise ValueError('worker ownership changed while stopping')
        if current['state'] in ('stopped', 'failed'):
            return drained(directory, current, deadline)
        time.sleep(0.1)
    raise TimeoutError('worker did not acknowledge stop; no process was signalled')
