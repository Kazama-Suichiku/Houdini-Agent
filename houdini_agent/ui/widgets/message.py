# -*- coding: utf-8 -*-
import base64
import html
import math
import re
import time
from typing import TYPE_CHECKING, List
from houdini_agent.qt_compat import QtWidgets, QtCore, QtGui

if TYPE_CHECKING:  # 仅类型检查期可见，运行时不执行（避免循环导入）
    from .shell_widgets import PythonShellWidget, SystemShellWidget
from .theme import CursorTheme, _fmt_duration
from .base import AuroraBar, CollapsibleSection, TurnTraceHeader
from .thinking import ThinkingSection
from .tool_call import ExecutionSection
from .code_block import RichContentWidget, CodeBlockWidget
from .markdown import SimpleMarkdown
from ..i18n import tr
from ..theme_engine import ThemeEngine


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
        self.setFixedSize(thumb_pixmap.size())
        self.setMinimumSize(thumb_pixmap.size())
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

    deleteRequested = QtCore.Signal(int, int)

    _SOFT_WRAP_EVERY = 36

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Maximum,
        )
        self._history_range = None
        self._full_text = text
        self._collapsed = False  # 初始状态由 _maybe_collapse 决定

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 16, 4, 8)
        layout.setSpacing(0)

        # ---- 主容器（带左边框） ----
        self._container = QtWidgets.QWidget()
        self._container.setObjectName("userMsgContainer")
        self._container.setMinimumWidth(0)
        self._container.setSizePolicy(
            QtWidgets.QSizePolicy.Maximum,
            QtWidgets.QSizePolicy.Preferred,
        )
        self._container_layout = QtWidgets.QVBoxLayout(self._container)
        self._container_layout.setContentsMargins(12, 8, 12, 4)
        self._container_layout.setSpacing(2)

        # ---- 内容标签 ----
        self.content = QtWidgets.QLabel(self._soft_wrap_text(text))
        self.content.setWordWrap(True)
        self.content.setTextFormat(QtCore.Qt.PlainText)
        self.content.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.content.setObjectName("userMsgText")
        self.content.setMinimumWidth(0)
        self._container_layout.addWidget(self.content)

        # ---- 展开/收起 按钮 ----
        self._toggle_btn = QtWidgets.QPushButton()
        self._toggle_btn.setFlat(True)
        self._toggle_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._toggle_btn.setFixedHeight(20)
        self._toggle_btn.setObjectName("userMsgToggle")
        self._toggle_btn.clicked.connect(self._toggle_collapse)
        self._toggle_btn.setVisible(False)  # 默认隐藏，_maybe_collapse 决定
        self._container_layout.addWidget(self._toggle_btn)

        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addStretch(1)
        self._delete_btn = QtWidgets.QPushButton("x")
        self._delete_btn.setFixedSize(20, 20)
        self._delete_btn.setFlat(True)
        self._delete_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._delete_btn.setToolTip("Delete record")
        self._delete_btn.setObjectName("msgDeleteBtn")
        self._delete_btn.clicked.connect(self._request_delete)
        self._delete_btn.setVisible(False)
        row.addWidget(self._delete_btn, 0, QtCore.Qt.AlignTop)
        row.addWidget(self._container, 0, QtCore.Qt.AlignRight)
        layout.addLayout(row)

        # 延迟判断是否需要折叠（等 QLabel 完成布局后再算行数）
        QtCore.QTimer.singleShot(0, self._maybe_collapse)
        QtCore.QTimer.singleShot(0, self._update_bubble_width)

    # ------------------------------------------------------------------
    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._update_bubble_width()

    def _ideal_content_width(self, max_content_w: int) -> int:
        text = self.content.text() or ""
        lines = text.splitlines() or [text]
        fm = self.content.fontMetrics()
        widest = 0
        for line in lines:
            # Ignore injected zero-width break points when estimating the preferred width.
            widest = max(widest, fm.horizontalAdvance(line.replace("​", "")))

        if self._toggle_btn.isVisible():
            widest = max(widest, self._toggle_btn.sizeHint().width())

        # Image-only messages and mixed image/text messages need enough room for thumbnails.
        for child in self._container.findChildren(QtWidgets.QWidget):
            if child is self.content or child is self._toggle_btn or child is self._delete_btn:
                continue
            hint = child.sizeHint()
            if hint.isValid():
                widest = max(widest, hint.width())

        return min(max_content_w, max(120, widest + 2))

    def _update_bubble_width(self):
        max_w = max(260, int(self.width() * 0.86))
        content_max = max(120, max_w - 24)
        content_w = self._ideal_content_width(content_max)
        bubble_w = min(max_w, content_w + 24)
        self._container.setFixedWidth(bubble_w)
        self.content.setFixedWidth(max(120, bubble_w - 24))
        self._container.updateGeometry()

    def add_image_widgets(self, image_widgets: list):
        """Add clickable thumbnails inside the message bubble."""
        if not image_widgets:
            return
        img_row = QtWidgets.QHBoxLayout()
        img_row.setSpacing(4)
        img_row.setContentsMargins(0, 4, 0, 4)
        for widget in image_widgets:
            widget.setParent(self._container)
            img_row.addWidget(widget)
        img_row.addStretch()
        insert_at = max(1, self._container_layout.indexOf(self._toggle_btn))
        self._container_layout.insertLayout(insert_at, img_row)
        QtCore.QTimer.singleShot(0, self._update_bubble_width)

    @classmethod
    def _soft_wrap_text(cls, text: str) -> str:
        """Insert break points only into long unbroken latin/code-like runs."""
        if not text:
            return ""
        out = []
        run = 0
        for ch in text:
            out.append(ch)
            code = ord(ch)
            is_cjk = (
                0x3400 <= code <= 0x4DBF
                or 0x4E00 <= code <= 0x9FFF
                or 0xF900 <= code <= 0xFAFF
                or 0x3040 <= code <= 0x30FF
                or 0xAC00 <= code <= 0xD7AF
            )
            if is_cjk:
                run = 0
                continue
            if ch.isspace() or ch in "/\\,.;:|()[]{}<>+-=*":
                run = 0
                continue
            run += 1
            if run >= cls._SOFT_WRAP_EVERY:
                out.append("​")
                run = 0
        return "".join(out)

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
        self.content.setText(self._soft_wrap_text(preview))
        remaining = len(lines) - self._COLLAPSED_MAX_LINES
        self._toggle_btn.setText(tr('msg.expand', remaining))
        QtCore.QTimer.singleShot(0, self._update_bubble_width)

    def _apply_expanded(self):
        """应用展开状态：显示完整文本"""
        self.content.setText(self._soft_wrap_text(self._full_text))
        self._toggle_btn.setText(tr('msg.collapse'))
        QtCore.QTimer.singleShot(0, self._update_bubble_width)

    def _toggle_collapse(self):
        self._collapsed = not self._collapsed
        if self._collapsed:
            self._apply_collapsed()
        else:
            self._apply_expanded()

    def set_history_range(self, start: int, end: int):
        if start is None or end is None or start < 0 or end <= start:
            self._history_range = None
            self._delete_btn.setVisible(False)
            return
        self._history_range = (start, end)
        self._delete_btn.setVisible(True)

    def _request_delete(self):
        if not self._history_range:
            return
        self.deleteRequested.emit(self._history_range[0], self._history_range[1])


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

    deleteRequested = QtCore.Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("aiResponse")
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.setMinimumWidth(0)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Maximum,
        )
        self._history_range = None
        self._start_time = time.time()
        self._content = ""
        self._has_thinking = False
        self._has_execution = False
        self._execution_batch_open = False
        self._trace_header = None
        self._shell_count = 0  # Python Shell 执行计数

        # ★ 增量渲染状态
        self._frozen_segments: list = []    # 已冻结的富文本段落
        self._pending_text = ""             # 尚未冻结的尾部文本
        self._in_code_fence = False         # 是否在代码块内
        self._code_fence_lang = ""          # 代码块语言
        self._in_table = False              # 是否在表格连续行内
        self._incremental_enabled = True    # 是否启用增量渲染
        self._table_flush_timer = QtCore.QTimer(self)
        self._table_flush_timer.setSingleShot(True)
        self._table_flush_timer.setInterval(600)
        self._table_flush_timer.timeout.connect(self._flush_pending_table)

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

        # Timeline sections are created lazily and inserted before the final
        # reply area, preserving the real order: thinking -> tools -> verify -> thinking.
        self._timeline_layout = layout
        self._thinking_sections: List[ThinkingSection] = []
        self._execution_sections: List[ExecutionSection] = []
        self.thinking_section = None
        self.execution_section = None
        self.shell_section = None

        # === System Shell 区块（可折叠，默认折叠）===
        self._sys_shell_count = 0
        self.sys_shell_section = None

        # === 总结/回复区域 ===
        self.summary_frame = QtWidgets.QFrame()
        self.summary_frame.setObjectName("aiSummary")
        self.summary_frame.setMinimumWidth(0)
        self._summary_layout = QtWidgets.QVBoxLayout(self.summary_frame)
        self._summary_layout.setContentsMargins(8, 8, 6, 8)
        self._summary_layout.setSpacing(4)

        # 状态行（水平布局：状态文字 + 复制按钮）
        status_row = QtWidgets.QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(8)

        self.status_label = QtWidgets.QLabel(tr('thinking.init'))
        self.status_label.setObjectName("aiStatusLabel")
        self.status_label.setMinimumWidth(0)
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.status_label.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum)
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

        self._delete_btn = QtWidgets.QPushButton("x")
        self._delete_btn.setFixedSize(20, 20)
        self._delete_btn.setFlat(True)
        self._delete_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._delete_btn.setToolTip("Delete record")
        self._delete_btn.setObjectName("msgDeleteBtn")
        self._delete_btn.clicked.connect(self._request_delete)
        self._delete_btn.setVisible(False)
        status_row.addWidget(self._delete_btn)

        self._summary_layout.addLayout(status_row)

        # ★ 已冻结段落容器 — 增量渲染时冻结的富文本/代码块放在这里
        self._frozen_container = QtWidgets.QWidget()
        self._frozen_container.setObjectName("aiFrozenContainer")
        self._frozen_container.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self._frozen_container.setMinimumWidth(0)
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
        self.content_label.setWordWrapMode(QtGui.QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.content_label.setMinimumWidth(0)
        # 让 size hint 跟随内容自动增长（不设固定高度）
        self.content_label.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum
        )
        self.content_label.setObjectName("aiContentLabel")
        # ★ 显式设置字体，确保流式和渲染后使用同一字体族和大小
        _stream_font = QtGui.QFont()
        _stream_font.setFamilies(['Microsoft YaHei', 'SimSun', 'Segoe UI'])
        _stream_font.setPixelSize(ThemeEngine.scaled_px(11))  # 与 {FS_CHAT}=11 一致
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

    def _ensure_trace_header(self) -> TurnTraceHeader:
        if self._trace_header is None:
            header = TurnTraceHeader(self)
            self._timeline_layout.insertWidget(0, header)
            self._trace_header = header
        return self._trace_header

    def _available_content_width(self) -> int:
        margins = self._summary_layout.contentsMargins()
        width = self.summary_frame.width() - margins.left() - margins.right() - 4
        return max(120, width)

    def _sync_content_widths(self):
        width = self._available_content_width()
        self.status_label.setMaximumWidth(width)
        self.content_label.document().setTextWidth(width)
        self.content_label.setMaximumWidth(width)
        for child in self._frozen_container.findChildren(QtWidgets.QWidget):
            name = child.objectName()
            if name in ("richText", "richImage") or isinstance(child, CodeBlockWidget):
                child.setMinimumWidth(0)
                child.setMaximumWidth(width)
                child.updateGeometry()
        for child in self.findChildren(QtWidgets.QPlainTextEdit):
            child.setMaximumWidth(width)
            if hasattr(child, "setWordWrapMode"):
                child.setWordWrapMode(QtGui.QTextOption.WrapAtWordBoundaryOrAnywhere)
        for child in self.findChildren(QtWidgets.QTextEdit):
            child.setMaximumWidth(width)
            if hasattr(child, "setWordWrapMode"):
                child.setWordWrapMode(QtGui.QTextOption.WrapAtWordBoundaryOrAnywhere)
        self._frozen_container.updateGeometry()
        for section in self._thinking_sections:
            if section.isVisible():
                section.thinking_label.document().setTextWidth(width)
                section._update_height()
        self._auto_resize_content()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_content_widths()

    def set_history_range(self, start: int, end: int):
        if start is None or end is None or start < 0 or end <= start:
            self._history_range = None
            self._delete_btn.setVisible(False)
            return
        self._history_range = (start, end)
        self._delete_btn.setVisible(True)

    def _request_delete(self):
        if not self._history_range:
            return
        self.deleteRequested.emit(self._history_range[0], self._history_range[1])

    def _timeline_insert_index(self) -> int:
        try:
            idx = self._timeline_layout.indexOf(self.summary_frame)
            return idx if idx >= 0 else self._timeline_layout.count()
        except (AttributeError, RuntimeError):
            return self._timeline_layout.count()

    def _insert_timeline_widget(self, widget: QtWidgets.QWidget):
        header = self._ensure_trace_header()
        self._timeline_layout.insertWidget(self._timeline_insert_index(), widget)
        header.add_target(widget)
        return widget

    def start_thinking_round(self) -> ThinkingSection:
        """Ensure the current thinking block belongs to the active reasoning round."""
        self._close_execution_batch_if_complete()
        if self.thinking_section is None or self.thinking_section._finalized:
            section = ThinkingSection(self)
            section.setVisible(True)
            section.expand()
            self._insert_timeline_widget(section)
            self._thinking_sections.append(section)
            self.thinking_section = section
            self._has_thinking = True
        return self.thinking_section

    def _ensure_execution_section(self) -> ExecutionSection:
        """Create or reuse the current continuous execution batch."""
        if (
            self.execution_section is None
            or not self._execution_batch_open
            or getattr(self.execution_section, '_finalized', False)
        ):
            section = ExecutionSection(self)
            section.setVisible(True)
            section.nodePathClicked.connect(self.nodePathClicked.emit)
            self._insert_timeline_widget(section)
            self._execution_sections.append(section)
            self.execution_section = section
            self._has_execution = True
            self._execution_batch_open = True
        return self.execution_section

    def _close_execution_batch_if_complete(self):
        section = self.execution_section
        if section is not None and section.is_complete():
            self._execution_batch_open = False

    def add_thinking(self, text: str):
        """添加思考内容"""
        section = self.start_thinking_round()
        section.append_thinking(text)

    def update_thinking_time(self):
        """更新思考时间（思考结束后不再更新状态标签）"""
        if self._trace_header is not None:
            self._trace_header.set_elapsed(time.time() - self._start_time, active=True)
        if self._has_thinking and self.thinking_section is not None:
            if self.thinking_section._finalized:
                return  # 思考已结束，不再更新
            self.thinking_section.update_time()
            total = self.thinking_section._total_elapsed()
            self.status_label.setText(tr('thinking.progress', _fmt_duration(total)))

    def add_shell_widget(self, widget: 'PythonShellWidget'):
        """将 PythonShellWidget 添加到 Python Shell 折叠区块"""
        self._shell_count += 1
        if self.shell_section is None:
            self.shell_section = CollapsibleSection(tr("shell.python"), collapsed=True, parent=self)
            self.shell_section.header.setObjectName("shellHeaderPython")
            self._insert_timeline_widget(self.shell_section)
        self.shell_section.setVisible(True)
        self.shell_section.set_title(f"{tr('shell.python')} ({self._shell_count})")
        self.shell_section.add_widget(widget)

    def add_sys_shell_widget(self, widget: 'SystemShellWidget'):
        """将 SystemShellWidget 添加到 System Shell 折叠区块"""
        self._sys_shell_count += 1
        if self.sys_shell_section is None:
            self.sys_shell_section = CollapsibleSection(tr("shell.system"), collapsed=True, parent=self)
            self.sys_shell_section.header.setObjectName("shellHeaderSystem")
            self._insert_timeline_widget(self.sys_shell_section)
        self.sys_shell_section.setVisible(True)
        self.sys_shell_section.set_title(f"{tr('shell.system')} ({self._sys_shell_count})")
        self.sys_shell_section.add_widget(widget)

    def add_status(self, text: str):
        """添加状态（处理工具调用）"""
        if text.startswith("[tool]"):
            tool_name = text[6:].strip()
            self._add_tool_call(tool_name)
        else:
            self.status_label.setText(UserMessage._soft_wrap_text(text))
            QtCore.QTimer.singleShot(0, self._sync_content_widths)

    def _add_tool_call(self, tool_name: str):
        """添加工具调用"""
        section = self._ensure_execution_section()
        section.add_tool_call(tool_name)
        if self._trace_header is not None:
            self._trace_header.set_elapsed(time.time() - self._start_time, active=True)
        self.status_label.setText(tr('exec.tool', tool_name))

    def add_tool_result(self, tool_name: str, result: str):
        """添加工具结果"""
        success = not result.startswith("[err]") and not result.startswith("错误") and not result.startswith("Error")
        clean_result = result.removeprefix("[ok] ").removeprefix("[err] ")
        section = self._ensure_execution_section()
        section.set_tool_result(tool_name, clean_result, success)
        if self._trace_header is not None:
            self._trace_header.set_elapsed(time.time() - self._start_time, active=True)

    def add_execution_detail(self, widget: QtWidgets.QWidget):
        """Add a supplementary execution widget inside the current collapsible block."""
        section = self._ensure_execution_section()
        widget.setParent(section)
        section.add_detail_widget(widget)

    def add_viewport_snapshot(self, label: str, b64_data: str, media_type: str = 'image/jpeg'):
        """Render a clickable viewport thumbnail inside the current execution block."""
        if not b64_data:
            return
        try:
            raw = base64.b64decode(b64_data)
        except Exception:
            return
        full_pixmap = QtGui.QPixmap()
        if not full_pixmap.loadFromData(raw) or full_pixmap.isNull():
            return

        row = QtWidgets.QWidget()
        row.setObjectName("viewportSnapshotRow")
        row.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        layout = QtWidgets.QHBoxLayout(row)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(6)

        thumb = full_pixmap.scaled(
            96, 54,
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )
        img = ClickableImageLabel(thumb, full_pixmap, row)
        img.setObjectName("imgThumb")
        layout.addWidget(img)

        caption = QtWidgets.QLabel(label or "Viewport snapshot")
        caption.setObjectName("toolResultLabel")
        caption.setWordWrap(True)
        layout.addWidget(caption, 1)
        self.add_execution_detail(row)

    def _apply_line_spacing(self, percent: int = 160):
        """为 QPlainTextEdit 设置 proportional 行间距。

        Qt 的 QPlainTextEdit 不直接支持 CSS line-height，
        需要通过 QTextBlockFormat.setLineHeight 来实现。
        percent: 160 = 1.6 倍行间距。
        """
        doc = self.content_label.document()
        doc.setTextWidth(self._available_content_width())
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
        doc.setTextWidth(self._available_content_width())
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
        if text.strip():
            self._close_execution_batch_if_complete()
        # 清除 U+FFFD 替换符（encoding 异常残留）
        if '�' in text:
            text = text.replace('�', '')
        self._content += text
        self._pending_text += text

        # 尝试冻结已完成的段落
        if self._incremental_enabled:
            self._try_freeze_completed()

            # 当 pending 中存在未完结的表格时，启动延时冻结定时器；
            # 如果持续有新行则不断重置，表格停止增长 600ms 后自动冻结
            if self._in_table:
                self._table_flush_timer.start()
            else:
                self._table_flush_timer.stop()

        # 更新活跃区域显示（只显示未冻结的文本）
        self.content_label.setPlainText(self._pending_text)
        self._apply_line_spacing(160)
        cursor = self.content_label.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        self.content_label.setTextCursor(cursor)

    _TABLE_SEP_RE_FREEZE = re.compile(r'^\|?\s*[-:]+[-| :]*$')

    def _try_freeze_completed(self):
        """检测并冻结已完成的段落

        检测规则：
        - 代码块: ``` 开启 → ``` 关闭，闭合后整个代码块冻结
        - 文本段落: 两个连续换行 (\\n\\n) 分隔的文本段落冻结
        - 表格: 表头 + 分隔行 + 数据行，表格后出现非表格行即冻结整段
        """
        text = self._pending_text
        if not text:
            return

        lines = text.split('\n')
        freeze_up_to = -1
        i = 0
        in_fence = self._in_code_fence
        in_table = self._in_table

        while i < len(lines):
            stripped = lines[i].strip()

            # --- 代码围栏 ---
            if in_fence:
                if stripped.startswith('```'):
                    in_fence = False
                    freeze_up_to = i + 1
                i += 1
                continue

            if stripped.startswith('```'):
                if in_table:
                    in_table = False
                in_fence = True
                self._code_fence_lang = stripped[3:].strip()
                freeze_up_to = i
                i += 1
                continue

            # --- 表格状态机 ---
            if in_table:
                if stripped and '|' in stripped:
                    i += 1
                    continue
                if not stripped:  # 表格内空行（LLM 松散表格格式）- 跳过，保持表格状态
                    i += 1
                    continue
                in_table = False
                freeze_up_to = i
                i += 1
                continue

            # 检测表格开始: 当前行含 | 且下一行是分隔行
            if (stripped and '|' in stripped
                    and i + 1 < len(lines)
                    and self._TABLE_SEP_RE_FREEZE.match(lines[i + 1].strip())):
                in_table = True
                i += 1
                continue

            # --- 空行 = 段落边界 ---
            if not stripped:
                if i > 0 and freeze_up_to < i:
                    start_scan = max(0, freeze_up_to + 1 if freeze_up_to >= 0 else 0)
                    seg_lines = [lines[j] for j in range(start_scan, i)]
                    if any(l.strip() for l in seg_lines):
                        # ★ 防止表格被拆分：若该段最后一行含 '|'（可能是表头，
                        # 其分隔行 |---| 尚未流式到达），且该空行之后再无已提交内容
                        # （位于 pending 末尾），则推迟冻结——等下一个 chunk 的分隔行
                        # 到达后由表格状态机整体冻结，或由 finalize 兜底整段解析。
                        last_content = next((l for l in reversed(seg_lines) if l.strip()), '')
                        is_tail = all(not lines[j].strip() for j in range(i, len(lines)))
                        if '|' in last_content and is_tail:
                            pass  # 推迟冻结，避免把表头与分隔行拆成两个段落
                        else:
                            freeze_up_to = i
            i += 1

        self._in_code_fence = in_fence
        self._in_table = in_table

        if freeze_up_to > 0 and not in_fence:
            frozen_text = '\n'.join(lines[:freeze_up_to])
            remaining_text = '\n'.join(lines[freeze_up_to:])

            if frozen_text.strip():
                self._freeze_text(frozen_text)

            self._pending_text = remaining_text

    def _flush_pending_table(self):
        """定时器触发：表格停止增长后将 pending 中包含表格的内容全部冻结"""
        if not self._pending_text or not self._in_table:
            return
        if not self._pending_text.strip():
            return
        self._freeze_text(self._pending_text)
        self._pending_text = ""
        self._in_table = False
        self.content_label.setPlainText("")

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
                lbl.setMaximumWidth(self._available_content_width())
                lbl.linkActivated.connect(self._on_link_activated)
                self._frozen_layout.addWidget(lbl)
            elif seg[0] == 'code':
                cb = CodeBlockWidget(seg[2], seg[1], self)
                cb.createWrangleRequested.connect(self.createWrangleRequested.emit)
                # 代码块与前后段落之间需要额外间距
                cb.setContentsMargins(0, 6, 0, 6)
                cb.setMaximumWidth(self._available_content_width())
                self._frozen_layout.addWidget(cb)
            elif seg[0] == 'image':
                img_lbl = QtWidgets.QLabel()
                img_lbl.setObjectName("richImage")
                img_lbl.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
                img_lbl.setMaximumWidth(self._available_content_width())
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
        self._sync_content_widths()

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
        self.aurora_bar.stop()
        self._table_flush_timer.stop()

        elapsed = time.time() - self._start_time
        if self._trace_header is not None:
            self._trace_header.set_elapsed(elapsed, active=False)

        for section in self._thinking_sections:
            section.finalize()

        # 完成执行区块
        for section in self._execution_sections:
            section.finalize()
        self._execution_batch_open = False

        # 更新状态
        parts = []
        if self._has_thinking:
            parts.append(tr('status.thinking'))
        if self._has_execution:
            tool_count = sum(len(section._tool_calls) for section in self._execution_sections)
            parts.append(tr('status.calls', tool_count))

        status_text = tr('status.done', _fmt_duration(elapsed))
        if parts:
            status_text += f" | {', '.join(parts)}"

        self.status_label.setText(status_text)
        if self._trace_header is not None:
            self.status_label.setVisible(False)

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
        """处理链接点击 — houdini:// 跳转节点，http(s):// 用系统浏览器打开"""
        if url.startswith('houdini://'):
            node_path = url[len('houdini://'):]
            self.nodePathClicked.emit(node_path)
        elif url.startswith(('http://', 'https://')):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))


# ============================================================
# 简洁状态行
# ============================================================

class StatusLine(QtWidgets.QLabel):
    """简洁状态行"""

    def __init__(self, text: str = "", parent=None):
        super().__init__(UserMessage._soft_wrap_text(text), parent)
        self.setObjectName("statusLine")
        self.setMinimumWidth(0)
        self.setWordWrap(True)
        self.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum)
