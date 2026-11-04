"""Shared task state (team workspace W0): one keyed task_state chain per task, advisory claims and leases.

A task is a revision chain under memory key ``task/<REF>`` in one scope. Each event's body carries the whole
state in ``metadata.task_state``. Claims are advisory: the log has no global order, so every reader applies the
same rules to the same events and converges after sync.

- An event is valid only against its parent (the event it revises): a live lease held by another agent makes it
  invalid, and so does a malformed state or a lease longer than MAX_LEASE_MS.
- At a fork the valid child with the lowest ULID wins (the first claim); the other branches are superseded.
- Two chains created independently for the same task: the valid root with the lowest ULID wins.

Times are the events' own ``created_at`` (author clock, milliseconds). A skewed clock can end a lease early;
that is the cost of not having a coordinator (see the design's W5 for a strong claim).
"""
import re

PREFIX = 'task/'
KIND = 'task_state'
STATUSES = ('open', 'claimed', 'in_progress', 'blocked', 'review', 'done', 'abandoned')
HELD = ('claimed', 'in_progress', 'blocked')
ROOT = ('open', 'claimed')
TERMINAL = ('done', 'abandoned')
MAX_LEASE_MS = 4 * 3600 * 1000
MIN_LEASE_MS = 60 * 1000
DEFAULT_LEASE_MS = 3600 * 1000
REF = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}')
FIELDS = ('status', 'title', 'owner', 'lease_until', 'next', 'blocked_on', 'handoff')
LIMITS = dict(title=200, next=2000, handoff=4000)
OWNER = ('account', 'device', 'agent')


def ref(value):
    if type(value) is not str or not REF.fullmatch(value): raise ValueError('invalid_request')
    return value.upper()


def key(task): return PREFIX + ref(task)


def is_task_body(body):
    if type(body) is not dict: return False
    meta = body.get('metadata')
    keyed = type(meta) is dict and type(meta.get('memory_key')) is str and meta['memory_key'].startswith(PREFIX)
    return body.get('kind') == KIND or keyed or (type(meta) is dict and 'task_state' in meta)


def shape(state):
    """Whether a stored state is well formed (independent of who wrote it)."""
    if type(state) is not dict or set(state) != set(FIELDS) or state['status'] not in STATUSES: return False
    for name, limit in LIMITS.items():
        value = state[name]
        if name == 'title':
            if type(value) is not str or not 1 <= len(value) <= limit: return False
        elif value is not None and (type(value) is not str or len(value) > limit): return False
    blocked = state['blocked_on']
    if type(blocked) is not list or len(blocked) > 20 or any(type(b) is not str or not 1 <= len(b) <= 200 for b in blocked):
        return False
    owner, lease = state['owner'], state['lease_until']
    if state['status'] in HELD:
        return (type(owner) is dict and set(owner) == set(OWNER) and all(type(owner[k]) is str for k in OWNER)
                and type(lease) is int)
    return owner is None and lease is None


class Fixed:
    """The W0 policy: fixed lease limits and no roles (so no overrides). A workspace replaces it in W1."""
    def lease(self, at): return DEFAULT_LEASE_MS, MAX_LEASE_MS
    def role(self, account, at): return None


FIXED = Fixed()
MANAGERS = ('owner', 'admin')


def live(state, at):
    """Whether a held state's lease is still running at time ``at`` (ms)."""
    return state['status'] in HELD and state['lease_until'] > at


def valid(parent, row, policy=FIXED):
    """Whether ``row`` is a valid successor of ``parent`` (None for a chain root).

    ``policy`` gives the lease limits and roles in force at the event's time (a workspace, or FIXED)."""
    task = row.get('_task')
    if task is None or not shape(task['state']): return False
    state, at, author = task['state'], task['at'], row['_author']
    override = task.get('override') is True
    if override and (parent is None or policy.role(author['account'], at) not in MANAGERS): return False
    if state['status'] in HELD:
        if state['owner'] != author: return False
        if not at < state['lease_until'] <= at + policy.lease(at)[1]: return False
    if parent is None: return state['status'] in ROOT
    before = parent['_task']['state']
    if live(before, at) and before['owner']['agent'] != author['agent'] and not override: return False
    if before['status'] in TERMINAL and state['status'] != 'open': return False
    return True


def resolve(rows, policy=FIXED):
    """The effective head of one task from every event of its chains.

    Returns (head, superseded, ignored): superseded are the first events of losing branches and roots, ignored
    are events that broke the rules (with everything after them). ``head`` is None when no root is valid."""
    by_id = {r['id']: r for r in rows}
    children = {}
    for r in rows:
        if r.get('revises') in by_id: children.setdefault(r['revises'], []).append(r)
    roots = sorted((r for r in rows if r.get('revises') not in by_id), key=lambda r: r['id'])
    good = [r for r in roots if valid(None, r, policy)]
    ignored = [r['id'] for r in roots if r not in good]
    if not good: return None, [], ignored
    node, superseded = good[0], [r['id'] for r in good[1:]]
    while True:
        kids = sorted(children.get(node['id'], []), key=lambda r: r['id'])
        ok = [k for k in kids if valid(node, k, policy)]
        ignored += [k['id'] for k in kids if k not in ok]
        if not ok: return node, superseded, ignored
        superseded += [k['id'] for k in ok[1:]]
        node = ok[0]


def content(state, task):
    """The searchable text of a task event: first line is the tree summary."""
    lines = [f"[{state['status']}] {task}: {state['title']}"]
    if state['next']: lines.append('Next: ' + state['next'])
    if state['blocked_on']: lines.append('Blocked on: ' + ', '.join(state['blocked_on']))
    if state['handoff']: lines.append('Handoff: ' + state['handoff'])
    return '\n'.join(lines)


def view(head, superseded, ignored, at, agent_id):
    """The answer row for one task: state, ownership and conflict facts, no private fields."""
    state = head['_task']['state']
    owner = state['owner']
    return dict(task=head['task_ref'], scope=head['scope'], project=head.get('project'), id=head['id'],
        label=head['label'], updated_at=head['updated_at'], **state, lease_live=live(state, at),
        mine=owner is not None and owner['agent'] == agent_id and live(state, at),
        conflict=bool(superseded), superseded=superseded, ignored=ignored,
        override=head['_task'].get('override') is True)


def successor(action, current, fields, author, at, policy=FIXED):
    """The new state for a write action, or ValueError('denied'/'invalid_request').

    ``current`` is the effective state (None when the task does not exist yet). ``override`` is an owner's or
    admin's write over a live lease; the caller also requires presence for it."""
    if action not in ('create', 'claim', 'update', 'override'): raise ValueError('invalid_request')
    default, maximum = policy.lease(at)
    lease_ms = fields.get('lease_ms', min(default, maximum))
    if type(lease_ms) is not int or not MIN_LEASE_MS <= lease_ms <= maximum: raise ValueError('invalid_request')
    if action == 'override':
        if current is None or policy.role(author['account'], at) not in MANAGERS: raise ValueError('denied')
        state = _apply(dict(current), fields)
        status = fields.get('status', current['status'])
        if status not in STATUSES: raise ValueError('invalid_request')
        if status in HELD: state.update(status=status, owner=author, lease_until=at + lease_ms)
        else: state.update(status=status, owner=None, lease_until=None)
        return state
    if action == 'create':
        if current is not None: raise ValueError('denied')
        if type(fields.get('title')) is not str: raise ValueError('invalid_request')
        state = dict(status='open', title=fields['title'], owner=None, lease_until=None, next=None, blocked_on=[],
                     handoff=None)
        state = _apply(state, fields)
        if fields.get('claim') is True: state.update(status='claimed', owner=author, lease_until=at + lease_ms)
        return state
    if current is None: raise ValueError('denied')
    mine = current['owner'] == author
    if live(current, at) and not mine: raise ValueError('denied')
    state = _apply(dict(current), fields)
    if action == 'claim':
        if current['status'] in TERMINAL: raise ValueError('denied')
        status = fields.get('status', 'claimed')
        if status not in HELD: raise ValueError('invalid_request')
        state.update(status=status, owner=author, lease_until=at + lease_ms)
        return state
    status = fields.get('status', current['status'])
    if status not in STATUSES: raise ValueError('invalid_request')
    if current['status'] in TERMINAL and status != 'open': raise ValueError('denied')
    if status in HELD:
        # Moving into a held status is a claim: only the current holder may do it through update.
        if not (current['status'] in HELD and mine): raise ValueError('denied')
        state.update(status=status, owner=author, lease_until=at + lease_ms)
    else: state.update(status=status, owner=None, lease_until=None)
    return state


def _apply(state, fields):
    for name in ('title', 'next', 'handoff', 'blocked_on'):
        if name in fields: state[name] = fields[name]
    return state
