"""S3-compatible plain PUT storage. Unique keys, not the backend, ensure immutability."""
import logging
import threading
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
        self._lock=threading.Lock()
        self.io = dict(bytes_up=0,bytes_down=0,class_a=0,class_b=0)
        self._active=threading.local()
        events=getattr(getattr(client,'meta',None),'events',None)
        self._hooks=events is not None
        if events is not None:
            for operation in ('PutObject','ListObjectsV2','GetObject','HeadObject'):
                def sent(request,operation=operation,**kwargs):
                    if not getattr(self._active,'value',False): return
                    with self._lock:
                        self.io['class_a' if operation in ('PutObject','ListObjectsV2') else 'class_b']+=1
                        if operation=='PutObject': self.io['bytes_up']+=getattr(self._active,'size',0)
                events.register('before-send.s3.'+operation,sent)

    def put(self, key, data):
        validate_key(key); validate_data(data)
        self._active.value=True; self._active.size=len(data)
        if not self._hooks:
            with self._lock: self.io['class_a']+=1
        try:
            self.client.put_object(Bucket=self.bucket, Key=self.prefix + key, Body=data)
            with self._lock: self.io['bytes_up']+=0 if self._hooks else len(data)
            return
        except Exception: pass
        finally: self._active.value=False
        raise StorageError('S3 put failed') from None

    def get(self, key):
        validate_key(key)
        self._active.value=True
        if not self._hooks:
            with self._lock: self.io['class_b']+=1
        try:
            body = self.client.get_object(Bucket=self.bucket, Key=self.prefix + key)['Body']
            try:
                data=body.read()
                with self._lock: self.io['bytes_down']+=len(data)
                return data
            finally: body.close()
        except Exception as exc:
            response=getattr(exc,'response',None)
            error=response.get('Error') if type(response) is dict else None
            code=error.get('Code') if type(error) is dict else None
            missing=type(code) is str and code in ('NoSuchKey','404','NotFound')
        finally: self._active.value=False
        if missing: raise ObjectNotFound('object not found') from None
        raise StorageError('S3 get failed') from None

    def list(self, prefix, *, page_size=None, start_after=None):
        validate_key(prefix, prefix=True)
        if start_after is not None: validate_key(start_after)
        keys = []; self._active.value=True
        try:
            paginator = self.client.get_paginator('list_objects_v2')
            options = {} if page_size is None else {'PaginationConfig': {'PageSize': page_size}}
            if start_after is not None: options['StartAfter'] = self.prefix + start_after
            pages=iter(paginator.paginate(Bucket=self.bucket, Prefix=self.prefix + prefix, **options))
            while True:
                if not self._hooks:
                    with self._lock: self.io['class_a']+=1
                try: page=next(pages)
                except StopIteration:
                    if not self._hooks:
                        with self._lock: self.io['class_a']-=1
                    break
                for item in page.get('Contents', []):
                    full = item['Key']
                    if not full.startswith(self.prefix + prefix): continue
                    key = full[len(self.prefix):]
                    try: validate_key(key)
                    except InvalidKey: continue
                    if start_after is None or key > start_after: keys.append(key)
        except Exception: pass
        else: return iter(sorted(keys))
        finally: self._active.value=False
        raise StorageError('S3 list failed') from None

    def head(self, key):
        validate_key(key); self._active.value=True
        if not self._hooks:
            with self._lock: self.io['class_b']+=1
        try: return self.client.head_object(Bucket=self.bucket,Key=self.prefix+key)
        except Exception: pass
        finally: self._active.value=False
        raise StorageError('S3 head failed') from None
