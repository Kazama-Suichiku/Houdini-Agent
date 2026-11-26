# -*- coding: utf-8 -*-
"""ControllerPlanMixin — extracted from controller.py."""

import copy
import json
import queue
from ..controller_shared import Slot


class ControllerPlanMixin:
    def _handle_todo(self, tool_name, kwargs):
        self._sigTodo.emit(json.dumps({
            "id": kwargs.get("todo_id", ""), "text": kwargs.get("text", ""),
            "status": kwargs.get("status", "pending")}, default=str))
        return {"success": True, "result": "todo %s updated" % kwargs.get("todo_id", "")}

    @Slot(str)
    def _ui_todo(self, js):
        try:
            d = json.loads(js)
        except Exception:
            return
        if self._todo_block is None:
            self._todo_block = {"kind": "todo", "items": []}
            self._blocks.append(self._todo_block)
        tid = d.get("id")
        for it in self._todo_block["items"]:
            if it["id"] == tid:
                if d.get("status"):
                    it["status"] = d["status"]
                if d.get("text"):
                    it["text"] = d["text"]
                break
        else:
            self._todo_block["items"].append(
                {"id": tid, "text": d.get("text", ""), "status": d.get("status", "pending")})
        self._flush()

    def _partial_plan_payload(self, acc):
        import re as _re
        try:
            d = json.loads(acc)
            if isinstance(d, dict):
                return self._build_plan_payload(d)
        except Exception:
            pass

        titles = [self._json_string(m) for m in _re.findall(r'"title"\s*:\s*"((?:[^"\\]|\\.)*)"', acc or "")]
        ids = [self._json_string(m) for m in _re.findall(r'"id"\s*:\s*"((?:[^"\\]|\\.)*)"', acc or "")]
        overview_m = _re.search(r'"overview"\s*:\s*"((?:[^"\\]|\\.)*)"', acc or "")
        overview = self._json_string(overview_m.group(1)) if overview_m else ""
        plan_title = titles[0] if titles else "Plan"
        step_titles = titles[1:]
        steps = []
        for i, title in enumerate(step_titles):
            sid = ids[i] if i < len(ids) and str(ids[i]).startswith("step") else "step-%d" % (i + 1)
            steps.append({
                "id": sid, "label": title or sid, "title": title or sid,
                "state": "active" if i == len(step_titles) - 1 else "pending",
                "status": "running" if i == len(step_titles) - 1 else "pending",
                "detail": "", "description": "", "sub_steps": [], "tools": [],
                "depends_on": ["step-%d" % i] if i > 0 else [],
                "expected_result": "", "risk": "low", "estimated_operations": 1,
                "fallback": "", "notes": "", "result_summary": "",
            })
        arch = self._build_step_architecture(steps, [])
        return {
            "title": plan_title, "overview": overview, "complexity": "",
            "estimated_total_operations": 0, "phases": [], "status": "streaming",
            "badge": "%d steps · generating" % len(steps),
            "steps": steps, "architecture": arch, "dag": arch.get("nodes", []),
        }

    @Slot(str)
    def _ui_plan_stream(self, acc):
        payload = self._partial_plan_payload(acc)
        payload["kind"] = "planstream"
        if self._planstream_block is None:
            self._planstream_block = payload
            self._blocks.append(self._planstream_block)
        else:
            self._planstream_block.clear()
            self._planstream_block.update(payload)
        self._flush()

    def _clear_planstream(self):
        if self._planstream_block is not None:
            try:
                self._blocks.remove(self._planstream_block)
            except ValueError:
                pass
            self._planstream_block = None
            self._flush()

    def _handle_create_plan(self, kwargs):
        import json
        payload = self._build_plan_payload(kwargs)
        self._plan_phase = "awaiting"
        if self._plan_revision_mode:
            self._plan_revision_mode = False
            payload["revision"] = self._to_int((self._plan_payload or {}).get("revision"), 1) + 1
            payload["revised"] = True
            payload["status"] = "revised"
            self._sigPlan.emit(json.dumps(payload, default=str))
            return {"success": True,
                    "result": "已基于原计划完成局部修订，并更新同一张计划卡片。请用一句话说明修改完成。"}
        self._sigPlan.emit(json.dumps(payload, default=str))
        return {"success": True,
                "result": "计划已创建并展示给用户（含步骤与 DAG）。等待用户点击「确认执行」后再开始执行。"
                          "现在用一句话简短告知用户计划已就绪。"}

    def _handle_update_plan_step(self, kwargs):
        import json
        self._sigPlanStep.emit(json.dumps({
            "step_id": kwargs.get("step_id", ""),
            "status": kwargs.get("status", ""),
            "summary": kwargs.get("result_summary", ""),
        }, default=str))
        return {"success": True,
                "result": "step %s -> %s" % (kwargs.get("step_id"), kwargs.get("status"))}

    @staticmethod
    def _as_list(v):
        return v if isinstance(v, list) else []

    @staticmethod
    def _to_int(v, default=0):
        try:
            return int(v)
        except Exception:
            return default

    @staticmethod
    def _build_step_architecture(steps, phases=None):
        nodes, connections = [], []
        has_deps = any(s.get("depends_on") for s in steps)
        for i, s in enumerate(steps):
            sid = s.get("id") or ("step-%d" % (i + 1))
            tools = s.get("tools") or []
            nodes.append({
                "id": sid,
                "label": (s.get("title") or s.get("label") or sid),
                "type": "other",
                "group": "",
                "is_new": True,
                "params": ", ".join(tools[:2]) if isinstance(tools, list) else "",
            })
            for dep in (s.get("depends_on") or []):
                connections.append({"from": dep, "to": sid, "label": ""})
        if not has_deps and len(steps) > 1:
            for i in range(len(steps) - 1):
                connections.append({"from": steps[i]["id"], "to": steps[i + 1]["id"], "label": ""})
        groups = []
        for p in (phases or []):
            name = p.get("name", "")
            ids = p.get("step_ids", [])
            if name and ids:
                groups.append({"name": name, "node_ids": ids, "color": "blue"})
        return {"nodes": nodes, "connections": connections, "groups": groups}

    def _build_plan_payload(self, k):
        allowed_types = {"sop", "obj", "mat", "vop", "rop", "dop", "lop", "cop",
                         "chop", "out", "subnet", "null", "other"}
        steps = self._as_list(k.get("steps"))
        phases = self._as_list(k.get("phases"))
        step_phase = {}
        out_phases = []
        for p in phases:
            if not isinstance(p, dict):
                continue
            name = str(p.get("name") or "")
            ids = [str(x) for x in self._as_list(p.get("step_ids"))]
            if not name or not ids:
                continue
            out_phases.append({"name": name, "step_ids": ids})
            for sid in ids:
                step_phase[sid] = name

        out_steps, idmap = [], {}
        for i, s in enumerate(steps):
            if not isinstance(s, dict):
                continue
            sid = str(s.get("id") or ("step-%d" % (i + 1)))
            idmap[sid] = len(out_steps)
            status = s.get("status") or "pending"
            state = {"running": "active", "done": "done", "error": "error"}.get(status, "pending")
            detail = s.get("description") or s.get("detail") or ""
            out_steps.append({
                "id": sid,
                "label": s.get("title") or sid,
                "title": s.get("title") or sid,
                "state": state,
                "status": status,
                "detail": detail,
                "description": detail,
                "sub_steps": self._as_list(s.get("sub_steps")),
                "tools": self._as_list(s.get("tools")),
                "depends_on": self._as_list(s.get("depends_on")),
                "expected_result": s.get("expected_result") or "",
                "risk": s.get("risk") or "low",
                "estimated_operations": self._to_int(s.get("estimated_operations"), 1),
                "fallback": s.get("fallback") or "",
                "notes": s.get("notes") or "",
                "phase": step_phase.get(sid, ""),
                "result_summary": s.get("result_summary") or "",
            })

        arch = k.get("architecture") if isinstance(k.get("architecture"), dict) else {}
        nodes = []
        for n in self._as_list(arch.get("nodes")):
            if not isinstance(n, dict):
                continue
            nid = str(n.get("id") or n.get("name") or "")
            if not nid:
                continue
            ntype = str(n.get("type") or "other").lower()
            nodes.append({
                "id": nid,
                "label": str(n.get("label") or nid),
                "type": ntype if ntype in allowed_types else "other",
                "group": str(n.get("group") or ""),
                "is_new": bool(n.get("is_new", True)),
                "params": str(n.get("params") or ""),
            })
        node_ids = {n["id"] for n in nodes}
        connections = []
        for c in self._as_list(arch.get("connections")):
            if not isinstance(c, dict):
                continue
            src, dst = str(c.get("from") or ""), str(c.get("to") or "")
            if src and dst and (not node_ids or (src in node_ids and dst in node_ids)):
                connections.append({"from": src, "to": dst, "label": str(c.get("label") or "")})
        groups = []
        for g in self._as_list(arch.get("groups")):
            if not isinstance(g, dict):
                continue
            name = str(g.get("name") or "")
            ids = [str(x) for x in self._as_list(g.get("node_ids"))]
            if name and ids:
                groups.append({"name": name, "node_ids": ids, "color": str(g.get("color") or "")})
        architecture = {"nodes": nodes, "connections": connections, "groups": groups}
        if not nodes:
            architecture = self._build_step_architecture(out_steps, out_phases)

        complexity = k.get("complexity") or ""
        badge = "%d steps" % len(out_steps)
        if complexity:
            badge += " · " + str(complexity)
        self._plan_idmap = idmap
        revision = self._to_int(k.get("revision"), 1)
        return {
            "title": k.get("title") or "Plan",
            "overview": k.get("overview") or "",
            "complexity": complexity,
            "estimated_total_operations": self._to_int(k.get("estimated_total_operations"), 0),
            "phases": out_phases,
            "status": k.get("status") or "draft",
            "badge": (badge + (" · rev %d" % revision if revision > 1 else "")),
            "revision": revision,
            "revision_notes": self._as_list(k.get("revision_notes")),
            "steps": out_steps,
            "architecture": architecture,
            "dag": architecture.get("nodes", []),
        }

    @Slot(str)
    def _ui_plan(self, js):
        import json
        try:
            payload = json.loads(js)
        except Exception:
            return
        self._clear_planstream()
        self._plan_payload = payload
        if self._plan_row is not None and payload.get("revised"):
            self._model.update_payload(self._plan_row, copy.deepcopy(payload))
            self.toast.emit("计划已更新，可继续修改或确认执行")
        else:
            self._plan_row = self._model.append({"type": "plan", "payload": payload})

    @Slot(str)
    def _ui_plan_step(self, js):
        import json
        try:
            d = json.loads(js)
        except Exception:
            return
        if self._plan_row is None or not self._plan_payload:
            return
        idx = self._plan_idmap.get(d.get("step_id"))
        if idx is None:
            return
        state = {"running": "active", "done": "done", "error": "error"}.get(d.get("status"), "pending")
        try:
            self._plan_payload["steps"][idx]["state"] = state
            self._plan_payload["steps"][idx]["status"] = d.get("status") or "pending"
            if d.get("summary"):
                self._plan_payload["steps"][idx]["result_summary"] = d["summary"]
                self._plan_payload["steps"][idx]["detail"] = d["summary"]
            self._model.update_payload(self._plan_row, copy.deepcopy(self._plan_payload))
        except Exception:
            pass

    def _begin_plan_execution(self):
        if not self._session:
            return
        self._plan_phase = "executing"
        self._model.append({"type": "user", "payload": {"text": "▶ 执行计划"}})
        plan_json = ""
        try:
            plan_json = json.dumps(self._plan_payload or {}, ensure_ascii=False, default=str)
        except Exception:
            plan_json = ""
        instr = ("用户已确认上述计划。现在按计划逐步执行：每一步开始前调用 "
                 "update_plan_step(step_id, status='running')，完成后调用 "
                 "update_plan_step(step_id, status='done')（失败用 'error'）。"
                 "严格按 depends_on 顺序执行，全部完成后给出简短总结。"
                 "\n\n以下是用户最终确认的修订版计划 JSON，请以它为准，不要执行旧版本：\n"
                 + plan_json)
        self._start_run(instr, tools=self._execution_tools(), max_iter=80)
        # Plan 执行通常会连续创建/连接节点，自动 cook 最容易让 Houdini 主线程假死。
        # 执行结束后用户仍可手动 cook/刷新视口，避免每一步都强制 cook。
        self._cook_suspended = True
        self.toast.emit("开始执行计划 · 已暂停自动 Cook 以避免卡死")
        self.planExecutionStarted.emit()

    @Slot(str, result=str)
    def confirmPlan(self, plan_id):
        if not self._session:
            return ""
        if self._running:
            if self._plan_phase == "awaiting":
                self._pending_plan_confirm = True
                self.toast.emit("计划生成收尾中，完成后会自动开始执行")
                return "pending"
            self.toast.emit("当前仍在运行，请等待或先 Stop")
            return ""
        self._pending_plan_confirm = False
        self._begin_plan_execution()
        return "confirmed"

    @Slot(str, result=bool)
    def rejectPlan(self, plan_id):
        if self._running and self._plan_phase != "awaiting":
            self.toast.emit("当前仍在运行，请等待或先 Stop")
            return False
        if self._running and self._plan_phase == "awaiting" and self._session:
            self._session.stop()
        self._pending_plan_confirm = False
        self._plan_phase = "idle"
        if self._session:
            try:
                self._session.history.append({"role": "user", "content": "用户驳回了该计划，不要执行。"})
            except Exception:
                pass
        return True

    def _ensure_plan_state(self, payload=None):
        if self._plan_row is not None and 0 <= self._plan_row < len(getattr(self._model, "_items", [])):
            if self._model._items[self._plan_row].get("type") == "plan":
                if payload is not None:
                    self._plan_payload = payload
                elif not self._plan_payload:
                    self._plan_payload = self._model._items[self._plan_row].get("payload") or {}
                return True
        for i in range(len(getattr(self._model, "_items", [])) - 1, -1, -1):
            item = self._model._items[i]
            if item.get("type") == "plan":
                self._plan_row = i
                self._plan_payload = payload if payload is not None else (item.get("payload") or {})
                self._plan_idmap = {s.get("id"): j for j, s in enumerate((self._plan_payload or {}).get("steps", []))}
                return True
        return False

    @Slot(str, result=bool)
    def applyPlanEdit(self, payload_json):
        if self._running:
            self.toast.emit("当前仍在运行，请等待或先 Stop")
            return False
        try:
            data = json.loads(payload_json or "{}")
            if not isinstance(data, dict):
                self.toast.emit("计划修改失败：无效数据")
                return False
            if not self._ensure_plan_state(data):
                self.toast.emit("计划修改失败：找不到当前计划卡片")
                return False
            data["revision"] = self._to_int((self._plan_payload or {}).get("revision"), 1) + 1
            data["revised"] = True
            data["status"] = "revised"
            payload = self._build_plan_payload(data)
            payload["revised"] = True
            payload["status"] = "revised"
            self._plan_payload = payload
            self._plan_idmap = {s.get("id"): i for i, s in enumerate(payload.get("steps", []))}
            self._model.update_payload(self._plan_row, copy.deepcopy(payload))
            self.toast.emit("手动修改已保存到当前计划")
            return True
        except Exception as e:
            self.toast.emit("计划修改失败：%s" % e)
            return False

    @Slot(str, str, result=bool)
    def revisePlan(self, instruction, payload_json):
        instruction = (instruction or "").strip()
        if not instruction:
            self.toast.emit("请先输入修改要求")
            return False
        if self._running:
            self.toast.emit("当前仍在运行，请等待或先 Stop")
            return False
        if not self._session:
            self.toast.emit("当前后端不可用，无法让 AI 修改计划")
            return False
        try:
            base = json.loads(payload_json or "{}")
            if not isinstance(base, dict):
                base = self._plan_payload
        except Exception:
            base = self._plan_payload
        if not base or not self._ensure_plan_state(base):
            self.toast.emit("计划修改失败：找不到当前计划")
            return False
        self._plan_revision_mode = True
        self._plan_phase = "planning"
        self._model.append({"type": "user", "payload": {"text": "✎ 修改计划：" + instruction}})
        prompt = (
            "用户对当前计划提出局部修改要求。请不要重新发散设计，不要生成多份方案。"
            "你必须基于原计划做最小必要修改，保留不相关步骤、step id、依赖关系和 DAG。"
            "修改完成后只调用 create_plan 返回修订后的完整计划 JSON。\n\n"
            "用户修改要求：\n%s\n\n当前计划 JSON：\n%s"
        ) % (instruction, json.dumps(base, ensure_ascii=False, default=str))
        self._start_run(prompt, tools=self._planning_tools(), max_iter=8)
        return True

    def _stop_requested(self):
        try:
            return bool(self._session and self._session.client.is_stop_requested())
        except Exception:
            return False

    def _await_confirm(self, tool_name, kwargs):
        self._int_seq += 1
        cid = "c%d" % self._int_seq
        q = queue.Queue()
        self._interactive[cid] = q
        self._sigConfirm.emit(cid, tool_name, self._confirm_preview(tool_name, kwargs))
        # 分段等待：每 0.3s 检查一次停止请求，最长 300s。否则用户点 Stop 时这里会一直
        # 阻塞到 300s 超时（确认卡完全不响应停止），与画廊/批量流的行为不一致。
        try:
            waited = 0.0
            while True:
                try:
                    return bool(q.get(timeout=0.3))
                except queue.Empty:
                    waited += 0.3
                    if self._stop_requested() or waited >= 300:
                        return False
        finally:
            self._interactive.pop(cid, None)

    def _handle_ask_question(self, kwargs):
        self._int_seq += 1
        qid = "q%d" % self._int_seq
        q = queue.Queue()
        self._interactive[qid] = q
        questions = kwargs.get("questions", []) or []
        self._sigAskQ.emit(qid, json.dumps(questions, default=str))
        # 分段等待：同 _await_confirm，点 Stop 时能及时退出而不是干等到 300s。
        ans = {}
        waited = 0.0
        try:
            while True:
                try:
                    ans = q.get(timeout=0.3)
                    break
                except queue.Empty:
                    waited += 0.3
                    if self._stop_requested() or waited >= 300:
                        ans = {}
                        break
        finally:
            self._interactive.pop(qid, None)
        return {"success": True, "result": "用户回答：" + json.dumps(ans, ensure_ascii=False)}

    @Slot(str, bool)
    def resolveConfirm(self, cid, ok):
        q = self._interactive.get(cid)
        if q is not None:
            q.put(bool(ok))
        self._sigResolveCard.emit(cid, "confirmed" if ok else "cancelled")

    @Slot(str, str)
    def resolveQuestion(self, qid, answers_json):
        q = self._interactive.get(qid)
        if q is not None:
            try:
                ans = json.loads(answers_json) if answers_json else {}
            except Exception:
                ans = {}
            q.put(ans)
        self._sigResolveCard.emit(qid, "answered")

    @Slot(str, str, str)
    def _ui_confirm(self, cid, tool_name, arg):
        self._blocks.append({"kind": "confirm", "cid": cid, "name": tool_name,
                             "arg": arg, "state": "pending"})
        self._flush()

    @Slot(str, str)
    def _ui_askq(self, qid, questions_json):
        try:
            questions = json.loads(questions_json)
        except Exception:
            questions = []
        self._blocks.append({"kind": "askq", "qid": qid, "questions": questions, "state": "pending"})
        self._flush()

    @Slot(str, str)
    def _ui_resolve_card(self, cid, state):
        for b in self._blocks:
            if b.get("cid") == cid or b.get("qid") == cid:
                b["state"] = state
                break
        self._flush()

