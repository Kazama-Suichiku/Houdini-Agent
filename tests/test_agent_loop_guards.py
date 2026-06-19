# -*- coding: utf-8 -*-
"""Agent loop 防护：轮询风暴熔断（P5）与多轮引导样板不落历史（P6）。

背景（2026-06-18 排查）：训练数据 S0 录到模型连续 36 次轮询 meshy_task_status；
且每条 tool 结果被原地拼接了 <think>/失败引导样板，污染历史与训练数据。
"""
import threading

import pytest

from houdini_agent.utils.ai_client_agent import AIClientAgentMixin


class _FakeAgent(AIClientAgentMixin):
    """最小可跑的 agent loop 宿主：把所有外部依赖 stub 掉，
    让 chat_stream 每轮都发同一个工具调用，模拟轮询风暴。"""

    def __init__(self):
        self._stop_event = threading.Event()
        self._RE_CLEAN_PATTERNS = []
        self.exec_count = 0
        self._tool_executor = self._fake_exec

    # 每轮模型都发完全相同的一个 meshy_task_status 调用
    def chat_stream(self, **kwargs):
        yield {"type": "tool_call", "tool_call": {
            "id": "call_poll",
            "type": "function",
            "function": {"name": "meshy_task_status", "arguments": '{"op": "abc"}'},
        }}

    def _fake_exec(self, tool_name, **kwargs):
        self.exec_count += 1
        return {"success": True, "result": "任务进行中 50%"}

    # ---- stub 掉 loop 依赖的兄弟 mixin 方法 ----
    def _strip_image_content(self, msgs, keep_recent_user=0):
        return 0

    def _sanitize_working_messages(self, msgs):
        return msgs

    def _estimate_messages_tokens(self, msgs, tools):
        return 0

    def _smart_compress_in_loop(self, msgs, *a, **k):
        return msgs

    def _ensure_tool_call_ids(self, calls):
        pass

    def is_reasoning_model(self, model):
        return False

    def _compress_tool_result(self, name, result):
        return str(result.get("result", result))

    def _is_tool_success(self, result):
        return bool(result.get("success"))


def _run(max_iter=12, enable_thinking=True):
    agent = _FakeAgent()
    result = agent.agent_loop_stream(
        messages=[{"role": "system", "content": "s"},
                  {"role": "user", "content": "查询进度"}],
        model="gpt-5.2", provider="openai",
        max_iterations=max_iter, enable_thinking=enable_thinking,
        supports_vision=False, tools_override=[],
    )
    return agent, result


def test_p5_polling_storm_is_capped():
    """模型连发 12 轮相同调用，真实执行次数被熔断封顶（不再是 36 次）。"""
    agent, result = _run(max_iter=12)
    # 达到连续上限后，后续相同调用在执行前就被重复失败保护拦截，不会真正执行
    assert agent.exec_count <= AIClientAgentMixin._CONSECUTIVE_SAME_CALL_LIMIT, agent.exec_count


def test_p5_block_message_appears():
    """熔断后，持久化的 tool 消息里出现明确的停止/拦截提示。"""
    agent, result = _run(max_iter=12)
    tool_texts = [m["content"] for m in result["new_messages"] if m.get("role") == "tool"]
    joined = "\n".join(tool_texts)
    assert ("停止重复调用" in joined) or ("重复失败保护" in joined), joined[:300]


def test_p6_guidance_not_persisted():
    """多轮 <think> 引导样板只在 loop 内发给模型，绝不写进持久化 new_messages。"""
    agent, result = _run(max_iter=6, enable_thinking=True)
    for m in result["new_messages"]:
        if m.get("role") == "tool":
            assert "必须以 <think>" not in m["content"]
            assert "无需调用check_errors" not in m["content"]
