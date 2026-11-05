"""Strict signed Phase 4 records and account-pinned registry reconstruction."""
import hashlib
import time
from .encoding import fields, hex_bytes, milliseconds, wire, parse
from .identity import Registry, sign, verify_signature
from .agekeys import validate_recipient
from .errors import IdentityError

SCHEMAS = {
 'device-heartbeat': {'device_id','issued_at','age'},
 'scope-public': {'profile','scope','key_id','public_key','issuer_device','issued_at'},
 'device-class': {'device_id','device_class','issued_at','issuer_device'},
 'profile-recipient': {'device_id','profile_id','age_recipient','issued_at'},
 'profile-recovery': {'profile_id','recovery_key_id','age_recipient','issued_at','age'},
 'device-record': {'certificate','age_recipient','issued_at'},
 'device-revocation': {'device_id','revoked_by','revoked_at','reason_code'},
 'recovery-record': {'account_id','recovery_signing_public','recovery_age_recipient','previous','issued_at'},
 'keyring-grant': {'profile','scope','key_id','recipient_device','issuer_device','issued_at','age'},
 'epoch': {'profile','scope','key_id','previous_key_id','created_at','reason','issuer_device'},
 'profile-state': {'profile','id','state','issuer_device'},
 'recovery-alert': {'account_id','id','recovery_key_id','device_id','issued_at'},
 'recovery-history': {'id','profile','recovery_key_id','issuer_device','issued_at','age'},
 'scope-meta': {'profile','recipient_device','issuer_device','issued_at','age'},
}


def now(): return time.time_ns() // 1_000_000

# Device classes (team workspace W2). A device without a record is a workstation.
DEVICE_CLASSES = ('workstation', 'server', 'write-only')


def device_class_record(subject, signer, device_class, *, issued_at=None):
    """``signer`` (a DeviceIdentity) states the class of ``subject`` (a device ID of the same account)."""
    if device_class not in DEVICE_CLASSES: raise ValueError('invalid device class')
    return signed('device-class',signer._private,device_id=subject,device_class=device_class,
                  issued_at=now() if issued_at is None else issued_at,issuer_device=signer.device_id)


def device_class_path(record):
    return 'registry/device-classes/'+record['device_id']+'/'+hashlib.sha256(wire(record)).hexdigest()+'.json'


def device_class(registry, device_id):
    return getattr(registry,'device_classes',{}).get(device_id,'workstation')

def domain(kind): return ('AgentMesh/'+kind+'/Ed25519/v1\x00').encode('ascii')

def signed(kind, private, **values):
    return sign({'format':'agentmesh-'+kind+'-v1',**values},private,domain(kind))

def shape(record, kind):
    fields(record, SCHEMAS[kind] | {'format','signature'}, 'agentmesh-'+kind+'-v1')
    hex_bytes(record['signature'],64)
    for key in ('issued_at','revoked_at','created_at'):
        if key in record: milliseconds(record[key])
    for key in ('profile','profile_id','scope','account_id','device_id'):
        if key in record: hex_bytes(record[key],16)
    for key in ('issuer_device','recipient_device','revoked_by'):
        if key in record and record[key] != 'recovery': hex_bytes(record[key],16)
    for key in ('key_id','recovery_key_id','previous_key_id','previous'):
        if key in record and record[key] is not None: hex_bytes(record[key],32)
    return record


def device_record(device, certificate, age, *, issued_at=None):
    return signed('device-record',device._private,certificate=certificate,age_recipient=age.recipient,
                  issued_at=now() if issued_at is None else issued_at)


def revocation(subject, signer, *, revoked_at=None, reason_code='removed'):
    return signed('device-revocation',signer._private,device_id=subject,revoked_by=signer.device_id,
                  revoked_at=now() if revoked_at is None else revoked_at,reason_code=reason_code)


def recovery_id(record):
    return hashlib.sha256(b'AgentMesh/recovery-key-id/v1\x00' + hex_bytes(record['recovery_signing_public'],32)).hexdigest()


class RegistryStore:
    def __init__(self, storage, account): self.storage,self.account = storage,account

    def load(self):
        registry = Registry(self.account)
        recovery = []
        for path in self.storage.list('registry/recovery/'):
            try:
                record = shape(parse(self.storage.get(path)),'recovery-record')
                if path != 'registry/recovery/'+recovery_id(record)+'.json': continue
                recovery.append(record)
            except ValueError: continue
        pending = recovery[:]
        while pending:
            progress = False
            for record in pending[:]:
                try: registry.add_recovery(record)
                except ValueError: continue
                pending.remove(record); progress = True
            if not progress: break
        pending = []
        for path in self.storage.list('registry/devices/'):
            try:
                record = shape(parse(self.storage.get(path)),'device-record')
                if path != 'registry/devices/'+record['certificate']['device_id']+'.json': continue
                pending.append(record)
            except (ValueError,KeyError): continue
        while pending:
            progress = False
            for record in pending[:]:
                try: self._device(registry,record)
                except ValueError: continue
                pending.remove(record); progress = True
            if not progress: break
        revocations = []
        for path in self.storage.list('registry/revocations/'):
            try:
                record = shape(parse(self.storage.get(path)),'device-revocation')
                if path != 'registry/revocations/'+record['device_id']+'.json': continue
                revocations.append(record)
            except ValueError: continue
        for record in sorted(revocations,key=lambda r:(r['revoked_at'],r['device_id'])):
            try: registry.add_revocation(record)
            except ValueError: continue
        self._classes(registry)
        return registry

    def _classes(self, registry):
        """The newest valid class per device. A record counts when it is signed by the device that enrolled the
        subject (its certificate issuer), or by an account- or recovery-certified device about itself, with the
        signer's key valid at the time. Servers cannot enroll devices, so they never classify others."""
        found = {}
        for path in self.storage.list('registry/device-classes/'):
            try:
                record = shape(parse(self.storage.get(path)),'device-class')
                if path != device_class_path(record) or record['device_class'] not in DEVICE_CLASSES: continue
                subject, issuer = record['device_id'], record['issuer_device']
                cert = registry.certificates.get(subject)
                if cert is None or issuer not in registry.certificates: continue
                if issuer == subject:
                    if cert['issuer'] not in ('account','recovery'): continue
                elif issuer != cert['issuer']: continue
                verify_signature(record,registry.device_public(issuer,at=record['issued_at']),domain('device-class'))
            except (ValueError,KeyError): continue
            old = found.get(subject)
            if old is None or (record['issued_at'],path) > (old[0]['issued_at'],old[1]): found[subject] = (record,path)
        registry.device_classes = {d: r['device_class'] for d,(r,_) in found.items()}

    def publish_device_class(self, record):
        shape(record,'device-class')
        if record['device_class'] not in DEVICE_CLASSES: raise ValueError('invalid device class')
        self.storage.put(device_class_path(record),wire(record))

    @staticmethod
    def _device(registry, record):
        shape(record,'device-record'); validate_recipient(record['age_recipient'])
        cert = record['certificate']
        verify_signature(record,hex_bytes(cert['device_public_key'],32),domain('device-record'))
        registry.add_device(cert); registry.device_records[cert['device_id']] = record

    def publish_device(self, record):
        registry = self.load(); self._device(registry,record)
        self.storage.put('registry/devices/'+record['certificate']['device_id']+'.json',wire(record))

    def publish_profile_recipient(self, record):
        shape(record,'profile-recipient'); validate_recipient(record['age_recipient'])
        verify_signature(record,self.load().device_public(record['device_id']),domain('profile-recipient'))
        self.storage.put(profile_recipient_path(record),wire(record))

    def publish_revocation(self, record):
        self.load().add_revocation(record)
        self.storage.put('registry/revocations/'+record['device_id']+'.json',wire(record))

    def publish_recovery(self, record):
        self.load().add_recovery(record)
        self.storage.put('registry/recovery/'+recovery_id(record)+'.json',wire(record))


def profile_recipient(device,profile_id,age,*,issued_at=None):
    return signed('profile-recipient',device._private,device_id=device.device_id,profile_id=profile_id,
        age_recipient=age.recipient,issued_at=now() if issued_at is None else issued_at)


def profile_recipient_path(record):
    shape(record,'profile-recipient')
    return f"registry/profile-recipients/{record['profile_id']}/{record['device_id']}.json"


def read_profile_recipient(storage,registry,profile,device):
    path=f'registry/profile-recipients/{profile}/{device}.json'
    record=shape(parse(storage.get(path)),'profile-recipient')
    if profile_recipient_path(record)!=path: raise IdentityError('invalid profile recipient binding')
    validate_recipient(record['age_recipient'])
    verify_signature(record,registry.device_public(device),domain('profile-recipient'))
    return record['age_recipient']


def read_profile_recovery(storage,registry,profile,rid):
    path=f'registry/profile-recovery/{profile}/{rid}.json'
    record=shape(parse(storage.get(path)),'profile-recovery')
    if record['profile_id']!=profile or record['recovery_key_id']!=rid: raise IdentityError('invalid profile recovery binding')
    master=registry.recoveries.get(rid)
    if master is None: raise IdentityError('unknown recovery key')
    validate_recipient(record['age_recipient'])
    verify_signature(record,bytes.fromhex(master['recovery_signing_public']),domain('profile-recovery'))
    return record
