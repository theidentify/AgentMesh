-- SQLite feasibility copy of the 8 live memory tables. PostgreSQL remains authoritative.
CREATE TABLE IF NOT EXISTS source_sessions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, source_agent TEXT NOT NULL DEFAULT 'omp',
 source_session_id TEXT, source_path TEXT NOT NULL UNIQUE, workspace TEXT, project TEXT,
 started_at TEXT, last_seen_at TEXT, metadata TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata)),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS observation_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT NOT NULL UNIQUE, source_agent TEXT NOT NULL DEFAULT 'omp',
 source_path TEXT NOT NULL, source_session_id TEXT, source_event_id TEXT, occurred_at TEXT, project TEXT,
 task_ref TEXT, role TEXT, kind TEXT NOT NULL, content TEXT NOT NULL,
 metadata TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata)), source_hash TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS observation_events_project_time_idx ON observation_events(project,occurred_at DESC);
CREATE INDEX IF NOT EXISTS observation_events_session_idx ON observation_events(source_session_id,occurred_at);
CREATE TABLE IF NOT EXISTS ingestion_cursors (
 source_path TEXT PRIMARY KEY, file_identity TEXT, byte_offset INTEGER NOT NULL DEFAULT 0 CHECK(byte_offset>=0),
 line_number INTEGER NOT NULL DEFAULT 0 CHECK(line_number>=0), source_hash TEXT,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS ingestion_errors (
 id INTEGER PRIMARY KEY AUTOINCREMENT, source_path TEXT NOT NULL, byte_offset INTEGER, line_number INTEGER,
 line_hash TEXT NOT NULL, error_type TEXT NOT NULL, error_message TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(source_path,line_hash)
);
CREATE TABLE IF NOT EXISTS memory_items (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 kind TEXT NOT NULL CHECK(kind IN ('fact','decision','constraint','preference','procedure','open_loop','summary')),
 scope TEXT NOT NULL CHECK(scope IN ('global','project','task','session')), scope_key TEXT, project TEXT,
 content TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','superseded','resolved','deleted')),
 confidence REAL NOT NULL DEFAULT 1.0 CHECK(confidence>=0 AND confidence<=1),
 supersedes_id INTEGER REFERENCES memory_items(id) DEFERRABLE INITIALLY DEFERRED,
 metadata TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata)),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 memory_key TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS memory_items_memory_key_idx ON memory_items(memory_key) WHERE memory_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS memory_items_active_scope_idx ON memory_items(scope,scope_key,project) WHERE status='active';
CREATE TABLE IF NOT EXISTS memory_sources (
 memory_id INTEGER NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
 event_id INTEGER NOT NULL REFERENCES observation_events(id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
 PRIMARY KEY(memory_id,event_id)
);
CREATE TABLE IF NOT EXISTS memory_summaries (
 id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL CHECK(scope IN ('project','task','session')),
 scope_key TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, content TEXT NOT NULL,
 metadata TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata)), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(scope,scope_key,version)
);
CREATE TABLE IF NOT EXISTS summary_state (
 consumer TEXT PRIMARY KEY, last_event_id INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
