import re
import html
from houdini_agent.qt_compat import QtWidgets, QtCore, QtGui
from .theme import CursorTheme, _linkify_node_paths_plain
from .syntax import SyntaxHighlighter
from .markdown import SimpleMarkdown
from ..i18n import tr


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
        self.setMinimumWidth(0)
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
        self._code_edit.setMinimumWidth(0)
        self._code_edit.setLineWrapMode(QtWidgets.QTextEdit.WidgetWidth)
        self._code_edit.setWordWrapMode(QtGui.QTextOption.WrapAtWordBoundaryOrAnywhere)
        self._code_edit.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self._code_edit.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._code_edit.setObjectName("codeBlockEdit")

        highlighted = self._highlight()
        code_html = self._add_line_numbers(highlighted) if self._show_line_numbers else highlighted
        self._code_edit.setHtml(
            f'<pre style="margin:0;white-space:pre-wrap;">{code_html}</pre>'
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
        QtCore.QTimer.singleShot(0, self._update_code_height)

    def _update_code_height(self):
        doc = self._code_edit.document()
        doc.setTextWidth(max(120, self._code_edit.viewport().width()))
        doc.adjustSize()
        self._full_h = int(doc.size().height()) + 20
        if self._collapsed:
            self._code_edit.setFixedHeight(min(self._collapsed_h, self._MAX_HEIGHT))
            self._code_edit.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        else:
            self._code_edit.setFixedHeight(min(self._full_h, self._MAX_HEIGHT))
            self._code_edit.setVerticalScrollBarPolicy(
                QtCore.Qt.ScrollBarAsNeeded if self._full_h > self._MAX_HEIGHT else QtCore.Qt.ScrollBarAlwaysOff
            )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_code_height()

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
        QtCore.QTimer.singleShot(0, self._update_code_height)

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
        self.setMinimumWidth(0)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)  # 段落间距由 HTML margin 控制

        segments = SimpleMarkdown.parse_segments(text)

        for seg in segments:
            if seg[0] == 'text':
                lbl = QtWidgets.QLabel()
                lbl.setMinimumWidth(0)
                lbl.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum)
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
                img_lbl.setMinimumWidth(0)
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
