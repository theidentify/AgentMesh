"""Ollama embedding adapter (loopback, or HTTPS behind Cloudflare Access), ported from profile-memory (Phase 8)."""
import http.client
import json
import ipaddress
import math
import re
import socket
import ssl
import threading
from urllib.parse import urlsplit

from .vectors import validate_vectors

# bge-m3 accepts 8192 tokens. Ollama's runner defaults (num_ctx 4096, batch 2048)
# reject longer embedding inputs with HTTP 400, so ask for the model's own limit.
CONTEXT_TOKENS = 8192


class InputRejected(RuntimeError):
    """Ollama refused this input (HTTP 400), for example text longer than the context."""


class _IPv4FirstHTTPS(http.client.HTTPSConnection):
    """Try IPv4 addresses before IPv6; certificate and hostname checks unchanged."""
    def connect(self):
        infos = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)
        infos.sort(key=lambda info: info[0] != socket.AF_INET)
        error = OSError('no address')
        for family, kind, proto, _, address in infos:
            sock = socket.socket(family, kind, proto)
            try:
                sock.settimeout(self.timeout); sock.connect(address); break
            except OSError as exc:
                error = exc; sock.close()
        else:
            raise error
        try: self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close(); raise


class OllamaEmbedding:
    def __init__(self, model='bge-m3:latest', base_url='http://127.0.0.1:11434', timeout=120, keep_alive='30m', headers=None):
        if not isinstance(model, str) or not 1 <= len(model) <= 256 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]*', model):
            raise ValueError('invalid model identifier')
        if type(timeout) not in (int, float) or not 0 < timeout <= 300 or not math.isfinite(timeout):
            raise ValueError('timeout must be finite and in (0, 300] seconds')
        if not isinstance(keep_alive,str) or not re.fullmatch(r'\d{1,4}[smh]',keep_alive):
            raise ValueError('invalid keep_alive')
        if not isinstance(base_url,str) or any(c.isspace() or ord(c)<32 for c in base_url):
            raise ValueError('invalid Ollama endpoint')
        try:
            url=urlsplit(base_url)
            if url.username is not None or url.password is not None or url.query or url.fragment:
                raise ValueError()
            if url.port is not None and not 1<=url.port<=65535: raise ValueError()
            if url.scheme=='http':
                if url.path not in ('','/') or headers is not None: raise ValueError()
                if not ipaddress.ip_address(url.hostname).is_loopback: raise ValueError()
            elif url.scheme=='https':
                host=url.netloc.rsplit(':',1)[0] if ':' in url.netloc else url.netloc
                if url.path or '?' in base_url or '#' in base_url: raise ValueError()
                if not re.fullmatch(r'[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+',host):
                    raise ValueError()
                if type(headers) is not dict or set(headers)!={'CF-Access-Client-Id','CF-Access-Client-Secret'}:
                    raise ValueError()
            else: raise ValueError()
        except (TypeError,ValueError) as exc:
            raise ValueError('invalid Ollama endpoint') from exc
        self._https=url.scheme=='https'; self._keep_alive=keep_alive
        self._headers={'Content-Type':'application/json','User-Agent':'AgentMesh-embed/1',**(headers or {})}
        self._connection=None; self._lock=threading.Lock()
        self._model = model
        self._base_url = base_url
        self._timeout = timeout
        self._host = url.hostname
        self._port = url.port
        self._dimension = None
        self._revision = self._installed_revision()

    @property
    def model(self):
        return self._model

    @property
    def base_url(self):
        return self._base_url

    @property
    def timeout(self):
        return self._timeout

    @property
    def space(self) -> dict:
        return {'provider': 'ollama', 'model': self.model, 'revision': self._revision, 'metric': 'cosine'}

    def _request(self, path, payload=None):
        body=None if payload is None else json.dumps(payload).encode()
        with self._lock:
            reused=self._connection is not None
            for attempt in range(2 if reused else 1):
                try:
                    if self._connection is None:
                        self._connection=(_IPv4FirstHTTPS(self._host,self._port,timeout=self.timeout,
                            context=ssl.create_default_context()) if self._https else
                            http.client.HTTPConnection(self._host,self._port,timeout=self.timeout))
                    self._connection.request('GET' if payload is None else 'POST',path,body=body,headers=self._headers)
                    response=self._connection.getresponse()
                    if response.status in (302,401,403):
                        raise RuntimeError('embedding endpoint refused credentials')
                    if response.status==400:
                        raise InputRejected('Ollama rejected the input (HTTP 400)')
                    if response.status!=200:
                        raise RuntimeError(f'local Ollama HTTP status {response.status}; redirects are forbidden')
                    raw=response.read(8*1024*1024+1)
                    if len(raw)>8*1024*1024: raise RuntimeError('Ollama response exceeds 8 MiB budget')
                    try: return json.loads(raw)
                    except (ValueError,UnicodeError,RecursionError) as exc:
                        raise RuntimeError('invalid Ollama JSON response') from exc
                except (OSError,http.client.HTTPException) as exc:
                    self._close()
                    if not reused or attempt: raise RuntimeError('Ollama transport failed; no fallback') from exc
                except Exception:
                    self._close(); raise

    def _close(self):
        if self._connection is not None: self._connection.close()
        self._connection=None

    def _installed_revision(self):
        data = self._request('/api/tags')
        models = data.get('models') if isinstance(data, dict) else None
        if not isinstance(models, list) or len(models) > 10000 or any(not isinstance(item, dict) for item in models):
            raise RuntimeError('invalid installed model inventory')
        matches = [item for item in models if item.get('name') == self.model]
        if len(matches) != 1:
            raise RuntimeError('requested model is not uniquely installed; no pull or fallback')
        digest = matches[0].get('digest')
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise RuntimeError('invalid installed model digest')
        return digest

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not isinstance(texts, list) or len(texts) > 64 or any(not isinstance(text, str) or not 1 <= len(text) <= 32768 for text in texts):
            raise ValueError('texts must be a list of at most 64 nonempty bounded strings')
        if sum(len(text) for text in texts) > 131072:
            raise ValueError('text batch exceeds character budget')
        try:
            byte_count = sum(len(text.encode('utf-8')) for text in texts)
        except UnicodeEncodeError as exc:
            raise ValueError('invalid Unicode text') from exc
        if byte_count > 524288:
            raise ValueError('text batch exceeds byte budget')
        if not texts:
            return []
        if self._installed_revision() != self._revision:
            raise RuntimeError('installed model revision changed; create a new adapter')
        data = self._request('/api/embed', {'model': self.model, 'input': texts, 'truncate': False, 'keep_alive': self._keep_alive,
                                            'options': {'num_ctx': CONTEXT_TOKENS, 'num_batch': CONTEXT_TOKENS}})
        if not isinstance(data, dict) or data.get('model') != self.model:
            raise ValueError('invalid embedding response model')
        vectors = validate_vectors(data.get('embeddings'), expected_count=len(texts), expected_dimension=self._dimension)
        if self._installed_revision() != self._revision:
            raise RuntimeError('installed model revision changed during embedding; discard batch')
        self._dimension = len(vectors[0])
        return vectors
