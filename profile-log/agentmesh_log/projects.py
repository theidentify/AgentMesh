"""Content-pinned project routing; project files never grant authority."""
import hashlib
from pathlib import Path
import sys
from . import settings
from .encoding import parse
from .broker.grants import names
from .presence import PresenceManager


def project_files(folder, diagnostics=None):
    diagnostics = [] if diagnostics is None else diagnostics
    folder = Path(folder).resolve(); directory = folder / '.agentmesh'
    settings.safe(directory, directory=True, checkout=True)
    merged = {}; digest = hashlib.sha256()
    for filename in ('settings.json', 'settings.local.json'):
        path = directory / filename; settings.safe(path, missing=True, checkout=True)
        present = path.exists()
        raw = settings.read_raw(path, checkout=True) if present else b''
        # Length framing distinguishes shared/local boundaries and missing files.
        digest.update(len(raw).to_bytes(8, 'big')); digest.update(raw)
        if present:
            try: value = settings.validate(parse(raw), project=True)
            except Exception:
                diagnostics.append('AgentMesh ignored project file ' + repr(str(path)))
                continue
            merged = settings.merge(merged, value)
    route = merged.get('project', {})
    if set(route) != {'name', 'scope'}: raise ValueError('invalid_settings')
    return str(folder), route, digest.hexdigest()


def trust(folder, *, console=None, backend=None, output=print):
    from .apply import open_home
    base = settings.root(); settings.safe(base, directory=True)
    diagnostics = []
    try: path, route, digest = project_files(folder, diagnostics)
    finally:
        for message in diagnostics: print(message, file=sys.stderr)
    home, store = open_home(base, backend)
    with home.lock():
        summary = 'Project ' + route['name'] + ' -> ' + route['scope']
        output(summary)
        PresenceManager(home, store).require('agent', 'trust', console=console, one_shot=True, summary=summary)
        # Do not approve bytes changed while the owner was reading the prompt.
        if project_files(folder) != (path, route, digest): raise ValueError('invalid_settings')
        state = store.load(); state.control.setdefault('trusted_projects', {})[path] = digest; store.save(state)
    return {'ok': True}


def effective_mapping(value, state, project_dirs=()):
    from .apply import mapping_value
    from .migrate.mapping import Mapping
    mapping = mapping_value(value)
    if mapping is None: raise ValueError('invalid_settings')
    existing = set(names(state).values()); allowed = set(value.get('routing', {}).get('allowed_scopes', []))
    trusted = state.control.get('trusted_projects', {})
    # Apply all remembered routes, plus explicitly requested folders (to warn if untrusted).
    folders = sorted(set(trusted) | {str(Path(p).resolve()) for p in project_dirs})
    for folder in folders:
        diagnostics = []
        try:
            path, route, digest = project_files(folder, diagnostics)
            if trusted.get(path) != digest or route['scope'] not in allowed or route['scope'] not in existing:
                raise ValueError('ignored_project')
            mapping['projects'][route['name']] = route['scope']
        except (ValueError, OSError):
            # repr escapes newlines/control characters in untrusted folder names.
            if not diagnostics: diagnostics.append('AgentMesh ignored project ' + repr(folder))
        for message in diagnostics: print(message, file=sys.stderr)
    return Mapping(mapping)
