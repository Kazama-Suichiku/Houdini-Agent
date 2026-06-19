# -*- coding: utf-8 -*-
"""Meshy 链路加固回归测试（2026-06-18 审计后修复）。

覆盖：动作 id 越界守卫、参数钳制、统一失败契约、无 glb 摘要、内容审核提示、
余额解析、原子下载清理。"""
import os

import pytest

from houdini_agent.meshy import animation_lib, network_ops
from houdini_agent.meshy.client import _friendly_task_error


# ---------------- animation_lib：越界 id 不再白烧 ----------------

def test_resolve_real_catalog_known_id():
    resolved, unknown = animation_lib.resolve([0])
    assert resolved and resolved[0]["id"] == 0 and not unknown


def test_resolve_gap_id_within_verified_passes_through():
    # 418 是目录空洞但 ≤ verified_max_id(603)，应允许透传
    resolved, unknown = animation_lib.resolve([418])
    assert resolved and resolved[0]["id"] == 418


def test_resolve_id_beyond_verified_is_unknown():
    # 999 > 603：不应被当合法动作透传（否则提交云端白烧 credits）
    resolved, unknown = animation_lib.resolve([999])
    assert not resolved and unknown == [999]


def test_resolve_bool_not_treated_as_id():
    resolved, unknown = animation_lib.resolve([True])
    assert not resolved and unknown == [True]


def test_resolve_blocks_all_ids_when_catalog_missing(monkeypatch):
    # 动作库缺失时 verified_max_id=0，任何整数 id 都不该透传
    monkeypatch.setattr(animation_lib, "_CACHE",
                        {"actions": [], "id_range": [0, 696], "verified_max_id": 0})
    resolved, unknown = animation_lib.resolve([5, 100, 600])
    assert not resolved and unknown == [5, 100, 600]


def test_empty_query_returns_common_actions():
    out = animation_lib.search("", limit=5)
    assert out and len(out) <= 5


# ---------------- network_ops：参数钳制 ----------------

def test_clip_prompt_truncates_to_600():
    assert len(network_ops._clip_prompt("x" * 5000)) == 600


def test_clamp_poly_bounds():
    assert network_ops._clamp_poly(5) == 100
    assert network_ops._clamp_poly(99999999) == 300000
    assert network_ops._clamp_poly("bad") == 30000
    assert network_ops._clamp_poly(30000) == 30000


def test_norm_topology_invalid_falls_back():
    assert network_ops._norm_topology("weird", "triangle") == "triangle"
    assert network_ops._norm_topology("quad") == "quad"


# ---------------- network_ops：统一失败契约 ----------------

def test_err_has_result_and_data_keys():
    e = network_ops._err("boom")
    assert e["success"] is False
    assert e["result"] == "" and e["data"] == {} and e["error"] == "boom"


# ---------------- network_ops：无 glb 不给空路径导入指令 ----------------

def test_summary_without_glb_warns_no_import():
    s = network_ops._summary("文生3D", {"glb": None, "texture_dir": None})
    assert "未获得可导入的 glb" in s
    assert 'glb_path=""' not in s


def test_summary_with_glb_has_import_line():
    s = network_ops._summary("文生3D", {"glb": "/tmp/a.glb", "texture_dir": None})
    assert "import_3d_asset(glb_path=\"/tmp/a.glb\"" in s


# ---------------- client：内容审核提示只给 FAILED ----------------

def test_friendly_error_failed_image_gets_hint():
    m = _friendly_task_error("FAILED",
                             "The input file or parameters could not be processed.",
                             kind="text-to-image")
    assert "内容审核" in m


def test_friendly_error_canceled_no_hint():
    m = _friendly_task_error("CANCELED", "invalid input", kind="text-to-image")
    assert "内容审核" not in m
    assert m.startswith("任务CANCELED")


# ---------------- network_ops：余额无法解析时不把 -1 当数字播报 ----------------

class _FakeBalClient:
    def __init__(self, val):
        self._val = val

    def balance(self):
        return self._val


def test_run_balance_unknown_when_negative():
    r = network_ops._run_balance(_FakeBalClient(-1), {}, None, None)
    assert r["success"] is True
    assert "-1" not in r["result"]
    assert "无法获取" in r["result"]
    assert r["data"]["balance"] == -1


def test_run_balance_reports_real_value():
    r = network_ops._run_balance(_FakeBalClient(42), {}, None, None)
    assert r["success"] is True
    assert "42" in r["result"]
    assert r["data"]["balance"] == 42


# ---------------- client：原子下载失败不留半截文件 ----------------

def test_download_atomic_cleanup_on_failure(tmp_path, monkeypatch):
    from houdini_agent.meshy import client as cli

    class _BoomResp:
        status_code = 200
        def iter_content(self, chunk_size=0):
            yield b"partial"
            raise IOError("network died mid-stream")

    monkeypatch.setattr(cli, "requests", type("R", (), {
        "get": staticmethod(lambda *a, **k: _BoomResp())})())
    c = cli.MeshyClient.__new__(cli.MeshyClient)
    c.api_key = "k"
    c.timeout = 1
    dest = str(tmp_path / "model.glb")
    with pytest.raises(Exception):
        c.download("http://x/model.glb", dest)
    assert not os.path.isfile(dest)             # 目标路径不能留下半截文件
    assert not os.path.isfile(dest + ".part")   # 临时文件也要清掉
