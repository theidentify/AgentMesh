"""S3-compatible plain PUT storage. Unique keys, not the backend, ensure immutability."""
import logging
from . import validate_key, validate_data, InvalidKey, StorageError, ObjectNotFound


class S3Storage:
    backend = 's3'
    def __init__(self, bucket, *, prefix='', endpoint_url=None, region_name='auto', client=None, client_factory=None):
        validate_key(prefix, prefix=True)
        self.bucket = bucket
        self.prefix = prefix.rstrip('/') + '/' if prefix else ''
        # SDK debug records include Authorization headers and signed URLs.
        for name in ('boto3','botocore','s3transfer','urllib3'):
            logger=logging.getLogger(name); logger.handlers=[logging.NullHandler()]; logger.propagate=False
            for child in list(logging.Logger.manager.loggerDict):
                if child.startswith(name+'.'): logging.getLogger(child).disabled=True
        if client is None:
            if client_factory is None:
                try: import boto3
                except ImportError: pass
                else: client_factory = boto3.client
                if client_factory is None: raise StorageError('S3 backend requires optional boto3 dependency') from None
            try: client = client_factory('s3', endpoint_url=endpoint_url, region_name=region_name)
            except Exception: pass
            if client is None: raise StorageError('S3 client initialization failed') from None
        self.client = client

    def put(self, key, data):
        validate_key(key); validate_data(data)
        try: self.client.put_object(Bucket=self.bucket, Key=self.prefix + key, Body=data); return
        except Exception: pass
        raise StorageError('S3 put failed') from None

    def get(self, key):
        validate_key(key)
        try:
            body = self.client.get_object(Bucket=self.bucket, Key=self.prefix + key)['Body']
            try: return body.read()
            finally: body.close()
        except Exception as exc:
            response=getattr(exc,'response',None)
            error=response.get('Error') if type(response) is dict else None
            code=error.get('Code') if type(error) is dict else None
            missing=type(code) is str and code in ('NoSuchKey','404','NotFound')
        if missing: raise ObjectNotFound('object not found') from None
        raise StorageError('S3 get failed') from None

    def list(self, prefix, *, page_size=None, start_after=None):
        validate_key(prefix, prefix=True)
        if start_after is not None: validate_key(start_after)
        keys = []
        try:
            paginator = self.client.get_paginator('list_objects_v2')
            options = {} if page_size is None else {'PaginationConfig': {'PageSize': page_size}}
            if start_after is not None: options['StartAfter'] = self.prefix + start_after
            for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix + prefix, **options):
                for item in page.get('Contents', []):
                    full = item['Key']
                    if not full.startswith(self.prefix + prefix): continue
                    key = full[len(self.prefix):]
                    try: validate_key(key)
                    except InvalidKey: continue
                    if start_after is None or key > start_after: keys.append(key)
        except Exception: pass
        else: return iter(sorted(keys))
        raise StorageError('S3 list failed') from None
