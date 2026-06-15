# -*- coding: utf-8 -*-
"""Dedicated local HTTP bridge for standalone UI."""
from __future__ import annotations

import json
import queue
import socket
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional, Tuple

try:
    import hou  # type: ignore
except Exception:
    hou = None  # type: ignore

from ..mcp.client import HoudiniMCP


def _collect_scene_context_impl() -> Dict[str, Any]:
    ctx = {"network_path": "", "selected_types": [], "selected_names": []}
    if hou is None:
        return ctx
    try:
        editors = [p for p in hou.ui.paneTabs() if p.type() == hou.paneTabType.NetworkEditor]
        if editors:
            pwd = editors[0].pwd()
            if pwd:
                ctx["network_path"] = pwd.path()
        for node in hou.selectedNodes()[:5]:
            ctx["selected_types"].append(node.type().name())
            ctx["selected_names"].append(node.name())
    except Exception:
        pass
    return ctx


@dataclass
class _BridgeState:
    host: str = "127.0.0.1"
    port: int = 0
    server: Optional[ThreadingHTTPServer] = None
    server_thread: Optional[threading.Thread] = None
    callback_registered: bool = False
    started_at: float = 0.0
    pending: "queue.Queue[Callable[[], None]]" = queue.Queue()
    active_requests: int = 0
    busy_lock: threading.Lock = threading.Lock()


_STATE = _BridgeState()


def _main_thread_executor(fn: Callable[[], Any], timeout: float = 8.0) -> Tuple[bool, Any]:
    if hou is None:
        return False, {"success": False, "error": "Houdini is not available"}

    done = threading.Event()
    payload: Dict[str, Any] = {}

    def _run():
        nonlocal payload
        try:
            payload = {"ok": True, "value": fn()}
        except Exception as e:
            payload = {"ok": False, "value": {"success": False, "error": str(e)}}
        finally:
            done.set()

    try:
        _STATE.pending.put(_run)
    except Exception as e:
        return False, {"success": False, "error": f"Bridge queue failure: {e}"}

    if not done.wait(timeout):
        return False, {
            "success": False,
            "error": f"Houdini bridge busy/timeout after {int(timeout)}s",
            "meta": {"busy": True, "timeout": timeout},
        }
    return True, payload.get("value")


def _event_loop_pump():
    processed = 0
    while processed < 8:
        try:
            fn = _STATE.pending.get_nowait()
        except queue.Empty:
            break
        try:
            fn()
        finally:
            processed += 1


class _BridgeHandler(BaseHTTPRequestHandler):
    server_version = "HoudiniAgentBridge/1.0"

    def _json_response(self, payload: Dict[str, Any], status: int = 200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return {}

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/") == "/health":
            self._json_response({
                "success": True,
                "ok": True,
                "result": {
                    "hou_available": bool(hou is not None),
                    "host": _STATE.host,
                    "port": _STATE.port,
                    "uptime_sec": max(0.0, time.time() - _STATE.started_at),
                    "active_requests": _STATE.active_requests,
                },
            })
            return
        self._json_response({"success": False, "error": "Not found"}, status=404)

    def do_POST(self):  # noqa: N802
        if self.path.rstrip("/") == "/tool":
            payload = self._read_json()
            tool_name = str(payload.get("tool_name", "") or "")
            arguments = payload.get("arguments", {}) or {}
            timeout = float(payload.get("timeout", 8.0) or 8.0)
            if not tool_name:
                self._json_response({"success": False, "error": "Missing tool_name"}, status=400)
                return

            def _invoke():
                client = HoudiniMCP()
                return client.execute_tool(tool_name, arguments)

            with _STATE.busy_lock:
                _STATE.active_requests += 1
            try:
                ok, result = _main_thread_executor(_invoke, timeout=timeout)
            finally:
                with _STATE.busy_lock:
                    _STATE.active_requests = max(0, _STATE.active_requests - 1)
            status = 200 if ok else 503
            if isinstance(result, dict):
                result.setdefault("meta", {})
                result["meta"].update({"remote": True, "tool_name": tool_name})
            self._json_response(result if isinstance(result, dict) else {"success": False, "error": str(result)}, status=status)
            return

        if self.path.rstrip("/") == "/context":
            payload = self._read_json()
            timeout = float(payload.get("timeout", 3.0) or 3.0)
            ok, result = _main_thread_executor(_collect_scene_context_impl, timeout=timeout)
            status = 200 if ok else 503
            if ok:
                self._json_response({"success": True, "result": result}, status=status)
            else:
                self._json_response(result if isinstance(result, dict) else {"success": False, "error": str(result)}, status=status)
            return

        self._json_response({"success": False, "error": "Not found"}, status=404)

    def log_message(self, format: str, *args):  # noqa: A003
        return


def _pick_port(host: str) -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((host, 0))
    _, port = sock.getsockname()
    sock.close()
    return int(port)


def ensure_bridge_running(host: str = "127.0.0.1", port: int = 0) -> Tuple[bool, str, str]:
    if hou is None:
        return False, "Houdini is not available; bridge not started", ""
    if _STATE.server_thread and _STATE.server_thread.is_alive():
        return True, "Bridge already running", f"http://{_STATE.host}:{_STATE.port}"

    host = host or "127.0.0.1"
    port = int(port or _pick_port(host))
    server = ThreadingHTTPServer((host, port), _BridgeHandler)
    _STATE.server = server
    _STATE.host = host
    _STATE.port = port
    _STATE.started_at = time.time()

    def _serve():
        try:
            server.serve_forever(poll_interval=0.2)
        finally:
            try:
                server.server_close()
            except Exception:
                pass

    _STATE.server_thread = threading.Thread(target=_serve, name="houdini-agent-bridge", daemon=True)
    _STATE.server_thread.start()

    if not _STATE.callback_registered:
        try:
            hou.ui.addEventLoopCallback(_event_loop_pump)
            _STATE.callback_registered = True
        except Exception:
            pass

    return True, "Bridge started", f"http://{host}:{port}"


def stop_bridge(timeout: float = 2.0) -> Tuple[bool, str]:
    server = _STATE.server
    thread = _STATE.server_thread
    if not server or not thread:
        return True, "Bridge is not running"
    try:
        server.shutdown()
    except Exception as e:
        return False, f"Failed to stop bridge: {e}"
    thread.join(timeout=timeout)
    _STATE.server = None
    _STATE.server_thread = None
    _STATE.started_at = 0.0
    return True, "Bridge stopped"


def get_bridge_status() -> Dict[str, Any]:
    return {
        "running": bool(_STATE.server_thread and _STATE.server_thread.is_alive()),
        "host": _STATE.host,
        "port": _STATE.port,
        "url": (f"http://{_STATE.host}:{_STATE.port}" if _STATE.port else ""),
        "uptime_sec": max(0.0, time.time() - _STATE.started_at) if _STATE.started_at else 0.0,
        "active_requests": _STATE.active_requests,
    }
