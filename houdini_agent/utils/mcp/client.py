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
    sys.path.insert(0, _lib_path)

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


class HoudiniMCP:
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
    # 网络结构读取（轻量级，只返回拓扑信息）
    # ========================================
    
    def get_network_structure(self, network_path: Optional[str] = None) -> Tuple[bool, Dict[str, Any]]:
        """获取节点网络的拓扑结构（节点名称、类型、连接关系）
        
        这是一个轻量级操作，不读取参数详情。
        
        Args:
            network_path: 网络路径，如 '/obj/geo1'。None 则使用当前网络。
        
        Returns:
            (success, data) 其中 data 包含:
            {
                "network_path": str,
                "network_type": str,
                "nodes": [
                    {
                        "name": str,
                        "path": str,
                        "type": str,
                        "type_label": str,
                        "is_displayed": bool,
                        "has_errors": bool,
                        "position": [x, y]
                    }
                ],
                "connections": [
                    {
                        "from": str,  # 源节点路径
                        "to": str,    # 目标节点路径
                        "input_index": int,
                        "input_label": str  # 输入端口名称（如有）
                    }
                ]
            }
        """
        if hou is None:
            return False, {"error": "未检测到 Houdini API（hou 模块）"}
        
        # 获取网络节点
        if network_path:
            network = hou.node(network_path)
            if network is None:
                return False, {"error": f"未找到网络: {network_path}"}
        else:
            network = self._current_network()
            if network is None:
                return False, {"error": "未找到当前网络，请打开网络编辑器"}
        
        nodes_data = []
        connections_data = []
        
        try:
            children = network.children()
            
            for node in children:
                try:
                    node_type = node.type()
                    category = node_type.category().name() if node_type else "Unknown"
                    type_name = node_type.name() if node_type else "unknown"
                    
                    # 获取位置
                    pos = node.position()
                    position = [pos[0], pos[1]] if pos else [0, 0]
                    
                    # 检查是否有错误
                    has_errors = False
                    try:
                        errors = node.errors()
                        has_errors = bool(errors)
                    except Exception:
                        pass
                    
                    node_info = {
                        "name": node.name(),
                        "path": node.path(),
                        "type": f"{category.lower()}/{type_name}",
                        "type_label": node_type.description() if node_type else "",
                        "is_displayed": node.isDisplayFlagSet() if hasattr(node, 'isDisplayFlagSet') else False,
                        "has_errors": has_errors,
                        "position": position
                    }
                    
                    # 检测 wrangle 类型节点，提取 VEX 代码
                    _wrangle_keywords = ('wrangle', 'snippet', 'vopnet')
                    if any(kw in type_name.lower() for kw in _wrangle_keywords):
                        try:
                            snippet = node.parm("snippet")
                            if snippet:
                                code = snippet.eval()
                                if code and code.strip():
                                    node_info["vex_code"] = code.strip()
                        except Exception:
                            pass
                    # 也检测 python 脚本节点
                    if 'python' in type_name.lower():
                        try:
                            for pname in ("python", "code", "script"):
                                parm = node.parm(pname)
                                if parm:
                                    code = parm.eval()
                                    if code and code.strip():
                                        node_info["python_code"] = code.strip()
                                        break
                        except Exception:
                            pass
                    
                    nodes_data.append(node_info)
                    
                    # 收集连接关系（含输入端口名称）
                    for input_idx, input_node in enumerate(node.inputs()):
                        if input_node is not None:
                            conn_info = {
                                "from": input_node.path(),
                                "to": node.path(),
                                "input_index": input_idx,
                            }
                            # 尝试获取输入端口标签
                            try:
                                input_label = node_type.inputLabel(input_idx)
                                if input_label:
                                    conn_info["input_label"] = input_label
                            except Exception:
                                pass
                            connections_data.append(conn_info)
                except Exception:
                    continue
            
            # 收集 NetworkBox 信息
            boxed_node_paths = set()
            boxes_data = []
            try:
                for box in network.networkBoxes():
                    box_nodes = box.nodes()
                    box_node_paths = [n.path() for n in box_nodes]
                    boxed_node_paths.update(box_node_paths)
                    boxes_data.append({
                        "name": box.name(),
                        "comment": box.comment() or "",
                        "node_count": len(box_nodes),
                        "nodes": box_node_paths,
                    })
            except Exception:
                pass  # networkBoxes() 可能在某些网络类型下不可用

            return True, {
                "network_path": network.path(),
                "network_type": network.type().name() if network.type() else "unknown",
                "node_count": len(nodes_data),
                "nodes": nodes_data,
                "connections": connections_data,
                "network_boxes": boxes_data,
                "boxed_node_paths": list(boxed_node_paths),
            }
        except Exception as e:
            return False, {"error": f"读取网络结构失败: {str(e)}"}

    def get_network_structure_text(self, network_path: Optional[str] = None,
                                   box_name: Optional[str] = None) -> Tuple[bool, str]:
        """获取节点网络结构的文本描述（适合 AI 阅读）
        
        三种模式：
        1. 无 box_name 且网络有 NetworkBox → 概览模式（折叠 box，省 token）
        2. 有 box_name → 钻入模式（只展示该 box 内节点）
        3. 无 box_name 且网络无 NetworkBox → 传统全展开模式
        """
        ok, data = self.get_network_structure(network_path)
        if not ok:
            return False, data.get("error", "未知错误")
        
        boxes = data.get("network_boxes", [])
        boxed_paths = set(data.get("boxed_node_paths", []))

        # ── 钻入模式：只展示指定 box 内的节点 ──
        if box_name:
            target = next((b for b in boxes if b["name"] == box_name), None)
            if not target:
                available = ", ".join(b["name"] for b in boxes) if boxes else "(无)"
                return False, f"未找到 NetworkBox: {box_name}。可用的 box: {available}"
            
            target_paths = set(target["nodes"])
            box_nodes = [n for n in data["nodes"] if n["path"] in target_paths]
            box_conns = [c for c in data["connections"]
                         if c["from"] in target_paths and c["to"] in target_paths]
            # box 与外部的跨组连接
            cross_conns = [c for c in data["connections"]
                           if (c["from"] in target_paths) != (c["to"] in target_paths)]
            
            lines = [
                f"## NetworkBox 详情: {box_name}",
                f"注释: {target['comment'] or '(无)'}",
                f"节点数量: {target['node_count']}",
                "", "### 节点列表:"
            ]
            wrangle_details = []
            self._format_node_list(box_nodes, lines, wrangle_details)
            
            if box_conns:
                lines.append("")
                lines.append("### 内部连接:")
                for conn in box_conns:
                    lines.append(self._format_connection(conn))
            
            if cross_conns:
                lines.append("")
                lines.append("### 跨组连接（与其他 box / 未分组节点）:")
                for conn in cross_conns:
                    lines.append(self._format_connection(conn))
            
            if wrangle_details:
                lines.append("")
                lines.append("### 节点内嵌代码:")
                for detail in wrangle_details:
                    lines.append(detail)
            
            return True, "\n".join(lines)

        # ── 概览模式：有 NetworkBox 时折叠显示（核心省 token 逻辑） ──
        if boxes:
            unboxed_nodes = [n for n in data["nodes"] if n["path"] not in boxed_paths]
            
            lines = [
                f"## 网络结构: {data['network_path']}",
                f"网络类型: {data['network_type']}",
                f"节点总数: {data['node_count']}",
                f"NetworkBox 分组: {len(boxes)} 个（包含 {len(boxed_paths)} 个节点）",
                "",
                "### NetworkBox 概览:"
            ]
            for b in boxes:
                # 统计 box 内节点类型摘要（取前 3 种）
                box_paths_set = set(b["nodes"])
                type_counts: Dict[str, int] = {}
                for n in data["nodes"]:
                    if n["path"] in box_paths_set:
                        short_type = n["type"].split("/")[-1] if "/" in n["type"] else n["type"]
                        type_counts[short_type] = type_counts.get(short_type, 0) + 1
                top_types = sorted(type_counts.items(), key=lambda x: -x[1])[:3]
                types_str = ", ".join(f"{t}×{c}" for t, c in top_types)
                if len(type_counts) > 3:
                    types_str += f" 等{len(type_counts)}种"
                
                lines.append(f"📦 **{b['name']}**: {b['comment'] or '(无注释)'} — {b['node_count']} 个节点 [{types_str}]")
            
            lines.append(f"\n💡 使用 get_network_structure(box_name=\"box名称\") 查看某个分组的详细节点")
            
            if unboxed_nodes:
                lines.append(f"\n### 未分组节点 ({len(unboxed_nodes)} 个):")
                wrangle_details = []
                self._format_node_list(unboxed_nodes, lines, wrangle_details)
                if wrangle_details:
                    lines.append("")
                    lines.append("### 未分组节点内嵌代码:")
                    for detail in wrangle_details:
                        lines.append(detail)
            
            # 跨组连接：两端不在同一个 box 中的连接
            cross_conns = []
            # 构建 node_path → box_name 映射
            path_to_box: Dict[str, str] = {}
            for b in boxes:
                for np in b["nodes"]:
                    path_to_box[np] = b["name"]
            for conn in data["connections"]:
                src_box = path_to_box.get(conn["from"], "__unboxed__")
                dst_box = path_to_box.get(conn["to"], "__unboxed__")
                if src_box != dst_box:
                    cross_conns.append(conn)
            
            if cross_conns:
                lines.append("")
                lines.append("### 跨组连接:")
                for conn in cross_conns:
                    from_name = conn['from'].split('/')[-1]
                    to_name = conn['to'].split('/')[-1]
                    src_box = path_to_box.get(conn["from"], "未分组")
                    dst_box = path_to_box.get(conn["to"], "未分组")
                    idx = conn['input_index']
                    label = conn.get('input_label', '')
                    port_str = f"{label}({idx})" if label else str(idx)
                    lines.append(f"- [{src_box}] {from_name} → {to_name}[{port_str}] [{dst_box}]")
            
            return True, "\n".join(lines)

        # ── 传统模式：无 NetworkBox，全部展开（兼容旧行为） ──
        lines = [
            f"## 网络结构: {data['network_path']}",
            f"网络类型: {data['network_type']}",
            f"节点数量: {data['node_count']}",
            "",
            "### 节点列表:"
        ]
        
        wrangle_details = []
        self._format_node_list(data['nodes'], lines, wrangle_details)
        
        if data['connections']:
            lines.append("")
            lines.append("### 连接关系:")
            for conn in data['connections']:
                lines.append(self._format_connection(conn))
        
        if wrangle_details:
            lines.append("")
            lines.append("### 节点内嵌代码:")
            for detail in wrangle_details:
                lines.append(detail)
        
        return True, "\n".join(lines)

    @staticmethod
    def _format_node_list(nodes: List[Dict], lines: List[str], wrangle_details: List[str]):
        """格式化节点列表到 lines，收集代码详情到 wrangle_details"""
        for node in nodes:
            status = []
            if node.get('is_displayed'):
                status.append("显示")
            if node.get('has_errors'):
                status.append("错误")
            status_str = f" [{', '.join(status)}]" if status else ""
            
            has_code = ""
            if node.get('vex_code'):
                has_code = " [含VEX代码]"
            elif node.get('python_code'):
                has_code = " [含Python代码]"
            
            lines.append(f"- `{node['name']}` ({node['type']}){status_str}{has_code}")
            
            if node.get('vex_code'):
                code = node['vex_code']
                code_lines = code.split('\n')
                if len(code_lines) > 30:
                    code = '\n'.join(code_lines[:30]) + f'\n// ... 共 {len(code_lines)} 行，已截断'
                wrangle_details.append(
                    f"#### `{node['name']}` VEX 代码:\n```vex\n{code}\n```"
                )
            elif node.get('python_code'):
                code = node['python_code']
                code_lines = code.split('\n')
                if len(code_lines) > 30:
                    code = '\n'.join(code_lines[:30]) + f'\n# ... 共 {len(code_lines)} 行，已截断'
                wrangle_details.append(
                    f"#### `{node['name']}` Python 代码:\n```python\n{code}\n```"
                )

    @staticmethod
    def _format_connection(conn: Dict[str, Any], prefix: str = "- ") -> str:
        """格式化单条连接信息，包含输入端口名称（如有）"""
        from_name = conn['from'].split('/')[-1]
        to_name = conn['to'].split('/')[-1]
        idx = conn['input_index']
        label = conn.get('input_label', '')
        if label:
            port_str = f"{label}({idx})"
        else:
            port_str = str(idx)
        return f"{prefix}{from_name} → {to_name}[{port_str}]"

    # ========================================
    # ATS (Abstract Type System) 构建
    # ========================================
    
    def _build_ats(self, node_type: Any) -> Dict[str, Any]:
        """构建节点类型的ATS（抽象类型系统）
        
        Args:
            node_type: Houdini节点类型对象
            
        Returns:
            ATS数据字典，包含参数模板、默认值等信息
        """
        if hou is None or node_type is None:
            return {}
        
        # 生成缓存键
        type_key = f"{node_type.category().name().lower()}/{node_type.name()}"
        
        # 检查缓存
        if type_key in HoudiniMCP._ats_cache:
            return HoudiniMCP._ats_cache[type_key]
        
        try:
            # 获取参数模板
            parm_template_group = node_type.parmTemplateGroup()
            ats_data = {
                "type": type_key,
                "type_label": node_type.description() if hasattr(node_type, 'description') else "",
                "input_count": {
                    "min": node_type.minNumInputs() if hasattr(node_type, 'minNumInputs') else 0,
                    "max": node_type.maxNumInputs() if hasattr(node_type, 'maxNumInputs') else 0,
                },
                "output_count": {
                    "min": node_type.minNumOutputs() if hasattr(node_type, 'minNumOutputs') else 0,
                    "max": node_type.maxNumOutputs() if hasattr(node_type, 'maxNumOutputs') else 0,
                },
                "parameters": {}
            }
            
            # 提取参数模板信息（只包含参数名、类型、默认值）
            if parm_template_group:
                for parm_template in parm_template_group.parmTemplates():
                    try:
                        parm_name = parm_template.name()
                        parm_type = parm_template.type().name() if hasattr(parm_template, 'type') else "unknown"
                        
                        # 获取默认值
                        default_value = None
                        if hasattr(parm_template, 'defaultValue'):
                            try:
                                default_value = parm_template.defaultValue()
                                # 格式化浮点数
                                if isinstance(default_value, float):
                                    default_value = round(default_value, 6)
                                elif isinstance(default_value, tuple):
                                    default_value = tuple(round(v, 6) if isinstance(v, float) else v for v in default_value)
                            except Exception:
                                pass
                        
                        # 只保存关键信息
                        ats_data["parameters"][parm_name] = {
                            "type": parm_type,
                            "default_value": default_value,
                            "is_hidden": parm_template.isHidden() if hasattr(parm_template, 'isHidden') else False,
                        }
                    except Exception:
                        continue
            
            # 缓存ATS数据
            HoudiniMCP._ats_cache[type_key] = ats_data
            return ats_data
            
        except Exception:
            return {}
    
    # ========================================
    # 节点详情读取（优化版：先构建ATS，再读取部分上下文）
    # ========================================
    
    def get_node_details(self, node_path: str) -> Tuple[bool, Dict[str, Any]]:
        """获取指定节点的详细信息（优化版：先构建ATS，再读取部分上下文）
        
        流程：
        1. 先构建ATS（节点类型的抽象信息，包括参数模板、默认值等）
        2. 针对特定节点只读取部分上下文（非默认参数、错误、连接等）
        
        Args:
            node_path: 节点完整路径
        
        Returns:
            (success, data) 其中 data 包含:
            {
                "name": str,
                "path": str,
                "type": str,
                "type_label": str,
                "comment": str,
                "flags": {...},
                "errors": [...],
                "inputs": [...],
                "outputs": [...],
                "parameters": {...},  # 只包含非默认参数
                "ats": {...}  # ATS信息（可选，用于参考）
            }
        """
        if hou is None:
            return False, {"error": "未检测到 Houdini API"}
        
        node = hou.node(node_path)
        if node is None:
            return False, {"error": f"未找到节点: {node_path}"}
        
        try:
            node_type = node.type()
            category = node_type.category().name() if node_type else "Unknown"
            type_name = node_type.name() if node_type else "unknown"
            type_key = f"{category.lower()}/{type_name}"
            
            # 第一步：构建ATS（节点类型的抽象信息）
            ats_data = self._build_ats(node_type)
            
            # 第二步：读取节点特定上下文（只读取部分信息）
            # 基本信息
            data = {
                "name": node.name(),
                "path": node.path(),
                "type": type_key,
                "type_label": node_type.description() if node_type else "",
                "comment": node.comment().strip() if node.comment() else "",
            }
            
            # 状态信息
            data["flags"] = {
                "display": node.isDisplayFlagSet() if hasattr(node, 'isDisplayFlagSet') else False,
                "render": node.isRenderFlagSet() if hasattr(node, 'isRenderFlagSet') else False,
                "bypass": node.isBypassed() if hasattr(node, 'isBypassed') else False,
                "locked": node.isLocked() if hasattr(node, 'isLocked') else False,
            }
            
            # 错误信息（重要，必须读取）
            errors = []
            try:
                errs = node.errors()
                if errs:
                    errors = list(errs)
            except Exception:
                pass
            data["errors"] = errors
            
            # 输入输出连接（重要，必须读取）
            inputs = []
            for i, inp in enumerate(node.inputs()):
                if inp is not None:
                    inputs.append({"index": i, "node": inp.path()})
            data["inputs"] = inputs
            
            outputs = []
            for out in node.outputs():
                outputs.append(out.path())
            data["outputs"] = outputs
            
            # 只读取非默认参数（部分上下文）
            params = {}
            for parm in node.parms():
                try:
                    if parm.isHidden() or parm.isDisabled():
                        continue
                    
                    parm_name = parm.name()
                    
                    # 检查是否为默认值
                    is_default = False
                    try:
                        is_default = parm.isAtDefault()
                    except Exception:
                        # 如果无法判断，则读取当前值
                        pass
                    
                    # 只保存非默认参数
                    if not is_default:
                        value = parm.eval()
                        
                        # 格式化浮点数
                        if isinstance(value, float):
                            value = round(value, 6)
                        elif isinstance(value, tuple):
                            value = tuple(round(v, 6) if isinstance(v, float) else v for v in value)
                        
                        params[parm_name] = {
                            "value": value,
                            "is_default": False
                        }
                except Exception:
                    continue
            
            data["parameters"] = params
            
            # 可选：添加ATS引用（用于参考，但不包含在主要数据中）
            # 如果需要完整ATS信息，可以通过 get_node_type_ats 单独获取
            
            return True, data
        except Exception as e:
            return False, {"error": f"读取节点详情失败: {str(e)}"}

    def get_node_details_text(self, node_path: str) -> Tuple[bool, str]:
        """获取节点详情的文本描述（优化版：只显示部分上下文）"""
        ok, data = self.get_node_details(node_path)
        if not ok:
            return False, data.get("error", "未知错误")
        
        lines = [
            f"## 节点: {data['name']}",
            f"路径: {data['path']}",
            f"类型: {data['type']} ({data['type_label']})",
        ]
        
        if data['comment']:
            lines.append(f"备注: {data['comment']}")
        
        # 状态
        flags = data['flags']
        status = []
        if flags['display']:
            status.append("显示")
        if flags['render']:
            status.append("渲染")
        if flags['bypass']:
            status.append("旁路")
        if flags['locked']:
            status.append("锁定")
        if status:
            lines.append(f"状态: {', '.join(status)}")
        
        # 错误（重要上下文）
        if data['errors']:
            lines.append("")
            lines.append("### 错误:")
            for err in data['errors']:
                lines.append(f"- {err}")
        
        # 连接（重要上下文）
        if data['inputs']:
            lines.append("")
            lines.append("### 输入连接:")
            for inp in data['inputs']:
                lines.append(f"- [{inp['index']}] ← {inp['node']}")
        
        if data['outputs']:
            lines.append("")
            lines.append("### 输出连接:")
            for out in data['outputs']:
                lines.append(f"- → {out}")
        
        # 非默认参数（部分上下文，已优化）
        lines.append("")
        lines.append("### 参数（非默认值）:")
        if data['parameters']:
            for name, info in data['parameters'].items():
                value = info['value']
                if isinstance(value, tuple):
                    value_str = "(" + ", ".join(str(v) for v in value) + ")"
                else:
                    value_str = str(value)
                lines.append(f"- {name} = {value_str}")
        else:
            lines.append("（所有参数均为默认值）")
        
        return True, "\n".join(lines)
    
    def get_node_type_ats(self, node_type: str, category: str = "sop") -> Tuple[bool, Dict[str, Any]]:
        """获取节点类型的ATS（抽象类型系统）信息
        
        Args:
            node_type: 节点类型名称，如 'box', 'scatter'
            category: 节点类别，默认 'sop'
        
        Returns:
            (success, ats_data) ATS数据包含参数模板、默认值等信息
        """
        if hou is None:
            return False, {"error": "未检测到 Houdini API"}
        
        try:
            # 获取节点类型对象
            categories = hou.nodeTypeCategories()
            cat_obj = categories.get(category.capitalize()) or categories.get(category.upper())
            if not cat_obj:
                return False, {"error": f"未找到类别: {category}"}
            
            node_type_obj = None
            type_lower = node_type.lower()
            for name, nt in cat_obj.nodeTypes().items():
                if name.lower() == type_lower or name.lower().endswith(f"::{type_lower}"):
                    node_type_obj = nt
                    break
            
            if not node_type_obj:
                return False, {"error": f"未找到节点类型: {node_type}"}
            
            # 构建ATS
            ats_data = self._build_ats(node_type_obj)
            if not ats_data:
                return False, {"error": "构建ATS失败"}
            
            return True, ats_data
            
        except Exception as e:
            return False, {"error": f"获取ATS失败: {str(e)}"}

    # ========================================
    # 错误和警告检查
    # ========================================
    
    def check_node_errors(self, node_path: Optional[str] = None) -> Tuple[bool, Dict[str, Any]]:
        """检查节点或网络中的错误和警告
        
        Args:
            node_path: 节点路径。如果是网络路径，检查其下所有节点。如果为 None，检查当前网络。
        
        Returns:
            (success, data) 其中 data 包含 errors 和 warnings 列表
        """
        if hou is None:
            return False, {"error": "未检测到 Houdini API"}
        
        try:
            # 确定要检查的节点
            if node_path:
                target = hou.node(node_path)
                if target is None:
                    return False, {"error": f"未找到节点: {node_path}"}
            else:
                # 获取当前网络
                try:
                    pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
                    target = pane.pwd() if pane else hou.node('/obj')
                except Exception:
                    target = hou.node('/obj')
            
            results = {
                "checked_path": target.path(),
                "total_nodes": 0,
                "error_count": 0,
                "warning_count": 0,
                "errors": [],
                "warnings": []
            }
            
            # 如果是容器节点，检查所有子节点
            if hasattr(target, 'children') and target.children():
                nodes_to_check = target.allSubChildren() if hasattr(target, 'allSubChildren') else target.children()
            else:
                nodes_to_check = [target]
            
            results["total_nodes"] = len(nodes_to_check)
            
            for node in nodes_to_check:
                try:
                    # 检查错误
                    errors = node.errors() if hasattr(node, 'errors') else []
                    for err in errors:
                        results["errors"].append({
                            "node_path": node.path(),
                            "node_name": node.name(),
                            "node_type": node.type().name() if node.type() else "unknown",
                            "message": str(err)
                        })
                        results["error_count"] += 1
                    
                    # 检查警告
                    warnings = node.warnings() if hasattr(node, 'warnings') else []
                    for warn in warnings:
                        results["warnings"].append({
                            "node_path": node.path(),
                            "node_name": node.name(),
                            "node_type": node.type().name() if node.type() else "unknown",
                            "message": str(warn)
                        })
                        results["warning_count"] += 1
                        
                except Exception:
                    continue
            
            return True, results
            
        except Exception as e:
            return False, {"error": f"检查错误失败: {str(e)}"}
    
    def check_node_errors_text(self, node_path: Optional[str] = None) -> Tuple[bool, str]:
        """获取错误检查的文本描述"""
        ok, data = self.check_node_errors(node_path)
        if not ok:
            return False, data.get("error", "未知错误")
        
        lines = [
            f"## 错误检查报告",
            f"检查路径: {data['checked_path']}",
            f"检查节点数: {data['total_nodes']}",
            f"错误数: {data['error_count']}",
            f"警告数: {data['warning_count']}",
        ]
        
        if data['errors']:
            lines.append("")
            lines.append("### 错误:")
            for err in data['errors']:
                lines.append(f"- **{err['node_name']}** ({err['node_type']}): {err['message']}")
        
        if data['warnings']:
            lines.append("")
            lines.append("### 警告:")
            for warn in data['warnings']:
                lines.append(f"- **{warn['node_name']}** ({warn['node_type']}): {warn['message']}")
        
        if not data['errors'] and not data['warnings']:
            lines.append("")
            lines.append("**没有发现错误或警告。**")
        
        return True, "\n".join(lines)

    # ========================================
    # 选中节点操作
    # ========================================
    
    def describe_selection(self, limit: int = 3, include_all_params: bool = False) -> Tuple[bool, str]:
        """读取选中节点的信息"""
        if hou is None:
            return False, "未检测到 Houdini API"
        
        nodes = hou.selectedNodes()
        if not nodes:
            return False, "未选择任何节点"
        
        lines: List[str] = []
        for node in nodes[:limit]:
            ok, text = self.get_node_details_text(node.path())
            if ok:
                lines.append(text)
                lines.append("")
        
        if len(nodes) > limit:
            lines.append(f"（仅展示前 {limit} 个节点，共选择 {len(nodes)} 个）")
        
        return True, "\n".join(lines)

    # ========================================
    # 节点搜索（使用缓存）
    # ========================================
    
    def _get_node_types_index(self) -> Dict[str, List[Tuple[str, str, str]]]:
        """获取节点类型索引（带缓存）
        
        返回: {category_lower: [(type_name, description, full_path), ...]}
        """
        import time as _time
        cache_duration = 300  # 5分钟缓存
        
        if (HoudiniMCP._node_types_cache is not None and 
            _time.time() - HoudiniMCP._node_types_cache_time < cache_duration):
            return HoudiniMCP._node_types_cache
        
        if hou is None:
            return {}
        
        index: Dict[str, List[Tuple[str, str, str]]] = {}
        try:
            for cat_name, cat in hou.nodeTypeCategories().items():
                cat_lower = cat_name.lower()
                index[cat_lower] = []
                for type_name, node_type in cat.nodeTypes().items():
                    try:
                        desc = node_type.description()
                        index[cat_lower].append((type_name, desc, f"{cat_lower}/{type_name}"))
                    except Exception:
                        continue
            
            HoudiniMCP._node_types_cache = index
            HoudiniMCP._node_types_cache_time = _time.time()
        except Exception:
            pass
        
        return index
    
    def search_nodes(self, keyword: str, limit: int = 12) -> Tuple[bool, str]:
        """搜索节点类型（使用缓存）"""
        if hou is None:
            return False, "未检测到 Houdini API"
        if not keyword:
            return False, "请输入关键字"
        
        kw = keyword.lower()
        matches: List[str] = []
        
        # 使用缓存的节点类型索引
        index = self._get_node_types_index()
        for cat_name, types in index.items():
            for type_name, desc, full_path in types:
                if kw in full_path.lower() or kw in desc.lower():
                    matches.append(f"- `{full_path}` — {desc}")
        
        if not matches:
            return False, f"未找到包含 '{keyword}' 的节点类型"
        
        if len(matches) > limit:
            extra = len(matches) - limit
            matches = matches[:limit] + [f"… 还有 {extra} 个结果"]
        
        return True, "\n".join(matches)

    def semantic_search_nodes(self, description: str, category: str = "sop") -> Tuple[bool, str]:
        """语义搜索节点 - 通过自然语言描述找到合适的节点
        
        内置常用节点的语义映射
        """
        if hou is None:
            return False, "未检测到 Houdini API"
        
        # 语义映射表：描述关键词 -> 节点类型
        # 格式: "关键词": ["节点1", "节点2", ...]
        semantic_map = {
            # 点操作
            "分布点": ["scatter", "pointsfromvolume"],
            "撒点": ["scatter"],
            "随机点": ["scatter", "add"],
            "删除点": ["blast", "delete"],
            "合并点": ["fuse"],
            "点云": ["scatter"],
            
            # 复制操作
            "复制到点": ["copytopoints"],
            "实例化": ["copytopoints"],
            "复制物体": ["copytopoints"],
            "克隆": ["copytopoints"],
            "instance": ["copytopoints"],
            
            # 变形操作
            "噪波": ["mountain"],
            "noise": ["mountain", "attribnoise"],
            "变形": ["transform", "bend", "twist"],
            "平滑": ["smooth", "relax"],
            "挤出": ["polyextrude"],
            "细分": ["subdivide", "remesh"],
            
            # 创建几何体
            "盒子": ["box"],
            "box": ["box"],
            "球": ["sphere"],
            "圆柱": ["tube"],
            "平面": ["grid"],
            "grid": ["grid"],
            "曲线": ["curve", "line"],
            
            # ⭐ 地形相关（常见需求，详细映射）
            "地形": ["grid", "mountain"],  # 地形 = grid + mountain
            "terrain": ["grid", "mountain"],
            "地面": ["grid"],
            "山": ["mountain"],
            "起伏": ["mountain"],
            "高度场": ["heightfield"],
            "heightfield": ["heightfield"],
            
            # 属性操作
            "设置属性": ["attribwrangle"],
            "颜色": ["color", "attribwrangle"],
            "法线": ["normal"],
            "UV": ["uvproject", "uvunwrap"],
            
            # 连接操作
            "合并": ["merge"],
            "merge": ["merge"],
            "分离": ["split", "blast"],
            "布尔": ["boolean"],
            "交集": ["boolean"],
            
            # 模拟相关
            "刚体": ["rbdmaterialfracture"],
            "破碎": ["voronoifracture"],
            "流体": ["flip", "pyro"],
            "布料": ["vellum"],
            "毛发": ["hairgen"],
        }
        
        desc_lower = description.lower()
        results = []
        scores = {}
        
        # 匹配语义映射
        for keywords, nodes in semantic_map.items():
            if any(k in desc_lower for k in keywords.split()):
                for node in nodes:
                    if node not in scores:
                        scores[node] = 0
                    scores[node] += 1
        
        # 获取匹配的节点详情
        cat_filter = category.lower() if category != "all" else None
        
        for node_name in sorted(scores.keys(), key=lambda x: -scores[x])[:10]:
            for cat_name, cat in hou.nodeTypeCategories().items():
                if cat_filter and cat_name.lower() != cat_filter:
                    continue
                for type_name, node_type in cat.nodeTypes().items():
                    if node_name in type_name.lower():
                        desc = node_type.description()
                        results.append(f"- `{cat_name.lower()}/{type_name}` — {desc}")
                        break
        
        # 如果语义匹配没找到，尝试直接关键词搜索
        if not results:
            for cat_name, cat in hou.nodeTypeCategories().items():
                if cat_filter and cat_name.lower() != cat_filter:
                    continue
                for type_name, node_type in cat.nodeTypes().items():
                    desc = node_type.description().lower()
                    if any(w in desc or w in type_name.lower() for w in desc_lower.split()):
                        results.append(f"- `{cat_name.lower()}/{type_name}` — {node_type.description()}")
                        if len(results) >= 10:
                            break
                if len(results) >= 10:
                    break
        
        if results:
            result_text = f"根据 '{description}' 找到以下节点:\n" + "\n".join(results[:10])
            return True, result_text
        
        return False, f"未找到匹配 '{description}' 的节点"

    def list_children(self, network_path: Optional[str] = None, 
                      recursive: bool = False, 
                      show_flags: bool = True) -> Tuple[bool, str]:
        """列出子节点"""
        if hou is None:
            return False, "未检测到 Houdini API"
        
        if network_path:
            network = hou.node(network_path)
            if not network:
                return False, f"未找到网络: {network_path}"
        else:
            network = self._current_network()
            if not network:
                return False, "未找到当前网络"
        
        def format_node(node, indent=0):
            prefix = "  " * indent
            flags = ""
            if show_flags:
                parts = []
                if hasattr(node, 'isDisplayFlagSet') and node.isDisplayFlagSet():
                    parts.append("[disp]")
                if hasattr(node, 'isRenderFlagSet') and node.isRenderFlagSet():
                    parts.append("🎬")
                if hasattr(node, 'isBypassed') and node.isBypassed():
                    parts.append("⏸")
                if parts:
                    flags = f" [{' '.join(parts)}]"
            
            node_type = node.type().name() if node.type() else "unknown"
            return f"{prefix}- {node.name()} ({node_type}){flags}"
        
        lines = [f"## {network.path()}"]
        
        def list_nodes(parent, indent=0):
            for child in parent.children():
                lines.append(format_node(child, indent))
                if recursive and hasattr(child, 'children') and child.children():
                    list_nodes(child, indent + 1)
        
        list_nodes(network)
        
        if len(lines) == 1:
            lines.append("（空网络）")
        
        return True, "\n".join(lines)

    def get_geometry_info(self, node_path: str, output_index: int = 0) -> Tuple[bool, str]:
        """获取几何体信息"""
        if hou is None:
            return False, "未检测到 Houdini API"
        
        node = hou.node(node_path)
        if not node:
            return False, f"未找到节点: {node_path}"
        
        try:
            geo = node.geometry()
            if not geo:
                return False, f"节点 {node_path} 没有几何体输出"
            
            info = {
                "点数": geo.intrinsicValue("pointcount"),
                "顶点数": geo.intrinsicValue("vertexcount"),
                "图元数": geo.intrinsicValue("primitivecount"),
            }
            
            # 点属性
            point_attrs = [f"{a.name()} ({a.dataType().name()})" for a in geo.pointAttribs()]
            # 顶点属性
            vertex_attrs = [f"{a.name()} ({a.dataType().name()})" for a in geo.vertexAttribs()]
            # 图元属性
            prim_attrs = [f"{a.name()} ({a.dataType().name()})" for a in geo.primAttribs()]
            # 全局属性
            detail_attrs = [f"{a.name()} ({a.dataType().name()})" for a in geo.globalAttribs()]
            
            lines = [
                f"## 几何体信息: {node_path}",
                f"- 点数: {info['点数']}",
                f"- 顶点数: {info['顶点数']}",
                f"- 图元数: {info['图元数']}",
                "",
                "### 属性",
            ]
            
            if point_attrs:
                lines.append(f"点属性: {', '.join(point_attrs)}")
            if vertex_attrs:
                lines.append(f"顶点属性: {', '.join(vertex_attrs)}")
            if prim_attrs:
                lines.append(f"图元属性: {', '.join(prim_attrs)}")
            if detail_attrs:
                lines.append(f"全局属性: {', '.join(detail_attrs)}")
            
            if not any([point_attrs, vertex_attrs, prim_attrs, detail_attrs]):
                lines.append("（无自定义属性）")
            
            return True, "\n".join(lines)
        except Exception as e:
            return False, f"获取几何体信息失败: {str(e)}"

    def set_display_flag(self, node_path: str, display: bool = True, 
                         render: bool = True) -> Tuple[bool, str]:
        """设置显示/渲染标志"""
        if hou is None:
            return False, "未检测到 Houdini API"
        
        node = hou.node(node_path)
        if not node:
            return False, f"未找到节点: {node_path}"
        
        try:
            if display and hasattr(node, 'setDisplayFlag'):
                node.setDisplayFlag(True)
            if render and hasattr(node, 'setRenderFlag'):
                node.setRenderFlag(True)
            
            flags = []
            if display:
                flags.append("显示")
            if render:
                flags.append("渲染")
            
            return True, f"已设置 {node.name()} 为{'/'.join(flags)}节点"
        except Exception as e:
            return False, f"设置标志失败: {str(e)}"

    def copy_node(self, source_path: str, dest_network: Optional[str] = None,
                  new_name: Optional[str] = None) -> Tuple[bool, str]:
        """复制节点"""
        if hou is None:
            return False, "未检测到 Houdini API"
        
        source = hou.node(source_path)
        if not source:
            return False, f"未找到源节点: {source_path}"
        
        if dest_network:
            dest = hou.node(dest_network)
            if not dest:
                return False, f"未找到目标网络: {dest_network}"
        else:
            dest = source.parent()
        
        try:
            new_node = hou.copyNodesTo([source], dest)[0]
            if new_name:
                new_node.setName(new_name)
            new_node.moveToGoodPosition()
            return True, f"已复制节点到: {new_node.path()}"
        except Exception as e:
            return False, f"复制失败: {str(e)}"

    def batch_set_parameters(self, node_paths: List[str], param_name: str, 
                             value: Any) -> Tuple[bool, str]:
        """批量设置参数"""
        if hou is None:
            return False, "未检测到 Houdini API"
        
        success = []
        failed = []
        
        for path in node_paths:
            node = hou.node(path)
            if not node:
                failed.append(f"{path}: 未找到")
                continue
            
            parm = node.parm(param_name)
            if not parm:
                parm_tuple = node.parmTuple(param_name)
                if parm_tuple and isinstance(value, (list, tuple)):
                    try:
                        parm_tuple.set(value)
                        success.append(node.name())
                    except Exception as e:
                        failed.append(f"{node.name()}: {e}")
                else:
                    failed.append(f"{node.name()}: 无参数 {param_name}")
                continue
            
            try:
                parm.set(value)
                success.append(node.name())
            except Exception as e:
                failed.append(f"{node.name()}: {e}")
        
        msg = f"修改成功: {len(success)} 个节点"
        if failed:
            msg += f"\n失败: {'; '.join(failed)}"
        
        return len(success) > 0, msg

    def find_nodes_by_param(self, param_name: str, value: Any = None,
                            network_path: Optional[str] = None,
                            recursive: bool = True) -> Tuple[bool, str]:
        """按参数值搜索节点"""
        if hou is None:
            return False, "未检测到 Houdini API"
        
        if network_path:
            network = hou.node(network_path)
            if not network:
                return False, f"未找到网络: {network_path}"
        else:
            network = self._current_network() or hou.node('/obj')
        
        results = []
        
        def search_in(parent):
            for node in parent.children():
                parm = node.parm(param_name)
                if parm:
                    parm_value = parm.eval()
                    if value is None or str(parm_value) == str(value):
                        results.append(f"- {node.path()}: {param_name}={parm_value}")
                if recursive and hasattr(node, 'children'):
                    search_in(node)
        
        search_in(network)
        
        if results:
            header = f"找到 {len(results)} 个节点包含参数 '{param_name}'"
            if value is not None:
                header += f" = {value}"
            return True, header + ":\n" + "\n".join(results[:50])
        
        return False, f"未找到包含参数 '{param_name}' 的节点"

    def save_hip(self, file_path: Optional[str] = None) -> Tuple[bool, str]:
        """保存 HIP 文件"""
        if hou is None:
            return False, "未检测到 Houdini API"
        
        try:
            if file_path:
                hou.hipFile.save(file_path)
                return True, f"已保存到: {file_path}"
            else:
                hou.hipFile.save()
                return True, f"已保存: {hou.hipFile.path()}"
        except Exception as e:
            return False, f"保存失败: {str(e)}"

    def undo_redo(self, action: str) -> Tuple[bool, str]:
        """撤销/重做"""
        if hou is None:
            return False, "未检测到 Houdini API"
        
        try:
            if action == "undo":
                hou.undos.performUndo()
                return True, "已撤销"
            elif action == "redo":
                hou.undos.performRedo()
                return True, "已重做"
            else:
                return False, f"未知操作: {action}"
        except Exception as e:
            return False, f"操作失败: {str(e)}"

    def search_documentation(self, node_type: str, category: str = "sop") -> Tuple[bool, str]:
        """查询节点文档"""
        if requests is None:
            return False, "requests 模块未安装"
        
        base_url = "https://www.sidefx.com/docs/houdini/nodes"
        doc_node_type = node_type.replace("::", "--")
        doc_url = f"{base_url}/{category}/{doc_node_type}.html"
        
        settings = read_settings()
        tries = max(1, settings.request_retries + 1)
        
        for _ in range(tries):
            try:
                response = requests.get(doc_url, timeout=settings.request_timeout)
                if response.status_code == 404:
                    return False, f"未找到文档: {category}/{node_type}"
                response.raise_for_status()
                
                content = response.text
                title_match = re.search(r'<title>(.*?)</title>', content, re.IGNORECASE)
                title = title_match.group(1) if title_match else f"{node_type} node"
                
                summary = ""
                summary_match = re.search(r'<div[^>]*class="[^"]*summary[^"]*"[^>]*>(.*?)</div>', content, re.DOTALL | re.IGNORECASE)
                if summary_match:
                    summary = re.sub(r'<[^>]+>', '', summary_match.group(1)).strip()
                
                result = f"## {title}\n\n**文档链接**: {doc_url}\n\n"
                if summary:
                    result += f"**描述**: {summary}\n"
                
                return True, result
            except Exception as e:
                time.sleep(settings.request_backoff)
        
        return False, f"查询失败: {doc_url}"

    # ========================================
    # Wrangle 节点创建（VEX 优先）
    # ========================================
    
    def create_wrangle_node(self, vex_code: str, 
                            wrangle_type: str = "attribwrangle",
                            node_name: Optional[str] = None,
                            run_over: str = "Points",
                            parent_path: Optional[str] = None) -> Tuple[bool, str]:
        """创建 Wrangle 节点并设置 VEX 代码
        
        这是解决几何处理问题的首选方式。
        
        Args:
            vex_code: VEX 代码
            wrangle_type: Wrangle 类型，默认 attribwrangle
            node_name: 节点名称（可选）
            run_over: 运行模式 (Points/Vertices/Primitives/Detail)
            parent_path: 父网络路径（可选）
        
        Returns:
            (success, message)
        """
        if hou is None:
            return False, "未检测到 Houdini API"
        
        if not vex_code or not vex_code.strip():
            return False, "VEX 代码为空"
        
        # 获取父网络
        if parent_path:
            network = hou.node(parent_path)
            if network is None:
                return False, f"未找到父网络: {parent_path}"
        else:
            network = self._current_network()
            if network is None:
                return False, "未找到当前网络"
        
        # 验证 wrangle 类型
        valid_types = ["attribwrangle", "pointwrangle", "primitivewrangle", 
                       "volumewrangle", "vertexwrangle"]
        if wrangle_type not in valid_types:
            wrangle_type = "attribwrangle"
        
        # 确保在正确的网络层级
        network = self._ensure_target_network(network, self._category_from_hint("sop"))
        
        # 创建节点
        safe_name = self._sanitize_node_name(node_name)
        
        try:
            # 根据文档，使用 force_valid_node_name=True 自动处理无效节点名
            new_node = network.createNode(
                wrangle_type,
                safe_name,
                run_init_scripts=True,
                load_contents=True,
                exact_type_name=False,  # 允许模糊匹配
                force_valid_node_name=True  # 自动清理无效节点名
            )
        except Exception as exc:
            return False, f"创建 Wrangle 节点失败: {exc}"
        
        # 设置 VEX 代码
        try:
            # 大多数 Wrangle 节点的代码参数名是 "snippet"
            snippet_parm = new_node.parm("snippet")
            if snippet_parm:
                snippet_parm.set(vex_code)
            else:
                # 某些节点可能用 "code" 或 "vexcode"
                for parm_name in ["code", "vexcode", "vex_code"]:
                    parm = new_node.parm(parm_name)
                    if parm:
                        parm.set(vex_code)
                        break
        except Exception as exc:
            return False, f"设置 VEX 代码失败: {exc}"
        
        # 设置运行模式
        run_over_map = {
            "Points": 0,
            "Vertices": 1, 
            "Primitives": 2,
            "Detail": 3
        }
        run_over_value = run_over_map.get(run_over, 0)
        
        try:
            class_parm = new_node.parm("class")
            if class_parm:
                class_parm.set(run_over_value)
        except Exception:
            pass  # 某些 wrangle 类型可能没有 class 参数
        
        # 布局和选择
        new_node.moveToGoodPosition()
        new_node.setSelected(True, clear_all_selected=True)
        
        try:
            new_node.setDisplayFlag(True)
            new_node.setRenderFlag(True)
        except Exception:
            pass
        
        try:
            editor = hou.ui.curDesktop().paneTabOfType(hou.paneTabType.NetworkEditor)
            if editor:
                editor.homeToSelection()
        except Exception:
            pass
        
        # 检查是否有编译错误
        errors = []
        try:
            node_errors = new_node.errors()
            if node_errors:
                errors = list(node_errors)
        except Exception:
            pass
        
        if errors:
            return True, f"已创建 Wrangle 节点: {new_node.path()}\nVEX 编译警告: {'; '.join(errors)}"
        
        return True, f"已创建 Wrangle 节点: {new_node.path()}"

    # ========================================
    # 节点创建
    # ========================================
    
    def create_node(self, type_hint: str, node_name: Optional[str] = None, 
                    parameters: Optional[Dict[str, Any]] = None,
                    parent_path: Optional[str] = None) -> Tuple[bool, str]:
        """创建单个节点"""
        if hou is None:
            return False, "未检测到 Houdini API"
        
        # 获取父网络
        if parent_path:
            network = hou.node(parent_path)
            if network is None:
                return False, f"未找到父网络: {parent_path}"
        else:
            network = self._current_network()
            if network is None:
                # 尝试使用默认网络
                try:
                    network = hou.node('/obj')
                    if network is None:
                        return False, "未找到当前网络，且无法访问默认网络 /obj。请确保Houdini已正确启动，或在网络编辑器中打开一个网络。"
                except Exception:
                    return False, "未找到当前网络，且无法访问默认网络。请确保Houdini已正确启动，或在网络编辑器中打开一个网络。"
        
        if not type_hint:
            return False, "未提供节点类型"
        
        # 根据文档，createNode 可以直接处理节点类型匹配，无需预先解析
        # 但我们需要确保网络类型正确
        desired_cat = self._desired_category_from_hint(type_hint, network)
        if desired_cat is None:
            # 如果无法识别类别，尝试根据节点类型推断（常见SOP节点）
            common_sop_nodes = ['box', 'sphere', 'grid', 'tube', 'line', 'circle', 'noise', 'mountain', 
                              'scatter', 'copytopoints', 'attribwrangle', 'pointwrangle', 'primitivewrangle',
                              'delete', 'blast', 'fuse', 'transform', 'subdivide', 'remesh']
            if type_hint.lower() in common_sop_nodes:
                # 这是一个SOP节点，需要SOP网络
                desired_cat = hou.sopNodeTypeCategory()
            else:
                # 如果无法识别类别，尝试使用当前网络的类别
                desired_cat = network.childTypeCategory() if network else None
                if desired_cat is None:
                    return False, f"无法识别节点类别: {type_hint}"
        
        # 确保目标网络类型正确（会自动创建容器）
        network = self._ensure_target_network(network, desired_cat)
        if network is None:
            return False, f"无法获取或创建目标网络: {type_hint}"
        
        # 清理节点名（但保留原始值用于错误提示）
        safe_name = self._sanitize_node_name(node_name)
        
        # 根据文档，createNode 支持以下参数：
        # createNode(node_type_name, node_name=None, run_init_scripts=True, 
        #            load_contents=True, exact_type_name=False, force_valid_node_name=False)
        # 
        # 我们使用 force_valid_node_name=True 让 Houdini 自动处理无效节点名
        # 使用 exact_type_name=False（默认）让 Houdini 进行模糊匹配
        
        try:
            # 直接使用 createNode，让它自己处理类型匹配
            # 如果 node_name 无效，force_valid_node_name=True 会自动清理
            new_node = network.createNode(
                type_hint,  # 直接传原始类型名，让 Houdini 处理匹配
                safe_name,  # 如果为 None，Houdini 会自动生成名称
                run_init_scripts=True,
                load_contents=True,
                exact_type_name=False,  # 允许模糊匹配
                force_valid_node_name=True  # 自动清理无效节点名
            )
        except hou.OperationFailed as exc:
            # 提供更详细的错误信息
            error_detail = str(exc)
            current_cat = network.childTypeCategory() if network else None
            cat_name = current_cat.name().lower() if current_cat else "unknown"
            network_path = network.path() if network else "unknown"
            
            # 尝试提供建议
            suggestions = []
            try:
                if current_cat:
                    node_types = list(current_cat.nodeTypes().keys())
                    hint_lower = type_hint.lower()
                    for nt in node_types:
                        if hint_lower in nt.lower() or nt.lower() in hint_lower:
                            suggestions.append(nt)
                            if len(suggestions) >= 5:
                                break
            except Exception:
                pass
            
            error_msg = f"创建节点失败: {type_hint}\n"
            error_msg += f"错误详情: {error_detail}\n"
            error_msg += f"当前网络: {network_path} (类别: {cat_name})"
            if suggestions:
                error_msg += f"\n建议的节点类型: {', '.join(suggestions[:5])}"
            return False, error_msg
        except Exception as exc:
            import traceback
            error_detail = str(exc)
            network_path = network.path() if network else "unknown" if network else "None"
            error_msg = f"创建节点失败: {type_hint}\n"
            error_msg += f"错误: {error_detail}\n"
            error_msg += f"网络: {network_path}"
            # 只在调试时输出完整traceback
            if "DEBUG" in os.environ:
                error_msg += f"\n{traceback.format_exc()}"
            return False, error_msg
        
        # 设置参数
        if parameters and isinstance(parameters, dict):
            for parm_name, parm_value in parameters.items():
                parm = new_node.parm(parm_name)
                if parm is None:
                    parm_tuple = new_node.parmTuple(parm_name)
                    if parm_tuple and isinstance(parm_value, (list, tuple)):
                        try:
                            parm_tuple.set(parm_value)
                        except Exception:
                            pass
                    continue
                try:
                    parm.set(parm_value)
                except Exception:
                    continue
        
        new_node.moveToGoodPosition()
        new_node.setSelected(True, clear_all_selected=True)
        
        try:
            editor = hou.ui.curDesktop().paneTabOfType(hou.paneTabType.NetworkEditor)
            if editor:
                editor.homeToSelection()
        except Exception:
            pass
        
        # 返回节点路径 + diff 信息（让 AI 了解变化）
        node_path = new_node.path()
        diff_parts = [f"✓{node_path}"]
        try:
            parent = new_node.parent()
            if parent:
                siblings = len(parent.children())
                diff_parts.append(f"(父网络: {parent.path()}, 子节点数: {siblings})")
            # 输入连接信息
            inputs = new_node.inputs()
            if inputs:
                connected = [n.path() for n in inputs if n is not None]
                if connected:
                    diff_parts.append(f"输入: {', '.join(connected)}")
        except Exception:
            pass
        return True, ' '.join(diff_parts)

    def create_network(self, plan: Dict[str, Any]) -> Tuple[bool, str]:
        """批量创建节点网络"""
        if hou is None:
            return False, "未检测到 Houdini API"
        
        network = self._current_network()
        if network is None:
            return False, "未找到当前网络"
        
        node_specs = plan.get("nodes") if isinstance(plan, dict) else None
        if not node_specs:
            return False, "缺少 nodes 字段"
        
        created: Dict[str, Any] = {}
        creation_order: List[str] = []
        messages: List[str] = []
        
        try:
            # 检测是否需要自动创建容器
            current_cat = network.childTypeCategory()
            current_cat_name = current_cat.name().lower() if current_cat else ""
            
            has_sop_node = any(
                isinstance(spec, dict) and 
                str(spec.get("type", "")).lower().startswith("sop/")
                for spec in node_specs
            )
            
            if has_sop_node and current_cat_name.startswith("object"):
                try:
                    # 根据文档，直接使用 createNode，让它自己处理匹配
                    auto_container = network.createNode(
                        "geo",
                        None,  # 让 Houdini 自动生成名称
                        run_init_scripts=True,
                        load_contents=True,
                        exact_type_name=False,
                        force_valid_node_name=True
                    )
                    auto_container.moveToGoodPosition()
                    messages.append(f"自动创建容器: {auto_container.name()}")
                    network = auto_container
                except Exception as exc:
                    messages.append(f"创建容器失败: {exc}")
            
            # 创建节点
            for idx, spec in enumerate(node_specs):
                if not isinstance(spec, dict):
                    continue
                
                node_id = spec.get("id") or spec.get("name") or f"node_{idx+1}"
                type_hint = spec.get("type") or spec.get("node_type")
                
                if not type_hint:
                    messages.append(f"[{node_id}] 缺少 type")
                    continue
                
                # 根据文档，createNode 可以直接处理节点类型匹配
                desired_cat = self._desired_category_from_hint(type_hint, network)
                if desired_cat is None:
                    # 如果无法识别类别，尝试使用当前网络的类别
                    desired_cat = network.childTypeCategory() if network else None
                    if desired_cat is None:
                        messages.append(f"[{node_id}] 无法识别类别: {type_hint}")
                        continue
                
                network = self._ensure_target_network(network, desired_cat)
                
                node_name = spec.get("name")
                safe_name = self._sanitize_node_name(node_name)
                
                # 直接使用 createNode，让它自己处理类型匹配
                try:
                    new_node = network.createNode(
                        type_hint,  # 直接传原始类型名
                        safe_name,
                        run_init_scripts=True,
                        load_contents=True,
                        exact_type_name=False,  # 允许模糊匹配
                        force_valid_node_name=True  # 自动清理无效节点名
                    )
                except hou.OperationFailed as exc:
                    messages.append(f"[{node_id}] 创建失败: {type_hint} - {exc}")
                    continue
                except Exception as exc:
                    messages.append(f"[{node_id}] 创建失败: {exc}")
                    continue
                
                # 设置参数
                params = spec.get("parameters") or spec.get("parms", {})
                if isinstance(params, dict):
                    for parm_name, parm_value in params.items():
                        parm = new_node.parm(parm_name)
                        if parm is None:
                            continue
                        try:
                            parm.set(parm_value)
                        except Exception:
                            pass
                
                created[node_id] = new_node
                creation_order.append(node_id)
            
            # 建立连接
            connections = plan.get("connections", [])
            for conn in connections:
                if not isinstance(conn, dict):
                    continue
                
                src_id = conn.get("from") or conn.get("src")
                dst_id = conn.get("to") or conn.get("dst")
                input_index = int(conn.get("input", 0))
                
                src_node = created.get(src_id)
                dst_node = created.get(dst_id)
                
                if src_node and dst_node:
                    try:
                        dst_node.setInput(input_index, src_node)
                    except Exception as exc:
                        messages.append(f"连接失败 {src_id}->{dst_id}: {exc}")
            
            # 自动布局
            if created:
                network.layoutChildren()
                if creation_order:
                    last_node = created[creation_order[-1]]
                    last_node.setSelected(True, clear_all_selected=True)
                    try:
                        last_node.setDisplayFlag(True)
                        last_node.setRenderFlag(True)
                    except Exception:
                        pass
            
            summary = ", ".join(created[nid].path() for nid in creation_order if nid in created)
            if created:
                msg = f"已创建 {len(created)} 个节点: {summary}"
                if messages:
                    msg += f"\n注意: {'; '.join(messages)}"
                return True, msg
            
            return False, "未创建任何节点"
        except Exception as exc:
            # 回滚：删除已创建的节点以保持场景干净
            if created:
                print(f"[MCP Client] 创建网络异常，回滚已创建的 {len(created)} 个节点...")
                for nid in reversed(creation_order):
                    try:
                        node = created.get(nid)
                        if node and node.path():
                            node.destroy()
                    except Exception:
                        pass
            return False, f"创建网络失败（已回滚）: {exc}"

    # ========================================
    # 节点连接
    # ========================================
    
    def connect_nodes(self, output_node_path: str, input_node_path: str, 
                      input_index: int = 0) -> Tuple[bool, str]:
        """连接两个节点"""
        if hou is None:
            return False, "未检测到 Houdini API"
        
        out_node = hou.node(output_node_path)
        if out_node is None:
            return False, f"未找到输出节点: {output_node_path}"
        
        in_node = hou.node(input_node_path)
        if in_node is None:
            return False, f"未找到输入节点: {input_node_path}"
        
        try:
            in_node.setInput(int(input_index), out_node, 0)
            return True, f"已连接: {output_node_path} → {input_node_path}[{input_index}]"
        except Exception as exc:
            return False, f"连接失败: {exc}"

    # ========================================
    # 参数设置
    # ========================================
    
    def set_parameter(self, node_path: str, param_name: str, value: Any) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """设置节点参数（设置前自动快照旧值，支持撤销）
        
        Returns:
            (success, message, undo_snapshot)
            undo_snapshot 包含 node_path, param_name, old_value, new_value
        """
        if hou is None:
            return False, "未检测到 Houdini API", None
        
        node = hou.node(node_path)
        if node is None:
            return False, f"未找到节点: {node_path}", None
        
        # 尝试获取参数
        parm = node.parm(param_name)
        if parm is None:
            # 尝试作为元组参数
            parm_tuple = node.parmTuple(param_name)
            if parm_tuple is None:
                # 列出相似参数名帮助 AI 纠正
                try:
                    all_parms = [p.name() for p in node.parms()]
                    hint_lower = param_name.lower()
                    similar = [p for p in all_parms if hint_lower in p.lower() or p.lower() in hint_lower][:8]
                    err = f"节点 {node_path} 不存在参数 '{param_name}'"
                    if similar:
                        err += f"\n相似参数: {', '.join(similar)}"
                    else:
                        # 列出前 15 个参数供参考
                        sample = all_parms[:15]
                        err += f"\n该节点可用参数(前15): {', '.join(sample)}"
                        if len(all_parms) > 15:
                            err += f" ... 共 {len(all_parms)} 个"
                except Exception:
                    err = f"未找到参数: {param_name}"
                return False, err, None
            
            if isinstance(value, (list, tuple)):
                try:
                    # 快照旧值（元组参数）
                    old_value = list(parm_tuple.eval())
                    parm_tuple.set(value)
                    new_value = list(parm_tuple.eval())
                    snapshot = {
                        "node_path": node_path,
                        "param_name": param_name,
                        "old_value": old_value,
                        "new_value": new_value,
                        "is_tuple": True,
                    }
                    return True, f"已设置 {node_path} {param_name}: {old_value} → {new_value}", snapshot
                except Exception as exc:
                    return False, f"设置失败: {exc}", None
            else:
                return False, f"参数 {param_name} 需要列表或元组值", None
        
        try:
            # 快照旧值（标量参数）
            try:
                old_expr = parm.expression()
                old_lang = str(parm.expressionLanguage())
                old_value = {"expr": old_expr, "lang": old_lang}
            except Exception:
                old_value = parm.eval()
            
            parm.set(value)
            actual_value = parm.eval()
            snapshot = {
                "node_path": node_path,
                "param_name": param_name,
                "old_value": old_value,
                "new_value": actual_value,
                "is_tuple": False,
            }
            return True, f"已设置 {node_path} {param_name}: {old_value} → {actual_value}", snapshot
        except Exception as exc:
            return False, f"设置失败: {exc}", None

    # ========================================
    # 节点删除
    # ========================================
    
    @staticmethod
    def _snapshot_node(node, _depth: int = 0) -> Optional[Dict[str, Any]]:
        """在删除前快照节点状态（用于撤销重建）
        
        ★ 递归快照：自动保存所有子节点树，确保删除父节点后可完整恢复。
        
        Args:
            node: 要快照的 Houdini 节点
            _depth: 递归深度（内部使用，防止无限递归）
        
        Returns:
            快照字典，包含重建节点及其完整子树所需的全部信息；失败返回 None
        """
        if _depth > 20:  # 防止极端嵌套导致栈溢出
            return None
        try:
            node_type = node.type()
            parent = node.parent()
            if not node_type or not parent:
                return None
            
            # 基本信息
            snapshot: Dict[str, Any] = {
                "parent_path": parent.path(),
                "node_type": node_type.name(),
                "node_name": node.name(),
                "position": [node.position()[0], node.position()[1]],
            }
            
            # 非默认参数值
            params = {}
            try:
                for parm in node.parms():
                    try:
                        # 跳过锁定/不可写参数
                        if parm.isLocked():
                            continue
                        # 只保存与默认值不同的参数
                        default = parm.parmTemplate().defaultValue()
                        current = parm.eval()
                        # 表达式优先保存
                        try:
                            expr = parm.expression()
                            if expr:
                                params[parm.name()] = {"expr": expr, "lang": str(parm.expressionLanguage())}
                                continue
                        except Exception:
                            pass
                        # 比较 float 时容忍精度误差
                        if isinstance(current, float) and isinstance(default, (float, int)):
                            if abs(current - float(default)) > 1e-9:
                                params[parm.name()] = current
                        elif current != default:
                            params[parm.name()] = current
                    except Exception:
                        continue
            except Exception:
                pass
            snapshot["params"] = params
            
            # 输入连接
            input_connections = []
            try:
                for i, conn in enumerate(node.inputs()):
                    if conn is not None:
                        input_connections.append({
                            "input_index": i,
                            "source_path": conn.path(),
                        })
            except Exception:
                pass
            snapshot["input_connections"] = input_connections
            
            # 输出连接
            output_connections = []
            try:
                for conn in node.outputConnections():
                    output_connections.append({
                        "output_index": conn.outputIndex(),
                        "dest_path": conn.outputNode().path() if conn.outputNode() else "",
                        "dest_input_index": conn.inputIndex(),
                    })
            except Exception:
                pass
            snapshot["output_connections"] = output_connections
            
            # 标志位
            try:
                snapshot["display_flag"] = node.isDisplayFlagSet() if hasattr(node, 'isDisplayFlagSet') else False
                snapshot["render_flag"] = node.isRenderFlagSet() if hasattr(node, 'isRenderFlagSet') else False
            except Exception:
                snapshot["display_flag"] = False
                snapshot["render_flag"] = False
            
            # ★ 递归快照子节点树 — 确保删除父节点后可完整恢复子节点
            children_snapshots = []
            try:
                children = node.children()
                if children:
                    for child in children:
                        try:
                            child_snap = HoudiniMCP._snapshot_node(child, _depth + 1)
                            if child_snap:
                                children_snapshots.append(child_snap)
                        except Exception:
                            continue
            except Exception:
                pass
            if children_snapshots:
                snapshot["children"] = children_snapshots
            
            # ★ 快照子节点间的内部连接（兄弟节点之间的连线）
            # 外部连接已在各子节点的 input_connections / output_connections 中记录，
            # 但恢复时子节点是逐个创建的，内部连接需要在所有子节点创建完毕后单独恢复。
            internal_connections = []
            try:
                if children:
                    child_paths = set(c.path() for c in children)
                    for child in children:
                        try:
                            for i, inp in enumerate(child.inputs()):
                                if inp is not None and inp.path() in child_paths:
                                    internal_connections.append({
                                        "src_name": inp.name(),
                                        "dest_name": child.name(),
                                        "dest_input": i,
                                    })
                        except Exception:
                            continue
            except Exception:
                pass
            if internal_connections:
                snapshot["internal_connections"] = internal_connections
            
            return snapshot
        except Exception:
            return None

    def delete_node_by_path(self, node_path: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """按路径删除节点（删除前自动快照，支持撤销重建）
        
        Returns:
            (success, message, undo_snapshot)
        """
        if hou is None:
            return False, "未检测到 Houdini API", None
        
        node = hou.node(node_path)
        if node is None:
            return False, f"未找到节点: {node_path}", None
        
        try:
            # 删除前快照（用于撤销）
            snapshot = self._snapshot_node(node)
            
            full_path = node.path()
            name = node.name()
            parent = node.parent()
            parent_path = parent.path() if parent else ""
            
            # 收集连接信息（删除前）
            input_nodes = [n.path() for n in node.inputs() if n is not None] if node.inputs() else []
            output_conns = []
            try:
                for conn in node.outputConnections():
                    out_node = conn.outputNode()
                    if out_node:
                        output_conns.append(out_node.path())
            except Exception:
                pass
            
            node.destroy()
            
            # 返回完整路径 + diff 信息
            diff_parts = [f"已删除节点: {full_path}"]
            if parent_path:
                try:
                    remaining = len(hou.node(parent_path).children()) if hou.node(parent_path) else 0
                    diff_parts.append(f"(父网络: {parent_path}, 剩余子节点: {remaining})")
                except Exception:
                    diff_parts.append(f"(父网络: {parent_path})")
            if input_nodes:
                diff_parts.append(f"原输入: {', '.join(input_nodes)}")
            if output_conns:
                diff_parts.append(f"原输出到: {', '.join(output_conns[:3])}")
            
            return True, ' '.join(diff_parts), snapshot
        except Exception as exc:
            return False, f"删除失败: {exc}", None

    def delete_selected(self) -> Tuple[bool, str]:
        """删除选中的节点"""
        if hou is None:
            return False, "未检测到 Houdini API"
        
        nodes = list(hou.selectedNodes())
        if not nodes:
            return False, "没有选中的节点"
        
        paths = [n.path() for n in nodes]
        for n in nodes:
            try:
                n.destroy()
            except Exception:
                pass
        
        return True, f"已删除 {len(paths)} 个节点"

    # ========================================
    # Python 代码执行（类似 Cursor 终端）
    # ========================================
    
    class _ExecInterrupt(Exception):
        """execute_python 超时或用户停止时抛出的中断异常"""
        pass

    def execute_python(self, code: str, timeout: int = 30) -> Tuple[bool, Dict[str, Any]]:
        """在 Houdini Python 环境中执行代码
        
        类似 Cursor 的终端功能，可以执行任意 Python 代码。
        
        Args:
            code: 要执行的 Python 代码
            timeout: 超时时间（秒）
        
        Returns:
            (success, result) 其中 result 包含:
            {
                "output": str,      # 输出内容
                "return_value": Any, # 最后一个表达式的返回值
                "error": str,       # 错误信息（如果有）
                "execution_time": float  # 执行时间（秒）
            }
        
        安全注意：
        - 此功能允许执行任意代码，应谨慎使用
        - 危险操作（如删除文件）需要用户确认
        
        ★ 超时保护（v1.4.5）：
        使用 sys.settrace 在每行 Python 代码执行前检查超时和停止标志。
        超时或用户停止时抛出 _ExecInterrupt 中断代码执行，防止卡死主线程。
        注意：对 C 扩展内部的阻塞（如 hou.node.cook）无法中断，
        但能在 C 调用返回后的下一行 Python 代码处中断。
        """
        if hou is None:
            return False, {"error": "未检测到 Houdini API"}
        
        if not code or not code.strip():
            return False, {"error": "代码为空"}
        
        import io
        import sys
        import traceback
        import threading
        
        start_time = time.time()
        _stop_event = self._stop_event  # 缓存引用
        _deadline = start_time + max(timeout, 5)  # 最少 5 秒
        _check_interval = 0.5  # 每 0.5s 检查一次（避免过于频繁）
        _last_check = [start_time]  # 用列表以便在闭包中修改
        
        def _trace_timeout(frame, event, arg):
            """sys.settrace 回调：每行代码执行前检查超时和停止标志"""
            now = time.time()
            # 降低检查频率：距上次检查不足 _check_interval 则跳过
            if now - _last_check[0] < _check_interval:
                return _trace_timeout
            _last_check[0] = now
            # 检查停止标志
            if _stop_event and _stop_event.is_set():
                raise HoudiniMCP._ExecInterrupt("用户已停止执行")
            # 检查超时
            if now > _deadline:
                raise HoudiniMCP._ExecInterrupt(
                    f"代码执行超时（{timeout}s），已中断。"
                    f"如需更长时间，请增加 timeout 参数。"
                )
            return _trace_timeout
        
        # 捕获输出
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        old_trace = sys.gettrace()
        captured_output = io.StringIO()
        captured_error = io.StringIO()
        
        result = {
            "output": "",
            "return_value": None,
            "error": "",
            "execution_time": 0.0
        }
        
        try:
            sys.stdout = captured_output
            sys.stderr = captured_error
            
            # 准备执行环境
            exec_globals = {
                'hou': hou,
                '__builtins__': __builtins__,
            }
            exec_locals = {}
            
            # ★ 安装超时 trace
            sys.settrace(_trace_timeout)
            
            # 尝试作为表达式求值（返回最后一个值）
            try:
                # 先尝试 eval（单个表达式）
                return_value = eval(code.strip(), exec_globals, exec_locals)
                result["return_value"] = self._safe_repr(return_value)
            except SyntaxError:
                # 不是单个表达式，用 exec 执行
                exec(code, exec_globals, exec_locals)
                
                # 尝试获取最后一个赋值的值
                if exec_locals:
                    last_var = list(exec_locals.keys())[-1]
                    if not last_var.startswith('_'):
                        result["return_value"] = self._safe_repr(exec_locals[last_var])
            
            result["output"] = captured_output.getvalue()
            
            # 检查 stderr
            stderr_content = captured_error.getvalue()
            if stderr_content:
                result["output"] += f"\n[stderr]\n{stderr_content}"
            
            result["execution_time"] = time.time() - start_time
            return True, result
        
        except HoudiniMCP._ExecInterrupt as e:
            result["error"] = str(e)
            result["output"] = captured_output.getvalue()
            result["execution_time"] = time.time() - start_time
            return False, result
            
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
            result["output"] = captured_output.getvalue()
            result["execution_time"] = time.time() - start_time
            return False, result
            
        finally:
            # ★ 必须恢复原始 trace，否则影响后续所有 Python 执行
            sys.settrace(old_trace)
            sys.stdout = old_stdout
            sys.stderr = old_stderr
    
    def _safe_repr(self, value: Any, max_length: int = 1000) -> str:
        """安全地获取对象的字符串表示"""
        try:
            # 处理常见类型
            if value is None:
                return "None"
            if isinstance(value, (int, float, bool)):
                return str(value)
            if isinstance(value, str):
                if len(value) > max_length:
                    return repr(value[:max_length] + "...")
                return repr(value)
            if isinstance(value, (list, tuple)):
                if len(value) > 10:
                    items = [self._safe_repr(v, 100) for v in value[:10]]
                    return f"[{', '.join(items)}, ... ({len(value)} items total)]"
                items = [self._safe_repr(v, 100) for v in value]
                return f"[{', '.join(items)}]"
            if isinstance(value, dict):
                if len(value) > 10:
                    items = [f"{k}: {self._safe_repr(v, 100)}" for k, v in list(value.items())[:10]]
                    return f"{{{', '.join(items)}, ... ({len(value)} items total)}}"
                items = [f"{k}: {self._safe_repr(v, 100)}" for k, v in value.items()]
                return f"{{{', '.join(items)}}}"
            
            # Houdini 对象
            if hou and hasattr(value, 'path'):
                return f"<{type(value).__name__}: {value.path()}>"
            if hou and hasattr(value, 'name'):
                return f"<{type(value).__name__}: {value.name()}>"
            
            # 默认
            s = repr(value)
            if len(s) > max_length:
                return s[:max_length] + "..."
            return s
        except Exception:
            return f"<{type(value).__name__}>"

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


    # ========================================
    # 内部辅助方法
    # ========================================
    
    def _current_network(self) -> Any:
        """获取当前网络编辑器中的网络
        
        优先级: 当前编辑器 > /obj/geo1 > /obj
        使用回退路径时会打印警告。
        """
        try:
            editor = hou.ui.curDesktop().paneTabOfType(hou.paneTabType.NetworkEditor)
            if editor:
                network = editor.pwd()
                if network:
                    return network
            # 回退到 /obj/geo1
            try:
                geo1 = hou.node('/obj/geo1')
                if geo1:
                    print("[MCP Client] ⚠️ 未找到活动网络编辑器，回退到 /obj/geo1")
                    return geo1
            except Exception:
                pass
            # 回退到 /obj
            try:
                obj = hou.node('/obj')
                if obj:
                    print("[MCP Client] ⚠️ 未找到活动网络编辑器，回退到 /obj")
                    return obj
            except Exception:
                pass
            return None
        except Exception as e:
            print(f"[MCP Client] _current_network 异常: {e}")
            try:
                geo1 = hou.node('/obj/geo1')
                if geo1:
                    return geo1
            except Exception:
                pass
            try:
                return hou.node('/obj')
            except Exception:
                return None

    def _category_from_hint(self, prefix: str) -> Any:
        """从前缀获取类别"""
        try:
            prefix_lower = (prefix or '').strip().lower()
            for name, category in hou.nodeTypeCategories().items():
                if name.lower() == prefix_lower:
                    return category
        except Exception:
            pass
        return None

    def _desired_category_from_hint(self, type_hint: str, network: Any) -> Any:
        """从类型提示获取期望的类别"""
        try:
            if "/" in (type_hint or ''):
                prefix = type_hint.split("/", 1)[0]
                return self._category_from_hint(prefix) or (network.childTypeCategory() if network else None)
            
            # 如果没有前缀，尝试根据节点名推断类别（常见SOP节点）
            hint_lower = (type_hint or '').lower().strip()
            common_sop_nodes = {
                'box', 'sphere', 'grid', 'tube', 'line', 'circle', 'font', 'curve',
                'noise', 'mountain', 'attribnoise', 'scatter', 'copytopoints', 
                'attribwrangle', 'pointwrangle', 'primitivewrangle', 'volumewrangle',
                'delete', 'blast', 'fuse', 'transform', 'subdivide', 'remesh',
                'polyextrude', 'smooth', 'relax', 'bend', 'twist', 'mountain',
                'add', 'merge', 'connect', 'group', 'partition'
            }
            if hint_lower in common_sop_nodes:
                # 这是一个SOP节点
                return hou.sopNodeTypeCategory()
            
            # 默认使用当前网络的类别
            return network.childTypeCategory() if network else None
        except Exception:
            return None

    def _ensure_target_network(self, network: Any, desired_category: Any) -> Any:
        """确保目标网络类型正确"""
        if network is None or desired_category is None:
            return network
            
        try:
            current_cat = network.childTypeCategory() if network else None
            if current_cat is None:
                return network
                
            # 如果类别匹配，直接返回
            if current_cat == desired_category:
                return network
            
            current_name = (current_cat.name().lower() if current_cat else "")
            desired_name = (desired_category.name().lower() if desired_category else "")
            
            if current_name == desired_name:
                return network
            
            # 如果在 obj 层级但需要创建 sop 节点，自动创建 geo 容器
            if current_name.startswith("object") and desired_name.startswith("sop"):
                try:
                    print(f"[MCP Client] 自动创建 geo 容器，从 {current_name} 到 {desired_name}")
                    # 根据文档，直接使用 createNode，让它自己处理匹配
                    container = network.createNode(
                        "geo",
                        None,  # 让 Houdini 自动生成名称
                        run_init_scripts=True,
                        load_contents=True,
                        exact_type_name=False,
                        force_valid_node_name=True
                    )
                    if container:
                        container.moveToGoodPosition()
                        print(f"[MCP Client] 成功创建 geo 容器: {container.path()}")
                        return container
                    else:
                        print(f"[MCP Client] 创建 geo 容器失败: 返回 None")
                        return network
                except Exception as e:
                    print(f"[MCP Client] 创建 geo 容器异常: {e}")
                    import traceback
                    traceback.print_exc()
                    return network
        except Exception as e:
            print(f"[MCP Client] _ensure_target_network 异常: {e}")
            import traceback
            traceback.print_exc()
        return network

    def _sanitize_node_name(self, name: Optional[str]) -> Optional[str]:
        """清理节点名称"""
        if not name:
            return None
        cleaned = str(name).strip()
        if not cleaned:
            return None
        cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", cleaned)
        cleaned = cleaned.strip("_") or None
        return cleaned

    # ========================================
    # Houdini 本地帮助文档查询
    # ========================================
    
    # Houdini nodeTypeCategories() 的 key 与 AI 传入的 category 映射
    _CATEGORY_MAP: Dict[str, str] = {
        "sop": "Sop", "obj": "Object", "dop": "Dop", "vop": "Vop",
        "cop": "Cop2", "cop2": "Cop2", "rop": "Driver", "driver": "Driver",
        "chop": "Chop", "shop": "Shop", "lop": "Lop", "top": "Top",
    }

    def _get_houdini_local_doc(self, node_type: str, category: str = "sop", page: int = 1) -> Tuple[bool, str]:
        """获取节点文档（多重降级策略，支持分页）

        优先级：
        1. 分页缓存（之前已获取的文档直接分页返回）
        2. Houdini 本地帮助服务器（http://127.0.0.1:{port}）
        3. SideFX 在线文档（https://www.sidefx.com/docs/houdini/）
        4. hou.NodeType.description() + 参数列表 作为最低限度的文档

        Args:
            node_type: 节点类型名
            category: 节点类别
            page: 页码（从 1 开始），大于 1 时优先从缓存读取

        Returns:
            (success, doc_text)
        """
        if hou is None:
            return False, "未检测到 Houdini API"

        type_name_lower = node_type.lower().strip()

        # ---------- 分页快速路径：缓存中已有完整文档 ----------
        cache_key = f"{category}/{node_type}".lower()
        if page > 1 and cache_key in self._doc_page_cache:
            return True, self._paginate_doc(self._doc_page_cache[cache_key], node_type, category, page)

        # ---------- 查找节点类型对象 ----------
        node_type_obj = None
        try:
            categories = hou.nodeTypeCategories()
            hou_cat_name = self._CATEGORY_MAP.get(category.lower(), category.capitalize())
            cat_obj = categories.get(hou_cat_name)
            # 如果精确匹配失败，遍历所有分类
            if cat_obj is None:
                for cname, cobj in categories.items():
                    if cname.lower() == category.lower():
                        cat_obj = cobj
                        break

            if cat_obj:
                for name, nt in cat_obj.nodeTypes().items():
                    name_low = name.lower()
                    if name_low == type_name_lower or name_low.endswith(f"::{type_name_lower}"):
                        node_type_obj = nt
                        break
            # 如果指定类别未找到，搜索全部类别
            if node_type_obj is None:
                for cname, cobj in categories.items():
                    for name, nt in cobj.nodeTypes().items():
                        name_low = name.lower()
                        if name_low == type_name_lower or name_low.endswith(f"::{type_name_lower}"):
                            node_type_obj = nt
                            # 更新 category 为实际找到的
                            for k, v in self._CATEGORY_MAP.items():
                                if v == cname:
                                    category = k
                                    break
                            break
                    if node_type_obj:
                        break
        except Exception as e:
            print(f"[MCP] 查找节点类型失败: {e}")

        # ---------- 策略 1: 本地帮助服务器 ----------
        local_result = self._fetch_local_help(node_type, category, node_type_obj, page)
        if local_result is not None:
            return True, local_result

        # ---------- 策略 2: SideFX 在线文档 ----------
        online_result = self._fetch_online_help(node_type, category, page)
        if online_result is not None:
            return True, online_result

        # ---------- 策略 3: 从 hou.NodeType 提取基本信息 ----------
        if node_type_obj is not None:
            return self._extract_type_info(node_type_obj, node_type)

        return False, f"找不到节点类型 '{node_type}' 的文档。请用 search_node_types 确认正确的节点名。"

    # ---- 帮助文档 子方法 ----

    def _html_to_text(self, html: str) -> str:
        """将 HTML 转为可读纯文本"""
        try:
            from bs4 import BeautifulSoup as BS
            soup = BS(html, 'html.parser')
            # 移除不需要的部分
            for tag in soup.find_all(['script', 'style', 'nav', 'header', 'footer']):
                tag.decompose()
            text = soup.get_text(separator='\n', strip=True)
        except Exception:
            # 无 bs4 时用正则
            text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<nav[^>]*>.*?</nav>', '', text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<header[^>]*>.*?</header>', '', text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<footer[^>]*>.*?</footer>', '', text, flags=re.DOTALL | re.IGNORECASE)
            # 块级标签换行
            text = re.sub(r'<(?:br|p|div|h[1-6]|li|tr)[^>]*>', '\n', text, flags=re.IGNORECASE)
            text = re.sub(r'<[^>]+>', ' ', text)
        # 清理多余空行
        lines = [l.strip() for l in text.split('\n')]
        lines = [l for l in lines if l]
        text = '\n'.join(lines)
        return text

    # 文档分页缓存：key = "category/node_type" → 完整纯文本
    _doc_page_cache: Dict[str, str] = {}
    _DOC_PAGE_SIZE = 2500  # 每页字符数

    def _paginate_doc(self, text: str, node_type: str, category: str, page: int = 1) -> str:
        """将文档按页返回，支持分页查看完整内容
        
        Args:
            text: 完整的纯文本文档
            node_type: 节点类型名
            category: 节点类别
            page: 页码（从 1 开始）
        """
        cache_key = f"{category}/{node_type}".lower()
        self._doc_page_cache[cache_key] = text

        total_chars = len(text)
        page_size = self._DOC_PAGE_SIZE
        total_pages = max(1, (total_chars + page_size - 1) // page_size)

        # 限制页码范围
        page = max(1, min(page, total_pages))

        start = (page - 1) * page_size
        end = min(start + page_size, total_chars)
        page_text = text[start:end]

        header = f"[{node_type} 节点文档] (第 {page}/{total_pages} 页, 共 {total_chars} 字符)\n\n"

        if total_pages == 1:
            return header + page_text
        
        if page < total_pages:
            footer = f"\n\n[第 {page}/{total_pages} 页] 还有更多内容，调用 get_houdini_node_doc(node_type=\"{node_type}\", category=\"{category}\", page={page + 1}) 查看下一页"
        else:
            footer = f"\n\n[第 {page}/{total_pages} 页 - 最后一页]"
        
        return header + page_text + footer

    def _fetch_local_help(self, node_type: str, category: str, node_type_obj, page: int = 1) -> Optional[str]:
        """从 Houdini 本地帮助服务器获取文档"""
        # 先检查分页缓存（避免重复请求）
        cache_key = f"{category}/{node_type}".lower()
        if cache_key in self._doc_page_cache and page > 1:
            return self._paginate_doc(self._doc_page_cache[cache_key], node_type, category, page)

        if not requests:
            return None
        settings = read_settings()
        help_port = getattr(settings, "help_server_port", 48626)
        help_server = f"http://127.0.0.1:{help_port}"

        # 构建 URL（优先 helpUrl，否则用标准路径）
        url_path = f"/nodes/{category.lower()}/{node_type.lower()}"
        if node_type_obj:
            try:
                help_url = node_type_obj.helpUrl()
                if help_url and not help_url.startswith(('http://', 'https://')):
                    url_path = help_url
            except Exception:
                pass
        full_url = f"{help_server}{url_path}"

        try:
            response = requests.get(full_url, timeout=5)
            if response.status_code == 200:
                text = self._html_to_text(response.text)
                if text and len(text) > 50:
                    return self._paginate_doc(text, node_type, category, page)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            pass  # 本地服务器不可用，降级到在线
        except Exception as e:
            print(f"[MCP] 本地帮助获取失败: {e}")
        return None

    def _fetch_online_help(self, node_type: str, category: str, page: int = 1) -> Optional[str]:
        """从 SideFX 在线文档获取"""
        # 先检查分页缓存
        cache_key = f"{category}/{node_type}".lower()
        if cache_key in self._doc_page_cache and page > 1:
            return self._paginate_doc(self._doc_page_cache[cache_key], node_type, category, page)

        if not requests:
            return None
        base_url = "https://www.sidefx.com/docs/houdini/"
        full_url = f"{base_url}nodes/{category.lower()}/{node_type.lower()}.html"
        try:
            response = requests.get(full_url, timeout=8)
            if response.status_code == 200:
                text = self._html_to_text(response.text)
                if text and len(text) > 50:
                    return self._paginate_doc(text, node_type, category, page)
        except Exception:
            pass
        return None

    def _extract_type_info(self, node_type_obj, node_type: str) -> Tuple[bool, str]:
        """从 hou.NodeType 对象提取基本文档信息（最后降级）"""
        try:
            label = node_type_obj.description() or node_type
            # 输入信息
            inputs = []
            try:
                input_labels = node_type_obj.inputLabels()
                for i, lbl in enumerate(input_labels):
                    inputs.append(f"  输入 {i}: {lbl}")
            except Exception:
                pass
            # 参数摘要（前 20 个）
            parms = []
            try:
                parm_templates = node_type_obj.parmTemplates()
                for pt in parm_templates[:20]:
                    parms.append(f"  {pt.name()}: {pt.label()} ({pt.type().name()})")
            except Exception:
                pass

            doc = [f"[{node_type} 节点基本信息]", f"名称: {label}"]
            if inputs:
                doc.append("输入端口:\n" + '\n'.join(inputs))
            if parms:
                doc.append(f"参数 (前{min(20, len(parms))}个):\n" + '\n'.join(parms))
            return True, '\n'.join(doc)
        except Exception as e:
            return False, f"提取节点信息失败: {e}"
    
    # 常见节点输入说明（从外部 JSON 加载，避免硬编码）
    # ========================================
    _COMMON_NODE_INPUTS: Dict[str, str] = {}

    @classmethod
    def _load_common_node_inputs(cls) -> Dict[str, str]:
        """从 node_inputs.json 懒加载常见节点输入信息"""
        if cls._COMMON_NODE_INPUTS:
            return cls._COMMON_NODE_INPUTS
        json_path = os.path.join(os.path.dirname(__file__), 'node_inputs.json')
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                cls._COMMON_NODE_INPUTS = json.load(f)
            print(f"[MCP Client] 已加载 {len(cls._COMMON_NODE_INPUTS)} 个节点输入信息")
        except FileNotFoundError:
            print(f"[MCP Client] ⚠️ 未找到 node_inputs.json: {json_path}")
        except Exception as e:
            print(f"[MCP Client] ⚠️ 加载 node_inputs.json 失败: {e}")
        return cls._COMMON_NODE_INPUTS

    def get_node_input_info(self, node_type: str, category: str = "sop") -> Tuple[bool, str]:
        """获取节点的输入端口信息（使用缓存，重要：帮助 AI 理解输入顺序）
        
        Args:
            node_type: 节点类型名称
            category: 节点类别
        
        Returns:
            (success, info) 输入端口信息
        """
        type_lower = node_type.lower()
        cache_key = f"{category}/{type_lower}"
        
        # 检查常见节点缓存（从 JSON 懒加载）
        common_inputs = self._load_common_node_inputs()
        if type_lower in common_inputs:
            return True, common_inputs[type_lower]
        
        # 检查动态缓存
        if cache_key in HoudiniMCP._common_node_inputs_cache:
            return True, HoudiniMCP._common_node_inputs_cache[cache_key]
        
        if hou is None:
            return False, "未检测到 Houdini API"
        
        try:
            # 获取节点类型
            categories = hou.nodeTypeCategories()
            cat_obj = categories.get(category.capitalize()) or categories.get(category.upper())
            if not cat_obj:
                return False, f"未找到类别: {category}"
            
            node_type_obj = None
            for name, nt in cat_obj.nodeTypes().items():
                if name.lower() == type_lower or name.lower().endswith(f"::{type_lower}"):
                    node_type_obj = nt
                    break
            
            if not node_type_obj:
                return False, f"未找到节点类型: {node_type}"
            
            # 获取输入信息
            max_inputs = node_type_obj.maxNumInputs()
            min_inputs = node_type_obj.minNumInputs()
            
            info_lines = [
                f"节点: {node_type} ({node_type_obj.description()})",
                f"输入端口数量: {min_inputs}-{max_inputs}",
                "",
                "输入端口详情:"
            ]
            
            for i in range(min(max_inputs, 6)):
                try:
                    label = node_type_obj.inputLabel(i)
                    required = i < min_inputs
                    req_str = "必需" if required else "可选"
                    info_lines.append(f"  [{i}] {label} ({req_str})")
                except Exception:
                    info_lines.append(f"  [{i}] Input {i}")
            
            result = "\n".join(info_lines)
            
            # 缓存结果
            HoudiniMCP._common_node_inputs_cache[cache_key] = result
            
            return True, result
            
        except Exception as e:
            return False, f"获取输入信息失败: {str(e)}"