"""Owner-run Insight deploy that moves Insight's last Hermes-side inputs into the AgentMesh home.

Dry run by default: prints the plan and changes nothing. --apply performs it.

Deploy (--rev <merge sha> --apply):
  1. stage ~/.agentmesh/status/insight-release-<sha12> from `git show` (0700 dir, 0600 files, release.json);
  2. build ~/.agentmesh/backups/archive/digest-cron from ~/.hermes/cron/usage_audit.jsonl and state.db
     (copy only: counters and timings of the retired digest job, no prompts or error text; Hermes keeps its files);
  3. copy ~/.hermes/omp-memory/summary-metrics.jsonl to
     backups/archive/summary-metrics.jsonl.pre-relocate-20261021-omp-memory (byte-checked),
     then move ~/.hermes/omp-memory to the Trash (recoverable);
  4. point org.agentmesh.insight at the release and drop HERMES_HOME from its plist;
  5. restart the agent and check /health (skip with --no-restart).

Rollback (--rollback --rev <old sha> --apply): restore omp-memory from the newest Trash entry when absent,
point the plist back and set HERMES_HOME again, restart. The archive copies stay (read-only history).
"""
import argparse
import hashlib
import json
import os
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

FILES = ('server.py', 'adapters.py', 'remote_access.py', 'metrics.py', 'libpq_reader.py', 'syncthing_transport.py',
         'index.html', 'metrics.html', 'storage.py', 'pricing.json')
JOB = '6fcf5646d34d'
AUDIT_FIELDS = ('fire_id', 'job_id', 'ts', 'duration_ms', 'prompt_tokens', 'completion_tokens', 'total_tokens', 'model')
SESSION_FIELDS = ('id', 'started_at', 'input_tokens', 'cache_read_tokens', 'reasoning_tokens', 'api_call_count', 'actual_cost_usd', 'model')
LEGACY_RUNS = 'summary-metrics.jsonl.pre-relocate-20261021-omp-memory'
LABEL = 'org.agentmesh.insight'


def stage_release(repo, rev, root):
    target = root/('insight-release-'+rev[:12])
    if target.exists():
        return target
    tmp = target.with_name(target.name+'.tmp')
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(mode=0o700, parents=True)
    hashes = {}
    for name in FILES:
        data = subprocess.run(['git', '-C', str(repo), 'show', rev+':insight/'+name], check=True, capture_output=True).stdout
        path = tmp/name
        path.write_bytes(data); path.chmod(0o600)
        hashes[name] = hashlib.sha256(data).hexdigest()
    (tmp/'release.json').write_text(json.dumps({'revision':rev, 'files':hashes}, indent=2))
    (tmp/'release.json').chmod(0o600)
    tmp.rename(target)
    return target


def build_cron_archive(hermes, target):
    """Copy only the retired digest job's run counters; Hermes' own files stay untouched."""
    tmp = target.with_name(target.name+'.tmp')
    shutil.rmtree(tmp, ignore_errors=True)
    (tmp/'cron').mkdir(mode=0o700, parents=True)
    rows = []
    audit = hermes/'cron/usage_audit.jsonl'
    if audit.exists():
        for line in audit.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get('job_id') == JOB:
                    rows.append(dict({k:r[k] for k in AUDIT_FIELDS if k in r}, error=bool(r.get('error'))))
    (tmp/'cron/usage_audit.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    sessions = []
    if (hermes/'state.db').exists():
        with sqlite3.connect((hermes/'state.db').resolve().as_uri()+'?mode=ro', uri=True) as s:
            have = [c for c in SESSION_FIELDS if c in {r[1] for r in s.execute('PRAGMA table_info(sessions)')}]
            sessions = s.execute('SELECT '+','.join(have)+' FROM sessions WHERE id LIKE ?', ('cron_'+JOB+'_%',)).fetchall()
        with sqlite3.connect(tmp/'state.db') as d:
            d.execute('CREATE TABLE sessions('+','.join(have)+')')
            d.executemany('INSERT INTO sessions VALUES('+','.join('?'*len(have))+')', sessions)
    for path in tmp.rglob('*'):
        path.chmod(0o700 if path.is_dir() else 0o600)
    tmp.rename(target)
    return len(rows), len(sessions)


def referencing_agents(agents, path):
    """LaunchAgent plists that still name path, e.g. as WorkingDirectory."""
    def named(value):
        if isinstance(value, dict): return any(named(v) for v in value.values())
        if isinstance(value, list): return any(named(v) for v in value)
        return isinstance(value, str) and (value == str(path) or value.startswith(str(path)+'/'))
    found = []
    for p in sorted(Path(agents).glob('*.plist')):
        try:
            with open(p, 'rb') as f:
                if named(plistlib.load(f)): found.append(p.name)
        except (OSError, plistlib.InvalidFileException, ValueError):
            continue
    return found


def set_plist(plist, release, hermes_home=None):
    with open(plist, 'rb') as f:
        value = plistlib.load(f)
    args = value['ProgramArguments']
    if not str(args[2]).endswith('/server.py'):
        raise SystemExit('unexpected ProgramArguments: '+repr(args))
    args[2] = str(release/'server.py')  # plistlib, never plutil -replace on array items (it inserts)
    value['WorkingDirectory'] = str(release)
    env = value.setdefault('EnvironmentVariables', {})
    if hermes_home: env['HERMES_HOME'] = str(hermes_home)
    else: env.pop('HERMES_HOME', None)
    tmp = Path(str(plist)+'.tmp')
    with open(tmp, 'wb') as f:
        plistlib.dump(value, f)
    os.replace(tmp, plist)


def restart(plist, port):
    domain = 'gui/'+str(os.getuid())
    subprocess.run(['launchctl', 'bootout', domain+'/'+LABEL], capture_output=True)
    subprocess.run(['launchctl', 'bootstrap', domain, str(plist)], check=True)
    for _ in range(15):
        try:
            with urllib.request.urlopen('http://127.0.0.1:'+str(port)+'/health', timeout=2) as r:
                return json.load(r)
        except OSError:
            time.sleep(1)
    raise SystemExit('Insight did not answer /health; run the rollback')


def main(argv=None):
    home = Path.home()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--rev', required=True, help='commit to deploy (or roll back to)')
    p.add_argument('--repo', type=Path, default=Path(__file__).resolve().parent.parent)
    p.add_argument('--agentmesh-home', type=Path, default=home/'.agentmesh')
    p.add_argument('--hermes-home', type=Path, default=home/'.hermes')
    p.add_argument('--plist', type=Path, default=home/'Library/LaunchAgents'/(LABEL+'.plist'))
    p.add_argument('--trash', type=Path, default=home/'.Trash')
    p.add_argument('--launch-agents', type=Path, default=home/'Library/LaunchAgents')
    p.add_argument('--port', type=int, default=51877)
    p.add_argument('--rollback', action='store_true')
    p.add_argument('--apply', action='store_true', help='perform the plan (default: dry run)')
    p.add_argument('--no-restart', action='store_true')
    a = p.parse_args(argv)
    rev = subprocess.run(['git', '-C', str(a.repo), 'rev-parse', a.rev+'^{commit}'], check=True, capture_output=True, text=True).stdout.strip()
    am, hermes = a.agentmesh_home, a.hermes_home
    releases, archive, omp = am/'status', am/'backups/archive', hermes/'omp-memory'
    release = releases/('insight-release-'+rev[:12])
    say = (lambda s: print(s)) if a.apply else (lambda s: print('[dry run] '+s))
    with open(a.plist, 'rb') as f:
        current = plistlib.load(f)
    # Check before touching anything, so a surprise never leaves a half-applied move.
    if current.get('Label') != LABEL or not str(current.get('ProgramArguments', [None]*3)[2]).endswith('/server.py'):
        raise SystemExit('unexpected plist: '+str(a.plist))
    users = [n for n in referencing_agents(a.launch_agents, omp) if n != a.plist.name] if not a.rollback and omp.exists() else []
    if users:
        # launchd cannot spawn a job whose WorkingDirectory is gone: run the PR #103 plist fix first.
        raise SystemExit('still referenced by '+', '.join(users)+'; move their WorkingDirectory off '+str(omp)+' first (PR #103)')
    say('plist now: '+current.get('WorkingDirectory', '?')+' HERMES_HOME='+str(current.get('EnvironmentVariables', {}).get('HERMES_HOME')))
    say(('reuse ' if release.exists() else 'stage ')+str(release))
    if a.apply: stage_release(a.repo, rev, releases)
    if not a.rollback:
        if (archive/'digest-cron').exists():
            say('keep existing '+str(archive/'digest-cron'))
        else:
            say('build '+str(archive/'digest-cron')+' from '+str(hermes/'cron/usage_audit.jsonl')+' and state.db (copy only)')
            if a.apply:
                archive.mkdir(mode=0o700, parents=True, exist_ok=True)
                say('archived %d audit rows, %d sessions' % build_cron_archive(hermes, archive/'digest-cron'))
        if omp.exists():
            runs = omp/'summary-metrics.jsonl'
            if runs.exists():
                dest = archive/LEGACY_RUNS
                say('copy '+str(runs)+' -> '+str(dest))
                if a.apply:
                    archive.mkdir(mode=0o700, parents=True, exist_ok=True)
                    if not dest.exists(): shutil.copy2(runs, dest); dest.chmod(0o600)
                    if dest.read_bytes() != runs.read_bytes(): raise SystemExit('archive copy differs from '+str(runs)+'; stopping')
            trashed = a.trash/('agentmesh-omp-memory-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
            say('move '+str(omp)+' -> '+str(trashed))
            if a.apply: shutil.move(str(omp), str(trashed))
        else:
            say('nothing at '+str(omp))
        say('plist -> '+str(release)+', drop HERMES_HOME')
        if a.apply: set_plist(a.plist, release)
    else:
        if not omp.exists():
            found = sorted(a.trash.glob('agentmesh-omp-memory-*'))
            if found:
                say('restore '+str(found[-1])+' -> '+str(omp))
                if a.apply: shutil.move(str(found[-1]), str(omp))
        say('plist -> '+str(release)+', HERMES_HOME='+str(hermes))
        if a.apply: set_plist(a.plist, release, hermes)
    if not a.apply:
        print('Dry run only; nothing changed. Re-run with --apply.')
        return 0
    if a.no_restart:
        return 0
    print('health:', restart(a.plist, a.port))
    try:
        with urllib.request.urlopen('http://127.0.0.1:'+str(a.port)+'/api/metrics', timeout=10) as r:
            d = json.load(r)
        print('runs:', len(d['runs']['bounded']), 'old cron runs:', len(d['runs']['historical']),
              'failure reasons:', [(x['reason'], x['count']) for x in (d.get('failure_reasons') or {}).get('rows', [])])
    except (OSError, ValueError, KeyError) as e:
        print('metrics check failed:', type(e).__name__)
    return 0


if __name__ == '__main__':
    sys.exit(main())
