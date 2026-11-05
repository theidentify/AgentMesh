"""Slack over Socket Mode: the adapter opens an outbound WebSocket to Slack, so nothing on the internet calls into
this device and no tunnel or public URL is needed.

Tokens come from the environment only (``AGENTMESH_SLACK_APP_TOKEN`` = ``xapp-…`` for the socket,
``AGENTMESH_SLACK_BOT_TOKEN`` = ``xoxb-…`` for replies). They are never written to disk, logged, or put in error
text. Every envelope is acknowledged first; only ``app_mention`` events from people (not bots, not edits) get a
reply, posted in the message's thread.
"""
import json
import re
import time
import urllib.request

API = 'https://slack.com/api/'
MENTION = re.compile(r'<@[A-Z0-9]+>')


class SlackError(RuntimeError): pass


def _post(url, token, payload, *, opener=urllib.request.urlopen):
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), method='POST',
                                     headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json; charset=utf-8'})
    try:
        with opener(request, timeout=15) as response: answer = json.loads(response.read(1 << 20))
    except Exception: raise SlackError('slack request failed') from None   # never echo tokens or bodies
    if not isinstance(answer, dict) or not answer.get('ok'):
        raise SlackError('slack error: ' + str(answer.get('error', 'unknown') if isinstance(answer, dict) else 'unknown'))
    return answer


class SlackSocket:
    def __init__(self, adapter, app_token, bot_token, *, opener=urllib.request.urlopen, connect=None, sleep=time.sleep):
        if not (type(app_token) is str and app_token.startswith('xapp-')): raise SlackError('AGENTMESH_SLACK_APP_TOKEN must be an xapp- token')
        if not (type(bot_token) is str and bot_token.startswith('xoxb-')): raise SlackError('AGENTMESH_SLACK_BOT_TOKEN must be an xoxb- token')
        if connect is None:
            from websockets.sync.client import connect
        self.adapter, self._app, self._bot, self.opener, self.connect, self.sleep = adapter, app_token, bot_token, opener, connect, sleep
        self._shared = {}

    def socket_url(self):
        url = _post(API + 'apps.connections.open', self._app, {}, opener=self.opener).get('url')
        if type(url) is not str or not url.startswith('wss://'): raise SlackError('slack gave no socket URL')
        return url

    def shared(self, channel):
        """Whether a channel is shared with another organization (Slack Connect); cached for the session."""
        if channel not in self._shared:
            info = _post(API + 'conversations.info', self._bot, {'channel': channel}, opener=self.opener).get('channel', {})
            self._shared[channel] = bool(info.get('is_ext_shared') or info.get('is_pending_ext_shared'))
        return self._shared[channel]

    def reply(self, channel, text, thread):
        _post(API + 'chat.postMessage', self._bot, {'channel': channel, 'text': text, 'thread_ts': thread,
                                                     'unfurl_links': False, 'unfurl_media': False}, opener=self.opener)

    def event(self, event):
        if event.get('type') != 'app_mention' or event.get('bot_id') or event.get('subtype'): return
        channel, user = event.get('channel'), event.get('user')
        if type(channel) is not str or type(user) is not str: return
        text = MENTION.sub('', event.get('text') or '').strip()
        answer = self.adapter.handle(channel, user, text, shared=self.shared(channel))
        if answer: self.reply(channel, answer, event.get('thread_ts') or event.get('ts'))

    def session(self, stop):
        """One socket session; returns when Slack asks to reconnect, the socket closes, or ``stop`` is set."""
        with self.connect(self.socket_url(), open_timeout=15, close_timeout=5, max_size=1 << 20) as socket:
            for raw in socket:
                if stop.is_set(): return
                try: envelope = json.loads(raw)
                except ValueError: continue
                if not isinstance(envelope, dict): continue
                if envelope.get('envelope_id'): socket.send(json.dumps({'envelope_id': envelope['envelope_id']}))
                if envelope.get('type') == 'disconnect': return
                if envelope.get('type') == 'events_api':
                    try: self.event((envelope.get('payload') or {}).get('event') or {})
                    except SlackError: continue          # one failed reply never ends the session

    def run(self, stop, *, max_backoff=60):
        backoff = 1
        while not stop.is_set():
            try: self.session(stop); backoff = 1
            except Exception: self.sleep(backoff); backoff = min(backoff * 2, max_backoff)
