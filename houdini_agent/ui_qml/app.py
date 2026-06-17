# -*- coding: utf-8 -*-
"""
Houdini entry point for the QML UI.

`launcher.show_tool()` routes here (unless HAGENT_UI=legacy). Builds a
Houdini-parented window hosting the QML view. Re-launching closes the old
window and reloads the bridge modules + QML from disk (dev hot-reload).
"""

import os

# Always re-read QML from disk so edits show up on relaunch (dev hot-reload).
os.environ.setdefault("QML_DISABLE_DISK_CACHE", "1")

_GEO_ORG = ("HoudiniAI", "QML")
_AGENT_WIN_CLS = None


def _qt():
    try:
        from PySide6.QtWidgets import QApplication, QMainWindow
        from PySide6.QtCore import Qt
    except ImportError:  # Houdini <= 20.5
        from PySide2.QtWidgets import QApplication, QMainWindow
        from PySide2.QtCore import Qt
    return QApplication, QMainWindow, Qt


def _agent_window_class():
    """QMainWindow subclass that persists its geometry on move/resize/close."""
    global _AGENT_WIN_CLS
    if _AGENT_WIN_CLS is not None:
        return _AGENT_WIN_CLS
    try:
        from PySide6.QtWidgets import QMainWindow
        from PySide6.QtCore import QTimer, QSettings
    except ImportError:
        from PySide2.QtWidgets import QMainWindow
        from PySide2.QtCore import QTimer, QSettings

    class _AgentWindow(QMainWindow):
        def __init__(self, parent=None):
            super().__init__(parent)
            self._geo_timer = QTimer(self)
            self._geo_timer.setSingleShot(True)
            self._geo_timer.timeout.connect(self._save_geo)

        def _save_geo(self):
            try:
                if not self.isMinimized():
                    QSettings(*_GEO_ORG).setValue("geometry", self.saveGeometry())
            except Exception:
                pass

        def moveEvent(self, e):
            super().moveEvent(e)
            self._geo_timer.start(500)

        def resizeEvent(self, e):
            super().resizeEvent(e)
            self._geo_timer.start(500)

        def closeEvent(self, e):
            self._save_geo()
            super().closeEvent(e)

    _AGENT_WIN_CLS = _AgentWindow
    return _AGENT_WIN_CLS


def show_tool(mock=False):
    import importlib
    import sys as _sys
    # Dev hot-reload: refresh changed modules from disk on every relaunch so
    # editing code + re-running show_tool() takes effect WITHOUT restarting
    # Houdini. Order matters — reload leaf modules before the ones importing
    # them (meshy submodules -> meshy -> sessions -> controller).
    for _name in (
        "houdini_agent.meshy.config", "houdini_agent.meshy.client",
        "houdini_agent.meshy.schemas", "houdini_agent.meshy.network_ops",
        "houdini_agent.meshy.houdini_io", "houdini_agent.meshy",
        "houdini_agent.ui_qml.agent_session", "houdini_agent.ui_qml.bridge_session",
    ):
        _m = _sys.modules.get(_name)
        if _m is not None:
            try:
                importlib.reload(_m)
            except Exception as _e:
                print("[app] reload failed for %s: %s" % (_name, _e))

    from houdini_agent.ui_qml import controller as ctrl_mod
    from houdini_agent.ui_qml import host as host_mod
    from houdini_agent.ui_qml import preview as preview_mod
    importlib.reload(ctrl_mod)
    importlib.reload(host_mod)
    importlib.reload(preview_mod)

    QApplication, QMainWindow, Qt = _qt()
    try:
        from PySide6.QtCore import QSettings, QTimer
    except ImportError:
        from PySide2.QtCore import QSettings, QTimer
    app = QApplication.instance() or QApplication([])

    parent = None
    try:
        import hou
        parent = hou.qt.mainWindow()
    except Exception:
        pass

    # close the previous window (its closeEvent saves geometry first)
    prev = getattr(app, "_hagent_qml_window", None)
    if prev is not None:
        try:
            prev.close()
            prev.deleteLater()
        except Exception:
            pass
        app._hagent_qml_window = None

    model = ctrl_mod.ChatModel()
    controller = ctrl_mod.Controller(model, use_backend=True)
    try:
        controller.restore()
    except Exception:
        pass
    if mock and controller._session is None:
        try:
            model.load(ctrl_mod.wrap_ai_rows(preview_mod.mock_conversation()))
        except Exception:
            pass

    view = host_mod.create_view(controller=controller, model=model)

    Win = _agent_window_class()
    win = Win(parent)
    win.setWindowTitle("Houdini Agent")
    win.setWindowFlags(Qt.Window)
    win.setStyleSheet("QMainWindow { background-color: #0d0d0d; }")
    win.setCentralWidget(view)

    # restore window geometry
    geo = QSettings(*_GEO_ORG).value("geometry")
    restored = False
    if geo is not None:
        try:
            restored = bool(win.restoreGeometry(geo))
        except Exception:
            restored = False
    if not restored:
        win.resize(440, 820)

    # save sessions when the Houdini scene changes
    try:
        import hou
        ctrl = view._controller

        def _on_hip(event_type):
            try:
                if event_type in (hou.hipFileEventType.BeforeClear, hou.hipFileEventType.BeforeLoad):
                    ctrl._save_all()
            except Exception:
                pass
        hou.hipFile.addEventCallback(_on_hip)
    except Exception:
        pass

    win.show()
    win.raise_()
    win.activateWindow()

    # Meshy 资产库：依附主窗口、向左外扩的滑动抽屉。打开时把窗口整体加宽
    # _LIB_DW（向左展开，新增空间全给抽屉列，聊天区宽度不变），关闭时缩回。
    _LIB_DW = 360
    try:
        from PySide6.QtCore import QPropertyAnimation, QRect, QEasingCurve
    except ImportError:
        from PySide2.QtCore import QPropertyAnimation, QRect, QEasingCurve

    def _on_library_toggle():
        w = getattr(app, "_hagent_qml_window", None)
        if w is None:
            return
        opening = bool(controller.libraryOpen)
        grown = bool(getattr(w, "_lib_grown", False))
        if opening == grown:
            return
        geo = w.geometry()
        if opening:
            new_x = max(0, geo.x() - _LIB_DW)
            w._lib_dx = geo.x() - new_x   # 实际左移量（贴屏边时可能 < _LIB_DW）
            end = QRect(new_x, geo.y(), geo.width() + _LIB_DW, geo.height())
            w._lib_grown = True
        else:
            dx = int(getattr(w, "_lib_dx", _LIB_DW))
            end = QRect(geo.x() + dx, geo.y(),
                        max(320, geo.width() - _LIB_DW), geo.height())
            w._lib_grown = False
        anim = QPropertyAnimation(w, b"geometry")
        anim.setDuration(200)
        anim.setStartValue(geo)
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start()
        w._lib_anim = anim   # keep a ref so it isn't GC'd mid-animation

    try:
        controller.libraryOpenChanged.connect(_on_library_toggle)
    except Exception as e:
        print("[app] library drawer wiring failed:", e)

    QTimer.singleShot(4000, view._controller.silentUpdateCheck)

    app._hagent_qml_window = win
    return win
