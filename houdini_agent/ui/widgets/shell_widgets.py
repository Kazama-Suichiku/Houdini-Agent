import html
import time
from houdini_agent.qt_compat import QtWidgets, QtCore, QtGui
from .theme import CursorTheme
from .syntax import SyntaxHighlighter


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
