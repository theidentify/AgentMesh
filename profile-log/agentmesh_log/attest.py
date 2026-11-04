"""Email attestation for workspace members (team workspace W1): an owner or admin vouches that an account
belongs to an email address, either after checking the person directly or after verifying an OIDC ID token.

Records are immutable, signed objects at ``registry/attestations/<workspace>/<hash>.json``. They never hold the
email: only ``email_hash`` (SHA-256 over a domain tag, the workspace ID and the normalized address). The hash is
public to anyone who can read the registry, including the storage provider, so it hides an address only from
someone who cannot guess it; it proves nothing on its own.

A record counts when its signing device was valid at ``issued_at`` and that device's account was an owner or
admin of the workspace at that time. The newest valid record per account is its current attestation.

OIDC: the member signs in on their own device (authorization code + PKCE, loopback redirect), binding the ID
token to their account through the nonce. They hand the token file to an admin, whose device verifies the
token against the provider's published keys and signs the attestation. No AgentMesh service is involved;
the devices talk to the provider directly.
"""
import base64
import hashlib
import json
import secrets
import time
from .encoding import fields, hex_bytes, milliseconds, parse, typed, wire
from .identity import sign, verify_signature
from .errors import IdentityError

FORMAT = 'agentmesh-workspace-attestation-v1'
RECORD_FIELDS = {'format', 'workspace', 'account', 'method', 'email_hash', 'channel', 'issuer', 'token_hash',
                 'issued_at', 'issuer_device', 'signature'}
DOMAIN = b'AgentMesh/workspace-attestation/Ed25519/v1\x00'
PREFIX = 'registry/attestations/'
METHODS = ('admin', 'oidc')
CHANNELS = ('in_person', 'video', 'code')
MANAGERS = ('owner', 'admin')
TOKEN_FORMAT = 'agentmesh-oidc-token-v1'
SKEW_S = 60
MAX_FETCH = 1 << 20


def normalize(email):
    if type(email) is not str: raise ValueError('invalid_request')
    value = email.strip().lower()
    local, at, host = value.partition('@')
    if not at or not local or '.' not in host or len(value) > 320 or any(c.isspace() for c in value):
        raise ValueError('invalid_request')
    return value


def email_hash(workspace, email):
    return hashlib.sha256(b'AgentMesh/email/v1\x00' + typed({'workspace': workspace, 'email': normalize(email)})).hexdigest()


def record_hash(record): return hashlib.sha256(b'AgentMesh/attestation-id/v1\x00' + typed(record)).hexdigest()


def path(record): return PREFIX + record['workspace'] + '/' + record_hash(record) + '.json'


def shape(record):
    fields(record, RECORD_FIELDS, FORMAT)
    for name in ('workspace', 'account', 'issuer_device'): hex_bytes(record[name], 16)
    hex_bytes(record['email_hash'], 32); hex_bytes(record['signature'], 64); milliseconds(record['issued_at'])
    if record['method'] == 'admin':
        if record['channel'] not in CHANNELS or record['issuer'] is not None or record['token_hash'] is not None:
            raise ValueError('invalid attestation')
    elif record['method'] == 'oidc':
        if record['channel'] is not None or type(record['issuer']) is not str or not record['issuer'].startswith('https://'):
            raise ValueError('invalid attestation')
        hex_bytes(record['token_hash'], 32)
    else: raise ValueError('invalid attestation')
    return record


def create(device, workspace, account, email, method, *, channel=None, issuer=None, token=None, issued_at=None):
    from .registry_store import now
    record = dict(format=FORMAT, workspace=workspace, account=account, method=method,
                  email_hash=email_hash(workspace, email), channel=channel, issuer=issuer,
                  token_hash=hashlib.sha256(token.encode()).hexdigest() if token is not None else None,
                  issued_at=now() if issued_at is None else issued_at, issuer_device=device.device_id)
    shape(dict(record, signature='00' * 64))
    return sign(record, device._private, DOMAIN)


def valid(record, registry, ws):
    """The signer's account when ``record`` is well formed, correctly signed and its signer was a manager then."""
    try: shape(record)
    except (ValueError, KeyError, TypeError): return None
    try:
        public = registry.device_public(record['issuer_device'], at=record['issued_at'])
        verify_signature(record, public, DOMAIN)
        if getattr(registry, 'device_classes', {}).get(record['issuer_device'], 'workstation') != 'workstation': return None
    except (IdentityError, ValueError, KeyError): return None
    signer = registry.certificates[record['issuer_device']]['account_id']
    return signer if ws.role(signer, record['issued_at']) in MANAGERS else None


def load(storage, workspace, registry, ws):
    """{account: newest valid record (with ``by`` = signer account)}, and the hashes of ignored records."""
    current, ignored = {}, []
    for key in storage.list(PREFIX + workspace + '/'):
        try: record = parse(storage.get(key))
        except (ValueError, KeyError, TypeError): continue
        if type(record) is not dict or record.get('workspace') != workspace or key != path(record):
            continue
        signer = valid(record, registry, ws)
        if signer is None: ignored.append(record_hash(record)); continue
        old = current.get(record['account'])
        if old is None or (record['issued_at'], record_hash(record)) > (old['issued_at'], record_hash(old)):
            current[record['account']] = dict(record, by=signer)
    return current, ignored


def publish(storage, record):
    shape(record); storage.put(path(record), wire(record)); return record_hash(record)


def summary(record):
    """What may be shown about an attestation: never the email or its hash."""
    return {k: record[k] for k in ('account', 'method', 'channel', 'issuer', 'issued_at', 'by')}


# --- OIDC -----------------------------------------------------------------------------------------------------

def nonce(workspace, account, seed):
    """Binds an ID token to one account and workspace; ``seed`` is a fresh random value kept with the token."""
    return _b64(hashlib.sha256(b'AgentMesh/oidc-nonce/v1\x00' + typed(
        {'workspace': workspace, 'account': account, 'seed': seed})).digest())


def _b64(data): return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _unb64(text):
    if type(text) is not str: raise ValueError('invalid token')
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def _public_key(jwk):
    from cryptography.hazmat.primitives.asymmetric import rsa, ec
    if jwk.get('kty') == 'RSA':
        n, e = (int.from_bytes(_unb64(jwk[k]), 'big') for k in ('n', 'e'))
        if n.bit_length() < 2048: raise ValueError('weak key')
        return 'RS256', rsa.RSAPublicNumbers(e, n).public_key()
    if jwk.get('kty') == 'EC' and jwk.get('crv') == 'P-256':
        x, y = (int.from_bytes(_unb64(jwk[k]), 'big') for k in ('x', 'y'))
        return 'ES256', ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
    raise ValueError('unsupported key')


def verify_id_token(token, jwks, *, issuer, audience, expected_nonce, now=None):
    """The verified, normalized email of an ID token, or ValueError.

    Checks the signature (RS256 or ES256 only) against ``jwks``, then iss, aud/azp, exp, iat, nonce and
    email_verified."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    if type(token) is not str or token.count('.') != 2 or len(token) > 16384: raise ValueError('invalid token')
    head_b64, body_b64, sig_b64 = token.split('.')
    try: header, claims = json.loads(_unb64(head_b64)), json.loads(_unb64(body_b64))
    except (ValueError, UnicodeDecodeError): raise ValueError('invalid token') from None
    if type(header) is not dict or type(claims) is not dict: raise ValueError('invalid token')
    alg, kid = header.get('alg'), header.get('kid')
    keys = [k for k in (jwks.get('keys') or []) if type(k) is dict and k.get('kid') == kid and k.get('use', 'sig') == 'sig']
    if alg not in ('RS256', 'ES256') or len(keys) != 1: raise ValueError('invalid token')
    key_alg, key = _public_key(keys[0])
    if key_alg != alg or keys[0].get('alg', alg) != alg: raise ValueError('invalid token')
    signed, signature = (head_b64 + '.' + body_b64).encode('ascii'), _unb64(sig_b64)
    try:
        if alg == 'RS256': key.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
        else:
            if len(signature) != 64: raise ValueError('invalid token')
            der = encode_dss_signature(int.from_bytes(signature[:32], 'big'), int.from_bytes(signature[32:], 'big'))
            key.verify(der, signed, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature: raise ValueError('invalid token') from None
    now = time.time() if now is None else now
    aud = claims.get('aud'); audiences = aud if type(aud) is list else [aud]
    if claims.get('iss') != issuer or audience not in audiences: raise ValueError('invalid token')
    if len(audiences) > 1 and claims.get('azp') != audience: raise ValueError('invalid token')
    exp, iat = claims.get('exp'), claims.get('iat')
    if type(exp) not in (int, float) or exp <= now - SKEW_S: raise ValueError('token expired')
    if type(iat) not in (int, float) or iat > now + SKEW_S: raise ValueError('invalid token')
    if claims.get('nonce') != expected_nonce: raise ValueError('invalid token')
    if claims.get('email_verified') not in (True, 'true'): raise ValueError('email not verified')
    return normalize(claims.get('email'))


def fetch_json(url, *, opener=None):
    """GET a small JSON document over HTTPS only."""
    import urllib.request
    if type(url) is not str or not url.startswith('https://'): raise ValueError('https required')
    request = urllib.request.Request(url, headers={'Accept': 'application/json', 'User-Agent': 'agentmesh-log'})
    with (opener or urllib.request.urlopen)(request, timeout=10) as response:
        data = response.read(MAX_FETCH + 1)
    if len(data) > MAX_FETCH: raise ValueError('response too large')
    return json.loads(data)


def discover(issuer, *, fetch=fetch_json):
    """The provider's metadata; its issuer must equal ``issuer`` exactly, and every endpoint must be HTTPS."""
    meta = fetch(issuer.rstrip('/') + '/.well-known/openid-configuration')
    if type(meta) is not dict or meta.get('issuer') != issuer: raise ValueError('issuer mismatch')
    for name in ('authorization_endpoint', 'token_endpoint', 'jwks_uri'):
        if type(meta.get(name)) is not str or not meta[name].startswith('https://'): raise ValueError('https required')
    return meta


def authorization_url(meta, client_id, redirect_uri, state, challenge, nonce_value):
    from urllib.parse import urlencode
    return meta['authorization_endpoint'] + '?' + urlencode(dict(
        response_type='code', client_id=client_id, redirect_uri=redirect_uri, scope='openid email', state=state,
        nonce=nonce_value, code_challenge=challenge, code_challenge_method='S256'))


def login(issuer, client_id, nonce_value, *, client_secret=None, timeout_s=300, open_browser=None, output=print,
          fetch=fetch_json):
    """Authorization code + PKCE with a loopback redirect; returns the ID token. Needs a browser on this device."""
    import http.server, threading, urllib.parse, urllib.request
    meta = discover(issuer, fetch=fetch)
    verifier = _b64(secrets.token_bytes(32)); challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    state = _b64(secrets.token_bytes(16)); result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            if urllib.parse.urlsplit(self.path).path == '/callback' and query.get('state') == [state]:
                result['code'] = (query.get('code') or [None])[0]; result['error'] = (query.get('error') or [None])[0]
            self.send_response(200); self.send_header('Content-Type', 'text/plain; charset=utf-8'); self.end_headers()
            self.wfile.write('AgentMesh: you can close this window.'.encode())
        def log_message(self, *args): pass

    server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
    redirect = f'http://127.0.0.1:{server.server_address[1]}/callback'
    url = authorization_url(meta, client_id, redirect, state, challenge, nonce_value)
    output('Open this address to sign in: ' + url)
    if open_browser is None:
        import webbrowser; open_browser = webbrowser.open
    open_browser(url)
    thread = threading.Thread(target=lambda: [server.handle_request() for _ in range(5) if 'code' not in result],
                              daemon=True)
    server.timeout = timeout_s; thread.start(); thread.join(timeout_s); server.server_close()
    if not result.get('code'): raise ValueError('sign-in not completed' + (': ' + result['error'] if result.get('error') else ''))
    form = dict(grant_type='authorization_code', code=result['code'], redirect_uri=redirect, client_id=client_id,
                code_verifier=verifier, **({'client_secret': client_secret} if client_secret else {}))
    request = urllib.request.Request(meta['token_endpoint'], data=urllib.parse.urlencode(form).encode(),
                                     headers={'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=15) as response: answer = json.loads(response.read(MAX_FETCH))
    token = answer.get('id_token') if type(answer) is dict else None
    if type(token) is not str: raise ValueError('no id_token returned')
    return token


def token_file(workspace, account, issuer, client_id, seed, token):
    return dict(format=TOKEN_FORMAT, workspace=workspace, account=account, issuer=issuer, client_id=client_id,
                seed=seed, id_token=token)


def read_token_file(record):
    fields(record, {'format', 'workspace', 'account', 'issuer', 'client_id', 'seed', 'id_token'}, TOKEN_FORMAT)
    hex_bytes(record['workspace'], 16); hex_bytes(record['account'], 16)
    if type(record['seed']) is not str or len(record['seed']) < 22: raise ValueError('invalid token file')
    return record
