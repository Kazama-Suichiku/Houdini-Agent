# -*- coding: utf-8 -*-
"""
Chat View — 对话显示和滚动逻辑

从 ai_tab.py 中拆分出的 Mixin，负责：
- 对话区域消息添加
- 滚动控制
- Toast 消息显示
"""

import base64

from houdini_agent.qt_compat import QtWidgets, QtCore, QtGui
from .cursor_widgets import (
    UserMessage,
    AIResponse,
    StatusLine,
    ClickableImageLabel,
)


class ChatViewMixin:
    """对话显示、滚动逻辑"""

    def _chat_end_index(self, layout=None) -> int:
        """Return the insertion index before the fixed end marker."""
        layout = layout or self.chat_layout
        count = layout.count()
        if count <= 0:
            return 0
        item = layout.itemAt(count - 1)
        widget = item.widget() if item else None
        if widget is not None and widget.property("chatEndMarker"):
            return count - 1
        return count

    def _insert_chat_widget(self, widget, layout=None, index: int = None):
        """Insert a widget into the chat stream before the end marker."""
        layout = layout or self.chat_layout
        if index is None:
            index = self._chat_end_index(layout)
        layout.insertWidget(index, widget)
        return widget

    def _clear_chat_widgets(self, layout=None):
        """Remove rendered chat widgets while keeping the end marker."""
        layout = layout or self.chat_layout
        i = 0
        while i < layout.count():
            item = layout.itemAt(i)
            widget = item.widget() if item else None
            if widget is not None and widget.property("chatEndMarker"):
                i += 1
                continue
            item = layout.takeAt(i)
            widget = item.widget() if item else None
            if widget:
                widget.deleteLater()

    def _image_tuple_from_b64(self, b64_data: str, media_type: str = 'image/png', thumb_size: int = 60):
        """Build the internal clickable-image tuple from base64 image data."""
        if not b64_data:
            return None
        try:
            raw = base64.b64decode(b64_data)
        except Exception:
            return None
        full_pixmap = QtGui.QPixmap()
        if not full_pixmap.loadFromData(raw) or full_pixmap.isNull():
            return None
        thumb = full_pixmap.scaled(
            thumb_size, thumb_size,
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )
        return (b64_data, media_type or 'image/png', thumb)

    def _image_tuple_from_data_url(self, url: str):
        """Parse data:image/...;base64,... into the internal clickable-image tuple."""
        if not isinstance(url, str) or not url.startswith('data:') or ';base64,' not in url:
            return None
        header, b64_data = url.split(';base64,', 1)
        media_type = header.replace('data:', '', 1) or 'image/png'
        return self._image_tuple_from_b64(b64_data, media_type)

    def _extract_multimodal_user_content(self, content: list):
        """Return (text, images) for OpenAI-style multimodal message content."""
        texts = []
        images = []
        for part in content or []:
            if not isinstance(part, dict):
                continue
            if part.get('type') == 'text':
                text = part.get('text', '')
                if text:
                    texts.append(text)
            elif part.get('type') == 'image_url':
                url = part.get('image_url', {}).get('url', '')
                image_tuple = self._image_tuple_from_data_url(url)
                if image_tuple:
                    images.append(image_tuple)
        return '\n'.join(texts).strip(), images

    def _add_user_message(self, text: str, images: list = None, history_range: tuple = None):
        """添加用户消息（可含图片缩略图，点击可放大）"""
        msg = UserMessage(text or "[Image]", self.chat_container)
        msg.deleteRequested.connect(self._delete_history_range)
        if history_range:
            msg.set_history_range(*history_range)
        # 如果有图片，在消息下方添加可点击的缩略图
        if images:
            image_widgets = []
            for b64_data, _mt, thumb in images:
                # 从 base64 还原完整 pixmap 用于放大预览
                full_pixmap = QtGui.QPixmap()
                full_pixmap.loadFromData(base64.b64decode(b64_data))
                if full_pixmap.isNull():
                    continue
                thumb_scaled = thumb.scaled(48, 48, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
                lbl = ClickableImageLabel(thumb_scaled, full_pixmap)
                lbl.setObjectName("imgThumb")
                image_widgets.append(lbl)
            msg.add_image_widgets(image_widgets)
        self._insert_chat_widget(msg)
        self._scroll_to_bottom()
        return msg

    def _add_ai_response(self, history_range: tuple = None) -> AIResponse:
        """添加 AI 回复块"""
        response = AIResponse(self.chat_container)
        response.createWrangleRequested.connect(self._on_create_wrangle)
        response.nodePathClicked.connect(self._navigate_to_node)
        response.deleteRequested.connect(self._delete_history_range)
        if history_range:
            response.set_history_range(*history_range)
        self._insert_chat_widget(response)
        self._current_response = response
        self._scroll_to_bottom(force=True)
        return response

    def _delete_history_range(self, start: int, end: int):
        """Delete one rendered history item/group and refresh the current session."""
        if getattr(self, '_agent_session_id', None) is not None:
            self._show_toast("Task is running. Delete records after it finishes.", 2500)
            return
        history = getattr(self, '_conversation_history', [])
        if start < 0 or end <= start or start >= len(history):
            return
        end = min(end, len(history))
        reply = QtWidgets.QMessageBox.question(
            self,
            "Delete record",
            "Delete this conversation record?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return
        del history[start:end]
        self._conversation_history = history
        try:
            self._save_current_session_state()
        except Exception:
            pass
        try:
            self._update_context_stats()
        except Exception:
            pass
        try:
            if getattr(self, '_auto_save_cache', False):
                if history:
                    self._save_cache()
                else:
                    cache_dir = getattr(self, '_cache_dir', None)
                    session_id = getattr(self, '_session_id', '')
                    if cache_dir and session_id:
                        session_file = cache_dir / f"session_{session_id}.json"
                        if session_file.exists():
                            session_file.unlink()
                    self._update_manifest()
        except Exception:
            pass
        if hasattr(self, '_rerender_history'):
            self._rerender_history()
        self._show_toast("Record deleted", 1800)

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
        # Insert into the chat stream instead of appending after the end anchor.
        self._insert_chat_widget(toast)
        self._scroll_to_bottom(force=True)
        def _remove():
            try:
                self.chat_layout.removeWidget(toast)
                toast.setParent(None)
                toast.deleteLater()
            except RuntimeError:
                pass
        QtCore.QTimer.singleShot(duration_ms, _remove)
