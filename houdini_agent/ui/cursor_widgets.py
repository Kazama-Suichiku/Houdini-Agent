# -*- coding: utf-8 -*-
"""
Cursor 风格 UI 组件 - 重构版
模仿 Cursor 侧边栏的简洁设计
每次对话形成完整块：思考 → 操作 → 总结
"""

from houdini_agent.qt_compat import QtWidgets, QtCore, QtGui
from datetime import datetime
from typing import Optional, List, Dict
import html
import math
import re
import time

from .i18n import tr


def _fmt_duration(seconds: float) -> str:
    """格式化时长: <60s -> '18s', >=60s -> '1m43s'"""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m{s % 60:02d}s"


# ============================================================
# 节点路径 → 可点击链接
# ============================================================

# 匹配 Houdini 节点路径: /obj/..., /out/..., /ch/..., /shop/..., /stage/..., /mat/..., /tasks/...
_NODE_PATH_RE = re.compile(
    r'(?<!["\w/])'                          # 不在引号、字母或 / 之后
    r'(/(?:obj|out|ch|shop|stage|mat|tasks)(?:/[\w.]+)+)'   # 路径本体
    r'(?!["\w/])'                           # 不在引号、字母或 / 之前
)

_NODE_LINK_STYLE = "color:#10b981;text-decoration:none;font-family:Consolas,Monaco,monospace;"


def _linkify_node_paths(text: str) -> str:
    """将文本中的 Houdini 节点路径转换为可点击的 <a> 标签
    
    使用 houdini:// 协议，点击后由 Qt 的 linkActivated 信号处理跳转。
    """
    return _NODE_PATH_RE.sub(
        lambda m: f'<a href="houdini://{m.group(1)}" style="{_NODE_LINK_STYLE}">{m.group(1)}</a>',
        text,
    )


def _linkify_node_paths_plain(text: str) -> str:
    """将纯文本中的节点路径转换为富文本 HTML（含可点击链接）
    
    先 html.escape 再 linkify，保证安全。
    """
    escaped = html.escape(text)
    return _linkify_node_paths(escaped).replace('\n', '<br>')


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
# 颜色主题 (深色主题)
# ============================================================

class CursorTheme:
    """Glassmorphism 深色主题 — 蓝紫底色 + 玻璃质感"""
    # 背景色（深邃蓝黑）
    BG_PRIMARY = "#0f1019"
    BG_SECONDARY = "#0c0e19"
    BG_TERTIARY = "#101224"
    BG_HOVER = "#1c1e36"
    
    # 边框色（玻璃边缘）
    BORDER = "rgba(255,255,255,12)"
    BORDER_FOCUS = "#3b82f6"
    
    # 文字色（更明亮）
    TEXT_PRIMARY = "#e2e8f0"
    TEXT_SECONDARY = "#94a3b8"
    TEXT_MUTED = "#64748b"
    TEXT_BRIGHT = "#ffffff"
    
    # 强调色（更鲜艳）
    ACCENT_BLUE = "#3b82f6"
    ACCENT_GREEN = "#10b981"
    ACCENT_ORANGE = "#f59e0b"
    ACCENT_RED = "#ef4444"
    ACCENT_PURPLE = "#a78bfa"
    ACCENT_YELLOW = "#fbbf24"
    ACCENT_BEIGE = "#d4a574"       # 暖色 — 工具调用/折叠区
    
    # 消息左边界
    BORDER_USER = "rgba(148,163,184,120)"   # 用户消息 — 柔和银灰
    BORDER_AI = "rgba(167,139,250,100)"     # AI 回复 — 淡紫光晕
    
    # 字体
    FONT_BODY = "'Microsoft YaHei', 'SimSun', 'Segoe UI', sans-serif"
    FONT_CODE = "'Consolas', 'Monaco', 'Courier New', monospace"


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
# 思考过程区块（无内置脉冲，动画移至输入框上方）
# ============================================================

class ThinkingSection(CollapsibleSection):
    """思考过程 - 显示 AI 的思考内容（支持多轮思考累计计时）
    
    脉冲/动画指示器已移至输入框上方的 ThinkingBar，此处仅做内容展示。
    ★ 使用 QPlainTextEdit(readOnly)，自带滚动条。
    高度计算采用与 ChatInput 相同的可靠方案：
      QTimer.singleShot(0) 延迟 + 逐块 block.layout().lineCount() 统计视觉行。
    """
    
    # 最大高度（像素），超过此值则固定高度，内置滚动条自动出现
    _MAX_HEIGHT_PX = 400
    
    def __init__(self, parent=None):
        # ★ 默认展开（用户要求不自动折叠）；section 整体初始 setVisible(False)，
        #   首次收到思考内容时 setVisible(True) 即可，内容区已处于展开状态。
        super().__init__(tr('thinking.init'), icon="", collapsed=False, parent=parent)
        # ★ 防止被父布局拉伸 —— 内容多大就多大
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Maximum,
        )
        self._thinking_text = ""
        self._start_time = time.time()
        self._accumulated_seconds = 0.0
        self._round_start = time.time()
        self._round_count = 0
        
        # ★ 思考内容 — QPlainTextEdit(readOnly)，自带滚动条
        self._text_font = QtGui.QFont(CursorTheme.FONT_BODY)
        self._text_font.setPixelSize(13)
        
        self.thinking_label = QtWidgets.QPlainTextEdit()
        self.thinking_label.setReadOnly(True)
        self.thinking_label.setFont(self._text_font)
        self.thinking_label.document().setDefaultFont(self._text_font)
        self.thinking_label.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.thinking_label.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.thinking_label.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.thinking_label.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
        self.thinking_label.setObjectName("thinkLabel")
        # 初始高度为一行（紧凑），流式输入时会动态增大
        self._line_h = QtGui.QFontMetrics(self._text_font).lineSpacing()
        self.thinking_label.setFixedHeight(self._line_h + 12)
        self.content_layout.addWidget(self.thinking_label)
        
        # 标题样式
        self.header.setObjectName("thinkHeader")
    
    def _update_height(self):
        """根据视觉行数（含自动换行）动态调整高度。
        
        与 ChatInput._adjust_height 相同的可靠方案：
        逐块遍历 block.layout().lineCount() 统计真实视觉行数。
        """
        doc = self.thinking_label.document()
        visual_lines = 0
        block = doc.begin()
        while block.isValid():
            bl = block.layout()
            if bl and bl.lineCount() > 0:
                visual_lines += bl.lineCount()
            else:
                visual_lines += 1
            block = block.next()
        visual_lines = max(1, visual_lines)
        
        desired = self._line_h * visual_lines + 12   # 12 = padding
        self.thinking_label.setFixedHeight(min(max(desired, self._line_h + 12), self._MAX_HEIGHT_PX))
    
    def _scroll_to_bottom(self):
        """滚动到底部"""
        vbar = self.thinking_label.verticalScrollBar()
        vbar.setValue(vbar.maximum())
    
    def _total_elapsed(self) -> float:
        if self._finalized:
            return self._accumulated_seconds
        return self._accumulated_seconds + (time.time() - self._round_start)
    
    def append_thinking(self, text: str):
        if '\ufffd' in text:
            text = text.replace('\ufffd', '')
        self._thinking_text += text
        self.thinking_label.setPlainText(self._thinking_text)
        # ★ 延迟到下一事件循环（确保 Qt 布局完成后再计算高度，和 ChatInput 同策略）
        QtCore.QTimer.singleShot(0, self._update_height)
        QtCore.QTimer.singleShot(0, self._scroll_to_bottom)
    
    def update_time(self):
        if self._finalized:
            return
        self.set_title(tr('thinking.progress', _fmt_duration(self._total_elapsed())))
    
    @property
    def _finalized(self):
        return getattr(self, '_is_finalized', False)
    
    def resume(self):
        self._is_finalized = False
        self._round_start = time.time()
        self._round_count += 1
        self._thinking_text += f"\n{tr('thinking.round', self._round_count + 1)}\n"
        self.thinking_label.setPlainText(self._thinking_text)
        QtCore.QTimer.singleShot(0, self._update_height)
        self.set_title(tr('thinking.progress', _fmt_duration(self._total_elapsed())))
        # ★ 始终确保展开
        self.expand()
    
    def finalize(self):
        if self._finalized:
            return
        self._is_finalized = True
        self._accumulated_seconds += (time.time() - self._round_start)
        total = self._accumulated_seconds
        self.set_title(tr('thinking.done', _fmt_duration(total)))
        # ★ 防御性展开：确保思考区块在任何情况下都保持展开
        self.expand()


# ============================================================
# 输入框上方 "思考中" 指示条（流光动画）
# ============================================================

class ThinkingBar(QtWidgets.QWidget):
    """显示在输入框上方的思考状态指示条。
    
    文字上有从左到右扫过的高亮流光效果，
    提示用户 AI 正在推理，替代原 ThinkingSection 内置的脉冲圆点。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(18)
        self.setVisible(False)

        self._elapsed = 0.0   # 秒
        self._phase = 0.0     # 流光相位 [0, 1]

        # 流光定时器 ~25fps
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)

        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

    def start(self):
        self._elapsed = 0.0
        self._phase = 0.0
        self.setVisible(True)
        self._timer.start()
        self.update()

    def stop(self):
        self._timer.stop()
        self.setVisible(False)

    def set_elapsed(self, seconds: float):
        self._elapsed = seconds
        self.update()

    def _tick(self):
        self._phase += 0.025
        if self._phase > 1.0:
            self._phase -= 1.0
        self.update()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setRenderHint(QtGui.QPainter.TextAntialiasing)

        s = int(self._elapsed)
        time_str = f"{s}s" if s < 60 else f"{s // 60}m{s % 60:02d}s"
        display = f"  ✦ {tr('thinking.progress', time_str)}"

        font = QtGui.QFont(CursorTheme.FONT_BODY, 9)
        p.setFont(font)
        fm = QtGui.QFontMetrics(font)
        y = (self.height() + fm.ascent() - fm.descent()) // 2

        x = 8
        for i, ch in enumerate(display):
            char_pos = i / max(len(display), 1)
            dist = abs(char_pos - self._phase)
            dist = min(dist, 1.0 - dist)
            glow = max(0.0, 1.0 - dist * 5.0)

            base = QtGui.QColor(CursorTheme.ACCENT_PURPLE)
            muted = QtGui.QColor(CursorTheme.TEXT_MUTED)
            r = int(muted.red()   + (base.red()   - muted.red())   * glow)
            g = int(muted.green() + (base.green() - muted.green()) * glow)
            b = int(muted.blue()  + (base.blue()  - muted.blue())  * glow)

            p.setPen(QtGui.QColor(r, g, b))
            p.drawText(x, y, ch)
            x += fm.horizontalAdvance(ch)

        p.end()


# ============================================================
# 确认模式 — 内联预览确认控件（替代弹窗）
# ============================================================

class VEXPreviewInline(QtWidgets.QFrame):
    """嵌入对话流中的工具执行预览卡片。
    
    用户点击 ✓ 确认 或 ✕ 取消后通过 confirmed / cancelled 信号通知。
    """

    confirmed = QtCore.Signal()
    cancelled = QtCore.Signal()

    def __init__(self, tool_name: str, args: dict, parent=None):
        super().__init__(parent)
        self._decided = False
        # ★ 卡片整体不允许被父布局拉伸 —— 内容多大就多大
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Maximum,
        )
        self.setObjectName("vexPreviewInline")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(3)

        # 标题行
        title = QtWidgets.QLabel(tr('confirm.title', tool_name))
        title.setObjectName("vexPreviewTitle")
        title.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        layout.addWidget(title)

        # ★ 紧凑参数摘要（只显示关键参数，每个一行，最多 6 行）
        summary_lines = []
        for k, v in args.items():
            sv = str(v)
            if len(sv) > 120:
                sv = sv[:117] + "..."
            summary_lines.append(f"  {k}: {sv}")
        if summary_lines:
            summary_text = "\n".join(summary_lines[:6])
            if len(summary_lines) > 6:
                summary_text += f"\n  {tr('confirm.params_more', len(summary_lines))}"
            summary_lbl = QtWidgets.QLabel(summary_text)
            summary_lbl.setWordWrap(True)
            summary_lbl.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            summary_lbl.setObjectName("vexInlineSummary")
            summary_lbl.setSizePolicy(
                QtWidgets.QSizePolicy.Expanding,
                QtWidgets.QSizePolicy.Maximum,
            )
            layout.addWidget(summary_lbl)

        # 按钮行（右对齐，紧凑）
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.addStretch()

        btn_cancel = QtWidgets.QPushButton(tr('confirm.cancel'))
        btn_cancel.setCursor(QtCore.Qt.PointingHandCursor)
        btn_cancel.setFixedHeight(24)
        btn_cancel.setObjectName("btnCancel")
        btn_cancel.clicked.connect(self._on_cancel)
        btn_row.addWidget(btn_cancel)

        btn_confirm = QtWidgets.QPushButton(tr('confirm.execute'))
        btn_confirm.setCursor(QtCore.Qt.PointingHandCursor)
        btn_confirm.setFixedHeight(24)
        btn_confirm.setObjectName("btnConfirmGreen")
        btn_confirm.clicked.connect(self._on_confirm)
        btn_row.addWidget(btn_confirm)

        layout.addLayout(btn_row)

    def _on_confirm(self):
        if self._decided:
            return
        self._decided = True
        # ★ 确认后直接隐藏整个卡片，不再显示"已确认执行"内嵌窗口
        self.setVisible(False)
        self.setFixedHeight(0)
        self.confirmed.emit()

    def _on_cancel(self):
        if self._decided:
            return
        self._decided = True
        # ★ 取消也直接隐藏整个卡片（和确认一致），不要内嵌窗口
        self.setVisible(False)
        self.setFixedHeight(0)
        self.cancelled.emit()

    def _show_decided(self, text: str, color: str):
        """决策后将整个卡片替换为简短状态"""
        layout = self.layout()
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
            sub = item.layout()
            if sub:
                while sub.count():
                    si = sub.takeAt(0)
                    sw = si.widget()
                    if sw:
                        sw.deleteLater()
        lbl = QtWidgets.QLabel(text)
        lbl.setObjectName("vexPreviewStatus")
        lbl.setProperty("state", "confirmed" if color == CursorTheme.ACCENT_GREEN else "cancelled")
        lbl.style().unpolish(lbl)
        lbl.style().polish(lbl)
        layout.addWidget(lbl)
        self.setFixedHeight(30)


# ============================================================
# 工具调用项
# ============================================================

class ToolCallItem(CollapsibleSection):
    """单个工具调用 — CollapsibleSection 风格（与 Result 折叠一致的灰色风格）
    
    标题栏：▶ tool_name            （执行中）
           ▶ tool_name (1.2s)      （完成）
    展开后显示完整 result 文本，节点路径可点击跳转。
    """

    nodePathClicked = QtCore.Signal(str)  # 节点路径被点击

    def __init__(self, tool_name: str, parent=None):
        super().__init__(tool_name, icon="", collapsed=True, parent=parent)
        self.tool_name = tool_name
        self._result = None
        self._success = None
        self._start_time = time.time()

        self.header.setObjectName("toolCallHeader")

        # 进度条（嵌入 content_layout 顶部，执行完毕后隐藏）
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setFixedHeight(2)
        self.progress_bar.setRange(0, 0)  # indeterminate
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setObjectName("toolProgress")
        self.content_layout.addWidget(self.progress_bar)

        self._result_label = None

    def set_result(self, result: str, success: bool = True):
        """设置工具执行结果"""
        self._result = result
        self._success = success
        elapsed = time.time() - self._start_time

        # 隐藏进度条
        self.progress_bar.setVisible(False)

        # 更新标题：只显示工具名 + 耗时，无图标
        self.set_title(f"{self.tool_name} ({elapsed:.1f}s)")

        # 失败时标题用白色（更亮），成功保持灰色
        if not success:
            self.header.setProperty("state", "failed")
            self.header.style().unpolish(self.header)
            self.header.style().polish(self.header)

        # 添加结果文本（灰色，失败时白色）—— 节点路径可点击
        if result.strip():
            rich_html = _linkify_node_paths_plain(result)
            self._result_label = QtWidgets.QLabel(rich_html)
            self._result_label.setWordWrap(True)
            self._result_label.setTextFormat(QtCore.Qt.RichText)
            self._result_label.setOpenExternalLinks(False)
            self._result_label.setTextInteractionFlags(
                QtCore.Qt.TextSelectableByMouse | QtCore.Qt.LinksAccessibleByMouse
            )
            self._result_label.linkActivated.connect(self._on_result_link)
            self._result_label.setObjectName("toolResultLabel")
            if not success:
                self._result_label.setProperty("state", "failed")
            self.content_layout.addWidget(self._result_label)

    def _on_result_link(self, url: str):
        """工具结果中的链接被点击"""
        if url.startswith('houdini://'):
            self.nodePathClicked.emit(url[len('houdini://'):])


# ============================================================
# 执行过程区块
# ============================================================

class ExecutionSection(CollapsibleSection):
    """执行过程 - 卡片式工具调用显示（默认折叠，用户手动展开）"""

    nodePathClicked = QtCore.Signal(str)  # 从子 ToolCallItem 冒泡上来

    def __init__(self, parent=None):
        super().__init__(tr('exec.running'), icon="", collapsed=True, parent=parent)
        self._tool_calls: List[ToolCallItem] = []
        self._start_time = time.time()
        
        # 更新标题样式
        self.header.setObjectName("execHeader")
    
    def add_tool_call(self, tool_name: str) -> ToolCallItem:
        """添加工具调用"""
        item = ToolCallItem(tool_name, self)
        item.nodePathClicked.connect(self.nodePathClicked.emit)
        self._tool_calls.append(item)
        self.content_layout.addWidget(item)
        self._update_title()
        return item
    
    def set_tool_result(self, tool_name: str, result: str, success: bool = True):
        """设置工具结果"""
        # 找到最后一个匹配的工具调用
        for item in reversed(self._tool_calls):
            if item.tool_name == tool_name and item._result is None:
                item.set_result(result, success)
                break
        self._update_title()
    
    def _update_title(self):
        """更新标题"""
        total = len(self._tool_calls)
        done = sum(1 for item in self._tool_calls if item._result is not None)
        if done < total:
            self.set_title(tr('exec.progress', done, total))
        else:
            elapsed = time.time() - self._start_time
            self.set_title(tr('exec.done', total, _fmt_duration(elapsed)))
    
    def finalize(self):
        """完成执行"""
        elapsed = time.time() - self._start_time
        total = len(self._tool_calls)
        
        # ⚠️ 兜底：强制关闭所有残留的进度条
        for item in self._tool_calls:
            if item._result is None:
                item.progress_bar.setVisible(False)
                item_elapsed = time.time() - item._start_time
                item.set_title(f"{item.tool_name} ({item_elapsed:.1f}s)")
                item._result = ""  # 标记已完成，避免被重复处理
                item._success = True
        
        success = sum(1 for item in self._tool_calls if item._success)
        failed = total - success
        
        if failed > 0:
            self.set_title(tr('exec.done_err', success, failed, _fmt_duration(elapsed)))
        else:
            self.set_title(tr('exec.done', total, _fmt_duration(elapsed)))


# ============================================================
# 图片预览弹窗（点击缩略图放大查看）
# ============================================================

class ImagePreviewDialog(QtWidgets.QDialog):
    """模态图片预览弹窗 — 点击缩略图后弹出，显示原尺寸/自适应窗口的大图"""

    def __init__(self, pixmap: QtGui.QPixmap, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr('img.preview'))
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowMaximizeButtonHint)
        self._pixmap = pixmap

        # 根据图片尺寸决定初始窗口大小（不超过屏幕 80%）
        screen = QtWidgets.QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            max_w, max_h = int(avail.width() * 0.8), int(avail.height() * 0.8)
        else:
            max_w, max_h = 1200, 800
        init_w = min(pixmap.width() + 40, max_w)
        init_h = min(pixmap.height() + 40, max_h)
        self.resize(init_w, init_h)

        # 深色背景
        self.setObjectName("imgPreviewDlg")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 可滚动区域
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setAlignment(QtCore.Qt.AlignCenter)
        scroll.setObjectName("chatScrollArea")

        self._img_label = QtWidgets.QLabel()
        self._img_label.setAlignment(QtCore.Qt.AlignCenter)
        scroll.setWidget(self._img_label)
        layout.addWidget(scroll)

        # 底栏：尺寸信息 + 关闭按钮
        bar = QtWidgets.QHBoxLayout()
        bar.setContentsMargins(12, 4, 12, 8)
        info = QtWidgets.QLabel(f"{pixmap.width()} × {pixmap.height()} px")
        info.setObjectName("imgInfoLabel")
        bar.addWidget(info)
        bar.addStretch()
        close_btn = QtWidgets.QPushButton(tr('btn.close'))
        close_btn.setObjectName("imgCloseBtn")
        close_btn.clicked.connect(self.close)
        bar.addWidget(close_btn)
        layout.addLayout(bar)

        self._update_preview()

    def _update_preview(self):
        """根据窗口大小缩放图片（保持比例）"""
        viewport_w = self.width() - 20
        viewport_h = self.height() - 50
        if self._pixmap.width() > viewport_w or self._pixmap.height() > viewport_h:
            scaled = self._pixmap.scaled(
                viewport_w, viewport_h,
                QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        else:
            scaled = self._pixmap
        self._img_label.setPixmap(scaled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_preview()

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Escape:
            self.close()
        super().keyPressEvent(event)


class ClickableImageLabel(QtWidgets.QLabel):
    """可点击的图片缩略图 — 点击后弹出 ImagePreviewDialog 放大查看"""

    def __init__(self, thumb_pixmap: QtGui.QPixmap, full_pixmap: QtGui.QPixmap, parent=None):
        super().__init__(parent)
        self._full_pixmap = full_pixmap
        self.setPixmap(thumb_pixmap)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip(tr('img.click_zoom'))

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            dlg = ImagePreviewDialog(self._full_pixmap, self.window())
            dlg.exec()
        else:
            super().mousePressEvent(event)


# ============================================================
# 用户消息
# ============================================================

class UserMessage(QtWidgets.QWidget):
    """用户消息 - 支持折叠（超过 2 行时自动折叠，点击展开/收起）"""

    _COLLAPSED_MAX_LINES = 2  # 折叠时显示的最大行数

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self._full_text = text
        self._collapsed = False  # 初始状态由 _maybe_collapse 决定

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 4)
        layout.setSpacing(0)

        # ---- 主容器（带左边框） ----
        self._container = QtWidgets.QWidget()
        self._container.setObjectName("userMsgContainer")
        container_layout = QtWidgets.QVBoxLayout(self._container)
        container_layout.setContentsMargins(12, 8, 12, 4)
        container_layout.setSpacing(2)

        # ---- 内容标签 ----
        self.content = QtWidgets.QLabel(text)
        self.content.setWordWrap(True)
        self.content.setTextFormat(QtCore.Qt.PlainText)
        self.content.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.content.setObjectName("userMsgText")
        container_layout.addWidget(self.content)

        # ---- 展开/收起 按钮 ----
        self._toggle_btn = QtWidgets.QPushButton()
        self._toggle_btn.setFlat(True)
        self._toggle_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._toggle_btn.setFixedHeight(20)
        self._toggle_btn.setObjectName("userMsgToggle")
        self._toggle_btn.clicked.connect(self._toggle_collapse)
        self._toggle_btn.setVisible(False)  # 默认隐藏，_maybe_collapse 决定
        container_layout.addWidget(self._toggle_btn)

        layout.addWidget(self._container)

        # 延迟判断是否需要折叠（等 QLabel 完成布局后再算行数）
        QtCore.QTimer.singleShot(0, self._maybe_collapse)

    # ------------------------------------------------------------------
    def _maybe_collapse(self):
        """检查文本是否超过阈值行数，超过则自动折叠"""
        line_count = self._full_text.count('\n') + 1
        if line_count > self._COLLAPSED_MAX_LINES:
            self._collapsed = True
            self._apply_collapsed()
            self._toggle_btn.setVisible(True)
        else:
            # 文字不够多，不需要折叠按钮
            self._toggle_btn.setVisible(False)

    def _apply_collapsed(self):
        """应用折叠状态：只显示前 N 行 + 省略号"""
        lines = self._full_text.split('\n')
        preview = '\n'.join(lines[:self._COLLAPSED_MAX_LINES])
        if len(lines) > self._COLLAPSED_MAX_LINES:
            preview += ' …'
        self.content.setText(preview)
        remaining = len(lines) - self._COLLAPSED_MAX_LINES
        self._toggle_btn.setText(tr('msg.expand', remaining))

    def _apply_expanded(self):
        """应用展开状态：显示完整文本"""
        self.content.setText(self._full_text)
        self._toggle_btn.setText(tr('msg.collapse'))

    def _toggle_collapse(self):
        self._collapsed = not self._collapsed
        if self._collapsed:
            self._apply_collapsed()
        else:
            self._apply_expanded()


# ============================================================
# AI 回复块（重构版）
# ============================================================

class AIResponse(QtWidgets.QWidget):
    """AI 回复 - Cursor 风格
    
    结构：
    +-- 思考过程（可折叠，默认折叠）
    +-- 执行过程（可折叠，默认折叠）
    +-- 总结（Markdown 渲染 + 代码块高亮）
    """
    
    createWrangleRequested = QtCore.Signal(str)  # vex_code
    nodePathClicked = QtCore.Signal(str)         # 节点路径被点击
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._start_time = time.time()
        self._content = ""
        self._has_thinking = False
        self._has_execution = False
        self._shell_count = 0  # Python Shell 执行计数
        
        # ★ 增量渲染状态
        self._frozen_segments: list = []    # 已冻结的富文本段落
        self._pending_text = ""             # 尚未冻结的尾部文本
        self._in_code_fence = False         # 是否在代码块内
        self._code_fence_lang = ""          # 代码块语言
        self._incremental_enabled = True    # 是否启用增量渲染
        
        # ★ 顶层水平布局：AuroraBar（左）+ 内容（右）
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 4, 0, 8)
        outer.setSpacing(0)
        
        # 流光边框（AI 响应活跃时流动）
        self.aurora_bar = AuroraBar(self)
        outer.addWidget(self.aurora_bar)
        
        # 内容列
        content_col = QtWidgets.QVBoxLayout()
        content_col.setContentsMargins(0, 0, 0, 0)
        content_col.setSpacing(4)
        outer.addLayout(content_col, 1)
        
        # 供外部引用（原来直接用 layout 的地方）
        layout = content_col
        
        # === 思考过程区块 ===
        self.thinking_section = ThinkingSection(self)
        self.thinking_section.setVisible(False)
        layout.addWidget(self.thinking_section)
        
        # === 执行过程区块 ===
        self.execution_section = ExecutionSection(self)
        self.execution_section.setVisible(False)
        self.execution_section.nodePathClicked.connect(self.nodePathClicked.emit)
        layout.addWidget(self.execution_section)
        
        # === Python Shell 区块（可折叠，默认折叠）===
        self.shell_section = CollapsibleSection("Python Shell", collapsed=True, parent=self)
        self.shell_section.setVisible(False)
        self.shell_section.header.setObjectName("shellHeaderPython")
        layout.addWidget(self.shell_section)
        
        # === System Shell 区块（可折叠，默认折叠）===
        self._sys_shell_count = 0
        self.sys_shell_section = CollapsibleSection("System Shell", collapsed=True, parent=self)
        self.sys_shell_section.setVisible(False)
        self.sys_shell_section.header.setObjectName("shellHeaderSystem")
        layout.addWidget(self.sys_shell_section)
        
        # === 总结/回复区域 ===
        self.summary_frame = QtWidgets.QFrame()
        self.summary_frame.setObjectName("aiSummary")
        self._summary_layout = QtWidgets.QVBoxLayout(self.summary_frame)
        self._summary_layout.setContentsMargins(8, 8, 6, 8)
        self._summary_layout.setSpacing(4)
        
        # 状态行（水平布局：状态文字 + 复制按钮）
        status_row = QtWidgets.QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(8)
        
        self.status_label = QtWidgets.QLabel(tr('thinking.init'))
        self.status_label.setObjectName("aiStatusLabel")
        status_row.addWidget(self.status_label)
        status_row.addStretch()
        
        # 复制全部按钮（完成后才显示）
        self._copy_btn = QtWidgets.QPushButton(tr('btn.copy'))
        self._copy_btn.setVisible(False)
        self._copy_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._copy_btn.setFixedHeight(22)
        self._copy_btn.setObjectName("aiCopyBtn")
        self._copy_btn.clicked.connect(self._copy_content)
        status_row.addWidget(self._copy_btn)
        
        self._summary_layout.addLayout(status_row)
        
        # ★ 已冻结段落容器 — 增量渲染时冻结的富文本/代码块放在这里
        self._frozen_container = QtWidgets.QWidget()
        self._frozen_layout = QtWidgets.QVBoxLayout(self._frozen_container)
        self._frozen_layout.setContentsMargins(0, 0, 0, 0)
        self._frozen_layout.setSpacing(0)  # 段落间距由 HTML margin 控制
        self._frozen_container.setVisible(False)
        self._summary_layout.addWidget(self._frozen_container)
        
        # 内容区域 —— 流式阶段使用 QPlainTextEdit（增量追加 O(1)），
        # finalize 时按需替换为 RichContentWidget（Markdown 渲染）。
        # ★ 关键：流式阶段的字体和间距必须与渲染后的 richText QLabel 一致，
        #   以避免 finalize 时产生"跳变"感。
        self.content_label = QtWidgets.QPlainTextEdit()
        self.content_label.setReadOnly(True)
        self.content_label.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.content_label.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.content_label.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.content_label.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
        # 让 size hint 跟随内容自动增长（不设固定高度）
        self.content_label.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum
        )
        self.content_label.setObjectName("aiContentLabel")
        # ★ 显式设置字体，确保流式和渲染后使用同一字体族和大小
        _stream_font = QtGui.QFont()
        _stream_font.setFamilies(['Microsoft YaHei', 'SimSun', 'Segoe UI'])
        _stream_font.setPixelSize(14)  # 与 {FS_MD}=14 一致
        self.content_label.setFont(_stream_font)
        self.content_label.document().setDefaultFont(_stream_font)
        # ★ 设置行间距为 1.6 倍，与 HTML 中的 line-height:1.6 保持一致
        self.content_label.document().setDocumentMargin(0)
        self._apply_line_spacing(160)  # 160% 行间距
        # 初始高度紧凑，流式输入时自动增长
        # 使用与 line-height 一致的行高计算
        fm = QtGui.QFontMetrics(_stream_font)
        self._content_line_h = int(fm.height() * 1.6)
        self.content_label.setFixedHeight(self._content_line_h + 4)
        self.content_label.document().contentsChanged.connect(self._auto_resize_content)
        self._summary_layout.addWidget(self.content_label)
        
        layout.addWidget(self.summary_frame)
        
        # === 详情区域（可折叠内容等）===
        self.details_layout = QtWidgets.QVBoxLayout()
        self.details_layout.setSpacing(2)
        layout.addLayout(self.details_layout)
    
    def add_thinking(self, text: str):
        """添加思考内容"""
        if not self._has_thinking:
            self._has_thinking = True
            self.thinking_section.setVisible(True)
            # 确保思考区块处于展开状态
            self.thinking_section.expand()
        self.thinking_section.append_thinking(text)
    
    def update_thinking_time(self):
        """更新思考时间（思考结束后不再更新状态标签）"""
        if self._has_thinking:
            if self.thinking_section._finalized:
                return  # 思考已结束，不再更新
            self.thinking_section.update_time()
            total = self.thinking_section._total_elapsed()
            self.status_label.setText(tr('thinking.progress', _fmt_duration(total)))
    
    def add_shell_widget(self, widget: 'PythonShellWidget'):
        """将 PythonShellWidget 添加到 Python Shell 折叠区块"""
        self._shell_count += 1
        if not self.shell_section.isVisible():
            self.shell_section.setVisible(True)
        self.shell_section.set_title(f"Python Shell ({self._shell_count})")
        self.shell_section.add_widget(widget)
    
    def add_sys_shell_widget(self, widget: 'SystemShellWidget'):
        """将 SystemShellWidget 添加到 System Shell 折叠区块"""
        self._sys_shell_count += 1
        if not self.sys_shell_section.isVisible():
            self.sys_shell_section.setVisible(True)
        self.sys_shell_section.set_title(f"System Shell ({self._sys_shell_count})")
        self.sys_shell_section.add_widget(widget)
    
    def add_status(self, text: str):
        """添加状态（处理工具调用）"""
        if text.startswith("[tool]"):
            tool_name = text[6:].strip()
            self._add_tool_call(tool_name)
        else:
            self.status_label.setText(text)
    
    def _add_tool_call(self, tool_name: str):
        """添加工具调用"""
        if not self._has_execution:
            self._has_execution = True
            self.execution_section.setVisible(True)
        self.execution_section.add_tool_call(tool_name)
        self.status_label.setText(tr('exec.tool', tool_name))
    
    def add_tool_result(self, tool_name: str, result: str):
        """添加工具结果"""
        success = not result.startswith("[err]") and not result.startswith("错误") and not result.startswith("Error")
        clean_result = result.removeprefix("[ok] ").removeprefix("[err] ")
        self.execution_section.set_tool_result(tool_name, clean_result, success)
    
    def _apply_line_spacing(self, percent: int = 160):
        """为 QPlainTextEdit 设置 proportional 行间距。
        
        Qt 的 QPlainTextEdit 不直接支持 CSS line-height，
        需要通过 QTextBlockFormat.setLineHeight 来实现。
        percent: 160 = 1.6 倍行间距。
        """
        doc = self.content_label.document()
        cursor = QtGui.QTextCursor(doc)
        cursor.select(QtGui.QTextCursor.Document)
        fmt = QtGui.QTextBlockFormat()
        fmt.setLineHeight(percent, 1)  # 1 = ProportionalHeight
        cursor.mergeBlockFormat(fmt)

    def _auto_resize_content(self):
        """根据 document 的实际渲染高度动态调整 QPlainTextEdit 的高度。
        
        使用 doc.size().height() 获取已布局的真实像素高度，
        加上一个小的底部边距作为最终高度。
        """
        doc = self.content_label.document()
        # 确保布局信息是最新的
        doc.adjustSize()
        doc_height = int(doc.size().height())
        target = doc_height + 4  # 底部留 4px 余量
        min_h = self._content_line_h + 4
        target = max(target, min_h)
        current_h = self.content_label.height()
        if abs(target - current_h) > 1:
            self.content_label.setFixedHeight(target)
    
    def append_content(self, text: str):
        """追加内容（流式场景高频调用，需要高效）
        
        ★ 增量渲染策略（借鉴 markstream-vue）：
        1. 文本追加到 _pending_text
        2. 检查是否有已完成的段落（双换行分隔 / 代码块闭合）
        3. 已完成段落冻结为 RichText Widget，不再变动
        4. 不完整的尾部保留在 QPlainTextEdit 中继续接收 delta
        """
        # ★ 修复：不丢弃包含换行符的 chunk
        # 纯换行符（\n\n）是 Markdown 段落分隔的关键信号，
        # 丢弃它们会导致多段内容粘连在一起
        if not text.strip() and '\n' not in text:
            return
        # 清除 U+FFFD 替换符（encoding 异常残留）
        if '\ufffd' in text:
            text = text.replace('\ufffd', '')
        self._content += text
        self._pending_text += text

        # 尝试冻结已完成的段落
        if self._incremental_enabled:
            self._try_freeze_completed()

        # 更新活跃区域显示（只显示未冻结的文本）
        self.content_label.setPlainText(self._pending_text)
        # setPlainText 会重置 block format，需要重新应用行间距
        self._apply_line_spacing(160)
        # 光标移到末尾
        cursor = self.content_label.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        self.content_label.setTextCursor(cursor)

    def _try_freeze_completed(self):
        """检测并冻结已完成的段落
        
        检测规则：
        - 代码块: ``` 开启 → ``` 关闭，闭合后整个代码块冻结
        - 文本段落: 两个连续换行 (\n\n) 分隔的文本段落冻结
        """
        text = self._pending_text
        if not text:
            return

        # 按行扫描，寻找可冻结的边界
        lines = text.split('\n')
        freeze_up_to = -1  # 冻结到第几行（不含）
        i = 0
        in_fence = self._in_code_fence

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if in_fence:
                # 在代码块内，检查关闭围栏
                if stripped.startswith('```'):
                    in_fence = False
                    # 代码块结束 → 可冻结到此行（含）
                    freeze_up_to = i + 1
                i += 1
                continue

            # 检查代码块开启
            if stripped.startswith('```'):
                # 如果之前有未冻结的文本段落，先冻结它们
                if i > 0 and freeze_up_to < i:
                    # 检查代码块之前是否有空行分隔
                    pass
                in_fence = True
                self._code_fence_lang = stripped[3:].strip()
                i += 1
                continue

            # 检查双换行分隔（空行）
            if not stripped:
                # 空行 → 如果之前有内容，可以冻结到上一个非空行
                if i > 0 and freeze_up_to < i:
                    # 找到一个段落边界
                    # 但只有在空行之前有实质内容时才冻结
                    has_content_before = any(lines[j].strip() for j in range(max(0, freeze_up_to + 1 if freeze_up_to >= 0 else 0), i))
                    if has_content_before:
                        freeze_up_to = i  # 冻结到空行（含空行）
            i += 1

        self._in_code_fence = in_fence

        # 执行冻结
        if freeze_up_to > 0 and not in_fence:
            frozen_text = '\n'.join(lines[:freeze_up_to])
            remaining_text = '\n'.join(lines[freeze_up_to:])

            if frozen_text.strip():
                self._freeze_text(frozen_text)

            self._pending_text = remaining_text

    def _freeze_text(self, text: str):
        """将一段文本冻结为富文本 Widget"""
        # 使用 SimpleMarkdown 解析
        segments = SimpleMarkdown.parse_segments(text)

        for seg in segments:
            if seg[0] == 'text':
                lbl = QtWidgets.QLabel()
                lbl.setWordWrap(True)
                lbl.setTextFormat(QtCore.Qt.RichText)
                lbl.setOpenExternalLinks(False)
                lbl.setTextInteractionFlags(
                    QtCore.Qt.TextSelectableByMouse
                    | QtCore.Qt.LinksAccessibleByMouse
                )
                lbl.setText(seg[1])
                lbl.setObjectName("richText")
                lbl.linkActivated.connect(self._on_link_activated)
                self._frozen_layout.addWidget(lbl)
            elif seg[0] == 'code':
                cb = CodeBlockWidget(seg[2], seg[1], self)
                cb.createWrangleRequested.connect(self.createWrangleRequested.emit)
                # 代码块与前后段落之间需要额外间距
                cb.setContentsMargins(0, 6, 0, 6)
                self._frozen_layout.addWidget(cb)
            elif seg[0] == 'image':
                img_lbl = QtWidgets.QLabel()
                img_lbl.setObjectName("richImage")
                img_lbl.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
                img_lbl.setText(
                    f'<div style="margin:4px 0;">'
                    f'<img src="{html.escape(seg[1])}" '
                    f'style="max-width:100%;max-height:300px;border-radius:6px;">'
                    f'</div>'
                )
                img_lbl.setTextFormat(QtCore.Qt.RichText)
                self._frozen_layout.addWidget(img_lbl)

        # 显示冻结容器
        if not self._frozen_container.isVisible():
            self._frozen_container.setVisible(True)
        self._frozen_segments.append(text)
    
    def set_content(self, text: str):
        """设置内容（一次性，非流式场景，如历史恢复）
        
        ★ 直接渲染为富文本，避免历史恢复时也出现跳变。
        """
        self._content = text
        self._pending_text = ""
        self._incremental_enabled = False
        
        content = self._clean_content(text)
        if not content:
            self.content_label.setPlainText("")
            return
        
        # 直接渲染为富文本 Widget，保持一致的外观
        self.content_label.setVisible(False)
        self._freeze_text(content)
    
    @staticmethod
    def _clean_content(text: str) -> str:
        """清理内容中的多余空白（仅在 finalize 时调用一次）"""
        if not text:
            return ""
        import re
        cleaned = re.sub(r'\n{3,}', '\n\n', text)
        return cleaned.strip()
    
    def add_collapsible(self, title: str, content: str) -> CollapsibleSection:
        """添加可折叠内容"""
        section = CollapsibleSection(title, collapsed=True, parent=self)
        section.add_text(content, "muted")
        self.details_layout.addWidget(section)
        return section
    
    def _copy_content(self):
        """复制完整正式回复内容到剪贴板"""
        content = self._clean_content(self._content)
        if content:
            QtWidgets.QApplication.clipboard().setText(content)
            # 临时反馈
            self._copy_btn.setText(tr('btn.copied'))
            self._copy_btn.setProperty("state", "copied")
            self._copy_btn.style().unpolish(self._copy_btn)
            self._copy_btn.style().polish(self._copy_btn)
            QtCore.QTimer.singleShot(1500, self._reset_copy_btn)
    
    def _reset_copy_btn(self):
        """恢复复制按钮样式"""
        try:
            self._copy_btn.setText(tr('btn.copy'))
            self._copy_btn.setProperty("state", "")
            self._copy_btn.style().unpolish(self._copy_btn)
            self._copy_btn.style().polish(self._copy_btn)
        except RuntimeError:
            pass  # widget 已销毁
    
    def start_aurora(self):
        """启动左侧流光边框动画"""
        self.aurora_bar.start()

    def stop_aurora(self):
        """停止左侧流光边框动画"""
        self.aurora_bar.stop()

    def finalize(self):
        """完成回复 - 提取最终总结
        
        ★ 增量渲染模式下，大部分段落已经冻结为 Widget，
        finalize 只需处理最后的 _pending_text 尾部残留。
        """
        # ★ 停止流光边框
        self.aurora_bar.stop()
        
        elapsed = time.time() - self._start_time
        
        # 完成思考区块
        if self._has_thinking:
            self.thinking_section.finalize()
        
        # 完成执行区块
        if self._has_execution:
            self.execution_section.finalize()
        
        # 更新状态
        parts = []
        if self._has_thinking:
            parts.append(tr('status.thinking'))
        if self._has_execution:
            tool_count = len(self.execution_section._tool_calls)
            parts.append(tr('status.calls', tool_count))
        
        status_text = tr('status.done', _fmt_duration(elapsed))
        if parts:
            status_text += f" | {', '.join(parts)}"
        
        self.status_label.setText(status_text)
        
        # 有内容时显示复制按钮
        if self._clean_content(self._content):
            self._copy_btn.setVisible(True)
        
        # ★ 增量渲染 finalize: 处理最后残余的 pending_text
        content = self._clean_content(self._content)
        
        if not content:
            if self._has_execution:
                self.content_label.setPlainText(tr('status.exec_done_see_above'))
            else:
                self.content_label.setPlainText(tr('status.no_reply'))
            self.content_label.setProperty("state", "empty")
            self.content_label.style().unpolish(self.content_label)
            self.content_label.style().polish(self.content_label)
        elif self._frozen_segments:
            # 增量模式：已有冻结段落，只需处理 pending 尾部
            remaining = self._clean_content(self._pending_text)
            if remaining:
                # ★ 始终将残余文本冻结为富文本，避免 finalize 时的跳变
                self._freeze_text(remaining)
                self.content_label.setVisible(False)
            else:
                # 没有残余文本，隐藏 QPlainTextEdit
                self.content_label.setVisible(False)
        else:
            # 传统模式（无冻结段落）—— 始终渲染为富文本以保持一致性
            self.content_label.setVisible(False)
            self._freeze_text(content)
    
    def _on_link_activated(self, url: str):
        """处理链接点击 — houdini:// 协议 → nodePathClicked 信号"""
        if url.startswith('houdini://'):
            node_path = url[len('houdini://'):]
            self.nodePathClicked.emit(node_path)


# ============================================================
# 简洁状态行
# ============================================================

class StatusLine(QtWidgets.QLabel):
    """简洁状态行"""
    
    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setObjectName("statusLine")
        self.setWordWrap(True)


# ============================================================
# 节点操作标签
# ============================================================

class NodeOperationLabel(QtWidgets.QWidget):
    """节点操作标签 - 显示 +1 node / -2 nodes，带 undo/keep 按钮"""
    
    nodeClicked = QtCore.Signal(str)      # 发送节点路径（点击节点名跳转）
    undoRequested = QtCore.Signal()       # 请求撤销此操作
    decided = QtCore.Signal()             # undo 或 keep 完成后通知（用于更新批量操作栏）
    
    # _BTN_STYLE removed — use objectName-based QSS instead
    
    def __init__(self, operation: str, count: int, node_paths: list = None, 
                 detail_text: str = None, param_diff: dict = None, parent=None):
        """
        Args:
            operation: 'create' | 'delete' | 'modify'
            count: 操作的节点/参数数量
            node_paths: 节点路径列表
            detail_text: 简单文本详情 (旧方式, 纯文字)
            param_diff: 参数 diff 信息 {"param_name": str, "old_value": Any, "new_value": Any}
        """
        super().__init__(parent)
        self._node_paths = node_paths or []
        self._decided = False  # 用户是否已做出选择
        
        # 如果有 param_diff，使用垂直布局（标题行 + diff 区域）
        # 否则使用原来的水平布局
        if param_diff and operation == 'modify':
            self._init_modify_layout(operation, count, param_diff)
            return
        
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(4)
        
        if operation == 'create':
            prefix = "+"
            color = CursorTheme.ACCENT_GREEN
        elif operation == 'modify':
            prefix = "~"
            color = CursorTheme.ACCENT_YELLOW
        else:
            prefix = "-"
            color = CursorTheme.ACCENT_RED
        
        if operation == 'modify':
            plural = "params" if count > 1 else "param"
        else:
            plural = "nodes" if count > 1 else "node"
        count_text = f"{prefix}{count} {plural}"
        
        count_label = QtWidgets.QLabel(count_text)
        count_label.setObjectName("nodeOpCount")
        count_label.setProperty("op", operation)
        count_label.style().unpolish(count_label)
        count_label.style().polish(count_label)
        layout.addWidget(count_label)
        
        # 每个节点名作为可点击按钮
        display_paths = self._node_paths[:5]
        for path in display_paths:
            short_name = path.rsplit('/', 1)[-1] if '/' in path else path
            btn = QtWidgets.QPushButton(short_name)
            btn.setFlat(True)
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            btn.setToolTip(tr('node.click_jump', path))
            btn.setObjectName("nodePathBtn")
            btn.clicked.connect(lambda checked=False, p=path: self.nodeClicked.emit(p))
            layout.addWidget(btn)
        
        if len(self._node_paths) > 5:
            more = QtWidgets.QLabel(f"+{len(self._node_paths) - 5} more")
            more.setObjectName("nodeOpMore")
            layout.addWidget(more)
        
        # 简单文本详情（仅在没有 param_diff 时使用）
        if detail_text:
            detail_label = QtWidgets.QLabel(detail_text)
            detail_label.setObjectName("nodeOpDetail")
            detail_label.setToolTip(detail_text)
            layout.addWidget(detail_label)
        
        layout.addStretch()
        
        # ── Undo / Keep 按钮 ──
        self._undo_btn = QtWidgets.QPushButton(tr('btn.undo'))
        self._undo_btn.setFixedHeight(20)
        self._undo_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._undo_btn.setObjectName("btnUndoOp")
        self._undo_btn.clicked.connect(self._on_undo)
        layout.addWidget(self._undo_btn)
        
        self._keep_btn = QtWidgets.QPushButton(tr('btn.keep'))
        self._keep_btn.setFixedHeight(20)
        self._keep_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._keep_btn.setObjectName("btnKeepOp")
        self._keep_btn.clicked.connect(self._on_keep)
        layout.addWidget(self._keep_btn)
        
        # 决定后的状态标签（替代按钮）
        self._status_label = QtWidgets.QLabel()
        self._status_label.setObjectName("nodeOpStatus")
        self._status_label.setVisible(False)
        layout.addWidget(self._status_label)
    
    def _init_modify_layout(self, operation: str, count: int, param_diff: dict):
        """modify 操作的专用布局：标题行(黄标签+节点名+undo/keep) + diff 展示区"""
        self._decided = False
        
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 2, 0, 2)
        root.setSpacing(2)
        
        # ── 第一行：标签 + 节点名 + undo/keep ──
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(4)
        
        color = CursorTheme.ACCENT_YELLOW
        plural = "params" if count > 1 else "param"
        count_label = QtWidgets.QLabel(f"~{count} {plural}")
        count_label.setObjectName("nodeOpCount")
        count_label.setProperty("op", "modify")
        count_label.style().unpolish(count_label)
        count_label.style().polish(count_label)
        header.addWidget(count_label)
        
        for path in self._node_paths[:3]:
            short_name = path.rsplit('/', 1)[-1] if '/' in path else path
            btn = QtWidgets.QPushButton(short_name)
            btn.setFlat(True)
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            btn.setToolTip(tr('node.click_jump', path))
            btn.setObjectName("nodePathBtn")
            btn.clicked.connect(lambda checked=False, p=path: self.nodeClicked.emit(p))
            header.addWidget(btn)
        
        header.addStretch()
        
        self._undo_btn = QtWidgets.QPushButton(tr('btn.undo'))
        self._undo_btn.setFixedHeight(20)
        self._undo_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._undo_btn.setObjectName("btnUndoOp")
        self._undo_btn.clicked.connect(self._on_undo)
        header.addWidget(self._undo_btn)
        
        self._keep_btn = QtWidgets.QPushButton(tr('btn.keep'))
        self._keep_btn.setFixedHeight(20)
        self._keep_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._keep_btn.setObjectName("btnKeepOp")
        self._keep_btn.clicked.connect(self._on_keep)
        header.addWidget(self._keep_btn)
        
        self._status_label = QtWidgets.QLabel()
        self._status_label.setObjectName("nodeOpStatus")
        self._status_label.setVisible(False)
        header.addWidget(self._status_label)
        
        root.addLayout(header)
        
        # ── 第二行：Diff 展示 ──
        self._diff_widget = ParamDiffWidget(
            param_name=param_diff.get("param_name", ""),
            old_value=param_diff.get("old_value", ""),
            new_value=param_diff.get("new_value", ""),
        )
        root.addWidget(self._diff_widget)
    
    def collapse_diff(self):
        """折叠 diff 展示区（Keep All 时调用）"""
        if hasattr(self, '_diff_widget') and self._diff_widget:
            self._diff_widget.collapse()
    
    def _on_undo(self):
        if self._decided:
            return
        self._decided = True
        self._undo_btn.setVisible(False)
        self._keep_btn.setVisible(False)
        self._status_label.setText(tr('status.undone'))
        self._status_label.setProperty("state", "undone")
        self._status_label.style().unpolish(self._status_label)
        self._status_label.style().polish(self._status_label)
        self._status_label.setVisible(True)
        self.undoRequested.emit()
        self.decided.emit()
    
    def _on_keep(self):
        if self._decided:
            return
        self._decided = True
        self._undo_btn.setVisible(False)
        self._keep_btn.setVisible(False)
        self._status_label.setText(tr('status.kept'))
        self._status_label.setVisible(True)
        self.decided.emit()


# ============================================================
# 流式代码预览组件（Streaming VEX Apply）
# ============================================================

class StreamingCodePreview(QtWidgets.QWidget):
    """流式代码预览 — 像 Cursor Apply 一样逐行显示 AI 正在写的代码
    
    在 tool_call 参数流式到达时，实时显示 VEX 代码的书写过程。
    工具执行完毕后，由 ai_tab 将其替换为正式的 ParamDiffWidget。
    """

    def __init__(self, tool_name: str, parent=None):
        super().__init__(parent)
        self.setObjectName("streamingCodePreview")
        self._tool_name = tool_name

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(0)

        # 标题行
        self._title = QtWidgets.QLabel("✍ Writing code...")
        self._title.setObjectName("streamingCodeTitle")
        layout.addWidget(self._title)

        # 代码显示区（只读，固定最大高度，自动滚动）
        self._code_area = QtWidgets.QPlainTextEdit()
        self._code_area.setReadOnly(True)
        self._code_area.setObjectName("streamingCodeArea")
        self._code_area.setMaximumHeight(200)
        self._code_area.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        layout.addWidget(self._code_area)

        # 记录上次已显示的代码长度，只追加增量
        self._last_len = 0

    def update_code(self, full_code: str):
        """用完整代码字符串更新显示（增量追加新部分）"""
        if len(full_code) > self._last_len:
            delta = full_code[self._last_len:]
            self._last_len = len(full_code)
            self._code_area.moveCursor(QtGui.QTextCursor.End)
            self._code_area.insertPlainText(delta)
            # 自动滚动到底部
            sb = self._code_area.verticalScrollBar()
            sb.setValue(sb.maximum())

    def finalize(self):
        """流式结束，更新标题"""
        self._title.setText("✓ Code complete")
        self._title.setProperty("state", "done")
        self._title.style().unpolish(self._title)
        self._title.style().polish(self._title)


# ============================================================
# 参数 Diff 展示组件
# ============================================================

class ParamDiffWidget(QtWidgets.QWidget):
    """参数变更 Diff 展示 — 旧值红框 / 新值绿框
    
    - 标量/短文本: 内联显示  [old_value] → [new_value]
    - 多行文本(VEX等): 展开式 diff, 红色背景删除行, 绿色背景新增行
    """
    
    # diff 颜色
    _RED_BG = "#3d1f1f"       # 删除行背景
    _RED_BORDER = "#6e3030"   # 删除行边框
    _RED_TEXT = "#f48771"     # 删除行文字
    _GREEN_BG = "#1f3d1f"     # 新增行背景
    _GREEN_BORDER = "#2e6e30" # 新增行边框
    _GREEN_TEXT = "#89d185"   # 新增行文字
    _GREY_TEXT = "#64748b"    # 上下文行文字
    
    # 行级通用样式（紧凑无间隙，像一个完整代码块）
    _LINE_BASE = (
        "font-size: 11px; font-family: {font}; "
        "margin: 0px; padding: 0px 6px; "
        "border: none; border-radius: 0px; "
        "min-height: 16px; max-height: 16px;"
    )

    def __init__(self, param_name: str, old_value, new_value, parent=None):
        super().__init__(parent)
        self._collapsed = True  # ★ 默认折叠（露出预览窗口）
        
        old_str = self._to_str(old_value)
        new_str = self._to_str(new_value)
        is_multiline = ('\n' in old_str or '\n' in new_str
                        or len(old_str) > 60 or len(new_str) > 60)
        
        root_layout = QtWidgets.QVBoxLayout(self)
        root_layout.setContentsMargins(0, 2, 0, 2)
        root_layout.setSpacing(0)
        
        if is_multiline:
            # ── 多行 diff (VEX 等) ──
            # 标题行: param_name ▶ （默认折叠，露出预览窗口）
            self._title_text = param_name
            self._toggle_btn = QtWidgets.QPushButton(f"▶ {param_name}")
            self._toggle_btn.setFlat(True)
            self._toggle_btn.setCursor(QtCore.Qt.PointingHandCursor)
            self._toggle_btn.setObjectName("diffToggle")
            self._toggle_btn.clicked.connect(self._toggle)
            root_layout.addWidget(self._toggle_btn)
            
            # diff 内容区（用 QScrollArea 包裹，折叠时露出预览窗口）
            self._diff_frame = QtWidgets.QFrame()
            self._diff_frame.setObjectName("diffFrame")
            diff_layout = QtWidgets.QVBoxLayout(self._diff_frame)
            diff_layout.setContentsMargins(0, 2, 0, 2)
            diff_layout.setSpacing(0)
            
            _font = CursorTheme.FONT_CODE
            
            # 使用 difflib 计算行级 diff
            import difflib
            old_lines = old_str.splitlines(keepends=True)
            new_lines = new_str.splitlines(keepends=True)
            diff = list(difflib.unified_diff(old_lines, new_lines, n=2))
            
            # 跳过 --- / +++ 头两行, 取实际 diff 行
            diff_body = diff[2:] if len(diff) > 2 else []
            
            if not diff_body:
                # 没有实际差异（或 difflib 无法处理）→ 并排显示
                self._add_block(diff_layout, tr('diff.old'), old_str, is_old=True)
                self._add_block(diff_layout, tr('diff.new'), new_str, is_old=False)
            else:
                for line in diff_body:
                    line_stripped = line.rstrip('\n')
                    lbl = QtWidgets.QLabel(line_stripped)
                    lbl.setObjectName("diffLine")
                    if line.startswith('@@'):
                        lbl.setProperty("diffType", "hunk")
                    elif line.startswith('-'):
                        lbl.setProperty("diffType", "del")
                    elif line.startswith('+'):
                        lbl.setProperty("diffType", "add")
                    else:
                        lbl.setProperty("diffType", "ctx")
                    diff_layout.addWidget(lbl)
            
            # ★ 用 QScrollArea 包裹 diff_frame，折叠时限制高度而不是完全隐藏
            self._scroll_area = QtWidgets.QScrollArea()
            self._scroll_area.setObjectName("diffScrollArea")
            self._scroll_area.setWidgetResizable(True)
            self._scroll_area.setFrameShape(QtWidgets.QFrame.NoFrame)
            self._scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
            self._scroll_area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
            self._scroll_area.setWidget(self._diff_frame)
            
            # 预览高度常量
            self._PREVIEW_HEIGHT = 120   # 折叠时露出的高度(px)
            
            root_layout.addWidget(self._scroll_area)
            self._scroll_area.setMaximumHeight(self._PREVIEW_HEIGHT)  # 默认折叠，露出预览窗口
        else:
            # ── 内联 diff (标量) ──
            inline = QtWidgets.QHBoxLayout()
            inline.setContentsMargins(0, 0, 0, 0)
            inline.setSpacing(4)
            
            # 参数名
            name_lbl = QtWidgets.QLabel(f"{param_name}:")
            name_lbl.setObjectName("diffParamName")
            inline.addWidget(name_lbl)
            
            # 旧值 (红框)
            old_lbl = QtWidgets.QLabel(self._truncate(old_str, 30))
            old_lbl.setToolTip(f"{tr('diff.old')}: {old_str}")
            old_lbl.setObjectName("diffOldValue")
            inline.addWidget(old_lbl)
            
            # 箭头
            arrow = QtWidgets.QLabel("→")
            arrow.setObjectName("diffArrow")
            inline.addWidget(arrow)
            
            # 新值 (绿框)
            new_lbl = QtWidgets.QLabel(self._truncate(new_str, 30))
            new_lbl.setToolTip(f"{tr('diff.new')}: {new_str}")
            new_lbl.setObjectName("diffNewValue")
            inline.addWidget(new_lbl)
            
            root_layout.addLayout(inline)
    
    def _toggle(self):
        self._collapsed = not self._collapsed
        if self._collapsed:
            # 折叠 → 限制高度，露出预览窗口
            self._scroll_area.setMaximumHeight(self._PREVIEW_HEIGHT)
        else:
            # 展开 → 取消高度限制
            self._scroll_area.setMaximumHeight(16777215)
        arrow = "▶" if self._collapsed else "▼"
        self._toggle_btn.setText(f"{arrow} {self._title_text}")
    
    def collapse(self):
        """外部调用：强制折叠 diff（仅对多行 diff 有效）"""
        if hasattr(self, '_scroll_area') and not self._collapsed:
            self._collapsed = True
            self._scroll_area.setMaximumHeight(self._PREVIEW_HEIGHT)
            self._toggle_btn.setText(f"▶ {self._title_text}")
    
    def _add_block(self, parent_layout, title: str, text: str, is_old: bool):
        """添加旧值/新值整块（用于 difflib 无差异时的 fallback）"""
        diff_type = "del" if is_old else "add"
        header = QtWidgets.QLabel(title)
        header.setObjectName("diffLine")
        header.setProperty("diffType", "hunk")
        parent_layout.addWidget(header)
        for line in text.splitlines():
            lbl = QtWidgets.QLabel(line)
            lbl.setObjectName("diffLine")
            lbl.setProperty("diffType", diff_type)
            parent_layout.addWidget(lbl)
    
    @staticmethod
    def _to_str(value) -> str:
        if isinstance(value, dict) and "expr" in value:
            return str(value["expr"])
        if isinstance(value, (list, tuple)):
            return ', '.join(str(v) for v in value)
        return str(value)
    
    @staticmethod
    def _truncate(s: str, max_len: int) -> str:
        return s if len(s) <= max_len else s[:max_len - 1] + "…"


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


# ============================================================
# 计划块（兼容旧代码）
# ============================================================

class PlanBlock(QtWidgets.QWidget):
    """执行计划显示"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._steps = []
        
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(2)
        
        self.title = QtWidgets.QLabel("Plan")
        self.title.setObjectName("planTitle")
        layout.addWidget(self.title)
        
        self.steps_layout = QtWidgets.QVBoxLayout()
        self.steps_layout.setSpacing(1)
        layout.addLayout(self.steps_layout)
    
    def add_step(self, text: str, status: str = "pending") -> QtWidgets.QLabel:
        icons = {"pending": "○", "running": "◎", "done": "●", "error": "✗"}
        
        label = QtWidgets.QLabel(f"{icons[status]} {text}")
        label.setObjectName("planStep")
        label.setProperty("state", status)
        label.style().unpolish(label)
        label.style().polish(label)
        self.steps_layout.addWidget(label)
        self._steps.append((label, text))
        return label
    
    def update_step(self, index: int, status: str):
        if 0 <= index < len(self._steps):
            label, text = self._steps[index]
            icons = {"pending": "○", "running": "◎", "done": "●", "error": "✗"}
            label.setText(f"{icons[status]} {text}")
            label.setProperty("state", status)
            label.style().unpolish(label)
            label.style().polish(label)


# ============================================================
# PlanDAGWidget — QPainter 自绘 DAG 流程图
# ============================================================

class PlanDAGWidget(QtWidgets.QWidget):
    """Houdini 节点网络架构蓝图，用 QPainter 自绘。

    展示 Plan 执行完成后的 **节点网络拓扑**（设计蓝图），
    而不是执行步骤顺序。

    特性：
    - 按节点类型着色（SOP=蓝、OBJ=橙、MAT=绿 等）
    - 分组容器（地形系统、散布系统 等）
    - 新节点 vs 已有节点 视觉区分
    - 贝塞尔曲线连线 + 箭头
    - 自动分层布局
    - QScrollArea 包裹，窗口窄时横向滚动
    """

    # 节点类型 → (填充色, 边框色, 文字色)
    _TYPE_COLORS = {
        "sop":    ("#0d1f3c", "#4a9eff", "#a3d4ff"),
        "obj":    ("#2d1f0d", "#e8a838", "#ffe0a0"),
        "mat":    ("#0d2d1a", "#34d399", "#6ee7b7"),
        "vop":    ("#1f0d2d", "#a78bfa", "#c4b5fd"),
        "rop":    ("#2d0d1a", "#f472b6", "#f9a8d4"),
        "dop":    ("#0d2d2d", "#22d3ee", "#67e8f9"),
        "lop":    ("#1a2d0d", "#a3e635", "#d9f99d"),
        "cop":    ("#2d2d0d", "#facc15", "#fef08a"),
        "chop":   ("#1f2d0d", "#84cc16", "#bef264"),
        "out":    ("#2d1215", "#f87171", "#fca5a5"),
        "subnet": ("#1a1a2e", "#818cf8", "#c7d2fe"),
        "null":   ("#1a1a1a", "#6b7280", "#9ca3af"),
        "other":  ("#1e2030", "#4a5068", "#8892a8"),
    }

    # 已有节点的暗化系数
    _EXISTING_ALPHA = 0.5

    NODE_W = 160
    NODE_H = 42
    H_GAP = 50       # 层间距（水平，连线区域）
    V_GAP = 20       # 同层节点间距（垂直）
    PAD = 30          # 画布内边距
    GROUP_PAD = 16    # 分组容器内边距
    GROUP_TITLE_H = 22  # 分组标题高度

    def __init__(self, arch_data: dict = None, parent=None):
        """
        Args:
            arch_data: architecture 字段数据，包含 nodes, connections, groups
        """
        super().__init__(parent)
        self._arch = arch_data or {}
        self._nodes = self._arch.get("nodes", [])
        self._connections = self._arch.get("connections", [])
        self._groups = self._arch.get("groups", [])
        self._node_rects = {}      # node_id -> QRectF
        self._group_rects = {}     # group_name -> QRectF
        self._collapsed = True
        self.setObjectName("planDAG")
        self._content_w = 0
        self._content_h = 0
        self._layout_nodes()
        self._pulse_phase = 0.0
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(40)   # ~25fps

    def _tick(self):
        self._pulse_phase = (self._pulse_phase + 0.04) % (math.pi * 2)
        # 架构图有新节点标记时微弱脉动
        has_new = any(n.get("is_new", True) for n in self._nodes)
        if has_new:
            self.update()

    def set_collapsed(self, collapsed: bool):
        self._collapsed = collapsed
        if collapsed:
            self.setFixedHeight(0)
            self.setMinimumWidth(0)
        else:
            self._layout_nodes()
        self.update()

    def update_architecture(self, arch_data: dict):
        """更新架构数据并重新布局"""
        self._arch = arch_data or {}
        self._nodes = self._arch.get("nodes", [])
        self._connections = self._arch.get("connections", [])
        self._groups = self._arch.get("groups", [])
        self._layout_nodes()
        self.update()

    # ----------------------------------------------------------
    # 布局算法
    # ----------------------------------------------------------
    def _layout_nodes(self):
        """Sugiyama 分层布局：按连接拓扑自动分层排列节点。"""
        if self._collapsed:
            self.setFixedHeight(0)
            self.setMinimumWidth(0)
            return
        if not self._nodes:
            self.setFixedHeight(0)
            self.setMinimumWidth(0)
            return

        node_map = {n["id"]: n for n in self._nodes}

        # ── 1) 构建邻接表 ──
        children = {n["id"]: [] for n in self._nodes}      # from → [to, ...]
        parents = {n["id"]: [] for n in self._nodes}        # to   → [from, ...]
        for conn in self._connections:
            f, t = conn.get("from", ""), conn.get("to", "")
            if f in node_map and t in node_map:
                children[f].append(t)
                parents[t].append(f)

        # ── 2) 计算深度（从源头开始 BFS） ──
        depths = {}
        def get_depth(nid, visited=None):
            if nid in depths:
                return depths[nid]
            if visited is None:
                visited = set()
            if nid in visited:  # 防环
                depths[nid] = 0
                return 0
            visited.add(nid)
            if not parents[nid]:
                depths[nid] = 0
                return 0
            d = max(get_depth(p, visited) for p in parents[nid]) + 1
            depths[nid] = d
            return d

        for n in self._nodes:
            get_depth(n["id"])

        # ── 3) 分层 ──
        layers = {}
        for nid, d in depths.items():
            layers.setdefault(d, []).append(nid)

        max_depth = max(layers.keys()) if layers else 0
        max_per_layer = max(len(v) for v in layers.values()) if layers else 1

        # 垂直方向布局（从上到下，更符合 Houdini 节点网络习惯）
        total_w = max_per_layer * (self.NODE_W + self.H_GAP) - self.H_GAP
        total_h = (max_depth + 1) * (self.NODE_H + self.V_GAP + 10) - self.V_GAP

        self._node_rects.clear()
        for depth, nids in layers.items():
            y = self.PAD + depth * (self.NODE_H + self.V_GAP + 10)
            layer_w = len(nids) * (self.NODE_W + self.H_GAP) - self.H_GAP
            start_x = self.PAD + (total_w - layer_w) / 2
            for i, nid in enumerate(nids):
                x = start_x + i * (self.NODE_W + self.H_GAP)
                self._node_rects[nid] = QtCore.QRectF(x, y, self.NODE_W, self.NODE_H)

        # ── 4) 计算分组容器 ──
        self._group_rects.clear()
        for grp in self._groups:
            grp_name = grp.get("name", "")
            grp_nids = [nid for nid in grp.get("node_ids", []) if nid in self._node_rects]
            if not grp_name or not grp_nids:
                continue
            rects = [self._node_rects[nid] for nid in grp_nids]
            gp = self.GROUP_PAD
            min_x = min(r.left() for r in rects) - gp
            min_y = min(r.top() for r in rects) - gp - self.GROUP_TITLE_H
            max_x = max(r.right() for r in rects) + gp
            max_y = max(r.bottom() for r in rects) + gp
            self._group_rects[grp_name] = (
                QtCore.QRectF(min_x, min_y, max_x - min_x, max_y - min_y),
                grp.get("color", ""),
            )

        # ── 5) 最终尺寸 ──
        all_rects = list(self._node_rects.values())
        all_rects += [r for r, _ in self._group_rects.values()]
        if all_rects:
            max_right = max(r.right() for r in all_rects)
            max_bottom = max(r.bottom() for r in all_rects)
            self._content_w = int(max_right + self.PAD)
            self._content_h = int(max_bottom + self.PAD)
        else:
            self._content_w = 200
            self._content_h = 80

        self.setMinimumWidth(self._content_w)
        self.setFixedHeight(self._content_h)

    # ----------------------------------------------------------
    # 工具方法
    # ----------------------------------------------------------
    def _elide_text(self, painter, text: str, max_width: int) -> str:
        """按像素宽度截断文字（支持 CJK）"""
        fm = painter.fontMetrics()
        if fm.horizontalAdvance(text) <= max_width:
            return text
        for i in range(len(text), 0, -1):
            candidate = text[:i] + "…"
            if fm.horizontalAdvance(candidate) <= max_width:
                return candidate
        return "…"

    _GROUP_HINT_COLORS = {
        "blue":    (74, 158, 255),
        "green":   (52, 211, 153),
        "purple":  (167, 139, 250),
        "orange":  (232, 168, 56),
        "red":     (248, 113, 113),
        "cyan":    (34, 211, 238),
        "yellow":  (250, 204, 21),
        "pink":    (244, 114, 182),
    }

    # ----------------------------------------------------------
    # 绘制
    # ----------------------------------------------------------
    def paintEvent(self, event):
        if self._collapsed or not self._nodes:
            return

        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setRenderHint(QtGui.QPainter.TextAntialiasing)

        # ── 0) 背景 ──
        bg_grad = QtGui.QLinearGradient(0, 0, self.width(), self.height())
        bg_grad.setColorAt(0.0, QtGui.QColor("#0d0f1a"))
        bg_grad.setColorAt(1.0, QtGui.QColor("#111420"))
        p.fillRect(self.rect(), bg_grad)

        # 背景网格点
        grid_color = QtGui.QColor(100, 116, 139, 12)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(grid_color)
        for gx in range(0, self.width(), 20):
            for gy in range(0, self.height(), 20):
                p.drawEllipse(QtCore.QPointF(gx, gy), 0.5, 0.5)

        # ── 1) 分组容器 ──
        for grp_name, (grect, color_hint) in self._group_rects.items():
            r, g, b = self._GROUP_HINT_COLORS.get(color_hint, (167, 139, 250))
            # 半透明填充
            p.setBrush(QtGui.QColor(r, g, b, 8))
            pen = QtGui.QPen(QtGui.QColor(r, g, b, 40), 1.0, QtCore.Qt.DashLine)
            p.setPen(pen)
            p.drawRoundedRect(grect, 10, 10)
            # 标题
            title_font = QtGui.QFont(CursorTheme.FONT_BODY.split(",")[0].strip("' "), 8)
            title_font.setWeight(QtGui.QFont.Medium)
            p.setFont(title_font)
            p.setPen(QtGui.QColor(r, g, b, 140))
            title_rect = QtCore.QRectF(grect.left() + 10, grect.top() + 3,
                                        grect.width() - 20, self.GROUP_TITLE_H - 4)
            p.drawText(title_rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, grp_name)

        node_map = {n["id"]: n for n in self._nodes}

        # ── 2) 连线（贝塞尔曲线）──
        for conn in self._connections:
            src_id = conn.get("from", "")
            dst_id = conn.get("to", "")
            src_rect = self._node_rects.get(src_id)
            dst_rect = self._node_rects.get(dst_id)
            if not src_rect or not dst_rect:
                continue

            # 连线颜色（取源节点类型色的淡化版）
            src_node = node_map.get(src_id, {})
            ntype = src_node.get("type", "other")
            _, border_c_hex, _ = self._TYPE_COLORS.get(ntype, self._TYPE_COLORS["other"])
            line_color = QtGui.QColor(border_c_hex)
            line_color.setAlpha(80)

            # 从源底部中点 → 目标顶部中点（垂直布局）
            x1 = src_rect.center().x()
            y1 = src_rect.bottom()
            x2 = dst_rect.center().x()
            y2 = dst_rect.top()

            path = QtGui.QPainterPath()
            path.moveTo(x1, y1)
            ctrl_v = abs(y2 - y1) * 0.4
            if abs(x2 - x1) < 5:
                # 纯垂直
                path.cubicTo(x1, y1 + ctrl_v, x2, y2 - ctrl_v, x2, y2)
            else:
                # S 形曲线
                mid_y = (y1 + y2) / 2
                path.cubicTo(x1, mid_y, x2, mid_y, x2, y2)

            p.setPen(QtGui.QPen(line_color, 1.4))
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawPath(path)

            # 箭头（向下）
            al = 6
            arr_angle = math.pi / 2
            arr_tip_x, arr_tip_y = x2, y2
            ax1 = arr_tip_x - al * math.cos(arr_angle - 0.4)
            ay1 = arr_tip_y - al * math.sin(arr_angle - 0.4)
            ax2 = arr_tip_x - al * math.cos(arr_angle + 0.4)
            ay2 = arr_tip_y - al * math.sin(arr_angle + 0.4)
            arrow = QtGui.QPolygonF([
                QtCore.QPointF(arr_tip_x, arr_tip_y),
                QtCore.QPointF(ax1, ay1),
                QtCore.QPointF(ax2, ay2),
            ])
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(line_color)
            p.drawPolygon(arrow)

            # 连线标签（如果有）
            conn_label = conn.get("label", "")
            if conn_label:
                lbl_font = QtGui.QFont(CursorTheme.FONT_BODY.split(",")[0].strip("' "), 7)
                p.setFont(lbl_font)
                lbl_color = QtGui.QColor(border_c_hex)
                lbl_color.setAlpha(100)
                p.setPen(lbl_color)
                mid_x = (x1 + x2) / 2
                mid_y_lbl = (y1 + y2) / 2
                p.drawText(QtCore.QPointF(mid_x + 4, mid_y_lbl), conn_label)

        # ── 3) 节点 ──
        label_font = QtGui.QFont(CursorTheme.FONT_BODY.split(",")[0].strip("' "), 9)
        type_font = QtGui.QFont(CursorTheme.FONT_BODY.split(",")[0].strip("' "), 7)

        for n in self._nodes:
            nid = n["id"]
            rect = self._node_rects.get(nid)
            if not rect:
                continue

            ntype = n.get("type", "other")
            is_new = n.get("is_new", True)
            fill_c, border_c, text_c = self._TYPE_COLORS.get(ntype, self._TYPE_COLORS["other"])

            # 新节点微弱脉动光晕
            if is_new:
                pulse = 0.7 + 0.3 * math.sin(self._pulse_phase)
                glow_color = QtGui.QColor(border_c)
                glow_color.setAlpha(int(30 * pulse))
                glow_rect = rect.adjusted(-3, -3, 3, 3)
                p.setPen(QtCore.Qt.NoPen)
                p.setBrush(glow_color)
                p.drawRoundedRect(glow_rect, 10, 10)

            # 节点背景
            bg = QtGui.QColor(fill_c)
            alpha = 220 if is_new else int(220 * self._EXISTING_ALPHA)
            bg.setAlpha(alpha)
            p.setBrush(bg)

            # 边框
            bc = QtGui.QColor(border_c)
            if not is_new:
                bc.setAlpha(int(255 * self._EXISTING_ALPHA))
            p.setPen(QtGui.QPen(bc, 1.5 if is_new else 1.0))
            p.drawRoundedRect(rect, 6, 6)

            # 左侧类型色条
            bar_w = 3
            bar_rect = QtCore.QRectF(rect.left() + 2, rect.top() + 4,
                                      bar_w, rect.height() - 8)
            p.setPen(QtCore.Qt.NoPen)
            bar_color = QtGui.QColor(border_c)
            if not is_new:
                bar_color.setAlpha(int(200 * self._EXISTING_ALPHA))
            p.setBrush(bar_color)
            p.drawRoundedRect(bar_rect, 1.5, 1.5)

            # 上行：节点标签（label）
            p.setFont(label_font)
            label_text = n.get("label", nid)
            label_text = self._elide_text(p, label_text, int(self.NODE_W - 20))
            tc = QtGui.QColor(text_c)
            if not is_new:
                tc.setAlpha(int(255 * self._EXISTING_ALPHA))
            p.setPen(tc)
            label_rect = QtCore.QRectF(rect.left() + 10, rect.top() + 3,
                                        rect.width() - 14, 20)
            p.drawText(label_rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, label_text)

            # 下行：类型 + 节点名
            p.setFont(type_font)
            sub_text = f"{ntype.upper()}: {nid}"
            sub_text = self._elide_text(p, sub_text, int(self.NODE_W - 20))
            sub_color = QtGui.QColor(border_c)
            if not is_new:
                sub_color.setAlpha(int(180 * self._EXISTING_ALPHA))
            else:
                sub_color.setAlpha(180)
            p.setPen(sub_color)
            sub_rect = QtCore.QRectF(rect.left() + 10, rect.top() + 22,
                                      rect.width() - 14, 16)
            p.drawText(sub_rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, sub_text)

            # 已有节点标记（虚线边框覆盖）
            if not is_new:
                exist_pen = QtGui.QPen(QtGui.QColor(border_c), 0.8, QtCore.Qt.DotLine)
                exist_pen.setColor(QtGui.QColor(border_c).darker(150))
                p.setPen(exist_pen)
                p.setBrush(QtCore.Qt.NoBrush)
                p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 5, 5)

        p.end()


# ============================================================
# StreamingPlanCard — 流式 Plan 生成 + 最终交互卡片（二合一）
# ============================================================

class StreamingPlanCard(QtWidgets.QWidget):
    """流式 Plan 卡片 — 生成阶段逐步构建，完成后原地升级为完整交互卡片。

    生命周期：
    1. 创建时只有标题骨架 + STREAMING 标签
    2. on_tool_args_delta 驱动 update_from_accumulated()，逐步渲染标题 → 概述 → 步骤
    3. 工具执行完毕后，调用 finalize_with_data(plan_data) 原地补充：
       - 步骤详情（sub_steps, tools, risk, deps, expected, fallback, notes）
       - DAG 架构图
       - 进度条
       - Confirm / Reject 按钮
    4. 后续 update_step_status / set_confirmed / set_rejected 等方法与旧 PlanViewer 完全兼容
    """

    planConfirmed = QtCore.Signal(dict)
    planRejected = QtCore.Signal()

    _STATUS_ICONS = {
        "pending": "○", "running": "◎", "done": "●", "error": "✗",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._plan = {}
        self._step_labels = {}   # step_id -> (icon_w, title_lbl)
        self._confirmed = False
        self._rejected = False
        self._finalized = False

        self.setObjectName("planViewerOuter")
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 6, 0, 6)
        outer.setSpacing(0)

        self._card = QtWidgets.QFrame(self)
        self._card.setObjectName("planViewerCard")
        self._card_lay = QtWidgets.QVBoxLayout(self._card)
        self._card_lay.setContentsMargins(14, 10, 14, 10)
        self._card_lay.setSpacing(6)

        # ── 标题行 ──
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(8)
        icon_lbl = QtWidgets.QLabel("📋")
        icon_lbl.setFixedWidth(18)
        header.addWidget(icon_lbl)

        self._title_lbl = QtWidgets.QLabel("Planning...")
        self._title_lbl.setObjectName("planViewerTitle")
        self._title_lbl.setWordWrap(True)
        header.addWidget(self._title_lbl, 1)

        self._status_badge = QtWidgets.QLabel("STREAMING")
        self._status_badge.setObjectName("planStatusBadge")
        self._status_badge.setAlignment(QtCore.Qt.AlignCenter)
        self._status_badge.setFixedHeight(20)
        self._status_badge.setMinimumWidth(60)
        header.addWidget(self._status_badge)
        self._card_lay.addLayout(header)

        # ── 概述 ──
        self._overview_lbl = QtWidgets.QLabel("")
        self._overview_lbl.setObjectName("planOverview")
        self._overview_lbl.setWordWrap(True)
        self._overview_lbl.setVisible(False)
        self._card_lay.addWidget(self._overview_lbl)

        # ── 分隔线 ──
        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        sep.setObjectName("planSeparator")
        self._card_lay.addWidget(sep)

        # ── 步骤容器（流式填充） ──
        self._steps_container = QtWidgets.QWidget()
        self._steps_lay = QtWidgets.QVBoxLayout(self._steps_container)
        self._steps_lay.setContentsMargins(0, 0, 0, 0)
        self._steps_lay.setSpacing(2)
        self._card_lay.addWidget(self._steps_container)

        # ── 正在生成指示器 ──
        self._loading_lbl = QtWidgets.QLabel("  ⋯ generating steps...")
        self._loading_lbl.setObjectName("planStepDep")
        self._card_lay.addWidget(self._loading_lbl)

        # ── 以下区域在 finalize_with_data 时动态添加 ──
        # DAG, 进度条, 按钮 → 预留 placeholder
        self._dag_widget = None
        self._dag_scroll = None
        self._dag_toggle = None
        self._progress_bar = None
        self._btn_row = None
        self._btn_confirm = None
        self._btn_reject = None

        outer.addWidget(self._card)

        # ── 流式跟踪状态 ──
        self._rendered_step_count = 0
        self._current_title = ""
        self._current_overview = ""

    # ==================================================================
    # 流式阶段 API — 由 on_tool_args_delta 驱动
    # ==================================================================

    def update_from_accumulated(self, accumulated: str):
        """从 create_plan 的不完整 JSON 中增量提取并渲染内容。"""
        if self._finalized:
            return
        import re as _re

        # 提取 title
        m_title = _re.search(r'"title"\s*:\s*"((?:[^"\\]|\\.)*)"', accumulated)
        if m_title and m_title.group(1) != self._current_title:
            self._current_title = m_title.group(1)
            self._title_lbl.setText(self._current_title)

        # 提取 overview
        m_ov = _re.search(r'"overview"\s*:\s*"((?:[^"\\]|\\.)*)"', accumulated)
        if m_ov and m_ov.group(1) != self._current_overview:
            self._current_overview = m_ov.group(1)
            self._overview_lbl.setText(self._current_overview)
            self._overview_lbl.setVisible(True)

        # 匹配 steps 数组中的每个 step 对象
        steps_match = _re.search(r'"steps"\s*:\s*\[', accumulated)
        if not steps_match:
            return

        steps_json_start = steps_match.end()
        step_pattern = _re.compile(
            r'\{\s*"id"\s*:\s*"(step-\d+)"\s*,\s*'
            r'"(?:title|description)"\s*:\s*"((?:[^"\\]|\\.)*)"',
        )
        all_steps = list(step_pattern.finditer(accumulated, steps_json_start))

        # 仅渲染新出现的 step
        for i in range(self._rendered_step_count, len(all_steps)):
            m = all_steps[i]
            self._add_streaming_step(m.group(1), m.group(2))
            self._rendered_step_count += 1

        # 检查是否进入 architecture 部分
        if '"architecture"' in accumulated:
            self._loading_lbl.setText("  ⋯ generating architecture...")

    def _add_streaming_step(self, step_id: str, text: str):
        """流式阶段：添加一行简化版步骤"""
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(6)
        row.setContentsMargins(4, 2, 0, 0)

        icon_w = QtWidgets.QLabel("○")
        icon_w.setFixedWidth(14)
        icon_w.setObjectName("planStepIcon")
        icon_w.setProperty("state", "pending")
        row.addWidget(icon_w)

        sid_lbl = QtWidgets.QLabel(step_id)
        sid_lbl.setObjectName("planStepId")
        sid_lbl.setFixedWidth(50)
        row.addWidget(sid_lbl)

        title_lbl = QtWidgets.QLabel(text)
        title_lbl.setObjectName("planStepTitle")
        title_lbl.setWordWrap(True)
        row.addWidget(title_lbl, 1)

        w = QtWidgets.QWidget()
        w.setLayout(row)
        self._steps_lay.addWidget(w)

        # 记录引用以便 finalize 时更新
        self._step_labels[step_id] = (icon_w, title_lbl)

    # ==================================================================
    # 完成阶段 API — 工具执行结束后调用
    # ==================================================================

    def finalize_with_data(self, plan_data: dict):
        """用完整的 plan_data 原地升级卡片 — 补充详情、DAG、进度条、按钮。

        此方法只会被调用一次。调用后卡片与旧 PlanViewer 功能完全等价。
        """
        if self._finalized:
            return
        self._finalized = True
        self._plan = plan_data

        # 隐藏加载指示器
        self._loading_lbl.setVisible(False)

        # 用完整数据刷新标题 + 概述（覆盖流式阶段的可能不完整内容）
        self._title_lbl.setText(plan_data.get("title", self._current_title or "Plan"))
        overview = plan_data.get("overview", "")
        if overview:
            self._overview_lbl.setText(overview)
            self._overview_lbl.setVisible(True)

        # ── 清空流式步骤，用完整步骤重建（含详情、deps 等） ──
        while self._steps_lay.count():
            item = self._steps_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._step_labels.clear()

        steps = plan_data.get("steps", [])
        phases = plan_data.get("phases", [])
        step_phase_map = {}
        for phase in phases:
            for sid in phase.get("step_ids", []):
                step_phase_map[sid] = phase.get("name", "")

        rendered_phases = set()
        for s in steps:
            step_id = s.get("id", "")

            # Phase 标题
            phase_name = step_phase_map.get(step_id, "")
            if phase_name and phase_name not in rendered_phases:
                rendered_phases.add(phase_name)
                phase_sep = QtWidgets.QFrame()
                phase_sep.setFrameShape(QtWidgets.QFrame.HLine)
                phase_sep.setObjectName("planPhaseSeparator")
                self._steps_lay.addWidget(phase_sep)
                phase_lbl = QtWidgets.QLabel(phase_name)
                phase_lbl.setObjectName("planPhaseHeader")
                self._steps_lay.addWidget(phase_lbl)

            # 步骤主行
            step_row = QtWidgets.QHBoxLayout()
            step_row.setSpacing(6)
            step_row.setContentsMargins(4, 2, 0, 0)

            status = s.get("status", "pending")
            icon_w = QtWidgets.QLabel(self._STATUS_ICONS.get(status, "○"))
            icon_w.setFixedWidth(14)
            icon_w.setObjectName("planStepIcon")
            icon_w.setProperty("state", status)
            step_row.addWidget(icon_w)

            sid_lbl = QtWidgets.QLabel(step_id)
            sid_lbl.setObjectName("planStepId")
            sid_lbl.setFixedWidth(50)
            step_row.addWidget(sid_lbl)

            title_text = s.get("title", s.get("description", ""))
            title_lbl = QtWidgets.QLabel(title_text)
            title_lbl.setObjectName("planStepTitle")
            title_lbl.setWordWrap(True)
            step_row.addWidget(title_lbl, 1)

            # 风险标记
            risk = s.get("risk", "")
            if risk and risk != "low":
                risk_lbl = QtWidgets.QLabel(f"⚠ {risk.upper()}")
                risk_lbl.setObjectName("planStepRisk")
                risk_lbl.setProperty("risk", risk)
                step_row.addWidget(risk_lbl)

            # 依赖标记
            deps = s.get("depends_on", [])
            if deps:
                short_deps = [d.replace("step-", "s") for d in deps]
                dep_lbl = QtWidgets.QLabel(f"← {','.join(short_deps)}")
                dep_lbl.setObjectName("planStepDep")
                dep_lbl.setMaximumWidth(80)
                step_row.addWidget(dep_lbl)

            row_w = QtWidgets.QWidget()
            row_w.setLayout(step_row)
            self._steps_lay.addWidget(row_w)

            # 步骤详情
            detail_w = QtWidgets.QWidget()
            detail_w.setObjectName("planStepDetail")
            detail_lay = QtWidgets.QVBoxLayout(detail_w)
            detail_lay.setContentsMargins(24, 0, 4, 4)
            detail_lay.setSpacing(2)

            for sub in s.get("sub_steps", []):
                lbl = QtWidgets.QLabel(f"  ├ {sub}")
                lbl.setObjectName("planSubStep")
                lbl.setWordWrap(True)
                detail_lay.addWidget(lbl)
            step_tools = s.get("tools", [])
            if step_tools:
                detail_lay.addWidget(QtWidgets.QLabel(f"Tools: {', '.join(step_tools)}"))
            expected = s.get("expected_result", "")
            if expected:
                lbl = QtWidgets.QLabel(f"Expected: {expected}")
                lbl.setObjectName("planStepExpected")
                lbl.setWordWrap(True)
                detail_lay.addWidget(lbl)
            fallback = s.get("fallback", "")
            if fallback:
                lbl = QtWidgets.QLabel(f"Fallback: {fallback}")
                lbl.setObjectName("planStepFallback")
                lbl.setWordWrap(True)
                detail_lay.addWidget(lbl)
            notes = s.get("notes", "")
            if notes:
                lbl = QtWidgets.QLabel(f"Note: {notes}")
                lbl.setObjectName("planStepNotes")
                lbl.setWordWrap(True)
                detail_lay.addWidget(lbl)

            if detail_lay.count() > 0:
                self._steps_lay.addWidget(detail_w)

            self._step_labels[step_id] = (icon_w, title_lbl)

        # ── DAG 架构图 ──
        sep2 = QtWidgets.QFrame()
        sep2.setFrameShape(QtWidgets.QFrame.HLine)
        sep2.setObjectName("planSeparator")
        self._card_lay.addWidget(sep2)

        dag_header_row = QtWidgets.QHBoxLayout()
        arch_data = plan_data.get("architecture", {})
        has_real_arch = bool(arch_data and arch_data.get("nodes"))
        if not has_real_arch:
            arch_data = PlanViewer._build_step_dag(steps)

        dag_title = "Architecture" if has_real_arch else "Flow"
        dag_label = QtWidgets.QLabel(dag_title)
        dag_label.setObjectName("planSectionHeader")
        dag_header_row.addWidget(dag_label)
        dag_header_row.addStretch()

        self._dag_toggle = QtWidgets.QPushButton("▾ Collapse")
        self._dag_toggle.setObjectName("planDAGToggle")
        self._dag_toggle.setCursor(QtCore.Qt.PointingHandCursor)
        self._dag_toggle.setFixedHeight(20)
        self._dag_toggle.clicked.connect(self._toggle_dag)
        dag_header_row.addWidget(self._dag_toggle)
        self._card_lay.addLayout(dag_header_row)

        self._dag_widget = PlanDAGWidget(arch_data, self)
        self._dag_widget.set_collapsed(False)

        self._dag_scroll = QtWidgets.QScrollArea()
        self._dag_scroll.setObjectName("planDAGScroll")
        self._dag_scroll.setWidgetResizable(False)
        self._dag_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self._dag_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._dag_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._dag_scroll.setWidget(self._dag_widget)
        h = self._dag_widget._content_h
        self._dag_scroll.setFixedHeight(min(h, 400) if h > 0 else 200)
        self._card_lay.addWidget(self._dag_scroll)

        # ── 进度条 ──
        self._progress_bar = QtWidgets.QProgressBar()
        self._progress_bar.setObjectName("planProgress")
        self._progress_bar.setFixedHeight(8)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setRange(0, max(len(steps), 1))
        self._progress_bar.setValue(0)
        self._card_lay.addWidget(self._progress_bar)

        # ── Confirm / Reject 按钮 ──
        self._btn_row = QtWidgets.QWidget()
        btn_lay = QtWidgets.QHBoxLayout(self._btn_row)
        btn_lay.setContentsMargins(0, 4, 0, 0)
        btn_lay.setSpacing(8)
        btn_lay.addStretch()

        self._btn_reject = QtWidgets.QPushButton("Reject")
        self._btn_reject.setObjectName("planBtnReject")
        self._btn_reject.setCursor(QtCore.Qt.PointingHandCursor)
        self._btn_reject.setFixedHeight(28)
        self._btn_reject.setMinimumWidth(80)
        self._btn_reject.clicked.connect(self._do_reject)
        btn_lay.addWidget(self._btn_reject)

        self._btn_confirm = QtWidgets.QPushButton("Confirm")
        self._btn_confirm.setObjectName("planBtnConfirm")
        self._btn_confirm.setCursor(QtCore.Qt.PointingHandCursor)
        self._btn_confirm.setFixedHeight(28)
        self._btn_confirm.setMinimumWidth(80)
        self._btn_confirm.clicked.connect(self._do_confirm)
        btn_lay.addWidget(self._btn_confirm)

        self._card_lay.addWidget(self._btn_row)

        # 刷新状态
        self._refresh_ui()

    # ==================================================================
    # PlanViewer 兼容 API — finalize 后可直接使用
    # ==================================================================

    def set_confirmed(self):
        self._confirmed = True
        self._plan["status"] = "confirmed"
        if self._btn_confirm:
            self._btn_confirm.setEnabled(False)
            self._btn_reject.setEnabled(False)
            self._btn_confirm.setText("✓ Confirmed")
        self._refresh_ui()

    def set_rejected(self):
        self._rejected = True
        self._plan["status"] = "rejected"
        if self._btn_confirm:
            self._btn_confirm.setEnabled(False)
            self._btn_reject.setEnabled(False)
            self._btn_reject.setText("✗ Rejected")
        self._refresh_ui()

    def update_step_status(self, step_id: str, status: str, result_summary: str = ""):
        for s in self._plan.get("steps", []):
            if s["id"] == step_id:
                s["status"] = status
                if result_summary:
                    s["result_summary"] = result_summary
                break
        if step_id in self._step_labels:
            icon_w, _ = self._step_labels[step_id]
            icon_w.setText(self._STATUS_ICONS.get(status, "○"))
            icon_w.setProperty("state", status)
            icon_w.style().unpolish(icon_w)
            icon_w.style().polish(icon_w)
        if self._progress_bar:
            self._update_progress()
        all_done = all(
            s.get("status") in ("done", "error")
            for s in self._plan.get("steps", [])
        )
        if all_done:
            self._plan["status"] = "completed"
            self._refresh_ui()

    def get_plan_data(self) -> dict:
        return self._plan

    # ==================================================================
    # 内部方法
    # ==================================================================

    def _do_confirm(self):
        if self._confirmed or self._rejected:
            return
        self.set_confirmed()
        self.planConfirmed.emit(dict(self._plan))

    def _do_reject(self):
        if self._confirmed or self._rejected:
            return
        self.set_rejected()
        self.planRejected.emit()

    def _toggle_dag(self):
        if not self._dag_widget:
            return
        collapsed = not self._dag_widget._collapsed
        self._dag_widget.set_collapsed(collapsed)
        self._dag_toggle.setText("▸ Expand" if collapsed else "▾ Collapse")
        if collapsed:
            self._dag_scroll.setFixedHeight(0)
        else:
            h = self._dag_widget._content_h
            self._dag_scroll.setFixedHeight(min(h + 14, 400) if h > 0 else 200)

    def _update_progress(self):
        if not self._progress_bar:
            return
        steps = self._plan.get("steps", [])
        done = sum(1 for s in steps if s.get("status") == "done")
        self._progress_bar.setValue(done)

    def _refresh_ui(self):
        status = self._plan.get("status", "draft")
        badge_map = {
            "draft":     ("DRAFT",     "#64748b"),
            "confirmed": ("CONFIRMED", "#a78bfa"),
            "executing": ("EXECUTING", "#3b82f6"),
            "completed": ("COMPLETED", "#10b981"),
            "rejected":  ("REJECTED",  "#ef4444"),
        }
        text, color = badge_map.get(status, ("DRAFT", "#64748b"))
        self._status_badge.setText(text)
        self._status_badge.setStyleSheet(
            f"color: {color}; background: rgba(0,0,0,0.3); "
            f"border: 1px solid {color}; border-radius: 4px; "
            f"font-size: 10px; padding: 1px 8px; font-weight: bold;"
        )
        if self._btn_row:
            show = status == "draft" and not self._confirmed and not self._rejected
            self._btn_row.setVisible(show)
        if self._progress_bar:
            self._update_progress()


# ============================================================
# PlanViewer — Plan 模式交互卡片（嵌入聊天流）
# ============================================================

class PlanViewer(QtWidgets.QWidget):
    """Plan 执行计划交互卡片。

    在聊天流中渲染为可折叠的卡片，包含：
    - 标题 + 状态
    - 概述
    - 步骤列表（含状态图标）
    - DAG 流程图（可展开/收起）
    - 进度条
    - Confirm / Reject 按钮（仅在 awaiting_confirmation 状态可见）
    """

    planConfirmed = QtCore.Signal(dict)   # 发射 plan_data
    planRejected = QtCore.Signal()

    _STATUS_ICONS = {
        "pending":  "○",
        "running":  "◎",
        "done":     "●",
        "error":    "✗",
    }

    def __init__(self, plan_data: dict, parent=None):
        super().__init__(parent)
        self._plan = plan_data
        self._step_labels = {}  # step_id -> QLabel
        self._confirmed = False
        self._rejected = False

        self.setObjectName("planViewerOuter")
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 6, 0, 6)
        outer.setSpacing(0)

        # ── 卡片容器 ──
        self._card = QtWidgets.QFrame(self)
        self._card.setObjectName("planViewerCard")
        card_lay = QtWidgets.QVBoxLayout(self._card)
        card_lay.setContentsMargins(14, 10, 14, 10)
        card_lay.setSpacing(6)

        # ── 标题行 ──
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(8)

        icon_lbl = QtWidgets.QLabel("📋")
        icon_lbl.setFixedWidth(18)
        header.addWidget(icon_lbl)

        self._title_lbl = QtWidgets.QLabel(plan_data.get("title", "Plan"))
        self._title_lbl.setObjectName("planViewerTitle")
        self._title_lbl.setWordWrap(True)
        header.addWidget(self._title_lbl, 1)

        self._status_badge = QtWidgets.QLabel("DRAFT")
        self._status_badge.setObjectName("planStatusBadge")
        self._status_badge.setAlignment(QtCore.Qt.AlignCenter)
        self._status_badge.setFixedHeight(20)
        self._status_badge.setMinimumWidth(60)
        header.addWidget(self._status_badge)

        card_lay.addLayout(header)

        # ── 概述 ──
        overview = plan_data.get("overview", "")
        if overview:
            ov_lbl = QtWidgets.QLabel(overview)
            ov_lbl.setObjectName("planOverview")
            ov_lbl.setWordWrap(True)
            card_lay.addWidget(ov_lbl)

        # ── 复杂度 & 预估操作数 ──
        complexity = plan_data.get("complexity", "")
        est_ops = plan_data.get("estimated_total_operations", 0)
        if complexity or est_ops:
            meta_parts = []
            if complexity:
                meta_parts.append(f"Complexity: {complexity.upper()}")
            if est_ops:
                meta_parts.append(f"Est. Operations: {est_ops}")
            meta_lbl = QtWidgets.QLabel("  |  ".join(meta_parts))
            meta_lbl.setObjectName("planMetaInfo")
            card_lay.addWidget(meta_lbl)

        # ── 分隔线 ──
        sep1 = QtWidgets.QFrame()
        sep1.setFrameShape(QtWidgets.QFrame.HLine)
        sep1.setObjectName("planSeparator")
        card_lay.addWidget(sep1)

        # ── 步骤列表（增强版：支持 phases 分组 + 子步骤 + 详情）──
        steps = plan_data.get("steps", [])
        phases = plan_data.get("phases", [])

        # 构建 step_id → phase 映射
        step_phase_map = {}
        for phase in phases:
            for sid in phase.get("step_ids", []):
                step_phase_map[sid] = phase.get("name", "")

        rendered_phases = set()
        for s in steps:
            step_id = s.get("id", "")

            # 如果此步骤属于某个 phase，且 phase 还未渲染过 → 插入 phase 标题
            phase_name = step_phase_map.get(step_id, "")
            if phase_name and phase_name not in rendered_phases:
                rendered_phases.add(phase_name)
                phase_sep = QtWidgets.QFrame()
                phase_sep.setFrameShape(QtWidgets.QFrame.HLine)
                phase_sep.setObjectName("planPhaseSeparator")
                card_lay.addWidget(phase_sep)
                phase_lbl = QtWidgets.QLabel(phase_name)
                phase_lbl.setObjectName("planPhaseHeader")
                card_lay.addWidget(phase_lbl)

            # ── 步骤标题行 ──
            step_row = QtWidgets.QHBoxLayout()
            step_row.setSpacing(6)
            step_row.setContentsMargins(4, 2, 0, 0)

            status = s.get("status", "pending")
            icon = self._STATUS_ICONS.get(status, "○")

            icon_w = QtWidgets.QLabel(icon)
            icon_w.setFixedWidth(14)
            icon_w.setObjectName("planStepIcon")
            icon_w.setProperty("state", status)
            step_row.addWidget(icon_w)

            sid_lbl = QtWidgets.QLabel(step_id)
            sid_lbl.setObjectName("planStepId")
            sid_lbl.setFixedWidth(50)
            step_row.addWidget(sid_lbl)

            # 使用 title 作为步骤列表显示文本，description 放在详情中
            title_text = s.get("title", s.get("description", ""))
            title_lbl = QtWidgets.QLabel(title_text)
            title_lbl.setObjectName("planStepTitle")
            title_lbl.setWordWrap(True)
            step_row.addWidget(title_lbl, 1)

            # 风险标记
            risk = s.get("risk", "")
            if risk and risk != "low":
                risk_lbl = QtWidgets.QLabel(f"⚠ {risk.upper()}")
                risk_lbl.setObjectName("planStepRisk")
                risk_lbl.setProperty("risk", risk)
                step_row.addWidget(risk_lbl)

            # 依赖标记（紧凑格式）
            deps = s.get("depends_on", [])
            if deps:
                # 将 "step-1" 缩写为 "s1"，节省空间
                short_deps = [d.replace("step-", "s") for d in deps]
                dep_lbl = QtWidgets.QLabel(f"← {','.join(short_deps)}")
                dep_lbl.setObjectName("planStepDep")
                dep_lbl.setMaximumWidth(80)
                step_row.addWidget(dep_lbl)

            row_w = QtWidgets.QWidget()
            row_w.setLayout(step_row)
            card_lay.addWidget(row_w)

            # ── 步骤详情区域（sub_steps + tools + expected + fallback）──
            detail_w = QtWidgets.QWidget()
            detail_w.setObjectName("planStepDetail")
            detail_lay = QtWidgets.QVBoxLayout(detail_w)
            detail_lay.setContentsMargins(24, 0, 4, 4)
            detail_lay.setSpacing(2)

            # 子步骤
            sub_steps = s.get("sub_steps", [])
            for sub in sub_steps:
                sub_lbl = QtWidgets.QLabel(f"  ├ {sub}")
                sub_lbl.setObjectName("planSubStep")
                sub_lbl.setWordWrap(True)
                detail_lay.addWidget(sub_lbl)

            # 工具列表
            tools = s.get("tools", [])
            if tools:
                tools_lbl = QtWidgets.QLabel(f"Tools: {', '.join(tools)}")
                tools_lbl.setObjectName("planStepTools")
                detail_lay.addWidget(tools_lbl)

            # 预期结果
            expected = s.get("expected_result", "")
            if expected:
                exp_lbl = QtWidgets.QLabel(f"Expected: {expected}")
                exp_lbl.setObjectName("planStepExpected")
                exp_lbl.setWordWrap(True)
                detail_lay.addWidget(exp_lbl)

            # 回退策略
            fallback = s.get("fallback", "")
            if fallback:
                fb_lbl = QtWidgets.QLabel(f"Fallback: {fallback}")
                fb_lbl.setObjectName("planStepFallback")
                fb_lbl.setWordWrap(True)
                detail_lay.addWidget(fb_lbl)

            # 备注
            notes = s.get("notes", "")
            if notes:
                notes_lbl = QtWidgets.QLabel(f"Note: {notes}")
                notes_lbl.setObjectName("planStepNotes")
                notes_lbl.setWordWrap(True)
                detail_lay.addWidget(notes_lbl)

            if detail_lay.count() > 0:
                card_lay.addWidget(detail_w)

            self._step_labels[step_id] = (icon_w, title_lbl)

        # ── DAG 流程图区域 ──
        sep2 = QtWidgets.QFrame()
        sep2.setFrameShape(QtWidgets.QFrame.HLine)
        sep2.setObjectName("planSeparator")
        card_lay.addWidget(sep2)

        dag_header_row = QtWidgets.QHBoxLayout()

        # 根据数据类型决定标题
        arch_data = plan_data.get("architecture", {})
        has_real_arch = bool(arch_data and arch_data.get("nodes"))

        if not has_real_arch:
            # ── 回退：从 steps 的 depends_on 自动生成步骤依赖图 ──
            arch_data = self._build_step_dag(steps)

        dag_title = "Architecture" if has_real_arch else "Flow"
        dag_label = QtWidgets.QLabel(dag_title)
        dag_label.setObjectName("planSectionHeader")
        dag_header_row.addWidget(dag_label)
        dag_header_row.addStretch()

        self._dag_toggle = QtWidgets.QPushButton("▾ Collapse")
        self._dag_toggle.setObjectName("planDAGToggle")
        self._dag_toggle.setCursor(QtCore.Qt.PointingHandCursor)
        self._dag_toggle.setFixedHeight(20)
        self._dag_toggle.clicked.connect(self._toggle_dag)
        dag_header_row.addWidget(self._dag_toggle)
        card_lay.addLayout(dag_header_row)

        self._dag_widget = PlanDAGWidget(arch_data, self)
        self._dag_widget.set_collapsed(False)

        # ★ 用 QScrollArea 包裹 DAG，窗口窄时自动出横向滚动条
        self._dag_scroll = QtWidgets.QScrollArea()
        self._dag_scroll.setObjectName("planDAGScroll")
        self._dag_scroll.setWidgetResizable(False)  # 保持 DAG 原始尺寸
        self._dag_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self._dag_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._dag_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._dag_scroll.setWidget(self._dag_widget)
        # 初始展开：高度跟随 DAG 内容
        h = self._dag_widget._content_h
        self._dag_scroll.setFixedHeight(min(h, 400) if h > 0 else 200)
        card_lay.addWidget(self._dag_scroll)

        # ── 进度条 ──
        self._progress_bar = QtWidgets.QProgressBar()
        self._progress_bar.setObjectName("planProgress")
        self._progress_bar.setFixedHeight(8)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setRange(0, max(len(steps), 1))
        self._progress_bar.setValue(0)
        card_lay.addWidget(self._progress_bar)

        # ── 按钮行 ──
        self._btn_row = QtWidgets.QWidget()
        btn_lay = QtWidgets.QHBoxLayout(self._btn_row)
        btn_lay.setContentsMargins(0, 4, 0, 0)
        btn_lay.setSpacing(8)
        btn_lay.addStretch()

        self._btn_reject = QtWidgets.QPushButton("Reject")
        self._btn_reject.setObjectName("planBtnReject")
        self._btn_reject.setCursor(QtCore.Qt.PointingHandCursor)
        self._btn_reject.setFixedHeight(28)
        self._btn_reject.setMinimumWidth(80)
        self._btn_reject.clicked.connect(self._do_reject)
        btn_lay.addWidget(self._btn_reject)

        self._btn_confirm = QtWidgets.QPushButton("Confirm")
        self._btn_confirm.setObjectName("planBtnConfirm")
        self._btn_confirm.setCursor(QtCore.Qt.PointingHandCursor)
        self._btn_confirm.setFixedHeight(28)
        self._btn_confirm.setMinimumWidth(80)
        self._btn_confirm.clicked.connect(self._do_confirm)
        btn_lay.addWidget(self._btn_confirm)

        card_lay.addWidget(self._btn_row)

        outer.addWidget(self._card)
        self._refresh_ui()

    # ----------------------------------------------------------
    # 公共方法
    # ----------------------------------------------------------

    def set_confirmed(self):
        """确认后禁用按钮"""
        self._confirmed = True
        self._plan["status"] = "confirmed"
        self._btn_confirm.setEnabled(False)
        self._btn_reject.setEnabled(False)
        self._btn_confirm.setText("✓ Confirmed")
        self._refresh_ui()

    def set_rejected(self):
        """拒绝后禁用按钮"""
        self._rejected = True
        self._plan["status"] = "rejected"
        self._btn_confirm.setEnabled(False)
        self._btn_reject.setEnabled(False)
        self._btn_reject.setText("✗ Rejected")
        self._refresh_ui()

    def update_step_status(self, step_id: str, status: str, result_summary: str = ""):
        """实时更新某个步骤的状态（执行阶段调用）"""
        # 更新内部数据
        for s in self._plan.get("steps", []):
            if s["id"] == step_id:
                s["status"] = status
                if result_summary:
                    s["result_summary"] = result_summary
                break

        # 更新步骤列表 UI
        if step_id in self._step_labels:
            icon_w, desc_lbl = self._step_labels[step_id]
            icon = self._STATUS_ICONS.get(status, "○")
            icon_w.setText(icon)
            icon_w.setProperty("state", status)
            icon_w.style().unpolish(icon_w)
            icon_w.style().polish(icon_w)

        # 架构图为静态蓝图，步骤状态变更时无需更新
        # self._dag_widget 展示的是最终节点网络拓扑

        # 更新进度条
        self._update_progress()

        # 检查是否全部完成
        all_done = all(
            s.get("status") in ("done", "error")
            for s in self._plan.get("steps", [])
        )
        if all_done:
            self._plan["status"] = "completed"
            self._refresh_ui()

    def get_plan_data(self) -> dict:
        return self._plan

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _do_confirm(self):
        if self._confirmed or self._rejected:
            return
        self.set_confirmed()
        self.planConfirmed.emit(dict(self._plan))

    def _do_reject(self):
        if self._confirmed or self._rejected:
            return
        self.set_rejected()
        self.planRejected.emit()

    @staticmethod
    def _build_step_dag(steps: list) -> dict:
        """从 steps 的 depends_on 关系自动构建步骤依赖 DAG 数据。

        当 plan 没有 architecture 字段时作为回退方案，
        将步骤列表转换为 PlanDAGWidget 可接受的 architecture 格式。
        """
        nodes = []
        connections = []

        # 收集所有 depends_on 关系
        has_any_deps = any(s.get("depends_on") for s in steps)

        for s in steps:
            sid = s.get("id", "")
            title = s.get("title", s.get("description", sid))
            # 截取前 20 字符作为 label
            label = title[:20] + ("…" if len(title) > 20 else "")
            nodes.append({
                "id": sid,
                "label": label,
                "type": "sop",   # 默认类型
                "is_new": True,
                "params": ", ".join(s.get("tools", [])[:2]) if s.get("tools") else "",
            })

            # 依赖关系 → 连线
            for dep_id in (s.get("depends_on") or []):
                connections.append({"from": dep_id, "to": sid})

        # 没有依赖关系时，自动生成线性链
        if not has_any_deps and len(steps) > 1:
            for i in range(len(steps) - 1):
                connections.append({
                    "from": steps[i]["id"],
                    "to": steps[i + 1]["id"],
                })

        # 尝试从 phases 构建分组（如果有的话不会到这里，但兼容）
        return {
            "nodes": nodes,
            "connections": connections,
            "groups": [],
        }

    def _toggle_dag(self):
        collapsed = not self._dag_widget._collapsed
        self._dag_widget.set_collapsed(collapsed)
        self._dag_toggle.setText("▸ Expand" if collapsed else "▾ Collapse")
        # ★ 同步滚动区域高度
        if collapsed:
            self._dag_scroll.setFixedHeight(0)
        else:
            # DAG 内容高度 + 滚动条可能占用的空间
            h = self._dag_widget._content_h
            scrollbar_h = 14  # 横向滚动条高度预留
            self._dag_scroll.setFixedHeight(h + scrollbar_h)
            self._dag_scroll.setMinimumHeight(h)

    def _update_progress(self):
        steps = self._plan.get("steps", [])
        done = sum(1 for s in steps if s.get("status") == "done")
        self._progress_bar.setValue(done)

    def _refresh_ui(self):
        status = self._plan.get("status", "draft")
        badge_map = {
            "draft":     ("DRAFT",     "#64748b"),
            "confirmed": ("CONFIRMED", "#a78bfa"),
            "executing": ("EXECUTING", "#3b82f6"),
            "completed": ("COMPLETED", "#10b981"),
            "rejected":  ("REJECTED",  "#ef4444"),
        }
        text, color = badge_map.get(status, ("DRAFT", "#64748b"))
        self._status_badge.setText(text)
        self._status_badge.setStyleSheet(
            f"color: {color}; background: rgba(0,0,0,0.3); "
            f"border: 1px solid {color}; border-radius: 4px; "
            f"font-size: 10px; padding: 1px 8px; font-weight: bold;"
        )
        # 按钮可见性
        show_buttons = status in ("draft", "confirmed") and not self._confirmed and not self._rejected
        self._btn_row.setVisible(show_buttons and status == "draft")
        self._update_progress()


# ============================================================
# AskQuestionCard — AI 主动提问交互卡片（Plan 规划阶段）
# ============================================================

class AskQuestionCard(QtWidgets.QFrame):
    """嵌入聊天流中的 AI 提问卡片。

    AI 在 Plan 规划阶段需要澄清信息时，通过 ask_question 工具发起提问。
    用户通过单选/多选/自由文本回答后，点击提交按钮。
    答案通过 answered 信号返回给后台线程。

    questions 结构示例:
        [
            {
                "id": "q1",
                "prompt": "你想用 HeightField 还是 Grid？",
                "options": [
                    {"id": "hf", "label": "HeightField (推荐)"},
                    {"id": "grid", "label": "Grid"}
                ],
                "allow_multiple": false,
                "allow_free_text": true
            }
        ]
    """

    answered = QtCore.Signal(dict)    # 发射答案 dict: {q_id: [selected_option_ids], ...}
    cancelled = QtCore.Signal()       # 用户取消

    def __init__(self, questions: list, parent=None):
        super().__init__(parent)
        self._questions = questions
        self._answered = False
        self._widgets = {}  # q_id -> {"buttons": [...], "group": QButtonGroup, "free_text": QLineEdit}

        self.setObjectName("askQuestionCard")
        self.setFrameShape(QtWidgets.QFrame.NoFrame)

        main_lay = QtWidgets.QVBoxLayout(self)
        main_lay.setContentsMargins(14, 10, 14, 10)
        main_lay.setSpacing(8)

        # ── 标题 ──
        title_row = QtWidgets.QHBoxLayout()
        title_row.setSpacing(6)
        icon_lbl = QtWidgets.QLabel("❓")
        icon_lbl.setFixedWidth(18)
        title_row.addWidget(icon_lbl)
        title_lbl = QtWidgets.QLabel("AI needs your input to proceed")
        title_lbl.setObjectName("askQuestionTitle")
        title_lbl.setWordWrap(True)
        title_row.addWidget(title_lbl, 1)
        main_lay.addLayout(title_row)

        # ── 各问题 ──
        for q in questions:
            q_id = q.get("id", "")
            prompt = q.get("prompt", "")
            options = q.get("options", [])
            allow_multiple = q.get("allow_multiple", False)
            allow_free_text = q.get("allow_free_text", False)

            # 问题分隔线
            sep = QtWidgets.QFrame()
            sep.setFrameShape(QtWidgets.QFrame.HLine)
            sep.setObjectName("askQuestionSep")
            main_lay.addWidget(sep)

            # 问题文本
            q_lbl = QtWidgets.QLabel(f"{q_id.upper()}: {prompt}")
            q_lbl.setObjectName("askQuestionPrompt")
            q_lbl.setWordWrap(True)
            main_lay.addWidget(q_lbl)

            # 选项
            btn_group = None
            buttons = []
            if not allow_multiple:
                btn_group = QtWidgets.QButtonGroup(self)
                btn_group.setExclusive(True)

            for opt in options:
                opt_id = opt.get("id", "")
                opt_label = opt.get("label", "")
                if allow_multiple:
                    btn = QtWidgets.QCheckBox(opt_label)
                else:
                    btn = QtWidgets.QRadioButton(opt_label)
                    btn_group.addButton(btn)
                btn.setObjectName("askQuestionOption")
                btn.setProperty("opt_id", opt_id)
                main_lay.addWidget(btn)
                buttons.append(btn)

            # 自由文本输入
            free_text = None
            if allow_free_text:
                free_text = QtWidgets.QLineEdit()
                free_text.setObjectName("askQuestionFreeText")
                free_text.setPlaceholderText("Or type your answer here...")
                main_lay.addWidget(free_text)

            self._widgets[q_id] = {
                "buttons": buttons,
                "group": btn_group,
                "free_text": free_text,
                "allow_multiple": allow_multiple,
            }

        # ── 按钮行 ──
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setContentsMargins(0, 6, 0, 0)
        btn_row.addStretch()

        self._btn_cancel = QtWidgets.QPushButton("Skip")
        self._btn_cancel.setObjectName("askQuestionBtnCancel")
        self._btn_cancel.setCursor(QtCore.Qt.PointingHandCursor)
        self._btn_cancel.setFixedHeight(28)
        self._btn_cancel.setMinimumWidth(60)
        self._btn_cancel.clicked.connect(self._do_cancel)
        btn_row.addWidget(self._btn_cancel)

        self._btn_submit = QtWidgets.QPushButton("Submit Answer")
        self._btn_submit.setObjectName("askQuestionBtnSubmit")
        self._btn_submit.setCursor(QtCore.Qt.PointingHandCursor)
        self._btn_submit.setFixedHeight(28)
        self._btn_submit.setMinimumWidth(100)
        self._btn_submit.clicked.connect(self._do_submit)
        btn_row.addWidget(self._btn_submit)

        main_lay.addLayout(btn_row)

    def _collect_answers(self) -> dict:
        """收集用户的回答"""
        answers = {}
        for q_id, w_info in self._widgets.items():
            selected = []
            for btn in w_info["buttons"]:
                if btn.isChecked():
                    selected.append(btn.property("opt_id"))
            # 自由文本
            free_text = w_info.get("free_text")
            if free_text and free_text.text().strip():
                selected.append(f"__free_text__:{free_text.text().strip()}")
            answers[q_id] = selected
        return answers

    def _do_submit(self):
        if self._answered:
            return
        self._answered = True
        answers = self._collect_answers()
        self._btn_submit.setEnabled(False)
        self._btn_cancel.setEnabled(False)
        self._btn_submit.setText("✓ Submitted")
        self.answered.emit(answers)

    def _do_cancel(self):
        if self._answered:
            return
        self._answered = True
        self._btn_submit.setEnabled(False)
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.setText("Skipped")
        self.cancelled.emit()


# ============================================================
# Markdown 解析器（专业版）
# ============================================================

class SimpleMarkdown:
    """将 Markdown 转换为 Qt Rich Text HTML（增强版）

    支持特性：
    - 标题 (# ~ ####)
    - 粗体 / 斜体 / 删除线 / 行内代码
    - 无序列表 / 有序列表 / 任务列表 / 嵌套列表
    - 引用块（多行合并，支持渐变背景）
    - 表格（居中 / 左对齐 / 右对齐）
    - 水平分割线
    - 链接 [text](url) / 自动 URL 检测
    - 图片 ![alt](url)
    - 脚注 [^id] / [^id]: ...
    - 转义字符 \\* \\` 等
    - 围栏代码块（交给 CodeBlockWidget）
    """

    _CODE_BLOCK_RE = re.compile(r'```(\w*)\n(.*?)```', re.DOTALL)
    _TABLE_SEP_RE = re.compile(r'^\|?\s*[-:]+[-| :]*$')  # 表头分割行
    # 自动检测裸 URL
    _AUTO_URL_RE = re.compile(
        r'(?<!["\w/=])(?<!\]\()(?<!\[)'       # 不在引号、字母、=、](、[ 之后
        r'(https?://[^\s<>\)\]\"\'`]+)'        # URL 本体
    )
    # 脚注引用
    _FOOTNOTE_REF_RE = re.compile(r'\[\^(\w+)\](?!:)')
    # 脚注定义
    _FOOTNOTE_DEF_RE = re.compile(r'^\[\^(\w+)\]:\s*(.*)')
    # 图片语法
    _IMAGE_RE = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')
    # 列表缩进检测
    _LIST_ITEM_RE = re.compile(r'^(\s*)([-*]|\d+\.)\s+(.*)')
    # 任务列表
    _TASK_ITEM_RE = re.compile(r'^(\s*)[-*]\s+\[([ xX])\]\s+(.*)')

    # -------- 公共接口 --------

    @classmethod
    def parse_segments(cls, text: str) -> list:
        """将文本拆分为 ('text', html), ('code', lang, raw_code), ('image', url, alt) 段落"""
        segments: list = []
        last = 0
        for m in cls._CODE_BLOCK_RE.finditer(text):
            before = text[last:m.start()]
            if before.strip():
                cls._parse_text_with_images(before, segments)
            segments.append(('code', m.group(1) or '', m.group(2).rstrip()))
            last = m.end()
        after = text[last:]
        if after.strip():
            cls._parse_text_with_images(after, segments)
        if not segments and text.strip():
            cls._parse_text_with_images(text, segments)
        return segments

    @classmethod
    def _parse_text_with_images(cls, text: str, segments: list):
        """将文本段落进一步拆分出独立的 image segment
        
        只有独占一行的 ![alt](url) 才作为独立 image segment，
        行内的图片语法仍按行内格式处理。
        """
        lines = text.split('\n')
        buf_lines: list = []

        def _flush_buf():
            if buf_lines:
                joined = '\n'.join(buf_lines)
                if joined.strip():
                    segments.append(('text', cls._text_to_html(joined)))
                buf_lines.clear()

        for line in lines:
            stripped = line.strip()
            img_match = cls._IMAGE_RE.fullmatch(stripped)
            if img_match:
                _flush_buf()
                segments.append(('image', img_match.group(2), img_match.group(1)))
            else:
                buf_lines.append(line)
        _flush_buf()

    @classmethod
    def has_rich_content(cls, text: str) -> bool:
        """判断文本是否包含 Markdown 格式"""
        if '```' in text:
            return True
        if re.search(r'^#{1,4}\s', text, re.MULTILINE):
            return True
        if '**' in text or '`' in text:
            return True
        if re.search(r'^[-*]\s', text, re.MULTILINE):
            return True
        if re.search(r'^\d+\.\s', text, re.MULTILINE):
            return True
        if '|' in text and re.search(r'^\|.+\|', text, re.MULTILINE):
            return True
        if cls._IMAGE_RE.search(text):
            return True
        if cls._FOOTNOTE_REF_RE.search(text):
            return True
        return False

    # -------- 块级解析 --------

    @classmethod
    def _get_indent(cls, line: str) -> int:
        """返回行的缩进空格数"""
        return len(line) - len(line.lstrip())

    @classmethod
    def _text_to_html(cls, text: str) -> str:
        lines = text.split('\n')
        out: list = []
        i = 0
        n = len(lines)

        # 嵌套列表状态栈: [(tag, indent_level), ...]
        list_stack: list = []
        # 引用块缓冲
        quote_buf: list = []
        # 脚注定义收集
        footnotes: dict = {}

        # 第一遍：收集脚注定义
        remaining_lines: list = []
        for line in lines:
            fn_match = cls._FOOTNOTE_DEF_RE.match(line.strip())
            if fn_match:
                footnotes[fn_match.group(1)] = fn_match.group(2)
            else:
                remaining_lines.append(line)
        lines = remaining_lines
        n = len(lines)

        def _flush_all_lists():
            while list_stack:
                _, ltag = list_stack.pop()
                out.append(f'</{ltag}>')

        def _flush_lists_to_indent(target_indent: int):
            """关闭所有缩进大于 target_indent 的列表层级"""
            while list_stack and list_stack[-1][0] > target_indent:
                _, ltag = list_stack.pop()
                out.append(f'</{ltag}>')

        def _flush_quote():
            nonlocal quote_buf
            if quote_buf:
                q_html = '<br>'.join(cls._inline(q, footnotes) for q in quote_buf)
                out.append(
                    f'<div style="border-left:2px solid rgba(148,163,184,50);padding:8px 14px;'
                    f'margin:8px 0;'
                    f'background:transparent;'
                    f'color:#cbd5e1;border-radius:0 6px 6px 0;'
                    f'line-height:1.6;">{q_html}</div>'
                )
                quote_buf = []

        while i < n:
            raw_line = lines[i]
            s = raw_line.strip()

            # ---- empty line ----
            if not s:
                _flush_quote()
                _flush_all_lists()
                out.append('<div style="height:4px;"></div>')
                i += 1
                continue

            # ---- horizontal rule ----
            if re.match(r'^[-*_]{3,}\s*$', s):
                _flush_quote()
                _flush_all_lists()
                out.append(
                    '<hr style="border:none;border-top:1px solid rgba(255,255,255,8);margin:16px 0;width:100%;">'
                )
                i += 1
                continue

            # ---- table ----
            if '|' in s and i + 1 < n and cls._TABLE_SEP_RE.match(lines[i + 1].strip()):
                _flush_quote()
                _flush_all_lists()
                table_html = cls._parse_table(lines, i)
                if table_html:
                    out.append(table_html[0])
                    i = table_html[1]
                    continue

            # ---- headers ----
            header_match = re.match(r'^(#{1,4})\s+(.+)', s)
            if header_match:
                _flush_quote()
                _flush_all_lists()
                lvl = len(header_match.group(1))
                content = header_match.group(2)
                styles = {
                    1: ('1.5em', '#f1f5f9', '700', '18px 0 8px 0', 'border-bottom:1px solid rgba(255,255,255,12);padding-bottom:8px;letter-spacing:0.3px;'),
                    2: ('1.3em', '#e2e8f0', '600', '16px 0 6px 0', 'letter-spacing:0.2px;'),
                    3: ('1.1em', '#cbd5e1', '600', '12px 0 4px 0', ''),
                    4: ('1.0em', '#94a3b8', '600', '10px 0 3px 0', ''),
                }
                sz, clr, wt, mg, extra = styles[lvl]
                out.append(
                    f'<p style="font-size:{sz};font-weight:{wt};'
                    f'color:{clr};margin:{mg};{extra}">'
                    f'{cls._inline(content, footnotes)}</p>'
                )
                i += 1
                continue

            # ---- blockquote (合并连续行) ----
            if s.startswith('> '):
                _flush_all_lists()
                quote_buf.append(s[2:])
                i += 1
                continue
            elif s.startswith('>'):
                _flush_all_lists()
                quote_buf.append(s[1:].lstrip())
                i += 1
                continue
            else:
                _flush_quote()

            # ---- task list (with nesting support) ----
            task_match = cls._TASK_ITEM_RE.match(raw_line)
            if task_match:
                indent = len(task_match.group(1))
                _flush_lists_to_indent(indent)
                if not list_stack or list_stack[-1][0] < indent:
                    out.append(
                        '<ul style="margin:2px 0;padding-left:4px;list-style:none;">'
                    )
                    list_stack.append((indent, 'ul'))
                checked = task_match.group(2) in ('x', 'X')
                box = (
                    '<span style="color:#10b981;font-weight:bold;margin-right:6px;">✓</span>'
                    if checked else
                    '<span style="color:#64748b;margin-right:6px;">○</span>'
                )
                text_style = 'color:#64748b;text-decoration:line-through;' if checked else ''
                out.append(
                    f'<li style="margin:4px 0;line-height:1.6;{text_style}">'
                    f'{box}{cls._inline(task_match.group(3), footnotes)}</li>'
                )
                i += 1
                continue

            # ---- unordered / ordered list (with nesting) ----
            list_match = cls._LIST_ITEM_RE.match(raw_line)
            if list_match:
                indent = len(list_match.group(1))
                marker = list_match.group(2)
                item_text = list_match.group(3)
                is_ordered = marker[-1] == '.'
                new_tag = 'ol' if is_ordered else 'ul'

                _flush_lists_to_indent(indent)

                if not list_stack or list_stack[-1][0] < indent:
                    # 开启新的嵌套层级
                    if is_ordered:
                        out.append(
                            '<ol style="margin:4px 0;padding-left:22px;color:#94a3b8;">'
                        )
                    else:
                        out.append(
                            '<ul style="margin:4px 0;padding-left:22px;'
                            'list-style-type:disc;color:#94a3b8;">'
                        )
                    list_stack.append((indent, new_tag))
                elif list_stack[-1][1] != new_tag:
                    # 同层级但类型切换
                    old_indent, old_tag = list_stack.pop()
                    out.append(f'</{old_tag}>')
                    if is_ordered:
                        out.append(
                            '<ol style="margin:4px 0;padding-left:22px;color:#94a3b8;">'
                        )
                    else:
                        out.append(
                            '<ul style="margin:4px 0;padding-left:22px;'
                            'list-style-type:disc;color:#94a3b8;">'
                        )
                    list_stack.append((indent, new_tag))

                out.append(
                    f'<li style="margin:4px 0;line-height:1.6;color:{CursorTheme.TEXT_PRIMARY};">'
                    f'{cls._inline(item_text, footnotes)}</li>'
                )
                i += 1
                continue

            # ---- normal paragraph ----
            _flush_all_lists()
            out.append(
                f'<p style="margin:4px 0;line-height:1.6;color:#e2e8f0;">'
                f'{cls._inline(s, footnotes)}</p>'
            )
            i += 1

        _flush_quote()
        _flush_all_lists()

        # 渲染脚注定义区域（如果有）
        if footnotes:
            out.append(
                '<hr style="border:none;border-top:1px solid rgba(255,255,255,8);'
                'margin:12px 0 6px 0;width:40%;">'
            )
            for fn_id, fn_text in footnotes.items():
                out.append(
                    f'<p style="margin:2px 0;font-size:0.85em;color:{CursorTheme.TEXT_SECONDARY};'
                    f'line-height:1.4;">'
                    f'<sup style="color:#60a5fa;">[{html.escape(fn_id)}]</sup> '
                    f'{cls._inline(fn_text, footnotes)}</p>'
                )

        return '\n'.join(out)

    # -------- 表格解析 --------

    @classmethod
    def _parse_table(cls, lines: list, start: int) -> tuple:
        """解析 Markdown 表格，返回 (html, next_line_index)"""
        header_line = lines[start].strip()
        if start + 1 >= len(lines):
            return None
        sep_line = lines[start + 1].strip()

        # 解析对齐方式
        sep_cells = [c.strip() for c in sep_line.strip('|').split('|')]
        aligns = []
        for c in sep_cells:
            c = c.strip()
            if c.startswith(':') and c.endswith(':'):
                aligns.append('center')
            elif c.endswith(':'):
                aligns.append('right')
            else:
                aligns.append('left')

        def _parse_row(line: str) -> list:
            line = line.strip()
            if line.startswith('|'):
                line = line[1:]
            if line.endswith('|'):
                line = line[:-1]
            return [c.strip() for c in line.split('|')]

        # 表头
        headers = _parse_row(header_line)

        # 表体
        rows = []
        j = start + 2
        while j < len(lines):
            row_s = lines[j].strip()
            if not row_s or '|' not in row_s:
                break
            rows.append(_parse_row(row_s))
            j += 1

        # 生成 HTML（现代极简：无外边框、无斑马纹、仅底线分隔）
        tbl = [
            '<table style="border-collapse:collapse;'
            'margin:10px 0;width:100%;font-size:0.92em;">'
        ]

        # thead
        tbl.append('<tr>')
        for ci, h in enumerate(headers):
            align = aligns[ci] if ci < len(aligns) else 'left'
            tbl.append(
                f'<th style="border-bottom:2px solid rgba(255,255,255,12);'
                f'padding:7px 14px;'
                f'background:transparent;color:#e2e8f0;font-weight:600;'
                f'text-align:{align};font-size:0.95em;">{cls._inline(h)}</th>'
            )
        tbl.append('</tr>')

        # tbody — 统一背景，仅底线分隔
        for ri, row in enumerate(rows):
            tbl.append('<tr>')
            for ci, cell in enumerate(row):
                align = aligns[ci] if ci < len(aligns) else 'left'
                border_bottom = (
                    'border-bottom:1px solid rgba(255,255,255,5);'
                    if ri < len(rows) - 1 else ''
                )
                tbl.append(
                    f'<td style="{border_bottom}padding:7px 14px;'
                    f'background:transparent;color:{CursorTheme.TEXT_PRIMARY};'
                    f'text-align:{align};line-height:1.5;">{cls._inline(cell)}</td>'
                )
            tbl.append('</tr>')

        tbl.append('</table>')
        return ('\n'.join(tbl), j)

    # -------- 行内解析 --------

    @classmethod
    def _inline(cls, text: str, footnotes: dict = None) -> str:
        """行内格式: **粗体**, *斜体*, ~~删除线~~, `代码`, [链接](url),
        ![图片](url), [^脚注], 自动URL, 转义字符, 节点路径"""
        # 1. 处理转义字符：先将 \X 替换为占位符，最后再还原
        _ESC_MAP = {}
        _esc_counter = [0]

        def _replace_escape(m):
            key = f'\x00ESC{_esc_counter[0]}\x00'
            _ESC_MAP[key] = m.group(1)  # 被转义的字符
            _esc_counter[0] += 1
            return key

        text = re.sub(r'\\([\\`*_~\[\]()#>!|])', _replace_escape, text)

        # 2. HTML 转义
        text = html.escape(text)

        # 3. 行内图片 ![alt](url)（行内级别，不独占行）
        text = re.sub(
            r'!\[([^\]]*)\]\(([^)]+)\)',
            r'<img src="\2" alt="\1" style="max-width:100%;max-height:200px;'
            r'border-radius:4px;margin:2px 0;vertical-align:middle;">',
            text,
        )

        # 4. 链接 [text](url)
        text = re.sub(
            r'\[([^\]]+?)\]\(([^)]+?)\)',
            r'<a href="\2" style="color:#818cf8;text-decoration:none;'
            r'border-bottom:1px solid rgba(129,140,248,0.3);">\1</a>',
            text,
        )

        # 5. 脚注引用 [^id]
        if footnotes:
            def _fn_ref(m):
                fid = m.group(1)
                if fid in footnotes:
                    return (
                        f'<sup style="color:#818cf8;cursor:pointer;">'
                        f'<a href="#fn-{html.escape(fid)}" style="color:#818cf8;'
                        f'text-decoration:none;">[{html.escape(fid)}]</a></sup>'
                    )
                return m.group(0)
            text = cls._FOOTNOTE_REF_RE.sub(_fn_ref, text)

        # 6. 粗体
        text = re.sub(r'\*\*(.+?)\*\*', r'<b style="color:#f1f5f9;font-weight:600;">\1</b>', text)
        # 7. 删除线
        text = re.sub(r'~~(.+?)~~', r'<s style="color:#64748b;">\1</s>', text)
        # 8. 斜体
        text = re.sub(r'(?<!\*)\*([^*]+?)\*(?!\*)', r'<i style="color:#cbd5e1;">\1</i>', text)
        # 9. 行内代码
        text = re.sub(
            r'`([^`]+?)`',
            r'<code style="background:rgba(255,255,255,8);padding:2px 7px;border-radius:5px;'
            r'font-family:Consolas,Monaco,monospace;color:#c9d1d9;'
            r'font-size:0.88em;border:1px solid rgba(255,255,255,5);">\1</code>',
            text,
        )
        # 10. 自动 URL 检测（裸链接）
        text = cls._AUTO_URL_RE.sub(
            r'<a href="\1" style="color:#818cf8;text-decoration:none;">\1</a>',
            text,
        )
        # 11. Houdini 节点路径 → 可点击链接
        text = _linkify_node_paths(text)

        # 12. 还原转义字符
        for key, char in _ESC_MAP.items():
            text = text.replace(key, html.escape(char))

        return text


# ============================================================
# 语法高亮器
# ============================================================

class SyntaxHighlighter:
    """代码语法高亮 — 基于 token 的着色
    
    支持语言: VEX, Python, JSON, YAML, Bash/Shell, JavaScript/TypeScript,
    HScript, GLSL, Markdown
    """

    COL = {
        'keyword':  '#569CD6',
        'type':     '#4EC9B0',
        'builtin':  '#DCDCAA',
        'string':   '#CE9178',
        'comment':  '#6A9955',
        'number':   '#B5CEA8',
        'attr':     '#9CDCFE',
        'key':      '#9CDCFE',    # JSON / YAML key
        'constant': '#569CD6',    # true / false / null
        'operator': '#D4D4D4',    # operators
        'directive': '#C586C0',   # preprocessor / shebang
    }

    # ---- VEX ----
    VEX_KW = frozenset(
        'if else for while return break continue foreach do switch case default'.split()
    )
    VEX_TY = frozenset(
        'float vector vector2 vector4 int string void matrix matrix3 dict'.split()
    )
    VEX_BI = frozenset(
        'set getattrib setattrib point prim detail chf chi chs chv chramp '
        'length normalize fit fit01 rand noise sin cos pow sqrt abs min max '
        'clamp lerp smooth cross dot addpoint addprim addvertex removeprim '
        'removepoint npoints nprims printf sprintf push pop append resize len '
        'find sort sample_direction_uniform pcopen pcfilter nearpoint '
        'nearpoints xyzdist primuv'.split()
    )

    # ---- Python ----
    PY_KW = frozenset(
        'import from def class return if else elif for while try except finally '
        'with as in not and or is None True False pass break continue raise '
        'yield lambda global nonlocal del assert'.split()
    )
    PY_BI = frozenset(
        'print len range str int float list dict tuple set type isinstance '
        'enumerate zip map filter sorted reversed open super property '
        'staticmethod classmethod hasattr getattr setattr'.split()
    )

    # ---- JavaScript / TypeScript ----
    JS_KW = frozenset(
        'var let const function return if else for while do switch case default '
        'break continue new this typeof instanceof void delete throw try catch '
        'finally class extends import export from as async await yield of in '
        'static get set super'.split()
    )
    JS_TY = frozenset(
        'string number boolean any void never unknown object symbol bigint '
        'undefined null Array Promise Map Set Record Partial Required Readonly '
        'interface type enum namespace'.split()
    )
    JS_BI = frozenset(
        'console log warn error parseInt parseFloat isNaN isFinite '
        'JSON Math Date RegExp Object Array String Number Boolean '
        'setTimeout setInterval clearTimeout clearInterval '
        'fetch require module exports process'.split()
    )

    # ---- Bash / Shell ----
    BASH_KW = frozenset(
        'if then else elif fi for do done while until case esac in '
        'function return exit break continue select'.split()
    )
    BASH_BI = frozenset(
        'echo printf cd ls cp mv rm mkdir rmdir cat grep sed awk find '
        'chmod chown tar gzip gunzip curl wget git pip python node npm '
        'export source alias unalias set unset read eval exec test '
        'true false shift'.split()
    )

    # ---- HScript ----
    HSCRIPT_KW = frozenset(
        'if else endif for foreach end set setenv echo opcf opcd '
        'opparm oprm opadd opsave opload chadd chkey chls optype '
        'opflag opname opset oppane opproperty'.split()
    )

    # ---- GLSL ----
    GLSL_KW = frozenset(
        'if else for while do return break continue discard switch case default '
        'struct void const in out inout uniform varying attribute '
        'layout precision highp mediump lowp flat smooth noperspective '
        'centroid sample'.split()
    )
    GLSL_TY = frozenset(
        'float vec2 vec3 vec4 int ivec2 ivec3 ivec4 uint uvec2 uvec3 uvec4 '
        'bool bvec2 bvec3 bvec4 mat2 mat3 mat4 mat2x2 mat2x3 mat2x4 '
        'mat3x2 mat3x3 mat3x4 mat4x2 mat4x3 mat4x4 '
        'sampler1D sampler2D sampler3D samplerCube sampler2DShadow'.split()
    )
    GLSL_BI = frozenset(
        'texture texture2D textureCube normalize length distance dot cross '
        'reflect refract mix clamp smoothstep step min max abs sign floor '
        'ceil fract mod pow exp log sqrt inversesqrt sin cos tan asin acos atan '
        'radians degrees dFdx dFdy fwidth'.split()
    )

    @classmethod
    def highlight_vex(cls, code: str) -> str:
        return cls._tokenize(code, cls.VEX_KW, cls.VEX_TY, cls.VEX_BI,
                              '//', ('/*', '*/'), '@')

    @classmethod
    def highlight_python(cls, code: str) -> str:
        return cls._tokenize(code, cls.PY_KW, frozenset(), cls.PY_BI,
                              '#', None, None)

    @classmethod
    def highlight_javascript(cls, code: str) -> str:
        return cls._tokenize(code, cls.JS_KW, cls.JS_TY, cls.JS_BI,
                              '//', ('/*', '*/'), None)

    @classmethod
    def highlight_bash(cls, code: str) -> str:
        return cls._tokenize(code, cls.BASH_KW, frozenset(), cls.BASH_BI,
                              '#', None, '$')

    @classmethod
    def highlight_hscript(cls, code: str) -> str:
        return cls._tokenize(code, cls.HSCRIPT_KW, frozenset(), frozenset(),
                              '#', None, '$')

    @classmethod
    def highlight_glsl(cls, code: str) -> str:
        return cls._tokenize(code, cls.GLSL_KW, cls.GLSL_TY, cls.GLSL_BI,
                              '//', ('/*', '*/'), None)

    @classmethod
    def highlight_json(cls, code: str) -> str:
        """JSON 高亮：key 和 value 区分着色"""
        parts: list = []
        i, n = 0, len(code)
        # 简单状态：上一个非空白字符是 { 或 , 或行首 → 下一个字符串是 key
        expect_key = True

        while i < n:
            c = code[i]

            # 空白
            if c in (' ', '\t', '\n', '\r'):
                parts.append(c)
                if c == '\n':
                    expect_key = True
                i += 1
                continue

            # 字符串
            if c == '"':
                j = i + 1
                while j < n and code[j] != '"':
                    if code[j] == '\\':
                        j += 1
                    j += 1
                if j < n:
                    j += 1
                s = code[i:j]
                # 判断是 key 还是 value
                # key 后面（跳过空白）应该是 :
                rest = code[j:].lstrip()
                if expect_key and rest.startswith(':'):
                    parts.append(cls._span('key', s))
                    expect_key = False
                else:
                    parts.append(cls._span('string', s))
                i = j
                continue

            # 冒号
            if c == ':':
                parts.append(html.escape(c))
                expect_key = False
                i += 1
                continue

            # 逗号
            if c == ',':
                parts.append(html.escape(c))
                expect_key = True
                i += 1
                continue

            # 大括号 / 方括号
            if c in ('{', '['):
                parts.append(html.escape(c))
                expect_key = True
                i += 1
                continue
            if c in ('}', ']'):
                parts.append(html.escape(c))
                i += 1
                continue

            # 数字
            if c.isdigit() or (c == '-' and i + 1 < n and code[i + 1].isdigit()):
                j = i + 1 if c == '-' else i
                while j < n and (code[j].isdigit() or code[j] in ('.', 'e', 'E', '+', '-')):
                    j += 1
                parts.append(cls._span('number', code[i:j]))
                i = j
                continue

            # true / false / null
            for kw in ('true', 'false', 'null'):
                if code[i:i + len(kw)] == kw:
                    parts.append(cls._span('constant', kw))
                    i += len(kw)
                    break
            else:
                parts.append(html.escape(c))
                i += 1

        return ''.join(parts)

    @classmethod
    def highlight_yaml(cls, code: str) -> str:
        """YAML 高亮：key-value 区分、注释、列表标记"""
        parts: list = []
        lines = code.split('\n')
        for li, line in enumerate(lines):
            if li > 0:
                parts.append('\n')

            stripped = line.lstrip()

            # 注释
            if stripped.startswith('#'):
                parts.append(cls._span('comment', line))
                continue

            # 文档分隔符 ---
            if stripped in ('---', '...'):
                parts.append(cls._span('directive', line))
                continue

            # 列表项 - xxx: value
            indent = line[:len(line) - len(stripped)]
            if indent:
                parts.append(html.escape(indent))

            # 检查 key: value 格式
            colon_pos = stripped.find(':')
            if colon_pos > 0 and (colon_pos + 1 >= len(stripped) or stripped[colon_pos + 1] == ' '):
                # 处理列表标记
                key_part = stripped[:colon_pos]
                if key_part.startswith('- '):
                    parts.append(html.escape('- '))
                    key_part = key_part[2:]

                parts.append(cls._span('key', key_part))
                parts.append(html.escape(':'))

                value_part = stripped[colon_pos + 1:]
                if value_part:
                    # 检查 value 中的注释
                    comment_pos = value_part.find(' #')
                    if comment_pos >= 0:
                        val = value_part[:comment_pos]
                        comment = value_part[comment_pos:]
                        parts.append(cls._highlight_yaml_value(val))
                        parts.append(cls._span('comment', comment))
                    else:
                        parts.append(cls._highlight_yaml_value(value_part))
            else:
                # 列表项或纯值
                if stripped.startswith('- '):
                    parts.append(html.escape('- '))
                    parts.append(cls._highlight_yaml_value(stripped[2:]))
                else:
                    parts.append(html.escape(stripped))

        return ''.join(parts)

    @classmethod
    def _highlight_yaml_value(cls, value: str) -> str:
        """高亮 YAML 值"""
        v = value.strip()
        if not v:
            return html.escape(value)

        # 保留前导空格
        leading = value[:len(value) - len(value.lstrip())]
        result = html.escape(leading) if leading else ''

        # 字符串（带引号）
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            return result + cls._span('string', v)
        # 布尔 / null
        if v.lower() in ('true', 'false', 'yes', 'no', 'on', 'off', 'null', '~'):
            return result + cls._span('constant', v)
        # 数字
        try:
            float(v)
            return result + cls._span('number', v)
        except ValueError:
            pass
        return result + html.escape(v)

    @classmethod
    def _tokenize(cls, code, keywords, types, builtins,
                   comment_single, comment_multi, attr_prefix):
        parts: list = []
        i, n = 0, len(code)
        while i < n:
            c = code[i]
            # --- single-line comment ---
            if comment_single and code[i:i + len(comment_single)] == comment_single:
                end = code.find('\n', i)
                if end == -1:
                    end = n
                parts.append(cls._span('comment', code[i:end]))
                i = end
                continue
            # --- multi-line comment ---
            if comment_multi and code[i:i + len(comment_multi[0])] == comment_multi[0]:
                end = code.find(comment_multi[1], i + len(comment_multi[0]))
                end = n if end == -1 else end + len(comment_multi[1])
                parts.append(cls._span('comment', code[i:end]))
                i = end
                continue
            # --- strings ---
            if c in ('"', "'", '`'):
                # Template literals (JS backtick strings)
                if c == '`':
                    j = i + 1
                    while j < n and code[j] != '`':
                        if code[j] == '\\':
                            j += 1
                        j += 1
                    if j < n:
                        j += 1
                    parts.append(cls._span('string', code[i:j]))
                    i = j
                    continue
                triple = code[i:i + 3]
                if triple in ('"""', "'''"):
                    end = code.find(triple, i + 3)
                    end = n if end == -1 else end + 3
                    parts.append(cls._span('string', code[i:end]))
                    i = end
                    continue
                j = i + 1
                while j < n and code[j] != c and code[j] != '\n':
                    if code[j] == '\\':
                        j += 1
                    j += 1
                if j < n and code[j] == c:
                    j += 1
                parts.append(cls._span('string', code[i:j]))
                i = j
                continue
            # --- attribute prefix (@P, $VAR etc.) ---
            if (attr_prefix and c == attr_prefix
                    and i + 1 < n and (code[i + 1].isalpha() or code[i + 1] == '_')):
                j = i + 1
                while j < n and (code[j].isalnum() or code[j] in ('_', '.')):
                    j += 1
                parts.append(cls._span('attr', code[i:j]))
                i = j
                continue
            # --- preprocessor directive (#include, #define) ---
            if c == '#' and (not comment_single or comment_single != '#'):
                if i == 0 or code[i - 1] == '\n':
                    end = code.find('\n', i)
                    if end == -1:
                        end = n
                    parts.append(cls._span('directive', code[i:end]))
                    i = end
                    continue
            # --- identifier / keyword ---
            if c.isalpha() or c == '_':
                j = i
                while j < n and (code[j].isalnum() or code[j] == '_'):
                    j += 1
                word = code[i:j]
                if word in keywords:
                    parts.append(cls._span('keyword', word))
                elif word in types:
                    parts.append(cls._span('type', word))
                elif word in builtins:
                    parts.append(cls._span('builtin', word))
                else:
                    parts.append(html.escape(word))
                i = j
                continue
            # --- number (including hex 0x...) ---
            if c.isdigit() or (c == '.' and i + 1 < n and code[i + 1].isdigit()):
                j = i
                if c == '0' and j + 1 < n and code[j + 1] in ('x', 'X'):
                    j += 2
                    while j < n and (code[j].isdigit() or code[j] in 'abcdefABCDEF'):
                        j += 1
                else:
                    while j < n and (code[j].isdigit() or code[j] in ('.', 'e', 'E', '+', '-', 'f')):
                        if code[j] in ('+', '-') and j > 0 and code[j - 1] not in ('e', 'E'):
                            break
                        j += 1
                parts.append(cls._span('number', code[i:j]))
                i = j
                continue
            parts.append(html.escape(c))
            i += 1
        return ''.join(parts)

    @classmethod
    def _span(cls, tok_type: str, text: str) -> str:
        color = cls.COL.get(tok_type, '#D4D4D4')
        return f'<span style="color:{color};">{html.escape(text)}</span>'


# ============================================================
# 可折叠 Shell 输出区域（Python Shell / System Shell 共用）
# ============================================================

class _CollapsibleShellOutput(QtWidgets.QWidget):
    """可折叠的 Shell 输出区域
    
    - 默认折叠：只显示 4 行，滚轮穿透到父窗口
    - 展开后：显示全部内容，滚轮可滚动内联区域
    """

    _COLLAPSED_LINES = 4
    _MAX_EXPANDED_H = 400  # 展开后最大高度

    def __init__(self, content_html: str, bg_color: str = "#141428",
                 parent=None):
        super().__init__(parent)
        self._collapsed = True
        self._full_h = 0
        self._collapsed_h = 0
        # 根据背景色推断 variant（python / system）
        self._variant = "system" if bg_color == "#141414" else "python"

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # ── QTextEdit（输出内容）──
        self._text = QtWidgets.QTextEdit()
        self._text.setReadOnly(True)
        self._text.setLineWrapMode(QtWidgets.QTextEdit.NoWrap)
        self._text.setObjectName("shellOutput")
        self._text.setProperty("variant", self._variant)
        self._text.setHtml(
            f'<pre style="margin:0;white-space:pre;font-family:Consolas,Monaco,monospace;'
            f'font-size:12px;">{content_html}</pre>'
        )
        lay.addWidget(self._text)

        # 计算尺寸
        doc = self._text.document()
        doc.setDocumentMargin(4)
        self._full_h = int(doc.size().height()) + 16

        # 计算折叠高度（4 行）
        fm = self._text.fontMetrics()
        line_h = fm.lineSpacing() if fm.lineSpacing() > 0 else 17
        self._collapsed_h = self._COLLAPSED_LINES * line_h + 16  # 16 = padding

        # 判断是否需要折叠（内容不足 4 行则不折叠）
        self._needs_collapse = self._full_h > self._collapsed_h + line_h

        if self._needs_collapse:
            # 初始折叠状态
            self._text.setFixedHeight(self._collapsed_h)
            self._text.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            self._text.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            # 安装事件过滤器拦截滚轮
            self._text.viewport().installEventFilter(self)

            # 计算总行数
            total_lines = content_html.count('<br>') + content_html.count('\n') + 1
            remaining = max(0, total_lines - self._COLLAPSED_LINES)

            # ── 展开/收起 toggle bar ──
            self._toggle = QtWidgets.QLabel(
                f"  ▼ 展开 ({remaining} 更多行)"
            )
            self._toggle.setCursor(QtCore.Qt.PointingHandCursor)
            self._toggle.setObjectName("shellToggle")
            self._toggle.setProperty("variant", self._variant)
            self._toggle.mousePressEvent = lambda e: self._toggle_collapse()
            self._toggle.setFixedHeight(22)
            lay.addWidget(self._toggle)
            self._remaining = remaining
        else:
            # 内容较短，不需要折叠，直接显示全部
            h = min(self._full_h, self._MAX_EXPANDED_H)
            self._text.setFixedHeight(h)
            self._text.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            self._text.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)

    def _toggle_collapse(self):
        """切换折叠/展开"""
        self._collapsed = not self._collapsed
        if self._collapsed:
            # 折叠
            self._text.setFixedHeight(self._collapsed_h)
            self._text.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            self._text.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            self._text.verticalScrollBar().setValue(0)
            self._toggle.setText(f"  ▼ 展开 ({self._remaining} 更多行)")
        else:
            # 展开
            h = min(self._full_h, self._MAX_EXPANDED_H)
            self._text.setFixedHeight(h)
            if self._full_h > self._MAX_EXPANDED_H:
                self._text.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
            else:
                self._text.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            self._text.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
            self._toggle.setText("  ▲ 收起")

    def eventFilter(self, obj, event):
        """折叠状态下，滚轮事件穿透到父窗口"""
        if (event.type() == QtCore.QEvent.Wheel
                and self._collapsed and self._needs_collapse):
            # 把滚轮事件转发给父 ScrollArea
            parent = self.parent()
            while parent:
                if isinstance(parent, QtWidgets.QScrollArea):
                    QtWidgets.QApplication.sendEvent(parent.viewport(), event)
                    return True
                parent = parent.parent()
            return True  # 即使没找到也吃掉，避免内联滚动
        return super().eventFilter(obj, event)


# ============================================================
# Python Shell 执行窗口
# ============================================================

class PythonShellWidget(QtWidgets.QFrame):
    """Python Shell 执行结果 — 显示代码 + 输出 + 错误"""
    
    def __init__(self, code: str, output: str = "", error: str = "",
                 exec_time: float = 0.0, success: bool = True, parent=None):
        super().__init__(parent)
        self.setObjectName("PythonShellWidget")
        
        self.setProperty("state", "ok" if success else "error")
        
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # ---- header: Python Shell + 执行时间 ----
        header = QtWidgets.QWidget()
        header.setObjectName("pyShellHeader")
        hl = QtWidgets.QHBoxLayout(header)
        hl.setContentsMargins(8, 4, 8, 4)
        hl.setSpacing(6)
        
        title_lbl = QtWidgets.QLabel("PYTHON SHELL")
        title_lbl.setObjectName("pyShellTitle")
        hl.addWidget(title_lbl)
        
        hl.addStretch()
        
        if exec_time > 0:
            time_lbl = QtWidgets.QLabel(f"{exec_time:.2f}s")
            time_lbl.setObjectName("shellTimeLbl")
            hl.addWidget(time_lbl)
        
        status_lbl = QtWidgets.QLabel("ok" if success else "err")
        status_lbl.setObjectName("shellStatusOk" if success else "shellStatusErr")
        hl.addWidget(status_lbl)
        
        layout.addWidget(header)
        
        # ---- 代码区域 ----
        code_widget = QtWidgets.QTextEdit()
        code_widget.setReadOnly(True)
        code_widget.setLineWrapMode(QtWidgets.QTextEdit.NoWrap)
        code_widget.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        code_widget.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        code_widget.setObjectName("shellCodeEdit")
        
        # Python 语法高亮
        highlighted_code = SyntaxHighlighter.highlight_python(code)
        code_widget.setHtml(f'<pre style="margin:0;white-space:pre;">{highlighted_code}</pre>')
        
        # 代码区高度自适应 (最高 200px)
        doc = code_widget.document()
        doc.setDocumentMargin(4)
        code_h = min(int(doc.size().height()) + 16, 200)
        code_widget.setFixedHeight(code_h)
        layout.addWidget(code_widget)
        
        # ---- 输出区域（可折叠）----
        has_output = bool(output and output.strip())
        has_error = bool(error and error.strip())
        
        if has_output or has_error:
            parts = []
            if has_output:
                parts.append(f'<span style="color:{CursorTheme.TEXT_PRIMARY};">'
                             f'{html.escape(output.strip())}</span>')
            if has_error:
                parts.append(f'<span style="color:{CursorTheme.ACCENT_RED};">'
                             f'{html.escape(error.strip())}</span>')
            content_html = '<br>'.join(parts)
            layout.addWidget(_CollapsibleShellOutput(content_html, "#141428", self))
        
        elif not success:
            err_label = QtWidgets.QLabel("执行失败（无详细信息）")
            err_label.setObjectName("shellErrFallback")
            layout.addWidget(err_label)


class SystemShellWidget(QtWidgets.QFrame):
    """System Shell 执行结果 — 显示命令 + stdout/stderr + 退出码"""

    def __init__(self, command: str, output: str = "", error: str = "",
                 exit_code: int = 0, exec_time: float = 0.0,
                 success: bool = True, cwd: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("SystemShellWidget")

        self.setProperty("state", "ok" if success else "error")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ---- header: SHELL + cwd + 执行时间 + 退出码 ----
        header = QtWidgets.QWidget()
        header.setObjectName("sysShellHeader")
        hl = QtWidgets.QHBoxLayout(header)
        hl.setContentsMargins(8, 4, 8, 4)
        hl.setSpacing(6)

        title_lbl = QtWidgets.QLabel("SHELL")
        title_lbl.setObjectName("sysShellTitle")
        hl.addWidget(title_lbl)

        if cwd:
            # 只显示最后两层目录
            parts = cwd.replace('\\', '/').rstrip('/').split('/')
            short_cwd = '/'.join(parts[-2:]) if len(parts) >= 2 else cwd
            cwd_lbl = QtWidgets.QLabel(short_cwd)
            cwd_lbl.setObjectName("shellCwdLbl")
            hl.addWidget(cwd_lbl)

        hl.addStretch()

        if exec_time > 0:
            time_lbl = QtWidgets.QLabel(f"{exec_time:.2f}s")
            time_lbl.setObjectName("shellTimeLbl")
            hl.addWidget(time_lbl)

        code_lbl = QtWidgets.QLabel(f"exit {exit_code}")
        code_lbl.setObjectName("shellStatusOk" if exit_code == 0 else "shellStatusErr")
        hl.addWidget(code_lbl)

        layout.addWidget(header)

        # ---- 命令区域 ----
        cmd_widget = QtWidgets.QTextEdit()
        cmd_widget.setReadOnly(True)
        cmd_widget.setLineWrapMode(QtWidgets.QTextEdit.NoWrap)
        cmd_widget.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        cmd_widget.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        cmd_widget.setObjectName("shellCmdEdit")

        # 命令显示：带 $ 或 > 前缀
        import html as _html
        prefix = "&gt;" if "win" in __import__('sys').platform else "$"
        cmd_html = (
            f'<pre style="margin:0;white-space:pre;">'
            f'<span style="color:{CursorTheme.ACCENT_GREEN};">{prefix}</span> '
            f'{_html.escape(command)}</pre>'
        )
        cmd_widget.setHtml(cmd_html)

        doc = cmd_widget.document()
        doc.setDocumentMargin(4)
        cmd_h = min(int(doc.size().height()) + 16, 80)
        cmd_widget.setFixedHeight(cmd_h)
        layout.addWidget(cmd_widget)

        # ---- 输出区域（可折叠）----
        has_output = bool(output and output.strip())
        has_error = bool(error and error.strip())

        if has_output or has_error:
            parts = []
            if has_output:
                parts.append(f'<span style="color:{CursorTheme.TEXT_PRIMARY};">'
                             f'{_html.escape(output.strip())}</span>')
            if has_error:
                parts.append(f'<span style="color:{CursorTheme.ACCENT_RED};">'
                             f'{_html.escape(error.strip())}</span>')
            content_html = '<br>'.join(parts)
            layout.addWidget(_CollapsibleShellOutput(content_html, "#141414", self))

        elif not success:
            err_label = QtWidgets.QLabel("命令执行失败（无详细信息）")
            err_label.setObjectName("shellErrFallback")
            layout.addWidget(err_label)


# ============================================================
# 代码块组件
# ============================================================

class CodeBlockWidget(QtWidgets.QFrame):
    """代码块 — 语法高亮 + 行号 + 复制 + 折叠 + 创建 Wrangle（VEX 专属）
    
    ★ Phase 6 增强:
    - 大于 5 行时自动显示行号
    - 超过 15 行默认折叠，点击展开
    - 语言标签显示在 header
    """

    createWrangleRequested = QtCore.Signal(str)  # vex_code

    _VEX_INDICATORS = (
        '@P', '@Cd', '@N', '@v', '@ptnum', '@numpt', '@opinput',
        'chf(', 'chi(', 'chs(', 'chv(', 'chramp(',
        'addpoint', 'addprim', 'setattrib', 'getattrib',
        'vector ', 'float ', '#include',
    )

    _COLLAPSE_THRESHOLD = 15   # 超过此行数默认折叠
    _LINE_NUM_THRESHOLD = 5    # 超过此行数显示行号
    _MAX_HEIGHT = 400          # 最大高度

    def __init__(self, code: str, language: str = "", parent=None):
        super().__init__(parent)
        self._code = code
        self._lang = language.lower()
        self._line_count = code.count('\n') + 1
        self._collapsed = self._line_count > self._COLLAPSE_THRESHOLD
        self._show_line_numbers = self._line_count > self._LINE_NUM_THRESHOLD

        self.setObjectName("CodeBlockWidget")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ---- header ----
        header = QtWidgets.QWidget()
        header.setObjectName("codeBlockHeader")
        hl = QtWidgets.QHBoxLayout(header)
        hl.setContentsMargins(8, 3, 4, 3)
        hl.setSpacing(4)

        lang_text = self._lang.upper() or ("VEX" if self._is_vex() else "CODE")
        # 语言标签 + 行数信息
        lang_info = f"{lang_text}"
        if self._line_count > 1:
            lang_info += f"  ({self._line_count} 行)"
        lang_lbl = QtWidgets.QLabel(lang_info)
        lang_lbl.setObjectName("codeBlockLang")
        hl.addWidget(lang_lbl)
        hl.addStretch()

        # 操作按钮列表（hover 时显示）
        self._action_btns: list = []

        # 折叠/展开按钮（仅在超过阈值时显示，始终可见）
        if self._line_count > self._COLLAPSE_THRESHOLD:
            self._toggle_btn = QtWidgets.QPushButton(
                f"展开 ({self._line_count} 行)" if self._collapsed else "收起"
            )
            self._toggle_btn.setCursor(QtCore.Qt.PointingHandCursor)
            self._toggle_btn.setObjectName("codeBlockBtn")
            self._toggle_btn.clicked.connect(self._toggle_collapse)
            hl.addWidget(self._toggle_btn)

        copy_btn = QtWidgets.QPushButton("复制")
        copy_btn.setCursor(QtCore.Qt.PointingHandCursor)
        copy_btn.setObjectName("codeBlockBtn")
        copy_btn.clicked.connect(self._on_copy)
        copy_btn.setVisible(False)
        hl.addWidget(copy_btn)
        self._action_btns.append(copy_btn)

        if self._lang in ('vex', 'vfl', '') and self._is_vex():
            wrangle_btn = QtWidgets.QPushButton("创建 Wrangle")
            wrangle_btn.setCursor(QtCore.Qt.PointingHandCursor)
            wrangle_btn.setObjectName("codeBlockBtnGreen")
            wrangle_btn.clicked.connect(lambda: self.createWrangleRequested.emit(self._code))
            wrangle_btn.setVisible(False)
            hl.addWidget(wrangle_btn)
            self._action_btns.append(wrangle_btn)

        layout.addWidget(header)

        # ---- code area ----
        self._code_edit = QtWidgets.QTextEdit()
        self._code_edit.setReadOnly(True)
        self._code_edit.setLineWrapMode(QtWidgets.QTextEdit.NoWrap)
        self._code_edit.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self._code_edit.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self._code_edit.setObjectName("codeBlockEdit")

        highlighted = self._highlight()
        code_html = self._add_line_numbers(highlighted) if self._show_line_numbers else highlighted
        self._code_edit.setHtml(
            f'<pre style="margin:0;white-space:pre;">{code_html}</pre>'
        )
        # auto-height (capped)
        doc = self._code_edit.document()
        doc.setDocumentMargin(4)
        self._full_h = int(doc.size().height()) + 20

        # 计算折叠高度（COLLAPSE_THRESHOLD 行）
        fm = self._code_edit.fontMetrics()
        line_h = fm.lineSpacing() if fm.lineSpacing() > 0 else 17
        self._collapsed_h = self._COLLAPSE_THRESHOLD * line_h + 20

        if self._collapsed:
            self._code_edit.setFixedHeight(min(self._collapsed_h, self._MAX_HEIGHT))
            self._code_edit.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        else:
            self._code_edit.setFixedHeight(min(self._full_h, self._MAX_HEIGHT))

        layout.addWidget(self._code_edit)

    def _add_line_numbers(self, highlighted_code: str) -> str:
        """为高亮代码添加行号（使用 HTML table 布局）"""
        lines = highlighted_code.split('\n')
        width = len(str(len(lines)))
        result: list = []
        num_color = '#4a5568'  # 暗灰色行号
        sep_color = 'rgba(255,255,255,6)'  # 分隔线

        for i, line in enumerate(lines, 1):
            num = str(i).rjust(width)
            result.append(
                f'<span style="color:{num_color};user-select:none;'
                f'padding-right:12px;border-right:1px solid {sep_color};'
                f'margin-right:12px;">{num}</span>{line}'
            )
        return '\n'.join(result)

    def _toggle_collapse(self):
        """切换代码块折叠/展开"""
        self._collapsed = not self._collapsed
        if self._collapsed:
            self._code_edit.setFixedHeight(min(self._collapsed_h, self._MAX_HEIGHT))
            self._code_edit.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            self._code_edit.verticalScrollBar().setValue(0)
            self._toggle_btn.setText(f"展开 ({self._line_count} 行)")
        else:
            self._code_edit.setFixedHeight(min(self._full_h, self._MAX_HEIGHT))
            if self._full_h > self._MAX_HEIGHT:
                self._code_edit.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
            else:
                self._code_edit.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            self._toggle_btn.setText("收起")

    # --- helpers ---
    def _is_vex(self) -> bool:
        return any(ind in self._code for ind in self._VEX_INDICATORS)

    def _highlight(self) -> str:
        lang = self._lang
        # VEX 自动检测
        if lang in ('vex', 'vfl') or (not lang and self._is_vex()):
            return SyntaxHighlighter.highlight_vex(self._code)
        # Python
        if lang in ('python', 'py'):
            return SyntaxHighlighter.highlight_python(self._code)
        # JSON
        if lang == 'json':
            return SyntaxHighlighter.highlight_json(self._code)
        # YAML
        if lang in ('yaml', 'yml'):
            return SyntaxHighlighter.highlight_yaml(self._code)
        # Bash / Shell
        if lang in ('bash', 'sh', 'shell', 'zsh', 'powershell', 'ps1', 'bat', 'cmd'):
            return SyntaxHighlighter.highlight_bash(self._code)
        # JavaScript / TypeScript
        if lang in ('javascript', 'js', 'typescript', 'ts', 'jsx', 'tsx'):
            return SyntaxHighlighter.highlight_javascript(self._code)
        # HScript
        if lang in ('hscript', 'hs'):
            return SyntaxHighlighter.highlight_hscript(self._code)
        # GLSL / HLSL / shader
        if lang in ('glsl', 'hlsl', 'shader', 'frag', 'vert', 'wgsl'):
            return SyntaxHighlighter.highlight_glsl(self._code)
        # C / C++ / C# (use GLSL tokenizer as base — similar syntax)
        if lang in ('c', 'cpp', 'c++', 'cxx', 'h', 'hpp', 'cs', 'csharp'):
            return SyntaxHighlighter.highlight_glsl(self._code)
        # XML / HTML — use plain escaped (simple approach)
        if lang in ('xml', 'html', 'svg'):
            return html.escape(self._code)
        # Fallback: no highlighting
        return html.escape(self._code)

    def enterEvent(self, event):
        for btn in self._action_btns:
            btn.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        for btn in self._action_btns:
            btn.setVisible(False)
        super().leaveEvent(event)

    def _on_copy(self):
        QtWidgets.QApplication.clipboard().setText(self._code)
        btn = self.sender()
        if btn:
            btn.setText("已复制")
            QtCore.QTimer.singleShot(1500, lambda: btn.setText("复制"))

    # _btn_css removed — styling now via QSS objectName selectors


# ============================================================
# 富文本内容组件
# ============================================================

class RichContentWidget(QtWidgets.QWidget):
    """渲染 Markdown 文本 + 交互式代码块

    采用与 Cursor / GitHub Copilot Chat 类似的排版风格：
    - 文本段落紧凑、行高舒适
    - 代码块与正文之间有清晰分隔
    - 表格、链接、列表等完整支持
    - Houdini 节点路径自动变为可点击链接
    """

    createWrangleRequested = QtCore.Signal(str)
    nodePathClicked = QtCore.Signal(str)  # 节点路径被点击

    # _TEXT_STYLE removed — use objectName-based QSS instead

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)  # 段落间距由 HTML margin 控制

        segments = SimpleMarkdown.parse_segments(text)

        for seg in segments:
            if seg[0] == 'text':
                lbl = QtWidgets.QLabel()
                lbl.setWordWrap(True)
                lbl.setTextFormat(QtCore.Qt.RichText)
                lbl.setOpenExternalLinks(False)  # 我们自己处理链接
                lbl.setTextInteractionFlags(
                    QtCore.Qt.TextSelectableByMouse
                    | QtCore.Qt.LinksAccessibleByMouse
                )
                lbl.setText(seg[1])
                lbl.setObjectName("richText")
                lbl.linkActivated.connect(self._on_link)
                layout.addWidget(lbl)
            elif seg[0] == 'code':
                cb = CodeBlockWidget(seg[2], seg[1], self)
                cb.createWrangleRequested.connect(self.createWrangleRequested.emit)
                cb.setContentsMargins(0, 6, 0, 6)
                layout.addWidget(cb)
            elif seg[0] == 'image':
                img_url = seg[1]
                img_alt = seg[2] if len(seg) > 2 else ''
                img_lbl = QtWidgets.QLabel()
                img_lbl.setObjectName("richImage")
                img_lbl.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
                img_lbl.setWordWrap(False)
                img_lbl.setText(
                    f'<div style="margin:4px 0;">'
                    f'<img src="{html.escape(img_url)}" '
                    f'alt="{html.escape(img_alt)}" '
                    f'style="max-width:100%;max-height:300px;border-radius:6px;">'
                    f'</div>'
                )
                img_lbl.setTextFormat(QtCore.Qt.RichText)
                layout.addWidget(img_lbl)

    def _on_link(self, url: str):
        """处理链接点击"""
        if url.startswith('houdini://'):
            self.nodePathClicked.emit(url[len('houdini://'):])
        else:
            # 外部链接用浏览器打开
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))


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


# ============================================================
# VEX 预览确认对话框
# ============================================================

class VEXPreviewDialog(QtWidgets.QDialog):
    """VEX 代码预览对话框 — 用户确认后才执行创建操作"""

    def __init__(self, tool_name: str, args: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"确认执行: {tool_name}")
        self.setMinimumSize(560, 400)
        self.setObjectName("vexPreviewDlg")

        self._accepted = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # 工具名称
        title = QtWidgets.QLabel(f"工具: {tool_name}")
        title.setObjectName("vexDlgTitle")
        layout.addWidget(title)

        # 参数摘要
        summary_parts = []
        if 'node_name' in args:
            summary_parts.append(f"节点名: {args['node_name']}")
        if 'wrangle_type' in args:
            summary_parts.append(f"类型: {args['wrangle_type']}")
        if 'run_over' in args:
            summary_parts.append(f"Run Over: {args['run_over']}")
        if 'parent_path' in args:
            summary_parts.append(f"父路径: {args['parent_path']}")
        if 'node_type' in args:
            summary_parts.append(f"节点类型: {args['node_type']}")
        if 'node_path' in args:
            summary_parts.append(f"节点路径: {args['node_path']}")
        if summary_parts:
            info = QtWidgets.QLabel("  |  ".join(summary_parts))
            info.setObjectName("vexDlgInfo")
            info.setWordWrap(True)
            layout.addWidget(info)

        # VEX 代码 / 主要参数
        vex_code = args.get('vex_code', '')
        param_value = args.get('value', '')
        code_text = vex_code or param_value or str(args)

        code_edit = QtWidgets.QPlainTextEdit()
        code_edit.setPlainText(code_text)
        code_edit.setReadOnly(True)
        code_edit.setObjectName("vexDlgCode")
        layout.addWidget(code_edit, 1)

        # 按钮行
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()

        btn_cancel = QtWidgets.QPushButton("取消")
        btn_cancel.setFixedHeight(30)
        btn_cancel.setObjectName("dlgBtnCancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        btn_confirm = QtWidgets.QPushButton("✓ 确认执行")
        btn_confirm.setFixedHeight(30)
        btn_confirm.setObjectName("dlgBtnConfirm")
        btn_confirm.clicked.connect(self.accept)
        btn_row.addWidget(btn_confirm)

        layout.addLayout(btn_row)


# ============================================================
# 节点路径补全弹出框
# ============================================================

class NodeCompleterPopup(QtWidgets.QListWidget):
    """节点路径自动补全弹出窗 — 在输入 @ 时显示场景节点列表"""

    pathSelected = QtCore.Signal(str)  # 用户选中了一个节点路径

    def __init__(self, parent=None):
        super().__init__(parent)
        # ★ 不在构造时设置 ToolTip 窗口标志（会创建原生窗口句柄导致闪烁）
        # 改为在 show_filtered 中首次显示时再设置
        self._flags_applied = False
        self.setFixedWidth(320)
        self.setMaximumHeight(200)
        self.setObjectName("nodeCompleter")
        self.itemActivated.connect(self._on_item_activated)
        self.setVisible(False)
        self._all_paths: list = []

    def set_node_paths(self, paths: list):
        """设置可选的节点路径列表"""
        self._all_paths = paths

    def show_filtered(self, prefix: str, anchor_widget: QtWidgets.QWidget, cursor_rect):
        """根据前缀过滤并显示"""
        # ★ 首次显示时才设置窗口标志，避免构造时创建原生 tooltip 窗口导致闪烁
        if not self._flags_applied:
            self._flags_applied = True
            self.setWindowFlags(QtCore.Qt.ToolTip | QtCore.Qt.FramelessWindowHint)
        self.clear()
        lower_prefix = prefix.lower()
        matches = [p for p in self._all_paths if lower_prefix in p.lower()][:30]
        if not matches:
            self.setVisible(False)
            return
        for p in matches:
            self.addItem(p)
        # 定位到光标下方
        global_pos = anchor_widget.mapToGlobal(cursor_rect.bottomLeft())
        self.move(global_pos.x(), global_pos.y() + 4)
        self.setVisible(True)
        self.setCurrentRow(0)

    def _on_item_activated(self, item):
        self.pathSelected.emit(item.text())
        self.setVisible(False)

    def keyPressEvent(self, event):
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            current = self.currentItem()
            if current:
                self.pathSelected.emit(current.text())
                self.setVisible(False)
                return
        elif event.key() == QtCore.Qt.Key_Escape:
            self.setVisible(False)
            return
        super().keyPressEvent(event)


# ============================================================
# 输入区域
# ============================================================

class ChatInput(QtWidgets.QPlainTextEdit):
    """聊天输入框 — 自适应高度，支持自动换行、多行输入、图片粘贴/拖拽
    
    核心逻辑：统计文档中所有视觉行（含软换行），按行高计算目标高度，
    使输入框向上扩展而非隐藏已有行。
    支持 @节点路径 补全和从 Network Editor 拖拽节点。
    """
    
    sendRequested = QtCore.Signal()
    imageDropped = QtCore.Signal(QtGui.QImage)  # 粘贴或拖拽图片时触发
    atTriggered = QtCore.Signal(str, QtCore.QRect)  # @ 触发补全: (当前前缀, 光标矩形)
    
    _MIN_H = 44
    _MAX_H = 220
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText(tr('placeholder'))
        # 确保自动换行
        self.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
        self.setWordWrapMode(QtGui.QTextOption.WrapAtWordBoundaryOrAnywhere)
        # 隐藏滚动条（高度不够时才出现）
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        # 启用拖拽
        self.setAcceptDrops(True)
        self.setObjectName("chatInput")
        self.setMinimumHeight(self._MIN_H)
        self.setMaximumHeight(self._MAX_H)
        # 使用 textChanged，并延迟到下一事件循环执行（确保布局先完成）
        self.textChanged.connect(self._schedule_adjust)
        self.textChanged.connect(self._check_at_trigger)
        # @ 补全状态
        self._at_active = False
        self._at_start_pos = -1
        self._completer_popup: 'NodeCompleterPopup | None' = None
    
    def set_completer_popup(self, popup: 'NodeCompleterPopup'):
        """设置节点补全弹出框引用，用于键盘导航和自动关闭"""
        self._completer_popup = popup
    
    def _schedule_adjust(self):
        """延迟调整高度，确保文档布局已更新"""
        QtCore.QTimer.singleShot(0, self._adjust_height)
    
    def _adjust_height(self):
        """根据视觉行数（含软换行）自动调整高度——向上扩展"""
        doc = self.document()
        # 统计所有视觉行（包括 word-wrap 产生的软换行）
        visual_lines = 0
        block = doc.begin()
        while block.isValid():
            bl = block.layout()
            if bl and bl.lineCount() > 0:
                visual_lines += bl.lineCount()
            else:
                visual_lines += 1
            block = block.next()
        # 空文档至少算 1 行
        visual_lines = max(1, visual_lines)
        
        # 行高
        line_h = self.fontMetrics().lineSpacing()
        # 内容高度 = 行数 * 行高
        content_h = visual_lines * line_h
        # 加上 padding(8*2) + border(1*2) + 额外余量
        margins = self.contentsMargins()
        frame_w = self.frameWidth()
        padding = margins.top() + margins.bottom() + frame_w * 2 + 18
        total = content_h + padding
        
        h = max(self._MIN_H, min(self._MAX_H, total))
        if h != self.height():
            self.setFixedHeight(h)
            # 通知父布局重新分配空间
            self.updateGeometry()
    
    def _hide_completer(self):
        """隐藏补全弹出框"""
        if self._completer_popup and self._completer_popup.isVisible():
            self._completer_popup.setVisible(False)

    def _check_at_trigger(self):
        """检测输入中的 @ 字符，触发节点路径补全"""
        cursor = self.textCursor()
        pos = cursor.position()
        text = self.toPlainText()
        if not text or pos == 0:
            if self._at_active:
                self._at_active = False
                self._hide_completer()
            return

        # 查找光标前最近的 @
        left = text[:pos]
        at_idx = left.rfind('@')
        if at_idx == -1:
            if self._at_active:
                self._at_active = False
                self._hide_completer()
            return

        # @ 后面的内容不能包含空格（否则认为已结束）
        prefix_after_at = left[at_idx + 1:]
        if ' ' in prefix_after_at or '\n' in prefix_after_at:
            if self._at_active:
                self._at_active = False
                self._hide_completer()
            return

        self._at_active = True
        self._at_start_pos = at_idx
        # 发射信号，由外部(ai_tab)提供节点列表
        crect = self.cursorRect(cursor)
        self.atTriggered.emit(prefix_after_at, crect)

    def cancel_at_completion(self):
        """取消当前 @ 补全并隐藏弹出框"""
        self._at_active = False
        self._at_start_pos = -1
        self._hide_completer()

    def insert_at_completion(self, path: str):
        """将补全结果插入文本，替换 @前缀"""
        if self._at_start_pos < 0:
            return
        cursor = self.textCursor()
        pos = cursor.position()
        # 选中从 @ 到当前位置的文本并替换
        cursor.setPosition(self._at_start_pos)
        cursor.setPosition(pos, QtGui.QTextCursor.KeepAnchor)
        cursor.insertText(path + " ")
        self.setTextCursor(cursor)
        self._at_active = False
        self._at_start_pos = -1

    def _is_completer_visible(self) -> bool:
        """补全弹出框是否可见"""
        return (self._completer_popup is not None
                and self._completer_popup.isVisible()
                and self._completer_popup.count() > 0)

    def keyPressEvent(self, event):
        key = event.key()
        
        # ── @ 补全活跃时的键盘处理 ──
        if self._at_active and self._is_completer_visible():
            popup = self._completer_popup
            
            if key == QtCore.Qt.Key_Escape:
                # Escape: 取消补全 + 隐藏弹窗
                self.cancel_at_completion()
                return
            
            if key == QtCore.Qt.Key_Up:
                # Up: 在列表中上移
                row = popup.currentRow()
                if row > 0:
                    popup.setCurrentRow(row - 1)
                return
            
            if key == QtCore.Qt.Key_Down:
                # Down: 在列表中下移
                row = popup.currentRow()
                if row < popup.count() - 1:
                    popup.setCurrentRow(row + 1)
                return
            
            if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter) and not (event.modifiers() & QtCore.Qt.ShiftModifier):
                # Enter: 选中当前项（而非发送消息）
                current = popup.currentItem()
                if current:
                    self.insert_at_completion(current.text())
                    self._hide_completer()
                return
            
            if key == QtCore.Qt.Key_Tab:
                # Tab: 也可以选中当前项
                current = popup.currentItem()
                if current:
                    self.insert_at_completion(current.text())
                    self._hide_completer()
                return
        
        elif self._at_active and key == QtCore.Qt.Key_Escape:
            # 补全活跃但弹窗不可见（如无匹配结果）：仍允许 Escape 取消
            self.cancel_at_completion()
            return
        
        # ── 常规键盘处理 ──
        if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            if event.modifiers() & QtCore.Qt.ShiftModifier:
                super().keyPressEvent(event)
            else:
                self.sendRequested.emit()
                return
        
        super().keyPressEvent(event)
    
    def mousePressEvent(self, event):
        """点击文本区域时，如果补全弹窗可见则关闭"""
        if self._is_completer_visible():
            self.cancel_at_completion()
        super().mousePressEvent(event)

    def focusOutEvent(self, event):
        """失焦时关闭补全弹窗"""
        # 延迟关闭：如果焦点转移到弹窗本身（用户点击弹窗），不关闭
        QtCore.QTimer.singleShot(100, self._check_focus_dismiss)
        super().focusOutEvent(event)

    def _check_focus_dismiss(self):
        """检查是否需要因失焦而关闭弹窗"""
        if not self.hasFocus() and self._is_completer_visible():
            # 检查弹窗本身是否获得焦点（ToolTip 窗口一般不获焦，但以防万一）
            if self._completer_popup and not self._completer_popup.hasFocus():
                self.cancel_at_completion()

    def resizeEvent(self, event):
        """窗口宽度变化时重新计算高度（自动换行可能改变行数）"""
        super().resizeEvent(event)
        self._schedule_adjust()

    # ---- 拖拽节点支持 ----
    
    def dragEnterEvent(self, event):
        """接受来自 Houdini Network Editor 的节点路径拖拽"""
        mime = event.mimeData()
        if mime.hasText():
            text = mime.text().strip()
            # 检查是否像 Houdini 节点路径
            if text.startswith('/') and '/' in text[1:]:
                event.acceptProposedAction()
                return
        # 也接受图片拖拽（原有逻辑）
        if mime.hasImage() or mime.hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event):
        """拖拽释放：优先检查节点路径，其次处理图片"""
        mime = event.mimeData()
        # 1) Houdini 节点路径拖拽
        if mime.hasText():
            text = mime.text().strip()
            if text.startswith('/') and '/' in text[1:]:
                cursor = self.cursorForPosition(
                    event.position().toPoint() if hasattr(event.position(), 'toPoint') else event.pos()
                )
                cursor.insertText(text + " ")
                self.setTextCursor(cursor)
                event.acceptProposedAction()
                return
        # 2) 图片拖拽
        if mime.hasImage():
            image = mime.imageData()
            if image and not image.isNull():
                self.imageDropped.emit(image)
                event.acceptProposedAction()
                return
        if mime.hasUrls():
            _IMG_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}
            for url in mime.urls():
                if url.isLocalFile():
                    import os
                    ext = os.path.splitext(url.toLocalFile())[1].lower()
                    if ext in _IMG_EXTS:
                        img = QtGui.QImage(url.toLocalFile())
                        if not img.isNull():
                            self.imageDropped.emit(img)
                            event.acceptProposedAction()
                            return
        super().dropEvent(event)
    
    # ---- 图片粘贴支持 ----
    
    def insertFromMimeData(self, source):
        """重写粘贴：支持从剪贴板粘贴图片"""
        if source.hasImage():
            image = source.imageData()
            if image and not image.isNull():
                self.imageDropped.emit(image)
                return
        # 粘贴文件路径中的图片
        if source.hasUrls():
            _IMG_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}
            for url in source.urls():
                if url.isLocalFile():
                    import os
                    ext = os.path.splitext(url.toLocalFile())[1].lower()
                    if ext in _IMG_EXTS:
                        img = QtGui.QImage(url.toLocalFile())
                        if not img.isNull():
                            self.imageDropped.emit(img)
                            return
        # 默认文本粘贴
        super().insertFromMimeData(source)


# ============================================================
# 停止按钮
# ============================================================

class StopButton(QtWidgets.QPushButton):
    """停止按钮"""
    
    def __init__(self, parent=None):
        super().__init__("Stop", parent)
        self.setObjectName("btnStop")


# ============================================================
# 发送按钮
# ============================================================

class SendButton(QtWidgets.QPushButton):
    """发送按钮"""
    
    def __init__(self, parent=None):
        super().__init__("Send", parent)
        self.setObjectName("btnSend")


# ============================================================
# Todo 系统
# ============================================================

class TodoItem(QtWidgets.QWidget):
    """单个 Todo 项"""
    
    statusChanged = QtCore.Signal(str, str)
    
    def __init__(self, todo_id: str, text: str, status: str = "pending", parent=None):
        super().__init__(parent)
        self.todo_id = todo_id
        self.text = text
        self.status = status
        
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 1, 0, 1)
        layout.setSpacing(4)
        
        self.status_label = QtWidgets.QLabel()
        self.status_label.setFixedWidth(14)
        layout.addWidget(self.status_label)
        
        self.text_label = QtWidgets.QLabel(text)
        self.text_label.setWordWrap(True)
        layout.addWidget(self.text_label, 1)
        
        self._update_style()
    
    def _update_style(self):
        icons = {
            "pending": "○",
            "in_progress": "◎", 
            "done": "●",
            "error": "✗"
        }
        
        icon = icons.get(self.status, "○")
        
        self.status_label.setText(icon)
        self.status_label.setObjectName("todoStatusIcon")
        self.status_label.setProperty("state", self.status)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        
        self.text_label.setObjectName("todoText")
        self.text_label.setProperty("state", self.status)
        self.text_label.style().unpolish(self.text_label)
        self.text_label.style().polish(self.text_label)
    
    def set_status(self, status: str):
        self.status = status
        self._update_style()
        self.statusChanged.emit(self.todo_id, status)


class TodoList(QtWidgets.QWidget):
    """Todo 列表 - 显示 AI 的任务计划（卡片式框体）"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._todos = {}
        
        # 最外层无间距
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 4, 0, 4)
        outer.setSpacing(0)
        
        # 卡片容器
        self._card = QtWidgets.QFrame(self)
        self._card.setObjectName("todoCard")
        card_layout = QtWidgets.QVBoxLayout(self._card)
        card_layout.setContentsMargins(10, 8, 10, 8)
        card_layout.setSpacing(4)
        
        # 标题行
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(6)
        
        self.title_label = QtWidgets.QLabel("Todo")
        self.title_label.setObjectName("todoTitle")
        header.addWidget(self.title_label)
        
        self.count_label = QtWidgets.QLabel("0/0")
        self.count_label.setObjectName("todoCount")
        header.addWidget(self.count_label)
        
        header.addStretch()
        
        self.clear_btn = QtWidgets.QPushButton("Clear")
        self.clear_btn.setFixedHeight(20)
        self.clear_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.clear_btn.setObjectName("todoClearBtn")
        self.clear_btn.clicked.connect(self.clear_all)
        header.addWidget(self.clear_btn)
        
        card_layout.addLayout(header)
        
        # 分隔线
        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        sep.setObjectName("todoSeparator")
        card_layout.addWidget(sep)
        
        # 任务列表
        self.list_layout = QtWidgets.QVBoxLayout()
        self.list_layout.setSpacing(2)
        self.list_layout.setContentsMargins(0, 2, 0, 0)
        card_layout.addLayout(self.list_layout)
        
        outer.addWidget(self._card)
        self.setVisible(False)
    
    def add_todo(self, todo_id: str, text: str, status: str = "pending") -> TodoItem:
        if todo_id in self._todos:
            self._todos[todo_id].text_label.setText(text)
            self._todos[todo_id].set_status(status)
            return self._todos[todo_id]
        
        item = TodoItem(todo_id, text, status, self)
        self._todos[todo_id] = item
        self.list_layout.addWidget(item)
        self._update_count()
        self.setVisible(True)
        return item
    
    def update_todo(self, todo_id: str, status: str):
        if todo_id in self._todos:
            self._todos[todo_id].set_status(status)
            self._update_count()
    
    def remove_todo(self, todo_id: str):
        if todo_id in self._todos:
            item = self._todos.pop(todo_id)
            item.deleteLater()
            self._update_count()
            if not self._todos:
                self.setVisible(False)
    
    def clear_all(self):
        for item in self._todos.values():
            item.deleteLater()
        self._todos.clear()
        self._update_count()
        self.setVisible(False)
    
    def _update_count(self):
        total = len(self._todos)
        done = sum(1 for item in self._todos.values() if item.status == "done")
        self.count_label.setText(f"{done}/{total}")
    
    def get_pending_todos(self) -> list:
        return [
            {"id": todo_id, "text": item.text, "status": item.status}
            for todo_id, item in self._todos.items()
            if item.status not in ("done", "error")
        ]
    
    def get_all_todos(self) -> list:
        return [
            {"id": todo_id, "text": item.text, "status": item.status}
            for todo_id, item in self._todos.items()
        ]
    
    def get_todos_data(self) -> list:
        """返回可序列化的 todo 列表（用于缓存保存/恢复）"""
        return [
            {"id": todo_id, "text": item.text, "status": item.status}
            for todo_id, item in self._todos.items()
        ]

    def restore_todos(self, todos_data: list):
        """从序列化数据恢复 todo 列表"""
        if not todos_data:
            return
        for td in todos_data:
            tid = td.get('id', '')
            text = td.get('text', '')
            status = td.get('status', 'pending')
            if tid and text:
                self.add_todo(tid, text, status)

    def get_todos_summary(self) -> str:
        if not self._todos:
            return ""
        
        lines = ["Current Todo List:"]
        for todo_id, item in self._todos.items():
            status_icons = {
                "pending": "[ ]",
                "in_progress": "[~]",
                "done": "[x]",
                "error": "[!]"
            }
            icon = status_icons.get(item.status, "[ ]")
            lines.append(f"  {icon} {item.text}")
        
        pending = [item for item in self._todos.values() if item.status == "pending"]
        if pending:
            lines.append(f"\nReminder: {len(pending)} tasks pending.")
        
        return "\n".join(lines)


# ============================================================
# Token Analytics Panel — 现代简约可视化分析面板
# ============================================================

class _BarWidget(QtWidgets.QWidget):
    """水平柱状图条——用于可视化 token 占比"""

    def __init__(self, segments: list, max_val: float, parent=None):
        """
        segments: [(value, color_hex), ...]
        max_val: 全局最大值（用于对齐）
        """
        super().__init__(parent)
        self._segments = segments
        self._max = max(max_val, 1)
        self.setFixedHeight(14)
        self.setMinimumWidth(60)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        w = self.width()
        h = self.height()
        x = 0.0
        for val, color in self._segments:
            seg_w = (val / self._max) * w
            if seg_w < 0.5:
                continue
            painter.setBrush(QtGui.QColor(color))
            painter.setPen(QtCore.Qt.NoPen)
            painter.drawRoundedRect(QtCore.QRectF(x, 1, seg_w, h - 2), 2, 2)
            x += seg_w
        painter.end()


class TokenAnalyticsPanel(QtWidgets.QDialog):
    """Token 使用分析面板 - 对齐 Cursor 风格
    
    新增：
    - 预估费用（按实际模型定价）
    - 推理 Token（Reasoning）
    - 延迟（Latency）
    - 每行费用
    """

    _COL_HEADERS = [
        "#", "时间", "模型", "Input", "Cache↓", "Cache↑",
        "Output", "Think", "Total", "延迟", "费用", "",
    ]

    def __init__(self, call_records: list, token_stats: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Token 使用分析")
        self.setMinimumSize(920, 560)
        self.resize(1020, 640)
        self.setObjectName("tokenPanel")

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(12)

        # ---- 摘要卡片 ----
        root.addWidget(self._build_summary(call_records, token_stats))

        # ---- 调用明细表 ----
        root.addWidget(self._build_table(call_records), 1)

        # ---- 底部按钮 ----
        self.should_reset_stats = False
        foot = QtWidgets.QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)

        reset_btn = QtWidgets.QPushButton("重置统计")
        reset_btn.setFixedWidth(82)
        reset_btn.setObjectName("tokenResetBtn")
        reset_btn.clicked.connect(self._on_reset)
        foot.addWidget(reset_btn)

        foot.addStretch()
        close_btn = QtWidgets.QPushButton("关闭")
        close_btn.setFixedWidth(72)
        close_btn.setObjectName("tokenCloseBtn")
        close_btn.clicked.connect(self.accept)
        foot.addWidget(close_btn)
        root.addLayout(foot)

    def _on_reset(self):
        """用户点击了重置按钮"""
        self.should_reset_stats = True
        self.accept()

    # -------- 摘要区 --------
    def _build_summary(self, records, stats) -> QtWidgets.QWidget:
        card = QtWidgets.QFrame()
        card.setObjectName("tokenSummaryCard")
        grid = QtWidgets.QGridLayout(card)
        grid.setContentsMargins(16, 12, 16, 12)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(8)

        total_in = stats.get('input_tokens', 0)
        total_out = stats.get('output_tokens', 0)
        reasoning = stats.get('reasoning_tokens', 0)
        cache_r = stats.get('cache_read', 0)
        cache_w = stats.get('cache_write', 0)
        reqs = stats.get('requests', 0)
        total = stats.get('total_tokens', 0)
        cost = stats.get('estimated_cost', 0.0)
        cache_total = cache_r + cache_w
        hit_rate = (cache_r / cache_total * 100) if cache_total > 0 else 0

        # 平均延迟
        latencies = [r.get('latency', 0) for r in records if r.get('latency', 0) > 0]
        avg_latency = sum(latencies) / len(latencies) if latencies else 0

        # 费用格式化
        if cost >= 1.0:
            cost_str = f"${cost:.2f}"
        elif cost > 0:
            cost_str = f"${cost:.4f}"
        else:
            cost_str = "$0.00"

        metrics = [
            ("Requests",       f"{reqs}",               CursorTheme.ACCENT_BLUE),
            ("Input",          self._fmt_k(total_in),    CursorTheme.ACCENT_PURPLE),
            ("Output",         self._fmt_k(total_out),   CursorTheme.ACCENT_GREEN),
            ("Reasoning",      self._fmt_k(reasoning),   CursorTheme.ACCENT_YELLOW),
            ("Cache Hit",      self._fmt_k(cache_r),     "#10b981"),
            ("Hit Rate",       f"{hit_rate:.1f}%",       "#10b981"),
            ("Avg Latency",    f"{avg_latency:.1f}s",    CursorTheme.TEXT_SECONDARY),
            ("Est. Cost",      cost_str,                 CursorTheme.ACCENT_BLUE),
        ]
        for col, (label, value, color) in enumerate(metrics):
            lbl = QtWidgets.QLabel(label)
            lbl.setObjectName("tokenMetricLabel")
            lbl.setAlignment(QtCore.Qt.AlignCenter)
            grid.addWidget(lbl, 0, col)

            val = QtWidgets.QLabel(value)
            val.setObjectName("tokenMetricValue")
            # Per-metric dynamic color via inline (unique per column)
            val.setStyleSheet(f"color:{color};")
            val.setAlignment(QtCore.Qt.AlignCenter)
            grid.addWidget(val, 1, col)

        # 进度条: input vs output vs cache
        if total > 0:
            bar = _BarWidget([
                (cache_r, "#10b981"),
                (cache_w, CursorTheme.ACCENT_ORANGE),
                (max(total_in - cache_r - cache_w, 0), CursorTheme.ACCENT_PURPLE),
                (reasoning, CursorTheme.ACCENT_YELLOW),
                (max(total_out - reasoning, 0), CursorTheme.ACCENT_GREEN),
            ], total)
            bar.setFixedHeight(8)
            grid.addWidget(bar, 2, 0, 1, len(metrics))

        return card

    # -------- 明细表 --------
    def _build_table(self, records) -> QtWidgets.QWidget:
        container = QtWidgets.QFrame()
        container.setObjectName("tokenTableCard")
        vbox = QtWidgets.QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        # 标题
        title_lbl = QtWidgets.QLabel(f"  调用明细 ({len(records)} calls)")
        title_lbl.setObjectName("tokenTableTitle")
        vbox.addWidget(title_lbl)

        if not records:
            empty = QtWidgets.QLabel("  暂无 API 调用记录")
            empty.setObjectName("tokenTableEmpty")
            vbox.addWidget(empty)
            return container

        # 滚动表格区域
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setObjectName("chatScrollArea")

        table_widget = QtWidgets.QWidget()
        table_layout = QtWidgets.QVBoxLayout(table_widget)
        table_layout.setContentsMargins(8, 0, 8, 8)
        table_layout.setSpacing(0)

        # 表头
        hdr = self._make_row_widget(self._COL_HEADERS, is_header=True)
        table_layout.addWidget(hdr)

        # 找最大 total 以绘制柱状图
        max_total = max((r.get('total_tokens', 0) for r in records), default=1)

        # 最新的调用显示在最上面
        for display_idx, (orig_idx, rec) in enumerate(
            reversed(list(enumerate(records)))
        ):
            row = self._make_record_row(orig_idx, rec, max_total)
            table_layout.addWidget(row)

        table_layout.addStretch()
        scroll.setWidget(table_widget)
        vbox.addWidget(scroll, 1)

        return container

    # 列宽定义
    _COL_WIDTHS = [24, 50, 90, 54, 54, 54, 54, 48, 54, 44, 52, 0]

    def _make_row_widget(self, cells: list, is_header=False) -> QtWidgets.QWidget:
        """创建一行（表头或数据行）"""
        row_w = QtWidgets.QWidget()
        row_h = QtWidgets.QHBoxLayout(row_w)
        row_h.setContentsMargins(4, 3, 4, 3)
        row_h.setSpacing(2)

        font_size = "10px" if is_header else "11px"
        fg = CursorTheme.TEXT_MUTED if is_header else CursorTheme.TEXT_PRIMARY
        weight = "bold" if is_header else "normal"
        font_family = f"font-family:'Consolas','Monaco',monospace;" if not is_header else ""

        widths = self._COL_WIDTHS

        for i, text in enumerate(cells):
            lbl = QtWidgets.QLabel(str(text))
            lbl.setObjectName("tokenHeaderCell" if is_header else "tokenDataCell")
            if i < len(widths) and widths[i] > 0:
                lbl.setFixedWidth(widths[i])
            # 数字列右对齐
            lbl.setAlignment(QtCore.Qt.AlignRight if 3 <= i <= 10 else QtCore.Qt.AlignLeft)
            if i < len(widths) and widths[i] == 0:
                row_h.addWidget(lbl, 1)
            else:
                row_h.addWidget(lbl)

        if is_header:
            row_w.setObjectName("tokenHeaderRow")

        return row_w

    def _make_record_row(self, idx: int, rec: dict, max_total: float) -> QtWidgets.QWidget:
        """构建单条记录行"""
        row_w = QtWidgets.QWidget()
        row_w.setObjectName("tokenDataRow")
        row_h = QtWidgets.QHBoxLayout(row_w)
        row_h.setContentsMargins(4, 2, 4, 2)
        row_h.setSpacing(2)

        ts = rec.get('timestamp', '')
        if len(ts) > 10:
            ts = ts[11:19]
        model = rec.get('model', '-')
        if len(model) > 12:
            model = model[:10] + '..'
        inp = rec.get('input_tokens', 0)
        c_hit = rec.get('cache_hit', 0)
        c_miss = rec.get('cache_miss', 0)
        out = rec.get('output_tokens', 0)
        reasoning = rec.get('reasoning_tokens', 0)
        total = rec.get('total_tokens', 0)
        latency = rec.get('latency', 0)

        # 单次费用（优先使用预计算值）
        row_cost = rec.get('estimated_cost', 0.0)
        if not row_cost:
            try:
                from houdini_agent.utils.token_optimizer import calculate_cost
                row_cost = calculate_cost(
                    model=rec.get('model', ''),
                    input_tokens=inp,
                    output_tokens=out,
                    cache_hit=c_hit,
                    cache_miss=c_miss,
                    reasoning_tokens=reasoning,
                )
            except Exception:
                row_cost = 0.0

        cost_str = f"${row_cost:.4f}" if row_cost > 0 else "-"
        latency_str = f"{latency:.1f}s" if latency > 0 else "-"

        cells = [
            str(idx + 1),
            ts,
            model,
            self._fmt_k(inp),
            self._fmt_k(c_hit),
            self._fmt_k(c_miss),
            self._fmt_k(out),
            self._fmt_k(reasoning) if reasoning > 0 else "-",
            self._fmt_k(total),
            latency_str,
            cost_str,
        ]
        widths = self._COL_WIDTHS[:-1]  # 除去最后的 stretch
        colors = [
            CursorTheme.TEXT_MUTED,       # #
            CursorTheme.TEXT_MUTED,       # 时间
            CursorTheme.TEXT_PRIMARY,     # 模型
            CursorTheme.ACCENT_PURPLE,    # Input
            "#10b981",                    # Cache Hit
            CursorTheme.ACCENT_ORANGE,    # Cache Write
            CursorTheme.ACCENT_GREEN,     # Output
            CursorTheme.ACCENT_YELLOW,    # Reasoning
            CursorTheme.TEXT_BRIGHT,      # Total
            CursorTheme.TEXT_SECONDARY,   # 延迟
            CursorTheme.ACCENT_BLUE,      # 费用
        ]
        for i, text in enumerate(cells):
            lbl = QtWidgets.QLabel(text)
            lbl.setObjectName("tokenDataCell")
            if i < len(widths):
                lbl.setFixedWidth(widths[i])
            align = QtCore.Qt.AlignRight if i >= 3 else QtCore.Qt.AlignLeft
            lbl.setAlignment(align)
            c = colors[i] if i < len(colors) else CursorTheme.TEXT_PRIMARY
            # Per-column unique color via inline
            lbl.setStyleSheet(f"color:{c};")
            row_h.addWidget(lbl)

        # 迷你柱状图
        bar = _BarWidget([
            (c_hit, "#10b981"),
            (c_miss, CursorTheme.ACCENT_ORANGE),
            (max(inp - c_hit - c_miss, 0), CursorTheme.ACCENT_PURPLE),
            (reasoning, CursorTheme.ACCENT_YELLOW),
            (max(out - reasoning, 0), CursorTheme.ACCENT_GREEN),
        ], max_total)
        row_h.addWidget(bar, 1)

        return row_w

    @staticmethod
    def _fmt_k(n: int) -> str:
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 10_000:
            return f"{n / 1000:.1f}K"
        if n >= 1000:
            return f"{n / 1000:.1f}K"
        return str(n)
