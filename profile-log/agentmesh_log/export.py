"""Owner export of a memory subtree as plaintext markdown files (memory tree T3b).

This is the one place AgentMesh writes memory as plaintext, on purpose and only for the owner: the caller
(the control plane) has already checked presence. Secret items are never written; internal ones only when asked.
The target must be new or empty and outside folders that sync or index themselves, unless the owner allows it.
"""
import os
from datetime import datetime, timezone
from pathlib import Path
from .broker.tree import split

MAX_FILES = 20000
README = """# AgentMesh memory export

This folder is a **plaintext** copy of encrypted AgentMesh memory, made on request by the owner.
AgentMesh does not track, update or delete it. Keep it private and delete it when you are done.

- Exported: {at}
- Device: {device}
- Path: {path}
- Labels: {labels}
- Files: {files}
- Secret items left out: {secret}
"""


def synced_roots(home):
    """Folders that copy or index their contents elsewhere (iCloud Drive, cloud providers, the Syncthing exchange)."""
    home=Path(home); icloud=home/'Library'/'Mobile Documents'
    roots=[icloud,home/'Library'/'CloudStorage',home/'.agentmesh'/'exchange',home/'.agentmesh']
    # Desktop & Documents in iCloud: the CloudDocs container then holds Desktop and Documents folders.
    if (icloud/'com~apple~CloudDocs'/'Desktop').exists() or (icloud/'com~apple~CloudDocs'/'Documents').exists():
        roots+=[home/'Desktop',home/'Documents']
    return roots


def check_target(folder, *, home, allow_synced=False):
    if type(folder) is not str or not folder or '\0' in folder: raise ValueError('bad_folder')
    path=Path(folder)
    if not path.is_absolute() or '..' in path.parts: raise ValueError('bad_folder')
    real=Path(os.path.realpath(path))
    if path.is_symlink() or (path.exists() and (not path.is_dir() or any(path.iterdir()))): raise ValueError('folder_not_empty')
    if not real.parent.is_dir(): raise ValueError('bad_folder')
    if not allow_synced:
        for root in synced_roots(home):
            root=Path(os.path.realpath(root))
            if real==root or root in real.parents: raise ValueError('synced_folder')
    return path


def collect(client, path, labels):
    """(relative file -> markdown, secret count) for every page and item under path that the labels allow."""
    files={}; secret=0; pending=[('/'+'/'.join(split(path)),0)]
    while pending:
        node,offset=pending.pop()
        listing=client.request(dict(op='ls',path=node,offset=offset))
        if 'error' in listing: raise ValueError(listing['error'])
        if listing['more']: pending.append((node,offset+len(listing['children'])))
        for entry in listing['children']:
            child=node.rstrip('/')+'/'+entry['name']
            if entry['type'] in ('dir','scope'): pending.append((child.rstrip('/'),0)); continue
            if entry['label']=='secret': secret+=1; continue
            if entry['label'] not in labels: continue
            page=client.request(dict(op='cat',path=child))
            if 'error' in page: raise ValueError(page['error'])
            # projects/ and tasks/ are views of the same items: write each once, at its canonical path.
            name=page['path'] if entry['type']=='page' else page['path']+'.md'
            files[name.lstrip('/')]=page['markdown']
            if len(files)>MAX_FILES: raise ValueError('too_many_files')
    return files,secret


def write(folder, files, *, path, labels, secret, device):
    folder=Path(folder); folder.mkdir(mode=0o700,exist_ok=True); os.chmod(folder,0o700)
    for name,text in sorted(files.items()):
        target=folder.joinpath(*name.split('/'))
        if folder.resolve() not in target.resolve().parents: raise ValueError('bad_path')
        target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as stream: stream.write(text)
    at=datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')
    fd=os.open(folder/'README.md',os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as stream:
        stream.write(README.format(at=at,device=device,path=path,labels=', '.join(labels),files=len(files),secret=secret))
    return len(files)
