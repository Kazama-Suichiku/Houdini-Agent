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
        # Windows 上绝不能把 DETACHED_PROCESS 与 CREATE_NO_WINDOW 同时传给 powershell.exe
        flags = calls[0][1].get("creationflags", 0)
        assert not (flags & getattr(subprocess, "DETACHED_PROCESS", 0))
        assert (state_dir / "updates" / "apply_update.ps1").is_file()

    @pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell swap test")
    def test_swap_helper_actually_executes_and_swaps_on_windows(self, tmp_path, monkeypatch):
        """真机端到端验证：隐藏拉起 powershell.exe 能真正执行替换、保留 unins000/config 并写日志。"""
        state_dir = tmp_path / "state"
        app_dir = tmp_path / "app"
        staged_dir = state_dir / "updates" / "staged" / "2.1.7"
        meta = state_dir / "updates" / "staged.json"
        (app_dir / "_internal").mkdir(parents=True)
        (app_dir / "config").mkdir()
        (app_dir / "Houdini Agent.exe").write_bytes(b"OLD_EXE")
        (app_dir / "_internal" / "VERSION").write_text("2.1.6\n", encoding="utf-8")
        (app_dir / "config" / "user.ini").write_text("k=v\n", encoding="utf-8")
        (app_dir / "unins000.exe").write_bytes(b"UNINS")
        (app_dir / "unins000.dat").write_bytes(b"DAT")

        (staged_dir / "_internal").mkdir(parents=True)
        (staged_dir / "Houdini Agent.exe").write_bytes(b"NEW_EXE")
        (staged_dir / "_internal" / "VERSION").write_text("2.1.7\n", encoding="utf-8")
        meta.write_text('{"version":"2.1.7"}', encoding="utf-8")

        monkeypatch.setattr(updater, "_user_state_dir", lambda: state_dir)
        monkeypatch.setattr(updater, "app_install_dir", lambda: app_dir)
        monkeypatch.setattr(updater.os, "getpid", lambda: 0)

        proc = updater._launch_swap_helper(str(staged_dir), relaunch=False)
        rc = proc.wait(timeout=20)
        assert rc == 0
        log_file = state_dir / "update_apply.log"
        assert log_file.is_file(), "apply_update.ps1 did not execute!"
        log_text = log_file.read_text(encoding="utf-8")
        assert "swap succeeded" in log_text, log_text
        assert (app_dir / "Houdini Agent.exe").read_bytes() == b"NEW_EXE"
        assert (app_dir / "_internal" / "VERSION").read_text(encoding="utf-8").strip() == "2.1.7"
        assert (app_dir / "config" / "user.ini").read_text(encoding="utf-8").strip() == "k=v"
        assert (app_dir / "unins000.exe").read_bytes() == b"UNINS"
        assert (app_dir / "unins000.dat").read_bytes() == b"DAT"
        assert not meta.exists()
        assert not staged_dir.exists()

    def test_single_flight_stage_update_deduplicates_concurrent_downloads(self, env, tmp_path, monkeypatch):
        """后台静默下载进行中时，手动点击立即更新应挂载到同一任务，只发一次 HTTP 请求。"""
        import threading
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        state_dir = tmp_path / "state"
        monkeypatch.setattr(updater, "_user_state_dir", lambda: state_dir)
        monkeypatch.setattr(updater, "app_install_dir", lambda: app_dir)
        monkeypatch.setattr(updater.sys, "frozen", True, raising=False)

        zbytes = _make_app_zip("2.1.8")
        zsha = hashlib.sha256(zbytes).hexdigest()
        zurl = "https://houdini-agent.com/download/HoudiniAgent-App-2.1.8.zip"
        m = manifest("2.1.8")
        m["app_zip"] = {"url": zurl, "size": len(zbytes), "sha256": zsha}
        updater._save_etag_cache({"manifest": m})

        started = threading.Event()
        release = threading.Event()

        class _SlowResp(Resp):
            def iter_content(self, chunk_size=65536):
                started.set()
                release.wait(timeout=5)
                yield self._content

        fake = env({zurl: _SlowResp(200, content=zbytes)})
        results = [None, None]
        pcts = [[], []]

        def t1():
            results[0] = updater.stage_update(progress_callback=pcts[0].append)

        def t2():
            started.wait(timeout=5)
            results[1] = updater.stage_update(progress_callback=pcts[1].append)

        th1 = threading.Thread(target=t1)
        th2 = threading.Thread(target=t2)
        th1.start()
        th2.start()
        started.wait(timeout=5)
        import time as _t
        _t.sleep(0.1)
        release.set()
        th1.join(timeout=10)
        th2.join(timeout=10)

        assert len(fake.calls) == 1
        assert results[0]["success"] and results[1]["success"]
        assert results[0]["staged"]["version"] == "2.1.8"
        assert results[1]["staged"]["version"] == "2.1.8"
        assert 100 in pcts[0] and 100 in pcts[1]

    def test_download_one_resumes_via_http_range(self, tmp_path):
        """大文件下载中途截断时，重试应携带 Range 头续传并最终通过 SHA-256 校验。"""
        payload = b"ABCD" * (300 * 1024)  # 1.2 MB
        split = 500 * 1024
        sha = hashlib.sha256(payload).hexdigest()
        seen_headers = []

        class _FakeReq:
            def get(self, url, stream=True, timeout=60, headers=None):
                seen_headers.append(dict(headers or {}))
                rng = (headers or {}).get("Range", "")
                if rng.startswith("bytes="):
                    offset = int(rng.split("=")[1].split("-")[0])
                    part = payload[offset:]
                    return Resp(206, content=part, headers={"content-length": str(len(part))})
                # 首次模拟中途断流，只返回前半段
                return Resp(200, content=payload[:split], headers={"content-length": str(len(payload))})

        target = tmp_path / "pkg.zip.part"
        err = updater._download_one(
            _FakeReq(),
            {"url": "https://example.com/pkg.zip", "size": len(payload), "sha256": sha},
            str(target),
            lambda pct: None,
        )
        assert err == ""
        assert len(seen_headers) == 2
        assert seen_headers[1].get("Range") == f"bytes={split}-"
        assert target.read_bytes() == payload

    def test_delta_patch_selected_when_runtime_hash_matches_and_falls_back_when_mismatch(self, env, tmp_path, monkeypatch):
        """RUNTIME_HASH 一致时走 ~1.5 MB 增量补丁 mode='patch'；不一致时自动回退全量 mode='swap'。"""
        app_dir = tmp_path / "app"
        (app_dir / "_internal" / "houdini_agent").mkdir(parents=True)
        (app_dir / "Houdini Agent.exe").write_bytes(b"FROZEN_EXE_BIN")
        (app_dir / "_internal" / "Qt6Core.dll").write_bytes(b"QT_RUNTIME_DLL")
        (app_dir / "_internal" / "VERSION").write_text("2.1.7\n", encoding="utf-8")
        (app_dir / "_internal" / "houdini_agent" / "a.py").write_text("V=1\n", encoding="utf-8")
        rt_hash = updater.compute_runtime_hash(app_dir)
        (app_dir / "_internal" / "RUNTIME_HASH").write_text(rt_hash + "\n", encoding="utf-8")

        state_dir = tmp_path / "state"
        monkeypatch.setattr(updater, "_user_state_dir", lambda: state_dir)
        monkeypatch.setattr(updater, "app_install_dir", lambda: app_dir)
        monkeypatch.setattr(updater.sys, "frozen", True, raising=False)

        # 构造极小增量补丁包（仅业务文件，不含 exe/dll）
        import io, zipfile
        pbuf = io.BytesIO()
        with zipfile.ZipFile(pbuf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("_internal/VERSION", "2.1.8\n")
            zf.writestr("_internal/RUNTIME_HASH", rt_hash + "\n")
            zf.writestr("_internal/houdini_agent/a.py", "V=2\n" + ("# pad\n" * 400))
        pbytes = pbuf.getvalue()
        psha = hashlib.sha256(pbytes).hexdigest()
        purl = "https://houdini-agent.com/download/HoudiniAgent-Patch-2.1.8.zip"

        zbytes = _make_app_zip("2.1.8")
        zsha = hashlib.sha256(zbytes).hexdigest()
        zurl = "https://houdini-agent.com/download/HoudiniAgent-App-2.1.8.zip"

        m = manifest("2.1.8")
        m["patch_zip"] = {"url": purl, "size": len(pbytes), "sha256": psha, "runtime_hash": rt_hash}
        m["app_zip"] = {"url": zurl, "size": len(zbytes), "sha256": zsha}
        updater._save_etag_cache({"manifest": m})
        fake = env({purl: Resp(200, content=pbytes), zurl: Resp(200, content=zbytes)})

        r = updater.stage_update()
        assert r["success"] and r["staged"]["mode"] == "patch"
        assert fake.calls == [purl]  # 只下了增量补丁，没下全量包！

        # 若远端 runtime_hash 变化（例如升级了 PySide6），应自动回退全量 app_zip
        updater.clear_staged_update()
        m["patch_zip"]["runtime_hash"] = "0" * 64
        updater._save_etag_cache({"manifest": m})
        r2 = updater.stage_update()
        assert r2["success"] and r2["staged"]["mode"] == "swap"

    @pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell patch test")
    def test_patch_mode_executes_and_health_check_rolls_back_on_crash_loop(self, tmp_path, monkeypatch):
        """真机验证 mode='patch' 逐文件覆盖 + 写 pending_verify.json + 连续闪退自动回滚 + 健康确认清理。"""
        state_dir = tmp_path / "state"
        app_dir = tmp_path / "app"
        staged_dir = state_dir / "updates" / "staged" / "2.1.9-patch"
        meta = state_dir / "updates" / "staged.json"
        (app_dir / "_internal" / "houdini_agent").mkdir(parents=True)
        (app_dir / "Houdini Agent.exe").write_bytes(b"UNCHANGED_EXE")
        (app_dir / "_internal" / "VERSION").write_text("2.1.8\n", encoding="utf-8")
        (app_dir / "_internal" / "houdini_agent" / "mod.py").write_text("OLD_CODE\n", encoding="utf-8")

        (staged_dir / "_internal" / "houdini_agent").mkdir(parents=True)
        (staged_dir / "_internal" / "VERSION").write_text("2.1.9\n", encoding="utf-8")
        (staged_dir / "_internal" / "houdini_agent" / "mod.py").write_text("NEW_CODE\n", encoding="utf-8")
        meta.write_text('{"version":"2.1.9"}', encoding="utf-8")

        monkeypatch.setattr(updater, "_user_state_dir", lambda: state_dir)
        monkeypatch.setattr(updater, "app_install_dir", lambda: app_dir)
        monkeypatch.setattr(updater, "get_local_version", lambda: "2.1.8")
        monkeypatch.setattr(updater.os, "getpid", lambda: 0)

        proc = updater._launch_swap_helper(str(staged_dir), relaunch=False, mode="patch", to_version="2.1.9")
        assert proc.wait(timeout=20) == 0
        assert (app_dir / "_internal" / "VERSION").read_text(encoding="utf-8").strip() == "2.1.9"
        assert (app_dir / "_internal" / "houdini_agent" / "mod.py").read_text(encoding="utf-8").strip() == "NEW_CODE"
        assert (app_dir / "Houdini Agent.exe").read_bytes() == b"UNCHANGED_EXE"
        assert not (state_dir / "updates" / "applying.lock").exists()
        assert (state_dir / "updates" / "pending_verify.json").is_file()

        # 模拟新版连续 2 次闪退 → 第 3 次启动 check_startup_health 自动回滚回 2.1.8！
        h1 = updater.check_startup_health(max_failures=2)
        assert h1["pending"] is True and h1["rolled_back"] is False and h1["attempts"] == 1
        h2 = updater.check_startup_health(max_failures=2)
        assert h2["pending"] is True and h2["rolled_back"] is False and h2["attempts"] == 2
        h3 = updater.check_startup_health(max_failures=2)
        assert h3["rolled_back"] is True and h3["from_version"] == "2.1.8"
        assert (app_dir / "_internal" / "VERSION").read_text(encoding="utf-8").strip() == "2.1.8"
        assert (app_dir / "_internal" / "houdini_agent" / "mod.py").read_text(encoding="utf-8").strip() == "OLD_CODE"
        assert not (state_dir / "updates" / "pending_verify.json").exists()

    def test_mark_update_healthy_cleans_verify_and_rollback(self, tmp_path, monkeypatch):
        state_dir = tmp_path / "state"
        app_dir = tmp_path / "app"
        rb = state_dir / "updates" / "rollback"
        rb.mkdir(parents=True)
        (rb / "old.txt").write_text("old", encoding="utf-8")
        vf = state_dir / "updates" / "pending_verify.json"
        vf.write_text(json.dumps({"rollback_dir": str(rb), "attempts": 1}), encoding="utf-8")
        monkeypatch.setattr(updater, "_user_state_dir", lambda: state_dir)
        monkeypatch.setattr(updater, "app_install_dir", lambda: app_dir)

        assert updater.mark_update_healthy() is True
        assert not vf.exists()
        assert not rb.exists()

    def test_ed25519_manifest_signature_verification(self):
        pytest.importorskip("cryptography")
        import base64
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption

        priv = Ed25519PrivateKey.generate()
        priv_b64 = base64.b64encode(priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())).decode("ascii")
        pub_b64 = base64.b64encode(priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode("ascii")

        m = manifest("2.1.9")
        m["signature"] = updater.sign_manifest(m, priv_b64)
        assert updater.verify_manifest_signature(m, pubkey_b64=pub_b64) is True

        # 篡改任意字段后验签必须失败
        tampered = dict(m, version="9.9.9")
        assert updater.verify_manifest_signature(tampered, pubkey_b64=pub_b64) is False
        # 缺少 signature 时在配置了公钥下必须失败
        unsigned = {k: v for k, v in m.items() if k != "signature"}
        assert updater.verify_manifest_signature(unsigned, pubkey_b64=pub_b64) is False

    def test_app_zip_sources_includes_github_release_mirror(self):
        m = manifest("2.1.9")
        m["app_zip"] = {"url": "https://houdini-agent.com/download/HoudiniAgent-App-2.1.9.zip",
                        "size": 100, "sha256": "ab" * 32}
        cache = {
            "manifest": m,
            "release_data": {
                "tag_name": "v2.1.9",
                "assets": [{
                    "name": "HoudiniAgent-App-2.1.9.zip",
                    "browser_download_url": "https://github.com/x/y/releases/download/v2.1.9/HoudiniAgent-App-2.1.9.zip",
                    "size": 100,
                }],
            },
        }
        srcs = updater.app_zip_sources(cache)
        assert len(srcs) == 2
        assert srcs[0]["label"] == "官网更新包"
        assert srcs[1]["label"] == "GitHub 更新包"
        assert srcs[1]["sha256"] == "ab" * 32
