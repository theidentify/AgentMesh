"""Operator confirmations for interactive consoles. Every default is the safe choice.

Routine operations use a one-line select (arrow keys, or y/N when keys are
unavailable). Destructive operations keep a typed word with live feedback.
Rendering uses carriage return plus padding; colors and ghost text appear only
when the console supports VT sequences (enabled explicitly on Windows).
"""
import os
import sys
import time

CANCELLED = 'Cancelled. Nothing was changed.'
IDLE = 300  # an abandoned prompt cancels itself instead of waiting forever

GREEN, RED, DIM, BOLD, RESET = '\x1b[32m', '\x1b[31m', '\x1b[2m', '\x1b[1;36m', '\x1b[0m'


def distance(a, b):
    """Levenshtein distance, for 'Did you mean' hints on short words."""
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        previous, row[0] = row[0], i
        for j, y in enumerate(b, 1):
            previous, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, previous + (x != y))
    return row[-1]


def mismatch(word, typed):
    if distance(word, typed.upper()) <= max(2, len(word) // 2):
        return f'Did you mean {word}? (typed {typed})'
    return f'"{typed}" does not match {word}.'


def enable_vt(stream):
    """True when ANSI sequences render; on Windows, turn on VT processing first."""
    if os.name != 'nt':
        return os.environ.get('TERM', '') != 'dumb'
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        handle = kernel32.GetStdHandle(-12 if stream is sys.stderr else -11)
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(mode.value & 0x0004 or kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except (OSError, AttributeError, ValueError):
        return False


class Keys:
    """Single keypresses with a timeout: msvcrt on Windows, cbreak termios on POSIX."""
    ARROWS = {'H': 'up', 'P': 'down', 'K': 'left', 'M': 'right', 'A': 'up', 'B': 'down', 'C': 'right', 'D': 'left'}

    def __init__(self, stdin=None):
        self.stdin = stdin or sys.stdin
        self.saved = None

    def __enter__(self):
        if os.name != 'nt':
            import termios
            import tty
            fd = self.stdin.fileno()
            self.saved = termios.tcgetattr(fd)
            tty.setcbreak(fd)
        return self

    def __exit__(self, *exc):
        if self.saved is not None:
            import termios
            termios.tcsetattr(self.stdin.fileno(), termios.TCSADRAIN, self.saved)
        return False

    @staticmethod
    def name(char):
        return {'\r': 'enter', '\n': 'enter', '\x1b': 'esc', '\x03': 'ctrl-c', '\t': 'tab',
                '\x08': 'backspace', '\x7f': 'backspace'}.get(char, char)

    def read(self, timeout):
        """A key name or printable character; None when the timeout passes."""
        if os.name == 'nt':
            import msvcrt
            deadline = time.monotonic() + timeout
            while not msvcrt.kbhit():
                if time.monotonic() > deadline:
                    return None
                time.sleep(0.02)
            char = msvcrt.getwch()
            if char in ('\x00', '\xe0'):
                return self.ARROWS.get(msvcrt.getwch(), 'other')
            return self.name(char)
        import select
        fd = self.stdin.fileno()
        if not select.select([fd], [], [], timeout)[0]:
            return None
        char = os.read(fd, 1).decode('utf-8', 'replace')
        if char == '\x1b' and select.select([fd], [], [], 0.05)[0]:
            sequence = os.read(fd, 2).decode('utf-8', 'replace')
            return self.ARROWS.get(sequence[-1:], 'other') if sequence[:1] in ('[', 'O') else 'esc'
        return self.name(char)


class _Nothing:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ConsolePrompt:
    def __init__(self, stdin=None, stream=None, keys=None, vt=None, idle=IDLE):
        self.stdin = stdin or sys.stdin
        self.stream = stream or sys.stderr
        self.keys = keys
        self.vt = vt
        self.idle = idle
        self.width = 0

    def interactive(self):
        try:
            return self.stdin.isatty() and self.stream.isatty()
        except (AttributeError, ValueError):
            return False

    def _keys(self):
        if self.keys is not None:
            return self.keys
        try:
            return Keys(self.stdin) if self.interactive() else None
        except (ImportError, OSError, ValueError):
            return None

    def _color(self):
        if self.vt is None:
            self.vt = enable_vt(self.stream)
        return self.vt

    def show(self, text):
        print(text, file=self.stream, flush=True)

    def _line(self, text, visible, final=False):
        """Redraw one line; `visible` is its printed length without color codes."""
        self.stream.write('\r' + text + ' ' * max(0, self.width - visible) + ('\n' if final else ''))
        self.stream.flush()
        self.width = 0 if final else visible

    def decide(self, question, yes_label):
        """One-line select defaulting to Cancel; y/N fallback without raw keys."""
        keys = self._keys()
        if keys is None:
            self.stream.write(f'{question} [y/N]: ')
            self.stream.flush()
            answer = self.stdin.readline()
            if not answer:
                self.show('')
            return answer.strip().lower() in ('y', 'yes')
        labels, choice = (yes_label, 'Cancel'), 1
        color, marker = self._color(), '❯' if getattr(self.stream, 'encoding', '').lower().replace('-', '') == 'utf8' else '>'
        def render(final=False):
            parts, visible = [], len(question) + 2
            for index, label in enumerate(labels):
                chosen = index == choice
                plain = (marker + ' ' if chosen else '  ') + label
                visible += len(plain) + 3
                parts.append(f'{BOLD}{plain}{RESET}' if chosen and color else plain)
            self._line(question + '  ' + '   '.join(parts) + '   ', visible, final)
        try:
            with keys:
                render()
                while True:
                    key = keys.read(self.idle)
                    if key in (None, 'esc', 'ctrl-c', 'n', 'N'):
                        choice = 1
                        break
                    if key in ('y', 'Y'):
                        choice = 0
                        break
                    if key in ('left', 'right', 'up', 'down', 'tab'):
                        choice = 1 - choice
                        render()
                    elif key == 'enter':
                        break
        except KeyboardInterrupt:
            choice = 1
        render(final=True)
        return choice == 0

    def typed(self, word, action, attempts=3):
        """Exact word (case-insensitive) with live prefix feedback and bounded retries."""
        self.show(f'Type {word} to {action}. Esc cancels.')
        keys = self._keys()
        # Raw mode is entered once: typeahead from before the prompt is discarded,
        # but keys typed right after a mismatch hint are kept for the next attempt.
        with keys if keys is not None else _Nothing():
            for attempt in range(attempts):
                answer = self._read_word(word, keys)
                if answer is None or not answer.strip():
                    return False
                if answer.strip().upper() == word:
                    return True
                remaining = attempts - attempt - 1
                self.show(mismatch(word, answer.strip()) + (f' {remaining} attempt(s) left.' if remaining else ''))
        return False

    def _read_word(self, word, keys):
        if keys is None:
            self.stream.write('  > ')
            self.stream.flush()
            answer = self.stdin.readline()
            return answer.rstrip('\r\n') if answer else None
        color, typed = self._color(), ''
        def render(final=False):
            ok = word.startswith(typed.strip().upper())
            ghost = word[len(typed.strip()):] if ok and not final else ''
            if color:
                text = '  > ' + (GREEN if ok else RED) + typed + RESET + (DIM + ghost + RESET if ghost else '')
            else:
                text = '  > ' + typed
            self._line(text, 4 + len(typed) + len(ghost if color else ''), final)
        try:
            render()
            while True:
                key = keys.read(self.idle)
                if key in (None, 'esc', 'ctrl-c'):
                    render(final=True)
                    return None
                if key == 'enter':
                    render(final=True)
                    return typed
                if key == 'backspace':
                    typed = typed[:-1]
                elif len(key) == 1 and key.isprintable():
                    typed += key
                render()
        except KeyboardInterrupt:
            render(final=True)
            return None


class GuiPrompt(ConsolePrompt):
    """Native Yes/No dialog for double-clicked helpers; No is the default button.

    Destructive typed words stay in the console. Only used with --gui on an
    interactive Windows console, never by the logon launcher or scripts.
    """
    MB_YESNO, MB_ICONQUESTION, MB_DEFBUTTON2, MB_SETFOREGROUND, MB_TOPMOST = 0x4, 0x20, 0x100, 0x10000, 0x40000
    MB_OK, MB_ICONERROR, MB_ICONINFORMATION, IDYES = 0x0, 0x10, 0x40, 6

    def __init__(self, *args, user32=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user32 = user32
        self.text = ''

    def _box(self, text, title, flags):
        if self.user32 is None:
            import ctypes
            self.user32 = ctypes.WinDLL('user32', use_last_error=True)
        return self.user32.MessageBoxW(None, text, title, flags | self.MB_SETFOREGROUND | self.MB_TOPMOST)

    def show(self, text):
        super().show(text)
        self.text = text  # the latest summary becomes the dialog body

    def decide(self, question, yes_label):
        body = self.text.strip() + '\n\n' + question + '\n\nYes = ' + yes_label + '    No = Cancel'
        approved = self._box(body, 'AgentMesh', self.MB_YESNO | self.MB_ICONQUESTION | self.MB_DEFBUTTON2) == self.IDYES
        self._line(question + '  ' + (yes_label if approved else 'Cancel') + ' (dialog)', 0, final=True)
        return approved

    def result(self, ok, text):
        self._box(text, 'AgentMesh', self.MB_OK | (self.MB_ICONINFORMATION if ok else self.MB_ICONERROR))
