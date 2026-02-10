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
# 思考过程区块
# ============================================================

class ThinkingSection(CollapsibleSection):
    """思考过程 - 显示 AI 的思考内容（支持多轮思考累计计时）"""
    
    def __init__(self, parent=None):
        super().__init__("思考中...", icon="", collapsed=True, parent=parent)
        self._thinking_text = ""
        self._start_time = time.time()
        self._accumulated_seconds = 0.0   # 已完成轮次的累计时间
        self._round_start = time.time()   # 当前轮次开始时间
        self._round_count = 0             # 思考轮次计数
        
        # 思考内容标签（必须 PlainText，否则 QLabel 会将 <scatter>/<grid> 等节点名当 HTML 标签吞掉）
        self.thinking_label = QtWidgets.QLabel()
        self.thinking_label.setTextFormat(QtCore.Qt.PlainText)
        self.thinking_label.setWordWrap(True)
        self.thinking_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.thinking_label.setStyleSheet(f"""
            color: {CursorTheme.TEXT_MUTED};
            font-size: 13px;
            font-family: {CursorTheme.FONT_BODY};
            padding: 4px;
            line-height: 1.4;
        """)
        self.content_layout.addWidget(self.thinking_label)
        
        # 更新标题样式
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
    
    def _total_elapsed(self) -> float:
        """当前累计总思考时间（含正在进行的轮次）"""
        if self._finalized:
            return self._accumulated_seconds
        return self._accumulated_seconds + (time.time() - self._round_start)
    
    def append_thinking(self, text: str):
        """追加思考内容"""
        # 清除可能残留的 U+FFFD 替换符（encoding 异常时产生的菱形乱码）
        if '\ufffd' in text:
            text = text.replace('\ufffd', '')
        self._thinking_text += text
        self.thinking_label.setText(self._thinking_text)
    
    def update_time(self):
        """更新思考时间（仅在未 finalize 时才更新）"""
        if self._finalized:
            return
        self.set_title(f"思考中... ({_fmt_duration(self._total_elapsed())})")
    
    @property
    def _finalized(self):
        return getattr(self, '_is_finalized', False)
    
    def resume(self):
        """恢复思考（新一轮 <think> 开始）— 重新开始计时"""
        # 标记为未完成
        self._is_finalized = False
        self._round_start = time.time()
        self._round_count += 1
        # 追加分隔符表示新一轮
        self._thinking_text += f"\n--- 第 {self._round_count + 1} 轮思考 ---\n"
        self.thinking_label.setText(self._thinking_text)
        self.set_title(f"思考中... ({_fmt_duration(self._total_elapsed())})")
        # 确保展开以显示新内容
        if self._collapsed:
            self.toggle()
    
    def finalize(self):
        """完成当前轮思考 — 累计时间并更新标题"""
        if self._finalized:
            return
        self._is_finalized = True
        # 累计本轮时间
        self._accumulated_seconds += (time.time() - self._round_start)
        total = self._accumulated_seconds
        self.set_title(f"思考过程 ({_fmt_duration(total)})")
        # 思考完成后自动折叠，用户可点击展开查看
        if not self._collapsed:
            self.toggle()


# ============================================================
# 工具调用项
# ============================================================

class ToolCallItem(QtWidgets.QFrame):
    """单个工具调用卡片 — 状态图标 + 名称 + 耗时 + 可展开结果"""
    
    _SUMMARY_LEN = 120  # 摘要截断长度
    
    def __init__(self, tool_name: str, parent=None):
        super().__init__(parent)
        self.tool_name = tool_name
        self._result = None
        self._full_result = ""   # 完整结果文本
        self._summary = ""       # 摘要文本
        self._expanded = False   # 是否展开
        self._success = None
        self._start_time = time.time()
        
        self.setStyleSheet(f"""
            ToolCallItem {{
                background: {CursorTheme.BG_SECONDARY};
                border: 1px solid {CursorTheme.BORDER};
                margin: 2px 0;
            }}
        """)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)
        
        # ---- 标题行: 图标 + 名称 + 展开提示 + 耗时 ----
        title_row = QtWidgets.QHBoxLayout()
        title_row.setSpacing(6)
        
        self.status_icon = QtWidgets.QLabel("...")
        self.status_icon.setStyleSheet("font-size: 12px;")
        self.status_icon.setFixedWidth(18)
        title_row.addWidget(self.status_icon)
        
        self.name_label = QtWidgets.QLabel(tool_name)
        self.name_label.setWordWrap(True)
        self.name_label.setStyleSheet(f"""
            color: {CursorTheme.ACCENT_BEIGE};
            font-size: 13px;
            font-family: {CursorTheme.FONT_CODE};
            font-weight: bold;
        """)
        title_row.addWidget(self.name_label, 1)
        
        # 展开/收缩提示（长结果才显示）
        self.expand_hint = QtWidgets.QLabel("")
        self.expand_hint.setStyleSheet(
            f"color:{CursorTheme.TEXT_MUTED};font-size:10px;"
            f"font-family:{CursorTheme.FONT_CODE};"
        )
        self.expand_hint.setVisible(False)
        title_row.addWidget(self.expand_hint)
        
        self.duration_label = QtWidgets.QLabel("")
        self.duration_label.setStyleSheet(
            f"color:{CursorTheme.TEXT_MUTED};font-size:11px;"
            f"font-family:{CursorTheme.FONT_CODE};"
        )
        title_row.addWidget(self.duration_label)
        
        layout.addLayout(title_row)
        
        # ---- 进度条 ----
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
        layout.addWidget(self.progress_bar)
        
        # ---- 结果区域（初始隐藏） ----
        self.result_label = QtWidgets.QLabel()
        self.result_label.setWordWrap(True)
        self.result_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.result_label.setVisible(False)
        self.result_label.setStyleSheet(f"""
            color: {CursorTheme.TEXT_MUTED};
            font-size: 12px;
            padding: 2px 0 0 24px;
            font-family: {CursorTheme.FONT_CODE};
        """)
        layout.addWidget(self.result_label)
    
    def mousePressEvent(self, event):
        """点击卡片切换展开/收缩"""
        if self._full_result and len(self._full_result) > self._SUMMARY_LEN:
            self._expanded = not self._expanded
            self._update_result_display()
        super().mousePressEvent(event)
    
    def _update_result_display(self):
        """根据展开状态更新结果显示"""
        if self._expanded:
            self.result_label.setText(self._full_result)
            self.expand_hint.setText("[collapse]")
        else:
            self.result_label.setText(self._summary)
            self.expand_hint.setText("[expand]")
    
    def set_result(self, result: str, success: bool = True):
        """设置工具执行结果"""
        self._result = result
        self._success = success
        self._full_result = result
        elapsed = time.time() - self._start_time
        
        # 隐藏进度条
        self.progress_bar.setVisible(False)
        
        # 更新图标和耗时
        if success:
            self.status_icon.setText("ok")
            self.status_icon.setStyleSheet(f"color: {CursorTheme.ACCENT_GREEN}; font-size: 11px; font-family: {CursorTheme.FONT_CODE};")
            self.setStyleSheet(f"""
                ToolCallItem {{
                    background: {CursorTheme.BG_SECONDARY};
                    border: 1px solid #3a3a30;
                    border-left: 3px solid {CursorTheme.ACCENT_BEIGE};
                    margin: 2px 0;
                }}
            """)
        else:
            self.status_icon.setText("err")
            self.status_icon.setStyleSheet(f"color: {CursorTheme.ACCENT_RED}; font-size: 11px; font-family: {CursorTheme.FONT_CODE};")
            self.setStyleSheet(f"""
                ToolCallItem {{
                    background: {CursorTheme.BG_SECONDARY};
                    border: 1px solid #4a2a2a;
                    border-left: 3px solid {CursorTheme.ACCENT_RED};
                    margin: 2px 0;
                }}
            """)
        
        self.duration_label.setText(f"{elapsed:.1f}s")
        
        # 构建摘要和完整内容
        if len(result) > self._SUMMARY_LEN:
            self._summary = result[:self._SUMMARY_LEN] + "..."
            self.expand_hint.setText("[expand]")
            self.expand_hint.setVisible(True)
        else:
            self._summary = result
        
        self.result_label.setText(self._summary)
        self.result_label.setVisible(True)
        
        if not success:
            self.result_label.setStyleSheet(f"""
                color: {CursorTheme.ACCENT_RED};
                font-size: 12px;
                padding: 2px 0 0 24px;
                font-family: {CursorTheme.FONT_CODE};
            """)


# ============================================================
# 执行过程区块
# ============================================================

class ExecutionSection(CollapsibleSection):
    """执行过程 - 卡片式工具调用显示（默认折叠，用户手动展开）"""
    
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
                item.status_icon.setText("ok")
                item.duration_label.setText(f"{time.time() - item._start_time:.1f}s")
                item._result = ""  # 标记已完成，避免被重复处理
                item._success = True
        
        success = sum(1 for item in self._tool_calls if item._success)
        failed = total - success
        
        if failed > 0:
            self.set_title(f"执行完成 ({success} ok, {failed} err, {_fmt_duration(elapsed)})")
        else:
            self.set_title(f"执行完成 ({total}个操作, {_fmt_duration(elapsed)})")


# ============================================================
# 用户消息
# ============================================================

class UserMessage(QtWidgets.QWidget):
    """用户消息 - 简洁显示"""
    
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 4)
        layout.setSpacing(2)
        
        # 用户消息内容
        self.content = QtWidgets.QLabel(text)
        self.content.setWordWrap(True)
        self.content.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.content.setStyleSheet(f"""
            QLabel {{
                color: {CursorTheme.TEXT_BRIGHT};
                font-size: 16px;
                font-family: {CursorTheme.FONT_BODY};
                padding: 8px 12px;
                background: {CursorTheme.BG_TERTIARY};
                border-left: 3px solid {CursorTheme.BORDER_USER};
            }}
        """)
        layout.addWidget(self.content)


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
        
        # 状态行
        self.status_label = QtWidgets.QLabel("思考中...")
        self.status_label.setStyleSheet(f"""
            color: {CursorTheme.TEXT_MUTED};
            font-size: 13px;
            font-family: {CursorTheme.FONT_BODY};
        """)
        summary_layout.addWidget(self.status_label)
        
        # 内容（流式 + 最终渲染统一 14px）
        # PlainText 防止流式内容中 <node_name> 被当 HTML 标签吞掉
        self.content_label = QtWidgets.QLabel()
        self.content_label.setTextFormat(QtCore.Qt.PlainText)
        self.content_label.setWordWrap(True)
        self.content_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.content_label.setStyleSheet(f"""
            color: {CursorTheme.TEXT_PRIMARY};
            font-size: 14px;
            font-family: {CursorTheme.FONT_BODY};
            line-height: 1.6;
        """)
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
    
    def append_content(self, text: str):
        """追加内容（流式场景高频调用，需要高效）"""
        if not text.strip():
            return
        # 清除 U+FFFD 替换符（encoding 异常残留）
        if '\ufffd' in text:
            text = text.replace('\ufffd', '')
        self._content += text
        # 流式阶段：直接追加到 QLabel，不做全量 re.sub
        # 只在最终 finalize 时做完整清理
        self.content_label.setText(self._content)
    
    def set_content(self, text: str):
        """设置内容"""
        self._content = text
        self.content_label.setText(self._clean_content(text))
    
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
        
        # 处理最终内容 — 使用富文本渲染
        content = self._clean_content(self._content)
        
        if not content:
            if self._has_execution:
                self.content_label.setText("执行完成，详见上方执行过程。")
            else:
                self.content_label.setText("（无回复内容）")
            self.content_label.setStyleSheet(f"""
                color: {CursorTheme.TEXT_MUTED};
                font-size: 13px;
            """)
        else:
            # 始终显示完整回复内容（不折叠）
            if SimpleMarkdown.has_rich_content(content):
                self.content_label.setVisible(False)
                rich = RichContentWidget(content, self.summary_frame)
                rich.createWrangleRequested.connect(self.createWrangleRequested.emit)
                self.summary_frame.layout().addWidget(rich)
            else:
                self.content_label.setText(content)
    


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
    
    def __init__(self, operation: str, count: int, node_paths: list = None, parent=None):
        super().__init__(parent)
        self._node_paths = node_paths or []
        self._decided = False  # 用户是否已做出选择
        
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(4)
        
        if operation == 'create':
            prefix = "+"
            color = CursorTheme.ACCENT_GREEN
        else:
            prefix = "-"
            color = CursorTheme.ACCENT_RED
        
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
    
    def _on_keep(self):
        if self._decided:
            return
        self._decided = True
        self._undo_btn.setVisible(False)
        self._keep_btn.setVisible(False)
        self._status_label.setText("已保留")
        self._status_label.setVisible(True)


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
        """行内格式: **粗体**, *斜体*, ~~删除线~~, `代码`, [链接](url)"""
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
        
        # ---- 输出区域 ----
        has_output = bool(output and output.strip())
        has_error = bool(error and error.strip())
        
        if has_output or has_error:
            output_widget = QtWidgets.QTextEdit()
            output_widget.setReadOnly(True)
            output_widget.setLineWrapMode(QtWidgets.QTextEdit.NoWrap)
            output_widget.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
            output_widget.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
            
            # 拼装输出内容
            parts = []
            if has_output:
                parts.append(f'<span style="color:{CursorTheme.TEXT_PRIMARY};">'
                             f'{html.escape(output.strip())}</span>')
            if has_error:
                parts.append(f'<span style="color:{CursorTheme.ACCENT_RED};">'
                             f'{html.escape(error.strip())}</span>')
            
            content_html = '<br>'.join(parts)
            output_widget.setHtml(
                f'<pre style="margin:0;white-space:pre;font-family:Consolas,Monaco,monospace;'
                f'font-size:12px;">{content_html}</pre>'
            )
            
            output_widget.setStyleSheet(f"""
                QTextEdit {{
                    background: #141428;
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
            
            doc2 = output_widget.document()
            doc2.setDocumentMargin(4)
            out_h = min(int(doc2.size().height()) + 16, 200)
            output_widget.setFixedHeight(out_h)
            layout.addWidget(output_widget)
        
        elif not success:
            # 没有输出也没有错误但失败了
            err_label = QtWidgets.QLabel("执行失败（无详细信息）")
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
    """

    createWrangleRequested = QtCore.Signal(str)

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
                lbl.setOpenExternalLinks(True)
                lbl.setTextInteractionFlags(
                    QtCore.Qt.TextSelectableByMouse
                    | QtCore.Qt.LinksAccessibleByMouse
                )
                lbl.setText(seg[1])
                lbl.setStyleSheet(self._TEXT_STYLE)
                layout.addWidget(lbl)
            elif seg[0] == 'code':
                cb = CodeBlockWidget(seg[2], seg[1], self)
                cb.createWrangleRequested.connect(self.createWrangleRequested.emit)
                layout.addWidget(cb)


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
# 输入区域
# ============================================================

class ChatInput(QtWidgets.QPlainTextEdit):
    """聊天输入框 — 自适应高度，支持自动换行和多行输入
    
    核心逻辑：统计文档中所有视觉行（含软换行），按行高计算目标高度，
    使输入框向上扩展而非隐藏已有行。
    """
    
    sendRequested = QtCore.Signal()
    
    _MIN_H = 44
    _MAX_H = 220
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("输入消息... (Enter 发送, Shift+Enter 换行)")
        # 确保自动换行
        self.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
        self.setWordWrapMode(QtGui.QTextOption.WrapAtWordBoundaryOrAnywhere)
        # 隐藏滚动条（高度不够时才出现）
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
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
    
    def keyPressEvent(self, event):
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            if event.modifiers() & QtCore.Qt.ShiftModifier:
                super().keyPressEvent(event)
            else:
                self.sendRequested.emit()
                return
        super().keyPressEvent(event)
    
    def resizeEvent(self, event):
        """窗口宽度变化时重新计算高度（自动换行可能改变行数）"""
        super().resizeEvent(event)
        self._schedule_adjust()


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
    """Token 使用分析面板 - 显示每次 API 调用的详细统计"""

    _COL_HEADERS = ["#", "时间", "模型", "Input", "Cache Hit", "Cache Write", "Output", "Total", ""]
    _COL_STRETCH = [0, 0, 1, 0, 0, 0, 0, 0, 1]

    def __init__(self, call_records: list, token_stats: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("API 调用分析")
        self.setMinimumSize(780, 520)
        self.resize(860, 600)
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
        foot = QtWidgets.QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)
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
        grid.setHorizontalSpacing(32)
        grid.setVerticalSpacing(8)

        total_in = stats.get('input_tokens', 0)
        total_out = stats.get('output_tokens', 0)
        cache_r = stats.get('cache_read', 0)
        cache_w = stats.get('cache_write', 0)
        reqs = stats.get('requests', 0)
        total = stats.get('total_tokens', 0)
        cache_total = cache_r + cache_w
        hit_rate = (cache_r / cache_total * 100) if cache_total > 0 else 0

        metrics = [
            ("Total Requests", f"{reqs}", CursorTheme.ACCENT_BLUE),
            ("Input Tokens", self._fmt_k(total_in), CursorTheme.ACCENT_PURPLE),
            ("Output Tokens", self._fmt_k(total_out), CursorTheme.ACCENT_GREEN),
            ("Cache Hit", self._fmt_k(cache_r), "#4ec9b0"),
            ("Cache Write", self._fmt_k(cache_w), CursorTheme.ACCENT_ORANGE),
            ("Hit Rate", f"{hit_rate:.1f}%", CursorTheme.ACCENT_YELLOW),
        ]
        for col, (label, value, color) in enumerate(metrics):
            lbl = QtWidgets.QLabel(label)
            lbl.setStyleSheet(f"color:{CursorTheme.TEXT_MUTED};font-size:11px;border:none;")
            lbl.setAlignment(QtCore.Qt.AlignCenter)
            grid.addWidget(lbl, 0, col)

            val = QtWidgets.QLabel(value)
            val.setStyleSheet(f"color:{color};font-size:18px;font-weight:bold;font-family:'Consolas','Monaco',monospace;border:none;")
            val.setAlignment(QtCore.Qt.AlignCenter)
            grid.addWidget(val, 1, col)

        # 进度条: input vs output vs cache
        if total > 0:
            bar = _BarWidget([
                (cache_r, "#4ec9b0"),
                (cache_w, CursorTheme.ACCENT_ORANGE),
                (max(total_in - cache_r - cache_w, 0), CursorTheme.ACCENT_PURPLE),
                (total_out, CursorTheme.ACCENT_GREEN),
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

        for idx, rec in enumerate(records):
            row = self._make_record_row(idx, rec, max_total)
            table_layout.addWidget(row)

        table_layout.addStretch()
        scroll.setWidget(table_widget)
        vbox.addWidget(scroll, 1)

        return container

    def _make_row_widget(self, cells: list, is_header=False) -> QtWidgets.QWidget:
        """创建一行（表头或数据行）"""
        row_w = QtWidgets.QWidget()
        row_h = QtWidgets.QHBoxLayout(row_w)
        row_h.setContentsMargins(4, 3, 4, 3)
        row_h.setSpacing(4)

        font_size = "11px" if is_header else "12px"
        fg = CursorTheme.TEXT_MUTED if is_header else CursorTheme.TEXT_PRIMARY
        weight = "bold" if is_header else "normal"
        font_family = f"font-family:'Consolas','Monaco',monospace;" if not is_header else ""

        widths = [28, 54, 100, 64, 64, 64, 64, 64, 0]  # 0 = stretch

        for i, text in enumerate(cells):
            lbl = QtWidgets.QLabel(str(text))
            lbl.setStyleSheet(f"color:{fg};font-size:{font_size};font-weight:{weight};{font_family}border:none;padding:0 2px;")
            if widths[i] > 0:
                lbl.setFixedWidth(widths[i])
            lbl.setAlignment(QtCore.Qt.AlignRight if i >= 3 and i <= 7 else QtCore.Qt.AlignLeft)
            if widths[i] == 0:
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
        row_h.setSpacing(4)

        ts = rec.get('timestamp', '')
        # 只显示 HH:MM:SS
        if len(ts) > 10:
            ts = ts[11:19]
        model = rec.get('model', '-')
        # 截短模型名
        if len(model) > 14:
            model = model[:12] + '..'
        inp = rec.get('input_tokens', 0)
        c_hit = rec.get('cache_hit', 0)
        c_miss = rec.get('cache_miss', 0)
        out = rec.get('output_tokens', 0)
        total = rec.get('total_tokens', 0)

        cells = [
            str(idx + 1),
            ts,
            model,
            self._fmt_k(inp),
            self._fmt_k(c_hit),
            self._fmt_k(c_miss),
            self._fmt_k(out),
            self._fmt_k(total),
        ]
        widths = [28, 54, 100, 64, 64, 64, 64, 64]
        colors = [
            CursorTheme.TEXT_MUTED,
            CursorTheme.TEXT_MUTED,
            CursorTheme.TEXT_PRIMARY,
            CursorTheme.ACCENT_PURPLE,
            "#4ec9b0",
            CursorTheme.ACCENT_ORANGE,
            CursorTheme.ACCENT_GREEN,
            CursorTheme.TEXT_BRIGHT,
        ]
        for i, text in enumerate(cells):
            lbl = QtWidgets.QLabel(text)
            lbl.setFixedWidth(widths[i])
            align = QtCore.Qt.AlignRight if i >= 3 else QtCore.Qt.AlignLeft
            lbl.setAlignment(align)
            lbl.setStyleSheet(
                f"color:{colors[i]};font-size:12px;"
                f"font-family:'Consolas','Monaco',monospace;"
                f"border:none;padding:0 2px;"
            )
            row_h.addWidget(lbl)

        # 迷你柱状图
        bar = _BarWidget([
            (c_hit, "#4ec9b0"),
            (c_miss, CursorTheme.ACCENT_ORANGE),
            (max(inp - c_hit - c_miss, 0), CursorTheme.ACCENT_PURPLE),
            (out, CursorTheme.ACCENT_GREEN),
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
