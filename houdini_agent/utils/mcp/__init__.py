# -*- coding: utf-8 -*-
"""Houdini MCP package.

架构：
    hou_core.py  → 底层 Houdini 操作（server / client 共享）
    client.py    → HoudiniMCP 类，面向内部 AI Agent（直接 Python 调用）
    server.py    → FastMCP HTTP 服务器，面向外部 MCP 客户端
    settings.py  → MCPSettings 配置数据类
    logger.py    → 日志工具

Public APIs:
- HoudiniMCP: UI-side helper client
- ensure_mcp_running / stop_mcp_server / get_mcp_status: server lifecycle
- MCPSettings / read_settings / get_logger: config and logging
- hou_core: shared Houdini operation primitives
"""
from __future__ import annotations

from .settings import MCPSettings, read_settings
from .logger import get_logger
from .client import HoudiniMCP
from .server import ensure_mcp_running, stop_mcp_server, get_mcp_status
from . import hou_core

__all__ = [
    "MCPSettings",
    "read_settings",
    "get_logger",
    "HoudiniMCP",
    "ensure_mcp_running",
    "stop_mcp_server",
    "get_mcp_status",
    "hou_core",
]
