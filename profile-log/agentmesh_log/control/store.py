"""control/state.db: unit backends and specs, native runs, watchdog kills and failed units (owner-only SQLite).

The supervisor and the owner's CLI (adopt, release, panic-revert) share it; every write is one autocommit statement.
"""
import json
import os
import sqlite3
import stat
import threading
from pathlib import Path
from ..storage.filesystem import _check

SCHEMA='''
CREATE TABLE IF NOT EXISTS units(name TEXT PRIMARY KEY, backend TEXT NOT NULL CHECK(backend IN ('launchd','native')),
    spec TEXT, changed_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, unit TEXT NOT NULL, pid INTEGER NOT NULL, pid_started TEXT,
    started_at INTEGER NOT NULL, ended_at INTEGER, exit INTEGER, outcome TEXT);
CREATE INDEX IF NOT EXISTS runs_unit ON runs(unit, started_at);
CREATE TABLE IF NOT EXISTS kills(unit TEXT NOT NULL, at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS failed(unit TEXT PRIMARY KEY, since INTEGER NOT NULL, reason TEXT NOT NULL);
'''


FAILURES=frozenset(('failed','killed_stuck','launch_error'))


class Store:
    def __init__(self, path):
        path=Path(path); _check(path)
        try: os.close(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600))
        except FileExistsError: pass
        info=path.lstat()
        if not stat.S_ISREG(info.st_mode) or (os.name!='nt' and (info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600)):
            raise ValueError('unsafe state db')
        self.lock=threading.Lock()
        self.db=sqlite3.connect(str(path),check_same_thread=False,isolation_level=None,timeout=10)
        self.db.executescript(SCHEMA)

    def _one(self, sql, args=()):
        with self.lock: return self.db.execute(sql,args).fetchone()

    def _all(self, sql, args=()):
        with self.lock: return self.db.execute(sql,args).fetchall()

    def _run(self, sql, args=()):
        with self.lock: return self.db.execute(sql,args)

    def backend(self, name):
        row=self._one('SELECT backend FROM units WHERE name=?',(name,))
        return row[0] if row else 'launchd'

    def spec(self, name):
        row=self._one('SELECT spec FROM units WHERE name=?',(name,))
        return json.loads(row[0]) if row and row[0] else None

    def changed_at(self, name):
        row=self._one('SELECT changed_at FROM units WHERE name=?',(name,))
        return row[0] if row else None

    def set_backend(self, name, backend, spec, at):
        self._run('INSERT INTO units(name,backend,spec,changed_at) VALUES(?,?,?,?) ON CONFLICT(name) DO UPDATE SET '
            'backend=excluded.backend,spec=excluded.spec,changed_at=excluded.changed_at',
            (name,backend,None if spec is None else json.dumps(spec,sort_keys=True),at))

    def native_units(self):
        return {name:json.loads(spec) for name,spec in self._all("SELECT name,spec FROM units WHERE backend='native'")}

    def start_run(self, unit, pid, pid_started, at):
        return self._run('INSERT INTO runs(unit,pid,pid_started,started_at) VALUES(?,?,?,?)',(unit,pid,pid_started,at)).lastrowid

    def end_run(self, run_id, exit, outcome, at):
        self._run('UPDATE runs SET ended_at=?,exit=?,outcome=? WHERE id=? AND ended_at IS NULL',(at,exit,outcome,run_id))

    def open_runs(self, unit=None):
        sql='SELECT id,unit,pid,pid_started,started_at FROM runs WHERE ended_at IS NULL'
        rows=self._all(sql+' AND unit=? ORDER BY id',(unit,)) if unit else self._all(sql+' ORDER BY id')
        return [dict(id=r[0],unit=r[1],pid=r[2],pid_started=r[3],started_at=r[4]) for r in rows]

    def last_start(self, unit):
        return self._one('SELECT MAX(started_at) FROM runs WHERE unit=?',(unit,))[0]

    def last_exit(self, unit):
        row=self._one('SELECT exit FROM runs WHERE unit=? AND ended_at IS NOT NULL ORDER BY id DESC LIMIT 1',(unit,))
        return row[0] if row else None

    def failure_streak(self, unit, before_id=None, limit=200):
        """Failed runs in a row, newest first, up to the last ok run. Stops, orphans and panic reverts are neither."""
        sql='SELECT outcome FROM runs WHERE unit=? AND ended_at IS NOT NULL'+(' AND id<?' if before_id else '')+' ORDER BY id DESC LIMIT ?'
        streak=0
        for (outcome,) in self._all(sql,(unit,before_id,limit) if before_id else (unit,limit)):
            if outcome in FAILURES: streak+=1
            elif outcome=='ok': break
        return streak

    def add_kill(self, unit, at): self._run('INSERT INTO kills(unit,at) VALUES(?,?)',(unit,at))

    def kills_since(self, unit, since):
        return self._one('SELECT COUNT(*) FROM kills WHERE unit=? AND at>=?',(unit,since))[0]

    def set_failed(self, unit, reason, at):
        self._run('INSERT OR IGNORE INTO failed(unit,since,reason) VALUES(?,?,?)',(unit,at,reason))

    def failed(self, unit):
        row=self._one('SELECT since,reason FROM failed WHERE unit=?',(unit,))
        return dict(since=row[0],reason=row[1]) if row else None

    def clear_failed(self, unit):
        return self._run('DELETE FROM failed WHERE unit=?',(unit,)).rowcount>0

    def close(self):
        with self.lock: self.db.close()
