# -*- coding: utf-8 -*-
"""桥接：纯函数 _diff_children + BridgeClient 的 JSON-lines 协议往返（mock server）。"""
import json
import socketserver
import threading

import pytest

from houdini_agent.bridge.server import _diff_children
from houdini_agent.bridge.client import BridgeClient


# ---------------- _diff_children (纯函数) ----------------

def test_diff_children_created():
    before = {"/obj/a": {"name": "a"}}
    after = {"/obj/a": {"name": "a"}, "/obj/b": {"name": "b"}}
    d = _diff_children(before, after)
    assert [c["name"] for c in d["created"]] == ["b"]
    assert d["deleted"] == []


def test_diff_children_deleted():
    before = {"/obj/a": {"name": "a"}, "/obj/b": {"name": "b"}}
    after = {"/obj/a": {"name": "a"}}
    d = _diff_children(before, after)
    assert [c["name"] for c in d["deleted"]] == ["b"]


def test_diff_children_no_change_returns_none():
    same = {"/obj/a": {"name": "a"}}
    assert _diff_children(same, dict(same)) is None


# ---------------- BridgeClient 协议往返 ----------------

class _EchoHandler(socketserver.StreamRequestHandler):
    captured = None

    def handle(self):
        line = self.rfile.readline()
        req = json.loads(line.decode("utf-8"))
        type(self).captured = req
        reply = self.server.reply_fn(req)
        self.wfile.write((json.dumps(reply) + "\n").encode("utf-8"))


class _MockServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


@pytest.fixture
def mock_bridge():
    """启一个本地 mock bridge，reply_fn 决定回什么。返回 (client, set_reply, get_request)。"""
    server = _MockServer(("127.0.0.1", 0), _EchoHandler)
    server.reply_fn = lambda req: {"id": req.get("id"), "success": True, "result": {"echo": True}}
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()
    host, port = server.server_address
    client = BridgeClient(host=host, port=port, connect_timeout=2.0, request_timeout=2.0)
    yield client, server, _EchoHandler
    server.shutdown()


def test_request_roundtrip_and_framing(mock_bridge):
    client, server, handler = mock_bridge
    result = client.request("execute_tool", {"name": "create_node", "args": {"x": 1}})
    assert result == {"echo": True}
    # 校验客户端发出的报文结构
    req = handler.captured
    assert req["action"] == "execute_tool"
    assert req["payload"] == {"name": "create_node", "args": {"x": 1}}
    assert isinstance(req["id"], str) and req["id"]


def test_execute_tool_helper(mock_bridge):
    client, server, handler = mock_bridge
    server.reply_fn = lambda req: {"id": req["id"], "success": True,
                                   "result": {"success": True, "name": req["payload"]["name"]}}
    out = client.execute_tool("save_hip", {"path": "/tmp/x.hip"})
    assert out["name"] == "save_hip"


def test_server_error_raises(mock_bridge):
    client, server, handler = mock_bridge
    server.reply_fn = lambda req: {"success": False, "error": "boom"}
    with pytest.raises(RuntimeError, match="boom"):
        client.request("execute_tool", {"name": "bad"})


def test_ping_returns_none_when_unreachable():
    # 指向一个没人监听的端口，ping 应静默返回 None
    client = BridgeClient(host="127.0.0.1", port=1, connect_timeout=0.2)
    assert client.ping() is None
