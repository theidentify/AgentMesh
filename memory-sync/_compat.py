"""Shared by the compatibility shims in this directory: import a module of agentmesh_log, from the installed package
or else from the profile-log/ directory beside this one (a deployed checkout of the whole repository)."""
import importlib
import sys
from pathlib import Path


def load(name):
    try:
        import agentmesh_log  # noqa: F401
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'profile-log'))
    return importlib.import_module(name)
