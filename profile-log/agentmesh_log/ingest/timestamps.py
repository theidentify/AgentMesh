"""ISO timestamps with microsecond precision on Python 3.10 and newer."""
from datetime import datetime, timezone
import re


def parse_utc(value, *, naive_as_utc=False):
    if not isinstance(value, str):
        raise TypeError('timestamp must be a string')
    if len(value[:-1] if value.endswith('Z') else value) <= 10:
        raise ValueError('timestamp requires a time')
    if value.endswith('Z'):
        value = value[:-1] + '+00:00'
    value = re.sub(r'(?<=\d{2}:\d{2}:\d{2})\.([0-9]+)',
                   lambda match: '.' + match[1][:6].ljust(6, '0'), value)
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        if not naive_as_utc:
            raise ValueError('timestamp requires an offset')
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)
