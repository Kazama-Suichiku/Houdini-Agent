# -*- coding: utf-8 -*-
"""QML Houdini version picker.

Drop-in replacement for the old QtWidgets HoudiniVersionDialog: renders the
Mono Editorial picker (qml/HAgent/HoudiniPicker.qml) inside a frameless modal
dialog and returns the chosen install dict (or None if cancelled).

    sel = pick_houdini(installs, parent)
"""

from pathlib import Path

try:
    from PySide6.QtCore import QObject, Signal, Slot, Property, QUrl, Qt
    from PySide6.QtWidgets import QDialog, QVBoxLayout
    from PySide6.QtQuickWidgets import QQuickWidget
except ImportError:  # Houdini <= 20.5 (Qt5)
    from PySide2.QtCore import QObject, Signal, Slot, Property, QUrl, Qt
    from PySide2.QtWidgets import QDialog, QVBoxLayout
    from PySide2.QtQuickWidgets import QQuickWidget

from . import host

QML_DIR = Path(__file__).parent / "qml"
PICKER_QML = QML_DIR / "HAgent" / "HoudiniPicker.qml"


class _PickerBridge(QObject):
    """Context object exposed to QML as `picker`."""

    accepted = Signal(int)
    cancelled = Signal()

    def __init__(self, installs, dialog):
        super().__init__()
        self._installs = list(installs or [])
        self._dialog = dialog

    @Property(list, constant=True)
    def installs(self):
        return self._installs

    @Slot(int)
    def choose(self, index):
        self.accepted.emit(index)

    @Slot()
    def cancel(self):
        self.cancelled.emit()

    @Slot()
    def beginMove(self):
        """Let the frameless dialog be dragged from the QML chrome bar."""
        try:
            wh = self._dialog.windowHandle()
            if wh is not None:
                wh.startSystemMove()
        except Exception:
            pass


def pick_houdini(installs, parent=None):
    """Show the picker modally; return the chosen install dict or None."""
    host.register_fonts()

    dlg = QDialog(parent)
    dlg.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
    dlg.setModal(True)
    dlg.resize(540, 460)
    dlg.setMinimumSize(500, 420)

    installs = list(installs or [])
    bridge = _PickerBridge(installs, dlg)
    chosen = {"sel": None}

    def _on_accept(index):
        if 0 <= index < len(installs):
            chosen["sel"] = installs[index]
            dlg.accept()

    bridge.accepted.connect(_on_accept)
    bridge.cancelled.connect(dlg.reject)

    view = QQuickWidget(dlg)
    view.engine().addImportPath(str(QML_DIR))
    view.rootContext().setContextProperty("picker", bridge)
    view.setResizeMode(QQuickWidget.SizeRootObjectToView)
    view.setSource(QUrl.fromLocalFile(str(PICKER_QML)))

    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(view)

    # keep python refs alive for the dialog lifetime
    dlg._picker_bridge = bridge
    dlg._picker_view = view

    try:
        dlg.exec_()
    except AttributeError:  # PySide6 renamed exec_ -> exec
        dlg.exec()
    return chosen["sel"]
