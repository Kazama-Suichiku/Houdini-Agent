# -*- coding: utf-8 -*-
"""Lightweight local bridge between standalone UI and Houdini.

Standalone UI imports this package too, so Houdini-only server code is loaded
only when the Houdini shelf launcher asks for it.
"""

from .remote_client import RemoteHoudiniMCPClient


def ensure_bridge_running(*args, **kwargs):
    from .server import ensure_bridge_running as _ensure_bridge_running
    return _ensure_bridge_running(*args, **kwargs)


def get_bridge_status(*args, **kwargs):
    from .server import get_bridge_status as _get_bridge_status
    return _get_bridge_status(*args, **kwargs)


def stop_bridge(*args, **kwargs):
    from .server import stop_bridge as _stop_bridge
    return _stop_bridge(*args, **kwargs)

__all__ = [
    "RemoteHoudiniMCPClient",
    "ensure_bridge_running",
    "get_bridge_status",
    "stop_bridge",
]
