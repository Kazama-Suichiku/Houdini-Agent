# -*- coding: utf-8 -*-
"""ControllerBridgeMixin — extracted from controller.py."""

import json
import os
import queue
import threading
from ..controller_shared import Slot, _BRIDGE_LOST_MSG


class ControllerBridgeMixin:
    # 只读 Houdini 工具白名单：其余所有经 Bridge 派发的工具默认视为可能修改场景，
    # 必须通过本轮目标实例/场景锁（_turn_bridge_target）校验后才允许执行。
    _BRIDGE_READONLY_TOOLS = frozenset({
        "get_network_structure", "get_node_parameters", "list_children",
        "read_selection", "search_node_types", "semantic_search_nodes",
        "find_nodes_by_param", "check_errors", "get_node_inputs",
        "get_node_positions", "list_network_boxes",
        "search_local_doc", "get_houdini_node_doc", "list_skills",
        "search_memory", "capture_viewport", "verify_and_summarize",
        "perf_start_profile", "perf_stop_and_report",
    })
    def _is_bridge_mode(self):
        return getattr(self._session, "bridge", None) is not None

    def _active_houdini_instance(self):
        for inst in self._houdini_instances:
            if inst.get("active"):
                return inst
        return self._houdini_instances[0] if self._houdini_instances else None

    def update_houdini_instances(self, instances):
        """UI 线程：更新多实例列表（由协调器心跳或扫描线程回传）。"""
        clean = [dict(x) for x in (instances or []) if isinstance(x, dict)]
        if clean != self._houdini_instances:
            self._houdini_instances = clean
            self.houdiniInstancesChanged.emit()
        active = self._active_houdini_instance()
        if active:
            new_path = active.get("path") or self._scene_path
            new_sel = active.get("selection") or self._scene_sel
            if new_path != self._scene_path or new_sel != self._scene_sel:
                self._scene_path = new_path
                self._scene_sel = new_sel
                self.sceneChanged.emit()

    @Slot(str)
    def _on_instances_scanned(self, js):
        self._scanning_instances = False
        try:
            items = json.loads(js) if js else []
        except Exception:
            items = []
        if isinstance(items, list):
            self.update_houdini_instances(items)

    @Slot()
    def scanHoudiniInstances(self):
        """后台异步扫描本机全部在线 Houdini 实例。"""
        if not self._is_bridge_mode() or self._scanning_instances:
            return
        self._scanning_instances = True

        def work():
            try:
                from houdini_agent.bridge import doctor
                items = doctor.discover_all_bridges(include_context=True)
            except Exception:
                items = []
            try:
                self._sigInstances.emit(json.dumps(items, ensure_ascii=False, default=str))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    @Slot(result="QVariantList")
    def houdiniInstanceItems(self):
        """ContextBar 实例切换下拉菜单项。"""
        items = []
        for inst in self._houdini_instances:
            lbl = inst.get("label") or ("Houdini :%s" % inst.get("port", ""))
            # 同名 .hip（如两个 untitled.hip）时附加端口号以便一眼区分
            same_label = sum(1 for x in self._houdini_instances if x.get("label") == lbl) > 1
            if same_label:
                lbl = "%s  (:%s)" % (lbl, inst.get("port", ""))
            items.append({
                "label": lbl,
                "val": str(inst.get("port", "")),
                "checked": bool(inst.get("active")),
            })
        if not items:
            items.append({"label": self.tr("没有已连接的 Houdini 实例"), "val": "__refresh__", "checked": False})
        items.append({"sep": True})
        items.append({"label": self.tr("刷新实例列表"), "val": "__refresh__", "checked": False})
        items.append({"label": self.tr("启动另一个 Houdini…"), "val": "__launch__", "checked": False})
        return items

    @Slot(str)
    def selectHoudiniInstance(self, val):
        v = str(val or "").strip()
        if v == "__refresh__":
            self.scanHoudiniInstances()
            self.refreshContext()
            return
        if v == "__launch__":
            self.requestLaunchAnotherHoudini.emit()
            return
        if not v.isdigit():
            return
        port = int(v)
        try:
            from houdini_agent.bridge import doctor
            doctor.adopt_port(port)
        except Exception:
            return
        chosen_lbl = ":%d" % port
        for inst in self._houdini_instances:
            inst["active"] = (int(inst.get("port") or 0) == port)
            if inst["active"]:
                chosen_lbl = inst.get("label") or chosen_lbl
        self.houdiniInstancesChanged.emit()
        self.toast.emit(self.tr("已切换到 ") + chosen_lbl)
        self.refreshContext()

    def _houdini_connected(self):
        """会话存在且 Bridge 仍可达。独立 exe 下关闭 Houdini 后 _session 可能仍在、
        但 bridge 已断，故用一次 ping 作为"是否真连着"的可靠信号；进程内会话无 bridge，视为已连。"""
        s = self._session
        if not s:
            return False
        b = getattr(s, "bridge", None)
        if b is None:
            return True
        try:
            return b.ping() is not None
        except Exception:
            return False

    def _prompt_open_houdini(self, what):
        """断开时引导用户打开 Houdini：说明情况 + 请求弹启动器（外部 exe 由协调器接住）。"""
        try:
            self.toast.emit(self.tr("Houdini 未连接，%s 需要先连接 Houdini，正在打开启动器…") % what)
        except Exception:
            pass
        try:
            self.requestOpenHoudini.emit()
        except Exception:
            pass

    @Slot(str)
    def focusNode(self, path):
        p = str(path or "").strip()
        # web links are not node paths — open them in the browser
        if p.startswith(("http://", "https://", "www.")):
            url = p if "://" in p else ("https://" + p)
            self._open_browser(url)
            return
        try:
            import hou
        except Exception:
            self.toast.emit("当前不在 Houdini 环境，无法定位节点")
            return
        try:
            n = hou.node(path)
            if not n:
                self.toast.emit("找不到节点：" + str(path))
                return
            n.setCurrent(True, clear_all_selected=True)
            ed = hou.ui.curDesktop().paneTabOfType(hou.paneTabType.NetworkEditor)
            if ed and n.parent():
                ed.cd(n.parent().path())
                try:
                    ed.homeToSelection()
                except Exception:
                    pass
        except Exception as e:
            print("[controller] focusNode failed:", e)
            self.toast.emit("定位节点失败：%s" % e)

    @Slot()
    def refreshContext(self):
        try:
            import hou
            net = None
            try:
                net = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor).pwd()
            except Exception:
                pass
            sel = hou.selectedNodes()
            self._scene_path = net.path() if net else "/obj"
            self._scene_sel = ("%d node%s selected" % (len(sel), "" if len(sel) == 1 else "s")) if sel else "no selection"
        except Exception:
            ctx = None
            try:
                bridge = getattr(getattr(self._session, "bridge", None), "scene_context", None)
                ctx = bridge() if bridge else None
            except Exception:
                ctx = None
            if isinstance(ctx, dict):
                self._scene_path = ctx.get("path") or "/obj"
                self._scene_sel = ctx.get("selection") or "no selection"
            else:
                self._scene_sel = "Houdini 未连接"
            self.scanHoudiniInstances()
        self.sceneChanged.emit()

    def _heal_bridge(self):
        """ping 失败后的静默自愈：扫描候选端口找活着的 Bridge 并固化端口。
        运行在工具执行线程（localhost 扫描 <1 秒），成功后刷新上下文条。"""
        try:
            from houdini_agent.bridge import doctor
            port, _info = doctor.ensure_connected(heartbeat=self._touch_activity)
        except Exception:
            return False
        if port:
            self._sigCtxRefresh.emit()
            return True
        return False

    def _probe_current_bridge_target(self):
        """读取当前活跃 Bridge 的会话与场景指纹（port / pid / hip_path / hip_name / houdini）。"""
        bridge = getattr(self._session, "bridge", None)
        if bridge is None:
            return None
        info = None
        try:
            ctx = bridge.scene_context()
            if isinstance(ctx, dict) and (ctx.get("houdini") or ctx.get("path") or ctx.get("pid")):
                info = ctx
        except Exception:
            info = None
        if not isinstance(info, dict):
            try:
                ping = bridge.ping()
                if isinstance(ping, dict) and ping.get("status") == "ok":
                    info = ping
            except Exception:
                info = None
        if not isinstance(info, dict):
            return None
        hip_path = str(info.get("hip_path") or "").strip()
        hip_name = str(info.get("hip_name") or "").strip() or (os.path.basename(hip_path) if hip_path else "")
        return {
            "port": int(getattr(bridge, "port", 0) or 0),
            "pid": int(info.get("pid") or 0) or None,
            "hip_path": hip_path,
            "hip_name": hip_name,
            "houdini": str(info.get("houdini") or "").strip(),
        }

    @staticmethod
    def _norm_hip_path(path):
        p = str(path or "").strip()
        if not p:
            return ""
        try:
            return os.path.normcase(os.path.normpath(p))
        except Exception:
            return p.lower()

    @classmethod
    def _bridge_targets_match(cls, locked, current):
        """判断当前连到的 Houdini 实例与本轮锁定的目标是否为同一个会话与场景文件。"""
        if not isinstance(locked, dict) or not isinstance(current, dict):
            return False
        l_pid, c_pid = locked.get("pid"), current.get("pid")
        if l_pid and c_pid:
            if int(l_pid) != int(c_pid):
                return False
        else:
            l_port, c_port = locked.get("port"), current.get("port")
            if l_port and c_port and int(l_port) != int(c_port):
                return False

        l_hip = cls._norm_hip_path(locked.get("hip_path"))
        c_hip = cls._norm_hip_path(current.get("hip_path"))
        if l_hip and c_hip:
            if l_hip != c_hip:
                return False
        else:
            l_name = str(locked.get("hip_name") or "").strip().lower()
            c_name = str(current.get("hip_name") or "").strip().lower()
            if l_name and c_name and l_name != c_name:
                return False
        return True

    @staticmethod
    def _format_bridge_target(target):
        if not isinstance(target, dict):
            return "未知 Houdini 实例"
        ver = target.get("houdini") or "Houdini"
        hip = target.get("hip_name") or (os.path.basename(target.get("hip_path") or "") if target.get("hip_path") else "untitled")
        parts = []
        if target.get("port"):
            parts.append(":%s" % target["port"])
        if target.get("pid"):
            parts.append("pid=%s" % target["pid"])
        suffix = (" (%s)" % ", ".join(parts)) if parts else ""
        return "%s · %s%s" % (ver, hip, suffix)

    def _check_bridge_write_target(self, tool_name):
        """Bridge 工具派发门禁：保留断连自动重连，但锁定写操作到原 Houdini 会话与场景。

        场景：Agent 正在修改 A.hip 时用户关闭了 A 窗口，自动重连落到了 B.hip（或用户中途切了窗口/换了 .hip）。
        - 读工具：允许继续检查；
        - 写工具：直接暂停并拦截，绝不把 A.hip 的写操作误发给 B.hip！
        """
        current = self._probe_current_bridge_target()
        if current is None:
            if not self._heal_bridge():
                return False, {"success": False, "error": _BRIDGE_LOST_MSG}
            current = self._probe_current_bridge_target()
            if current is None:
                return False, {"success": False, "error": _BRIDGE_LOST_MSG}

        locked = getattr(self, "_turn_bridge_target", None)
        if locked is None:
            self._turn_bridge_target = current
            return True, None

        if self._bridge_targets_match(locked, current):
            # 同一实例/场景：补全可能此前缺失的 hip_path 元数据
            if not locked.get("hip_path") and current.get("hip_path"):
                self._turn_bridge_target = current
            return True, None

        # 目标实例或打开的 .hip 场景已变化！
        if tool_name in self._BRIDGE_READONLY_TOOLS:
            return True, None

        old_desc = self._format_bridge_target(locked)
        new_desc = self._format_bridge_target(current)
        try:
            self.toast.emit("已拦截写操作 %s：目标已变为 %s（原锁定 %s）" % (tool_name, new_desc, old_desc))
        except Exception:
            pass
        return False, {
            "success": False,
            "error": (
                f"[写入保护已暂停] 原先正在操作的 Houdini 实例/场景为 {old_desc}，"
                f"但当前连接已变为 {new_desc}。为避免把旧场景的修改误写到错误文件中，"
                f"系统已拦截本次写操作 `{tool_name}`。\n"
                f"请立即暂停后续写入，向用户说明原窗口/文件已断开或切换；"
                f"若用户确认要在 {new_desc} 上继续，请先显式调用 switch_houdini_instance 再执行。"
            ),
        }

    def _run_connection_tool(self, tool_name, kwargs):
        """连接诊断/修复及多 Houdini 实例协作工具：app 侧执行。"""
        if getattr(self._session, "bridge", None) is None:
            return {"success": True,
                    "result": "当前直接运行在 Houdini 进程内，不经过 Bridge，无需连接诊断或多实例切换。"}
        try:
            from houdini_agent.bridge import doctor
            if tool_name == "check_houdini_connection":
                res = doctor.diagnose()
            elif tool_name == "repair_houdini_connection":
                res = doctor.repair(kwargs.get("action") or "",
                                    version=kwargs.get("version"),
                                    wait_seconds=kwargs.get("wait_seconds"),
                                    heartbeat=self._touch_activity)
            elif tool_name == "list_houdini_instances":
                res = doctor.list_instances_result(heartbeat=self._touch_activity)
            elif tool_name == "switch_houdini_instance":
                res = doctor.switch_instance(kwargs.get("instance") or "",
                                             heartbeat=self._touch_activity)
            elif tool_name == "run_in_houdini_instance":
                res = doctor.run_in_instance(
                    kwargs.get("instance") or "",
                    kwargs.get("tool_name") or "",
                    arguments=kwargs.get("arguments") or {},
                    heartbeat=self._touch_activity,
                    read_only=(self._mode in ("Ask", "Plan")))
            elif tool_name == "transfer_nodes_between_instances":
                if self._mode in ("Ask", "Plan"):
                    return {"success": False,
                            "error": "当前处于只读模式（Ask/Plan），禁止跨实例写入节点。请切换到 Agent 模式。"}
                res = doctor.transfer_nodes(
                    kwargs.get("source_instance") or "",
                    kwargs.get("target_instance") or "",
                    kwargs.get("node_paths") or [],
                    target_parent=kwargs.get("target_parent") or "/obj",
                    create_parent=bool(kwargs.get("create_parent", True)),
                    heartbeat=self._touch_activity)
            else:
                return {"success": False, "error": "未知连接/多实例工具: %s" % tool_name}
        except Exception as e:
            return {"success": False, "error": "连接/多实例工具执行失败: %s" % e}
        if isinstance(res, dict):
            if isinstance(res.get("instances"), list):
                try:
                    self._sigInstances.emit(json.dumps(res["instances"], ensure_ascii=False, default=str))
                except Exception:
                    pass
            if res.get("reconnected") or res.get("switched"):
                self._sigCtxRefresh.emit()
            # 只有 Agent 显式调用 switch_houdini_instance 成功时，才主动更新本轮写操作目标锁
            if tool_name == "switch_houdini_instance" and res.get("success"):
                self._turn_bridge_target = self._probe_current_bridge_target()
        return res

    def _realtime_cook(self, result):
        """Cook display nodes with an interruptible progress dialog (Esc to cancel),
        measure time, and report it. On interrupt/slow cook, switch to Manual and
        suspend realtime cook for the rest of the run (avoids re-cook deadlock)."""
        if self._cook_suspended:
            return
        import time as _t
        try:
            import hou
        except Exception:
            return
        obj = hou.node("/obj")
        if obj is None:
            return
        targets = []
        for child in obj.children():
            try:
                if child.type().name() not in ("geo", "subnet"):
                    continue
                dn = child.displayNode()
                if dn is not None:
                    targets.append(dn)
            except Exception:
                pass
        if not targets:
            return

        interrupted = False
        budget = False
        cooked = False
        t0 = _t.time()
        try:
            with hou.InterruptableOperation("Cooking", long_operation_name="Houdini Agent: cooking",
                                            open_interrupt_dialog=True) as op:
                total = len(targets)
                for i, dn in enumerate(targets):
                    try:
                        a = _t.time()
                        dn.cook(force=False)
                        if _t.time() - a >= 0.05:
                            cooked = True
                    except hou.OperationInterrupted:
                        interrupted = True
                        break
                    except Exception:
                        pass
                    if (_t.time() - t0) >= self._SLOW_COOK_SEC:
                        budget = True
                        break
                    try:
                        op.updateProgress((i + 1) / float(total))
                    except Exception:
                        pass
        except hou.OperationInterrupted:
            interrupted = True
        except Exception as e:
            print("[Cook] interruptable op failed:", e)
            return

        elapsed = _t.time() - t0
        slow = elapsed >= self._SLOW_COOK_SEC or budget

        if interrupted or slow:
            try:
                if hou.updateModeSetting() != hou.updateMode.Manual:
                    hou.setUpdateMode(hou.updateMode.Manual)
            except Exception:
                pass
            self._cook_suspended = True

        note = ""
        if interrupted:
            note = "cook 已中断 (%.1fs) · 已切手动更新" % elapsed
        elif slow:
            note = "cook %.1fs · 较慢，已切手动更新（⋯ 可切回实时）" % elapsed
        elif cooked:
            note = "cook %.2fs" % elapsed
        if note:
            result["result"] = (str(result.get("result", "")) + " · " + note).strip(" ·")

    @staticmethod
    def _target_network(kwargs):
        try:
            import hou
        except Exception:
            return None
        p = kwargs.get("parent_path") or kwargs.get("parent")
        if p:
            return hou.node(p)
        np = kwargs.get("node_path") or kwargs.get("path")
        if np:
            n = hou.node(np)
            if n and n.parent():
                return n.parent()
        try:
            ed = hou.ui.curDesktop().paneTabOfType(hou.paneTabType.NetworkEditor)
            if ed:
                return ed.pwd()
        except Exception:
            pass
        return hou.node("/obj/geo1") or hou.node("/obj")

    @staticmethod
    def _snapshot_children(network):
        if network is None:
            return {}
        try:
            return {n.path(): {"name": n.name(), "type": n.type().name(), "path": n.path()}
                    for n in network.children()}
        except Exception:
            return {}

    @staticmethod
    def _diff_children(before, after):
        bp, ap = set(before), set(after)
        created = [after[p] for p in sorted(ap - bp)]
        deleted = [before[p] for p in sorted(bp - ap)]
        if not created and not deleted:
            return None
        return {"created": created, "deleted": deleted}

    def _emit_node_ops(self, name, r):
        ops = []
        nc = r.get("_node_changes")
        if isinstance(nc, dict):
            cre = [x.get("path") for x in nc.get("created", []) if x.get("path")]
            dele = [x.get("path") for x in nc.get("deleted", []) if x.get("path")]
            if cre:
                ops.append({"op": "create", "paths": cre})
            if dele:
                ops.append({"op": "delete", "paths": dele})
        if name == "set_node_parameter":
            snap = r.get("_undo_snapshot")
            if isinstance(snap, dict) and snap.get("node_path"):
                ops.append({"op": "modify", "paths": [snap["node_path"]], "snapshot": snap})
        for op in ops:
            self._op_seq += 1
            oid = str(self._op_seq)
            self._op_ctx[oid] = op
            self._sigNodeOp.emit(oid)

    @Slot(str)
    def _ui_node_op(self, oid):
        ctx = self._op_ctx.get(oid)
        if not ctx:
            return
        op = ctx.get("op")
        paths = ctx.get("paths", []) or []
        if op == "create":
            block = {"kind": "nodeop", "opId": oid, "badge": "+%d" % len(paths),
                     "text": "nodes created", "paths": " · ".join(paths)}
        elif op == "delete":
            block = {"kind": "nodeop", "opId": oid, "badge": "-%d" % len(paths),
                     "text": "nodes deleted", "paths": " · ".join(paths)}
        elif op == "modify":
            snap = ctx.get("snapshot") or {}
            block = {"kind": "nodeop", "opId": oid, "badge": "~",
                     "text": snap.get("param_name", ""),
                     "old": self._short_val(snap.get("old_value"), 60),
                     "new": self._short_val(snap.get("new_value"), 60),
                     "paths": " · ".join(paths)}
        else:
            return
        self._blocks.append(block)
        self._pending_ops += 1
        self.pendingOpsChanged.emit()
        self._flush()

    def _exec_houdini_tool(self, name, kwargs):
        """从后台线程执行一个 Houdini 侧工具（走 bridge 或主线程阻塞队列）。
        与 _tool_executor 的 Houdini 分支同路径，但跳过确认门/停止门（用户已显式触发）。"""
        sess = self._session
        if sess is None:
            return {"success": False, "error": "无会话"}
        if getattr(sess, "bridge", None) is not None:
            if sess.bridge.ping() is None:
                return {"success": False, "error": _BRIDGE_LOST_MSG}
            try:
                return sess.mcp.execute_tool(name, kwargs)
            except Exception as e:
                if sess.bridge.ping() is None:
                    return {"success": False, "error": _BRIDGE_LOST_MSG}
                return {"success": False, "error": str(e)}
        with self._tool_lock:
            while not self._tool_q.empty():
                try:
                    self._tool_q.get_nowait()
                except queue.Empty:
                    break
            self._sigToolExec.emit(name, json.dumps(kwargs, ensure_ascii=False))
            try:
                return self._tool_q.get(timeout=120)
            except queue.Empty:
                return {"success": False, "error": "导入超时（120s）"}

    @Slot()
    def undoAll(self):
        if not self._op_ctx:
            return
        self._request_confirm("全部撤销", "确定要撤销本轮所有待确认的节点操作吗？", self._undo_all_now)

    def _undo_all_now(self):
        failed = 0
        for oid in reversed(list(self._op_ctx.keys())):
            if self._do_undo(self._op_ctx.get(oid)):
                self._op_ctx.pop(oid, None)
            else:
                failed += 1
        if failed:
            self.toast.emit("部分操作撤销失败，仍保留待处理")
            self._pending_ops = len(self._op_ctx)
            self.pendingOpsChanged.emit()
            self.refreshContext()
            return
        self._pending_ops = 0
        self.pendingOpsChanged.emit()
        self.batchResolved.emit("reverted")
        self.refreshContext()

    @Slot()
    def keepAll(self):
        self._op_ctx.clear()
        self._pending_ops = 0
        self.pendingOpsChanged.emit()
        self.batchResolved.emit("kept")

    def _do_undo(self, ctx):
        if not ctx:
            return False
        bridge = getattr(getattr(self._session, "bridge", None), "undo_node_op", None)
        if bridge is not None:
            try:
                res = bridge(ctx)
                ok = bool(res.get("success")) if isinstance(res, dict) else bool(res)
                if not ok:
                    err = res.get("error") if isinstance(res, dict) else "unknown bridge undo failure"
                    self._log_external("Bridge undo failed: %s ctx=%s" % (err, ctx))
                    self.toast.emit("撤销失败：%s" % err)
                return ok
            except Exception as e:
                self._log_external("Bridge undo exception: %s ctx=%s" % (e, ctx))
                self.toast.emit("撤销失败：%s" % e)
                return False
        try:
            import hou
        except Exception:
            return False
        op = ctx.get("op")
        try:
            if op == "create":
                for p in ctx.get("paths", []):
                    n = hou.node(p)
                    if n is not None:
                        n.destroy()
            elif op == "modify":
                snap = ctx.get("snapshot") or {}
                n = hou.node(snap.get("node_path", ""))
                if n is not None:
                    pn = snap.get("param_name", "")
                    old = snap.get("old_value")
                    if snap.get("is_tuple"):
                        pt = n.parmTuple(pn)
                        if pt is not None:
                            pt.set(old)
                    else:
                        pm = n.parm(pn)
                        if pm is not None:
                            if isinstance(old, dict) and "expr" in old:
                                lang = (hou.exprLanguage.Python
                                        if "python" in str(old.get("lang", "")).lower()
                                        else hou.exprLanguage.Hscript)
                                pm.setExpression(old["expr"], lang)
                            else:
                                pm.set(old)
            elif op == "delete":
                hou.undos.performUndo()
            return True
        except Exception as e:
            print("[undo] failed:", e)
            self.toast.emit("撤销失败：%s" % e)
            return False

    @Slot(str, result=bool)
    def keepNodeOp(self, oid):
        if self._op_ctx.pop(oid, None) is not None:
            self._dec_pending()
            return True
        return False

    @Slot(str, result=bool)
    def undoNodeOp(self, oid):
        ctx = self._op_ctx.get(oid)
        if not ctx:
            self.toast.emit("该操作已不可撤销或已处理")
            return False
        if not self._do_undo(ctx):
            return False
        self._op_ctx.pop(oid, None)
        self._dec_pending()
        self.refreshContext()
        return True

