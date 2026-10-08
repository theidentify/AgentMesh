"""AgentMesh Insight: local read-only operational and memory browser."""
import argparse
import json
import os
from pathlib import Path
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
import adapters


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


def make_server(home, port, sqlite=None, dsn=None):
    home = Path(home).expanduser()
    readers = {'sqlite':adapters.MemoryReader(sqlite=sqlite), 'postgres':adapters.MemoryReader(dsn=dsn, backend='postgres')}
    default = 'postgres' if dsn else 'sqlite'
    metrics_cache = Cache(lambda: adapters.metrics(home))
    ops_cache = {key:Cache(lambda key=key: adapters.operations(home, readers[key], sqlite)) for key in readers}

    def connections():
        rows = []
        for key, reader in readers.items():
            configured = bool(dsn) if key == 'postgres' else bool(sqlite)
            row = {'id':key,'label':reader.label,'configured':configured,'available':False,'authority':key=='postgres','items':None,'summaries':None}
            if configured:
                try:
                    with reader.connect() as c:
                        row['items'] = reader.query(c, 'SELECT COUNT(*) AS count FROM ' + reader.table('items'))[0]['count']
                        row['summaries'] = reader.query(c, 'SELECT COUNT(*) AS count FROM ' + reader.table('summaries'))[0]['count']
                    row['available'] = True
                except Exception:
                    row['error'] = 'Configured backend unavailable'
            rows.append(row)
        return {'default':default,'rows':rows,'readonly':True}

    connection_cache = Cache(connections)

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
            if host not in ('127.0.0.1:'+str(self.server.server_port), 'localhost:'+str(self.server.server_port)):
                return self.send(403, {'error':'Loopback host required'})
            origin = self.headers.get('Origin')
            if origin and origin != 'http://'+host:
                return self.send(403, {'error':'Same origin required'})
            if len(self.path) > 2048:
                return self.send(400, {'error':'Request too long'})
            parsed = urlsplit(self.path)
            path = parsed.path
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
                    return self.send(200, {'status':'ok','pid':os.getpid(),'readonly':True})
                if path == '/api/metrics':
                    if args:
                        raise ValueError('Unknown parameter')
                    value = metrics_cache.get()
                    return self.send(503 if 'error' in value else 200, value)
                if path == '/api/connections':
                    if args:
                        raise ValueError('Unknown parameter')
                    return self.send(200, connection_cache.get())
                if path == '/api/operations' or path == '/api/memory' or re.fullmatch(r'/api/memory/(items|summaries)/[0-9]+', path):
                    backend = args.pop('backend', default)
                    if backend not in readers:
                        raise ValueError('Unknown backend')
                    reader = readers[backend]
                    if path == '/api/operations':
                        if args:
                            raise ValueError('Unknown parameter')
                        return self.send(200, ops_cache[backend].get())
                    if path == '/api/memory':
                        collection = args.pop('collection','items')
                        # Validate user filters before trying an absent backend.
                        reader.table(collection)
                        if set(args)-{'q','project','kind','status','scope','limit','offset'}:
                            raise ValueError('Unknown filter')
                        value = reader.browse(collection, args)
                    else:
                        if args:
                            raise ValueError('Unknown detail parameter')
                        _, _, _, collection, item_id = path.split('/')
                        value = reader.detail(collection, int(item_id))
                    return self.send(200, value)
                return self.send(404, {'error':'Not found'})
            except ValueError:
                return self.send(400, {'error':'Invalid parameters'})
            except Exception:
                return self.send(503, adapters.unavailable('Requested read-only source'))

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
    parser.add_argument('--home', type=Path, default=Path(os.environ.get('HERMES_HOME', str(Path.home()/'.hermes'))))
    parser.add_argument('--sqlite', type=Path, help='Existing AgentMesh staging database, never created')
    # DSN only via environment, not CLI/process list or a web form.
    args = parser.parse_args()
    server = make_server(args.home, args.port, args.sqlite, os.environ.get('INSIGHT_PG_DSN'))
    print(json.dumps({'url':'http://127.0.0.1:'+str(server.server_port),'pid':os.getpid(),'readonly':True}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
