# -*- coding: utf-8 -*-
"""
Session Manager — 多会话管理和缓存

从 ai_tab.py 中拆分出的 Mixin，负责：
- 多会话创建/切换/关闭
- 会话标签栏
- 会话状态保存/恢复
"""

import uuid
from houdini_agent.qt_compat import QtWidgets, QtCore

from ..ui.cursor_widgets import (
    CursorTheme,
    TodoList,
)


class SessionManagerMixin:
    """多会话管理"""

    def _build_session_tabs(self) -> QtWidgets.QWidget:
        """会话标签栏 - 支持多个对话窗口"""
        container = QtWidgets.QFrame()
        container.setStyleSheet(f"""
            QFrame {{
                background-color: {CursorTheme.BG_SECONDARY};
                border-bottom: 1px solid {CursorTheme.BORDER};
            }}
        """)
        
        hl = QtWidgets.QHBoxLayout(container)
        hl.setContentsMargins(4, 2, 4, 2)
        hl.setSpacing(0)
        
        self.session_tabs = QtWidgets.QTabBar()
        self.session_tabs.setTabsClosable(False)
        self.session_tabs.setMovable(False)
        self.session_tabs.setExpanding(False)
        self.session_tabs.setDrawBase(False)
        self.session_tabs.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.session_tabs.customContextMenuRequested.connect(self._on_tab_context_menu)
        self.session_tabs.setStyleSheet(f"""
            QTabBar {{
                background: transparent;
            }}
            QTabBar::tab {{
                background: {CursorTheme.BG_TERTIARY};
                color: {CursorTheme.TEXT_MUTED};
                border: 1px solid {CursorTheme.BORDER};
                border-bottom: none;
                padding: 5px 12px;
                margin-right: 2px;
                font-size: 11px;
                font-family: {CursorTheme.FONT_BODY};
                min-width: 60px;
                max-width: 200px;
            }}
            QTabBar::tab:selected {{
                background: {CursorTheme.BG_PRIMARY};
                color: {CursorTheme.TEXT_PRIMARY};
                border-bottom: 2px solid {CursorTheme.ACCENT_BEIGE};
            }}
            QTabBar::tab:hover:!selected {{
                background: {CursorTheme.BG_HOVER};
                color: {CursorTheme.TEXT_SECONDARY};
            }}
        """)
        hl.addWidget(self.session_tabs, 1)
        
        # "+" 新建对话按钮
        self.btn_new_session = QtWidgets.QPushButton("+")
        self.btn_new_session.setFixedSize(22, 22)
        self.btn_new_session.setToolTip("新建对话")
        self.btn_new_session.setStyleSheet(f"""
            QPushButton {{
                background: {CursorTheme.BG_TERTIARY};
                color: {CursorTheme.TEXT_SECONDARY};
                border: 1px solid {CursorTheme.BORDER};
                border-radius: 3px;
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: {CursorTheme.BG_HOVER};
                color: {CursorTheme.TEXT_PRIMARY};
            }}
        """)
        hl.addWidget(self.btn_new_session)
        
        return container
    
    def _on_tab_context_menu(self, pos):
        """Tab 栏右键菜单：关闭 / 关闭其他"""
        tab_index = self.session_tabs.tabAt(pos)
        if tab_index < 0:
            return
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background: {CursorTheme.BG_SECONDARY};
                color: {CursorTheme.TEXT_PRIMARY};
                border: 1px solid {CursorTheme.BORDER};
                font-size: 12px;
                font-family: {CursorTheme.FONT_BODY};
                padding: 4px 0px;
            }}
            QMenu::item {{
                padding: 4px 20px;
            }}
            QMenu::item:selected {{
                background: {CursorTheme.BG_HOVER};
            }}
        """)
        close_action = menu.addAction("关闭此对话")
        close_others = menu.addAction("关闭其他对话")
        if self.session_tabs.count() <= 1:
            close_others.setEnabled(False)

        chosen = menu.exec_(self.session_tabs.mapToGlobal(pos))
        if chosen == close_action:
            self._close_session_tab(tab_index)
        elif chosen == close_others:
            # 从后往前关闭，跳过当前 tab
            for i in range(self.session_tabs.count() - 1, -1, -1):
                if i != tab_index:
                    self._close_session_tab(i)
    
    def _create_session_widgets(self) -> tuple:
        """创建单个会话的 scroll_area / chat_container / chat_layout"""
        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOn)
        scroll_area.setStyleSheet(f"QScrollArea {{ border: none; }}")
        
        chat_container = QtWidgets.QWidget()
        chat_layout = QtWidgets.QVBoxLayout(chat_container)
        chat_layout.setContentsMargins(4, 8, 4, 8)
        chat_layout.setSpacing(0)
        chat_layout.addStretch()
        
        chat_container.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Minimum
        )
        scroll_area.setWidget(chat_container)
        scroll_area.setWidgetResizable(True)
        
        return scroll_area, chat_container, chat_layout
    
    def _create_initial_session(self):
        """创建第一个（默认）会话"""
        self._session_counter = 1
        session_id = self._session_id  # __init__ 已生成
        
        scroll_area, chat_container, chat_layout = self._create_session_widgets()
        self.session_stack.addWidget(scroll_area)
        
        tab_index = self.session_tabs.addTab("Chat 1")
        self.session_tabs.setTabData(tab_index, session_id)
        
        # 设置当前引用
        self.scroll_area = scroll_area
        self.chat_container = chat_container
        self.chat_layout = chat_layout
        
        # 每个会话独立的 TodoList
        todo = self._create_todo_list(chat_container)
        self.todo_list = todo
        
        # 存入 sessions 字典
        self._sessions[session_id] = {
            'scroll_area': scroll_area,
            'chat_container': chat_container,
            'chat_layout': chat_layout,
            'todo_list': todo,
            'conversation_history': self._conversation_history,
            'context_summary': self._context_summary,
            'current_response': self._current_response,
            'token_stats': self._token_stats,
        }
    
    def _create_todo_list(self, parent=None) -> TodoList:
        """为会话创建 TodoList 控件（初始隐藏，首次使用时插入 chat_layout）"""
        return TodoList(parent)
    
    def _ensure_todo_in_chat(self, todo=None, layout=None):
        """确保 todo_list 已在 chat_layout 中（跟随对话流）
        
        Args:
            todo: 要插入的 TodoList，默认使用 self.todo_list
            layout: 目标 chat_layout，默认使用 self.chat_layout
        """
        todo = todo or self.todo_list
        layout = layout or self.chat_layout
        if not todo or not layout:
            return
        # 如果已在 layout 中，不要重复插入
        for i in range(layout.count()):
            if layout.itemAt(i).widget() is todo:
                return
        # 插入到当前最末的消息之后（stretch 之前）
        idx = layout.count() - 1  # -1 跳过末尾 stretch
        layout.insertWidget(idx, todo)
    
    def _new_session(self):
        """新建对话会话"""
        # 保存当前会话状态（如果当前 session 正在被 agent 写入则跳过，避免覆盖）
        if self._agent_session_id != self._session_id:
            self._save_current_session_state()
        
        # 自动保存旧会话缓存
        if self._auto_save_cache and self._conversation_history:
            self._save_cache()
        
        # 创建新会话
        self._session_counter += 1
        new_id = str(uuid.uuid4())[:8]
        label = f"Chat {self._session_counter}"
        
        scroll_area, chat_container, chat_layout = self._create_session_widgets()
        self.session_stack.addWidget(scroll_area)
        
        tab_index = self.session_tabs.addTab(label)
        self.session_tabs.setTabData(tab_index, new_id)
        
        # 初始化新会话状态
        new_token_stats = {
            'input_tokens': 0, 'output_tokens': 0,
            'cache_read': 0, 'cache_write': 0,
            'total_tokens': 0, 'requests': 0,
        }
        
        todo = self._create_todo_list(chat_container)
        
        self._sessions[new_id] = {
            'scroll_area': scroll_area,
            'chat_container': chat_container,
            'chat_layout': chat_layout,
            'todo_list': todo,
            'conversation_history': [],
            'context_summary': '',
            'current_response': None,
            'token_stats': new_token_stats,
        }
        
        # 切换到新会话
        self._session_id = new_id
        self._conversation_history = []
        self._context_summary = ''
        self._current_response = None
        self._token_stats = new_token_stats
        self._pending_ops.clear()
        self._update_batch_bar()
        self.scroll_area = scroll_area
        self.chat_container = chat_container
        self.chat_layout = chat_layout
        self.todo_list = todo
        
        # 切换 UI
        self.session_tabs.blockSignals(True)
        self.session_tabs.setCurrentIndex(tab_index)
        self.session_tabs.blockSignals(False)
        self.session_stack.setCurrentWidget(scroll_area)
        
        self._update_context_stats()
    
    def _switch_session(self, tab_index: int):
        """切换到指定标签页的会话（运行中也允许切换）"""
        new_session_id = self.session_tabs.tabData(tab_index)
        if not new_session_id or new_session_id == self._session_id:
            return
        
        # 保存当前会话（如果当前不是 agent 正在写入的 session，正常保存）
        if self._agent_session_id != self._session_id:
            self._save_current_session_state()
        
        # 加载目标会话
        self._load_session_state(new_session_id)
        
        # 切换显示
        sdata = self._sessions[new_session_id]
        self.session_stack.setCurrentWidget(sdata['scroll_area'])
        
        # 更新按钮状态（取决于目标 session 是否就是正在运行的 session）
        self._update_run_buttons()
        self._update_context_stats()
    
    def _close_session_tab(self, tab_index: int):
        """关闭指定标签页"""
        sid = self.session_tabs.tabData(tab_index)
        # 禁止关闭正在运行的 session
        if sid and self._agent_session_id == sid:
            return
        
        session_id = self.session_tabs.tabData(tab_index)
        if not session_id:
            return
        
        # 如果只剩一个标签，不关闭，只清空
        if self.session_tabs.count() <= 1:
            self._on_clear()
            return
        
        # 如果关闭的是当前活动会话，先切到相邻标签
        if session_id == self._session_id:
            new_index = tab_index - 1 if tab_index > 0 else tab_index + 1
            new_sid = self.session_tabs.tabData(new_index)
            if new_sid:
                self._load_session_state(new_sid)
                sdata = self._sessions[new_sid]
                self.session_stack.setCurrentWidget(sdata['scroll_area'])
        
        # 移除标签和会话数据
        self.session_tabs.removeTab(tab_index)
        sdata = self._sessions.pop(session_id, None)
        if sdata and sdata.get('scroll_area'):
            self.session_stack.removeWidget(sdata['scroll_area'])
            sdata['scroll_area'].deleteLater()
        
        self._update_context_stats()
    
    def _save_current_session_state(self):
        """将当前瞬态状态存入 _sessions 字典"""
        if self._session_id not in self._sessions:
            return
        s = self._sessions[self._session_id]
        s['conversation_history'] = self._conversation_history
        s['context_summary'] = self._context_summary
        s['current_response'] = self._current_response
        s['token_stats'] = self._token_stats
    
    def _load_session_state(self, session_id: str):
        """从 _sessions 恢复指定会话的状态"""
        sdata = self._sessions.get(session_id)
        if not sdata:
            return
        
        self._session_id = session_id
        self._conversation_history = sdata.get('conversation_history', [])
        self._context_summary = sdata.get('context_summary', '')
        self._current_response = sdata.get('current_response')
        self._token_stats = sdata.get('token_stats', {
            'input_tokens': 0, 'output_tokens': 0,
            'cache_read': 0, 'cache_write': 0,
            'total_tokens': 0, 'requests': 0,
        })
        self.scroll_area = sdata['scroll_area']
        self.chat_container = sdata['chat_container']
        self.chat_layout = sdata['chat_layout']
        self.todo_list = sdata.get('todo_list') or self._create_todo_list(self.chat_container)
    
    def _auto_rename_tab(self, text: str):
        """根据用户首条消息自动重命名当前标签"""
        for i in range(self.session_tabs.count()):
            if self.session_tabs.tabData(i) == self._session_id:
                current_label = self.session_tabs.tabText(i)
                if current_label.startswith("Chat "):
                    short = text[:18].replace('\n', ' ').strip()
                    if len(text) > 18:
                        short += "..."
                    self.session_tabs.setTabText(i, short)
                break
