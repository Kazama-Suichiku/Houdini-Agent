# -*- coding: utf-8 -*-
"""Meshy 用量埋点：事件构建、spool 往返、去重、opt-out、安装 ID、上传。"""
import json
import os

from shared import common_utils as cu


def _read_spool_lines(t):
    path = t._spool_path()
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [ln for ln in f.read().splitlines() if ln.strip()]


def test_build_event_shape(telemetry_mod):
    t = telemetry_mod
    ev = t._build_event("text-to-3d", {"id": "task42", "consumed_credits": 5,
                                       "prompt": "a rock", "ai_model": "m", "mode": "preview"})
    assert ev["kind"] == "text-to-3d"
    assert ev["task_id"] == "task42"
    assert ev["credits"] == 5
    # 隐私默认：不上报提示词原文，只上报长度
    assert "prompt" not in ev
    assert ev["prompt_len"] == len("a rock")
    assert ev["status"] == "SUCCEEDED"
    assert len(ev["event_id"]) == 32          # uuid4 hex
    assert ev["install_id"]


def test_prompt_sent_only_when_enabled(telemetry_mod, monkeypatch):
    monkeypatch.setenv("HAGENT_TELEMETRY_SEND_PROMPT", "1")
    ev = telemetry_mod._build_event("text-to-image", {"id": "p", "prompt": "a rock"})
    assert ev["prompt"] == "a rock"


def test_build_event_credits_coerced(telemetry_mod):
    ev = telemetry_mod._build_event("remesh", {"id": "x", "consumed_credits": None})
    assert ev["credits"] == 0
    ev2 = telemetry_mod._build_event("remesh", {"id": "y", "consumed_credits": "bad"})
    assert ev2["credits"] == 0


def test_prompt_truncated(telemetry_mod, monkeypatch):
    monkeypatch.setenv("HAGENT_TELEMETRY_SEND_PROMPT", "1")
    long = "z" * 5000
    ev = telemetry_mod._build_event("text-to-image", {"id": "p", "prompt": long})
    assert len(ev["prompt"]) == telemetry_mod._PROMPT_MAX
    assert ev["prompt_len"] == telemetry_mod._PROMPT_MAX


def test_install_id_stable_and_persisted(telemetry_mod):
    a = telemetry_mod.install_id()
    telemetry_mod._install_id_cache[0] = None      # 清缓存，强制从 ini 再读
    b = telemetry_mod.install_id()
    assert a == b
    cfg, _ = cu.load_config("ai", dcc_type="houdini")
    assert cfg.get("telemetry_install_id") == a


def test_is_enabled_default_true(telemetry_mod):
    assert telemetry_mod.is_enabled() is True


def test_is_enabled_env_optout(telemetry_mod, monkeypatch):
    monkeypatch.setenv("HAGENT_TELEMETRY_OFF", "1")
    assert telemetry_mod.is_enabled() is False


def test_is_enabled_ini_optout(telemetry_mod):
    assert telemetry_mod.set_optout(True)
    assert telemetry_mod.is_enabled() is False
    assert telemetry_mod.set_optout(False)
    assert telemetry_mod.is_enabled() is True


def test_record_task_writes_spool(telemetry_mod):
    telemetry_mod.record_task("text-to-3d", {"id": "t1", "consumed_credits": 3, "prompt": "hi"})
    telemetry_mod._wait_writes()      # 落盘是异步的，等写线程完成再断言
    lines = _read_spool_lines(telemetry_mod)
    assert len(lines) == 1
    ev = json.loads(lines[0])
    assert ev["task_id"] == "t1" and ev["credits"] == 3


def test_record_task_dedups_same_task(telemetry_mod):
    for _ in range(3):
        telemetry_mod.record_task("text-to-3d", {"id": "dup", "consumed_credits": 1})
        telemetry_mod._wait_writes()
    assert len(_read_spool_lines(telemetry_mod)) == 1     # 同 task 只入账一次


def test_record_task_optout_writes_nothing(telemetry_mod, monkeypatch):
    monkeypatch.setenv("HAGENT_TELEMETRY_OFF", "1")
    telemetry_mod.record_task("text-to-3d", {"id": "t2", "consumed_credits": 9})
    telemetry_mod._wait_writes()
    assert _read_spool_lines(telemetry_mod) == []


def test_flush_once_uploads_and_clears(telemetry_mod):
    telemetry_mod.record_task("text-to-3d", {"id": "a", "consumed_credits": 1})
    telemetry_mod.record_task("image-to-3d", {"id": "b", "consumed_credits": 2})
    telemetry_mod._wait_writes()
    sent, remaining = telemetry_mod.flush_once()
    assert sent == 2 and remaining == 0
    assert _read_spool_lines(telemetry_mod) == []          # 上传成功后 spool 清空
    # _post 被捕获：一批两条
    assert telemetry_mod.posted and len(telemetry_mod.posted[0][1]) == 2


def test_flush_once_failure_keeps_spool(telemetry_mod, monkeypatch):
    telemetry_mod.record_task("text-to-3d", {"id": "c", "consumed_credits": 1})
    telemetry_mod._wait_writes()
    monkeypatch.setattr(telemetry_mod, "_post", lambda url, events: False)   # 模拟上传失败
    sent, remaining = telemetry_mod.flush_once()
    assert sent == 0 and remaining == 1
    assert len(_read_spool_lines(telemetry_mod)) == 1       # 失败原样保留


def test_local_pending_summary(telemetry_mod):
    telemetry_mod.record_task("text-to-3d", {"id": "a", "consumed_credits": 4})
    telemetry_mod.record_task("remesh", {"id": "b", "consumed_credits": 6})
    telemetry_mod._wait_writes()
    summ = telemetry_mod.local_pending_summary()
    assert summ["pending"] == 2
    assert summ["pending_credits"] == 10
