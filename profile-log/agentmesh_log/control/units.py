"""The units AgentMesh runs on a Mac. Packet sync (org.agentmesh.sync) and catch-up (the SQLite import, R5b) are retired
and absent.

Each unit starts as its own launchd plist. `control consolidate --apply` moves every native-capable one under the
supervisor (org.agentmesh.control), so macOS lists one background item instead of one per unit.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Unit:
    name: str
    label: str
    kind: str                         # 'job' runs on a schedule (or once at load); 'service' is KeepAlive
    wrapped: bool = False             # runs through the job wrapper (control/jobrun.py), so maintenance and quota holds apply
    max_runtime_s: int | None = None  # a job running longer than this is reported stuck
    native_ok: bool = False           # may move to the supervisor's native backend


UNITS=(
    Unit('ingest','org.agentmesh.ingest','job',True,3600,True),
    Unit('summary','org.agentmesh.summary','job',True,3600,True),
    Unit('retention','org.agentmesh.retention','job',True,3600,True),
    Unit('broker','org.agentmesh.profile-log.broker','service',native_ok=True),
    Unit('bridge','org.agentmesh.profile-log.bridge','service',native_ok=True),
    Unit('insight','org.agentmesh.insight','service',native_ok=True),
    Unit('insight-tunnel','org.agentmesh.insight-tunnel','service',native_ok=True),
    Unit('bridge-tunnel','org.agentmesh.bridge-tunnel','service',native_ok=True),
    Unit('embed-tunnel','org.agentmesh.embed-tunnel','service',native_ok=True),
    Unit('ollama-env','org.agentmesh.ollama-env','job',False,600,True),  # RunAtLoad only: once per supervisor start
)


def get(name):
    for unit in UNITS:
        if unit.name==name: return unit
    raise KeyError(name)
