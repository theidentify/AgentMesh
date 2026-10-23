"""The launchd units AgentMesh runs on a Mac. Packet sync (org.agentmesh.sync) is retired and absent."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Unit:
    name: str
    label: str
    kind: str                         # 'job' runs on a schedule; 'service' is KeepAlive
    wrapped: bool = False             # runs through job_notify.py, so maintenance and quota holds apply
    max_runtime_s: int | None = None  # a job running longer than this is reported stuck
    native_ok: bool = False           # may move to the supervisor's native backend (M2: scheduled jobs only)


UNITS=(
    Unit('ingest','org.agentmesh.ingest','job',True,3600,True),
    Unit('summary','org.agentmesh.summary','job',True,3600,True),
    Unit('retention','org.agentmesh.retention','job',True,3600,True),
    Unit('catch-up','org.agentmesh.profile-log.catch-up','job',False,1800,True),
    Unit('broker','org.agentmesh.profile-log.broker','service'),
    Unit('bridge','org.agentmesh.profile-log.bridge','service'),
    Unit('insight','org.agentmesh.insight','service'),
    Unit('insight-tunnel','org.agentmesh.insight-tunnel','service'),
)


def get(name):
    for unit in UNITS:
        if unit.name==name: return unit
    raise KeyError(name)
