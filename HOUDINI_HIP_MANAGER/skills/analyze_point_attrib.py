# -*- coding: utf-8 -*-
"""閫氱敤鍑犱綍灞炴€у垎鏋?Skill

鍒嗘瀽 Houdini 鑺傜偣鍑犱綍浣撶殑灞炴€х粺璁′俊鎭紝鏀寔 point/vertex/prim/detail 鍥涚灞炴€х被鍒€?
涓嶆寚瀹氬睘鎬у悕鏃惰繑鍥炲睘鎬у垪琛紝鎸囧畾鏃惰繑鍥炵粺璁′俊鎭紙min/max/mean/std/nan/inf锛夈€?
"""

SKILL_INFO = {
    "name": "analyze_geometry_attribs",
    "description": (
        "鍒嗘瀽鑺傜偣鍑犱綍浣撳睘鎬с€傛敮鎸?point/vertex/prim/detail 鍥涚绫诲埆銆?
        "涓嶄紶 attrib_name 鏃惰繑鍥炲睘鎬у垪琛紱浼犲叆鏃惰繑鍥炵粺璁′俊鎭紙min/max/mean/std/nan/inf锛夈€?
    ),
    "parameters": {
        "node_path": {
            "type": "string",
            "description": "鑺傜偣璺緞锛屽 /obj/geo1/box1",
            "required": True,
        },
        "attrib_name": {
            "type": "string",
            "description": "灞炴€у悕锛堝 P, N, uv, Cd锛夈€傜暀绌哄垯杩斿洖璇ョ被鍒殑灞炴€у垪琛?,
            "required": False,
        },
        "attrib_class": {
            "type": "string",
            "description": "灞炴€х被鍒? point, vertex, prim, detail锛堥粯璁?point锛?,
            "required": False,
        },
        "max_sample": {
            "type": "integer",
            "description": "鏈€澶ч噰鏍锋暟锛堥粯璁?100000锛岃秴杩囨椂闅忔満閲囨牱锛?,
            "required": False,
        },
    },
}


def run(node_path, attrib_name=None, attrib_class="point", max_sample=100000):
    """鍏ュ彛鍑芥暟

    Args:
        node_path: 鑺傜偣璺緞
        attrib_name: 灞炴€у悕锛圢one 鍒欒繑鍥炲睘鎬у垪琛級
        attrib_class: 灞炴€х被鍒?- point/vertex/prim/detail
        max_sample: 鏈€澶ч噰鏍锋暟
    """
    import hou  # type: ignore
    import numpy as np

    node = hou.node(node_path)
    if not node:
        return {"error": f"鑺傜偣涓嶅瓨鍦? {node_path}"}

    geo = node.geometry()
    if not geo:
        return {"error": "鏃犳硶鑾峰彇鍑犱綍浣?}

    # 灞炴€х被鍒槧灏?
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
        return {"error": f"鏃犳晥鐨勫睘鎬х被鍒? {attrib_class}锛屽彲閫? point, vertex, prim, detail"}

    find_func, float_func, int_func, str_func, elem_count = attrib_map[attrib_class]

    # 濡傛灉娌℃湁鎸囧畾灞炴€у悕锛岃繑鍥炲睘鎬у垪琛?
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

    # 鏌ユ壘鎸囧畾灞炴€?
    attrib = find_func(attrib_name)
    if not attrib:
        return {"error": f"灞炴€т笉瀛樺湪: {attrib_name} (绫诲埆: {attrib_class})"}

    size = attrib.size()
    data_type = str(attrib.dataType()).split(".")[-1]

    # Detail 灞炴€х壒娈婂鐞?
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

    # 鑾峰彇灞炴€у€?
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

    # 閲嶅澶氱淮灞炴€?
    if size > 1:
        vals = vals.reshape((-1, size))

    # 閲囨牱锛堝ぇ鏁版嵁閲忔椂锛?
    max_sample = min(int(max_sample), 500000)
    n = len(vals) if vals.ndim == 1 else vals.shape[0]
    sampled = False
    if n > max_sample:
        idx = np.random.choice(n, max_sample, replace=False)
        vals = vals[idx] if vals.ndim == 1 else vals[idx, :]
        sampled = True

    # 杩斿洖缁熻淇℃伅
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

    # NaN/Inf 妫€娴嬶紙浠?float锛?
    if data_type == "Float":
        result["nan_count"] = int(np.isnan(vals).sum())
        result["inf_count"] = int(np.isinf(vals).sum())

    return result



