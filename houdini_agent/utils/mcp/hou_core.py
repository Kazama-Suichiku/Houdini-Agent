# -*- coding: utf-8 -*-
"""Houdini 核心操作层 - 供 server.py 和 client.py 共享的底层 Houdini API 封装。

架构说明：
    server.py  → 面向外部 MCP 客户端（通过 HTTP），返回 {status, message, data}
    client.py  → 面向内部 AI Agent（直接 Python 调用），返回 {success, result, error}

    本模块提供底层 Houdini 操作函数，无格式化封装。
    两个上层模块通过适配层调用本模块并自行格式化返回值。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

try:
    import hou  # type: ignore
except Exception:
    hou = None  # type: ignore


def hou_available() -> bool:
    """检查 Houdini API 是否可用"""
    return hou is not None


def resolve_node(path: str) -> Optional[Any]:
    """通过路径获取节点，失败返回 None"""
    if hou is None:
        return None
    try:
        return hou.node(path)
    except Exception:
        return None


def create_node(parent_path: str, node_type: str, node_name: str = "") -> Tuple[bool, str, Optional[Any]]:
    """创建节点
    
    Returns:
        (success, message, node_or_None)
    """
    if hou is None:
        return False, "Houdini 环境不可用", None
    parent = hou.node(parent_path)
    if not parent:
        return False, f"父节点 {parent_path} 不存在", None
    try:
        node = parent.createNode(node_type, node_name or None)
        return True, f"已创建节点 {node.path()}", node
    except Exception as e:
        return False, f"创建节点失败: {e}", None


def delete_node(node_path: str) -> Tuple[bool, str]:
    """删除节点"""
    if hou is None:
        return False, "Houdini 环境不可用"
    node = hou.node(node_path)
    if not node:
        return False, f"节点 '{node_path}' 不存在"
    try:
        node.destroy()
        return True, f"已删除节点 '{node_path}'"
    except Exception as e:
        return False, f"删除失败: {e}"


def connect_nodes(output_path: str, input_path: str, input_index: int = 0) -> Tuple[bool, str]:
    """连接两个节点"""
    if hou is None:
        return False, "Houdini 环境不可用"
    output_node = hou.node(output_path)
    input_node = hou.node(input_path)
    if output_node is None:
        return False, f"输出节点 '{output_path}' 不存在"
    if input_node is None:
        return False, f"输入节点 '{input_path}' 不存在"
    max_inputs = input_node.type().maxNumInputs()
    if input_index < 0 or input_index >= max_inputs:
        return False, f"输入端口索引 {input_index} 无效 (有效范围 0~{max_inputs - 1})"
    try:
        input_node.setInput(input_index, output_node, 0)
        return True, f"已连接 {output_path} -> {input_path}[{input_index}]"
    except Exception as e:
        return False, f"连接失败: {e}"


def set_parameter(node_path: str, param_name: str, value: Any) -> Tuple[bool, str]:
    """设置节点参数"""
    if hou is None:
        return False, "Houdini 环境不可用"
    node = hou.node(node_path)
    if not node:
        return False, f"节点 '{node_path}' 不存在"
    parm = node.parm(param_name)
    if parm is None:
        # 尝试 parmTuple
        pt = node.parmTuple(param_name)
        if pt is not None:
            try:
                pt.set(value)
                return True, f"已设置 {node_path}/{param_name} = {value}"
            except Exception as e:
                return False, f"设置失败: {e}"
        return False, f"参数 '{param_name}' 不存在"
    try:
        parm.set(value)
        return True, f"已设置 {node_path}/{param_name} = {value}"
    except Exception as e:
        return False, f"设置失败: {e}"


def get_node_info(node_path: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """获取节点信息"""
    if hou is None:
        return False, "Houdini 环境不可用", None
    node = hou.node(node_path)
    if not node:
        return False, f"节点 '{node_path}' 不存在", None
    info = {
        "type": node.type().name(),
        "path": node.path(),
        "inputs": [i.path() for i in node.inputs() if i],
        "outputs": [o.path() for o in node.outputs() if o],
    }
    return True, "查询成功", info


def set_display_flag(node_path: str) -> Tuple[bool, str]:
    """设置节点显示标志"""
    if hou is None:
        return False, "Houdini 环境不可用"
    node = hou.node(node_path)
    if not node:
        return False, f"节点 '{node_path}' 不存在"
    try:
        node.setDisplayFlag(True)
        node.setRenderFlag(True)
        return True, f"已设置 {node_path} 为显示节点"
    except Exception as e:
        return False, f"设置失败: {e}"


def check_errors(node_path: Optional[str] = None) -> Tuple[bool, str, List[str]]:
    """检查节点错误"""
    if hou is None:
        return False, "Houdini 环境不可用", []
    if node_path:
        node = hou.node(node_path)
        if not node:
            return False, f"节点 '{node_path}' 不存在", []
        errors = node.errors() or []
        return True, ("存在错误" if errors else "无错误"), errors
    else:
        error_nodes = []
        for n in hou.node('/').allSubChildren():
            try:
                if n.errors():
                    error_nodes.append(n.path())
            except Exception:
                continue
        return True, (f"发现 {len(error_nodes)} 个错误节点" if error_nodes else "无错误节点"), error_nodes


def layout_children(parent_path: str) -> Tuple[bool, str]:
    """自动布局子节点"""
    if hou is None:
        return False, "Houdini 环境不可用"
    parent = hou.node(parent_path)
    if not parent:
        return False, f"节点 '{parent_path}' 不存在"
    try:
        parent.layoutChildren()
        return True, f"已自动布局 {parent_path} 的子节点"
    except Exception as e:
        return False, f"布局失败: {e}"
