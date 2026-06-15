from __future__ import annotations

import os
import json
import time
from typing import Any, Optional, Dict, List, Tuple

try:
    import hou  # type: ignore
except Exception:
    hou = None  # type: ignore


class NetworkInspectMixin:
    """Network inspection: structure, node details, search, geometry."""

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

        # 检查缓存（通过实例访问，MRO 会找到 HoudiniMCP._ats_cache）
        if type_key in self._ats_cache:
            return self._ats_cache[type_key]

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
            self._ats_cache[type_key] = ats_data
            return ats_data

        except Exception:
            return {}

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

    def _get_node_types_index(self) -> Dict[str, List[Tuple[str, str, str]]]:
        """获取节点类型索引（带缓存）

        返回: {category_lower: [(type_name, description, full_path), ...]}
        """
        import time as _time
        cache_duration = 300  # 5分钟缓存

        if (self._node_types_cache is not None and
            _time.time() - self._node_types_cache_time < cache_duration):
            return self._node_types_cache

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

            type(self)._node_types_cache = index
            type(self)._node_types_cache_time = _time.time()
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
        if cache_key in self._common_node_inputs_cache:
            return True, self._common_node_inputs_cache[cache_key]

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
            type(self)._common_node_inputs_cache[cache_key] = result

            return True, result

        except Exception as e:
            return False, f"获取输入信息失败: {str(e)}"

    def _load_common_node_inputs(self) -> Dict[str, str]:
        """从 node_inputs.json 懒加载常见节点输入信息"""
        # 通过 type(self) 访问具体类（HoudiniMCP）上的缓存属性，避免循环导入
        cls = type(self)
        if cls._COMMON_NODE_INPUTS:
            return cls._COMMON_NODE_INPUTS
        json_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'node_inputs.json')
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                cls._COMMON_NODE_INPUTS = json.load(f)
            print(f"[MCP Client] 已加载 {len(cls._COMMON_NODE_INPUTS)} 个节点输入信息")
        except FileNotFoundError:
            print(f"[MCP Client] ⚠️ 未找到 node_inputs.json: {json_path}")
        except Exception as e:
            print(f"[MCP Client] ⚠️ 加载 node_inputs.json 失败: {e}")
        return cls._COMMON_NODE_INPUTS
