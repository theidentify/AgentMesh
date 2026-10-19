"""Minimal object storage contract and shared path validation."""
import re
from typing import Iterator, Protocol
from ..errors import LogError


class StorageError(LogError):
    pass

class InvalidKey(StorageError):
    pass

class ObjectNotFound(StorageError):
    pass

class ObjectExists(StorageError):
    pass


class Storage(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def list(self, prefix: str) -> Iterator[str]: ...


def validate_key(key, *, prefix=False):
    if type(key) is not str or len(key) > 1024: raise InvalidKey('invalid object key')
    if prefix and key == '': return key
    value = key[:-1] if prefix and key.endswith('/') else key
    if not value or any(not re.fullmatch('[A-Za-z0-9_-][A-Za-z0-9._-]*', part) for part in value.split('/')):
        raise InvalidKey('invalid object key')
    return key


def validate_data(data):
    if type(data) is not bytes: raise StorageError('object data must be bytes')
