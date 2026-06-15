import re
import time
from houdini_agent.qt_compat import QtWidgets, QtCore, QtGui
from .theme import CursorTheme
from ..i18n import tr


# ── 斜杠命令注册表 ──
# 每条: (command, icon, label_zh, label_en, description_zh, description_en, category)
SLASH_COMMANDS = [
    # ── 会话管理 ──
    ("clear",     "🗑",  "清空对话",     "Clear Chat",      "清空当前对话历史",           "Clear current conversation",   "session"),
    ("new",       "✨",  "新建会话",     "New Chat",         "创建一个新的对话",           "Create a new conversation",    "session"),
    # ── 记忆系统 ──
    ("memory",    "🧠",  "记忆状态",     "Memory Status",    "查看长期记忆统计和核心记忆", "View memory stats & core memories", "memory"),
    ("remember",  "📌",  "记住偏好",     "Remember",         "将内容写入核心记忆",         "Save content to core memory",  "memory"),
    ("forget",    "🧹",  "清除记忆",     "Forget",           "搜索并删除指定记忆",         "Search and delete a memory",   "memory"),
    ("search_mem","🔍",  "搜索记忆",     "Search Memory",    "在长期记忆中搜索",           "Search long-term memory",      "memory"),
    ("memories",  "📚",  "记忆库",       "Memory Library",   "打开记忆管理窗口",         "Open memory manager (full CRUD)", "memory"),
    # ── Houdini 场景 ──
    ("network",   "🌐",  "读取网络",     "Read Network",     "读取当前网络结构",           "Read current network structure","scene"),
    ("selection", "👆",  "读取选中",     "Read Selection",   "读取当前选中节点信息",       "Read selected node info",      "scene"),
    ("skills",    "⚡",  "技能列表",     "List Skills",      "列出所有可用 Skill",         "List all available skills",    "scene"),
    # ── 工具 ──
    ("status",    "📊",  "系统状态",     "System Status",    "查看记忆/成长/上下文统计",   "View memory/growth/context stats", "tool"),
    ("export",    "💾",  "导出训练",     "Export Training",  "导出对话为训练数据",         "Export conversation as training data", "tool"),
    ("image",     "🖼",  "附加图片",     "Attach Image",     "从文件选择图片附加到消息",   "Select image to attach",       "tool"),
    ("help",      "❓",  "帮助",         "Help",             "显示所有可用斜杠命令",       "Show all available commands",   "tool"),
]

# 按分类分组的标题
_SLASH_CATEGORY_LABELS = {
    "session": ("── 会话 ──", "── Session ──"),
    "memory":  ("── 记忆 ──", "── Memory ──"),
    "scene":   ("── 场景 ──", "── Scene ──"),
    "tool":    ("── 工具 ──", "── Tools ──"),
}


class NodeCompleterPopup(QtWidgets.QListWidget):
    """节点路径自动补全弹出窗 — 在输入 @ 时显示场景节点列表"""

    pathSelected = QtCore.Signal(str)  # 用户选中了一个节点路径

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(QtCore.Qt.ToolTip | QtCore.Qt.FramelessWindowHint)
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
# 斜杠命令弹出框
# ============================================================

class SlashCommandPopup(QtWidgets.QListWidget):
    """斜杠命令弹出窗 — 在输入 / 时显示可用命令"""

    commandSelected = QtCore.Signal(str)  # 用户选中了一个命令名

    def __init__(self, parent=None):
        super().__init__(parent)
        self._flags_applied = False
        self.setFixedWidth(300)
        self.setMaximumHeight(320)
        self.setObjectName("slashCompleter")
        self.itemActivated.connect(self._on_item_activated)
        self.setVisible(False)

    def show_filtered(self, prefix: str, anchor_widget: QtWidgets.QWidget,
                      cursor_rect, lang: str = 'zh'):
        """根据前缀过滤并显示命令列表"""
        if not self._flags_applied:
            self._flags_applied = True
            self.setWindowFlags(QtCore.Qt.ToolTip | QtCore.Qt.FramelessWindowHint)

        self.clear()
        lower_prefix = prefix.lower()
        is_zh = (lang == 'zh')

        # 按分类分组
        last_cat = None
        match_count = 0
        for cmd, icon, lbl_zh, lbl_en, desc_zh, desc_en, cat in SLASH_COMMANDS:
            label = lbl_zh if is_zh else lbl_en
            desc = desc_zh if is_zh else desc_en
            # 匹配命令名、标签、描述
            if lower_prefix and not any(lower_prefix in s.lower() for s in (cmd, label, desc)):
                continue
            # 分类标题
            if cat != last_cat:
                last_cat = cat
                cat_label = _SLASH_CATEGORY_LABELS.get(cat, ("──", "──"))
                header_item = QtWidgets.QListWidgetItem(cat_label[0] if is_zh else cat_label[1])
                header_item.setFlags(QtCore.Qt.NoItemFlags)  # 不可选
                font = header_item.font()
                font.setPointSize(max(7, font.pointSize() - 1))
                header_item.setFont(font)
                header_item.setForeground(QtGui.QColor(120, 130, 160))
                self.addItem(header_item)
            # 命令项
            display_text = f"{icon}  /{cmd}    {desc}"
            item = QtWidgets.QListWidgetItem(display_text)
            item.setData(QtCore.Qt.UserRole, cmd)
            self.addItem(item)
            match_count += 1

        if match_count == 0:
            self.setVisible(False)
            return

        # 定位到光标下方
        global_pos = anchor_widget.mapToGlobal(cursor_rect.bottomLeft())
        self.move(global_pos.x(), global_pos.y() + 4)
        # 动态调整高度
        row_h = 24
        total_h = min(320, (self.count()) * row_h + 12)
        self.setFixedHeight(max(80, total_h))
        self.setVisible(True)
        # 选中第一个非标题项
        for i in range(self.count()):
            if self.item(i).flags() & QtCore.Qt.ItemIsSelectable:
                self.setCurrentRow(i)
                break

    def _on_item_activated(self, item):
        cmd = item.data(QtCore.Qt.UserRole)
        if cmd:
            self.commandSelected.emit(cmd)
            self.setVisible(False)

    def select_next(self):
        """选中下一个可选项"""
        row = self.currentRow()
        for i in range(row + 1, self.count()):
            if self.item(i).flags() & QtCore.Qt.ItemIsSelectable:
                self.setCurrentRow(i)
                return

    def select_prev(self):
        """选中上一个可选项"""
        row = self.currentRow()
        for i in range(row - 1, -1, -1):
            if self.item(i).flags() & QtCore.Qt.ItemIsSelectable:
                self.setCurrentRow(i)
                return

    def confirm_current(self) -> bool:
        """确认当前选中项，返回是否成功"""
        current = self.currentItem()
        if current:
            cmd = current.data(QtCore.Qt.UserRole)
            if cmd:
                self.commandSelected.emit(cmd)
                self.setVisible(False)
                return True
        return False

    def keyPressEvent(self, event):
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            self.confirm_current()
            return
        elif event.key() == QtCore.Qt.Key_Escape:
            self.setVisible(False)
            return
        super().keyPressEvent(event)


# ============================================================
# 输入区域
# ============================================================

class InputResizeHandle(QtWidgets.QFrame):
    """Thin drag handle above the chat input."""

    dragged = QtCore.Signal(int)  # dy since last mouse move
    resetRequested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("inputResizeHandle")
        self.setFixedHeight(8)
        self.setCursor(QtCore.Qt.SizeVerCursor)
        self.setToolTip(tr("input.resize.tooltip"))
        self._dragging = False
        self._last_global_y = 0

    @staticmethod
    def _event_global_y(event) -> int:
        try:
            return int(event.globalY())
        except AttributeError:
            try:
                return int(event.globalPosition().y())
            except AttributeError:
                return int(event.globalPos().y())

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == QtCore.Qt.LeftButton:
            self._dragging = True
            self._last_global_y = self._event_global_y(event)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._dragging:
            y = self._event_global_y(event)
            dy = y - self._last_global_y
            self._last_global_y = y
            if dy:
                self.dragged.emit(dy)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        self._dragging = False
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        if event.button() == QtCore.Qt.LeftButton:
            self.resetRequested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def retranslate(self):
        self.setToolTip(tr("input.resize.tooltip"))


class ChatInput(QtWidgets.QPlainTextEdit):
    """聊天输入框 — 自适应高度，支持自动换行、多行输入、图片粘贴/拖拽

    核心逻辑：统计文档中所有视觉行（含软换行），按行高计算目标高度，
    使输入框向上扩展而非隐藏已有行。
    支持 @节点路径 补全和从 Network Editor 拖拽节点。
    """

    sendRequested = QtCore.Signal()
    imageDropped = QtCore.Signal(QtGui.QImage)  # 粘贴或拖拽图片时触发
    atTriggered = QtCore.Signal(str, QtCore.QRect)  # @ 触发补全: (当前前缀, 光标矩形)
    slashTriggered = QtCore.Signal(str, QtCore.QRect)  # / 触发补全: (当前前缀, 光标矩形)

    _MIN_H = 44
    _AUTO_MAX_H = 220
    _MANUAL_MAX_H = 360

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
        self.setMaximumHeight(self._MANUAL_MAX_H)
        self._manual_height = self._load_manual_height()
        if self._manual_height is not None:
            self.setFixedHeight(self._manual_height)

        # ★ PySide2 / PySide6 全平台 IME 支持（中文/日文/韩文）
        # ------------------------------------------------------------------
        # 问题背景：
        #   PySide2 嵌入 Houdini 时，macOS / Windows 上输入法可能不激活。
        #   macOS 的 NSTextInputClient 协议尤其依赖 inputMethodQuery 返回
        #   正确的光标矩形/周围文本/光标位置等信息，否则 IME 候选窗口
        #   无法定位甚至不会弹出。
        # ------------------------------------------------------------------
        # 1. 显式启用输入法
        self.setAttribute(QtCore.Qt.WA_InputMethodEnabled, True)
        # 2. 显式设置焦点策略，确保 Tab/Click 都能获取焦点
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        # 3. 设置输入法提示：自由文本
        try:
            self.setInputMethodHints(QtCore.Qt.ImhNone)
        except Exception:
            pass  # 极少数 PySide2 版本不支持此调用
        # 4. macOS 特有：确保焦点矩形可见（某些嵌入场景下默认关闭）
        try:
            self.setAttribute(QtCore.Qt.WA_MacShowFocusRect, True)
        except Exception:
            pass

        # 使用 textChanged，并延迟到下一事件循环执行（确保布局先完成）
        self.textChanged.connect(self._schedule_adjust)
        self.textChanged.connect(self._check_at_trigger)
        self.textChanged.connect(self._check_slash_trigger)
        # @ 补全状态
        self._at_active = False
        self._at_start_pos = -1
        self._completer_popup: 'NodeCompleterPopup | None' = None
        # / 斜杠命令补全状态
        self._slash_active = False
        self._slash_start_pos = -1
        self._slash_popup: 'SlashCommandPopup | None' = None
        # ★ IME 预编辑状态追踪
        self._ime_composing = False

    def set_completer_popup(self, popup: 'NodeCompleterPopup'):
        """设置节点补全弹出框引用，用于键盘导航和自动关闭"""
        self._completer_popup = popup

    def set_slash_popup(self, popup: 'SlashCommandPopup'):
        """设置斜杠命令弹出框引用"""
        self._slash_popup = popup

    def _schedule_adjust(self):
        """延迟调整高度，确保文档布局已更新"""
        QtCore.QTimer.singleShot(0, self._adjust_height)

    def _adjust_height(self):
        """根据视觉行数（含软换行）自动调整高度——向上扩展"""
        if self._manual_height is not None:
            h = max(self._MIN_H, min(self._MANUAL_MAX_H, int(self._manual_height)))
            if h != self.height():
                self.setFixedHeight(h)
                self.updateGeometry()
            return

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

        h = max(self._MIN_H, min(self._AUTO_MAX_H, total))
        if h != self.height():
            self.setFixedHeight(h)
            # 通知父布局重新分配空间
            self.updateGeometry()

    def _load_manual_height(self):
        try:
            settings = QtCore.QSettings("HoudiniAI", "Assistant")
            val = settings.value("input_manual_height", "")
            if val in ("", None):
                return None
            h = int(val)
            return max(self._MIN_H, min(self._MANUAL_MAX_H, h))
        except Exception:
            return None

    def _save_manual_height(self):
        try:
            settings = QtCore.QSettings("HoudiniAI", "Assistant")
            if self._manual_height is None:
                settings.remove("input_manual_height")
            else:
                settings.setValue("input_manual_height", int(self._manual_height))
        except Exception:
            pass

    def set_manual_height(self, height: int):
        self._manual_height = max(self._MIN_H, min(self._MANUAL_MAX_H, int(height)))
        self.setFixedHeight(self._manual_height)
        self._save_manual_height()
        self.updateGeometry()

    def reset_auto_height(self):
        self._manual_height = None
        self._save_manual_height()
        self._adjust_height()

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

    # ---- 斜杠命令补全 ----

    def _check_slash_trigger(self):
        """检测输入中的 / 字符，触发斜杠命令补全（仅在行首或纯 / 开头时触发）"""
        cursor = self.textCursor()
        pos = cursor.position()
        text = self.toPlainText()

        if not text or pos == 0:
            if self._slash_active:
                self._slash_active = False
                self._hide_slash()
            return

        # 仅当 / 在文本最开头时触发（整个输入为 /xxx）
        if not text.startswith('/'):
            if self._slash_active:
                self._slash_active = False
                self._hide_slash()
            return

        # 提取 / 之后到光标位置的内容
        prefix_after_slash = text[1:pos]
        # 如果包含空格或换行，说明已超出命令名范围
        if ' ' in prefix_after_slash or '\n' in prefix_after_slash:
            if self._slash_active:
                self._slash_active = False
                self._hide_slash()
            return

        self._slash_active = True
        self._slash_start_pos = 0
        crect = self.cursorRect(cursor)
        self.slashTriggered.emit(prefix_after_slash, crect)

    def _hide_slash(self):
        """隐藏斜杠命令弹出框"""
        if self._slash_popup and self._slash_popup.isVisible():
            self._slash_popup.setVisible(False)

    def cancel_slash_completion(self):
        """取消当前斜杠命令补全"""
        self._slash_active = False
        self._slash_start_pos = -1
        self._hide_slash()

    def insert_slash_completion(self, command: str):
        """斜杠命令被选中后，清空输入框（命令将直接执行，不需要保留文字）"""
        self.clear()
        self._slash_active = False
        self._slash_start_pos = -1

    def _is_slash_visible(self) -> bool:
        """斜杠命令弹出框是否可见"""
        return (self._slash_popup is not None
                and self._slash_popup.isVisible()
                and self._slash_popup.count() > 0)

    def inputMethodQuery(self, query):
        """★ macOS IME 关键修复：为输入法提供光标位置和周围文本信息

        macOS 的输入法框架（NSTextInputClient 协议）通过此方法查询：
          - ImEnabled       → 此控件是否接受输入法输入
          - ImCursorRectangle → 光标在控件中的矩形区域（用于定位候选框）
          - ImSurroundingText → 光标周围的文本（辅助联想/智能选词）
          - ImCursorPosition  → 光标在周围文本中的位置
          - ImFont           → 当前字体信息
          - ImHints          → 输入法提示

        如果不覆写此方法，PySide2 嵌入 Houdini 时（尤其 macOS）
        可能返回错误值或零矩形，导致 IME 不激活或候选框位置异常。
        """
        qt = QtCore.Qt
        if query == qt.ImEnabled:
            return True
        if query == qt.ImCursorRectangle:
            # 返回光标在控件坐标系中的矩形
            cursor_rect = self.cursorRect()
            return cursor_rect
        if query == qt.ImFont:
            return self.font()
        if query == qt.ImCursorPosition:
            tc = self.textCursor()
            block = tc.block()
            return tc.position() - block.position()
        if query == qt.ImSurroundingText:
            tc = self.textCursor()
            block = tc.block()
            return block.text()
        if query == qt.ImCurrentSelection:
            tc = self.textCursor()
            return tc.selectedText()
        try:
            if query == qt.ImHints:
                return qt.ImhNone
        except Exception:
            pass
        # 其他查询交给父类
        return super().inputMethodQuery(query)

    def inputMethodEvent(self, event):
        """★ IME 输入法事件处理（中文/日文/韩文等）— 全平台增强版

        PySide2 在 Houdini 环境下需要显式处理 inputMethodEvent，
        否则中文输入法的预编辑（composing）和提交（commit）可能无法正常工作。

        IME 工作流程：
        1. 用户开始输入拼音 → preeditString 不为空（composing 状态）
        2. 用户选择候选词 → commitString 不为空，preeditString 清空
        3. 用户按 Esc 取消 → preeditString 清空，commitString 为空

        macOS 特别注意：
        - 某些 PySide2 版本在 macOS 上不会正确传递 commit 事件
        - 需要确保 commitString 被手动插入文本光标
        """
        preedit = event.preeditString()
        commit = event.commitString()

        # 更新 composing 状态
        self._ime_composing = bool(preedit)

        # 先让父类处理（标准路径）
        super().inputMethodEvent(event)

        # macOS PySide2 修补：如果父类没有正确处理 commitString，
        # 手动将已确认的文字插入光标位置。
        # 通过检查：如果有 commit 文字，但当前文本中找不到它（说明父类漏了），
        # 则手动插入。
        if commit and not preedit:
            tc = self.textCursor()
            current_text = self.toPlainText()
            # 简单检查：如果 commit 的文字在光标位置之前不存在，手动插入
            # 注意：这是一个保守检查，只有在父类确实没有处理时才介入
            pos = tc.position()
            before = current_text[:pos]
            if not before.endswith(commit):
                tc.insertText(commit)
                self.setTextCursor(tc)

    def keyPressEvent(self, event):
        key = event.key()

        # ★ IME composing 中：不拦截任何按键，全部交给输入法处理
        # 当用户正在输入拼音/选择候选词时，Enter/Esc 等键应由 IME 处理，
        # 而不是触发"发送消息"或"取消补全"
        if self._ime_composing:
            super().keyPressEvent(event)
            return

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

        # ── / 斜杠命令补全活跃时的键盘处理 ──
        if self._slash_active and self._is_slash_visible():
            popup = self._slash_popup

            if key == QtCore.Qt.Key_Escape:
                self.cancel_slash_completion()
                return

            if key == QtCore.Qt.Key_Up:
                popup.select_prev()
                return

            if key == QtCore.Qt.Key_Down:
                popup.select_next()
                return

            if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter) and not (event.modifiers() & QtCore.Qt.ShiftModifier):
                if popup.confirm_current():
                    return

            if key == QtCore.Qt.Key_Tab:
                if popup.confirm_current():
                    return

        elif self._slash_active and key == QtCore.Qt.Key_Escape:
            self.cancel_slash_completion()
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
        if self._is_slash_visible():
            self.cancel_slash_completion()
        super().mousePressEvent(event)

    def focusInEvent(self, event):
        """★ 获焦时确保 IME 正确激活（macOS 关键修复）

        macOS 上，当 QPlainTextEdit 嵌入 Houdini 等宿主应用时，
        获焦时 IME 可能不会自动激活。通过显式调用 update() 和
        重新设置 WA_InputMethodEnabled，强制系统重新检查 IME 状态。
        """
        super().focusInEvent(event)
        # 确保 IME 标志仍然有效
        self.setAttribute(QtCore.Qt.WA_InputMethodEnabled, True)
        # 触发控件重绘，间接通知系统重新查询 inputMethodQuery
        self.update()

    def focusOutEvent(self, event):
        """失焦时关闭补全弹窗并重置 IME 状态"""
        self._ime_composing = False  # 重置 IME 状态
        # 延迟关闭：如果焦点转移到弹窗本身（用户点击弹窗），不关闭
        QtCore.QTimer.singleShot(100, self._check_focus_dismiss)
        super().focusOutEvent(event)

    def _check_focus_dismiss(self):
        """检查是否需要因失焦而关闭弹窗"""
        if not self.hasFocus():
            if self._is_completer_visible():
                if self._completer_popup and not self._completer_popup.hasFocus():
                    self.cancel_at_completion()
            if self._is_slash_visible():
                if self._slash_popup and not self._slash_popup.hasFocus():
                    self.cancel_slash_completion()

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
        super().__init__(tr("btn.stop"), parent)
        self.setObjectName("btnStop")

    def retranslate(self):
        self.setText(tr("btn.stop"))


# ============================================================
# 发送按钮
# ============================================================

class SendButton(QtWidgets.QPushButton):
    """发送按钮"""

    def __init__(self, parent=None):
        super().__init__(tr("btn.send"), parent)
        self.setObjectName("btnSend")

    def retranslate(self):
        self.setText(tr("btn.send"))
