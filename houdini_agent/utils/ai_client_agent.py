# -*- coding: utf-8 -*-
import json
import time
import re
from typing import List, Dict, Optional, Any, Callable

from .web_searcher import WebSearcher


def _get_default_tools():
    """Lazy import to avoid circular dependency with ai_client.py"""
    from houdini_agent.utils.ai_client import HOUDINI_TOOLS
    return HOUDINI_TOOLS


class AIClientAgentMixin:
    """Agent loop: streaming and JSON-mode tool-call orchestration."""

    # 查询型工具 & 操作型工具分类（共用常量）
    _QUERY_TOOLS = frozenset({
        'get_network_structure', 'get_node_parameters',
        'list_children',
        'read_selection', 'search_node_types',
        'semantic_search_nodes', 'find_nodes_by_param', 'check_errors',
        'search_local_doc', 'get_houdini_node_doc', 'get_node_inputs',
        'execute_python', 'execute_shell', 'web_search', 'fetch_webpage',
        'run_skill', 'list_skills',
        'capture_viewport',
    })
    _OP_TOOLS = frozenset({
        'create_node', 'create_nodes_batch', 'connect_nodes',
        'set_node_parameter', 'create_wrangle_node',
    })

    # ============================================================
    # Agent Loop（流式版本）
    # ============================================================

    def agent_loop_stream(self,
                          messages: List[Dict[str, Any]],
                          model: str = 'gpt-5.2',
                          provider: str = 'openai',
                          max_iterations: int = 999,
                          temperature: float = 0.17,
                          max_tokens: Optional[int] = None,
                          enable_thinking: bool = True,
                          supports_vision: bool = True,
                          tools_override: Optional[List[dict]] = None,
                          on_content: Optional[Callable[[str], None]] = None,
                          on_thinking: Optional[Callable[[str], None]] = None,
                          on_tool_call: Optional[Callable[[str, dict], None]] = None,
                          on_tool_result: Optional[Callable[[str, dict, dict], None]] = None,
                          on_tool_args_delta: Optional[Callable[[str, str, str], None]] = None,
                          on_iteration_start: Optional[Callable[[int], None]] = None,
                          on_plan_incomplete: Optional[Callable[[], Optional[str]]] = None,
                          context_limit: int = 128000) -> Dict[str, Any]:
        """流式 Agent Loop

        Args:
            enable_thinking: 是否启用思考模式（影响原生推理模型的 thinking 参数）
            supports_vision: 模型是否支持图片输入（False 时自动剥离 image_url 内容）
            on_content: 内容回调 (content) -> None
            on_thinking: 思考回调 (content) -> None
            on_tool_call: 工具调用开始回调 (name, args) -> None
            on_tool_result: 工具结果回调 (name, args, result) -> None
            on_iteration_start: 每轮 API 请求开始时的回调 (iteration) -> None
                                用于 UI 显示 "Generating..." 等待状态
            on_plan_incomplete: Plan 未完成检测回调 () -> Optional[str]
                                当 AI 返回纯文本（无 tool_calls）时调用此回调。
                                如果 Plan 尚有未完成步骤，返回一条提醒消息字符串，
                                agent loop 会将其注入为 user 消息并继续迭代。
                                如果 Plan 已全部完成或不需要续接，返回 None。
            context_limit: 上下文 token 上限（默认 128000），用于主动压缩判断

        Returns:
            {"ok": bool, "content": str, "final_content": str,
             "new_messages": list, "tool_calls_history": list, "iterations": int}
        """
        if not self._tool_executor:
            return {'ok': False, 'error': '未设置工具执行器', 'content': '', 'tool_calls_history': [], 'iterations': 0}

        working_messages = list(messages)

        # ── 预处理：非视觉模型剥离所有 image_url 内容 ──
        if not supports_vision:
            n_stripped = self._strip_image_content(working_messages, keep_recent_user=0)
            if n_stripped > 0:
                print(f"[AI Client] 非视觉模型 ({model})：已剥离 {n_stripped} 张图片")

        initial_msg_count = len(working_messages)  # 跟踪初始消息数量，用于提取新消息链
        tool_calls_history = []
        call_records = []  # 每次 API 调用的详细记录（对齐 Cursor）
        full_content = ""
        iteration = 0

        # ★ 工具列表：支持外部覆盖（用于 Ask 模式等场景）
        # 注意：外部插件工具已在 ai_tab._run_agent 中合并到 tools_override，
        # 此处不再重复合并，避免工具重复。
        effective_tools = tools_override if tools_override is not None else _get_default_tools()

        # 累积 usage 统计（用于 cache 命中率统计）
        total_usage = {
            'prompt_tokens': 0,
            'completion_tokens': 0,
            'reasoning_tokens': 0,
            'total_tokens': 0,
            'cache_hit_tokens': 0,
            'cache_miss_tokens': 0,
        }

        # 防止死循环：检测重复工具调用
        recent_tool_signatures = []  # 最近的工具调用签名
        max_tool_calls = 999  # 不限制总调用次数（仅保留连续重复检测）
        total_tool_calls = 0
        consecutive_same_calls = 0  # 连续相同调用计数
        last_call_signature = None
        server_error_retries = 0    # 连续服务端错误重试计数
        max_server_retries = 3      # 最多重试 3 次服务端错误

        # ★ Cursor 风格：同轮去重缓存
        # 如果 AI 在同一 turn 中用相同参数调用相同工具，直接返回缓存结果
        # key: "tool_name:sorted_args_json" → value: result dict
        _turn_dedup_cache: Dict[str, dict] = {}

        # ★ 消息清洗 dirty 标志（避免每轮都 O(n) 遍历消息列表）
        _needs_sanitize = True

        while iteration < max_iterations:
            # 检查停止请求
            if self._stop_event.is_set():
                return {
                    'ok': False,
                    'error': '用户停止了请求',
                    'content': full_content,
                    'final_content': '',
                    'new_messages': working_messages[initial_msg_count:],
                    'tool_calls_history': tool_calls_history,
                    'call_records': call_records,
                    'iterations': iteration,
                    'stopped': True,
                    'usage': total_usage
                }

            iteration += 1
            _call_start = time.time()  # 记录本次 API 调用起始时间（对齐 Cursor 延迟统计）

            # 收集本轮的内容和工具调用
            round_content = ""
            round_thinking = ""
            round_tool_calls = []
            should_retry = False  # 错误恢复标志
            should_abort = False  # 不可恢复错误标志
            abort_error = ""
            _round_content_started = False  # ★ 标记本轮是否已发出首个 content chunk

            # 发送前清洗消息（仅在新增 tool 消息后才需要，避免无谓的 O(n) 遍历）
            if _needs_sanitize:
                working_messages = self._sanitize_working_messages(working_messages)
                _needs_sanitize = False

            # 诊断：仅打印消息数量摘要（完整内容通过"导出训练数据"功能获取）
            if iteration > 1:
                from collections import Counter
                role_counts = Counter(m.get('role', '?') for m in working_messages)
                summary = ', '.join(f"{r}={c}" for r, c in role_counts.items())
                print(f"[AI Client] iteration={iteration}, messages={len(working_messages)} ({summary})")

            # ★ 主动式上下文压缩（每轮迭代前，从第 4 轮开始检查）
            # 不等到 context_length_exceeded 错误才压缩，而是提前检测并压缩
            if iteration > 3 and len(working_messages) > 15:
                est_tokens = self._estimate_messages_tokens(working_messages, effective_tools)
                if est_tokens > context_limit * 0.85:
                    print(f"[AI Client] ⚠️ 上下文 ~{est_tokens} tokens（阈值 {int(context_limit * 0.85)}），启动主动压缩")
                    working_messages = self._smart_compress_in_loop(
                        working_messages, tool_calls_history,
                        context_limit, supports_vision
                    )
                    _needs_sanitize = True

            # ★ 通知 UI 新一轮 API 请求即将开始（用于显示 "Generating..." 状态）
            if on_iteration_start:
                on_iteration_start(iteration)

            # ★ Hook: on_before_request — 允许插件修改 messages
            try:
                from .hooks import get_hook_manager as _ghm
                working_messages = _ghm().fire_filter(
                    'on_before_request', working_messages,
                    model=model, provider=provider, iteration=iteration)
            except Exception:
                pass

            # 流式请求
            for chunk in self.chat_stream(
                messages=working_messages,
                model=model,
                provider=provider,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=effective_tools,
                tool_choice='auto',
                enable_thinking=enable_thinking
            ):
                # 检查停止请求
                if self._stop_event.is_set():
                    return {
                        'ok': False,
                        'error': '用户停止了请求',
                        'content': full_content + round_content,
                        'final_content': round_content,
                        'new_messages': working_messages[initial_msg_count:],
                        'tool_calls_history': tool_calls_history,
                        'call_records': call_records,
                        'iterations': iteration,
                        'stopped': True,
                        'usage': total_usage
                    }

                chunk_type = chunk.get('type')

                if chunk_type == 'stopped':
                    return {
                        'ok': False,
                        'error': '用户停止了请求',
                        'content': full_content + round_content,
                        'final_content': round_content,
                        'new_messages': working_messages[initial_msg_count:],
                        'tool_calls_history': tool_calls_history,
                        'call_records': call_records,
                        'iterations': iteration,
                        'stopped': True,
                        'usage': total_usage
                    }

                if chunk_type == 'content':
                    content = chunk.get('content', '')
                    # 清理XML标签（使用预编译正则，避免每 chunk 重复编译）
                    cleaned_chunk = content
                    for _pat in self._RE_CLEAN_PATTERNS:
                        cleaned_chunk = _pat.sub('', cleaned_chunk)

                    # ★ 修复多轮 iteration 内容粘连：
                    # 如果上一轮已有 content（full_content 非空），且本轮是首个 content chunk，
                    # 自动注入 \n\n 段落分隔符，避免 AI 跨 iteration 的文字粘在一起
                    if cleaned_chunk and not _round_content_started and full_content:
                        # 检查 full_content 末尾是否已有足够换行
                        if not full_content.endswith('\n\n'):
                            sep = '\n\n' if not full_content.endswith('\n') else '\n'
                            round_content += sep
                            if on_content:
                                on_content(sep)
                        _round_content_started = True
                    elif cleaned_chunk:
                        _round_content_started = True

                    round_content += cleaned_chunk
                    if on_content and cleaned_chunk:
                        on_content(cleaned_chunk)
                    # ★ Hook: on_content_chunk — 插件实时过滤/转换内容
                    if cleaned_chunk:
                        try:
                            from .hooks import get_hook_manager as _ghm
                            _ghm().fire('on_content_chunk', content=cleaned_chunk, iteration=iteration)
                        except Exception:
                            pass

                elif chunk_type == 'thinking':
                    thinking_text = chunk.get('content', '')
                    round_thinking += thinking_text
                    if on_thinking and thinking_text:
                        on_thinking(thinking_text)

                elif chunk_type == 'tool_args_delta':
                    if on_tool_args_delta:
                        on_tool_args_delta(
                            chunk.get('name', ''),
                            chunk.get('delta', ''),
                            chunk.get('accumulated', ''),
                        )

                elif chunk_type == 'tool_call':
                    tc = chunk.get('tool_call')
                    print(f"[AI Client] Tool call: {tc.get('function', {}).get('name', 'unknown')}")
                    round_tool_calls.append(tc)

                elif chunk_type == 'error':
                    error_msg = chunk.get('error', '')
                    error_lower = error_msg.lower()
                    print(f"[AI Client] Agent loop error at iteration {iteration}: {error_msg}")

                    # ---- 精确分类错误类型 ----
                    # 1. 真正的上下文超限（API 明确告知 token 超限）
                    is_context_exceeded = any(k in error_lower for k in (
                        'context_length_exceeded', 'maximum context length',
                        'max_tokens', 'token limit', 'too many tokens',
                        'request too large', 'payload too large',
                        'context window', 'input too long',
                    )) or ('HTTP 413' in error_msg)

                    # 2. 临时服务器错误 / 连接中断（502/503/529 / InvalidChunkLength 等）
                    is_server_transient = any(k in error_msg for k in (
                        'HTTP 502', 'HTTP 503', 'HTTP 529', 'no available',
                        'InvalidChunkLength', 'ChunkedEncodingError',
                        'Connection broken', 'IncompleteRead',
                        'ConnectionReset', 'RemoteDisconnected',
                        '连接错误', '连接中断',
                    ))

                    # 3. 压缩/格式问题
                    is_format_error = ('HTTP 4' in error_msg and not is_context_exceeded and iteration > 1)
                    is_compress_fail = '压缩失败' in error_msg

                    is_recoverable = is_context_exceeded or is_server_transient or is_format_error or is_compress_fail

                    if is_recoverable:
                        server_error_retries += 1

                        # 超过最大重试次数 → 停止
                        if server_error_retries > max_server_retries:
                            print(f"[AI Client] 错误已重试 {max_server_retries} 次，放弃")
                            if on_content:
                                on_content(f"\n[连续出错 {max_server_retries} 次，已停止重试。请稍后再试。]\n")
                            should_abort = True
                            abort_error = f"连续出错 {max_server_retries} 次: {error_msg}"
                            break

                        cleanup_count = 0

                        if is_context_exceeded:
                            # ---- 真正的上下文超限：渐进式裁剪 ----
                            print(f"[AI Client] 上下文超限，进行渐进式裁剪 (第{server_error_retries}次)")
                            if on_content:
                                on_content(f"\n[上下文超限，正在智能裁剪后重试 ({server_error_retries}/{max_server_retries})...]\n")
                            old_len = len(working_messages)
                            working_messages = self._progressive_trim(
                                working_messages, tool_calls_history,
                                trim_level=server_error_retries,  # 逐次加大裁剪力度
                                supports_vision=supports_vision
                            )
                            cleanup_count = old_len - len(working_messages)

                        elif is_server_transient or is_compress_fail:
                            # ---- 临时服务器错误：先等待重试，不急着裁剪 ----
                            wait_seconds = 5 * server_error_retries
                            if on_content:
                                on_content(f"\n[服务端暂时不可用，{wait_seconds}秒后重试 ({server_error_retries}/{max_server_retries})...]\n")
                            time.sleep(wait_seconds)

                            # 只在第2次及以后重试时才裁剪（第1次纯等待重试，给服务器恢复机会）
                            if server_error_retries >= 2:
                                print(f"[AI Client] 服务端连续出错，尝试轻度裁剪上下文")
                                old_len = len(working_messages)
                                working_messages = self._progressive_trim(
                                    working_messages, tool_calls_history,
                                    trim_level=server_error_retries - 1,  # 比上下文超限更温和
                                    supports_vision=supports_vision
                                )
                                cleanup_count = old_len - len(working_messages)

                        else:
                            # ---- 4xx 格式问题 → 移除末尾可能有问题的消息 ----
                            # 先剥离尾部注入的多模态 user 消息（capture_viewport 回注的图片块）
                            # 这类消息不在 tool/assistant 清理范围内，但格式错误会导致连续 400
                            while (working_messages and cleanup_count < 5 and
                                   working_messages[-1].get('role') == 'user' and
                                   working_messages[-1] is not messages[0] and
                                   isinstance(working_messages[-1].get('content'), list) and
                                   any(p.get('type') in ('image_url', 'image')
                                       for p in working_messages[-1]['content']
                                       if isinstance(p, dict))):
                                working_messages.pop()
                                cleanup_count += 1
                            while (working_messages and cleanup_count < 20 and
                                   working_messages[-1].get('role') in ('tool', 'system')
                                   and working_messages[-1] is not messages[0]):
                                working_messages.pop()
                                cleanup_count += 1
                            if working_messages and working_messages[-1].get('role') == 'assistant':
                                working_messages.pop()
                                cleanup_count += 1

                        print(f"[AI Client] 重试 {server_error_retries}/{max_server_retries}, 移除了 {cleanup_count} 条消息")
                        should_retry = True
                        break  # 退出 for 循环，回到 while 循环重试

                    # 无法恢复
                    should_abort = True
                    abort_error = error_msg
                    break  # 退出 for 循环

                elif chunk_type == 'done':
                    # 成功收到响应 → 重置服务端错误重试计数
                    server_error_retries = 0
                    # 收集 usage 信息（包含 cache 统计）
                    usage = chunk.get('usage', {})
                    if usage:
                        total_usage['prompt_tokens'] += usage.get('prompt_tokens', 0)
                        total_usage['completion_tokens'] += usage.get('completion_tokens', 0)
                        total_usage['reasoning_tokens'] += usage.get('reasoning_tokens', 0)
                        total_usage['total_tokens'] += usage.get('total_tokens', 0)
                        total_usage['cache_hit_tokens'] += usage.get('cache_hit_tokens', 0)
                        total_usage['cache_miss_tokens'] += usage.get('cache_miss_tokens', 0)

                    # ---- 记录本次 API 调用详情（对齐 Cursor） ----
                    import datetime as _dt
                    _call_latency = time.time() - _call_start
                    _rec_inp = usage.get('prompt_tokens', 0)
                    _rec_out = usage.get('completion_tokens', 0)
                    _rec_reason = usage.get('reasoning_tokens', 0)
                    _rec_chit = usage.get('cache_hit_tokens', 0)
                    _rec_cmiss = usage.get('cache_miss_tokens', 0)
                    try:
                        from houdini_agent.utils.token_optimizer import calculate_cost as _calc_cost
                        _rec_cost = _calc_cost(model, _rec_inp, _rec_out, _rec_chit, _rec_cmiss, _rec_reason)
                    except Exception:
                        _rec_cost = 0.0
                    call_records.append({
                        'timestamp': _dt.datetime.now().isoformat(),
                        'model': model,
                        'iteration': iteration,
                        'input_tokens': _rec_inp,
                        'output_tokens': _rec_out,
                        'reasoning_tokens': _rec_reason,
                        'cache_hit': _rec_chit,
                        'cache_miss': _rec_cmiss,
                        'total_tokens': usage.get('total_tokens', 0),
                        'latency': round(_call_latency, 2),
                        'has_tool_calls': len(round_tool_calls) > 0,
                        'estimated_cost': _rec_cost,
                    })
                    break

            # 错误恢复：跳过本轮剩余逻辑，重新请求 API
            if should_retry:
                full_content += round_content
                continue  # 正确地重新进入 while 循环

            # 不可恢复错误：返回
            if should_abort:
                return {
                    'ok': False,
                    'error': abort_error,
                    'content': full_content,
                    'final_content': '',
                    'new_messages': working_messages[initial_msg_count:],
                    'tool_calls_history': tool_calls_history,
                    'call_records': call_records,
                    'iterations': iteration,
                    'usage': total_usage
                }

            # 如果没有工具调用，完成
            if not round_tool_calls:
                # ★ Plan 续接检测：AI 输出了纯文本，但 Plan 可能还有未完成步骤
                # 通过回调询问 UI 层 Plan 是否已完成
                _plan_resume_msg = None
                if on_plan_incomplete and iteration > 1:
                    try:
                        _plan_resume_msg = on_plan_incomplete()
                    except Exception as _pe:
                        print(f"[AI Client] on_plan_incomplete error: {_pe}")

                if _plan_resume_msg:
                    # Plan 尚未完成 → 将 AI 的当前回复存入历史，注入提醒消息，继续循环
                    print(f"[AI Client] ★ Plan 续接：AI 提前终止，注入提醒消息继续执行")
                    full_content += round_content

                    # 1. 将 AI 的纯文本回复作为 assistant 消息存入 working_messages
                    _assistant_msg = {'role': 'assistant', 'content': round_content or ''}
                    if round_thinking:
                        _assistant_msg['reasoning_content'] = round_thinking
                    working_messages.append(_assistant_msg)

                    # 2. 注入 "Plan 未完成" 的提醒消息作为 user 消息
                    working_messages.append({'role': 'user', 'content': _plan_resume_msg})
                    _needs_sanitize = True
                    _round_content_started = False
                    continue  # 继续 while 循环，开始新一轮 API 请求

                full_content += round_content
                # 计算 cache 命中率
                prompt_total = total_usage['cache_hit_tokens'] + total_usage['cache_miss_tokens']
                if prompt_total > 0:
                    total_usage['cache_hit_rate'] = total_usage['cache_hit_tokens'] / prompt_total
                else:
                    total_usage['cache_hit_rate'] = 0

                _result = {
                    'ok': True,
                    'content': full_content,
                    'final_content': round_content,  # 最后一轮的回复（不含中间轮次）
                    'new_messages': working_messages[initial_msg_count:],  # 原生工具交互链
                    'tool_calls_history': tool_calls_history,
                    'call_records': call_records,
                    'iterations': iteration,
                    'usage': total_usage
                }

                # ★ Hook: on_after_response — 通知插件 Agent Loop 结束
                try:
                    from .hooks import get_hook_manager as _ghm
                    _ghm().fire('on_after_response',
                               result=_result, model=model, provider=provider)
                except Exception:
                    pass

                return _result

            # 添加助手消息（确保 tool_call ID 完整）
            self._ensure_tool_call_ids(round_tool_calls)

            # ★ 防御性修复：确保每个 tool_call 的 arguments 是合法 JSON
            # 某些代理（如 duojie）可能产生拼接的无效 JSON，存入历史后会导致下一轮 API 400 错误
            for _tc in round_tool_calls:
                _args_str = _tc.get('function', {}).get('arguments', '{}')
                try:
                    json.loads(_args_str)
                except (json.JSONDecodeError, ValueError):
                    # arguments 不是合法 JSON，尝试提取第一个完整 JSON 对象
                    _depth = 0
                    _start = -1
                    _fixed = None
                    for _ci, _ch in enumerate(_args_str):
                        if _ch == '{':
                            if _depth == 0:
                                _start = _ci
                            _depth += 1
                        elif _ch == '}':
                            _depth -= 1
                            if _depth == 0 and _start >= 0:
                                _candidate = _args_str[_start:_ci+1]
                                try:
                                    json.loads(_candidate)
                                    _fixed = _candidate
                                except:
                                    pass
                                break
                    _tc['function']['arguments'] = _fixed if _fixed else '{}'
                    print(f"[AI Client] 修正了无效的 tool_call arguments -> {_tc['function']['arguments'][:80]}")

            assistant_msg = {'role': 'assistant', 'tool_calls': round_tool_calls}
            # content 为空时必须传 None（null）而非空字符串
            # Claude/Anthropic 兼容代理拒绝 content="" + tool_calls 共存
            assistant_msg['content'] = round_content or None
            # reasoning_content 仅在回传消息时对 DeepSeek / 原生 GLM 有效
            # Duojie 的 reasoning_content 无需在后续请求中回传
            if self.is_reasoning_model(model) and provider in ('deepseek', 'glm'):
                assistant_msg['reasoning_content'] = round_thinking or ''
            working_messages.append(assistant_msg)

            # 执行工具调用（web 工具并行，Houdini 工具串行）
            # 预处理所有工具调用
            parsed_calls = []
            for tool_call in round_tool_calls:
                tool_id = tool_call.get('id', '')
                function = tool_call.get('function', {})
                tool_name = function.get('name', '')
                args_str = function.get('arguments', '{}')
                try:
                    arguments = json.loads(args_str)
                except:
                    arguments = {}
                parsed_calls.append((tool_id, tool_name, arguments, tool_call))

            # ★ 同轮去重：纯查询类工具用相同参数重复调用时直接返回缓存
            # 只对无副作用的查询工具去重（execute_python/run_skill/web_search 等有副作用的不去重）
            _DEDUP_TOOLS = frozenset({
                'get_network_structure', 'get_node_parameters', 'list_children',
                'read_selection', 'search_node_types', 'semantic_search_nodes',
                'find_nodes_by_param', 'check_errors', 'search_local_doc',
                'get_houdini_node_doc', 'get_node_inputs', 'list_skills',
                'perf_stop_and_report',
            })

            # 分离可并行工具（web + shell）和 Houdini 工具（需主线程串行）
            _ASYNC_TOOL_NAMES = frozenset({'web_search', 'fetch_webpage', 'execute_shell'})
            async_calls = [(i, pc) for i, pc in enumerate(parsed_calls) if pc[1] in _ASYNC_TOOL_NAMES]
            houdini_calls = [(i, pc) for i, pc in enumerate(parsed_calls) if pc[1] not in _ASYNC_TOOL_NAMES]

            # 结果槽位：保持原始顺序
            results_ordered = [None] * len(parsed_calls)
            dedup_flags = [False] * len(parsed_calls)  # 标记哪些是缓存命中

            # --- 先检查去重缓存 ---
            for idx, (tid, tname, targs, _tc) in enumerate(parsed_calls):
                dedup_key = f"{tname}:{json.dumps(targs, sort_keys=True)}"
                if tname in _DEDUP_TOOLS and dedup_key in _turn_dedup_cache:
                    # ★ 缓存命中：直接返回之前的结果
                    results_ordered[idx] = _turn_dedup_cache[dedup_key]
                    dedup_flags[idx] = True
                    print(f"[AI Client] ♻️ 同轮去重命中: {tname}({json.dumps(targs, ensure_ascii=False)[:80]})")

            # 分离未缓存的调用
            uncached_async = [(i, pc) for i, pc in enumerate(parsed_calls)
                             if pc[1] in _ASYNC_TOOL_NAMES and not dedup_flags[i]]
            uncached_houdini = [(i, pc) for i, pc in enumerate(parsed_calls)
                               if pc[1] not in _ASYNC_TOOL_NAMES and not dedup_flags[i]]

            # --- 并行执行未缓存的 async 工具（web + shell） ---
            if len(uncached_async) > 1:
                import concurrent.futures
                def _exec_async(idx_pc):
                    idx, (tid, tname, targs, _tc) = idx_pc
                    if tname == 'web_search':
                        return idx, self._execute_web_search(targs)
                    elif tname == 'fetch_webpage':
                        return idx, self._execute_fetch_webpage(targs)
                    else:  # execute_shell
                        return idx, self._tool_executor(tname, **targs)
                with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(uncached_async))) as pool:
                    for idx, result in pool.map(_exec_async, uncached_async):
                        results_ordered[idx] = result
            elif len(uncached_async) == 1:
                idx, (tid, tname, targs, _tc) = uncached_async[0]
                if tname == 'web_search':
                    results_ordered[idx] = self._execute_web_search(targs)
                elif tname == 'fetch_webpage':
                    results_ordered[idx] = self._execute_fetch_webpage(targs)
                else:  # execute_shell
                    results_ordered[idx] = self._tool_executor(tname, **targs)

            # --- 执行未缓存的 Houdini 工具（需主线程） ---
            # ★ 只读工具批量执行：减少 N 次信号往返为 1 次
            _BATCH_READONLY = frozenset({
                'get_network_structure', 'get_node_parameters', 'list_children',
                'read_selection', 'search_node_types', 'semantic_search_nodes',
                'find_nodes_by_param', 'get_node_inputs', 'check_errors',
                'search_local_doc', 'get_houdini_node_doc', 'list_skills',
                'get_node_positions', 'list_network_boxes',
                'perf_start_profile', 'perf_stop_and_report',
            })
            # 分离只读和写入工具
            readonly_batch = [(i, pc) for i, pc in uncached_houdini if pc[1] in _BATCH_READONLY]
            mutating_calls = [(i, pc) for i, pc in uncached_houdini if pc[1] not in _BATCH_READONLY]

            # 批量执行只读工具（如果有 batch executor 且 >1 个只读调用）
            if len(readonly_batch) > 1 and self._batch_tool_executor:
                batch_input = [(tname, targs) for _, (_, tname, targs, _) in readonly_batch]
                try:
                    batch_results = self._batch_tool_executor(batch_input)
                    for (idx, _), result in zip(readonly_batch, batch_results):
                        results_ordered[idx] = result
                except Exception as e:
                    print(f"[AI Client] 批量执行失败，回退串行: {e}")
                    for idx, (tid, tname, targs, _tc) in readonly_batch:
                        results_ordered[idx] = self._tool_executor(tname, **targs)
            else:
                # 单个只读工具或无 batch executor → 串行
                for idx, (tid, tname, targs, _tc) in readonly_batch:
                    results_ordered[idx] = self._tool_executor(tname, **targs)

            # 写入工具始终串行（有副作用，顺序敏感）
            for idx, (tid, tname, targs, _tc) in mutating_calls:
                results_ordered[idx] = self._tool_executor(tname, **targs)

            # ★ 早期终止：跳过冗余查询
            # 当已执行的工具结果已提供足够信息时，跳过剩余同类查询
            _early_skip_count = 0
            if len(parsed_calls) > 2:
                # 收集已有结果中的信息
                _check_errors_paths = set()
                _empty_network_paths = set()
                for idx, (_, tname, targs, _) in enumerate(parsed_calls):
                    if results_ordered[idx] is None:
                        continue
                    result = results_ordered[idx]
                    # check_errors 发现错误 → 同路径的 get_node_parameters 不再需要
                    if tname == 'check_errors' and result.get('success'):
                        r_text = result.get('result', '')
                        if '错误' in r_text or 'error' in r_text.lower():
                            path = targs.get('node_path', '')
                            if path:
                                _check_errors_paths.add(path)
                    # get_network_structure 返回空 → 同路径的子查询不需要
                    if tname == 'get_network_structure' and result.get('success'):
                        r_text = result.get('result', '')
                        if '节点数量: 0' in r_text or 'Nodes: 0' in r_text or not r_text.strip():
                            path = targs.get('network_path', '') or targs.get('node_path', '')
                            if path:
                                _empty_network_paths.add(path)

                # 标记可跳过的工具（仅对尚未执行的 readonly 调用）
                for idx, (tid, tname, targs, _tc) in enumerate(parsed_calls):
                    if results_ordered[idx] is not None:
                        continue  # 已有结果
                    path = targs.get('node_path', '') or targs.get('network_path', '')
                    # 规则 1：check_errors 已发现错误 → 跳过同路径的 get_node_parameters
                    if tname == 'get_node_parameters' and path in _check_errors_paths:
                        results_ordered[idx] = {
                            "success": True,
                            "result": f"[已跳过] {path} 已有错误信息，请先修复错误。"
                        }
                        _early_skip_count += 1
                    # 规则 2：网络为空 → 跳过 list_children / get_node_parameters
                    elif tname in ('list_children', 'get_node_parameters') and path in _empty_network_paths:
                        results_ordered[idx] = {
                            "success": True,
                            "result": f"[已跳过] {path} 网络为空，无子节点。"
                        }
                        _early_skip_count += 1
                if _early_skip_count > 0:
                    print(f"[AI Client] ⏭️ 早期终止: 跳过 {_early_skip_count} 个冗余查询")

            # --- 缓存维护 ---
            # 如果本轮有操作类工具（创建/删除/连接节点等），清除网络结构相关缓存
            # 因为操作改变了网络状态，之前缓存的查询结果可能已过期
            _NETWORK_MUTATING_TOOLS = frozenset({
                'create_node', 'create_nodes_batch', 'delete_node', 'connect_nodes',
                'create_wrangle_node', 'copy_node', 'set_display_flag', 'undo_redo',
            })
            has_mutation = any(
                pc[1] in _NETWORK_MUTATING_TOOLS
                for idx_m, pc in enumerate(parsed_calls)
                if not dedup_flags[idx_m]
            )
            if has_mutation:
                # 清除 get_network_structure / list_children / check_errors 的缓存
                keys_to_remove = [k for k in _turn_dedup_cache
                                  if k.startswith(('get_network_structure:', 'list_children:', 'check_errors:'))]
                for k in keys_to_remove:
                    del _turn_dedup_cache[k]

            # 将新执行的查询工具结果写入去重缓存
            for idx, (tid, tname, targs, _tc) in enumerate(parsed_calls):
                if not dedup_flags[idx] and tname in _DEDUP_TOOLS and results_ordered[idx]:
                    dedup_key = f"{tname}:{json.dumps(targs, sort_keys=True)}"
                    _turn_dedup_cache[dedup_key] = results_ordered[idx]

            # --- 统一处理结果（保持原始顺序） ---
            should_break_tool_limit = False
            for i, (tool_id, tool_name, arguments, _tc) in enumerate(parsed_calls):
                result = results_ordered[i]

                # 防止死循环：检测重复工具调用
                total_tool_calls += 1
                call_signature = f"{tool_name}:{json.dumps(arguments, sort_keys=True)}"

                if total_tool_calls > max_tool_calls:
                    print(f"[AI Client] ⚠️ 达到最大工具调用次数限制 ({max_tool_calls})")
                    should_break_tool_limit = True
                    break

                if call_signature == last_call_signature:
                    consecutive_same_calls += 1
                else:
                    consecutive_same_calls = 1
                    last_call_signature = call_signature

                # 回调
                if on_tool_call:
                    on_tool_call(tool_name, arguments)

                tool_calls_history.append({
                    'tool_name': tool_name,
                    'arguments': arguments,
                    'result': result
                })

                if on_tool_result:
                    on_tool_result(tool_name, arguments, result)

                result_content = self._compress_tool_result(tool_name, result)

                # ★ 去重命中时追加提示，引导 AI 不要再重复调用
                if dedup_flags[i]:
                    result_content = f"[缓存] 本轮已用相同参数调用过此工具，以下是之前的结果（无需再次调用）:\n{result_content}"

                working_messages.append({
                    'role': 'tool',
                    'tool_call_id': tool_id,
                    'content': result_content
                })
                _needs_sanitize = True  # 新增 tool 消息，下轮需要清洗

                # ★ 视口截图注入：如果工具返回了 _viewport_image，
                # 追加一条包含图片的 user 消息，让模型可以视觉分析
                if supports_vision and result.get('_viewport_image'):
                    _img_b64 = result['_viewport_image']
                    _img_mt = result.get('_image_media_type', 'image/jpeg')
                    _use_anth = self._is_anthropic_protocol(provider, model)
                    working_messages.append({
                        'role': 'user',
                        'content': [
                            {"type": "text", "text": "[viewport snapshot attached — please analyze the current viewport state, check for visual issues or confirm the result is correct]"},
                            self._build_image_block(_img_b64, _img_mt, _use_anth)
                        ]
                    })
                    print(f"[AI Client] 📸 视口截图已注入消息 ({len(_img_b64)//1024}KB, {'anthropic' if _use_anth else 'openai'} format)")

            if should_break_tool_limit:
                return {
                    'ok': True,
                    'content': full_content + f"\n\n已达到工具调用次数限制({max_tool_calls})，自动停止。",
                    'final_content': f"\n\n已达到工具调用次数限制({max_tool_calls})，自动停止。",
                    'new_messages': working_messages[initial_msg_count:],
                    'tool_calls_history': tool_calls_history,
                    'call_records': call_records,
                    'iterations': iteration,
                    'usage': total_usage
                }

            # 多轮思考引导：在最后一条工具结果后附加提示
            # 检测本轮是否有工具调用失败
            _round_failed = False
            for _ri, (_tid, _tn, _ta, _tc) in enumerate(parsed_calls):
                if not results_ordered[_ri].get('success'):
                    _round_failed = True
                    break

            if working_messages and working_messages[-1].get('role') == 'tool':
                if _round_failed:
                    working_messages[-1]['content'] += (
                        '\n\n[注意：上述工具调用返回了错误，这是工具调用层面的参数或执行错误，'
                        '不是Houdini节点cooking错误，无需调用check_errors。'
                        '请直接根据错误信息修正参数后重新调用该工具。]'
                    )
                if enable_thinking:
                    working_messages[-1]['content'] += (
                        '\n\n[重要：你的下一条回复必须以 <think> 标签开头。'
                        '在标签内分析以上执行结果和当前进度，'
                        '检查 Todo 列表中哪些步骤已完成（用 update_todo 标记为 done），'
                        '确认下一步计划后再继续执行。不要跳过 <think> 标签。]'
                    )

            # 保存当前轮次的内容
            full_content += round_content

        # 如果循环结束但内容为空，且有工具调用历史，强制要求生成总结
        if not full_content.strip() and tool_calls_history:
            print("[AI Client] ⚠️ Stream模式：工具调用完成但无回复内容，强制要求生成总结")
            # 最后一次请求，强制要求总结
            working_messages.append({
                'role': 'user',
                'content': '请生成最终总结，说明已完成的操作和结果。'
            })

            # 再次请求生成总结
            summary_content = ""
            for chunk in self.chat_stream(
                messages=working_messages,
                model=model,
                provider=provider,
                temperature=temperature,
                max_tokens=max_tokens or 500,  # 限制总结长度
                tools=None,  # 总结阶段不需要工具
                tool_choice=None
            ):
                if chunk.get('type') == 'content':
                    content = chunk.get('content', '')
                    summary_content += content
                    if on_content:
                        on_content(content)
                elif chunk.get('type') == 'done':
                    break

            full_content = summary_content if summary_content else full_content

        print(f"[AI Client] Reached max iterations ({iteration})")
        # 计算 cache 命中率
        prompt_total = total_usage['cache_hit_tokens'] + total_usage['cache_miss_tokens']
        if prompt_total > 0:
            total_usage['cache_hit_rate'] = total_usage['cache_hit_tokens'] / prompt_total
        else:
            total_usage['cache_hit_rate'] = 0
        return {
            'ok': True,
            'content': full_content if full_content.strip() else "(工具调用完成，但未生成回复)",
            'final_content': '',  # max iterations 时无明确的最终回复
            'new_messages': working_messages[initial_msg_count:],
            'tool_calls_history': tool_calls_history,
            'call_records': call_records,
            'iterations': iteration,
            'usage': total_usage
        }

    def _execute_web_search(self, arguments: dict) -> dict:
        """执行网络搜索（通用：天气/新闻/文档/任何话题）"""
        query = arguments.get('query', '')
        max_results = arguments.get('max_results', 5)

        if not query:
            return {"success": False, "error": "缺少搜索关键词"}

        result = self._web_searcher.search(query, max_results)

        if result.get('success'):
            items = result.get('results', [])
            if not items:
                return {"success": True, "result": f"搜索 '{query}' 未找到结果。可尝试换用不同关键词。"}

            # 格式化结果：标题 + URL + 摘要
            lines = [f"搜索 '{query}' 的结果（来源: {result.get('source', 'Unknown')}，共 {len(items)} 条）：\n"]
            for i, item in enumerate(items, 1):
                lines.append(f"{i}. {item.get('title', '无标题')}")
                lines.append(f"   URL: {item.get('url', '')}")
                snippet = item.get('snippet', '')
                if snippet:
                    lines.append(f"   摘要: {snippet[:300]}")
                lines.append("")

            lines.append("提示: 如需查看详细内容，请用 fetch_webpage(url=...) 获取网页正文。引用信息时务必在段落末标注 [来源: 标题](URL)。请勿用相同关键词重复搜索。")

            return {"success": True, "result": "\n".join(lines)}
        else:
            return {"success": False, "error": result.get('error', '搜索失败')}

    def _execute_fetch_webpage(self, arguments: dict) -> dict:
        """获取网页内容（分页返回，支持翻页）"""
        url = arguments.get('url', '')
        start_line = arguments.get('start_line', 1)

        if not url:
            return {"success": False, "error": "缺少 URL"}

        # 确保 start_line 合法
        try:
            start_line = max(1, int(start_line))
        except (TypeError, ValueError):
            start_line = 1

        result = self._web_searcher.fetch_page_content(url, max_lines=80, start_line=start_line)

        if result.get('success'):
            content = result.get('content', '')
            return {"success": True, "result": f"网页正文（{url}）：\n\n{content}"}
        else:
            return {"success": False, "error": result.get('error', '获取失败')}

    # 保持兼容性
    def agent_loop(self, *args, **kwargs):
        """兼容旧接口"""
        return self.agent_loop_stream(*args, **kwargs)

    # ============================================================
    # JSON 解析模式（用于不支持 Function Calling 的模型）
    # ============================================================

    def _supports_function_calling(self, provider: str, model: str) -> bool:
        """检查模型是否支持原生 Function Calling"""
        # Ollama 模型默认不支持
        if provider == 'ollama':
            return False
        # Custom provider 根据用户配置决定
        if provider == 'custom':
            return self._CUSTOM_SUPPORTS_FC
        # 其他云端模型都支持
        return True

    def _get_json_mode_system_prompt(self, tools_list: Optional[List[dict]] = None) -> str:
        """获取 JSON 模式的系统提示（执行器模式）"""
        # 构建工具列表说明
        tool_descriptions = []
        for tool in (tools_list or _get_default_tools()):
            func = tool['function']
            params = func.get('parameters', {}).get('properties', {})
            required = func.get('parameters', {}).get('required', [])

            param_desc = []
            for pname, pinfo in params.items():
                req_mark = "(必填)" if pname in required else "(可选)"
                param_desc.append(f"    - {pname} {req_mark}: {pinfo.get('description', '')}")

            tool_descriptions.append(f"""
**{func['name']}** - {func['description']}
参数:
{chr(10).join(param_desc) if param_desc else '    无'}
""")

        return f"""你是Houdini执行器。只执行，不思考，不解释。

严格禁止（违反会浪费token）:
-禁止生成任何思考过程、推理步骤、分析过程
-禁止说明"为什么"、"让我先"、"我需要"
-禁止逐步说明、分步解释
-禁止输出任何非执行性内容

只允许:
-直接调用工具执行操作
-直接给出执行结果(1句以内)
-不输出任何思考内容

节点路径输出规范:
-回复中提及节点时必须写完整绝对路径(如/obj/geo1/box1),不能只写节点名(如box1)
-路径会自动变为可点击链接,用户可直接跳转到对应节点

工具调用参数规范（最高优先级）:
-调用前必须确认所有(必填)参数都已填写,缺少必填参数会导致调用失败
-node_path必须用完整绝对路径(如"/obj/geo1/box1"),不能只写节点名
-参数值类型必须正确:string/number/boolean/array,不要混用
-工具返回"缺少参数"错误时,直接修正参数重试,不要调用check_errors
-每次调用都要完整填写所有必填参数,不要假设系统记住上次参数

安全操作规则（必须遵守）:
-首次了解网络时调用get_network_structure,已查询过的网络不要重复调用(系统缓存同轮查询结果)
-设置参数前必须先用get_node_parameters查询正确的参数名和类型,不要猜测参数名
-execute_python中必须检查None:node=hou.node(path);if node:...
-创建节点后用返回的路径操作,不要猜测路径
-连接节点前确认两个节点都已存在

完成前必须检查（任务结束前强制执行）:
-调用verify_and_summarize自动检测(已内置网络检查,不需先调get_network_structure)
-如有问题修复后重新调用verify_and_summarize直到通过

## 工具调用格式

```json
{{"tool": "工具名称", "args": {{"参数名": "参数值"}}}}
```

规则:
1.每次只调用一个工具
2.工具调用在独立JSON代码块中
3.调用后等待结果再继续
4.不解释，直接执行
5.先查询确认再操作
6.调用前检查所有(必填)参数是否已填写,不要遗漏node_path等必填参数
7.node_path必须写完整绝对路径(如"/obj/geo1/box1"),不能只写节点名

## 可用工具

{chr(10).join(tool_descriptions)}

## 示例

创建节点（不解释，直接执行）:
```json
{{"tool": "create_node", "args": {{"node_type": "box"}}}}
```
"""

    def _parse_json_tool_calls(self, content: str) -> List[Dict]:
        """从文本内容中解析 JSON 格式的工具调用（改进版：支持多种格式）"""
        import re

        tool_calls = []

        # 1. 清理XML标签（如果AI错误输出了XML格式）
        content = re.sub(r'</?tool_call[^>]*>', '', content)
        content = re.sub(r'<arg_key>([^<]+)</arg_key>\s*<arg_value>([^<]+)</arg_value>', r'"\1": "\2"', content)

        # 2. 匹配 ```json ... ``` 代码块
        json_blocks = re.findall(r'```(?:json)?\s*\n?({[^`]+})\s*\n?```', content, re.DOTALL)

        # 3. 如果没有代码块，尝试直接匹配JSON对象
        if not json_blocks:
            # 尝试匹配独立的JSON对象（不在代码块中）
            json_pattern = r'\{\s*"(?:tool|name)"\s*:\s*"[^"]+"\s*,\s*"(?:args|arguments)"\s*:\s*\{[^}]+\}\s*\}'
            json_blocks = re.findall(json_pattern, content, re.DOTALL)

        for block in json_blocks:
            try:
                # 清理可能的格式问题
                block = block.strip()
                # 修复常见的JSON格式错误
                block = re.sub(r',\s*}', '}', block)  # 移除末尾多余逗号
                block = re.sub(r',\s*]', ']', block)  # 移除数组末尾多余逗号

                data = json.loads(block)
                if 'tool' in data:
                    tool_calls.append({
                        'name': data['tool'],
                        'arguments': data.get('args', data.get('arguments', {}))
                    })
                elif 'name' in data:
                    # 兼容 {"name": "xxx", "arguments": {...}} 格式
                    tool_calls.append({
                        'name': data['name'],
                        'arguments': data.get('arguments', data.get('args', {}))
                    })
            except (json.JSONDecodeError, KeyError) as e:
                # 记录解析失败但不中断
                print(f"[AI Client] JSON解析失败: {e}, 内容: {block[:100]}")
                continue

        return tool_calls

    def agent_loop_json_mode(self,
                              messages: List[Dict[str, Any]],
                              model: str = 'qwen2.5:14b',
                              provider: str = 'ollama',
                              max_iterations: int = 999,
                              temperature: float = 0.17,
                              max_tokens: Optional[int] = None,
                              enable_thinking: bool = True,
                              supports_vision: bool = True,
                              tools_override: Optional[List[dict]] = None,
                              on_content: Optional[Callable[[str], None]] = None,
                              on_thinking: Optional[Callable[[str], None]] = None,
                              on_tool_call: Optional[Callable[[str, dict], None]] = None,
                              on_tool_result: Optional[Callable[[str, dict, dict], None]] = None,
                              on_tool_args_delta: Optional[Callable[[str, str, str], None]] = None,
                              on_iteration_start: Optional[Callable[[int], None]] = None,
                              on_plan_incomplete: Optional[Callable[[], Optional[str]]] = None,
                              context_limit: int = 128000) -> Dict[str, Any]:
        """JSON 模式 Agent Loop（用于不支持 Function Calling 的模型）"""

        if not self._tool_executor:
            return {'ok': False, 'error': '未设置工具执行器', 'content': '', 'tool_calls_history': [], 'iterations': 0}

        # ★ 工具列表：支持外部覆盖（用于 Ask 模式等场景）
        # 注意：外部插件工具已在 ai_tab._run_agent 中合并到 tools_override，
        # 此处不再重复合并，避免工具重复。
        effective_tools = tools_override if tools_override is not None else _get_default_tools()

        # 添加 JSON 模式系统提示
        json_system_prompt = self._get_json_mode_system_prompt(effective_tools)
        working_messages = []

        # 处理消息，在第一个 system 消息后追加 JSON 模式说明
        system_found = False
        for msg in messages:
            if msg.get('role') == 'system' and not system_found:
                working_messages.append({
                    'role': 'system',
                    'content': msg.get('content', '') + '\n\n' + json_system_prompt
                })
                system_found = True
            else:
                working_messages.append(msg)

        if not system_found:
            working_messages.insert(0, {'role': 'system', 'content': json_system_prompt})

        # ── 预处理：非视觉模型剥离所有 image_url 内容 ──
        if not supports_vision:
            n_stripped = self._strip_image_content(working_messages, keep_recent_user=0)
            if n_stripped > 0:
                print(f"[AI Client] 非视觉模型 ({model})：已剥离 {n_stripped} 张图片")

        tool_calls_history = []
        call_records = []  # 每次 API 调用的详细记录（对齐 Cursor）
        full_content = ""
        iteration = 0
        self._json_thinking_buffer = ""  # 初始化思考缓冲区

        # 累积 usage 统计（用于 cache 命中率统计）
        total_usage = {
            'prompt_tokens': 0,
            'completion_tokens': 0,
            'reasoning_tokens': 0,
            'total_tokens': 0,
            'cache_hit_tokens': 0,
            'cache_miss_tokens': 0,
        }

        # 防止死循环：检测重复工具调用
        max_tool_calls = 999  # 不限制总调用次数（仅保留连续重复检测）
        total_tool_calls = 0
        consecutive_same_calls = 0
        last_call_signature = None
        server_error_retries = 0    # 连续服务端错误重试计数
        max_server_retries = 3      # 最多重试 3 次服务端错误

        while iteration < max_iterations:
            if self._stop_event.is_set():
                return {
                    'ok': False, 'error': '用户停止了请求',
                    'content': full_content, 'tool_calls_history': tool_calls_history,
                    'call_records': call_records,
                    'iterations': iteration, 'stopped': True, 'usage': total_usage
                }

            iteration += 1
            _call_start = time.time()  # 记录本次 API 调用起始时间（对齐 Cursor 延迟统计）
            round_content = ""

            # ★ 主动式上下文压缩（从第 4 轮开始检查，替代旧的简单截断逻辑）
            if iteration > 3 and len(working_messages) > 15:
                est_tokens = self._estimate_messages_tokens(working_messages, effective_tools)
                if est_tokens > context_limit * 0.85:
                    print(f"[AI Client] ⚠️ JSON模式上下文 ~{est_tokens} tokens（阈值 {int(context_limit * 0.85)}），启动主动压缩")
                    working_messages = self._smart_compress_in_loop(
                        working_messages, tool_calls_history,
                        context_limit, supports_vision
                    )
            elif iteration > 1 and len(working_messages) > 20:
                # 轻量级防御：仅在未触发主动压缩时做简单截断
                protect_start = max(1, len(working_messages) - 6)
                for i, m in enumerate(working_messages):
                    if i == 0 or i >= protect_start:
                        continue
                    role = m.get('role', '')
                    if role == 'user':
                        continue
                    c = m.get('content') or ''
                    if role == 'tool' and len(c) > 400:
                        m['content'] = self._summarize_tool_content(c, 400)
                    elif role == 'assistant' and len(c) > 600:
                        m['content'] = c[:600] + '...[已截断]'

            # ★ 通知 UI 新一轮 API 请求即将开始（用于显示 "Generating..." 状态）
            if on_iteration_start:
                on_iteration_start(iteration)

            # 流式请求（不传 tools 参数）
            for chunk in self.chat_stream(
                messages=working_messages,
                model=model,
                provider=provider,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=None,  # JSON 模式不使用原生工具
                tool_choice=None
            ):
                if self._stop_event.is_set():
                    return {
                        'ok': False, 'error': '用户停止了请求',
                        'content': full_content + round_content,
                        'tool_calls_history': tool_calls_history,
                        'call_records': call_records,
                        'iterations': iteration, 'stopped': True, 'usage': total_usage
                    }

                chunk_type = chunk.get('type')

                if chunk_type == 'content':
                    content = chunk.get('content', '')
                    round_content += content
                    if on_content:
                        on_content(content)

                elif chunk_type == 'thinking':
                    thinking_text = chunk.get('content', '')
                    if on_thinking and thinking_text:
                        on_thinking(thinking_text)

                elif chunk_type == 'error':
                    err_msg = chunk.get('error', '')
                    err_lower = err_msg.lower()

                    # 精确分类错误
                    is_context_exceeded = any(k in err_lower for k in (
                        'context_length_exceeded', 'maximum context length',
                        'max_tokens', 'token limit', 'too many tokens',
                        'request too large', 'payload too large',
                        'context window', 'input too long',
                    )) or ('HTTP 413' in err_msg)
                    is_server_transient = any(k in err_msg for k in (
                        'HTTP 502', 'HTTP 503', 'HTTP 529', '压缩失败', 'no available'
                    ))

                    if is_context_exceeded or is_server_transient:
                        server_error_retries += 1
                        if server_error_retries > max_server_retries:
                            if on_content:
                                on_content(f"\n[连续出错 {max_server_retries} 次，已停止重试。]\n")
                            return {
                                'ok': False, 'error': f"连续出错: {err_msg}",
                                'content': full_content, 'tool_calls_history': tool_calls_history,
                                'call_records': call_records,
                                'iterations': iteration, 'usage': total_usage
                            }

                        if is_context_exceeded:
                            # 上下文超限：立即裁剪
                            if on_content:
                                on_content(f"\n[上下文超限，智能裁剪后重试 ({server_error_retries}/{max_server_retries})...]\n")
                            working_messages = self._progressive_trim(
                                working_messages, tool_calls_history,
                                trim_level=server_error_retries,
                                supports_vision=supports_vision
                            )
                        else:
                            # 临时服务器错误：等待，第2次开始才裁剪
                            wait_seconds = 5 * server_error_retries
                            if on_content:
                                on_content(f"\n[服务端暂时不可用，{wait_seconds}秒后重试 ({server_error_retries}/{max_server_retries})...]\n")
                            time.sleep(wait_seconds)
                            if server_error_retries >= 2:
                                working_messages = self._progressive_trim(
                                    working_messages, tool_calls_history,
                                    trim_level=server_error_retries - 1,
                                    supports_vision=supports_vision
                                )
                        break  # 退出 for，回到 while 重试
                    return {
                        'ok': False, 'error': err_msg,
                        'content': full_content, 'tool_calls_history': tool_calls_history,
                        'call_records': call_records,
                        'iterations': iteration, 'usage': total_usage
                    }

                elif chunk_type == 'done':
                    # 成功收到响应 → 重置服务端错误重试计数
                    server_error_retries = 0
                    # 收集 usage 信息（包含 cache 统计）
                    usage = chunk.get('usage', {})
                    if usage:
                        total_usage['prompt_tokens'] += usage.get('prompt_tokens', 0)
                        total_usage['completion_tokens'] += usage.get('completion_tokens', 0)
                        total_usage['reasoning_tokens'] += usage.get('reasoning_tokens', 0)
                        total_usage['total_tokens'] += usage.get('total_tokens', 0)
                        total_usage['cache_hit_tokens'] += usage.get('cache_hit_tokens', 0)
                        total_usage['cache_miss_tokens'] += usage.get('cache_miss_tokens', 0)

                    # ---- 记录本次 API 调用详情（对齐 Cursor） ----
                    import datetime as _dt
                    _call_latency = time.time() - _call_start
                    _rec_inp = usage.get('prompt_tokens', 0)
                    _rec_out = usage.get('completion_tokens', 0)
                    _rec_reason = usage.get('reasoning_tokens', 0)
                    _rec_chit = usage.get('cache_hit_tokens', 0)
                    _rec_cmiss = usage.get('cache_miss_tokens', 0)
                    try:
                        from houdini_agent.utils.token_optimizer import calculate_cost as _calc_cost
                        _rec_cost = _calc_cost(model, _rec_inp, _rec_out, _rec_chit, _rec_cmiss, _rec_reason)
                    except Exception:
                        _rec_cost = 0.0
                    call_records.append({
                        'timestamp': _dt.datetime.now().isoformat(),
                        'model': model,
                        'iteration': iteration,
                        'input_tokens': _rec_inp,
                        'output_tokens': _rec_out,
                        'reasoning_tokens': _rec_reason,
                        'cache_hit': _rec_chit,
                        'cache_miss': _rec_cmiss,
                        'total_tokens': usage.get('total_tokens', 0),
                        'latency': round(_call_latency, 2),
                        'has_tool_calls': False,
                        'estimated_cost': _rec_cost,
                    })
                    break

            # 清理内容中的XML标签和格式问题（使用预编译正则）
            cleaned_content = round_content
            for _pat in self._RE_CLEAN_PATTERNS:
                cleaned_content = _pat.sub('', cleaned_content)
            # 清理其他可能的XML标签
            cleaned_content = re.sub(r'<[^>]+>', '', cleaned_content)  # 清理所有剩余的XML标签

            # 解析 JSON 工具调用
            tool_calls = self._parse_json_tool_calls(cleaned_content)

            # 如果没有工具调用，检查是否完成
            if not tool_calls:
                # 清理后的内容添加到full_content（只添加一次，避免重复）
                if cleaned_content.strip():
                    # 检查是否与已有内容重复（避免重复添加）
                    if cleaned_content.strip() not in full_content:
                        full_content += cleaned_content
                # 如果内容为空或只有空白，检查是否需要继续
                if not cleaned_content.strip() and tool_calls_history:
                    # 有工具调用历史但无内容，继续循环等待总结
                    continue

                # ★ Plan 续接检测（JSON 模式）
                _plan_resume_msg = None
                if on_plan_incomplete and iteration > 1:
                    try:
                        _plan_resume_msg = on_plan_incomplete()
                    except Exception as _pe:
                        print(f"[AI Client] on_plan_incomplete error (json mode): {_pe}")

                if _plan_resume_msg:
                    print(f"[AI Client] ★ Plan 续接 (JSON mode)：AI 提前终止，注入提醒消息继续执行")
                    working_messages.append({'role': 'assistant', 'content': cleaned_content or ''})
                    working_messages.append({'role': 'user', 'content': _plan_resume_msg})
                    continue

                # 计算 cache 命中率
                prompt_total = total_usage['cache_hit_tokens'] + total_usage['cache_miss_tokens']
                if prompt_total > 0:
                    total_usage['cache_hit_rate'] = total_usage['cache_hit_tokens'] / prompt_total
                else:
                    total_usage['cache_hit_rate'] = 0
                return {
                    'ok': True,
                    'content': full_content,
                    'tool_calls_history': tool_calls_history,
                    'call_records': call_records,
                    'iterations': iteration,
                    'usage': total_usage
                }

            # 添加助手消息（使用清理后的内容，但不要重复添加到full_content）
            json_assistant_msg = {'role': 'assistant', 'content': cleaned_content}
            # reasoning_content 仅在回传时对 DeepSeek / 原生 GLM 有效（Duojie 无需回传）
            if self.is_reasoning_model(model) and provider in ('deepseek', 'glm'):
                json_assistant_msg['reasoning_content'] = ''
            working_messages.append(json_assistant_msg)

            # 执行工具调用（web 工具并行，Houdini 工具串行）
            tool_results = []

            _ASYNC_TOOL_NAMES_JSON = frozenset({'web_search', 'fetch_webpage', 'execute_shell'})
            async_tc = [(i, tc) for i, tc in enumerate(tool_calls) if tc['name'] in _ASYNC_TOOL_NAMES_JSON]
            houdini_tc = [(i, tc) for i, tc in enumerate(tool_calls) if tc['name'] not in _ASYNC_TOOL_NAMES_JSON]

            # 结果槽位
            exec_results = [None] * len(tool_calls)

            # 并行 async 工具（web + shell）
            if len(async_tc) > 1:
                import concurrent.futures
                def _exec_async_json(idx_tc):
                    idx, tc = idx_tc
                    tname, targs = tc['name'], tc['arguments']
                    if tname == 'web_search':
                        return idx, self._execute_web_search(targs)
                    elif tname == 'fetch_webpage':
                        return idx, self._execute_fetch_webpage(targs)
                    else:  # execute_shell
                        return idx, self._tool_executor(tname, **targs)
                with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(async_tc))) as pool:
                    for idx, res in pool.map(_exec_async_json, async_tc):
                        exec_results[idx] = res
            elif len(async_tc) == 1:
                idx, tc = async_tc[0]
                tname, targs = tc['name'], tc['arguments']
                if tname == 'web_search':
                    exec_results[idx] = self._execute_web_search(targs)
                elif tname == 'fetch_webpage':
                    exec_results[idx] = self._execute_fetch_webpage(targs)
                else:  # execute_shell
                    exec_results[idx] = self._tool_executor(tname, **targs)

            # Houdini 工具（只读批量 / 写入串行）
            _BATCH_READONLY_JSON = frozenset({
                'get_network_structure', 'get_node_parameters', 'list_children',
                'read_selection', 'search_node_types', 'semantic_search_nodes',
                'find_nodes_by_param', 'get_node_inputs', 'check_errors',
                'search_local_doc', 'get_houdini_node_doc', 'list_skills',
                'get_node_positions', 'list_network_boxes',
                'perf_start_profile', 'perf_stop_and_report',
            })
            readonly_batch_j = [(i, tc) for i, tc in houdini_tc if tc['name'] in _BATCH_READONLY_JSON]
            mutating_calls_j = [(i, tc) for i, tc in houdini_tc if tc['name'] not in _BATCH_READONLY_JSON]

            if len(readonly_batch_j) > 1 and self._batch_tool_executor:
                batch_input = [(tc['name'], tc['arguments']) for _, tc in readonly_batch_j]
                try:
                    batch_results = self._batch_tool_executor(batch_input)
                    for (idx, _), result in zip(readonly_batch_j, batch_results):
                        exec_results[idx] = result
                except Exception as e:
                    print(f"[AI Client] JSON模式批量执行失败，回退串行: {e}")
                    for idx, tc in readonly_batch_j:
                        tname, targs = tc['name'], tc['arguments']
                        try:
                            exec_results[idx] = self._tool_executor(tname, **targs)
                        except Exception as ex:
                            exec_results[idx] = {"success": False, "error": str(ex)}
            else:
                for idx, tc in readonly_batch_j:
                    tname, targs = tc['name'], tc['arguments']
                    if not self._tool_executor:
                        exec_results[idx] = {"success": False, "error": f"工具执行器未设置: {tname}"}
                    else:
                        try:
                            exec_results[idx] = self._tool_executor(tname, **targs)
                        except Exception as e:
                            exec_results[idx] = {"success": False, "error": str(e)}

            for idx, tc in mutating_calls_j:
                tname, targs = tc['name'], tc['arguments']
                if not self._tool_executor:
                    exec_results[idx] = {"success": False, "error": f"工具执行器未设置: {tname}"}
                else:
                    try:
                        exec_results[idx] = self._tool_executor(tname, **targs)
                    except Exception as e:
                        import traceback
                        exec_results[idx] = {"success": False, "error": f"工具执行异常: {str(e)}\n{traceback.format_exc()[:200]}"}

            # 统一处理结果
            should_break_limit = False
            for i, tc in enumerate(tool_calls):
                tool_name = tc['name']
                arguments = tc['arguments']
                result = exec_results[i]

                total_tool_calls += 1
                call_signature = f"{tool_name}:{json.dumps(arguments, sort_keys=True)}"

                if total_tool_calls > max_tool_calls:
                    print(f"[AI Client] ⚠️ JSON模式：达到最大工具调用次数限制 ({max_tool_calls})")
                    should_break_limit = True
                    break

                if call_signature == last_call_signature:
                    consecutive_same_calls += 1
                else:
                    consecutive_same_calls = 1
                    last_call_signature = call_signature

                if on_tool_call:
                    on_tool_call(tool_name, arguments)

                tool_calls_history.append({
                    'tool_name': tool_name,
                    'arguments': arguments,
                    'result': result
                })

                if not result.get('success'):
                    error_detail = result.get('error', '未知错误')
                    print(f"[AI Client] ⚠️ 工具执行失败: {tool_name}")
                    print(f"[AI Client]   错误详情: {error_detail[:200]}")

                if on_tool_result:
                    on_tool_result(tool_name, arguments, result)

                compressed = self._compress_tool_result(tool_name, result)
                if result.get('success'):
                    tool_results.append(f"{tool_name}:{compressed}")
                else:
                    tool_results.append(f"{tool_name}:错误:{compressed}")

            if should_break_limit:
                return {
                    'ok': True,
                    'content': full_content + f"\n\n已达到工具调用次数限制({max_tool_calls})，自动停止。",
                    'tool_calls_history': tool_calls_history,
                    'iterations': iteration
                }

            # 极简格式：工具结果，继续或总结
            # 收集失败的工具详情（明确指出哪个工具、什么错误）
            failed_tool_details = []
            for r in tool_results:
                if ':错误:' in r:
                    failed_tool_details.append(r)
            has_failed_tools = len(failed_tool_details) > 0
            # 检查是否有未完成的todo（通过检查工具调用历史）
            has_pending_todos = False
            for tc in tool_calls_history:
                if tc.get('tool_name') == 'add_todo':
                    # 如果有add_todo但没有对应的update_todo done，说明还有未完成的任务
                    has_pending_todos = True
                    break

            # 构造提示（带多轮思考引导）
            think_hint = '先在<think>标签内分析执行结果和当前进度，再决定下一步。' if enable_thinking else ''

            todo_hint = '已完成的步骤请立即用 update_todo 标记为 done。'
            if has_failed_tools:
                # 明确列出失败的工具及错误原因，避免AI误解为需要调用check_errors
                fail_summary = '; '.join(failed_tool_details)
                prompt = ('|'.join(tool_results)
                          + f'|⚠️ 以下工具调用返回了错误（这是工具调用层面的参数/执行错误，不是Houdini节点错误，'
                          + f'无需调用check_errors，请直接根据错误原因修正参数后重试）: {fail_summary}'
                          + f'|{think_hint}{todo_hint}请根据上述错误原因修正后继续完成任务。不要因为失败就提前结束。')
            elif has_pending_todos and iteration < max_iterations - 2:
                prompt = '|'.join(tool_results) + f'|检测到还有未完成的任务，{think_hint}{todo_hint}请继续执行。'
            elif iteration >= max_iterations - 1:
                prompt = '|'.join(tool_results) + f'|{todo_hint}请生成最终总结，说明已完成的操作'
            else:
                prompt = '|'.join(tool_results) + f'|{think_hint}{todo_hint}继续或总结'

            # 使用 system 角色传递工具结果，避免与用户消息混淆
            # 注意：部分模型不支持多个 system 消息，此处使用明确的 [TOOL_RESULT] 标记
            # ★ 检查是否有视口截图需要注入
            _viewport_imgs = []
            if supports_vision:
                for tc in tool_calls:
                    _r = exec_results.get(tool_calls.index(tc))
                    if isinstance(_r, dict) and _r.get('_viewport_image'):
                        _viewport_imgs.append((_r['_viewport_image'], _r.get('_image_media_type', 'image/jpeg')))

            if _viewport_imgs:
                # 多模态消息：文本 + 图片
                _use_anth = self._is_anthropic_protocol(provider, model)
                _content_parts = [{"type": "text", "text": f"[TOOL_RESULT]\n{prompt}\n[viewport snapshot attached — please analyze the current viewport state]"}]
                for _vimg_b64, _vimg_mt in _viewport_imgs:
                    _content_parts.append(self._build_image_block(_vimg_b64, _vimg_mt, _use_anth))
                    print(f"[AI Client] 📸 视口截图已注入消息 (JSON mode, {len(_vimg_b64)//1024}KB, {'anthropic' if _use_anth else 'openai'} format)")
                working_messages.append({'role': 'user', 'content': _content_parts})
            else:
                working_messages.append({
                    'role': 'user',
                    'content': f'[TOOL_RESULT]\n{prompt}'
                })

            # 保存当前轮次的内容（使用预编译正则清理XML标签）
            cleaned_round = round_content
            for _pat in self._RE_CLEAN_PATTERNS:
                cleaned_round = _pat.sub('', cleaned_round)
            cleaned_round = re.sub(r'<[^>]+>', '', cleaned_round)  # 清理所有剩余的XML标签
            # 只添加非空且不重复的内容
            if cleaned_round.strip():
                # 检查是否与已有内容重复（简单去重：如果内容完全相同，跳过）
                if cleaned_round.strip() not in full_content:
                    full_content += cleaned_round
                else:
                    # 如果内容重复，只添加一次（避免多次重复）
                    pass

        # 如果循环结束但内容为空，且有工具调用历史，强制要求生成总结
        if not full_content.strip() and tool_calls_history:
            print("[AI Client] ⚠️ JSON模式：工具调用完成但无回复内容，强制要求生成总结")
            # 最后一次请求，强制要求总结
            working_messages.append({
                'role': 'user',
                'content': '请生成最终总结，说明已完成的操作和结果。'
            })

            # 再次请求生成总结
            summary_content = ""
            for chunk in self.chat_stream(
                messages=working_messages,
                model=model,
                provider=provider,
                temperature=temperature,
                max_tokens=max_tokens or 500,  # 限制总结长度
                tools=None,
                tool_choice=None
            ):
                if chunk.get('type') == 'content':
                    content = chunk.get('content', '')
                    summary_content += content
                    if on_content:
                        on_content(content)
                elif chunk.get('type') == 'done':
                    break

            full_content = summary_content if summary_content else full_content

        # 计算 cache 命中率
        prompt_total = total_usage['cache_hit_tokens'] + total_usage['cache_miss_tokens']
        if prompt_total > 0:
            total_usage['cache_hit_rate'] = total_usage['cache_hit_tokens'] / prompt_total
        else:
            total_usage['cache_hit_rate'] = 0

        _result = {
            'ok': True,
            'content': full_content if full_content.strip() else "(工具调用完成，但未生成回复)",
            'tool_calls_history': tool_calls_history,
            'call_records': call_records,
            'iterations': iteration,
            'usage': total_usage
        }

        # ★ Hook: on_after_response — 通知插件 Agent Loop 结束
        try:
            from .hooks import get_hook_manager as _ghm
            _ghm().fire('on_after_response',
                       result=_result, model=model, provider=provider)
        except Exception:
            pass

        return _result

    def agent_loop_auto(self,
                        messages: List[Dict[str, Any]],
                        model: str = 'gpt-5.2',
                        provider: str = 'openai',
                        **kwargs) -> Dict[str, Any]:
        """自动选择合适的 Agent Loop 模式"""
        if self._supports_function_calling(provider, model):
            return self.agent_loop_stream(messages=messages, model=model, provider=provider, **kwargs)
        else:
            return self.agent_loop_json_mode(messages=messages, model=model, provider=provider, **kwargs)
