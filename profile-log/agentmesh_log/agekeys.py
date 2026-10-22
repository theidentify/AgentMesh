"""Real age X25519 identities; BIP-173 encoding for deterministic recovery keys."""
import pyrage
from .errors import CryptoError

_ALPHABET = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l'


def _polymod(values):
    chk = 1
    for value in values:
        top = chk >> 25
        chk = ((chk & 0x1ffffff) << 5) ^ value
        for i, generator in enumerate((0x3b6a57b2,0x26508e6d,0x1ea119fa,0x3d4233dd,0x2a1462b3)):
            if (top >> i) & 1: chk ^= generator
    return chk


def _expand(hrp): return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def encode_secret(raw):
    if type(raw) is not bytes or len(raw) != 32: raise CryptoError('invalid age identity')
    hrp = 'age-secret-key-'; bits = int.from_bytes(raw,'big') << 4
    data = [(bits >> shift) & 31 for shift in range(255,-1,-5)]
    check = _polymod(_expand(hrp) + data + [0]*6) ^ 1
    return (hrp + '1' + ''.join(_ALPHABET[v] for v in data + [(check >> (5*i)) & 31 for i in range(5,-1,-1)])).upper()


def decode_secret(value):
    try:
        if type(value) is not str or value != value.upper(): raise ValueError()
        hrp, data = value.lower().rsplit('1',1)
        digits = [_ALPHABET.index(c) for c in data]
        if hrp != 'age-secret-key-' or len(digits) != 58 or _polymod(_expand(hrp)+digits) != 1: raise ValueError()
        bits = 0
        for d in digits[:-6]: bits = (bits << 5) | d
        if bits & 15: raise ValueError()
        return (bits >> 4).to_bytes(32,'big')
    except Exception: raise CryptoError('invalid age identity') from None


class DeviceAgeIdentity:
    def __init__(self): self._identity = pyrage.x25519.Identity.generate()

    @classmethod
    def parse(cls, value):
        decode_secret(value)
        try:
            obj = cls.__new__(cls); obj._identity = pyrage.x25519.Identity.from_str(value); return obj
        except Exception: raise CryptoError('invalid age identity') from None

    @classmethod
    def from_raw(cls, raw): return cls.parse(encode_secret(raw))

    def secret(self): return str(self._identity)
    def raw(self): return decode_secret(self.secret())
    @property
    def recipient(self): return str(self._identity.to_public())
    def decrypt(self, data):
        try: return pyrage.decrypt(data,[self._identity])
        except Exception: raise CryptoError('age decryption failed') from None


def validate_recipient(value):
    try:
        recipient = pyrage.x25519.Recipient.from_str(value)
        if str(recipient) != value: raise ValueError()
        return recipient
    except Exception: raise CryptoError('invalid age recipient') from None


def encrypt(data, recipient):
    try: return pyrage.encrypt(data,[validate_recipient(recipient)])
    except Exception: raise CryptoError('age encryption failed') from None


def encrypt_many(data, recipients):
    try:
        targets=[validate_recipient(r) for r in recipients]
        if not targets: raise ValueError()
        return pyrage.encrypt(data,targets)
    except Exception: raise CryptoError('age encryption failed') from None
