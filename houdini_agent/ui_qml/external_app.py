# -*- coding: utf-8 -*-
"""Standalone double-click entry point for Houdini Agent."""

import os
import traceback
from pathlib import Path

os.environ.setdefault("QML_DISABLE_DISK_CACHE", "1")

try:
    from PySide6.QtCore import QSettings, QTimer
    from PySide6.QtWidgets import QApplication, QMainWindow
except ImportError:
    from PySide2.QtCore import QSettings, QTimer
    from PySide2.QtWidgets import QApplication, QMainWindow

from houdini_agent.bridge.client import BridgeClient
from houdini_agent.launcher.houdini_discovery import (
    find_houdini_installs, install_package, is_houdini_running, launch_houdini
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


class ExternalCoordinator:
    def __init__(self, win, controller, repo_root):
        self.win = win
        self.controller = controller
        self.repo_root = str(repo_root)
        self.bridge = BridgeClient()
        self.connected = False
        self.prompted = False
        self.installs = []
        self._running_seen_at_start = False
        self.poll = QTimer(win)
        self.poll.setInterval(1000)
        self.poll.timeout.connect(self.check_bridge)

    def _log(self, msg):
        try:
            p = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "HoudiniAgent" / "launcher.log"
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(str(msg).rstrip() + "\n")
        except Exception:
            pass

    def start(self):
        self.installs = find_houdini_installs()
        self._ensure_packages()
        self._running_seen_at_start = is_houdini_running()
        if self.check_bridge():
            return
        self.poll.start()
        if self._running_seen_at_start:
            self.controller.toast.emit("检测到 Houdini 已打开，正在连接 Bridge…")
            QTimer.singleShot(5000, self._maybe_prompt_for_bridge)
        else:
            QTimer.singleShot(300, self.show_launcher)

    def _ensure_packages(self):
        for install in self.installs:
            try:
                install_package(self.repo_root, install)
            except Exception as exc:
                print("[external launcher] package install failed:", exc)

    def check_bridge(self):
        if self.connected:
            return True
        info = self.bridge.ping()
        if not info:
            return False
        try:
            session = BridgeAgentSession(self.bridge)
            self.controller.attach_backend_session(session)
            self.connected = True
            self.poll.stop()
            return True
        except Exception as exc:
            self._log("Bridge attach failed: %s\n%s" % (exc, traceback.format_exc()))
            self.controller.toast.emit("Bridge 已响应，但 Agent 后端初始化失败，正在重试…")
            return False

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
