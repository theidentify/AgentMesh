#!/usr/bin/env python3
"""Cross-machine run of the team workspace real-use test, steps 1-6: Alice, Carol and Dave on this Mac,
Bob on Windows, synchronized through the real R2 bucket under the test prefix ``ws-test/``.

Bob's steps are done on Windows (by its Claude session, and by you for presence prompts there). At each one
this runner prints what Bob must do and waits for ``~/amtest/handoff/<name>.json``, which the coordinating
Claude session writes from Bob's report: ``{"ids": {...}, "checks": {"name": true|false}}``. Bob's checks
are recorded with the Mac's in ``~/amtest/results.jsonl``.

Run from the profile-log checkout, with its virtualenv:  python scripts/amtest_cross.py
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import amtest_guided as g                                        # noqa: E402
from amtest_guided import A, C, D, ROOT, run, check, ok, denied, sync, say, find, agent_key, start_broker  # noqa: E402

g.REMOTE_FLAGS = ['--s3-bucket', 'agentmesh', '--s3-prefix', 'ws-test/',
                  '--s3-endpoint', 'https://19d9d3d9e01958698de9225232ade883.r2.cloudflarestorage.com',
                  '--s3-credentials', 'keychain:org.agentmesh.profile-log.r2']
HANDOFF = ROOT / 'handoff'
ids = g.ids


def publish(name, value):
    """Values Bob needs, for the coordinating session to pass to Windows."""
    (HANDOFF / f'mac-{name}.json').write_text(json.dumps(value, indent=1))


def wait_bob(name, what):
    say(f'   Waiting for Bob on Windows: {what}')
    print(f'   (handoff file: ~/amtest/handoff/{name}.json)', flush=True)
    path = HANDOFF / f'{name}.json'
    while not path.exists(): time.sleep(2)
    time.sleep(0.5)
    data = json.loads(path.read_text())
    for check_name, passed in data.get('checks', {}).items(): check('bob/windows: ' + check_name, passed is True)
    ids.update(data.get('ids', {}))
    return data


def step1():
    say('1. Accounts (Alice and Carol here, Bob on Windows)')
    check('alice init', ok(run('broker', 'init', '--home', A, '--profile', 'acme', '--out', ROOT / 'alice-recovery')))
    check('scope acme/mesh', ok(run('broker', 'scope', 'add', '--home', A, 'acme/mesh')))
    check('scope acme/inbox', ok(run('broker', 'scope', 'add', '--home', A, 'acme/inbox')))
    say('   Presence: create the workspace.')
    check('workspace init', ok(run('workspace', 'init', '--home', A, '--profile', 'acme')))
    me = run('workspace', 'account', '--home', A)
    ids.update(alice=me.get('account'), alice_fp=me.get('fingerprint'))
    shown = run('workspace', 'show', '--home', A, '--profile', 'acme'); ids['workspace'] = shown.get('workspace')
    check('one member, alice as owner', [(m['account'], m['role']) for m in shown.get('members', [])] == [(ids['alice'], 'owner')], shown)
    sync(A)
    check('carol init', ok(run('broker', 'init', '--home', C, '--profile', 'carolself', '--out', ROOT / 'carol-recovery')))
    sync(C)
    publish('alice', dict(workspace=ids['workspace'], account=ids['alice'], fingerprint=ids['alice_fp']))


def step2():
    say('2. Bob joins by fingerprint')
    wait_bob('bob-joined', 'init, sync, join with a wrong then the right fingerprint, sync; report account, fingerprint, device')
    sync(A)
    say('   Presence: the next command uses the WRONG fingerprint on purpose; it should be denied.')
    bad = run('workspace', 'member', '--home', A, '--profile', 'acme', '--account', ids['bob'], '--role', 'member',
              '--root-fingerprint', ids['alice_fp'])
    check('member with a wrong root fingerprint is denied', denied(bad), bad)
    say('   Presence: add Bob as member, then grant his device acme/mesh.')
    check('add bob as member', ok(run('workspace', 'member', '--home', A, '--profile', 'acme', '--account', ids['bob'],
                                     '--role', 'member', '--root-fingerprint', ids['bob_fp'])))
    check('grant bob acme/mesh', ok(run('workspace', 'grant', '--home', A, '--device', ids['bob_device'], '--scope', 'acme/mesh')))
    sync(A)
    publish('granted', dict(ok=True))
    wait_bob('bob-ready', 'sync, agent key for acme/mesh, first-access window, broker running')
    shown = run('workspace', 'show', '--home', A, '--profile', 'acme')
    bob = [m for m in shown.get('members', []) if m['account'] == ids['bob']]
    check('alice sees bob as member, pinned', bob == [dict(account=ids['bob'], role='member', pinned=True)], shown.get('members'))


def step3():
    say('3. Tasks and notes across machines')
    start_broker(A)
    if ids.get('alice_key'):                        # resumed: the key exists; its first access still needs a window
        say('   Presence: open the first-access window on alice (type "open").')
        check('first-access window on alice', ok(run('presence', 'open', '--home', A)))
        KA = ids['alice_key']
    else:
        key = agent_key(A, '--name', 'alice-agent', '--read', 'acme/mesh', '--write', 'acme/mesh')
        check('alice agent key', ok(key), key); ids['alice_key'] = KA = key.get('agent_key')
    check('alice creates and claims AM-1', ok(run('task', 'create', 'AM-1', '--scope', 'acme/mesh', '--title', 'Real-use test',
                                                  '--claim', '--next', 'check leases', '--agent-key', KA)))
    sync(A)
    wait_bob('bob-3a', 'sync; tasks shows AM-1 claimed; claim denied; note "Ready when you are"; sync')
    sync(A)
    notes = run('notes', 'AM-1', '--agent-key', KA).get('notes', [])
    mine = [n for n in notes if 'Ready when you are' in (n.get('text') or '')]
    check('alice sees bob\'s note, authored by bob\'s account', bool(mine) and mine[0].get('author', {}).get('account') == ids['bob'], notes)
    check('alice hands off', ok(run('task', 'update', 'AM-1', '--scope', 'acme/mesh', '--status', 'open', '--handoff', 'over to Bob', '--agent-key', KA)))
    sync(A)
    wait_bob('bob-3b', 'sync; claim AM-1 --status in_progress succeeds; sync')
    sync(A)
    t = find(run('tasks', '--agent-key', KA))
    check('alice sees in_progress with the handoff, owned by bob', t.get('status') == 'in_progress' and t.get('handoff') == 'over to Bob'
          and (t.get('owner') or {}).get('account') == ids['bob'], t)
    wait_bob('bob-reopen', 'update AM-1 --status open; sync')
    sync(A)
    say('   Race: Alice claims now without syncing; then Bob claims before syncing.')
    check('alice race claim accepted locally', ok(run('task', 'claim', 'AM-1', '--scope', 'acme/mesh', '--agent-key', KA)))
    publish('alice-claimed', dict(ok=True))
    wait_bob('bob-race', 'claim AM-1 WITHOUT syncing first; then report')
    sync(A)
    publish('alice-synced', dict(ok=True))
    wait_bob('bob-race-sync', 'sync twice; report tasks AM-1 owner and conflict')
    sync(A, A)
    ta = find(run('tasks', '--agent-key', KA))
    bob_view = dict(owner=ids.get('race_owner'), conflict=ids.get('race_conflict'))
    check('both machines agree on the owner, with conflict: true',
          (ta.get('owner') or {}).get('agent') == bob_view['owner'] and ta.get('conflict') is True and bob_view['conflict'] is True,
          [ta, bob_view])


def step4():
    say('4. Review and quotas')
    KA = ids['alice_key']
    say('   Presence: staged review on, quota 5 per agent.')
    check('settings on', ok(run('workspace', 'settings', '--home', A, '--profile', 'acme', '--review-staged', 'on', '--quota-agent', '5')))
    sync(A)
    wait_bob('bob-4', 'sync; note; 4 facts (4th quota_exceeded); AM-2 create --claim then release; sync')
    sync(A)
    plain = run('recall', 'real-use fact', '--agent-key', KA).get('results', [])
    check('plain recall hides bob\'s staged facts', not [r for r in plain if 'real-use fact' in (r.get('content') or '')], plain)
    pending = [r for r in run('recall', 'real-use fact', '--include-pending', '--agent-key', KA).get('results', [])
               if 'real-use fact' in (r.get('content') or '')]
    check('include-pending shows bob\'s 3 facts as pending', len(pending) == 3 and all(r.get('review') == 'pending' for r in pending), pending)
    if pending:
        say('   Presence: open a workspace window on Alice (type "open").')
        check('workspace window on alice', ok(run('presence', 'open', '--home', A, '--class', 'workspace')))
        target = pending[0]['id']
        check('review approves', ok(run('review', target, '--verdict', 'approved', '--agent-key', KA)))
        after = [r for r in run('recall', 'real-use fact', '--agent-key', KA).get('results', []) if r.get('id') == target]
        check('approved fact shows in plain recall as approved', bool(after) and after[0].get('review') == 'approved', after)
    notes = run('notes', 'AM-1', '--agent-key', KA).get('notes', [])
    check('task notes are not staged', any('note while staged' in (n.get('text') or '') for n in notes), notes)
    say('   Presence: staged review off.')
    check('settings off', ok(run('workspace', 'settings', '--home', A, '--profile', 'acme', '--review-staged', 'off')))
    sync(A)


def step6():
    say('6. Removal and key rotation')
    KA, KC = ids['alice_key'], ids['carol_key']
    say('   Presence: remove Bob.')
    removed = run('workspace', 'member', '--home', A, '--profile', 'acme', '--account', ids['bob'], '--role', 'none')
    check('every acme scope rotated, none not_rotated', ok(removed) and bool(removed.get('rotated')) and 'not_rotated' not in removed, removed)
    sync(A)
    check('alice writes after removal', ok(run('remember', 'after removal', '--scope', 'acme/mesh', '--agent-key', KA)))
    sync(A)
    wait_bob('bob-6', 'sync; remember denied; recall "after removal" shows nothing')
    sync(C)
    check('carol can still write to the inbox', ok(run('remember', 'contractor report 2', '--scope', 'acme/inbox', '--agent-key', KC)))


def resume():
    """Continue an interrupted run from step 3 with the homes, workspace and Bob that already exist."""
    ids.update(json.loads((ROOT / 'ids.json').read_text()))
    for key in sorted((A / 'agents').glob('*.key')) if (A / 'agents').exists() else []:
        ids.setdefault('alice_key', str(key))
    for done in HANDOFF.glob('bob-3*.json'): done.rename(done.with_suffix('.old'))


def main():
    resuming = '--resume' in sys.argv
    if resuming: resume()
    elif ROOT.exists() and any(ROOT.iterdir()):
        print(f'{ROOT} is not empty; move it away first (or pass --resume to continue from step 3).'); return 2
    else: ROOT.mkdir(exist_ok=True); HANDOFF.mkdir()
    g.REMOTE = None
    say('AgentMesh team workspace: cross-machine test (Mac: Alice, Carol, Dave; Windows: Bob; R2 prefix ws-test/)')
    print('Type presence words here when asked. Bob\'s steps happen on Windows; this window waits for them.')
    try:
        for step in ((step3, step4, g.step5, step6) if resuming else (step1, step2, step3, step4, g.step5, step6)): step()
    except KeyboardInterrupt: print('\nstopped')
    except Exception as exc: check('runner error', False, repr(exc))
    finally:
        g.stop_brokers()
        (ROOT / 'ids.json').write_text(json.dumps({k: v for k, v in ids.items() if not k.endswith('_key')}, indent=1))
        say(f'Done: {g.tally["pass"]} passed, {g.tally["fail"]} failed. Results: ~/amtest/results.jsonl')


if __name__ == '__main__':
    sys.exit(main())
