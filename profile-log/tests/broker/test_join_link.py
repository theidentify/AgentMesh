"""One-click join (J1): link, request, admin approval, sealed answer, sync, agent and rules."""
import json
import os
from pathlib import Path
import pytest
from .test_cli import cli
from .test_federation import run, sync, write, recall


@pytest.fixture(autouse=True)
def private_home(tmp_path, monkeypatch):
    """join-link installs agent rules under Path.home() when a run omits --no-rules: never let that be the real home."""
    monkeypatch.setenv('HOME', str(tmp_path / 'user')); monkeypatch.setenv('USERPROFILE', str(tmp_path / 'user'))
    monkeypatch.setenv('AGENTMESH_HOME', str(tmp_path / 'am'))


def out(result):
    return json.loads(result.stdout) if result.stdout.strip() else {}


def admin_workspace(tmp_path):
    alice, remote = tmp_path/'alice', tmp_path/'remote'
    run('broker', 'init', '--home', alice, '--profile', 'work')
    run('broker', 'scope', 'add', '--home', alice, 'work/mesh')
    run('workspace', 'init', '--home', alice, '--profile', 'work')
    sync(alice, remote)
    return alice, remote


def make_link(alice, remote, path, **extra):
    args = ['workspace', 'invite', 'create', '--home', alice, '--profile', 'work', '--scope', 'work/mesh',
            '--link', path, '--label', 'Bob', '--filesystem', remote]
    for k, v in extra.items(): args += ['--' + k.replace('_', '-'), v]
    made = run(*args)
    sync(alice, remote)
    return made


def test_folder_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENTMESH_HOME', str(tmp_path/'am'))
    alice, remote = admin_workspace(tmp_path)
    link = tmp_path/'bob.link'
    made = make_link(alice, remote, link)
    text = link.read_text(encoding='utf-8')
    assert text.startswith('agentmesh://join#') and 'secret_access_key' not in text
    bob = tmp_path/'bob'
    first = run('workspace', 'join-link', '--link-file', link, '--home', bob, '--folder', remote,
                '--recovery-out', tmp_path/'bob-recovery', '--project', str(tmp_path/'proj'))
    assert first['state'] == 'waiting_for_admin' and first['label'] == 'Bob'
    assert len(list((tmp_path/'bob-recovery').iterdir())) == 3
    # a second run while nobody approved just reports the same state
    assert run('workspace', 'join-link', '--link-file', link, '--home', bob, '--folder', remote)['state'] == 'waiting_for_admin'
    pending = run('workspace', 'invite', 'requests', '--home', alice, '--profile', 'work', '--filesystem', remote)['requests']
    assert [(p['slot'], p['ready'], p['role']) for p in pending] == [(made['slot'], True, 'member')]
    accepted = run('workspace', 'invite', 'accept', '--home', alice, '--profile', 'work', '--request', made['slot'],
                   '--filesystem', remote)
    assert accepted['accepted']['role'] == 'member'
    again = cli('workspace', 'invite', 'accept', '--home', alice, '--profile', 'work', '--request', made['slot'], '--filesystem', remote)
    assert out(again) == {'error': 'already_answered'}
    assert run('workspace', 'invite', 'requests', '--home', alice, '--profile', 'work', '--filesystem', remote)['requests'] == []
    sync(alice, remote)
    done = run('workspace', 'join-link', '--link-file', link, '--home', bob, '--folder', remote)
    assert done['state'] == 'ready' and done['scopes'] == ['work/mesh'] and done['rules']['claude'] == 'written'
    assert json.loads((bob/'remote.json').read_text())['filesystem'] == str(remote.resolve())
    # the broker home now syncs from remote.json alone
    assert out(cli('broker', 'sync', '--home', bob))['errors'] == 0
    joins = json.loads((bob/'joins.json').read_text())
    assert [j['state'] for j in joins] == ['ready'] and 'title' not in json.dumps(joins)
    rules = (tmp_path/'user'/'.claude'/'rules'/'agentmesh-workspace.md').read_text()
    assert 'Bob' in rules and done['agent_key'] in rules and str(tmp_path/'proj') in rules
    # the new agent writes, Alice reads it after sync
    from agentmesh_log.encoding import parse
    from agentmesh_log.identity import AgentIdentity
    record = parse(Path(done['agent_key']).read_bytes())
    bob_agent = AgentIdentity.from_private(record['agent_cert'], bytes.fromhex(record['agent_private_key']))
    assert 'id' in write(bob, bob_agent, 'work/mesh', 'joined with one click')
    sync(bob, remote); sync(alice, remote)
    from .test_federation import agent, open_windows
    open_windows(alice)
    assert 'joined with one click' in recall(alice, agent(alice, 'work/mesh', 'alice-agent'), 'one click')
    # finished joins stay finished
    assert run('workspace', 'join-link', '--link-file', link, '--home', bob, '--folder', remote)['state'] == 'ready'


def test_reject(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENTMESH_HOME', str(tmp_path/'am'))
    alice, remote = admin_workspace(tmp_path)
    link = tmp_path/'carol.link'
    made = make_link(alice, remote, link)
    carol = tmp_path/'carol'
    run('workspace', 'join-link', '--link-file', link, '--home', carol, '--folder', remote, '--recovery-out', tmp_path/'rc', '--no-rules')
    run('workspace', 'invite', 'reject', '--home', alice, '--profile', 'work', '--request', made['slot'], '--reason', 'unknown person',
        '--filesystem', remote)
    result = run('workspace', 'join-link', '--link-file', link, '--home', carol, '--folder', remote)
    assert result['state'] == 'rejected' and result['reason'] == 'unknown person'


def test_file_channel(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENTMESH_HOME', str(tmp_path/'am'))
    alice, remote = admin_workspace(tmp_path)
    link = tmp_path/'dave.link'
    made = make_link(alice, remote, link)
    dave = tmp_path/'dave'
    request = tmp_path/'dave-request.json'
    first = run('workspace', 'join-link', '--link-file', link, '--home', dave, '--request-out', request,
                '--recovery-out', tmp_path/'rd', '--no-rules')
    assert first['state'] == 'waiting_for_admin' and request.exists()
    # a request whose objects were swapped fails its checks
    record = json.loads(request.read_text())
    forged = dict(record, device_id='00'*16)
    bad = tmp_path/'forged.json'; bad.write_text(json.dumps(forged))
    listed = run('workspace', 'invite', 'requests', '--home', alice, '--profile', 'work', '--request-file', bad)['requests']
    assert listed[0]['ready'] is False
    answer = tmp_path/'dave-answer.json'
    run('workspace', 'invite', 'accept', '--home', alice, '--profile', 'work', '--request-file', request, '--answer-out', answer,
        '--filesystem', remote)
    sync(alice, remote)
    # the answer is sealed to Dave's request; it says nothing readable about credentials
    assert json.loads(answer.read_text())['verdict'] == 'approved' and not (dave/'remote.json').exists()
    done = run('workspace', 'join-link', '--link-file', link, '--home', dave, '--answer-file', answer, '--folder', remote)
    assert done['state'] == 'ready'


def test_link_shapes():
    from agentmesh_log import invites, joinlink
    from agentmesh_log.federation import account_record, fingerprint
    owner = account_record('ab'*16, 'cd'*32)
    base = invites.token('11'*16, '22'*16, '33'*32, 'ab'*16, fingerprint('cd'*32), 'member', 10**13)
    remote = dict(kind='s3', provider='minio', endpoint='https://minio.example.test', region='us-east-1', bucket='team-ws',
                  prefix='acme', path_style=True, credentials_kind='s3-generic')
    channels = [dict(kind='storage', request_url='https://minio.example.test/team-ws/acme/join/req/x?X-Amz-Signature=1',
                     answer_url='https://minio.example.test/team-ws/acme/join/ans/x?X-Amz-Signature=2'), dict(kind='file')]
    token = invites.token_v2(base, owner, remote, channels, '44'*16, 'Bob')
    link = invites.encode_link(token)
    assert invites.decode_link(link) == token and invites.decode_link(json.dumps(token)) == token
    for bad in (dict(token, owner_fingerprint='0000'), dict(token, remote=dict(remote, endpoint='http://minio.example.test')),
                dict(token, channels=[dict(kind='storage', request_url='https://a.test/x', answer_url='http://b.test/y')]),
                dict(token, remote=dict(remote, credentials='x')), dict(token, role='owner')):
        with pytest.raises(ValueError): invites.read_token(bad)
    with pytest.raises(ValueError): invites.decode_link('agentmesh://join#not base64!')


def test_storage_channel(tmp_path, monkeypatch):
    """The storage channel end to end against an in-memory S3: presigned PUT/GET, a sealed per-member credential,
    stored through the OS-store seam and named in remote.json."""
    monkeypatch.setenv('AGENTMESH_HOME', str(tmp_path/'am'))
    from agentmesh_log import invites, joinlink, joinflow
    from agentmesh_log.federation import account_record
    from agentmesh_log.storage.filesystem import FilesystemStorage
    alice, remote = admin_workspace(tmp_path)
    # Alice makes a v1 invite, then a v2 link by hand around it with storage-channel URLs that the fake opener serves
    # from the same folder the workspace syncs through (it stands in for the bucket).
    run('workspace', 'invite', 'create', '--home', alice, '--profile', 'work', '--scope', 'work/mesh', '--out', tmp_path/'v1.json')
    sync(alice, remote)
    base = json.loads((tmp_path/'v1.json').read_text())
    slot = joinlink.new_slot()
    req_url = f'https://bucket.example.test/join/req/{slot}.json?sig=put'
    ans_url = f'https://bucket.example.test/join/ans/{slot}.json?sig=get'
    run('workspace', 'account', '--home', alice); sync(alice, remote)   # publishes Alice's account record
    root = json.loads((remote/'registry'/'accounts'/(base['owner_account'] + '.json')).read_text())['root_public_key']
    owner = account_record(base['owner_account'], root)
    s3 = dict(kind='s3', provider='aws', endpoint='https://s3.example.test', region='us-east-1', bucket='team-ws', prefix='',
              path_style=False, credentials_kind='s3-aws')
    token = invites.token_v2(base, owner, s3, [dict(kind='storage', request_url=req_url, answer_url=ans_url)], slot, None)
    link = tmp_path/'erin.link'; link.write_text(invites.encode_link(token))
    bucket = FilesystemStorage(remote); calls = []
    def opener(method, url, data):
        calls.append(method); key = url.split('example.test/')[1].split('?')[0]
        if method == 'PUT': bucket.put(key, data); return 200, b''
        try: return 200, bucket.get(key)
        except Exception: return 404, b''
    stored = {}
    erin = tmp_path/'erin'
    from agentmesh_log.presence import ConsolePresence
    from .test_cli import _backend
    console = ConsolePresence(input_fn=lambda p: p.split()[1].rstrip(':'), isatty=lambda: True, output=lambda _: None)
    import argparse
    args = argparse.Namespace(link_file=str(link), home=str(erin), recovery_out=str(tmp_path/'re'), folder=None, request_out=None,
                              answer_file=None, wait=0.0, agent_name=None, no_rules=True, project=None)
    first = joinflow.join_link(args, backend=_backend, console=console, opener=opener)
    assert first['state'] == 'waiting_for_admin' and calls == ['PUT', 'GET']
    creds = tmp_path/'erin-creds.json'
    creds.write_text(json.dumps(dict(kind='s3-aws', access_key_id='AKIA' + 'A'*16, secret_access_key='b'*40)))
    run('workspace', 'invite', 'accept', '--home', alice, '--profile', 'work', '--request', slot, '--filesystem', remote,
        '--member-credentials', f'file:{creds}')
    assert b'AKIA' not in bucket.get(joinlink.answer_key(slot))   # sealed
    # the remote is S3 (not reachable here, and tests never touch a native keychain): stop at syncing
    monkeypatch.setattr(joinflow, '_sync', lambda home, store: {})
    monkeypatch.setattr(joinflow, '_membership', lambda home, store, token: (None, None))
    second = joinflow.join_link(args, backend=_backend, console=console, opener=opener,
                                store_creds=lambda ref, values, kind: stored.update({ref: (values, kind)}))
    assert second['state'] == 'syncing'
    ((ref, (values, kind)),) = stored.items()
    assert ref.endswith('org.agentmesh.ws-' + base['workspace'][:8]) and values == ('AKIA' + 'A'*16, 'b'*40) and kind == 's3-aws'
    saved = json.loads((erin/'remote.json').read_text())
    assert saved['s3_credentials'] == ref and saved['s3_bucket'] == 'team-ws' and 'b' * 40 not in json.dumps(saved) and 'AKIA' not in json.dumps(saved)   # only the reference, never the pair


def test_presign_offline():
    """Presigning is local SigV4 math: no network, and the URL never holds the secret key."""
    import boto3
    from botocore.config import Config
    from agentmesh_log import joinlink
    from agentmesh_log.storage.s3 import S3Storage
    secret = 'S' * 40
    client = boto3.client('s3', endpoint_url='https://minio.example.test', region_name='us-east-1', aws_access_key_id='AKIA' + 'B' * 16,
                          aws_secret_access_key=secret, config=Config(signature_version='s3v4', s3={'addressing_style': 'path'}))
    store = S3Storage('team-ws', prefix='acme', client=client)
    url = joinlink.presign(store, joinlink.request_key('ab' * 16), 'PUT', 3600)
    assert url.startswith('https://minio.example.test/team-ws/acme/join/req/' + 'ab' * 16) and secret not in url
    with pytest.raises(ValueError): joinlink.presign(store, joinlink.request_key('ab' * 16), 'PUT', 8 * 86400)
    remote = joinlink.remote_of(store)
    assert remote == dict(kind='s3', provider='s3', endpoint='https://minio.example.test', region='us-east-1', bucket='team-ws',
                          prefix='acme', path_style=True, credentials_kind='s3-generic')
    joinlink.shape_remote(remote)


def test_credential_kinds(monkeypatch):
    from agentmesh_log.storage import r2creds, StorageError
    assert r2creds.check_values('s3-aws', ('AKIA' + 'C' * 16, 'd/+' + 'e' * 37))
    for kind, values in (('s3-aws', ('AKIB' + 'C' * 16, 'x' * 40)), ('s3-generic', ('ab', 'y' * 8)), ('s3-generic', ('abc', 'has"quote')),
                         ('s3', ('A' * 32, 'b' * 64))):
        with pytest.raises(StorageError): r2creds.check_values(kind, values)
    if os.name == 'nt': return   # the Keychain store is macOS-only (POSIX keychain paths)
    monkeypatch.setattr(r2creds.sys, 'platform', 'darwin')
    seen = {}
    class Result:
        def __init__(self, code=0, out=''): self.returncode, self.stdout = code, out
    def runner(argv, **kw):
        if argv[1] == '-i':
            seen['argv'] = argv; seen['script'] = kw['input']
            for line in kw['input'].splitlines(): parts = line.split(); seen[parts[5]] = parts[7]
            return Result()
        return Result(0, seen.get(argv[argv.index('-a') + 1], '') + '\n')
    r2creds.store_keychain_credentials('org.agentmesh.ws-12345678', ('AKIA' + 'C' * 16, 'z' * 40), runner=runner,
                                       keychain='/tmp/test.keychain-db', kind='s3-aws')
    assert seen['argv'] == ['/usr/bin/security', '-i'] and 'z' * 40 in seen['script']   # secret over stdin, never argv


def test_rules_foreign_files(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENTMESH_HOME', str(tmp_path / 'am'))
    from agentmesh_log import agentrules
    entry = dict(workspace='aa' * 16, label='Acme', role='member', home='/h', agent_key='/h/agents/x.key', scopes=['acme/mesh'],
                 projects=['/p'])
    (tmp_path / '.agents').mkdir()
    assert agentrules.register(entry, root=tmp_path) == {'claude': 'written', 'agents': 'written'}
    target = tmp_path / '.claude' / 'rules' / 'agentmesh-workspace.md'
    target.write_text('my notes\n' + target.read_text() + 'more notes\n')
    assert agentrules.register(dict(entry, label='Acme Co'), root=tmp_path)['claude'] == 'written'
    text = target.read_text()
    assert text.startswith('my notes\n') and text.endswith('more notes\n') and 'Acme Co' in text and text.count(agentrules.BEGIN) == 1
    target.write_text('hand-written rules, no markers\n')
    assert agentrules.register(entry, root=tmp_path)['claude'] == 'refused'
    assert target.read_text() == 'hand-written rules, no markers\n'
