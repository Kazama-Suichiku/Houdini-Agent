from __future__ import annotations

from typing import Any, Optional, Dict, List, Tuple

try:
    import hou  # type: ignore
except Exception:
    hou = None  # type: ignore


class ParamOpsMixin:
    """Parameter get/set operations."""

    def set_parameter(self, node_path: str, param_name: str, value: Any) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """设置节点参数（设置前自动快照旧值，支持撤销）

        Returns:
            (success, message, undo_snapshot)
            undo_snapshot 包含 node_path, param_name, old_value, new_value
        """
        if hou is None:
            return False, "未检测到 Houdini API", None

        node = hou.node(node_path)
        if node is None:
            return False, f"未找到节点: {node_path}", None

        # 尝试获取参数
        parm = node.parm(param_name)
        if parm is None:
            # 尝试作为元组参数
            parm_tuple = node.parmTuple(param_name)
            if parm_tuple is None:
                # 列出相似参数名帮助 AI 纠正
                try:
                    all_parms = [p.name() for p in node.parms()]
                    hint_lower = param_name.lower()
                    similar = [p for p in all_parms if hint_lower in p.lower() or p.lower() in hint_lower][:8]
                    err = f"节点 {node_path} 不存在参数 '{param_name}'"
                    if similar:
                        err += f"\n相似参数: {', '.join(similar)}"
                    else:
                        # 列出前 15 个参数供参考
                        sample = all_parms[:15]
                        err += f"\n该节点可用参数(前15): {', '.join(sample)}"
                        if len(all_parms) > 15:
                            err += f" ... 共 {len(all_parms)} 个"
                except Exception:
                    err = f"未找到参数: {param_name}"
                return False, err, None

            if isinstance(value, (list, tuple)):
                try:
                    # 快照旧值（元组参数）
                    old_value = list(parm_tuple.eval())
                    parm_tuple.set(value)
                    new_value = list(parm_tuple.eval())
                    snapshot = {
                        "node_path": node_path,
                        "param_name": param_name,
                        "old_value": old_value,
                        "new_value": new_value,
                        "is_tuple": True,
                    }
                    return True, f"已设置 {node_path} {param_name}: {old_value} → {new_value}", snapshot
                except Exception as exc:
                    return False, f"设置失败: {exc}", None
            else:
                return False, f"参数 {param_name} 需要列表或元组值", None

        try:
            # 快照旧值（标量参数）
            try:
                old_expr = parm.expression()
                old_lang = str(parm.expressionLanguage())
                old_value = {"expr": old_expr, "lang": old_lang}
            except Exception:
                old_value = parm.eval()

            parm.set(value)
            actual_value = parm.eval()
            snapshot = {
                "node_path": node_path,
                "param_name": param_name,
                "old_value": old_value,
                "new_value": actual_value,
                "is_tuple": False,
            }
            return True, f"已设置 {node_path} {param_name}: {old_value} → {actual_value}", snapshot
        except Exception as exc:
            return False, f"设置失败: {exc}", None

    def batch_set_parameters(self, node_paths: List[str], param_name: str,
                             value: Any) -> Tuple[bool, str]:
        """批量设置参数"""
        if hou is None:
            return False, "未检测到 Houdini API"

        success = []
        failed = []

        for path in node_paths:
            node = hou.node(path)
            if not node:
                failed.append(f"{path}: 未找到")
                continue

            parm = node.parm(param_name)
            if not parm:
                parm_tuple = node.parmTuple(param_name)
                if parm_tuple and isinstance(value, (list, tuple)):
                    try:
                        parm_tuple.set(value)
                        success.append(node.name())
                    except Exception as e:
                        failed.append(f"{node.name()}: {e}")
                else:
                    failed.append(f"{node.name()}: 无参数 {param_name}")
                continue

            try:
                parm.set(value)
                success.append(node.name())
            except Exception as e:
                failed.append(f"{node.name()}: {e}")

        msg = f"修改成功: {len(success)} 个节点"
        if failed:
            msg += f"\n失败: {'; '.join(failed)}"

        return len(success) > 0, msg

    def find_nodes_by_param(self, param_name: str, value: Any = None,
                            network_path: Optional[str] = None,
                            recursive: bool = True) -> Tuple[bool, str]:
        """按参数值搜索节点"""
        if hou is None:
            return False, "未检测到 Houdini API"

        if network_path:
            network = hou.node(network_path)
            if not network:
                return False, f"未找到网络: {network_path}"
        else:
            network = self._current_network() or hou.node('/obj')

        results = []

        def search_in(parent):
            for node in parent.children():
                parm = node.parm(param_name)
                if parm:
                    parm_value = parm.eval()
                    if value is None or str(parm_value) == str(value):
                        results.append(f"- {node.path()}: {param_name}={parm_value}")
                if recursive and hasattr(node, 'children'):
                    search_in(node)

        search_in(network)

        if results:
            header = f"找到 {len(results)} 个节点包含参数 '{param_name}'"
            if value is not None:
                header += f" = {value}"
            return True, header + ":\n" + "\n".join(results[:50])

        return False, f"未找到包含参数 '{param_name}' 的节点"
