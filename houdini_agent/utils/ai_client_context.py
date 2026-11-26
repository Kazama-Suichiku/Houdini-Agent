# -*- coding: utf-8 -*-
"""Claude Agent SDK / Codex 风格的上下文压缩管理。

核心原则（对齐 Anthropic Claude Agent SDK compaction_control）：
1. 平时完全不动历史前缀（零 [Stale] 改写、零按工具正则微裁剪），保证 100% Prompt Cache 命中率；
2. 仅当真实/估算上下文逼近窗口阈值（默认 85%，大窗口额外预留 16k 缓冲）或用户手动触发时，
   执行一次性 LLM 结构化交接摘要（保留头部 system prompt + 最近少量原文尾部，替代更早历史）；
3. 仅在 LLM 摘要不可用（离线/失败）或 API 仍报 413/超限时，按完整轮次/原子工具步干净丢弃最老历史；
4. 绝不在对话中间或末尾插入 role='system'，且双向清洗确保 assistant.tool_calls 与 role='tool' 严格配对。
"""

import copy
import json
import re
from typing import Any, Dict, List, Optional, Tuple


class AIClientContextMixin:
    """Context window management: Claude Agent SDK-style compaction & overflow recovery."""

    # 已自带分页的工具，入口处不再二次截断
    _SELF_PAGED_TOOLS = frozenset({
        'get_houdini_node_doc', 'get_network_structure', 'get_node_parameters',
        'list_children', 'execute_python', 'execute_shell',
    })

    _SUMMARY_PREFIXES = (
        '[对话历史摘要]',
        '[Compacted Context Summary]',
        '[Context]',
        '[上下文管理]',
        '[历史对话摘要]',
    )

    # ------------------------------------------------------------------
    # 工具输出入口防爆上限（现代大上下文窗口不再微裁剪，只防极端大输出）
    # ------------------------------------------------------------------

    @staticmethod
    def _paginate_result(text: str, max_lines: int = 200) -> str:
        """按行上限截断超长文本，未超限原样返回。"""
        if not text:
            return text
        lines = text.split('\n')
        total = len(lines)
        if total <= max_lines:
            return text
        page = '\n'.join(lines[:max_lines])
        return (
            f"{page}\n\n"
            f"[输出截断] 显示前 {max_lines} 行，共 {total} 行。"
            f"如需更多内容请使用更精确的过滤/分页参数。"
        )

    @staticmethod
    def _cap_text(text: str, max_chars: int = 20000) -> str:
        """对极端超长工具输出做头尾保留截断，常规输出原样保留。"""
        if not text or len(text) <= max_chars:
            return text
        head = int(max_chars * 0.75)
        tail = max_chars - head
        omitted = len(text) - max_chars
        return f"{text[:head]}\n\n...[省略中间 {omitted} 字符]...\n\n{text[-tail:]}"

    def _compress_tool_result(self, tool_name: str, result: dict) -> str:
        """工具返回结果入口防爆处理：保留完整语义，只限制极端大块输出。"""
        cook_note = result.get('_cook_note') if isinstance(result, dict) else None

        if result.get('success'):
            content = str(result.get('result', '') or '')
            if tool_name not in self._SELF_PAGED_TOOLS:
                content = self._cap_text(content, max_chars=20000)
            if cook_note:
                content = f"{content}\n{cook_note}"
            return content

        error = str(result.get('error', '未知错误') or '未知错误')
        return self._cap_text(error, max_chars=4000)

    @classmethod
    def _summarize_tool_content(cls, content: str, max_len: int = 200) -> str:
        """兼容旧 QtWidgets 路径的轻量文本截断辅助。"""
        if not content or len(content) <= max_len:
            return content
        return content[:max_len] + '...[摘要]'

    # ------------------------------------------------------------------
    # 消息清洗：确保 tool_calls / tool 配对合法
    # ------------------------------------------------------------------

    @staticmethod
    def _ensure_tool_call_ids(tool_calls: list) -> list:
        """确保每个 tool_call 都有有效的 id / type / function 字段。"""
        import uuid
        for tc in tool_calls:
            if not tc.get('id'):
                tc['id'] = f"call_{uuid.uuid4().hex[:24]}"
            if not tc.get('type'):
                tc['type'] = 'function'
            fn = tc.get('function', {})
            if not fn.get('name'):
                fn['name'] = 'unknown'
            if not str(fn.get('arguments', '')).strip():
                fn['arguments'] = '{}'
            tc['function'] = fn
        return tool_calls

    def _sanitize_working_messages(self, messages: list) -> list:
        """发送前双向清洗消息列表，修复 tool_calls / tool 配对问题。

        1. 补齐缺失的 tool_call id
        2. 移除孤儿 role:tool 消息（前面没有对应的 assistant tool_call）
        3. 清理悬挂的 assistant.tool_calls（后面没有对应的 role:tool 响应，避免 API 400）
        """
        responded_tc_ids = set()
        for msg in messages:
            if msg.get('role') == 'assistant' and msg.get('tool_calls'):
                self._ensure_tool_call_ids(msg['tool_calls'])
            elif msg.get('role') == 'tool':
                tc_id = msg.get('tool_call_id', '')
                if tc_id:
                    responded_tc_ids.add(tc_id)

        seen_tc_ids = set()
        sanitized = []
        for msg in messages:
            role = msg.get('role')
            if role == 'assistant' and msg.get('tool_calls'):
                kept_tcs = []
                for tc in msg['tool_calls']:
                    tc_id = tc.get('id', '')
                    if tc_id and tc_id in responded_tc_ids:
                        kept_tcs.append(tc)
                        seen_tc_ids.add(tc_id)
                if len(kept_tcs) == len(msg['tool_calls']):
                    sanitized.append(msg)
                else:
                    cleaned = dict(msg)
                    if kept_tcs:
                        cleaned['tool_calls'] = kept_tcs
                    else:
                        cleaned.pop('tool_calls', None)
                        if not cleaned.get('content'):
                            cleaned['content'] = '[已省略未完成的工具调用]'
                    sanitized.append(cleaned)
                continue

            if role == 'tool':
                tc_id = msg.get('tool_call_id', '')
                if not tc_id or tc_id not in seen_tc_ids:
                    continue
            sanitized.append(msg)
        return sanitized

    # ------------------------------------------------------------------
    # 图片剥离（用于非视觉模型或 413 溢出恢复）
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_image_content(messages: list, keep_recent_user: int = 0) -> int:
        """就地剥离消息中的 base64 图片块，保留文字部分。"""
        stripped = 0
        protected_indices: set = set()
        if keep_recent_user > 0:
            count = 0
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].get('role') == 'user':
                    protected_indices.add(i)
                    count += 1
                    if count >= keep_recent_user:
                        break

        for idx, msg in enumerate(messages):
            content = msg.get('content')
            if not isinstance(content, list) or idx in protected_indices:
                continue

            text_parts = []
            has_image = False
            for part in content:
                if isinstance(part, dict):
                    if part.get('type') == 'text':
                        text_parts.append(part.get('text', ''))
                    elif part.get('type') in ('image_url', 'image'):
                        has_image = True
                        stripped += 1
                elif isinstance(part, str):
                    text_parts.append(part)

            if has_image:
                combined = '\n'.join(t for t in text_parts if t)
                combined = (combined + '\n[图片已移除以节省上下文空间]') if combined else '[图片已移除]'
                msg['content'] = combined

        return stripped

    # ------------------------------------------------------------------
    # Token 估算（UTF-8 CJK/ASCII 快速估算）
    # ------------------------------------------------------------------

    @staticmethod
    def _estimate_text_tokens(text: Any) -> int:
        """快速区分 ASCII 与中文/非 ASCII 字符估算 token 数。"""
        if not text:
            return 0
        if not isinstance(text, str):
            text = str(text)
        n_chars = len(text)
        if n_chars == 0:
            return 0
        n_bytes = len(text.encode('utf-8'))
        if n_bytes == n_chars:
            return max(1, int(n_chars / 3.8))
        non_ascii = min(n_chars, (n_bytes - n_chars) // 2)
        ascii_n = n_chars - non_ascii
        return max(1, int(ascii_n / 3.8 + non_ascii * 0.75))

    @classmethod
    def _estimate_messages_tokens(cls, messages: list, tools: Optional[list] = None) -> int:
        """快速估算消息列表 + 工具定义的 token 数。"""
        total = 0
        for msg in messages:
            content = msg.get('content') or ''
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get('type') == 'text':
                        total += cls._estimate_text_tokens(part.get('text', ''))
                    elif isinstance(part, dict) and part.get('type') in ('image_url', 'image'):
                        total += 765
                    elif isinstance(part, str):
                        total += cls._estimate_text_tokens(part)
            else:
                total += cls._estimate_text_tokens(content)

            tcs = msg.get('tool_calls')
            if tcs:
                for tc in tcs:
                    fn = tc.get('function', {})
                    total += cls._estimate_text_tokens(fn.get('name', ''))
                    total += cls._estimate_text_tokens(fn.get('arguments', ''))
                    total += 8
            total += 4

        if tools:
            for t in tools:
                fn = t.get('function', {})
                total += cls._estimate_text_tokens(fn.get('name', ''))
                total += cls._estimate_text_tokens(fn.get('description', ''))
                params = fn.get('parameters', {})
                if params:
                    try:
                        total += cls._estimate_text_tokens(json.dumps(params, ensure_ascii=False))
                    except Exception:
                        total += cls._estimate_text_tokens(str(params))
                total += 24

        return total

    @staticmethod
    def _compaction_threshold(context_limit: int) -> int:
        """计算自动触发压缩的 token 阈值（对齐 Claude Code / Agent SDK 预留输出缓冲）。"""
        limit = max(1, int(context_limit or 128000))
        ratio_cap = int(limit * 0.85)
        if limit >= 32000:
            return min(ratio_cap, max(int(limit * 0.5), limit - 16000))
        return ratio_cap

    # ------------------------------------------------------------------
    # 消息分组与安全摘要构造
    # ------------------------------------------------------------------

    @staticmethod
    def _split_leading_system(messages: list) -> Tuple[list, list]:
        """分离头部所有连续的 role='system' 消息与正文消息。"""
        idx = 0
        n = len(messages)
        while idx < n and messages[idx].get('role') == 'system':
            idx += 1
        return list(messages[:idx]), list(messages[idx:])

    @staticmethod
    def _group_into_rounds(body: list) -> list:
        """按 user 消息为边界划分对话轮次。"""
        rounds = []
        cur = []
        for m in body:
            if m.get('role') == 'user' and cur:
                rounds.append(cur)
                cur = []
            cur.append(m)
        if cur:
            rounds.append(cur)
        return rounds

    @staticmethod
    def _split_round_into_atomic_steps(rnd: list) -> list:
        """将单轮拆成不可分割的原子步（assistant.tool_calls + 紧随的 role='tool' 绑定为一步）。"""
        steps = []
        i = 0
        n = len(rnd)
        while i < n:
            m = rnd[i]
            if m.get('role') == 'assistant' and m.get('tool_calls'):
                group = [m]
                i += 1
                while i < n and rnd[i].get('role') == 'tool':
                    group.append(rnd[i])
                    i += 1
                steps.append(group)
            else:
                steps.append([m])
                i += 1
        return steps

    @staticmethod
    def _make_summary_messages(summary_text: str, next_role: Optional[str]) -> list:
        """构造全协议兼容的压缩交接消息（绝不插入 mid-conversation role='system'）。"""
        msgs = [{'role': 'user', 'content': summary_text}]
        if next_role == 'user':
            msgs.append({
                'role': 'assistant',
                'content': '已了解上述上下文压缩摘要，将继续当前任务。',
            })
        return msgs

    def _pick_summary_model(self, model: str = '', provider: str = '') -> Tuple[str, str]:
        """选择用于生成压缩摘要的模型：默认使用当前会话已验证可用的 provider/model。"""
        prov = (provider or '').strip().lower()
        mdl = (model or '').strip()
        if prov and mdl:
            return mdl, prov
        if hasattr(self, '_get_api_key') and self._get_api_key('deepseek'):
            return 'deepseek-v4-flash', 'deepseek'
        return mdl or 'gpt-5.2', prov or 'openai'

    def _split_compact_head_and_tail(self, body: list, keep_recent_rounds: int = 2) -> Tuple[list, list, list]:
        """将正文切分为 (preserved_head, to_compact, kept_tail)：
        - 多轮对话（>= keep_recent_rounds + 1 轮）：压缩早期轮次，保留最近 keep_recent_rounds 轮原文
        - 少轮但单轮长工具链（原子步 > 4）：保留原始 user 提问与最近 2 个原子步，压缩中间步骤
        - 极少轮但单条巨长：整段压缩为交接摘要（tail 为空）
        """
        rounds = self._group_into_rounds(body)
        if len(rounds) >= keep_recent_rounds + 1:
            to_compact = [m for r in rounds[:-keep_recent_rounds] for m in r]
            kept_tail = [m for r in rounds[-keep_recent_rounds:] for m in r]
            return [], to_compact, kept_tail

        # 少于 keep_recent_rounds + 1 轮：按原子步切分
        steps = []
        for rnd in rounds:
            steps.extend(self._split_round_into_atomic_steps(rnd))

        if len(steps) > 4:
            head = steps[:1] if steps[0][0].get('role') == 'user' else []
            keep_tail_steps = 2
            middle = steps[len(head):-keep_tail_steps]
            tail = steps[-keep_tail_steps:]
            return (
                [m for st in head for m in st],
                [m for st in middle for m in st],
                [m for st in tail for m in st],
            )

        # 只有 1~4 步但依然需要压缩（例如用户发了超长文本或大图）
        return [], list(body), []

    def _fallback_trim_to_target(self, leading_sys: list, body: list,
                                 target_tokens: int,
                                 tools: Optional[list] = None,
                                 keep_recent_images: int = 1) -> list:
        """离线/摘要失败时的干净兜底：剥离旧图 + 按完整轮次/原子步丢弃最老历史。"""
        body_copy = copy.deepcopy(body)
        self._strip_image_content(body_copy, keep_recent_user=keep_recent_images)

        rounds = self._group_into_rounds(body_copy)
        orig_rounds = len(rounds)
        has_existing_summary = bool(
            rounds and rounds[0]
            and isinstance(rounds[0][0].get('content'), str)
            and any(rounds[0][0]['content'].lstrip().startswith(p) for p in self._SUMMARY_PREFIXES)
        )
        dropped_rounds = 0
        dropped_steps = 0

        def _cur_tokens(rnds):
            return self._estimate_messages_tokens(
                leading_sys + [m for r in rnds for m in r], tools
            )

        min_keep = 2 if has_existing_summary else 2
        while len(rounds) > min_keep and _cur_tokens(rounds) > target_tokens:
            drop_idx = 1 if has_existing_summary else 0
            rounds.pop(drop_idx)
            dropped_rounds += 1

        if _cur_tokens(rounds) > target_tokens and rounds:
            for r_idx in range(len(rounds)):
                if _cur_tokens(rounds) <= target_tokens:
                    break
                if has_existing_summary and r_idx == 0:
                    continue
                steps = self._split_round_into_atomic_steps(rounds[r_idx])
                if len(steps) <= 3:
                    continue
                head = steps[:1] if steps[0][0].get('role') == 'user' else []
                middle = steps[len(head):]
                min_tail = 2 if r_idx == len(rounds) - 1 else 1
                while len(middle) > min_tail and _cur_tokens(rounds) > target_tokens:
                    middle.pop(0)
                    dropped_steps += 1
                    rounds[r_idx] = [m for st in (head + middle) for m in st]

        kept_body = [m for r in rounds for m in r]
        if (dropped_rounds > 0 or dropped_steps > 0) and not has_existing_summary:
            note = f"[Compacted Context Summary]\n已省略早期 {dropped_rounds} 轮对话"
            if dropped_steps > 0:
                note += f"及 {dropped_steps} 个早期工具步骤"
            note += f"（共 {orig_rounds} 轮），请基于保留的最近上下文继续当前任务。"
            next_role = kept_body[0].get('role') if kept_body else None
            kept_body = self._make_summary_messages(note, next_role) + kept_body

        return self._sanitize_working_messages(leading_sys + kept_body)

    # ------------------------------------------------------------------
    # 核心压缩入口：自动压缩 / 溢出恢复 / 手动压缩（统一走一套逻辑）
    # ------------------------------------------------------------------

    def _compact_messages(self, working_messages: list,
                          context_limit: int,
                          supports_vision: bool = True,
                          tools: Optional[list] = None,
                          model: str = '',
                          provider: str = '',
                          keep_recent_rounds: int = 2,
                          use_llm: bool = True,
                          state_context: str = '',
                          meta_out: Optional[dict] = None) -> Tuple[list, bool]:
        """执行一次 Claude Agent SDK 风格的上下文压缩，返回 (新消息列表, 是否使用了 LLM 摘要)。"""
        leading_sys, body = self._split_leading_system(copy.deepcopy(working_messages))
        if not body:
            return working_messages, False

        before_tokens = self._estimate_messages_tokens(working_messages, tools)
        target = int(context_limit * 0.60)
        head_keep, to_compact, kept_tail = self._split_compact_head_and_tail(
            body, keep_recent_rounds=keep_recent_rounds
        )

        # 剥离被压缩段内的旧图片（保留尾部最新图片，若不支持视觉则全剥）
        self._strip_image_content(to_compact, keep_recent_user=0)
        if not supports_vision:
            self._strip_image_content(head_keep, keep_recent_user=0)
            self._strip_image_content(kept_tail, keep_recent_user=0)

        used_llm = False
        summary_text = None
        summary_usage: Dict[str, Any] = {}
        s_model, s_prov = self._pick_summary_model(model, provider)

        if use_llm and to_compact:
            try:
                from houdini_agent.utils.token_optimizer import LLMSummarizer
                summary_text = LLMSummarizer.summarize_messages(
                    ai_client=self,
                    messages=to_compact,
                    model=s_model,
                    provider=s_prov,
                    usage_out=summary_usage,
                )
                if summary_text:
                    used_llm = True
            except Exception as e:
                print(f"[AI Client] LLM 压缩摘要失败，回退干净裁剪: {e}")

        state_suffix = ""
        if state_context and state_context.strip():
            state_suffix = "\n\n# Active Plan / Todo State\n" + state_context.strip()

        if summary_text:
            header = (
                "[Compacted Context Summary]\n"
                "The earlier conversation history was compacted into the following checkpoint. "
                "Continue seamlessly from this state without mentioning the compaction:\n\n"
                + summary_text
                + state_suffix
            )
            next_role = kept_tail[0].get('role') if kept_tail else None
            summary_msgs = self._make_summary_messages(header, next_role)
            new_body = head_keep + summary_msgs + kept_tail
            result = self._sanitize_working_messages(leading_sys + new_body)
            after_tokens = self._estimate_messages_tokens(result, tools)
            # 确认确实缩减了体积；若仍远超上限再做一次尾部兜底
            if after_tokens > int(context_limit * 0.80):
                result = self._fallback_trim_to_target(
                    leading_sys, new_body, target, tools=tools,
                    keep_recent_images=1 if supports_vision else 0,
                )
                after_tokens = self._estimate_messages_tokens(result, tools)
            print(f"[AI Client] 🗜️ 上下文压缩完成 (LLM): "
                  f"{len(working_messages)} → {len(result)} 条消息, "
                  f"~{after_tokens} tokens")
            if isinstance(meta_out, dict):
                meta_out.update({
                    'before_tokens': before_tokens,
                    'after_tokens': after_tokens,
                    'before_count': len(working_messages),
                    'after_count': len(result),
                    'used_llm': True,
                    'usage': summary_usage,
                    'model': s_model,
                    'provider': s_prov,
                })
            return result, True

        # LLM 不可用/离线：干净丢弃最老轮次或原子步到目标容量
        result = self._fallback_trim_to_target(
            leading_sys, body, target, tools=tools,
            keep_recent_images=1 if supports_vision else 0,
        )
        if state_suffix:
            # 将活跃 Plan / Todo 附在压缩说明里，确保离线兜底也不忘任务进度
            for m in result:
                c = m.get('content')
                if isinstance(c, str) and c.lstrip().startswith('[Compacted Context Summary]'):
                    m['content'] = c + state_suffix
                    break
        after_tokens = self._estimate_messages_tokens(result, tools)
        print(f"[AI Client] 🗜️ 上下文压缩完成 (Fallback): "
              f"{len(working_messages)} → {len(result)} 条消息, "
              f"~{after_tokens} tokens")
        if isinstance(meta_out, dict):
            meta_out.update({
                'before_tokens': before_tokens,
                'after_tokens': after_tokens,
                'before_count': len(working_messages),
                'after_count': len(result),
                'used_llm': False,
                'usage': summary_usage,
                'model': s_model,
                'provider': s_prov,
            })
        return result, False

    def _smart_compress_in_loop(self, working_messages: list,
                                tool_calls_history: list,
                                context_limit: int,
                                supports_vision: bool = True,
                                tools: Optional[list] = None,
                                model: str = '',
                                provider: str = '',
                                current_tokens: Optional[int] = None,
                                state_context: str = '',
                                meta_out: Optional[dict] = None) -> list:
        """Agent 循环内自动压缩（Claude Agent SDK 风格）。

        - 未达阈值：直接原样返回，零修改前缀，100% 保护 Prompt Cache；
        - 达到阈值：执行一次完整的 LLM 结构化压缩交接（失败时回退干净裁剪）。
        """
        if not working_messages:
            return working_messages

        threshold = self._compaction_threshold(context_limit)
        est = self._estimate_messages_tokens(working_messages, tools)
        cur = max(int(current_tokens or 0), est)
        if cur < threshold:
            return working_messages

        compacted, _ = self._compact_messages(
            working_messages=working_messages,
            context_limit=context_limit,
            supports_vision=supports_vision,
            tools=tools,
            model=model,
            provider=provider,
            keep_recent_rounds=2,
            use_llm=True,
            state_context=state_context,
            meta_out=meta_out,
        )
        if isinstance(meta_out, dict) and cur > meta_out.get('before_tokens', 0):
            meta_out['before_tokens'] = cur
        return compacted

    def _progressive_trim(self, working_messages: list, tool_calls_history: list,
                          trim_level: int = 1, supports_vision: bool = True,
                          context_limit: int = 128000,
                          model: str = '', provider: str = '') -> list:
        """API 实际返回上下文超限 / 413 时的溢出恢复。

        - level 1：优先尝试标准 LLM 压缩交接（并剥离旧图）
        - level 2+：剥离全部图片，只保留最近 1~2 轮 / 最近 4 个原子步，确保重试必过
        """
        if not working_messages:
            return working_messages

        if trim_level <= 1:
            compacted, _ = self._compact_messages(
                working_messages=working_messages,
                context_limit=context_limit,
                supports_vision=supports_vision,
                model=model,
                provider=provider,
                keep_recent_rounds=2,
                use_llm=bool(model and provider),
            )
            if len(compacted) < len(working_messages) or (
                self._estimate_messages_tokens(compacted) < self._estimate_messages_tokens(working_messages)
            ):
                return compacted

        # 强力兜底：剥离全部图片，仅保留最近少量完整轮次/原子步
        msgs = copy.deepcopy(working_messages)
        self._strip_image_content(msgs, keep_recent_user=0)
        leading_sys, body = self._split_leading_system(msgs)
        if not body:
            return msgs

        rounds = self._group_into_rounds(body)
        keep_n = 2 if trim_level == 2 else 1
        kept_rounds = rounds[-keep_n:] if len(rounds) > keep_n else rounds

        # 若最后保留轮本身仍包含超多工具步，按原子步保留 user + 最近 4 步
        if kept_rounds and len(kept_rounds[-1]) > 12:
            steps = self._split_round_into_atomic_steps(kept_rounds[-1])
            if len(steps) > 5:
                head = steps[:1] if steps[0][0].get('role') == 'user' else []
                tail = steps[-4:]
                kept_rounds[-1] = [m for st in (head + tail) for m in st]

        kept_body = [m for r in kept_rounds for m in r]
        if len(kept_rounds) < len(rounds) or len(kept_body) < len(body):
            note = (
                f"[Compacted Context Summary]\n"
                f"由于上下文超限，已自动裁剪早期历史（保留最近 {len(kept_rounds)} 轮）。"
                f"请继续完成当前任务，不要提及此裁剪。"
            )
            next_role = kept_body[0].get('role') if kept_body else None
            kept_body = self._make_summary_messages(note, next_role) + kept_body

        result = self._sanitize_working_messages(leading_sys + kept_body)
        print(f"[AI Client] 溢出恢复: level={trim_level}, 消息 {len(working_messages)} → {len(result)}")
        return result

    def compress_history(self, history: list,
                         context_limit: int = 128000,
                         model: str = '',
                         provider: str = '',
                         supports_vision: bool = True,
                         tools: Optional[list] = None,
                         use_llm: bool = True,
                         state_context: str = '') -> Tuple[list, Dict[str, Any]]:
        """手动压缩会话历史（对齐 Claude Code / Codex 的 /compact）。"""
        if not history:
            return [], {'compressed': False, 'reason': '当前会话为空，无需压缩'}

        before_tokens = self._estimate_messages_tokens(history, None)
        before_count = len(history)

        leading_sys, body = self._split_leading_system(copy.deepcopy(history))
        if len(body) <= 2 and before_tokens < max(1500, int(context_limit * 0.10)):
            return history, {
                'compressed': False,
                'reason': '上下文较短，无需压缩',
                'before_tokens': before_tokens,
                'after_tokens': before_tokens,
                'before_count': before_count,
                'after_count': before_count,
            }

        # 手动 /compact：优先把早期历史用 LLM 压缩为结构化交接摘要，保留最近 1~2 轮原文
        keep_n = 2 if len(self._group_into_rounds(body)) >= 4 else 1
        head_keep, to_compact, kept_tail = self._split_compact_head_and_tail(
            body, keep_recent_rounds=keep_n
        )

        self._strip_image_content(to_compact, keep_recent_user=0)
        self._strip_image_content(kept_tail, keep_recent_user=1 if supports_vision else 0)

        used_llm = False
        summary_text = None
        summary_usage: Dict[str, Any] = {}
        if use_llm and to_compact and model and provider:
            try:
                from houdini_agent.utils.token_optimizer import LLMSummarizer
                s_model, s_prov = self._pick_summary_model(model, provider)
                summary_text = LLMSummarizer.summarize_messages(
                    ai_client=self,
                    messages=to_compact,
                    model=s_model,
                    provider=s_prov,
                    usage_out=summary_usage,
                )
                if summary_text:
                    used_llm = True
            except Exception as e:
                print(f"[AI Client] 手动压缩 LLM 摘要失败，回退规则摘要: {e}")

        if not summary_text and to_compact:
            try:
                from houdini_agent.utils.token_optimizer import TokenOptimizer
                summary_text = TokenOptimizer()._generate_balanced_summary(to_compact)
            except Exception:
                summary_text = f"已压缩 {len(to_compact)} 条早期历史消息。"

        if summary_text:
            state_suffix = ""
            if state_context and state_context.strip():
                state_suffix = "\n\n# Active Plan / Todo State\n" + state_context.strip()
            header = (
                "[Compacted Context Summary]\n"
                "以下是早期对话的结构化压缩摘要，请基于此上下文继续当前任务：\n\n"
                + summary_text
                + state_suffix
            )
            next_role = kept_tail[0].get('role') if kept_tail else None
            new_body = head_keep + self._make_summary_messages(header, next_role) + kept_tail
        else:
            new_body = body

        result = self._sanitize_working_messages(leading_sys + new_body)
        after_tokens = self._estimate_messages_tokens(result, None)

        if after_tokens >= before_tokens and len(result) >= before_count:
            return history, {
                'compressed': False,
                'reason': '上下文已较精简，无需压缩',
                'before_tokens': before_tokens,
                'after_tokens': before_tokens,
                'before_count': before_count,
                'after_count': before_count,
            }

        return result, {
            'compressed': True,
            'before_tokens': before_tokens,
            'after_tokens': after_tokens,
            'before_count': before_count,
            'after_count': len(result),
            'used_llm': used_llm,
            'usage': summary_usage,
        }
