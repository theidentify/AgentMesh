"""SQLite retirement R5b: only listing and deleting the archives R4 left is still possible; there is no rollback."""
import json
import os
from pathlib import Path
import stat

from agentmesh_log.cli import parser
from agentmesh_log.control import sqlite_archive
from agentmesh_log.control.cli import execute
from agentmesh_log.control.layout import Layout


class Yes:
    def __init__(self, answer=True): self.answer, self.asked = answer, []
    def confirm(self, action, summary): self.asked.append(action); return self.answer


def make_archive(root, name='sqlite-mac-20261102-135815', created='2026-11-02T13:58:15+00:00'):
    """An archive as sqlite-retire left it: owner-only folders, read-only files."""
    archive = Path(root)/'backups'/'archive'/name
    (archive/'export').mkdir(parents=True)
    (archive/'staging.db').write_bytes(b'x' * 10)
    (archive/'export'/'_Index.md').write_text('plaintext projection')
    (archive/'manifest.json').write_text(json.dumps(dict(created_at=created, files=[])))
    for path in (archive/'staging.db', archive/'export'/'_Index.md', archive/'manifest.json'): os.chmod(path, 0o400)
    os.chmod(archive/'export', 0o700); os.chmod(archive, 0o700)
    return archive


def test_list_and_delete(tmp_path):
    layout = Layout(tmp_path)
    archive = make_archive(tmp_path)
    (tmp_path/'backups'/'archive'/'unrelated').mkdir()
    rows = sqlite_archive.archives(tmp_path)
    assert [r['archive'] for r in rows] == [archive.name] and rows[0]['bytes'] > 10 and 'rollback_open' not in rows[0]
    refused = Yes(False)
    assert sqlite_archive.delete(layout, tmp_path, archive.name, console=refused, helper=None) == {'error': 'presence_required'}
    assert refused.asked == ['delete'] and archive.is_dir()
    # A young archive is deleted at once: with no rollback left, there is no window to wait for.
    assert sqlite_archive.delete(layout, tmp_path, archive.name, console=Yes(), helper=None) == dict(ok=True, deleted=archive.name)
    assert not archive.exists() and sqlite_archive.archives(tmp_path) == []
    outcomes = [json.loads(line)['outcome'] for line in layout.audit.read_text().splitlines()]
    assert outcomes == ['presence_denied', 'ok']


def test_delete_only_archives(tmp_path):
    layout = Layout(tmp_path)
    make_archive(tmp_path)
    for name in ('unrelated', '../sqlite-x', 'sqlite-missing', 'sqlite-mac-20261102-135815/export'):
        assert sqlite_archive.delete(layout, tmp_path, name, console=Yes(), helper=None) == {'error': 'archive not found'}


def test_helper_must_agree(tmp_path):
    archive = make_archive(tmp_path)
    helper = Yes(False)
    assert sqlite_archive.delete(Layout(tmp_path), tmp_path, archive.name, console=Yes(), helper=helper)['error'] == 'presence_required'
    assert helper.asked == ['delete'] and archive.is_dir()


def test_cli(tmp_path):
    layout = Layout(tmp_path)
    assert execute(parser().parse_args(['control', 'sqlite-archive', 'list']), layout=layout) == {'archives': []}
    assert execute(parser().parse_args(['control', 'sqlite-archive', 'delete']), layout=layout) == {'error': 'archive_required'}
    for retired in (['control', 'sqlite-retire'], ['control', 'sqlite-rollback', '--archive', 'x'],
                    ['control', 'sqlite-archive', 'delete', '--archive', 'x', '--before-30-days']):
        try: parser().parse_args(retired)
        except SystemExit: continue
        raise AssertionError(retired)
