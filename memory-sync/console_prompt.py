"""Operator confirmations for interactive consoles. Every default is the safe choice.

Routine operations use a one-line select (arrow keys, or y/N when keys are
unavailable). Destructive operations keep a typed word with live feedback.
Rendering uses carriage return plus padding; colors and ghost text appear only
when the console supports VT sequences (enabled explicitly on Windows).
"""
import os
import sys
import time

from terminal_progress import columns

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

    def room(self):
        return max(10, columns(self.stream) - 1)

    def _line(self, text, visible, final=False):
        """Redraw one line; `visible` is its printed length without color codes.

        Callers keep `visible` within room() so carriage return never meets a
        wrapped row; padding never extends past the terminal width either.
        """
        pad = max(0, min(self.width, self.room()) - visible)
        self.stream.write('\r' + text + ' ' * pad + ('\n' if final else ''))
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
        self.show(question + '  (arrow keys or y/n, then Enter; Enter alone cancels)')
        color, marker = self._color(), '❯' if getattr(self.stream, 'encoding', '').lower().replace('-', '') == 'utf8' else '>'
        def render(final=False):
            parts, visible = [], 2 + 2 * (len(labels) - 1)
            for index, label in enumerate(labels):
                chosen = index == choice
                plain = (marker + ' ' if chosen else '  ') + label
                visible += len(plain)
                parts.append(f'{BOLD}{plain}{RESET}' if chosen and color else plain)
            self._line('  ' + '  '.join(parts), min(visible, self.room()), final)
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
            if 4 + len(typed) + len(ghost) > self.room():  # long input: show its tail, uncoloured
                tail = typed[-max(1, self.room() - 7):]
                self._line('  > ...' + tail, 7 + len(tail), final)
                return
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
    """Native dialogs for double-clicked helpers; Cancel is always the default.

    Chain: TaskDialogIndirect (Common Controls v6, labelled buttons) -> MessageBoxW
    (Yes/No, natural wording) -> console select. Never raises because a dialog
    is unavailable. Destructive typed words stay in the console. Only used with
    --gui on an interactive Windows console, never by the logon launcher or scripts.
    """
    IDCANCEL, IDYES, IDCLOSE, PROCEED = 2, 6, 8, 1001
    MB_YESNO, MB_ICONQUESTION, MB_DEFBUTTON2, MB_SETFOREGROUND, MB_TOPMOST = 0x4, 0x20, 0x100, 0x10000, 0x40000
    MB_OK, MB_ICONERROR, MB_ICONINFORMATION = 0x0, 0x10, 0x40
    TDF_ALLOW_DIALOG_CANCELLATION, TDF_SIZE_TO_CONTENT = 0x8, 0x1000000
    TDCBF_CANCEL_BUTTON, TDCBF_CLOSE_BUTTON = 0x8, 0x20
    TD_ERROR_ICON, TD_INFORMATION_ICON, TD_SHIELD_ICON = 0xFFFE, 0xFFFD, 0xFFFC

    def __init__(self, *args, user32=None, comctl32=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user32, self.comctl32 = user32, comctl32
        self.text, self.info = '', None

    def show(self, text):
        super().show(text)
        self.text = text

    def remember(self, info):
        """Structured plan: instruction, content lines, details lines, button label."""
        self.info = info

    # -- native boundaries (replaced by fakes in tests) --
    def _user32(self):
        if self.user32 is None:
            import ctypes
            self.user32 = ctypes.WinDLL('user32', use_last_error=True)
        return self.user32

    def _task_dialog(self, instruction, content, details, buttons, default, icon, common):
        """Returns the pressed button id, or None when TaskDialog is unavailable."""
        try:
            if self.comctl32 is not None:
                return self.comctl32.task_dialog(instruction, content, details, buttons, default, icon, common)
            import ctypes
            from ctypes import wintypes

            class Button(ctypes.Structure):
                _pack_ = 1
                _fields_ = [('nButtonID', ctypes.c_int), ('pszButtonText', wintypes.LPCWSTR)]

            class Config(ctypes.Structure):
                _pack_ = 1  # commctrl.h declares these under pshpack1
                _fields_ = [('cbSize', wintypes.UINT), ('hwndParent', wintypes.HWND), ('hInstance', wintypes.HINSTANCE),
                            ('dwFlags', ctypes.c_int), ('dwCommonButtons', ctypes.c_int),
                            ('pszWindowTitle', wintypes.LPCWSTR), ('pszMainIcon', ctypes.c_void_p),
                            ('pszMainInstruction', wintypes.LPCWSTR), ('pszContent', wintypes.LPCWSTR),
                            ('cButtons', wintypes.UINT), ('pButtons', ctypes.POINTER(Button)),
                            ('nDefaultButton', ctypes.c_int), ('cRadioButtons', wintypes.UINT),
                            ('pRadioButtons', ctypes.c_void_p), ('nDefaultRadioButton', ctypes.c_int),
                            ('pszVerificationText', wintypes.LPCWSTR), ('pszExpandedInformation', wintypes.LPCWSTR),
                            ('pszExpandedControlText', wintypes.LPCWSTR), ('pszCollapsedControlText', wintypes.LPCWSTR),
                            ('pszFooterIcon', ctypes.c_void_p), ('pszFooter', wintypes.LPCWSTR),
                            ('pfCallback', ctypes.c_void_p), ('lpCallbackData', ctypes.c_void_p), ('cxWidth', wintypes.UINT)]

            comctl32 = ctypes.WinDLL('comctl32', use_last_error=True)
            function = comctl32.TaskDialogIndirect  # AttributeError without Common Controls v6
            array = (Button * len(buttons))(*[Button(i, label) for i, label in buttons]) if buttons else None
            config = Config(cbSize=ctypes.sizeof(Config), dwFlags=self.TDF_ALLOW_DIALOG_CANCELLATION | self.TDF_SIZE_TO_CONTENT,
                            dwCommonButtons=common, pszWindowTitle='AgentMesh', pszMainIcon=icon,
                            pszMainInstruction=instruction, pszContent=content,
                            cButtons=len(buttons), pButtons=array, nDefaultButton=default,
                            pszExpandedInformation=details or None,
                            pszExpandedControlText='Hide details' if details else None,
                            pszCollapsedControlText='Show details' if details else None,
                            pszFooterIcon=self.TD_SHIELD_ICON,
                            pszFooter='Unsigned development build: checksums verified, publisher not verified.')
            pressed = ctypes.c_int(0)
            if function(ctypes.byref(config), ctypes.byref(pressed), None, None) != 0:
                return None
            return pressed.value
        except Exception:
            return None

    def _box(self, text, flags):
        try:
            return self._user32().MessageBoxW(None, text, 'AgentMesh', flags | self.MB_SETFOREGROUND | self.MB_TOPMOST)
        except Exception:
            return None

    def _plan(self, question, yes_label):
        info = self.info or {}
        return (info.get('instruction') or question, '\n'.join(info.get('content') or [self.text.strip()]),
                '\n'.join(info.get('details') or []), info.get('button') or yes_label)

    def decide(self, question, yes_label):
        instruction, content, details, label = self._plan(question, yes_label)
        pressed = self._task_dialog(instruction, content, details, [(self.PROCEED, label)],
                                    self.IDCANCEL, self.TD_SHIELD_ICON, self.TDCBF_CANCEL_BUTTON)
        if pressed is None:
            answer = self._box(instruction + '\n\n' + content, self.MB_YESNO | self.MB_ICONQUESTION | self.MB_DEFBUTTON2)
            if answer is None:
                return super().decide(question, yes_label)  # no dialog at all: console select
            pressed = self.PROCEED if answer == self.IDYES else self.IDCANCEL
        approved = pressed == self.PROCEED
        self._line('Confirmed in dialog: ' + label if approved else 'Cancelled in dialog', 0, final=True)
        return approved

    def result(self, ok, instruction, content, details=''):
        icon = self.TD_INFORMATION_ICON if ok else self.TD_ERROR_ICON
        if self._task_dialog(instruction, content, details, [], self.IDCLOSE, icon, self.TDCBF_CLOSE_BUTTON) is None:
            self._box(instruction + '\n\n' + content + ('\n\n' + details if details else ''),
                      self.MB_OK | (self.MB_ICONINFORMATION if ok else self.MB_ICONERROR))
