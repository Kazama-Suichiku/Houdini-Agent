import time
from houdini_agent.qt_compat import QtWidgets, QtCore, QtGui
from .theme import CursorTheme, _fmt_duration
from .base import CollapsibleSection, PulseIndicator
from ..i18n import tr
from ..theme_engine import ThemeEngine


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
        # 历史恢复时默认折叠；实时生成思考内容时由 AIResponse.add_thinking 展开。
        super().__init__(tr('thinking.init'), icon="", collapsed=True, parent=parent)
        self.setMinimumWidth(0)
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
        self._text_font = ThemeEngine.font(CursorTheme.FONT_BODY, 11)

        self.thinking_label = QtWidgets.QPlainTextEdit()
        self.thinking_label.setReadOnly(True)
        self.thinking_label.setFont(self._text_font)
        self.thinking_label.document().setDefaultFont(self._text_font)
        self.thinking_label.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.thinking_label.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.thinking_label.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.thinking_label.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
        self.thinking_label.setWordWrapMode(QtGui.QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.thinking_label.setMinimumWidth(0)
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
        doc.setTextWidth(max(120, self.thinking_label.viewport().width()))
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
        if '�' in text:
            text = text.replace('�', '')
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
        self.collapse()


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

        font = ThemeEngine.font(CursorTheme.FONT_BODY, 12)
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
