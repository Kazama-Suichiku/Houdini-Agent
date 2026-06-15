from typing import Optional
from houdini_agent.qt_compat import QtWidgets, QtCore, QtGui
from .theme import CursorTheme
from ..i18n import tr
from ..theme_engine import ThemeEngine


class RulesEditorDialog(QtWidgets.QDialog):
    """用户自定义规则编辑器对话框

    左侧：规则列表 + 操作按钮
    右侧：标题 + 内容编辑区（或空状态引导）
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("rulesEditorDlg")
        self.setWindowTitle(tr('rules.title'))
        self.setMinimumSize(580, 400)
        self.resize(640, 440)
        self._current_rule_id: Optional[str] = None
        self._rules: list = []
        self._dirty = False

        self._build_ui()
        self._load_rules()

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- 标题栏 ----
        header = QtWidgets.QFrame()
        header.setObjectName("rulesHeader")
        header.setFixedHeight(40)
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(14, 0, 14, 0)
        header_layout.setSpacing(8)

        title_lbl = QtWidgets.QLabel(f"📋  {tr('rules.title')}")
        title_lbl.setObjectName("rulesEditorTitle")
        header_layout.addWidget(title_lbl)
        header_layout.addStretch()

        self._count_label = QtWidgets.QLabel("")
        self._count_label.setObjectName("rulesCountLabel")
        header_layout.addWidget(self._count_label)

        root.addWidget(header)

        # ---- 主体 ----
        body = QtWidgets.QHBoxLayout()
        body.setContentsMargins(10, 8, 10, 0)
        body.setSpacing(8)

        # ── 左侧面板 ──
        left_panel = QtWidgets.QFrame()
        left_panel.setObjectName("rulesLeftPanel")
        left_panel.setFixedWidth(200)
        left_v = QtWidgets.QVBoxLayout(left_panel)
        left_v.setContentsMargins(0, 0, 0, 0)
        left_v.setSpacing(6)

        self._list_widget = QtWidgets.QListWidget()
        self._list_widget.setObjectName("rulesList")
        self._list_widget.currentRowChanged.connect(self._on_rule_selected)
        left_v.addWidget(self._list_widget, 1)

        # 操作按钮
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(4)

        self._btn_add = QtWidgets.QPushButton(f"＋ {tr('rules.add')}")
        self._btn_add.setObjectName("rulesAddBtn")
        self._btn_add.setCursor(QtCore.Qt.PointingHandCursor)
        self._btn_add.clicked.connect(self._on_add)
        btn_row.addWidget(self._btn_add)

        self._btn_delete = QtWidgets.QPushButton("✕")
        self._btn_delete.setObjectName("rulesDelBtn")
        self._btn_delete.setFixedWidth(28)
        self._btn_delete.setCursor(QtCore.Qt.PointingHandCursor)
        self._btn_delete.setToolTip(tr('rules.delete'))
        self._btn_delete.clicked.connect(self._on_delete)
        btn_row.addWidget(self._btn_delete)

        left_v.addLayout(btn_row)
        body.addWidget(left_panel)

        # ── 右侧面板 (QStackedWidget: 空状态 / 编辑区) ──
        self._right_stack = QtWidgets.QStackedWidget()
        self._right_stack.setObjectName("rulesRightStack")

        # page 0: 空状态引导
        empty_page = QtWidgets.QWidget()
        empty_lay = QtWidgets.QVBoxLayout(empty_page)
        empty_lay.setAlignment(QtCore.Qt.AlignCenter)

        empty_icon = QtWidgets.QLabel("📝")
        empty_icon.setAlignment(QtCore.Qt.AlignCenter)
        empty_icon.setStyleSheet(f"font-size: {ThemeEngine.scaled_px(32)}px; background: transparent;")
        empty_lay.addWidget(empty_icon)

        self._empty_label = QtWidgets.QLabel(tr('rules.empty_hint'))
        self._empty_label.setObjectName("rulesEmptyHint")
        self._empty_label.setAlignment(QtCore.Qt.AlignCenter)
        self._empty_label.setWordWrap(True)
        empty_lay.addWidget(self._empty_label)

        # 空状态下的新建按钮
        empty_add_btn = QtWidgets.QPushButton(f"＋ {tr('rules.add')}")
        empty_add_btn.setObjectName("rulesAddBtn")
        empty_add_btn.setCursor(QtCore.Qt.PointingHandCursor)
        empty_add_btn.setFixedWidth(120)
        empty_add_btn.clicked.connect(self._on_add)
        empty_btn_wrap = QtWidgets.QHBoxLayout()
        empty_btn_wrap.setAlignment(QtCore.Qt.AlignCenter)
        empty_btn_wrap.addWidget(empty_add_btn)
        empty_lay.addLayout(empty_btn_wrap)

        self._right_stack.addWidget(empty_page)  # index 0

        # page 1: 编辑区
        edit_page = QtWidgets.QWidget()
        edit_lay = QtWidgets.QVBoxLayout(edit_page)
        edit_lay.setContentsMargins(0, 0, 0, 0)
        edit_lay.setSpacing(6)

        self._title_edit = QtWidgets.QLineEdit()
        self._title_edit.setObjectName("rulesTitleEdit")
        self._title_edit.setPlaceholderText(tr('rules.placeholder_title'))
        self._title_edit.textChanged.connect(self._on_title_changed)
        edit_lay.addWidget(self._title_edit)

        self._content_edit = QtWidgets.QPlainTextEdit()
        self._content_edit.setObjectName("rulesContentEdit")
        self._content_edit.setPlaceholderText(tr('rules.placeholder_content'))
        self._content_edit.textChanged.connect(self._on_content_changed)
        edit_lay.addWidget(self._content_edit, 1)

        # 底部状态行
        bottom_row = QtWidgets.QHBoxLayout()
        bottom_row.setSpacing(6)

        self._source_label = QtWidgets.QLabel("")
        self._source_label.setObjectName("rulesSourceLabel")
        bottom_row.addWidget(self._source_label, 1)

        self._btn_toggle = QtWidgets.QPushButton(tr('rules.disable'))
        self._btn_toggle.setObjectName("rulesToggleBtn")
        self._btn_toggle.setCursor(QtCore.Qt.PointingHandCursor)
        self._btn_toggle.clicked.connect(self._on_toggle_enabled)
        bottom_row.addWidget(self._btn_toggle)

        edit_lay.addLayout(bottom_row)

        self._right_stack.addWidget(edit_page)  # index 1

        body.addWidget(self._right_stack, 1)
        root.addLayout(body, 1)

        # ---- 底部栏 ----
        footer = QtWidgets.QFrame()
        footer.setObjectName("rulesFooter")
        footer.setFixedHeight(36)
        footer_lay = QtWidgets.QHBoxLayout(footer)
        footer_lay.setContentsMargins(14, 0, 14, 0)
        footer_lay.setSpacing(6)
        footer_lay.addStretch()

        self._btn_open_dir = QtWidgets.QPushButton(f"📂  {tr('rules.open_folder')}")
        self._btn_open_dir.setObjectName("rulesFooterBtn")
        self._btn_open_dir.setCursor(QtCore.Qt.PointingHandCursor)
        self._btn_open_dir.clicked.connect(self._on_open_dir)
        footer_lay.addWidget(self._btn_open_dir)

        root.addWidget(footer)

        # 初始显示空状态
        self._right_stack.setCurrentIndex(0)

    def _load_rules(self):
        """从 rules_manager 加载所有规则"""
        try:
            from ..utils.rules_manager import get_all_rules
            self._rules = get_all_rules(force_reload=True)
        except Exception as e:
            print(f"[RulesEditor] Failed to load rules: {e}")
            self._rules = []

        self._refresh_list()

    def _refresh_list(self):
        """刷新左侧规则列表"""
        self._list_widget.blockSignals(True)
        self._list_widget.clear()

        for r in self._rules:
            title = r.get("title", "") or tr('rules.untitled')
            source = r.get("source", "ui")
            enabled = r.get("enabled", True)

            # 构造显示文本
            prefix = ""
            if source == "file":
                prefix = "[F] "
            if not enabled:
                prefix += "(" + tr('rules.disable') + ") "

            item = QtWidgets.QListWidgetItem(prefix + title)
            # 禁用的规则灰色显示
            if not enabled:
                item.setForeground(QtCore.Qt.gray)
            self._list_widget.addItem(item)

        self._list_widget.blockSignals(False)

        # 更新计数
        enabled_count = sum(1 for r in self._rules if r.get("enabled", True))
        self._count_label.setText(tr('rules.count', enabled_count))

        # 空状态 / 编辑区切换
        has_rules = len(self._rules) > 0
        if not has_rules:
            self._right_stack.setCurrentIndex(0)  # 空状态页
        else:
            self._right_stack.setCurrentIndex(1)  # 编辑页

        # 恢复选中
        if self._current_rule_id:
            for i, r in enumerate(self._rules):
                if r.get("id") == self._current_rule_id:
                    self._list_widget.setCurrentRow(i)
                    break
        elif has_rules:
            self._list_widget.setCurrentRow(0)

    def _on_rule_selected(self, row: int):
        """选中规则时更新右侧编辑区"""
        if row < 0 or row >= len(self._rules):
            self._current_rule_id = None
            self._set_editor_enabled(False)
            return

        rule = self._rules[row]
        self._current_rule_id = rule.get("id")
        is_file = rule.get("source") == "file"

        # 更新编辑区
        self._title_edit.blockSignals(True)
        self._content_edit.blockSignals(True)

        self._title_edit.setText(rule.get("title", ""))
        self._content_edit.setPlainText(rule.get("content", ""))

        self._title_edit.blockSignals(False)
        self._content_edit.blockSignals(False)

        # 文件规则只读
        self._title_edit.setReadOnly(is_file)
        self._content_edit.setReadOnly(is_file)

        # 源标签
        if is_file:
            fp = rule.get("file_path", "")
            self._source_label.setText(f"{tr('rules.file_readonly')}  {fp}")
        else:
            self._source_label.setText("")

        # 启用/禁用按钮
        enabled = rule.get("enabled", True)
        if is_file:
            self._btn_toggle.setVisible(False)
        else:
            self._btn_toggle.setVisible(True)
            self._btn_toggle.setText(
                tr('rules.disable') if enabled else tr('rules.enable')
            )

        self._set_editor_enabled(True)

    def _set_editor_enabled(self, enabled: bool):
        """切换右侧面板：编辑区 / 空状态"""
        if enabled:
            self._right_stack.setCurrentIndex(1)
        else:
            self._right_stack.setCurrentIndex(0)

    def _on_title_changed(self, text: str):
        """标题变更时实时保存"""
        if self._current_rule_id and not self._current_rule_id.startswith("file:"):
            for r in self._rules:
                if r.get("id") == self._current_rule_id:
                    r["title"] = text
                    self._dirty = True
                    break
            self._auto_save()
            # 更新列表显示
            row = self._list_widget.currentRow()
            if 0 <= row < self._list_widget.count():
                item = self._list_widget.item(row)
                if item:
                    item.setText(text or tr('rules.untitled'))

    def _on_content_changed(self):
        """内容变更时实时保存"""
        if self._current_rule_id and not self._current_rule_id.startswith("file:"):
            text = self._content_edit.toPlainText()
            for r in self._rules:
                if r.get("id") == self._current_rule_id:
                    r["content"] = text
                    self._dirty = True
                    break
            self._auto_save()

    def _auto_save(self):
        """自动保存 UI 规则"""
        if not self._dirty:
            return
        try:
            from ..utils.rules_manager import save_all_ui_rules
            ui_rules = [r for r in self._rules if r.get("source", "ui") == "ui"]
            save_all_ui_rules(ui_rules)
            self._dirty = False
        except Exception as e:
            print(f"[RulesEditor] Auto-save failed: {e}")

    def _on_add(self):
        """新增一条 UI 规则"""
        try:
            from ..utils.rules_manager import add_rule
            rule = add_rule(title=tr('rules.untitled'), content="")
            rule["source"] = "ui"
            self._rules.append(rule)
            self._current_rule_id = rule["id"]
            self._refresh_list()
            # 选中新建的规则
            self._list_widget.setCurrentRow(len(self._rules) - 1)
        except Exception as e:
            print(f"[RulesEditor] Add rule failed: {e}")

    def _on_delete(self):
        """删除当前选中的 UI 规则"""
        if not self._current_rule_id:
            return
        if self._current_rule_id.startswith("file:"):
            return  # 文件规则不允许在 UI 中删除

        # 确认
        current_rule = None
        for r in self._rules:
            if r.get("id") == self._current_rule_id:
                current_rule = r
                break
        if not current_rule:
            return

        title = current_rule.get("title", tr('rules.untitled'))
        reply = QtWidgets.QMessageBox.question(
            self, tr('rules.delete'),
            tr('rules.delete_confirm', title),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return

        try:
            from ..utils.rules_manager import delete_rule
            delete_rule(self._current_rule_id)
            self._rules = [r for r in self._rules if r.get("id") != self._current_rule_id]
            self._current_rule_id = None
            self._refresh_list()
        except Exception as e:
            print(f"[RulesEditor] Delete rule failed: {e}")

    def _on_toggle_enabled(self):
        """切换当前规则的启用/禁用状态"""
        if not self._current_rule_id or self._current_rule_id.startswith("file:"):
            return

        for r in self._rules:
            if r.get("id") == self._current_rule_id:
                new_enabled = not r.get("enabled", True)
                r["enabled"] = new_enabled
                self._dirty = True
                self._auto_save()
                self._refresh_list()
                # 更新按钮文本
                self._btn_toggle.setText(
                    tr('rules.disable') if new_enabled else tr('rules.enable')
                )
                break

    def _on_open_dir(self):
        """打开 rules/ 目录"""
        try:
            from ..utils.rules_manager import get_rules_dir, ensure_rules_dir
            import os, subprocess
            import sys as _sys
            ensure_rules_dir()
            rules_dir = get_rules_dir()
            if _sys.platform == 'win32':
                os.startfile(str(rules_dir))
            elif _sys.platform == 'darwin':
                subprocess.Popen(['open', str(rules_dir)])
            else:
                subprocess.Popen(['xdg-open', str(rules_dir)])
        except Exception as e:
            print(f"[RulesEditor] Open dir failed: {e}")

    def resizeEvent(self, event):
        """调整空状态提示的位置"""
        super().resizeEvent(event)
        # 让 empty_label 覆盖右侧编辑区域
        if hasattr(self, '_empty_label') and self._empty_label.parent():
            self._empty_label.setGeometry(self._empty_label.parent().rect())
