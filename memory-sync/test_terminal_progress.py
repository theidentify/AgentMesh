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
    def isatty(self):
        return True


def test_wait_reports_start_periodic_elapsed_and_finish_on_a_console(monkeypatch):
    import terminal_progress
    clock = [100.0]
    monkeypatch.setattr(terminal_progress.time, 'monotonic', lambda: clock[0])
    stream = Console()
    wait = terminal_progress.Wait('Waiting for worker to stop', 120, stream=stream, every=5)
    for clock[0] in (101.0, 104.0, 105.5, 108.0, 111.0):
        wait.tick()
    wait.done('stopped')
    lines = stream.getvalue().splitlines()
    assert lines[0] == 'Waiting for worker to stop (up to 120s)...'
    assert lines[1:3] == ['  ...5s / 120s', '  ...11s / 120s']
    assert lines[3] == 'Waiting for worker to stop: stopped (11s)'
    assert stream.getvalue().isascii()


def test_wait_and_stage_are_silent_when_stderr_is_captured():
    import terminal_progress
    stream = io.StringIO()
    wait = terminal_progress.Wait('Waiting', 60, stream=stream, every=0)
    wait.tick(); wait.done()
    terminal_progress.stage('Stopping worker', stream=stream)
    assert stream.getvalue() == ''


def test_stage_and_missing_stream_are_safe():
    import terminal_progress
    stream = Console()
    terminal_progress.stage('Updating logon task', stream=stream)
    assert stream.getvalue() == 'Updating logon task...\n'
    terminal_progress.Wait('Waiting', 5, stream=None).tick()  # windowless: sys.stderr may be None
