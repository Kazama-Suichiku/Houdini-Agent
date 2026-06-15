# -*- coding: utf-8 -*-
"""
History Mixin — 对话历史渲染

从 ai_tab.py 拆分：
  - _compress_to_summary
  - _CONTEXT_HEADERS / _BATCH_INITIAL / _BATCH_SIZE / _BATCH_BUDGET_MS
  - _render_conversation_history / _group_messages_into_turns
  - _render_message_groups / _render_single_group / _render_next_batch / _finish_batch_render
  - _replay_todo_from_tool_call
  - _render_native_tool_turn / _find_tool_name_by_id
  - _render_user_history / _TOOL_LINE_PREFIXES / _render_tool_summary_history
  - _restore_shell_widgets / _render_old_tool_msgs
"""

import json
import re
import time

from houdini_agent.qt_compat import QtWidgets, QtCore

from .cursor_widgets import PythonShellWidget, SystemShellWidget
from .theme_engine import ThemeEngine


class HistoryMixin:
    """对话历史的渲染、分批加载与旧格式兼容"""

    def _compress_to_summary(self):
        """将旧对话压缩为摘要，减少 token 消耗"""
        if len(self._conversation_history) <= 4:
            QtWidgets.QMessageBox.information(self, "提示", "对话历史太短，无需压缩")
            return

        # 确认操作
        reply = QtWidgets.QMessageBox.question(
            self, "确认压缩",
            f"将把前 {len(self._conversation_history) - 4} 条对话压缩为摘要，"
            f"保留最近 4 条完整对话。\n\n"
            f"这样可以大幅减少 token 消耗。是否继续？",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )

        if reply != QtWidgets.QMessageBox.Yes:
            return

        # 执行压缩
        old_messages = self._conversation_history[:-4]
        recent_messages = self._conversation_history[-4:]

        # 生成详细摘要
        summary_parts = ["[历史对话摘要 - 已压缩以节省 token]"]

        user_requests = []
        ai_results = []

        for msg in old_messages:
            role = msg.get('role', '')
            content = msg.get('content', '')

            if role == 'user':
                # 提取用户请求的核心（前200字符）
                user_request = content[:200].replace('\n', ' ')
                if len(content) > 200:
                    user_request += "..."
                user_requests.append(user_request)

            elif role == 'assistant' and content:
                # 提取 AI 回复的关键信息
                lines = [l.strip() for l in content.split('\n') if l.strip()]
                if lines:
                    # 取最后一行或前150字符
                    result_summary = lines[-1][:150].replace('\n', ' ')
                    if len(lines[-1]) > 150:
                        result_summary += "..."
                    ai_results.append(result_summary)

        # 合并摘要
        if user_requests:
            summary_parts.append(f"\n用户请求 ({len(user_requests)} 条):")
            for i, req in enumerate(user_requests[:10], 1):  # 最多显示10条
                summary_parts.append(f"  {i}. {req}")
            if len(user_requests) > 10:
                summary_parts.append(f"  ... 还有 {len(user_requests) - 10} 条请求")

        if ai_results:
            summary_parts.append(f"\nAI 完成的任务 ({len(ai_results)} 条):")
            for i, res in enumerate(ai_results[:10], 1):  # 最多显示10条
                summary_parts.append(f"  {i}. {res}")
            if len(ai_results) > 10:
                summary_parts.append(f"  ... 还有 {len(ai_results) - 10} 条结果")

        summary_text = "\n".join(summary_parts)

        # 更新历史：用摘要替换旧对话
        self._conversation_history = [
            {'role': 'system', 'content': summary_text}
        ] + recent_messages

        # 更新上下文摘要
        self._context_summary = summary_text

        # 重新渲染
        self._render_conversation_history()

        # 更新统计
        self._update_context_stats()

        # 计算节省的 token
        old_tokens = sum(self._estimate_tokens(json.dumps(msg)) for msg in old_messages)
        new_tokens = self._estimate_tokens(summary_text)
        saved_tokens = old_tokens - new_tokens

        QtWidgets.QMessageBox.information(
            self, "压缩完成",
            f"对话已压缩！\n\n"
            f"原始: ~{old_tokens} tokens\n"
            f"压缩后: ~{new_tokens} tokens\n"
            f"节省: ~{saved_tokens} tokens ({saved_tokens/old_tokens*100:.1f}%)"
        )

    # ---------- 历史渲染辅助 ----------
    _CONTEXT_HEADERS = ('[Network structure]', '[Selected nodes]',
                        '[网络结构]', '[选中节点]')

    # ★ 分批渲染常量（借鉴 markstream-vue 的批次策略）
    _BATCH_INITIAL = 30      # 首批渲染最后 N 条消息（用户最近看到的）
    _BATCH_SIZE = 15          # 后续每批渲染 N 条
    _BATCH_BUDGET_MS = 8      # 每批时间预算（毫秒）

    def _render_conversation_history(self):
        """重新渲染对话历史到 UI

        ★ 分批渲染策略（借鉴 markstream-vue）：
        1. 首批渲染最后 _BATCH_INITIAL 条消息（用户最近看到的）
        2. 用 QTimer.singleShot(0) 模拟 idle callback，逐批渲染剩余
        3. 每批设时间预算，超出则暂停让出主线程

        处理三种数据格式：
        1. role="user" 中嵌入 [Network structure] / [Selected nodes] 等上下文
           → 用户文字正常显示，上下文数据放入可折叠区域
        2. role="assistant" 以 [工具执行结果] 开头
           → 解析每一条 [ok]/[err]/✅/❌ 行，创建折叠式 ToolCallItem
        3. role="tool"（旧缓存格式）
           → 先 add_tool_call 再 set_tool_result（折叠式）
        """
        # 清空当前显示（保留末尾聊天锚点）
        self._clear_chat_widgets()

        # 取消之前的分批渲染定时器
        if hasattr(self, '_batch_render_timer') and self._batch_render_timer is not None:
            self._batch_render_timer.stop()
            self._batch_render_timer = None

        messages = self._conversation_history
        if not messages:
            return

        # ★ 预扫描：将消息分组为逻辑"轮次"（每轮 = 一组相关消息）
        groups = self._group_messages_into_turns(messages)
        total_groups = len(groups)

        if total_groups <= self._BATCH_INITIAL:
            # 消息量小，一次性渲染
            self._render_message_groups(groups, 0, total_groups)
        else:
            # ★ 分批渲染：先渲染最后 _BATCH_INITIAL 组（用户最近看到的）
            # 早期消息用占位符
            early_count = total_groups - self._BATCH_INITIAL

            # 插入占位符
            self._batch_placeholder = QtWidgets.QLabel(
                f"⏳ 加载历史消息 ({early_count} 轮)..."
            )
            self._batch_placeholder.setObjectName("batchPlaceholder")
            self._batch_placeholder.setStyleSheet(
                f"color: #64748b; padding: 8px 12px; font-size: {ThemeEngine.scaled_px(12)}px; "
                "font-style: italic; background: transparent;"
            )
            self._batch_placeholder.setAlignment(QtCore.Qt.AlignCenter)
            # 插入到聊天锚点之前
            self._insert_chat_widget(self._batch_placeholder)

            # 渲染最后 _BATCH_INITIAL 组
            self._render_message_groups(groups, early_count, total_groups)

            # 用 QTimer 分批渲染早期消息
            self._batch_groups = groups
            self._batch_cursor = early_count  # 从 early_count 向 0 回退
            self._batch_insert_pos = 0  # 早期消息插入到布局头部
            self._batch_render_timer = QtCore.QTimer(self)
            self._batch_render_timer.setSingleShot(True)
            self._batch_render_timer.timeout.connect(self._render_next_batch)
            self._batch_render_timer.start(0)  # 下一帧开始

    def _group_messages_into_turns(self, messages: list) -> list:
        """将消息列表分组为逻辑轮次

        返回: list of (start_idx, end_idx) 元组
        """
        groups: list = []
        i = 0
        while i < len(messages):
            msg = messages[i]
            role = msg.get('role', '')

            if role == 'user':
                groups.append((i, i + 1))
                i += 1
            elif role == 'assistant':
                if msg.get('tool_calls'):
                    # 收集工具交互轮次
                    j = i + 1
                    while j < len(messages):
                        m = messages[j]
                        r = m.get('role', '')
                        if r == 'tool':
                            j += 1
                        elif r == 'assistant':
                            j += 1
                            if not m.get('tool_calls'):
                                break
                        else:
                            break
                    groups.append((i, j))
                    i = j
                else:
                    # 普通 assistant + 后续 tool 消息
                    j = i + 1
                    while j < len(messages) and messages[j].get('role') == 'tool':
                        j += 1
                    groups.append((i, j))
                    i = j
            elif role == 'system':
                groups.append((i, i + 1))
                i += 1
            else:
                groups.append((i, i + 1))
                i += 1
        return groups

    def _render_message_groups(self, groups: list, start: int, end: int):
        """渲染 [start, end) 范围内的消息组"""
        messages = self._conversation_history
        for gi in range(start, end):
            si, ei = groups[gi]
            try:
                self._render_single_group(messages, si, ei)
            except Exception:
                import traceback
                traceback.print_exc()

    def _render_single_group(self, messages: list, si: int, ei: int):
        """渲染一个消息组"""
        msg = messages[si]
        role = msg.get('role', '')
        if self._is_internal_viewport_message(msg):
            msg = self._visible_viewport_message(msg)
            role = msg.get('role', '')
        raw_content = msg.get('content', '') or ''
        if isinstance(raw_content, list):
            content = '\n'.join(
                part.get('text', '') for part in raw_content
                if isinstance(part, dict) and part.get('type') == 'text'
            )
        else:
            content = raw_content

        if role == 'user' and isinstance(raw_content, list):
            history_text, history_images = self._extract_multimodal_user_content(raw_content)
            if history_images:
                self._add_user_message(history_text or "[Image]", images=history_images, history_range=(si, ei))
            else:
                self._render_user_history(history_text or content, history_range=(si, ei))

        elif role == 'user':
            self._render_user_history(content, history_range=(si, ei))

        elif role == 'assistant':
            if msg.get('tool_calls'):
                turn_msgs = messages[si:ei]
                self._render_native_tool_turn(turn_msgs, history_range=(si, ei))
            else:
                tool_msgs = [messages[j] for j in range(si + 1, ei)
                             if messages[j].get('role') == 'tool']

                if content.lstrip().startswith('[工具执行结果]'):
                    self._render_tool_summary_history(content, msg, history_range=(si, ei))
                else:
                    response = self._add_ai_response(history_range=(si, ei))
                    thinking = msg.get('thinking', '')
                    if thinking:
                        response.add_thinking(thinking)
                        if getattr(response, 'thinking_section', None) is not None:
                            response.thinking_section.finalize()
                    self._render_old_tool_msgs(response, tool_msgs)
                    self._restore_shell_widgets(response, msg)
                    response.set_content(content)
                    response.status_label.setText("历史")
                    response.finalize()
                    parts = []
                    if thinking:
                        parts.append("思考")
                    if tool_msgs:
                        parts.append(f"{len(tool_msgs)}次调用")
                    label = f"历史 | {', '.join(parts)}" if parts else "历史"
                    response.status_label.setText(label)

        elif role == 'system' and '[历史对话摘要' in content:
            response = self._add_ai_response(history_range=(si, ei))
            response.add_collapsible("历史对话摘要", content)
            response.status_label.setText("历史摘要")
            response.finalize()
            response.status_label.setText("历史摘要")

    def _render_next_batch(self):
        """分批渲染回调 — 渲染下一批早期消息（从后向前，插入到布局头部）"""
        if not hasattr(self, '_batch_groups') or not self._batch_groups:
            return
        if self._batch_cursor <= 0:
            # 全部渲染完毕，移除占位符
            self._finish_batch_render()
            return

        batch_start = max(0, self._batch_cursor - self._BATCH_SIZE)
        batch_end = self._batch_cursor
        start_time = time.time()

        # ★ 早期消息需要插入到占位符之前（即布局的第 0 个位置开始）
        # 我们从 batch_start 到 batch_end 按顺序渲染，每个 widget 插入到
        # 占位符位置之前（insert_pos 递增）
        messages = self._conversation_history
        insert_pos = self._batch_insert_pos  # 在此位置之前插入
        rendered_count = 0

        for gi in range(batch_start, batch_end):
            si, ei = self._batch_groups[gi]
            try:
                widgets_before = self.chat_layout.count()
                self._render_single_group(messages, si, ei)
                widgets_after = self.chat_layout.count()
                added = widgets_after - widgets_before

                # 将新添加的 widget 移动到正确位置（占位符之前）
                if added > 0:
                    for _ in range(added):
                        # 取出最后添加的 widget（在聊天锚点之前）
                        from_idx = self._chat_end_index() - 1
                        item = self.chat_layout.takeAt(from_idx)
                        if item and item.widget():
                            self.chat_layout.insertWidget(insert_pos, item.widget())
                            insert_pos += 1
                    rendered_count += added
            except Exception:
                import traceback
                traceback.print_exc()

            # 时间预算检查
            elapsed_ms = (time.time() - start_time) * 1000
            if elapsed_ms > self._BATCH_BUDGET_MS and gi < batch_end - 1:
                self._batch_cursor = gi + 1
                self._batch_insert_pos = insert_pos
                remaining = gi + 1
                if hasattr(self, '_batch_placeholder') and self._batch_placeholder:
                    try:
                        self._batch_placeholder.setText(
                            f"⏳ 加载历史消息 ({remaining} 轮)..."
                        )
                    except RuntimeError:
                        pass
                self._batch_render_timer.start(0)
                return

        self._batch_cursor = batch_start
        self._batch_insert_pos = insert_pos

        if self._batch_cursor > 0:
            if hasattr(self, '_batch_placeholder') and self._batch_placeholder:
                try:
                    self._batch_placeholder.setText(
                        f"⏳ 加载历史消息 ({self._batch_cursor} 轮)..."
                    )
                except RuntimeError:
                    pass
            self._batch_render_timer.start(0)
        else:
            self._finish_batch_render()

    def _finish_batch_render(self):
        """完成分批渲染，清理占位符"""
        if hasattr(self, '_batch_placeholder') and self._batch_placeholder:
            try:
                self._batch_placeholder.setVisible(False)
                self._batch_placeholder.deleteLater()
            except RuntimeError:
                pass
            self._batch_placeholder = None
        self._batch_groups = None
        self._batch_render_timer = None

    # ------------------------------------------------------------------
    def _replay_todo_from_tool_call(self, tool_name: str, arguments_str: str):
        """从历史工具调用中恢复 todo 项（不显示在 UI 执行列表中）

        注意：todo 数据现在通过 todo_data 字段在缓存中保存/恢复，
        此方法仅作为兼容旧缓存的后备方案。
        """
        try:
            if isinstance(arguments_str, str) and arguments_str:
                args = json.loads(arguments_str)
            elif isinstance(arguments_str, dict):
                args = arguments_str
            else:
                return
            if tool_name == 'add_todo':
                tid = args.get('todo_id', '')
                text = args.get('text', '')
                status = args.get('status', 'pending')
                if tid and text and hasattr(self, 'todo_list') and self.todo_list:
                    self.todo_list.add_todo(tid, text, status)
                    self._ensure_todo_in_chat(self.todo_list, self.chat_layout)
            elif tool_name == 'update_todo':
                tid = args.get('todo_id', '')
                status = args.get('status', 'done')
                if tid and hasattr(self, 'todo_list') and self.todo_list:
                    self.todo_list.update_todo(tid, status)
        except Exception:
            pass  # 解析失败忽略

    # ------------------------------------------------------------------
    def _render_native_tool_turn(self, turn_msgs: list, history_range: tuple = None):
        """渲染 Cursor 风格原生工具调用轮次

        turn_msgs 格式：
          assistant(tool_calls) → tool → [assistant(tool_calls) → tool →] ... → assistant(reply)
        静默工具（add_todo/update_todo）不显示在执行列表中，但会恢复 todo 数据。
        """
        response = self._add_ai_response(history_range=history_range)
        tool_count = 0
        final_content = ''
        thinking = ''
        final_msg = {}

        for m in turn_msgs:
            if m.get('role') == 'assistant' and not m.get('tool_calls') and m.get('thinking'):
                thinking = m.get('thinking', '')
                response.add_thinking(thinking)
                if getattr(response, 'thinking_section', None) is not None:
                    response.thinking_section.finalize()
                break

        for m in turn_msgs:
            r = m.get('role', '')
            if r == 'assistant':
                tc_list = m.get('tool_calls', [])
                if tc_list:
                    # 工具调用 assistant 消息：注册每个工具调用
                    for tc in tc_list:
                        fn = tc.get('function', {})
                        name = fn.get('name', 'unknown')
                        # 静默工具：恢复 todo 但不显示在执行列表
                        if name in self._SILENT_TOOLS:
                            self._replay_todo_from_tool_call(name, fn.get('arguments', ''))
                            continue
                        response.add_status(f"[tool]{name}")
                        tool_count += 1
                else:
                    # 最终回复 assistant 消息
                    final_content = m.get('content', '') or ''
                    thinking = thinking or m.get('thinking', '')
                    final_msg = m
            elif r == 'tool':
                tc_id = m.get('tool_call_id', '')
                t_content = m.get('content', '') or ''
                # 从 tool_call_id 查找对应的工具名
                t_name = self._find_tool_name_by_id(turn_msgs, tc_id) or 'tool'
                # 静默工具的结果也不显示
                if t_name in self._SILENT_TOOLS:
                    continue
                success = not t_content.lstrip().startswith('[err]') and 'error' not in t_content[:50].lower()
                prefix = "[ok] " if success else "[err] "
                response.add_tool_result(t_name, f"{prefix}{t_content}")

        # 恢复 Shell 折叠面板
        self._restore_shell_widgets(response, final_msg)

        # AI 回复内容
        if final_content:
            response.set_content(final_content)

        # 状态标签
        parts = []
        if thinking:
            parts.append("思考")
        if tool_count > 0:
            parts.append(f"{tool_count}次调用")
        label = f"历史 | {', '.join(parts)}" if parts else "历史"
        response.status_label.setText(label)
        response.finalize()
        response.status_label.setText(label)

    @staticmethod
    def _find_tool_name_by_id(messages: list, tool_call_id: str) -> str:
        """从消息列表中根据 tool_call_id 查找对应的工具名"""
        if not tool_call_id:
            return ''
        for m in messages:
            if m.get('role') == 'assistant':
                for tc in m.get('tool_calls', []):
                    if tc.get('id') == tool_call_id:
                        return tc.get('function', {}).get('name', '')
        return ''

    # ------------------------------------------------------------------
    def _render_user_history(self, content: str, history_range: tuple = None):
        """渲染用户历史消息，长上下文自动折叠"""
        # 检查是否包含 [Network structure] 等上下文注入
        split_pos = -1
        header_tag = ''
        for tag in self._CONTEXT_HEADERS:
            pos = content.find(tag)
            if pos != -1:
                split_pos = pos
                header_tag = tag
                break

        if split_pos > 0 and len(content) > 300:
            # 用户实际输入 + 上下文注入
            user_text = content[:split_pos].strip()
            context_data = content[split_pos:]
            # 显示用户实际文字
            if user_text:
                self._add_user_message(user_text, history_range=history_range)
            # 上下文放进折叠区域
            resp = self._add_ai_response(history_range=history_range)
            resp.add_collapsible(header_tag.strip('[]'), context_data)
            resp.status_label.setText("上下文")
            resp.finalize()
            resp.status_label.setText("上下文")
        elif split_pos == 0 and len(content) > 300:
            # 纯上下文（无用户文字），整块折叠
            resp = self._add_ai_response(history_range=history_range)
            resp.add_collapsible(header_tag.strip('[]'), content)
            resp.status_label.setText("上下文")
            resp.finalize()
            resp.status_label.setText("上下文")
        else:
            self._add_user_message(content, history_range=history_range)

    # ------------------------------------------------------------------
    _TOOL_LINE_PREFIXES = ('[ok] ', '[err] ', '✅ ', '❌ ')

    def _render_tool_summary_history(self, content: str, msg: dict = None, history_range: tuple = None):
        """渲染 [工具执行结果] 格式的 assistant 消息

        格式示例：
          [工具执行结果]
          [ok] get_network_structure: ## 网络结构: /obj
          网络类型: obj          ← 上一条的续行
          节点数量: 0            ← 上一条的续行
          [ok] create_node: /obj/geo1
        """
        if msg is None:
            msg = {}
        response = self._add_ai_response(history_range=history_range)

        # 先按行分组：以 [ok]/[err]/✅/❌ 开头的行开始新条目，
        # 其他行归到前一条目的续行
        entries = []  # [(first_line, [continuation_lines])]
        for line in content.split('\n'):
            stripped = line.strip()
            if not stripped or stripped == '[工具执行结果]':
                # 空行或标题→如果有上一条目，添加空行到续行（保留格式）
                if entries:
                    entries[-1][1].append('')
                continue
            is_new_entry = any(stripped.startswith(p) for p in self._TOOL_LINE_PREFIXES)
            if is_new_entry:
                entries.append((stripped, []))
            elif entries:
                entries[-1][1].append(stripped)
            # else: 没有前导条目的散行，忽略

        tool_count = 0
        for first_line, cont_lines in entries:
            t_name = 'unknown'
            success = True
            # 解析前缀
            rest = first_line
            for prefix in self._TOOL_LINE_PREFIXES:
                if first_line.startswith(prefix):
                    if 'err' in prefix or '❌' in prefix:
                        success = False
                    rest = first_line[len(prefix):]
                    break
            # 解析 tool_name: result
            if ':' in rest:
                parts = rest.split(':', 1)
                t_name = parts[0].strip()
                first_result = parts[1].strip() if len(parts) > 1 else ''
            else:
                first_result = rest

            # 合并续行
            all_parts = [first_result] + cont_lines
            t_result = '\n'.join(all_parts).strip()

            # 静默工具不显示在执行列表
            if t_name in self._SILENT_TOOLS:
                continue
            # 注册工具 + 设置结果
            response.add_status(f"[tool]{t_name}")
            tool_count += 1
            result_prefix = "[ok] " if success else "[err] "
            response.add_tool_result(t_name, f"{result_prefix}{t_result}")

        # 恢复 Shell 折叠面板
        self._restore_shell_widgets(response, msg)

        # 恢复 thinking
        thinking = msg.get('thinking', '')
        if thinking:
            response.add_thinking(thinking)
            if getattr(response, 'thinking_section', None) is not None:
                response.thinking_section.finalize()

        # 恢复正文（[工具执行结果]之后可能还有 AI 正式回复）
        # 找到工具摘要之后的正文部分
        text_after_tools = ''
        parts = content.split('\n\n')
        for idx_p, part in enumerate(parts):
            if not part.strip().startswith('[工具执行结果]') and not any(
                part.strip().startswith(p) for p in self._TOOL_LINE_PREFIXES
            ):
                # 检查是否整段都是工具结果行
                is_tool_block = all(
                    any(line.strip().startswith(p) for p in self._TOOL_LINE_PREFIXES)
                    or not line.strip()
                    or line.strip() == '[工具执行结果]'
                    for line in part.split('\n')
                )
                if not is_tool_block and part.strip():
                    text_after_tools = '\n\n'.join(parts[idx_p:])
                    break
        if text_after_tools:
            response.set_content(text_after_tools)

        label_parts = []
        if thinking:
            label_parts.append("思考")
        label_parts.append(f"{tool_count}次调用")
        response.status_label.setText(f"历史 | {', '.join(label_parts)}")
        response.finalize()
        response.status_label.setText(f"历史 | {', '.join(label_parts)}")

    # ------------------------------------------------------------------
    def _restore_shell_widgets(self, response, msg: dict):
        """从历史消息中恢复 Python Shell / System Shell 折叠面板"""
        # 恢复 Python Shell
        for ps in msg.get('python_shells', []):
            code = ps.get('code', '')
            raw_output = ps.get('output', '')
            error = ps.get('error', '')
            success = ps.get('success', True)
            # 提取执行时间（和 _on_add_python_shell 相同逻辑）
            exec_time = 0.0
            clean_parts = []
            for line in raw_output.split('\n'):
                time_match = re.match(r'^执行时间:\s*([\d.]+)s$', line.strip())
                if time_match:
                    exec_time = float(time_match.group(1))
                    continue
                if line.strip() == '输出:':
                    continue
                clean_parts.append(line)
            clean_output = '\n'.join(clean_parts).strip()
            widget = PythonShellWidget(
                code=code, output=clean_output, error=error,
                exec_time=exec_time, success=success, parent=response
            )
            response.add_shell_widget(widget)

        # 恢复 System Shell
        for ss in msg.get('system_shells', []):
            command = ss.get('command', '')
            raw_output = ss.get('output', '')
            error = ss.get('error', '')
            success = ss.get('success', True)
            cwd = ss.get('cwd', '')
            exec_time = 0.0
            exit_code = 0
            stdout_parts = []
            for line in raw_output.split('\n'):
                tm = re.search(r'耗时:\s*([\d.]+)s', line)
                cm = re.search(r'退出码:\s*(\d+)', line)
                if tm:
                    exec_time = float(tm.group(1))
                if cm:
                    exit_code = int(cm.group(1))
                if tm or cm:
                    continue
                if line.strip() in ('--- stdout ---', '--- stderr ---'):
                    continue
                stdout_parts.append(line)
            clean_output = '\n'.join(stdout_parts).strip()
            widget = SystemShellWidget(
                command=command, output=clean_output, error=error,
                exit_code=exit_code, exec_time=exec_time,
                success=success, cwd=cwd, parent=response
            )
            response.add_sys_shell_widget(widget)

    # ------------------------------------------------------------------
    def _render_old_tool_msgs(self, response, tool_msgs: list):
        """渲染旧格式 role=tool 消息到 AIResponse"""
        for tm in tool_msgs:
            t_name = tm.get('name', 'unknown')
            t_content = tm.get('content', '')
            # 解析 tool_name:result_text
            if ':' in t_content:
                parts = t_content.split(':', 1)
                t_name = parts[0].strip() or t_name
                t_result = parts[1].strip() if len(parts) > 1 else t_content
            else:
                t_result = t_content
            # 静默工具不显示在执行列表
            if t_name in self._SILENT_TOOLS:
                continue
            success = not t_result.startswith('[err]') and not t_result.startswith('❌')
            # 先注册工具调用
            response.add_status(f"[tool]{t_name}")
            result_prefix = "[ok] " if success else "[err] "

            response.add_tool_result(t_name, f"{result_prefix}{t_result}")
