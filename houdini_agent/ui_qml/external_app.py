# -*- coding: utf-8 -*-
"""Standalone double-click entry point for Houdini Agent."""

import os
import threading
import traceback
from pathlib import Path

os.environ.setdefault("QML_DISABLE_DISK_CACHE", "1")

try:
    from PySide6.QtCore import QSettings, QTimer, QObject, Signal
    from PySide6.QtWidgets import QApplication, QMainWindow
except ImportError:
    from PySide2.QtCore import QSettings, QTimer, QObject, Signal
    from PySide2.QtWidgets import QApplication, QMainWindow

from houdini_agent.bridge.client import BridgeClient
from houdini_agent.launcher.houdini_discovery import (
    find_houdini_installs, install_all_packages, is_houdini_running, launch_houdini
)
from houdini_agent.ui_qml import host
from houdini_agent.ui_qml.bridge_session import BridgeAgentSession
from houdini_agent.ui_qml.controller import ChatModel, Controller
from houdini_agent.ui_qml.houdini_picker import pick_houdini


_GEO_ORG = ("HoudiniAI", "External")


class ExternalWindow(QMainWindow):
    def closeEvent(self, event):
        try:
            QSettings(*_GEO_ORG).setValue("geometry", self.saveGeometry())
        except Exception:
            pass
        try:
            ctrl = getattr(self, "_controller", None)
            if ctrl is not None:
                ctrl._snapshot_active()
                ctrl._save_all()
        except Exception:
            pass
        super().closeEvent(event)


class ExternalCoordinator(QObject):
    # 后台探测线程通过此信号把结果回传到 UI 线程（跨线程 emit 默认走队列连接）。
    _probe_done = Signal(object)
    # 后台构建好的聊天后端（BridgeAgentSession，失败为 None）回传到 UI 线程挂载。
    _session_ready = Signal(object)

    def __init__(self, win, controller, repo_root):
        super().__init__(win)
        self.win = win
        self.controller = controller
        self.repo_root = str(repo_root)
        self.bridge = BridgeClient()
        self.connected = False
        self.prompted = False
        self.installs = []
        self._running_seen_at_start = False
        self._probing = False
        self._building_session = False
        self._session_build_fails = 0
        self._misses = 0            # 已连接状态下心跳连续失败次数
        self._ever_connected = False
        self.poll = QTimer(win)
        self.poll.setInterval(1500)
        # 周期探测改走非阻塞路径：在后台线程 ping，避免冻结 UI 主线程。
        self.poll.timeout.connect(self._poll_tick)
        self._probe_done.connect(self._on_poll_result)
        self._session_ready.connect(self._on_session_ready)

    def _log(self, msg):
        try:
            import time
            p = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "HoudiniAgent" / "launcher.log"
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + str(msg).rstrip() + "\n")
        except Exception:
            pass

    def start(self):
        self.installs = find_houdini_installs()
        self._ensure_packages()
        # ★ 不等 Bridge：先把聊天后端建好。没有 Houdini 也必须能正常聊天/用 Meshy，
        #   场景工具在 Bridge 连上后自动生效（BridgeClient 每次请求现连、端口动态解析）。
        self._start_session_build()
        self._running_seen_at_start = is_houdini_running()
        self.poll.start()
        self._poll_tick()  # 立即非阻塞探测一次（结果异步回来）
        if self._running_seen_at_start:
            self.controller.toast.emit("检测到 Houdini 已打开，正在连接 Bridge…")
            QTimer.singleShot(5000, self._maybe_prompt_for_bridge)
        else:
            QTimer.singleShot(300, self.show_launcher)

    def _ensure_packages(self):
        """给所有相关 Houdini 版本装集成包：写全部候选用户目录（注册表 Documents /
        USERPROFILE / env），并覆盖安装扫描漏掉但用户目录存在的版本。"""
        try:
            written = install_all_packages(self.repo_root, self.installs)
            self._log("packages installed: %s" % written)
        except Exception as exc:
            print("[external launcher] package install failed:", exc)
            self._log("package install failed: %s" % exc)

    @staticmethod
    def _probe():
        """探一次 Bridge：先 ping 当前解析端口，不通就全端口扫描并自动采纳
        （治愈端口发现文件过期/环境变量指错/服务器顺延换端口）。可能阻塞 ~1 秒，
        只允许在后台线程调用。返回 ping 结果或 None。"""
        try:
            from houdini_agent.bridge import doctor
            _port, info = doctor.ensure_connected()
            return info
        except Exception:
            return None

    def check_bridge(self):
        """同步探测一次（仅用于用户主动触发的 prompt_relaunch；周期探测走 _poll_tick）。"""
        if self.connected:
            return True
        if not self._probe():
            return False
        return self._attach()

    def _attach(self):
        """Bridge 已响应：标记连接并广播。聊天后端在启动时就已异步构建（不依赖 Bridge），
        BridgeClient 每次请求现连，场景工具自此自动生效。轮询不停止而是降频为心跳，
        这样 Houdini 关闭/重启能被发现，恢复后自动重连。"""
        if self.connected:
            return True
        self.connected = True
        self._misses = 0
        self.poll.setInterval(5000)     # 心跳档：已连接时低频巡检
        if self.controller._session is not None:
            self.controller.toast.emit("已重新连接 Houdini Bridge" if self._ever_connected
                                       else "已连接 Houdini Bridge")
            try:
                self.controller.refreshContext()
            except Exception:
                pass
        else:
            # 后端还没建好/上次构建失败：现在重试，建好后 _on_session_ready 会补报连接
            self._start_session_build()
        self._ever_connected = True
        return True

    def _on_bridge_lost(self):
        """心跳连续丢失：进入断开状态并提速轮询等待恢复（恢复后 _attach 自动重连）。"""
        self.connected = False
        self._misses = 0
        self.poll.setInterval(1500)     # 恢复快轮询
        self.controller.toast.emit("Houdini 连接已断开——聊天不受影响，重新打开 Houdini 后会自动重连")
        try:
            self.controller.refreshContext()
        except Exception:
            pass

    def _start_session_build(self):
        """后台线程构建聊天后端（LLM 会话）。构建含系统提示词/文档索引，可能耗时数秒，
        放后台线程避免卡 UI；完成后经 _session_ready 信号回 UI 线程挂载。"""
        if (self._building_session or self.controller._session is not None
                or self._session_build_fails >= 3):
            return
        self._building_session = True

        def worker():
            session = None
            try:
                session = BridgeAgentSession(self.bridge)
            except Exception as exc:
                self._log("Chat backend init failed: %s\n%s" % (exc, traceback.format_exc()))
            try:
                self._session_ready.emit(session)   # 跨线程：队列连接，槽在 UI 线程执行
            except Exception:
                pass

        threading.Thread(target=worker, name="HAgentSessionBuild", daemon=True).start()

    def _on_session_ready(self, session):
        """运行在 UI 线程：挂载后台构建好的聊天后端。"""
        self._building_session = False
        if session is None:
            self._session_build_fails += 1
            if self._session_build_fails == 1:
                self.controller.toast.emit("AI 后端初始化失败，正在自动重试…")
            elif self._session_build_fails >= 3:
                self.controller.toast.emit(
                    "AI 后端多次初始化失败，聊天暂不可用。请反馈日志：%LOCALAPPDATA%\\HoudiniAgent\\launcher.log")
            return
        if self.controller._session is not None:
            return
        self._session_build_fails = 0
        self.controller.attach_backend_session(session, announce=False)
        if self.connected:
            self.controller.toast.emit("已连接 Houdini Bridge")

    def _poll_tick(self):
        """周期触发：在【后台线程】做可能阻塞的探测（ping+必要时扫描，最长约 1 秒），
        避免冻结 UI 主线程——这是之前端口被占用时 GUI 卡死的根因。
        未连接时是 1.5s 快轮询等待接入；已连接时是 5s 心跳巡检断线。"""
        if self.controller._session is None:
            self._start_session_build()   # 构建失败后的自动重试（最多 3 次）
        if self._probing:
            return
        self._probing = True

        def worker():
            info = self._probe()
            try:
                self._probe_done.emit(info)   # 跨线程：队列连接，槽在 UI 线程执行
            except Exception:
                pass

        threading.Thread(target=worker, name="HAgentBridgeProbe", daemon=True).start()

    def _on_poll_result(self, info):
        """运行在 UI 线程：应用后台探测结果（连接/断开状态机）。"""
        self._probing = False
        if info:
            self._misses = 0
            if not self.connected:
                self._attach()
            return
        if self.connected:
            self._misses += 1
            if self._misses >= 2:      # 连续两次心跳失败（约 10 秒）才判断开，避免抖动误报
                self._on_bridge_lost()

    def _maybe_prompt_for_bridge(self):
        if not self.connected:
            self.controller.toast.emit("当前 Houdini 未加载 Bridge。已安装 Bridge，请从这里打开或重启 Houdini。")
            self.show_launcher()

    def show_launcher(self):
        if self.connected or self.prompted:
            return
        self.prompted = True
        if not self.installs:
            self.installs = find_houdini_installs()
        selected = pick_houdini(self.installs, self.win)
        if selected:
            try:
                launch_houdini(self.repo_root, selected)
                self.controller.toast.emit("已启动 Houdini，正在等待 Bridge 连接…")
                self.poll.start()
            except Exception as exc:
                self.controller.toast.emit("启动 Houdini 失败：%s" % exc)

    def prompt_relaunch(self):
        """断开后由 UI 触发（如配置需要会话的功能时）：先探一次 Bridge，
        已恢复就直接重连，否则重弹启动器让用户重新打开 Houdini。"""
        self.connected = False
        self.prompted = False
        if self.check_bridge():
            self.controller.toast.emit("已重新连接到 Houdini。")
            return
        self.show_launcher()


def _app_icon():
    """品牌应用图标（运行时窗口/任务栏）。打包后取 _MEIPASS/assets，开发时取仓库 assets。"""
    import os, sys
    from PySide6.QtGui import QIcon
    cands = []
    base = getattr(sys, "_MEIPASS", None)
    if base:
        cands.append(os.path.join(base, "assets", "houdini-agent.ico"))
    cands.append(str(Path(__file__).resolve().parents[2] / "assets" / "houdini-agent.ico"))
    for c in cands:
        if os.path.isfile(c):
            return QIcon(c)
    return QIcon()


def show_tool():
    app = QApplication.instance() or QApplication([])
    app.setWindowIcon(_app_icon())
    repo_root = Path(__file__).resolve().parents[2]
    model = ChatModel()
    controller = Controller(model, use_backend=False)
    try:
        controller.restore()
    except Exception:
        pass
    view = host.create_view(controller=controller, model=model)

    prev = getattr(app, "_hagent_external_window", None)
    if prev is not None:
        try:
            prev.close()
            prev.deleteLater()
        except Exception:
            pass

    win = ExternalWindow()
    win.setWindowTitle("Houdini Agent")
    win.setStyleSheet("QMainWindow { background-color: #0d0d0d; }")
    win.setCentralWidget(view)
    win.setMinimumSize(420, 760)
    geo = QSettings(*_GEO_ORG).value("geometry")
    restored = False
    if geo is not None:
        try:
            restored = bool(win.restoreGeometry(geo))
        except Exception:
            restored = False
    if not restored:
        win.resize(440, 820)

    coord = ExternalCoordinator(win, controller, repo_root)
    try:
        controller.requestOpenHoudini.connect(coord.prompt_relaunch)
    except Exception:
        pass
    win._controller = controller
    win._coordinator = coord
    win.show()
    win.raise_()
    win.activateWindow()
    QTimer.singleShot(100, coord.start)
    QTimer.singleShot(4000, controller.silentUpdateCheck)
    app._hagent_external_window = win
    return win


def main():
    app = QApplication.instance() or QApplication([])
    show_tool()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
