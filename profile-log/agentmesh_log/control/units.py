"""The launchd units AgentMesh runs on a Mac. Packet sync (org.agentmesh.sync) is retired and absent."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Unit:
    name: str
    label: str
    kind: str                         # 'job' runs on a schedule; 'service' is KeepAlive
    wrapped: bool = False             # runs through job_notify.py, so maintenance and quota holds apply
    max_runtime_s: int | None = None  # a job running longer than this is reported stuck


UNITS=(
    Unit('ingest','org.agentmesh.ingest','job',True,3600),
    Unit('summary','org.agentmesh.summary','job',True,3600),
    Unit('retention','org.agentmesh.retention','job',True,3600),
    Unit('catch-up','org.agentmesh.profile-log.catch-up','job',False,1800),
    Unit('broker','org.agentmesh.profile-log.broker','service'),
    Unit('bridge','org.agentmesh.profile-log.bridge','service'),
    Unit('insight','org.agentmesh.insight','service'),
    Unit('insight-tunnel','org.agentmesh.insight-tunnel','service'),
)


def get(name):
    for unit in UNITS:
        if unit.name==name: return unit
    raise KeyError(name)
