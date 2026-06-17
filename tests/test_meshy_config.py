# -*- coding: utf-8 -*-
"""Meshy API Key 解析/脱敏/读写测试。"""
import os

from houdini_agent.meshy import config as c
from shared import common_utils as cu


def test_no_key_by_default():
    assert c.get_api_key() is None
    assert c.has_api_key() is False
    assert c.masked_key() == ""


def test_env_var_takes_precedence(monkeypatch):
    cu.save_config("ai", {"meshy_api_key": "ini_key_xxxx"}, dcc_type="houdini")
    monkeypatch.setenv("MESHY_API_KEY", "env_key_yyyy")
    assert c.get_api_key() == "env_key_yyyy"   # env 优先于 ini


def test_ini_fallback_when_no_env():
    cu.save_config("ai", {"meshy_api_key": "ini_key_zzzz"}, dcc_type="houdini")
    assert c.get_api_key() == "ini_key_zzzz"
    assert c.has_api_key() is True


def test_key_is_stripped():
    cu.save_config("ai", {"meshy_api_key": "  spaced_key  "}, dcc_type="houdini")
    assert c.get_api_key() == "spaced_key"


def test_set_api_key_persists_to_ini():
    try:
        assert c.set_api_key("msy_secret_1234", persist=True)
        os.environ.pop("MESHY_API_KEY", None)        # 去掉 env 以验证落盘
        cfg, _ = cu.load_config("ai", dcc_type="houdini")
        assert cfg.get("meshy_api_key") == "msy_secret_1234"
    finally:
        os.environ.pop("MESHY_API_KEY", None)


def test_set_api_key_rejects_empty():
    assert c.set_api_key("", persist=True) is False
    assert c.set_api_key("   ", persist=True) is False


def test_clear_api_key(monkeypatch):
    cu.save_config("ai", {"meshy_api_key": "to_be_cleared"}, dcc_type="houdini")
    monkeypatch.setenv("MESHY_API_KEY", "to_be_cleared")
    c.clear_api_key()
    assert os.environ.get("MESHY_API_KEY") is None
    cfg, _ = cu.load_config("ai", dcc_type="houdini")
    assert "meshy_api_key" not in cfg
    assert c.get_api_key() is None


def test_masked_key_format():
    cu.save_config("ai", {"meshy_api_key": "msy_1234567890abcd"}, dcc_type="houdini")
    masked = c.masked_key()
    assert masked == "msy_" + "*" * 4 + "abcd"
    assert "1234567890" not in masked     # 中间不泄露


def test_masked_short_key_all_stars():
    cu.save_config("ai", {"meshy_api_key": "short"}, dcc_type="houdini")
    assert c.masked_key() == "*" * len("short")
