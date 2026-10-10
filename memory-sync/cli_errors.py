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
from worker_error_details import DETAILS, SYSTEM_ERRORS
SAFE_REASONS = SAFE_REASONS | DETAILS.keys()
TRUSTED_MODULES = frozenset({'install_adopt', 'worker_lifecycle', 'signed_packets',
                           'windows_acl', 'install_setup', 'runtime_lock', 'worker_diagnostics'})


def report(exc, action):
    result: dict[str, object] = {'error': type(exc).__name__}
    if action not in ('worker-run', 'worker-start', 'worker-status', 'worker-stop', 'diagnose'):
        return result
    message = str(exc)
    known = message in SAFE_REASONS
    result['reason'] = message if known else 'details withheld; inspect diagnostic stage'
    code, hint = DETAILS.get(message, (
        'GUARD_REFUSED' if known else 'UNCLASSIFIED_ERROR',
        'Review this guard and diagnostic target; do not bypass it.' if known else
        'Run diagnose with the same executable/runtime; share code and stage. Private input values are withheld.'))
    if not known and result['error'] in SYSTEM_ERRORS:
        code, hint = SYSTEM_ERRORS[type(exc).__name__]
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
