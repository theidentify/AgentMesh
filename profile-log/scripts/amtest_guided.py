#!/usr/bin/env python3
"""Guided run of the team workspace real-use test plan, steps 1-6, on one Mac.

Alice, Bob and Carol are three separate accounts in disposable homes under ~/amtest, and a folder
(~/amtest/remote) stands in for the R2 bucket. Every command runs in a real terminal, so each
presence prompt waits for you to type the word yourself; nothing here answers for you. Each check
is printed as it runs, and recorded in ~/amtest/results.jsonl, with the full output in ~/amtest/run.log.

Run from the profile-log checkout, with its virtualenv:  python scripts/amtest_guided.py
"""
import json
import os
import pty
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path.home() / 'amtest'
A, B, C, D = ROOT / 'alice', ROOT / 'bob', ROOT / 'carol', ROOT / 'dave'
REMOTE = ROOT / 'remote'
CLI = [sys.executable, '-m', 'agentmesh_log.cli']
LOG, RESULTS = ROOT / 'run.log', ROOT / 'results.jsonl'
BOLD, GREEN, RED, DIM, END = '\033[1m', '\033[32m', '\033[31m', '\033[2m', '\033[0m'
brokers, ids, tally = {}, {}, {'pass': 0, 'fail': 0}


def say(text): print(f'\n{BOLD}{text}{END}', flush=True)


def last_json(text):
    """The last top-level JSON object printed (the CLI prints its result last)."""
    decoder, best = json.JSONDecoder(), None
    for at in [0] + [i + 1 for i, ch in enumerate(text) if ch == '\n']:
        if text[at:at + 1] != '{': continue
        try: value, end = decoder.raw_decode(text, at)
        except ValueError: continue
        if not text[end:].strip(): best = value
    return best


def run(*args, label=None):
    """Run one CLI command in a pseudo-terminal (so presence prompts reach you) and return its JSON."""
    argv = CLI + [str(a) for a in args]
    shown = ' '.join(str(a) for a in args).replace(str(ROOT), '~/amtest')
    print(f'{DIM}$ cli {shown}{END}', flush=True)
    chunks = []
    def read(fd):
        data = os.read(fd, 4096); chunks.append(data); return data
    pty.spawn(argv, read)
    text = b''.join(chunks).decode('utf-8', 'replace').replace('\r\n', '\n')
    with LOG.open('a') as log: log.write(f'$ cli {shown}\n{text}\n')
    return last_json(text) or {'error': 'no_json_output'}


def check(name, ok, detail=None):
    tally['pass' if ok else 'fail'] += 1
    mark = f'{GREEN}PASS{END}' if ok else f'{RED}FAIL{END}'
    print(f'  [{mark}] {name}' + (f'  {DIM}{json.dumps(detail)[:300]}{END}' if detail is not None and not ok else ''), flush=True)
    with RESULTS.open('a') as out: out.write(json.dumps(dict(check=name, ok=bool(ok), detail=detail)) + '\n')
    return ok


def agent_key(home, *args):
    """Create an agent key, then open this home's first-access window (a new agent's first request needs it)."""
    key = run('broker', 'grant', '--home', home, *args)
    if ok(key):
        say(f'   Presence: open the first-access window on {home.name} (type "open").')
        check(f'first-access window on {home.name}', ok(run('presence', 'open', '--home', home)))
    return key


def ok(result): return isinstance(result, dict) and 'error' not in result
def denied(result, code='denied'): return isinstance(result, dict) and result.get('error') == code
def sync(*homes):
    for home in homes:
        result = run('broker', 'sync', '--home', home, '--filesystem', REMOTE)
        if not ok(result): check(f'sync {home.name}', False, result)
    time.sleep(2.5)          # running brokers look for synced objects every 2 s


def start_broker(home):
    (home / 'logs').mkdir(exist_ok=True)
    out = open(home / 'logs' / 'broker.out', 'ab')
    brokers[home.name] = subprocess.Popen(CLI + ['broker', 'run', '--home', str(home), '--log-dir', str(home / 'logs')],
                                          stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
    time.sleep(3)
    check(f'broker {home.name} running', brokers[home.name].poll() is None)


def stop_brokers():
    for proc in brokers.values():
        if proc.poll() is None: proc.terminate()
    for proc in brokers.values():
        try: proc.wait(10)
        except subprocess.TimeoutExpired: proc.kill()


def find(tasks, ref='AM-1'):
    for t in (tasks or {}).get('tasks', []):
        if t.get('task') == ref: return t
    return {}


def step1():
    say('1. Three accounts')
    check('alice init', ok(run('broker', 'init', '--home', A, '--profile', 'acme', '--out', ROOT / 'alice-recovery')))
    check('scope acme/mesh', ok(run('broker', 'scope', 'add', '--home', A, 'acme/mesh')))
    check('scope acme/inbox', ok(run('broker', 'scope', 'add', '--home', A, 'acme/inbox')))
    say('   Presence: type the word shown to create the workspace.')
    check('workspace init', ok(run('workspace', 'init', '--home', A, '--profile', 'acme')))
    me = run('workspace', 'account', '--home', A)
    ids.update(alice=me.get('account'), alice_fp=me.get('fingerprint'), alice_device=me.get('device'))
    shown = run('workspace', 'show', '--home', A, '--profile', 'acme')
    ids['workspace'] = shown.get('workspace')
    check('one member, alice as owner', [(m['account'], m['role']) for m in shown.get('members', [])] == [(ids['alice'], 'owner')], shown)
    sync(A)
    check('bob init', ok(run('broker', 'init', '--home', B, '--profile', 'bobself', '--out', ROOT / 'bob-recovery')))
    check('carol init', ok(run('broker', 'init', '--home', C, '--profile', 'carolself', '--out', ROOT / 'carol-recovery')))
    sync(B, C)


def step2():
    say('2. Bob joins by fingerprint')
    wrong = run('workspace', 'join', '--home', B, '--profile-id', ids['workspace'], '--owner-account', ids['alice'],
                '--owner-fingerprint', '0000-0000-0000-0000-0000-0000-0000-0000')
    check('wrong fingerprint is denied', denied(wrong), wrong)
    joined = run('workspace', 'join', '--home', B, '--profile-id', ids['workspace'], '--owner-account', ids['alice'],
                 '--owner-fingerprint', ids['alice_fp'])
    check('join with the right fingerprint', ok(joined), joined)
    bob = run('workspace', 'account', '--home', B)
    ids.update(bob=bob.get('account'), bob_fp=bob.get('fingerprint'), bob_device=bob.get('device'))
    sync(B, A)
    say('   Presence: the next command uses the WRONG fingerprint on purpose. Type the word if asked; it should still be denied.')
    bad = run('workspace', 'member', '--home', A, '--profile', 'acme', '--account', ids['bob'], '--role', 'member',
              '--root-fingerprint', ids['alice_fp'])
    check('member with a wrong root fingerprint is denied', denied(bad), bad)
    say('   Presence: add Bob as member, then grant his device acme/mesh.')
    check('add bob as member', ok(run('workspace', 'member', '--home', A, '--profile', 'acme', '--account', ids['bob'],
                                     '--role', 'member', '--root-fingerprint', ids['bob_fp'])))
    check('grant bob acme/mesh', ok(run('workspace', 'grant', '--home', A, '--device', ids['bob_device'], '--scope', 'acme/mesh')))
    sync(A, B)
    say('   Presence: create Bob\'s agent key (this also proves acme/mesh exists on his home).')
    key = agent_key(B, '--name', 'bob-agent', '--read', 'acme/mesh', '--write', 'acme/mesh')
    check('bob has acme/mesh (agent grant succeeds)', ok(key), key); ids['bob_key'] = key.get('agent_key')
    shown = run('workspace', 'show', '--home', A, '--profile', 'acme')
    bob = [m for m in shown.get('members', []) if m['account'] == ids['bob']]
    check('alice sees bob as member, pinned', bob == [dict(account=ids['bob'], role='member', pinned=True)], shown.get('members'))


def step3():
    say('3. Tasks and notes across accounts')
    start_broker(A); start_broker(B)
    say('   Presence: create Alice\'s agent key.')
    key = agent_key(A, '--name', 'alice-agent', '--read', 'acme/mesh', '--write', 'acme/mesh')
    check('alice agent key', ok(key), key); ids['alice_key'] = KA = key.get('agent_key'); KB = ids['bob_key']
    created = run('task', 'create', 'AM-1', '--scope', 'acme/mesh', '--title', 'Real-use test', '--claim', '--next', 'check leases', '--agent-key', KA)
    check('alice creates and claims AM-1', ok(created), created)
    sync(A, B)
    seen = find(run('tasks', '--agent-key', KB))
    check('bob sees AM-1 claimed', seen.get('status') == 'claimed', seen)
    check('bob\'s claim is denied (live lease)', denied(run('task', 'claim', 'AM-1', '--scope', 'acme/mesh', '--agent-key', KB)))
    check('bob adds a note', ok(run('task', 'note', 'AM-1', '--scope', 'acme/mesh', '--text', 'Ready when you are', '--agent-key', KB)))
    sync(B, A)
    notes = run('notes', 'AM-1', '--agent-key', KA).get('notes', [])
    mine = [n for n in notes if 'Ready when you are' in (n.get('text') or '')]
    check('alice sees bob\'s note, authored by bob\'s account', bool(mine) and mine[0].get('author', {}).get('account') == ids['bob'], notes)
    check('alice hands off', ok(run('task', 'update', 'AM-1', '--scope', 'acme/mesh', '--status', 'open', '--handoff', 'over to Bob', '--agent-key', KA)))
    sync(A, B)
    took = run('task', 'claim', 'AM-1', '--scope', 'acme/mesh', '--status', 'in_progress', '--agent-key', KB)
    check('bob\'s claim succeeds after handoff', ok(took), took)
    sync(B, A)
    for who, k in (('alice', KA), ('bob', KB)):
        t = find(run('tasks', '--agent-key', k))
        check(f'{who} sees in_progress with the handoff', t.get('status') == 'in_progress' and t.get('handoff') == 'over to Bob', t)
    ids['bob_owner'] = t.get('owner')
    say('   Race: both claim before syncing.')
    check('bob reopens', ok(run('task', 'update', 'AM-1', '--scope', 'acme/mesh', '--status', 'open', '--agent-key', KB)))
    sync(B, A)
    first = run('task', 'claim', 'AM-1', '--scope', 'acme/mesh', '--agent-key', KA)
    second = run('task', 'claim', 'AM-1', '--scope', 'acme/mesh', '--agent-key', KB)
    check('both race claims accepted locally', ok(first) and ok(second), [first, second])
    sync(A, B, A, B)
    ta, tb = find(run('tasks', '--agent-key', KA)), find(run('tasks', '--agent-key', KB))
    check('both devices agree on the owner, with conflict: true',
          ta.get('owner') == tb.get('owner') and ta.get('conflict') is True and tb.get('conflict') is True, [ta, tb])


def step4():
    say('4. Review and quotas')
    KA, KB = ids['alice_key'], ids['bob_key']
    # Bob's agent wrote one note today (step 3); notes and memories count toward the cap, task changes do not.
    say('   Presence: turn on staged review with a quota of 5 per agent.')
    check('settings on', ok(run('workspace', 'settings', '--home', A, '--profile', 'acme', '--review-staged', 'on', '--quota-agent', '5')))
    sync(A, B)
    check('bob\'s note during staging', ok(run('task', 'note', 'AM-1', '--scope', 'acme/mesh', '--text', 'note while staged', '--agent-key', KB)))
    writes = [run('remember', f'real-use fact {n}', '--scope', 'acme/mesh', '--agent-key', KB) for n in range(1, 5)]
    check('bob\'s first 3 facts succeed (2 notes + 3 facts = 5)', all(ok(w) for w in writes[:3]), writes[:3])
    check('bob\'s 4th fact is quota_exceeded', denied(writes[3], 'quota_exceeded'), writes[3])
    # AM-1 belongs to Alice's agent after the race, so use a task Bob's agent owns.
    check('a capped agent can still claim a task', ok(run('task', 'create', 'AM-2', '--scope', 'acme/mesh', '--title', 'Quota check', '--claim', '--agent-key', KB)))
    check('a capped agent can still release it', ok(run('task', 'update', 'AM-2', '--scope', 'acme/mesh', '--status', 'open', '--handoff', 'released while capped', '--agent-key', KB)))
    sync(B, A)
    plain = run('recall', 'real-use fact', '--agent-key', KA).get('results', [])
    check('plain recall hides bob\'s staged facts', not [r for r in plain if 'real-use fact' in (r.get('content') or '')], plain)
    pending = [r for r in run('recall', 'real-use fact', '--include-pending', '--agent-key', KA).get('results', [])
               if 'real-use fact' in (r.get('content') or '')]
    check('include-pending shows them as pending', bool(pending) and all(r.get('review') == 'pending' for r in pending), pending)
    if pending:
        say('   Presence: open a workspace window on Alice (type "open"); review runs through her broker.')
        check('workspace window on alice', ok(run('presence', 'open', '--home', A, '--class', 'workspace')))
        target = pending[0].get('id') or pending[0].get('event_id')
        check('review approves', ok(run('review', target, '--verdict', 'approved', '--agent-key', KA)))
        after = [r for r in run('recall', 'real-use fact', '--agent-key', KA).get('results', []) if (r.get('id') or r.get('event_id')) == target]
        check('approved fact shows in plain recall as approved', bool(after) and after[0].get('review') == 'approved', after)
    notes = run('notes', 'AM-1', '--agent-key', KA).get('notes', [])
    check('task notes are not staged', any('note while staged' in (n.get('text') or '') for n in notes), notes)
    say('   Presence: turn staged review off again.')
    check('settings off', ok(run('workspace', 'settings', '--home', A, '--profile', 'acme', '--review-staged', 'off')))
    sync(A, B)


def step5():
    say('5. Invite and contributor inbox')
    KA = ids['alice_key']
    say('   Presence: set the inbox scope, then create a one-use contributor invite.')
    check('inbox scope', ok(run('workspace', 'settings', '--home', A, '--profile', 'acme', '--inbox-scope', 'acme/inbox')))
    check('invite create', ok(run('workspace', 'invite', 'create', '--home', A, '--profile', 'acme', '--scope', 'acme/inbox',
                                  '--role', 'contributor', '--max-uses', '1', '--days', '1', '--out', ROOT / 'invite.json')))
    sync(A)
    joined = run('workspace', 'join', '--home', C, '--invite', ROOT / 'invite.json')
    check('carol joins with the invite', ok(joined), joined)
    sync(C, A)
    say('   Presence: accept Carol.')
    accepted = run('workspace', 'invite', 'accept', '--home', A, '--profile', 'acme')
    check('invite accept', ok(accepted), accepted)
    sync(A, C)
    carol = run('workspace', 'account', '--home', C).get('account')
    roles = {m['account']: m['role'] for m in run('workspace', 'show', '--home', A, '--profile', 'acme').get('members', [])}
    check('carol is a contributor', roles.get(carol) == 'contributor', roles)
    say('   Reuse: a fourth, brand-new account (Dave) tries the same invite.')
    check('dave init', ok(run('broker', 'init', '--home', D, '--profile', 'daveself', '--out', ROOT / 'dave-recovery')))
    sync(D)
    reuse = run('workspace', 'join', '--home', D, '--invite', ROOT / 'invite.json')
    sync(D, A)
    say('   Presence: accept again; Dave\'s use should be skipped as "no uses left".')
    again = run('workspace', 'invite', 'accept', '--home', A, '--profile', 'acme')
    check('second use of the token is refused (no uses left)', 'no uses left' in json.dumps(again), [reuse, again])
    say('   Presence: create Carol\'s write-only key.')
    key = agent_key(C, '--name', 'drop', '--write', 'acme/inbox')
    check('carol write-only key', ok(key), key); ids['carol_key'] = KC = key.get('agent_key')
    start_broker(C)
    check('carol writes to the inbox', ok(run('remember', 'contractor report', '--scope', 'acme/inbox', '--agent-key', KC)))
    check('carol\'s recall is denied', denied(run('recall', 'contractor report', '--agent-key', KC)))
    sync(C, A)
    found = run('recall', 'contractor report', '--scope', 'acme/inbox', '--agent-key', KA)
    if denied(found):   # alice-agent was granted acme/mesh only; give the inbox to a second key
        say('   Presence: create an Alice key that reads the inbox.')
        ids['alice_inbox_key'] = agent_key(A, '--name', 'alice-inbox', '--read', 'acme/inbox',
                                     '--write', 'acme/inbox').get('agent_key')
        found = run('recall', 'contractor report', '--scope', 'acme/inbox', '--agent-key', ids['alice_inbox_key'])
    check('alice recalls the contractor report', any('contractor report' in (r.get('content') or '') for r in found.get('results', [])), found)


def step6():
    say('6. Removal and key rotation')
    KA, KB, KC = ids['alice_key'], ids['bob_key'], ids['carol_key']
    say('   Presence: remove Bob.')
    removed = run('workspace', 'member', '--home', A, '--profile', 'acme', '--account', ids['bob'], '--role', 'none')
    check('every acme scope rotated, none not_rotated', ok(removed) and bool(removed.get('rotated')) and 'not_rotated' not in removed, removed)
    sync(A)
    wrote = run('remember', 'after removal', '--scope', 'acme/mesh', '--agent-key', KA)
    check('alice writes after removal', ok(wrote), wrote)
    sync(A, B)
    bw = run('remember', 'bob after removal', '--scope', 'acme/mesh', '--agent-key', KB)
    check('bob\'s write is denied', denied(bw), bw)
    br = run('recall', 'after removal', '--agent-key', KB)
    check('bob cannot recall "after removal"', not any((r.get('content') or '') == 'after removal' for r in br.get('results', [])), br)
    sync(C)
    cw = run('remember', 'contractor report 2', '--scope', 'acme/inbox', '--agent-key', KC)
    check('carol can still write to the inbox', ok(cw), cw)


def main():
    if ROOT.exists() and any(ROOT.iterdir()):
        print(f'{ROOT} is not empty; move it away first (it may hold an earlier run).'); return 2
    ROOT.mkdir(exist_ok=True); REMOTE.mkdir()
    say('AgentMesh team workspace: guided real-use test (steps 1-6, three accounts on this Mac)')
    print('When you see "Type <word>:", type that word and press Return. Ctrl-C stops the run.')
    try:
        for step in (step1, step2, step3, step4, step5, step6): step()
    except KeyboardInterrupt: print('\nstopped')
    except Exception as exc: check('runner error', False, repr(exc))
    finally:
        stop_brokers()
        (ROOT / 'ids.json').write_text(json.dumps({k: v for k, v in ids.items() if not k.endswith('_key')}, indent=1))
        say(f'Done: {tally["pass"]} passed, {tally["fail"]} failed. Results: ~/amtest/results.jsonl, log: ~/amtest/run.log')


if __name__ == '__main__':
    sys.exit(main())
