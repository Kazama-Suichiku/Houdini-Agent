# -*- coding: utf-8 -*-
"""Small localhost JSON-lines client for the Houdini Agent bridge."""

import json
import os
import socket
import uuid
from pathlib import Path


DEFAULT_HOST = "127.0.0.1"
_FALLBACK_PORT = 45172


def port_file():
    """服务器启动后把【实际】监听端口写到这里，供客户端发现。
    当默认端口被其它程序占用、服务器自动顺延到别的端口时，客户端据此仍能连上。"""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "HoudiniAgent" / "bridge.port"


def bridges_dir():
    """多实例发现目录：每个启动的 Houdini Bridge 写入 <pid>.json，互不覆盖。"""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "HoudiniAgent" / "bridges"


def registered_ports():
    """读取所有已登记的 Bridge 实例端口（按修改时间新到旧，去重）。"""
    d = bridges_dir()
    if not d.is_dir():
        return []
    entries = []
    for f in d.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            p = int(data.get("port") or 0)
            if 0 < p < 65536:
                entries.append((f.stat().st_mtime, p))
        except Exception:
            continue
    entries.sort(reverse=True)
    seen, out = set(), []
    for _, p in entries:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _read_port_file():
    try:
        return int(port_file().read_text(encoding="utf-8").strip())
    except Exception:
        return None


def resolve_port():
    """端口解析优先级：环境变量 HAGENT_BRIDGE_PORT > 服务器写下的发现文件 > 默认 45172。"""
    env = os.environ.get("HAGENT_BRIDGE_PORT")
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    disc = _read_port_file()
    if disc:
        return disc
    return _FALLBACK_PORT


# 向后兼容：模块级常量（server.py 等仍引用）。环境变量优先，否则用默认。
DEFAULT_PORT = int(os.environ.get("HAGENT_BRIDGE_PORT") or _FALLBACK_PORT)


class BridgeClient:
    def __init__(self, host=DEFAULT_HOST, port=None, connect_timeout=0.25, request_timeout=180.0):
        self.host = host
        # port=None → 每次请求动态解析（env > 发现文件 > 默认），以适配服务器自动换端口；
        # 显式传入端口则固定使用该端口。
        self._fixed_port = int(port) if port is not None else None
        self.connect_timeout = float(connect_timeout)
        self.request_timeout = float(request_timeout)

    @property
    def port(self):
        return self._fixed_port if self._fixed_port is not None else resolve_port()

    def request(self, action, payload=None, timeout=None):
        msg = {
            "id": uuid.uuid4().hex,
            "action": action,
            "payload": payload or {},
        }
        raw = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        with socket.create_connection((self.host, self.port), timeout=self.connect_timeout) as sock:
            sock.settimeout(float(timeout or self.request_timeout))
            sock.sendall(raw)
            data = b""
            while not data.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
        if not data:
            raise RuntimeError("empty bridge response")
        res = json.loads(data.decode("utf-8", "replace"))
        if not res.get("success"):
            raise RuntimeError(str(res.get("error") or "bridge request failed"))
        return res.get("result")

    def ping(self):
        try:
            return self.request("ping", timeout=1.0)
        except Exception:
            return None

    def execute_tool(self, name, args=None):
        return self.request("execute_tool", {"name": name, "args": args or {}})

    def undo_node_op(self, ctx):
        return self.request("undo_node_op", ctx or {}, timeout=30.0)

    def scene_context(self):
        try:
            return self.request("scene_context", timeout=2.0)
        except Exception:
            return None

    def export_items(self, node_paths, file_path):
        return self.request("export_items", {
            "node_paths": list(node_paths or []),
            "file_path": str(file_path or ""),
        }, timeout=60.0)

    def import_items(self, parent_path, file_path, create_parent=True):
        return self.request("import_items", {
            "parent_path": str(parent_path or "/obj"),
            "file_path": str(file_path or ""),
            "create_parent": bool(create_parent),
        }, timeout=60.0)
