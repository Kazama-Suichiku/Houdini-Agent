from houdini_agent.qt_compat import QtWidgets, QtCore, QtGui
from .theme import CursorTheme
from .base import PulseIndicator
from ..i18n import tr


# ============================================================
# 节点上下文栏 (Houdini 专属)
# ============================================================

class NodeContextBar(QtWidgets.QFrame):
    """显示当前 Houdini 网络路径 / 选中节点"""

    refreshRequested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(22)
        self.setObjectName("NodeContextBar")

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 6, 0)
        lay.setSpacing(6)

        self.path_label = QtWidgets.QLabel("/obj")
        self.path_label.setObjectName("ctxPathLabel")
        lay.addWidget(self.path_label)

        self.sel_label = QtWidgets.QLabel("")
        self.sel_label.setObjectName("ctxSelLabel")
        self.sel_label.setVisible(False)
        lay.addWidget(self.sel_label)

        lay.addStretch()

        ref_btn = QtWidgets.QPushButton("R")
        ref_btn.setFixedSize(22, 22)
        ref_btn.setFlat(True)
        ref_btn.setCursor(QtCore.Qt.PointingHandCursor)
        ref_btn.setObjectName("ctxRefreshBtn")
        ref_btn.clicked.connect(self.refreshRequested.emit)
        lay.addWidget(ref_btn)

    def update_context(self, path: str = "", selected: list = None):
        self.path_label.setText(path if path else "/obj")
        if selected:
            names = [n.rsplit('/', 1)[-1] for n in selected[:3]]
            text = ', '.join(names)
            if len(selected) > 3:
                text += f" +{len(selected) - 3}"
            self.sel_label.setText(text)
            self.sel_label.setVisible(True)
        else:
            self.sel_label.setText("")
            self.sel_label.setVisible(False)


# ============================================================
# 工具执行状态栏
# ============================================================

class ToolStatusBar(QtWidgets.QFrame):
    """底部工具状态栏 — 显示当前正在执行的工具名 + 脉冲指示器"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(22)
        self.setObjectName("toolStatusBar")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(4)

        self._pulse = PulseIndicator(CursorTheme.ACCENT_BEIGE, 5, self)
        lay.addWidget(self._pulse)

        self._label = QtWidgets.QLabel("")
        self._label.setObjectName("toolStatusLabel")
        lay.addWidget(self._label)
        lay.addStretch()

        self.setVisible(False)

    def show_tool(self, tool_name: str):
        """显示正在执行的工具"""
        self._label.setText(f"⚡ {tool_name}")
        self._pulse.start()
        self.setVisible(True)

    def hide_tool(self):
        """隐藏工具状态"""
        self._pulse.stop()
        self.setVisible(False)
        self._label.setText("")


# ============================================================
# 统一状态指示栏（合并 ThinkingBar + ToolStatusBar）
# ============================================================

class UnifiedStatusBar(QtWidgets.QWidget):
    """统一状态指示栏 — 合并思考状态、生成状态和工具执行状态为一条指示条。

    提供四个接口：
        start()                 显示思考中 + 流光动画
        show_generating()       显示生成中 + 流光动画（API 迭代等待）
        show_tool(tool_name)    显示工具执行中 + 脉冲动画
        stop()                  隐藏状态栏
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(24)
        self.setObjectName("unifiedStatusBar")
        self.setVisible(False)

        self._mode = None  # 'thinking' | 'generating' | 'tool' | None
        self._elapsed = 0.0
        self._phase = 0.0

        # 流光定时器 ~25fps
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)

        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

    # ---- 公共 API ----

    def start(self):
        """启动思考模式（兼容旧 ThinkingBar.start）"""
        self._mode = 'thinking'
        self._elapsed = 0.0
        self._phase = 0.0
        self.setVisible(True)
        self._timer.start()
        self.update()

    def stop(self):
        """停止所有状态（兼容旧 ThinkingBar.stop）"""
        self._mode = None
        self._timer.stop()
        self.setVisible(False)

    def set_elapsed(self, seconds: float):
        """更新思考耗时（兼容旧 ThinkingBar.set_elapsed）"""
        self._elapsed = seconds
        self.update()

    def show_generating(self):
        """切换到生成模式 — API 请求等待中

        在工具执行完毕后、下一轮 LLM 响应开始前显示，
        填补"思考结束 → 下轮内容到达"之间的视觉空白期。
        """
        self._mode = 'generating'
        self._phase = 0.0
        self.setVisible(True)
        if not self._timer.isActive():
            self._timer.start()
        self.update()

    def show_planning(self, progress: str = ""):
        """切换到规划模式 — 显示 Plan 生成进度

        Args:
            progress: 进度文本，如 "step 3" 或空字符串
        """
        self._mode = 'planning'
        self._planning_progress = progress
        self.setVisible(True)
        if not self._timer.isActive():
            self._timer.start()
        self.update()

    def show_tool(self, tool_name: str):
        """切换到工具执行模式"""
        self._mode = 'tool'
        self._tool_name = tool_name
        self._phase = 0.0
        self.setVisible(True)
        if not self._timer.isActive():
            self._timer.start()
        self.update()

    def hide_tool(self):
        """隐藏工具状态 → 自动切换到 generating 模式（等待下轮 API 响应）"""
        if self._mode == 'tool':
            # 不完全隐藏，切换到 generating 模式以填补视觉空白
            self.show_generating()

    # ---- 内部 ----

    def _tick(self):
        self._phase += 0.025
        if self._phase > 1.0:
            self._phase -= 1.0
        self.update()

    def paintEvent(self, event):
        if self._mode == 'thinking':
            self._paint_thinking(event)
        elif self._mode == 'generating':
            self._paint_generating(event)
        elif self._mode == 'planning':
            self._paint_planning(event)
        elif self._mode == 'tool':
            self._paint_tool(event)

    def _paint_thinking(self, event):
        """绘制思考状态 — 流光文字"""
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        w, h = self.width(), self.height()
        text = f"Thinking {self._elapsed:.1f}s" if self._elapsed > 0 else "Thinking..."
        font = QtGui.QFont(CursorTheme.FONT_BODY, 10)
        p.setFont(font)
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(text)
        x = (w - tw) // 2
        y = (h + fm.ascent() - fm.descent()) // 2
        # 底色文字
        p.setPen(QtGui.QColor(100, 116, 139, 120))
        p.drawText(x, y, text)
        # 流光高亮（扫过效果）
        grad = QtGui.QLinearGradient(x, 0, x + tw, 0)
        pos = self._phase
        before = max(0.0, pos - 0.15)
        after = min(1.0, pos + 0.15)
        grad.setColorAt(0.0, QtGui.QColor(226, 232, 240, 0))
        if before > 0:
            grad.setColorAt(before, QtGui.QColor(226, 232, 240, 0))
        grad.setColorAt(pos, QtGui.QColor(226, 232, 240, 200))
        if after < 1.0:
            grad.setColorAt(after, QtGui.QColor(226, 232, 240, 0))
        grad.setColorAt(1.0, QtGui.QColor(226, 232, 240, 0))
        p.setPen(QtGui.QPen(QtGui.QBrush(grad), 0))
        p.drawText(x, y, text)
        p.end()

    def _paint_generating(self, event):
        """绘制生成状态 — 流光文字（与 thinking 相似但使用暖色调 + 不同文本）"""
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        w, h = self.width(), self.height()
        text = "Generating..."
        font = QtGui.QFont(CursorTheme.FONT_BODY, 10)
        p.setFont(font)
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(text)
        x = (w - tw) // 2
        y = (h + fm.ascent() - fm.descent()) // 2
        # 底色文字（暖灰色）
        p.setPen(QtGui.QColor(139, 116, 100, 120))
        p.drawText(x, y, text)
        # 流光高亮（暖白色扫过）
        grad = QtGui.QLinearGradient(x, 0, x + tw, 0)
        pos = self._phase
        before = max(0.0, pos - 0.15)
        after = min(1.0, pos + 0.15)
        grad.setColorAt(0.0, QtGui.QColor(240, 226, 210, 0))
        if before > 0:
            grad.setColorAt(before, QtGui.QColor(240, 226, 210, 0))
        grad.setColorAt(pos, QtGui.QColor(240, 232, 220, 200))
        if after < 1.0:
            grad.setColorAt(after, QtGui.QColor(240, 226, 210, 0))
        grad.setColorAt(1.0, QtGui.QColor(240, 226, 210, 0))
        p.setPen(QtGui.QPen(QtGui.QBrush(grad), 0))
        p.drawText(x, y, text)
        p.end()

    def _paint_planning(self, event):
        """绘制规划状态 — 紫色调流光 + 进度文本"""
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        w, h = self.width(), self.height()
        progress = getattr(self, '_planning_progress', '')
        text = f"Planning... {progress}" if progress else "Planning..."
        font = QtGui.QFont(CursorTheme.FONT_BODY, 10)
        p.setFont(font)
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(text)
        x = (w - tw) // 2
        y = (h + fm.ascent() - fm.descent()) // 2
        # 底色文字（紫灰色）
        p.setPen(QtGui.QColor(139, 120, 160, 120))
        p.drawText(x, y, text)
        # 流光高亮（紫白色扫过）
        grad = QtGui.QLinearGradient(x, 0, x + tw, 0)
        pos = self._phase
        before = max(0.0, pos - 0.15)
        after = min(1.0, pos + 0.15)
        grad.setColorAt(0.0, QtGui.QColor(200, 180, 240, 0))
        if before > 0:
            grad.setColorAt(before, QtGui.QColor(200, 180, 240, 0))
        grad.setColorAt(pos, QtGui.QColor(220, 200, 250, 220))
        if after < 1.0:
            grad.setColorAt(after, QtGui.QColor(200, 180, 240, 0))
        grad.setColorAt(1.0, QtGui.QColor(200, 180, 240, 0))
        p.setPen(QtGui.QPen(QtGui.QBrush(grad), 0))
        p.drawText(x, y, text)
        p.end()

    def _paint_tool(self, event):
        """绘制工具执行状态 — 流光文字（金色调，与 Thinking/Generating 统一风格）"""
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        w, h = self.width(), self.height()
        tool_name = getattr(self, '_tool_name', '')
        text = f"Exec: {tool_name}" if tool_name else "Executing..."
        font = QtGui.QFont(CursorTheme.FONT_BODY, 10)
        p.setFont(font)
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(text)
        x = (w - tw) // 2
        y = (h + fm.ascent() - fm.descent()) // 2
        # 底色文字（暗金色）
        p.setPen(QtGui.QColor(170, 145, 100, 120))
        p.drawText(x, y, text)
        # 流光高亮（金色扫过）
        grad = QtGui.QLinearGradient(x, 0, x + tw, 0)
        pos = self._phase
        before = max(0.0, pos - 0.15)
        after = min(1.0, pos + 0.15)
        grad.setColorAt(0.0, QtGui.QColor(212, 190, 140, 0))
        if before > 0:
            grad.setColorAt(before, QtGui.QColor(212, 190, 140, 0))
        grad.setColorAt(pos, QtGui.QColor(230, 210, 170, 220))
        if after < 1.0:
            grad.setColorAt(after, QtGui.QColor(212, 190, 140, 0))
        grad.setColorAt(1.0, QtGui.QColor(212, 190, 140, 0))
        p.setPen(QtGui.QPen(QtGui.QBrush(grad), 0))
        p.drawText(x, y, text)
        p.end()
