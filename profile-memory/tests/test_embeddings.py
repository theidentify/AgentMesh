"""Real loopback HTTP boundary tests; no model download or remote requests."""
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

DIGEST = 'a' * 64


@contextmanager
def server():
    state = {'digest': DIGEST, 'response': {'model': 'bge-m3:latest', 'embeddings': [[3, 4], [0, 2]]}, 'calls': []}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass
        def do_GET(self):
            state['calls'].append(('GET', self.path, None))
            self.respond(state.get('tags', {'models': [{'name': 'bge-m3:latest', 'digest': state['digest']}]}))
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state['calls'].append(('POST', self.path, body))
            self.respond(state['response'])
        def respond(self, body):
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(state.get('status', 200))
            if 'location' in state:
                self.send_header('Location', state['location'])
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{httpd.server_port}', state
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


def test_real_http_embed_pins_digest_and_reports_space():
    from agentmesh_memory.embeddings import OllamaEmbedding, EmbeddingProvider
    with server() as (url, state):
        adapter = OllamaEmbedding(base_url=url)
        assert isinstance(adapter, EmbeddingProvider)
        assert adapter.space == {'provider': 'ollama', 'model': 'bge-m3:latest', 'revision': DIGEST, 'dimension': None, 'metric': 'cosine'}
        assert adapter.embed(['fixture one', 'fixture two']) == [[0.6, 0.8], [0.0, 1.0]]
        assert adapter.space['dimension'] == 2
        assert state['calls'] == [
            ('GET', '/api/tags', None), ('GET', '/api/tags', None),
            ('POST', '/api/embed', {'model': 'bge-m3:latest', 'input': ['fixture one', 'fixture two'], 'truncate': False}),
            ('GET', '/api/tags', None),
        ]


@pytest.mark.parametrize('url', [
    'https://127.0.0.1:11434', 'http://localhost:11434',
    'http://example.com', 'http://192.168.1.1',
    'http://user:password@127.0.0.1', 'http://127.0.0.1/path',
    'http://127.0.0.1?query=x', 'http://127.0.0.1#fragment',
    'file:///tmp/model', ' http://127.0.0.1', 'http://127.0.0.1:0',
])
def test_rejects_nonliteral_loopback_endpoints_before_network(url):
    from agentmesh_memory.embeddings import OllamaEmbedding
    # Patch only connection construction: no invalid endpoint is contacted.
    import unittest.mock
    with unittest.mock.patch('http.client.HTTPConnection') as connection:
        with pytest.raises(ValueError):
            OllamaEmbedding(base_url=url)
        connection.assert_not_called()


@pytest.mark.parametrize('timeout', [0, -1, True, float('nan'), float('inf'), 301, '120', 10**1000], ids=['zero', 'negative', 'bool', 'nan', 'inf', 'large', 'string', 'huge-int'])
def test_rejects_invalid_timeout_before_network(timeout):
    from agentmesh_memory.embeddings import OllamaEmbedding
    import unittest.mock
    with unittest.mock.patch('http.client.HTTPConnection') as connection:
        with pytest.raises(ValueError):
            OllamaEmbedding(timeout=timeout)
        connection.assert_not_called()


def test_refuses_tag_change_between_inventory_and_embedding_response():
    from agentmesh_memory.embeddings import OllamaEmbedding
    with server() as (url, state):
        adapter = OllamaEmbedding(base_url=url)
        request = adapter._request
        def swap_after_inventory(path, payload=None):
            result = request(path, payload)
            if path == '/api/tags':
                state['digest'] = 'b' * 64
                state['response']['embeddings'] = [[0, 1]]
            return result
        adapter._request = swap_after_inventory
        with pytest.raises(RuntimeError, match='revision'):
            adapter.embed(['fixture'])
        assert adapter.space['revision'] == DIGEST
        assert adapter.space['dimension'] is None


def test_refuses_changed_model_revision_before_post():
    from agentmesh_memory.embeddings import OllamaEmbedding
    with server() as (url, state):
        adapter = OllamaEmbedding(base_url=url)
        state['digest'] = 'b' * 64
        with pytest.raises(RuntimeError, match='revision'):
            adapter.embed(['fixture'])
        assert all(call[0] == 'GET' for call in state['calls'])
        assert adapter.space['revision'] == DIGEST


@pytest.mark.parametrize('tags', [
    {}, {'models': []}, {'models': [{'name': 'bge-m3:latest', 'digest': 'bad'}]},
    {'models': [{'name': 'bge-m3:latest'}]}, {'models': None},
    {'models': ['not an object']},
])
def test_refuses_missing_or_malformed_installed_model(tags):
    from agentmesh_memory.embeddings import OllamaEmbedding
    with server() as (url, state):
        state['tags'] = tags
        with pytest.raises(RuntimeError, match='model'):
            OllamaEmbedding(base_url=url)
        assert state['calls'] == [('GET', '/api/tags', None)]


@pytest.mark.parametrize('texts', [
    'not a list', [None], [''], ['x'] * 65, ['x' * 32769],
    ['x' * 32768] * 5, ['\ud800'],
])
def test_rejects_invalid_or_oversized_text_before_http(texts):
    from agentmesh_memory.embeddings import OllamaEmbedding
    with server() as (url, state):
        adapter = OllamaEmbedding(base_url=url)
        with pytest.raises(ValueError):
            adapter.embed(texts)
        assert len(state['calls']) == 1


def test_empty_text_batch_is_noop_without_network():
    from agentmesh_memory.embeddings import OllamaEmbedding
    with server() as (url, state):
        adapter = OllamaEmbedding(base_url=url)
        assert adapter.embed([]) == []
        assert adapter.space['dimension'] is None
        assert len(state['calls']) == 1


@pytest.mark.parametrize('response', [
    {'model': 'other', 'embeddings': [[1, 0]]},
    {'embeddings': [[1, 0]]}, {'model': None, 'embeddings': [[1, 0]]},
    [], {'model': 'bge-m3:latest'},
    {'model': 'bge-m3:latest', 'embeddings': [[0, 0]]},
    {'model': 'bge-m3:latest', 'embeddings': [[float('nan'), 1]]},
    {'model': 'bge-m3:latest', 'embeddings': [1, 2]},
    {'model': 'bge-m3:latest', 'embeddings': [[1], [2]]},
])
def test_rejects_invalid_embedding_response(response):
    from agentmesh_memory.embeddings import OllamaEmbedding
    with server() as (url, state):
        adapter = OllamaEmbedding(base_url=url)
        state['response'] = response
        with pytest.raises(ValueError):
            adapter.embed(['fixture'])
        assert adapter.space['dimension'] is None


def test_dimension_is_pinned_after_first_success():
    from agentmesh_memory.embeddings import OllamaEmbedding
    with server() as (url, state):
        adapter = OllamaEmbedding(base_url=url)
        state['response']['embeddings'] = [[1, 0]]
        adapter.embed(['fixture'])
        state['response']['embeddings'] = [[1, 0, 0]]
        with pytest.raises(ValueError, match='dimension'):
            adapter.embed(['fixture'])
        assert adapter.space['dimension'] == 2


@pytest.mark.parametrize('status', [302, 307, 400, 500])
def test_refuses_http_errors_and_redirects(status):
    from agentmesh_memory.embeddings import OllamaEmbedding
    with server() as (url, state):
        adapter = OllamaEmbedding(base_url=url)
        state['status'] = status
        state['location'] = 'http://example.com/never-contact'
        with pytest.raises(RuntimeError, match='HTTP'):
            adapter.embed(['fixture one', 'fixture two'])
        assert len(state['calls']) == 2


@pytest.mark.parametrize('raw', [b'{invalid', b'x' * (8 * 1024 * 1024 + 1)], ids=['malformed', 'oversized'])
def test_refuses_malformed_or_oversized_http_response(raw):
    from agentmesh_memory.embeddings import OllamaEmbedding
    with server() as (url, state):
        adapter = OllamaEmbedding(base_url=url)
        state['response'] = raw
        with pytest.raises(RuntimeError, match='response'):
            adapter.embed(['fixture'])
        assert adapter.space['dimension'] is None


@pytest.mark.parametrize('attribute,value', [('model', 'other'), ('base_url', 'http://example.com'), ('timeout', 0)])
def test_pinned_adapter_settings_are_readonly(attribute, value):
    from agentmesh_memory.embeddings import OllamaEmbedding
    with server() as (url, state):
        adapter = OllamaEmbedding(base_url=url)
        with pytest.raises(AttributeError):
            setattr(adapter, attribute, value)
        identity = adapter.space
        identity['revision'] = 'tampered'
        assert adapter.space['revision'] == DIGEST


@pytest.mark.parametrize('model', [None, '', 'x' * 257, 'bad\nmodel', '../model', 'model name'])
def test_rejects_invalid_model_before_network(model):
    from agentmesh_memory.embeddings import OllamaEmbedding
    import unittest.mock
    with unittest.mock.patch('http.client.HTTPConnection') as connection:
        with pytest.raises(ValueError, match='model'):
            OllamaEmbedding(model=model)
        connection.assert_not_called()


def test_transport_failure_is_explicit_without_fallback():
    from agentmesh_memory.embeddings import OllamaEmbedding
    # Bind then close a loopback port; no remote networking or model changes.
    import socket
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with pytest.raises(RuntimeError, match='Ollama'):
        OllamaEmbedding(base_url=f'http://127.0.0.1:{port}', timeout=1)
