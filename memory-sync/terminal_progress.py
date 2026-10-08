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
