"""Compatibility shim (memory-sync retirement): this module is now agentmesh_log.ingest.timestamps. Removed in PR B."""
import sys
import _compat
sys.modules[__name__] = _compat.load('agentmesh_log.ingest.timestamps')
