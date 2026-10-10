"""ASCII-only human progress; keep machine-readable output separate."""
import math
import sys
import threading
import time


class TerminalProgress:
    def __init__(self, stream=None, interval=5.0):
        self.stream = sys.stderr if stream is None else stream
        self.interval = interval
        self.started = time.monotonic()
        self.stage = 'Starting'
        self.done = self.total = None
        self.tty = self.stream.isatty()
        self.stop = threading.Event()
        self.lock = threading.RLock()
        self.thread = None
        self.spin = 0
        self.width = 0
        self.deadline = None

    def wait(self, seconds, message):
        with self.lock:
            self.stage = message
            self.done = self.total = None
            self.deadline = time.monotonic() + seconds
            self.render()
        while True:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0 or self.stop.wait(min(remaining, 1.0)):
                break
        self.render()

    def __call__(self, message):
        with self.lock:
            self.stage = message
            self.done = self.total = None
            self.deadline = None
            self.render()

    def rows(self, done, total):
        with self.lock:
            self.done, self.total = done, total
            self.render()

    def frame(self):
        elapsed = int(time.monotonic() - self.started)
        if self.deadline is not None:
            remaining = max(0, math.ceil(self.deadline - time.monotonic()))
            return f'{self.stage} | Next sync in {remaining}s'
        if self.total is not None and self.total > 0 and self.done is not None:
            done = min(max(self.done, 0), self.total)
            filled = done * 20 // self.total
            bar = '[' + '#' * filled + '-' * (20 - filled) + ']'
            return f'{self.stage} | {bar} {done * 100 // self.total}% | {done:,}/{self.total:,} rows | elapsed {elapsed}s'
        spinner = '|/-\\'[self.spin % 4]
        prefix = f'[{spinner}] ' if self.tty else ''
        return f'{prefix}{self.stage} | elapsed {elapsed}s'

    def render(self):
        with self.lock:
            text = self.frame()
            if self.tty:
                self.stream.write('\r' + text.ljust(self.width))
                self.width = len(text)
                self.stream.flush()
            else:
                print(text, file=self.stream, flush=True)

    def _heartbeat(self):
        tick = min(self.interval, 0.2) if self.tty else self.interval
        while not self.stop.wait(tick):
            with self.lock:
                self.spin += 1
                self.render()

    def __enter__(self):
        self.thread = threading.Thread(target=self._heartbeat, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        if self.thread is not None:
            self.thread.join()
        if self.tty:
            print(file=self.stream, flush=True)


_STDERR = object()


def _console(stream):
    # Only an interactive console gets wait feedback: captured stderr carries
    # machine-readable error JSON, and windowless processes may have no stream.
    stream = sys.stderr if stream is _STDERR else stream
    try:
        return stream if stream is not None and stream.isatty() else None
    except (AttributeError, OSError, ValueError):
        return None


def stage(message, *, stream=_STDERR):
    console = _console(stream)
    if console is not None:
        print(message + '...', file=console, flush=True)


class Wait:
    """Line-based elapsed feedback for a bounded wait, e.g. a cooperative stop."""
    def __init__(self, label, timeout, *, stream=_STDERR, every=5.0):
        self.console = _console(stream)
        self.label, self.timeout, self.every = label, timeout, every
        self.started = self.last = time.monotonic()
        self.write(f'{label} (up to {timeout:.0f}s)...')

    def write(self, text):
        if self.console is not None:
            print(text, file=self.console, flush=True)

    def tick(self):
        now = time.monotonic()
        if now - self.last >= self.every:
            self.last = now
            self.write(f'  ...{int(now - self.started)}s / {self.timeout:.0f}s')

    def done(self, result='done'):
        self.write(f'{self.label}: {result} ({int(time.monotonic() - self.started)}s)')
