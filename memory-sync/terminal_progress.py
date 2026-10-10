"""ASCII-only human progress; keep machine-readable output separate."""
import math
import os
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
UNICODE = ('⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏', '✔', '✖')
ASCII = ('|/-\\', '[OK]', '[FAIL]')


def _console(stream):
    # Only an interactive console gets live feedback: captured stderr carries
    # machine-readable error JSON, and windowless processes may have no stream.
    stream = sys.stderr if stream is _STDERR else stream
    try:
        return stream if stream is not None and stream.isatty() else None
    except (AttributeError, OSError, ValueError):
        return None


def glyphs(console, *, environ=None, nt=None):
    """Braille spinner and check marks only where they render; ASCII otherwise.

    Classic conhost fonts (e.g. Consolas) lack braille, so Windows gets Unicode
    only inside Windows Terminal or an editor terminal.
    """
    environ = os.environ if environ is None else environ
    nt = os.name == 'nt' if nt is None else nt
    encoding = (getattr(console, 'encoding', None) or '').lower().replace('-', '').replace('_', '')
    capable = not nt or bool(environ.get('WT_SESSION') or environ.get('TERM_PROGRAM'))
    return UNICODE if encoding == 'utf8' and capable else ASCII


class Wait:
    """One self-updating console line for a stage, then a kept final status line.

    Uses carriage return plus padding (no ANSI sequences), so it works in conhost,
    Windows Terminal and POSIX terminals alike. Silent when stderr is not a console.
    """
    def __init__(self, label, timeout=None, *, done=None, stream=_STDERR, interval=0.1, symbols=None):
        self.console = _console(stream)
        self.label, self.timeout, self.done_label = label, timeout, done or label
        self.started = time.monotonic()
        self.finished = False
        self.width = 0
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None
        if self.console is not None:
            self.spinner, self.ok, self.bad = symbols or glyphs(self.console)
            self.render(0)
            self.thread = threading.Thread(target=self._spin, args=(interval,), daemon=True)
            self.thread.start()

    def elapsed(self):
        return int(time.monotonic() - self.started)

    def write(self, text, final=False):
        with self.lock:
            self.console.write('\r' + text + ' ' * max(0, self.width - len(text)) + ('\n' if final else ''))
            self.console.flush()
            self.width = 0 if final else len(text)

    def render(self, frame):
        limit = f' / {self.timeout:.0f}s' if self.timeout else ''
        self.write(f'{self.spinner[frame % len(self.spinner)]} {self.label}  {self.elapsed()}s{limit}')

    def _spin(self, interval):
        frame = 0
        while not self.stop.wait(interval):
            frame += 1
            self.render(frame)

    def _finish(self, symbol, text):
        if self.finished:
            return
        self.finished = True
        if self.console is None:
            return
        self.stop.set()
        if self.thread is not None:
            self.thread.join()
        self.write(f'{symbol} {text} ({self.elapsed()}s)', final=True)

    def tick(self):
        """Kept for callers' poll loops; the spinner thread renders on its own."""

    def done(self, text=None):
        self._finish(getattr(self, 'ok', ''), text or self.done_label)

    def fail(self, text=None):
        self._finish(getattr(self, 'bad', ''), text or self.label)

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        if kind is None:
            self.done()
        else:
            self.fail()
        return False


def step(label, done=None, *, stream=_STDERR):
    """Spinner for a short stage of unknown length: `with step(...):`."""
    return Wait(label, done=done, stream=stream)
