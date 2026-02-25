# -*- coding: utf-8 -*-
"""节点依赖追溯 Skill

追溯节点的上游依赖树或下游影响范围，输出层级结构和可视化树形文本。
"""

SKILL_INFO = {
    "name": "trace_node_dependencies",
    "description": (
        "追溯节点的上游依赖树或下游影响范围。"
        "upstream: 查看该节点依赖了哪些上游节点；downstream: 查看修改该节点会影响哪些下游。"
        "返回层级分组列表和可视化树形文本。"
    ),
    "parameters": {
        "node_path": {
            "type": "string",
            "description": "节点路径，如 /obj/geo1/OUT",
            "required": True,
        },
        "direction": {
            "type": "string",
            "description": "追溯方向: upstream(上游依赖) 或 downstream(下游影响)，默认 upstream",
            "required": False,
        },
        "max_depth": {
            "type": "integer",
            "description": "最大追溯深度，默认 10",
            "required": False,
        },
    },
}


def run(node_path, direction="upstream", max_depth=10):
    """入口函数"""
    import hou  # type: ignore

    node = hou.node(node_path)
    if not node:
        return {"error": f"节点不存在: {node_path}"}

    max_depth = min(int(max_depth), 50)
    if direction not in ("upstream", "downstream"):
        return {"error": f"无效方向: {direction}，可选 upstream / downstream"}

    visited = set()
    levels = []

    def traverse(n, depth):
        if depth > max_depth or n.path() in visited:
            return None
        visited.add(n.path())

        connected = n.inputs() if direction == "upstream" else n.outputs()

        children = {}
        for conn in connected:
            if conn:
                child_tree = traverse(conn, depth + 1)
                if child_tree is not None:
                    children[conn.name()] = child_tree

        while len(levels) <= depth:
            levels.append([])
        levels[depth].append({
            "name": n.name(),
            "type": n.type().name(),
            "path": n.path(),
        })

        return {
            "type": n.type().name(),
            "path": n.path(),
            "connections": children,
        }

    def tree_to_text(t, indent=0):
        if t is None:
            return ""
        lines = []
        name = t["path"].split("/")[-1]
        prefix = "  " * indent + ("└─ " if indent > 0 else "")
        lines.append(f"{prefix}{name} ({t['type']})")
        for _child_name, child_tree in t.get("connections", {}).items():
            lines.append(tree_to_text(child_tree, indent + 1))
        return "\n".join(lines)

    tree = traverse(node, 0)

    return {
        "root": node.name(),
        "direction": direction,
        "total_nodes": len(visited),
        "max_depth": len(levels) - 1 if levels else 0,
        "levels": levels,
        "tree_text": tree_to_text(tree),
    }
