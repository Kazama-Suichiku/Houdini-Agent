# -*- coding: utf-8 -*-
"""Cook 性能分析 Skill

遍历网络中所有节点，收集 cook 时间、cook 次数、几何体大小等指标，
识别瓶颈节点和几何体膨胀点，返回结构化分析报告。

不依赖 hou.perfMon，直接使用 node.lastCookTime() 等 HOM API，
适合快速诊断场景。
"""

SKILL_INFO = {
    "name": "analyze_cook_performance",
    "description": (
        "分析网络中所有节点的 cook 性能：耗时排名、几何体膨胀点、"
        "错误/警告节点、总 cook 时间统计。适用于性能诊断和优化场景。"
    ),
    "parameters": {
        "network_path": {
            "type": "string",
            "description": "网络路径，如 /obj/geo1",
            "required": True,
        },
        "top_n": {
            "type": "integer",
            "description": "返回最慢的前 N 个节点（默认 10）",
            "required": False,
        },
        "force_cook": {
            "type": "boolean",
            "description": "分析前是否强制重新 cook 以获取最新数据（默认 false）",
            "required": False,
        },
    },
}


def run(network_path, top_n=10, force_cook=False):
    """分析网络 cook 性能

    Args:
        network_path: 网络路径
        top_n: 返回最慢的前 N 个节点
        force_cook: 是否强制 cook
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
            "total_cook_time_ms": 0,
            "slow_nodes": [],
            "geometry_growth": [],
            "error_nodes": [],
            "suggestions": ["网络为空，无节点可分析。"],
        }

    # 可选：强制 cook 以获取最新数据
    if force_cook:
        # 找到 display 节点并强制 cook 整条链
        display_node = None
        for node in children:
            if node.isDisplayFlagSet():
                display_node = node
                break
        if display_node:
            try:
                display_node.cook(force=True)
            except Exception:
                pass  # cook 可能因节点错误而失败，继续分析

    # ---- 收集数据 ----
    node_data = []
    error_nodes = []

    for node in children:
        info = {
            "name": node.name(),
            "type": node.type().name(),
            "path": node.path(),
        }

        # cook 时间（毫秒）
        try:
            info["cook_time_ms"] = round(node.lastCookTime() * 1000, 3)
        except Exception:
            info["cook_time_ms"] = 0.0

        # cook 次数
        try:
            info["cook_count"] = node.cookCount()
        except Exception:
            info["cook_count"] = 0

        # 几何体大小
        try:
            geo = node.geometry()
            if geo:
                info["points"] = geo.intrinsicValue("pointcount")
                info["prims"] = geo.intrinsicValue("primitivecount")
            else:
                info["points"] = 0
                info["prims"] = 0
        except Exception:
            info["points"] = 0
            info["prims"] = 0

        # 是否 time dependent
        try:
            info["time_dependent"] = node.isTimeDependent()
        except Exception:
            info["time_dependent"] = False

        # 错误 / 警告
        has_error = False
        has_warning = False
        try:
            errs = node.errors()
            warns = node.warnings()
            has_error = bool(errs)
            has_warning = bool(warns)
        except Exception:
            pass

        if has_error or has_warning:
            error_nodes.append({
                "name": info["name"],
                "path": info["path"],
                "type": info["type"],
                "has_error": has_error,
                "has_warning": has_warning,
            })

        node_data.append(info)

    # ---- 按 cook 时间降序排列 ----
    node_data.sort(key=lambda x: x["cook_time_ms"], reverse=True)
    total_cook_time = sum(n["cook_time_ms"] for n in node_data)
    slow_nodes = node_data[:top_n]

    # ---- 检测几何体膨胀点 ----
    # 沿连接链追踪，找到输出点数远大于输入点数的节点
    geometry_growth = []
    for node_obj in children:
        try:
            inputs = node_obj.inputs()
            if not inputs:
                continue
            # 取第一个有效输入
            input_node = inputs[0]
            if input_node is None:
                continue
            out_geo = node_obj.geometry()
            in_geo = input_node.geometry()
            if out_geo is None or in_geo is None:
                continue

            out_pts = out_geo.intrinsicValue("pointcount")
            in_pts = in_geo.intrinsicValue("pointcount")

            if in_pts > 0 and out_pts > in_pts * 2:
                ratio = round(out_pts / in_pts, 2)
                geometry_growth.append({
                    "name": node_obj.name(),
                    "path": node_obj.path(),
                    "type": node_obj.type().name(),
                    "input_points": in_pts,
                    "output_points": out_pts,
                    "growth_ratio": ratio,
                })
        except Exception:
            continue

    geometry_growth.sort(key=lambda x: x["growth_ratio"], reverse=True)

    # ---- 生成建议 ----
    suggestions = []

    if slow_nodes and slow_nodes[0]["cook_time_ms"] > 100:
        top = slow_nodes[0]
        suggestions.append(
            f"最慢节点 {top['name']}({top['type']}) 耗时 {top['cook_time_ms']:.1f}ms，"
            f"考虑在其后添加 Cache 节点减少重复 cook。"
        )

    time_dep_count = sum(1 for n in node_data if n.get("time_dependent"))
    if time_dep_count > 3:
        suggestions.append(
            f"有 {time_dep_count} 个 time dependent 节点，"
            "检查是否有不必要的时间表达式导致每帧重新 cook。"
        )

    if geometry_growth:
        worst = geometry_growth[0]
        suggestions.append(
            f"节点 {worst['name']} 使几何体从 {worst['input_points']} 点膨胀到 "
            f"{worst['output_points']} 点(x{worst['growth_ratio']})，"
            "考虑降低细分/散点数量，或使用 Packed Primitives。"
        )

    if error_nodes:
        err_names = ", ".join(n["name"] for n in error_nodes[:3])
        suggestions.append(
            f"有 {len(error_nodes)} 个错误/警告节点({err_names})，"
            "错误节点可能导致上下游级联 cook 问题。"
        )

    # 检测 Python SOP（性能远低于 VEX）
    python_sops = [
        n for n in node_data
        if "python" in n["type"].lower() and n["cook_time_ms"] > 10
    ]
    if python_sops:
        names = ", ".join(n["name"] for n in python_sops[:3])
        suggestions.append(
            f"检测到 Python SOP 节点({names})，"
            "Python SOP 性能远低于 VEX，建议用 Wrangle 节点替代。"
        )

    if not suggestions:
        suggestions.append("未发现明显性能瓶颈。")

    return {
        "network": network_path,
        "total_nodes": len(node_data),
        "total_cook_time_ms": round(total_cook_time, 3),
        "bottleneck_count": sum(1 for n in node_data if n["cook_time_ms"] > 50),
        "slow_nodes": slow_nodes,
        "geometry_growth": geometry_growth[:5],
        "error_nodes": error_nodes,
        "time_dependent_count": sum(1 for n in node_data if n.get("time_dependent")),
        "suggestions": suggestions,
    }
