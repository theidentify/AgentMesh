"""Windowless Windows logon entry (agentmeshw.exe). Never a general CLI.

Built as a GUI-subsystem executable so Windows allocates no console at logon.
It runs only `worker-start` through the adjacent console agentmesh.exe, with
CREATE_NO_WINDOW, and returns its exit code. The worker lifecycle is unchanged.
"""
import os
from pathlib import Path
import subprocess
import sys


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] != 'worker-start':
        return 2
    cli = Path(sys.executable).with_name('agentmesh.exe')
    env = dict(os.environ)
    # agentmesh.exe is another onefile program: never let it reuse this extraction.
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    return subprocess.run([str(cli), *argv], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, env=env,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).returncode


if __name__ == '__main__':
    raise SystemExit(main())
