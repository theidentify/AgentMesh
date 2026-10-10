import io


def test_import_bar_uses_real_rows_and_plain_english():
    from terminal_progress import TerminalProgress
    stream = io.StringIO()
    progress = TerminalProgress(stream=stream)
    progress('[4/6] Importing memory')
    progress.rows(60, 100)
    text = stream.getvalue()
    assert '[############--------]' in text
    assert '60%' in text and '60/100 rows' in text
    assert text.isascii()
    assert '%' not in text.splitlines()[0]
    progress.rows(100, 100)
    assert '100%' in stream.getvalue()


def test_unknown_work_animates_on_terminal_without_percent():
    import time
    from terminal_progress import TerminalProgress

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    stream = Terminal()
    with TerminalProgress(stream=stream, interval=0.01) as progress:
        progress('Checking package')
        time.sleep(0.07)
    text = stream.getvalue()
    assert '\r' in text
    assert '[|]' in text and '[/]' in text
    assert '%' not in text
    assert text.isascii()
    assert text.endswith('\n')


def test_wait_displays_next_sync_countdown_without_busy_animation():
    import time
    from terminal_progress import TerminalProgress
    stream = io.StringIO()
    started = time.monotonic()
    with TerminalProgress(stream=stream, interval=0.01) as progress:
        progress.wait(0.04, 'Last sync succeeded')
    assert time.monotonic() - started >= 0.04
    text = stream.getvalue()
    assert 'Last sync succeeded | Next sync in 1s' in text
    assert 'Last sync succeeded | Next sync in 0s' in text
    assert '%' not in text and 'Starting' not in text
    assert text.isascii()


class Console(io.StringIO):
    encoding = 'utf-8'

    def isatty(self):
        return True


def test_live_line_updates_in_place_then_keeps_final_status():
    import terminal_progress
    stream = Console()
    with terminal_progress.Wait('Waiting for the worker to stop', 120, done='Worker stopped',
                                stream=stream, interval=0.01, symbols=terminal_progress.UNICODE):
        __import__('time').sleep(0.08)
    text = stream.getvalue()
    frames, final = text[:-1].split('\r')[1:-1], text.split('\r')[-1]
    assert len(frames) >= 3 and '\n' not in text[:-1]  # one line, rewritten in place
    assert frames[0].startswith('⠋ Waiting for the worker to stop  0s / 120s')
    assert final.startswith('✔ Worker stopped (0s)') and final.endswith('\n')
    assert len(final.rstrip('\n')) >= len(frames[-1])  # padding clears the previous frame


def test_failure_marks_stage_and_ends_the_line_before_errors():
    import terminal_progress
    stream = Console()
    try:
        with terminal_progress.Wait('Starting worker', 60, stream=stream, interval=0.01, symbols=terminal_progress.ASCII):
            raise TimeoutError('fixture')
    except TimeoutError:
        pass
    assert stream.getvalue().split('\r')[-1].startswith('[FAIL] Starting worker (0s)')
    assert stream.getvalue().endswith('\n') and stream.getvalue().isascii()


def test_done_is_idempotent_and_step_has_no_limit():
    import terminal_progress
    stream = Console()
    wait = terminal_progress.step('Updating the start-at-login task', 'Start-at-login task updated', stream=stream)
    wait.done(); wait.done(); wait.fail()
    assert stream.getvalue().count('\n') == 1 and ' / ' not in stream.getvalue()


def test_glyphs_fall_back_to_ascii_where_braille_may_not_render():
    import terminal_progress as tp
    utf8, cp = Console(), type('C', (), {'encoding': 'cp437'})()
    assert tp.glyphs(utf8, environ={}, nt=False) == tp.UNICODE
    assert tp.glyphs(utf8, environ={}, nt=True) == tp.ASCII  # classic conhost
    assert tp.glyphs(utf8, environ={'WT_SESSION': 'x'}, nt=True) == tp.UNICODE
    assert tp.glyphs(cp, environ={'WT_SESSION': 'x'}, nt=True) == tp.ASCII


def test_progress_is_silent_when_stderr_is_captured_or_missing():
    import terminal_progress
    stream = io.StringIO()
    with terminal_progress.Wait('Waiting', 60, stream=stream):
        pass
    with terminal_progress.step('Copying', stream=stream):
        pass
    assert stream.getvalue() == ''
    with terminal_progress.Wait('Waiting', 5, stream=None) as wait:
        wait.tick()
