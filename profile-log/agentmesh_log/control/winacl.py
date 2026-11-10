"""Windows: give files written from an elevated shell back to the interactive user.

An elevated process owns what it creates as BUILTIN\\Administrators, and Python 3.13+ turns mkdir(mode=0o700) into an
ACL for that owner, SYSTEM and Administrators only. The user's normal token carries Administrators deny-only, so a
later `control release` or `panic-revert` from a normal window cannot read the saved task. When this process is
elevated, each path is handed to the user (owner) with full control; a path that cannot be fixed is returned, so the
caller can warn instead of failing the move that already happened.
"""
import os
import subprocess


def elevated():
    if os.name!='nt': return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError,OSError): return False


def user():
    """The account this process runs as: an elevated window is the same account with a fuller token."""
    name,domain=os.environ.get('USERNAME'),os.environ.get('USERDOMAIN')
    if not name: return None
    return domain+'\\'+name if domain else name


def run(argv):
    return subprocess.run(argv,capture_output=True,timeout=60)


def give_to_user(paths, *, is_elevated=None, account=None, runner=None):
    """Owner and full control for the interactive user on every existing path, when elevated. Returns the paths that
    could not be fixed (all of them when the account is unknown); [] when not elevated or nothing failed."""
    is_elevated,account,runner=is_elevated or elevated,account or user,runner or run
    paths=[str(p) for p in paths if os.path.lexists(p)]
    if not paths or not is_elevated(): return []
    who=account()
    if not who: return paths
    failed=[]
    for path in paths:
        grant=who+(':(OI)(CI)F' if os.path.isdir(path) else ':F')
        try:
            ok=all(runner(argv).returncode==0 for argv in (['icacls',path,'/setowner',who],['icacls',path,'/grant',grant]))
        except (OSError,subprocess.SubprocessError): ok=False
        if not ok: failed.append(path)
    return failed
