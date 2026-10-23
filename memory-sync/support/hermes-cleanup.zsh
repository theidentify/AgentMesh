#!/bin/zsh
# Mac: move AgentMesh's remaining files and names out of ~/.hermes (PR #103).
#   zsh memory-sync/support/hermes-cleanup.zsh deploy            # dry run: checks preconditions, prints actions
#   zsh memory-sync/support/hermes-cleanup.zsh deploy --apply
#   zsh memory-sync/support/hermes-cleanup.zsh rollback          # dry run
#   zsh memory-sync/support/hermes-cleanup.zsh rollback --apply
#   zsh memory-sync/support/hermes-cleanup.zsh check             # read-only: what AgentMesh still has under ~/.hermes
# Run from your own terminal in the main checkout after PR #103 is merged, and
# BEFORE the PR #104 Insight deploy: that one removes ~/.hermes/omp-memory, which
# is the ingest/summary WorkingDirectory until this script moves it.
# ~/.hermes/omp-memory and the org.agentmesh.insight plist belong to PR #104.
# No new copies are made. Remnants are moved (not copied) into a read-only archive.
# Code rollback comes from git (BASE), so no plaintext backups are written.
setopt err_exit no_unset pipe_fail
ACTION=${1:-check} APPLY=${2:-}
R=${AGENTMESH_REPO:-${0:A:h:h:h}} BASE=4b5dd50
AM=$HOME/.agentmesh MS=$HOME/.agentmesh/memory-sync J=$HOME/.agentmesh/jobs H=$HOME/.hermes
DB=$MS/data/mac.db PY=$J/.venv/bin/python LA=$HOME/Library/LaunchAgents D=gui/$(id -u)
ARC=$AM/backups/archive REM=$AM/backups/archive/hermes-remnants-20261023
OLD_WD=$H/omp-memory NEW_WD=$J
CODE=(jobs/agentmesh_production.py:$J/agentmesh_production.py
      summarize_memory.py:$MS/summarize_memory.py
      bounded_digest.py:$MS/bounded_digest.py
      staging_retention.py:$MS/staging_retention.py)
# AgentMesh-owned leftovers in ~/.hermes (Hermes's own files such as power_watchdog.py stay).
STUBS=(${(f)"$(grep -l '^# Moved to ' $H/scripts/*.py 2>/dev/null || true)"})
MOVES=($H/scripts/omp-session-watch.py:$REM/scripts/omp-session-watch.py
       $H/skills/omp-shared-memory:$REM/skills/omp-shared-memory
       $H/skills/omp-summary-metrics-dashboard:$REM/skills/omp-summary-metrics-dashboard
       $AM/services/catch-up:$REM/services/catch-up)
for f in $STUBS; do MOVES+=($f:$REM/scripts/${f:t}); done
for f in $H/scripts/__pycache__/(agentmesh_production|export_omp_memory_to_obsidian|omp_memory_*).cpython-*.pyc(N); do
  MOVES+=($f:$REM/scripts/__pycache__/${f:t}); done
[[ $ACTION == rollback ]] && STUBS=(${(f)"$(ls $REM/scripts/*.py 2>/dev/null | grep -v omp-session-watch || true)"}) && \
  for f in $STUBS; do MOVES+=($H/scripts/${f:t}:$f); done && \
  for f in $REM/scripts/__pycache__/*.pyc(N); do MOVES+=($H/scripts/__pycache__/${f:t}:$f); done

run() { print -r -- "+ $*"; [[ $APPLY == --apply ]] && "$@"; return 0; }
die() { print -r -- "ABORT: $*" >&2; exit 1; }
idle() { while launchctl print $D/org.agentmesh.$1 2>/dev/null | grep -q 'state = running'; do sleep 2; done; }
set_wd() { # role dir
  local p=$LA/org.agentmesh.$1.plist
  run plutil -replace WorkingDirectory -string $2 $p
  run plutil -lint -s $p
  [[ $APPLY == --apply ]] || return 0
  [[ $(plutil -extract WorkingDirectory raw $p) == $2 ]] || die "WorkingDirectory not set in $p"
  print "waiting for $1 tick to finish..."; idle $1
  launchctl bootout $D/org.agentmesh.$1 || true
  launchctl bootstrap $D $p || die "bootstrap failed: $1"
}
checkpoint() { # direction: forward|back
  if [[ $1 == forward ]]; then run $PY $MS/summarize_memory.py migrate-legacy-checkpoint $DB; return; fi
  print -r -- "+ rename summary_state agentmesh:legacy-pg-checkpoint back to hermes"
  [[ $APPLY == --apply ]] || return 0
  $PY - $MS $DB <<'PY'
import sys; sys.path.insert(0, sys.argv[1]); import memory_sync
with memory_sync.connect(sys.argv[2]) as c, c:
    c.execute('BEGIN IMMEDIATE')
    row = c.execute("SELECT last_event_id,updated_at FROM summary_state WHERE consumer='agentmesh:legacy-pg-checkpoint'").fetchone()
    if row and not c.execute("SELECT 1 FROM summary_state WHERE consumer='hermes'").fetchone():
        c.execute("INSERT INTO summary_state(consumer,last_event_id,updated_at) VALUES('hermes',?,?)", tuple(row))
    c.execute("DELETE FROM summary_state WHERE consumer='agentmesh:legacy-pg-checkpoint'")
    print(dict(restored=row[0] if row else None))
PY
}
check() {
  print "== AgentMesh leftovers under ~/.hermes (expect none)"
  print -l $H/skills/omp-*(N) $H/scripts/omp-session-watch.py(N)
  [[ -e $H/omp-memory ]] && print "$H/omp-memory (removed by the PR #104 Insight deploy)"
  grep -l '^# Moved to ' $H/scripts/*.py 2>/dev/null || true
  ls $H/scripts/__pycache__ 2>/dev/null | grep -E '^(agentmesh_|export_omp|omp_)' || true
  print "== launchd plists naming ~/.hermes (expect only ai.hermes.gateway and, until the Insight session ships, org.agentmesh.insight)"
  grep -l '\.hermes' $LA/*.plist || true
  print "== ~/.agentmesh runtime files naming ~/.hermes (expect only install_agent_rules.py, the Hermes recall-skill installer, and insight releases)"
  grep -rIl '\.hermes' --exclude-dir=venv --exclude-dir=.venv --exclude-dir=__pycache__ --exclude-dir=data \
    --exclude-dir=backups --exclude-dir=versions $AM 2>/dev/null || true
  print "== summary_state legacy checkpoint (expect agentmesh:legacy-pg-checkpoint only)"
  $PY -c 'import sqlite3,sys; c=sqlite3.connect("file:"+sys.argv[1]+"?mode=ro",uri=True); print(c.execute("SELECT consumer,last_event_id FROM summary_state WHERE consumer IN (?,?)",("hermes","agentmesh:legacy-pg-checkpoint")).fetchall())' $DB
  print "== newest job results (expect exit 0)"
  for j in ingest summary; do tail -1 $AM/status/launchd-$j.log; done
}

case $ACTION in
check) check; exit 0;;
deploy)
  [[ $(git -C $R rev-parse --abbrev-ref HEAD) == main ]] || die "$R is not on main"
  git -C $R diff --quiet HEAD -- memory-sync || die "$R has local memory-sync changes"
  grep -q "LEGACY_CHECKPOINT = 'agentmesh:legacy-pg-checkpoint'" $R/memory-sync/summarize_memory.py || die "PR #103 not in $R (pull main first)"
  for pair in $CODE; do src=${pair%%:*} dst=${pair#*:}
    cmp -s $dst =(git -C $R show $BASE:memory-sync/$src) || die "$dst differs from $BASE; rollback would not be exact"; done
  for role in ingest summary; do
    [[ $(plutil -extract WorkingDirectory raw $LA/org.agentmesh.$role.plist) == $OLD_WD ]] || die "unexpected WorkingDirectory in $role plist"; done
  for pair in $MOVES; do [[ -e ${pair%%:*} ]] || die "missing ${pair%%:*}"; [[ ! -e ${pair#*:} ]] || die "exists ${pair#*:}"; done
  print "preconditions ok (${#MOVES} moves)"
  for pair in $CODE; do run install -m 600 $R/memory-sync/${pair%%:*} ${pair#*:}; done
  checkpoint forward
  for role in ingest summary; do set_wd $role $NEW_WD; done
  run mkdir -p -m 700 $ARC $REM/scripts/__pycache__ $REM/skills $REM/services
  for pair in $MOVES; do run mv ${pair%%:*} ${pair#*:}; done
  run chmod -R a-w $REM
  [[ $APPLY == --apply ]] && { sleep 70; check; } || print "dry run only; re-run with --apply";;
rollback)
  run chmod -R u+w $REM
  run mkdir -p $H/omp-memory  # old WorkingDirectory; PR #104 may have removed it
  for pair in $MOVES; do run mv ${pair#*:} ${pair%%:*}; done
  run find $REM -depth -type d -empty -delete
  for role in ingest summary; do set_wd $role $OLD_WD; done
  checkpoint back
  for pair in $CODE; do src=${pair%%:*} dst=${pair#*:}
    print -r -- "+ git -C $R show $BASE:memory-sync/$src > $dst"
    [[ $APPLY == --apply ]] && git -C $R show $BASE:memory-sync/$src > $dst; done
  [[ $APPLY == --apply ]] && { sleep 70; check; } || print "dry run only; re-run with --apply";;
*) die "usage: deploy|rollback|check [--apply]";;
esac
