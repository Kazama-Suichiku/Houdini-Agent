# -*- coding: utf-8 -*-
"""死节点查找 Skill

查找网络中没有下游连接且不是显示/渲染节点的"死节点"。
区分完全孤立节点（无输入无输出）和链末端未使用节点（有输入无输出）。
"""

SKILL_INFO = {
    "name": "find_dead_nodes",
    "description": (
        "查找网络中的死节点（无下游连接且非显示/渲染节点）。"
        "区分孤立节点（无输入无输出）和链末端未使用节点。"
        "适用于清理网络、优化管线等场景。"
    ),
    "parameters": {
        "network_path": {
            "type": "string",
            "description": "网络路径，如 /obj/geo1",
            "required": True,
        },
    },
}


def run(network_path):
    """入口函数

    Args:
        network_path: 网络路径
    """
    import hou  # type: ignore

    network = hou.node(network_path)
    if not network:
        return {"error": f"网络不存在: {network_path}"}

    children = network.children()
    if not children:
        return {
            "network": network_path,
            "total_nodes": 0,
            "dead_node_count": 0,
            "orphan_nodes": [],
            "unused_end_nodes": [],
        }

    dead_nodes = []
    display_node = None
    render_node = None

    for node in children:
        if node.isDisplayFlagSet():
            display_node = node.path()
        if hasattr(node, 'isRenderFlagSet') and node.isRenderFlagSet():
            render_node = node.path()

    for node in children:
        outputs = node.outputs()
        is_dead = (
            len(outputs) == 0
            and not node.isDisplayFlagSet()
            and (not hasattr(node, 'isRenderFlagSet') or not node.isRenderFlagSet())
        )

        if is_dead:
            has_error = False
            try:
                has_error = bool(node.errors() or node.warnings())
            except Exception:
                pass

            dead_nodes.append({
                "name": node.name(),
                "type": node.type().name(),
                "path": node.path(),
                "has_inputs": len(node.inputs()) > 0,
                "has_error": has_error,
            })

    orphan_nodes = [n for n in dead_nodes if not n["has_inputs"]]
    end_nodes = [n for n in dead_nodes if n["has_inputs"]]

    return {
        "network": network_path,
        "total_nodes": len(children),
        "display_node": display_node,
        "render_node": render_node,
        "dead_node_count": len(dead_nodes),
        "orphan_nodes": orphan_nodes,
        "unused_end_nodes": end_nodes,
    }
