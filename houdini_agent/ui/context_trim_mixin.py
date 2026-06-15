# -*- coding: utf-8 -*-
"""
ContextTrimMixin — In-conversation context window trimming and compression.
Extracted from ai_tab.py.
"""

import re

from .i18n import tr


class ContextTrimMixin:
    """In-conversation context window trimming and compression."""

    def _manage_context(self):
        """管理上下文长度 — Cursor 风格轮次裁剪

        核心原则（与 _progressive_trim 一致）：
        - **永不截断 user / assistant 消息**
        - 只压缩 tool 结果（role='tool' 的 content）
        - 按「轮次」（以 user 消息为分界）裁剪，保护最近 N 轮
        - 如果仅压缩 tool 仍不够，整轮删除最早的轮次
        - 保持 assistant(tool_calls) ↔ tool 的原生链不被打破
        """
        # ★ 使用 agent 锚定的 history（避免压缩错误 session）
        history = self._agent_history if self._agent_history is not None else self._conversation_history

        # ★ 工作上下文：_send_context 优先（已裁剪的副本），否则用 history
        # _conversation_history 作为永久存档，压缩只改写 _send_context，显示不受影响。
        work = self._send_context if self._send_context is not None else history

        if len(work) < 6:
            return  # 太少，不需管理

        current_tokens = self.token_optimizer.calculate_message_tokens(work)
        context_limit = self._get_current_context_limit()

        # 更新预算
        self.token_optimizer.budget.max_tokens = context_limit
        should_compress, reason = self.token_optimizer.should_compress(current_tokens, context_limit)

        if not (should_compress and self._auto_optimize):
            if reason and ('警告' in reason or 'warning' in reason.lower()):
                self._addStatus.emit(f"Note: {reason}")
            return

        # ★ 深度睡眠：_manage_context 压缩前整理全部上下文为长期记忆
        if self._is_memory_active() and self._reflection_module and not self._sleep_in_progress:
            _params = getattr(self, '_last_agent_params', {})
            if _params:
                self._addStatus.emit("😴 深度睡眠：正在整理全部上下文为长期记忆...")
                try:
                    self._sleep_in_progress = True
                    deep_result = self._reflection_module.deep_sleep(
                        session_id=self._session_id,
                        all_messages=list(work),
                        ai_client=self.client,
                        model=_params.get('model', 'deepseek-v4-flash'),
                        provider=_params.get('provider', 'deepseek'),
                    )
                    if deep_result.get("success"):
                        n_rules = len(deep_result.get("new_rules", []))
                        n_strats = len(deep_result.get("new_strategies", []))
                        self._addStatus.emit(
                            f"😴 深度睡眠完成: {n_rules} 条经验 + {n_strats} 条策略已写入长期记忆"
                        )
                except Exception as e:
                    print(f"[Sleep] _manage_context 深度睡眠异常: {e}")
                finally:
                    self._sleep_in_progress = False

        old_tokens = current_tokens

        # --- 按 user 消息划分轮次（在工作副本上操作）---
        # 先做一份独立副本，以便压缩 tool content 时不污染 _conversation_history
        work_copy = [m.copy() for m in work]
        rounds = []
        current_round = []
        for m in work_copy:
            if m.get('role') == 'user' and current_round:
                rounds.append(current_round)
                current_round = []
            current_round.append(m)
        if current_round:
            rounds.append(current_round)

        if len(rounds) <= 2:
            return  # 只有 1-2 轮，不裁剪

        # --- 第一遍：压缩旧轮次的 tool 结果（保留最近 60%）---
        n_rounds = len(rounds)
        protect_n = max(2, int(n_rounds * 0.6))
        for r_idx in range(n_rounds - protect_n):
            for m in rounds[r_idx]:
                if m.get('role') == 'tool':
                    c = m.get('content') or ''
                    if len(c) > 200:
                        m['content'] = self.client._summarize_tool_content(c, 200) if hasattr(self.client, '_summarize_tool_content') else c[:200] + '...[summary]'

        # 重新计算
        compressed = [m for rnd in rounds for m in rnd]
        new_tokens = self.token_optimizer.calculate_message_tokens(compressed)

        if new_tokens < context_limit * self.token_optimizer.budget.compression_threshold:
            # 压缩 tool 就够了 — 写入 _send_context，不动 _conversation_history
            self._send_context = compressed
            saved = old_tokens - new_tokens
            if saved > 0:
                self._addStatus.emit(tr('opt.auto_status', saved))
            return

        # --- 第二遍：删除最早的完整轮次，直到低于阈值 ---
        target = int(context_limit * 0.65)  # 目标降到 65%
        while len(rounds) > 2:
            rounds.pop(0)
            compressed = [m for rnd in rounds for m in rnd]
            new_tokens = self.token_optimizer.calculate_message_tokens(compressed)
            if new_tokens <= target:
                break

        # 在头部插入摘要提示 — 写入 _send_context，不动 _conversation_history
        summary_note = {
            'role': 'system',
            'content': tr('ai.old_rounds', n_rounds - len(rounds))
        }
        self._send_context = [summary_note] + [m for rnd in rounds for m in rnd]

        saved = old_tokens - self.token_optimizer.calculate_message_tokens(self._send_context)
        if saved > 0:
            self._addStatus.emit(tr('opt.auto_status', saved))

    def _compress_context(self):
        """压缩上下文 — 智能摘要，保留关键信息

        改进策略:
        1. 按轮次（user→assistant 对）提取信息，而非简单截取
        2. 提取用户意图、工具操作、关键结果、节点路径
        3. 识别错误和纠正行为
        4. 生成结构化摘要
        """
        if len(self._conversation_history) <= 4:
            return  # 太短不需要压缩

        # 将旧对话压缩成摘要
        old_messages = self._conversation_history[:-4]  # 保留最近 4 条
        recent_messages = self._conversation_history[-4:]

        # 按轮次分组
        rounds_info = []
        current_round = {"user": "", "assistant": "", "tools": [], "errors": []}

        for msg in old_messages:
            role = msg.get('role', '')
            content = msg.get('content', '')

            if isinstance(content, list):
                # 多模态内容 → 提取文字
                content = ' '.join(
                    p.get('text', '') for p in content if isinstance(p, dict) and p.get('type') == 'text'
                )

            if role == 'user':
                if current_round["user"]:
                    rounds_info.append(current_round)
                    current_round = {"user": "", "assistant": "", "tools": [], "errors": []}
                current_round["user"] = content[:120].replace('\n', ' ').strip()
            elif role == 'assistant' and content:
                # 去除 think 标签
                clean = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
                if clean:
                    # 提取关键句（最后两行通常是结论）
                    lines = [l.strip() for l in clean.split('\n') if l.strip()]
                    summary_lines = lines[-2:] if len(lines) > 2 else lines
                    current_round["assistant"] = ' '.join(summary_lines)[:100]
                # 提取工具调用
                tool_calls = msg.get('tool_calls', [])
                if tool_calls:
                    for tc in tool_calls:
                        fn = tc.get('function', {})
                        current_round["tools"].append(fn.get('name', ''))
            elif role == 'tool':
                tool_content = content or ''
                if 'error' in tool_content.lower() or 'fail' in tool_content.lower():
                    current_round["errors"].append(tool_content[:60])

        if current_round["user"]:
            rounds_info.append(current_round)

        # 生成结构化摘要
        summary_parts = []
        for i, rnd in enumerate(rounds_info[-5:], 1):  # 最多保留最近 5 轮
            parts = []
            if rnd["user"]:
                parts.append(f"Q: {rnd['user'][:60]}")
            if rnd["assistant"]:
                parts.append(f"A: {rnd['assistant'][:60]}")
            if rnd["tools"]:
                unique_tools = list(dict.fromkeys(rnd["tools"]))[:3]
                parts.append(f"Tools: {','.join(unique_tools)}")
            if rnd["errors"]:
                parts.append(f"⚠ {rnd['errors'][0][:40]}")
            if parts:
                summary_parts.append(f"R{i}: " + " | ".join(parts))

        # 提取提到的节点路径
        all_text = ' '.join(msg.get('content', '') for msg in old_messages if isinstance(msg.get('content'), str))
        node_paths = list(set(re.findall(r'/obj/[a-zA-Z0-9_/]+', all_text)))
        if node_paths:
            summary_parts.append(f"Nodes: {', '.join(node_paths[:5])}")

        # 生成上下文摘要
        if summary_parts:
            self._context_summary = "\n".join(summary_parts)
        else:
            self._context_summary = ""

        # 更新历史（只保留最近的）
        self._conversation_history = recent_messages

        print(f"[Context] 压缩上下文: 保留 {len(recent_messages)} 条消息, "
              f"摘要 {len(self._context_summary)} 字符 ({len(rounds_info)} 轮提取)")
