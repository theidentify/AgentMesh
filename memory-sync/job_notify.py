"""Compatibility shim (memory-sync retirement): this script is now `python -m agentmesh_log.control.jobrun`. Removed in PR B."""
import sys
import _compat
_module = _compat.load('agentmesh_log.control.jobrun')
if __name__ == "__main__":
    raise SystemExit(_module.main())
sys.modules[__name__] = _module
