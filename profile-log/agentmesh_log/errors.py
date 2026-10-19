"""Public failures contain fixed messages, never input or secret material."""

class LogError(ValueError):
    pass

class ValidationError(LogError):
    pass

class IdentityError(LogError):
    pass

class CryptoError(LogError):
    pass

class KeyAccessError(LogError):
    pass

class LabelLoweringRequiresConfirmation(LogError):
    pass
