# -*- coding: utf-8 -*-
"""
Chat View — 对话显示和滚动逻辑

从 ai_tab.py 中拆分出的 Mixin，负责：
- 对话区域消息添加
- 滚动控制
- Toast 消息显示
"""

from houdini_agent.qt_compat import QtWidgets, QtCore, QtGui
from .cursor_widgets import (
    UserMessage,
    AIResponse,
    StatusLine,
    ClickableImageLabel,
)


class ChatViewMixin:
    """对话显示、滚动逻辑"""

    def _add_user_message(self, text: str, images: list = None):
        """添加用户消息（可含图片缩略图，点击可放大）"""
        msg = UserMessage(text, self.chat_container)
        # 如果有图片，在消息下方添加可点击的缩略图
        if images:
            img_row = QtWidgets.QHBoxLayout()
            img_row.setSpacing(4)
            img_row.setContentsMargins(12, 0, 12, 4)
            for b64_data, _mt, thumb in images:
                # 从 base64 还原完整 pixmap 用于放大预览
                full_pixmap = QtGui.QPixmap()
                full_pixmap.loadFromData(__import__('base64').b64decode(b64_data))
                thumb_scaled = thumb.scaled(48, 48, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
                lbl = ClickableImageLabel(thumb_scaled, full_pixmap)
                lbl.setObjectName("imgThumb")
                img_row.addWidget(lbl)
            img_row.addStretch()
            msg.layout().addLayout(img_row)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, msg)
        self._scroll_to_bottom()

    def _add_ai_response(self) -> AIResponse:
        """添加 AI 回复块"""
        response = AIResponse(self.chat_container)
        response.createWrangleRequested.connect(self._on_create_wrangle)
        response.nodePathClicked.connect(self._navigate_to_node)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, response)
        self._current_response = response
        self._scroll_to_bottom(force=True)
        return response

    def _is_user_scrolled_up(self) -> bool:
        """检查用户是否在查看历史（滚动条不在底部）"""
        scrollbar = self.scroll_area.verticalScrollBar()
        # 如果滚动条位置距离底部超过 100 像素，认为用户在查看历史
        return scrollbar.maximum() - scrollbar.value() > 100

    def _scroll_to_bottom(self, force: bool = False):
        """滚动到底部，但尊重用户的查看位置（带节流防止事件循环过载）
        
        Args:
            force: 强制滚动（用于新消息）
        """
        if force or not self._is_user_scrolled_up():
            # 节流：如果已有待执行的滚动定时器，跳过本次
            if not hasattr(self, '_scroll_timer'):
                self._scroll_timer = QtCore.QTimer(self)
                self._scroll_timer.setSingleShot(True)
                self._scroll_timer.setInterval(60)
                self._scroll_timer.timeout.connect(self._do_scroll)
            if not self._scroll_timer.isActive():
                self._scroll_timer.start()
    
    def _do_scroll(self):
        """实际执行滚动"""
        try:
            sb = self.scroll_area.verticalScrollBar()
            sb.setValue(sb.maximum())
        except RuntimeError:
            pass  # 控件可能已销毁
    
    def _scroll_agent_to_bottom(self, force: bool = False):
        """滚动 agent 所在的 session（如果正在显示则滚动，否则跳过）"""
        # 只有当前显示的 session 就是 agent session 时才滚动
        if self._agent_session_id and self._agent_session_id != self._session_id:
            return  # agent 在后台 session 跑，不要干扰用户正在看的 session
        self._scroll_to_bottom(force=force)
    
    def _show_toast(self, text: str, duration_ms: int = 3000):
        """在聊天区域底部显示临时提示，自动消失"""
        toast = StatusLine(text)
        # ★ 必须用 insertWidget 插到 stretch 之前，
        #   否则 addWidget 会放到 stretch 之后，导致后续消息也在 stretch 后面产生空白间隙
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, toast)
        self._scroll_to_bottom(force=True)
        def _remove():
            try:
                self.chat_layout.removeWidget(toast)
                toast.setParent(None)
                toast.deleteLater()
            except RuntimeError:
                pass
        QtCore.QTimer.singleShot(duration_ms, _remove)
