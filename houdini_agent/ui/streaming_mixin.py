# -*- coding: utf-8 -*-
"""
Streaming Mixin — 流式内容处理与思考区块管理

从 ai_tab.py 拆分：
  - _on_append_content / _on_content_with_limit
  - _partial_tag_at_end / _drain_tag_buffer
  - _finalize_thinking / _resume_thinking / _finalize_thinking_main_thread / _resume_thinking_main_thread
  - _emit_normal_content / _check_output_token_limit
  - _on_thinking_chunk / _on_add_thinking / _on_add_status / _on_update_thinking
"""

import time

from houdini_agent.qt_compat import QtCore

from .i18n import tr


class StreamingMixin:
    """流式输出 + <think> 标签解析 + 思考面板管理"""

    _RETRY_NOTICE_KEYWORDS = (
        '服务端暂时不可用',
        '上下文超限',
        '连续出错',
    )

    def _split_retry_notices(self, text: str) -> tuple:
        if not text:
            return "", []
        normal_parts = []
        notices = []
        for line in text.splitlines(keepends=True):
            stripped = line.strip()
            is_notice = (
                stripped.startswith('[')
                and stripped.endswith(']')
                and any(keyword in stripped for keyword in self._RETRY_NOTICE_KEYWORDS)
            )
            if is_notice:
                notices.append(stripped)
            else:
                normal_parts.append(line)
        return ''.join(normal_parts), notices

    def _append_retry_notices(self, resp, notices: list):
        if not notices:
            return
        lines = getattr(resp, '_retry_notice_lines', None)
        if lines is None:
            lines = []
            setattr(resp, '_retry_notice_lines', lines)
        lines.extend(notices)

        section = getattr(resp, '_retry_notice_section', None)
        label = getattr(resp, '_retry_notice_label', None)
        try:
            if section is None:
                section = resp.add_collapsible(tr('retry.log_title', len(lines)), '')
                setattr(resp, '_retry_notice_section', section)
                item = section.content_layout.itemAt(0)
                label = item.widget() if item else None
                setattr(resp, '_retry_notice_label', label)
            section.set_title(tr('retry.log_title', len(lines)))
            if label is not None:
                label.setText('\n'.join(lines))
        except RuntimeError:
            pass

    @staticmethod
    def _is_internal_viewport_message(msg: dict) -> bool:
        """Return True for model-only viewport image prompts that should not render as chat."""
        if msg.get('role') != 'user':
            return False
        content = msg.get('content')
        if not isinstance(content, list):
            return False
        text = '\n'.join(
            part.get('text', '') for part in content
            if isinstance(part, dict) and part.get('type') == 'text'
        )
        return (
            '[viewport snapshot attached' in text
            or '[auto visual checkpoint attached' in text
        )

    @staticmethod
    def _visible_viewport_message(msg: dict) -> dict:
        """Convert an internal viewport-analysis prompt into a visible chat snapshot."""
        if msg.get('role') != 'user':
            return msg
        content = msg.get('content')
        if not isinstance(content, list):
            return msg

        text = '\n'.join(
            part.get('text', '') for part in content
            if isinstance(part, dict) and part.get('type') == 'text'
        )
        if '[auto visual checkpoint attached' in text:
            label = '[Auto viewport verification]'
        elif '[viewport snapshot attached' in text:
            label = '[Viewport snapshot]'
        else:
            return msg

        visible_parts = [{"type": "text", "text": label}]
        for part in content:
            if isinstance(part, dict) and part.get('type') == 'image_url':
                visible_parts.append(part)
        return {'role': 'user', 'content': visible_parts}

    def _on_append_content(self, text: str):
        """处理内容追加（主线程槽函数）

        注意：内容已经在 _on_content_with_limit → _drain_tag_buffer →
        _emit_normal_content 中经过了 <think> 标签过滤和伪造检测。
        这里只负责将文本交给 UI 控件显示，不做额外过滤。
        """
        resp = self._agent_response or self._current_response
        if not text or not resp:
            return
        text, retry_notices = self._split_retry_notices(text)
        # ★ 修复：不丢弃包含换行符的 chunk
        # 纯换行符（\n\n）是 Markdown 段落分隔的关键信号，
        # 丢弃它们会导致多段内容粘连在一起
        if retry_notices and not text.strip():
            self._append_retry_notices(resp, retry_notices)
            return
        if not text.strip() and '\n' not in text:
            self._append_retry_notices(resp, retry_notices)
            return
        try:
            self._append_retry_notices(resp, retry_notices)
            # ★ 内容开始流入 → 隐藏 "Generating..." 状态（如果正在显示）
            if hasattr(self, 'thinking_bar') and getattr(self.thinking_bar, '_mode', None) == 'generating':
                self.thinking_bar.stop()
            if (
                getattr(resp, '_has_thinking', False)
                and getattr(resp, 'thinking_section', None) is not None
                and not resp.thinking_section._finalized
            ):
                resp.thinking_section.finalize()
            resp.append_content(text)
            self._scroll_agent_to_bottom(force=False)
        except RuntimeError:
            pass  # widget 已被 clear 销毁

    def _on_content_with_limit(self, text: str):
        """处理内容追加，解析 <think> 标签，分离思考和正式内容"""
        if not text:
            return

        # 初始化输出缓冲
        if not hasattr(self, '_output_buffer'):
            self._output_buffer = ""
            self._last_flush_time = time.time()
            self._adaptive_buf_size = 80
            self._adaptive_interval = 0.15
            self._last_render_duration = 0.0
            self._flush_count = 0
            self._is_first_content_chunk = True

        # 追加到标签解析缓冲区
        self._tag_parse_buf += text
        self._drain_tag_buffer()

    # ------------------------------------------------------------------
    # <think> 标签流式解析
    # ------------------------------------------------------------------

    @staticmethod
    def _partial_tag_at_end(text: str, tag: str) -> int:
        """检测 text 末尾是否有 tag 的不完整前缀，返回匹配长度 (0 = 无)"""
        for i in range(min(len(tag) - 1, len(text)), 0, -1):
            if tag[:i] == text[-i:]:
                return i
        return 0

    def _drain_tag_buffer(self):
        """处理 _tag_parse_buf，将内容分发到正式输出或思考面板"""
        buf = self._tag_parse_buf
        while buf:
            if not self._in_think_block:
                # ── 正常模式：寻找 <think> ──
                pos = buf.find('<think>')
                if pos >= 0:
                    if pos > 0:
                        self._emit_normal_content(buf[:pos])
                    buf = buf[pos + 7:]          # 跳过 <think>
                    self._in_think_block = True
                    # ★ Think 开关打开时才显示思考面板；关闭时静默丢弃 <think> 内容
                    if self._think_enabled:
                        self._thinking_needs_finalize = True  # 进入思考，标记需要 finalize
                        # 如果思考已 finalize，恢复为活跃状态并重启计时
                        self._resume_thinking()
                    continue
                # 检查末尾是否有不完整的 <think>
                hold = self._partial_tag_at_end(buf, '<think>')
                if hold:
                    self._emit_normal_content(buf[:-hold])
                    self._tag_parse_buf = buf[-hold:]
                    return
                # 全部是正常内容
                self._emit_normal_content(buf)
                self._tag_parse_buf = ""
                return
            else:
                # ── 思考模式：寻找 </think> ──
                pos = buf.find('</think>')
                if pos >= 0:
                    if self._think_enabled and pos > 0:
                        self._addThinking.emit(buf[:pos])
                    buf = buf[pos + 8:]          # 跳过 </think>
                    self._in_think_block = False
                    # 思考结束：立即 finalize 思考区块并停止计时器
                    if self._think_enabled:
                        self._finalize_thinking()
                    continue
                # 检查末尾是否有不完整的 </think>
                hold = self._partial_tag_at_end(buf, '</think>')
                if hold:
                    if self._think_enabled:
                        safe = buf[:-hold]
                        if safe:
                            self._addThinking.emit(safe)
                    self._tag_parse_buf = buf[-hold:]
                    return
                # 全部是思考内容
                if self._think_enabled:
                    self._addThinking.emit(buf)
                # ★ Think 关闭时：静默丢弃 <think> 块内的内容
                self._tag_parse_buf = ""
                return
        self._tag_parse_buf = ""

    def _finalize_thinking(self):
        """思考阶段结束（线程安全：自动分派到主线程）"""
        self._finalizeThinkingSignal.emit()

    def _resume_thinking(self):
        """新一轮 <think> 开始（线程安全：自动分派到主线程）"""
        self._resumeThinkingSignal.emit()

    @QtCore.Slot()
    def _finalize_thinking_main_thread(self):
        """[主线程] 实际执行 finalize 思考区块并停止计时器"""
        try:
            resp = self._agent_response or self._current_response
            if resp and resp._has_thinking and getattr(resp, 'thinking_section', None) is not None:
                if not resp.thinking_section._finalized:
                    resp.thinking_section.finalize()
        except RuntimeError:
            pass  # widget 已被 clear 销毁
        if self._thinking_timer:
            self._thinking_timer.stop()
            self._thinking_timer = None
        # ★ 停止输入框上方的思考指示条
        try:
            self.thinking_bar.stop()
        except (RuntimeError, AttributeError):
            pass

    @QtCore.Slot()
    def _resume_thinking_main_thread(self):
        """[主线程] 实际执行恢复思考区块并重启计时器"""
        if not getattr(self, '_is_running', False):
            return  # Agent 已停止，忽略延迟到达的信号
        try:
            resp = self._agent_response or self._current_response
            if resp:
                resp.start_thinking_round()
        except RuntimeError:
            pass  # widget 已被 clear 销毁
        # 重启计时器（如果已停止）
        if not self._thinking_timer:
            self._thinking_timer = QtCore.QTimer(self)
            self._thinking_timer.timeout.connect(lambda: self._updateThinkingTime.emit())
            self._thinking_timer.start(1000)
        # ★ 重新启动输入框上方的思考指示条
        try:
            self.thinking_bar.start()
        except (RuntimeError, AttributeError):
            pass

    def _emit_normal_content(self, text: str):
        """发送正式内容（带 token 限制 + 自适应缓冲刷新）

        ★ 自适应策略（借鉴 markstream-vue 的时间预算机制）：
        - 首个 chunk 立即刷新，消除首字延迟
        - 后续根据上一次渲染耗时动态调整缓冲大小：
          渲染快 → 小缓冲、多刷新（流畅感）
          渲染慢 → 大缓冲、少刷新（避免卡顿）
        - 换行始终立即刷新（段落边界及时显示）
        """
        if not text:
            return
        # 首次正式内容到达时，确保思考区块已 finalize（适配 DeepSeek 原生 reasoning_content）
        # 使用标志位避免从后台线程访问 Qt 控件属性
        if self._in_think_block is False and getattr(self, '_thinking_needs_finalize', True):
            self._finalize_thinking()  # 通过信号分派到主线程
            self._thinking_needs_finalize = False

        # Token 限制仅对正式内容计数
        if not self._check_output_token_limit(text):
            if self._output_buffer:
                self._appendContent.emit(self._output_buffer)
                self._output_buffer = ""
            self._appendContent.emit(tr('ai.token_limit'))
            self._addStatus.emit(tr('ai.token_limit_status'))
            self.client.request_stop()
            return

        self._output_buffer += text

        # ★ 自适应缓冲刷新策略
        should_flush = False
        current_time = time.time()

        # 初始化自适应状态（首次调用）
        if not hasattr(self, '_adaptive_buf_size'):
            self._adaptive_buf_size = 80       # 初始缓冲大小（字符）
            self._adaptive_interval = 0.15     # 初始兜底间隔（秒）
            self._last_render_duration = 0.0   # 上次渲染耗时
            self._flush_count = 0              # flush 计数（性能追踪）
            self._is_first_content_chunk = True  # 首个 chunk 标志

        # 规则 1: 首个 chunk 立即刷新（消除首字延迟）
        if self._is_first_content_chunk:
            should_flush = True
            self._is_first_content_chunk = False
        # 规则 2: 缓冲区达到自适应阈值
        elif len(self._output_buffer) >= self._adaptive_buf_size:
            should_flush = True
        # 规则 3: 换行时立即刷新（段落边界及时显示）
        elif '\n' in text:
            should_flush = True
        # 规则 4: 自适应兜底间隔
        elif current_time - self._last_flush_time > self._adaptive_interval:
            should_flush = True

        if should_flush and self._output_buffer:
            flush_start = time.time()

            # 实时过滤伪造的工具调用行
            buf = self._output_buffer
            if '[ok]' in buf or '[err]' in buf or '[工具执行结果]' in buf or '[Tool Result]' in buf:
                lines = buf.split('\n')
                filtered = []
                has_fake = False
                for ln in lines:
                    s = ln.strip()
                    if s == '[工具执行结果]' or s == '[Tool Result]' or self._FAKE_TOOL_PATTERNS.match(s):
                        has_fake = True
                        continue
                    filtered.append(ln)
                buf = '\n'.join(filtered)
                if has_fake and not getattr(self, '_fake_warned', False):
                    self._addStatus.emit(tr('ai.fake_tool'))
                    self._fake_warned = True
            if buf.strip():
                self._appendContent.emit(buf)
            self._output_buffer = ""
            self._last_flush_time = current_time
            self._flush_count += 1

            # ★ 自适应调整：根据上次渲染耗时动态调整缓冲参数
            render_dur = time.time() - flush_start
            self._last_render_duration = render_dur
            if render_dur < 0.004:
                # 渲染很快 → 减小缓冲，更频繁刷新（流畅感）
                self._adaptive_buf_size = max(40, self._adaptive_buf_size - 20)
                self._adaptive_interval = max(0.08, self._adaptive_interval - 0.02)
            elif render_dur > 0.012:
                # 渲染较慢 → 增大缓冲，减少刷新（避免卡顿）
                self._adaptive_buf_size = min(500, self._adaptive_buf_size + 40)
                self._adaptive_interval = min(0.40, self._adaptive_interval + 0.05)

    def _check_output_token_limit(self, text: str) -> bool:
        """检查正式输出 token 是否超过限制（思考内容不计入）"""
        if not text:
            return True
        new_tokens = self.token_optimizer.estimate_tokens(text)
        self._current_output_tokens += new_tokens
        if self._current_output_tokens >= self._max_output_tokens:
            return False
        if (self._current_output_tokens >= self._output_token_warning
                and self._current_output_tokens < self._max_output_tokens):
            remaining = self._max_output_tokens - self._current_output_tokens
            if remaining < 400:
                self._addStatus.emit(
                    tr('ai.approaching_limit', self._current_output_tokens, self._max_output_tokens))
        return True

    def _on_thinking_chunk(self, text: str):
        """处理原生 reasoning_content（DeepSeek R1 等模型）

        ★ 受 Think 开关控制：关闭时静默丢弃
        """
        if text and self._think_enabled:
            self._addThinking.emit(text)

    @QtCore.Slot(str)
    def _on_add_thinking(self, text: str):
        """在主线程更新思考内容（槽函数）"""
        if not getattr(self, '_is_running', False):
            return  # Agent 已停止，忽略延迟到达的信号
        try:
            resp = self._agent_response or self._current_response
            if resp:
                resp.add_thinking(text)
                # ★ 首次思考内容 → 启动输入框上方思考指示条
                if hasattr(self, 'thinking_bar') and not self.thinking_bar.isVisible():
                    self.thinking_bar.start()
            self._scroll_agent_to_bottom(force=False)
        except RuntimeError:
            pass  # widget 已被 clear 销毁

    def _on_add_status(self, text: str):
        try:
            resp = self._agent_response or self._current_response
            if resp:
                resp.add_status(text)
                self._scroll_agent_to_bottom(force=False)
        except RuntimeError:
            pass  # widget 已被 clear 销毁

    def _on_update_thinking(self):
        try:
            resp = self._agent_response or self._current_response
            if resp:
                resp.update_thinking_time()
                # ★ 同步更新输入框上方思考指示条的时间
                if hasattr(self, 'thinking_bar') and self.thinking_bar.isVisible():
                    if resp._has_thinking and getattr(resp, 'thinking_section', None) is not None:
                        self.thinking_bar.set_elapsed(resp.thinking_section._total_elapsed())
        except RuntimeError:
            pass  # 控件可能已销毁
