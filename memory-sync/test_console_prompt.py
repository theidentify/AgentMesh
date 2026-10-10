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
    def __init__(self, answer=None, fail=False):
        self.answer, self.fail, self.calls = answer, fail, []

    def MessageBoxW(self, owner, text, title, flags):
        if self.fail:
            raise OSError('no desktop')
        self.calls.append((text, title, flags))
        return self.answer


class Comctl32:
    """Fake TaskDialogIndirect: None means unavailable (no Common Controls v6)."""
    def __init__(self, pressed):
        self.pressed, self.calls = pressed, []

    def task_dialog(self, instruction, content, details, buttons, default, icon, common):
        self.calls.append(dict(instruction=instruction, content=content, details=details, buttons=buttons,
                               default=default, icon=icon, common=common))
        return self.pressed


PLAN = {'instruction': 'Upgrade AgentMesh to 0.2.0-rc.11 (abc1234)?',
        'content': ['From 0.2.0-rc.10 (3d5b785) to 0.2.0-rc.11 (abc1234)', '\u2022 Copy and verify', '', 'Takes 1-3 minutes.',
                    'You can roll back to 0.2.0-rc.10 (3d5b785).'],
        'details': ['Policy: legacy', 'Task: AgentMesh-x'], 'button': 'Upgrade now'}


def gui(pressed=None, answer=None, user32_fail=False, keys=()):
    out = Console()
    g = cp.GuiPrompt(stdin=Console(), stream=out, keys=Keys(*keys), vt=False,
                     user32=User32(answer, fail=user32_fail), comctl32=Comctl32(pressed))
    g.show('console plan text')
    g.remember(PLAN)
    return g, out


@pytest.mark.parametrize('pressed,expected,line', [(1001, True, 'Confirmed in dialog: Upgrade now'),
                                                   (2, False, 'Cancelled in dialog')])
def test_task_dialog_has_labelled_buttons_and_cancel_is_default(pressed, expected, line):
    g, out = gui(pressed=pressed)
    assert g.decide('Upgrade now?', 'Upgrade now') is expected
    call = g.comctl32.calls[0]
    assert call['instruction'] == PLAN['instruction'] and call['buttons'] == [(1001, 'Upgrade now')]
    assert call['default'] == g.IDCANCEL and call['common'] == g.TDCBF_CANCEL_BUTTON
    assert 'Yes =' not in call['content'] and 'You can roll back to 0.2.0-rc.10' in call['content']
    assert call['details'] == 'Policy: legacy\nTask: AgentMesh-x'
    assert out.getvalue().split('\r')[-1].strip() == line
    assert g.user32.calls == []  # no MessageBox when TaskDialog works


@pytest.mark.parametrize('answer,expected', [(6, True), (7, False), (0, False)])
def test_without_task_dialog_messagebox_reads_naturally_and_defaults_to_no(answer, expected):
    g, _ = gui(pressed=None, answer=answer)
    assert g.decide('Upgrade now?', 'Upgrade now') is expected
    text, title, flags = g.user32.calls[0]
    assert text.startswith('Upgrade AgentMesh to 0.2.0-rc.11 (abc1234)?') and 'Yes =' not in text
    assert flags & g.MB_DEFBUTTON2 and flags & g.MB_YESNO and title == 'AgentMesh'


@pytest.mark.parametrize('keys,expected', [(('enter',), False), (('left', 'enter'), True)])
def test_without_any_dialog_the_console_select_takes_over(keys, expected):
    g, out = gui(pressed=None, user32_fail=True, keys=keys)
    assert g.decide('Upgrade now?', 'Upgrade now') is expected
    assert 'Cancel' in out.getvalue()


def test_result_dialog_uses_close_button_and_falls_back():
    g, _ = gui(pressed=8)  # IDCLOSE
    g.result(True, 'Upgrade complete', 'Now using 0.2.0-rc.11.\nWorker restarted and healthy.')
    call = g.comctl32.calls[-1]
    assert call['buttons'] == [] and call['common'] == g.TDCBF_CLOSE_BUTTON and call['icon'] == g.TD_INFORMATION_ICON
    g, _ = gui(pressed=None, answer=1)
    g.result(False, 'Upgrade failed', 'program checksum mismatch', 'Code: GUARD_REFUSED')
    text, _, flags = g.user32.calls[-1]
    assert text.startswith('Upgrade failed') and 'GUARD_REFUSED' in text and flags & g.MB_ICONERROR
    g, _ = gui(pressed=None, user32_fail=True)
    g.result(True, 'Upgrade complete', 'ok')  # neither dialog available: no exception


def visible(frame):
    import re
    return len(re.sub(r'\x1b\[[0-9;]*m', '', frame))


@pytest.mark.parametrize('width', [30, 40, 60])
def test_prompt_lines_fit_narrow_terminals(width, monkeypatch):
    monkeypatch.setattr(cp, 'columns', lambda stream: width)
    p, out = prompt('left', 'right', 'enter', vt=True)
    p.decide('Finish the interrupted upgrade to 0.2.0-rc.9?', 'Finish upgrade')
    q, out2 = prompt(*'x' * 80, 'enter', 'esc', vt=True)
    q.typed('UNINSTALL', 'remove the program files and start-at-login task')
    for text in (out.getvalue(), out2.getvalue()):
        frames = [f for line in text.split('\n') for f in line.split('\r')[1:]]
        assert frames and all(visible(f) <= width - 1 for f in frames), max(map(visible, frames))
