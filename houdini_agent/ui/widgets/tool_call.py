import time
from houdini_agent.qt_compat import QtWidgets, QtCore, QtGui
from .theme import _fmt_duration, _linkify_node_paths_plain
from .base import CollapsibleSection
from ..i18n import tr

from typing import List


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
        elif url.startswith(('http://', 'https://')):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))


# ============================================================
# 执行过程区块
# ============================================================

class ExecutionSection(CollapsibleSection):
    """执行过程 - 卡片式工具调用显示（默认折叠，用户手动展开）"""

    nodePathClicked = QtCore.Signal(str)  # 从子 ToolCallItem 冒泡上来

    def __init__(self, parent=None):
        super().__init__(tr('exec.running'), icon="", collapsed=True, parent=parent)
        self.setMinimumWidth(0)
        self._tool_calls: List[ToolCallItem] = []
        self._start_time = time.time()
        self._finalized = False

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

    def is_complete(self) -> bool:
        """Return True once all visible tool calls in this section have results."""
        return bool(self._tool_calls) and all(item._result is not None for item in self._tool_calls)

    def add_detail_widget(self, widget: QtWidgets.QWidget):
        """Attach operation details to this execution block so they collapse together."""
        self.content_layout.addWidget(widget)

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
        if self._finalized:
            return
        self._finalized = True
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
