import json
from .test_cli import cli


def out(result):
    assert result.returncode in (0, 1), result.stderr
    return json.loads(result.stdout)


def test_workspace_cli(tmp_path):
    home = tmp_path/'home'
    assert cli('broker', 'init', '--home', home, '--profile', 'work').returncode == 0
    assert out(cli('workspace', 'show', '--home', home, '--profile', 'work'))['exists'] is False
    assert out(cli('workspace', 'member', '--home', home, '--profile', 'work', '--account', 'ab'*16,
                   '--role', 'admin')) == {'error': 'not_found'}
    assert out(cli('workspace', 'init', '--home', home, '--profile', 'work'))['ok']
    assert out(cli('workspace', 'init', '--home', home, '--profile', 'work')) == {'error': 'denied'}
    assert out(cli('workspace', 'member', '--home', home, '--profile', 'work', '--account', 'ab'*16, '--role', 'admin'))['ok']
    assert out(cli('workspace', 'settings', '--home', home, '--profile', 'work', '--lease-max-minutes', '30',
                   '--lease-default-minutes', '45')) == {'error': 'invalid_request'}
    assert out(cli('workspace', 'settings', '--home', home, '--profile', 'work', '--lease-max-minutes', '30',
                   '--lease-default-minutes', '10'))['ok']
    shown = out(cli('workspace', 'show', '--home', home, '--profile', 'work'))
    roles = {m['account']: m['role'] for m in shown['members']}
    assert roles['ab'*16] == 'admin' and list(roles.values()).count('owner') == 1
    assert shown['lease'] == {'default_ms': 600_000, 'max_ms': 1_800_000}
    # the only owner cannot remove itself
    me = next(a for a, r in roles.items() if r == 'owner')
    assert out(cli('workspace', 'member', '--home', home, '--profile', 'work', '--account', me, '--role', 'none')) == {'error': 'denied'}
    # write-then-publish path; a stale entry is refused
    entry = tmp_path/'entry.json'
    assert out(cli('workspace', 'member', '--home', home, '--profile', 'work', '--account', 'cd'*16, '--role', 'member',
                   '--out', entry))['written']
    assert out(cli('workspace', 'publish', '--home', home, '--in', entry))['ok']
    assert out(cli('workspace', 'publish', '--home', home, '--in', entry)) == {'error': 'stale'}
    assert out(cli('workspace', 'show', '--home', home, '--profile', 'work'))['head'] is not None


def test_attest_cli(tmp_path, monkeypatch):
    from agentmesh_log import attest
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location('attest_fixtures', pathlib.Path(__file__).parents[1]/'test_attest.py')
    fixtures = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixtures)
    token, JWKS, ISS, AUD = fixtures.token, fixtures.JWKS, fixtures.ISS, fixtures.AUD
    home = tmp_path/'home'
    assert cli('broker', 'init', '--home', home, '--profile', 'work').returncode == 0
    base = ('--home', home, '--profile', 'work')
    assert out(cli('workspace', 'attest', 'admin', *base, '--account', '55'*16, '--channel', 'video',
                   '--email', 'a@example.com')) == {'error': 'not_found'}
    assert out(cli('workspace', 'init', *base))['ok']
    result = cli('workspace', 'attest', 'admin', *base, '--account', '55'*16, '--channel', 'video', '--email', 'Ann@Example.com')
    assert out(result)['ok'] and 'example.com' not in result.stdout.lower()
    listed = out(cli('workspace', 'attest', 'list', *base))['attestations']
    assert [(a['account'], a['method'], a['channel']) for a in listed] == [('55'*16, 'admin', 'video')]
    assert out(cli('workspace', 'attest', 'check', *base, '--email', 'ann@example.com'))['accounts'] == ['55'*16]
    assert out(cli('workspace', 'attest', 'check', *base, '--email', 'bob@example.com'))['accounts'] == []
    # OIDC: a member's token file, verified here against the provider keys (provider calls replaced)
    workspace_id = out(cli('workspace', 'show', *base))['workspace']
    seed = 's' * 32
    nonce = attest.nonce(workspace_id, '66'*16, seed)
    good = dict(issuer=ISS, authorization_endpoint=ISS+'/a', token_endpoint=ISS+'/t', jwks_uri=ISS+'/k')
    monkeypatch.setattr(attest, 'discover', lambda issuer, **_: good)
    monkeypatch.setattr(attest, 'fetch_json', lambda url, **_: JWKS)
    path = tmp_path/'token.json'
    path.write_text(json.dumps(attest.token_file(workspace_id, '66'*16, ISS, AUD, seed, token(nonce=nonce, email='cat@example.com'))))
    assert out(cli('workspace', 'attest', 'oidc', '--home', home, '--in', path, '--issuer', ISS, '--client-id', 'other')) == {'error': 'denied'}
    assert out(cli('workspace', 'attest', 'oidc', '--home', home, '--in', path, '--issuer', ISS, '--client-id', AUD))['ok']
    assert out(cli('workspace', 'attest', 'check', *base, '--email', 'cat@example.com'))['accounts'] == ['66'*16]
    wrong = tmp_path/'wrong.json'   # token bound to another account
    wrong.write_text(json.dumps(attest.token_file(workspace_id, '77'*16, ISS, AUD, seed, token(nonce=nonce))))
    assert out(cli('workspace', 'attest', 'oidc', '--home', home, '--in', wrong, '--issuer', ISS, '--client-id', AUD)) == {'error': 'token_rejected'}
