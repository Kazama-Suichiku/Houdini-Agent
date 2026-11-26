# -*- coding: utf-8 -*-
"""AgentSession-compatible backend that executes Houdini tools through BridgeClient."""

from houdini_agent.bridge.client import BridgeClient
from houdini_agent.ui_qml.agent_session import BaseAgentSession


_BRIDGE_SYSTEM_NOTE = (
    "\n\n[Standalone Bridge Mode & Multi-Instance Houdini]\n"
    "You run inside a standalone desktop app connected to Houdini through local TCP bridges. "
    "Chat, planning, web/doc search and image/3D generation always work, even without Houdini. "
    "Multiple Houdini instances (same version or different versions, e.g. 20.5 + 21.0) can be "
    "connected simultaneously, distinguished by port, PID, Houdini version, and open .hip filename. "
    "Use `list_houdini_instances` to inspect all open Houdini windows, `switch_houdini_instance` "
    "to change the active default target, `run_in_houdini_instance` to inspect or edit another "
    "open .hip file directly without switching, and `transfer_nodes_between_instances` to copy "
    "nodes, wrangles, or subnets across open Houdini instances/files (including across versions). "
    "If a scene tool reports the Houdini connection is lost, do NOT give up: call "
    "`check_houdini_connection` to diagnose, then follow its advice with `repair_houdini_connection` "
    "(action='reconnect' for port changes, 'reinstall_package' when the integration package is "
    "missing/stale, 'launch_houdini' to start Houdini and wait for it). "
    "The integration package adds NO menus, shelves or visible UI inside Houdini — never ask the "
    "user to look for one. After the user restarts Houdini, verify by calling "
    "repair_houdini_connection(action='reconnect', wait_seconds=30) yourself. "
    "If Houdini must be restarted manually, tell the user exactly what to do and continue helping."
)


class _BridgeMCP:
    def __init__(self, bridge):
        self._bridge = bridge

    def execute_tool(self, name, args):
        return self._bridge.execute_tool(name, args)


class BridgeAgentSession(BaseAgentSession):
    """Standalone desktop app session routing Houdini tools through BridgeClient."""

    def __init__(self, bridge=None):
        self._init_common(system_note=_BRIDGE_SYSTEM_NOTE)
        self.bridge = bridge or BridgeClient()
        self.mcp = _BridgeMCP(self.bridge)
        # 连接诊断/修复工具（仅独立 app 有意义：让 agent 能排查甚至重连 Houdini）
        try:
            from houdini_agent.bridge.doctor import CONNECTION_TOOLS
            self.tools = self.tools + list(CONNECTION_TOOLS)
        except Exception as e:
            print("[bridge_session] connection tools unavailable:", e)
