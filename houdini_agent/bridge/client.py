# -*- coding: utf-8 -*-
"""Small localhost JSON-lines client for the Houdini Agent bridge."""

import json
import os
import socket
import uuid


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = int(os.environ.get("HAGENT_BRIDGE_PORT", "45172") or "45172")


class BridgeClient:
    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, connect_timeout=0.25, request_timeout=180.0):
        self.host = host
        self.port = int(port)
        self.connect_timeout = float(connect_timeout)
        self.request_timeout = float(request_timeout)

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
