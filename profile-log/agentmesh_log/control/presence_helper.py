"""The supervisor's presence: it runs its own agentmesh-presence child and trusts only that child's exit code.

Touch ID or the Mac password (LAPolicyDeviceOwnerAuthentication). Any failed path check, timeout or non-zero exit is a
refusal. The summary goes over stdin, never argv, and the child gets HOME only. The path checks stop a symlink or loose
mode, not a same-user attacker who can rewrite ~/.agentmesh (the spec already accepts that).
"""
import os
import stat
import subprocess
from ..presence import ACTIONS

TIMEOUT_S=75  # the helper's own sheet timeout is 60 s


def safe(path):
    """An owner-only regular file in an owner-only directory, neither a symlink."""
    try: info,parent=os.lstat(path),os.lstat(os.path.dirname(path))
    except OSError: return False
    for item in (info,parent):
        if item.st_uid!=os.getuid() or stat.S_IMODE(item.st_mode)&0o022: return False
    return stat.S_ISREG(info.st_mode) and stat.S_ISDIR(parent.st_mode)


class HelperPresence:
    def __init__(self, path, *, runner=subprocess.run, timeout_s=TIMEOUT_S):
        self.path,self.runner,self.timeout_s=str(path),runner,timeout_s

    def run(self, argv, data=b''):
        """The helper's exit code, or None when it was refused before running or did not finish."""
        if os.name=='nt' or not safe(self.path): return None
        try: return self.runner([self.path,*argv],input=data,capture_output=True,timeout=self.timeout_s,
                                env={'HOME':os.environ.get('HOME','')}).returncode
        except (OSError,subprocess.SubprocessError): return None

    def available(self): return self.run(['available'])==0

    def confirm(self, action, summary):
        if action not in ACTIONS or type(summary) is not str: return False
        return self.run(['confirm','--action',action],summary.encode('utf-8'))==0
