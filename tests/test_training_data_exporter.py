# -*- coding: utf-8 -*-
"""训练数据导出器：回归测试，锁住四类数据质量修复。

历史上 trainData 的导出数据暴露过四个问题（见 2026-06-18 排查）：
  P1 真实 system prompt 丢失，被极简默认值顶替；
  P2 孤儿 tool 结果被 content.split(':')[0] 伪造成中文函数名；
  P3 样本在 system 之后不以 user 开头（上下文窗口从轮次中段截断）；
  P4 agent loop 注入到 tool 正文的 <think>/失败引导样板污染数据。
"""
import json
import tempfile

import pytest

from houdini_agent.utils.training_data_exporter import (
    ChatTrainingExporter,
    _strip_injected_guidance,
)

_THINK_INJ = (
    "\n\n[重要：你的下一条回复必须以 <think> 标签开头。"
    "在标签内分析以上执行结果和当前进度，"
    "检查 Todo 列表中哪些步骤已完成（用 update_todo 标记为 done），"
    "确认下一步计划后再继续执行。不要跳过 <think> 标签。]"
)
_FAIL_INJ = (
    "\n\n[注意：上述工具调用返回了错误，这是工具调用层面的参数或执行错误，"
    "不是Houdini节点cooking错误，无需调用check_errors。"
    "请直接根据错误信息修正参数后重新调用该工具。]"
)


def _export(history, system_prompt=None, split_by_user=True):
    exp = ChatTrainingExporter(output_dir=tempfile.mkdtemp())
    path = exp.export_conversation(history, system_prompt=system_prompt,
                                   split_by_user=split_by_user)
    return [json.loads(line) for line in open(path, encoding="utf-8")]


# ---- P4: 注入样板剥除 ----
def test_strip_injected_guidance_removes_think_block():
    assert _strip_injected_guidance("已截取视口快照: 960x540" + _THINK_INJ) == "已截取视口快照: 960x540"


def test_strip_injected_guidance_removes_fail_and_think():
    assert _strip_injected_guidance("图片生成失败: FAILED" + _FAIL_INJ + _THINK_INJ) == "图片生成失败: FAILED"


def test_strip_injected_guidance_keeps_clean_text():
    assert _strip_injected_guidance("输出:\nbox1 created") == "输出:\nbox1 created"


# ---- 端到端：四类问题同时存在的历史 ----
@pytest.fixture
def messy_history():
    return [
        # 上下文从一轮中段开始：以 assistant/tool 打头（P3）
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c0", "type": "function",
             "function": {"name": "capture_viewport", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c0", "content": "已截取视口快照: 960x540" + _THINK_INJ},
        {"role": "user", "content": "生成一个盒子"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "execute_python", "arguments": '{"code":"hou.node"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "输出:\nbox1 created" + _THINK_INJ},
        # 孤儿 tool：无配对 assistant，中文开头（P2）
        {"role": "tool", "tool_call_id": "orphanX",
         "content": "工具不存在: verify_and_summarize\n可用工具: create_node" + _THINK_INJ},
        {"role": "assistant", "content": "完成了"},
    ]


def test_p1_real_system_prompt_preserved(messy_history):
    samples = _export(messy_history, system_prompt="真实系统提示PROD")
    assert samples
    assert samples[0]["messages"][0]["role"] == "system"
    assert samples[0]["messages"][0]["content"] == "真实系统提示PROD"


def test_p3_first_message_after_system_is_user(messy_history):
    for s in _export(messy_history, system_prompt="x"):
        assert s["messages"][1]["role"] == "user", [m["role"] for m in s["messages"]]


def test_p2_no_nonascii_function_names(messy_history):
    for s in _export(messy_history, system_prompt="x"):
        for m in s["messages"]:
            if m["role"] == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    name = tc["function"]["name"]
                    assert name and all(ord(c) < 128 for c in name), repr(name)


def test_p4_no_injected_guidance_in_tool_content(messy_history):
    for s in _export(messy_history, system_prompt="x"):
        for m in s["messages"]:
            if m["role"] == "tool":
                assert "必须以 <think>" not in m["content"]
                assert "无需调用check_errors" not in m["content"]
