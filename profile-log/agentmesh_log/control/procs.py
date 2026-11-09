"""The process primitives the native backend needs, one implementation per OS.

POSIX: each child leads its own session (start_new_session), so SIGTERM and SIGKILL go to its whole process group.
`ps -o lstart` names one process for the pid-reuse check.

Windows has no process groups to signal and no SIGTERM. Each child gets CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
and stopping it is `taskkill /T /F`: the child and every process it started, ended at once. That is what Task Scheduler
did to these tasks before (End task and the execution time limit both terminate), so a unit loses nothing it had. The
pid-reuse token is the process creation time from GetProcessTimes, and a process that has exited has no token.
"""
import os
import signal
import subprocess

WINDOWS=os.name=='nt'
CREATE_NEW_PROCESS_GROUP=0x00000200
CREATE_NO_WINDOW=0x08000000
STILL_ACTIVE=259
PROCESS_QUERY_LIMITED_INFORMATION=0x1000


def spawn_options(spec, windows=WINDOWS):
    """Popen keyword arguments that detach a child the way its OS scheduler did."""
    if windows: return dict(creationflags=CREATE_NEW_PROCESS_GROUP|CREATE_NO_WINDOW,close_fds=True)
    return dict(start_new_session=True,umask=spec['umask'],close_fds=True)


def taskkill():
    return os.path.join(os.environ.get('SystemRoot') or r'C:\Windows','System32','taskkill.exe')


def end_tree(pid, runner=subprocess.run):
    """Windows: end pid and its descendants. A process that is already gone is not an error."""
    try: runner([taskkill(),'/PID',str(pid),'/T','/F'],capture_output=True,timeout=30)
    except (OSError,subprocess.SubprocessError): pass


def signal_group(pid, sig):
    try: os.killpg(pid,sig)
    except (ProcessLookupError,PermissionError): pass


def terminate(pid, *, windows=WINDOWS, runner=subprocess.run):
    """Ask the child's group to stop: SIGTERM on POSIX; on Windows there is no polite form, so end the tree."""
    if windows: end_tree(pid,runner)
    else: signal_group(pid,signal.SIGTERM)


def kill(pid, *, windows=WINDOWS, runner=subprocess.run):
    if windows: end_tree(pid,runner)
    else: signal_group(pid,signal.SIGKILL)


def posix_token(pid, runner=subprocess.run):
    try: process=runner(['/bin/ps','-o','lstart=','-p',str(pid)],capture_output=True,text=True,timeout=5)
    except (OSError,subprocess.SubprocessError): return None
    if process.returncode!=0: return None
    return process.stdout.strip() or None


def windows_token(pid):
    """The creation time (FILETIME ticks) of a live process, or None."""
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.OpenProcess.argtypes=(wintypes.DWORD,wintypes.BOOL,wintypes.DWORD)
    kernel.GetProcessTimes.argtypes=(wintypes.HANDLE,)+(ctypes.POINTER(wintypes.FILETIME),)*4
    kernel.GetExitCodeProcess.argtypes=(wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD))
    kernel.CloseHandle.argtypes=(wintypes.HANDLE,)
    handle=kernel.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,False,int(pid))
    if not handle: return None
    try:
        # A handle someone still holds (the supervisor's own Popen) opens an exited process too.
        code=wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)) or code.value!=STILL_ACTIVE: return None
        times=[wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle,*(ctypes.byref(t) for t in times)): return None
        return str((times[0].dwHighDateTime<<32)|times[0].dwLowDateTime)
    finally: kernel.CloseHandle(handle)


def started_token(pid, runner=subprocess.run):
    """With the pid it names one process, so a reused pid is never signalled."""
    if WINDOWS:
        try: return windows_token(pid)
        except (OSError,ValueError,OverflowError): return None
    return posix_token(pid,runner)
