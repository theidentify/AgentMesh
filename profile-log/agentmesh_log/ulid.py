"""Canonical ULIDs, monotonic across threads and clock rollback in this process."""
import secrets
import threading
import time
from .errors import ValidationError

ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'
_lock = threading.Lock()
_last = -1


def parse(value):
    if type(value) is not str or len(value) != 26 or any(c not in ALPHABET for c in value):
        raise ValidationError('invalid ULID')
    number = 0
    for char in value:
        number = number * 32 + ALPHABET.index(char)
    if number >= 2**128: raise ValidationError('ULID overflow')
    return number


def timestamp(value):
    return parse(value) >> 80


def generate():
    global _last
    with _lock:
        ms = time.time_ns() // 1_000_000
        if not 0 <= ms < 2**48: raise ValidationError('ULID time overflow')
        number = max((ms << 80) | secrets.randbits(80), _last + 1) if ms > (_last >> 80) else _last + 1
        if number >= 2**128: raise ValidationError('ULID overflow')
        _last = number
    return ''.join(ALPHABET[(number >> shift) & 31] for shift in range(125, -1, -5))
