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
    monkeypatch.setattr(hd, "known_pref_versions", lambda: set())
    monkeypatch.setattr(hd, "user_pref_dirs", lambda mm: [tmp_path / ("houdini%s" % mm)])
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
    monkeypatch.setattr(hd, "known_pref_versions", lambda: set())
    monkeypatch.setattr(hd, "user_pref_dirs", lambda mm: [tmp_path / ("houdini%s" % mm)])
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
    monkeypatch.setattr(hd, "known_pref_versions", lambda: set())
    pref = tmp_path / "houdini21.0"
    monkeypatch.setattr(hd, "user_pref_dirs", lambda mm: [pref])
    pkg = pref / "packages" / "HoudiniAgent.json"
    pkg.parent.mkdir(parents=True, exist_ok=True)
    pkg.write_text(json.dumps({"enable": True, "env": [
        {"HAGENT_REPO": "C:/old/gone/install"}]}), encoding="utf-8")
    out = doctor.diagnose()
    assert out["bridge_connected"] is False
    assert out["packages"][0]["exists"] is True
    assert out["packages"][0]["target_exists"] is False
    assert "reinstall_package" in out["advice"]


def test_diagnose_pref_dir_missing_package(monkeypatch, tmp_path):
    """用户目录存在但没有集成包（文档重定向导致旧版装错位置）→ 明确点名 + reinstall。"""
    dead = _free_port()
    monkeypatch.setenv("HAGENT_BRIDGE_PORT", str(dead))
    monkeypatch.setattr(doctor, "_candidate_ports", lambda: [dead])
    monkeypatch.setattr(hd, "find_houdini_installs", _fake_installs)
    monkeypatch.setattr(hd, "is_houdini_running", lambda: True)
    monkeypatch.setattr(hd, "known_pref_versions", lambda: set())
    real_pref = tmp_path / "docs_d" / "houdini21.0"    # Houdini 真在用的目录（D 盘文档）
    real_pref.mkdir(parents=True)                       # 存在但没有 packages
    wrong_pref = tmp_path / "docs_c" / "houdini21.0"   # 旧版写包的位置（不存在）
    monkeypatch.setattr(hd, "user_pref_dirs", lambda mm: [real_pref, wrong_pref])
    out = doctor.diagnose()
    assert out["bridge_connected"] is False
    gap = [p for p in out["packages"] if p["pref_dir_exists"] and not p["exists"]]
    assert gap and gap[0]["pref_dir"] == str(real_pref)
    assert "reinstall_package" in out["advice"]
    assert "重定向" in out["advice"] or "移动" in out["advice"]


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


def test_repair_reinstall_writes_all_candidates(monkeypatch, tmp_path):
    """重装会写进全部候选用户目录（注册表 Documents + USERPROFILE 等）。"""
    docs_a, docs_b = tmp_path / "docs_a", tmp_path / "docs_b"
    monkeypatch.setattr(hd, "find_houdini_installs", _fake_installs)
    monkeypatch.setattr(hd, "known_pref_versions", lambda: set())
    monkeypatch.setattr(hd, "user_pref_dirs",
                        lambda mm: [docs_a / ("houdini%s" % mm), docs_b / ("houdini%s" % mm)])
    res = doctor.repair("reinstall_package")
    assert res["success"] is True
    for docs in (docs_a, docs_b):
        pkg = docs / "houdini21.0" / "packages" / "HoudiniAgent.json"
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


# ------------------------------------------------------------------ multi-instance & cross-file

def _start_rich_bridge(houdini_ver, hip_path, pid, path="/obj/geo1",
                       on_tool=None, on_export=None, on_import=None):
    """启一个模拟完整多实例协议（ping / scene_context / execute_tool / export_items / import_items）的假 Bridge。"""
    import os
    hip_name = os.path.basename(hip_path) if hip_path else "untitled.hip"

    class H(socketserver.StreamRequestHandler):
        def handle(self):
            line = self.rfile.readline(1024 * 1024)
            if not line:
                return
            req = json.loads(line.decode("utf-8"))
            act = req.get("action")
            pl = req.get("payload") or {}
            port = self.server.server_address[1]
            if act == "ping":
                res = {"status": "ok", "houdini": houdini_ver, "pid": pid, "port": port,
                       "hip_path": hip_path, "hip_name": hip_name}
            elif act == "scene_context":
                res = {"status": "ok", "houdini": houdini_ver, "pid": pid, "port": port,
                       "hip_path": hip_path, "hip_name": hip_name, "path": path, "selection": "1 node selected"}
            elif act == "execute_tool":
                res = on_tool(pl) if on_tool else {"success": True, "result": "ran %s on %s" % (pl.get("name"), hip_name)}
            elif act == "export_items":
                if on_export:
                    res = on_export(pl)
                else:
                    fp = pl["file_path"]
                    open(fp, "wb").write(b"FAKE-CPIO:" + ",".join(pl["node_paths"]).encode("utf-8"))
                    res = {"success": True, "file_path": fp, "parent": "/obj/geo1",
                           "count": len(pl["node_paths"]),
                           "names": [p.rsplit("/", 1)[-1] for p in pl["node_paths"]],
                           "paths": pl["node_paths"]}
            elif act == "import_items":
                if on_import:
                    res = on_import(pl)
                else:
                    data = open(pl["file_path"], "rb").read().decode("utf-8")
                    assert data.startswith("FAKE-CPIO:")
                    parent = pl.get("parent_path") or "/obj"
                    names = [p.rsplit("/", 1)[-1] for p in data.split(":", 1)[1].split(",")]
                    created = [parent.rstrip("/") + "/" + n for n in names]
                    res = {"success": True, "parent": parent, "created": created, "count": len(created)}
            else:
                out = {"id": req.get("id"), "success": False, "error": "unknown action: %s" % act}
                self.wfile.write((json.dumps(out) + "\n").encode("utf-8"))
                return
            out = {"id": req.get("id"), "success": True, "result": res}
            self.wfile.write((json.dumps(out) + "\n").encode("utf-8"))

    class Srv(socketserver.ThreadingMixIn, socketserver.TCPServer):
        allow_reuse_address = True
        daemon_threads = True

    srv = Srv(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def test_server_rejects_duplicate_bind_same_port():
    """多开 Houdini 不能绑定同一个端口（Windows 必须用 SO_EXCLUSIVEADDRUSE 禁止重绑）。"""
    from houdini_agent.bridge.server import _Server
    s1 = _Server(("127.0.0.1", 0), socketserver.BaseRequestHandler)
    port = s1.server_address[1]
    try:
        with pytest.raises(OSError):
            s2 = _Server(("127.0.0.1", port), socketserver.BaseRequestHandler)
            s2.server_close()
    finally:
        s1.server_close()


def test_discover_distinguish_and_switch_multi_instances(monkeypatch):
    """同版本多开（两个 21.0）+ 跨版本（20.5）同时在线：能全部发现、按文件名/版本/端口区分并切换。"""
    s1, p1 = _start_rich_bridge("21.0.440", "D:/proj/shot_010.hip", pid=1001)
    s2, p2 = _start_rich_bridge("21.0.440", "D:/proj/character_rig.hip", pid=1002)
    s3, p3 = _start_rich_bridge("20.5.332", "D:/proj/fx_sim.hip", pid=1003)
    try:
        monkeypatch.setenv("HAGENT_BRIDGE_PORT", str(p1))
        monkeypatch.setattr(doctor, "_candidate_ports", lambda: [p1, p2, p3])
        found = doctor.discover_all_bridges(include_context=True)
        assert len(found) == 3
        assert {i["hip_name"] for i in found} == {"shot_010.hip", "character_rig.hip", "fx_sim.hip"}
        assert [i["port"] for i in found if i["active"]] == [p1]

        # 按 .hip 名、唯一版本号、PID、端口均可准确解析
        inst, err = doctor.resolve_instance("character_rig.hip", instances=found)
        assert err == "" and inst["port"] == p2
        inst, err = doctor.resolve_instance("20.5", instances=found)
        assert err == "" and inst["port"] == p3
        inst, err = doctor.resolve_instance("pid:1002", instances=found)
        assert err == "" and inst["port"] == p2
        inst, err = doctor.resolve_instance(str(p3), instances=found)
        assert err == "" and inst["port"] == p3

        # 同版本有两个 21.0 实例时，只传 "21.0" 应报清晰消歧提示而非盲猜
        inst, err = doctor.resolve_instance("21.0", instances=found)
        assert inst is None and "多个" in err

        # 切换默认实例到 20.5
        sw = doctor.switch_instance("fx_sim.hip")
        assert sw["success"] is True and sw["port"] == p3
        assert resolve_port() == p3

        # 列出实例摘要包含全部 3 个并标明当前默认
        lst = doctor.list_instances_result()
        assert lst["success"] is True and lst["count"] == 3
        assert "fx_sim.hip" in lst["result"] and "[当前默认]" in lst["result"]
    finally:
        for s in (s1, s2, s3):
            s.shutdown()
            s.server_close()


def test_run_in_instance_and_cross_instance_transfer(monkeypatch):
    """跨文件/跨版本定向执行工具 + 跨实例无损传输节点（导出 .cpio → 导入目标并清理临时文件）。"""
    captured_tmp = []

    def _rec_export(pl):
        captured_tmp.append(pl["file_path"])
        open(pl["file_path"], "wb").write(b"FAKE-CPIO:" + ",".join(pl["node_paths"]).encode("utf-8"))
        return {"success": True, "file_path": pl["file_path"], "parent": "/obj/geo1",
                "count": len(pl["node_paths"]),
                "names": [p.rsplit("/", 1)[-1] for p in pl["node_paths"]],
                "paths": pl["node_paths"]}

    s_src, p_src = _start_rich_bridge("20.5.332", "D:/proj/asset_src.hip", pid=2001, on_export=_rec_export)
    s_dst, p_dst = _start_rich_bridge("21.0.440", "D:/proj/shot_dst.hip", pid=2002)
    try:
        monkeypatch.setenv("HAGENT_BRIDGE_PORT", str(p_dst))
        monkeypatch.setattr(doctor, "_candidate_ports", lambda: [p_src, p_dst])

        # 1) 不切换默认实例，直接去 20.5 源实例查询场景
        r = doctor.run_in_instance("asset_src.hip", "get_scene_info", {})
        assert r["success"] is True
        assert "asset_src.hip" in r["result"]
        assert resolve_port() == p_dst   # 默认实例未被改变

        # 2) 只读模式下禁止在其它实例写入
        ro = doctor.run_in_instance("asset_src.hip", "create_node", {"type": "box"}, read_only=True)
        assert ro["success"] is False and "只读模式" in ro["error"]

        # 3) 跨版本（20.5 -> 21.0）传输节点
        xfer = doctor.transfer_nodes(
            "asset_src.hip", "shot_dst.hip",
            ["/obj/geo1/grid1", "/obj/geo1/mountain_vex"],
            target_parent="/obj/imported_geo",
        )
        assert xfer["success"] is True
        assert xfer["created"] == ["/obj/imported_geo/grid1", "/obj/imported_geo/mountain_vex"]
        assert "asset_src.hip" in xfer["result"] and "shot_dst.hip" in xfer["result"]
        # 临时 .cpio 文件已清理
        import os
        assert captured_tmp and not os.path.exists(captured_tmp[0])
    finally:
        for s in (s_src, s_dst):
            s.shutdown()
            s.server_close()
