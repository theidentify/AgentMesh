"""Bounded typed binary encoding and strict JSON wire transport."""
import json
import math
import struct
from .errors import ValidationError

MAX_BYTES = 1024 * 1024
MAX_DEPTH = 64
MAX_VALUES = 100000


def typed(value):
    """Injective typed binary encoding, NOT RFC 8785 / JCS. See FORMAT.md."""
    count = 0
    def encode(v, depth):
        nonlocal count
        count += 1
        if count > MAX_VALUES or depth > MAX_DEPTH:
            raise ValidationError('packet structure limit exceeded')
        if v is None: return b'n'
        if type(v) is bool: return b't' if v else b'f'
        if type(v) is int:
            if not -(2**63) <= v < 2**63: raise ValidationError('integer outside signed 64-bit range')
            return b'i' + str(v).encode('ascii') + b';'
        if type(v) is float:
            if not math.isfinite(v): raise ValidationError('non-finite number')
            return b'd' + struct.pack('>d', v)
        if type(v) is str:
            try: raw = v.encode('utf-8', errors='strict')
            except UnicodeError as exc: raise ValidationError('invalid Unicode scalar') from None
            return b's' + str(len(raw)).encode('ascii') + b':' + raw
        if type(v) is list:
            return b'l' + str(len(v)).encode('ascii') + b':' + b''.join(encode(x, depth + 1) for x in v)
        if type(v) is dict and all(type(k) is str for k in v):
            keys = sorted(v, key=lambda k: k.encode('utf-8', errors='strict'))
            return b'o' + str(len(keys)).encode('ascii') + b':' + b''.join(encode(k, depth + 1) + encode(v[k], depth + 1) for k in keys)
        raise ValidationError('unsupported JSON value')
    try: result = encode(value, 0)
    except (UnicodeError, RecursionError) as exc: raise ValidationError('invalid or excessive structure') from None
    if len(result) > MAX_BYTES: raise ValidationError('encoded packet too large')
    return result


def wire(value):
    typed(value)
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(data) > MAX_BYTES: raise ValidationError('packet too large')
    return data


def parse(data):
    if type(data) is not bytes: raise ValidationError('bytes required')
    if len(data) > MAX_BYTES: raise ValidationError('packet too large')
    def pairs(items):
        obj = {}
        for k, v in items:
            if k in obj: raise ValidationError('duplicate JSON key')
            obj[k] = v
        return obj
    def invalid(_): raise ValidationError('non-finite number')
    try:
        if isinstance(data, bytes): data = data.decode('utf-8', errors='strict')
        value = json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)
        typed(value)
        return value
    except (UnicodeError, RecursionError, ValueError) as exc:
        raise ValidationError('invalid encoding or excessive structure') from None


def hex_bytes(value, length):
    import re
    if type(value) is not str or not re.fullmatch('[0-9a-f]{%d}' % (length * 2), value):
        raise ValidationError('invalid hex field')
    return bytes.fromhex(value)


def fields(record, required, format):
    if type(record) is not dict or set(record) != set(required) or record['format'] != format:
        raise ValidationError('invalid record shape or format')


def milliseconds(value):
    if type(value) is not int or not 0 <= value < 2**63:
        raise ValidationError('invalid timestamp')
    return value
