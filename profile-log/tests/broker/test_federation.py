"""W2 end to end: two accounts share one workspace through a filesystem remote."""
import json
from .test_cli import cli, _backend


def run(*args):
    result = cli(*args)
    data = json.loads(result.stdout) if result.stdout.strip() else {}
    assert result.returncode == 0, (args, result.stdout, result.stderr)
    return data


def open_windows(home):
    from agentmesh_log.broker.keystore import BrokerHome
    from agentmesh_log.oskeystore import select_store
    from agentmesh_log.registry_store import now
    from agentmesh_log.presence import CLASSES
    store = select_store(BrokerHome(home), _backend); state = store.load()
    state.control['windows'] = {kind: now() + 890000 for kind in CLASSES}; store.save(state)


def broker(home):
    from agentmesh_log.broker.keystore import BrokerHome
    from agentmesh_log.broker.service import Broker
    from agentmesh_log.oskeystore import select_store
    return Broker(BrokerHome(home), store=select_store(BrokerHome(home), _backend))


def agent(home, scope, name):
    path = run('broker', 'grant', '--home', home, '--name', name, '--read', scope, '--write', scope)['agent_key']
    from agentmesh_log.broker.client import BrokerClient
    from agentmesh_log.encoding import parse
    from agentmesh_log.identity import AgentIdentity
    from pathlib import Path
    record = parse(Path(path).read_bytes())
    return AgentIdentity.from_private(record['agent_cert'], bytes.fromhex(record['agent_private_key']))


def write(home, identity, scope, text):
    from agentmesh_log.broker.service import Session
    from agentmesh_log.events import EVENT_DOMAIN
    from agentmesh_log.encoding import typed
    b = broker(home); s = Session(identity.certificate())
    try:
        r = b.request(s, dict(op='remember', body=dict(kind='fact', content=text), scope=scope, tags=[], task=None, label=None))
        if r.get('stage') != 'sign': return r
        return b.request(s, dict(op='sign', signature=identity._private.sign(EVENT_DOMAIN + typed(r['unsigned'])).hex()))
    finally: b.close()


def recall(home, identity, query):
    from agentmesh_log.broker.service import Session
    b = broker(home)
    try: return [r['content'] for r in b.request(Session(identity.certificate()), dict(op='recall', query=query))['results']]
    finally: b.close()


def sync(home, remote): run('broker', 'sync', '--home', home, '--filesystem', remote)


def test_two_accounts_share_a_workspace(tmp_path):
    alice, bob, carol, remote = tmp_path/'alice', tmp_path/'bob', tmp_path/'carol', tmp_path/'remote'
    run('broker', 'init', '--home', alice, '--profile', 'work')
    run('broker', 'scope', 'add', '--home', alice, 'work/mesh')
    run('workspace', 'init', '--home', alice, '--profile', 'work')
    me = run('workspace', 'account', '--home', alice)
    profile = run('workspace', 'show', '--home', alice, '--profile', 'work')['workspace']
    sync(alice, remote)
    for home, name in ((bob, 'bobself'), (carol, 'carolself')):
        run('broker', 'init', '--home', home, '--profile', name); sync(home, remote)
    # Bob trusts Alice only with the right fingerprint
    assert json.loads(cli('workspace', 'join', '--home', bob, '--profile-id', profile, '--owner-account', me['account'],
                          '--owner-fingerprint', '0000-0000-0000-0000-0000-0000-0000-0000').stdout) == {'error': 'denied'}
    joined = run('workspace', 'join', '--home', bob, '--profile-id', profile, '--owner-account', me['account'],
                 '--owner-fingerprint', me['fingerprint'])
    run('workspace', 'join', '--home', carol, '--profile-id', profile, '--owner-account', me['account'],
        '--owner-fingerprint', me['fingerprint'])
    carol_device = run('workspace', 'account', '--home', carol)['device']
    sync(bob, remote); sync(carol, remote); sync(alice, remote)
    # Alice adds Bob (pinning his root) and grants his device; Carol is not a member, so no keys for her
    wrong = me['fingerprint']   # someone else's fingerprint must not pin Bob's root
    assert json.loads(cli('workspace', 'member', '--home', alice, '--profile', 'work', '--account', joined['account'],
                          '--role', 'member', '--root-fingerprint', wrong).stdout) == {'error': 'denied'}
    run('workspace', 'member', '--home', alice, '--profile', 'work', '--account', joined['account'], '--role', 'member',
        '--root-fingerprint', joined['fingerprint'])
    assert json.loads(cli('workspace', 'grant', '--home', alice, '--device', carol_device, '--scope', 'work/mesh').stdout)['error']
    run('workspace', 'grant', '--home', alice, '--device', joined['device'], '--scope', 'work/mesh')
    sync(alice, remote); sync(bob, remote)
    # Bob's device now has the workspace scope and writes into it with his own account
    open_windows(bob); open_windows(alice)
    bob_agent = agent(bob, 'work/mesh', 'bob-agent')
    assert 'id' in write(bob, bob_agent, 'work/mesh', 'bob says migrate the index first')
    sync(bob, remote); sync(alice, remote)
    alice_agent = agent(alice, 'work/mesh', 'alice-agent')
    assert 'bob says migrate the index first' in recall(alice, alice_agent, 'migrate index')
    assert 'id' in write(alice, alice_agent, 'work/mesh', 'alice agrees migrate index')
    sync(alice, remote); sync(bob, remote)
    assert 'alice agrees migrate index' in recall(bob, bob_agent, 'migrate index')
    # After Bob is removed, Alice no longer accepts events he writes
    removed = run('workspace', 'member', '--home', alice, '--profile', 'work', '--account', joined['account'], '--role', 'none')
    assert set(removed['rotated']) == {'work', 'work/common', 'work/mesh'} and 'not_rotated' not in removed
    sync(alice, remote); sync(bob, remote)
    # Bob's own broker refuses: his key epoch is no longer current after the rotation
    assert write(bob, bob_agent, 'work/mesh', 'bob late note about migrate index') == {'error': 'denied'}
    sync(bob, remote); sync(alice, remote)
    seen = recall(alice, alice_agent, 'migrate index')
    assert 'bob says migrate the index first' in seen and 'bob late note about migrate index' not in seen
    # Alice's writes after the rotation use a key epoch Bob's device never received
    open_windows(alice)
    assert 'id' in write(alice, alice_agent, 'work/mesh', 'alice private plan to migrate index')
    sync(alice, remote); sync(bob, remote); open_windows(bob)
    assert 'alice private plan to migrate index' not in recall(bob, bob_agent, 'migrate index')
    assert 'alice private plan to migrate index' in recall(alice, alice_agent, 'migrate index')
