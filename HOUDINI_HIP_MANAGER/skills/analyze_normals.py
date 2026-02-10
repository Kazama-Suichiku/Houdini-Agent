# -*- coding: utf-8 -*-
"""法线质量检测 Skill

全面分析 Houdini 节点几何体的法线状态，检测以下问题：
  - NO_NORMAL     : 几何体没有法线属性 (N)
  - NAN_NORMAL    : 法线包含 NaN 值
  - INF_NORMAL    : 法线包含 Inf 值
  - ZERO_NORMAL   : 零向量法线（长度为 0）
  - NON_NORMALIZED: 未归一化（长度 ≠ 1）
  - FLIPPED_FACES : 翻转的面（相邻面法线方向相反）
"""

SKILL_INFO = {
    "name": "analyze_normals",
    "description": (
        "检测几何体法线质量。检查是否存在法线属性、NaN/Inf 值、零向量、"
        "未归一化法线以及翻转面等问题。返回逐项诊断报告。"
    ),
    "parameters": {
        "node_path": {
            "type": "string",
            "description": "节点路径，如 /obj/geo1/box1",
            "required": True,
        },
        "tolerance": {
            "type": "float",
            "description": "归一化容差（默认 0.001，长度偏离 1.0 超过此值视为未归一化）",
            "required": False,
        },
        "flip_angle_threshold": {
            "type": "float",
            "description": "翻转检测角度阈值（度，默认 120，相邻面法线夹角超过此值视为翻转）",
            "required": False,
        },
        "max_sample": {
            "type": "integer",
            "description": "最大采样数（默认 200000，超过时随机采样）",
            "required": False,
        },
    },
}


# 问题严重级别
_SEVERITY = {
    "NO_NORMAL":      "WARNING",
    "NAN_NORMAL":     "ERROR",
    "INF_NORMAL":     "ERROR",
    "ZERO_NORMAL":    "WARNING",
    "NON_NORMALIZED": "INFO",
    "FLIPPED_FACES":  "WARNING",
}

_DESCRIPTIONS = {
    "NO_NORMAL":      "几何体没有法线属性",
    "NAN_NORMAL":     "法线包含 NaN 值",
    "INF_NORMAL":     "法线包含 Inf 值",
    "ZERO_NORMAL":    "零向量法线",
    "NON_NORMALIZED": "未归一化 (长度≠1)",
    "FLIPPED_FACES":  "翻转的面",
}


def run(node_path, tolerance=0.001, flip_angle_threshold=120.0, max_sample=200000):
    """入口函数

    Args:
        node_path: 节点路径
        tolerance: 归一化容差
        flip_angle_threshold: 翻转检测角度阈值（度）
        max_sample: 最大采样数
    """
    import hou  # type: ignore
    import math

    node = hou.node(node_path)
    if not node:
        return {"error": f"节点不存在: {node_path}"}

    geo = node.geometry()
    if not geo:
        return {"error": f"无法获取几何体: {node_path}"}

    point_count = geo.intrinsicValue("pointcount")
    prim_count = geo.intrinsicValue("primitivecount")

    issues = []
    summary = {"status": "OK", "total_issues": 0}

    # ---- 1. 检查法线属性是否存在 ----
    n_attrib = geo.findPointAttrib("N")
    if not n_attrib:
        issues.append({
            "type": "NO_NORMAL",
            "severity": _SEVERITY["NO_NORMAL"],
            "description": _DESCRIPTIONS["NO_NORMAL"],
            "detail": f"几何体 ({point_count} 点, {prim_count} 面) 没有点法线属性 N",
        })
        summary["status"] = "WARNING"
        summary["total_issues"] = 1
        return {
            "node_path": node_path,
            "point_count": point_count,
            "prim_count": prim_count,
            "has_normals": False,
            "issues": issues,
            "summary": summary,
        }

    # ---- 获取法线数据 ----
    try:
        raw_vals = geo.pointFloatAttribValues("N")
    except Exception as e:
        return {"error": f"读取法线数据失败: {e}"}

    n_size = n_attrib.size()
    if n_size != 3:
        return {"error": f"法线属性维度异常: 期望 3，实际 {n_size}"}

    total_points = len(raw_vals) // 3

    # 使用 numpy 加速（如果可用）
    try:
        import numpy as np
        use_numpy = True
    except ImportError:
        use_numpy = False

    if use_numpy:
        normals = np.array(raw_vals, dtype=np.float64).reshape((-1, 3))

        # 采样
        sampled = False
        max_sample = min(int(max_sample), 500000)
        if total_points > max_sample:
            idx = np.random.choice(total_points, max_sample, replace=False)
            normals_sample = normals[idx]
            sampled = True
        else:
            normals_sample = normals

        # ---- 2. NaN 检测 ----
        nan_mask = np.isnan(normals_sample).any(axis=1)
        nan_count = int(nan_mask.sum())
        if nan_count > 0:
            issues.append({
                "type": "NAN_NORMAL",
                "severity": _SEVERITY["NAN_NORMAL"],
                "description": _DESCRIPTIONS["NAN_NORMAL"],
                "count": nan_count,
                "detail": f"{nan_count} 个点的法线包含 NaN 值",
            })

        # ---- 3. Inf 检测 ----
        inf_mask = np.isinf(normals_sample).any(axis=1)
        inf_count = int(inf_mask.sum())
        if inf_count > 0:
            issues.append({
                "type": "INF_NORMAL",
                "severity": _SEVERITY["INF_NORMAL"],
                "description": _DESCRIPTIONS["INF_NORMAL"],
                "count": inf_count,
                "detail": f"{inf_count} 个点的法线包含 Inf 值",
            })

        # 过滤掉 NaN 和 Inf 后再做后续检查
        valid_mask = ~(nan_mask | inf_mask)
        valid_normals = normals_sample[valid_mask]

        if len(valid_normals) > 0:
            # 计算长度
            lengths = np.linalg.norm(valid_normals, axis=1)

            # ---- 4. 零向量检测 ----
            zero_mask = lengths < 1e-10
            zero_count = int(zero_mask.sum())
            if zero_count > 0:
                issues.append({
                    "type": "ZERO_NORMAL",
                    "severity": _SEVERITY["ZERO_NORMAL"],
                    "description": _DESCRIPTIONS["ZERO_NORMAL"],
                    "count": zero_count,
                    "detail": f"{zero_count} 个零向量",
                })

            # ---- 5. 归一化检测 ----
            non_zero_lengths = lengths[~zero_mask]
            if len(non_zero_lengths) > 0:
                not_normalized = np.abs(non_zero_lengths - 1.0) > tolerance
                not_norm_count = int(not_normalized.sum())
                if not_norm_count > 0:
                    # 统计偏差
                    deviations = non_zero_lengths[not_normalized]
                    issues.append({
                        "type": "NON_NORMALIZED",
                        "severity": _SEVERITY["NON_NORMALIZED"],
                        "description": _DESCRIPTIONS["NON_NORMALIZED"],
                        "count": not_norm_count,
                        "detail": f"{not_norm_count} 个长度≠{1.0}",
                        "length_range": [float(deviations.min()), float(deviations.max())],
                        "length_mean": float(deviations.mean()),
                    })

        stats = {
            "nan_count": nan_count,
            "inf_count": inf_count,
            "zero_count": zero_count if len(valid_normals) > 0 else 0,
            "sampled": sampled,
            "sample_size": len(normals_sample),
        }

    else:
        # ---- 纯 Python 路径 (无 numpy) ----
        nan_count = 0
        inf_count = 0
        zero_count = 0
        not_norm_count = 0
        lengths_not_norm = []

        for i in range(total_points):
            if i >= max_sample:
                break
            nx = raw_vals[i * 3]
            ny = raw_vals[i * 3 + 1]
            nz = raw_vals[i * 3 + 2]

            # NaN
            if math.isnan(nx) or math.isnan(ny) or math.isnan(nz):
                nan_count += 1
                continue
            # Inf
            if math.isinf(nx) or math.isinf(ny) or math.isinf(nz):
                inf_count += 1
                continue

            length = math.sqrt(nx * nx + ny * ny + nz * nz)

            if length < 1e-10:
                zero_count += 1
            elif abs(length - 1.0) > tolerance:
                not_norm_count += 1
                lengths_not_norm.append(length)

        if nan_count > 0:
            issues.append({
                "type": "NAN_NORMAL",
                "severity": _SEVERITY["NAN_NORMAL"],
                "description": _DESCRIPTIONS["NAN_NORMAL"],
                "count": nan_count,
                "detail": f"{nan_count} 个点的法线包含 NaN 值",
            })
        if inf_count > 0:
            issues.append({
                "type": "INF_NORMAL",
                "severity": _SEVERITY["INF_NORMAL"],
                "description": _DESCRIPTIONS["INF_NORMAL"],
                "count": inf_count,
                "detail": f"{inf_count} 个点的法线包含 Inf 值",
            })
        if zero_count > 0:
            issues.append({
                "type": "ZERO_NORMAL",
                "severity": _SEVERITY["ZERO_NORMAL"],
                "description": _DESCRIPTIONS["ZERO_NORMAL"],
                "count": zero_count,
                "detail": f"{zero_count} 个零向量",
            })
        if not_norm_count > 0:
            issues.append({
                "type": "NON_NORMALIZED",
                "severity": _SEVERITY["NON_NORMALIZED"],
                "description": _DESCRIPTIONS["NON_NORMALIZED"],
                "count": not_norm_count,
                "detail": f"{not_norm_count} 个长度≠{1.0}",
                "length_range": [min(lengths_not_norm), max(lengths_not_norm)] if lengths_not_norm else [],
                "length_mean": sum(lengths_not_norm) / len(lengths_not_norm) if lengths_not_norm else 0,
            })

        stats = {
            "nan_count": nan_count,
            "inf_count": inf_count,
            "zero_count": zero_count,
            "sampled": total_points > max_sample,
            "sample_size": min(total_points, max_sample),
        }

    # ---- 6. 翻转面检测 ----
    if prim_count > 0:
        flipped_count = _check_flipped_faces(geo, prim_count, flip_angle_threshold, max_sample)
        if flipped_count > 0:
            issues.append({
                "type": "FLIPPED_FACES",
                "severity": _SEVERITY["FLIPPED_FACES"],
                "description": _DESCRIPTIONS["FLIPPED_FACES"],
                "count": flipped_count,
                "detail": f"{flipped_count} 组相邻面法线方向异常（夹角>{flip_angle_threshold}°）",
            })

    # ---- 汇总 ----
    if any(i["severity"] == "ERROR" for i in issues):
        summary["status"] = "ERROR"
    elif any(i["severity"] == "WARNING" for i in issues):
        summary["status"] = "WARNING"
    elif any(i["severity"] == "INFO" for i in issues):
        summary["status"] = "INFO"
    else:
        summary["status"] = "OK"

    summary["total_issues"] = len(issues)
    summary["point_count"] = total_points
    summary["prim_count"] = prim_count

    return {
        "node_path": node_path,
        "point_count": total_points,
        "prim_count": prim_count,
        "has_normals": True,
        "issues": issues,
        "summary": summary,
        "stats": stats,
    }


def _check_flipped_faces(geo, prim_count, angle_threshold, max_check):
    """检测翻转面：通过比较相邻面法线方向判断

    构建简易邻接关系（基于共享边/点），比较相邻面法线夹角。
    """
    import math

    cos_threshold = math.cos(math.radians(angle_threshold))
    flipped = 0

    # 构建 prim -> normal 映射（使用面中心法线或 prim N）
    prim_normals = {}
    has_prim_n = geo.findPrimAttrib("N") is not None

    check_count = min(prim_count, max_check)

    # 构建 point -> prim 邻接（用于查找共享点的面）
    point_to_prims = {}

    for i, prim in enumerate(geo.iterPrims()):
        if i >= check_count:
            break

        # 获取面法线
        if has_prim_n:
            n = prim.attribValue("N")
        else:
            # 从面的顶点位置计算法线
            verts = prim.vertices()
            if len(verts) >= 3:
                p0 = verts[0].point().position()
                p1 = verts[1].point().position()
                p2 = verts[2].point().position()
                e1 = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
                e2 = (p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2])
                n = (
                    e1[1] * e2[2] - e1[2] * e2[1],
                    e1[2] * e2[0] - e1[0] * e2[2],
                    e1[0] * e2[1] - e1[1] * e2[0],
                )
            else:
                continue

        length = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2)
        if length < 1e-10:
            continue

        prim_normals[i] = (n[0] / length, n[1] / length, n[2] / length)

        # 记录 point -> prim 映射
        for v in prim.vertices():
            pt_num = v.point().number()
            if pt_num not in point_to_prims:
                point_to_prims[pt_num] = []
            point_to_prims[pt_num].append(i)

    # 检查相邻面（共享至少一个点）
    checked_pairs = set()
    for pt_num, prim_ids in point_to_prims.items():
        for a in range(len(prim_ids)):
            for b in range(a + 1, len(prim_ids)):
                pa, pb = prim_ids[a], prim_ids[b]
                pair = (min(pa, pb), max(pa, pb))
                if pair in checked_pairs:
                    continue
                checked_pairs.add(pair)

                na = prim_normals.get(pa)
                nb = prim_normals.get(pb)
                if na is None or nb is None:
                    continue

                # 点乘判断
                dot = na[0] * nb[0] + na[1] * nb[1] + na[2] * nb[2]
                if dot < cos_threshold:
                    flipped += 1

        # 限制检查量
        if len(checked_pairs) > max_check:
            break

    return flipped
