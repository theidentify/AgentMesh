"""W3 invites end to end: one invite token, two joiners, one use left; a revoked invite admits nobody."""
import json
from .test_cli import cli
from .test_federation import run, sync, open_windows, agent, write, recall


def test_invite_admits_up_to_max_uses(tmp_path):
    alice, bob, carol, remote = tmp_path/'alice', tmp_path/'bob', tmp_path/'carol', tmp_path/'remote'
    run('broker', 'init', '--home', alice, '--profile', 'work')
    run('broker', 'scope', 'add', '--home', alice, 'work/mesh')
    run('workspace', 'init', '--home', alice, '--profile', 'work')
    token = tmp_path/'invite.json'
    made = run('workspace', 'invite', 'create', '--home', alice, '--profile', 'work', '--scope', 'work/mesh',
               '--max-uses', '1', '--days', '2', '--out', token)
    assert 'secret' not in json.dumps(run('workspace', 'invite', 'list', '--home', alice, '--profile', 'work'))
    sync(alice, remote)
    for home, name in ((bob, 'bobself'), (carol, 'carolself')):
        run('broker', 'init', '--home', home, '--profile', name); sync(home, remote)
        assert run('workspace', 'join', '--home', home, '--invite', token)['invite'] == made['invite']
        sync(home, remote)
    sync(alice, remote)
    result = run('workspace', 'invite', 'accept', '--home', alice, '--profile', 'work')
    assert len(result['accepted']) == 1 and result['skipped'][0]['reason'] == 'no uses left'
    first = result['accepted'][0]['account']
    joiner = bob if first == run('workspace', 'account', '--home', bob)['account'] else carol
    sync(alice, remote); sync(joiner, remote)
    roles = {m['account']: m['role'] for m in run('workspace', 'show', '--home', alice, '--profile', 'work')['members']}
    assert roles[first] == 'member'
    assert run('workspace', 'invite', 'list', '--home', alice, '--profile', 'work')['invites'][0]['used'] == 1
    open_windows(joiner); open_windows(alice)
    member_agent = agent(joiner, 'work/mesh', 'joiner-agent')
    assert 'id' in write(joiner, member_agent, 'work/mesh', 'joined through an invite token')
    sync(joiner, remote); sync(alice, remote)
    assert 'joined through an invite token' in recall(alice, agent(alice, 'work/mesh', 'alice-agent'), 'invite token')
    # a revoked invite admits nobody
    second = tmp_path/'second.json'
    made2 = run('workspace', 'invite', 'create', '--home', alice, '--profile', 'work', '--scope', 'work/mesh', '--out', second)
    run('workspace', 'invite', 'revoke', '--home', alice, '--profile', 'work', '--id', made2['invite'])
    other = carol if joiner is bob else bob
    sync(alice, remote); sync(other, remote)
    run('workspace', 'join', '--home', other, '--invite', second); sync(other, remote); sync(alice, remote)
    again = run('workspace', 'invite', 'accept', '--home', alice, '--profile', 'work')
    assert again['accepted'] == [] and {s['reason'] for s in again['skipped']} >= {'revoked'}


def test_use_checks():
    from agentmesh_log import invites
    from agentmesh_log.identity import DeviceIdentity
    secret, other = invites.new_secret(), invites.new_secret()
    tok = invites.token('ab'*16, 'cd'*16, secret, 'ef'*16, 'x', 'member', 10**13)
    use = invites.use_record(DeviceIdentity(), '11'*16, '22'*32, tok, issued_at=1000)
    invite = dict(role='member', scopes=['ab'*16], max_uses=1, expires_at=5000, invite_key=invites.invite_key(secret), revoked=False, used=0)
    class Empty:
        def list(self, prefix): return []
    assert invites.check_use(use, Empty(), invite) == 'device not verified (sync first?)'   # proof fine, device unknown
    assert invites.check_use(use, Empty(), dict(invite, invite_key=invites.invite_key(other))) == 'bad proof'
    assert invites.check_use(use, Empty(), dict(invite, revoked=True)) == 'revoked'
    assert invites.check_use(use, Empty(), dict(invite, expires_at=500)) == 'expired'
    assert invites.check_use(use, Empty(), dict(invite, used=1)) == 'no uses left'
    assert invites.check_use(use, Empty(), None) == 'unknown invite'
    forged = dict(use, account='33'*16)   # the proof binds the account
    assert invites.check_use(forged, Empty(), invite) == 'bad proof'
