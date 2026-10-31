"""Control-plane paths under the AgentMesh home; state files are owner-only JSON."""
import json
from pathlib import Path
from .. import settings
from ..broker.keystore import atomic_write
from ..storage.filesystem import _check


class Layout:
    def __init__(self, root=None):
        self.root=Path(root) if root is not None else settings.root()
        self.control=self.root/'control'; self.status=self.root/'status'; self.run=self.root/'run'
        self.socket=self.run/'control.sock'; self.audit=self.control/'audit.jsonl'
        self.maintenance=self.control/'maintenance.json'
        self.quota=self.control/'quota.json'; self.quota_policy=self.control/'quota-policy.json'
        self.state=self.control/'state.db'; self.presence_helper=self.root/'bin'/'agentmesh-presence'
        self.mapping=self.root/'migration-mapping.json'; self.profile_status=self.root/'profile-log-status.json'
        self.webhook=Path.home()/'.config'/'agentmesh'/'discord-webhook'

    def ensure(self):
        for directory in (self.control,self.run):
            _check(directory); directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        return self


def read_json(path):
    """A JSON object, or None when the file is missing or not an object. Symlinks are refused."""
    path=Path(path); _check(path)
    try: value=json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError,ValueError): return None
    return value if isinstance(value,dict) else None


def write_json(path, value):
    atomic_write(path,json.dumps(value,sort_keys=True,ensure_ascii=False).encode('utf-8'))
