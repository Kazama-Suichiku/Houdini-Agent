# -*- coding: utf-8 -*-
"""更新检查与下载：官网清单优先、GitHub 备用、失败时不再谎称"已是最新"，
下载按来源依次尝试并校验大小 / sha256。全部离线（requests 用假对象）。"""
import ast
import glob
import hashlib
import json
import os

import pytest

from houdini_agent.utils import updater

MANIFEST_URL = updater._MANIFEST_URL
GITHUB_URL = updater._API_LATEST_RELEASE


class Resp:
    def __init__(self, status=200, data=None, headers=None, content=b""):
        self.status_code = status
        self._data = data
        self.headers = headers or {}
        self._content = content

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP %d" % self.status_code)

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self._content), chunk_size):
            yield self._content[i:i + chunk_size]


class ReadTimeout(Exception):
    pass


class FakeRequests:
    """按 URL 路由的假 requests：值可以是 Resp 或要抛出的异常。"""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(url)
        r = self.routes.get(url)
        if r is None:
            raise ConnectionError("no route " + url)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "_ETAG_CACHE_FILE", tmp_path / "update_cache.json")
    monkeypatch.setattr(updater, "get_local_version", lambda: "2.0.16")

    def use(routes):
        fake = FakeRequests(routes)
        monkeypatch.setattr(updater, "_requests", lambda: fake)
        return fake
    return use


def manifest(ver="2.0.17", sha="ab" * 32, size=5 * 1024 * 1024, notes="## 修复更新按钮\n更多"):
    return {"version": ver, "name": "v" + ver, "notes": notes,
            "installer": {"url": "https://houdini-agent.com/download/HoudiniAgent-Setup-%s.exe" % ver,
                          "size": size, "sha256": sha},
            "source_zip": "https://github.com/x/archive/refs/tags/v%s.zip" % ver}


def release(ver="2.0.17", asset_size=5 * 1024 * 1024):
    return {"tag_name": "v" + ver, "name": "v" + ver, "body": "## 标题\n正文",
            "zipball_url": "https://api.github.com/zip",
            "assets": [{"name": "HoudiniAgent-Setup-%s.exe" % ver, "size": asset_size,
                        "browser_download_url": "https://github.com/dl/HoudiniAgent-Setup-%s.exe" % ver}]}


# ---------------------------------------------------------------- check_update
class TestCheckUpdate:
    def test_manifest_is_primary_and_github_not_called(self, env):
        fake = env({MANIFEST_URL: Resp(200, manifest())})
        r = updater.check_update()
        assert r["has_update"] and r["remote_version"] == "2.0.17"
        assert r["source"] == "manifest" and not r["stale"] and not r["error"]
        assert r["release_notes"] == "修复更新按钮"        # Markdown 标题符号被去掉
        assert GITHUB_URL not in fake.calls
        cached = json.loads(updater._ETAG_CACHE_FILE.read_text(encoding="utf-8"))
        assert cached["manifest"]["version"] == "2.0.17"

    def test_github_fallback_when_manifest_unreachable(self, env):
        env({MANIFEST_URL: ReadTimeout("t"), GITHUB_URL: Resp(200, release(), {"ETag": 'W/"1"'})})
        r = updater.check_update()
        assert r["has_update"] and r["source"] == "github" and r["release_notes"] == "标题"

    def test_both_down_no_cache_reports_error_not_latest(self, env):
        env({MANIFEST_URL: Resp(404), GITHUB_URL: Resp(403)})
        r = updater.check_update()
        assert not r["has_update"]
        assert "无法连接更新服务器" in r["error"] and "403" in r["error"]

    def test_both_down_with_newer_cache_still_offers_update(self, env):
        updater._save_etag_cache({"manifest": manifest("2.0.99")})
        env({MANIFEST_URL: ReadTimeout("t"), GITHUB_URL: ReadTimeout("t")})
        r = updater.check_update()
        assert r["has_update"] and r["remote_version"] == "2.0.99"
        assert r["stale"] and r["source"] == "cache" and not r["error"]

    def test_both_down_with_old_cache_does_not_claim_latest(self, env):
        updater._save_etag_cache({"etag": 'W/"x"', "release_data": release("2.0.15")})
        env({MANIFEST_URL: ReadTimeout("t"), GITHUB_URL: Resp(403)})
        r = updater.check_update()
        assert not r["has_update"] and r["error"]          # 以前这里会静默显示"已是最新版本"

    def test_github_304_uses_cache_as_fresh(self, env):
        updater._save_etag_cache({"etag": 'W/"x"', "release_data": release("2.0.17")})
        env({MANIFEST_URL: Resp(500), GITHUB_URL: Resp(304)})
        r = updater.check_update()
        assert r["has_update"] and r["source"] == "github" and not r["stale"]

    def test_up_to_date(self, env):
        env({MANIFEST_URL: Resp(200, manifest("2.0.16"))})
        r = updater.check_update()
        assert not r["has_update"] and not r["error"]

    def test_bad_manifest_falls_back(self, env):
        env({MANIFEST_URL: Resp(200, {"version": ""}), GITHUB_URL: Resp(200, release())})
        assert updater.check_update()["source"] == "github"


# ---------------------------------------------------------------- download sources + verification
class TestInstallerSources:
    def test_order_and_checksums_when_both_agree(self):
        srcs = updater.installer_sources({"manifest": manifest(), "release_data": release()})
        assert [s["label"] for s in srcs] == ["官网", "GitHub", "官网稳定链接"]
        assert all(s["sha256"] == "ab" * 32 for s in srcs)

    def test_github_newer_than_manifest(self):
        srcs = updater.installer_sources({"manifest": manifest("2.0.16"), "release_data": release("2.0.17")})
        assert srcs[0]["label"] == "GitHub"
        assert srcs[-1]["url"] == updater._STABLE_INSTALLER_URL and srcs[-1]["sha256"] == ""

    def test_no_cache_falls_back_to_stable(self):
        srcs = updater.installer_sources({})
        assert [s["url"] for s in srcs] == [updater._STABLE_INSTALLER_URL]


class TestDownloadInstaller:
    def test_wrong_checksum_tries_next_source(self, env, tmp_path, monkeypatch):
        good = os.urandom(2 * 1024 * 1024)
        stale = os.urandom(2 * 1024 * 1024)
        sha = hashlib.sha256(good).hexdigest()
        m = manifest(sha=sha, size=len(good))
        updater._save_etag_cache({"manifest": m})
        monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))
        # 官网版本化链接挂了，稳定链接给的是旧包 → 都不行；没有其它来源时应失败
        env({m["installer"]["url"]: ConnectionError("down"),
             updater._STABLE_INSTALLER_URL: Resp(200, content=stale)})
        r = updater.download_installer()
        assert not r["success"] and "校验值不符" in r["error"]
        assert not os.path.exists(os.path.join(str(tmp_path), "HoudiniAgent-Setup-Update.exe"))
        # 稳定链接换成新包后成功
        env({m["installer"]["url"]: ConnectionError("down"),
             updater._STABLE_INSTALLER_URL: Resp(200, content=good)})
        progress = []
        r = updater.download_installer(progress_callback=progress.append)
        assert r["success"] and open(r["path"], "rb").read() == good
        assert progress[-1] == 100

    def test_size_mismatch_rejected(self, env, tmp_path, monkeypatch):
        data = os.urandom(2 * 1024 * 1024)
        updater._save_etag_cache({"release_data": release(asset_size=len(data) + 1)})
        monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))
        env({"https://github.com/dl/HoudiniAgent-Setup-2.0.17.exe": Resp(200, content=data),
             updater._STABLE_INSTALLER_URL: Resp(404)})
        r = updater.download_installer()
        assert not r["success"] and "文件大小不符" in r["error"]


# ---------------------------------------------------------------- bug-class guard
def test_no_qtimer_singleshot_inside_thread_targets():
    """在 Python 后台线程里调 QTimer.singleShot，回调永远不会执行（线程没有事件循环）。
    更新横幅就是因此从未出现。回主线程一律用 Signal。"""
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "houdini_agent")
    hits = []
    for path in glob.glob(os.path.join(root, "**", "*.py"), recursive=True):
        try:
            tree = ast.parse(open(path, encoding="utf-8").read())
        except Exception:
            continue
        targets = {kw.value.id for node in ast.walk(tree)
                   if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "Thread"
                   for kw in node.keywords if kw.arg == "target" and isinstance(kw.value, ast.Name)}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in targets:
                for sub in ast.walk(node):
                    if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                            and sub.func.attr == "singleShot"):
                        hits.append("%s:%d" % (os.path.relpath(path, root), sub.lineno))
    assert hits == []


# ---------------------------------------------------------------- telemetry shared id
def test_shared_leaked_install_id_is_regenerated(tmp_path, monkeypatch):
    from houdini_agent.meshy import telemetry
    leaked = next(iter(telemetry._SHARED_LEAKED_IDS))
    (tmp_path / "install_id").write_text(leaked, encoding="utf-8")
    monkeypatch.setattr(telemetry, "_user_state_dir", lambda: str(tmp_path))
    monkeypatch.setattr(telemetry, "load_config", lambda *a, **k: ({"telemetry_install_id": leaked}, ""))
    monkeypatch.setattr(telemetry, "_install_id_cache", [""])
    iid = telemetry.install_id()
    assert iid and iid != leaked
    assert (tmp_path / "install_id").read_text(encoding="utf-8") == iid
