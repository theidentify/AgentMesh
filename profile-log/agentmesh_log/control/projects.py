"""New projects waiting for a scope, and mapping.set: the owner picks one, proved by the supervisor's presence check.

catch-up holds back the rows of a project the migration mapping does not cover and reports it in
profile-log-status.json. mapping.set only adds a mapping for such a pending project and only to a scope the mapping
already uses (or skip): it never changes an existing mapping, so an agent cannot widen who reads a project's memory,
and every addition needs the owner's Touch ID or Mac password. The file is replaced atomically, owner-only.
"""
from ..broker.keystore import atomic_write
from ..encoding import parse, wire
from ..migrate.mapping import Mapping
from .moves import MoveError


def load(layout):
    return Mapping.load(layout.mapping)


def covered(mapping, name):
    try: mapping.target(dict(scope='project',project=name)); return True
    except ValueError: return False


def pending(layout):
    """Projects the last catch-up held back that the mapping still does not cover. Unreadable state means none."""
    try:
        status=parse(layout.profile_status.read_bytes())
        names=status.get('last_catch_up',{}).get('unmapped',[])
        mapping=load(layout)
    except Exception: return []
    return sorted({n for n in names if type(n) is str and n and not covered(mapping,n)})


def scopes(layout):
    try: return sorted(load(layout).targets)+['skip']
    except Exception: return []


def summary(project, target):
    if target=='skip': return 'Skip new project %s: its memory stays out of the profile log.'%project
    return 'Map new project %s to %s. Agents that may read %s will see its memory.'%(project,target,target)


class Mappings:
    def __init__(self, layout, presence, moves):
        self.layout,self.presence,self.moves=layout,presence,moves

    def set(self, project, target):
        if type(project) is not str or project not in pending(self.layout): raise MoveError('not_pending')
        if type(target) is not str or target not in scopes(self.layout): raise MoveError('bad_args')
        self.moves.hold()
        try:
            if not self.presence.available(): raise MoveError('presence_unavailable')
            self.moves.prompted()
            if not self.presence.confirm('migrate',summary(project,target)): raise MoveError('presence_required')
            value=parse(self.layout.mapping.read_bytes())
            if project in value['projects']: raise MoveError('not_pending')
            value['projects'][project]=target
            Mapping(value)  # refuse to write a mapping catch-up could not load
            atomic_write(self.layout.mapping,wire(value))
        finally: self.moves.unhold()
        return dict(project=project,target=target)
