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


# ---------------------------------------------------------------- install phase decisions
class TestInstallWatchStep:
    def test_window_means_installing_quit_now(self):
        assert updater.install_watch_step(True, None) == ("quit", "")

    def test_still_waiting_for_uac(self):
        assert updater.install_watch_step(False, None) == ("wait", "")

    def test_cancelled_before_install_keeps_app_running(self):
        for code in (2, 5):
            action, reason = updater.install_watch_step(False, code)
            assert action == "failed" and "管理员授权" in reason

    def test_other_failure_reports_exit_code(self):
        action, reason = updater.install_watch_step(False, 3)
        assert action == "failed" and "3" in reason

    def test_finished_without_window_still_quits(self):
        assert updater.install_watch_step(False, 0) == ("quit", "")


def test_installer_window_detection_is_safe_without_installer():
    assert updater.installer_window_visible() is False


# ---------------------------------------------------------------- silent background staging & swap
def _make_app_zip(ver="2.1.5", payload_pad=1100 * 1024):
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("Houdini Agent.exe", b"MZ" + b"\x00" * payload_pad)
        zf.writestr("_internal/VERSION", ver + "\n")
        zf.writestr("_internal/app.py", "VERSION = %r\n" % ver)
    return buf.getvalue()


class TestSilentBackgroundUpdate:
    def test_stage_swap_fast_path_and_cache_and_cleanup(self, env, tmp_path, monkeypatch):
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        state_dir = tmp_path / "state"
        monkeypatch.setattr(updater, "_user_state_dir", lambda: state_dir)
        monkeypatch.setattr(updater, "app_install_dir", lambda: app_dir)
        monkeypatch.setattr(updater.sys, "frozen", True, raising=False)

        zbytes = _make_app_zip("2.1.5")
        zsha = hashlib.sha256(zbytes).hexdigest()
        zurl = "https://houdini-agent.com/download/HoudiniAgent-App-2.1.5.zip"
        m = manifest("2.1.5")
        m["app_zip"] = {"url": zurl, "size": len(zbytes), "sha256": zsha}
        updater._save_etag_cache({"manifest": m})
        fake = env({zurl: Resp(200, content=zbytes)})

        r1 = updater.stage_update()
        assert r1["success"] and not r1["cached"]
        st = r1["staged"]
        assert st["mode"] == "swap" and st["version"] == "2.1.5"
        assert (tmp_path / "state" / "updates" / "staged" / "2.1.5" / "Houdini Agent.exe").is_file()
        assert not (tmp_path / "state" / "updates" / "tmp" / "HoudiniAgent-App-2.1.5.zip").exists()
        assert updater.can_apply_silently_on_exit(st) is True

        # 二次调用命中本地暂存，不再发网络请求
        r2 = updater.stage_update()
        assert r2["success"] and r2["cached"] is True
        assert len(fake.calls) == 1

        # 升级完成后本机版本追平 → get_staged_update 自动清理暂存并返回 None
        monkeypatch.setattr(updater, "get_local_version", lambda: "2.1.5")
        assert updater.get_staged_update() is None
        assert not (state_dir / "updates" / "staged.json").exists()

    def test_stage_falls_back_to_installer_when_app_dir_readonly(self, env, tmp_path, monkeypatch):
        app_dir = tmp_path / "progfiles"
        app_dir.mkdir()
        state_dir = tmp_path / "state"
        monkeypatch.setattr(updater, "_user_state_dir", lambda: state_dir)
        monkeypatch.setattr(updater, "app_install_dir", lambda: app_dir)
        monkeypatch.setattr(updater, "is_dir_writable", lambda p: False)
        monkeypatch.setattr(updater.sys, "frozen", True, raising=False)

        inst_bytes = b"MZ" + b"x" * (1200 * 1024)
        isha = hashlib.sha256(inst_bytes).hexdigest()
        m = manifest("2.1.5", sha=isha, size=len(inst_bytes))
        m["app_zip"] = {"url": "https://houdini-agent.com/download/HoudiniAgent-App-2.1.5.zip",
                        "size": 123, "sha256": "00" * 32}
        updater._save_etag_cache({"manifest": m})
        env({m["installer"]["url"]: Resp(200, content=inst_bytes)})

        r = updater.stage_update()
        assert r["success"] and r["staged"]["mode"] == "installer"
        assert os.path.isfile(r["staged"]["path"])
        # Program Files 只读目录需要 UAC，退出时不应静默弹 UAC
        assert updater.can_apply_silently_on_exit(r["staged"]) is False

    def test_apply_staged_swap_spawns_hidden_helper(self, tmp_path, monkeypatch):
        state_dir = tmp_path / "state"
        app_dir = tmp_path / "app"
        staged_dir = state_dir / "updates" / "staged" / "2.1.5"
        app_dir.mkdir()
        staged_dir.mkdir(parents=True)
        (staged_dir / "Houdini Agent.exe").write_bytes(b"MZ")
        monkeypatch.setattr(updater, "_user_state_dir", lambda: state_dir)
        monkeypatch.setattr(updater, "app_install_dir", lambda: app_dir)

        calls = []
        import subprocess

        def fake_popen(cmd, **kw):
            calls.append((cmd, kw))
            return object()

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        res = updater.apply_staged_update(
            {"version": "2.1.5", "mode": "swap", "staged_dir": str(staged_dir)},
            relaunch=False,
        )
        assert res["started"] is True and res["mode"] == "swap" and res["needs_watch"] is False
        assert calls and "apply_update.ps1" in " ".join(calls[0][0])
        assert "-Relaunch" in calls[0][0] and calls[0][0][calls[0][0].index("-Relaunch") + 1] == "0"
        assert (state_dir / "updates" / "apply_update.ps1").is_file()
