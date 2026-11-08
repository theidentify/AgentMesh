"""Staged writes, review and quotas in a workspace (team workspace W3), computed by every reader alike.

Nothing here trusts a flag the author set. Each reader derives an event's standing from the workspace settings in
force at the event's time (its ULID) and from signed review events:

- ``direct``: the workspace does not stage writes then, the kind is exempt (task state, pages, reviews), or the
  author agent is listed in ``review.direct_agents``.
- ``pending``: staged and not yet reviewed. ``approved`` / ``rejected``: the newest valid review of it decides.
- ``over_quota``: beyond the day's cap for its agent or member in that scope (``quota.*_events_per_day``).

A review is an event of kind ``review`` in the target's scope, naming ``target`` and ``verdict``. It counts when its
author's account held owner, admin or member at the review's time. The broker additionally requires a person at the
device (presence) before it prepares one; other readers cannot check that, which is why the role check exists.
Default recall shows only ``direct`` and ``approved`` events that are within quota.
"""
from .. import ulid

KIND = 'review'
EXEMPT = ('task_state', 'task_note', 'page', KIND)
# Task state changes (claim, handoff, release) never count toward a daily quota and are never refused by one: a cap
# that blocked a release would leave the task leased until its lease ran out. Notes and memories still count.
QUOTA_FREE = ('task_state', KIND)
VERDICTS = ('approved', 'rejected')
REVIEWERS = ('owner', 'admin', 'member')
DAY_MS = 86_400_000
VISIBLE = ('direct', 'approved')


def is_review_body(body):
    return type(body) is dict and (body.get('kind') == KIND or (type(body.get('metadata')) is dict and 'review' in body['metadata']))


def body(target, verdict):
    if verdict not in VERDICTS: raise ValueError('invalid_request')
    ulid.parse(target)
    return dict(kind=KIND, content=f'{verdict} {target}', confidence=1.0, metadata=dict(review=dict(target=target, verdict=verdict)))


def _at(row): return ulid.timestamp(row['id'])


def _target(row):
    meta = row.get('_review')
    if type(meta) is not dict or meta.get('verdict') not in VERDICTS or type(meta.get('target')) is not str: return None
    return meta


def standing(rows, workspace_of):
    """{event id: 'direct'|'pending'|'approved'|'rejected'} and the set of over-quota ids.

    ``workspace_of(profile_name)`` returns the profile's Workspace, or None when it has none."""
    by_id = {r['id']: r for r in rows}
    result, verdicts = {}, {}
    for row in sorted(rows, key=lambda r: r['id']):
        ws = workspace_of(row['profile'])
        if row['kind'] == KIND:
            result[row['id']] = 'direct'
            meta = _target(row); target = by_id.get(meta['target']) if meta else None
            author = row.get('_author') or {}
            if target is None or target['scope'] != row['scope'] or ws is None: continue
            if ws.role(author.get('account'), _at(row)) not in REVIEWERS: continue
            verdicts[target['id']] = meta['verdict']   # rows are in ULID order: the newest valid review wins
            continue
        if ws is None: result[row['id']] = 'direct'; continue
        settings = ws.at(_at(row)).settings
        agent = (row.get('_author') or {}).get('agent')
        staged = settings.get('review.staged', 0) == 1
        result[row['id']] = 'direct' if (not staged or row['kind'] in EXEMPT or agent in settings.get('review.direct_agents', [])) else 'pending'
    for target, verdict in verdicts.items():
        if result.get(target) == 'pending': result[target] = verdict
    return result, over_quota(rows, workspace_of)


def over_quota(rows, workspace_of):
    """Events past their agent's or member's daily cap in one scope, counted in ULID order (UTC days)."""
    counts, over = {}, set()
    for row in sorted(rows, key=lambda r: r['id']):
        ws = workspace_of(row['profile'])
        if ws is None or row['kind'] in QUOTA_FREE: continue
        settings = ws.at(_at(row)).settings
        author = row.get('_author') or {}; day = _at(row) // DAY_MS
        for field, key in (('agent', 'quota.agent_events_per_day'), ('account', 'quota.member_events_per_day')):
            cap = settings.get(key)
            if cap is None: continue
            slot = (field, author.get(field), row['scope'], day)
            counts[slot] = counts.get(slot, 0) + 1
            if counts[slot] > cap: over.add(row['id'])
    return over


def used_today(rows, scope, field, value, now_ms):
    """How many events ``value`` (an agent or account) wrote in ``scope`` today, for the writer-side check."""
    day = now_ms // DAY_MS
    return sum(1 for r in rows if r['scope'] == scope and r['kind'] not in QUOTA_FREE and _at(r) // DAY_MS == day
               and (r.get('_author') or {}).get(field) == value)
