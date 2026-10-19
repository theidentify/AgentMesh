"""S3-compatible plain PUT storage. Unique keys, not the backend, ensure immutability."""
import re
from . import validate_key, validate_data, InvalidKey, StorageError, ObjectNotFound


def _failed(action, exc):
    # Only a well-formed S3 error code or the exception class name; never messages, keys or credentials.
    code = getattr(exc, 'response', {}).get('Error', {}).get('Code') if isinstance(getattr(exc, 'response', None), dict) else None
    reason = code if type(code) is str and re.fullmatch('[A-Za-z0-9.]{1,64}', code) else type(exc).__name__
    return StorageError(f'S3 {action} failed ({reason})')


class S3Storage:
    def __init__(self, bucket, *, prefix='', endpoint_url=None, region_name='auto', client=None, client_factory=None):
        validate_key(prefix, prefix=True)
        self.bucket = bucket
        self.prefix = prefix.rstrip('/') + '/' if prefix else ''
        if client is None:
            if client_factory is None:
                try: import boto3
                except ImportError: raise StorageError('S3 backend requires optional boto3 dependency') from None
                client_factory = boto3.client
            try: client = client_factory('s3', endpoint_url=endpoint_url, region_name=region_name)
            except Exception: raise StorageError('S3 client initialization failed') from None
        self.client = client

    def put(self, key, data):
        validate_key(key); validate_data(data)
        try: self.client.put_object(Bucket=self.bucket, Key=self.prefix + key, Body=data)
        except Exception as exc: raise _failed('put', exc) from None

    def get(self, key):
        validate_key(key)
        try:
            body = self.client.get_object(Bucket=self.bucket, Key=self.prefix + key)['Body']
            try: return body.read()
            finally: body.close()
        except Exception as exc:
            code = getattr(exc, 'response', {}).get('Error', {}).get('Code')
            if code in ('NoSuchKey', '404', 'NotFound'): raise ObjectNotFound('object not found') from None
            raise _failed('get', exc) from None

    def list(self, prefix, *, page_size=None):
        validate_key(prefix, prefix=True)
        keys = []
        try:
            paginator = self.client.get_paginator('list_objects_v2')
            options = {} if page_size is None else {'PaginationConfig': {'PageSize': page_size}}
            for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix + prefix, **options):
                for item in page.get('Contents', []):
                    full = item['Key']
                    if not full.startswith(self.prefix + prefix): continue
                    key = full[len(self.prefix):]
                    try: validate_key(key)
                    except InvalidKey: continue
                    keys.append(key)
        except StorageError: raise
        except Exception as exc: raise _failed('list', exc) from None
        return iter(sorted(keys))
