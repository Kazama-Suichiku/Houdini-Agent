# -*- coding: utf-8 -*-
import os
import sys
import json
import time
import re
from typing import List, Dict, Optional, Any, Generator

# 强制使用本地 lib 目录中的依赖库（与 ai_client.py 一致，保证 mixin 模块自洽）
_lib_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'lib')
if os.path.exists(_lib_path):
    if _lib_path in sys.path:
        sys.path.remove(_lib_path)
    sys.path.insert(0, _lib_path)

# 导入 requests（这些方法原属 ai_client.py，依赖模块级 requests / HAS_REQUESTS）
HAS_REQUESTS = False
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    pass


class AIClientStreamingMixin:
    """Streaming chat: Anthropic and OpenAI-compatible streaming."""

    # ★ 预编译流式内容清洗正则（避免每个 SSE chunk 都重新编译）
    _RE_CLEAN_PATTERNS = [
        re.compile(r'</?tool_call[^>]*>'),
        re.compile(r'<arg_key>([^<]+)</arg_key>\s*<arg_value>([^<]+)</arg_value>'),
        re.compile(r'</?arg_key[^>]*>'),
        re.compile(r'</?arg_value[^>]*>'),
        re.compile(r'</?redacted_reasoning[^>]*>'),
    ]

    @staticmethod
    def _convert_messages_to_anthropic(messages: List[Dict[str, Any]]) -> tuple:
        """将 OpenAI 格式的消息列表转换为 Anthropic Messages API 格式。

        Returns:
            (system_text, anthropic_messages)
            - system_text: 系统提示（Anthropic 要求单独传 system 参数）
            - anthropic_messages: Anthropic 格式的 messages 列表
        """
        system_text = ""
        anthropic_msgs: List[Dict[str, Any]] = []

        for msg in messages:
            role = msg.get('role', '')

            if role == 'system':
                # Anthropic 的 system 不在 messages 里，单独传
                system_text += (("\n\n" if system_text else "") + (msg.get('content', '') or ''))
                continue

            if role == 'user':
                content = msg.get('content', '')
                # 支持 OpenAI 多模态格式: content 可能是 list
                if isinstance(content, list):
                    # 转换 OpenAI 多模态格式 → Anthropic 格式
                    anth_content = []
                    for part in content:
                        if part.get('type') == 'text':
                            anth_content.append({'type': 'text', 'text': part['text']})
                        elif part.get('type') == 'image_url':
                            url = part.get('image_url', {}).get('url', '')
                            if url.startswith('data:'):
                                # data:image/png;base64,xxxx
                                import re as _re
                                m = _re.match(r'data:(image/\w+);base64,(.+)', url, _re.DOTALL)
                                if m:
                                    anth_content.append({
                                        'type': 'image',
                                        'source': {
                                            'type': 'base64',
                                            'media_type': m.group(1),
                                            'data': m.group(2),
                                        }
                                    })
                            else:
                                anth_content.append({
                                    'type': 'image',
                                    'source': {'type': 'url', 'url': url}
                                })
                    anthropic_msgs.append({'role': 'user', 'content': anth_content})
                else:
                    anthropic_msgs.append({'role': 'user', 'content': str(content or '')})
                continue

            if role == 'assistant':
                content_blocks: List[Dict[str, Any]] = []
                text = msg.get('content')
                if text:
                    content_blocks.append({'type': 'text', 'text': str(text)})
                # tool_calls → tool_use blocks
                for tc in (msg.get('tool_calls') or []):
                    func = tc.get('function', {})
                    try:
                        input_obj = json.loads(func.get('arguments', '{}'))
                    except (json.JSONDecodeError, ValueError):
                        input_obj = {}
                    content_blocks.append({
                        'type': 'tool_use',
                        'id': tc.get('id', ''),
                        'name': func.get('name', ''),
                        'input': input_obj,
                    })
                if not content_blocks:
                    content_blocks.append({'type': 'text', 'text': ''})
                anthropic_msgs.append({'role': 'assistant', 'content': content_blocks})
                continue

            if role == 'tool':
                # OpenAI tool result → Anthropic tool_result (放在 user 消息中)
                tool_result_block = {
                    'type': 'tool_result',
                    'tool_use_id': msg.get('tool_call_id', ''),
                    'content': str(msg.get('content', '')),
                }
                # 如果上一条也是 user（连续的 tool results），合并到同一条 user 消息
                if anthropic_msgs and anthropic_msgs[-1]['role'] == 'user':
                    last_content = anthropic_msgs[-1]['content']
                    if isinstance(last_content, list):
                        last_content.append(tool_result_block)
                    else:
                        anthropic_msgs[-1]['content'] = [
                            {'type': 'text', 'text': last_content},
                            tool_result_block,
                        ]
                else:
                    anthropic_msgs.append({
                        'role': 'user',
                        'content': [tool_result_block],
                    })
                continue

        # Anthropic 要求消息以 user 开头，如果第一条是 assistant 则补一条 user
        if anthropic_msgs and anthropic_msgs[0]['role'] == 'assistant':
            anthropic_msgs.insert(0, {'role': 'user', 'content': '请继续。'})

        # Anthropic 要求角色严格交替（user/assistant/user/...）
        # 合并连续相同角色的消息
        merged: List[Dict[str, Any]] = []
        for m in anthropic_msgs:
            if merged and merged[-1]['role'] == m['role']:
                # 合并内容
                prev_content = merged[-1]['content']
                curr_content = m['content']
                # 统一为 list 格式
                if isinstance(prev_content, str):
                    prev_content = [{'type': 'text', 'text': prev_content}]
                if isinstance(curr_content, str):
                    curr_content = [{'type': 'text', 'text': curr_content}]
                if not isinstance(prev_content, list):
                    prev_content = [prev_content]
                if not isinstance(curr_content, list):
                    curr_content = [curr_content]
                merged[-1]['content'] = prev_content + curr_content
            else:
                merged.append(m)

        return system_text, merged

    @staticmethod
    def _convert_tools_to_anthropic(tools: List[dict]) -> List[dict]:
        """将 OpenAI Function Calling 格式的工具列表转换为 Anthropic 格式。

        OpenAI:  {"type": "function", "function": {"name": ..., "description": ..., "parameters": {...}}}
        Anthropic: {"name": ..., "description": ..., "input_schema": {...}}
        """
        if not tools:
            return []
        anthropic_tools = []
        for tool in tools:
            func = tool.get('function', tool)  # 兼容裸 function dict
            anthropic_tools.append({
                'name': func.get('name', ''),
                'description': func.get('description', ''),
                'input_schema': func.get('parameters', {'type': 'object', 'properties': {}}),
            })
        return anthropic_tools

    def _chat_stream_anthropic(self,
                                messages: List[Dict[str, Any]],
                                model: str,
                                provider: str,
                                temperature: float = 0.17,
                                max_tokens: Optional[int] = None,
                                tools: Optional[List[dict]] = None,
                                tool_choice: str = 'auto',
                                enable_thinking: bool = True,
                                api_key: str = '') -> Generator[Dict[str, Any], None, None]:
        """Anthropic Messages 协议的流式 Chat。

        将 OpenAI 格式的输入转换为 Anthropic 格式，调用 /v1/messages，
        解析 Anthropic SSE 事件流，yield 与 OpenAI 分支相同的内部 chunk 格式。
        """
        api_url = self._get_api_url(provider, model)

        # 消息转换
        system_text, anth_messages = self._convert_messages_to_anthropic(messages)

        payload: Dict[str, Any] = {
            'model': model,
            'messages': anth_messages,
            'max_tokens': max_tokens or 16384,
            'stream': True,
        }
        # temperature（Anthropic 范围 0-1）
        if temperature is not None:
            payload['temperature'] = min(max(temperature, 0.0), 1.0)

        if system_text:
            payload['system'] = system_text

        # 思考模式
        if enable_thinking:
            payload['thinking'] = {'type': 'enabled', 'budget_tokens': min(max_tokens or 16384, 10000)}

        # 工具
        if tools:
            payload['tools'] = self._convert_tools_to_anthropic(tools)
            if tool_choice == 'auto':
                payload['tool_choice'] = {'type': 'auto'}
            elif tool_choice == 'none':
                payload['tool_choice'] = {'type': 'none'}
            elif tool_choice == 'required':
                payload['tool_choice'] = {'type': 'any'}

        # 请求头（Anthropic 格式）
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'text/event-stream',
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01',
        }

        print(f"[AI Client] Anthropic protocol: {api_url} model={model}")

        for attempt in range(self._max_retries):
            try:
                with self._http_session.post(
                    api_url,
                    json=payload,
                    headers=headers,
                    stream=True,
                    timeout=(10, self._chunk_timeout),
                    proxies={'http': None, 'https': None}
                ) as response:
                    response.encoding = 'utf-8'
                    print(f"[AI Client] Anthropic response status: {response.status_code}")

                    if response.status_code != 200:
                        try:
                            err = response.json()
                            err_msg = err.get('error', {}).get('message', response.text)
                        except Exception:
                            err_msg = response.text
                        print(f"[AI Client] Anthropic error: {err_msg}")

                        if response.status_code >= 500 and attempt < self._max_retries - 1:
                            wait = self._retry_delay * (attempt + 1)
                            print(f"[AI Client] Anthropic server error {response.status_code}, retrying in {wait}s...")
                            time.sleep(wait)
                            continue

                        yield {"type": "error", "error": f"HTTP {response.status_code}: {err_msg}"}
                        return

                    # ── 解析 Anthropic SSE 事件流 ──
                    # 状态
                    _content_blocks: Dict[int, Dict[str, Any]] = {}  # index → block info
                    _tool_args_acc: Dict[int, str] = {}  # index → accumulated JSON args
                    _pending_usage: Dict[str, Any] = {}
                    _last_stop_reason = None
                    _got_thinking = False
                    _enable_thinking_flag = enable_thinking  # 闭包变量

                    import codecs
                    _utf8_decoder = codecs.getincrementaldecoder('utf-8')(errors='ignore')
                    _line_buf = ""
                    _event_type = ""  # 当前 SSE event 类型

                    def _process_anthropic_event(event_type: str, data_str: str):
                        """处理单个 Anthropic SSE 事件，返回要 yield 的 dict 列表"""
                        nonlocal _content_blocks, _tool_args_acc, _pending_usage, _last_stop_reason, _got_thinking
                        results = []

                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            return results

                        ev_type = data.get('type', event_type)

                        if ev_type == 'message_start':
                            msg = data.get('message', {})
                            usage = msg.get('usage', {})
                            if usage:
                                _pending_usage = self._parse_usage(usage)

                        elif ev_type == 'content_block_start':
                            idx = data.get('index', 0)
                            block = data.get('content_block', {})
                            _content_blocks[idx] = {
                                'type': block.get('type', 'text'),
                                'id': block.get('id', ''),
                                'name': block.get('name', ''),
                            }
                            if block.get('type') == 'tool_use':
                                _tool_args_acc[idx] = ''

                        elif ev_type == 'content_block_delta':
                            idx = data.get('index', 0)
                            delta = data.get('delta', {})
                            delta_type = delta.get('type', '')
                            block_info = _content_blocks.get(idx, {})

                            if delta_type == 'text_delta':
                                text = delta.get('text', '')
                                if text:
                                    results.append({"type": "content", "content": text})

                            elif delta_type == 'thinking_delta':
                                thinking = delta.get('thinking', '')
                                if thinking:
                                    if not _got_thinking:
                                        _got_thinking = True
                                        print(f"[AI Client] 🧠 Anthropic thinking (首个 chunk, len={len(thinking)}, enable={_enable_thinking_flag})")
                                    if _enable_thinking_flag:
                                        results.append({"type": "thinking", "content": thinking})

                            elif delta_type == 'input_json_delta':
                                partial = delta.get('partial_json', '')
                                if partial and idx in _tool_args_acc:
                                    _tool_args_acc[idx] += partial
                                    # 广播 tool_args_delta → UI 流式预览
                                    tool_name = block_info.get('name', '')
                                    if tool_name:
                                        results.append({
                                            "type": "tool_args_delta",
                                            "index": idx,
                                            "name": tool_name,
                                            "delta": partial,
                                            "accumulated": _tool_args_acc[idx],
                                        })

                        elif ev_type == 'content_block_stop':
                            idx = data.get('index', 0)
                            block_info = _content_blocks.get(idx, {})
                            if block_info.get('type') == 'tool_use':
                                # 工具调用完成 → 转换为 OpenAI 格式的 tool_call
                                tool_id = block_info.get('id', '')
                                tool_name = block_info.get('name', '')
                                args_str = _tool_args_acc.get(idx, '{}')
                                results.append({
                                    "type": "tool_call",
                                    "tool_call": {
                                        'id': tool_id,
                                        'type': 'function',
                                        'function': {
                                            'name': tool_name,
                                            'arguments': args_str,
                                        }
                                    }
                                })

                        elif ev_type == 'message_delta':
                            delta = data.get('delta', {})
                            _last_stop_reason = delta.get('stop_reason')
                            usage = data.get('usage', {})
                            if usage:
                                # 合并 usage
                                parsed = self._parse_usage(usage)
                                for k, v in parsed.items():
                                    if isinstance(v, (int, float)):
                                        _pending_usage[k] = _pending_usage.get(k, 0) + v

                        elif ev_type == 'message_stop':
                            # 映射 stop_reason: end_turn → stop, tool_use → tool_calls
                            finish = 'stop'
                            if _last_stop_reason == 'tool_use':
                                finish = 'tool_calls'
                            elif _last_stop_reason == 'max_tokens':
                                finish = 'length'
                            results.append({
                                "type": "done",
                                "finish_reason": finish,
                                "usage": _pending_usage,
                            })

                        elif ev_type == 'error':
                            err_msg = data.get('error', {}).get('message', str(data))
                            results.append({"type": "error", "error": err_msg})

                        return results

                    # ── 主循环 ──
                    _should_return = False
                    for raw_chunk in response.iter_content(chunk_size=4096, decode_unicode=False):
                        if not raw_chunk:
                            continue
                        if self._stop_event.is_set():
                            yield {"type": "stopped", "message": "用户停止了请求"}
                            return

                        decoded = _utf8_decoder.decode(raw_chunk)
                        _line_buf += decoded

                        while '\n' in _line_buf:
                            one_line, _line_buf = _line_buf.split('\n', 1)
                            one_line = one_line.rstrip('\r')

                            if not one_line:
                                continue

                            # Anthropic SSE: "event: xxx" 行后跟 "data: {...}" 行
                            if one_line.startswith('event: '):
                                _event_type = one_line[7:].strip()
                                continue

                            if one_line.startswith('data: '):
                                data_str = one_line[6:]
                                for item in _process_anthropic_event(_event_type, data_str):
                                    yield item
                                    if item.get('type') in ('done', 'error'):
                                        _should_return = True
                                _event_type = ""  # 重置

                        if _should_return:
                            return

                    # 处理残留
                    _line_buf += _utf8_decoder.decode(b'', final=True)
                    if _line_buf.strip():
                        for line in _line_buf.strip().split('\n'):
                            line = line.strip()
                            if line.startswith('event: '):
                                _event_type = line[7:].strip()
                            elif line.startswith('data: '):
                                for item in _process_anthropic_event(_event_type, line[6:]):
                                    yield item
                                    if item.get('type') in ('done', 'error'):
                                        return

                    # 流结束但未收到 message_stop
                    if not _should_return:
                        yield {"type": "done", "finish_reason": _last_stop_reason or "stop", "usage": _pending_usage}
                    return

            except requests.exceptions.Timeout:
                if attempt < self._max_retries - 1:
                    time.sleep(self._retry_delay * (attempt + 1))
                    continue
                yield {"type": "error", "error": f"请求超时（已重试 {self._max_retries} 次）"}
                return
            except requests.exceptions.ConnectionError as e:
                if attempt < self._max_retries - 1:
                    time.sleep(self._retry_delay * (attempt + 1))
                    continue
                yield {"type": "error", "error": f"连接错误: {str(e)}"}
                return
            except Exception as e:
                err_str = str(e)
                is_transient = any(k in err_str for k in (
                    'InvalidChunkLength', 'ChunkedEncodingError',
                    'Connection broken', 'IncompleteRead',
                    'ConnectionReset', 'RemoteDisconnected',
                ))
                if is_transient and attempt < self._max_retries - 1:
                    wait = self._retry_delay * (attempt + 1)
                    print(f"[AI Client] Anthropic 连接中断 ({err_str[:80]}), {wait}s 后重试")
                    time.sleep(wait)
                    continue
                yield {"type": "error", "error": f"请求失败: {err_str}"}
                return

    def _chat_anthropic(self,
                        messages: List[Dict[str, Any]],
                        model: str,
                        provider: str,
                        temperature: float = 0.17,
                        max_tokens: int = 4096,
                        tools: Optional[List[dict]] = None,
                        tool_choice: str = 'auto',
                        api_key: str = '',
                        timeout: int = 60) -> Dict[str, Any]:
        """Anthropic Messages 协议的非流式 Chat。"""
        api_url = self._get_api_url(provider, model)
        system_text, anth_messages = self._convert_messages_to_anthropic(messages)

        payload: Dict[str, Any] = {
            'model': model,
            'messages': anth_messages,
            'max_tokens': max_tokens,
        }
        if temperature is not None:
            payload['temperature'] = min(max(temperature, 0.0), 1.0)
        if system_text:
            payload['system'] = system_text
        if tools:
            payload['tools'] = self._convert_tools_to_anthropic(tools)
            if tool_choice == 'auto':
                payload['tool_choice'] = {'type': 'auto'}

        headers = {
            'Content-Type': 'application/json',
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01',
        }

        for attempt in range(self._max_retries):
            try:
                response = self._http_session.post(
                    api_url, json=payload, headers=headers,
                    timeout=timeout, proxies={'http': None, 'https': None}
                )
                response.raise_for_status()
                obj = response.json()

                # 解析 Anthropic 响应 → OpenAI 统一格式
                content_text = ''
                tool_calls_list = []
                for block in obj.get('content', []):
                    if block.get('type') == 'text':
                        content_text += block.get('text', '')
                    elif block.get('type') == 'tool_use':
                        tool_calls_list.append({
                            'id': block.get('id', ''),
                            'type': 'function',
                            'function': {
                                'name': block.get('name', ''),
                                'arguments': json.dumps(block.get('input', {}), ensure_ascii=False),
                            }
                        })

                stop_reason = obj.get('stop_reason', 'end_turn')
                finish = 'stop' if stop_reason == 'end_turn' else ('tool_calls' if stop_reason == 'tool_use' else stop_reason)

                return {
                    'ok': True,
                    'content': content_text or None,
                    'tool_calls': tool_calls_list or None,
                    'finish_reason': finish,
                    'usage': self._parse_usage(obj.get('usage', {})),
                    'raw': obj,
                }
            except requests.exceptions.Timeout:
                if attempt < self._max_retries - 1:
                    time.sleep(self._retry_delay)
                    continue
                return {'ok': False, 'error': '请求超时'}
            except Exception as e:
                if attempt < self._max_retries - 1:
                    time.sleep(self._retry_delay)
                    continue
                return {'ok': False, 'error': str(e)}

        return {'ok': False, 'error': '请求失败'}

    def chat_stream(self,
                    messages: List[Dict[str, str]],
                    model: str = 'gpt-5.2',
                    provider: str = 'openai',
                    temperature: float = 0.17,
                    max_tokens: Optional[int] = None,
                    tools: Optional[List[dict]] = None,
                    tool_choice: str = 'auto',
                    enable_thinking: bool = True,
                    response_format: Optional[dict] = None) -> Generator[Dict[str, Any], None, None]:
        """流式 Chat API

        Yields:
            {"type": "content", "content": str}  # 内容片段
            {"type": "tool_call", "tool_call": dict}  # 工具调用
            {"type": "thinking", "content": str}  # 思考内容（DeepSeek / GLM 原生 reasoning_content）
            {"type": "done", "finish_reason": str}  # 完成
            {"type": "error", "error": str}  # 错误
        """
        if not HAS_REQUESTS:
            yield {"type": "error", "error": "需要安装 requests 库"}
            return

        provider = (provider or 'openai').lower()
        api_key = self._get_api_key(provider)

        # Ollama / Custom（无 Key）不需要 API Key 验证
        if provider not in ('ollama', 'custom') and not api_key:
            yield {"type": "error", "error": f"缺少 {self._get_vendor_name(provider)} API Key"}
            return

        # ★ Anthropic 协议分支（Duojie GLM 等）
        if self._is_anthropic_protocol(provider, model):
            yield from self._chat_stream_anthropic(
                messages=messages, model=model, provider=provider,
                temperature=temperature, max_tokens=max_tokens,
                tools=tools, tool_choice=tool_choice,
                enable_thinking=enable_thinking, api_key=api_key,
            )
            return

        api_url = self._get_api_url(provider, model)

        payload = {
            'model': model,
            'messages': messages,
            'temperature': temperature,
            'stream': True,
            # 必须加 stream_options 才能在流式响应中获取 usage 统计
            'stream_options': {'include_usage': True},
        }
        if max_tokens:
            payload['max_tokens'] = max_tokens
        if response_format:
            payload['response_format'] = response_format

        # GLM-4.7 专属参数（仅原生 GLM 接口）：深度思考 + 流式工具调用
        if self.is_glm47(model) and provider == 'glm' and enable_thinking:
            payload['thinking'] = {'type': 'enabled'}
            if tools:
                payload['tool_stream'] = True

        # DeepSeek V4 thinking 参数（v4-flash / v4-pro 显式启用思考）
        if provider == 'deepseek' and enable_thinking and 'deepseek-v4' in model.lower():
            payload['thinking'] = {'type': 'enabled'}
            if 'v4-pro' in model.lower():
                payload['reasoning_effort'] = 'high'

        # Duojie 中转：思考模式通过系统提示词中的 <think> 标签实现
        # 经测试 thinking/reasoningEffort 参数对 Duojie API 无效（reasoning_tokens 始终为 0）
        # 且 thinking 参数偶尔导致 403，因此不发送任何额外参数

        # DeepSeek / OpenAI prompt caching 自动启用（保持前缀稳定即可命中）

        # 工具调用（所有支持 function calling 的 provider 通用）
        if tools:
            payload['tools'] = tools
            payload['tool_choice'] = tool_choice

        # 构建请求头
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'text/event-stream',
        }

        # Ollama 和无 Key 的 Custom 不需要 Authorization 头
        if provider != 'ollama' and api_key:
            headers['Authorization'] = f'Bearer {api_key}'

        # OpenRouter 需要额外的请求头用于标识来源（参见 https://openrouter.ai/docs/quickstart）
        if provider == 'openrouter':
            headers['HTTP-Referer'] = 'https://github.com/Kazama-Suichiku/Houdini-Agent'
            headers['X-OpenRouter-Title'] = 'Houdini Agent'

        # 重试逻辑
        print(f"[AI Client] Requesting {api_url} with model {model}")
        for attempt in range(self._max_retries):
            try:
                with self._http_session.post(
                    api_url,
                    json=payload,
                    headers=headers,
                    stream=True,
                    timeout=(10, self._chunk_timeout),  # (连接超时, 读取超时)
                    proxies={'http': None, 'https': None}
                ) as response:
                    # 强制 UTF-8 编码（requests 对 text/event-stream 默认 ISO-8859-1，会导致中文乱码）
                    response.encoding = 'utf-8'
                    print(f"[AI Client] Response status: {response.status_code}")

                    if response.status_code != 200:
                        try:
                            err = response.json()
                            err_msg = err.get('error', {}).get('message', response.text)
                        except:
                            err_msg = response.text
                        print(f"[AI Client] Error: {err_msg}")

                        # 5xx 服务端错误（502/503/529 等）可重试
                        if response.status_code >= 500 and attempt < self._max_retries - 1:
                            wait = self._retry_delay * (attempt + 1)
                            print(f"[AI Client] Server error {response.status_code}, retrying in {wait}s...")
                            time.sleep(wait)
                            continue  # 重试

                        yield {"type": "error", "error": f"HTTP {response.status_code}: {err_msg}"}
                        return

                    # 解析 SSE 流
                    tool_calls_buffer = {}  # 缓存工具调用片段
                    pending_usage = {}  # 收集 usage 数据
                    last_finish_reason = None
                    _got_reasoning = False  # 诊断：本轮是否收到 reasoning_content
                    _enable_thinking = enable_thinking  # 闭包变量，供 _process_sse_line 使用

                    # ── 使用 iter_content + 增量解码器 + 手动分行 ──
                    # 比 iter_lines() 更健壮：
                    #   1. iter_content() 返回 HTTP body 原始字节块
                    #   2. 增量解码器正确处理跨 chunk 切断的多字节 UTF-8
                    #   3. 手动按 \n 分行，避免 requests 内部分行时的编码干扰
                    import codecs
                    _utf8_decoder = codecs.getincrementaldecoder('utf-8')(errors='ignore')
                    _line_buf = ""  # 解码后、尚未遇到 \n 的文本缓冲

                    def _process_sse_line(line):
                        """处理单行 SSE data，返回要 yield 的 dict 列表"""
                        nonlocal tool_calls_buffer, pending_usage, last_finish_reason, _got_reasoning, _enable_thinking
                        results = []

                        if not line.startswith('data: '):
                            return results

                        data_str = line[6:]

                        if data_str.strip() == '[DONE]':
                            _reason_tokens = pending_usage.get('reasoning_tokens', 0)
                            print(f"[AI Client] Received [DONE], reasoning={'YES' if _got_reasoning else 'NO'}(tokens={_reason_tokens}), usage={pending_usage}")
                            results.append({"type": "done", "finish_reason": last_finish_reason or "stop", "usage": pending_usage})
                            return results

                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            return results

                        choices = data.get('choices', [])
                        usage_data = data.get('usage')

                        # usage-only chunk
                        if usage_data:
                            pending_usage = self._parse_usage(usage_data)

                        if not choices:
                            return results

                        choice = choices[0]
                        delta = choice.get('delta', {})
                        finish_reason = choice.get('finish_reason')

                        # 思考内容（仅在 enable_thinking=True 时显示）
                        # 不同代理可能使用不同字段名：reasoning_content / thinking_content / reasoning
                        # 统一拦截，Think 关闭时全部静默丢弃
                        _thinking_text = (
                            delta.get('reasoning_content')
                            or delta.get('thinking_content')
                            or delta.get('reasoning')
                            or ''
                        )
                        if _thinking_text:
                            if not _got_reasoning:
                                _got_reasoning = True
                                # 诊断：记录字段名以便排查
                                _field = ('reasoning_content' if 'reasoning_content' in delta
                                          else 'thinking_content' if 'thinking_content' in delta
                                          else 'reasoning')
                                print(f"[AI Client] 🧠 收到 {_field}（首个 chunk，len={len(_thinking_text)}，enable_thinking={_enable_thinking}）")
                            if _enable_thinking:
                                results.append({"type": "thinking", "content": _thinking_text})

                        # 普通内容
                        if 'content' in delta and delta['content']:
                            results.append({"type": "content", "content": delta['content']})

                        # 工具调用
                        if delta.get('tool_calls'):
                            for tc in delta['tool_calls']:
                                idx = tc.get('index', 0)
                                tc_id = tc.get('id', '')

                                if tc_id and idx in tool_calls_buffer:
                                    existing_id = tool_calls_buffer[idx].get('id', '')
                                    if existing_id and existing_id != tc_id:
                                        idx = max(tool_calls_buffer.keys()) + 1

                                if idx not in tool_calls_buffer:
                                    tool_calls_buffer[idx] = {
                                        'id': tc_id,
                                        'type': 'function',
                                        'function': {'name': '', 'arguments': ''}
                                    }

                                if tc_id:
                                    tool_calls_buffer[idx]['id'] = tc_id
                                if 'function' in tc:
                                    fn = tc['function']
                                    if 'name' in fn and fn['name']:
                                        tool_calls_buffer[idx]['function']['name'] = fn['name']
                                    if 'arguments' in fn:
                                        tool_calls_buffer[idx]['function']['arguments'] += fn['arguments']
                                        # ★ 广播 tool_call 参数增量 → UI 流式预览
                                        _tname = tool_calls_buffer[idx]['function'].get('name', '')
                                        if _tname:
                                            results.append({
                                                "type": "tool_args_delta",
                                                "index": idx,
                                                "name": _tname,
                                                "delta": fn['arguments'],
                                                "accumulated": tool_calls_buffer[idx]['function']['arguments'],
                                            })

                        # 完成（先发送工具调用，但不 return，等后续 usage chunk / [DONE]）
                        if finish_reason:
                            if tool_calls_buffer:
                                # ★ 修复：检测并拆分被代理错误拼接的 arguments（如 duojie Claude 代理）
                                # 某些代理在流式传输时对多个工具调用使用相同 index，导致 arguments 被拼接为 {...}{...}
                                import uuid as _uuid
                                fixed_buffer = {}
                                next_fix_idx = max(tool_calls_buffer.keys()) + 1
                                for idx_k in sorted(tool_calls_buffer.keys()):
                                    tc_entry = tool_calls_buffer[idx_k]
                                    args_str = tc_entry['function']['arguments'].strip()
                                    if args_str.startswith('{'):
                                        try:
                                            json.loads(args_str)
                                            fixed_buffer[idx_k] = tc_entry
                                        except (json.JSONDecodeError, ValueError):
                                            # 尝试拆分拼接的多个 JSON 对象: {...}{...}
                                            split_parts = []
                                            depth = 0
                                            start = -1
                                            for ci, ch in enumerate(args_str):
                                                if ch == '{':
                                                    if depth == 0:
                                                        start = ci
                                                    depth += 1
                                                elif ch == '}':
                                                    depth -= 1
                                                    if depth == 0 and start >= 0:
                                                        part = args_str[start:ci+1]
                                                        try:
                                                            json.loads(part)
                                                            split_parts.append(part)
                                                        except:
                                                            pass
                                                        start = -1
                                            if split_parts:
                                                print(f"[AI Client] 修复拼接的 tool_call arguments: 拆分为 {len(split_parts)} 个独立调用")
                                                tc_entry['function']['arguments'] = split_parts[0]
                                                fixed_buffer[idx_k] = tc_entry
                                                for extra_args in split_parts[1:]:
                                                    fixed_buffer[next_fix_idx] = {
                                                        'id': f"call_{_uuid.uuid4().hex[:24]}",
                                                        'type': 'function',
                                                        'function': {
                                                            'name': tc_entry['function']['name'],
                                                            'arguments': extra_args
                                                        }
                                                    }
                                                    next_fix_idx += 1
                                            else:
                                                fixed_buffer[idx_k] = tc_entry
                                    else:
                                        fixed_buffer[idx_k] = tc_entry
                                tool_calls_buffer = fixed_buffer

                                for idx_k in sorted(tool_calls_buffer.keys()):
                                    results.append({"type": "tool_call", "tool_call": tool_calls_buffer[idx_k]})
                                tool_calls_buffer = {}
                            last_finish_reason = finish_reason

                        return results

                    # ── 主循环：读取原始字节块 → 解码 → 分行 → 处理 ──
                    _should_return = False
                    for raw_chunk in response.iter_content(chunk_size=4096, decode_unicode=False):
                        if not raw_chunk:
                            continue

                        if self._stop_event.is_set():
                            yield {"type": "stopped", "message": "用户停止了请求"}
                            return

                        # 增量解码：跨 chunk 的多字节 UTF-8 字符在此正确拼合
                        decoded = _utf8_decoder.decode(raw_chunk)
                        _line_buf += decoded

                        # 逐行分割并处理
                        while '\n' in _line_buf:
                            one_line, _line_buf = _line_buf.split('\n', 1)
                            one_line = one_line.rstrip('\r')
                            if not one_line:
                                continue

                            for item in _process_sse_line(one_line):
                                yield item
                                if item.get('type') == 'done':
                                    _should_return = True

                        if _should_return:
                            return

                    # 处理缓冲区残留（流结束时没有以 \n 结尾的尾行）
                    _line_buf += _utf8_decoder.decode(b'', final=True)
                    if _line_buf.strip():
                        for item in _process_sse_line(_line_buf.strip()):
                            yield item
                            if item.get('type') == 'done':
                                return

                    # 流结束但没有收到 [DONE]
                    if tool_calls_buffer:
                        # ★ 同样需要修复拼接的 arguments（与 finish_reason 分支保持一致）
                        import uuid as _uuid2
                        fixed_buffer2 = {}
                        next_fix_idx2 = max(tool_calls_buffer.keys()) + 1
                        for idx_k2 in sorted(tool_calls_buffer.keys()):
                            tc_entry2 = tool_calls_buffer[idx_k2]
                            args_str2 = tc_entry2['function']['arguments'].strip()
                            if args_str2.startswith('{'):
                                try:
                                    json.loads(args_str2)
                                    fixed_buffer2[idx_k2] = tc_entry2
                                except (json.JSONDecodeError, ValueError):
                                    split_parts2 = []
                                    depth2 = 0
                                    start2 = -1
                                    for ci2, ch2 in enumerate(args_str2):
                                        if ch2 == '{':
                                            if depth2 == 0:
                                                start2 = ci2
                                            depth2 += 1
                                        elif ch2 == '}':
                                            depth2 -= 1
                                            if depth2 == 0 and start2 >= 0:
                                                part2 = args_str2[start2:ci2+1]
                                                try:
                                                    json.loads(part2)
                                                    split_parts2.append(part2)
                                                except:
                                                    pass
                                                start2 = -1
                                    if split_parts2:
                                        print(f"[AI Client] 修复拼接的 tool_call arguments (尾部): 拆分为 {len(split_parts2)} 个独立调用")
                                        tc_entry2['function']['arguments'] = split_parts2[0]
                                        fixed_buffer2[idx_k2] = tc_entry2
                                        for extra_args2 in split_parts2[1:]:
                                            fixed_buffer2[next_fix_idx2] = {
                                                'id': f"call_{_uuid2.uuid4().hex[:24]}",
                                                'type': 'function',
                                                'function': {
                                                    'name': tc_entry2['function']['name'],
                                                    'arguments': extra_args2
                                                }
                                            }
                                            next_fix_idx2 += 1
                                    else:
                                        fixed_buffer2[idx_k2] = tc_entry2
                            else:
                                fixed_buffer2[idx_k2] = tc_entry2
                        tool_calls_buffer = fixed_buffer2
                        for idx in sorted(tool_calls_buffer.keys()):
                            yield {"type": "tool_call", "tool_call": tool_calls_buffer[idx]}
                    yield {"type": "done", "finish_reason": last_finish_reason or "stop", "usage": pending_usage}
                    return

            except requests.exceptions.Timeout:
                if attempt < self._max_retries - 1:
                    time.sleep(self._retry_delay * (attempt + 1))
                    continue
                yield {"type": "error", "error": f"请求超时（已重试 {self._max_retries} 次）"}
                return
            except requests.exceptions.ConnectionError as e:
                if attempt < self._max_retries - 1:
                    time.sleep(self._retry_delay * (attempt + 1))
                    continue
                yield {"type": "error", "error": f"连接错误: {str(e)}"}
                return
            except Exception as e:
                err_str = str(e)
                # InvalidChunkLength / ChunkedEncodingError 等连接中断可重试
                is_transient = any(k in err_str for k in (
                    'InvalidChunkLength', 'ChunkedEncodingError',
                    'Connection broken', 'IncompleteRead',
                    'ConnectionReset', 'RemoteDisconnected',
                ))
                if is_transient and attempt < self._max_retries - 1:
                    wait = self._retry_delay * (attempt + 1)
                    print(f"[AI Client] 连接中断 ({err_str[:80]}), {wait}s 后重试 ({attempt+1}/{self._max_retries})")
                    time.sleep(wait)
                    continue
                yield {"type": "error", "error": f"请求失败: {err_str}"}
                return

    def chat(self,
             messages: List[Dict[str, str]],
             model: str = 'gpt-5.2',
             provider: str = 'openai',
             temperature: float = 0.17,
             max_tokens: Optional[int] = None,
             timeout: int = 60,
             tools: Optional[List[dict]] = None,
             tool_choice: str = 'auto',
             response_format: Optional[dict] = None) -> Dict[str, Any]:
        """非流式 Chat（兼容旧接口）"""

        if not HAS_REQUESTS:
            return {'ok': False, 'error': '需要安装 requests 库'}

        provider = (provider or 'openai').lower()
        api_key = self._get_api_key(provider)
        if not api_key and provider not in ('ollama', 'custom'):
            return {'ok': False, 'error': f'缺少 API Key'}

        payload = {
            'model': model,
            'messages': messages,
            'temperature': temperature,
        }
        if max_tokens:
            payload['max_tokens'] = max_tokens
        if response_format:
            payload['response_format'] = response_format

        # GLM-4.7 专属参数（仅原生 GLM 接口）
        if self.is_glm47(model) and provider == 'glm':
            payload['thinking'] = {'type': 'enabled'}

        # DeepSeek V4-Pro：非流式也启用思考（Pro 的核心能力）
        if provider == 'deepseek' and 'v4-pro' in model.lower():
            payload['thinking'] = {'type': 'enabled'}
            payload['reasoning_effort'] = 'high'

        # DeepSeek / OpenAI prompt caching 自动启用

        if tools:
            payload['tools'] = tools
            payload['tool_choice'] = tool_choice

        headers = {
            'Content-Type': 'application/json',
        }
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'

        # OpenRouter 需要额外的请求头用于标识来源（参见 https://openrouter.ai/docs/quickstart）
        if provider == 'openrouter':
            headers['HTTP-Referer'] = 'https://github.com/Kazama-Suichiku/Houdini-Agent'
            headers['X-OpenRouter-Title'] = 'Houdini Agent'

        # ★ Anthropic 协议分支（非流式）
        if self._is_anthropic_protocol(provider, model):
            return self._chat_anthropic(
                messages=messages, model=model, provider=provider,
                temperature=temperature, max_tokens=max_tokens or 4096,
                tools=tools, tool_choice=tool_choice, api_key=api_key,
                timeout=timeout,
            )

        for attempt in range(self._max_retries):
            try:
                response = self._http_session.post(
                    self._get_api_url(provider, model),
                    json=payload,
                    headers=headers,
                    timeout=timeout,
                    proxies={'http': None, 'https': None}
                )
                response.raise_for_status()
                obj = response.json()

                choice = obj.get('choices', [{}])[0]
                message = choice.get('message', {})

                return {
                    'ok': True,
                    'content': message.get('content'),
                    'tool_calls': message.get('tool_calls'),
                    'finish_reason': choice.get('finish_reason'),
                    'usage': self._parse_usage(obj.get('usage', {})),
                    'raw': obj
                }
            except requests.exceptions.Timeout:
                if attempt < self._max_retries - 1:
                    time.sleep(self._retry_delay)
                    continue
                return {'ok': False, 'error': '请求超时'}
            except Exception as e:
                if attempt < self._max_retries - 1:
                    time.sleep(self._retry_delay)
                    continue
                return {'ok': False, 'error': str(e)}

        return {'ok': False, 'error': '请求失败'}
