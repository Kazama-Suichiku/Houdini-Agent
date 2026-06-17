# -*- coding: utf-8 -*-
"""Standalone double-click entry point for Houdini Agent."""

import os
import traceback
from pathlib import Path

os.environ.setdefault("QML_DISABLE_DISK_CACHE", "1")

try:
    from PySide6.QtCore import QSize, QSettings, QTimer, Qt
    from PySide6.QtWidgets import (
        QApplication, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
        QMainWindow, QPushButton, QVBoxLayout, QWidget
    )
except ImportError:
    from PySide2.QtCore import QSize, QSettings, QTimer, Qt
    from PySide2.QtWidgets import (
        QApplication, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
        QMainWindow, QPushButton, QVBoxLayout, QWidget
    )

from houdini_agent.bridge.client import BridgeClient
from houdini_agent.launcher.houdini_discovery import (
    find_houdini_installs, install_package, is_houdini_running, launch_houdini
)
from houdini_agent.ui_qml import host
from houdini_agent.ui_qml.bridge_session import BridgeAgentSession
from houdini_agent.ui_qml.controller import ChatModel, Controller


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


class HoudiniVersionDialog(QDialog):
    def __init__(self, installs, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Open Houdini")
        self.setModal(True)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.resize(520, 380)
        self.setMinimumSize(500, 360)
        self.selected = None
        self._installs = list(installs or [])
        self._drag_pos = None
        self.setStyleSheet("""
            QDialog {
                background: #0d0f12;
                color: #eef0f2;
                font-family: "Microsoft YaHei", "Segoe UI";
            }
            QLabel#eyebrow {
                color: #6aa8ff;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 1px;
            }
            QLabel#title {
                color: #f5f7fa;
                font-size: 24px;
                font-weight: 700;
            }
            QLabel#body {
                color: #aeb7c2;
                font-size: 13px;
                line-height: 1.45;
            }
            QLabel#chromeTitle {
                color: #7d8794;
                font-size: 12px;
                font-weight: 700;
            }
            QListWidget {
                background: #111419;
                color: #eef0f2;
                border: 1px solid #252b34;
                border-radius: 14px;
                padding: 8px;
                outline: none;
            }
            QListWidget::item {
                background: #171b22;
                color: #e7edf5;
                border: 1px solid #2b323d;
                border-radius: 12px;
                padding: 14px 16px;
                margin: 6px;
            }
            QListWidget::item:hover {
                background: #1c2531;
                border-color: #3b83f6;
            }
            QListWidget::item:selected {
                background: #12243b;
                border: 1px solid #46a0ff;
                color: #ffffff;
            }
            QPushButton {
                min-width: 112px;
                min-height: 36px;
                border-radius: 10px;
                padding: 0 18px;
                font-size: 13px;
                font-weight: 600;
                color: #d9e1ec;
                background: #171b22;
                border: 1px solid #303845;
            }
            QPushButton:hover {
                background: #202735;
                border-color: #4b5b70;
            }
            QPushButton:pressed {
                background: #121820;
            }
            QPushButton#primary {
                color: #07111d;
                background: #64b5ff;
                border: 1px solid #84c6ff;
            }
            QPushButton#primary:hover {
                background: #7bc0ff;
            }
            QPushButton:disabled {
                color: #66707c;
                background: #14171c;
                border-color: #242a32;
            }
            QPushButton#close {
                min-width: 28px;
                max-width: 28px;
                min-height: 28px;
                max-height: 28px;
                padding: 0;
                border-radius: 14px;
                color: #9ba6b4;
                background: transparent;
                border: 1px solid transparent;
                font-size: 18px;
                font-weight: 400;
            }
            QPushButton#close:hover {
                color: #ffffff;
                background: #2a1518;
                border-color: #6b2a31;
            }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 20)
        root.setSpacing(10)

        chrome = QHBoxLayout()
        chrome.setContentsMargins(0, 0, 0, 4)
        chrome_title = QLabel("HOUDINI AGENT")
        chrome_title.setObjectName("chromeTitle")
        close_btn = QPushButton("×")
        close_btn.setObjectName("close")
        close_btn.clicked.connect(self.reject)
        chrome.addWidget(chrome_title)
        chrome.addStretch(1)
        chrome.addWidget(close_btn)
        root.addLayout(chrome)

        eyebrow = QLabel("HOUDINI AGENT")
        eyebrow.setObjectName("eyebrow")
        title = QLabel("选择要连接的 Houdini")
        title.setObjectName("title")
        body = QLabel("完整的节点创建、场景读取和执行功能需要 Houdini Bridge。请选择一个 Houdini 20.5 或更高版本，Agent 会自动启动并连接。")
        body.setObjectName("body")
        body.setWordWrap(True)
        root.addWidget(eyebrow)
        root.addWidget(title)
        root.addWidget(body)

        self.list = QListWidget()
        self.list.setFrameShape(QListWidget.NoFrame)
        self.list.setSpacing(2)
        self.list.setFocusPolicy(Qt.NoFocus)
        for install in self._installs:
            item = QListWidgetItem("Houdini %s\n%s" % (install["version"], install["path"]))
            item.setData(Qt.UserRole, install)
            item.setSizeHint(QSize(0, 64))
            self.list.addItem(item)
        if self.list.count() > 0:
            self.list.setCurrentRow(0)
        else:
            item = QListWidgetItem("未检测到 Houdini 20.5+")
            item.setFlags(Qt.NoItemFlags)
            item.setSizeHint(QSize(0, 64))
            self.list.addItem(item)
        root.addWidget(self.list, 1)

        row = QHBoxLayout()
        row.setContentsMargins(0, 6, 0, 0)
        hint = QLabel("可以稍后从 Agent 再次连接 Houdini")
        hint.setObjectName("body")
        row.addWidget(hint)
        row.addStretch(1)
        self.cancel_btn = QPushButton("稍后再说")
        self.open_btn = QPushButton("打开 Houdini")
        self.open_btn.setObjectName("primary")
        self.open_btn.setEnabled(bool(self._installs))
        self.open_btn.setDefault(True)
        row.addWidget(self.cancel_btn)
        row.addWidget(self.open_btn)
        root.addLayout(row)
        self.cancel_btn.clicked.connect(self.reject)
        self.open_btn.clicked.connect(self._accept)

    def _accept(self):
        item = self.list.currentItem()
        self.selected = item.data(Qt.UserRole) if item else None
        if self.selected:
            self.accept()

    def _global_pos(self, event):
        try:
            return event.globalPosition().toPoint()
        except Exception:
            return event.globalPos()

    def mousePressEvent(self, event):
        try:
            y = event.position().y()
        except Exception:
            y = event.pos().y()
        if event.button() == Qt.LeftButton and y < 56:
            self._drag_pos = self._global_pos(event) - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            self.move(self._global_pos(event) - self._drag_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)


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
        dlg = HoudiniVersionDialog(self.installs, self.win)
        if dlg.exec_() == QDialog.Accepted and dlg.selected:
            try:
                launch_houdini(self.repo_root, dlg.selected)
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
