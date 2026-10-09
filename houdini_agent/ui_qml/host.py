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


try:
    from PySide6.QtCore import QTimer as _QTimer
    from PySide6.QtQml import QQmlIncubationController as _QQmlIncubationController
except ImportError:
    from PySide2.QtCore import QTimer as _QTimer
    from PySide2.QtQml import QQmlIncubationController as _QQmlIncubationController


class _FrameIncubator(_QQmlIncubationController):
    """有待创建的异步 QML 对象时，每 16ms 拿出 8ms 做增量创建，其余时间留给界面。"""

    def __init__(self):
        super().__init__()
        self._timer = _QTimer()
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def incubatingObjectCountChanged(self, count):
        if count > 0:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()

    def _tick(self):
        if self.incubatingObjectCount() > 0:
            self.incubateFor(8)
        else:
            self._timer.stop()


def create_view(parent=None, controller=None, model=None):
    """Build the QQuickWidget. Returns the widget (with ._controller/._model)."""
    register_fonts()
    if model is None:
        model = ChatModel()
    if controller is None:
        controller = Controller(model)

    view = QQuickWidget(parent)
    # QQuickWidget 的离屏窗口没有渲染循环，引擎上也就没有孵化控制器；没有它
    # Loader.asynchronous 会退化成同步创建。装一个分帧孵化器后，打开长会话时
    # 较早的消息才能在后续帧里分批创建，界面不被冻住。
    try:
        if view.engine().incubationController() is None:
            inc = _FrameIncubator()
            view.engine().setIncubationController(inc)
            view._incubator = inc   # 引擎不持有所有权，必须保活
    except Exception as e:
        print("[host] incubation controller setup failed:", e)
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
