# -*- coding: utf-8 -*-
"""集成包安装：用户目录多候选解析（文档重定向）、按版本并集装包、pythonrc 防重入与 shim。"""
import json
import sys
from pathlib import Path

import pytest

from houdini_agent.launcher import houdini_discovery as hd

_REPO = Path(__file__).resolve().parents[1]
_PKG_ROOT = _REPO / "houdini_agent" / "houdini_package"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("HOUDINI_USER_PREF_DIR", raising=False)


# ------------------------------------------------------------ pref dir 解析

def test_user_pref_dirs_env_override_first(monkeypatch, tmp_path):
    monkeypatch.setenv("HOUDINI_USER_PREF_DIR", str(tmp_path / "custom" / "houdini__HVER__"))
    dirs = hd.user_pref_dirs("21.0")
    assert dirs[0] == tmp_path / "custom" / "houdini21.0"   # env 最优先且展开 __HVER__


def test_user_pref_dirs_multiple_documents(monkeypatch, tmp_path):
    docs_d, docs_c = tmp_path / "D_Documents", tmp_path / "C_Documents"
    monkeypatch.setattr(hd, "_documents_candidates", lambda: [docs_d, docs_c])
    dirs = hd.user_pref_dirs("20.5")
    assert dirs == [docs_d / "houdini20.5", docs_c / "houdini20.5"]


def test_user_pref_dirs_dedup(monkeypatch, tmp_path):
    docs = tmp_path / "Documents"
    monkeypatch.setattr(hd, "_documents_candidates", lambda: [docs, docs])
    assert hd.user_pref_dirs("21.0") == [docs / "houdini21.0"]


def test_known_pref_versions_scans_and_filters(monkeypatch, tmp_path):
    docs = tmp_path / "Documents"
    for name in ("houdini21.0", "houdini20.5", "houdini19.0", "houdini_backup", "notes"):
        (docs / name).mkdir(parents=True)
    monkeypatch.setattr(hd, "_documents_candidates", lambda: [docs])
    vers = hd.known_pref_versions()
    assert vers == {"21.0", "20.5"}          # 19.0 低于下限被滤掉，非版本目录忽略


# ------------------------------------------------------------------ 装包

def test_install_package_for_version_writes_all_candidates(monkeypatch, tmp_path):
    docs_d, docs_c = tmp_path / "D_Documents", tmp_path / "C_Documents"
    monkeypatch.setattr(hd, "_documents_candidates", lambda: [docs_d, docs_c])
    written = hd.install_package_for_version(str(_REPO), "21.0")
    assert len(written) == 2
    for docs in (docs_d, docs_c):
        pkg = docs / "houdini21.0" / "packages" / "HoudiniAgent.json"
        assert pkg.is_file()
        data = json.loads(pkg.read_text(encoding="utf-8"))
        envs = {k: v for e in data["env"] for k, v in e.items()}
        assert envs["HOUDINI_PATH"].endswith("houdini_package;&")


def test_install_all_packages_unions_versions(monkeypatch, tmp_path):
    """已发现安装(21.0) ∪ 用户目录出现过的版本(20.5) 都要装包。"""
    docs = tmp_path / "Documents"
    (docs / "houdini20.5").mkdir(parents=True)     # Steam/自定义安装跑过留下的目录
    monkeypatch.setattr(hd, "_documents_candidates", lambda: [docs])
    installs = [{"version": "21.0.440", "major_minor": "21.0",
                 "path": "x", "exe": "y"}]
    out = hd.install_all_packages(str(_REPO), installs)
    assert set(out) == {"21.0", "20.5"}
    assert (docs / "houdini21.0" / "packages" / "HoudiniAgent.json").is_file()
    assert (docs / "houdini20.5" / "packages" / "HoudiniAgent.json").is_file()


# ------------------------------------------------- pythonrc 防重入 + shim

def _exec_file(path):
    """精确复刻 Houdini 执行启动脚本的方式：globals 里【没有 __file__】。
    （2.0.12 真机翻车：脚本用了 __file__，Houdini exec 时 NameError 打断 bridge 启动。）"""
    src = Path(path).read_text(encoding="utf-8")
    exec(compile(src, str(path), "exec"), {"__name__": "__main__"})


@pytest.fixture
def _pythonrc_env(monkeypatch):
    """打桩：不真起服务器；屏蔽 QTimer 走直接调用分支；复位防重入哨兵。"""
    import types
    from houdini_agent.bridge import server
    calls = []
    monkeypatch.setattr(server, "start_bridge", lambda *a, **k: calls.append(1))
    fake_qtcore = types.ModuleType("PySide6.QtCore")
    fake_qtcore.QTimer = None                       # None → 走无 QTimer 的直接 _start 分支
    monkeypatch.setitem(sys.modules, "PySide6.QtCore", fake_qtcore)
    monkeypatch.setattr(sys, "_hagent_pythonrc_ran", False, raising=False)
    return calls


def test_pythonrc_runs_once(_pythonrc_env, tmp_path):
    rc = _PKG_ROOT / "scripts" / "python" / "pythonrc.py"
    _exec_file(rc)
    _exec_file(rc)                                  # 第二次被哨兵拦下
    assert _pythonrc_env == [1]
    log = Path(tmp_path / "appdata") / "HoudiniAgent" / "bridge.log"
    assert log.is_file() and "loaded from" in log.read_text(encoding="utf-8")


def test_pythonlibs_shims_forward_to_real_pythonrc(_pythonrc_env):
    shims = sorted(_PKG_ROOT.glob("python3.*libs/pythonrc.py"))
    assert len(shims) >= 5                          # 3.9–3.13 全覆盖（19.5 到未来版本）
    _exec_file(shims[0])                            # shim → 真 pythonrc → start_bridge
    assert _pythonrc_env == [1]
    for shim in shims[1:]:
        _exec_file(shim)                            # 其余 shim 都被哨兵拦下
    assert _pythonrc_env == [1]
