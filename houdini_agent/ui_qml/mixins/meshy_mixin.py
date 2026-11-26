# -*- coding: utf-8 -*-
"""ControllerMeshyMixin — extracted from controller.py."""

import json
import os
import queue
import threading
import time
from ..controller_shared import BlockModel, MESHY_LABELS, Slot, _MESHY_URLS, _MESHY_UTM, _meshy


class ControllerMeshyMixin:
    @Slot(str)
    def openMeshy(self, kind):
        """在浏览器打开某个 Meshy 公开网页（链接附带 utm 来源标记）。kind ∈ home/workspace/apikey/pricing/docs。"""
        base = _MESHY_URLS.get(str(kind or ""))
        if not base:
            return
        sep = "&" if "?" in base else "?"
        self._open_browser(base + sep + _MESHY_UTM)

    @Slot(str, result=str)
    def imageModelDisplayName(self, spec=""):
        """生图模型短显示名（画廊按钮 / 提示用）。"""
        s = str(spec or "").strip() or self._effective_default_image_model()
        if s.startswith("codex:"):
            return "Codex · " + s[6:]
        if s.startswith("meshy:"):
            return s[6:]
        return s

    @Slot(result="QVariantList")
    def meshyImageModelOptions(self):
        """设置页：Meshy 生图模型选项。"""
        return [{"key": val, "label": self.tr(lbl)} for val, lbl in self._MESHY_IMAGE_CHOICES]

    @Slot(result="QVariantList")
    def codexImageModelOptions(self):
        """设置页：ChatGPT 订阅（Codex）生图模型选项（自动随 CPA /v1/models 同步）。"""
        models = self._codex_image_models()
        cur = self._effective_default_image_model()
        if cur.startswith("codex:"):
            cm = cur[6:]
            if cm and cm not in models:
                models.append(cm)
        return [{"key": "codex:" + m, "label": m} for m in models]

    @Slot(str)
    def setDefaultImageModel(self, val):
        """设置默认生图模型（持久化到 QSettings）。支持 Meshy 模型及 codex:<model>。"""
        v = str(val or "").strip()
        if v.startswith("meshy:"):
            v = v[6:].strip()
        meshy_keys = {k for k, _ in self._MESHY_IMAGE_CHOICES}
        if v.startswith("codex:"):
            if len(v) <= 6:
                return
            if not self._codex_logged_in():
                self.toast.emit(self.tr("请先登录 ChatGPT 订阅后再选择 Codex 生图模型"))
                return
        elif v not in meshy_keys:
            return
        if v != self._default_image_model:
            self._default_image_model = v
            self._set_pref("default_image_model", v)
            self.imageModelChanged.emit()

    @Slot()
    def openCodexSubscription(self):
        """从设置页直接打开 ChatGPT 订阅（Codex）登录弹窗。"""
        self._sub_dialog_provider = "codex"
        self.openSubscriptionDialog.emit()

    @Slot()
    def refreshCodexImageModels(self):
        """手动同步本机 CPA /v1/models 里的 Codex 生图模型列表（后台线程）。"""
        if self._codex_image_refreshing:
            return
        if not self._codex_logged_in():
            self.toast.emit(self.tr("请先登录 ChatGPT 订阅"))
            return
        self._codex_image_refreshing = True
        self.codexImageRefreshingChanged.emit()

        def work():
            try:
                from houdini_agent.subscription import manager
                manager.ensure_running(timeout=15)
                models = manager.refresh_image_models("codex")
                payload = {"ok": True, "models": models}
            except Exception as e:
                payload = {"ok": False, "error": str(e)}
            try:
                self._sigCodexImageRefresh.emit(json.dumps(payload, ensure_ascii=False))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    @Slot(str)
    def _on_codex_image_refreshed(self, js):
        try:
            d = json.loads(js) if js else {}
        except Exception:
            d = {}
        self._codex_image_refreshing = False
        self.codexImageRefreshingChanged.emit()
        self.imageModelChanged.emit()
        self.subscriptionChanged.emit()
        self.providerChanged.emit()
        if d.get("ok"):
            models = d.get("models") or []
            suffix = (" (%s)" % ", ".join(models[:4])) if models else ""
            self.toast.emit(self.tr("Codex 生图模型已更新") + suffix)
        else:
            self.toast.emit(self.tr("同步 Codex 生图模型失败：") + str(d.get("error") or ""))

    @Slot(str, result="QVariantList")
    def imageModelItems(self, current=""):
        """生图模型下拉项（画廊里"弹窗选模型"用）。已登录 Codex 时同时列出 Codex 与 Meshy 模型。"""
        cur = str(current or "").strip() or self._effective_default_image_model()
        meshy_models = [(val, self.tr(lbl)) for val, lbl in self._MESHY_IMAGE_CHOICES]
        show_codex = self._codex_logged_in() or cur.startswith("codex:")
        if not show_codex:
            return [{"label": lbl, "val": val, "checked": (val == cur)}
                    for val, lbl in meshy_models]
        cmodels = self._codex_image_models()
        if cur.startswith("codex:"):
            cm = cur[6:]
            if cm and cm not in cmodels:
                cmodels.append(cm)
        items = [{"label": "Codex · " + m, "val": "codex:" + m, "checked": ("codex:" + m == cur)}
                 for m in cmodels]
        if items:
            items.append({"sep": True})
        items.extend({"label": "Meshy · " + lbl, "val": val, "checked": (val == cur)}
                     for val, lbl in meshy_models)
        return items

    @Slot(str)
    def composePrefill(self, text):
        """把一段起始提示词塞进输入框并聚焦（资产库"+用 Meshy 生成"快捷用）。"""
        try:
            self.prefillComposer.emit(str(text or ""))
        except Exception:
            pass

    @Slot(result="QVariantList")
    def meshyMenuItems(self):
        """顶栏 Meshy 按钮的下拉项（val 传给 openMeshy）。"""
        t = self.tr
        return [
            {"label": t("打开 Meshy 官网"), "val": "home"},
            {"label": t("我的工作台"), "val": "workspace"},
            {"label": t("充值 / 定价"), "val": "pricing"},
            {"sep": True},
            {"label": t("API Key 设置"), "val": "apikey"},
            {"label": t("API 文档"), "val": "docs"},
        ]

    def _run_meshy_network(self, tool_name, kwargs):
        """Run a Meshy network tool, streaming progress to a MeshyCard. Runs the
        actual polling on a sub-thread so the user can hit 转入后台: the tool then
        returns to the agent immediately and the result is delivered as a new
        message when it finishes. Never touches Houdini / the bridge."""
        import uuid
        op = uuid.uuid4().hex
        label = MESHY_LABELS.get(tool_name, tool_name)
        bg_evt = threading.Event()
        self._meshy_bg_requests[op] = bg_evt
        self._meshy_bg[op] = {"op": op, "tool": tool_name, "label": label,
                              "stage": "提交", "progress": 0, "status": "PENDING",
                              "done": False, "ok": None, "summary": ""}

        def emit(stage, progress, status, done=False, ok=True, thumb="", summary="",
                 background=False):
            t = self._meshy_bg.get(op)
            if t is not None:
                t.update(stage=stage, progress=int(progress), status=status,
                         done=done, ok=ok, summary=summary or t.get("summary", ""))
            try:
                self._sigMeshyProgress.emit(json.dumps(
                    {"op": op, "tool": tool_name, "stage": stage,
                     "progress": int(progress), "status": status, "done": done,
                     "ok": ok, "thumb": thumb, "summary": summary,
                     "background": background, "backgroundable": not done}, default=str))
            except Exception:
                pass

        def on_progress(stage, progress, status):
            emit(stage, progress, status)
            try:
                self._sigStatus.emit("tool:%s · %s %d%%" % (tool_name, stage, progress))
            except Exception:
                pass

        def should_stop():
            if bg_evt.is_set():
                return False   # 已转后台：不随 agent 的停止 / 新一轮而中断
            try:
                return bool(self._session and self._session.client.is_stop_requested())
            except Exception:
                return False

        emit("提交", 0, "PENDING")
        holder = {}
        done_evt = threading.Event()

        def work():
            try:
                res = _meshy.run_network(tool_name, kwargs,
                                         on_progress=on_progress, should_stop=should_stop)
            except Exception as e:
                res = {"success": False, "error": "Meshy 执行异常: %s" % e}
            holder["res"] = res
            if bg_evt.is_set():
                # 已转后台：不再更新卡片（避免与新一轮的 block 错位），结果只发给 agent
                self._deliver_bg_result(op, tool_name, res)
            else:
                ok = bool(res.get("success"))
                data = res.get("data") or {}
                summary = res.get("result") if ok else res.get("error", "")
                emit("完成" if ok else "失败", 100 if ok else 0,
                     "SUCCEEDED" if ok else "FAILED", done=True, ok=ok,
                     thumb=data.get("thumbnail") or "", summary=summary or "")
            done_evt.set()

        threading.Thread(target=work, daemon=True).start()

        # 在 agent 线程上等待：要么任务完成（内联返回），要么用户点了"转入后台"
        while True:
            if done_evt.wait(timeout=0.15):
                self._meshy_bg_requests.pop(op, None)
                if not bg_evt.is_set():
                    self._meshy_bg.pop(op, None)   # 内联完成的不留在后台列表里
                self._last_activity = time.monotonic()
                return holder.get("res", {"success": False, "error": "未知错误"})
            if bg_evt.is_set():
                self._meshy_bg_requests.pop(op, None)
                # 卡片立刻冻结为"已转入后台"终态：不再更新、按钮消失、无法重复点击
                prog = int((self._meshy_bg.get(op) or {}).get("progress", 0))
                emit("已转入后台", prog, "后台", done=True, ok=True, background=True,
                     summary="已转入后台运行，完成后结果会自动发给 Agent（此卡片不再更新）。")
                self._last_activity = time.monotonic()
                return {"success": True, "error": "",
                        "result": ("已将【%s】转入后台运行（任务号 %s）。你现在可以继续做"
                                   "别的事；该任务完成后，结果会自动作为新消息发给你——"
                                   "无需轮询 meshy_task_status，请直接结束本轮或处理其他事项，"
                                   "只在用户主动追问进度时才查询。"
                                   % (label, op[:8])),
                        "data": {"background": True, "op": op}}

    @Slot(str)
    def backgroundMeshyTask(self, op):
        """QML -> 把某个正在跑的 Meshy 任务转入后台（不再阻塞 agent）。"""
        evt = self._meshy_bg_requests.get(op)
        if evt is not None:
            evt.set()
            self.toast.emit(self.tr("已转入后台，完成后自动通知"))

    def _enqueue_bg_feedback(self, text, label=""):
        """把一条后台完成结果排队，并通知主线程投递给 agent。"""
        with self._meshy_bg_lock:
            self._meshy_bg_feedback.append(text)
        try:
            self.toast.emit(self.tr("后台任务完成") + (("：" + label) if label else ""))
        except Exception:
            pass
        try:
            self._sigDeliverBg.emit()   # 切到主线程投递
        except Exception:
            pass

    def _deliver_bg_result(self, op, tool_name, res):
        """后台单任务完成：组织反馈文本并排队（agent 空闲时作为新一轮投递）。"""
        label = MESHY_LABELS.get(tool_name, tool_name)
        ok = bool(res.get("success"))
        summary = (res.get("result") if ok else res.get("error", "")) or ""
        text = ("【后台任务完成】%s（任务号 %s）%s\n%s"
                % (label, op[:8], "成功" if ok else "失败", summary))
        if ok:
            text += "\n请根据上面的结果继续后续步骤（例如调用 import_3d_asset 导入到 Houdini）。"
        t = self._meshy_bg.get(op)
        if t is not None:
            t["done"] = True
            t["ok"] = ok
        self._enqueue_bg_feedback(text, label)

    def _await_or_background(self, op, runnable, on_bg_complete):
        """在子线程跑 runnable()，在 agent 线程上等待：要么完成（返回
        ('done', result, error)），要么用户点了"转入后台"（立即返回 ('bg', None, None)，
        子线程继续，完成时回调 on_bg_complete(result, error)）。"""
        holder = {}
        done_evt = threading.Event()
        evt = self._meshy_bg_requests.get(op) or threading.Event()

        def work():
            try:
                holder["res"] = runnable()
            except Exception as e:
                holder["err"] = e
            done_evt.set()
            if evt.is_set():
                try:
                    on_bg_complete(holder.get("res"), holder.get("err"))
                except Exception as ex:
                    print("[meshy] gallery bg deliver failed:", ex)

        threading.Thread(target=work, daemon=True).start()
        while True:
            if done_evt.wait(timeout=0.15):
                return ("done", holder.get("res"), holder.get("err"))
            if evt.is_set():
                return ("bg", None, None)

    @Slot()
    def _flush_bg_feedback(self):
        """主线程：若 agent 空闲，则把已完成的后台任务结果作为新一轮发给它继续。"""
        if self._running:
            return   # 当前还在跑——等这轮结束后由 _ui_done 再调用一次
        with self._meshy_bg_lock:
            if not self._meshy_bg_feedback:
                return
            batch = list(self._meshy_bg_feedback)
            self._meshy_bg_feedback = []
            # 清掉已完成且已投递的后台任务
            for k in [k for k, v in self._meshy_bg.items() if v.get("done")]:
                self._meshy_bg.pop(k, None)
        text = "\n\n".join(batch)
        if not self._session:
            # 无后端（预览）时只提示
            self.toast.emit(self.tr("后台任务完成"))
            return
        # 静默喂给 agent：不往聊天窗口里塞这条原始后台反馈（避免困扰用户）。
        # agent 收到后据此继续，它后续的动作/回复会正常显示。
        self._log_chat("BG", text)
        self._start_run(text, tools=None, max_iter=None, images=None)

    @Slot(result="QVariantMap")
    def _meshy_task_status(self, kwargs):
        op = (kwargs or {}).get("op") or (kwargs or {}).get("task_id")
        items = list(self._meshy_bg.values())
        if op:
            op = str(op)

            def _match(t_op):
                # 完整 token 精确匹配；只有 hex 任务号（agent 拿 op[:8] 查）才按前缀匹配，
                # 且要求前缀长度>=8，避免短数字 token "batch1" 误命中 "batch10"。
                return t_op == op or (len(op) >= 8 and t_op.startswith(op))

            items = [t for t in items if _match(t.get("op", ""))]
            if not items:
                return {"success": True, "error": "",
                        "result": "没找到任务 %s（可能已完成并已把结果发给你了）。" % op}
        if not items:
            return {"success": True, "error": "",
                    "result": "当前没有正在后台运行的 Meshy 任务。", "data": {"tasks": []}}
        lines = ["当前后台 Meshy 任务："]
        for t in items:
            lines.append("- %s · 任务号 %s · %s · %d%% · %s%s"
                         % (t.get("label"), (t.get("op") or "")[:8], t.get("stage"),
                            int(t.get("progress", 0)), t.get("status"),
                            "（已完成，结果即将发给你）" if t.get("done") else ""))
        return {"success": True, "error": "", "result": "\n".join(lines),
                "data": {"tasks": items}}

    def _run_meshy_concept(self, kwargs, mode="concept", tool_name=""):
        """Concept/image gallery flow (human-in-the-loop, worker thread): generate
        N images in parallel -> gallery (multi-select + editable prompt + regenerate
        + 二次图生图编辑) -> finish with images (mode 'image') or image-to-3d for the
        chosen ones (mode 'concept'). Loops in-place (no LLM round-trip). If reference
        images are supplied (meshy_image_to_image, or the user picks 'edit'),
        generation goes through Meshy image-to-image."""
        maxp = getattr(_meshy, "MAX_PARALLEL", 4)
        prompt = (kwargs.get("prompt") or "").strip()
        prompts_in = kwargs.get("prompts")
        prompt_drop = 0
        if isinstance(prompts_in, (list, tuple)) and any(str(p).strip() for p in prompts_in):
            # 多个不同提示词，各并行出一张
            clean = [str(p).strip() for p in prompts_in if str(p).strip()]
            if len(clean) > maxp:
                prompt_drop = len(clean) - maxp   # 超出单次上限的提示词，明确告知而非静默丢
            prompt_list = clean[:maxp]
        elif prompt:
            # 单一提示词的 count 个变体
            cnt = max(1, min(int(kwargs.get("count", 2) or 2), maxp))
            prompt_list = [prompt] * cnt
        else:
            return {"success": False, "error": "缺少 prompt 或 prompts"}
        card_prompt = prompt or prompt_list[0]
        raw_model = str(kwargs.get("ai_model") or "").strip()
        ai_model = raw_model if raw_model else self._effective_default_image_model()
        aspect_ratio = kwargs.get("aspect_ratio", "1:1")
        enable_pbr = bool(kwargs.get("enable_pbr", True))
        topology = kwargs.get("topology", "triangle")
        poly = int(kwargs.get("target_polycount", 30000) or 30000)

        # 初始参考图（meshy_image_to_image 用 image/images）：有则首轮即走图生图
        refs_in = kwargs.get("images")
        if not (isinstance(refs_in, (list, tuple)) and refs_in):
            single = kwargs.get("image")
            refs_in = [single] if single else []
        base_refs = [r for r in refs_in if r] or None
        # 图生图参考图优先级：
        #  1) 模型显式给的、确实存在的本地文件——这正是画廊"二次编辑选中概念图"指令里
        #     回传的真实概念图路径，必须优先，绝不能被会话里更早的旧附图覆盖（否则
        #     用户一旦本会话传过任意图，之后所有概念图二次编辑都会去改那张旧图）。
        #  2) 否则用用户本会话附带的图（模型常编造不存在的 URL/路径，附图更可信）。
        #  3) 否则用模型给的 URL/data URI 原样。
        if tool_name == "meshy_image_to_image":
            model_local = [r for r in (base_refs or []) if os.path.isfile(str(r))]
            attached = list(getattr(self, "_last_attached_images", []) or [])
            if model_local:
                base_refs = model_local[:5]
            elif attached:
                base_refs = attached[:5]
            if base_refs is None:
                return {"success": False,
                        "error": "meshy_image_to_image 需要参考图：请在对话中附带一张图片，"
                                 "或用 image 参数给出图片的本地路径/URL。"}

        self._int_seq += 1
        token = "concept%d" % self._int_seq
        q = queue.Queue()
        self._interactive[token] = q

        # 注册为可后台化任务（用 token 当 op）：长生成阶段可「转入后台」
        label = MESHY_LABELS.get(tool_name, "图片生成")
        bg_evt = threading.Event()
        self._meshy_bg_requests[token] = bg_evt
        self._meshy_bg[token] = {"op": token, "tool": tool_name or "meshy_image_to_image",
                                 "label": label, "stage": "生成图片", "progress": 0,
                                 "status": "IN_PROGRESS", "done": False, "ok": None, "summary": ""}
        _STAGE = {"gen": "生成图片", "making3d": "生成3D", "pick": "等待选择",
                  "done": "完成", "cancelled": "已取消", "background": "已转入后台"}

        def show(**fields):
            t = self._meshy_bg.get(token)
            if t is not None:
                if "progress" in fields:
                    try:
                        t["progress"] = int(fields["progress"])
                    except Exception:
                        pass
                ph = fields.get("phase")
                if ph:
                    t["stage"] = _STAGE.get(ph, ph)
            payload = {"token": token, "mode": mode, "op": token, "model": ai_model}
            payload.update(fields)
            try:
                self._sigConcept.emit(json.dumps(payload, default=str))
            except Exception:
                pass

        def should_stop():
            if bg_evt.is_set():
                return False   # 已转后台：不随 agent 的停止 / 新一轮而中断
            try:
                return bool(self._session and self._session.client.is_stop_requested())
            except Exception:
                return False

        def imgs(concepts):
            return [{"index": c["index"], "image": c["image"], "prompt": c.get("prompt", "")}
                    for c in concepts]

        # 待生成请求 {prompts, refs}：首轮取初始提示词（图生图工具带初始参考图）；
        # 之后由 regenerate / edit 重新设置。refs 非空 → 走图生图。
        pending = {"prompts": prompt_list, "refs": base_refs}
        concepts = []

        try:
            while True:
                if should_stop():
                    show(phase="cancelled", note="已停止")
                    return {"success": False, "error": "stopped"}

                if pending is not None:
                    gen_prompts = pending["prompts"]
                    gen_refs = pending.get("refs")
                    gen_per_refs = pending.get("per_refs")
                    gen_count = max(1, len(gen_prompts))
                    pending = None
                    editing = bool(gen_refs or gen_per_refs)
                    note0 = (("图生图编辑中 · %d 张…" % gen_count) if editing
                             else ("并行生成 %d 张图片…" % gen_count))
                    if prompt_drop:
                        note0 += "（单次最多 %d 张，已略去多余的 %d 个提示词）" % (maxp, prompt_drop)
                        prompt_drop = 0   # 只提示一次
                    # render N empty slots up-front so parallel generation is visible
                    show(phase="gen", prompt=card_prompt, count=gen_count, progress=0,
                         images=[], selected=[], note=note0)
                    done_imgs = []

                    def on_img(c, _n=gen_count):
                        done_imgs.append({"index": c["index"], "image": c["image"],
                                          "prompt": c.get("prompt", "")})
                        show(phase="gen", prompt=card_prompt, count=_n,
                             images=list(done_imgs), progress=len(done_imgs) * 100 // _n,
                             note="%d/%d 完成" % (len(done_imgs), _n))

                    def deliver_gen_bg(res, err):
                        # 后台生成完成：在独立消息行弹出完整可交互画廊，让用户照常挑选
                        if err or not res:
                            self._enqueue_bg_feedback(
                                "【后台任务】%s 失败：%s" % (label, err or "未知"), label)
                            return
                        concepts_full = [{"index": c["index"], "image": c["image"],
                                          "prompt": c.get("prompt", "")} for c in res]
                        with self._meshy_bg_lock:
                            self._bg_galleries[token] = {
                                "concepts": concepts_full, "mode": mode, "model": ai_model,
                                "card_prompt": card_prompt, "tool": tool_name, "label": label}
                            self._meshy_bg.pop(token, None)   # 不再算"运行中"
                        self._sigShowBgGallery.emit(token)

                    status, concepts, gerr = self._await_or_background(
                        token,
                        lambda: _meshy.generate_concepts(
                            gen_prompts, ai_model, aspect_ratio=aspect_ratio,
                            reference_images=gen_refs, per_prompt_refs=gen_per_refs,
                            on_progress=lambda s, p, st: None,
                            on_image=on_img, should_stop=should_stop),
                        deliver_gen_bg)
                    if status == "bg":
                        # 转后台：这张卡片定格（显示已生成的部分），完成后会另弹一张可交互画廊
                        show(phase="background", prompt=card_prompt,
                             images=list(done_imgs),
                             note="已转入后台运行中…完成后会在下方弹出可挑选的画廊")
                        return {"success": True, "error": "",
                                "result": ("已将【%s】的概念图生成转入后台（任务号 %s）。完成后会"
                                           "【自动弹出可交互画廊让用户挑选】——你无需处理这批图，"
                                           "继续响应用户的其他需求即可。" % (label, token[:8])),
                                "data": {"background": True, "op": token}}
                    if gerr:
                        show(phase="cancelled", note="生成失败: %s" % gerr)
                        return {"success": False, "error": "图片生成失败: %s" % gerr}

                show(phase="pick", prompt=card_prompt, count=len(concepts),
                     images=imgs(concepts), selected=[], progress=100, note="")
                # 分段等待用户选择：每 0.3s 检查一次停止/转后台请求，最长 1800s。
                # 否则一次性 q.get(1800) 阻塞期间用户点 Stop 完全没反应（要等满 30 分钟）。
                decision = None
                _waited = 0.0
                while decision is None:
                    if should_stop():
                        show(phase="cancelled", note="已停止")
                        return {"success": False, "error": "stopped"}
                    if bg_evt.is_set():
                        decision = {"action": "cancel"}
                        break
                    try:
                        decision = q.get(timeout=0.3)
                    except queue.Empty:
                        _waited += 0.3
                        if _waited >= 1800:
                            decision = {"action": "cancel"}
                action = (decision or {}).get("action")

                if action == "set_model":
                    # 用户在画廊里换了生图模型：用相同提示词就地换模型重出（不绕回 Agent、不改提示词）
                    nm = (decision.get("model") or "").strip()
                    if nm:
                        ai_model = nm
                    cur_prompts = [c.get("prompt") for c in concepts if c.get("prompt")] or prompt_list
                    pending = {"prompts": cur_prompts, "refs": base_refs}
                    show(phase="gen", prompt=card_prompt, count=len(cur_prompts),
                         images=[], selected=[], progress=0,
                         note="切换模型 %s · 重新生成…" % self.imageModelDisplayName(ai_model))
                    continue

                if action == "regenerate":
                    # 不直接拿用户文字调 API：把反馈交回 Agent，让它理解意图、改写提示词后重做
                    fb = (decision.get("prompt") or "").strip()
                    gen_tool = "meshy_image_to_image" if base_refs else "meshy_text_to_image"
                    show(phase="done", images=imgs(concepts), progress=100,
                         note="已把你的意见交给 Agent，优化提示词后重做…")
                    lines = ["【用户反馈 · 要求重做这批图】"]
                    lines.append("用户意见：%s" % fb if fb
                                 else "用户没满意但未给具体意见——请你判断如何改进。")
                    lines.append("当前提示词：%s" % card_prompt)
                    lines.append("请【理解用户意图、自行改写/优化提示词】（不要原样照搬用户字面），"
                                 "然后重新调用 %s 生成新的一批。" % gen_tool)
                    return {"success": True, "result": "\n".join(lines), "error": "",
                            "data": {"action": "regenerate", "feedback": fb,
                                     "prev_prompt": card_prompt}}

                if action == "edit":
                    # 不直接调 API：把"选中的图 + 用户改动意见"交回 Agent，由它写出更好的
                    # 图生图提示词，再调用 meshy_image_to_image(image=该图路径) 重做。
                    fb = (decision.get("prompt") or "").strip()
                    sel = [int(i) for i in (decision.get("selected") or [])]
                    sel_imgs = [c["image"] for c in concepts if c["index"] in sel]
                    if not sel_imgs:
                        show(phase="pick", prompt=card_prompt, count=len(concepts),
                             images=imgs(concepts), note="请先选中要编辑的图片，再点二次编辑")
                        continue
                    if not fb:
                        show(phase="pick", prompt=card_prompt, count=len(concepts),
                             images=imgs(concepts), note="请在提示词框写明想要的改动，再点二次编辑")
                        continue
                    show(phase="done", images=imgs(concepts), selected=sel, progress=100,
                         note="已把你的反馈交给 Agent，做二次编辑…")
                    lines = ["【用户反馈 · 二次编辑选中图】",
                             "用户希望的改动：%s" % fb,
                             "选中的参考图（本地路径）："]
                    lines += ["- %s" % p for p in sel_imgs]
                    lines.append("请理解意图、写出更好的英文编辑提示词，然后调用 "
                                 "meshy_image_to_image(image=\"<上面某个路径>\", prompt=\"<优化后的编辑提示>\") 重做"
                                 "（要同时参考多张就用 images=[...]）。")
                    return {"success": True, "result": "\n".join(lines), "error": "",
                            "data": {"action": "edit", "feedback": fb, "images": sel_imgs}}

                if action == "done":
                    # 2D-only: finish with the (selected, or all) images, no 3D
                    selset = set(int(i) for i in (decision.get("selected") or []))
                    kept = [c for c in concepts if c["index"] in selset] or concepts
                    show(phase="done", images=imgs(concepts),
                         selected=sorted(selset), progress=100,
                         note="已保留 %d 张图片" % len(kept))
                    lines = ["已生成 %d 张图片：" % len(kept)]
                    for c in kept:
                        lines.append("- %s" % c["image"])
                    lines.append("若要把某张做成 3D，调用 meshy_image_to_3d(image=\"<本地路径>\")；"
                                 "若要在某张基础上继续改图，调用 meshy_image_to_image(image=\"<本地路径>\", prompt=\"…\")。")
                    return {"success": True, "result": "\n".join(lines), "error": "",
                            "data": {"images": kept}}

                if action != "submit":
                    show(phase="cancelled", note="已取消")
                    return {"success": False, "error": "用户取消了图片选择"}

                sel = [int(i) for i in (decision.get("selected") or [])]
                chosen = [c for c in concepts if c["index"] in sel]
                if not chosen:
                    # 未选中任何图就点"做成 3D"：不默认拿第一张去计费（每个都烧 credits），
                    # 回到选择态提示用户先选，避免无感知扣费。
                    show(phase="pick", prompt=card_prompt, count=len(concepts),
                         images=imgs(concepts),
                         note="请先选中要做成 3D 的图片（每个都会消耗 credits），再点生成")
                    continue
                sel_count = len(chosen)
                dropped_n = 0
                if sel_count > maxp:
                    # 单次最多并行 maxp 个 image-to-3d；超出的不静默丢弃，明确告知。
                    dropped_n = sel_count - maxp
                    chosen = chosen[:maxp]
                note3d = "生成 %d 个 3D 模型中…" % len(chosen)
                if dropped_n:
                    note3d += "（单次最多 %d 个，另 %d 张本次未处理）" % (maxp, dropped_n)
                show(phase="making3d", prompt=card_prompt, images=imgs(concepts),
                     selected=sel, progress=0, note=note3d)

                _PROC_NOTE = (
                    "【流程说明 · 概念图→3D 已完成】用户在画廊里选中了图片，并选择把它们"
                    "【直接升级成 3D 模型】——这是预期内的流程。下面是最终产物：【3D 模型】"
                    "（不是概念图）。请直接用 import_3d_asset 导入，"
                    "【不要再重新生成概念图，也不要重做 2D 图】。\n")

                m3errs = []

                def _models_summary(results):
                    out = ["3D 生成完成，共 %d 个模型：" % len(results)]
                    for r in results:
                        g = r.get("glb"); td = r.get("texture_dir")
                        out.append("- glb: %s%s" % (g, (" · texture_dir: %s" % td) if td else ""))
                    if dropped_n:
                        out.append("注意：你选了 %d 张，但单次最多并行 %d 个 image-to-3d，"
                                   "其余 %d 张本次未生成；如需补做请对它们再次发起 meshy_image_to_3d。"
                                   % (sel_count, maxp, dropped_n))
                    if m3errs:
                        out.append("注意：有 %d 张图的 3D 生成失败（可能已消耗 credits）：%s"
                                   % (len(m3errs), "; ".join(str(e)[:80] for e in m3errs)))
                    out.append("请对每个 glb 调用 import_3d_asset 导入 Houdini。")
                    return "\n".join(out)

                def deliver_3d_bg(results, err):
                    # 后台 3D 完成：不再更新卡片（已冻结），只把模型路径 + 流程说明发给 agent
                    if err or not results:
                        self._enqueue_bg_feedback("【后台任务】%s 3D 生成失败：%s"
                                                  % (label, err or "未知"), label)
                        return
                    self._enqueue_bg_feedback("【后台任务完成】" + _PROC_NOTE + _models_summary(results), label)

                status, results, m3err = self._await_or_background(
                    token,
                    lambda: _meshy.concepts_to_3d(
                        chosen, enable_pbr=enable_pbr, topology=topology,
                        target_polycount=poly,
                        on_progress=lambda s, p, st: show(phase="making3d", progress=p,
                                                          note="%s %s" % (s, st),
                                                          images=imgs(concepts), selected=sel),
                        should_stop=should_stop, errors_out=m3errs),
                    deliver_3d_bg)
                if status == "bg":
                    # 冻结卡片为"已转入后台"终态
                    show(phase="background", prompt=card_prompt, images=imgs(concepts),
                         selected=sel, note="已转入后台运行中…3D 完成后结果会自动发给 Agent")
                    return {"success": True, "error": "",
                            "result": ("已将【%s 的 3D 生成】转入后台（任务号 %s）。完成后我会把"
                                       "模型路径自动发给你以便导入。" % (label, token[:8])),
                            "data": {"background": True, "op": token}}
                if m3err:
                    show(phase="cancelled", note="3D 生成失败: %s" % m3err)
                    return {"success": False, "error": "3D 生成失败: %s" % m3err}

                res_thumbs = [{"index": r.get("concept_index", i),
                               "image": r.get("thumbnail") or ""}
                              for i, r in enumerate(results)]
                show(phase="done", images=imgs(concepts), selected=sel,
                     results=res_thumbs, progress=100,
                     note="生成的 3D 模型 · %d" % len(results))
                return {"success": True, "result": _PROC_NOTE + _models_summary(results),
                        "error": "", "data": {"models": results}}
        finally:
            self._interactive.pop(token, None)
            self._meshy_bg_requests.pop(token, None)
            # 内联完成（未转后台）的画廊任务不留在后台列表里
            if not bg_evt.is_set():
                self._meshy_bg.pop(token, None)
            self._last_activity = time.monotonic()

    def _run_meshy_batch(self, tool_name, kwargs, count):
        """Parallel multi-generation for a batch-capable Meshy tool (count>1):
        generate N variants concurrently, fill gallery slots as each finishes,
        then show the result thumbnails. Returns all GLBs for the agent to import.
        Non-interactive (no pick step) — reuses the gallery card via _sigConcept.
        可「转入后台」：长时间的 count>1 批量生成不再独占 agent 线程。"""
        count = max(1, min(int(count or 1), getattr(_meshy, "MAX_PARALLEL", 4)))
        self._int_seq += 1
        token = "batch%d" % self._int_seq
        labels = {"meshy_text_to_3d": "文生3D", "meshy_image_to_3d": "图生3D",
                  "meshy_retexture": "重打材质", "meshy_remesh": "重拓扑"}
        title = "%s ×%d" % (labels.get(tool_name, tool_name), count)
        label = labels.get(tool_name, tool_name)

        bg_evt = threading.Event()
        self._meshy_bg_requests[token] = bg_evt
        self._meshy_bg[token] = {"op": token, "tool": tool_name, "label": label,
                                 "stage": "生成", "progress": 0, "status": "IN_PROGRESS",
                                 "done": False, "ok": None, "summary": ""}

        def show(**fields):
            payload = {"token": token, "mode": "batch", "title": title,
                       "backgroundable": True}
            payload.update(fields)
            try:
                self._sigConcept.emit(json.dumps(payload, default=str))
            except Exception:
                pass

        def should_stop():
            if bg_evt.is_set():
                return False     # 已转后台：不随 agent 停止/新一轮而中断
            try:
                return bool(self._session and self._session.client.is_stop_requested())
            except Exception:
                return False

        show(phase="gen", count=count, images=[], progress=0,
             note="并行生成 %d 个…（可转入后台）" % count)
        done = []

        def on_item(i, data):
            done.append({"index": i, "image": data.get("thumbnail") or ""})
            show(phase="gen", count=count, images=list(done),
                 progress=len(done) * 100 // count,
                 note="%d/%d 完成" % (len(done), count))

        def _summary(results, errors):
            lines = ["并行生成完成，共 %d 个%s："
                     % (len(results), ("（请求 %d 个）" % count) if len(results) < count else "")]
            for r in results:
                g = r.get("glb")
                t = r.get("texture_dir")
                lines.append("- glb: %s%s" % (g, (" · texture_dir: %s" % t) if t else ""))
            if errors:
                lines.append("注意：有 %d 个生成失败（可能已消耗 credits）：%s"
                             % (len(errors), "; ".join(str(e)[:80] for e in errors)))
            lines.append("请对每个 glb 调用 import_3d_asset 导入 Houdini。")
            return "\n".join(lines)

        errs = []

        def deliver_bg(results, err):
            if err or not results:
                self._enqueue_bg_feedback("【后台任务】%s 失败：%s" % (label, err or "未知"), label)
                return
            self._enqueue_bg_feedback("【后台任务完成】" + _summary(results, errs), label)

        try:
            status, results, perr = self._await_or_background(
                token,
                lambda: _meshy.run_network_parallel(
                    tool_name, kwargs, count, on_item=on_item,
                    should_stop=should_stop, errors_out=errs),
                deliver_bg)
            if status == "bg":
                show(phase="background", count=count, images=list(done),
                     note="已转入后台运行中…完成后结果会自动发给 Agent")
                return {"success": True, "error": "",
                        "result": ("已将【%s ×%d】转入后台运行（任务号 %s）。完成后结果会"
                                   "自动作为新消息发给你——无需轮询。" % (label, count, token[:8])),
                        "data": {"background": True, "op": token}}
            if perr:
                show(phase="cancelled", note="失败: %s" % perr)
                return _meshy._err("并行生成失败: %s" % perr) if hasattr(_meshy, "_err") \
                    else {"success": False, "error": "并行生成失败: %s" % perr, "result": "", "data": {}}
            thumbs = [{"index": i, "image": r.get("thumbnail") or ""}
                      for i, r in enumerate(results)]
            show(phase="done", images=thumbs, results=thumbs, progress=100,
                 note="生成结果 · %d" % len(results))
            return {"success": True, "result": _summary(results, errs), "error": "",
                    "data": {"models": results}}
        finally:
            self._meshy_bg_requests.pop(token, None)
            if not bg_evt.is_set():
                self._meshy_bg.pop(token, None)
            self._last_activity = time.monotonic()

    @Slot(str)
    def _ui_meshy_progress(self, js):
        try:
            d = json.loads(js)
        except Exception:
            return
        # Meshy 生成期间只发进度信号、不走 content/tool 回调；这里刷新活动时间，
        # 否则长达数分钟的正常生成会被看门狗误判为"超过 45 秒无进展"。
        self._touch_activity()
        op = d.get("op")
        block = self._meshy_ops.get(op)
        if block is None:
            block = {"kind": "meshy", "op": op, "tool": d.get("tool", ""),
                     "stage": "", "progress": 0, "status": "", "done": False,
                     "ok": True, "thumb": "", "summary": ""}
            self._meshy_ops[op] = block
            self._blocks.append(block)
        block["stage"] = d.get("stage", block["stage"])
        block["progress"] = d.get("progress", block["progress"])
        block["status"] = d.get("status", block["status"])
        if d.get("done"):
            block["done"] = True
            block["ok"] = bool(d.get("ok", True))
            block["thumb"] = d.get("thumb", "")
            block["summary"] = d.get("summary", "")
        self._flush()

    @Slot(str)
    def _ui_concept(self, js):
        try:
            d = json.loads(js)
        except Exception:
            return
        self._touch_activity()   # 画廊生成/等待选择期间同样刷新活动时间，避免看门狗误报
        token = d.get("token")
        if not token:
            return
        block = self._concept_blocks.get(token)
        if block is None:
            block = {"kind": "concept", "token": token, "phase": "gen",
                     "prompt": "", "images": [], "selected": [], "progress": 0,
                     "note": "", "count": 2}
            self._concept_blocks[token] = block
            self._blocks.append(block)
        for k, v in d.items():
            if k == "token":
                continue
            block[k] = v
        self._flush()

    @Slot(str, str)
    def resolveConcept(self, token, decision_json):
        """QML -> worker: {action:'submit',selected:[..]} | {action:'regenerate',prompt} | {action:'cancel'}"""
        try:
            d = json.loads(decision_json) if decision_json else {}
        except Exception:
            d = {}
        q = self._interactive.get(token)
        if q is not None:
            q.put(d)
            return
        # 后台跑完弹出的画廊：原阻塞流程已结束，挑选结果转成给 Agent 的指令
        if token in self._bg_galleries:
            self._handle_bg_gallery_decision(token, d)

    @Slot(str)
    def _show_bg_gallery(self, token):
        """后台概念图跑完：在一条独立消息行里弹出完整可交互画廊（agent 忙时排队）。"""
        g = self._bg_galleries.get(token)
        if not g:
            return
        if self._running:
            if token not in self._pending_bg_galleries:
                self._pending_bg_galleries.append(token)
            return
        concepts = g["concepts"]
        # 独立系统消息行承载这张画廊（不调 _start_run，避免触发 agent 轮次）。
        # 每张后台画廊各占一行、各有自己的 BlockModel；用 token→bm 映射记下，
        # 这样后续解析某张画廊时能精确刷新它自己那一行（多张并存时不互相覆盖）。
        bm = BlockModel()
        self._bm = bm
        self._ai_row = self._model.append({"type": "ai", "payload": {"bm": bm}})
        block = {"kind": "concept", "token": token, "phase": "pick",
                 "mode": g.get("mode", "concept"), "prompt": g.get("card_prompt", ""),
                 "model": g.get("model", self._effective_default_image_model()),
                 "images": list(concepts), "selected": [], "progress": 100,
                 "count": len(concepts),
                 "note": "后台已生成 %d 张 · 勾选后可生成 3D / 二次编辑 / 换提示词" % len(concepts)}
        self._blocks = [block]
        if not isinstance(getattr(self, "_concept_blocks", None), dict):
            self._concept_blocks = {}
        self._concept_blocks[token] = block          # 更新而非整体替换，保留其它 token
        if not hasattr(self, "_bg_gallery_bm"):
            self._bg_gallery_bm = {}
        self._bg_gallery_bm[token] = bm
        bm.sync(self._blocks)
        self.toast.emit("后台已生成 %d 张概念图，请在画廊里挑选" % len(concepts))

    def _drain_pending_bg_galleries(self):
        if self._running or not self._pending_bg_galleries:
            return
        pend = self._pending_bg_galleries
        self._pending_bg_galleries = []
        for tok in pend:
            if tok in self._bg_galleries:
                self._show_bg_gallery(tok)

    def _handle_bg_gallery_decision(self, token, d):
        """后台画廊（原阻塞已结束）里的挑选：统一转成给 Agent 的指令并发起新一轮。"""
        g = self._bg_galleries.get(token)
        if not g:
            return
        action = (d or {}).get("action")
        concepts = g["concepts"]
        card_prompt = g.get("card_prompt", "")

        def _blk(**fields):
            blk = (self._concept_blocks or {}).get(token)
            if blk is None:
                return
            blk.update(fields)
            # 精确刷新这张画廊自己的那一行（不依赖 self._bm 当前指向哪一行），
            # 多张后台画廊并存时也能正确更新。
            bm = getattr(self, "_bg_gallery_bm", {}).get(token)
            if bm is not None:
                bm.sync([blk])
            else:
                self._do_flush()

        def _drop_token():
            self._bg_galleries.pop(token, None)
            getattr(self, "_bg_gallery_bm", {}).pop(token, None)

        if action == "submit":
            sel = [int(i) for i in (d.get("selected") or [])]
            chosen = [c for c in concepts if c["index"] in sel] or concepts[:1]
            paths = [c["image"] for c in chosen]
            _blk(phase="done", selected=sel, note="已交给 Agent 生成 3D…")
            _drop_token()
            lines = ["【用户操作 · 把选中的概念图做成 3D】",
                     "用户在后台完成的画廊里选了这 %d 张图，要【直接升级成 3D 模型】"
                     "（不是要你重做 2D 图）：" % len(paths)]
            lines += ["- %s" % p for p in paths]
            lines.append("请对每张调用 meshy_image_to_3d(image=\"<上面的本地路径>\") 生成，"
                         "再用 import_3d_asset 导入 Houdini。")
            self._start_run("\n".join(lines), tools=None, max_iter=None, images=None)
            return

        if action == "edit":
            fb = (d.get("prompt") or "").strip()
            sel = [int(i) for i in (d.get("selected") or [])]
            sel_imgs = [c["image"] for c in concepts if c["index"] in sel]
            if not sel_imgs or not fb:
                _blk(note="请先选中图片并在提示词框写明想要的改动，再点二次编辑")
                return
            _blk(phase="done", selected=sel, note="已把反馈交给 Agent，做二次编辑…")
            _drop_token()
            lines = ["【用户反馈 · 二次编辑选中图】", "用户希望的改动：%s" % fb,
                     "选中的参考图（本地路径）："]
            lines += ["- %s" % p for p in sel_imgs]
            lines.append("请理解意图、写出更好的英文编辑提示词，再调用 "
                         "meshy_image_to_image(image=\"<上面某个路径>\", prompt=\"<优化后的编辑提示>\") 重做。")
            self._start_run("\n".join(lines), tools=None, max_iter=None, images=None)
            return

        if action == "set_model":
            nm = (d.get("model") or "").strip()
            gen_tool = ("meshy_image_to_image" if g.get("tool") == "meshy_image_to_image"
                        else "meshy_text_to_image")
            _blk(phase="done", model=nm or g.get("model", ""),
                 note="切换模型 %s · 重新生成…" % self.imageModelDisplayName(nm))
            _drop_token()
            lines = ["【用户操作 · 切换生图模型重新生成】",
                     "目标模型：%s" % nm,
                     "原提示词：%s" % card_prompt,
                     "请使用相同提示词调用 %s(prompt=..., ai_model=%r) 重新生成。" % (gen_tool, nm)]
            self._start_run("\n".join(lines), tools=None, max_iter=None, images=None)
            return

        if action == "regenerate":
            fb = (d.get("prompt") or "").strip()
            gen_tool = ("meshy_image_to_image" if g.get("tool") == "meshy_image_to_image"
                        else "meshy_text_to_image")
            _blk(phase="done", note="已把你的意见交给 Agent，优化提示词后重做…")
            _drop_token()
            lines = ["【用户反馈 · 要求重做这批图】"]
            lines.append("用户意见：%s" % fb if fb
                         else "用户没满意但未给具体意见——请你判断如何改进。")
            lines.append("当前提示词：%s" % card_prompt)
            lines.append("请【理解用户意图、自行改写/优化提示词】（不要原样照搬用户字面），"
                         "再重新调用 %s 生成新的一批。" % gen_tool)
            self._start_run("\n".join(lines), tools=None, max_iter=None, images=None)
            return

        if action == "done":
            selset = sorted(set(int(i) for i in (d.get("selected") or [])))
            _blk(phase="done", selected=selset,
                 note="已保留 %d 张图片" % (len(selset) or len(concepts)))
            _drop_token()
            return

        # cancel / 其他
        _blk(phase="cancelled", note="已取消")
        _drop_token()

    @Slot()
    def toggleLibrary(self):
        self.setLibraryOpen(not self._library_open)

    @Slot(bool)
    def setLibraryOpen(self, on):
        on = bool(on)
        if on == self._library_open:
            return
        self._library_open = on
        self.libraryOpenChanged.emit()
        if on:
            # 每次打开都校验账号（拉余额）并重新拉取资产，保证看到的是最新状态
            self.syncMeshyAccount()
            if not self._library_busy:
                self.refreshLibrary()

    @Slot(result="QVariantList")
    def libraryItems(self):
        return list(self._library_items)

    @Slot()
    def refreshLibrary(self):
        self._library_page = 1
        self._load_library(page=1, append=False)

    @Slot()
    def loadMoreLibrary(self):
        self._load_library(page=self._library_page + 1, append=True)

    def _load_library(self, page, append):
        if _meshy is None:
            self.toast.emit(self.tr("Meshy 集成不可用"))
            return
        if self._library_busy:
            # 已有加载在飞：记一个挂起刷新标记，本次加载结束后补做一次整表刷新
            # （否则生成成功触发的自动刷新会被静默吞掉，新资产不显示）。
            if not append:
                self._library_refresh_pending = True
            return
        try:
            from houdini_agent.meshy import config as _mcfg
            if not _mcfg.has_api_key():
                self.toast.emit(self.tr("请先配置 Meshy API Key"))
                return
        except Exception:
            pass
        self._library_busy = True
        self._library_loading = True
        self.libraryLoadingChanged.emit()

        def work():
            items, err = [], ""
            try:
                items = _meshy.list_library(page_num=page, page_size=40)
            except Exception as e:
                err = str(e)
            try:
                self._sigLibrary.emit(json.dumps(
                    {"items": items, "append": append, "page": page, "error": err},
                    default=str))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    @Slot(str)
    def _ui_library(self, js):
        try:
            d = json.loads(js)
        except Exception:
            d = {}
        self._library_busy = False
        if self._library_loading:
            self._library_loading = False
            self.libraryLoadingChanged.emit()
        err = d.get("error")
        if err:
            self.toast.emit(self.tr("资产库加载失败") + ": " + str(err)[:120])
            return
        items = d.get("items") or []
        if d.get("append"):
            seen = set(x.get("id") for x in self._library_items)
            added = 0
            for it in items:
                if it.get("id") not in seen:
                    self._library_items.append(it)
                    added += 1
            if added:
                self._library_page = int(d.get("page") or self._library_page)
            else:
                self.toast.emit(self.tr("没有更多了"))
        else:
            # 整表替换时，把仍在导入中的条目的状态按 id 迁移到新对象，
            # 避免刷新使正在导入的卡片丢失进度/cached 标记（item 变孤儿）。
            old_by_id = {x.get("id"): x for x in self._library_items}
            for it in items:
                old = old_by_id.get(it.get("id"))
                if old:
                    for k in ("importing", "import_stage", "cached", "local_glb"):
                        if old.get(k):
                            it[k] = old[k]
            self._library_items = items
            self._library_page = int(d.get("page") or 1)
        self.libraryChanged.emit()
        # 导入/生成期间被推迟的刷新：本次加载结束后补做一次。
        if getattr(self, "_library_refresh_pending", False) and not self._library_busy:
            self._library_refresh_pending = False
            self.refreshLibrary()

    @Slot(str)
    def importLibraryItem(self, task_id):
        if _meshy is None:
            return
        if not hasattr(self, "_importing_ids"):
            self._importing_ids = set()
        # 用 id 级别去重，而非依赖 item["importing"]——刷新会换掉 item 对象、丢掉该标记，
        # 否则刷新后可对同一资产重复点击触发双重下载/导入。
        if task_id in self._importing_ids:
            return
        item = next((x for x in self._library_items if x.get("id") == task_id), None)
        if not item:
            self.toast.emit(self.tr("找不到该资产"))
            return
        self._importing_ids.add(task_id)

        def _cur():
            # 始终取"当前列表里这个 id"的对象（刷新后是新对象），取不到回退到原 item。
            return next((x for x in self._library_items if x.get("id") == task_id), item)

        def stage(label):
            it = _cur()
            it["importing"] = True
            it["import_stage"] = label
            self.libraryChanged.emit()

        def finish(ok, msg=""):
            it = _cur()
            it["importing"] = False
            it["import_stage"] = ""
            self._importing_ids.discard(task_id)
            self.libraryChanged.emit()
            if msg:
                (self.toast.emit if ok else
                 (lambda m: self._sigInfo.emit(self.tr("导入失败"), m)))(msg)

        stage(self.tr("下载中…"))

        def work():
            try:
                data = _meshy.fetch_library_asset(item)
            except Exception as e:
                finish(False, str(e)[:600])
                return
            glb = data.get("glb")
            if not glb:
                finish(False, self.tr("未获得 glb（云端链接可能已过期）"))
                return
            stage(self.tr("导入中…"))
            args = {"glb_path": glb}
            if data.get("texture_dir"):
                args["texture_dir"] = data["texture_dir"]
            res = self._exec_houdini_tool("import_3d_asset", args)
            if isinstance(res, dict) and res.get("success"):
                it = _cur()
                it["cached"] = True
                it["local_glb"] = glb
                finish(True, self.tr("已导入") + ": " + os.path.basename(glb))
            else:
                finish(False, str((res or {}).get("error", "未知错误"))[:600])

        threading.Thread(target=work, daemon=True).start()

    @Slot(result="QVariantMap")
    def meshyAccount(self):
        """返回账号连接状态供 UI 展示。balance: -1 表示未校验/未知。"""
        connected = False
        masked = ""
        if _meshy is not None:
            try:
                connected = _meshy.has_api_key()
                masked = _meshy.masked_key() if connected else ""
            except Exception:
                pass
        return {"connected": bool(connected), "key": masked,
                "balance": int(self._meshy_balance)}

    @Slot()
    def syncMeshyAccount(self):
        """后台校验 Key 并拉取账号余额（credits）。完成后发 meshyAccountChanged。"""
        if _meshy is None or self._meshy_account_busy:
            self.meshyAccountChanged.emit()
            return
        if not _meshy.has_api_key():
            self._meshy_balance = -1
            self.meshyAccountChanged.emit()
            return
        self._meshy_account_busy = True

        def work():
            res = {"connected": True, "balance": -1, "error": ""}
            try:
                r = _meshy.run_network("meshy_balance", {})
                if isinstance(r, dict) and r.get("success"):
                    res["balance"] = int((r.get("data") or {}).get("balance", -1))
                else:
                    res["error"] = (r or {}).get("error", "校验失败")
            except Exception as e:
                res["error"] = str(e)
            try:
                self._sigMeshyAccount.emit(json.dumps(res, default=str))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    @Slot(str)
    def _ui_meshy_account(self, js):
        try:
            d = json.loads(js)
        except Exception:
            d = {}
        self._meshy_account_busy = False
        bal = d.get("balance", -1)
        try:
            self._meshy_balance = int(bal)
        except Exception:
            self._meshy_balance = -1
        err = d.get("error")
        if err:
            self.toast.emit(self.tr("Meshy 账号校验失败") + ": " + str(err)[:120])
        self.meshyAccountChanged.emit()

    @Slot()
    def openMeshyLogin(self):
        """打开 API Key 对话框（登录 = 填入账号的 API Key）。"""
        try:
            self.openApiKeyDialog.emit("meshy")
        except Exception as e:
            print("[controller] open meshy login failed:", e)

    @Slot()
    def meshyLogout(self):
        """退出登录：清除 Key，重置账号与资产库状态。"""
        if _meshy is not None:
            try:
                _meshy.clear_api_key()
            except Exception as e:
                print("[controller] meshy logout failed:", e)
        self._meshy_balance = -1
        self._library_items = []
        self._library_page = 1
        self.meshyAccountChanged.emit()
        self.libraryChanged.emit()
        self.toast.emit(self.tr("已退出 Meshy 账号"))

