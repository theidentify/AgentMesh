"""Local authorization: descendant reads, exact writes, opaque grant digests."""
from dataclasses import dataclass, asdict
import hashlib
from ..encoding import wire


@dataclass
class Grant:
    agent_id: str
    agent_public_key: str
    read: list
    write: list
    default_write: str
    default_profile: str
    allow_secret: bool = False
    allow_cross_profile: bool = False
    allow_import: bool = False

    def __post_init__(self):
        if type(self.allow_import) is not bool: raise ValueError('invalid import grant')

    def digest(self):
        return hashlib.sha256(wire(asdict(self))).hexdigest()


def names(state):
    result = {}
    for tree in state.trees:
        def name(scope):
            return scope.name if scope.parent is None else name(tree.scopes[scope.parent]) + '/' + scope.name
        result.update({sid: name(scope) for sid, scope in tree.scopes.items()})
    if len(set(result.values())) != len(result): raise ValueError('duplicate scope name')
    return result


def resolve(state, name):
    for sid, value in names(state).items():
        if value == name: return sid
    raise ValueError('denied')


def descendants(state, roots):
    result = set(roots)
    for tree in state.trees:
        changed = True
        while changed:
            before = len(result)
            result.update(s.scope_id for s in tree.scopes.values() if s.parent in result)
            changed = len(result) != before
    return result


def readable(state, grant):
    return descendants(state, grant.read)
