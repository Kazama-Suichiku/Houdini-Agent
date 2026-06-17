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
    assert len(names) == 15
    for expected in ("meshy_text_to_3d", "meshy_image_to_3d", "meshy_text_to_image",
                     "meshy_image_to_image", "meshy_concept_to_3d", "meshy_retexture",
                     "meshy_remesh", "meshy_balance", "meshy_task_status",
                     "import_3d_asset", "export_node_to_glb",
                     # 2.0.6：角色绑定 + 动画
                     "meshy_rig", "meshy_search_animations", "meshy_animate",
                     "import_rigged_character"):
        assert expected in names


def test_category_sets_are_subsets_of_all():
    for category in (s.NETWORK_TOOLS, s.INTERACTIVE_TOOLS, s.HOUDINI_TOOLS,
                     s.CONFIRM_TOOLS, s.MUTATING_TOOLS, s.LOCAL_TOOLS):
        assert category <= s.ALL_TOOL_NAMES


def test_network_and_houdini_tools_disjoint():
    # 网络工具(app 侧)与 Houdini 工具(主线程)不应重叠
    assert not (s.NETWORK_TOOLS & s.HOUDINI_TOOLS)


def test_interactive_tools_are_network_tools():
    # 交互工具都属于网络工具(都要烧 credits、走 app 侧)
    assert s.INTERACTIVE_TOOLS <= s.NETWORK_TOOLS


def test_confirm_tools_exclude_free_ones():
    # balance / task_status / 动作库检索 免费，不应进确认门
    assert "meshy_balance" not in s.CONFIRM_TOOLS
    assert "meshy_task_status" not in s.CONFIRM_TOOLS
    assert "meshy_search_animations" not in s.CONFIRM_TOOLS


def test_houdini_tools_exact():
    assert s.HOUDINI_TOOLS == frozenset(
        {"import_3d_asset", "import_rigged_character", "export_node_to_glb"})


def test_local_tools_free_and_isolated():
    # 本地检索工具：免费、不联网、与网络/Houdini 工具不重叠
    assert s.LOCAL_TOOLS == frozenset({"meshy_search_animations"})
    assert not (s.LOCAL_TOOLS & s.NETWORK_TOOLS)
    assert not (s.LOCAL_TOOLS & s.HOUDINI_TOOLS)
    assert not (s.LOCAL_TOOLS & s.CONFIRM_TOOLS)


def test_rig_animate_are_confirmable_network_tools():
    # 绑定/套动作：烧 credits → 网络工具 + 确认门
    for t in ("meshy_rig", "meshy_animate"):
        assert t in s.NETWORK_TOOLS
        assert t in s.CONFIRM_TOOLS
    # 套动作产物导入会建节点 → 计入会修改场景的工具
    assert "import_rigged_character" in s.MUTATING_TOOLS
