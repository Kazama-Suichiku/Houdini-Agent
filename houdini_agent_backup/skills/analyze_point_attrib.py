# -*- coding: utf-8 -*-
"""通用几何属性分析 Skill

分析 Houdini 节点几何体的属性统计信息，支持 point/vertex/prim/detail 四种属性类别。
不指定属性名时返回属性列表，指定时返回统计信息（min/max/mean/std/nan/inf）。
"""

SKILL_INFO = {
    "name": "analyze_geometry_attribs",
    "description": (
        "分析节点几何体属性。支持 point/vertex/prim/detail 四种类别。"
        "不传 attrib_name 时返回属性列表；传入时返回统计信息（min/max/mean/std/nan/inf）。"
    ),
    "parameters": {
        "node_path": {
            "type": "string",
            "description": "节点路径，如 /obj/geo1/box1",
            "required": True,
        },
        "attrib_name": {
            "type": "string",
            "description": "属性名（如 P, N, uv, Cd）。留空则返回该类别的属性列表",
            "required": False,
        },
        "attrib_class": {
            "type": "string",
            "description": "属性类别: point, vertex, prim, detail（默认 point）",
            "required": False,
        },
        "max_sample": {
            "type": "integer",
            "description": "最大采样数（默认 100000，超过时随机采样）",
            "required": False,
        },
    },
}


def run(node_path, attrib_name=None, attrib_class="point", max_sample=100000):
    """入口函数

    Args:
        node_path: 节点路径
        attrib_name: 属性名（None 则返回属性列表）
        attrib_class: 属性类别 - point/vertex/prim/detail
        max_sample: 最大采样数
    """
    import hou  # type: ignore
    import numpy as np

    node = hou.node(node_path)
    if not node:
        return {"error": f"节点不存在: {node_path}"}

    geo = node.geometry()
    if not geo:
        return {"error": "无法获取几何体"}

    # 属性类别映射
    attrib_map = {
        "point": (
            geo.findPointAttrib,
            geo.pointFloatAttribValues,
            geo.pointIntAttribValues,
            geo.pointStringAttribValues,
            geo.intrinsicValue("pointcount"),
        ),
        "vertex": (
            geo.findVertexAttrib,
            geo.vertexFloatAttribValues,
            geo.vertexIntAttribValues,
            geo.vertexStringAttribValues,
            geo.intrinsicValue("vertexcount"),
        ),
        "prim": (
            geo.findPrimAttrib,
            geo.primFloatAttribValues,
            geo.primIntAttribValues,
            geo.primStringAttribValues,
            geo.intrinsicValue("primitivecount"),
        ),
        "detail": (
            geo.findGlobalAttrib,
            None,
            None,
            None,
            1,
        ),
    }

    if attrib_class not in attrib_map:
        return {"error": f"无效的属性类别: {attrib_class}，可选: point, vertex, prim, detail"}

    find_func, float_func, int_func, str_func, elem_count = attrib_map[attrib_class]

    # 如果没有指定属性名，返回属性列表
    if attrib_name is None:
        attrib_list_map = {
            "point": geo.pointAttribs,
            "vertex": geo.vertexAttribs,
            "prim": geo.primAttribs,
            "detail": geo.globalAttribs,
        }
        attribs = attrib_list_map[attrib_class]()
        return {
            "node_path": node_path,
            "attrib_class": attrib_class,
            "element_count": elem_count,
            "attribs": [
                {
                    "name": a.name(),
                    "size": a.size(),
                    "type": str(a.dataType()).split(".")[-1],
                }
                for a in attribs
            ],
        }

    # 查找指定属性
    attrib = find_func(attrib_name)
    if not attrib:
        return {"error": f"属性不存在: {attrib_name} (类别: {attrib_class})"}

    size = attrib.size()
    data_type = str(attrib.dataType()).split(".")[-1]

    # Detail 属性特殊处理
    if attrib_class == "detail":
        if data_type == "Float":
            val = (
                geo.floatAttribValue(attrib_name)
                if size == 1
                else list(geo.floatListAttribValue(attrib_name))
            )
        elif data_type == "Int":
            val = (
                geo.intAttribValue(attrib_name)
                if size == 1
                else list(geo.intListAttribValue(attrib_name))
            )
        else:
            val = (
                geo.stringAttribValue(attrib_name)
                if size == 1
                else list(geo.stringListAttribValue(attrib_name))
            )
        return {
            "node_path": node_path,
            "name": attrib_name,
            "type": data_type,
            "size": size,
            "value": val,
        }

    # 获取属性值
    if data_type == "Float":
        vals = np.array(float_func(attrib_name))
    elif data_type == "Int":
        vals = np.array(int_func(attrib_name))
    else:  # String
        vals = str_func(attrib_name)
        unique = list(set(vals))
        return {
            "node_path": node_path,
            "name": attrib_name,
            "type": "String",
            "count": len(vals),
            "unique_count": len(unique),
            "unique_values": unique[:20],
        }

    # 重塑多维属性
    if size > 1:
        vals = vals.reshape((-1, size))

    # 采样（大数据量时）
    max_sample = min(int(max_sample), 500000)
    n = len(vals) if vals.ndim == 1 else vals.shape[0]
    sampled = False
    if n > max_sample:
        idx = np.random.choice(n, max_sample, replace=False)
        vals = vals[idx] if vals.ndim == 1 else vals[idx, :]
        sampled = True

    # 返回统计信息
    result = {
        "node_path": node_path,
        "name": attrib_name,
        "type": data_type,
        "size": size,
        "count": int(n),
        "sampled": sampled,
        "min": vals.min(axis=0).tolist() if size > 1 else float(vals.min()),
        "max": vals.max(axis=0).tolist() if size > 1 else float(vals.max()),
        "mean": vals.mean(axis=0).tolist() if size > 1 else float(vals.mean()),
        "std": vals.std(axis=0).tolist() if size > 1 else float(vals.std()),
    }

    # NaN/Inf 检测（仅 float）
    if data_type == "Float":
        result["nan_count"] = int(np.isnan(vals).sum())
        result["inf_count"] = int(np.isinf(vals).sum())

    return result
