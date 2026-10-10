"""Scripted keypresses only; no real terminal mode changes or dialogs."""
import io

import pytest
import console_prompt as cp


class Console(io.StringIO):
    encoding = 'utf-8'

    def isatty(self):
        return True


class Keys:
    def __init__(self, *keys):
        self.keys = list(keys)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, timeout):
        if not self.keys:
            return None  # idle timeout
        key = self.keys.pop(0)
        if key == 'interrupt':
            raise KeyboardInterrupt
        return key


def prompt(*keys, vt=False, stdin=None):
    out = Console()
    return cp.ConsolePrompt(stdin=stdin or Console(), stream=out, keys=Keys(*keys), vt=vt), out


@pytest.mark.parametrize('keys,expected', [
    (('enter',), False),                     # default is Cancel
    (('left', 'enter'), True),
    (('right', 'left', 'enter'), False), (('right', 'left', 'tab', 'enter'), True),
    (('y',), True), (('n',), False), (('esc',), False), (('ctrl-c',), False),
    (('interrupt',), False), ((), False),    # Ctrl+C signal, idle timeout
])
def test_select_defaults_to_cancel_and_moves_with_keys(keys, expected):
    p, out = prompt(*keys)
    assert p.decide('Upgrade now?', 'Upgrade now') is expected
    final = out.getvalue().split('\r')[-1]
    assert final.endswith('\n') and ('❯ Upgrade now' if expected else '❯ Cancel') in final


def test_select_without_raw_keys_falls_back_to_y_n_default_no():
    for answer, expected in (('y\n', True), ('YES\n', True), ('\n', False), ('upgrade\n', False), ('', False)):
        out = Console()
        p = cp.ConsolePrompt(stdin=io.StringIO(answer), stream=out, keys=None)
        p.keys = None
        p._keys = lambda: None
        assert p.decide('Upgrade now?', 'Upgrade now') is expected
        assert '[y/N]' in out.getvalue()


def test_typed_word_accepts_case_insensitive_exact_match_with_live_feedback():
    p, out = prompt('r', 'o', 'l', 'l', 'b', 'a', 'c', 'k', 'enter', vt=True)
    assert p.typed('ROLLBACK', 'switch back to rc.8') is True
    text = out.getvalue()
    assert 'Type ROLLBACK to switch back to rc.8. Esc cancels.' in text
    assert cp.GREEN + 'rol' + cp.RESET + cp.DIM + 'LBACK' in text  # matching prefix + ghost remainder


def test_typed_word_marks_mismatch_and_suggests_the_word():
    p, out = prompt('R', 'O', 'L', 'B', 'enter', 'esc', vt=True)
    assert p.typed('ROLLBACK', 'switch back') is False
    text = out.getvalue()
    assert cp.RED + 'ROLB' in text
    assert 'Did you mean ROLLBACK? (typed ROLB) 2 attempt(s) left.' in text


def test_typed_word_has_three_attempts_and_esc_or_empty_cancels():
    p, out = prompt(*('x', 'enter') * 3)
    assert p.typed('UNINSTALL', 'remove the program') is False
    assert out.getvalue().count('does not match UNINSTALL') == 3
    for keys in (('esc',), ('enter',), ('interrupt',), ()):
        assert prompt(*keys)[0].typed('UNINSTALL', 'remove') is False


def test_typed_word_backspace_and_line_fallback():
    p, _ = prompt('U', 'N', 'X', 'backspace', 'I', 'N', 'S', 'T', 'A', 'L', 'L', 'enter')
    assert p.typed('UNINSTALL', 'remove') is True
    p = cp.ConsolePrompt(stdin=io.StringIO('uninstal\n uninstall \n'), stream=Console())
    p._keys = lambda: None
    assert p.typed('UNINSTALL', 'remove') is True


def test_mismatch_hint_only_for_near_misses():
    assert cp.mismatch('UPGRADE', 'upgarde') == 'Did you mean UPGRADE? (typed upgarde)'
    assert cp.mismatch('UPGRADE', 'hello') == '"hello" does not match UPGRADE.'


def test_not_interactive_when_either_stream_is_redirected():
    assert cp.ConsolePrompt(stdin=io.StringIO(), stream=Console()).interactive() is False
    assert cp.ConsolePrompt(stdin=Console(), stream=io.StringIO()).interactive() is False
    assert cp.ConsolePrompt(stdin=Console(), stream=Console()).interactive() is True


class User32:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def MessageBoxW(self, owner, text, title, flags):
        self.calls.append((text, title, flags))
        return self.answer


@pytest.mark.parametrize('answer,expected', [(6, True), (7, False), (0, False)])
def test_gui_dialog_shows_summary_defaults_to_no_and_reports_result(answer, expected):
    user32 = User32(answer)
    g = cp.GuiPrompt(stdin=Console(), stream=Console(), user32=user32)
    g.show('AgentMesh upgrade plan\n  From: rc.8\n  To: rc.9')
    assert g.decide('Upgrade now?', 'Upgrade now') is expected
    text, title, flags = user32.calls[0]
    assert 'From: rc.8' in text and 'Upgrade now?' in text and title == 'AgentMesh'
    assert flags & g.MB_DEFBUTTON2 and flags & g.MB_YESNO  # No is the default button
    g.result(True, 'Upgraded to rc.9; worker healthy')
    assert user32.calls[1][0] == 'Upgraded to rc.9; worker healthy' and user32.calls[1][2] & g.MB_ICONINFORMATION
