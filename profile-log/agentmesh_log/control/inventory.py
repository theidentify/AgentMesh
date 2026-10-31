"""keys.inventory: which keys and devices exist, how old they are, and what looks wrong. Read-only, tier 0.

The supervisor never opens a key file or the sealed keystore: files are lstat'ed only. Grants, recovery and devices come
from the broker's content-free sections of profile-log-status.json, which name agents by key_ref, never by agent_id.
Each source may be missing on its own; that part is then None with a finding, and the op still answers.
"""
from datetime import datetime
import os
import re
import stat
from .. import status
from ..heartbeats import iso
from .layout import read_json

DEVICE_STALE_S=14*86400
BACKUP_MAX_S=30*86400
KEYS_MAX_S=86400
ORDER={'warning':0,'info':1}


def seconds(value):
    """An ISO time as epoch seconds, or None."""
    try: return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
    except (AttributeError,TypeError,ValueError): return None


def sections(layout):
    try: value=read_json(layout.profile_status)
    except Exception: return {}
    return value if value is not None and status.valid(value) else {}


def entry(layout, path):
    """lstat only. kind is file, symlink, directory, other or missing."""
    row=dict(path=path.relative_to(layout.root).as_posix(),kind='missing',mode=None,owner_ok=None,size=None,modified_at=None)
    try: info=os.lstat(path)
    except FileNotFoundError: return row
    mode=info.st_mode
    row.update(kind='file' if stat.S_ISREG(mode) else 'symlink' if stat.S_ISLNK(mode) else 'directory' if stat.S_ISDIR(mode) else 'other',
        mode=stat.S_IMODE(mode),owner_ok=info.st_uid==os.getuid(),size=info.st_size,modified_at=iso(int(info.st_mtime*1000)))
    return row


def unsafe(row):
    return row['kind']!='file' or row['mode']&0o077 or not row['owner_ok'] or row['size']==0


def key_files(layout, findings):
    rows=[]
    for directory in (layout.agent_keys,layout.named_keys):
        top=entry(layout,directory)
        if top['kind']=='missing': continue
        if top['kind']!='directory':
            findings.append(('warning','key_file_unsafe',top['path'])); continue
        for name in sorted(os.listdir(directory)):
            if name.endswith('.key'): rows.append((directory,entry(layout,directory/name)))
    return rows


def inventory(layout, now):
    found=[]; status_=sections(layout)
    overview,keys,backup=status_.get('overview'),status_.get('keys'),status_.get('last_backup')
    by_ref={a['key_ref']:a for a in keys['agents']} if keys else None
    scopes={a['name']:a for a in overview['agents']} if overview else {}

    files=[]
    for directory,row in key_files(layout,found):
        stem=row['path'].rsplit('/',1)[-1][:-len('.key')]
        grant=None
        if directory==layout.agent_keys and by_ref is not None and re.fullmatch('[0-9a-f]{32}',stem):
            grant=by_ref.get(status.key_ref(stem))
            if grant is None: found.append(('info','key_file_orphan',row['path']))
        if unsafe(row): found.append(('warning','key_file_unsafe',row['path']))
        files.append(dict(row,agent=grant['name'] if grant else None))
    keystore=entry(layout,layout.keystore)
    if unsafe(keystore): found.append(('warning','keystore_unsafe',keystore['path']))

    devices=None
    if overview:
        devices=[]
        for d in overview['devices']:
            row=dict(device_id=d['device_id'],revoked=d['revoked'],this_device=d.get('this_device',False),name=d.get('name'),
                platform=d.get('platform'),last_seen_at=d.get('last_seen_at'),last_sync_at=d.get('last_sync_at'))
            seen=seconds(row['last_seen_at'])
            if not row['this_device'] and not row['revoked'] and (seen is None or now-seen>DEVICE_STALE_S):
                found.append(('warning','device_stale',row['device_id']))
            devices.append(row)

    agents=recovery=None
    if keys:
        agents=[]
        for a in keys['agents']:
            named=scopes.get(a['name']) if a['name'] is not None else None
            agents.append(dict(a,read=named['read'] if named else None,write=named['write'] if named else None))
            until=seconds(a['secret_until'])
            if until is not None and until>now: found.append(('info','secret_grant_active',a['key_ref']))
        recovery=dict(keys['recoveries'])
        if recovery['count']==0: found.append(('warning','no_recovery','profile'))
    generated=seconds(keys['generated_at']) if keys else None
    if generated is None or now-generated>KEYS_MAX_S: found.append(('warning','inventory_stale','broker'))

    taken=seconds(backup['at']) if backup else None
    if taken is None: found.append(('warning','backup_never','profile'))
    elif now-taken>BACKUP_MAX_S: found.append(('warning','backup_old','profile'))

    findings=[dict(id=kind+':'+subject,severity=severity,kind=kind,subject=subject)
        for severity,kind,subject in sorted(set(found),key=lambda f:(ORDER[f[0]],f[1],f[2]))]
    return dict(generated_at=iso(int(now*1000)),devices=devices,agents=agents,key_files=files,keystore=keystore,
        recovery=recovery,backup=dict(backup) if backup else None,findings=findings)
