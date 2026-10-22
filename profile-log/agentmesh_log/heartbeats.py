"""Sealed device telemetry; best-effort and never an authority source."""
from datetime import datetime, timezone
import socket
import sys
from . import settings
from .agekeys import DeviceAgeIdentity, encrypt_many
from .broker.handshake import b64
from .encoding import wire, parse, hex_bytes, milliseconds
from .identity import verify_signature
from .keys import decode_ciphertext
from .registry_store import shape, signed, domain, now

PREFIX='registry/heartbeats/'
PLATFORMS=('macos','windows','linux','ios','android','other')
OUTCOMES=('ok','credentials_missing','credentials_refused','remote_error','error')
FIELDS={'device_id','name','platform','app_version','last_sync_at','last_sync_outcome'}


def name_valid(value): return type(value) is str and 1<=len(value)<=64 and value.isprintable()


def iso(value):
    return datetime.fromtimestamp(value/1000,timezone.utc).isoformat() if value is not None else None


def iso_valid(value):
    try:
        return type(value) is str and len(value)<=64 and 'T' in value and datetime.fromisoformat(value.replace('Z','+00:00')).tzinfo is not None
    except (ValueError,OverflowError): return False


def validate(value):
    if type(value) is not dict or set(value)!=FIELDS: raise ValueError('invalid heartbeat')
    hex_bytes(value['device_id'],16)
    if not name_valid(value['name']) or value['platform'] not in PLATFORMS: raise ValueError('invalid heartbeat')
    version=value['app_version']
    if version is not None and not name_valid(version): raise ValueError('invalid heartbeat')
    if value['last_sync_at'] is not None:
        milliseconds(value['last_sync_at']); iso(value['last_sync_at'])
    if value['last_sync_outcome'] is not None and value['last_sync_outcome'] not in OUTCOMES: raise ValueError('invalid heartbeat')
    return value


def local(device, row=None):
    name=socket.gethostname().removesuffix('.local')[:64]
    if not name_valid(name): name='device'
    try: name=settings.load().get('device',{}).get('name',name)
    except Exception: pass
    platform={'darwin':'macos','win32':'windows','linux':'linux','ios':'ios','android':'android'}.get(sys.platform,'other')
    row=row or {}; at=row.get('at')
    return validate(dict(device_id=device,name=name,platform=platform,app_version=None,
        last_sync_at=int(datetime.fromisoformat(at.replace('Z','+00:00')).timestamp()*1000) if at else None,
        last_sync_outcome=row.get('outcome')))


def recipients(registry):
    return tuple(sorted((d,r['age_recipient']) for d,r in registry.device_records.items() if d not in registry.revocations))


def path(record): return PREFIX+record['device_id']+'.json'


def make(device, registry, value, *, at=None):
    at=now() if at is None else at
    validate(value)
    if value['device_id']!=device.device_id: raise ValueError('invalid heartbeat binding')
    registry.device_public(device.device_id)
    targets=recipients(registry)
    if device.device_id not in dict(targets): raise ValueError('missing issuer recipient')
    return signed('device-heartbeat',device._private,device_id=device.device_id,issued_at=at,
        age=b64(encrypt_many(wire(value),[r for _,r in targets])))


def open_record(key, record, registry, identity, *, at=None):
    at=now() if at is None else at; shape(record,'device-heartbeat')
    if key!=path(record) or record['device_id'] in registry.revocations or record['issued_at']>at+600000:
        raise ValueError('invalid heartbeat binding')
    verify_signature(record,registry.device_public(record['device_id'],at=record['issued_at']),domain('device-heartbeat'))
    value=validate(parse(identity.decrypt(decode_ciphertext(record['age']))))
    if value['device_id']!=record['device_id']: raise ValueError('invalid heartbeat binding')
    iso(record['issued_at'])
    return dict(value,issued_at=record['issued_at'])


def read(storage, registry, identity, *, at=None):
    rows={}
    for key in storage.list(PREFIX):
        try:
            value=open_record(key,parse(storage.get(key)),registry,identity,at=at)
            rows[value['device_id']]=value
        except Exception: continue
    return rows


class Heartbeats:
    def __init__(self):
        self.rows={}; self.written=None; self.read_at=None; self.change=None

    def update(self, storage, state, row, *, tick, at=None):
        at=now() if at is None else at
        registry=state.registry(); change=(row['outcome'],recipients(registry))
        if self.written is None or tick-self.written>=900 or change!=self.change:
            # Throttle failed attempts too; heartbeat failures must never change sync.
            self.written=tick; self.change=change
            try:
                record=make(state.device,registry,local(state.device.device_id,row),at=at)
                storage.put(path(record),wire(record))
            except Exception as exc: self.error(exc)
        if self.read_at is None or tick-self.read_at>=900:
            self.read_at=tick
            try: self.rows=read(storage,registry,DeviceAgeIdentity.parse(state.control['age_identity']),at=at)
            except Exception as exc: self.error(exc)
        self.rows={d:r for d,r in self.rows.items() if d in registry.device_records and d not in registry.revocations}

    @staticmethod
    def error(exc):
        try: print('profile-log heartbeat: '+type(exc).__name__,file=sys.stderr)
        except Exception: pass
