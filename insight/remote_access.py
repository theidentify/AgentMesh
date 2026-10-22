"""Fail-closed Cloudflare Access gate; cache public keys, never assertions."""
import base64
import http.client
import json
import os
import re
import socket
import ssl
import threading
import time

try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
except ImportError:
    rsa = None


ALLOWED_PATHS = frozenset(('/', '/metrics.html', '/health', '/api/profile-log', '/api/metrics', '/api/connections'))
DOMAIN = re.compile(r'(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', re.I)
MAX_RESPONSE = 1024 * 1024


def is_remote(headers):
    return (any(name.lower() == 'host' and value == os.environ.get('INSIGHT_REMOTE_HOST', '')
                for name, value in headers.items()) or
            any(name.lower().startswith(('cf-', 'x-forwarded-')) for name in headers))


def configuration():
    host, team, aud, emails = (os.environ.get('INSIGHT_'+name, '') for name in
                              ('REMOTE_HOST', 'ACCESS_TEAM_DOMAIN', 'ACCESS_AUD', 'ALLOWED_EMAILS'))
    allowed = {email.strip().lower() for email in emails.split(',')}
    if (not DOMAIN.fullmatch(host) or not DOMAIN.fullmatch(team) or
            not aud or len(aud) > 1024 or any(c.isspace() or ord(c) < 32 for c in aud) or
            not all(re.fullmatch(r'[^\s@,]+@[^\s@,]+\.[^\s@,]+', email) for email in allowed)):
        raise ValueError('Invalid configuration')
    return host, team, aud, allowed


def fetch_jwks(team):
    """Direct verified HTTPS, no proxies/redirects; prefer IPv4 only here."""
    context = ssl.create_default_context()
    deadline = time.monotonic() + 3
    addresses = socket.getaddrinfo(team, 443, type=socket.SOCK_STREAM)
    addresses.sort(key=lambda row: row[0] != socket.AF_INET)
    for family, kind, proto, _, address in addresses:
        connection = http.client.HTTPSConnection(team, timeout=3, context=context)
        sock = None
        timer = None
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            sock = socket.socket(family, kind, proto)
            sock.settimeout(remaining)
            sock.connect(address)
            sock.settimeout(max(0.001, deadline-time.monotonic()))
            stream = context.wrap_socket(sock, server_hostname=team)
            connection.sock = stream
            def abort():
                try:
                    stream.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            timer = threading.Timer(max(0.001, deadline-time.monotonic()), abort)
            timer.daemon = True
            timer.start()
            connection.request('GET', '/cdn-cgi/access/certs', headers={'Accept':'application/json'})
            connection.sock.settimeout(max(0.001, deadline-time.monotonic()))
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError('JWKS unavailable')
            chunks, size = [], 0
            while True:
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                stream.settimeout(remaining)
                chunk = response.read1(min(65536, MAX_RESPONSE+1-size))
                if not chunk:
                    if time.monotonic() >= deadline:
                        raise TimeoutError()
                    return json.loads(b''.join(chunks).decode('utf-8'))
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_RESPONSE:
                    raise ValueError('JWKS too large')
        except (OSError, ssl.SSLError):
            if time.monotonic() >= deadline:
                raise TimeoutError() from None
        finally:
            if timer is not None:
                timer.cancel()
            connection.close()
            if sock is not None:
                sock.close()
    raise OSError('JWKS unavailable')


def decode(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('Invalid encoding')
    return base64.urlsafe_b64decode(value+'='*((-len(value)) % 4))


class Verifier:
    def __init__(self, fetcher=None, clock=time.monotonic):
        self.fetcher = fetcher or fetch_jwks
        self.clock = clock
        self.team, self.until, self.retry = None, 0, 0
        self.keys = []
        self.lock = threading.Lock()

    def key(self, team, kid):
        with self.lock:
            now = self.clock()
            fresh = self.team != team or now >= self.until
            key = next((key for key in self.keys if key.get('kid') == kid), None)
            if fresh or (key is None and now >= self.retry):
                # Failed rotation fetches are throttled too.
                if not fresh:
                    self.retry = now+60
                data = self.fetcher(team)
                keys = data['keys']
                if not isinstance(keys, list) or len(keys) > 100 or not all(isinstance(k, dict) for k in keys):
                    raise ValueError('Invalid keys')
                self.team, self.until, self.keys = team, now+600, keys
                key = next((key for key in keys if key.get('kid') == kid), None)
            return key

    def allowed(self, headers):
        try:
            host, team, aud, emails = configuration()
            if rsa is None or len(headers.get_all('Host', [])) != 1:
                return False
            if headers.get('Origin') is not None and headers.get_all('Origin') != ['https://'+host]:
                return False
            tokens = headers.get_all('Cf-Access-Jwt-Assertion', [])
            if len(tokens) != 1 or not 0 < len(tokens[0]) <= 16384:
                return False
            parts = tokens[0].split('.')
            if len(parts) != 3:
                return False
            head, claims = (json.loads(decode(part).decode('utf-8')) for part in parts[:2])
            if head.get('alg') != 'RS256' or not isinstance(head.get('kid'), str) or not head['kid'] or 'crit' in head:
                return False
            key = self.key(team, head['kid'])
            if not key or key.get('kty') != 'RSA' or key.get('alg', 'RS256') != 'RS256' or key.get('use', 'sig') != 'sig':
                return False
            public = rsa.RSAPublicNumbers(int.from_bytes(decode(key['e']), 'big'), int.from_bytes(decode(key['n']), 'big')).public_key()
            public.verify(decode(parts[2]), (parts[0]+'.'+parts[1]).encode('ascii'), padding.PKCS1v15(), hashes.SHA256())
            now = time.time()
            audience = claims.get('aud')
            audience = audience if isinstance(audience, list) else [audience]
            exp, iat, nbf = claims.get('exp'), claims.get('iat'), claims.get('nbf')
            integer = lambda value: type(value) is int and abs(value) <= 9007199254740991
            return (claims.get('iss') == 'https://'+team and aud in audience and
                    integer(exp) and integer(iat) and exp > now-30 and iat <= now+30 and iat < exp and
                    ('nbf' not in claims or (integer(nbf) and nbf <= now+30 and nbf < exp)) and
                    isinstance(claims.get('email'), str) and claims['email'].lower() in emails)
        except Exception:
            return False
