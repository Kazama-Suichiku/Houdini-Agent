# -*- coding: utf-8 -*-
"""Meshy 工具 schema 与工具集合的自洽性测试。"""
from houdini_agent.meshy import schemas as s


def _names():
    return {t["function"]["name"] for t in s.MESHY_TOOLS}


def test_all_tools_have_wellformed_schema():
    for t in s.MESHY_TOOLS:
        assert t.get("type") == "function"
        fn = t.get("function") or {}
        assert isinstance(fn.get("name"), str) and fn["name"]
        assert isinstance(fn.get("description"), str) and fn["description"]
        params = fn.get("parameters") or {}
        assert params.get("type") == "object"
        assert isinstance(params.get("properties"), dict)


def test_tool_names_unique():
    names = [t["function"]["name"] for t in s.MESHY_TOOLS]
    assert len(names) == len(set(names))


def test_expected_tool_count_and_membership():
    names = _names()
    assert names == s.ALL_TOOL_NAMES
    assert len(names) == 11
    for expected in ("meshy_text_to_3d", "meshy_image_to_3d", "meshy_text_to_image",
                     "meshy_image_to_image", "meshy_concept_to_3d", "meshy_retexture",
                     "meshy_remesh", "meshy_balance", "meshy_task_status",
                     "import_3d_asset", "export_node_to_glb"):
        assert expected in names


def test_category_sets_are_subsets_of_all():
    for category in (s.NETWORK_TOOLS, s.INTERACTIVE_TOOLS, s.HOUDINI_TOOLS,
                     s.CONFIRM_TOOLS, s.MUTATING_TOOLS):
        assert category <= s.ALL_TOOL_NAMES


def test_network_and_houdini_tools_disjoint():
    # 网络工具(app 侧)与 Houdini 工具(主线程)不应重叠
    assert not (s.NETWORK_TOOLS & s.HOUDINI_TOOLS)


def test_interactive_tools_are_network_tools():
    # 交互工具都属于网络工具(都要烧 credits、走 app 侧)
    assert s.INTERACTIVE_TOOLS <= s.NETWORK_TOOLS


def test_confirm_tools_exclude_free_ones():
    # balance / task_status 免费，不应进确认门
    assert "meshy_balance" not in s.CONFIRM_TOOLS
    assert "meshy_task_status" not in s.CONFIRM_TOOLS


def test_houdini_tools_exact():
    assert s.HOUDINI_TOOLS == frozenset({"import_3d_asset", "export_node_to_glb"})
