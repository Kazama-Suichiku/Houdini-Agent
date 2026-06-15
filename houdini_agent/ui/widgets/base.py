import math
from typing import List
from houdini_agent.qt_compat import QtWidgets, QtCore, QtGui

from .theme import CursorTheme, _fmt_duration
from ..i18n import tr


# ============================================================
# 流光边框 — AI 响应活跃时在左侧显示流动渐变光带
# ============================================================

class AuroraBar(QtWidgets.QWidget):
    """流动渐变光带 — 放在 AIResponse 左侧，AI 回复期间持续流动。

    宽度仅 3px，银白单色系。通过在固定等距停靠点上采样
    一条虚拟循环色带（带相位偏移），保证停靠点始终递增，
    消除跳变伪影。停止后凝固为极淡银灰色。
    """

    _NUM_STOPS = 10  # 渐变采样点数量，越多越平滑

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(3)
        self._phase = 0.0
        self._active = False
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(30)  # ~33 fps
        self._timer.timeout.connect(self._tick)
        # 循环色带关键色（首尾相同 → 无缝衔接）
        self._key_colors = [
            QtGui.QColor(226, 232, 240, 200),  # 亮银白
            QtGui.QColor(100, 116, 139, 100),   # 暗银
            QtGui.QColor(226, 232, 240, 200),   # 亮银白（循环闭合）
        ]

    # -- public API --------------------------------------------------

    def start(self):
        """启动流光动画"""
        self._active = True
        self._phase = 0.0
        self.setFixedWidth(3)
        self.setVisible(True)
        self._timer.start()
        self.update()

    def stop(self):
        """停止流光动画，收缩为零宽度以保持卡片干净"""
        self._active = False
        self._timer.stop()
        self.setFixedWidth(0)
        self.update()

    @property
    def running(self) -> bool:
        return self._active

    # -- internal ----------------------------------------------------

    def _tick(self):
        self._phase += 0.006
        if self._phase >= 1.0:
            self._phase -= 1.0
        self.update()

    def _sample(self, t: float) -> QtGui.QColor:
        """在虚拟循环色带上采样，t ∈ [0, 1]，平滑插值。"""
        keys = self._key_colors
        n = len(keys) - 1  # 段数（首尾同色 → n 段覆盖一整圈）
        scaled = (t % 1.0) * n
        idx = int(scaled)
        frac = scaled - idx
        c1 = keys[idx]
        c2 = keys[min(idx + 1, n)]
        return QtGui.QColor(
            int(c1.red()   + (c2.red()   - c1.red())   * frac),
            int(c1.green() + (c2.green() - c1.green()) * frac),
            int(c1.blue()  + (c2.blue()  - c1.blue())  * frac),
            int(c1.alpha() + (c2.alpha() - c1.alpha()) * frac),
        )

    def paintEvent(self, event):  # noqa: N802
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = self.rect()
        if self._active:
            grad = QtGui.QLinearGradient(0, 0, 0, rect.height())
            for i in range(self._NUM_STOPS + 1):
                pos = i / self._NUM_STOPS          # 固定递增 0.0 → 1.0
                color = self._sample(pos + self._phase)  # 相位偏移
                grad.setColorAt(pos, color)
            p.fillRect(rect, grad)
        else:
            p.fillRect(rect, QtGui.QColor(148, 163, 184, 50))
        p.end()


# ============================================================
# 可折叠区块（通用）
# ============================================================

class CollapsibleSection(QtWidgets.QWidget):
    """可折叠区块 - 点击标题展开/收起"""

    def __init__(self, title: str, icon: str = "", collapsed: bool = True, parent=None):
        super().__init__(parent)
        self._collapsed = collapsed
        self._title = title
        self._icon = icon

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(0)

        # 标题栏（可点击）
        self.header = QtWidgets.QPushButton()
        self.header.setFlat(True)
        self.header.setCursor(QtCore.Qt.PointingHandCursor)
        self.header.clicked.connect(self.toggle)
        self._update_header()
        self.header.setObjectName("collapseHeader")
        layout.addWidget(self.header)

        # 内容区
        self.content_widget = QtWidgets.QWidget()
        self.content_layout = QtWidgets.QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(6, 4, 4, 4)
        self.content_layout.setSpacing(2)
        self.content_widget.setObjectName("collapseContent")
        layout.addWidget(self.content_widget)
        # ★ 必须在 addWidget 之后再 setVisible，否则无 parent 的 widget 会闪烁为独立窗口
        self.content_widget.setVisible(not collapsed)

    def _update_header(self):
        arrow = "▶" if self._collapsed else "▼"
        icon_part = f"{self._icon} " if self._icon else ""
        self.header.setText(f"{arrow} {icon_part}{self._title}")

    def toggle(self):
        self._collapsed = not self._collapsed
        self.content_widget.setVisible(not self._collapsed)
        self._update_header()

    def set_title(self, title: str):
        self._title = title
        self._update_header()

    def expand(self):
        if self._collapsed:
            self.toggle()

    def collapse(self):
        if not self._collapsed:
            self.toggle()

    def add_widget(self, widget: QtWidgets.QWidget):
        self.content_layout.addWidget(widget)

    def add_text(self, text: str, style: str = "normal"):
        label = QtWidgets.QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        label.setObjectName("collapseText")
        label.setProperty("textStyle", style)
        self.content_layout.addWidget(label)
        return label


class TurnTraceHeader(QtWidgets.QWidget):
    """One-line controller for a response's thinking/tool trace."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._collapsed = False
        self._elapsed = 0.0
        self._targets: List[QtWidgets.QWidget] = []
        self.setObjectName("turnTraceHeader")

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(6)

        self._button = QtWidgets.QPushButton()
        self._button.setFlat(True)
        self._button.setCursor(QtCore.Qt.PointingHandCursor)
        self._button.setObjectName("turnTraceToggle")
        self._button.clicked.connect(self.toggle)
        layout.addWidget(self._button)

        self._line = QtWidgets.QFrame()
        self._line.setFrameShape(QtWidgets.QFrame.HLine)
        self._line.setObjectName("turnTraceLine")
        layout.addWidget(self._line, 1)
        self.set_elapsed(0.0, active=True)

    def add_target(self, widget: QtWidgets.QWidget):
        if widget not in self._targets:
            self._targets.append(widget)
            widget.setVisible(not self._collapsed)

    def set_elapsed(self, seconds: float, active: bool = False):
        self._elapsed = max(0.0, float(seconds or 0.0))
        label = tr("status.processing") if active else tr("status.processed")
        arrow = "›" if self._collapsed else "⌄"
        self._button.setText(f"{label} {_fmt_duration(self._elapsed)} {arrow}")

    def toggle(self):
        self._collapsed = not self._collapsed
        for widget in list(self._targets):
            try:
                widget.setVisible(not self._collapsed)
            except RuntimeError:
                pass
        self.set_elapsed(self._elapsed, active=False)


# ============================================================
# 脉冲指示器
# ============================================================

class PulseIndicator(QtWidgets.QWidget):
    """小型脉冲圆点 — 通过 opacity 动画表示"正在进行"状态"""

    def __init__(self, color: str = CursorTheme.ACCENT_PURPLE, size: int = 8, parent=None):
        super().__init__(parent)
        self._color = QtGui.QColor(color)
        self._dot_size = size
        self._opacity = 1.0
        self.setFixedSize(size + 6, size + 6)

        self._anim = QtCore.QPropertyAnimation(self, b"pulseOpacity")
        self._anim.setDuration(1200)
        self._anim.setStartValue(0.25)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QtCore.QEasingCurve.InOutSine)
        self._anim.setLoopCount(-1)  # 无限循环

    # ---- Qt Property ----
    def _get_opacity(self):
        return self._opacity

    def _set_opacity(self, v):
        self._opacity = v
        self.update()

    pulseOpacity = QtCore.Property(float, _get_opacity, _set_opacity)

    def start(self):
        self._anim.start()

    def stop(self):
        self._anim.stop()
        self._opacity = 0.0
        self.update()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        c = QtGui.QColor(self._color)
        c.setAlphaF(self._opacity)
        p.setBrush(c)
        p.setPen(QtCore.Qt.NoPen)
        x = (self.width() - self._dot_size) / 2
        y = (self.height() - self._dot_size) / 2
        p.drawEllipse(QtCore.QRectF(x, y, self._dot_size, self._dot_size))
        p.end()


# ============================================================
# 可折叠内容块（兼容旧代码）
# ============================================================

class CollapsibleContent(QtWidgets.QWidget):
    """可折叠内容 - 点击标题展开/收起"""

    def __init__(self, title: str, content: str = "", parent=None):
        super().__init__(parent)
        self._collapsed = True
        self._title = title

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 1, 0, 1)
        layout.setSpacing(0)

        self.title_btn = QtWidgets.QPushButton(f"▶ {title}")
        self.title_btn.setFlat(True)
        self.title_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.title_btn.clicked.connect(self.toggle)
        self.title_btn.setObjectName("collapseContentTitle")
        layout.addWidget(self.title_btn)

        self.content_label = QtWidgets.QLabel(content)
        self.content_label.setWordWrap(True)
        self.content_label.setObjectName("collapseContentLabel")
        self.content_label.setVisible(False)
        layout.addWidget(self.content_label)

    def toggle(self):
        self._collapsed = not self._collapsed
        self.content_label.setVisible(not self._collapsed)
        arrow = "▶" if self._collapsed else "▼"
        self.title_btn.setText(f"{arrow} {self._title}")

    def set_content(self, content: str):
        self.content_label.setText(content)

    def expand(self):
        if self._collapsed:
            self.toggle()
