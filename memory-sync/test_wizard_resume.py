"""Existing-install interactive CLI runs on disposable state only."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_install_inspect import fixture_install
from test_signed_packets import secure_peers


def run_cli(runtime, inputs=''):
    return subprocess.run([sys.executable, str(Path(__file__).with_name('agentmesh.py')),
                           'wizard-resume', '--runtime', str(runtime)],
                          input=inputs, text=True, capture_output=True, timeout=180 if os.name == 'nt' else 30)


def test_missing_runtime_is_blocked_without_creating_installation(tmp_path):
    runtime = tmp_path / 'missing' / 'runtime.json'
    result = run_cli(runtime)
    assert result.returncode == 1
    assert not runtime.parent.exists()


def test_declined_create_leaves_existing_installation_pending(tmp_path):
    runtime, db, exchange = fixture_install(tmp_path)
    before = db.read_bytes(), runtime.read_bytes()
    result = run_cli(runtime, '\n')
    assert result.returncode == 2, result.stderr
    report = json.loads(result.stdout)
    assert report['policy'] == 'legacy' and report['wizard_step'] == 'identity'
    assert 'Type CREATE' in result.stderr
    assert (db.read_bytes(), runtime.read_bytes()) == before
    assert not (runtime.parent.parent / 'identity').exists()
    assert not (exchange / 'pairing').exists()


def existing_identity(tmp_path):
    import security_wizard
    import signed_packets
    runtime, db, exchange = fixture_install(tmp_path)
    identity = tmp_path / 'custom-keys'
    state = tmp_path / 'custom-state' / 'wizard.json'
    config = json.loads(runtime.read_text())
    config.update(security_dir=str(identity), security_state=str(state))
    runtime.write_text(json.dumps(config))
    signed_packets.init_identity(identity, '00000000-0000-4000-8000-000000000001', 'mac')
    security_wizard.resume(db, exchange, identity, state)
    return runtime, db, exchange, identity, state


def test_custom_security_paths_preserve_identity_and_pending_state(tmp_path):
    import security_wizard
    import signed_packets
    runtime, db, exchange, identity, state = existing_identity(tmp_path)
    public = signed_packets.Security(identity).public
    before = db.read_bytes(), runtime.read_bytes()
    identity_before = {p.name: p.read_bytes() for p in identity.iterdir() if p.is_file()}
    state_before = security_wizard.load_state(state)
    result = run_cli(runtime, '\n\n')
    assert result.returncode == 2, result.stderr
    assert json.loads(result.stdout)['pairing'] == 'pending'
    assert signed_packets.Security(identity).public == public
    assert {p.name: p.read_bytes() for p in identity.iterdir() if p.is_file()} == identity_before
    state_after = security_wizard.load_state(state)
    assert state_before is not None and state_after is not None
    assert {k: v for k, v in state_after.items() if k != 'updated_at'} == {k: v for k, v in state_before.items() if k != 'updated_at'}
    assert (db.read_bytes(), runtime.read_bytes()) == before
    assert not (runtime.parent / 'security-wizard.json').exists()
    assert not (runtime.parent.parent / 'identity').exists()
    assert not (exchange / 'pairing').exists()
    assert public['key_id'] not in result.stdout
    assert 'private_key' not in result.stdout and 'backup' not in result.stdout
    private_key = json.loads((identity / 'identity.json').read_text())['private_key']
    assert private_key not in result.stdout and private_key not in result.stderr


def test_eof_blocks_without_publishing_or_activating(tmp_path):
    runtime, db, exchange, identity, state = existing_identity(tmp_path)
    before = db.read_bytes(), runtime.read_bytes()
    result = run_cli(runtime)
    assert result.returncode == 1
    assert not result.stdout
    assert (db.read_bytes(), runtime.read_bytes()) == before
    assert not (exchange / 'pairing').exists()


@pytest.mark.parametrize('directory_exists', [False, True])
def test_strict_missing_identity_is_blocked_before_any_wizard_write(tmp_path, directory_exists):
    import sqlite3
    runtime, db, exchange = fixture_install(tmp_path)
    identity = runtime.parent.parent / 'identity'
    if directory_exists:
        identity.mkdir(mode=0o700)
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE _sync_security(sender TEXT, group_id TEXT, node TEXT)')
    result = run_cli(runtime, 'CREATE\nReplacement\n')
    assert result.returncode == 1
    assert not identity.exists() or not list(identity.iterdir())
    assert not (runtime.parent / 'security-wizard.json').exists()
    assert not (runtime.parent / 'security-backups').exists()


@pytest.mark.parametrize('field', ['database', 'exchange', 'node', 'security_dir', 'security_state', 'database_group', 'missing_identity'])
def test_scope_changed_while_prompting_blocks_publication(tmp_path, monkeypatch, field):
    import install_inspect
    import sqlite3
    import shutil
    runtime, db, exchange, identity, state = existing_identity(tmp_path)
    state_at_prompt = []
    def answer(prompt):
        if not state_at_prompt:
            state_at_prompt.append(state.read_bytes())
            config = json.loads(runtime.read_text())
            if field == 'database_group':
                with sqlite3.connect(db) as c:
                    c.execute("UPDATE _sync_config SET group_id='00000000-0000-4000-8000-000000000002'")
            elif field == 'missing_identity':
                shutil.rmtree(identity)
            else:
                config[field] = 'windows' if field == 'node' else str(tmp_path / ('changed-' + field))
                runtime.write_text(json.dumps(config))
            return 'PUBLISH'
        return ''
    monkeypatch.setattr('builtins.input', answer)
    with pytest.raises((ValueError, OSError)):
        install_inspect.wizard_resume(runtime)
    assert state.read_bytes() == state_at_prompt[0]
    assert not (exchange / 'pairing').exists()


def test_missing_persistent_legacy_identity_never_prompts_for_replacement(tmp_path, monkeypatch):
    import install_inspect
    import shutil
    runtime, db, exchange, identity, state = existing_identity(tmp_path)
    before = state.read_bytes()
    shutil.rmtree(identity)
    prompts = []
    monkeypatch.setattr('builtins.input', lambda prompt: prompts.append(prompt) or 'CREATE')
    with pytest.raises(ValueError):
        install_inspect.wizard_resume(runtime)
    assert not prompts
    assert not identity.exists()
    assert state.read_bytes() == before


def test_strict_database_binding_must_match_full_identity_scope(tmp_path):
    import sqlite3
    import signed_packets
    runtime, db, exchange, identity, state = existing_identity(tmp_path)
    public = signed_packets.Security(identity).public
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE _sync_security(sender TEXT, group_id TEXT, node TEXT)')
        c.execute('INSERT INTO _sync_security VALUES(?,?,?)',
                  (public['sender'], '00000000-0000-4000-8000-000000000002', 'mac'))
    before = state.read_bytes()
    result = run_cli(runtime, '\n\n')
    assert result.returncode == 1
    assert state.read_bytes() == before


@pytest.mark.parametrize('inputs, code', [('\n\n\nBOTH\nDRAINED\n\n', 2), ('\n\n\n\nDRAINED\nACTIVATE\n', 1)])
def test_verified_probe_never_implicitly_activates(secure_peers, inputs, code):
    from test_security_wizard import verified
    first, _ = verified(secure_peers)
    runtime = first['database'].with_name('runtime.json')
    runtime.write_text(json.dumps({'database': str(first['database']), 'exchange': str(first['exchange']),
                                  'node': 'mac', 'security_dir': str(first['security_dir']),
                                  'security_state': str(first['state_path'])}))
    before = first['database'].read_bytes()
    result = run_cli(runtime, inputs)
    assert result.returncode == code, result.stderr
    assert 'Type ACTIVATE' in result.stderr
    assert first['database'].read_bytes() == before
    if code == 2:
        assert json.loads(result.stdout)['policy'] == 'legacy'
    else:
        assert not result.stdout


def test_explicit_create_and_publish_only_leave_activation_pending(tmp_path):
    import signed_packets
    runtime, db, exchange = fixture_install(tmp_path)
    before = db.read_bytes()
    result = run_cli(runtime, 'CREATE\nDisposable Fixture\nPUBLISH\n\n')
    assert result.returncode == 2, result.stderr
    identity = runtime.parent.parent / 'identity'
    public = signed_packets.Security(identity).public
    assert (exchange / 'pairing' / 'proposals' / (public['sender'] + '.json')).is_file()
    assert json.loads(result.stdout)['policy'] == 'legacy'
    assert db.read_bytes() == before
    for path in identity.iterdir():
        if path.is_file():
            assert path.read_text() not in result.stdout
