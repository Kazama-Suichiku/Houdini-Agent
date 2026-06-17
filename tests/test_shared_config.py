# -*- coding: utf-8 -*-
"""shared.common_utils 的配置/历史读写测试（已隔离到临时目录）。"""
import os

from shared import common_utils as cu


def test_save_and_load_roundtrip():
    ok, path = cu.save_config("ai", {"k1": "v1", "k2": "v2"}, dcc_type="houdini")
    assert ok
    assert os.path.basename(path) == "houdini_ai.ini"
    cfg, p2 = cu.load_config("ai", dcc_type="houdini")
    assert cfg["k1"] == "v1"
    assert cfg["k2"] == "v2"
    assert p2 == path


def test_load_missing_returns_empty():
    cfg, path = cu.load_config("does_not_exist", dcc_type="houdini")
    assert cfg == {}
    assert path.endswith("houdini_does_not_exist.ini")


def test_value_may_contain_colon():
    # 实现按第一个冒号切分，URL 之类带冒号的值应完整保留
    cu.save_config("ai", {"url": "https://x.y/z"}, dcc_type="houdini")
    cfg, _ = cu.load_config("ai", dcc_type="houdini")
    assert cfg["url"] == "https://x.y/z"


def test_config_dir_is_isolated(tmp_path):
    # 隔离夹具应把 config 目录指到临时根下，绝不落在真实仓库
    d = cu.get_config_dir()
    assert "repo" in d and str(tmp_path) in d


def test_history_roundtrip():
    assert cu.add_to_history("chat", "hello", dcc_type="houdini")
    rows = cu.load_history("chat", dcc_type="houdini")
    assert rows and rows[0][0] == "hello"
