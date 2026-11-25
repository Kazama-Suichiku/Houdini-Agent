# -*- coding: utf-8 -*-
"""Houdini-side localhost bridge server."""

import atexit
import json
import os
import socket
import sys
import socketserver
import threading
import time
import traceback
from pathlib import Path

from .client import DEFAULT_HOST, DEFAULT_PORT, bridges_dir, port_file

_SERVER = None
_THREAD = None
_MCP = None
_BOUND_PORT = None
_REG_FILE = None
_CACHED_META = {}


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


def _hip_info():
    """读取当前打开的 .hip 文件路径与文件名（失败时安全回退）。"""
    try:
        import hou
        hp = str(hou.hipFile.path() or "")
        hn = str(hou.hipFile.basename() or "")
        if not hn and hp:
            hn = os.path.basename(hp)
        return hp, hn or "untitled.hip"
    except Exception:
        return _CACHED_META.get("hip_path", ""), _CACHED_META.get("hip_name", "")


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
    hip_path, hip_name = _hip_info()
    ver = hou.applicationVersionString()
    info = {
        "status": "ok",
        "path": net.path() if net else "/obj",
        "selection": "%d node%s selected" % (len(sel), "" if len(sel) == 1 else "s") if sel else "no selection",
        "houdini": ver,
        "pid": os.getpid(),
        "port": _BOUND_PORT,
        "hip_path": hip_path,
        "hip_name": hip_name,
    }
    _CACHED_META.update({
        "houdini": ver,
        "hip_path": hip_path,
        "hip_name": hip_name,
    })
    return info


def _export_items(payload):
    """用 Houdini 原生 saveItemsToFile 导出节点/子网到临时 .cpio 文件，供跨实例复制。"""
    payload = payload or {}
    node_paths = [str(p).strip() for p in (payload.get("node_paths") or []) if str(p or "").strip()]
    file_path = str(payload.get("file_path") or "").strip()
    if not node_paths:
        return {"success": False, "error": "node_paths 不能为空"}
    if not file_path:
        return {"success": False, "error": "file_path 不能为空"}

    def run():
        import hou
        nodes = []
        missing = []
        for p in node_paths:
            n = hou.node(p)
            if n is None:
                missing.append(p)
            else:
                nodes.append(n)
        if missing:
            return {"success": False, "error": "源实例中找不到节点: %s" % ", ".join(missing)}
        parents = {n.parent().path() for n in nodes if n.parent() is not None}
        if len(parents) != 1:
            return {"success": False,
                    "error": "一次跨文件传输的节点必须位于同一父网络下（当前跨越了: %s），请按父网络分批传输。"
                             % ", ".join(sorted(parents))}
        parent = nodes[0].parent()
        out_dir = os.path.dirname(file_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        parent.saveItemsToFile(nodes, file_path)
        return {
            "success": True,
            "file_path": file_path,
            "parent": parent.path(),
            "parent_type": parent.type().name(),
            "parent_category": parent.childTypeCategory().name(),
            "count": len(nodes),
            "names": [n.name() for n in nodes],
            "paths": [n.path() for n in nodes],
        }

    return _main_thread(run)


def _import_items(payload):
    """用 Houdini 原生 loadItemsFromFile 从 .cpio 导入节点到指定父网络（支持撤销分组）。"""
    payload = payload or {}
    parent_path = str(payload.get("parent_path") or "/obj").strip() or "/obj"
    file_path = str(payload.get("file_path") or "").strip()
    create_parent = bool(payload.get("create_parent", True))
    if not file_path or not os.path.isfile(file_path):
        return {"success": False, "error": "导入文件不存在: %s" % file_path}

    def run():
        import hou
        parent = hou.node(parent_path)
        created_container = None
        if parent is None and create_parent and parent_path.startswith("/obj/"):
            parts = [p for p in parent_path.split("/") if p]
            if len(parts) == 2 and parts[0] == "obj":
                obj = hou.node("/obj")
                if obj is not None:
                    parent = obj.createNode("geo", parts[1])
                    created_container = parent.path()
        if parent is None:
            return {"success": False, "error": "目标实例中找不到父网络: %s" % parent_path}
        before = _snapshot_children(parent)
        grouped = False
        try:
            try:
                hou.undos.beginGroup("Houdini Agent: transfer_nodes")
                grouped = True
            except Exception:
                grouped = False
            parent.loadItemsFromFile(file_path)
            after = _snapshot_children(parent)
            changes = _diff_children(before, after)
            created = [c["path"] for c in (changes or {}).get("created", [])]
            res = {
                "success": True,
                "parent": parent.path(),
                "created": created,
                "count": len(created),
            }
            if created_container:
                res["created_container"] = created_container
            if changes:
                res["_node_changes"] = changes
            return res
        except Exception as exc:
            _log("import_items failed: %s\n%s" % (exc, traceback.format_exc()))
            return {"success": False, "error": str(exc)}
        finally:
            if grouped:
                try:
                    hou.undos.endGroup()
                except Exception:
                    pass

    return _main_thread(run)


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
                ver = hou.applicationVersionString()
                hip_path, hip_name = _hip_info()
                _CACHED_META.update({"houdini": ver, "hip_path": hip_path, "hip_name": hip_name})
                result = {
                    "status": "ok",
                    "houdini": ver,
                    "pid": os.getpid(),
                    "port": _BOUND_PORT,
                    "hip_path": hip_path,
                    "hip_name": hip_name,
                }
            elif action == "scene_context":
                result = _main_thread(_scene_context)
            elif action == "execute_tool":
                result = _execute_tool(payload)
            elif action == "undo_node_op":
                result = _undo_node_op(payload)
            elif action == "export_items":
                result = _export_items(payload)
            elif action == "import_items":
                result = _import_items(payload)
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
    # Windows 上 SO_REUSEADDR 允许两个不同进程同时绑定同一端口，会导致多开 Houdini 抢占 45172 而不顺延！
    # Windows 必须关闭 allow_reuse_address 并启用 SO_EXCLUSIVEADDRUSE，让第二个实例稳定顺延到 45173/45174...
    allow_reuse_address = (os.name != "nt")
    daemon_threads = True

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except OSError:
                pass
        super().server_bind()


def _port_in_use(host, port):
    """兜底探测：即使旧版 Houdini 实例仍用旧 SO_REUSEADDR 占着端口，新实例也能感知并顺延。"""
    try:
        with socket.create_connection((host, int(port)), timeout=0.15):
            return True
    except OSError:
        return False


def _write_port_file(port):
    """把实际监听端口写到发现文件，同时在 bridges/<pid>.json 登记本实例（多实例互不覆盖）。"""
    global _BOUND_PORT, _REG_FILE
    _BOUND_PORT = int(port)
    try:
        p = port_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(port), encoding="utf-8")
    except Exception as e:
        _log("write port file failed: %s" % e)
    try:
        d = bridges_dir()
        d.mkdir(parents=True, exist_ok=True)
        ver = ""
        try:
            import hou
            ver = hou.applicationVersionString()
        except Exception:
            pass
        reg = d / ("%d.json" % os.getpid())
        reg.write_text(json.dumps({
            "pid": os.getpid(),
            "port": int(port),
            "houdini": ver,
            "started_at": time.time(),
        }, ensure_ascii=False), encoding="utf-8")
        _REG_FILE = reg
        atexit.register(_cleanup_reg_file)
    except Exception as e:
        _log("write instance reg failed: %s" % e)


def _cleanup_reg_file():
    try:
        if _REG_FILE is not None and _REG_FILE.is_file():
            _REG_FILE.unlink()
    except Exception:
        pass


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
        if _port_in_use(host, p):
            continue
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
