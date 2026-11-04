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
