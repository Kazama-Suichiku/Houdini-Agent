# -*- coding: utf-8 -*-
"""
Meshy 集成包 — 把 Meshy 的图生/文生 3D、重打材质、重拓扑接入 Houdini Agent。

自包含设计：本包在被 import 时自动把 7 个工具注册进 ToolRegistry。核心文件只需：
  - 把 MESHY_TOOLS 拼进 LLM 可见的工具列表（agent_session / bridge_session）
  - controller 用 NETWORK_TOOLS / CONFIRM_TOOLS 做路由与确认门
  - bridge/server 在 Houdini 侧 import 本包，使导入/导出 handler 在该进程注册

线程边界：
  - NETWORK_TOOLS 在 app 侧后台线程执行（run_network），绝不进 Houdini/bridge
  - HOUDINI_TOOLS（import_3d_asset/export_node_to_glb）在 Houdini 主线程执行
"""

# 兜底：确保顶层 shared 包可被 import。Houdini 侧 bridge 从 bridge_payload 加载 houdini_agent 时，
# 若加载根未在 sys.path 上会报 "No module named 'shared'"，导致 import_3d_asset 等工具注册失败。
import os as _os
import sys as _sys
_root = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _root not in _sys.path:
    _sys.path.insert(0, _root)

from .schemas import (
    MESHY_TOOLS, NETWORK_TOOLS, HOUDINI_TOOLS, CONFIRM_TOOLS,
    MUTATING_TOOLS, INTERACTIVE_TOOLS, ALL_TOOL_NAMES,
)
from .network_ops import (
    run_network, run_network_parallel, generate_concepts, concepts_to_3d,
    list_library, fetch_library_asset, LIBRARY_KINDS,
    BATCH_TOOLS, MAX_PARALLEL,
)
from .config import (
    has_api_key, set_api_key, get_api_key, masked_key, clear_api_key,
)
from . import telemetry

__all__ = [
    "MESHY_TOOLS", "NETWORK_TOOLS", "HOUDINI_TOOLS", "CONFIRM_TOOLS",
    "MUTATING_TOOLS", "INTERACTIVE_TOOLS", "ALL_TOOL_NAMES",
    "BATCH_TOOLS", "MAX_PARALLEL",
    "run_network", "run_network_parallel", "generate_concepts", "concepts_to_3d",
    "list_library", "fetch_library_asset", "LIBRARY_KINDS",
    "has_api_key", "set_api_key", "get_api_key", "masked_key", "clear_api_key",
    "telemetry",
    "register",
]

_REGISTERED = False


def _make_network_handler(name):
    """注册用的 handler：无进度回调的同步执行（legacy/registry 回退路径用）。
    QML 主路径在 controller 里直接调 run_network 并带进度信号，不走这里。"""
    def _h(args):
        return run_network(name, args or {})
    return _h


def _interactive_fallback(name):
    """交互工具的 registry 回退 handler：非交互环境下不静默烧 credits，明确报错。
    QML 主路径在 controller 里特殊编排（画廊/多选/重生），不走这里。"""
    def _h(args):
        return {"success": False,
                "error": "%s 需要交互式 UI（QML 前端）才能运行" % name}
    return _h


def register():
    """把 Meshy 工具注册进 ToolRegistry（幂等）。import 时自动调用。"""
    global _REGISTERED
    if _REGISTERED:
        return
    try:
        from houdini_agent.utils.tool_registry import get_tool_registry
        reg = get_tool_registry()
    except Exception as e:
        print("[meshy] ToolRegistry 不可用，跳过注册: %s" % e)
        return

    modes = {"agent", "plan_executing"}
    schema_by_name = {t["function"]["name"]: t for t in MESHY_TOOLS}

    # 网络工具：handler 走 run_network（供 registry 回退；主路径在 controller 拦截）
    for name in NETWORK_TOOLS:
        sch = schema_by_name.get(name)
        if not sch:
            continue
        handler = (_interactive_fallback(name) if name in INTERACTIVE_TOOLS
                   else _make_network_handler(name))
        reg.register(name, sch, handler=handler,
                     source="core", tags={"meshy", "network"}, modes=modes)

    # Houdini 工具：handler 在主线程调 hou.*（延迟 import，注册时不需要 hou）
    try:
        from . import houdini_io
        hou_handlers = {
            "import_3d_asset": houdini_io.import_3d_asset,
            "export_node_to_glb": houdini_io.export_node_to_glb,
        }
        for name in HOUDINI_TOOLS:
            sch = schema_by_name.get(name)
            if sch:
                reg.register(name, sch, handler=hou_handlers[name],
                             source="core", tags={"meshy", "geometry"}, modes=modes)
    except Exception as e:
        print("[meshy] Houdini 工具注册失败: %s" % e)

    _REGISTERED = True


# import 即自注册
register()
