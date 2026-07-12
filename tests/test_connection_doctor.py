# -*- coding: utf-8 -*-
"""Bridge 连接医生：端口扫描/自动采纳、诊断建议分支、修复动作。全部离线（假 Bridge 服务器）。"""
import json
import socket
import socketserver
import threading

import pytest

from houdini_agent.bridge import doctor
from houdini_agent.bridge.client import resolve_port
from houdini_agent.launcher import houdini_discovery as hd


@pytest.fixture(autouse=True)
def _isolate_bridge_env(monkeypatch, tmp_path):
    """隔离端口发现文件/日志目录，并清掉可能影响端口解析的环境变量。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("HAGENT_BRIDGE_PORT", raising=False)


def _free_port():
    """拿一个当前没人监听的端口（bind 后立刻释放）。"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def fake_bridge():
    """极简 Bridge 服务器：只会回 ping。返回其端口。"""

    class H(socketserver.StreamRequestHandler):
        def handle(self):
            line = self.rfile.readline(65536)
            if not line:
                return
            try:
                req = json.loads(line.decode("utf-8"))
            except Exception:
                return
            out = {"id": req.get("id"), "success": True,
                   "result": {"status": "ok", "houdini": "21.0.test"}}
            self.wfile.write((json.dumps(out) + "\n").encode("utf-8"))

    class Srv(socketserver.ThreadingMixIn, socketserver.TCPServer):
        allow_reuse_address = True
        daemon_threads = True

    srv = Srv(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _fake_installs():
    return [{"version": "21.0.440", "major_minor": "21.0",
             "path": r"C:\fake\Houdini 21.0.440", "exe": r"C:\fake\houdini.exe"}]


# ---------------------------------------------------------------- primitives

def test_candidate_ports_cover_env_file_and_default(monkeypatch, tmp_path):
    monkeypatch.setenv("HAGENT_BRIDGE_PORT", "50000")
    pf = doctor.port_file()
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text("51234", encoding="utf-8")
    cands = doctor._candidate_ports()
    assert cands[0] == 50000                      # 解析端口（env 优先）排最前
    assert 51234 in cands                         # 发现文件端口
    assert 45172 in cands and 45192 in cands      # 默认基址顺延段全覆盖
    assert len(cands) == len(set(cands))          # 去重


def test_scan_finds_fake_bridge(monkeypatch, fake_bridge):
    dead = _free_port()
    monkeypatch.setattr(doctor, "_candidate_ports", lambda: [dead, fake_bridge])
    port, info = doctor.scan_for_bridge()
    assert port == fake_bridge
    assert info["houdini"] == "21.0.test"


def test_scan_returns_none_when_all_dead(monkeypatch):
    monkeypatch.setattr(doctor, "_candidate_ports", lambda: [_free_port(), _free_port()])
    assert doctor.scan_for_bridge() == (None, None)


def test_ensure_connected_adopts_found_port(monkeypatch, fake_bridge):
    dead = _free_port()
    monkeypatch.setenv("HAGENT_BRIDGE_PORT", str(dead))       # 解析端口指向死端口
    monkeypatch.setattr(doctor, "_candidate_ports", lambda: [dead, fake_bridge])
    port, info = doctor.ensure_connected()
    assert port == fake_bridge
    import os
    assert os.environ["HAGENT_BRIDGE_PORT"] == str(fake_bridge)   # 端口已固化到本进程
    assert resolve_port() == fake_bridge                          # 后续请求直接用新端口


def test_ensure_connected_fast_path_skips_scan(monkeypatch, fake_bridge):
    monkeypatch.setenv("HAGENT_BRIDGE_PORT", str(fake_bridge))

    def boom():
        raise AssertionError("fast path must not scan")

    monkeypatch.setattr(doctor, "_candidate_ports", boom)
    port, info = doctor.ensure_connected()
    assert port == fake_bridge and info["status"] == "ok"


# ------------------------------------------------------------------ diagnose

def test_diagnose_connected(monkeypatch, fake_bridge):
    monkeypatch.setenv("HAGENT_BRIDGE_PORT", str(fake_bridge))
    monkeypatch.setattr(hd, "find_houdini_installs", _fake_installs)
    monkeypatch.setattr(hd, "is_houdini_running", lambda: True)
    out = doctor.diagnose()
    assert out["bridge_connected"] is True
    assert out["houdini"] == "21.0.test"
    assert "正常" in out["advice"]


def test_diagnose_finds_bridge_on_other_port(monkeypatch, fake_bridge, tmp_path):
    dead = _free_port()
    monkeypatch.setenv("HAGENT_BRIDGE_PORT", str(dead))
    monkeypatch.setattr(doctor, "_candidate_ports", lambda: [dead, fake_bridge])
    monkeypatch.setattr(hd, "find_houdini_installs", _fake_installs)
    monkeypatch.setattr(hd, "is_houdini_running", lambda: True)
    monkeypatch.setattr(hd, "user_pref_dir", lambda mm: tmp_path / ("houdini%s" % mm))
    out = doctor.diagnose()
    assert out["bridge_connected"] is False
    assert out["bridge_found_on_port"] == fake_bridge
    assert "reconnect" in out["advice"]


def test_diagnose_houdini_not_running(monkeypatch, tmp_path):
    dead = _free_port()
    monkeypatch.setenv("HAGENT_BRIDGE_PORT", str(dead))
    monkeypatch.setattr(doctor, "_candidate_ports", lambda: [dead])
    monkeypatch.setattr(hd, "find_houdini_installs", _fake_installs)
    monkeypatch.setattr(hd, "is_houdini_running", lambda: False)
    monkeypatch.setattr(hd, "user_pref_dir", lambda mm: tmp_path / ("houdini%s" % mm))
    out = doctor.diagnose()
    assert out["bridge_connected"] is False
    assert "launch_houdini" in out["advice"]


def test_diagnose_stale_package_advises_reinstall(monkeypatch, tmp_path):
    """集成包指向已不存在的旧安装目录（应用被移动/重装）→ 建议 reinstall_package。"""
    dead = _free_port()
    monkeypatch.setenv("HAGENT_BRIDGE_PORT", str(dead))
    monkeypatch.setattr(doctor, "_candidate_ports", lambda: [dead])
    monkeypatch.setattr(hd, "find_houdini_installs", _fake_installs)
    monkeypatch.setattr(hd, "is_houdini_running", lambda: True)
    pref = tmp_path / "houdini21.0"
    monkeypatch.setattr(hd, "user_pref_dir", lambda mm: pref)
    pkg = pref / "packages" / "HoudiniAgent.json"
    pkg.parent.mkdir(parents=True, exist_ok=True)
    pkg.write_text(json.dumps({"enable": True, "env": [
        {"HAGENT_REPO": "C:/old/gone/install"}]}), encoding="utf-8")
    out = doctor.diagnose()
    assert out["bridge_connected"] is False
    assert out["packages"][0]["exists"] is True
    assert out["packages"][0]["target_exists"] is False
    assert "reinstall_package" in out["advice"]


# -------------------------------------------------------------------- repair

def test_repair_reconnect_success(monkeypatch, fake_bridge):
    dead = _free_port()
    monkeypatch.setenv("HAGENT_BRIDGE_PORT", str(dead))
    monkeypatch.setattr(doctor, "_candidate_ports", lambda: [dead, fake_bridge])
    res = doctor.repair("reconnect", wait_seconds=3)
    assert res["success"] is True and res["reconnected"] is True
    assert res["port"] == fake_bridge


def test_repair_reconnect_timeout(monkeypatch):
    dead = _free_port()
    monkeypatch.setenv("HAGENT_BRIDGE_PORT", str(dead))
    monkeypatch.setattr(doctor, "_candidate_ports", lambda: [dead])
    res = doctor.repair("reconnect", wait_seconds=2)
    assert res["success"] is False
    assert "check_houdini_connection" in res["error"]


def test_repair_reinstall_writes_package(monkeypatch, tmp_path):
    pref = tmp_path / "houdini21.0"
    monkeypatch.setattr(hd, "find_houdini_installs", _fake_installs)
    monkeypatch.setattr(hd, "user_pref_dir", lambda mm: pref)
    res = doctor.repair("reinstall_package")
    assert res["success"] is True
    pkg = pref / "packages" / "HoudiniAgent.json"
    assert pkg.is_file()
    data = json.loads(pkg.read_text(encoding="utf-8"))
    envs = {k: v for e in data["env"] for k, v in e.items()}
    assert envs["HAGENT_REPO"]
    assert "重启" in res["result"]     # 明确告诉 agent 需要重启 Houdini 生效


def test_repair_launch_refuses_second_instance(monkeypatch):
    dead = _free_port()
    monkeypatch.setenv("HAGENT_BRIDGE_PORT", str(dead))
    monkeypatch.setattr(doctor, "_candidate_ports", lambda: [dead])
    monkeypatch.setattr(hd, "find_houdini_installs", _fake_installs)
    monkeypatch.setattr(hd, "is_houdini_running", lambda: True)
    res = doctor.repair("launch_houdini")
    assert res["success"] is False
    assert "已在运行" in res["error"]


def test_repair_unknown_action():
    res = doctor.repair("format_c_drive")
    assert res["success"] is False
    assert "reconnect" in res["error"]


def test_connection_tool_schemas_wellformed():
    names = {t["function"]["name"] for t in doctor.CONNECTION_TOOLS}
    assert names == doctor.CONNECTION_TOOL_NAMES
    for t in doctor.CONNECTION_TOOLS:
        f = t["function"]
        assert f["description"] and f["parameters"]["type"] == "object"
