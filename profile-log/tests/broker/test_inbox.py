"""W3 contributor inboxes end to end: write-only access through an invite, no read keys."""
import json
from .test_cli import cli
from .test_federation import run, sync, open_windows, agent, write, recall, broker


def test_inbox_write_only(tmp_path):
    alice, carol, remote = tmp_path/'alice', tmp_path/'carol', tmp_path/'remote'
    run('broker', 'init', '--home', alice, '--profile', 'work')
    for scope in ('work/inbox', 'work/mesh'): run('broker', 'scope', 'add', '--home', alice, scope)
    run('workspace', 'init', '--home', alice, '--profile', 'work')
    run('workspace', 'settings', '--home', alice, '--profile', 'work', '--inbox-scope', 'work/inbox')
    assert json.loads(cli('workspace', 'settings', '--home', alice, '--profile', 'work', '--inbox-scope', 'nope').stdout)['error']
    token = tmp_path/'invite.json'
    run('workspace', 'invite', 'create', '--home', alice, '--profile', 'work', '--scope', 'work/inbox', '--role', 'contributor', '--out', token)
    sync(alice, remote)
    run('broker', 'init', '--home', carol, '--profile', 'carolself'); sync(carol, remote)
    run('workspace', 'join', '--home', carol, '--invite', token); sync(carol, remote)
    sync(alice, remote)
    accepted = run('workspace', 'invite', 'accept', '--home', alice, '--profile', 'work')['accepted']
    assert [a['role'] for a in accepted] == ['contributor']
    sync(alice, remote); sync(carol, remote)
    # Carol's device knows the inbox scope and its public key, but holds no read key for anything in work
    b = broker(carol)
    try:
        tree = next(t for t in b.state.trees if t.root.name == 'work')
        inbox = next(s for s in tree.scopes.values() if s.name == 'inbox')
        assert inbox.scope_id not in tree.keys and not tree.keys
        assert inbox.scope_id in b.state.control['write_keys']
        assert all(s.name != 'mesh' for s in tree.scopes.values())    # only the inbox and its ancestors
    finally: b.close()
    open_windows(carol); open_windows(alice)
    path = run('broker', 'grant', '--home', carol, '--name', 'drop', '--write', 'work/inbox')['agent_key']
    from agentmesh_log.encoding import parse
    from agentmesh_log.identity import AgentIdentity
    from pathlib import Path
    record = parse(Path(path).read_bytes())
    drop = AgentIdentity.from_private(record['agent_cert'], bytes.fromhex(record['agent_private_key']))
    assert 'id' in write(carol, drop, 'work/inbox', 'contractor report on the index rebuild')
    from agentmesh_log.broker.service import Session
    b = broker(carol)
    try: assert b.request(Session(drop.certificate()), dict(op='recall', query='index rebuild')) == {'error': 'denied'}
    finally: b.close()                                                  # write-only: cannot read even its own
    sync(carol, remote); sync(alice, remote)
    assert 'contractor report on the index rebuild' in recall(alice, agent(alice, 'work/inbox', 'triage'), 'index rebuild')


def test_inbox_acceptance():
    from agentmesh_log.federation import Federation
    from agentmesh_log.identity import Registry
    from agentmesh_log.model import Account
    from agentmesh_log.workspace import State
    own = Registry(Account().public_record()); other = Registry(Account().public_record())
    class WS:
        def role(self, account, at): return 'contributor'
        def at(self, when): return State(settings={'inbox.scopes': ['11' * 16]})
    fed = Federation(own, {'cc' * 16: other}, {'ab' * 16: own.account_id}, {'ab' * 16: WS()})
    assert fed.accepts('cc' * 16, 'ab' * 16, 0, '11' * 16)
    assert not fed.accepts('cc' * 16, 'ab' * 16, 0, '22' * 16) and not fed.accepts('cc' * 16, 'ab' * 16, 0)
    assert not fed.may_hold('ab' * 16, 'cc' * 16, 0)
