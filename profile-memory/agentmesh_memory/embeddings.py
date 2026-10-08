"""Provider-neutral embeddings and a local-only Ollama adapter.

No model pull, fallback, remote endpoint or persisted corpus is supported.
The model digest is pinned at construction and rechecked before and after each
nonempty batch. These checks detect ordinary tag changes, not an ABA tag swap;
operator-controlled model tags must remain frozen throughout inference. Dimension
is None until the first successful, revision-checked embed and then pinned.
Vectors are fresh L2-normalized float lists. This adapter uses only stdlib.

Bounds: 64 texts/batch, 32768 characters/text, 131072 characters/batch,
524288 UTF-8 bytes/batch, 8 MiB per HTTP response, timeout (0, 300] seconds.
Ollama API reference: https://docs.ollama.com/api/embed
"""
import http.client
import json
import ipaddress
import math
import re
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

from .vectors import validate_vectors


@runtime_checkable
class EmbeddingProvider(Protocol):
    @property
    def space(self) -> dict:
        """Provider/model/revision/dimension/metric identity; dimension may be None."""
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        ...


class OllamaEmbedding:
    def __init__(self, model='bge-m3:latest', base_url='http://127.0.0.1:11434', timeout=120):
        if not isinstance(model, str) or not 1 <= len(model) <= 256 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]*', model):
            raise ValueError('invalid model identifier')
        if type(timeout) not in (int, float) or not 0 < timeout <= 300 or not math.isfinite(timeout):
            raise ValueError('timeout must be finite and in (0, 300] seconds')
        if not isinstance(base_url, str) or any(c.isspace() or ord(c) < 32 for c in base_url):
            raise ValueError('invalid loopback URL')
        try:
            url = urlsplit(base_url)
            if url.scheme != 'http' or url.username is not None or url.password is not None or url.path not in ('', '/') or url.query or url.fragment:
                raise ValueError('only plain HTTP loopback origins are permitted')
            if not url.hostname or not ipaddress.ip_address(url.hostname).is_loopback or (url.port is not None and not 1 <= url.port <= 65535):
                raise ValueError('only literal loopback addresses are permitted')
        except (TypeError, ValueError) as exc:
            raise ValueError('invalid local Ollama endpoint') from exc
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
        return {'provider': 'ollama', 'model': self.model, 'revision': self._revision, 'dimension': self._dimension, 'metric': 'cosine'}

    def _request(self, path, payload=None):
        connection = http.client.HTTPConnection(self._host, self._port, timeout=self.timeout)
        try:
            body = None if payload is None else json.dumps(payload).encode()
            connection.request('GET' if payload is None else 'POST', path, body=body, headers={'Content-Type': 'application/json'})
            response = connection.getresponse()
            if response.status != 200:
                raise RuntimeError(f'local Ollama HTTP status {response.status}; redirects are forbidden')
            raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise RuntimeError('Ollama response exceeds 8 MiB budget')
            try:
                return json.loads(raw)
            except (ValueError, UnicodeError, RecursionError) as exc:
                raise RuntimeError('invalid Ollama JSON response') from exc
        except (OSError, http.client.HTTPException) as exc:
            raise RuntimeError('local Ollama transport failed; no fallback') from exc
        finally:
            connection.close()

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
        data = self._request('/api/embed', {'model': self.model, 'input': texts, 'truncate': False})
        if not isinstance(data, dict) or data.get('model') != self.model:
            raise ValueError('invalid embedding response model')
        vectors = validate_vectors(data.get('embeddings'), expected_count=len(texts), expected_dimension=self._dimension)
        if self._installed_revision() != self._revision:
            raise RuntimeError('installed model revision changed during embedding; discard batch')
        self._dimension = len(vectors[0])
        return vectors
