# -*- coding: utf-8 -*-
import time
from houdini_agent.qt_compat import QtWidgets, QtCore, QtGui
from .theme import CursorTheme
from ..i18n import tr


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
