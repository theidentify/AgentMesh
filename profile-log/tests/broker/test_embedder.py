import pytest
from agentmesh_log.broker import embedder

DIGEST = 'a'*64

def stub(monkeypatch, digests, vectors):
    seq = iter(digests)
    def request(self, path, payload=None):
        if path == '/api/tags': return {'models': [{'name': 'bge-m3:latest', 'digest': next(seq)}]}
        assert payload['keep_alive'] == '30m' and payload['truncate'] is False
        return {'model': 'bge-m3:latest', 'embeddings': vectors}
    monkeypatch.setattr(embedder.OllamaEmbedding, '_request', request)

def test_embed_normalizes_and_pins_space(monkeypatch):
    stub(monkeypatch, [DIGEST]*3, [[3.0, 4.0]])
    e = embedder.OllamaEmbedding()
    assert e.embed(['x']) == [[pytest.approx(0.6), pytest.approx(0.8)]]
    assert e.space == dict(provider='ollama', model='bge-m3:latest', revision=DIGEST, metric='cosine')

def test_revision_change_discards_batch(monkeypatch):
    stub(monkeypatch, [DIGEST, DIGEST, 'b'*64], [[1.0, 0.0]])
    with pytest.raises(RuntimeError, match='revision changed'): embedder.OllamaEmbedding().embed(['x'])

@pytest.mark.parametrize('url', ['http://10.0.0.1:11434', 'http://localhost:11434', 'ftp://127.0.0.1'], ids=['remote','dns','ftp'])
def test_http_loopback_only(url):
    with pytest.raises(ValueError): embedder.OllamaEmbedding(base_url=url)

ACCESS = {'CF-Access-Client-Id': 'a'*32+'.access', 'CF-Access-Client-Secret': 'b'*64}

@pytest.mark.parametrize('url,headers', [
    ('https://embed.example.com', None),                         # remote needs Access headers
    ('https://embed.example.com/api', ACCESS),                   # no path
    ('https://user@embed.example.com', ACCESS),                  # no userinfo
    ('https://Embed.Example.com', ACCESS),                       # lowercase DNS only
    ('http://127.0.0.1:11434', ACCESS),                          # loopback takes no headers
    ('https://embed.example.com', {'CF-Access-Client-Id': 'x'}), # exact header set
], ids=['auth','path','user','case','http','keys'])
def test_remote_endpoint_rules(url, headers):
    with pytest.raises(ValueError): embedder.OllamaEmbedding(base_url=url, headers=headers)

class FakeResponse:
    def __init__(self, status, body): self.status, self.body = status, body
    def read(self, n): return self.body[:n]

class FakeConnection:
    instances = []
    def __init__(self, *a, **kw): self.sent = []; self.closed = False; FakeConnection.instances.append(self)
    def request(self, method, path, body=None, headers=None): self.sent.append((path, headers))
    def getresponse(self): return FakeConnection.replies.pop(0)
    def close(self): self.closed = True

def remote(monkeypatch, replies):
    import json
    FakeConnection.instances = []
    FakeConnection.replies = [FakeResponse(s, json.dumps(b).encode()) for s, b in replies]
    monkeypatch.setattr(embedder, '_IPv4FirstHTTPS', FakeConnection)
    return embedder.OllamaEmbedding(base_url='https://embed.example.com', headers=ACCESS)

TAGS = {'models': [{'name': 'bge-m3:latest', 'digest': DIGEST}]}

def test_remote_headers_and_reuse(monkeypatch):
    e = remote(monkeypatch, [(200, TAGS), (200, TAGS), (200, {'model': 'bge-m3:latest', 'embeddings': [[1.0, 0.0]]}), (200, TAGS)])
    e.embed(['x'])
    assert len(FakeConnection.instances) == 1
    headers = FakeConnection.instances[0].sent[0][1]
    assert headers['CF-Access-Client-Id'] == ACCESS['CF-Access-Client-Id'] and headers['User-Agent'] == 'AgentMesh-embed/1'

@pytest.mark.parametrize('status', [302, 401, 403], ids=['302','401','403'])
def test_access_refusal(monkeypatch, status):
    with pytest.raises(RuntimeError, match='refused credentials'): remote(monkeypatch, [(status, {})])

@pytest.mark.parametrize('value', ['', '30', '10000m', '1d', None, 30],
    ids=['empty','unit','long','day','none','int'])
def test_keep_alive_invalid(value):
    with pytest.raises(ValueError): embedder.OllamaEmbedding(keep_alive=value)


def test_reused_connection_retries_once(monkeypatch):
    e=remote(monkeypatch, [(200,TAGS),(200,TAGS)])
    old=FakeConnection.instances[0]
    def fail(*a,**kw): raise OSError('closed')
    monkeypatch.setattr(old,'request',fail)
    assert e._request('/api/tags')==TAGS
    assert old.closed and len(FakeConnection.instances)==2


def test_retry_failure_closes_connection(monkeypatch):
    e=remote(monkeypatch, [(200,TAGS)])
    def fail(*a,**kw): raise embedder.http.client.HTTPException('closed')
    monkeypatch.setattr(FakeConnection,'request',fail)
    with pytest.raises(RuntimeError,match='^Ollama transport failed; no fallback$'): e._request('/api/tags')
    assert len(FakeConnection.instances)==2 and all(c.closed for c in FakeConnection.instances)


def test_fresh_failure_is_not_retried(monkeypatch):
    def fail(*a,**kw): raise OSError('offline')
    monkeypatch.setattr(FakeConnection,'request',fail)
    with pytest.raises(RuntimeError,match='transport failed'): remote(monkeypatch,[])
    assert len(FakeConnection.instances)==1 and FakeConnection.instances[0].closed


def test_response_budget_closes(monkeypatch):
    e=remote(monkeypatch,[(200,TAGS)])
    FakeConnection.replies=[FakeResponse(200,b' '*(8*1024*1024+1))]
    with pytest.raises(RuntimeError,match='8 MiB'): e._request('/api/tags')
    assert FakeConnection.instances[0].closed

@pytest.mark.parametrize('tls_fail',[False,True],ids=['ok','fail'])
def test_ipv4_first_and_tls_cleanup(monkeypatch,tls_fail):
    from types import SimpleNamespace
    socket=embedder.socket; made=[]; wrapped=[]
    class Sock:
        def __init__(self,family,*a): self.family=family; self.closed=False; made.append(self)
        def settimeout(self,value): assert value==4
        def connect(self,address):
            if self.family==socket.AF_INET: raise OSError('unreachable')
        def close(self): self.closed=True
    def wrap(sock,server_hostname):
        wrapped.append(server_hostname)
        if tls_fail: raise OSError('TLS failed')
        return sock
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**kw:[
        (socket.AF_INET6,socket.SOCK_STREAM,0,'',('::1',443)),
        (socket.AF_INET,socket.SOCK_STREAM,0,'',('127.0.0.1',443))])
    monkeypatch.setattr(socket,'socket',Sock)
    connection=embedder._IPv4FirstHTTPS('embed.example.com',timeout=4)
    connection._context=SimpleNamespace(wrap_socket=wrap)
    if tls_fail:
        with pytest.raises(OSError,match='TLS failed'): connection.connect()
        assert made[-1].closed
    else:
        connection.connect(); assert connection.sock is made[-1]
    assert [s.family for s in made]==[socket.AF_INET,socket.AF_INET6]
    assert made[0].closed and wrapped==['embed.example.com']
