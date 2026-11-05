"""Platform-neutral adapter core: turn one chat message into at most one reply, with the sender's own rights.

Effective permission = the adapter agent's broker grant (which scopes it can touch at all) ∩ the channel's scope
(one channel maps to one scope) ∩ the sender's workspace role, resolved from chat links on every message:

- owner, admin, member: ``recall``, ``tasks``, ``task REF``, ``note REF text``, ``remember text``;
- contributor or unlinked: only ``remember text``, and only when the channel's scope is an inbox scope.

The adapter refuses to start with an ``allow_secret`` grant, so ``secret`` memory never reaches a chat vendor, and it
stays silent in channels shared outside the company unless the config allows it. Replies are short and plain;
whatever is posted is then stored by the chat vendor under its own retention.
"""
from ..broker.client import sign_prepared

READERS = ('owner', 'admin', 'member')
MAX_REPLY = 3000
HELP = ('AgentMesh commands: recall <query> · tasks [status] · task <ID> · note <ID> <text> · remember <text> · help. '
        'Replies are stored by this chat service.')


class Refused(ValueError): pass


def parse_command(text):
    words = (text or '').strip().split(None, 1)
    if not words: return 'help', ''
    command, rest = words[0].lower(), (words[1] if len(words) > 1 else '')
    return (command, rest.strip()) if command in ('recall', 'tasks', 'task', 'note', 'remember', 'help') else ('help', '')


def _line(text, limit=200):
    first = next((l.strip() for l in (text or '').splitlines() if l.strip()), '')
    return first if len(first) <= limit else first[:limit - 1] + '…'


class Adapter:
    def __init__(self, client, config, identify, inbox_scopes, platform):
        """``identify(user) -> (account or None, role or None)``; ``inbox_scopes() -> set of scope names``."""
        who = client.request(dict(op='whoami'))
        if 'error' in who: raise Refused('adapter agent unavailable')
        if who.get('allow_secret'): raise Refused('adapter grant must not allow secret memory')
        channels = config.get('channels')
        if type(channels) is not dict or not channels: raise Refused('no channels configured')
        for scope in channels.values():
            if scope not in who['read'] and scope not in who['write']: raise Refused('channel scope outside the adapter grant: ' + str(scope))
        self.client, self.config, self.identify, self.inbox_scopes, self.platform = client, config, identify, inbox_scopes, platform
        self.profile = config.get('profile')

    def handle(self, channel, user, text, *, shared=False):
        scope = self.config['channels'].get(channel)
        if scope is None: return None                       # not a mapped channel: stay silent
        if shared and not self.config.get('allow_shared_channels', False):
            return 'This channel is shared outside the organization, so AgentMesh does not answer here.'
        account, role = self.identify(user)
        command, rest = parse_command(text)
        if command == 'help': return HELP
        if command == 'remember':
            if not rest: return HELP
            if role not in READERS and scope not in self.inbox_scopes():
                return 'Link your chat account to a workspace member first, or use an inbox channel.'
            return self._remember(scope, rest, account)
        if role not in READERS: return 'Only linked workspace members can read here.'
        if command == 'recall': return self._recall(scope, rest) if rest else HELP
        if command == 'tasks': return self._tasks(scope, rest.split() if rest else None)
        if command == 'task': return self._task(scope, rest.split()[0] if rest else None)
        ref, _, body = rest.partition(' ')
        if not ref or not body.strip(): return HELP
        return self._note(scope, ref, body.strip(), account)

    def _attribution(self, account):
        return f'[{self.platform}' + (f' {account[:8]}' if account else ' unlinked') + '] '

    def _remember(self, scope, text, account):
        result = self.client.write(dict(kind='fact', content=self._attribution(account) + text), scope=scope,
                                   tags=['chat:' + self.platform])
        return 'Stored.' if 'id' in result else 'Not stored (' + str(result.get('error', 'error')) + ').'

    def _recall(self, scope, query):
        result = self.client.request(dict(op='recall', query=query[:2048], scopes=[scope], limit=5,
                                          **({'profiles': [self.profile]} if self.profile else {})))
        if 'error' in result: return 'Recall failed (' + result['error'] + ').'
        rows = [r for r in result.get('results', []) if r.get('label') in ('general', 'internal')]
        if not rows: return 'Nothing found.'
        return self._fit('\n'.join(f'• [{r["kind"]}] {_line(r["content"])}' for r in rows))

    def _tasks(self, scope, status):
        request = dict(op='tasks', scope=scope, **({'status': status} if status else {}))
        result = self.client.request(request)
        if 'error' in result: return 'Tasks failed (' + result['error'] + ').'
        tasks = [t for t in result['tasks'] if t.get('label') in ('general', 'internal')]
        if not tasks: return 'No tasks.'
        return self._fit('\n'.join(f'• {t["task"]} [{t["status"]}] {_line(t["title"], 120)}' + (' (lease live)' if t['lease_live'] else '')
                                   for t in tasks[:30]))

    def _task(self, scope, ref):
        if not ref: return HELP
        result = self.client.request(dict(op='tasks', scope=scope, task=ref))
        if 'error' in result or not result.get('tasks'): return 'No such task here.'
        t = result['tasks'][0]
        lines = [f'{t["task"]} [{t["status"]}] {_line(t["title"], 160)}']
        if t.get('next'): lines.append('Next: ' + _line(t['next']))
        if t.get('handoff'): lines.append('Handoff: ' + _line(t['handoff']))
        notes = self.client.request(dict(op='notes', task=t['task'], scope=scope, limit=5))
        for n in notes.get('notes', []) if 'error' not in notes else []:
            if n.get('label') in ('general', 'internal'): lines.append('– ' + _line(n['text'], 160))
        return self._fit('\n'.join(lines))

    def _note(self, scope, ref, text, account):
        response = self.client.request(dict(op='note', task=ref, scope=scope, text=self._attribution(account) + text, to=None))
        if 'error' in response: return 'Note not added (' + response['error'] + ').'
        result = sign_prepared(self.client, response, None)
        return 'Note added.' if 'id' in result else 'Note not added (' + str(result.get('error', 'error')) + ').'

    @staticmethod
    def _fit(text): return text if len(text) <= MAX_REPLY else text[:MAX_REPLY - 1] + '…'
