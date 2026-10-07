#!/bin/sh
set -eu
[ "$#" -le 2 ] || { printf '%s\n' 'Usage: sh setup_linux.sh [exchange-folder] [local-dir]' >&2; exit 2; }
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
command -v python3 >/dev/null 2>&1 || { printf '%s\n' 'Install Python 3.10+ with SQLite FTS5 and JSON support, then retry.' >&2; exit 1; }
command -v syncthing >/dev/null 2>&1 || { printf '%s\n' 'Install Syncthing, pair your devices and accept the exchange folder, then retry.' >&2; exit 1; }
python3 - <<'PY'
import sys, sqlite3
try:
    assert sys.version_info >= (3, 10)
    db = sqlite3.connect(':memory:')
    db.execute('CREATE VIRTUAL TABLE probe USING fts5(text)')
    db.execute("SELECT json('{}')")
except (AssertionError, sqlite3.Error):
    sys.exit('Install Python 3.10+ built with SQLite FTS5 and JSON support, then retry.')
PY
exchange=${1:-$SCRIPT_DIR}
local_dir=${2:-"${XDG_DATA_HOME:-${HOME}/.local/share}/AgentMesh"}
exec python3 "$SCRIPT_DIR/bootstrap_windows.py" --node linux --exchange "$exchange" --local-dir "$local_dir"
