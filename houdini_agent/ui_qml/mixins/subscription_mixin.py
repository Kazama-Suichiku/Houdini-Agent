# -*- coding: utf-8 -*-
"""ControllerSubscriptionMixin — extracted from controller.py."""

import json
import threading
import time
from ..controller_shared import CONTEXT_LIMITS, MODEL_MAP, PROVIDER_LABELS, Slot, VISION_MODELS, catalog


class ControllerSubscriptionMixin:
    def _cached_models_updated_at(self):
        """从磁盘缓存推断目录更新时间：取各 provider 最近一次 fetched_at 格式化为 "HH:MM"。"""
        try:
            cache = catalog.load_cache()
            latest = None
            for p in catalog.PROVIDER_SOURCES:
                fetched = cache.get("providers", {}).get(p, {}).get("fetched_at")
                if isinstance(fetched, (int, float)) and not isinstance(fetched, bool):
                    ts = float(fetched)
                    latest = ts if latest is None else max(latest, ts)
            if latest is not None:
                return time.strftime("%H:%M", time.localtime(latest))
        except Exception:
            pass
        return ""

    @Slot()
    def refreshModels(self):
        """后台拉取各 provider 的 /v1/models 并更新目录；无参、非阻塞、可重入安全。"""
        self._start_models_refresh(quiet=False)

    def _start_models_refresh(self, quiet):
        """启动后台刷新。quiet=True 时（启动自动刷新）不弹 toast。"""
        if self._models_refreshing:
            return
        self._models_refresh_quiet = bool(quiet)
        self._models_refreshing = True
        self.modelsRefreshingChanged.emit()

        def work():
            result = {}
            sub_synced = False
            try:
                result = catalog.refresh_all(timeout=10)
            except Exception:
                result = {}
            # 已登录 ChatGPT / Claude / Google 反重力订阅时同步拉取最新模型（自动升级 CPA 并同步全部订阅模型）
            try:
                from houdini_agent.subscription import manager
                logged_kinds = [k for k in ("codex", "claude", "antigravity") if manager.logged_in(k)]
                if logged_kinds:
                    need_sub = (
                        (not quiet)
                        or manager.healthy()
                        or any(manager.models_stale(k) for k in logged_kinds)
                        or ("codex" in logged_kinds and manager.image_models_stale())
                    )
                    if need_sub:
                        manager.ensure_running(timeout=20)
                        for k in logged_kinds:
                            try:
                                manager.refresh_models(k)
                                sub_synced = True
                            except Exception:
                                pass
            except Exception:
                pass
            if sub_synced:
                result["_sub_synced"] = True
            try:
                self._sigModels.emit(json.dumps(result, ensure_ascii=False))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _on_models_refreshed(self, payload_json):
        """主线程：应用后台刷新结果，就地更新 MODEL_MAP / CONTEXT_LIMITS / VISION_MODELS。"""
        try:
            result = json.loads(payload_json) if payload_json else {}
        except Exception:
            result = {}
        if not isinstance(result, dict):
            result = {}
        sub_synced = bool(result.pop("_sub_synced", False) or result.pop("_codex_synced", False))
        ok_any = sub_synced
        try:
            for p, ids in result.items():
                if not ids:
                    continue
                ok_any = True
                MODEL_MAP[p] = list(ids)
                for m in ids:
                    CONTEXT_LIMITS.setdefault(m, catalog.context_limit(m, p))
                    if catalog.supports_vision(m, p):
                        VISION_MODELS.add(m)
                    else:
                        VISION_MODELS.discard(m)
            # 保留当前选择：当前模型被下架时把它 append 到列表末尾，不静默切换用户选择
            if (self._provider in catalog.PROVIDER_SOURCES
                    and self._model_name
                    and self._model_name not in MODEL_MAP.get(self._provider, [])):
                MODEL_MAP.setdefault(self._provider, []).append(self._model_name)
            if ok_any:
                self._models_updated_at = time.strftime("%H:%M")
        except Exception:
            pass
        finally:
            self._models_refreshing = False
            self.modelsRefreshingChanged.emit()
            self.modelsChanged.emit()
            if sub_synced:
                self.imageModelChanged.emit()
                self.providerChanged.emit()
        if self._models_refresh_quiet:
            return
        if ok_any:
            self.showToast(self.tr("模型列表已更新"))
        else:
            self.showToast(self.tr("模型列表刷新失败，已沿用本地列表"))

    def _auto_refresh_models_if_stale(self):
        try:
            need = any(catalog.is_stale(p) for p in catalog.PROVIDER_SOURCES)
            if not need:
                from houdini_agent.subscription import manager
                for k in ("codex", "claude", "antigravity"):
                    if manager.logged_in(k) and (manager.models_stale(k)
                                                 or (k == "codex" and manager.image_models_stale())):
                        need = True
                        break
            if need:
                self._start_models_refresh(quiet=True)
        except Exception:
            pass

    def _sub_logged_in(self, provider=None):
        kind = self._sub_kind(provider)
        if not kind:
            return False
        try:
            from houdini_agent.subscription import manager
            return manager.logged_in(kind)
        except Exception:
            return False

    def _sub_target(self):
        """订阅对话框 / 退出登录作用的供应商：最近一次打开对话框时指定的，否则当前供应商。"""
        p = getattr(self, "_sub_dialog_provider", "") or ""
        if not self._sub_kind(p):
            p = self._provider if self._sub_kind() else "codex"
        return p

    @Slot()
    def subscriptionDialogOpened(self):
        """对话框每次打开：不在登录流程中时清掉上一次的提示（两种订阅共用一份状态）。"""
        if not self._sub.get("busy"):
            self._sub.update(message="", loginUrl="", deviceUrl="", deviceCode="")

    @Slot(result="QVariantMap")
    def subscriptionStatus(self):
        target = self._sub_target()
        kind = self._sub_kind(target)
        base = dict(self._sub, kind=kind, title=self.tr(PROVIDER_LABELS.get(target, target)))
        try:
            from houdini_agent.subscription import manager, CPA_VERSION
            accounts = manager.accounts(kind)
            installed = manager.installed()
        except Exception as e:
            return dict(base, installed=False, version="", accounts=[], error=str(e))
        return dict(base, installed=installed, version=CPA_VERSION,
                    accounts=[{"email": a["email"], "plan": a["plan"], "file": a["file"]} for a in accounts])

    def _sub_set(self, **kw):
        self._sub.update(kw)
        self.subscriptionChanged.emit()

    def _on_sub_event(self, kind, js):
        """主线程：应用后台订阅流程的事件。"""
        try:
            d = json.loads(js) if js else {}
        except Exception:
            d = {}
        if kind == "progress":
            self._sub_set(phase="install", progress=int(d.get("p", 0)),
                          message="%s %d%%" % (self.tr("正在下载订阅组件…"), int(d.get("p", 0))))
        elif kind == "phase":
            # message 是翻译表里的固定句子，suffix 是不需要翻译的附加内容（模型名、错误原文）
            self._sub_set(phase=d.get("phase", ""), message=self.tr(d.get("message", "")) + d.get("suffix", ""))
        elif kind == "url":
            self._sub_set(phase="login", loginUrl=d.get("url", ""),
                          message=self.tr("已在浏览器打开登录页，登录并授权后会自动完成。"))
        elif kind == "device":
            self._sub_set(phase="login", deviceUrl=d.get("url", ""), deviceCode=d.get("code", ""),
                          message=self.tr("在浏览器打开的页面里输入下面的验证码，授权后会自动完成。"))
        elif kind == "done":
            ok = bool(d.get("ok"))
            msg = self._sub_result_text(d)
            self._sub_set(busy=False, phase="", loginUrl="", deviceUrl="", deviceCode="", message=msg)
            if ok:
                self.toast.emit(msg or self.tr("已登录"))
                target = self._sub_target()
                names = self._provider_model_names(target)
                if self._provider == target and names and self._model_name not in names:
                    self._model_name = names[0]
                    self.modelChanged.emit()
                self.providerChanged.emit()     # 让顶栏 / 供应商面板重新读取登录状态

    def _sub_result_text(self, d):
        """按当前界面语言组织登录结果（manager 返回结果类型 code，不在底层写死文案）。"""
        code = d.get("code") or ("ok" if d.get("ok") else "error")
        en = self._lang == "en"
        if code == "ok":
            plan = d.get("plan") or ""
            return self.tr("已登录 ") + (d.get("who") or "") + ((" (%s)" if en else "（%s）") % plan if plan else "")
        if code == "cancelled":
            return self.tr("已取消登录")
        if code == "timeout":
            return self.tr("登录超时（15 分钟内未完成授权）")
        if code == "port":
            return self.tr("本机端口被占用，请关闭占用它的程序后重试：") + str(d.get("port") or "")
        if code == "failed":
            return self.tr("登录没有完成") + ((" (%s)" if en else "（%s）") % d.get("detail") if d.get("detail") else "")
        return self.tr("出错了：") + str(d.get("message") or "")

    @Slot(bool)
    def subscriptionLogin(self, device):
        if self._sub.get("busy"):
            return
        self._sub_set(busy=True, phase="install", progress=0, message=self.tr("正在准备订阅组件…"),
                      loginUrl="", deviceUrl="", deviceCode="")

        sub_kind = self._sub_kind(self._sub_target())

        def emit(kind, data):
            self._sigSub.emit(kind, json.dumps(data, ensure_ascii=False))

        def work():
            try:
                import webbrowser
                from houdini_agent.subscription import manager
                manager.install(progress=lambda p: emit("progress", {"p": p}))
                emit("phase", {"phase": "login", "message": "正在发起登录…"})

                def on_event(kind, data):
                    if kind == "url":
                        webbrowser.open(data)
                        emit("url", {"url": data})
                    elif kind == "device":
                        webbrowser.open(data.get("url", ""))
                        emit("device", data)

                res = manager.login(device=bool(device) and sub_kind == "codex", on_event=on_event,
                                    kind=sub_kind)
                if res.get("ok"):
                    emit("phase", {"phase": "start", "message": "登录成功，正在启动本机订阅服务…"})
                    manager.ensure_running()
                    try:
                        manager.refresh_models(sub_kind)
                    except Exception:
                        pass
                emit("done", res)
            except Exception as e:
                emit("done", {"ok": False, "code": "error", "message": str(e)})

        threading.Thread(target=work, daemon=True).start()

    @Slot()
    def subscriptionCancelLogin(self):
        try:
            from houdini_agent.subscription import manager
            manager.cancel_login()
        except Exception:
            pass

    def _confirm_sub_logout(self):
        self._sub_dialog_provider = self._provider
        kind = self._sub_kind()
        try:
            from houdini_agent.subscription import manager
            accs = manager.accounts(kind)
        except Exception:
            accs = []
        brand = {"claude": "Claude", "antigravity": "Google 反重力"}.get(kind, "ChatGPT")
        who = ", ".join(a.get("email") or brand for a in accs)
        body = self.tr("将删除这台电脑上保存的登录凭据：") + who
        if kind == "codex":
            body += "\n\n" + self.tr("OpenAI 账号本身不受影响；要彻底撤销授权，请在 ChatGPT 网页的安全设置里移除 Codex。")
        title = "退出 Google 反重力登录？" if kind == "antigravity" else ("退出 %s 订阅登录？" % brand)
        self._request_confirm(title, body, lambda: self.subscriptionLogout(""))

    @Slot(str)
    def subscriptionLogout(self, file_name):
        try:
            from houdini_agent.subscription import manager
            n = manager.logout(file_name or None, kind=self._sub_kind(self._sub_target()))
        except Exception as e:
            self.toast.emit("退出登录失败：%s" % e)
            return
        self._sub_set(message=self.tr("已退出登录") if n else "")
        self.providerChanged.emit()     # 供应商菜单的「未登录」标记跟着变

    @Slot()
    def subscriptionRefreshModels(self):
        sub_kind = self._sub_kind(self._sub_target())

        def work():
            try:
                from houdini_agent.subscription import manager
                manager.ensure_running()
                models = manager.refresh_models(sub_kind)
                self._sigSub.emit("phase", json.dumps({"phase": "", "message": "可用模型：",
                                                       "suffix": ", ".join(models)}, ensure_ascii=False))
            except Exception as e:
                self._sigSub.emit("phase", json.dumps({"phase": "", "message": "刷新模型失败：",
                                                       "suffix": str(e)}, ensure_ascii=False))
        threading.Thread(target=work, daemon=True).start()

    def _sub_error_hint(self, err):
        """订阅渠道的 CPA 报错翻译成一句人话。"""
        if not self._sub_kind():
            return ""
        e = str(err)
        if "auth_unavailable" in e or "no auth available" in e:
            return self.tr("订阅账号暂时不可用：通常是额度用完或被限流，等几分钟再试；"
                           "如果一直这样，退出登录后重新登录。")
        if "empty_stream" in e:
            return self.tr("上游连接在开始输出前断开了，一般是对方服务临时波动，重新发送即可。")
        return ""

