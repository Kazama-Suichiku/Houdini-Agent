# -*- coding: utf-8 -*-
"""
Cursor 风格 UI 组件 - 重构版
模仿 Cursor 侧边栏的简洁设计
每次对话形成完整块：思考 → 操作 → 总结
"""

from PySide6 import QtWidgets, QtCore, QtGui
from datetime import datetime
from typing import Optional, List, Dict
import html
import re
import time


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

_NODE_LINK_STYLE = "color:#4ec9b0;text-decoration:none;font-family:Consolas,Monaco,monospace;"


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
# 颜色主题 (深色主题)
# ============================================================

class CursorTheme:
    """Cursor 风格深色主题"""
    # 背景色
    BG_PRIMARY = "#1e1e1e"
    BG_SECONDARY = "#252526"
    BG_TERTIARY = "#2d2d30"
    BG_HOVER = "#3c3c3c"
    
    # 边框色
    BORDER = "#3c3c3c"
    BORDER_FOCUS = "#007acc"
    
    # 文字色
    TEXT_PRIMARY = "#cccccc"
    TEXT_SECONDARY = "#858585"
    TEXT_MUTED = "#6a6a6a"
    TEXT_BRIGHT = "#ffffff"
    
    # 强调色
    ACCENT_BLUE = "#007acc"
    ACCENT_GREEN = "#4ec9b0"
    ACCENT_ORANGE = "#ce9178"
    ACCENT_RED = "#f14c4c"
    ACCENT_PURPLE = "#c586c0"
    ACCENT_YELLOW = "#dcdcaa"
    ACCENT_BEIGE = "#c8a882"       # 米色 — 工具调用/折叠区
    
    # 消息左边界
    BORDER_USER = "#555555"        # 用户消息 — 浅灰
    BORDER_AI = "#a0a0a0"          # AI 回复 — 适中亮度白
    
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
        self.header.setStyleSheet(f"""
            QPushButton {{
                color: {CursorTheme.TEXT_MUTED};
                font-size: 16px;
                font-family: {CursorTheme.FONT_BODY};
                text-align: left;
                padding: 4px 8px;
                border: none;
                background: {CursorTheme.BG_TERTIARY};
            }}
            QPushButton:hover {{
                background: {CursorTheme.BG_HOVER};
                color: {CursorTheme.TEXT_PRIMARY};
            }}
        """)
        layout.addWidget(self.header)
        
        # 内容区
        self.content_widget = QtWidgets.QWidget()
        self.content_layout = QtWidgets.QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(6, 4, 4, 4)
        self.content_layout.setSpacing(2)
        self.content_widget.setVisible(not collapsed)
        self.content_widget.setStyleSheet(f"""
            QWidget {{
                background: {CursorTheme.BG_TERTIARY};
            }}
        """)
        layout.addWidget(self.content_widget)
    
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
        
        if style == "muted":
            color = CursorTheme.TEXT_MUTED
        elif style == "success":
            color = CursorTheme.ACCENT_GREEN
        elif style == "error":
            color = CursorTheme.ACCENT_RED
        else:
            color = CursorTheme.TEXT_SECONDARY
        
        label.setStyleSheet(f"""
            color: {color};
            font-size: 14px;
            padding: 2px 0;
        """)
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
        # ★ 默认折叠（和原版一致），首次收到思考内容时自动展开
        super().__init__("思考中...", icon="", collapsed=True, parent=parent)
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
        self.thinking_label.setStyleSheet(f"""
            QPlainTextEdit {{
                color: {CursorTheme.TEXT_MUTED};
                background: transparent;
                border: none;
                padding: 4px;
                font-size: 13px;
                font-family: {CursorTheme.FONT_BODY};
            }}
        """)
        # 初始高度为一行（紧凑），流式输入时会动态增大
        self._line_h = QtGui.QFontMetrics(self._text_font).lineSpacing()
        self.thinking_label.setFixedHeight(self._line_h + 12)
        self.content_layout.addWidget(self.thinking_label)
        
        # 标题样式
        self.header.setStyleSheet(f"""
            QPushButton {{
                color: {CursorTheme.ACCENT_PURPLE};
                font-size: 16px;
                font-family: {CursorTheme.FONT_BODY};
                text-align: left;
                padding: 4px 8px;
                border: none;
                background: {CursorTheme.BG_TERTIARY};
            }}
            QPushButton:hover {{
                background: {CursorTheme.BG_HOVER};
            }}
        """)
    
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
        self.set_title(f"思考中... ({_fmt_duration(self._total_elapsed())})")
    
    @property
    def _finalized(self):
        return getattr(self, '_is_finalized', False)
    
    def resume(self):
        self._is_finalized = False
        self._round_start = time.time()
        self._round_count += 1
        self._thinking_text += f"\n--- 第 {self._round_count + 1} 轮思考 ---\n"
        self.thinking_label.setPlainText(self._thinking_text)
        QtCore.QTimer.singleShot(0, self._update_height)
        self.set_title(f"思考中... ({_fmt_duration(self._total_elapsed())})")
        if self._collapsed:
            self.toggle()
    
    def finalize(self):
        if self._finalized:
            return
        self._is_finalized = True
        self._accumulated_seconds += (time.time() - self._round_start)
        total = self._accumulated_seconds
        self.set_title(f"思考过程 ({_fmt_duration(total)})")
        if not self._collapsed:
            self.toggle()


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
        display = f"  ✦ 思考中... ({time_str})"

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
        self.setStyleSheet(f"""
            VEXPreviewInline {{
                background: {CursorTheme.BG_TERTIARY};
                border: 1.5px solid {CursorTheme.ACCENT_BEIGE};
                border-radius: 6px;
            }}
        """)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(3)

        # 标题行
        title = QtWidgets.QLabel(f"确认执行: {tool_name}")
        title.setStyleSheet(f"color:{CursorTheme.ACCENT_BEIGE};font-size:13px;font-weight:bold;")
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
                summary_text += f"\n  ... 共 {len(summary_lines)} 个参数"
            summary_lbl = QtWidgets.QLabel(summary_text)
            summary_lbl.setWordWrap(True)
            summary_lbl.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            summary_lbl.setStyleSheet(f"""
                color: {CursorTheme.TEXT_PRIMARY};
                font-size: 11px;
                font-family: {CursorTheme.FONT_CODE};
                background: transparent;
                padding: 2px 4px;
            """)
            summary_lbl.setSizePolicy(
                QtWidgets.QSizePolicy.Expanding,
                QtWidgets.QSizePolicy.Maximum,
            )
            layout.addWidget(summary_lbl)

        # 按钮行（右对齐，紧凑）
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.addStretch()

        btn_cancel = QtWidgets.QPushButton("✕ 取消")
        btn_cancel.setCursor(QtCore.Qt.PointingHandCursor)
        btn_cancel.setFixedHeight(24)
        btn_cancel.setStyleSheet(f"""
            QPushButton {{
                color: {CursorTheme.TEXT_SECONDARY};
                background: {CursorTheme.BG_TERTIARY};
                border: 1px solid {CursorTheme.BORDER};
                border-radius: 4px;
                padding: 0 12px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: {CursorTheme.BG_HOVER};
                color: {CursorTheme.TEXT_PRIMARY};
            }}
        """)
        btn_cancel.clicked.connect(self._on_cancel)
        btn_row.addWidget(btn_cancel)

        btn_confirm = QtWidgets.QPushButton("↵ 确认执行")
        btn_confirm.setCursor(QtCore.Qt.PointingHandCursor)
        btn_confirm.setFixedHeight(24)
        btn_confirm.setStyleSheet(f"""
            QPushButton {{
                color: {CursorTheme.TEXT_BRIGHT};
                background: {CursorTheme.ACCENT_GREEN};
                border: none;
                border-radius: 4px;
                padding: 0 14px;
                font-size: 12px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: #5fd9c0;
            }}
        """)
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
        lbl.setStyleSheet(f"color:{color};font-size:12px;padding:4px 0;")
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

    # 统一灰色标题样式（和 CollapsibleSection 默认一致）
    _HEADER_STYLE = f"""
        QPushButton {{
            color: {CursorTheme.TEXT_MUTED};
            font-size: 13px;
            font-family: {CursorTheme.FONT_CODE};
            text-align: left;
            padding: 3px 8px;
            border: none;
            background: transparent;
        }}
        QPushButton:hover {{
            background: {CursorTheme.BG_HOVER};
            color: {CursorTheme.TEXT_PRIMARY};
        }}
    """

    def __init__(self, tool_name: str, parent=None):
        super().__init__(tool_name, icon="", collapsed=True, parent=parent)
        self.tool_name = tool_name
        self._result = None
        self._success = None
        self._start_time = time.time()

        self.header.setStyleSheet(self._HEADER_STYLE)

        # 进度条（嵌入 content_layout 顶部，执行完毕后隐藏）
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setFixedHeight(2)
        self.progress_bar.setRange(0, 0)  # indeterminate
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background: {CursorTheme.BG_TERTIARY};
                border: none;
            }}
            QProgressBar::chunk {{
                background: {CursorTheme.ACCENT_BEIGE};
            }}
        """)
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
            self.header.setStyleSheet(f"""
                QPushButton {{
                    color: {CursorTheme.TEXT_BRIGHT};
                    font-size: 13px;
                    font-family: {CursorTheme.FONT_CODE};
                    text-align: left;
                    padding: 3px 8px;
                    border: none;
                    background: transparent;
                }}
                QPushButton:hover {{
                    background: {CursorTheme.BG_HOVER};
                }}
            """)

        # 添加结果文本（灰色，失败时白色）—— 节点路径可点击
        if result.strip():
            text_color = CursorTheme.TEXT_MUTED if success else CursorTheme.TEXT_BRIGHT
            rich_html = _linkify_node_paths_plain(result)
            self._result_label = QtWidgets.QLabel(rich_html)
            self._result_label.setWordWrap(True)
            self._result_label.setTextFormat(QtCore.Qt.RichText)
            self._result_label.setOpenExternalLinks(False)
            self._result_label.setTextInteractionFlags(
                QtCore.Qt.TextSelectableByMouse | QtCore.Qt.LinksAccessibleByMouse
            )
            self._result_label.linkActivated.connect(self._on_result_link)
            self._result_label.setStyleSheet(f"""
                color: {text_color};
                font-size: 12px;
                padding: 2px 4px;
                font-family: {CursorTheme.FONT_CODE};
            """)
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
        super().__init__("执行中...", icon="", collapsed=True, parent=parent)
        self._tool_calls: List[ToolCallItem] = []
        self._start_time = time.time()
        
        # 更新标题样式
        self.header.setStyleSheet(f"""
            QPushButton {{
                color: {CursorTheme.ACCENT_BEIGE};
                font-size: 16px;
                font-family: {CursorTheme.FONT_BODY};
                text-align: left;
                padding: 4px 8px;
                border: none;
                background: {CursorTheme.BG_TERTIARY};
            }}
            QPushButton:hover {{
                background: {CursorTheme.BG_HOVER};
            }}
        """)
    
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
            self.set_title(f"执行中... ({done}/{total})")
        else:
            elapsed = time.time() - self._start_time
            self.set_title(f"执行完成 ({total}个操作, {_fmt_duration(elapsed)})")
    
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
            self.set_title(f"执行完成 ({success} ok, {failed} err, {_fmt_duration(elapsed)})")
        else:
            self.set_title(f"执行完成 ({total}个操作, {_fmt_duration(elapsed)})")


# ============================================================
# 图片预览弹窗（点击缩略图放大查看）
# ============================================================

class ImagePreviewDialog(QtWidgets.QDialog):
    """模态图片预览弹窗 — 点击缩略图后弹出，显示原尺寸/自适应窗口的大图"""

    def __init__(self, pixmap: QtGui.QPixmap, parent=None):
        super().__init__(parent)
        self.setWindowTitle("图片预览")
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
        self.setStyleSheet(f"background: {CursorTheme.BG_PRIMARY};")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 可滚动区域
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setAlignment(QtCore.Qt.AlignCenter)
        scroll.setStyleSheet("QScrollArea { border: none; }")

        self._img_label = QtWidgets.QLabel()
        self._img_label.setAlignment(QtCore.Qt.AlignCenter)
        self._img_label.setStyleSheet("background: transparent;")
        scroll.setWidget(self._img_label)
        layout.addWidget(scroll)

        # 底栏：尺寸信息 + 关闭按钮
        bar = QtWidgets.QHBoxLayout()
        bar.setContentsMargins(12, 4, 12, 8)
        info = QtWidgets.QLabel(f"{pixmap.width()} × {pixmap.height()} px")
        info.setStyleSheet(f"color: {CursorTheme.TEXT_SECONDARY}; font-size: 12px;")
        bar.addWidget(info)
        bar.addStretch()
        close_btn = QtWidgets.QPushButton("关闭")
        close_btn.setStyleSheet(f"""
            QPushButton {{ background: {CursorTheme.BG_TERTIARY}; color: {CursorTheme.TEXT_PRIMARY};
                          border: 1px solid {CursorTheme.BORDER}; border-radius: 4px;
                          padding: 4px 16px; font-size: 13px; }}
            QPushButton:hover {{ background: {CursorTheme.BG_HOVER}; }}
        """)
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
        self.setToolTip("点击放大查看")

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
        self._container.setStyleSheet(f"""
            QWidget#userMsgContainer {{
                background: {CursorTheme.BG_TERTIARY};
                border-left: 3px solid {CursorTheme.BORDER_USER};
            }}
        """)
        self._container.setObjectName("userMsgContainer")
        container_layout = QtWidgets.QVBoxLayout(self._container)
        container_layout.setContentsMargins(12, 8, 12, 4)
        container_layout.setSpacing(2)

        # ---- 内容标签 ----
        self.content = QtWidgets.QLabel(text)
        self.content.setWordWrap(True)
        self.content.setTextFormat(QtCore.Qt.PlainText)
        self.content.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.content.setStyleSheet(f"""
            QLabel {{
                color: {CursorTheme.TEXT_BRIGHT};
                font-size: 16px;
                font-family: {CursorTheme.FONT_BODY};
                background: transparent;
            }}
        """)
        container_layout.addWidget(self.content)

        # ---- 展开/收起 按钮 ----
        self._toggle_btn = QtWidgets.QPushButton()
        self._toggle_btn.setFlat(True)
        self._toggle_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._toggle_btn.setFixedHeight(20)
        self._toggle_btn.setStyleSheet(f"""
            QPushButton {{
                color: {CursorTheme.TEXT_MUTED};
                font-size: 12px;
                font-family: {CursorTheme.FONT_BODY};
                border: none;
                background: transparent;
                text-align: left;
                padding: 0;
            }}
            QPushButton:hover {{
                color: {CursorTheme.ACCENT_BLUE};
            }}
        """)
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
        self._toggle_btn.setText(f"▶ 展开 ({remaining} 行更多)")

    def _apply_expanded(self):
        """应用展开状态：显示完整文本"""
        self.content.setText(self._full_text)
        self._toggle_btn.setText("▼ 收起")

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
        
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 8)
        layout.setSpacing(4)
        
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
        self.shell_section.header.setStyleSheet(f"""
            QPushButton {{
                color: {CursorTheme.ACCENT_YELLOW if hasattr(CursorTheme, 'ACCENT_YELLOW') else '#E5C07B'};
                font-size: 14px;
                font-family: {CursorTheme.FONT_BODY};
                text-align: left;
                padding: 4px 8px;
                border: none;
                background: {CursorTheme.BG_TERTIARY};
            }}
            QPushButton:hover {{
                background: {CursorTheme.BG_HOVER};
            }}
        """)
        layout.addWidget(self.shell_section)
        
        # === System Shell 区块（可折叠，默认折叠）===
        self._sys_shell_count = 0
        self.sys_shell_section = CollapsibleSection("System Shell", collapsed=True, parent=self)
        self.sys_shell_section.setVisible(False)
        self.sys_shell_section.header.setStyleSheet(f"""
            QPushButton {{
                color: {CursorTheme.ACCENT_GREEN};
                font-size: 14px;
                font-family: {CursorTheme.FONT_BODY};
                text-align: left;
                padding: 4px 8px;
                border: none;
                background: {CursorTheme.BG_TERTIARY};
            }}
            QPushButton:hover {{
                background: {CursorTheme.BG_HOVER};
            }}
        """)
        layout.addWidget(self.sys_shell_section)
        
        # === 总结/回复区域 ===
        self.summary_frame = QtWidgets.QFrame()
        self.summary_frame.setStyleSheet(f"""
            QFrame {{
                background: {CursorTheme.BG_SECONDARY};
                border-left: 3px solid {CursorTheme.BORDER_AI};
            }}
        """)
        summary_layout = QtWidgets.QVBoxLayout(self.summary_frame)
        summary_layout.setContentsMargins(8, 8, 6, 8)
        summary_layout.setSpacing(4)
        
        # 状态行（水平布局：状态文字 + 复制按钮）
        status_row = QtWidgets.QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(8)
        
        self.status_label = QtWidgets.QLabel("思考中...")
        self.status_label.setStyleSheet(f"""
            color: {CursorTheme.TEXT_MUTED};
            font-size: 13px;
            font-family: {CursorTheme.FONT_BODY};
        """)
        status_row.addWidget(self.status_label)
        status_row.addStretch()
        
        # 复制全部按钮（完成后才显示）
        self._copy_btn = QtWidgets.QPushButton("复制")
        self._copy_btn.setVisible(False)
        self._copy_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._copy_btn.setFixedHeight(22)
        self._copy_btn.setStyleSheet(f"""
            QPushButton {{
                color: {CursorTheme.TEXT_SECONDARY};
                background: transparent;
                border: 1px solid {CursorTheme.BORDER};
                border-radius: 4px;
                font-size: 12px;
                font-family: {CursorTheme.FONT_BODY};
                padding: 0 8px;
            }}
            QPushButton:hover {{
                color: {CursorTheme.TEXT_PRIMARY};
                background: {CursorTheme.BG_HOVER};
                border-color: {CursorTheme.ACCENT_BLUE};
            }}
        """)
        self._copy_btn.clicked.connect(self._copy_content)
        status_row.addWidget(self._copy_btn)
        
        summary_layout.addLayout(status_row)
        
        # 内容区域 —— 流式阶段使用 QPlainTextEdit（增量追加 O(1)），
        # finalize 时按需替换为 RichContentWidget（Markdown 渲染）。
        # QPlainTextEdit 的 insertPlainText 只做光标处插入，不会像 QLabel.setText
        # 那样每次重算全文 word-wrap，解决长回复流式卡顿问题。
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
        self.content_label.setStyleSheet(f"""
            QPlainTextEdit {{
                color: {CursorTheme.TEXT_PRIMARY};
                background: transparent;
                border: none;
                padding: 0px;
                font-size: 14px;
                font-family: {CursorTheme.FONT_BODY};
            }}
        """)
        # 初始高度紧凑，流式输入时自动增长
        self._content_line_h = QtGui.QFontMetrics(
            self.content_label.document().defaultFont()
        ).lineSpacing()
        self.content_label.setFixedHeight(self._content_line_h + 8)
        self.content_label.document().contentsChanged.connect(self._auto_resize_content)
        summary_layout.addWidget(self.content_label)
        
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
            # 首次收到思考内容时自动展开，让用户看到实时推理
            if self.thinking_section._collapsed:
                self.thinking_section.toggle()
        self.thinking_section.append_thinking(text)
    
    def update_thinking_time(self):
        """更新思考时间（思考结束后不再更新状态标签）"""
        if self._has_thinking:
            if self.thinking_section._finalized:
                return  # 思考已结束，不再更新
            self.thinking_section.update_time()
            total = self.thinking_section._total_elapsed()
            self.status_label.setText(f"思考中... ({_fmt_duration(total)})")
    
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
        self.status_label.setText(f"执行: {tool_name}")
    
    def add_tool_result(self, tool_name: str, result: str):
        """添加工具结果"""
        success = not result.startswith("[err]") and not result.startswith("错误")
        clean_result = result.removeprefix("[ok] ").removeprefix("[err] ")
        self.execution_section.set_tool_result(tool_name, clean_result, success)
    
    def _auto_resize_content(self):
        """根据文档实际行数自动调整 QPlainTextEdit 高度"""
        doc = self.content_label.document()
        # documentSize().height() 返回文档布局后的像素高度
        doc_height = int(doc.size().height())
        # 加上上下 margin
        new_h = doc_height + 8
        min_h = self._content_line_h + 8
        target = max(new_h, min_h)
        if target != self.content_label.maximumHeight():
            self.content_label.setFixedHeight(target)
    
    def append_content(self, text: str):
        """追加内容（流式场景高频调用，需要高效）
        
        使用 QPlainTextEdit.insertPlainText 增量追加，O(1) 复杂度，
        不触发全文 word-wrap 重算。
        """
        if not text.strip():
            return
        # 清除 U+FFFD 替换符（encoding 异常残留）
        if '\ufffd' in text:
            text = text.replace('\ufffd', '')
        self._content += text
        # ★ 增量追加：移到文档末尾插入，不重排之前的文本
        cursor = self.content_label.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        cursor.insertText(text)
        self.content_label.setTextCursor(cursor)
    
    def set_content(self, text: str):
        """设置内容（一次性，非流式场景）"""
        self._content = text
        self.content_label.setPlainText(self._clean_content(text))
    
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
            self._copy_btn.setText("已复制")
            self._copy_btn.setStyleSheet(f"""
                QPushButton {{
                    color: {CursorTheme.ACCENT_GREEN};
                    background: transparent;
                    border: 1px solid {CursorTheme.ACCENT_GREEN};
                    border-radius: 4px;
                    font-size: 12px;
                    font-family: {CursorTheme.FONT_BODY};
                    padding: 0 8px;
                }}
            """)
            QtCore.QTimer.singleShot(1500, self._reset_copy_btn)
    
    def _reset_copy_btn(self):
        """恢复复制按钮样式"""
        try:
            self._copy_btn.setText("复制")
            self._copy_btn.setStyleSheet(f"""
                QPushButton {{
                    color: {CursorTheme.TEXT_SECONDARY};
                    background: transparent;
                    border: 1px solid {CursorTheme.BORDER};
                    border-radius: 4px;
                    font-size: 12px;
                    font-family: {CursorTheme.FONT_BODY};
                    padding: 0 8px;
                }}
                QPushButton:hover {{
                    color: {CursorTheme.TEXT_PRIMARY};
                    background: {CursorTheme.BG_HOVER};
                    border-color: {CursorTheme.ACCENT_BLUE};
                }}
            """)
        except RuntimeError:
            pass  # widget 已销毁
    
    def finalize(self):
        """完成回复 - 提取最终总结"""
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
            parts.append("思考")
        if self._has_execution:
            tool_count = len(self.execution_section._tool_calls)
            parts.append(f"{tool_count}次调用")
        
        status_text = f"完成 ({_fmt_duration(elapsed)})"
        if parts:
            status_text += f" | {', '.join(parts)}"
        
        self.status_label.setText(status_text)
        
        # 有内容时显示复制按钮
        if self._clean_content(self._content):
            self._copy_btn.setVisible(True)
        
        # 处理最终内容 — 使用富文本渲染
        content = self._clean_content(self._content)
        
        if not content:
            if self._has_execution:
                self.content_label.setPlainText("执行完成，详见上方执行过程。")
            else:
                self.content_label.setPlainText("（无回复内容）")
            self.content_label.setStyleSheet(f"""
                QPlainTextEdit {{
                    color: {CursorTheme.TEXT_MUTED};
                    background: transparent;
                    border: none;
                    font-size: 13px;
                }}
            """)
        else:
            # 始终显示完整回复内容（不折叠）
            has_node_path = bool(_NODE_PATH_RE.search(content))
            if SimpleMarkdown.has_rich_content(content) or has_node_path:
                self.content_label.setVisible(False)
                rich = RichContentWidget(content, self.summary_frame)
                rich.createWrangleRequested.connect(self.createWrangleRequested.emit)
                rich.nodePathClicked.connect(self.nodePathClicked.emit)
                self.summary_frame.layout().addWidget(rich)
            else:
                self.content_label.setPlainText(content)
    
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
        self.setStyleSheet(f"""
            QLabel {{
                color: {CursorTheme.TEXT_MUTED};
                font-size: 16px;
                padding: 1px 0;
            }}
        """)
        self.setWordWrap(True)


# ============================================================
# 节点操作标签
# ============================================================

class NodeOperationLabel(QtWidgets.QWidget):
    """节点操作标签 - 显示 +1 node / -2 nodes，带 undo/keep 按钮"""
    
    nodeClicked = QtCore.Signal(str)      # 发送节点路径（点击节点名跳转）
    undoRequested = QtCore.Signal()       # 请求撤销此操作
    decided = QtCore.Signal()             # undo 或 keep 完成后通知（用于更新批量操作栏）
    
    _BTN_STYLE = f"""
        QPushButton {{{{
            color: {{color}};
            font-size: 11px;
            font-family: {CursorTheme.FONT_BODY};
            padding: 1px 6px;
            border: 1px solid {{border}};
            border-radius: 3px;
            background: transparent;
        }}}}
        QPushButton:hover {{{{
            background: {{hover}};
        }}}}
        QPushButton:disabled {{{{
            color: {CursorTheme.TEXT_MUTED};
            border-color: transparent;
        }}}}
    """
    
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
        count_label.setStyleSheet(f"""
            QLabel {{
                color: {color};
                font-size: 13px;
                font-family: {CursorTheme.FONT_BODY};
                font-weight: bold;
                padding: 2px 6px;
                background: {CursorTheme.BG_TERTIARY};
                border-radius: 3px;
            }}
        """)
        layout.addWidget(count_label)
        
        # 每个节点名作为可点击按钮
        display_paths = self._node_paths[:5]
        for path in display_paths:
            short_name = path.rsplit('/', 1)[-1] if '/' in path else path
            btn = QtWidgets.QPushButton(short_name)
            btn.setFlat(True)
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            btn.setToolTip(f"点击跳转: {path}")
            btn.setStyleSheet(f"""
                QPushButton {{
                    color: {CursorTheme.ACCENT_BEIGE};
                    font-size: 12px;
                    font-family: {CursorTheme.FONT_CODE};
                    padding: 1px 4px;
                    border: 1px solid transparent;
                    text-decoration: underline;
                }}
                QPushButton:hover {{
                    color: {CursorTheme.TEXT_BRIGHT};
                    background: {CursorTheme.BG_HOVER};
                    border-color: {CursorTheme.BORDER};
                }}
            """)
            btn.clicked.connect(lambda checked=False, p=path: self.nodeClicked.emit(p))
            layout.addWidget(btn)
        
        if len(self._node_paths) > 5:
            more = QtWidgets.QLabel(f"+{len(self._node_paths) - 5} more")
            more.setStyleSheet(f"color: {CursorTheme.TEXT_MUTED}; font-size: 10px;")
            layout.addWidget(more)
        
        # 简单文本详情（仅在没有 param_diff 时使用）
        if detail_text:
            detail_label = QtWidgets.QLabel(detail_text)
            detail_label.setStyleSheet(f"""
                color: {CursorTheme.TEXT_SECONDARY};
                font-size: 11px;
                font-family: {CursorTheme.FONT_CODE};
            """)
            detail_label.setToolTip(detail_text)
            layout.addWidget(detail_label)
        
        layout.addStretch()
        
        # ── Undo / Keep 按钮 ──
        self._undo_btn = QtWidgets.QPushButton("undo")
        self._undo_btn.setFixedHeight(20)
        self._undo_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._undo_btn.setStyleSheet(self._BTN_STYLE.format(
            color=CursorTheme.ACCENT_RED, border=CursorTheme.ACCENT_RED,
            hover=CursorTheme.BG_HOVER))
        self._undo_btn.clicked.connect(self._on_undo)
        layout.addWidget(self._undo_btn)
        
        self._keep_btn = QtWidgets.QPushButton("keep")
        self._keep_btn.setFixedHeight(20)
        self._keep_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._keep_btn.setStyleSheet(self._BTN_STYLE.format(
            color=CursorTheme.ACCENT_GREEN, border=CursorTheme.ACCENT_GREEN,
            hover=CursorTheme.BG_HOVER))
        self._keep_btn.clicked.connect(self._on_keep)
        layout.addWidget(self._keep_btn)
        
        # 决定后的状态标签（替代按钮）
        self._status_label = QtWidgets.QLabel()
        self._status_label.setStyleSheet(f"""
            color: {CursorTheme.TEXT_MUTED};
            font-size: 11px;
            font-family: {CursorTheme.FONT_BODY};
        """)
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
        count_label.setStyleSheet(f"""
            QLabel {{
                color: {color};
                font-size: 13px;
                font-family: {CursorTheme.FONT_BODY};
                font-weight: bold;
                padding: 2px 6px;
                background: {CursorTheme.BG_TERTIARY};
                border-radius: 3px;
            }}
        """)
        header.addWidget(count_label)
        
        for path in self._node_paths[:3]:
            short_name = path.rsplit('/', 1)[-1] if '/' in path else path
            btn = QtWidgets.QPushButton(short_name)
            btn.setFlat(True)
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            btn.setToolTip(f"点击跳转: {path}")
            btn.setStyleSheet(f"""
                QPushButton {{
                    color: {CursorTheme.ACCENT_BEIGE};
                    font-size: 12px;
                    font-family: {CursorTheme.FONT_CODE};
                    padding: 1px 4px;
                    border: 1px solid transparent;
                    text-decoration: underline;
                }}
                QPushButton:hover {{
                    color: {CursorTheme.TEXT_BRIGHT};
                    background: {CursorTheme.BG_HOVER};
                    border-color: {CursorTheme.BORDER};
                }}
            """)
            btn.clicked.connect(lambda checked=False, p=path: self.nodeClicked.emit(p))
            header.addWidget(btn)
        
        header.addStretch()
        
        self._undo_btn = QtWidgets.QPushButton("undo")
        self._undo_btn.setFixedHeight(20)
        self._undo_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._undo_btn.setStyleSheet(self._BTN_STYLE.format(
            color=CursorTheme.ACCENT_RED, border=CursorTheme.ACCENT_RED,
            hover=CursorTheme.BG_HOVER))
        self._undo_btn.clicked.connect(self._on_undo)
        header.addWidget(self._undo_btn)
        
        self._keep_btn = QtWidgets.QPushButton("keep")
        self._keep_btn.setFixedHeight(20)
        self._keep_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._keep_btn.setStyleSheet(self._BTN_STYLE.format(
            color=CursorTheme.ACCENT_GREEN, border=CursorTheme.ACCENT_GREEN,
            hover=CursorTheme.BG_HOVER))
        self._keep_btn.clicked.connect(self._on_keep)
        header.addWidget(self._keep_btn)
        
        self._status_label = QtWidgets.QLabel()
        self._status_label.setStyleSheet(f"""
            color: {CursorTheme.TEXT_MUTED};
            font-size: 11px;
            font-family: {CursorTheme.FONT_BODY};
        """)
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
        self._status_label.setText("已撤销")
        self._status_label.setStyleSheet(f"""
            color: {CursorTheme.ACCENT_RED};
            font-size: 11px;
            font-family: {CursorTheme.FONT_BODY};
        """)
        self._status_label.setVisible(True)
        self.undoRequested.emit()
        self.decided.emit()
    
    def _on_keep(self):
        if self._decided:
            return
        self._decided = True
        self._undo_btn.setVisible(False)
        self._keep_btn.setVisible(False)
        self._status_label.setText("已保留")
        self._status_label.setVisible(True)
        self.decided.emit()


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
    _GREY_TEXT = "#6a6a6a"    # 上下文行文字
    
    # 行级通用样式（紧凑无间隙，像一个完整代码块）
    _LINE_BASE = (
        "font-size: 11px; font-family: {font}; "
        "margin: 0px; padding: 0px 6px; "
        "border: none; border-radius: 0px; "
        "min-height: 16px; max-height: 16px;"
    )

    def __init__(self, param_name: str, old_value, new_value, parent=None):
        super().__init__(parent)
        self._collapsed = False  # ★ 默认展开
        
        old_str = self._to_str(old_value)
        new_str = self._to_str(new_value)
        is_multiline = ('\n' in old_str or '\n' in new_str
                        or len(old_str) > 60 or len(new_str) > 60)
        
        root_layout = QtWidgets.QVBoxLayout(self)
        root_layout.setContentsMargins(0, 2, 0, 2)
        root_layout.setSpacing(0)
        
        if is_multiline:
            # ── 多行 diff (VEX 等) ──
            # 标题行: param_name ▼ （默认展开，可手动折叠）
            self._title_text = param_name
            self._toggle_btn = QtWidgets.QPushButton(f"▼ {param_name}")
            self._toggle_btn.setFlat(True)
            self._toggle_btn.setCursor(QtCore.Qt.PointingHandCursor)
            self._toggle_btn.setStyleSheet(f"""
                QPushButton {{
                    color: {CursorTheme.ACCENT_YELLOW};
                    font-size: 11px;
                    font-family: {CursorTheme.FONT_CODE};
                    text-align: left;
                    padding: 1px 4px;
                    border: none;
                    background: transparent;
                }}
                QPushButton:hover {{
                    color: {CursorTheme.TEXT_BRIGHT};
                }}
            """)
            self._toggle_btn.clicked.connect(self._toggle)
            root_layout.addWidget(self._toggle_btn)
            
            # diff 内容区（默认展开）
            self._diff_frame = QtWidgets.QFrame()
            self._diff_frame.setStyleSheet(f"""
                QFrame {{
                    background: {CursorTheme.BG_PRIMARY};
                    border: 1px solid {CursorTheme.BORDER};
                    border-radius: 3px;
                }}
            """)
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
                self._add_block(diff_layout, "旧值", old_str, is_old=True)
                self._add_block(diff_layout, "新值", new_str, is_old=False)
            else:
                for line in diff_body:
                    line_stripped = line.rstrip('\n')
                    if line.startswith('@@'):
                        lbl = QtWidgets.QLabel(line_stripped)
                        lbl.setStyleSheet(
                            f"color: {CursorTheme.ACCENT_PURPLE}; "
                            f"background: {CursorTheme.BG_TERTIARY}; "
                            + self._LINE_BASE.format(font=_font)
                        )
                        diff_layout.addWidget(lbl)
                    elif line.startswith('-'):
                        lbl = QtWidgets.QLabel(line_stripped)
                        lbl.setStyleSheet(
                            f"color: {self._RED_TEXT}; "
                            f"background: {self._RED_BG}; "
                            + self._LINE_BASE.format(font=_font)
                        )
                        diff_layout.addWidget(lbl)
                    elif line.startswith('+'):
                        lbl = QtWidgets.QLabel(line_stripped)
                        lbl.setStyleSheet(
                            f"color: {self._GREEN_TEXT}; "
                            f"background: {self._GREEN_BG}; "
                            + self._LINE_BASE.format(font=_font)
                        )
                        diff_layout.addWidget(lbl)
                    else:
                        lbl = QtWidgets.QLabel(line_stripped)
                        lbl.setStyleSheet(
                            f"color: {self._GREY_TEXT}; "
                            f"background: {CursorTheme.BG_PRIMARY}; "
                            + self._LINE_BASE.format(font=_font)
                        )
                        diff_layout.addWidget(lbl)
            
            self._diff_frame.setVisible(True)  # ★ 默认展开
            root_layout.addWidget(self._diff_frame)
        else:
            # ── 内联 diff (标量) ──
            inline = QtWidgets.QHBoxLayout()
            inline.setContentsMargins(0, 0, 0, 0)
            inline.setSpacing(4)
            
            # 参数名
            name_lbl = QtWidgets.QLabel(f"{param_name}:")
            name_lbl.setStyleSheet(f"""
                color: {CursorTheme.TEXT_SECONDARY};
                font-size: 11px;
                font-family: {CursorTheme.FONT_CODE};
            """)
            inline.addWidget(name_lbl)
            
            # 旧值 (红框)
            old_lbl = QtWidgets.QLabel(self._truncate(old_str, 30))
            old_lbl.setToolTip(f"旧值: {old_str}")
            old_lbl.setStyleSheet(f"""
                QLabel {{
                    color: {self._RED_TEXT};
                    background: {self._RED_BG};
                    border: 1px solid {self._RED_BORDER};
                    border-radius: 3px;
                    font-size: 11px;
                    font-family: {CursorTheme.FONT_CODE};
                    padding: 1px 6px;
                }}
            """)
            inline.addWidget(old_lbl)
            
            # 箭头
            arrow = QtWidgets.QLabel("→")
            arrow.setStyleSheet(f"""
                color: {CursorTheme.TEXT_MUTED};
                font-size: 11px;
            """)
            inline.addWidget(arrow)
            
            # 新值 (绿框)
            new_lbl = QtWidgets.QLabel(self._truncate(new_str, 30))
            new_lbl.setToolTip(f"新值: {new_str}")
            new_lbl.setStyleSheet(f"""
                QLabel {{
                    color: {self._GREEN_TEXT};
                    background: {self._GREEN_BG};
                    border: 1px solid {self._GREEN_BORDER};
                    border-radius: 3px;
                    font-size: 11px;
                    font-family: {CursorTheme.FONT_CODE};
                    padding: 1px 6px;
                }}
            """)
            inline.addWidget(new_lbl)
            
            root_layout.addLayout(inline)
    
    def _toggle(self):
        self._collapsed = not self._collapsed
        self._diff_frame.setVisible(not self._collapsed)
        arrow = "▶" if self._collapsed else "▼"
        self._toggle_btn.setText(f"{arrow} {self._title_text}")
    
    def collapse(self):
        """外部调用：强制折叠 diff（仅对多行 diff 有效）"""
        if hasattr(self, '_diff_frame') and not self._collapsed:
            self._collapsed = True
            self._diff_frame.setVisible(False)
            self._toggle_btn.setText(f"▶ {self._title_text}")
    
    def _add_block(self, parent_layout, title: str, text: str, is_old: bool):
        """添加旧值/新值整块（用于 difflib 无差异时的 fallback）"""
        if is_old:
            bg, fg = self._RED_BG, self._RED_TEXT
        else:
            bg, fg = self._GREEN_BG, self._GREEN_TEXT
        _font = CursorTheme.FONT_CODE
        header = QtWidgets.QLabel(title)
        header.setStyleSheet(
            f"color: {fg}; background: {CursorTheme.BG_TERTIARY}; "
            + self._LINE_BASE.format(font=_font)
        )
        parent_layout.addWidget(header)
        for line in text.splitlines():
            lbl = QtWidgets.QLabel(line)
            lbl.setStyleSheet(
                f"color: {fg}; background: {bg}; "
                + self._LINE_BASE.format(font=_font)
            )
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
        self.title_btn.setStyleSheet(f"""
            QPushButton {{
                color: {CursorTheme.TEXT_MUTED};
                font-size: 16px;
                font-family: {CursorTheme.FONT_BODY};
                text-align: left;
                padding: 2px 0;
                border: none;
                background: transparent;
            }}
            QPushButton:hover {{
                color: {CursorTheme.TEXT_PRIMARY};
            }}
        """)
        layout.addWidget(self.title_btn)
        
        self.content_label = QtWidgets.QLabel(content)
        self.content_label.setWordWrap(True)
        self.content_label.setStyleSheet(f"""
            QLabel {{
                color: {CursorTheme.TEXT_SECONDARY};
                font-size: 14px;
                font-family: {CursorTheme.FONT_CODE};
                padding: 4px 0 4px 12px;
                background: {CursorTheme.BG_TERTIARY};
            }}
        """)
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
        self.title.setStyleSheet(f"""
            QLabel {{
                color: {CursorTheme.ACCENT_BEIGE};
                font-size: 16px;
                font-family: {CursorTheme.FONT_BODY};
                font-weight: bold;
                padding: 2px 0;
            }}
        """)
        layout.addWidget(self.title)
        
        self.steps_layout = QtWidgets.QVBoxLayout()
        self.steps_layout.setSpacing(1)
        layout.addLayout(self.steps_layout)
    
    def add_step(self, text: str, status: str = "pending") -> QtWidgets.QLabel:
        icons = {"pending": "○", "running": "◎", "done": "●", "error": "✗"}
        colors = {
            "pending": CursorTheme.TEXT_MUTED,
            "running": CursorTheme.ACCENT_BLUE,
            "done": CursorTheme.ACCENT_GREEN,
            "error": CursorTheme.ACCENT_RED
        }
        
        label = QtWidgets.QLabel(f"{icons[status]} {text}")
        label.setStyleSheet(f"""
            QLabel {{
                color: {colors[status]};
                font-size: 16px;
                padding: 1px 0 1px 8px;
            }}
        """)
        self.steps_layout.addWidget(label)
        self._steps.append((label, text))
        return label
    
    def update_step(self, index: int, status: str):
        if 0 <= index < len(self._steps):
            label, text = self._steps[index]
            icons = {"pending": "○", "running": "◎", "done": "●", "error": "✗"}
            colors = {
                "pending": CursorTheme.TEXT_MUTED,
                "running": CursorTheme.ACCENT_BLUE,
                "done": CursorTheme.ACCENT_GREEN,
                "error": CursorTheme.ACCENT_RED
            }
            label.setText(f"{icons[status]} {text}")
            label.setStyleSheet(f"""
                QLabel {{
                    color: {colors[status]};
                    font-size: 16px;
                    padding: 1px 0 1px 8px;
                }}
            """)


# ============================================================
# Markdown 解析器（专业版）
# ============================================================

class SimpleMarkdown:
    """将 Markdown 转换为 Qt Rich Text HTML

    支持特性：
    - 标题 (# ~ ####)
    - 粗体 / 斜体 / 删除线 / 行内代码
    - 无序列表 / 有序列表 / 任务列表
    - 引用块（多行合并）
    - 表格（居中 / 左对齐 / 右对齐）
    - 水平分割线
    - 链接 [text](url)
    - 围栏代码块（交给 CodeBlockWidget）
    """

    _CODE_BLOCK_RE = re.compile(r'```(\w*)\n(.*?)```', re.DOTALL)
    _TABLE_SEP_RE = re.compile(r'^\|?\s*[-:]+[-| :]*$')  # 表头分割行

    # -------- 公共接口 --------

    @classmethod
    def parse_segments(cls, text: str) -> list:
        """将文本拆分为 ('text', html) 和 ('code', lang, raw_code) 段落"""
        segments: list = []
        last = 0
        for m in cls._CODE_BLOCK_RE.finditer(text):
            before = text[last:m.start()]
            if before.strip():
                segments.append(('text', cls._text_to_html(before)))
            segments.append(('code', m.group(1) or '', m.group(2).rstrip()))
            last = m.end()
        after = text[last:]
        if after.strip():
            segments.append(('text', cls._text_to_html(after)))
        if not segments and text.strip():
            segments.append(('text', cls._text_to_html(text)))
        return segments

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
        return False

    # -------- 块级解析 --------

    @classmethod
    def _text_to_html(cls, text: str) -> str:
        lines = text.split('\n')
        out: list = []
        i = 0
        n = len(lines)

        # 当前列表状态栈
        in_list = False
        tag = ''
        # 引用块缓冲
        quote_buf: list = []

        def _flush_list():
            nonlocal in_list, tag
            if in_list:
                out.append(f'</{tag}>')
                in_list = False
                tag = ''

        def _flush_quote():
            nonlocal quote_buf
            if quote_buf:
                q_html = '<br>'.join(cls._inline(q) for q in quote_buf)
                out.append(
                    f'<div style="border-left:3px solid {CursorTheme.ACCENT_BEIGE};padding:6px 12px;'
                    f'margin:6px 0;background:#2a2520;'
                    f'color:#b0a090;font-style:italic;">{q_html}</div>'
                )
                quote_buf = []

        while i < n:
            s = lines[i].strip()

            # ---- empty line ----
            if not s:
                _flush_quote()
                _flush_list()
                out.append('<div style="height:6px;"></div>')
                i += 1
                continue

            # ---- horizontal rule ----
            if re.match(r'^[-*_]{3,}\s*$', s):
                _flush_quote()
                _flush_list()
                out.append(
                    '<hr style="border:none;border-top:1px solid #3c3c3c;margin:10px 0;">'
                )
                i += 1
                continue

            # ---- table ----
            if '|' in s and i + 1 < n and cls._TABLE_SEP_RE.match(lines[i + 1].strip()):
                _flush_quote()
                _flush_list()
                table_html = cls._parse_table(lines, i)
                if table_html:
                    out.append(table_html[0])
                    i = table_html[1]
                    continue

            # ---- headers ----
            header_match = re.match(r'^(#{1,4})\s+(.+)', s)
            if header_match:
                _flush_quote()
                _flush_list()
                lvl = len(header_match.group(1))
                content = header_match.group(2)
                # 层级样式
                styles = {
                    1: ('18px', '#e0e0ff', '600', '14px 0 6px 0', f'border-bottom:1px solid #3c3c3c;padding-bottom:6px;'),
                    2: ('16px', '#c8d8e8', '600', '12px 0 4px 0', ''),
                    3: ('14px', '#b0c0d0', '600', '10px 0 3px 0', ''),
                    4: ('13px', '#98a8b8', '500', '8px 0 2px 0', ''),
                }
                sz, clr, wt, mg, extra = styles[lvl]
                out.append(
                    f'<p style="font-size:{sz};font-weight:{wt};'
                    f'color:{clr};margin:{mg};{extra}">'
                    f'{cls._inline(content)}</p>'
                )
                i += 1
                continue

            # ---- blockquote (合并连续行) ----
            if s.startswith('> '):
                _flush_list()
                quote_buf.append(s[2:])
                i += 1
                continue
            elif s.startswith('>'):
                _flush_list()
                quote_buf.append(s[1:].lstrip())
                i += 1
                continue
            else:
                _flush_quote()

            # ---- task list ----
            task_match = re.match(r'^[-*]\s+\[([ xX])\]\s+(.*)', s)
            if task_match:
                if not in_list or tag != 'ul':
                    _flush_list()
                    out.append(
                        '<ul style="margin:4px 0;padding-left:4px;list-style:none;">'
                    )
                    in_list, tag = True, 'ul'
                checked = task_match.group(1) in ('x', 'X')
                box = (
                    '<span style="color:#4ec9b0;font-weight:bold;margin-right:4px;">[x]</span>'
                    if checked else
                    '<span style="color:#6a6a6a;margin-right:4px;">[ ]</span>'
                )
                text_style = 'color:#6a6a6a;text-decoration:line-through;' if checked else ''
                out.append(
                    f'<li style="margin:2px 0;{text_style}">'
                    f'{box}{cls._inline(task_match.group(2))}</li>'
                )
                i += 1
                continue

            # ---- unordered list ----
            if s.startswith('- ') or s.startswith('* '):
                if not in_list or tag != 'ul':
                    _flush_list()
                    out.append(
                        '<ul style="margin:4px 0;padding-left:20px;'
                        'list-style-type:disc;color:#858585;">'
                    )
                    in_list, tag = True, 'ul'
                out.append(
                    f'<li style="margin:3px 0;color:{CursorTheme.TEXT_PRIMARY};">'
                    f'{cls._inline(s[2:])}</li>'
                )
                i += 1
                continue

            # ---- ordered list ----
            ol_match = re.match(r'^(\d+)\.\s+(.+)', s)
            if ol_match:
                if not in_list or tag != 'ol':
                    _flush_list()
                    out.append(
                        '<ol style="margin:4px 0;padding-left:20px;color:#858585;">'
                    )
                    in_list, tag = True, 'ol'
                out.append(
                    f'<li style="margin:3px 0;color:{CursorTheme.TEXT_PRIMARY};">'
                    f'{cls._inline(ol_match.group(2))}</li>'
                )
                i += 1
                continue

            # ---- normal paragraph ----
            _flush_list()
            out.append(
                f'<p style="margin:3px 0;line-height:1.6;">'
                f'{cls._inline(s)}</p>'
            )
            i += 1

        _flush_quote()
        _flush_list()
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

        # 生成 HTML
        tbl = [
            '<table style="border-collapse:collapse;margin:8px 0;width:100%;'
            'font-size:13px;">'
        ]

        # thead
        tbl.append('<tr>')
        for ci, h in enumerate(headers):
            align = aligns[ci] if ci < len(aligns) else 'left'
            tbl.append(
                f'<th style="border:1px solid #3c3c3c;padding:6px 10px;'
                f'background:#2a2a3a;color:#c8d8e8;font-weight:600;'
                f'text-align:{align};">{cls._inline(h)}</th>'
            )
        tbl.append('</tr>')

        # tbody
        for ri, row in enumerate(rows):
            bg = '#1e1e2e' if ri % 2 == 0 else '#252535'
            tbl.append('<tr>')
            for ci, cell in enumerate(row):
                align = aligns[ci] if ci < len(aligns) else 'left'
                tbl.append(
                    f'<td style="border:1px solid #3c3c3c;padding:5px 10px;'
                    f'background:{bg};color:{CursorTheme.TEXT_PRIMARY};'
                    f'text-align:{align};">{cls._inline(cell)}</td>'
                )
            tbl.append('</tr>')

        tbl.append('</table>')
        return ('\n'.join(tbl), j)

    # -------- 行内解析 --------

    @classmethod
    def _inline(cls, text: str) -> str:
        """行内格式: **粗体**, *斜体*, ~~删除线~~, `代码`, [链接](url), 节点路径"""
        text = html.escape(text)
        # 链接 [text](url)
        text = re.sub(
            r'\[([^\]]+?)\]\(([^)]+?)\)',
            r'<a href="\2" style="color:#4e8fca;text-decoration:none;">\1</a>',
            text,
        )
        # 粗体
        text = re.sub(r'\*\*(.+?)\*\*', r'<b style="color:#e0e0e0;">\1</b>', text)
        # 删除线
        text = re.sub(r'~~(.+?)~~', r'<s style="color:#6a6a6a;">\1</s>', text)
        # 斜体
        text = re.sub(r'(?<!\*)\*([^*]+?)\*(?!\*)', r'<i style="color:#b8c8d8;">\1</i>', text)
        # 行内代码
        text = re.sub(
            r'`([^`]+?)`',
            r'<code style="background:#2a2a3a;padding:2px 6px;border-radius:3px;'
            r'font-family:Consolas,Monaco,monospace;color:#ce9178;'
            r'font-size:0.9em;border:1px solid #3a3a4a;">\1</code>',
            text,
        )
        # Houdini 节点路径 → 可点击链接
        text = _linkify_node_paths(text)
        return text


# ============================================================
# 语法高亮器
# ============================================================

class SyntaxHighlighter:
    """代码语法高亮 (VEX / Python) — 基于 token 的着色"""

    COL = {
        'keyword': '#569CD6',
        'type':    '#4EC9B0',
        'builtin': '#DCDCAA',
        'string':  '#CE9178',
        'comment': '#6A9955',
        'number':  '#B5CEA8',
        'attr':    '#9CDCFE',
    }

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

    @classmethod
    def highlight_vex(cls, code: str) -> str:
        return cls._tokenize(code, cls.VEX_KW, cls.VEX_TY, cls.VEX_BI,
                              '//', ('/*', '*/'), '@')

    @classmethod
    def highlight_python(cls, code: str) -> str:
        return cls._tokenize(code, cls.PY_KW, frozenset(), cls.PY_BI,
                              '#', None, None)

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
            if c in ('"', "'"):
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
            # --- VEX attribute (@P etc.) ---
            if (attr_prefix and c == attr_prefix
                    and i + 1 < n and (code[i + 1].isalpha() or code[i + 1] == '_')):
                j = i + 1
                while j < n and (code[j].isalnum() or code[j] in ('_', '.')):
                    j += 1
                parts.append(cls._span('attr', code[i:j]))
                i = j
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
            # --- number ---
            if c.isdigit() or (c == '.' and i + 1 < n and code[i + 1].isdigit()):
                j = i
                while j < n and (code[j].isdigit() or code[j] == '.'):
                    j += 1
                parts.append(cls._span('number', code[i:j]))
                i = j
                continue
            parts.append(html.escape(c))
            i += 1
        return ''.join(parts)

    @classmethod
    def _span(cls, tok_type: str, text: str) -> str:
        return f'<span style="color:{cls.COL[tok_type]};">{html.escape(text)}</span>'


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

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # ── QTextEdit（输出内容）──
        self._text = QtWidgets.QTextEdit()
        self._text.setReadOnly(True)
        self._text.setLineWrapMode(QtWidgets.QTextEdit.NoWrap)
        self._text.setStyleSheet(f"""
            QTextEdit {{
                background: {bg_color};
                color: {CursorTheme.TEXT_PRIMARY};
                border: none;
                padding: 6px 8px;
                font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
                font-size: 12px;
            }}
            QTextEdit QScrollBar:vertical {{ width:5px; background:transparent; }}
            QTextEdit QScrollBar::handle:vertical {{ background:#3c3c3c; border-radius:2px; }}
            QTextEdit QScrollBar:horizontal {{ height:5px; background:transparent; }}
            QTextEdit QScrollBar::handle:horizontal {{ background:#3c3c3c; border-radius:2px; }}
        """)
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
            self._toggle.setStyleSheet(f"""
                QLabel {{
                    background: {bg_color};
                    color: {CursorTheme.ACCENT_BLUE};
                    font-size: 11px;
                    padding: 3px 8px;
                    border-top: 1px solid {CursorTheme.BORDER};
                    font-family: {CursorTheme.FONT_CODE};
                }}
                QLabel:hover {{
                    color: {CursorTheme.TEXT_PRIMARY};
                    background: #1e1e3e;
                }}
            """)
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
        
        border_color = CursorTheme.ACCENT_BEIGE if success else CursorTheme.ACCENT_RED
        self.setStyleSheet(f"""
            #PythonShellWidget {{
                background: #1a1a2e;
                border: 1px solid {CursorTheme.BORDER};
                border-left: 3px solid {border_color};
            }}
        """)
        
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # ---- header: Python Shell + 执行时间 ----
        header = QtWidgets.QWidget()
        header.setStyleSheet("background:#252535;")
        hl = QtWidgets.QHBoxLayout(header)
        hl.setContentsMargins(8, 4, 8, 4)
        hl.setSpacing(6)
        
        title_lbl = QtWidgets.QLabel("PYTHON SHELL")
        title_lbl.setStyleSheet(
            f"color:{CursorTheme.ACCENT_BEIGE};font-size:11px;font-weight:bold;"
            f"font-family:{CursorTheme.FONT_CODE};"
        )
        hl.addWidget(title_lbl)
        
        hl.addStretch()
        
        if exec_time > 0:
            time_lbl = QtWidgets.QLabel(f"{exec_time:.2f}s")
            time_lbl.setStyleSheet(f"color:{CursorTheme.TEXT_MUTED};font-size:11px;")
            hl.addWidget(time_lbl)
        
        status_lbl = QtWidgets.QLabel("ok" if success else "err")
        status_color = CursorTheme.ACCENT_GREEN if success else CursorTheme.ACCENT_RED
        status_lbl.setStyleSheet(
            f"color:{status_color};font-size:11px;font-weight:bold;"
            f"font-family:Consolas,Monaco,monospace;"
        )
        hl.addWidget(status_lbl)
        
        layout.addWidget(header)
        
        # ---- 代码区域 ----
        code_widget = QtWidgets.QTextEdit()
        code_widget.setReadOnly(True)
        code_widget.setLineWrapMode(QtWidgets.QTextEdit.NoWrap)
        code_widget.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        code_widget.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        code_widget.setStyleSheet(f"""
            QTextEdit {{
                background: #1e1e3a;
                color: {CursorTheme.TEXT_PRIMARY};
                border: none;
                border-bottom: 1px solid {CursorTheme.BORDER};
                padding: 6px 8px;
                font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
                font-size: 13px;
            }}
            QTextEdit QScrollBar:vertical {{ width:5px; background:transparent; }}
            QTextEdit QScrollBar::handle:vertical {{ background:#3c3c3c; border-radius:2px; }}
            QTextEdit QScrollBar:horizontal {{ height:5px; background:transparent; }}
            QTextEdit QScrollBar::handle:horizontal {{ background:#3c3c3c; border-radius:2px; }}
        """)
        
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
            err_label.setStyleSheet(
                f"color:{CursorTheme.ACCENT_RED};font-size:12px;padding:6px 8px;"
            )
            layout.addWidget(err_label)


class SystemShellWidget(QtWidgets.QFrame):
    """System Shell 执行结果 — 显示命令 + stdout/stderr + 退出码"""

    def __init__(self, command: str, output: str = "", error: str = "",
                 exit_code: int = 0, exec_time: float = 0.0,
                 success: bool = True, cwd: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("SystemShellWidget")

        border_color = CursorTheme.ACCENT_GREEN if success else CursorTheme.ACCENT_RED
        self.setStyleSheet(f"""
            #SystemShellWidget {{
                background: #1a1a1a;
                border: 1px solid {CursorTheme.BORDER};
                border-left: 3px solid {border_color};
            }}
        """)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ---- header: SHELL + cwd + 执行时间 + 退出码 ----
        header = QtWidgets.QWidget()
        header.setStyleSheet("background:#252525;")
        hl = QtWidgets.QHBoxLayout(header)
        hl.setContentsMargins(8, 4, 8, 4)
        hl.setSpacing(6)

        title_lbl = QtWidgets.QLabel("SHELL")
        title_lbl.setStyleSheet(
            f"color:{CursorTheme.ACCENT_GREEN};font-size:11px;font-weight:bold;"
            f"font-family:{CursorTheme.FONT_CODE};"
        )
        hl.addWidget(title_lbl)

        if cwd:
            # 只显示最后两层目录
            parts = cwd.replace('\\', '/').rstrip('/').split('/')
            short_cwd = '/'.join(parts[-2:]) if len(parts) >= 2 else cwd
            cwd_lbl = QtWidgets.QLabel(short_cwd)
            cwd_lbl.setStyleSheet(
                f"color:{CursorTheme.TEXT_MUTED};font-size:10px;"
                f"font-family:{CursorTheme.FONT_CODE};"
            )
            hl.addWidget(cwd_lbl)

        hl.addStretch()

        if exec_time > 0:
            time_lbl = QtWidgets.QLabel(f"{exec_time:.2f}s")
            time_lbl.setStyleSheet(f"color:{CursorTheme.TEXT_MUTED};font-size:11px;")
            hl.addWidget(time_lbl)

        code_lbl = QtWidgets.QLabel(f"exit {exit_code}")
        code_color = CursorTheme.ACCENT_GREEN if exit_code == 0 else CursorTheme.ACCENT_RED
        code_lbl.setStyleSheet(
            f"color:{code_color};font-size:11px;font-weight:bold;"
            f"font-family:Consolas,Monaco,monospace;"
        )
        hl.addWidget(code_lbl)

        layout.addWidget(header)

        # ---- 命令区域 ----
        cmd_widget = QtWidgets.QTextEdit()
        cmd_widget.setReadOnly(True)
        cmd_widget.setLineWrapMode(QtWidgets.QTextEdit.NoWrap)
        cmd_widget.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        cmd_widget.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        cmd_widget.setStyleSheet(f"""
            QTextEdit {{
                background: #1e1e1e;
                color: {CursorTheme.TEXT_PRIMARY};
                border: none;
                border-bottom: 1px solid {CursorTheme.BORDER};
                padding: 6px 8px;
                font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
                font-size: 13px;
            }}
            QTextEdit QScrollBar:vertical {{ width:5px; background:transparent; }}
            QTextEdit QScrollBar::handle:vertical {{ background:#3c3c3c; border-radius:2px; }}
            QTextEdit QScrollBar:horizontal {{ height:5px; background:transparent; }}
            QTextEdit QScrollBar::handle:horizontal {{ background:#3c3c3c; border-radius:2px; }}
        """)

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
            err_label.setStyleSheet(
                f"color:{CursorTheme.ACCENT_RED};font-size:12px;padding:6px 8px;"
            )
            layout.addWidget(err_label)


# ============================================================
# 代码块组件
# ============================================================

class CodeBlockWidget(QtWidgets.QFrame):
    """代码块 — 语法高亮 + 复制 + 创建 Wrangle（VEX 专属）"""

    createWrangleRequested = QtCore.Signal(str)  # vex_code

    _VEX_INDICATORS = (
        '@P', '@Cd', '@N', '@v', '@ptnum', '@numpt', '@opinput',
        'chf(', 'chi(', 'chs(', 'chv(', 'chramp(',
        'addpoint', 'addprim', 'setattrib', 'getattrib',
        'vector ', 'float ', '#include',
    )

    def __init__(self, code: str, language: str = "", parent=None):
        super().__init__(parent)
        self._code = code
        self._lang = language.lower()

        self.setObjectName("CodeBlockWidget")
        self.setStyleSheet(f"""
            #CodeBlockWidget {{
                background: #1a1a2e;
                border: 1px solid {CursorTheme.BORDER};
            }}
        """)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ---- header ----
        header = QtWidgets.QWidget()
        header.setStyleSheet("background:#252535;")
        hl = QtWidgets.QHBoxLayout(header)
        hl.setContentsMargins(8, 3, 4, 3)
        hl.setSpacing(4)

        lang_text = self._lang.upper() or ("VEX" if self._is_vex() else "CODE")
        lang_lbl = QtWidgets.QLabel(lang_text)
        lang_lbl.setStyleSheet(
            f"color:{CursorTheme.TEXT_MUTED};font-size:11px;font-weight:bold;"
            f"font-family:Consolas,Monaco,monospace;"
        )
        hl.addWidget(lang_lbl)
        hl.addStretch()

        copy_btn = QtWidgets.QPushButton("复制")
        copy_btn.setCursor(QtCore.Qt.PointingHandCursor)
        copy_btn.setStyleSheet(self._btn_css())
        copy_btn.clicked.connect(self._on_copy)
        hl.addWidget(copy_btn)

        if self._lang in ('vex', 'vfl', '') and self._is_vex():
            wrangle_btn = QtWidgets.QPushButton("创建 Wrangle")
            wrangle_btn.setCursor(QtCore.Qt.PointingHandCursor)
            wrangle_btn.setStyleSheet(self._btn_css(CursorTheme.ACCENT_GREEN))
            wrangle_btn.clicked.connect(lambda: self.createWrangleRequested.emit(self._code))
            hl.addWidget(wrangle_btn)

        layout.addWidget(header)

        # ---- code area ----
        self._code_edit = QtWidgets.QTextEdit()
        self._code_edit.setReadOnly(True)
        self._code_edit.setLineWrapMode(QtWidgets.QTextEdit.NoWrap)
        self._code_edit.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self._code_edit.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self._code_edit.setStyleSheet(f"""
            QTextEdit {{
                background: #1a1a2e;
                color: {CursorTheme.TEXT_PRIMARY};
                border: none;
                padding: 6px 8px;
                font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
                font-size: 13px;
            }}
            QTextEdit QScrollBar:vertical {{
                width: 6px; background: transparent;
            }}
            QTextEdit QScrollBar::handle:vertical {{
                background: #3c3c3c; border-radius: 3px;
            }}
            QTextEdit QScrollBar:horizontal {{
                height: 6px; background: transparent;
            }}
            QTextEdit QScrollBar::handle:horizontal {{
                background: #3c3c3c; border-radius: 3px;
            }}
        """)

        highlighted = self._highlight()
        self._code_edit.setHtml(
            f'<pre style="margin:0;white-space:pre;">{highlighted}</pre>'
        )
        # auto-height (capped at 400)
        doc = self._code_edit.document()
        doc.setDocumentMargin(4)
        h = int(doc.size().height()) + 20
        self._code_edit.setFixedHeight(min(h, 400))
        layout.addWidget(self._code_edit)

    # --- helpers ---
    def _is_vex(self) -> bool:
        return any(ind in self._code for ind in self._VEX_INDICATORS)

    def _highlight(self) -> str:
        if self._lang in ('vex', 'vfl') or (not self._lang and self._is_vex()):
            return SyntaxHighlighter.highlight_vex(self._code)
        if self._lang in ('python', 'py'):
            return SyntaxHighlighter.highlight_python(self._code)
        return html.escape(self._code)

    def _on_copy(self):
        QtWidgets.QApplication.clipboard().setText(self._code)
        btn = self.sender()
        if btn:
            btn.setText("已复制")
            QtCore.QTimer.singleShot(1500, lambda: btn.setText("复制"))

    @staticmethod
    def _btn_css(color=None):
        c = color or CursorTheme.TEXT_MUTED
        return f"""
            QPushButton {{
                color: {c}; background: transparent;
                border: 1px solid {CursorTheme.BORDER};
                border-radius: 3px; font-size: 11px; padding: 2px 8px;
            }}
            QPushButton:hover {{
                background: {CursorTheme.BG_HOVER}; color: {CursorTheme.TEXT_BRIGHT};
            }}
        """


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

    # 正文 QLabel 通用样式
    _TEXT_STYLE = f"""
        QLabel {{
            color: {CursorTheme.TEXT_PRIMARY};
            font-size: 14px;
            font-family: {CursorTheme.FONT_BODY};
            line-height: 1.6;
            padding: 0;
        }}
        QLabel a {{
            color: #4e8fca;
            text-decoration: none;
        }}
    """

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

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
                lbl.setStyleSheet(self._TEXT_STYLE)
                lbl.linkActivated.connect(self._on_link)
                layout.addWidget(lbl)
            elif seg[0] == 'code':
                cb = CodeBlockWidget(seg[2], seg[1], self)
                cb.createWrangleRequested.connect(self.createWrangleRequested.emit)
                layout.addWidget(cb)

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
        self.setFixedHeight(28)
        self.setObjectName("NodeContextBar")
        self.setStyleSheet(f"""
            #NodeContextBar {{
                background: {CursorTheme.BG_TERTIARY};
                border-bottom: 1px solid {CursorTheme.BORDER};
            }}
        """)

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 6, 0)
        lay.setSpacing(8)

        self.path_label = QtWidgets.QLabel("/obj")
        self.path_label.setStyleSheet(
            f"color:{CursorTheme.TEXT_MUTED};font-size:12px;"
            f"font-family:Consolas,Monaco,monospace;"
        )
        lay.addWidget(self.path_label)

        self.sel_label = QtWidgets.QLabel("")
        self.sel_label.setStyleSheet(f"color:{CursorTheme.ACCENT_BLUE};font-size:12px;")
        self.sel_label.setVisible(False)
        lay.addWidget(self.sel_label)

        lay.addStretch()

        ref_btn = QtWidgets.QPushButton("R")
        ref_btn.setFixedSize(22, 22)
        ref_btn.setFlat(True)
        ref_btn.setCursor(QtCore.Qt.PointingHandCursor)
        ref_btn.setStyleSheet("border:none;font-size:12px;")
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
        self.setStyleSheet(f"""
            QFrame {{
                background: transparent;
                border: none;
            }}
        """)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(4)

        self._pulse = PulseIndicator(CursorTheme.ACCENT_BEIGE, 5, self)
        lay.addWidget(self._pulse)

        self._label = QtWidgets.QLabel("")
        self._label.setStyleSheet(f"""
            color: {CursorTheme.TEXT_MUTED};
            font-size: 11px;
            font-family: {CursorTheme.FONT_CODE};
        """)
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
# VEX 预览确认对话框
# ============================================================

class VEXPreviewDialog(QtWidgets.QDialog):
    """VEX 代码预览对话框 — 用户确认后才执行创建操作"""

    def __init__(self, tool_name: str, args: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"确认执行: {tool_name}")
        self.setMinimumSize(560, 400)
        self.setStyleSheet(f"""
            QDialog {{
                background: {CursorTheme.BG_PRIMARY};
                color: {CursorTheme.TEXT_PRIMARY};
            }}
        """)

        self._accepted = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # 工具名称
        title = QtWidgets.QLabel(f"工具: {tool_name}")
        title.setStyleSheet(f"color:{CursorTheme.ACCENT_BEIGE};font-size:14px;font-weight:bold;")
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
            info.setStyleSheet(f"color:{CursorTheme.TEXT_SECONDARY};font-size:12px;")
            info.setWordWrap(True)
            layout.addWidget(info)

        # VEX 代码 / 主要参数
        vex_code = args.get('vex_code', '')
        param_value = args.get('value', '')
        code_text = vex_code or param_value or str(args)

        code_edit = QtWidgets.QPlainTextEdit()
        code_edit.setPlainText(code_text)
        code_edit.setReadOnly(True)
        code_edit.setStyleSheet(f"""
            QPlainTextEdit {{
                background: {CursorTheme.BG_TERTIARY};
                color: {CursorTheme.TEXT_PRIMARY};
                border: 1px solid {CursorTheme.BORDER};
                border-radius: 4px;
                padding: 8px;
                font-size: 13px;
                font-family: {CursorTheme.FONT_CODE};
            }}
        """)
        layout.addWidget(code_edit, 1)

        # 按钮行
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()

        btn_cancel = QtWidgets.QPushButton("取消")
        btn_cancel.setFixedHeight(30)
        btn_cancel.setStyleSheet(f"""
            QPushButton {{
                color: {CursorTheme.TEXT_SECONDARY};
                background: {CursorTheme.BG_TERTIARY};
                border: 1px solid {CursorTheme.BORDER};
                border-radius: 4px;
                padding: 0 20px;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background: {CursorTheme.BG_HOVER};
                color: {CursorTheme.TEXT_PRIMARY};
            }}
        """)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        btn_confirm = QtWidgets.QPushButton("✓ 确认执行")
        btn_confirm.setFixedHeight(30)
        btn_confirm.setStyleSheet(f"""
            QPushButton {{
                color: {CursorTheme.TEXT_BRIGHT};
                background: {CursorTheme.ACCENT_GREEN};
                border: none;
                border-radius: 4px;
                padding: 0 20px;
                font-size: 13px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: #5fd9c0;
            }}
        """)
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
        self.setStyleSheet(f"""
            QListWidget {{
                background: {CursorTheme.BG_TERTIARY};
                color: {CursorTheme.TEXT_PRIMARY};
                border: 1px solid {CursorTheme.BORDER_FOCUS};
                border-radius: 4px;
                font-size: 12px;
                font-family: {CursorTheme.FONT_CODE};
                padding: 2px;
            }}
            QListWidget::item {{
                padding: 3px 6px;
                border-radius: 2px;
            }}
            QListWidget::item:selected {{
                background: {CursorTheme.ACCENT_BLUE};
                color: {CursorTheme.TEXT_BRIGHT};
            }}
            QListWidget::item:hover {{
                background: {CursorTheme.BG_HOVER};
            }}
        """)
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
        self.setPlaceholderText("输入消息... (Enter 发送, Shift+Enter 换行, @提及节点)")
        # 确保自动换行
        self.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
        self.setWordWrapMode(QtGui.QTextOption.WrapAtWordBoundaryOrAnywhere)
        # 隐藏滚动条（高度不够时才出现）
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        # 启用拖拽
        self.setAcceptDrops(True)
        self.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {CursorTheme.BG_TERTIARY};
                color: {CursorTheme.TEXT_PRIMARY};
                border: 1px solid {CursorTheme.BORDER};
                border-radius: 4px;
                padding: 8px;
                font-size: 16px;
                font-family: {CursorTheme.FONT_BODY};
            }}
            QPlainTextEdit:focus {{
                border-color: {CursorTheme.ACCENT_BEIGE};
            }}
        """)
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
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {CursorTheme.ACCENT_RED};
                color: {CursorTheme.TEXT_BRIGHT};
                border: none;
                border-radius: 4px;
                font-size: 17px;
                padding: 8px 20px;
                min-height: 32px;
            }}
            QPushButton:hover {{
                background-color: #ff6b6b;
            }}
        """)


# ============================================================
# 发送按钮
# ============================================================

class SendButton(QtWidgets.QPushButton):
    """发送按钮"""
    
    def __init__(self, parent=None):
        super().__init__("Send", parent)
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {CursorTheme.ACCENT_BLUE};
                color: {CursorTheme.TEXT_BRIGHT};
                border: none;
                border-radius: 4px;
                font-size: 17px;
                padding: 8px 20px;
                min-height: 32px;
            }}
            QPushButton:hover {{
                background-color: #1a8cff;
            }}
            QPushButton:disabled {{
                background-color: {CursorTheme.BG_TERTIARY};
                color: {CursorTheme.TEXT_MUTED};
            }}
        """)


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
        colors = {
            "pending": CursorTheme.TEXT_MUTED,
            "in_progress": CursorTheme.ACCENT_BLUE,
            "done": CursorTheme.ACCENT_GREEN,
            "error": CursorTheme.ACCENT_RED
        }
        
        color = colors.get(self.status, CursorTheme.TEXT_MUTED)
        icon = icons.get(self.status, "○")
        
        self.status_label.setText(icon)
        self.status_label.setStyleSheet(f"color: {color}; font-size: 12px; font-family: {CursorTheme.FONT_BODY};")
        
        if self.status == "done":
            self.text_label.setStyleSheet(f"""
                color: {CursorTheme.TEXT_MUTED};
                font-size: 13px;
                font-family: {CursorTheme.FONT_BODY};
                text-decoration: line-through;
            """)
        else:
            self.text_label.setStyleSheet(f"""
                color: {color};
                font-size: 13px;
                font-family: {CursorTheme.FONT_BODY};
            """)
    
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
        self._card.setStyleSheet(f"""
            QFrame#todoCard {{
                background: {CursorTheme.BG_SECONDARY};
                border: 1px solid {CursorTheme.BORDER};
                border-radius: 6px;
            }}
        """)
        self._card.setObjectName("todoCard")
        card_layout = QtWidgets.QVBoxLayout(self._card)
        card_layout.setContentsMargins(10, 8, 10, 8)
        card_layout.setSpacing(4)
        
        # 标题行
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(6)
        
        self.title_label = QtWidgets.QLabel("Todo")
        self.title_label.setStyleSheet(f"""
            color: {CursorTheme.ACCENT_PURPLE};
            font-size: 14px;
            font-weight: bold;
            font-family: {CursorTheme.FONT_BODY};
        """)
        header.addWidget(self.title_label)
        
        self.count_label = QtWidgets.QLabel("0/0")
        self.count_label.setStyleSheet(f"""
            color: {CursorTheme.TEXT_MUTED};
            font-size: 11px;
            font-family: {CursorTheme.FONT_BODY};
        """)
        header.addWidget(self.count_label)
        
        header.addStretch()
        
        self.clear_btn = QtWidgets.QPushButton("Clear")
        self.clear_btn.setFixedHeight(20)
        self.clear_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self.clear_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {CursorTheme.TEXT_MUTED};
                border: none;
                font-size: 11px;
                padding: 2px 6px;
                font-family: {CursorTheme.FONT_BODY};
            }}
            QPushButton:hover {{
                color: {CursorTheme.TEXT_PRIMARY};
            }}
        """)
        self.clear_btn.clicked.connect(self.clear_all)
        header.addWidget(self.clear_btn)
        
        card_layout.addLayout(header)
        
        # 分隔线
        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        sep.setStyleSheet(f"color: {CursorTheme.BORDER}; max-height: 1px;")
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
        self.setStyleSheet(f"""
            QDialog {{
                background: {CursorTheme.BG_PRIMARY};
                color: {CursorTheme.TEXT_PRIMARY};
                font-family: {CursorTheme.FONT_BODY};
            }}
        """)

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
        reset_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {CursorTheme.ACCENT_ORANGE};
                border: 1px solid {CursorTheme.ACCENT_ORANGE};
                border-radius: 4px;
                padding: 5px 0;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background: rgba(255,150,50,0.12);
            }}
        """)
        reset_btn.clicked.connect(self._on_reset)
        foot.addWidget(reset_btn)

        foot.addStretch()
        close_btn = QtWidgets.QPushButton("关闭")
        close_btn.setFixedWidth(72)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: {CursorTheme.BG_TERTIARY};
                color: {CursorTheme.TEXT_PRIMARY};
                border: 1px solid {CursorTheme.BORDER};
                border-radius: 4px;
                padding: 5px 0;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background: {CursorTheme.BG_HOVER};
            }}
        """)
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
        card.setStyleSheet(f"""
            QFrame {{
                background: {CursorTheme.BG_SECONDARY};
                border: 1px solid {CursorTheme.BORDER};
                border-radius: 6px;
            }}
        """)
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
            ("Cache Hit",      self._fmt_k(cache_r),     "#4ec9b0"),
            ("Hit Rate",       f"{hit_rate:.1f}%",       "#4ec9b0"),
            ("Avg Latency",    f"{avg_latency:.1f}s",    CursorTheme.TEXT_SECONDARY),
            ("Est. Cost",      cost_str,                 CursorTheme.ACCENT_BLUE),
        ]
        for col, (label, value, color) in enumerate(metrics):
            lbl = QtWidgets.QLabel(label)
            lbl.setStyleSheet(f"color:{CursorTheme.TEXT_MUTED};font-size:10px;border:none;")
            lbl.setAlignment(QtCore.Qt.AlignCenter)
            grid.addWidget(lbl, 0, col)

            val = QtWidgets.QLabel(value)
            fs = "16px" if col < 6 else "15px"
            val.setStyleSheet(f"color:{color};font-size:{fs};font-weight:bold;font-family:'Consolas','Monaco',monospace;border:none;")
            val.setAlignment(QtCore.Qt.AlignCenter)
            grid.addWidget(val, 1, col)

        # 进度条: input vs output vs cache
        if total > 0:
            bar = _BarWidget([
                (cache_r, "#4ec9b0"),
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
        container.setStyleSheet(f"""
            QFrame {{
                background: {CursorTheme.BG_SECONDARY};
                border: 1px solid {CursorTheme.BORDER};
                border-radius: 6px;
            }}
        """)
        vbox = QtWidgets.QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        # 标题
        title_lbl = QtWidgets.QLabel(f"  调用明细 ({len(records)} calls)")
        title_lbl.setStyleSheet(f"""
            color: {CursorTheme.TEXT_PRIMARY};
            font-size: 13px;
            font-weight: bold;
            padding: 8px 12px 4px 12px;
            border: none;
        """)
        vbox.addWidget(title_lbl)

        if not records:
            empty = QtWidgets.QLabel("  暂无 API 调用记录")
            empty.setStyleSheet(f"color:{CursorTheme.TEXT_MUTED};font-size:12px;padding:16px;border:none;")
            vbox.addWidget(empty)
            return container

        # 滚动表格区域
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"""
            QScrollArea {{
                border: none;
                background: transparent;
            }}
            QScrollBar:vertical {{
                background: {CursorTheme.BG_PRIMARY};
                width: 6px;
                border-radius: 3px;
            }}
            QScrollBar::handle:vertical {{
                background: {CursorTheme.BORDER};
                border-radius: 3px;
                min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)

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
            lbl.setStyleSheet(f"color:{fg};font-size:{font_size};font-weight:{weight};{font_family}border:none;padding:0 1px;")
            if i < len(widths) and widths[i] > 0:
                lbl.setFixedWidth(widths[i])
            # 数字列右对齐
            lbl.setAlignment(QtCore.Qt.AlignRight if 3 <= i <= 10 else QtCore.Qt.AlignLeft)
            if i < len(widths) and widths[i] == 0:
                row_h.addWidget(lbl, 1)
            else:
                row_h.addWidget(lbl)

        if is_header:
            row_w.setStyleSheet(f"border-bottom:1px solid {CursorTheme.BORDER};")

        return row_w

    def _make_record_row(self, idx: int, rec: dict, max_total: float) -> QtWidgets.QWidget:
        """构建单条记录行"""
        row_w = QtWidgets.QWidget()
        row_w.setStyleSheet(f"""
            QWidget:hover {{
                background: {CursorTheme.BG_HOVER};
                border-radius: 3px;
            }}
        """)
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
            "#4ec9b0",                    # Cache Hit
            CursorTheme.ACCENT_ORANGE,    # Cache Write
            CursorTheme.ACCENT_GREEN,     # Output
            CursorTheme.ACCENT_YELLOW,    # Reasoning
            CursorTheme.TEXT_BRIGHT,      # Total
            CursorTheme.TEXT_SECONDARY,   # 延迟
            CursorTheme.ACCENT_BLUE,      # 费用
        ]
        for i, text in enumerate(cells):
            lbl = QtWidgets.QLabel(text)
            if i < len(widths):
                lbl.setFixedWidth(widths[i])
            align = QtCore.Qt.AlignRight if i >= 3 else QtCore.Qt.AlignLeft
            lbl.setAlignment(align)
            c = colors[i] if i < len(colors) else CursorTheme.TEXT_PRIMARY
            lbl.setStyleSheet(
                f"color:{c};font-size:11px;"
                f"font-family:'Consolas','Monaco',monospace;"
                f"border:none;padding:0 1px;"
            )
            row_h.addWidget(lbl)

        # 迷你柱状图
        bar = _BarWidget([
            (c_hit, "#4ec9b0"),
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
