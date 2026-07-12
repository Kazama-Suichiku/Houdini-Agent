# -*- coding: utf-8 -*-
"""Houdini-side localhost bridge server."""

import json
import os
import sys
import socketserver
import threading
import traceback
from pathlib import Path

from .client import DEFAULT_HOST, DEFAULT_PORT, port_file

_SERVER = None
_THREAD = None
_MCP = None


def _log(msg):
    try:
        import time
        p = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "HoudiniAgent" / "bridge.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + str(msg).rstrip() + "\n")
    except Exception:
        pass


def _ensure_package_path():
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        import houdini_agent
        pkg_dir = str(root / "houdini_agent")
        paths = getattr(houdini_agent, "__path__", None)
        if paths is not None and pkg_dir not in list(paths):
            paths.append(pkg_dir)
    except Exception:
        pass


def _json_default(obj):
    try:
        return str(obj)
    except Exception:
        return repr(obj)


def _main_thread(fn):
    try:
        import hdefereval
        return hdefereval.executeInMainThreadWithResult(fn)
    except Exception:
        return fn()


def _mcp():
    global _MCP
    if _MCP is None:
        _ensure_package_path()
        try:
            from houdini_agent.utils.mcp import HoudiniMCP
        except Exception as exc:
            try:
                import houdini_agent
                pkg = "houdini_agent file=%s path=%s" % (
                    getattr(houdini_agent, "__file__", None),
                    list(getattr(houdini_agent, "__path__", [])),
                )
            except Exception:
                pkg = "houdini_agent unavailable"
            _log("MCP import failed: %s\n%s\nsys.path=%s\n%s" % (
                exc, pkg, sys.path, traceback.format_exc()
            ))
            raise
        _MCP = HoudiniMCP()
        # 让 Meshy 的 Houdini 侧 handler（import_3d_asset/export_node_to_glb）
        # 在本进程的 ToolRegistry 注册，使 execute_tool 能回退分派到它们。
        try:
            import houdini_agent.meshy  # noqa: F401  (import 即自注册)
        except Exception as exc:
            _log("meshy register failed: %s" % exc)
    return _MCP


def _scene_context():
    import hou
    net = None
    try:
        net = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor).pwd()
    except Exception:
        pass
    sel = []
    try:
        sel = hou.selectedNodes()
    except Exception:
        pass
    return {
        "path": net.path() if net else "/obj",
        "selection": "%d node%s selected" % (len(sel), "" if len(sel) == 1 else "s") if sel else "no selection",
        "houdini": hou.applicationVersionString(),
    }


_MUTATING = {
    "create_node", "create_nodes_batch", "create_wrangle_node", "delete_node",
    "set_node_parameter", "batch_set_parameters", "connect_nodes", "copy_node",
    "set_display_flag", "execute_python", "run_skill",
    "import_3d_asset",  # Meshy 导入会建节点
}


def _target_network(kwargs):
    import hou
    p = kwargs.get("parent_path") or kwargs.get("parent")
    if p:
        return hou.node(p)
    np = kwargs.get("node_path") or kwargs.get("path")
    if np:
        n = hou.node(np)
        if n and n.parent():
            return n.parent()
    try:
        ed = hou.ui.curDesktop().paneTabOfType(hou.paneTabType.NetworkEditor)
        if ed:
            return ed.pwd()
    except Exception:
        pass
    return hou.node("/obj/geo1") or hou.node("/obj")


def _snapshot_children(network):
    if network is None:
        return {}
    try:
        return {n.path(): {"name": n.name(), "type": n.type().name(), "path": n.path()}
                for n in network.children()}
    except Exception:
        return {}


def _diff_children(before, after):
    bp, ap = set(before), set(after)
    created = [after[p] for p in sorted(ap - bp)]
    deleted = [before[p] for p in sorted(bp - ap)]
    if not created and not deleted:
        return None
    return {"created": created, "deleted": deleted}


def _execute_tool(payload):
    name = str((payload or {}).get("name") or "")
    args = (payload or {}).get("args") or {}
    if not name:
        return {"success": False, "error": "missing tool name"}

    def run():
        import hou
        use_group = name in _MUTATING
        should_snap = name in _MUTATING and name not in ("save_hip", "set_node_parameter")
        target = _target_network(args) if should_snap else None
        before = _snapshot_children(target) if should_snap else {}
        grouped = False
        try:
            if use_group:
                try:
                    hou.undos.beginGroup("Houdini Agent: %s" % name)
                    grouped = True
                except Exception:
                    grouped = False
            res = _mcp().execute_tool(name, args)
            result = res if isinstance(res, dict) else {"success": False, "error": "tool returned non-dict"}
            if should_snap and result.get("success"):
                try:
                    changes = _diff_children(before, _snapshot_children(target))
                    if changes:
                        result["_node_changes"] = changes
                except Exception:
                    pass
            return result
        except Exception as exc:
            _log("Tool failed %s: %s\n%s" % (name, exc, traceback.format_exc()))
            return {"success": False, "error": str(exc)}
        finally:
            if grouped:
                try:
                    hou.undos.endGroup()
                except Exception:
                    pass

    return _main_thread(run)


def _undo_node_op(ctx):
    ctx = ctx or {}

    def run():
        import hou
        op = ctx.get("op")
        try:
            if op == "create":
                for p in ctx.get("paths", []) or []:
                    n = hou.node(p)
                    if n is not None:
                        n.destroy()
            elif op == "modify":
                snap = ctx.get("snapshot") or {}
                n = hou.node(snap.get("node_path", ""))
                if n is not None:
                    pn = snap.get("param_name", "")
                    old = snap.get("old_value")
                    if snap.get("is_tuple"):
                        pt = n.parmTuple(pn)
                        if pt is not None:
                            pt.set(old)
                    else:
                        pm = n.parm(pn)
                        if pm is not None:
                            if isinstance(old, dict) and "expr" in old:
                                lang = (hou.exprLanguage.Python
                                        if "python" in str(old.get("lang", "")).lower()
                                        else hou.exprLanguage.Hscript)
                                pm.setExpression(old["expr"], lang)
                            else:
                                pm.set(old)
            elif op == "delete":
                hou.undos.performUndo()
            else:
                return {"success": False, "error": "unknown undo op: %s" % op}
            return {"success": True, "result": "undone"}
        except Exception as exc:
            _log("Undo failed %s: %s\n%s" % (op, exc, traceback.format_exc()))
            return {"success": False, "error": str(exc)}

    return _main_thread(run)


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline(16 * 1024 * 1024)
        if not line:
            return
        try:
            req = json.loads(line.decode("utf-8", "replace"))
            action = req.get("action")
            payload = req.get("payload") or {}
            if action == "ping":
                import hou
                result = {"status": "ok", "houdini": hou.applicationVersionString()}
            elif action == "scene_context":
                result = _main_thread(_scene_context)
            elif action == "execute_tool":
                result = _execute_tool(payload)
            elif action == "undo_node_op":
                result = _undo_node_op(payload)
            else:
                raise RuntimeError("unknown action: %s" % action)
            out = {"id": req.get("id"), "success": True, "result": result}
        except Exception as exc:
            out = {
                "success": False,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
        self.wfile.write((json.dumps(out, ensure_ascii=False, default=_json_default) + "\n").encode("utf-8"))


class _Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def _write_port_file(port):
    """把实际监听端口写到发现文件，客户端据此连接（端口被占用自动顺延时仍能连上）。"""
    try:
        p = port_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(port), encoding="utf-8")
    except Exception as e:
        _log("write port file failed: %s" % e)


def start_bridge(host=DEFAULT_HOST, port=None):
    """Start the Houdini bridge once and return the server object.

    端口被占用（如其它程序占了默认端口，Windows 上表现为 WinError 10013/10048）时，
    自动顺延到下一个可用端口，并把实际端口写入发现文件供客户端连接；
    全部尝试失败则打印清晰提示而非直接抛错。"""
    global _SERVER, _THREAD
    if _SERVER is not None:
        return _SERVER
    base = int(port or os.environ.get("HAGENT_BRIDGE_PORT", DEFAULT_PORT))
    candidates = [base] + [base + i for i in range(1, 21)]
    last_err = None
    bound = None
    for p in candidates:
        try:
            _SERVER = _Server((host, p), _Handler)
            bound = p
            break
        except OSError as e:
            last_err = e
            _SERVER = None
            continue
    if _SERVER is None:
        msg = ("[Houdini Agent] 无法启动 Bridge：端口 %s 起连续 %d 个端口都被占用或被系统禁止 (%s)。"
               "可设置环境变量 HAGENT_BRIDGE_PORT 指定一个空闲端口后重启 Houdini。"
               % (base, len(candidates), last_err))
        _log(msg)
        print(msg)
        return None
    _write_port_file(bound)
    _THREAD = threading.Thread(target=_SERVER.serve_forever, name="HoudiniAgentBridge", daemon=True)
    _THREAD.start()
    _log("Bridge listening on %s:%s root=%s" % (host, bound, Path(__file__).resolve().parents[2]))
    print("[Houdini Agent] Bridge listening on %s:%s" % (host, bound))
    if bound != base:
        print("[Houdini Agent] （默认端口 %s 被占用，已自动改用 %s）" % (base, bound))
    return _SERVER
