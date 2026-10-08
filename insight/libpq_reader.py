"""Minimal parameterized libpq reader, no Python PostgreSQL dependency.

No SQL is accepted from HTTP clients. This private adapter executes only the
server's fixed SELECT templates in a transaction explicitly marked READ ONLY.
"""
import ctypes as C
import ctypes.util
import json
import os
from pathlib import Path


class Postgres:
    def __init__(self, dsn):
        library = os.environ.get('INSIGHT_LIBPQ') or ctypes.util.find_library('pq')
        if not library:
            library = next((str(p) for p in (Path('/opt/homebrew/opt/libpq/lib/libpq.dylib'), Path('/usr/local/opt/libpq/lib/libpq.dylib')) if p.exists()), None)
        if not library:
            raise RuntimeError('libpq unavailable')
        self.lib = C.CDLL(library)
        for name, result, args in (
            ('PQconnectdb', C.c_void_p, [C.c_char_p]),
            ('PQstatus', C.c_int, [C.c_void_p]),
            ('PQfinish', None, [C.c_void_p]),
            ('PQexec', C.c_void_p, [C.c_void_p, C.c_char_p]),
            ('PQexecParams', C.c_void_p, [C.c_void_p, C.c_char_p, C.c_int, C.c_void_p, C.POINTER(C.c_char_p), C.c_void_p, C.c_void_p, C.c_int]),
            ('PQresultStatus', C.c_int, [C.c_void_p]),
            ('PQntuples', C.c_int, [C.c_void_p]),
            ('PQgetvalue', C.c_char_p, [C.c_void_p, C.c_int, C.c_int]),
            ('PQclear', None, [C.c_void_p]),
        ):
            fn = getattr(self.lib, name)
            fn.restype = result
            fn.argtypes = args
        # Bound connection latency even for an unavailable authority.
        self.conn = self.lib.PQconnectdb((dsn + ' connect_timeout=3').encode())
        if not self.conn or self.lib.PQstatus(self.conn) != 0:
            self.close()
            raise RuntimeError('PostgreSQL unavailable')
        try:
            for command in ('BEGIN READ ONLY', "SET LOCAL statement_timeout='3000ms'", "SET LOCAL idle_in_transaction_session_timeout='5000ms'"):
                self.command(command)
        except Exception:
            self.close()
            raise

    def command(self, sql):
        result = self.lib.PQexec(self.conn, sql.encode())
        try:
            if not result or self.lib.PQresultStatus(result) != 1:
                raise RuntimeError('PostgreSQL read transaction unavailable')
        finally:
            if result:
                self.lib.PQclear(result)

    def query(self, sql, params=()):
        # Templates share SQLite placeholders. None of them contain literal '?'.
        for n in range(len(params)):
            sql = sql.replace('?', '$' + str(n + 1), 1)
        values = (C.c_char_p * len(params))(*[str(v).encode() for v in params])
        result = self.lib.PQexecParams(self.conn, ('SELECT row_to_json(t)::text FROM (' + sql + ') t').encode(), len(params), None, values, None, None, 0)
        try:
            if not result or self.lib.PQresultStatus(result) != 2:
                raise RuntimeError('PostgreSQL read failed')
            return [json.loads(self.lib.PQgetvalue(result, n, 0)) for n in range(self.lib.PQntuples(result))]
        finally:
            if result:
                self.lib.PQclear(result)

    def close(self):
        if getattr(self, 'conn', None):
            self.lib.PQfinish(self.conn)
            self.conn = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()  # Disconnect rolls back the read-only transaction.
