import io
import json
import threading
import pytest
from agentmesh_log.adapters import links, core, slack
from agentmesh_log import workspace as ws
from agentmesh_log.broker.keystore import BrokerState
from agentmesh_log.storage.filesystem import FilesystemStorage
from agentmesh_log.registry_store import now


def test_links(tmp_path):
    state = BrokerState.create(['work']); storage = FilesystemStorage(tmp_path / 'replica')
    profile, me = state.trees[0].root.scope_id, state.account.account_id
    t = now() + 10_000
    ws.publish(storage, ws.create(state.device, profile, 'genesis', subject=me, role='owner', issued_at=t))
    w = ws.load(storage, profile, state.registry(), me)
    early = links.create(state.device, profile, 'slack', 'U1', 'aa' * 16, issued_at=t - 5000)   # before the workspace
    links.publish(storage, early)
    assert links.resolve(storage, profile, state.registry(), w, 'slack', 'U1') is None
    links.publish(storage, links.create(state.device, profile, 'slack', 'U1', me, issued_at=t + 1))
    assert links.resolve(storage, profile, state.registry(), w, 'slack', 'U1') == me
    assert links.resolve(storage, profile, state.registry(), w, 'slack', 'U2') is None
    assert links.resolve(storage, profile, state.registry(), w, 'discord', 'U1') is None
    record = links.create(state.device, profile, 'slack', 'U1', None, issued_at=t + 2)
    assert 'U1' not in json.dumps(record)                                       # only the hash is stored
    links.publish(storage, record)
    assert links.resolve(storage, profile, state.registry(), w, 'slack', 'U1') is None


class FakeClient:
    def __init__(self, allow_secret=False):
        self.calls, self.allow_secret = [], allow_secret
    def request(self, req):
        self.calls.append(req); op = req['op']
        if op == 'whoami': return dict(read=['work', 'work/mesh', 'work/inbox'], write=['work/mesh', 'work/inbox'], allow_secret=self.allow_secret)
        if op == 'recall': return dict(results=[dict(kind='fact', content='Rebuild the index first\nmore', label='internal'),
                                                dict(kind='fact', content='hidden', label='secret')])
        if op == 'tasks': return dict(tasks=[dict(task='AM-1', status='open', title='Index', lease_live=False, label='general', next='run', handoff=None)])
        if op == 'notes': return dict(notes=[dict(text='looks good', label='general')])
        if op == 'note': return dict(stage='sign', unsigned={})
        return {'error': 'denied'}
    def write(self, body, *, scope=None, tags=None, **_): self.calls.append(dict(op='write', body=body, scope=scope, tags=tags)); return {'id': 'X'}


ROLES = {'Umember': ('ab' * 16, 'member'), 'Ucontrib': ('cd' * 16, 'contributor')}
CONFIG = dict(profile='work', channels={'Cmesh': 'work/mesh', 'Cinbox': 'work/inbox'})


def adapter(client=None, config=CONFIG):
    return core.Adapter(client or FakeClient(), config, lambda u: ROLES.get(u, (None, None)), lambda: {'work/inbox'}, 'slack')


def test_core_permissions(monkeypatch):
    with pytest.raises(core.Refused): adapter(FakeClient(allow_secret=True))
    with pytest.raises(core.Refused): adapter(config=dict(profile='work', channels={'C': 'work/other'}))
    monkeypatch.setattr(core, 'sign_prepared', lambda client, response, scope: {'id': 'N'})
    a = adapter()
    assert a.handle('Cunknown', 'Umember', 'recall x') is None
    answer = a.handle('Cmesh', 'Umember', 'recall index')
    assert 'Rebuild the index first' in answer and 'hidden' not in answer and 'more' not in answer
    assert 'AM-1 [open] Index' in a.handle('Cmesh', 'Umember', 'tasks')
    detail = a.handle('Cmesh', 'Umember', 'task AM-1')
    assert 'Next: run' in detail and 'looks good' in detail
    assert a.handle('Cmesh', 'Umember', 'note AM-1 ship it') == 'Note added.'
    note = [c for c in a.client.calls if c['op'] == 'note'][-1]
    assert note['text'] == '[slack abababab] ship it' and note['task'] == 'AM-1' and note['scope'] == 'work/mesh'
    assert a.handle('Cmesh', 'Ucontrib', 'recall index') == 'Only linked workspace members can read here.'
    assert a.handle('Cmesh', 'Ustranger', 'remember hi').startswith('Link your chat account')
    assert a.handle('Cinbox', 'Ustranger', 'remember report attached') == 'Stored.'
    stored = [c for c in a.client.calls if c['op'] == 'write'][-1]
    assert stored['scope'] == 'work/inbox' and stored['body']['content'] == '[slack unlinked] report attached'
    assert a.handle('Cmesh', 'Umember', 'recall x', shared=True).startswith('This channel is shared')
    assert a.handle('Cmesh', 'Umember', 'whatever') == core.HELP
    recall = [c for c in a.client.calls if c['op'] == 'recall'][0]
    assert recall['scopes'] == ['work/mesh'] and recall['profiles'] == ['work']


class Socket:
    def __init__(self, frames): self.frames, self.sent = frames, []
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def __iter__(self): return iter(self.frames)
    def send(self, data): self.sent.append(json.loads(data))


class Answer(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): pass


def test_slack_session():
    posted = []
    def opener(request, timeout=None):
        body = json.loads(request.data)
        assert request.headers['Authorization'].startswith('Bearer ')
        if request.full_url.endswith('apps.connections.open'):
            assert request.headers['Authorization'] == 'Bearer xapp-1'; return Answer(b'{"ok":true,"url":"wss://x"}')
        if request.full_url.endswith('conversations.info'): return Answer(json.dumps(dict(ok=True, channel=dict(is_ext_shared=body['channel'] == 'Cext'))).encode())
        assert request.headers['Authorization'] == 'Bearer xoxb-2'; posted.append(body); return Answer(b'{"ok":true}')
    seen = []
    class A:
        def handle(self, channel, user, text, shared=False): seen.append((channel, user, text, shared)); return 'answer'
    frames = [json.dumps(dict(type='hello')),
              json.dumps(dict(envelope_id='e1', type='events_api', payload=dict(event=dict(type='app_mention', channel='Cmesh', user='U1', text='<@B1> tasks', ts='1.1')))),
              json.dumps(dict(envelope_id='e2', type='events_api', payload=dict(event=dict(type='app_mention', channel='Cmesh', user='U1', text='x', bot_id='B9')))),
              json.dumps(dict(envelope_id='e3', type='events_api', payload=dict(event=dict(type='app_mention', channel='Cext', user='U2', text='<@B1> recall a', ts='2.2', thread_ts='2.0')))),
              'not json', json.dumps(dict(type='disconnect')), json.dumps(dict(envelope_id='late'))]
    sock = Socket(frames)
    s = slack.SlackSocket(A(), 'xapp-1', 'xoxb-2', opener=opener, connect=lambda url, **kw: sock)
    s.session(threading.Event())
    assert [e['envelope_id'] for e in sock.sent] == ['e1', 'e2', 'e3']          # everything acknowledged, stops at disconnect
    assert seen == [('Cmesh', 'U1', 'tasks', False), ('Cext', 'U2', 'recall a', True)]
    assert [(p['channel'], p['thread_ts'], p['unfurl_links']) for p in posted] == [('Cmesh', '1.1', False), ('Cext', '2.0', False)]
    with pytest.raises(slack.SlackError): slack.SlackSocket(A(), 'xoxb-wrong', 'xoxb-2', connect=lambda *a, **k: None)
    def failing(request, timeout=None): raise OSError('xapp-1 leaked?')
    with pytest.raises(slack.SlackError) as error: slack.SlackSocket(A(), 'xapp-1', 'xoxb-2', opener=failing, connect=lambda *a, **k: None).socket_url()
    assert 'xapp' not in str(error.value) and 'xoxb' not in str(error.value)
