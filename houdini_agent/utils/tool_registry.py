# -*- coding: utf-8 -*-
"""
ToolRegistry — 统一工具注册中心

将三套能力系统（Core Tools / Skills / Plugin Tools）统一管理：
  - 按模式获取可用工具列表 (agent / ask / plan_planning / plan_executing)
  - 统一执行入口
  - 支持工具启用/禁用（持久化到 config/plugins.json）
  - 为 UI 提供工具列表元数据
"""

import json
import threading
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set
from dataclasses import dataclass, field


# ─────────────────────────────────────────────
# 数据模型
# ─────────────────────────────────────────────

@dataclass
class ToolMeta:
    """工具元数据"""
    name: str                                    # 唯一标识
    schema: dict                                 # OpenAI function calling schema
    handler: Optional[Callable] = None           # 执行函数 (args: dict) -> dict；可为 None（由 MCP Client 分派）
    source: str = "core"                         # "core" | "skill" | "plugin" | "user"
    plugin_name: str = ""                        # 如果是插件工具，插件名
    tags: Set[str] = field(default_factory=set)  # {"readonly", "geometry", "network", "system", ...}
    modes: Set[str] = field(default_factory=set) # {"agent", "ask", "plan_planning", "plan_executing"}
    enabled: bool = True                         # 是否启用


# ─────────────────────────────────────────────
# 模式推断辅助（仅用于核心工具自动注册）
# ─────────────────────────────────────────────

# Ask 模式白名单（只读 / 查询工具）
_ASK_TOOLS = frozenset({
    'get_network_structure', 'get_node_parameters', 'list_children',
    'read_selection', 'search_node_types', 'semantic_search_nodes',
    'find_nodes_by_param', 'get_node_inputs', 'check_errors',
    'verify_and_summarize', 'web_search', 'fetch_webpage',
    'search_local_doc', 'get_houdini_node_doc', 'list_skills',
    'add_todo', 'update_todo', 'get_node_positions',
    'list_network_boxes', 'perf_start_profile', 'perf_stop_and_report',
})

# Plan 规划阶段白名单
_PLAN_PLANNING_TOOLS = frozenset({
    'get_network_structure', 'get_node_parameters', 'list_children',
    'read_selection', 'search_node_types', 'semantic_search_nodes',
    'find_nodes_by_param', 'get_node_inputs', 'check_errors',
    'verify_and_summarize', 'web_search', 'fetch_webpage',
    'search_local_doc', 'get_houdini_node_doc', 'list_skills',
    'add_todo', 'update_todo', 'get_node_positions',
    'list_network_boxes', 'perf_start_profile', 'perf_stop_and_report',
    'create_plan', 'ask_question',
})

# 只读标签推断
_READONLY_TOOLS = frozenset({
    'get_network_structure', 'get_node_parameters', 'list_children',
    'read_selection', 'search_node_types', 'semantic_search_nodes',
    'find_nodes_by_param', 'get_node_inputs', 'check_errors',
    'verify_and_summarize', 'web_search', 'fetch_webpage',
    'search_local_doc', 'get_houdini_node_doc', 'list_skills',
    'get_node_positions', 'list_network_boxes',
    'perf_start_profile', 'perf_stop_and_report',
})


def _infer_modes(name: str) -> Set[str]:
    """根据工具名自动推断适用模式"""
    modes = {"agent", "plan_executing"}  # 所有工具默认可在 Agent 和 Plan 执行阶段使用
    if name in _ASK_TOOLS:
        modes.add("ask")
    if name in _PLAN_PLANNING_TOOLS:
        modes.add("plan_planning")
    return modes


def _infer_tags(name: str) -> Set[str]:
    """根据工具名自动推断标签"""
    tags: Set[str] = set()
    if name in _READONLY_TOOLS:
        tags.add("readonly")
    # 几何/网络相关
    geo_kw = ('node', 'network', 'connect', 'create', 'delete', 'display',
              'parameter', 'children', 'selection', 'wrangle', 'copy', 'batch',
              'inputs', 'flag', 'layout', 'box')
    for kw in geo_kw:
        if kw in name.lower():
            tags.add("network")
            break
    # 系统/Shell
    if name in ('execute_python', 'execute_shell', 'save_hip', 'undo_redo'):
        tags.add("system")
    # 搜索/文档
    if name in ('web_search', 'fetch_webpage', 'search_local_doc', 'get_houdini_node_doc'):
        tags.add("docs")
    # Skill
    if name.startswith("skill:") or name in ('run_skill', 'list_skills'):
        tags.add("skill")
    # 任务管理
    if name in ('add_todo', 'update_todo'):
        tags.add("task")
    return tags


# ─────────────────────────────────────────────
# ToolRegistry 单例
# ─────────────────────────────────────────────

class ToolRegistry:
    """统一工具注册中心

    每个工具注册时需要：
      - name: 唯一标识
      - schema: OpenAI function calling schema
      - handler: 执行函数 (args: dict) -> dict，可为 None
      - source: "core" | "skill" | "plugin" | "user"
      - tags: set[str]  例如 {"readonly", "geometry", "network"}
      - modes: set[str]  例如 {"agent", "ask", "plan_planning", "plan_executing"}
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._tools: Dict[str, ToolMeta] = {}       # name -> ToolMeta
        self._disabled_tools: Set[str] = set()       # 持久化禁用列表
        self._initialized = False

    # ---------- 注册 / 注销 ----------

    def register(self, name: str, schema: dict,
                 handler: Optional[Callable] = None,
                 source: str = "core",
                 plugin_name: str = "",
                 tags: Optional[Set[str]] = None,
                 modes: Optional[Set[str]] = None,
                 enabled: bool = True):
        """注册工具"""
        with self._lock:
            meta = ToolMeta(
                name=name,
                schema=schema,
                handler=handler,
                source=source,
                plugin_name=plugin_name,
                tags=tags or set(),
                modes=modes or set(),
                enabled=enabled and (name not in self._disabled_tools),
            )
            self._tools[name] = meta

    def unregister(self, name: str):
        """注销工具"""
        with self._lock:
            self._tools.pop(name, None)

    def unregister_by_source(self, source: str, plugin_name: str = ""):
        """按来源注销（可指定插件名）"""
        with self._lock:
            to_remove = [
                n for n, m in self._tools.items()
                if m.source == source and (not plugin_name or m.plugin_name == plugin_name)
            ]
            for n in to_remove:
                del self._tools[n]

    # ---------- 查询 ----------

    def get_tools_for_mode(self, mode: str) -> List[dict]:
        """按模式获取工具 schema 列表（仅返回启用的工具）"""
        with self._lock:
            result = []
            for meta in self._tools.values():
                if not meta.enabled:
                    continue
                if mode in meta.modes:
                    result.append(meta.schema)
            return result

    def get_tool_schemas(self, names: Optional[List[str]] = None) -> List[dict]:
        """获取指定工具的 schema 列表（如 names 为 None 则返回全部启用的）"""
        with self._lock:
            result = []
            for meta in self._tools.values():
                if not meta.enabled:
                    continue
                if names is None or meta.name in names:
                    result.append(meta.schema)
            return result

    def has_tool(self, name: str) -> bool:
        """检查工具是否已注册"""
        return name in self._tools

    def get_handler(self, name: str) -> Optional[Callable]:
        """获取工具的执行函数"""
        meta = self._tools.get(name)
        return meta.handler if meta else None

    def list_all(self) -> List[Dict[str, Any]]:
        """列出所有工具元数据（供 UI 显示）"""
        with self._lock:
            result = []
            for meta in self._tools.values():
                result.append({
                    "name": meta.name,
                    "source": meta.source,
                    "plugin_name": meta.plugin_name,
                    "tags": sorted(meta.tags),
                    "modes": sorted(meta.modes),
                    "enabled": meta.enabled,
                    "description": meta.schema.get("function", {}).get("description", "")[:120],
                })
            return sorted(result, key=lambda x: (x["source"], x["name"]))

    # ---------- 执行 ----------

    def execute(self, name: str, args: dict) -> dict:
        """统一执行入口

        如果工具有 handler，直接调用。否则返回错误。
        注意：核心 Houdini 工具的 handler 为 None，由 MCP Client 分派。
        """
        meta = self._tools.get(name)
        if not meta:
            return {"success": False, "error": f"工具未注册: {name}"}
        if not meta.enabled:
            return {"success": False, "error": f"工具已禁用: {name}"}
        if not meta.handler:
            return {"success": False, "error": f"工具 {name} 无 handler（由 MCP Client 分派）"}
        try:
            return meta.handler(args)
        except Exception as e:
            return {"success": False, "error": f"工具 {name} 执行失败: {e}\n{traceback.format_exc()[:500]}"}

    # ---------- 启用 / 禁用 ----------

    def set_enabled(self, name: str, enabled: bool):
        """设置工具启用/禁用状态"""
        with self._lock:
            meta = self._tools.get(name)
            if meta:
                meta.enabled = enabled
            if enabled:
                self._disabled_tools.discard(name)
            else:
                self._disabled_tools.add(name)

    def is_enabled(self, name: str) -> bool:
        """查询工具是否启用"""
        meta = self._tools.get(name)
        return meta.enabled if meta else False

    def load_disabled_from_config(self, disabled_list: List[str]):
        """从配置文件加载禁用列表"""
        with self._lock:
            self._disabled_tools = set(disabled_list)
            for name, meta in self._tools.items():
                meta.enabled = name not in self._disabled_tools

    def get_disabled_tools(self) -> List[str]:
        """获取当前禁用列表"""
        return sorted(self._disabled_tools)

    def save_disabled_to_config(self):
        """将禁用列表保存到 plugins.json"""
        try:
            from .hooks import _plugin_config, _save_plugin_config
            _plugin_config["disabled_tools"] = sorted(self._disabled_tools)
            _save_plugin_config()
        except Exception as e:
            print(f"[ToolRegistry] 保存禁用列表失败: {e}")

    # ---------- 核心工具批量注册 ----------

    def register_core_tools(self, houdini_tools: List[dict]):
        """将 HOUDINI_TOOLS 列表批量注册为核心工具

        handler 为 None — 核心工具由 MCP Client 通过 _TOOL_DISPATCH 分派。
        """
        for tool_def in houdini_tools:
            name = tool_def.get("function", {}).get("name", "")
            if not name:
                continue
            self.register(
                name=name,
                schema=tool_def,
                handler=None,
                source="core",
                tags=_infer_tags(name),
                modes=_infer_modes(name),
            )
        self._initialized = True

    # ---------- 意图感知工具过滤 ----------

    # 工具按功能分组
    _INTENT_TOOL_GROUPS: Dict[str, Set[str]] = {
        'query': {
            'get_network_structure', 'get_node_parameters', 'list_children',
            'check_errors', 'read_selection', 'get_node_inputs',
            'get_node_positions', 'list_network_boxes',
            'verify_and_summarize',
        },
        'create': {
            'create_node', 'create_nodes_batch', 'create_wrangle_node',
            'connect_nodes', 'copy_node',
        },
        'modify': {
            'set_node_parameter', 'batch_set_parameters', 'set_display_flag',
        },
        'code': {
            'execute_python', 'execute_shell',
        },
        'search': {
            'web_search', 'fetch_webpage', 'search_local_doc',
            'search_node_types', 'semantic_search_nodes',
            'find_nodes_by_param', 'get_houdini_node_doc',
        },
        'layout': {
            'layout_nodes', 'create_network_box',
        },
        'task': {
            'add_todo', 'update_todo',
        },
        'perf': {
            'perf_start_profile', 'perf_stop_and_report',
        },
        'file': {
            'save_hip', 'undo_redo',
        },
        'plan': {
            'create_plan', 'update_plan_step', 'ask_question',
        },
        'skill': set(),  # 动态填充
    }

    # 意图关键词（中英文）
    _INTENT_KEYWORDS: Dict[str, List[str]] = {
        'query': ['what', 'show', 'list', 'check', 'look', 'display', 'view', 'see',
                  '查看', '检查', '分析', '看看', '显示', '状态', '有什么', '哪些'],
        'create': ['create', 'build', 'make', 'add', 'generate', 'construct',
                   '创建', '搭建', '添加', '生成', '建', '做', '造'],
        'modify': ['change', 'set', 'modify', 'update', 'adjust', 'tweak',
                   '修改', '设置', '调整', '改', '变'],
        'code': ['python', 'script', 'code', 'run', 'execute', 'vex', 'wrangle',
                 '脚本', '代码', '运行', '执行'],
        'search': ['search', 'find', 'where', 'document', 'doc', 'web', 'online',
                   '搜索', '查找', '文档', '网上', '在线'],
        'layout': ['layout', 'organize', 'arrange', 'position', 'move',
                   '排列', '布局', '整理', '位置'],
        'perf': ['performance', 'profile', 'benchmark', 'speed', 'slow',
                 '性能', '速度', '慢', '优化'],
        'file': ['save', 'undo', 'redo', '保存', '撤销', '重做'],
    }

    def classify_intent(self, user_message: str) -> Set[str]:
        """根据用户消息关键词推断意图类别

        Returns:
            命中的意图集合，如 {'query', 'create'}
        """
        if not user_message:
            return set()
        msg_lower = user_message.lower()
        matched = set()
        for intent, keywords in self._INTENT_KEYWORDS.items():
            for kw in keywords:
                if kw in msg_lower:
                    matched.add(intent)
                    break  # 一个关键词命中即可
        return matched

    def get_tools_for_intent(self, intents: Set[str], mode: str = 'agent') -> List[dict]:
        """根据意图集获取相关工具 schema

        始终包含 'query' 和 'task' 组（基础工具），额外包含匹配意图的工具组。
        只返回该 mode 下允许且启用的工具。
        """
        # 始终包含基础工具组
        active_groups = {'query', 'task'} | intents

        # 收集目标工具名集合
        target_names: Set[str] = set()
        for group in active_groups:
            target_names |= self._INTENT_TOOL_GROUPS.get(group, set())

        # 添加所有 skill 工具（skill 通常应始终可用）
        with self._lock:
            for name, meta in self._tools.items():
                if meta.source == 'skill' and meta.enabled:
                    target_names.add(name)

        # 过滤：必须在指定 mode 中且启用
        with self._lock:
            result = []
            for meta in self._tools.values():
                if not meta.enabled:
                    continue
                if mode not in meta.modes:
                    continue
                if meta.name in target_names:
                    result.append(meta.schema)
            return result

    def is_tool_allowed_in_mode(self, tool_name: str, mode: str) -> bool:
        """检查工具是否被允许在指定模式下使用"""
        meta = self._tools.get(tool_name)
        if not meta:
            return False
        return meta.enabled and mode in meta.modes

    @property
    def initialized(self) -> bool:
        return self._initialized


# ─────────────────────────────────────────────
# 全局单例
# ─────────────────────────────────────────────

_instance: Optional[ToolRegistry] = None
_instance_lock = threading.Lock()


def get_tool_registry() -> ToolRegistry:
    """获取 ToolRegistry 全局单例"""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = ToolRegistry()
    return _instance
