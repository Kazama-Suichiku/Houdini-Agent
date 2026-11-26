# -*- coding: utf-8 -*-
"""ControllerUpdaterMixin — extracted from controller.py."""

import json
import sys
import threading
from ..controller_shared import QTimer, Slot


class ControllerUpdaterMixin:
    def _show_update_dialog(self, title, text):
        try:
            self.openUpdateDialog.emit(self.tr(title), str(text)[:3000])
        except Exception as e:
            print("[controller] update dialog:", title, text, e)

    def _set_update(self, info, state="available"):
        self._update_info = info
        self._update_state = state if info else ""
        self.updateAvailableChanged.emit()

    @Slot()
    def dismissUpdate(self):
        if self._update_state in ("downloading", "installing"):
            return                      # 下载 / 安装中不允许关横幅（没有取消语义）
        self._stop_update_timers()      # "waiting" 时关横幅 = 取消待安装
        self._update_info = None
        self._update_state = ""
        self._update_progress = 0
        self.updateAvailableChanged.emit()

    @Slot()
    def silentUpdateCheck(self):
        """静默检查更新；打包版开启自动静默更新时，在后台直接下载预解压校验，重启即秒级生效。"""
        # 启动 4 小时周期巡检（仅建一次），长开窗口也能后台拿新版本
        if getattr(self, "_periodic_update_timer", None) is None:
            t = QTimer(self)
            t.setInterval(4 * 3600 * 1000)
            t.timeout.connect(self.silentUpdateCheck)
            t.start()
            self._periodic_update_timer = t
        if self._silent_checking or self._update_state in ("downloading", "waiting", "installing"):
            return
        self._silent_checking = True
        auto_stage = bool(self._auto_silent_update and getattr(sys, "frozen", False))

        def work():
            try:
                from houdini_agent.utils.updater import check_update, get_staged_update, stage_update
                existing = get_staged_update()
                if existing:
                    self._sigUpdateState.emit("staged", 100, json.dumps(existing, ensure_ascii=False))
                r = check_update()
                if isinstance(r, dict) and r.get("has_update"):
                    rver = str(r.get("remote_version") or "")
                    if auto_stage:
                        if existing and existing.get("version") == rver:
                            return
                        st = stage_update()
                        if st.get("success") and st.get("staged"):
                            self._sigUpdateState.emit("staged", 100, json.dumps(st["staged"], ensure_ascii=False))
                            return
                    if not existing:
                        self._sigUpdateFound.emit("发现新版本 %s" % rver)
            except Exception:
                pass
            finally:
                self._silent_checking = False

        threading.Thread(target=work, daemon=True).start()

    @Slot()
    def startUpdate(self):
        """一键更新 / 立即重启应用已就绪更新。
        若后台已完成静默下载（state=='staged'），零等待直接切换并重启；否则下载后立即应用。"""
        if self._update_state in ("downloading", "waiting", "installing"):
            return
        if not getattr(sys, "frozen", False):
            try:
                import webbrowser
                webbrowser.open("https://houdini-agent.com")
            except Exception:
                pass
            self.toast.emit("源码运行无法就地更新，请 git pull 或从官网安装")
            return
        # 后台静默预下载已就绪 → 直接跳过下载，零等待重启切换！
        if self._update_state == "staged" and isinstance(self._staged_update, dict):
            self._try_install()
            return
        try:
            from houdini_agent.utils.updater import get_staged_update
            st = get_staged_update()
            if st:
                self._staged_update = st
                self._try_install()
                return
        except Exception:
            pass
        self._update_state = "downloading"
        self._update_progress = 0
        self.updateAvailableChanged.emit()

        def work():
            try:
                from houdini_agent.utils.updater import check_update, stage_update
                check_update()          # 刷新清单缓存，拿到最新 app_zip / installer 直链
                last = [-1]

                def prog(pct):
                    if pct != last[0]:
                        last[0] = pct
                        self._sigUpdateState.emit("downloading", int(pct), "")

                r = stage_update(progress_callback=prog)
                if r.get("success") and r.get("staged"):
                    self._sigUpdateState.emit("ready", 100, json.dumps(r["staged"], ensure_ascii=False))
                elif r.get("up_to_date"):
                    self._sigUpdateState.emit("uptodate", 100, "")
                else:
                    self._sigUpdateState.emit("failed", 0, r.get("error") or "下载失败")
            except Exception as e:
                self._sigUpdateState.emit("failed", 0, str(e))

        threading.Thread(target=work, daemon=True).start()

    def _on_update_state(self, state, percent, message):
        """运行在 UI 线程：应用更新状态机。staged → 提示重启即可；ready → 立即切换重启。"""
        self._update_state = state
        self._update_progress = percent
        if state == "downloading":
            self._update_info = "正在准备新版本… %d%%" % percent
        elif state == "staged":
            try:
                st = json.loads(message) if message else {}
            except Exception:
                st = {}
            if isinstance(st, dict) and st.get("version"):
                self._staged_update = st
                self._update_info = "新版本 %s 已就绪 · 重启即可生效" % st["version"]
            else:
                self._update_info = "新版本已就绪 · 重启即可生效"
        elif state == "uptodate":
            self._update_state = ""
            self._update_info = None
            self.toast.emit("当前已是最新版本")
        elif state == "failed":
            self._update_info = "更新下载失败：%s — 点「立即更新」重试" % (message or "")[:80]
            self._update_state = "available"
        elif state == "ready":
            try:
                st = json.loads(message) if message else None
            except Exception:
                st = None
            if isinstance(st, dict) and st.get("version"):
                self._staged_update = st
            self._try_install()
            return
        self.updateAvailableChanged.emit()

    def _update_busy(self):
        if self._running:
            return True
        try:
            with self._meshy_bg_lock:
                return any(not (v or {}).get("done") for v in self._meshy_bg.values())
        except Exception:
            return False

    def _stop_update_timers(self):
        for name in ("_update_wait_timer", "_update_watch_timer"):
            t = getattr(self, name, None)
            if t is not None:
                t.stop()

    def _update_timer(self, name, interval, slot):
        t = getattr(self, name, None)
        if t is None:
            t = QTimer(self)
            t.setInterval(interval)
            t.timeout.connect(slot)
            setattr(self, name, t)
        return t

    def _try_install(self):
        if self._update_busy():
            self._update_state = "waiting"
            self._update_progress = 100
            self._update_info = "新版本已就绪，等当前任务结束后自动重启更新（关闭横幅可取消）"
            self.updateAvailableChanged.emit()
            self._update_timer("_update_wait_timer", 1500, self._try_install).start()
            return
        self._stop_update_timers()
        self._begin_install()

    def _begin_install(self):
        try:
            from houdini_agent.utils.updater import apply_staged_update, get_staged_update
            st = self._staged_update or get_staged_update()
            res = apply_staged_update(st, relaunch=True)
        except Exception as e:
            self._update_state = "staged" if self._staged_update else "available"
            self._update_info = "应用更新失败：%s — 点「立即重启」重试" % e
            self.updateAvailableChanged.emit()
            return
        if not res.get("started"):
            self._update_state = "available"
            self._update_info = "应用更新失败：%s — 点「立即更新」重试" % (res.get("error") or "未知错误")
            self.updateAvailableChanged.emit()
            return
        self._update_applying = True
        if not res.get("needs_watch"):
            # 免安装程序原子替换 / 无 UAC 静默安装：直接保存并退出，1 秒后自动以新版重启
            self._update_state = "installing"
            self._update_info = "正在应用更新并重启…"
            self.updateAvailableChanged.emit()
            self._quit_for_update()
            return
        self._update_proc = res.get("proc")
        self._update_state = "installing"
        self._update_info = "正在应用更新…如弹出管理员授权，请点「是」"
        self.updateAvailableChanged.emit()
        self._update_timer("_update_watch_timer", 300, self._install_watch).start()

    def apply_staged_on_exit(self):
        """用户正常关闭应用时触发（ExternalWindow.closeEvent）：
        若后台已静默下载好且无需 UAC，则在退出后静默完成替换且不重开窗口，下次打开即已是新版。"""
        if self._update_applying or not getattr(sys, "frozen", False):
            return False
        try:
            from houdini_agent.utils.updater import (
                apply_staged_update, can_apply_silently_on_exit, get_staged_update,
            )
            st = self._staged_update or get_staged_update()
            if not st or not can_apply_silently_on_exit(st):
                return False
            self._update_applying = True
            res = apply_staged_update(st, relaunch=False)
            return bool(res.get("started"))
        except Exception:
            return False

    def _install_watch(self):
        from houdini_agent.utils.updater import install_watch_step, installer_window_visible
        proc = getattr(self, "_update_proc", None)
        code = proc.poll() if proc is not None else 0
        action, reason = install_watch_step(installer_window_visible(), code)
        if action == "wait":
            return
        self._stop_update_timers()
        self._update_proc = None
        if action == "quit":
            self._update_info = "正在安装，应用即将关闭并自动重启…"
            self.updateAvailableChanged.emit()
            self._quit_for_update()
        else:
            self._update_applying = False
            self._update_state = "staged" if self._staged_update else "available"
            self._update_info = "%s。点「立即重启」重试" % reason
            self.updateAvailableChanged.emit()

    def _quit_for_update(self):
        """保存会话后退出，让安装器替换文件（装完由安装器重启应用）。"""
        try:
            self._snapshot_active()
            self._save_all()
        except Exception:
            pass

        def _quit():
            try:
                from PySide6.QtWidgets import QApplication
            except ImportError:
                from PySide2.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                app.closeAllWindows()
                QTimer.singleShot(200, app.quit)
        QTimer.singleShot(300, _quit)

    def _check_update(self):
        self.toast.emit("正在检查更新…")
        def work():
            try:
                from houdini_agent.utils.updater import check_update
                r = check_update()
                if isinstance(r, dict):
                    if r.get("error"):
                        msg = "检查更新失败：%s" % r.get("error")
                    elif r.get("has_update"):
                        notes = r.get("release_notes") or r.get("release_name") or ""
                        msg = "发现新版本：%s\n当前版本：%s" % (
                            r.get("remote_version", "?"), r.get("local_version", "?"))
                        if notes:
                            msg += "\n\n" + notes
                        if r.get("stale"):
                            msg += "\n\n（暂时连不上更新服务器，以上为最近一次成功检查的结果）"
                        # 先置横幅状态（available），再弹带按钮的对话框；两个信号按发出顺序到主线程
                        self._sigUpdateFound.emit("发现新版本 %s" % r.get("remote_version", ""))
                        self._sigUpdateDialog.emit("检查更新", msg)
                        return
                    else:
                        msg = "已是最新版本。\n\n当前版本：%s\n最新 Release：%s" % (
                            r.get("local_version", "?"), r.get("remote_version", "?"))
                else:
                    msg = str(r)
            except Exception as e:
                msg = "检查失败：%s" % e
            self._sigInfo.emit("检查更新", msg)
        threading.Thread(target=work, daemon=True).start()

