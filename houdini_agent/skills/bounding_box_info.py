# -*- coding: utf-8 -*-
"""边界盒信息 Skill

获取几何体的边界盒、中心点、尺寸、对角线长度、体积、表面积、长宽比等。
"""

SKILL_INFO = {
    "name": "get_bounding_info",
    "description": (
        "获取几何体的边界盒信息：min/max/center/size/对角线长度/体积/表面积/长宽比/最长轴/最短轴。"
        "适用于检查模型尺寸、对齐、缩放等场景。"
    ),
    "parameters": {
        "node_path": {
            "type": "string",
            "description": "节点路径，如 /obj/geo1/box1",
            "required": True,
        },
    },
}


def run(node_path):
    """入口函数

    Args:
        node_path: 节点路径
    """
    import hou  # type: ignore

    node = hou.node(node_path)
    if not node:
        return {"error": f"节点不存在: {node_path}"}

    geo = node.geometry()
    if not geo:
        return {"error": "无法获取几何体"}

    bbox = geo.boundingBox()

    min_pt = bbox.minvec()
    max_pt = bbox.maxvec()
    center = bbox.center()
    size = bbox.sizevec()

    diagonal = size.length()
    volume = size[0] * size[1] * size[2]
    surface_area = 2 * (size[0] * size[1] + size[1] * size[2] + size[0] * size[2])

    axes = {"X": size[0], "Y": size[1], "Z": size[2]}
    longest_axis = max(axes, key=axes.get)
    shortest_axis = min(axes, key=axes.get)

    return {
        "node_path": node_path,
        "min": [round(v, 4) for v in min_pt],
        "max": [round(v, 4) for v in max_pt],
        "center": [round(v, 4) for v in center],
        "size": [round(v, 4) for v in size],
        "diagonal": round(diagonal, 4),
        "bbox_volume": round(volume, 4),
        "bbox_surface_area": round(surface_area, 4),
        "longest_axis": longest_axis,
        "shortest_axis": shortest_axis,
        "aspect_ratio": round(max(size) / max(min(size), 0.0001), 4),
    }
