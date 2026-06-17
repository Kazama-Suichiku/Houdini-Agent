# -*- coding: utf-8 -*-
import json
import re
from typing import List, Dict, Optional, Any, Tuple


class AIClientContextMixin:
    """Context window management: trimming, compression, summarization."""

    @staticmethod
    def _paginate_result(text: str, max_lines: int = 50) -> str:
        """将工具结果按行分页，超出部分截断并附带分页提示。

        - 不超过 max_lines 行时原样返回
        - 超过时保留前 max_lines 行，并追加分页说明

        Args:
            text: 原始工具输出文本
            max_lines: 每页最大行数（默认 50）

        Returns:
            分页后的文本
        """
        if not text:
            return text
        lines = text.split('\n')
        total = len(lines)
        if total <= max_lines:
            return text
        page = '\n'.join(lines[:max_lines])
        return (
            f"{page}\n\n"
            f"[分页提示] 显示第 1-{max_lines} 行，共 {total} 行（已截断）。"
            f"当前信息如已足够请直接使用。"
            f"注意：用相同参数重复调用会得到相同结果。"
            f"如需更多信息请换用更精确的查询条件，或使用 fetch_webpage 获取特定 URL 的完整内容（支持 start_line 翻页）。"
        )

    # ------------------------------------------------------------------
    # 消息清洗：确保发送给 API 的消息格式正确
    # ------------------------------------------------------------------

    @staticmethod
    def _ensure_tool_call_ids(tool_calls: list) -> list:
        """确保每个 tool_call 都有有效的 id 字段

        代理 API（如 Duojie）有时不在第一个 chunk 提供 tool_call_id，
        导致后续 role:tool 消息的 tool_call_id 为空 → API 400 错误。
        """
        import uuid
        for tc in tool_calls:
            if not tc.get('id'):
                tc['id'] = f"call_{uuid.uuid4().hex[:24]}"
            # 确保 type 字段存在
            if not tc.get('type'):
                tc['type'] = 'function'
            # 确保 function 字段完整
            fn = tc.get('function', {})
            if not fn.get('name'):
                fn['name'] = 'unknown'
            if not fn.get('arguments', '').strip():
                fn['arguments'] = '{}'
            tc['function'] = fn
        return tool_calls

    # ----------------------------------------------------------
    # 智能摘要：提取工具结果的关键信息
    # ----------------------------------------------------------

    _PATH_RE = re.compile(r'/(?:obj|out|stage|tasks|ch|shop|img|mat|vex)/[\w/]+')
    _COUNT_RE = re.compile(r'(?:节点数量|点数量|错误数|警告数|count|total)[：:\s]*(\d+)', re.IGNORECASE)

    @classmethod
    def _summarize_tool_content(cls, content: str, max_len: int = 200) -> str:
        """智能摘要工具结果——提取关键信息而非简单截断

        提取优先级: 路径 > 数值统计 > 第一行摘要 > 截断
        """
        if not content or len(content) <= max_len:
            return content

        parts = []

        # 1. 提取节点路径
        paths = cls._PATH_RE.findall(content)
        if paths:
            unique_paths = list(dict.fromkeys(paths))[:5]  # 去重保留顺序
            parts.append("路径: " + ", ".join(unique_paths))

        # 2. 提取数量信息
        counts = cls._COUNT_RE.findall(content)
        if counts:
            parts.append("统计: " + ", ".join(counts[:4]))

        # 3. 检测成功/失败状态
        if '错误' in content[:100] or 'error' in content[:100].lower():
            # 错误信息——保留更多内容
            first_line = content.split('\n', 1)[0][:200]
            parts.append(first_line)
        elif not parts:
            # 没提取到结构化信息，保留第一行
            first_line = content.split('\n', 1)[0][:150]
            parts.append(first_line)

        summary = " | ".join(parts)
        if len(summary) > max_len:
            summary = summary[:max_len]
        return summary + '...[摘要]'

    # ----------------------------------------------------------
    # 图片内容剥离
    # ----------------------------------------------------------

    @staticmethod
    def _strip_image_content(messages: list, keep_recent_user: int = 0) -> int:
        """就地剥离消息中的 image_url 内容，将多模态 content 转为纯文本

        Args:
            messages: 消息列表（就地修改）
            keep_recent_user: 保留最近 N 条 user 消息的图片（0 = 全部剥离）

        Returns:
            剥离的图片数量
        """
        stripped = 0

        # 找出最近 N 条 user 消息的索引（从后往前）
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
            if not isinstance(content, list):
                continue
            if idx in protected_indices:
                continue

            # 多模态 content: [{"type":"text","text":"..."},{"type":"image_url",...}]
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
                if combined:
                    combined += '\n[图片已移除以节省上下文空间]'
                else:
                    combined = '[图片已移除]'
                msg['content'] = combined

        return stripped

    # ----------------------------------------------------------
    # 渐进式裁剪
    # ----------------------------------------------------------

    def _progressive_trim(self, working_messages: list, tool_calls_history: list,
                          trim_level: int = 1, supports_vision: bool = True) -> list:
        """渐进式裁剪上下文，根据 trim_level 逐步加大裁剪力度

        Cursor 风格核心原则:
        - **永不截断 user 消息的文本部分**
        - **永不截断 assistant 消息**（保留完整回复——这是 Cursor 的关键设计）
        - 只压缩 tool 结果（role='tool'）
        - 剥离旧轮次中的图片（base64 图片是 body 膨胀的主因）
        - 按「轮次」裁剪，保留最近 N 轮完整对话
        - 最早的轮次优先删除

        trim_level=1: 轻度 - 压缩旧轮 tool 结果，保留最近 70% 轮次，剥离旧轮图片
        trim_level=2: 中度 - 保留最近 3 轮，较短的 tool 摘要，剥离所有旧图片
        trim_level=3+: 重度 - 保留最近 2 轮，激进压缩 tool 结果，剥离全部图片
        """
        if not working_messages:
            return working_messages

        # ── 第 0 步：剥离图片（base64 图片是 413 的主因）──
        if not supports_vision or trim_level >= 3:
            # 非视觉模型 或 重度裁剪：剥离所有图片
            n_stripped = self._strip_image_content(working_messages, keep_recent_user=0)
        elif trim_level == 2:
            # 中度裁剪：只保留最近 1 条 user 消息的图片
            n_stripped = self._strip_image_content(working_messages, keep_recent_user=1)
        else:
            # 轻度裁剪：保留最近 2 条 user 消息的图片
            n_stripped = self._strip_image_content(working_messages, keep_recent_user=2)

        if n_stripped > 0:
            print(f"[AI Client] 裁剪: 剥离了 {n_stripped} 张图片")

        sys_msg = working_messages[0] if working_messages[0].get('role') == 'system' else None
        body = working_messages[1:] if sys_msg else working_messages[:]

        if not body:
            return working_messages

        # --- 划分轮次：以 user 消息为分界 ---
        rounds = []  # [[msg, msg, ...], ...]
        current_round = []
        for m in body:
            if m.get('role') == 'user' and current_round:
                rounds.append(current_round)
                current_round = []
            current_round.append(m)
        if current_round:
            rounds.append(current_round)

        if trim_level <= 1:
            # 轻度：只压缩非最近 30% 轮次的 tool 结果
            n_rounds = len(rounds)
            protect_n = max(3, int(n_rounds * 0.7))  # 保护最近 70%
            for r_idx, rnd in enumerate(rounds):
                if r_idx >= n_rounds - protect_n:
                    break
                for m in rnd:
                    c = m.get('content') or ''
                    if m.get('role') == 'tool' and isinstance(c, str) and len(c) > 300:
                        m['content'] = self._summarize_tool_content(c, 300)
                    # ★ assistant 和 user 文本完全保留 ★

            keep_rounds = max(5, int(n_rounds * 0.7))
            if n_rounds > keep_rounds:
                rounds = rounds[-keep_rounds:]

        elif trim_level == 2:
            # 中度：保留最近 3 轮（而非 5 轮，避免 level 1 → level 2 无效裁剪）
            rounds = rounds[-3:] if len(rounds) > 3 else rounds
            for r_idx, rnd in enumerate(rounds):
                if r_idx >= len(rounds) - 2:
                    break  # 最近 2 轮的 tool 结果不压缩
                for m in rnd:
                    c = m.get('content') or ''
                    if m.get('role') == 'tool' and isinstance(c, str) and len(c) > 150:
                        m['content'] = self._summarize_tool_content(c, 150)
                    # ★ assistant 和 user 文本完全保留 ★

        else:
            # 重度：保留最近 2 轮，激进压缩 tool 结果
            rounds = rounds[-2:] if len(rounds) > 2 else rounds
            for rnd in rounds[:-1]:  # 最后一轮不压缩
                for m in rnd:
                    c = m.get('content') or ''
                    if m.get('role') == 'tool' and isinstance(c, str) and len(c) > 100:
                        m['content'] = self._summarize_tool_content(c, 100)
                    # ★ assistant 和 user 文本完全保留 ★

        # 重组
        body = [m for rnd in rounds for m in rnd]
        result = ([sys_msg] if sys_msg else []) + body

        # 恢复提示
        history_summary = ""
        if tool_calls_history:
            op_history = [h for h in tool_calls_history
                          if h['tool_name'] not in self._QUERY_TOOLS]
            if op_history:
                recent = op_history[-8:]
                lines = []
                for h in recent:
                    r = h.get('result', {})
                    status = 'ok' if (isinstance(r, dict) and r.get('success')) else 'err'
                    r_str = str(r.get('result', '') if isinstance(r, dict) else r)[:60]
                    lines.append(f"  [{status}] {h['tool_name']}: {r_str}")
                history_summary = "\n已完成的操作:\n" + "\n".join(lines)

        result.append({
            'role': 'system',
            'content': (
                f'[上下文管理] 已自动裁剪历史（级别 {trim_level}）。'
                f'{history_summary}'
                f'\n请继续完成当前任务。不要提及此裁剪。'
            )
        })

        print(f"[AI Client] 渐进式裁剪: level={trim_level}, "
              f"消息 {len(working_messages)} → {len(result)}, "
              f"轮次 {len(rounds)}")
        return result

    def _sanitize_working_messages(self, messages: list) -> list:
        """在发送给 API 之前清洗消息列表，修复常见格式问题

        修复项：
        1. assistant 消息中 tool_calls 的 id 为空
        2. role:tool 消息的 tool_call_id 与 assistant 中的 id 不匹配
        3. 移除无效的 tool 消息（没有对应 assistant tool_call）
        """
        # 收集所有有效的 tool_call_id
        valid_tc_ids = set()
        for msg in messages:
            if msg.get('role') == 'assistant' and 'tool_calls' in msg:
                self._ensure_tool_call_ids(msg['tool_calls'])
                for tc in msg['tool_calls']:
                    if tc.get('id'):
                        valid_tc_ids.add(tc['id'])

        # 修复 tool 消息的 tool_call_id
        sanitized = []
        for msg in messages:
            if msg.get('role') == 'tool':
                tc_id = msg.get('tool_call_id', '')
                if not tc_id or tc_id not in valid_tc_ids:
                    # 跳过孤儿 tool 消息（没有对应的 assistant tool_call）
                    continue
            sanitized.append(msg)
        return sanitized

    # 已自带分页的工具，不再二次截断
    _SELF_PAGED_TOOLS = frozenset({
        'get_houdini_node_doc', 'get_network_structure', 'get_node_parameters',
        'list_children', 'execute_python', 'execute_shell',
    })

    def _compress_tool_result(self, tool_name: str, result: dict) -> str:
        """统一工具结果压缩逻辑（供两种 agent loop 共用）

        策略：
        - 已自带分页的工具 → 直接返回（如 get_houdini_node_doc）
        - 查询工具 → 按行分页（默认 50 行）
        - 操作工具 → 提取路径，保留关键信息
        - 其他工具 → 适度截断
        - 失败 → 保留完整错误
        """
        # ★ Cook 耗时反馈（实时 Cook 模式）：始终追加到结果文本末尾，
        # 不受工具分类裁剪影响，确保 Agent 能看到 cook 耗时与慢 cook 警告。
        cook_note = result.get('_cook_note') if isinstance(result, dict) else None

        if result.get('success'):
            content = result.get('result', '')
            # 已自带分页逻辑的工具，直接返回不再截断
            if tool_name in self._SELF_PAGED_TOOLS:
                return content + (f"\n{cook_note}" if cook_note else "")
            if tool_name in self._QUERY_TOOLS:
                content = self._paginate_result(content, max_lines=50)
            elif tool_name in self._OP_TOOLS:
                if len(content) > 300:
                    import re
                    paths = re.findall(r'[/\w]+(?:/[\w]+)+', content)
                    if paths:
                        content = ' '.join(paths[:5])
                        if len(content) > 300:
                            content = content[:300] + '...'
                    else:
                        content = content[:300]
            else:
                # 其他工具也按行分页，但更宽松
                content = self._paginate_result(content, max_lines=80)
            if cook_note:
                content = f"{content}\n{cook_note}"
            return content
        else:
            error = result.get('error', '未知错误')
            limit = 2500 if tool_name in ('execute_python', 'execute_shell', 'run_skill') else 1200
            return error[:limit] if len(error) > limit else error

    # ----------------------------------------------------------
    # ★ 分级工具结果压缩（用于上下文压缩阶段，比 _summarize_tool_content 更智能）
    # ----------------------------------------------------------

    # 工具名 → 压缩钩子映射（tool_call_id 上的 assistant.tool_calls 保留名称信息）
    _TIERED_COMPRESS_NEVER = frozenset({'check_errors'})  # 错误信息永不压缩

    @classmethod
    def _tiered_compress_tool(cls, tool_name: str, content: str, max_len: int = 300) -> str:
        """根据工具类型做分级压缩，保留最有用的信息而非简单截断。

        与 _summarize_tool_content（通用路径/数量提取）不同，本方法针对具体工具
        定制压缩策略，在上下文管理阶段使用。
        """
        if not content or len(content) <= max_len:
            return content

        # check_errors: 永不压缩（错误消息是最重要的反馈）
        if tool_name in cls._TIERED_COMPRESS_NEVER:
            return content

        # get_network_structure: 保留节点名/类型/连接，去掉位置坐标
        if tool_name == 'get_network_structure':
            lines = content.split('\n')
            kept = []
            for line in lines:
                # 跳过纯位置信息行
                if re.match(r'\s*(位置|position|pos)\s*[:：]', line, re.IGNORECASE):
                    continue
                # 跳过空行和装饰线
                stripped = line.strip()
                if not stripped or stripped.startswith('---') or stripped.startswith('==='):
                    continue
                kept.append(line)
            result = '\n'.join(kept)
            if len(result) > max_len:
                result = result[:max_len] + '...[结构已压缩]'
            return result

        # get_node_parameters: 保留非默认/已修改参数，折叠默认值
        if tool_name == 'get_node_parameters':
            lines = content.split('\n')
            kept = []
            default_count = 0
            for line in lines:
                # 含 "默认" 或 "(default)" 的参数行被折叠
                if re.search(r'\(default\)|默认值|unchanged', line, re.IGNORECASE):
                    default_count += 1
                    continue
                kept.append(line)
            if default_count > 0:
                kept.append(f'  ...({default_count} 个默认参数已省略)')
            result = '\n'.join(kept)
            if len(result) > max_len:
                result = result[:max_len] + '...[参数已压缩]'
            return result

        # execute_python: 保留 stdout，截断 traceback（保留首行错误）
        if tool_name == 'execute_python':
            # 如果有 traceback，只保留最后的错误行
            tb_idx = content.find('Traceback (most recent call last)')
            if tb_idx >= 0:
                before_tb = content[:tb_idx].strip()
                # 提取 traceback 最后一行（实际错误描述）
                tb_lines = content[tb_idx:].strip().split('\n')
                error_line = tb_lines[-1] if tb_lines else ''
                result = before_tb
                if error_line:
                    result += f'\n[Error] {error_line}'
                if len(result) > max_len:
                    result = result[:max_len] + '...[已压缩]'
                return result
            # 无 traceback：正常截断
            if len(content) > max_len:
                return content[:max_len] + '...[输出已压缩]'
            return content

        # search_node_types / semantic_search_nodes: 保留 Top N 结果
        if tool_name in ('search_node_types', 'semantic_search_nodes'):
            lines = content.split('\n')
            # 保留前 5 个有实质内容的行
            kept = [l for l in lines if l.strip()][:5]
            total = len([l for l in lines if l.strip()])
            result = '\n'.join(kept)
            if total > 5:
                result += f'\n...共 {total} 条结果，已显示前 5 条'
            if len(result) > max_len:
                result = result[:max_len] + '...[搜索结果已压缩]'
            return result

        # web_search: 保留标题 + 摘要，丢弃 URL
        if tool_name == 'web_search':
            # 移除 URL 行（http:// 或 https:// 开头）
            lines = content.split('\n')
            kept = [l for l in lines if not re.match(r'\s*https?://', l.strip())]
            result = '\n'.join(kept)
            if len(result) > max_len:
                result = result[:max_len] + '...[搜索已压缩]'
            return result

        # 默认：使用通用智能摘要
        return cls._summarize_tool_content(content, max_len)

    # ----------------------------------------------------------
    # ★ 过时工具结果检测与标记
    # ----------------------------------------------------------

    # 可被后续同名调用"覆盖"的工具（查询类）
    _STALEABLE_TOOLS = frozenset({
        'get_network_structure', 'get_node_parameters', 'list_children',
        'check_errors', 'get_node_inputs', 'read_selection',
    })

    @classmethod
    def _mark_stale_tool_results(cls, working_messages: list) -> int:
        """检测并压缩过时的工具结果。

        当同一个查询工具以相同/重叠参数被多次调用时，早期的结果已过时
        （AI 已有更新的数据）。将早期结果替换为简短标记以节省 token。

        Returns:
            被标记为过时的工具结果数量
        """
        # 收集 tool 消息的 (tool_call_id → 工具名, 参数) 映射
        # 需要从 assistant 的 tool_calls 中提取工具名和参数
        tc_id_to_info: Dict[str, Tuple[str, str]] = {}  # tc_id → (tool_name, key_arg)
        for msg in working_messages:
            if msg.get('role') == 'assistant' and 'tool_calls' in msg:
                for tc in msg.get('tool_calls', []):
                    tc_id = tc.get('id', '')
                    fn = tc.get('function', {})
                    name = fn.get('name', '')
                    args_str = fn.get('arguments', '{}')
                    # 提取关键参数（通常是 node_path 或 network_path）
                    try:
                        args = json.loads(args_str)
                    except Exception:
                        args = {}
                    key_arg = args.get('node_path', '') or args.get('network_path', '') or args.get('box_name', '')
                    if tc_id and name:
                        tc_id_to_info[tc_id] = (name, key_arg)

        # 从后往前扫描 tool 消息，记录每个 (tool_name, key_arg) 最后出现的位置
        latest_seen: Dict[str, int] = {}  # "(tool_name):(key_arg)" → 最后出现的消息索引
        tool_msg_indices = []
        for i, msg in enumerate(working_messages):
            if msg.get('role') == 'tool':
                tc_id = msg.get('tool_call_id', '')
                info = tc_id_to_info.get(tc_id)
                if info:
                    tool_msg_indices.append((i, info[0], info[1]))

        # 反向记录最后出现位置
        for idx, tool_name, key_arg in reversed(tool_msg_indices):
            sig = f"{tool_name}:{key_arg}"
            if sig not in latest_seen:
                latest_seen[sig] = idx

        # 标记较早的重复查询为过时
        stale_count = 0
        for idx, tool_name, key_arg in tool_msg_indices:
            if tool_name not in cls._STALEABLE_TOOLS:
                continue
            sig = f"{tool_name}:{key_arg}"
            if sig in latest_seen and latest_seen[sig] != idx:
                # 此消息不是最新的 → 过时
                content = working_messages[idx].get('content', '')
                if content and not content.startswith('[Stale]'):
                    working_messages[idx]['content'] = (
                        f'[Stale] 此 {tool_name} 结果已被后续查询更新，详见最新结果。'
                    )
                    stale_count += 1

        return stale_count

    # ----------------------------------------------------------
    # ★ 主动式上下文压缩（agent_loop 内使用）
    # ----------------------------------------------------------

    @classmethod
    def _estimate_messages_tokens(cls, messages: list, tools: Optional[list] = None) -> int:
        """快速估算消息列表 + 工具定义的 token 数。

        使用启发式方法，避免每轮都调用 tiktoken（性能开销）。
        """
        total = 0
        for msg in messages:
            content = msg.get('content') or ''
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get('type') == 'text':
                        total += len(part.get('text', '')) // 3
                    elif isinstance(part, dict) and part.get('type') in ('image_url', 'image'):
                        total += 765
                    elif isinstance(part, str):
                        total += len(part) // 3
            else:
                # 快速估算：英文 ~4 chars/token, 中文 ~1.5 chars/token
                # 综合取 ~3 chars/token
                total += len(content) // 3
            # tool_calls 开销
            tcs = msg.get('tool_calls')
            if tcs:
                for tc in tcs:
                    fn = tc.get('function', {})
                    total += len(fn.get('name', '')) + len(fn.get('arguments', '')) // 3 + 8
            total += 4  # 消息格式开销

        # 工具定义 token（每个工具 ~100-200 tokens）
        if tools:
            for t in tools:
                fn = t.get('function', {})
                total += len(fn.get('description', '')) // 4
                params = fn.get('parameters', {})
                total += len(json.dumps(params)) // 4 if params else 0
                total += 30  # 函数结构开销

        return total

    def _smart_compress_in_loop(self, working_messages: list,
                                tool_calls_history: list,
                                context_limit: int,
                                supports_vision: bool = True,
                                tools: Optional[list] = None) -> list:
        """主动式上下文压缩，在 agent loop 内每轮迭代前调用。

        分层压缩策略：
        1. 标记过时工具结果 → 替换为简短标记
        2. 对旧轮次工具结果做分级压缩（按工具类型）
        3. 剥离旧轮次图片
        4. 仍超限则按轮次裁剪

        与 _progressive_trim 的区别：
        - _progressive_trim 是错误恢复（被动），本方法是主动预防
        - 本方法使用分级压缩而非简单截断
        - 本方法不会删除最近轮次的数据
        """
        if not working_messages:
            return working_messages

        target = int(context_limit * 0.75)  # 压缩目标：75% 容量

        # ── 第 1 步：标记过时的工具结果 ──
        stale_count = self._mark_stale_tool_results(working_messages)
        if stale_count > 0:
            print(f"[AI Client] 🔄 标记了 {stale_count} 个过时工具结果")

        current = self._estimate_messages_tokens(working_messages, tools)
        if current <= target:
            return working_messages

        # ── 第 2 步：剥离旧轮次图片 ──
        n_stripped = self._strip_image_content(working_messages, keep_recent_user=2)
        if n_stripped > 0:
            print(f"[AI Client] 🖼 剥离了 {n_stripped} 张旧图片")
            current = self._estimate_messages_tokens(working_messages, tools)
            if current <= target:
                return working_messages

        # ── 第 3 步：分级压缩旧轮次的工具结果 ──
        sys_msg = working_messages[0] if working_messages[0].get('role') == 'system' else None
        body = working_messages[1:] if sys_msg else working_messages[:]

        # 划分轮次（以 user 消息为分界）
        rounds: list = []
        cur_round: list = []
        for m in body:
            if m.get('role') == 'user' and cur_round:
                rounds.append(cur_round)
                cur_round = []
            cur_round.append(m)
        if cur_round:
            rounds.append(cur_round)

        n_rounds = len(rounds)
        protect_n = max(2, n_rounds // 2)  # 保护最近 50% 的轮次

        # 从最老的轮次开始，使用分级压缩
        # 先从 assistant.tool_calls 中提取工具名映射
        tc_id_to_name: Dict[str, str] = {}
        for m in body:
            if m.get('role') == 'assistant' and 'tool_calls' in m:
                for tc in m.get('tool_calls', []):
                    tc_id = tc.get('id', '')
                    fn_name = tc.get('function', {}).get('name', '')
                    if tc_id and fn_name:
                        tc_id_to_name[tc_id] = fn_name

        for r_idx in range(n_rounds - protect_n):
            for m in rounds[r_idx]:
                if m.get('role') == 'tool':
                    c = m.get('content') or ''
                    if len(c) > 200:
                        # 获取工具名（从 tool_call_id 反查）
                        tc_id = m.get('tool_call_id', '')
                        t_name = tc_id_to_name.get(tc_id, '')
                        m['content'] = self._tiered_compress_tool(t_name, c, 200)

        current = self._estimate_messages_tokens(
            ([sys_msg] if sys_msg else []) + [m for rnd in rounds for m in rnd],
            tools
        )
        if current <= target:
            body = [m for rnd in rounds for m in rnd]
            return ([sys_msg] if sys_msg else []) + body

        # ── 第 4 步：仍超限 → 尝试 LLM 摘要（如果轮次足够多） ──
        if len(rounds) >= 6:
            try:
                llm_result = self._llm_summarize_history(
                    ([sys_msg] if sys_msg else []) + [m for rnd in rounds for m in rnd],
                    tool_calls_history, int(target / 0.75),  # 传入原始 context_limit
                )
                llm_tokens = self._estimate_messages_tokens(llm_result, tools)
                if llm_tokens < current:
                    return llm_result
            except Exception as e:
                print(f"[AI Client] LLM 摘要失败，回退裁剪: {e}")

        # ── 第 5 步：仍超限 → 裁剪最老的轮次 ──
        while len(rounds) > 2 and current > target:
            rounds.pop(0)
            current = self._estimate_messages_tokens(
                ([sys_msg] if sys_msg else []) + [m for rnd in rounds for m in rnd],
                tools
            )

        body = [m for rnd in rounds for m in rnd]
        result = ([sys_msg] if sys_msg else []) + body

        # 添加裁剪提示
        n_dropped = n_rounds - len(rounds)
        if n_dropped > 0:
            # 在系统消息后插入裁剪提示
            insert_idx = 1 if sys_msg else 0
            # 附带操作历史摘要
            history_lines = []
            if tool_calls_history:
                op_history = [h for h in tool_calls_history
                              if h['tool_name'] not in self._QUERY_TOOLS]
                for h in op_history[-6:]:
                    r = h.get('result', {})
                    status = 'ok' if (isinstance(r, dict) and r.get('success')) else 'err'
                    r_str = str(r.get('result', '') if isinstance(r, dict) else r)[:50]
                    history_lines.append(f"  [{status}] {h['tool_name']}: {r_str}")

            hint = f'[Context] 已自动压缩 {n_dropped} 个早期对话轮次以保持上下文窗口。'
            if history_lines:
                hint += '\n已完成的操作:\n' + '\n'.join(history_lines)
            hint += '\n请继续当前任务，不要提及此压缩。'
            result.insert(insert_idx, {'role': 'system', 'content': hint})

        print(f"[AI Client] 🗜️ 主动压缩: {n_rounds} 轮 → {len(rounds)} 轮, "
              f"~{self._estimate_messages_tokens(result, tools)} tokens (目标 {target})")

        return result

    def _llm_summarize_history(self, working_messages: list,
                                tool_calls_history: list,
                                context_limit: int,
                                model: str = '',
                                provider: str = '') -> list:
        """使用 LLM 生成上下文摘要，替换旧轮次。

        仅在 _smart_compress_in_loop 裁剪后仍然过长时调用。
        使用廉价模型生成摘要，避免阻塞主 agent loop 过久。

        Returns:
            替换后的消息列表
        """
        try:
            from houdini_agent.utils.token_optimizer import LLMSummarizer

            # 分离系统消息和正文
            sys_msg = working_messages[0] if working_messages[0].get('role') == 'system' else None
            body = working_messages[1:] if sys_msg else working_messages[:]

            # 划分轮次
            rounds = []
            cur = []
            for m in body:
                if m.get('role') == 'user' and cur:
                    rounds.append(cur)
                    cur = []
                cur.append(m)
            if cur:
                rounds.append(cur)

            n_rounds = len(rounds)
            if n_rounds < 4:
                return working_messages  # 太少，不值得摘要

            # 摘要前半部分（保留最近 3 轮完整）
            to_summarize = rounds[:-3]
            to_keep = rounds[-3:]

            # 确定摘要模型（优先用 deepseek-v4-flash，否则用当前模型）
            summary_model = 'deepseek-v4-flash'
            summary_provider = 'deepseek'
            # 检查是否有 deepseek key
            if not self._get_api_key('deepseek'):
                summary_model = model or 'gpt-5.2'
                summary_provider = provider or 'openai'

            summary_text = LLMSummarizer.summarize_rounds(
                ai_client=self,
                rounds=to_summarize,
                model=summary_model,
                provider=summary_provider,
            )

            if not summary_text:
                print("[AI Client] LLM 摘要生成失败，使用裁剪策略")
                return working_messages

            # 构建新消息列表：系统消息 + 摘要 + 保留的最近轮次
            result = []
            if sys_msg:
                result.append(sys_msg)

            result.append({
                'role': 'system',
                'content': (
                    f'[对话历史摘要] 以下是早期 {len(to_summarize)} 轮对话的摘要，'
                    '请基于此上下文继续当前任务：\n\n' + summary_text
                )
            })

            for rnd in to_keep:
                result.extend(rnd)

            new_tokens = self._estimate_messages_tokens(result)
            print(f"[AI Client] 📝 LLM 摘要: {n_rounds} 轮 → 摘要 + {len(to_keep)} 轮, "
                  f"~{new_tokens} tokens")

            return result

        except Exception as e:
            print(f"[AI Client] LLM 摘要异常: {e}")
            return working_messages
