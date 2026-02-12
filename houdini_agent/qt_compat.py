# -*- coding: utf-8 -*-
"""
Qt 兼容层 — 统一 PySide6 / PySide2 导入

Houdini 20.5 及之前版本自带 PySide2，Houdini 21+ 自带 PySide6。
所有模块统一从此处导入 QtWidgets / QtCore / QtGui / QSettings，
无需在每个文件中写 try/except。

用法:
    from houdini_agent.qt_compat import QtWidgets, QtCore, QtGui, QSettings
"""

try:
    from PySide6 import QtWidgets, QtCore, QtGui          # noqa: F401
    from PySide6.QtCore import QSettings                   # noqa: F401
    PYSIDE_VERSION = 6
except ImportError:
    from PySide2 import QtWidgets, QtCore, QtGui          # noqa: F401
    from PySide2.QtCore import QSettings                   # noqa: F401
    PYSIDE_VERSION = 2


def invoke_on_main(receiver, slot_name: str, *args):
    """线程安全地在主线程调用 slot（兼容 PySide2 / PySide6）

    PySide6 支持 QMetaObject.invokeMethod + Q_ARG，
    PySide2 不支持 Q_ARG，改用 QTimer.singleShot(0, lambda)。

    Args:
        receiver: 目标 QObject（仅 PySide6 使用）
        slot_name: slot 方法名
        *args: 传递给 slot 的参数
    """
    if PYSIDE_VERSION == 6:
        q_args = [QtCore.Q_ARG(type(a), a) for a in args]
        QtCore.QMetaObject.invokeMethod(
            receiver, slot_name,
            QtCore.Qt.QueuedConnection,
            *q_args
        )
    else:
        # PySide2: 通过 QTimer.singleShot 排队到主线程
        method = getattr(receiver, slot_name)
        QtCore.QTimer.singleShot(0, lambda: method(*args))
