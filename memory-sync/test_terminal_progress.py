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
