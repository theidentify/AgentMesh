import os
import subprocess
import pytest
from agentmesh_log.control.presence_helper import HelperPresence, safe

pytestmark = pytest.mark.skipif(os.name=='nt', reason='the presence helper is macOS only')


def helper(tmp_path, mode=0o700, dir_mode=0o700):
    directory=tmp_path/'bin'; directory.mkdir(); path=directory/'agentmesh-presence'
    path.write_bytes(b'#!/bin/sh\n'); path.chmod(mode); directory.chmod(dir_mode)
    return path


class Runner:
    def __init__(self, code=0, raises=None): self.code,self.raises,self.calls=code,raises,[]
    def __call__(self, argv, **kw):
        self.calls.append((argv,kw))
        if self.raises: raise self.raises
        return subprocess.CompletedProcess(argv,self.code,b'{}',b'')


def test_approve_stdin(tmp_path):
    path=helper(tmp_path); runner=Runner(0)
    assert HelperPresence(path,runner=runner).confirm('migrate','Move ingest (secret argv)')
    argv,kw=runner.calls[0]
    assert argv==[str(path),'confirm','--action','migrate'] and kw['input']=='Move ingest (secret argv)'.encode()
    assert kw['env']=={'HOME':os.environ.get('HOME','')} and kw['timeout']==75 and kw['capture_output']


@pytest.mark.parametrize('code', [1,2,3,64,-9], ids=['denied','unavail','timeout','usage','killed'])
def test_nonzero(tmp_path, code):
    assert not HelperPresence(helper(tmp_path),runner=Runner(code)).confirm('migrate','x')


@pytest.mark.parametrize('exc', [subprocess.TimeoutExpired('h',75),OSError('exec')], ids=['timeout','oserror'])
def test_run_error(tmp_path, exc):
    assert not HelperPresence(helper(tmp_path),runner=Runner(raises=exc)).confirm('migrate','x')


def test_bad_action(tmp_path):
    runner=Runner(0); presence=HelperPresence(helper(tmp_path),runner=runner)
    assert not presence.confirm('not-an-action','x') and not presence.confirm('migrate',b'x')
    assert runner.calls==[]


def test_available(tmp_path):
    path=helper(tmp_path)
    assert HelperPresence(path,runner=Runner(0)).available()
    runner=Runner(2); assert not HelperPresence(path,runner=runner).available()
    assert runner.calls[0][0]==[str(path),'available'] and runner.calls[0][1]['input']==b''


def test_symlink(tmp_path):
    real=helper(tmp_path); link=real.parent/'link'; link.symlink_to(real); runner=Runner(0)
    assert not safe(link) and not HelperPresence(link,runner=runner).confirm('migrate','x') and runner.calls==[]


@pytest.mark.parametrize('mode,dir_mode', [(0o720,0o700),(0o702,0o700),(0o700,0o770),(0o700,0o707)],
    ids=['group-file','world-file','group-dir','world-dir'])
def test_loose_mode(tmp_path, mode, dir_mode):
    runner=Runner(0); path=helper(tmp_path,mode,dir_mode)
    assert not HelperPresence(path,runner=runner).confirm('migrate','x') and runner.calls==[]


def test_not_a_file(tmp_path):
    runner=Runner(0)
    assert not HelperPresence(tmp_path/'bin'/'nope',runner=runner).available()
    (tmp_path/'d').mkdir(mode=0o700); (tmp_path/'d'/'x').mkdir(mode=0o700)
    assert not HelperPresence(tmp_path/'d'/'x',runner=runner).available() and runner.calls==[]


def test_helper_allows_supervisor_actions():
    """Every action the supervisor hands the Touch ID helper must be in its allowlist (PresenceCore `actions`), or
    the helper exits 64 without a prompt and the app says Touch ID was cancelled. connector.approve uses the CLI's
    presence action 'approve' (found 2026-11-11); moves use 'migrate'; sqlite-archive delete uses 'delete'."""
    import re
    from pathlib import Path
    swift = (Path(__file__).resolve().parents[3]/'control-app/Sources/PresenceCore/Presence.swift').read_text(encoding='utf-8')
    allowed = set(re.findall(r'"([a-z]+)"', re.search(r'public let actions: Set<String> = \[([^\]]*)\]', swift).group(1)))
    connector = (Path(__file__).resolve().parents[2]/'agentmesh_log/connector_cli.py').read_text(encoding='utf-8')
    assert "require('agent','approve'" in connector
    assert {'approve', 'migrate', 'delete'} <= allowed
