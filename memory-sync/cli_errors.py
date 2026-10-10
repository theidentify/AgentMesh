"""Public diagnostics: fixed reasons and trusted code locations, never input values."""

# Exact constants emitted by installation/lifecycle/private-storage guards.
# Do not expose arbitrary exception text, paths, keys or subprocess output.
SAFE_REASONS = frozenset({
    'absolute paths without traversal required',
    'partial first-run installation requires manual review',
    'existing app root and runtime parent required',
    'user-owned state, not writable by others, required',
    'local state must remain separate from exchange',
    'existing database and accepted exchange required',
    'existing workflow configuration required',
    'invalid database scope', 'database scope mismatch',
    'database policy changed; explicit review required',
    'strict identity binding mismatch', 'persistent signing key changed',
    'invalid runtime', 'finite positive interval/timeout required',
    'corrupt or foreign worker metadata; manual review required',
    'corrupt worker nonce', 'corrupt stop request',
    'legacy worker must be drained explicitly',
    'legacy worker must be drained explicitly; acknowledge unsigned policy',
    'invalid worker nonce', 'startup deadline expired',
    'database must remain outside exchange',
    'stale worker metadata; no process was signalled',
    'worker ownership changed while stopping',
    'worker did not acknowledge stop; no process was signalled',
    'invalid package metadata', 'package manifest does not match executable',
    'runtime configuration changed; restart after review',
    'database scope or signing key changed',
    'worker already running; stop it first',
    'worker failed before a healthy cycle',
    'worker startup did not finish; nonce stop requested',
    'symlink path not permitted', 'reparse path not permitted',
    'regular file required', 'file too large', 'duplicate JSON key',
    'local security files must be owner-only (chmod 600)',
    'security directory required',
    'security directory must be owner-only (chmod 700)',
    'unverifiable Windows ACL', 'unsupported Windows user SID',
    'Windows private owner/type mismatch',
    'Windows private ACL must be protected',
    'Windows private ACL has no explicit grants',
    'unsupported Windows ACE',
    'unapproved Windows ACL principal or ACE type',
    'unsupported or inherited Windows ACE',
    'unsupported Windows ACL rights',
    'Windows private ACL requires user full control',
    'native Windows ACL adapter requires Windows',
    'Windows PowerShell unavailable',
    'Windows private ACL could not be verified',
})
WINDOWS_REASONS = frozenset({
    'OS PowerShell unavailable',
    'Scheduled Task binding is not owned',
    'Scheduled Task changed during confirmation',
    'Scheduled Task could not be verified',
    'Scheduled Task readback mismatch',
    'Scheduled Task removal readback mismatch',
    'Windows installation requires native Windows',
    'Windows runtime node required',
    'binding inputs changed during confirmation',
    'binding inputs changed while draining',
    'existing installed-state required',
    'existing runtime required',
    'destructive action requires matching --confirm word',
    'interactive confirmation unavailable; pass --yes',
    'existing program-root parent required',
    'explicit prior legacy-worker drain approval required',
    'explicit prior unmanaged-worker drain approval required',
    'explicit program root required',
    'foreign Scheduled Task refused',
    'foreign or changed Scheduled Task refused',
    'installation ownership or recovery status uncertain',
    'installed BUILD.json changed',
    'installed bundle verification failed',
    'installed program changed while draining',
    'installed program directory changed',
    'installed program file changed',
    'installed program readback mismatch',
    'installed runtime or database scope changed',
    'installed-state changed during confirmation',
    'installed-state must be the owned root installed.json',
    'installed-state readback failed',
    'invalid BUILD.json checksum',
    'invalid development BUILD.json schema',
    'invalid owned program manifest',
    'invalid owned task name',
    'invalid owned version inventory',
    'live replacement is upgrade-only',
    'no previous owned program available',
    'operator confirmation required',
    'paths, program, configuration or scope changed during confirmation',
    'program checksum mismatch',
    'program root exists; use upgrade only for owned intact storage',
    'program root overlaps existing state or exchange',
    'regular program file required',
    'replacement healthy cycle not verified',
    'replacement recurring cycle not verified; recovery retained',
    'replacement recurring worker health failed',
    'runtime/database preservation failed',
    'same revision has different program bytes',
    'select exactly one of enable or disable',
    'source bundle must be local and outside target/exchange',
    'task SID changed',
    'task binding readback failed',
    'task name changed',
    'task removal not verified',
    'uninstall file removal failed',
    'uninstall ownership changed',
    'uninstall program changed',
    'unowned program-root entries require review',
    'unowned version directory refused',
    'unsupported user SID',
    'worker changed while draining',
    'worker must be proven stopped; stale or running refused',
})
from worker_error_details import DETAILS, SYSTEM_ERRORS
SAFE_REASONS = SAFE_REASONS | DETAILS.keys()
TRUSTED_MODULES = frozenset({'install_adopt', 'worker_lifecycle', 'signed_packets',
                           'windows_acl', 'install_setup', 'runtime_lock', 'worker_diagnostics',
                           'windows_install', 'windows_task'})


def report(exc, action):
    result: dict[str, object] = {'error': type(exc).__name__}
    is_windows = action in ('windows-install', 'windows-upgrade', 'windows-rollback', 'windows-uninstall', 'windows-autostart')
    if not is_windows and action not in ('worker-run', 'worker-start', 'worker-status', 'worker-stop', 'diagnose'):
        return result
    message = str(exc)
    known = message in SAFE_REASONS or (is_windows and message in WINDOWS_REASONS)
    result['reason'] = message if known else 'details withheld; inspect diagnostic stage'
    code, hint = DETAILS.get(message, (
        'GUARD_REFUSED' if known else 'UNCLASSIFIED_ERROR',
        'Review this guard and diagnostic target; do not bypass it.' if known else
        'Run diagnose with the same executable/runtime; share code and stage. Private input values are withheld.'))
    if not known and result['error'] in SYSTEM_ERRORS:
        code, hint = SYSTEM_ERRORS[type(exc).__name__]
    if is_windows:
        hint = 'Retain installed-state and program files for operator review; do not retry blindly or restore the database.'
    result.update(code=code, next_action=hint)
    for key in ('errno', 'winerror', 'sqlite_errorcode'):
        value = getattr(exc, key, None)
        if type(value) is int:
            result[key] = value
    result['stage'] = action
    frame = exc.__traceback__
    while frame is not None:
        module = frame.tb_frame.f_globals.get('__name__', '')
        if module in TRUSTED_MODULES:
            result['stage'] = module + '.' + frame.tb_frame.f_code.co_name
        frame = frame.tb_next
    return result
