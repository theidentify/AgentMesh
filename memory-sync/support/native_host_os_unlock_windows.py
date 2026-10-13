"""Native Windows host check for #7 (OS unlock). Disposable install, identity and task only.

Never touches the real AgentMesh identity, runtime, database or logon task. The only real
Credential Manager effect is one generic credential named after a random disposable key_id,
removed by `cleanup`.

Run from the memory-sync directory of a checkout, with the pinned security requirements:
    python support\\native_host_os_unlock_windows.py ROOT PHASE [ARG]

Phases, in order:
    setup                   disposable signed install, protected identity, OS unlock enabled
    status                  identity-os-unlock status (read-only)
    task-run                start one worker cycle through a disposable interactive scheduled task
    watch SECONDS           worker every 15 s via the task; lock the screen (Win+L) meanwhile
    delete-item             delete the credential, then run task-run again: it must refuse
    reenable                restore OS unlock after delete-item
    s4u                     optional; needs an elevated prompt: same worker as an S4U task, must refuse
    cleanup                 remove the task(s) and the credential; ROOT can then be deleted
"""
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # memory-sync modules
import key_protection as kp
import memory_sync
import os_unlock as ou
import signed_packets as signed
import sqlite_memory
import worker_lifecycle

PASS = 'disposable windows native passphrase'
GROUP = '00000000-0000-4000-8000-0000000000cc'
root, phase = Path(sys.argv[1]).resolve(), sys.argv[2]
app, exchange = root / 'local', root / 'exchange'
data, identity = app / 'data', app / 'identity'
db, runtime = data / 'memory.db', data / 'runtime.json'
state = root / 'state.json'
SOURCE = Path(__file__).resolve().parent.parent  # memory-sync


def load_state():
    return json.loads(state.read_text())


def key_id():
    return signed.parse(signed.read_local(identity / 'identity.json', private=True))['key_id']


def private_dir(path):
    path.mkdir(mode=0o700)
    signed.windows_private(path, provision=True)


def setup():
    private_dir(root)
    private_dir(app)
    private_dir(data)
    sqlite_memory.init_database(db)
    memory_sync.initialize(db, 'windows', GROUP)
    private_dir(exchange)
    (exchange / '.stfolder').mkdir()
    signed.init_identity(identity, GROUP, 'windows')
    kp.protect_identity(identity, lambda prompt: PASS)  # production KDF cost
    workflow = data / 'workflow.json'
    signed.write_local(workflow, {'ingest': False, 'summarize': False})
    signed.write_local(runtime, {'app_root': str(app), 'database': str(db), 'exchange': str(exchange),
                                 'node': 'windows', 'security_dir': str(identity), 'workflow_config': str(workflow)})
    report = ou.enable(identity, ask=lambda prompt: PASS)  # real Credential Manager, disposable key_id
    memory_sync.cycle(db, exchange, security=signed.Security(identity, unlock=lambda: PASS))  # strict policy
    task = 'AgentMesh-OSUnlock-Test-' + uuid.uuid4().hex[:8]
    state.write_text(json.dumps({'task': task, 'key_id': report['key_id']}))
    print(json.dumps({'setup': report, 'task': task}))


def status():
    print(json.dumps(ou.status(identity)))


def wrapper(interval=None):
    args = worker_lifecycle.command(runtime, once=interval is None, interval=interval or 60)
    script = root / ('run-%s.cmd' % ('once' if interval is None else 'watch'))
    script.write_text('@echo off\r\n' + subprocess.list2cmdline(args) + ' > "%s" 2>&1\r\n' % (root / 'task.log'),
                      encoding='utf-8')
    return script


def schtasks(*args, check=True):
    return subprocess.run(['schtasks', *args], capture_output=True, text=True, check=check)


def create_task(name, script, *, s4u=False):
    schtasks('/Delete', '/TN', name, '/F', check=False)
    base = ['/Create', '/TN', name, '/TR', '"%s"' % script, '/SC', 'ONCE', '/ST', '23:59', '/F']
    # /IT = run only when the user is logged on (InteractiveToken, as the real logon task);
    # /NP = S4U ("whether logged on or not", no stored password).
    schtasks(*base, *(['/RU', os.environ['USERNAME'], '/NP'] if s4u else ['/IT']))


def record():
    path = data / '.agentmesh-worker' / 'process.json'
    return json.loads(path.read_text()) if path.exists() else {}


def screen_locked():
    out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq LogonUI.exe', '/NH'], capture_output=True, text=True).stdout
    return 'LogonUI.exe' in out


def run_task(name, seconds=120):
    before = record().get('nonce')
    started = time.monotonic()
    schtasks('/Run', '/TN', name)
    while time.monotonic() - started < seconds:
        current = record()
        if current.get('nonce') != before and current.get('state') in ('stopped', 'failed'):
            break
        time.sleep(0.5)
    current = record()
    report = exchange / 'status' / 'windows.json'
    policy = json.loads(report.read_text())['security']['policy'] if report.exists() else None
    return {'seconds': round(time.monotonic() - started, 1), 'state': current.get('state'),
            'cycles': current.get('cycles'), 'error': current.get('last_error_detail'),
            'console_attached': current.get('console_attached'), 'policy': policy}


def task_run():
    name = load_state()['task']
    create_task(name, wrapper())
    print(json.dumps(run_task(name)))


def watch(seconds):
    name = load_state()['task'] + '-watch'
    create_task(name, wrapper(interval=15))
    schtasks('/Run', '/TN', name)
    timeline, started, last = [], time.monotonic(), None
    try:
        while time.monotonic() - started < seconds:
            current = record()
            sample = (current.get('state'), current.get('cycles'),
                      (current.get('last_error_detail') or {}).get('reason'), screen_locked())
            if sample != last:
                timeline.append([round(time.monotonic() - started), *sample])
                print(json.dumps(timeline[-1]), flush=True)
                last = sample
            time.sleep(1)
    finally:
        subprocess.run([sys.executable, str(SOURCE / 'agentmesh.py'), 'worker-stop', '--runtime', str(runtime)],
                       capture_output=True, cwd=SOURCE)
        schtasks('/End', '/TN', name, check=False)
        schtasks('/Delete', '/TN', name, '/F', check=False)
    print('summary', json.dumps(timeline))


def delete_item():
    ou.current_backend().delete(key_id())
    print('deleted credential for disposable key_id')
    task_run()


def reenable():
    ou.disable(identity)
    print(json.dumps(ou.enable(identity, ask=lambda prompt: PASS)))


def s4u():
    name = load_state()['task'] + '-s4u'
    try:
        create_task(name, wrapper(), s4u=True)
    except subprocess.CalledProcessError as exc:
        print('S4U task could not be created (needs an elevated prompt):', exc.stderr.strip()[:200])
        return
    try:
        print(json.dumps(run_task(name)))
    finally:
        schtasks('/Delete', '/TN', name, '/F', check=False)


def cleanup():
    if state.exists():
        name = load_state()['task']
        for suffix in ('', '-watch', '-s4u'):
            schtasks('/Delete', '/TN', name + suffix, '/F', check=False)
        ou.current_backend().delete(load_state()['key_id'])
    print('cleaned up tasks and the disposable credential; delete', root, 'when done')


{'setup': setup, 'status': status, 'task-run': task_run, 'watch': lambda: watch(int(sys.argv[3])),
 'delete-item': delete_item, 'reenable': reenable, 's4u': s4u, 'cleanup': cleanup}[phase]()
