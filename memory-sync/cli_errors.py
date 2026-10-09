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
TRUSTED_MODULES = frozenset({'install_adopt', 'worker_lifecycle', 'signed_packets',
                           'windows_acl', 'install_setup', 'runtime_lock'})


def report(exc, action):
    result = {'error': type(exc).__name__}
    if action not in ('worker-run', 'worker-start', 'worker-status', 'worker-stop'):
        return result
    message = str(exc)
    result['reason'] = message if message in SAFE_REASONS else 'details withheld; inspect diagnostic stage'
    result['stage'] = action
    frame = exc.__traceback__
    while frame is not None:
        module = frame.tb_frame.f_globals.get('__name__', '')
        if module in TRUSTED_MODULES:
            result['stage'] = module + '.' + frame.tb_frame.f_code.co_name
        frame = frame.tb_next
    return result
