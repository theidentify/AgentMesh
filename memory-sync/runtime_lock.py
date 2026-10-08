"""Kernel-owned process lock shared by worker cycles and the local updater."""
from contextlib import contextmanager
from functools import wraps
import os
from pathlib import Path
import time


@contextmanager
def lock(path, timeout=120):
    from signed_packets import no_symlinks
    path = no_symlinks(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    stream = os.fdopen(fd, 'r+b', buffering=0)
    acquired = False
    try:
        if os.fstat(fd).st_size == 0:
            stream.write(b'0')
        deadline = time.monotonic() + timeout
        while True:
            try:
                if os.name == 'nt':
                    import msvcrt
                    stream.seek(0)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise TimeoutError('worker cycle did not drain')
                time.sleep(0.05)
        yield
    finally:
        if acquired:
            if os.name == 'nt':
                import msvcrt
                stream.seek(0)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        stream.close()


def cycle_locked(function):
    @wraps(function)
    def wrapped(database, *args, **kwargs):
        expected_version = kwargs.pop('_ota_version', None)
        directory = Path(database).absolute().parent
        with lock(directory / 'worker.lock'):
            state_path = directory.parent / 'ota-state.json'
            if state_path.exists():
                from signed_packets import parse, read_local
                state = parse(read_local(state_path, private=True))
                if type(expected_version) is not int or expected_version != state['active']:
                    raise ValueError('OTA worker version no longer active; use the installed consumer')
            elif expected_version is not None:
                raise ValueError('OTA worker requires durable installation state')
            return function(database, *args, **kwargs)
    return wrapped
