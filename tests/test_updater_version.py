# -*- coding: utf-8 -*-
"""更新器本地版本读取：兼容源码与打包，避免误报 0.0.0。"""
import sys

from houdini_agent.utils import updater


def test_reads_source_version_nonzero():
    # 源码树下应读到真实 VERSION，绝不是 0.0.0
    v = updater.get_local_version()
    assert v and v != "0.0.0"


def test_meipass_takes_priority(tmp_path, monkeypatch):
    # 模拟 PyInstaller：VERSION 在 _MEIPASS 根，应优先命中
    (tmp_path / "VERSION").write_text("9.9.9\n", encoding="utf-8")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert updater.get_local_version() == "9.9.9"


def test_fallback_zero_when_no_version_anywhere(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(updater, "_version_candidates", lambda: [empty])
    assert updater.get_local_version() == "0.0.0"


def test_candidates_include_meipass_when_frozen(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    roots = [str(p) for p in updater._version_candidates()]
    assert str(tmp_path) == roots[0]      # 打包时 _MEIPASS 必须排在最前
