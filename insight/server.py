"""AgentMesh Insight: local read-only status for the profile log, its jobs and this device."""
import argparse
import json
import os
from pathlib import Path
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
import adapters
import remote_access
import storage


class Cache:
    def __init__(self, load, ttl=15):
        self.load, self.ttl = load, ttl
        self.expires, self.value = 0, None
        self.lock = threading.Lock()

    def get(self):
        with self.lock:
            if time.monotonic() >= self.expires:
                try:
                    self.value = self.load()
                except Exception:
                    self.value = adapters.unavailable('Metrics source')
                self.expires = time.monotonic() + self.ttl
            return self.value


def make_server(port, agentmesh_home=None, metrics_logs=None, cron_archive=None, insight_agent_key=None):
    agentmesh_home = adapters.agentmesh_root(agentmesh_home)
    metrics_cache = Cache(lambda: adapters.metrics(agentmesh_home, metrics_logs, cron_archive))
    ops_cache = Cache(lambda: adapters.operations(agentmesh_home))
    profile_cache = Cache(lambda: adapters.profile_log_status(agentmesh_home))
    stats_cache = Cache(lambda: adapters.memory_stats(agentmesh_home, insight_agent_key), ttl=60)
    storage_cache = storage.Cache(storage.Observer(agentmesh_home, metrics_logs, running=Path(__file__).parent).compute)
    storage_cache.warm()
    access = remote_access.Verifier()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def send(self, code, data, kind='application/json; charset=utf-8'):
            body = data if isinstance(data, bytes) else json.dumps(adapters.redact(data), ensure_ascii=False, allow_nan=False).encode()
            self.send_response(code)
            for name, value in (
                ('Content-Type',kind),('Content-Length',str(len(body))),('Cache-Control','no-store'),
                ('X-Content-Type-Options','nosniff'),('X-Frame-Options','SAMEORIGIN'),
                ('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"),
                ('Referrer-Policy','no-referrer'),
            ):
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            host = self.headers.get('Host','')
            remote = remote_access.is_remote(self.headers)
            if remote and not access.allowed(self.headers):
                return self.send(403, {'error':'Forbidden'})
            if not remote and host not in ('127.0.0.1:'+str(self.server.server_port), 'localhost:'+str(self.server.server_port)):
                return self.send(403, {'error':'Loopback host required'})
            origin = self.headers.get('Origin')
            if not remote and origin and origin != 'http://'+host:
                return self.send(403, {'error':'Same origin required'})
            if len(self.path) > 2048:
                return self.send(400, {'error':'Request too long'})
            parsed = urlsplit(self.path)
            path = parsed.path
            if remote and path not in remote_access.ALLOWED_PATHS:
                return self.send(404, {'error':'Not found'})
            try:
                multi = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=12)
                if any(len(v) != 1 for v in multi.values()):
                    raise ValueError('Duplicate parameter')
                args = {k:v[0] for k,v in multi.items()}
                if path in ('/', '/metrics.html'):
                    if args:
                        raise ValueError('No page parameters')
                    return self.send(200, Path(__file__).with_name('index.html' if path=='/' else 'metrics.html').read_bytes(), 'text/html; charset=utf-8')
                if path == '/health':
                    return self.send(200, {'status':'ok','pid':os.getpid(),'readonly':True,'remote':remote})
                if path == '/api/storage':
                    if args: raise ValueError('Unknown parameter')
                    return self.send(200, storage_cache.get())
                if path == '/api/profile-log':
                    if args:
                        raise ValueError('Unknown parameter')
                    return self.send(200, profile_cache.get())
                if path == '/api/log-counts':
                    if args: raise ValueError('Unknown parameter')
                    return self.send(200, stats_cache.get())
                if path == '/api/metrics':
                    if args:
                        raise ValueError('Unknown parameter')
                    value = metrics_cache.get()
                    return self.send(503 if 'error' in value else 200, value)
                if path == '/api/operations':
                    if args:
                        raise ValueError('Unknown parameter')
                    return self.send(200, ops_cache.get())
                return self.send(404, {'error':'Not found'})
            except ValueError:
                return self.send(400, {'error':'Invalid parameters'})
            except Exception:
                return self.send(503, adapters.unavailable('Requested read-only source'))

        def __getattr__(self, name):
            if name.startswith('do_') and remote_access.is_remote(self.headers):
                return self.do_POST
            raise AttributeError(name)

        def do_POST(self):
            self.send(405, {'error':'Read-only; GET only'})

        do_PUT = do_POST
        do_DELETE = do_POST
        do_PATCH = do_POST
        do_OPTIONS = do_POST
        do_HEAD = do_POST

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--cron-archive', type=Path, help='Frozen history of the retired cron digest; defaults to backups/archive/digest-cron under the AgentMesh home')
    parser.add_argument('--agentmesh-home', type=Path, default=Path(os.environ.get('AGENTMESH_HOME', str(Path.home()/'.agentmesh'))))
    parser.add_argument('--metrics-log', type=Path, action='append')
    # The staging database is gone (SQLite retirement R5b). Installed plists still pass --sqlite: accept and ignore it.
    parser.add_argument('--sqlite', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--insight-agent-key', type=Path, help='Read-only profile-log agent for memory counts; defaults to agents/insight.key')
    args = parser.parse_args()
    server = make_server(args.port, agentmesh_home=args.agentmesh_home, metrics_logs=args.metrics_log, cron_archive=args.cron_archive, insight_agent_key=args.insight_agent_key)
    print(json.dumps({'url':'http://127.0.0.1:'+str(server.server_port),'pid':os.getpid(),'readonly':True}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
