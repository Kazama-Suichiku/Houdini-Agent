# -*- coding: utf-8 -*-
"""
QQuickWidget host — embeds the QML UI inside a QWidget so it can live in
Houdini's PySide widget tree (drop-in replacement for the old AITab widget).
"""

from pathlib import Path

try:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QFont
    from PySide6.QtQuickWidgets import QQuickWidget
except ImportError:  # Houdini <= 20.5 (Qt5)
    from PySide2.QtCore import QUrl
    from PySide2.QtGui import QFont
    from PySide2.QtQuickWidgets import QQuickWidget

from .controller import ChatModel, Controller

QML_DIR = Path(__file__).parent / "qml"
MAIN_QML = QML_DIR / "Main.qml"

_FONTS_REGISTERED = False


def register_fonts():
    """Load the bundled Editorial TTFs (fonts/) and set CJK + generic fallbacks."""
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    try:
        from PySide6.QtGui import QFontDatabase
    except ImportError:
        from PySide2.QtGui import QFontDatabase
    fonts_dir = Path(__file__).parent / "fonts"
    for ttf in ("Fraunces.ttf", "Newsreader.ttf", "SpaceMono-Regular.ttf"):
        p = fonts_dir / ttf
        if p.exists():
            try:
                QFontDatabase.addApplicationFont(str(p))
            except Exception as e:
                print("[host] font load failed:", ttf, e)
    # CJK + generic fallbacks (the bundled fonts are Latin-only)
    QFont.insertSubstitutions("Fraunces",   ["Georgia", "Microsoft YaHei", "Songti SC", "serif"])
    QFont.insertSubstitutions("Newsreader", ["Georgia", "Microsoft YaHei", "Songti SC", "serif"])
    QFont.insertSubstitutions("Space Mono", ["Consolas", "Courier New", "monospace"])
    _FONTS_REGISTERED = True


def create_view(parent=None, controller=None, model=None):
    """Build the QQuickWidget. Returns the widget (with ._controller/._model)."""
    register_fonts()
    if model is None:
        model = ChatModel()
    if controller is None:
        controller = Controller(model)

    view = QQuickWidget(parent)
    view.engine().addImportPath(str(QML_DIR))
    ctx = view.rootContext()
    ctx.setContextProperty("chatModel", model)
    ctx.setContextProperty("controller", controller)
    view.setResizeMode(QQuickWidget.SizeRootObjectToView)
    view.setSource(QUrl.fromLocalFile(str(MAIN_QML)))

    # keep python refs alive
    view._controller = controller
    view._model = model
    return view
