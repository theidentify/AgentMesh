"""Windowless Windows logon entry (agentmeshw.exe). Never a general CLI.

Built as a GUI-subsystem executable so Windows allocates no console at logon.
It runs only `worker-start` through the adjacent console agentmesh.exe, with
CREATE_NO_WINDOW, and returns its exit code. The worker lifecycle is unchanged.
"""
import os
from pathlib import Path
import subprocess
import sys

# A cold boot can take minutes before the first sync cycle completes; a short
# deadline made worker-start stop the worker it had just started (rc.11).
LOGON_TIMEOUT = 900


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] != 'worker-start':
        return 2
    cli = Path(sys.executable).with_name('agentmesh.exe')
    env = dict(os.environ)
    # agentmesh.exe is another onefile program: never let it reuse this extraction.
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    # argparse keeps the last --timeout, so this overrides the task's value
    # without changing the registered (owned, read-back) task definition.
    return subprocess.run([str(cli), *argv, '--timeout', str(LOGON_TIMEOUT)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, env=env,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).returncode


if __name__ == '__main__':
    raise SystemExit(main())
