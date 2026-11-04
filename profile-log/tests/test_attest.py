import base64
import io
import json
import threading
import time
import urllib.parse
import http.client
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, ec, padding
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from agentmesh_log import attest, workspace as ws
from agentmesh_log.broker.keystore import BrokerState
from agentmesh_log.storage.filesystem import FilesystemStorage

ISS, AUD = 'https://accounts.example.test', 'client-123'


def b64(data): return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def num(n, size=None): return b64(n.to_bytes(size or (n.bit_length() + 7) // 8, 'big'))


RSA = rsa.generate_private_key(public_exponent=65537, key_size=2048)
EC = ec.generate_private_key(ec.SECP256R1())
JWKS = {'keys': [
    dict(kty='RSA', kid='r1', alg='RS256', use='sig', n=num(RSA.public_key().public_numbers().n), e=num(65537)),
    dict(kty='EC', kid='e1', crv='P-256', x=num(EC.public_key().public_numbers().x, 32), y=num(EC.public_key().public_numbers().y, 32))]}


def token(claims=None, *, kid='r1', alg='RS256', key=None, header=None, **overrides):
    now = int(time.time())
    body = dict(iss=ISS, aud=AUD, exp=now + 300, iat=now, nonce='N', email='Ann@Example.com', email_verified=True)
    body.update(claims or {}); body.update(overrides)
    head = b64(json.dumps(header or dict(alg=alg, kid=kid)).encode()); payload = b64(json.dumps(body).encode())
    signed = (head + '.' + payload).encode()
    if alg == 'RS256': sig = (key or RSA).sign(signed, padding.PKCS1v15(), hashes.SHA256())
    elif alg == 'ES256':
        r, s = decode_dss_signature((key or EC).sign(signed, ec.ECDSA(hashes.SHA256()))); sig = r.to_bytes(32, 'big') + s.to_bytes(32, 'big')
    else: sig = b''
    return head + '.' + payload + '.' + b64(sig)


def check(t, **kw):
    return attest.verify_id_token(t, JWKS, **dict(dict(issuer=ISS, audience=AUD, expected_nonce='N'), **kw))


def test_token_ok():
    assert check(token()) == 'ann@example.com'
    assert check(token(kid='e1', alg='ES256')) == 'ann@example.com'
    assert check(token(aud=[AUD, 'other'], azp=AUD)) == 'ann@example.com'


@pytest.mark.parametrize('bad', [
    dict(iss='https://evil.test'), dict(aud='other'), dict(aud=[AUD, 'other']), dict(exp=int(time.time()) - 120),
    dict(iat=int(time.time()) + 600), dict(nonce='other'), dict(email_verified=False), dict(email='not-an-email')],
    ids=['iss', 'aud', 'azp', 'exp', 'iat', 'nonce', 'unverified', 'email'])
def test_token_claims(bad):
    with pytest.raises(ValueError): check(token(**bad))


def test_token_signatures():
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    t = token()
    head, body, sig = t.split('.')
    tampered = head + '.' + b64(json.dumps(dict(json.loads(base64.urlsafe_b64decode(body + '==')), email='eve@example.com')).encode()) + '.' + sig
    for bad in (token(key=other), tampered, token(alg='none', header=dict(alg='none', kid='r1')),
                token(header=dict(alg='HS256', kid='r1')), token(kid='missing'), token(kid='e1'), 'a.b', 'x' * 20000):
        with pytest.raises(ValueError): check(bad)
    weak = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    jwks = {'keys': [dict(kty='RSA', kid='w', n=num(weak.public_key().public_numbers().n), e=num(65537))]}
    with pytest.raises(ValueError): attest.verify_id_token(token(kid='w', key=weak), jwks, issuer=ISS, audience=AUD, expected_nonce='N')


def test_discover():
    good = dict(issuer=ISS, authorization_endpoint=ISS + '/auth', token_endpoint=ISS + '/token', jwks_uri=ISS + '/keys')
    assert attest.discover(ISS, fetch=lambda url: good)['jwks_uri'] == ISS + '/keys'
    with pytest.raises(ValueError): attest.discover(ISS, fetch=lambda url: dict(good, issuer='https://other.test'))
    with pytest.raises(ValueError): attest.discover(ISS, fetch=lambda url: dict(good, token_endpoint='http://plain.test'))
    with pytest.raises(ValueError): attest.fetch_json('http://plain.test/x')


def test_login(monkeypatch):
    meta = dict(issuer=ISS, authorization_endpoint=ISS + '/auth', token_endpoint=ISS + '/token', jwks_uri=ISS + '/keys')
    seen = {}
    def browser(url):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        seen.update(query)
        redirect = urllib.parse.urlsplit(query['redirect_uri'][0])
        def visit():
            conn = http.client.HTTPConnection(redirect.hostname, redirect.port, timeout=5)
            conn.request('GET', redirect.path + '?' + urllib.parse.urlencode(dict(code='C', state=query['state'][0])))
            conn.getresponse().read()
        threading.Thread(target=visit).start()
    class Answer(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): pass
    def urlopen(request, timeout=None):
        form = urllib.parse.parse_qs(request.data.decode())
        assert request.full_url == ISS + '/token' and form['code'] == ['C'] and form['code_verifier']
        return Answer(json.dumps(dict(id_token='T')).encode())
    monkeypatch.setattr('urllib.request.urlopen', urlopen)
    assert attest.login(ISS, AUD, 'NONCE', fetch=lambda url: meta, open_browser=browser, output=lambda _: None, timeout_s=10) == 'T'
    assert seen['nonce'] == ['NONCE'] and seen['code_challenge_method'] == ['S256'] and seen['scope'] == ['openid email']
    assert seen['redirect_uri'][0].startswith('http://127.0.0.1:')


def setup(tmp_path):
    state = BrokerState.create(['work']); storage = FilesystemStorage(tmp_path / 'replica')
    profile = state.trees[0].root.scope_id; me = state.account.account_id
    from agentmesh_log.registry_store import now
    t = now() + 10_000   # after the device certificate
    ws.publish(storage, ws.create(state.device, profile, 'genesis', subject=me, role='owner', issued_at=t))
    return state, storage, profile, ws.load(storage, profile, state.registry(), me), t


def test_records(tmp_path):
    state, storage, profile, w, t = setup(tmp_path)
    other = '55' * 16
    r1 = attest.create(state.device, profile, other, ' Ann@Example.com ', 'admin', channel='in_person', issued_at=t + 1000)
    r2 = attest.create(state.device, profile, other, 'ann@example.com', 'oidc', issuer=ISS, token='tok', issued_at=t + 2000)
    assert 'example.com' not in json.dumps(r1).lower() and r1['email_hash'] == r2['email_hash']
    for r in (r1, r2): attest.publish(storage, r)
    early = attest.create(state.device, profile, other, 'x@example.com', 'admin', channel='code', issued_at=t - 5000)
    attest.publish(storage, early)   # before the workspace existed: the signer was no manager then
    records, ignored = attest.load(storage, profile, state.registry(), w)
    assert records[other]['method'] == 'oidc' and records[other]['by'] == state.account.account_id
    assert attest.record_hash(early) in ignored
    assert set(attest.summary(records[other])) == {'account', 'method', 'channel', 'issuer', 'issued_at', 'by'}
    with pytest.raises(ValueError): attest.create(state.device, profile, other, 'a@b.co', 'admin', channel='email')
    with pytest.raises(ValueError): attest.create(state.device, profile, other, 'a@b.co', 'oidc', issuer='http://x', token='t')


def test_nonce():
    a = attest.nonce('ab' * 16, 'cd' * 16, 'seed')
    assert a != attest.nonce('ab' * 16, 'ef' * 16, 'seed') and a != attest.nonce('ab' * 16, 'cd' * 16, 'seed2')
