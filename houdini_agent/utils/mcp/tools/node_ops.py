from __future__ import annotations

import os
import re
import traceback
from typing import Any, Optional, Dict, List, Tuple

try:
    import hou  # type: ignore
except Exception:
    hou = None  # type: ignore


class NodeOpsMixin:
    """Node creation, connection, deletion, layout helpers."""

    def create_wrangle_node(self, vex_code: str,
                            wrangle_type: str = "attribwrangle",
                            node_name: Optional[str] = None,
                            run_over: str = "Points",
                            parent_path: Optional[str] = None) -> Tuple[bool, str]:
        """创建 Wrangle 节点并设置 VEX 代码

        这是解决几何处理问题的首选方式。

        Args:
            vex_code: VEX 代码
            wrangle_type: Wrangle 类型，默认 attribwrangle
            node_name: 节点名称（可选）
            run_over: 运行模式 (Points/Vertices/Primitives/Detail)
            parent_path: 父网络路径（可选）

        Returns:
            (success, message)
        """
        if hou is None:
            return False, "未检测到 Houdini API"

        if not vex_code or not vex_code.strip():
            return False, "VEX 代码为空"

        # 获取父网络
        if parent_path:
            network = hou.node(parent_path)
            if network is None:
                return False, f"未找到父网络: {parent_path}"
        else:
            network = self._current_network()
            if network is None:
                return False, "未找到当前网络"

        # 验证 wrangle 类型
        valid_types = ["attribwrangle", "pointwrangle", "primitivewrangle",
                       "volumewrangle", "vertexwrangle"]
        if wrangle_type not in valid_types:
            wrangle_type = "attribwrangle"

        # 确保在正确的网络层级
        network = self._ensure_target_network(network, self._category_from_hint("sop"))

        # 创建节点
        safe_name = self._sanitize_node_name(node_name)

        try:
            # 根据文档，使用 force_valid_node_name=True 自动处理无效节点名
            new_node = network.createNode(
                wrangle_type,
                safe_name,
                run_init_scripts=True,
                load_contents=True,
                exact_type_name=False,  # 允许模糊匹配
                force_valid_node_name=True  # 自动清理无效节点名
            )
        except Exception as exc:
            return False, f"创建 Wrangle 节点失败: {exc}"

        # 设置 VEX 代码
        try:
            # 大多数 Wrangle 节点的代码参数名是 "snippet"
            snippet_parm = new_node.parm("snippet")
            if snippet_parm:
                snippet_parm.set(vex_code)
            else:
                # 某些节点可能用 "code" 或 "vexcode"
                for parm_name in ["code", "vexcode", "vex_code"]:
                    parm = new_node.parm(parm_name)
                    if parm:
                        parm.set(vex_code)
                        break
        except Exception as exc:
            return False, f"设置 VEX 代码失败: {exc}"

        # 设置运行模式（与 Houdini Attrib Wrangle parm("class") 菜单一致：0=Detail, 1=Primitives, 2=Points, 3=Vertices, 4=Numbers）
        run_over_map = {
            "Detail": 0,
            "Primitives": 1,
            "Points": 2,
            "Vertices": 3,
            "Numbers": 4,
        }
        run_over_value = run_over_map.get(run_over, 2)  # 默认 Points

        try:
            class_parm = new_node.parm("class")
            if class_parm:
                class_parm.set(run_over_value)
        except Exception:
            pass  # 某些 wrangle 类型可能没有 class 参数

        # 布局和选择
        new_node.moveToGoodPosition()
        new_node.setSelected(True, clear_all_selected=True)

        try:
            new_node.setDisplayFlag(True)
            new_node.setRenderFlag(True)
        except Exception:
            pass

        try:
            editor = hou.ui.curDesktop().paneTabOfType(hou.paneTabType.NetworkEditor)
            if editor:
                editor.homeToSelection()
        except Exception:
            pass

        # 检查是否有编译错误
        errors = []
        try:
            node_errors = new_node.errors()
            if node_errors:
                errors = list(node_errors)
        except Exception:
            pass

        if errors:
            return True, f"已创建 Wrangle 节点: {new_node.path()}\nVEX 编译警告: {'; '.join(errors)}"

        return True, f"已创建 Wrangle 节点: {new_node.path()}"

    def create_node(self, type_hint: str, node_name: Optional[str] = None,
                    parameters: Optional[Dict[str, Any]] = None,
                    parent_path: Optional[str] = None) -> Tuple[bool, str]:
        """创建单个节点"""
        if hou is None:
            return False, "未检测到 Houdini API"

        # 获取父网络
        if parent_path:
            network = hou.node(parent_path)
            if network is None:
                return False, f"未找到父网络: {parent_path}"
        else:
            network = self._current_network()
            if network is None:
                # 尝试使用默认网络
                try:
                    network = hou.node('/obj')
                    if network is None:
                        return False, "未找到当前网络，且无法访问默认网络 /obj。请确保Houdini已正确启动，或在网络编辑器中打开一个网络。"
                except Exception:
                    return False, "未找到当前网络，且无法访问默认网络。请确保Houdini已正确启动，或在网络编辑器中打开一个网络。"

        if not type_hint:
            return False, "未提供节点类型"

        # 根据文档，createNode 可以直接处理节点类型匹配，无需预先解析
        # 但我们需要确保网络类型正确
        desired_cat = self._desired_category_from_hint(type_hint, network)
        if desired_cat is None:
            # 如果无法识别类别，尝试根据节点类型推断（常见SOP节点）
            common_sop_nodes = ['box', 'sphere', 'grid', 'tube', 'line', 'circle', 'noise', 'mountain',
                              'scatter', 'copytopoints', 'attribwrangle', 'pointwrangle', 'primitivewrangle',
                              'delete', 'blast', 'fuse', 'transform', 'subdivide', 'remesh']
            if type_hint.lower() in common_sop_nodes:
                # 这是一个SOP节点，需要SOP网络
                desired_cat = hou.sopNodeTypeCategory()
            else:
                # 如果无法识别类别，尝试使用当前网络的类别
                desired_cat = network.childTypeCategory() if network else None
                if desired_cat is None:
                    return False, f"无法识别节点类别: {type_hint}"

        # 确保目标网络类型正确（会自动创建容器）
        network = self._ensure_target_network(network, desired_cat)
        if network is None:
            return False, f"无法获取或创建目标网络: {type_hint}"

        # 清理节点名（但保留原始值用于错误提示）
        safe_name = self._sanitize_node_name(node_name)

        # 根据文档，createNode 支持以下参数：
        # createNode(node_type_name, node_name=None, run_init_scripts=True,
        #            load_contents=True, exact_type_name=False, force_valid_node_name=False)
        #
        # 我们使用 force_valid_node_name=True 让 Houdini 自动处理无效节点名
        # 使用 exact_type_name=False（默认）让 Houdini 进行模糊匹配

        try:
            # 直接使用 createNode，让它自己处理类型匹配
            # 如果 node_name 无效，force_valid_node_name=True 会自动清理
            new_node = network.createNode(
                type_hint,  # 直接传原始类型名，让 Houdini 处理匹配
                safe_name,  # 如果为 None，Houdini 会自动生成名称
                run_init_scripts=True,
                load_contents=True,
                exact_type_name=False,  # 允许模糊匹配
                force_valid_node_name=True  # 自动清理无效节点名
            )
        except hou.OperationFailed as exc:
            # 提供更详细的错误信息
            error_detail = str(exc)
            current_cat = network.childTypeCategory() if network else None
            cat_name = current_cat.name().lower() if current_cat else "unknown"
            network_path = network.path() if network else "unknown"

            # 尝试提供建议
            suggestions = []
            try:
                if current_cat:
                    node_types = list(current_cat.nodeTypes().keys())
                    hint_lower = type_hint.lower()
                    for nt in node_types:
                        if hint_lower in nt.lower() or nt.lower() in hint_lower:
                            suggestions.append(nt)
                            if len(suggestions) >= 5:
                                break
            except Exception:
                pass

            error_msg = f"创建节点失败: {type_hint}\n"
            error_msg += f"错误详情: {error_detail}\n"
            error_msg += f"当前网络: {network_path} (类别: {cat_name})"
            if suggestions:
                error_msg += f"\n建议的节点类型: {', '.join(suggestions[:5])}"
            return False, error_msg
        except Exception as exc:
            error_detail = str(exc)
            network_path = network.path() if network else "unknown" if network else "None"
            error_msg = f"创建节点失败: {type_hint}\n"
            error_msg += f"错误: {error_detail}\n"
            error_msg += f"网络: {network_path}"
            # 只在调试时输出完整traceback
            if "DEBUG" in os.environ:
                error_msg += f"\n{traceback.format_exc()}"
            return False, error_msg

        # 设置参数
        if parameters and isinstance(parameters, dict):
            for parm_name, parm_value in parameters.items():
                parm = new_node.parm(parm_name)
                if parm is None:
                    parm_tuple = new_node.parmTuple(parm_name)
                    if parm_tuple and isinstance(parm_value, (list, tuple)):
                        try:
                            parm_tuple.set(parm_value)
                        except Exception:
                            pass
                    continue
                try:
                    parm.set(parm_value)
                except Exception:
                    continue

        new_node.moveToGoodPosition()
        new_node.setSelected(True, clear_all_selected=True)

        try:
            editor = hou.ui.curDesktop().paneTabOfType(hou.paneTabType.NetworkEditor)
            if editor:
                editor.homeToSelection()
        except Exception:
            pass

        # 返回节点路径 + diff 信息（让 AI 了解变化）
        node_path = new_node.path()
        diff_parts = [f"✓{node_path}"]
        try:
            parent = new_node.parent()
            if parent:
                siblings = len(parent.children())
                diff_parts.append(f"(父网络: {parent.path()}, 子节点数: {siblings})")
            # 输入连接信息
            inputs = new_node.inputs()
            if inputs:
                connected = [n.path() for n in inputs if n is not None]
                if connected:
                    diff_parts.append(f"输入: {', '.join(connected)}")
        except Exception:
            pass
        return True, ' '.join(diff_parts)

    def create_network(self, plan: Dict[str, Any]) -> Tuple[bool, str]:
        """批量创建节点网络"""
        if hou is None:
            return False, "未检测到 Houdini API"

        network = self._current_network()
        if network is None:
            return False, "未找到当前网络"

        node_specs = plan.get("nodes") if isinstance(plan, dict) else None
        if not node_specs:
            return False, "缺少 nodes 字段"

        created: Dict[str, Any] = {}
        creation_order: List[str] = []
        messages: List[str] = []

        try:
            # 检测是否需要自动创建容器
            current_cat = network.childTypeCategory()
            current_cat_name = current_cat.name().lower() if current_cat else ""

            has_sop_node = any(
                isinstance(spec, dict) and
                str(spec.get("type", "")).lower().startswith("sop/")
                for spec in node_specs
            )

            if has_sop_node and current_cat_name.startswith("object"):
                try:
                    # 根据文档，直接使用 createNode，让它自己处理匹配
                    auto_container = network.createNode(
                        "geo",
                        None,  # 让 Houdini 自动生成名称
                        run_init_scripts=True,
                        load_contents=True,
                        exact_type_name=False,
                        force_valid_node_name=True
                    )
                    auto_container.moveToGoodPosition()
                    messages.append(f"自动创建容器: {auto_container.name()}")
                    network = auto_container
                except Exception as exc:
                    messages.append(f"创建容器失败: {exc}")

            # 创建节点
            for idx, spec in enumerate(node_specs):
                if not isinstance(spec, dict):
                    continue

                node_id = spec.get("id") or spec.get("name") or f"node_{idx+1}"
                type_hint = spec.get("type") or spec.get("node_type")

                if not type_hint:
                    messages.append(f"[{node_id}] 缺少 type")
                    continue

                # 根据文档，createNode 可以直接处理节点类型匹配
                desired_cat = self._desired_category_from_hint(type_hint, network)
                if desired_cat is None:
                    # 如果无法识别类别，尝试使用当前网络的类别
                    desired_cat = network.childTypeCategory() if network else None
                    if desired_cat is None:
                        messages.append(f"[{node_id}] 无法识别类别: {type_hint}")
                        continue

                network = self._ensure_target_network(network, desired_cat)

                node_name = spec.get("name")
                safe_name = self._sanitize_node_name(node_name)

                # 直接使用 createNode，让它自己处理类型匹配
                try:
                    new_node = network.createNode(
                        type_hint,  # 直接传原始类型名
                        safe_name,
                        run_init_scripts=True,
                        load_contents=True,
                        exact_type_name=False,  # 允许模糊匹配
                        force_valid_node_name=True  # 自动清理无效节点名
                    )
                except hou.OperationFailed as exc:
                    messages.append(f"[{node_id}] 创建失败: {type_hint} - {exc}")
                    continue
                except Exception as exc:
                    messages.append(f"[{node_id}] 创建失败: {exc}")
                    continue

                # 设置参数
                params = spec.get("parameters") or spec.get("parms", {})
                if isinstance(params, dict):
                    for parm_name, parm_value in params.items():
                        parm = new_node.parm(parm_name)
                        if parm is None:
                            continue
                        try:
                            parm.set(parm_value)
                        except Exception:
                            pass

                created[node_id] = new_node
                creation_order.append(node_id)

            # 建立连接
            connections = plan.get("connections", [])
            for conn in connections:
                if not isinstance(conn, dict):
                    continue

                src_id = conn.get("from") or conn.get("src")
                dst_id = conn.get("to") or conn.get("dst")
                input_index = int(conn.get("input", 0))

                src_node = created.get(src_id)
                dst_node = created.get(dst_id)

                if src_node and dst_node:
                    try:
                        dst_node.setInput(input_index, src_node)
                    except Exception as exc:
                        messages.append(f"连接失败 {src_id}->{dst_id}: {exc}")

            # 自动布局
            if created:
                network.layoutChildren()
                if creation_order:
                    last_node = created[creation_order[-1]]
                    last_node.setSelected(True, clear_all_selected=True)
                    try:
                        last_node.setDisplayFlag(True)
                        last_node.setRenderFlag(True)
                    except Exception:
                        pass

            summary = ", ".join(created[nid].path() for nid in creation_order if nid in created)
            if created:
                msg = f"已创建 {len(created)} 个节点: {summary}"
                if messages:
                    msg += f"\n注意: {'; '.join(messages)}"
                return True, msg

            return False, "未创建任何节点"
        except Exception as exc:
            # 回滚：删除已创建的节点以保持场景干净
            if created:
                print(f"[MCP Client] 创建网络异常，回滚已创建的 {len(created)} 个节点...")
                for nid in reversed(creation_order):
                    try:
                        node = created.get(nid)
                        if node and node.path():
                            node.destroy()
                    except Exception:
                        pass
            return False, f"创建网络失败（已回滚）: {exc}"

    def connect_nodes(self, output_node_path: str, input_node_path: str,
                      input_index: int = 0) -> Tuple[bool, str]:
        """连接两个节点"""
        if hou is None:
            return False, "未检测到 Houdini API"

        out_node = hou.node(output_node_path)
        if out_node is None:
            return False, f"未找到输出节点: {output_node_path}"

        in_node = hou.node(input_node_path)
        if in_node is None:
            return False, f"未找到输入节点: {input_node_path}"

        try:
            in_node.setInput(int(input_index), out_node, 0)
            return True, f"已连接: {output_node_path} → {input_node_path}[{input_index}]"
        except Exception as exc:
            return False, f"连接失败: {exc}"

    @staticmethod
    def _snapshot_node(node, _depth: int = 0) -> Optional[Dict[str, Any]]:
        """在删除前快照节点状态（用于撤销重建）

        ★ 递归快照：自动保存所有子节点树，确保删除父节点后可完整恢复。

        Args:
            node: 要快照的 Houdini 节点
            _depth: 递归深度（内部使用，防止无限递归）

        Returns:
            快照字典，包含重建节点及其完整子树所需的全部信息；失败返回 None
        """
        if _depth > 20:  # 防止极端嵌套导致栈溢出
            return None
        try:
            node_type = node.type()
            parent = node.parent()
            if not node_type or not parent:
                return None

            # 基本信息
            snapshot: Dict[str, Any] = {
                "parent_path": parent.path(),
                "node_type": node_type.name(),
                "node_name": node.name(),
                "position": [node.position()[0], node.position()[1]],
            }

            # 非默认参数值
            params = {}
            try:
                for parm in node.parms():
                    try:
                        # 跳过锁定/不可写参数
                        if parm.isLocked():
                            continue
                        # 只保存与默认值不同的参数
                        default = parm.parmTemplate().defaultValue()
                        current = parm.eval()
                        # 表达式优先保存
                        try:
                            expr = parm.expression()
                            if expr:
                                params[parm.name()] = {"expr": expr, "lang": str(parm.expressionLanguage())}
                                continue
                        except Exception:
                            pass
                        # 比较 float 时容忍精度误差
                        if isinstance(current, float) and isinstance(default, (float, int)):
                            if abs(current - float(default)) > 1e-9:
                                params[parm.name()] = current
                        elif current != default:
                            params[parm.name()] = current
                    except Exception:
                        continue
            except Exception:
                pass
            snapshot["params"] = params

            # 输入连接
            input_connections = []
            try:
                for i, conn in enumerate(node.inputs()):
                    if conn is not None:
                        input_connections.append({
                            "input_index": i,
                            "source_path": conn.path(),
                        })
            except Exception:
                pass
            snapshot["input_connections"] = input_connections

            # 输出连接
            output_connections = []
            try:
                for conn in node.outputConnections():
                    output_connections.append({
                        "output_index": conn.outputIndex(),
                        "dest_path": conn.outputNode().path() if conn.outputNode() else "",
                        "dest_input_index": conn.inputIndex(),
                    })
            except Exception:
                pass
            snapshot["output_connections"] = output_connections

            # 标志位
            try:
                snapshot["display_flag"] = node.isDisplayFlagSet() if hasattr(node, 'isDisplayFlagSet') else False
                snapshot["render_flag"] = node.isRenderFlagSet() if hasattr(node, 'isRenderFlagSet') else False
            except Exception:
                snapshot["display_flag"] = False
                snapshot["render_flag"] = False

            # ★ 递归快照子节点树 — 确保删除父节点后可完整恢复子节点
            children_snapshots = []
            try:
                children = node.children()
                if children:
                    for child in children:
                        try:
                            child_snap = NodeOpsMixin._snapshot_node(child, _depth + 1)
                            if child_snap:
                                children_snapshots.append(child_snap)
                        except Exception:
                            continue
            except Exception:
                pass
            if children_snapshots:
                snapshot["children"] = children_snapshots

            # ★ 快照子节点间的内部连接（兄弟节点之间的连线）
            # 外部连接已在各子节点的 input_connections / output_connections 中记录，
            # 但恢复时子节点是逐个创建的，内部连接需要在所有子节点创建完毕后单独恢复。
            internal_connections = []
            try:
                if children:
                    child_paths = set(c.path() for c in children)
                    for child in children:
                        try:
                            for i, inp in enumerate(child.inputs()):
                                if inp is not None and inp.path() in child_paths:
                                    internal_connections.append({
                                        "src_name": inp.name(),
                                        "dest_name": child.name(),
                                        "dest_input": i,
                                    })
                        except Exception:
                            continue
            except Exception:
                pass
            if internal_connections:
                snapshot["internal_connections"] = internal_connections

            return snapshot
        except Exception:
            return None

    def delete_node_by_path(self, node_path: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """按路径删除节点（删除前自动快照，支持撤销重建）

        Returns:
            (success, message, undo_snapshot)
        """
        if hou is None:
            return False, "未检测到 Houdini API", None

        node = hou.node(node_path)
        if node is None:
            return False, f"未找到节点: {node_path}", None

        try:
            # 删除前快照（用于撤销）
            snapshot = self._snapshot_node(node)

            full_path = node.path()
            name = node.name()
            parent = node.parent()
            parent_path = parent.path() if parent else ""

            # 收集连接信息（删除前）
            input_nodes = [n.path() for n in node.inputs() if n is not None] if node.inputs() else []
            output_conns = []
            try:
                for conn in node.outputConnections():
                    out_node = conn.outputNode()
                    if out_node:
                        output_conns.append(out_node.path())
            except Exception:
                pass

            node.destroy()

            # 返回完整路径 + diff 信息
            diff_parts = [f"已删除节点: {full_path}"]
            if parent_path:
                try:
                    remaining = len(hou.node(parent_path).children()) if hou.node(parent_path) else 0
                    diff_parts.append(f"(父网络: {parent_path}, 剩余子节点: {remaining})")
                except Exception:
                    diff_parts.append(f"(父网络: {parent_path})")
            if input_nodes:
                diff_parts.append(f"原输入: {', '.join(input_nodes)}")
            if output_conns:
                diff_parts.append(f"原输出到: {', '.join(output_conns[:3])}")

            return True, ' '.join(diff_parts), snapshot
        except Exception as exc:
            return False, f"删除失败: {exc}", None

    def delete_selected(self) -> Tuple[bool, str]:
        """删除选中的节点"""
        if hou is None:
            return False, "未检测到 Houdini API"

        nodes = list(hou.selectedNodes())
        if not nodes:
            return False, "没有选中的节点"

        paths = [n.path() for n in nodes]
        for n in nodes:
            try:
                n.destroy()
            except Exception:
                pass

        return True, f"已删除 {len(paths)} 个节点"

    def copy_node(self, source_path: str, dest_network: Optional[str] = None,
                  new_name: Optional[str] = None) -> Tuple[bool, str]:
        """复制节点"""
        if hou is None:
            return False, "未检测到 Houdini API"

        source = hou.node(source_path)
        if not source:
            return False, f"未找到源节点: {source_path}"

        if dest_network:
            dest = hou.node(dest_network)
            if not dest:
                return False, f"未找到目标网络: {dest_network}"
        else:
            dest = source.parent()

        try:
            new_node = hou.copyNodesTo([source], dest)[0]
            if new_name:
                new_node.setName(new_name)
            new_node.moveToGoodPosition()
            return True, f"已复制节点到: {new_node.path()}"
        except Exception as e:
            return False, f"复制失败: {str(e)}"

    def set_display_flag(self, node_path: str, display: bool = True,
                         render: bool = True) -> Tuple[bool, str]:
        """设置显示/渲染标志"""
        if hou is None:
            return False, "未检测到 Houdini API"

        node = hou.node(node_path)
        if not node:
            return False, f"未找到节点: {node_path}"

        try:
            if display and hasattr(node, 'setDisplayFlag'):
                node.setDisplayFlag(True)
            if render and hasattr(node, 'setRenderFlag'):
                node.setRenderFlag(True)

            flags = []
            if display:
                flags.append("显示")
            if render:
                flags.append("渲染")

            return True, f"已设置 {node.name()} 为{'/'.join(flags)}节点"
        except Exception as e:
            return False, f"设置标志失败: {str(e)}"

    def _current_network(self) -> Any:
        """获取当前网络编辑器中的网络

        优先级: 当前编辑器 > /obj/geo1 > /obj
        使用回退路径时会打印警告。
        """
        try:
            editor = hou.ui.curDesktop().paneTabOfType(hou.paneTabType.NetworkEditor)
            if editor:
                network = editor.pwd()
                if network:
                    return network
            # 回退到 /obj/geo1
            try:
                geo1 = hou.node('/obj/geo1')
                if geo1:
                    print("[MCP Client] ⚠️ 未找到活动网络编辑器，回退到 /obj/geo1")
                    return geo1
            except Exception:
                pass
            # 回退到 /obj
            try:
                obj = hou.node('/obj')
                if obj:
                    print("[MCP Client] ⚠️ 未找到活动网络编辑器，回退到 /obj")
                    return obj
            except Exception:
                pass
            return None
        except Exception as e:
            print(f"[MCP Client] _current_network 异常: {e}")
            try:
                geo1 = hou.node('/obj/geo1')
                if geo1:
                    return geo1
            except Exception:
                pass
            try:
                return hou.node('/obj')
            except Exception:
                return None

    def _category_from_hint(self, prefix: str) -> Any:
        """从前缀获取类别"""
        try:
            prefix_lower = (prefix or '').strip().lower()
            for name, category in hou.nodeTypeCategories().items():
                if name.lower() == prefix_lower:
                    return category
        except Exception:
            pass
        return None

    def _desired_category_from_hint(self, type_hint: str, network: Any) -> Any:
        """从类型提示获取期望的类别"""
        try:
            if "/" in (type_hint or ''):
                prefix = type_hint.split("/", 1)[0]
                return self._category_from_hint(prefix) or (network.childTypeCategory() if network else None)

            # 如果没有前缀，尝试根据节点名推断类别（常见SOP节点）
            hint_lower = (type_hint or '').lower().strip()
            common_sop_nodes = {
                'box', 'sphere', 'grid', 'tube', 'line', 'circle', 'font', 'curve',
                'noise', 'mountain', 'attribnoise', 'scatter', 'copytopoints',
                'attribwrangle', 'pointwrangle', 'primitivewrangle', 'volumewrangle',
                'delete', 'blast', 'fuse', 'transform', 'subdivide', 'remesh',
                'polyextrude', 'smooth', 'relax', 'bend', 'twist', 'mountain',
                'add', 'merge', 'connect', 'group', 'partition'
            }
            if hint_lower in common_sop_nodes:
                # 这是一个SOP节点
                return hou.sopNodeTypeCategory()

            # 默认使用当前网络的类别
            return network.childTypeCategory() if network else None
        except Exception:
            return None

    def _ensure_target_network(self, network: Any, desired_category: Any) -> Any:
        """确保目标网络类型正确"""
        if network is None or desired_category is None:
            return network

        try:
            current_cat = network.childTypeCategory() if network else None
            if current_cat is None:
                return network

            # 如果类别匹配，直接返回
            if current_cat == desired_category:
                return network

            current_name = (current_cat.name().lower() if current_cat else "")
            desired_name = (desired_category.name().lower() if desired_category else "")

            if current_name == desired_name:
                return network

            # 如果在 obj 层级但需要创建 sop 节点，自动创建 geo 容器
            if current_name.startswith("object") and desired_name.startswith("sop"):
                try:
                    print(f"[MCP Client] 自动创建 geo 容器，从 {current_name} 到 {desired_name}")
                    # 根据文档，直接使用 createNode，让它自己处理匹配
                    container = network.createNode(
                        "geo",
                        None,  # 让 Houdini 自动生成名称
                        run_init_scripts=True,
                        load_contents=True,
                        exact_type_name=False,
                        force_valid_node_name=True
                    )
                    if container:
                        container.moveToGoodPosition()
                        print(f"[MCP Client] 成功创建 geo 容器: {container.path()}")
                        return container
                    else:
                        print(f"[MCP Client] 创建 geo 容器失败: 返回 None")
                        return network
                except Exception as e:
                    print(f"[MCP Client] 创建 geo 容器异常: {e}")
                    import traceback
                    traceback.print_exc()
                    return network
        except Exception as e:
            print(f"[MCP Client] _ensure_target_network 异常: {e}")
            import traceback
            traceback.print_exc()
        return network

    def _sanitize_node_name(self, name: Optional[str]) -> Optional[str]:
        """清理节点名称"""
        if not name:
            return None
        cleaned = str(name).strip()
        if not cleaned:
            return None
        cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", cleaned)
        cleaned = cleaned.strip("_") or None
        return cleaned

    def undo_redo(self, action: str) -> Tuple[bool, str]:
        """撤销/重做"""
        if hou is None:
            return False, "未检测到 Houdini API"

        try:
            if action == "undo":
                hou.undos.performUndo()
                return True, "已撤销"
            elif action == "redo":
                hou.undos.performRedo()
                return True, "已重做"
            else:
                return False, f"未知操作: {action}"
        except Exception as e:
            return False, f"操作失败: {str(e)}"
