"""INS-R gates: isolated signing keys and real loopback HTTP, no live Access calls."""
import base64
import http.client
import json
import threading
import time
from unittest.mock import patch

import pytest
import server
import remote_access


def encoded(value):
    raw = json.dumps(value).encode('utf-8') if isinstance(value, dict) else value
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


@pytest.fixture
def signing():
    rsa = pytest.importorskip('cryptography.hazmat.primitives.asymmetric.rsa')
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = key.public_key().public_numbers()
    jwk = {'kty':'RSA', 'kid':'test', 'alg':'RS256', 'use':'sig',
           'n':encoded(public.n.to_bytes(256, 'big')), 'e':encoded(public.e.to_bytes(3, 'big'))}

    def token(claims=None, head=None, bad_signature=False):
        now = int(time.time())
        payload = {'iss':'https://test.cloudflareaccess.com', 'aud':['audience'],
                   'email':'OWNER@example.com', 'iat':now, 'exp':now+300}
        payload.update(claims or {})
        header = {'alg':'RS256', 'kid':'test'}
        header.update(head or {})
        data = encoded(header)+'.'+encoded(payload)
        signature = key.sign(data.encode('ascii'), padding.PKCS1v15(), hashes.SHA256())
        if bad_signature:
            signature = bytes([signature[0] ^ 1])+signature[1:]
        return data+'.'+encoded(signature)
    return jwk, token


@pytest.fixture
def configured(monkeypatch):
    for name, value in {'HOST':'insight.example.com', 'ACCESS_TEAM_DOMAIN':'test.cloudflareaccess.com',
                        'ACCESS_AUD':'audience', 'ALLOWED_EMAILS':'owner@example.com'}.items():
        monkeypatch.setenv('INSIGHT_REMOTE_HOST' if name == 'HOST' else 'INSIGHT_'+name, value)


@pytest.fixture(params=['http', 'handler'])
def app(tmp_path, monkeypatch, signing, configured, request):
    monkeypatch.setattr(remote_access, 'fetch_jwks', lambda team: {'keys':[signing[0]]})
    monkeypatch.setattr(server.adapters, 'metrics', lambda *args: {'available':True})
    in_memory = request.param == 'handler'
    if in_memory:
        from types import SimpleNamespace
        monkeypatch.setattr(server, 'ThreadingHTTPServer', lambda address, handler:
            SimpleNamespace(server_port=51877, handler=handler))
    try:
        httpd = server.make_server(0, agentmesh_home=tmp_path)
    except PermissionError:
        pytest.skip('Loopback binding denied')
    if not in_memory:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

    def request(path='/health', token=None, headers=None, method='GET', local=False):
        supplied = {'Host':f'127.0.0.1:{httpd.server_port}' if local else 'insight.example.com'}
        if token is not None:
            supplied['Cf-Access-Jwt-Assertion'] = token
        supplied.update(headers or {})
        if in_memory:
            import io
            raw = (method+' '+path+' HTTP/1.0\r\n'+''.join(name+': '+value+'\r\n' for name,value in supplied.items())+'\r\n').encode('ascii')
            class Client:
                def __init__(self):
                    self.output = io.BytesIO()
                def makefile(self, mode, *args):
                    return io.BytesIO(raw)
                def sendall(self, value):
                    self.output.write(value)
            client = Client()
            httpd.handler(client, ('127.0.0.1', 12345), httpd)
            response_head, body = client.output.getvalue().split(b'\r\n\r\n', 1)
            return int(response_head.split()[1]), body
        connection = http.client.HTTPConnection('127.0.0.1', httpd.server_port, timeout=3)
        try:
            connection.request(method, path, headers=supplied)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()
    yield request
    if not in_memory:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


@pytest.mark.parametrize('path', ['/', '/metrics.html', '/health', '/api/profile-log', '/api/metrics', '/api/connections'])
def test_INS_R1_allowed(app, signing, path):
    status, body = app(path, signing[1]())
    assert status == 200
    if path == '/health':
        assert set(json.loads(body)) == {'status', 'pid', 'readonly', 'remote'}
        assert json.loads(body)['remote'] is True


@pytest.mark.parametrize('claims,head,bad', [
    ({}, {}, True), ({'exp':0}, {}, False), ({'aud':['wrong']}, {}, False),
    # Truly expired: iat < exp, both in the past beyond the 30 s skew.
    ({'iat':int(time.time())-600, 'exp':int(time.time())-120}, {}, False),
    ({'iss':'https://wrong.example'}, {}, False), ({'email':'other@example.com'}, {}, False),
    ({}, {'kid':'unknown'}, False), ({}, {'alg':'none'}, False), ({}, {'alg':'HS256'}, False),
    ({'iat':9999999999}, {}, False), ({'nbf':9999999999}, {}, False),
    ({'iat':True}, {}, False), ({'exp':'9999999999'}, {}, False), ({}, {'crit':[]}, False),
])
def test_INS_R2_invalid(app, signing, claims, head, bad):
    assert app(token=signing[1](claims, head, bad)) == (403, b'{"error": "Forbidden"}')


@pytest.mark.parametrize('headers,local', [({}, False), ({'cF-Ray':'x'}, True),
    ({'x-FoRwArDeD-Anything':'x'}, True), ({'Cookie':'CF_Authorization=ignored'}, False)])
def test_INS_R3_no_assertion(app, headers, local):
    assert app(headers=headers, local=local) == (403, b'{"error": "Forbidden"}')


@pytest.mark.parametrize('path', ['/api/memory', '/api/memory/items/1', '/api/memory/summaries/1', '/unknown'])
def test_INS_R4_content_denied(app, signing, path):
    assert app(path, signing[1]())[0] == 404


@pytest.mark.parametrize('method', ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD', 'TRACE', 'CONNECT', 'CUSTOM'])
def test_INS_R5_methods(app, signing, method):
    assert app(token=signing[1](), method=method)[0] == 405


@pytest.mark.parametrize('name,value', [('REMOTE_HOST',''), ('REMOTE_HOST','https://bad/'),
    ('ACCESS_TEAM_DOMAIN',''), ('ACCESS_TEAM_DOMAIN','bad/path'), ('ACCESS_AUD',''),
    ('ACCESS_AUD',' '), ('ALLOWED_EMAILS',''), ('ALLOWED_EMAILS','not-an-email')])
def test_INS_R6_config(app, monkeypatch, signing, name, value):
    monkeypatch.setenv('INSIGHT_'+name, value)
    assert app(token=signing[1]())[0] == 403


def test_INS_R7_crypto_missing(app, signing):
    with patch.object(remote_access, 'rsa', None):
        assert app(token=signing[1]())[0] == 403
        assert app(local=True)[0] == 200


def test_INS_R8_origin_local_and_bounds(app, signing):
    token = signing[1]()
    assert app(token=token, headers={'Origin':'https://insight.example.com'})[0] == 200
    assert app(token=token, headers={'Origin':'http://insight.example.com'})[0] == 403
    assert app(token='a'*16385)[0] == 403
    assert json.loads(app(local=True)[1])['remote'] is False
    assert app(local=True, headers={'Host':'evil.example'})[0] == 403


def test_INS_R9_cache(signing):
    calls = []
    clock = [1000]
    verifier = remote_access.Verifier(fetcher=lambda team: calls.append(team) or {'keys':[signing[0]]}, clock=lambda: clock[0])
    for kid in ['test', 'test', 'missing', 'another']:
        verifier.key('test.cloudflareaccess.com', kid)
    assert len(calls) == 2
    clock[0] += 60
    verifier.key('test.cloudflareaccess.com', 'missing')
    assert len(calls) == 3
    clock[0] += 601
    verifier.key('test.cloudflareaccess.com', 'test')
    assert len(calls) == 4


@pytest.mark.parametrize('case', ['valid', 'bad-signature', 'expired', 'aud', 'iss', 'email',
    'kid', 'none', 'HS256', 'future-iat', 'future-nbf', 'bool-iat', 'string-exp', 'crit',
    'missing', 'oversized', 'malformed', 'duplicate', 'origin', 'crypto', 'config'])
def test_INS_R10_verifier(configured, signing, monkeypatch, case):
    from email.message import Message
    claims, head = {}, {}
    changes = {'expired':('exp',0), 'aud':('aud','wrong'), 'iss':('iss','wrong'),
               'email':('email','wrong@example.com'), 'future-iat':('iat',9999999999),
               'future-nbf':('nbf',9999999999), 'bool-iat':('iat',True), 'string-exp':('exp','9999999999')}
    if case in changes:
        key, value = changes[case]
        claims[key] = value
    if case in ('none', 'HS256'):
        head['alg'] = case
    if case == 'kid':
        head['kid'] = 'unknown'
    if case == 'crit':
        head['crit'] = []
    headers = Message()
    headers['Host'] = 'insight.example.com'
    if case != 'missing':
        headers['Cf-Access-Jwt-Assertion'] = ('x'*16385 if case == 'oversized' else
            'invalid' if case == 'malformed' else signing[1](claims, head, case == 'bad-signature'))
    if case == 'duplicate':
        headers['Cf-Access-Jwt-Assertion'] = signing[1]()
    if case == 'origin':
        headers['Origin'] = 'https://other.example'
    if case == 'crypto':
        monkeypatch.setattr(remote_access, 'rsa', None)
    if case == 'config':
        monkeypatch.delenv('INSIGHT_ACCESS_AUD')
    verifier = remote_access.Verifier(fetcher=lambda team: {'keys':[signing[0]]})
    assert verifier.allowed(headers) is (case == 'valid')


@pytest.mark.parametrize('headers,expected', [({'Host':'insight.example.com'}, True),
    ({'Host':'localhost:51877','cF-Anything':''}, True),
    ({'Host':'localhost:51877','X-fOrWaRdEd-Other':''}, True),
    ({'Host':'localhost:51877'}, False)])
def test_INS_R11_classification(configured, headers, expected):
    assert remote_access.is_remote(headers) is expected


def test_INS_R12_fetch(monkeypatch):
    """No network: exercise actual fetch plumbing, IPv4 fallback and TLS hostname."""
    import io
    import socket
    from types import SimpleNamespace
    events = []
    status, payload = [200], [b'{"keys":[]}']

    class Socket:
        def settimeout(self, value):
            assert 0 < value <= 3
        def connect(self, address):
            events.append(address)
            if address[0] == 'v4':
                raise OSError('unreachable')
        def close(self):
            pass

    class Response:
        def __init__(self):
            self.status = status[0]
            self.data = io.BytesIO(payload[0])
            self.fp = SimpleNamespace(raw=SimpleNamespace(_sock=Socket()))
        def read1(self, size):
            return self.data.read(size)

    class Connection:
        def __init__(self, host, **kwargs):
            assert host == 'test.cloudflareaccess.com'
            assert kwargs['timeout'] == 3
        def request(self, method, path, headers):
            assert (method, path) == ('GET', '/cdn-cgi/access/certs')
        def getresponse(self):
            return Response()
        def close(self):
            pass

    def wrap(sock, server_hostname):
        assert server_hostname == 'test.cloudflareaccess.com'
        return sock
    monkeypatch.setattr(remote_access.ssl, 'create_default_context', lambda: SimpleNamespace(wrap_socket=wrap))
    monkeypatch.setattr(remote_access.socket, 'getaddrinfo', lambda *args, **kwargs: [
        (socket.AF_INET6, socket.SOCK_STREAM, 0, '', ('v6',443)),
        (socket.AF_INET, socket.SOCK_STREAM, 0, '', ('v4',443))])
    monkeypatch.setattr(remote_access.socket, 'socket', lambda *args: Socket())
    monkeypatch.setattr(remote_access.http.client, 'HTTPSConnection', Connection)
    assert remote_access.fetch_jwks('test.cloudflareaccess.com') == {'keys':[]}
    assert events == [('v4',443), ('v6',443)]
    status[0] = 302
    with pytest.raises(ValueError):
        remote_access.fetch_jwks('test.cloudflareaccess.com')
    status[0], payload[0] = 200, b' '* (remote_access.MAX_RESPONSE+1)
    with pytest.raises(ValueError):
        remote_access.fetch_jwks('test.cloudflareaccess.com')


@pytest.mark.parametrize('remote', [True, False])
def test_INS_R13_ui(remote):
    """Run the shipped page script with a minimal DOM; inspect actual fetches."""
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable for UI script check')
    script = Path(__file__).with_name('index.html').read_text(encoding='utf-8').split('<script>')[1].split('</script>')[0]
    harness = r'''
const vm=require('node:vm'), assert=require('node:assert/strict');
const elements=new Map(), calls=[], views=['overview','metrics','memory','agents','pipeline','sync','profile-log'];
function element(id){if(!elements.has(id))elements.set(id,{id,dataset:{view:id},hidden:false,disabled:false,value:'sqlite',options:[],classList:{toggle(){}},setAttribute(){},closest(){return element('label')},contentWindow:{postMessage(){}}});return elements.get(id)}
const context={console,Intl,URLSearchParams,Date,Object,String,Number,location:{hash:'#memory',origin:'https://insight.example.com'},localStorage:{getItem(){},setItem(){}},setInterval(){},document:{getElementById:element,body:element('body'),addEventListener(){},querySelector:element,querySelectorAll(selector){return selector.includes('button')&&selector!=='nav button'?[element('search')]:views.map(element)}},window:{scrollTo(){},addEventListener(){}},fetch:async url=>{calls.push(url);return {ok:true,json:async()=>url==='/health'?{remote:REMOTE}:url==='/api/connections'?{rows:[],default:'sqlite'}:{}}}};
vm.createContext(context);
vm.runInContext(SCRIPT,context);
vm.runInContext('renderOperations=()=>{};renderProfileLog=()=>{};',context);
setImmediate(()=>{try{
assert.equal(calls[0],'/health');
if(REMOTE){assert(calls.includes('/api/operations?backend=sqlite'));assert(!calls.some(c=>c.startsWith('/api/memory')));assert.equal(element('memory').hidden,true);assert.equal(element('memory').disabled,true);assert.equal(element('connection').disabled,true);vm.runInContext("navigate('memory');loadMemory();",context);assert.equal(context.location.hash,'overview');assert(!calls.some(c=>c.startsWith('/api/memory')));}
else{assert(calls.includes('/api/operations?backend=sqlite'));assert.equal(element('connection').disabled,false);}
}catch(error){console.error(error.message);process.exitCode=1;}});
'''
    result = subprocess.run([node, '-e', 'const REMOTE='+json.dumps(remote)+';const SCRIPT='+json.dumps(script)+';'+harness], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr


def test_storage_remote(app,signing,monkeypatch):
    import storage
    monkeypatch.setattr(storage.Observer,'compute',lambda self:{'available':True})
    assert app('/api/storage',signing[1]())[0]==200
    assert app('/api/storage?path=x',signing[1]())[0]==400
    assert app('/api/storage')[0]==403
