# -*- coding: utf-8 -*-
"""ToolRegistry：注册、模式访问控制、标签、启用/禁用。"""
import pytest

from houdini_agent.utils.tool_registry import ToolRegistry, get_tool_registry


def _schema(name):
    return {"type": "function",
            "function": {"name": name, "description": "d",
                         "parameters": {"type": "object", "properties": {}}}}


@pytest.fixture
def reg():
    # 用独立实例，避免污染全局单例
    return ToolRegistry()


def test_register_and_mode_guard(reg):
    reg.register("foo", _schema("foo"), modes={"agent"}, tags={"meshy"})
    assert reg.is_tool_allowed_in_mode("foo", "agent") is True
    assert reg.is_tool_allowed_in_mode("foo", "ask") is False
    assert reg.is_tool_allowed_in_mode("missing", "agent") is False


def test_get_tools_for_mode_only_enabled(reg):
    reg.register("a", _schema("a"), modes={"agent"})
    reg.register("b", _schema("b"), modes={"ask"})
    agent_names = {t["function"]["name"] for t in reg.get_tools_for_mode("agent")}
    assert "a" in agent_names and "b" not in agent_names


def test_disable_blocks_tool(reg):
    reg.register("c", _schema("c"), modes={"agent"})
    assert reg.is_tool_allowed_in_mode("c", "agent")
    reg.set_enabled("c", False)
    assert reg.is_tool_allowed_in_mode("c", "agent") is False


def test_unregister(reg):
    reg.register("d", _schema("d"), modes={"agent"})
    reg.unregister("d")
    assert reg.is_tool_allowed_in_mode("d", "agent") is False


def test_unregister_by_source(reg):
    reg.register("p1", _schema("p1"), source="plugin", plugin_name="P", modes={"agent"})
    reg.register("core1", _schema("core1"), source="core", modes={"agent"})
    reg.unregister_by_source("plugin", "P")
    assert "p1" not in reg._tools
    assert "core1" in reg._tools


def test_get_tool_registry_is_singleton():
    assert get_tool_registry() is get_tool_registry()
