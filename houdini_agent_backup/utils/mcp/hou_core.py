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


# ============================================================
# 节点布局工具
# ============================================================

def layout_nodes(
    parent_path: str = "",
    node_paths: Optional[List[str]] = None,
    method: str = "auto",
    spacing: float = 1.0,
) -> Tuple[bool, str, List[Dict[str, Any]]]:
    """多策略节点布局

    Args:
        parent_path: 父网络路径，留空使用当前活跃网络
        node_paths: 要布局的节点路径列表；为空时布局整个网络
        method: 布局方法 auto / grid / columns
        spacing: 间距倍率（默认 1.0）

    Returns:
        (success, message, positions_list)
        positions_list 中每项: {name, path, x, y}
    """
    if hou is None:
        return False, "Houdini 环境不可用", []

    # 解析父网络
    parent = None
    if parent_path:
        parent = hou.node(parent_path)
    if parent is None:
        # 尝试当前网络编辑器的 pwd
        try:
            editor = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
            if editor:
                parent = editor.pwd()
        except Exception:
            pass
    if parent is None:
        try:
            parent = hou.node("/obj")
        except Exception:
            pass
    if parent is None:
        return False, "未找到目标网络", []

    try:
        # ---------- 收集目标节点 ----------
        if node_paths:
            nodes = [hou.node(p) for p in node_paths if hou.node(p)]
            if not nodes:
                return False, "指定的节点路径均无效", []
        else:
            nodes = list(parent.children())
            if not nodes:
                return False, f"{parent.path()} 下没有子节点", []

        # ---------- 执行布局 ----------
        layout_method_used = method

        if method == "auto":
            if node_paths:
                # 有指定节点 → 优先用 NetworkEditor.layoutNodes
                try:
                    editor = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
                    if editor and hasattr(editor, "layoutNodes"):
                        editor.layoutNodes(nodes)
                        layout_method_used = "NetworkEditor.layoutNodes"
                    else:
                        raise AttributeError("layoutNodes 不可用")
                except Exception:
                    # 降级：逐个 moveToGoodPosition
                    for n in nodes:
                        try:
                            n.moveToGoodPosition()
                        except Exception:
                            pass
                    layout_method_used = "moveToGoodPosition"
            else:
                # 全网络 → layoutChildren（支持间距）
                h_sp = 2.0 * spacing
                v_sp = 1.0 * spacing
                try:
                    parent.layoutChildren(
                        horizontal_spacing=h_sp,
                        vertical_spacing=v_sp,
                    )
                    layout_method_used = "layoutChildren"
                except TypeError:
                    # 旧版 Houdini 可能不支持间距参数
                    parent.layoutChildren()
                    layout_method_used = "layoutChildren(no-spacing)"

        elif method == "grid":
            _layout_grid(nodes, spacing)
            layout_method_used = "grid"

        elif method == "columns":
            _layout_columns(nodes, spacing)
            layout_method_used = "columns"

        else:
            return False, f"未知布局方法: {method}", []

        # ---------- 收集结果位置 ----------
        positions = []
        for n in nodes:
            pos = n.position()
            positions.append({
                "name": n.name(),
                "path": n.path(),
                "x": round(pos[0], 3),
                "y": round(pos[1], 3),
            })

        return (
            True,
            f"已布局 {len(nodes)} 个节点（方法: {layout_method_used}）",
            positions,
        )

    except Exception as e:
        return False, f"布局失败: {e}", []


def _layout_grid(nodes: list, spacing: float = 1.0) -> None:
    """网格布局：按节点列表顺序排成 N 列网格"""
    if not nodes:
        return
    import math
    cols = max(1, int(math.ceil(math.sqrt(len(nodes)))))
    h_sp = 3.5 * spacing
    v_sp = 1.5 * spacing
    for idx, node in enumerate(nodes):
        col = idx % cols
        row = idx // cols
        node.setPosition(hou.Vector2(col * h_sp, -row * v_sp))


def _layout_columns(nodes: list, spacing: float = 1.0) -> None:
    """按拓扑深度分列布局：根节点在最上方，逐层向下排列"""
    if not nodes:
        return
    node_set = set(id(n) for n in nodes)

    # 计算每个节点的深度（输入链长度）
    depth_map: Dict[int, int] = {}

    def _depth(n) -> int:
        nid = id(n)
        if nid in depth_map:
            return depth_map[nid]
        inputs = [inp for inp in (n.inputs() or []) if inp and id(inp) in node_set]
        if not inputs:
            depth_map[nid] = 0
            return 0
        d = max(_depth(inp) for inp in inputs) + 1
        depth_map[nid] = d
        return d

    for n in nodes:
        _depth(n)

    # 按深度分组
    layers: Dict[int, list] = {}
    for n in nodes:
        d = depth_map.get(id(n), 0)
        layers.setdefault(d, []).append(n)

    h_sp = 3.5 * spacing
    v_sp = 2.0 * spacing
    for depth in sorted(layers.keys()):
        layer_nodes = layers[depth]
        for idx, node in enumerate(layer_nodes):
            x = (idx - len(layer_nodes) / 2.0 + 0.5) * h_sp
            y = -depth * v_sp
            node.setPosition(hou.Vector2(x, y))


def get_node_positions(
    parent_path: str = "",
    node_paths: Optional[List[str]] = None,
) -> Tuple[bool, str, List[Dict[str, Any]]]:
    """获取节点位置信息

    Args:
        parent_path: 父网络路径（当 node_paths 为空时使用）
        node_paths: 特定节点路径列表

    Returns:
        (success, message, positions_list)
        positions_list 每项: {name, path, x, y, type}
    """
    if hou is None:
        return False, "Houdini 环境不可用", []

    nodes = []
    if node_paths:
        for p in node_paths:
            n = hou.node(p)
            if n:
                nodes.append(n)
        if not nodes:
            return False, "指定的节点路径均无效", []
    else:
        parent = hou.node(parent_path) if parent_path else None
        if parent is None:
            try:
                editor = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
                if editor:
                    parent = editor.pwd()
            except Exception:
                pass
        if parent is None:
            return False, "未找到目标网络", []
        nodes = list(parent.children())
        if not nodes:
            return False, f"{parent.path()} 下没有子节点", []

    positions = []
    for n in nodes:
        pos = n.position()
        positions.append({
            "name": n.name(),
            "path": n.path(),
            "x": round(pos[0], 3),
            "y": round(pos[1], 3),
            "type": n.type().name(),
        })

    return True, f"获取了 {len(positions)} 个节点的位置", positions


# ============================================================
# NetworkBox 操作
# ============================================================

# NetworkBox 语义颜色预设
_BOX_COLORS: Dict[str, Tuple[float, float, float]] = {
    "input":      (0.2, 0.4, 0.8),   # 蓝色 - 数据输入
    "processing": (0.3, 0.7, 0.3),   # 绿色 - 几何处理
    "deform":     (0.8, 0.6, 0.2),   # 橙色 - 变形/动画
    "output":     (0.7, 0.2, 0.3),   # 红色 - 输出/渲染
    "simulation": (0.6, 0.3, 0.7),   # 紫色 - 物理模拟
    "utility":    (0.5, 0.5, 0.5),   # 灰色 - 辅助工具
}


def create_network_box(
    parent_path: str,
    name: str = "",
    comment: str = "",
    color_preset: str = "",
    node_paths: Optional[List[str]] = None
) -> Tuple[bool, str, Optional[Any]]:
    """创建 NetworkBox 并可选地将节点加入其中

    Args:
        parent_path: 父网络路径（如 /obj/geo1）
        name: box 名称
        comment: 注释（显示在标题栏，描述这组节点的功能）
        color_preset: 颜色预设（input/processing/deform/output/simulation/utility）
        node_paths: 要加入 box 的节点路径列表

    Returns:
        (success, message, network_box_or_None)
    """
    if hou is None:
        return False, "Houdini 环境不可用", None

    parent = hou.node(parent_path)
    if not parent:
        return False, f"父网络 '{parent_path}' 不存在", None

    try:
        box = parent.createNetworkBox(name or None)

        if comment:
            box.setComment(comment)

        # 设置颜色
        if color_preset and color_preset in _BOX_COLORS:
            r, g, b = _BOX_COLORS[color_preset]
            box.setColor(hou.Color((r, g, b)))

        # 添加节点
        added = []
        if node_paths:
            for np in node_paths:
                node = hou.node(np)
                if node:
                    box.addNode(node)
                    added.append(np)

            if added:
                box.fitAroundContents()

        msg = f"已创建 NetworkBox: {box.name()}"
        if comment:
            msg += f" ({comment})"
        if added:
            msg += f"，包含 {len(added)} 个节点"

        return True, msg, box
    except Exception as e:
        return False, f"创建 NetworkBox 失败: {e}", None


def add_nodes_to_box(
    parent_path: str,
    box_name: str,
    node_paths: List[str],
    auto_fit: bool = True
) -> Tuple[bool, str]:
    """将节点添加到已有的 NetworkBox

    Args:
        parent_path: 父网络路径
        box_name: 目标 NetworkBox 名称
        node_paths: 要添加的节点路径列表
        auto_fit: 是否自动调整 box 大小

    Returns:
        (success, message)
    """
    if hou is None:
        return False, "Houdini 环境不可用"

    parent = hou.node(parent_path)
    if not parent:
        return False, f"父网络 '{parent_path}' 不存在"

    # 查找 NetworkBox
    target_box = None
    for box in parent.networkBoxes():
        if box.name() == box_name:
            target_box = box
            break

    if not target_box:
        return False, f"未找到 NetworkBox: {box_name}"

    added = []
    for np in node_paths:
        node = hou.node(np)
        if node:
            target_box.addNode(node)
            added.append(np)

    if auto_fit and added:
        target_box.fitAroundContents()

    return True, f"已将 {len(added)} 个节点添加到 {box_name}"


def list_network_boxes(parent_path: str) -> Tuple[bool, str, List[Dict[str, Any]]]:
    """列出网络中所有 NetworkBox 及其内容

    Args:
        parent_path: 父网络路径

    Returns:
        (success, message, boxes_info_list)
    """
    if hou is None:
        return False, "Houdini 环境不可用", []

    parent = hou.node(parent_path)
    if not parent:
        return False, f"父网络 '{parent_path}' 不存在", []

    boxes_info = []
    for box in parent.networkBoxes():
        nodes = box.nodes()
        boxes_info.append({
            "name": box.name(),
            "comment": box.comment() or "",
            "node_count": len(nodes),
            "nodes": [n.path() for n in nodes],
            "minimized": box.isMinimized(),
        })

    return True, f"找到 {len(boxes_info)} 个 NetworkBox", boxes_info