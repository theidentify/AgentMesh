"""CLI delegates to the same synthetic relocation path, without real services."""
import io
import json

from test_mac_relocate_lifecycle import full_install, installation


def test_cli_dry_run_and_confirmed_move(full_install, monkeypatch, capsys):
    import agentmesh
    import mac_relocate
    options, _, _ = full_install
    original = mac_relocate.relocate
    def relocate(manifest, **kwargs):
        return original(**{**options, 'manifest': manifest}, **kwargs)
    monkeypatch.setattr(mac_relocate, 'relocate', relocate)
    assert agentmesh.main(['mac-relocate', '--manifest', str(options['manifest']), '--dry-run']) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'planned'
    monkeypatch.setattr('sys.stdin', io.StringIO('RELOCATE\nBIND\n'))
    assert agentmesh.main(['mac-relocate', '--manifest', str(options['manifest'])]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'relocated'


def test_cli_rollback_and_failure_exit_codes(monkeypatch, capsys):
    import agentmesh
    import mac_relocate
    calls = []
    def rollback(backup, **kwargs):
        calls.append(backup)
        return {'status': 'rolled_back'}
    monkeypatch.setattr(mac_relocate, 'rollback', rollback)
    assert agentmesh.main(['mac-relocate-rollback', '--backup', '/synthetic/backup']) == 0
    assert calls == ['/synthetic/backup']
    monkeypatch.setattr(mac_relocate, 'relocate', lambda *a, **kw: {'status': 'pending'})
    assert agentmesh.main(['mac-relocate', '--manifest', '/synthetic/manifest']) == 2
    monkeypatch.setattr(mac_relocate, 'relocate', lambda *a, **kw: {'status': 'rolled_back', 'error': 'ValueError'})
    assert agentmesh.main(['mac-relocate', '--manifest', '/synthetic/manifest']) == 1


def test_packaging_contains_relocation():
    from build_package import FILES
    from standalone_build import HIDDEN_MODULES
    assert 'mac_relocate.py' in FILES
    assert 'mac_relocate' in HIDDEN_MODULES
