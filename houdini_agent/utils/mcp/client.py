# -*- coding: utf-8 -*-
"""
Houdini MCP Client
提供节点操作的核心功能，支持 AI Agent 的工具调用
"""
from __future__ import annotations

import os
import sys
import re
import time
import json
from typing import Any, Optional, Dict, List, Tuple
from pathlib import Path

try:
    import hou  # type: ignore
except Exception:
    hou = None  # type: ignore


# ============================================================
# 文档检索功能已移除，请使用 web_search 查询官方文档
# ============================================================

# 强制使用本地 lib 目录中的依赖库
_lib_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), 'lib')
if os.path.exists(_lib_path):
    # 将 lib 目录添加到 sys.path 最前面，确保优先使用
    if _lib_path in sys.path:
        sys.path.remove(_lib_path)
    sys.path.append(_lib_path)

# 导入 requests
try:
    import requests
except ImportError:
    requests = None  # type: ignore

from .settings import read_settings

# 导入 RAG 检索系统
try:
    from ..doc_rag import get_doc_rag
    HAS_DOC_RAG = True
except ImportError:
    HAS_DOC_RAG = False
    print("[MCP Client] DocRAG 模块未找到，本地文档检索功能不可用")

# 导入 Skill 系统
HAS_SKILLS = False
_list_skills = None   # type: ignore
_run_skill = None     # type: ignore
try:
    from ...skills import list_skills as _list_skills, run_skill as _run_skill
    HAS_SKILLS = True
except (ImportError, ValueError, SystemError):
    pass

if not HAS_SKILLS:
    try:
        import importlib
        _skills_mod = importlib.import_module('houdini_agent.skills')
        _list_skills = _skills_mod.list_skills
        _run_skill = _skills_mod.run_skill
        HAS_SKILLS = True
    except Exception:
        pass

if not HAS_SKILLS:
    # 最后尝试：基于文件路径直接导入
    try:
        import importlib.util
        _skills_init = Path(__file__).parent.parent.parent / 'skills' / '__init__.py'
        if _skills_init.exists():
            _spec = importlib.util.spec_from_file_location('houdini_skills', str(_skills_init))
            _skills_mod = importlib.util.module_from_spec(_spec)
            _spec.loader.exec_module(_skills_mod)
            _list_skills = _skills_mod.list_skills
            _run_skill = _skills_mod.run_skill
            HAS_SKILLS = True
    except Exception:
        pass

if not HAS_SKILLS:
    print("[MCP Client] Skill 系统未加载，run_skill/list_skills 不可用")

from .tools.network_inspect import NetworkInspectMixin
from .tools.node_ops import NodeOpsMixin
from .tools.param_ops import ParamOpsMixin
from .tools.exec_ops import ExecOpsMixin
from .tools.doc_ops import DocOpsMixin


class HoudiniMCP(NetworkInspectMixin, NodeOpsMixin, ParamOpsMixin, ExecOpsMixin, DocOpsMixin):
    """Houdini 节点操作客户端

    提供节点网络的读取、创建、修改、删除等操作。
    设计为 AI Agent 的工具执行后端。
    """

    # 类级别缓存（跨实例共享，只加载一次）
    _node_types_cache: Optional[Dict[str, List[str]]] = None  # {category: [type_names]}
    _node_types_cache_time: float = 0  # 缓存时间
    _common_node_inputs_cache: Dict[str, str] = {}  # 常见节点输入信息缓存
    _ats_cache: Dict[str, Dict[str, Any]] = {}  # ATS缓存: {node_type_key: ats_data}

    # perfMon 性能分析：当前活跃的 profile 对象
    _active_perf_profile: Any = None

    # 通用工具结果分页缓存：key = "tool_name:unique_key" → 完整文本
    _tool_page_cache: Dict[str, str] = {}
    _TOOL_PAGE_LINES = 50  # 每页行数

    # 常见节点输入说明（从外部 JSON 加载，避免硬编码）
    _COMMON_NODE_INPUTS: Dict[str, str] = {}

    def __init__(self):
        import threading
        self._stop_event: Optional[threading.Event] = None

    def set_stop_event(self, event):
        """设置停止事件（从 AIClient 传入，用于检测用户中断）

        在 execute_python / execute_shell 中通过检查此事件来支持用户中断。
        """
        self._stop_event = event

    @classmethod
    def _paginate_tool_result(cls, text: str, cache_key: str, tool_hint: str,
                              page: int = 1, page_lines: int = 0) -> str:
        """通用工具结果分页

        Args:
            text: 完整的文本结果
            cache_key: 缓存键（如 "get_node_parameters:/obj/geo1/box1"）
            tool_hint: 供 AI 翻页的工具调用提示（如 'get_node_parameters(node_path="/obj/geo1/box1", page=2)'）
            page: 页码（从 1 开始）
            page_lines: 每页行数，0 表示使用默认值
        """
        if not page_lines:
            page_lines = cls._TOOL_PAGE_LINES

        cls._tool_page_cache[cache_key] = text

        lines = text.split('\n')
        total_lines = len(lines)
        total_pages = max(1, (total_lines + page_lines - 1) // page_lines)

        page = max(1, min(page, total_pages))

        start = (page - 1) * page_lines
        end = min(start + page_lines, total_lines)
        page_text = '\n'.join(lines[start:end])

        if total_pages == 1:
            return page_text

        header = f"[第 {page}/{total_pages} 页, 共 {total_lines} 行]\n\n"

        if page < total_pages:
            # 将 page_hint 中的页码替换为下一页
            next_page = page + 1
            footer = f"\n\n[第 {page}/{total_pages} 页] 还有更多内容，调用 {tool_hint.replace(f'page={page}', f'page={next_page}')} 查看下一页"
        else:
            footer = f"\n\n[第 {page}/{total_pages} 页 - 最后一页]"

        return header + page_text + footer

    # ========================================
    # 工具分派处理器（每个工具一个方法）
    # ========================================

    def _tool_create_wrangle_node(self, args: Dict[str, Any]) -> Dict[str, Any]:
        vex_code = args.get("vex_code", "")
        if not vex_code:
            return {"success": False, "error": "缺少 vex_code 参数"}
        ok, msg = self.create_wrangle_node(
            vex_code, args.get("wrangle_type", "attribwrangle"),
            args.get("node_name"), args.get("run_over", "Points"),
            args.get("parent_path"))
        return {"success": ok, "result": msg if ok else "", "error": "" if ok else msg}

    def _tool_get_network_structure(self, args: Dict[str, Any]) -> Dict[str, Any]:
        network_path = args.get("network_path")
        box_name = args.get("box_name")  # NetworkBox 钻入参数
        page = int(args.get("page", 1))

        # 分页快速路径（box_name 也参与缓存键）
        cache_suffix = f":{box_name}" if box_name else ""
        cache_key = f"get_network_structure:{network_path or '_current'}{cache_suffix}"
        if page > 1 and cache_key in self._tool_page_cache:
            np_arg = f'network_path="{network_path}", ' if network_path else ''
            bx_arg = f'box_name="{box_name}", ' if box_name else ''
            hint = f'get_network_structure({np_arg}{bx_arg}page={page})'
            return {"success": True, "result": self._paginate_tool_result(
                self._tool_page_cache[cache_key], cache_key, hint, page)}

        ok, data = self.get_network_structure(network_path)
        if ok:
            _, text = self.get_network_structure_text(network_path, box_name=box_name)
            np_arg = f'network_path="{network_path}", ' if network_path else ''
            bx_arg = f'box_name="{box_name}", ' if box_name else ''
            hint = f'get_network_structure({np_arg}{bx_arg}page={page})'
            return {"success": True, "result": self._paginate_tool_result(
                text, cache_key, hint, page)}
        return {"success": False, "error": data.get("error", "未知错误")}

    def _tool_get_node_parameters(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """获取节点的所有可用参数（名称、类型、默认值、当前值），支持分页"""
        node_path = args.get("node_path", "")
        if not node_path:
            return {"success": False, "error": "缺少 node_path 参数"}
        page = int(args.get("page", 1))

        if hou is None:
            return {"success": False, "error": "未检测到 Houdini API"}

        # 分页快速路径：缓存中已有完整结果
        cache_key = f"get_node_parameters:{node_path}"
        if page > 1 and cache_key in self._tool_page_cache:
            hint = f'get_node_parameters(node_path="{node_path}", page={page})'
            return {"success": True, "result": self._paginate_tool_result(
                self._tool_page_cache[cache_key], cache_key, hint, page)}

        node = hou.node(node_path)
        if node is None:
            return {"success": False, "error": f"未找到节点: {node_path}"}

        try:
            node_type = node.type()
            type_key = f"{node_type.category().name().lower()}/{node_type.name()}"
            lines = [
                f"## {node.name()} ({node.path()})",
                f"类型: {type_key} ({node_type.description()})",
            ]

            # ★ 节点概况（原 get_node_details 功能合并） ★
            # 状态标志
            flags = []
            if hasattr(node, 'isDisplayFlagSet') and node.isDisplayFlagSet():
                flags.append('display')
            if hasattr(node, 'isRenderFlagSet') and node.isRenderFlagSet():
                flags.append('render')
            if hasattr(node, 'isBypassed') and node.isBypassed():
                flags.append('bypass')
            if hasattr(node, 'isLocked') and node.isLocked():
                flags.append('locked')
            if flags:
                lines.append(f"标志: {', '.join(flags)}")

            # 错误信息
            try:
                errs = node.errors()
                if errs:
                    lines.append(f"⚠ 错误: {'; '.join(errs[:3])}")
            except Exception:
                pass

            # 输入连接
            inputs = []
            for i, inp in enumerate(node.inputs()):
                if inp is not None:
                    inputs.append(f"[{i}]{inp.path()}")
            if inputs:
                lines.append(f"输入: {', '.join(inputs)}")

            # 输出连接
            outputs = [o.path() for o in node.outputs()] if node.outputs() else []
            if outputs:
                lines.append(f"输出: {', '.join(outputs[:5])}")

            lines.append("")  # 空行分隔

            # 遍历所有参数模板（完整列表）
            parm_group = node_type.parmTemplateGroup()
            if not parm_group:
                lines.append("(无参数)")
                return {"success": True, "result": "\n".join(lines)}

            count = 0
            for pt in parm_group.parmTemplates():
                try:
                    if pt.isHidden():
                        continue
                    name = pt.name()
                    ptype = pt.type().name() if hasattr(pt, 'type') else "?"
                    label = pt.label() if hasattr(pt, 'label') else ""

                    # 获取默认值
                    default = None
                    try:
                        default = pt.defaultValue()
                        if isinstance(default, float):
                            default = round(default, 4)
                        elif isinstance(default, tuple):
                            default = tuple(round(v, 4) if isinstance(v, float) else v for v in default)
                    except Exception:
                        pass

                    # 获取当前值
                    current = None
                    try:
                        parm = node.parm(name)
                        if parm:
                            current = parm.eval()
                            if isinstance(current, float):
                                current = round(current, 4)
                            elif isinstance(current, tuple):
                                current = tuple(round(v, 4) if isinstance(v, float) else v for v in current)
                    except Exception:
                        pass

                    # 菜单选项（如果有）
                    menu_items = ""
                    if ptype == "Menu" and hasattr(pt, 'menuItems'):
                        try:
                            items = pt.menuItems()
                            labels = pt.menuLabels() if hasattr(pt, 'menuLabels') else items
                            if items and len(items) <= 10:
                                pairs = [f"{it}({lb})" if lb != it else it
                                         for it, lb in zip(items, labels)]
                                menu_items = f" options=[{', '.join(pairs)}]"
                            elif items:
                                menu_items = f" options=[{', '.join(items[:8])}...]"
                        except Exception:
                            pass

                    is_default = (current == default) if current is not None and default is not None else None
                    marker = "" if is_default else " *"  # * 标记非默认值

                    lines.append(
                        f"- {name} ({ptype}, {label}): "
                        f"default={default}, current={current}{marker}{menu_items}"
                    )
                    count += 1
                except Exception:
                    continue

            lines.insert(2, f"参数数量: {count}")
            full_text = "\n".join(lines)

            # 分页返回
            hint = f'get_node_parameters(node_path="{node_path}", page={page})'
            return {"success": True, "result": self._paginate_tool_result(
                full_text, cache_key, hint, page)}

        except Exception as e:
            return {"success": False, "error": f"获取参数失败: {str(e)}"}

    def _tool_set_node_parameter(self, args: Dict[str, Any]) -> Dict[str, Any]:
        node_path = args.get("node_path", "")
        param_name = args.get("param_name", "")
        value = args.get("value")
        missing = []
        if not node_path:
            missing.append("node_path(节点路径)")
        if not param_name:
            missing.append("param_name(参数名)")
        if missing:
            return {"success": False, "error": f"缺少必要参数: {', '.join(missing)}"}
        ok, msg, snapshot = self.set_parameter(node_path, param_name, value)
        result = {"success": ok, "result": msg if ok else "", "error": "" if ok else msg}
        if ok and snapshot:
            # ★ 参数前后值一致时不生成 checkpoint，避免显示无意义的"修改"
            old_v = snapshot.get("old_value")
            new_v = snapshot.get("new_value")
            if old_v != new_v:
                result["_undo_snapshot"] = snapshot  # 供 UI 撤销使用，不会发给 AI
        return result

    def _tool_create_node(self, args: Dict[str, Any]) -> Dict[str, Any]:
        node_type = args.get("node_type", "")
        if not node_type:
            return {"success": False, "error": "缺少 node_type 参数"}
        ok, msg = self.create_node(
            node_type, args.get("node_name"),
            args.get("parameters"), args.get("parent_path"))
        if ok:
            return {"success": True, "result": msg, "error": ""}
        error_msg = msg if msg else f"创建节点失败: {node_type}"
        print(f"[MCP Client] create_node 失败: {error_msg[:200]}")
        return {"success": False, "result": "", "error": error_msg}

    def _tool_create_nodes_batch(self, args: Dict[str, Any]) -> Dict[str, Any]:
        nodes = args.get("nodes", [])
        if not nodes:
            return {"success": False, "error": "缺少 nodes 参数"}
        plan = {"nodes": nodes, "connections": args.get("connections", [])}
        ok, msg = self.create_network(plan)
        return {"success": ok, "result": msg if ok else "", "error": "" if ok else msg}

    def _tool_connect_nodes(self, args: Dict[str, Any]) -> Dict[str, Any]:
        from_path = args.get("from_path", "")
        to_path = args.get("to_path", "")
        missing = []
        if not from_path:
            missing.append("from_path(上游节点路径)")
        if not to_path:
            missing.append("to_path(下游节点路径)")
        if missing:
            return {"success": False, "error": f"缺少必要参数: {', '.join(missing)}"}
        ok, msg = self.connect_nodes(from_path, to_path, args.get("input_index", 0))
        return {"success": ok, "result": msg if ok else "", "error": "" if ok else msg}

    def _tool_delete_node(self, args: Dict[str, Any]) -> Dict[str, Any]:
        node_path = args.get("node_path", "")
        if not node_path:
            return {"success": False, "error": "缺少 node_path 参数"}
        ok, msg, snapshot = self.delete_node_by_path(node_path)
        result = {"success": ok, "result": msg if ok else "", "error": "" if ok else msg}
        if ok and snapshot:
            result["_undo_snapshot"] = snapshot  # 供 UI 撤销使用，不会发给 AI
        return result

    def _tool_search_node_types(self, args: Dict[str, Any]) -> Dict[str, Any]:
        keyword = args.get("keyword", "")
        if not keyword:
            return {"success": False, "error": "缺少 keyword 参数"}
        ok, msg = self.search_nodes(keyword, args.get("limit", 10))
        return {"success": ok, "result": msg if ok else "", "error": "" if ok else msg}

    def _tool_semantic_search_nodes(self, args: Dict[str, Any]) -> Dict[str, Any]:
        description = args.get("description", "")
        if not description:
            return {"success": False, "error": "缺少 description 参数"}
        ok, msg = self.semantic_search_nodes(description, args.get("category", "sop"))
        return {"success": ok, "result": msg if ok else "", "error": "" if ok else msg}

    def _tool_list_children(self, args: Dict[str, Any]) -> Dict[str, Any]:
        network_path = args.get("network_path")
        recursive = args.get("recursive", False)
        page = int(args.get("page", 1))

        # 分页快速路径
        cache_key = f"list_children:{network_path or '_current'}:r={recursive}"
        if page > 1 and cache_key in self._tool_page_cache:
            np_arg = f'network_path="{network_path}", ' if network_path else ''
            hint = f'list_children({np_arg}recursive={recursive}, page={page})'
            return {"success": True, "result": self._paginate_tool_result(
                self._tool_page_cache[cache_key], cache_key, hint, page)}

        ok, msg = self.list_children(network_path, recursive, args.get("show_flags", True))
        if not ok:
            return {"success": False, "error": msg}

        np_arg = f'network_path="{network_path}", ' if network_path else ''
        hint = f'list_children({np_arg}recursive={recursive}, page={page})'
        return {"success": True, "result": self._paginate_tool_result(
            msg, cache_key, hint, page)}

    def _tool_get_geometry_info(self, args: Dict[str, Any]) -> Dict[str, Any]:
        node_path = args.get("node_path", "")
        if not node_path:
            return {"success": False, "error": "缺少 node_path 参数"}
        ok, msg = self.get_geometry_info(node_path, args.get("output_index", 0))
        return {"success": ok, "result": msg if ok else "", "error": "" if ok else msg}

    def _tool_read_selection(self, args: Dict[str, Any]) -> Dict[str, Any]:
        include_params = args.get("include_params", True)
        include_geometry = args.get("include_geometry", False)
        ok, msg = self.describe_selection(limit=5, include_all_params=include_params)
        if ok and include_geometry and hou:
            nodes = hou.selectedNodes()
            for node in nodes[:3]:
                geo_ok, geo_msg = self.get_geometry_info(node.path())
                if geo_ok:
                    msg += f"\n\n{geo_msg}"
        return {"success": ok, "result": msg if ok else "", "error": "" if ok else msg}

    def _tool_set_display_flag(self, args: Dict[str, Any]) -> Dict[str, Any]:
        node_path = args.get("node_path", "")
        if not node_path:
            return {"success": False, "error": "缺少 node_path 参数"}
        ok, msg = self.set_display_flag(
            node_path, args.get("display", True), args.get("render", True))
        return {"success": ok, "result": msg if ok else "", "error": "" if ok else msg}

    def _tool_copy_node(self, args: Dict[str, Any]) -> Dict[str, Any]:
        source_path = args.get("source_path", "")
        if not source_path:
            return {"success": False, "error": "缺少 source_path 参数"}
        ok, msg = self.copy_node(
            source_path, args.get("dest_network"), args.get("new_name"))
        return {"success": ok, "result": msg if ok else "", "error": "" if ok else msg}

    def _tool_batch_set_parameters(self, args: Dict[str, Any]) -> Dict[str, Any]:
        node_paths = args.get("node_paths", [])
        param_name = args.get("param_name", "")
        missing = []
        if not node_paths:
            missing.append("node_paths(节点路径列表)")
        if not param_name:
            missing.append("param_name(参数名)")
        if missing:
            return {"success": False, "error": f"缺少必要参数: {', '.join(missing)}"}
        ok, msg = self.batch_set_parameters(node_paths, param_name, args.get("value"))
        return {"success": ok, "result": msg if ok else "", "error": "" if ok else msg}

    def _tool_find_nodes_by_param(self, args: Dict[str, Any]) -> Dict[str, Any]:
        param_name = args.get("param_name", "")
        if not param_name:
            return {"success": False, "error": "缺少 param_name 参数"}
        ok, msg = self.find_nodes_by_param(
            param_name, args.get("value"),
            args.get("network_path"), args.get("recursive", True))
        return {"success": ok, "result": msg if ok else "", "error": "" if ok else msg}

    def _tool_save_hip(self, args: Dict[str, Any]) -> Dict[str, Any]:
        ok, msg = self.save_hip(args.get("file_path"))
        return {"success": ok, "result": msg if ok else "", "error": "" if ok else msg}

    def _tool_undo_redo(self, args: Dict[str, Any]) -> Dict[str, Any]:
        action = args.get("action", "")
        if not action:
            return {"success": False, "error": "缺少 action 参数"}
        ok, msg = self.undo_redo(action)
        return {"success": ok, "result": msg if ok else "", "error": "" if ok else msg}

    def _tool_execute_python(self, args: Dict[str, Any]) -> Dict[str, Any]:
        code = args.get("code", "")
        if not code:
            return {"success": False, "error": "缺少 code 参数"}
        page = int(args.get("page", 1))

        # 分页快速路径（只对成功的输出缓存）
        # 用 code 的 hash 作为缓存键，避免 key 过长
        import hashlib
        code_hash = hashlib.md5(code.encode()).hexdigest()[:12]
        cache_key = f"execute_python:{code_hash}"
        if page > 1 and cache_key in self._tool_page_cache:
            hint = f'execute_python(code="...同上...", page={page})'
            return {"success": True, "result": self._paginate_tool_result(
                self._tool_page_cache[cache_key], cache_key, hint, page)}

        # 安全检查：检测危险操作
        security_msg = self._check_code_security(code)
        if security_msg:
            return {"success": False, "error": security_msg}
        timeout = int(args.get("timeout", 30))
        ok, result = self.execute_python(code, timeout=timeout)
        if ok:
            output_parts = []
            if result.get("output"):
                output_parts.append(f"输出:\n{result['output']}")
            if result.get("return_value") is not None:
                output_parts.append(f"返回值: {result['return_value']}")
            output_parts.append(f"执行时间: {result['execution_time']:.3f}s")
            full_text = "\n".join(output_parts)

            hint = f'execute_python(code="...同上...", page={page})'
            return {"success": True, "result": self._paginate_tool_result(
                full_text, cache_key, hint, page)}
        # 失败：包含部分输出（如果有）+ 完整错误 + 执行时间
        error_parts = []
        partial_output = result.get("output", "")
        if partial_output:
            error_parts.append(f"[部分输出]\n{partial_output}")
        error_parts.append(result.get("error", "执行失败"))
        error_parts.append(f"执行时间: {result.get('execution_time', 0):.3f}s")
        return {"success": False, "error": "\n".join(error_parts), "result": partial_output}

    # ========================================
    # 系统 Shell 沙盒执行
    # ========================================

    # Shell 命令黑名单（正则，忽略大小写）
    _SHELL_DANGEROUS_PATTERNS = [
        # 文件/目录批量删除
        (r'\brm\s+.*-r', "禁止递归删除 (rm -r)"),
        (r'\brm\s+.*-f', "禁止强制删除 (rm -f)"),
        (r'\brmdir\s+/s', "禁止递归删除目录 (rmdir /s)"),
        (r'\bdel\s+/s', "禁止递归删除 (del /s)"),
        (r'\bdel\s+/q', "禁止静默删除 (del /q)"),
        (r'\brd\s+/s', "禁止递归删除 (rd /s)"),
        # 格式化
        (r'\bformat\s+[a-zA-Z]:', "禁止格式化磁盘"),
        # 注册表
        (r'\breg\s+(delete|add)', "禁止修改注册表"),
        # 关机/重启
        (r'\bshutdown\b', "禁止关机"),
        (r'\breboot\b', "禁止重启"),
        # 权限提升
        (r'\brunas\b', "禁止 runas 提权"),
        (r'\bsudo\b', "禁止 sudo 提权"),
        # 网络配置
        (r'\bnetsh\b', "禁止修改网络配置"),
        # 进程注入
        (r'\btaskkill\s+/f', "禁止强制结束进程"),
        # 危险 PowerShell
        (r'Remove-Item\s+.*-Recurse', "禁止 PowerShell 递归删除"),
        (r'Invoke-Expression', "禁止 Invoke-Expression"),
        (r'\biex\b', "禁止 iex (Invoke-Expression 别名)"),
        # 磁盘操作
        (r'\bdiskpart\b', "禁止 diskpart"),
        # fork bomb
        (r'%0\|%0', "禁止 fork bomb"),
        (r':\(\)\{.*\}', "禁止 fork bomb"),
    ]

    # 允许的命令前缀白名单（粗粒度，不在名单中的也可以执行，只有黑名单才拦截）
    # 这个白名单仅用于日志提示
    _SHELL_COMMON_COMMANDS = frozenset({
        'pip', 'python', 'git', 'dir', 'ls', 'cd', 'echo', 'type', 'cat',
        'where', 'which', 'whoami', 'hostname', 'ipconfig', 'ifconfig',
        'curl', 'wget', 'ffmpeg', 'ffprobe', 'magick', 'convert',
        'hython', 'hbatch', 'mantra', 'hcmd',
        'node', 'npm', 'npx', 'conda', 'env', 'set', 'tree',
        'find', 'grep', 'rg', 'awk', 'sed', 'head', 'tail', 'wc',
        'mkdir', 'copy', 'cp', 'move', 'mv', 'ren', 'rename',
        'tar', 'zip', 'unzip', '7z',
    })

    def _check_shell_security(self, command: str) -> Optional[str]:
        """检查 Shell 命令是否包含危险操作"""
        for pattern, msg in self._SHELL_DANGEROUS_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return f"安全拦截: {msg}\n命令: {command}\n如确需执行，请在系统终端中手动运行。"
        return None

    def _tool_execute_shell(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """在系统 Shell 中执行命令（沙盒环境）

        ★ v1.4.4 改进：使用 Popen + 轮询替代 subprocess.run
        - 支持用户通过停止按钮中断正在执行的命令
        - Windows 上正确杀死整个进程树（不只是 cmd.exe 父进程）
        - 防止 pipe buffer 满导致的死锁（使用 communicate 分块读取）
        """
        import subprocess
        import hashlib

        command = args.get("command", "").strip()
        if not command:
            return {"success": False, "error": "缺少 command 参数"}

        page = int(args.get("page", 1))
        timeout = min(int(args.get("timeout", 30)), 120)  # 最大 120 秒

        # 分页快速路径
        cmd_hash = hashlib.md5(command.encode()).hexdigest()[:12]
        cache_key = f"shell:{cmd_hash}"
        if page > 1 and cache_key in self._tool_page_cache:
            hint = f'execute_shell(command="...同上...", page={page})'
            return {"success": True, "result": self._paginate_tool_result(
                self._tool_page_cache[cache_key], cache_key, hint, page)}

        # 安全检查
        security_msg = self._check_shell_security(command)
        if security_msg:
            return {"success": False, "error": security_msg}

        # 工作目录
        cwd = args.get("cwd", "")
        if not cwd:
            # 默认：项目根目录
            cwd = str(Path(__file__).parent.parent.parent.parent)
        if not os.path.isdir(cwd):
            return {"success": False, "error": f"工作目录不存在: {cwd}"}

        # ★ 获取停止事件引用（从 AIClient 传入，用于检测用户中断）
        stop_event = getattr(self, '_stop_event', None)

        start_time = time.time()
        proc = None
        try:
            # 启动子进程（非阻塞）
            popen_kwargs = dict(
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
            )
            if sys.platform == 'win32':
                popen_kwargs.update(
                    encoding='utf-8',
                    errors='replace',
                    env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
                    creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
                )
            else:
                popen_kwargs.update(text=True)

            proc = subprocess.Popen(command, **popen_kwargs)

            # ★ 轮询等待：每 0.5s 检查一次停止标志和超时
            deadline = start_time + timeout
            while proc.poll() is None:
                # 检查用户中断
                if stop_event and stop_event.is_set():
                    self._kill_process_tree(proc)
                    elapsed = time.time() - start_time
                    return {"success": False, "error": f"命令被用户中断\n命令: {command}\n已运行: {elapsed:.1f}s"}

                # 检查超时
                if time.time() > deadline:
                    self._kill_process_tree(proc)
                    elapsed = time.time() - start_time
                    return {"success": False, "error": f"命令超时（{timeout}s 限制）\n命令: {command}\n耗时: {elapsed:.2f}s"}

                # 短暂等待避免 CPU 空转
                try:
                    proc.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    pass

            # 进程已结束，读取输出
            stdout, stderr = proc.communicate(timeout=5)
            elapsed = time.time() - start_time

            # 组装输出
            parts = []
            if stdout:
                parts.append(stdout.rstrip())
            if stderr:
                parts.append(f"[stderr]\n{stderr.rstrip()}")
            parts.append(f"[退出码: {proc.returncode}, 耗时: {elapsed:.2f}s]")
            full_text = "\n".join(parts)

            success = proc.returncode == 0
            hint = f'execute_shell(command="...同上...", page={page})'
            return {"success": success, "result": self._paginate_tool_result(
                full_text, cache_key, hint, page)}

        except Exception as e:
            if proc and proc.poll() is None:
                self._kill_process_tree(proc)
            return {"success": False, "error": f"Shell 执行失败: {e}"}

    @staticmethod
    def _kill_process_tree(proc):
        """杀死进程及其所有子进程

        Windows 上使用 taskkill /F /T 杀死整个进程树，
        避免只杀 cmd.exe 而子进程继续运行导致挂起。
        """
        import subprocess as _sp
        try:
            if sys.platform == 'win32':
                # /F = 强制  /T = 杀死整个进程树  /PID = 进程 ID
                _sp.run(
                    f'taskkill /F /T /PID {proc.pid}',
                    shell=True,
                    capture_output=True,
                    timeout=5,
                    creationflags=_sp.CREATE_NO_WINDOW,
                )
            else:
                import signal
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    # ========================================
    # 节点布局工具
    # ========================================

    def _tool_layout_nodes(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """布局节点 — 多策略自动整理节点位置"""
        from . import hou_core

        parent_path = args.get("network_path", "") or args.get("parent_path", "")
        if not parent_path:
            net = self._current_network()
            if net is not None:
                parent_path = net.path()

        node_paths = args.get("node_paths", None)
        if isinstance(node_paths, str):
            node_paths = [p.strip() for p in node_paths.split(",") if p.strip()]
        if node_paths is not None and len(node_paths) == 0:
            node_paths = None

        method = args.get("method", "auto")
        spacing = float(args.get("spacing", 1.0))

        ok, msg, positions = hou_core.layout_nodes(
            parent_path=parent_path,
            node_paths=node_paths,
            method=method,
            spacing=spacing,
        )
        if ok:
            # 构建可读的位置摘要
            lines = [msg]
            if positions and len(positions) <= 20:
                lines.append("节点位置:")
                for p in positions:
                    lines.append(f"  {p['path']}: ({p['x']}, {p['y']})")
            elif positions:
                lines.append(f"(共 {len(positions)} 个节点，仅显示前 10 个)")
                for p in positions[:10]:
                    lines.append(f"  {p['path']}: ({p['x']}, {p['y']})")
            return {"success": True, "result": "\n".join(lines)}
        return {"success": False, "error": msg}

    def _tool_get_node_positions(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """获取节点位置信息"""
        from . import hou_core

        parent_path = args.get("network_path", "") or args.get("parent_path", "")
        if not parent_path:
            net = self._current_network()
            if net is not None:
                parent_path = net.path()

        node_paths = args.get("node_paths", None)
        if isinstance(node_paths, str):
            node_paths = [p.strip() for p in node_paths.split(",") if p.strip()]
        if node_paths is not None and len(node_paths) == 0:
            node_paths = None

        ok, msg, positions = hou_core.get_node_positions(
            parent_path=parent_path,
            node_paths=node_paths,
        )
        if ok:
            lines = [msg]
            for p in positions:
                lines.append(f"  {p['path']} ({p['type']}): ({p['x']}, {p['y']})")
            return {"success": True, "result": "\n".join(lines)}
        return {"success": False, "error": msg}

    # ========================================
    # NetworkBox 操作
    # ========================================

    def _tool_create_network_box(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """创建 NetworkBox 并可选地将节点加入其中"""
        from . import hou_core

        parent_path = args.get("parent_path", "")
        if not parent_path:
            # 默认使用当前网络
            net = self._current_network()
            if net is None:
                return {"success": False, "error": "未找到当前网络，请指定 parent_path"}
            parent_path = net.path()

        name = args.get("name", "")
        comment = args.get("comment", "")
        color_preset = args.get("color_preset", "")
        node_paths = args.get("node_paths", [])
        if isinstance(node_paths, str):
            node_paths = [p.strip() for p in node_paths.split(",") if p.strip()]

        ok, msg, box = hou_core.create_network_box(
            parent_path, name, comment, color_preset, node_paths
        )
        if ok:
            result_data = {"box_name": box.name() if box else name, "message": msg}
            return {"success": True, "result": msg}
        return {"success": False, "error": msg}

    def _tool_add_nodes_to_box(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """将节点添加到已有的 NetworkBox"""
        from . import hou_core

        parent_path = args.get("parent_path", "")
        if not parent_path:
            net = self._current_network()
            if net is None:
                return {"success": False, "error": "未找到当前网络，请指定 parent_path"}
            parent_path = net.path()

        box_name = args.get("box_name", "")
        if not box_name:
            return {"success": False, "error": "缺少 box_name 参数"}

        node_paths = args.get("node_paths", [])
        if isinstance(node_paths, str):
            node_paths = [p.strip() for p in node_paths.split(",") if p.strip()]
        if not node_paths:
            return {"success": False, "error": "缺少 node_paths 参数"}

        auto_fit = args.get("auto_fit", True)
        ok, msg = hou_core.add_nodes_to_box(parent_path, box_name, node_paths, auto_fit)
        return {"success": ok, "result": msg if ok else "", "error": "" if ok else msg}

    def _tool_list_network_boxes(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """列出网络中所有 NetworkBox 及其内容"""
        from . import hou_core

        parent_path = args.get("parent_path", "")
        if not parent_path:
            net = self._current_network()
            if net is None:
                return {"success": False, "error": "未找到当前网络，请指定 parent_path"}
            parent_path = net.path()

        ok, msg, boxes_info = hou_core.list_network_boxes(parent_path)
        if ok:
            if not boxes_info:
                return {"success": True, "result": f"{parent_path} 中没有 NetworkBox"}
            lines = [f"{parent_path} 中有 {len(boxes_info)} 个 NetworkBox:\n"]
            for box in boxes_info:
                status = "📦" if not box["minimized"] else "📦(折叠)"
                lines.append(f"{status} {box['name']}: {box['comment'] or '(无注释)'}")
                lines.append(f"   包含 {box['node_count']} 个节点: {', '.join(box['nodes'][:10])}")
                if box['node_count'] > 10:
                    lines.append(f"   ...及另外 {box['node_count'] - 10} 个节点")
            return {"success": True, "result": "\n".join(lines)}
        return {"success": False, "error": msg}

    # ========================================
    # Skill 系统
    # ========================================

    def _tool_list_skills(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """列出所有可用 Skill"""
        if not HAS_SKILLS or _list_skills is None:
            return {"success": False, "error": "Skill 系统未加载"}
        try:
            skills = _list_skills()
            if not skills:
                return {"success": True, "result": "当前没有可用的 Skill。"}
            lines = [f"可用 Skill ({len(skills)} 个):\n"]
            for s in skills:
                lines.append(f"### {s['name']}")
                lines.append(f"  {s.get('description', '')}")
                params = s.get('parameters', {})
                if params:
                    lines.append("  参数:")
                    for pname, pinfo in params.items():
                        req = " (必填)" if pinfo.get('required') else ""
                        lines.append(f"    - {pname}: {pinfo.get('description', '')}{req}")
                lines.append("")
            return {"success": True, "result": "\n".join(lines)}
        except Exception as e:
            return {"success": False, "error": f"列出 Skill 失败: {e}"}

    def _tool_run_skill(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """执行指定 Skill"""
        if not HAS_SKILLS or _run_skill is None:
            return {"success": False, "error": "Skill 系统未加载"}

        skill_name = args.get("skill_name", "")
        if not skill_name:
            return {"success": False, "error": "缺少 skill_name 参数"}

        params = args.get("params", {})
        if not isinstance(params, dict):
            try:
                params = json.loads(str(params))
            except Exception:
                return {"success": False, "error": "params 必须是 JSON 对象"}

        try:
            result = _run_skill(skill_name, params)
            if "error" in result:
                return {"success": False, "error": result["error"]}

            # 格式化输出
            import json as _json
            formatted = _json.dumps(result, ensure_ascii=False, indent=2)
            return {"success": True, "result": formatted}
        except Exception as e:
            import traceback
            return {"success": False, "error": f"Skill 执行异常: {e}\n{traceback.format_exc()[:500]}"}

    def _tool_check_errors(self, args: Dict[str, Any]) -> Dict[str, Any]:
        ok, text = self.check_node_errors_text(args.get("node_path"))
        return {"success": ok, "result": text if ok else "", "error": "" if ok else text}

    def _tool_search_local_doc(self, args: Dict[str, Any]) -> Dict[str, Any]:
        if not HAS_DOC_RAG:
            return {"success": False, "error": "DocIndex 模块未加载"}
        query = args.get("query", "")
        if not query:
            return {"success": False, "error": "缺少 query 参数"}
        try:
            index = get_doc_rag()
            results = index.search(query, top_k=min(args.get("top_k", 5), 10))
            if not results:
                return {"success": True, "result": f"未找到与 '{query}' 相关的文档"}
            parts = [f"找到 {len(results)} 个相关条目:\n"]
            for idx, r in enumerate(results, 1):
                parts.append(f"{idx}. [{r['type'].upper()}] {r['name']} (score={r['score']:.1f})")
                parts.append(f"   {r['snippet']}\n")
            return {"success": True, "result": "\n".join(parts)}
        except Exception as e:
            import traceback
            return {"success": False, "error": f"文档检索失败: {e}\n{traceback.format_exc()}"}

    def _tool_get_houdini_node_doc(self, args: Dict[str, Any]) -> Dict[str, Any]:
        node_type = args.get("node_type", "")
        if not node_type:
            return {"success": False, "error": "缺少 node_type 参数"}
        page = int(args.get("page", 1))
        ok, doc_text = self._get_houdini_local_doc(node_type, args.get("category", "sop"), page)
        return {"success": ok, "result": doc_text if ok else "", "error": "" if ok else doc_text}

    def _tool_get_node_inputs(self, args: Dict[str, Any]) -> Dict[str, Any]:
        node_type = args.get("node_type", "")
        if not node_type:
            return {"success": False, "error": "缺少 node_type 参数"}
        ok, info = self.get_node_input_info(node_type, args.get("category", "sop"))
        return {"success": ok, "result": info if ok else "", "error": "" if ok else info}

    # ========================================
    # 性能分析 (perfMon) 工具
    # ========================================

    def _tool_perf_start_profile(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """启动 hou.perfMon 性能 profile"""
        if hou is None:
            return {"success": False, "error": "Houdini 环境不可用"}

        title = args.get("title", "AI Performance Analysis")
        force_cook_node = args.get("force_cook_node", "")

        # 如果已有活跃 profile，先停止旧的
        if self._active_perf_profile is not None:
            try:
                self._active_perf_profile.stop()
            except Exception:
                pass
            self._active_perf_profile = None

        try:
            profile = hou.perfMon.startProfile(title)
            self._active_perf_profile = profile
        except Exception as e:
            return {"success": False, "error": f"启动 perfMon profile 失败: {e}"}

        result_msg = f"已启动性能 profile: {title}"

        # 可选：启动后立即强制 cook 指定节点
        if force_cook_node:
            node = hou.node(force_cook_node)
            if node:
                try:
                    node.cook(force=True)
                    result_msg += f"\n已强制 cook 节点: {force_cook_node}"
                except Exception as e:
                    result_msg += f"\n强制 cook {force_cook_node} 失败: {e}"
            else:
                result_msg += f"\n警告: 节点 {force_cook_node} 不存在，跳过 cook"

        result_msg += "\n提示: 完成操作后调用 perf_stop_and_report 获取分析报告。"
        return {"success": True, "result": result_msg}

    def _tool_perf_stop_and_report(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """停止 perfMon profile 并返回分析报告"""
        if hou is None:
            return {"success": False, "error": "Houdini 环境不可用"}

        if self._active_perf_profile is None:
            return {"success": False, "error": "没有活跃的性能 profile。请先调用 perf_start_profile 启动。"}

        save_path = args.get("save_path", "")

        profile = self._active_perf_profile
        self._active_perf_profile = None

        try:
            profile.stop()
        except Exception as e:
            return {"success": False, "error": f"停止 profile 失败: {e}"}

        # 获取统计数据
        stats_data = None
        try:
            stats_data = profile.stats()
        except Exception as e:
            return {"success": False, "error": f"获取 profile 统计数据失败: {e}"}

        # 可选：保存到磁盘
        save_msg = ""
        if save_path:
            try:
                hou.perfMon.saveProfile(profile, save_path)
                save_msg = f"\n已保存 profile 到: {save_path}"
            except Exception as e:
                save_msg = f"\n保存 profile 失败: {e}"

        # 解析统计数据，提取关键指标
        report_parts = ["=== 性能分析报告 ==="]

        if isinstance(stats_data, dict):
            # 尝试提取 cook 事件统计
            cook_stats = stats_data.get("cookStats", stats_data.get("cook_stats", {}))
            script_stats = stats_data.get("scriptStats", stats_data.get("script_stats", {}))
            memory_stats = stats_data.get("memoryStats", stats_data.get("memory_stats", {}))

            if cook_stats:
                report_parts.append("\n--- Cook 统计 ---")
                # 解析节点 cook 时间
                node_times = []
                if isinstance(cook_stats, dict):
                    for key, val in cook_stats.items():
                        if isinstance(val, dict):
                            t = val.get("time", val.get("selfTime", 0))
                            node_times.append((key, t))
                        elif isinstance(val, (int, float)):
                            node_times.append((key, val))
                node_times.sort(key=lambda x: x[1], reverse=True)
                for name, t in node_times[:15]:
                    report_parts.append(f"  {name}: {t:.2f}ms")
                if len(node_times) > 15:
                    report_parts.append(f"  ... 还有 {len(node_times) - 15} 个条目")

            if script_stats:
                report_parts.append("\n--- 脚本统计 ---")
                if isinstance(script_stats, dict):
                    for key, val in list(script_stats.items())[:10]:
                        report_parts.append(f"  {key}: {val}")

            if memory_stats:
                report_parts.append("\n--- 内存统计 ---")
                if isinstance(memory_stats, dict):
                    for key, val in list(memory_stats.items())[:10]:
                        report_parts.append(f"  {key}: {val}")

            if not cook_stats and not script_stats and not memory_stats:
                # 统计格式未知，输出原始数据的摘要
                import json as _json
                raw = _json.dumps(stats_data, indent=2, default=str, ensure_ascii=False)
                if len(raw) > 2000:
                    raw = raw[:2000] + "\n... (truncated)"
                report_parts.append("\n--- 原始统计数据 ---")
                report_parts.append(raw)
        elif isinstance(stats_data, str):
            report_parts.append(stats_data[:3000])
        else:
            report_parts.append(f"统计数据类型: {type(stats_data).__name__}")
            report_parts.append(str(stats_data)[:3000])

        if save_msg:
            report_parts.append(save_msg)

        full_report = "\n".join(report_parts)

        # 使用分页返回
        page = int(args.get("page", 1))
        cache_key = "perf_stop_and_report:latest"
        hint = f'perf_stop_and_report(page={page})'
        return {"success": True, "result": self._paginate_tool_result(
            full_report, cache_key, hint, page)}

    # ========================================
    # 工具分派表 & 用法提示 & 安全检查
    # ========================================

    # 工具用法提示：参数缺失或调用出错时附带正确调用方式
    _TOOL_USAGE: Dict[str, str] = {
        "get_network_structure": 'get_network_structure(network_path="/obj/geo1", page=1)',
        "get_node_parameters": 'get_node_parameters(node_path="/obj/geo1/box1", page=1)',
        "set_node_parameter": 'set_node_parameter(node_path="/obj/geo1/box1", param_name="sizex", value=2.0)',
        "create_node": 'create_node(parent_path="/obj/geo1", node_type="box", node_name="box1")',
        "create_nodes_batch": 'create_nodes_batch(parent_path="/obj/geo1", nodes=[{"type":"box","name":"box1"},...])',
        "create_wrangle_node": 'create_wrangle_node(parent_path="/obj/geo1", code="@P.y += 1;", name="my_wrangle")',
        "connect_nodes": 'connect_nodes(from_path="/obj/geo1/box1", to_path="/obj/geo1/merge1", input_index=0)',
        "delete_node": 'delete_node(node_path="/obj/geo1/box1")',
        "search_node_types": 'search_node_types(keyword="scatter", category="sop")',
        "semantic_search_nodes": 'semantic_search_nodes(query="随机散布点", category="sop")',
        "list_children": 'list_children(path="/obj/geo1", page=1)',
        "read_selection": 'read_selection()',
        "set_display_flag": 'set_display_flag(node_path="/obj/geo1/box1")',
        "copy_node": 'copy_node(source_path="/obj/geo1/box1", dest_parent="/obj/geo1", new_name="box1_copy")',
        "batch_set_parameters": 'batch_set_parameters(node_path="/obj/geo1/box1", parameters={"sizex":2,"sizey":3})',
        "find_nodes_by_param": 'find_nodes_by_param(network_path="/obj/geo1", param_name="file", param_value="*.bgeo")',
        "save_hip": 'save_hip(file_path="C:/path/to/file.hip")',
        "undo_redo": 'undo_redo(action="undo")',
        "execute_python": 'execute_python(code="import hou; print(hou.node(\\"/obj\\").children())")',
        "execute_shell": 'execute_shell(command="pip list", cwd="C:/project", timeout=30)',
        "check_errors": 'check_errors(node_path="/obj/geo1/box1")',
        "search_local_doc": 'search_local_doc(keyword="scatter")',
        "get_houdini_node_doc": 'get_houdini_node_doc(node_type="scatter", page=1)',
        "get_node_inputs": 'get_node_inputs(node_type="copytopoints", category="sop")',
        "run_skill": 'run_skill(skill_name="analyze_geometry_attribs", params={"node_path":"/obj/geo1/box1"})',
        "list_skills": 'list_skills()',
        # 节点布局
        "layout_nodes": 'layout_nodes(network_path="/obj/geo1", method="auto")',
        "get_node_positions": 'get_node_positions(network_path="/obj/geo1")',
        # NetworkBox
        "create_network_box": 'create_network_box(parent_path="/obj/geo1", name="input_stage", comment="数据输入", color_preset="input", node_paths=["/obj/geo1/box1"])',
        "add_nodes_to_box": 'add_nodes_to_box(parent_path="/obj/geo1", box_name="input_stage", node_paths=["/obj/geo1/box1"])',
        "list_network_boxes": 'list_network_boxes(parent_path="/obj/geo1")',
        # PerfMon 性能分析
        "perf_start_profile": 'perf_start_profile(title="Cook Analysis", force_cook_node="/obj/geo1/output0")',
        "perf_stop_and_report": 'perf_stop_and_report(save_path="C:/tmp/profile.hperf")',
    }

    # 工具名称 -> 处理方法名的映射表
    _TOOL_DISPATCH: Dict[str, str] = {
        "create_wrangle_node": "_tool_create_wrangle_node",
        "get_network_structure": "_tool_get_network_structure",
        "get_node_parameters": "_tool_get_node_parameters",
        "set_node_parameter": "_tool_set_node_parameter",
        "create_node": "_tool_create_node",
        "create_nodes_batch": "_tool_create_nodes_batch",
        "connect_nodes": "_tool_connect_nodes",
        "delete_node": "_tool_delete_node",
        "search_node_types": "_tool_search_node_types",
        "semantic_search_nodes": "_tool_semantic_search_nodes",
        "list_children": "_tool_list_children",
        # "get_geometry_info" 已移除，由 skill 替代
        "read_selection": "_tool_read_selection",
        "set_display_flag": "_tool_set_display_flag",
        "copy_node": "_tool_copy_node",
        "batch_set_parameters": "_tool_batch_set_parameters",
        "find_nodes_by_param": "_tool_find_nodes_by_param",
        "save_hip": "_tool_save_hip",
        "undo_redo": "_tool_undo_redo",
        "execute_python": "_tool_execute_python",
        "execute_shell": "_tool_execute_shell",
        "check_errors": "_tool_check_errors",
        "search_local_doc": "_tool_search_local_doc",
        "get_houdini_node_doc": "_tool_get_houdini_node_doc",
        "get_node_inputs": "_tool_get_node_inputs",
        "run_skill": "_tool_run_skill",
        "list_skills": "_tool_list_skills",
        # 节点布局
        "layout_nodes": "_tool_layout_nodes",
        "get_node_positions": "_tool_get_node_positions",
        # NetworkBox
        "create_network_box": "_tool_create_network_box",
        "add_nodes_to_box": "_tool_add_nodes_to_box",
        "list_network_boxes": "_tool_list_network_boxes",
        # PerfMon 性能分析
        "perf_start_profile": "_tool_perf_start_profile",
        "perf_stop_and_report": "_tool_perf_stop_and_report",
        # 长期记忆主动搜索
        "search_memory": "_tool_search_memory",
        # 视口截图
        "capture_viewport": "_tool_capture_viewport",
    }

    # Python 代码安全黑名单
    _DANGEROUS_PATTERNS = [
        (r'\bos\.remove\b', "禁止使用 os.remove 删除文件"),
        (r'\bos\.rmdir\b', "禁止使用 os.rmdir 删除目录"),
        (r'\bshutil\.rmtree\b', "禁止使用 shutil.rmtree 递归删除"),
        (r'\bos\.system\b', "禁止使用 os.system 执行系统命令"),
        (r'\bsubprocess\b', "禁止使用 subprocess 执行外部进程"),
        (r'\b__import__\b', "禁止使用 __import__ 动态导入"),
        (r'\bopen\s*\([^)]*["\']w["\']', "禁止以写入模式打开文件（可用读取模式）"),
        (r'\bhou\.exit\b', "禁止使用 hou.exit 退出 Houdini"),
        (r'\bhou\.hipFile\.clear\b', "禁止使用 hou.hipFile.clear 清空场景"),
    ]

    def _check_code_security(self, code: str) -> Optional[str]:
        """检查代码是否包含危险操作，返回警告消息或 None"""
        for pattern, msg in self._DANGEROUS_PATTERNS:
            if re.search(pattern, code):
                return f"⛔ 安全拦截: {msg}\n如确需执行，请在 Houdini Python Shell 中手动运行。"
        return None

    # 这些工具出错时应提示 AI 先查阅文档再重试，不要盲目重试
    _DOC_CHECK_TOOLS: frozenset = frozenset({
        'create_node',
        'create_nodes_batch',
        'create_wrangle_node',
        'set_node_parameter',
        'batch_set_parameters',
        'connect_nodes',
    })

    def _append_usage_hint(self, tool_name: str, error_msg: str) -> str:
        """在错误消息末尾附加工具的正确调用方式，以及查阅文档的建议"""
        parts = [error_msg]

        usage = self._TOOL_USAGE.get(tool_name)
        if usage:
            parts.append(f"正确调用方式: {usage}")

        # 节点创建/参数设置类工具出错 → 强烈建议查阅文档再重试
        if tool_name in self._DOC_CHECK_TOOLS:
            parts.append(
                "⚠️ 请不要盲目重试！先通过以下方式确认正确信息再重新调用:\n"
                "  1. search_node_types(keyword=\"...\") — 搜索正确的节点类型名\n"
                "  2. get_houdini_node_doc(node_type=\"...\") — 查阅该节点的参数文档\n"
                "  3. get_node_parameters(node_path=\"...\") — 查看已有节点的实际参数名和当前值\n"
                "确认节点类型名、参数名、参数值类型无误后，再重新调用本工具。"
            )

        return "\n\n".join(parts)

    def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """执行工具调用 - AI Agent 的统一工具入口（基于分派表）

        Args:
            tool_name: 工具名称
            arguments: 工具参数

        Returns:
            {"success": bool, "result": str, "error": str}
        """
        print(f"[MCP Client] 执行工具: {tool_name}, 参数: {list(arguments.keys())}")

        # ★ Hook: on_before_tool — 允许插件拦截/审计/修改参数
        try:
            from ..hooks import get_hook_manager as _ghm
            _hm = _ghm()
            _hm.fire('on_before_tool', tool_name=tool_name, args=arguments)
        except Exception:
            pass

        handler_name = self._TOOL_DISPATCH.get(tool_name)

        # ★ 如果内部分派表中不存在，尝试外部工具（HookManager + ToolRegistry）
        if handler_name is None:
            try:
                from ..hooks import get_hook_manager as _ghm
                _hm = _ghm()
                if _hm.has_external_tool(tool_name):
                    result = _hm.execute_external_tool(tool_name, arguments)
                    # ★ Hook: on_after_tool
                    try:
                        _hm.fire('on_after_tool', tool_name=tool_name, args=arguments, result=result)
                    except Exception:
                        pass
                    return result
            except Exception:
                pass
            # ★ 尝试 ToolRegistry（Skill 工具以 skill: 前缀注册）
            try:
                from ..tool_registry import get_tool_registry
                _reg = get_tool_registry()
                if _reg.has_tool(tool_name):
                    _handler = _reg.get_handler(tool_name)
                    if _handler:
                        result = _handler(arguments)
                        if not isinstance(result, dict):
                            result = {"success": True, "result": str(result)}
                        try:
                            _ghm_inst = _ghm()
                            _ghm_inst.fire('on_after_tool', tool_name=tool_name, args=arguments, result=result)
                        except Exception:
                            pass
                        return result
            except Exception:
                pass
            return self._tool_unknown(tool_name)

        handler = getattr(self, handler_name, None)
        if handler is None:
            return {"success": False, "error": f"工具处理器未实现: {handler_name}"}

        try:
            result = handler(arguments)
            # 工具返回失败时，自动附加用法提示
            if not result.get("success") and result.get("error"):
                result["error"] = self._append_usage_hint(tool_name, result["error"])
            # ★ Hook: on_after_tool — 通知插件工具执行完成
            try:
                from ..hooks import get_hook_manager as _ghm
                _ghm().fire('on_after_tool', tool_name=tool_name, args=arguments, result=result)
            except Exception:
                pass
            return result
        except Exception as e:
            import traceback
            print(f"[MCP Client] 工具执行异常: {traceback.format_exc()}")
            err = f"工具 {tool_name} 执行异常: {str(e)}"
            return {"success": False, "error": self._append_usage_hint(tool_name, err)}

    # ========================================
    # 长期记忆主动搜索
    # ========================================

    def _tool_search_memory(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """搜索长期记忆库 — 跨层级 chunk 检索"""
        query = args.get("query", "")
        print(f"[search_memory] 收到搜索请求: query={query!r}, args={args}")
        # 长期记忆与工作流经验沉淀始终开启。保留此段只用于覆盖旧版本
        # 曾经写入的 disabled 设置，避免 search_memory 被旧状态误拦截。
        try:
            from houdini_agent.qt_compat import QSettings
            _s = QSettings("HoudiniAI", "Assistant")
            _s.setValue("memory_enabled", True)
        except Exception:
            pass
        if not query:
            return {"success": False, "error": "query 参数不能为空"}

        category = args.get("category")
        top_k = min(max(args.get("top_k", 5), 1), 10)

        try:
            from ..memory_store import get_memory_store, ABSTRACTION_LEVELS
            store = get_memory_store()
            total = store.count_semantic()
            print(f"[search_memory] 记忆库中有 {total} 条语义记忆")

            results = store.search_all_levels(
                query=query,
                category=category,
                top_k=top_k,
                min_confidence=0.1,
            )
            print(f"[search_memory] 搜索结果: {len(results)} 条")

            if not results:
                return {
                    "success": True,
                    "count": 0,
                    "memories": [],
                    "message": f"未找到相关记忆（库中共 {total} 条语义记忆，min_confidence=0.1）",
                }

            memories = []
            for rec, score in results:
                level_name = ABSTRACTION_LEVELS.get(rec.abstraction_level, "unknown")
                memories.append({
                    "rule": rec.rule,
                    "category": rec.category,
                    "abstraction_level": rec.abstraction_level,
                    "level_name": level_name,
                    "confidence": round(rec.confidence, 2),
                    "relevance": round(score, 3),
                    "activation_count": rec.activation_count,
                })

            # 更新激活计数
            for rec, _ in results:
                try:
                    store.increment_semantic_activation(rec.id)
                except Exception:
                    pass

            return {
                "success": True,
                "count": len(memories),
                "query": query,
                "category_filter": category,
                "memories": memories,
            }

        except Exception as e:
            return {"success": False, "error": f"记忆搜索失败: {str(e)}"}

    # ========================================
    # 视口截图
    # ========================================

    def _tool_capture_viewport(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """截取当前 Houdini 3D 视口的快照，返回 base64 编码的图片。

        使用 flipbook 机制截取当前帧的单帧图片，供 AI 视觉分析节点运行结果。
        ★ 必须在主线程执行（涉及 hou UI 操作）。
        """
        if hou is None:
            return {"success": False, "error": "Houdini 环境不可用"}

        width = args.get("width", 960)
        height = args.get("height", 540)
        output_path = args.get("output_path", "")
        # 限制分辨率范围
        width = max(160, min(width, 1920))
        height = max(120, min(height, 1080))

        try:
            import tempfile
            import base64

            # 获取 Scene Viewer
            viewer = None
            try:
                desktop = hou.ui.curDesktop()
                if desktop:
                    viewer = desktop.paneTabOfType(hou.paneTabType.SceneViewer)
            except Exception:
                pass

            if viewer is None:
                try:
                    viewer = hou.ui.paneTabOfType(hou.paneTabType.SceneViewer)
                except Exception:
                    pass

            if viewer is None:
                return {"success": False, "error": "找不到 Scene Viewer 面板，请确保有打开的 3D 视口"}

            # 获取当前帧
            current_frame = int(hou.frame())

            # 生成临时文件路径
            tmp_dir = tempfile.gettempdir()
            tmp_file = os.path.join(tmp_dir, f"houdini_viewport_{int(time.time() * 1000)}.jpg")

            # 使用 flipbook 截取单帧
            try:
                flip_settings = viewer.flipbookSettings().stash()
                flip_settings.output(tmp_file)
                flip_settings.frameRange((current_frame, current_frame))
                flip_settings.resolution((width, height))
                flip_settings.outputToMPlay(False)

                # 执行单帧截图
                viewport = viewer.curViewport()
                viewer.flipbook(viewport, flip_settings)
            except Exception as e:
                # 某些 Houdini 版本可能不支持 flipbook API
                return {"success": False, "error": f"Flipbook 截图失败: {e}"}

            # 读取生成的图片
            if not os.path.exists(tmp_file):
                # flipbook 可能使用帧号作为文件名后缀
                import glob
                pattern = tmp_file.replace('.jpg', '*.jpg')
                candidates = sorted(glob.glob(pattern))
                if candidates:
                    tmp_file = candidates[0]
                else:
                    return {"success": False, "error": "截图文件未生成，请检查视口状态"}

            # 读取并编码
            with open(tmp_file, 'rb') as f:
                img_bytes = f.read()

            if len(img_bytes) == 0:
                return {"success": False, "error": "截图文件为空"}

            b64_data = base64.b64encode(img_bytes).decode('utf-8')

            # 清理临时文件
            try:
                os.remove(tmp_file)
            except Exception:
                pass

            # 获取视口信息
            viewport_name = ""
            try:
                viewport_name = viewer.curViewport().name()
            except Exception:
                pass

            cam_info = ""
            try:
                vp = viewer.curViewport()
                cam = vp.camera()
                if cam:
                    cam_info = f", camera={cam.path()}"
            except Exception:
                pass

            size_kb = len(img_bytes) / 1024

            result_msg = (
                f"已截取视口快照: {width}x{height}, frame={current_frame}, "
                f"viewport={viewport_name}{cam_info}, "
                f"size={size_kb:.1f}KB"
            )

            # 如果指定了 output_path，保存到文件
            if output_path:
                try:
                    # 支持 $HIP 等 Houdini 变量展开
                    expanded_path = hou.text.expandString(output_path) if hasattr(hou, 'text') else output_path
                    save_dir = os.path.dirname(expanded_path)
                    if save_dir and not os.path.exists(save_dir):
                        os.makedirs(save_dir, exist_ok=True)
                    with open(expanded_path, 'wb') as f:
                        f.write(img_bytes)
                    result_msg += f"\n截图已保存到: {expanded_path}"
                except Exception as e:
                    result_msg += f"\n保存到 {output_path} 失败: {e}"

            return {
                "success": True,
                "result": result_msg,
                # ★ 特殊字段：包含 base64 图片数据，
                # agent_loop_stream 中检测到此字段会将图片注入消息
                "_viewport_image": b64_data,
                "_image_media_type": "image/jpeg",
            }

        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"success": False, "error": f"视口截图失败: {str(e)}"}

    def _tool_unknown(self, tool_name: str) -> Dict[str, Any]:
        """处理未知工具名称，提供建议"""
        available = list(self._TOOL_DISPATCH.keys())
        error_msg = f"工具不存在: {tool_name}"
        similar = [t for t in available
                   if tool_name.lower() in t.lower() or t.lower() in tool_name.lower()]
        if similar:
            error_msg += f"\n建议的工具: {', '.join(similar[:3])}"
        else:
            error_msg += f"\n可用工具: {', '.join(available[:8])}..."
        error_msg += f"\n请使用正确的工具名称，不要重复调用不存在的工具。"
        return {"success": False, "error": error_msg}
