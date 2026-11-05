"""Task notes (team workspace W6): async messages between agents and people, attached to a shared task.

A note is an ordinary encrypted, signed event of kind ``task_note`` in the task's scope, tagged with the task and
optionally addressed (``to``: an account or agent ID). Addressing routes attention; it is not secrecy, since every
reader of the scope can read the note. Notes are exempt from staged review, like task state, and count towards
quotas. Delivery takes as long as sync does: this is not a chat.
"""
from ..encoding import hex_bytes

KIND = 'task_note'
MAX_TEXT = 4000


def is_note_body(body):
    return type(body) is dict and (body.get('kind') == KIND or (type(body.get('metadata')) is dict and 'task_note' in body['metadata']))


def body(task, text, to=None):
    if type(text) is not str or not 1 <= len(text) <= MAX_TEXT or not text.strip(): raise ValueError('invalid_request')
    if to is not None:
        try: hex_bytes(to, 16)
        except (ValueError, TypeError): raise ValueError('invalid_request') from None
    return dict(kind=KIND, content=text, task_ref=task, confidence=1.0, metadata=dict(task_note=dict(task=task, to=to)))


def view(row, standing, me):
    author = row.get('_author') or {}
    to = (row.get('_note') or {}).get('to')
    return dict(id=row['id'], at=row['created_at'], scope=row['scope'], text=row['content'], to=to,
                for_me=to is not None and to in me, author=dict(account=author.get('account'), agent=author.get('agent')),
                review=standing.get(row['id'], 'direct'), label=row['label'])
