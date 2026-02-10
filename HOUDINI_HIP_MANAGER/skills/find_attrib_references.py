# -*- coding: utf-8 -*-
"""属性引用查找 Skill

查找网络中所有引用了指定属性的节点，包括 VEX 代码、参数表达式、参数字符串值。
"""

SKILL_INFO = {
    "name": "find_attribute_references",
    "description": (
        "查找网络中所有引用了某个属性的节点。"
        "检查范围: VEX 代码（wrangle）、参数表达式、字符串参数值。"
        "适用于追踪属性使用、重构属性名、调试管线等场景。"
    ),
    "parameters": {
        "network_path": {
            "type": "string",
            "description": "网络路径，如 /obj/geo1",
            "required": True,
        },
        "attr_name": {
            "type": "string",
            "description": "属性名称，如 P, Cd, class, piece",
            "required": True,
        },
        "recursive": {
            "type": "boolean",
            "description": "是否递归搜索子网络，默认 False",
            "required": False,
        },
    },
}


def run(network_path, attr_name, recursive=False):
    """入口函数"""
    import hou  # type: ignore

    network = hou.node(network_path)
    if not network:
        return {"error": f"网络不存在: {network_path}"}

    if not attr_name:
        return {"error": "缺少 attr_name 参数"}

    results = []

    _VEX_TYPES = {
        "attribwrangle", "pointwrangle", "volumewrangle",
        "primitivewrangle", "vertexwrangle",
    }

    def search_in_network(net):
        for node in net.children():
            references = []

            # 检查 VEX 代码 (wrangle 节点)
            if node.type().name() in _VEX_TYPES:
                try:
                    snippet_parm = node.parm("snippet")
                    if snippet_parm:
                        vex_code = snippet_parm.eval()
                        if attr_name in vex_code:
                            lines_with_ref = []
                            for i, line in enumerate(vex_code.split("\n"), 1):
                                if attr_name in line and not line.strip().startswith("//"):
                                    lines_with_ref.append(f"L{i}: {line.strip()[:60]}")
                            if lines_with_ref:
                                references.append({
                                    "type": "VEX代码",
                                    "lines": lines_with_ref[:5],
                                })
                except Exception:
                    pass

            # 检查参数表达式和字符串参数值
            for parm in node.parms():
                # 表达式
                try:
                    expr = parm.expression()
                    if attr_name in expr:
                        references.append({
                            "type": "参数表达式",
                            "param": parm.name(),
                            "expr": expr[:80],
                        })
                except Exception:
                    pass
                # 字符串参数值
                try:
                    val = parm.eval()
                    if isinstance(val, str) and attr_name in val and parm.name() != "snippet":
                        references.append({
                            "type": "参数值",
                            "param": parm.name(),
                            "value": val[:80],
                        })
                except Exception:
                    pass

            if references:
                results.append({
                    "node": node.name(),
                    "node_type": node.type().name(),
                    "path": node.path(),
                    "references": references,
                })

            # 递归搜索子网络
            if recursive and node.children():
                search_in_network(node)

    search_in_network(network)

    return {
        "attribute": attr_name,
        "network": network_path,
        "total_references": len(results),
        "nodes": results,
    }
