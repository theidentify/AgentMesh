"""Small helpers the broker summary and its Claude worker share: canonical JSON, strict packet parsing, the run lock,
the metrics log and the model worker call. Moved here from memory-sync/digest_io.py."""
from __future__ import annotations

from contextlib import contextmanager
import json

from .protocol import ValidationError


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def utf8_stdio():
    """Packets cross worker pipes as UTF-8. A Python started by a Windows scheduled task (no PYTHONIOENCODING) reads
    stdin and writes stdout in the ANSI code page, which mangles every non-ASCII request and reply."""
    import sys
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')


def load_packet(text):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result: raise ValueError('duplicate JSON key')
            result[key]=value
        return result
    def invalid_constant(value): raise ValueError('non-finite JSON number: '+value)
    return json.loads(text,object_pairs_hook=pairs,parse_constant=invalid_constant)


@contextmanager
def run_lock(target):
    """A non-blocking OS lock on `<target>.bounded.lock`, held across model calls; a held lock is `overlap`.

    The lock file name is unchanged from memory-sync, so a job still running the old code and one running this
    package exclude each other while a device migrates."""
    import os
    from pathlib import Path
    path = str(Path(target).resolve()) + '.bounded.lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    acquired = False
    try:
        try:
            if os.name == 'nt':
                import msvcrt
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b'0')
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            raise ValidationError('overlap: bounded digest already running') from None
        yield
    finally:
        if acquired:
            if os.name == 'nt':
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        # Never unlink a lock inode while another process may be waiting on it.


def append_metric(path, row):
    import os
    from pathlib import Path
    path = Path(path)
    with run_lock(path):
        fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'a', encoding='utf-8') as handle:
            handle.write(canonical(row) + '\n')
            handle.flush()
            os.fsync(handle.fileno())


def extract_command(request, *, model='gpt-6-luna', command=None, timeout=120):
    import subprocess
    if not isinstance(command, (list, tuple)) or not command or any(not isinstance(a, str) or not a or '\x00' in a for a in command):
        raise ValidationError('explicit authenticated worker argv required')
    try:
        result = subprocess.run([*command, '--model', model], input=canonical(request),
                                text=True, encoding='utf-8', capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ValidationError('model worker timed out; coverage unchanged') from None
    except OSError:
        raise ValidationError('model worker unavailable; coverage unchanged') from None
    if result.returncode or len(result.stdout) > 2000000:
        raise ValidationError('model worker failed%s; coverage unchanged' % worker_reason(result.stdout))
    try:
        output = load_packet(result.stdout)
    except (UnicodeError, ValueError):
        raise ValidationError('model worker unavailable or invalid; coverage unchanged') from None
    if not isinstance(output, dict) or set(output) != {'payload','provider','model','api_calls','usage'} or output['model'] != model:
        raise ValidationError('invalid model result envelope')
    return output


def worker_reason(stdout):
    """': <reason>' from a failed worker's own fixed status line, else ''."""
    import re
    try:
        reason = json.loads(stdout).get('reason') if len(stdout) < 1000 else None
    except (ValueError, AttributeError):
        return ''
    return ': ' + reason if isinstance(reason, str) and re.fullmatch(r'(?:claude (?:auth failed|login expired|rate limited|timeout|executable missing|executable denied|exit -?[0-9]+)|api_error(?: [1-5][0-9]{2})?|error_(?:max_budget_usd|max_turns|during_execution|max_structured_output_retries))', reason) else ''
