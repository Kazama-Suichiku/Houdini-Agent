# -*- coding: utf-8 -*-
"""埋点接收后端：去重入库、统计聚合，以及 HTTP 端到端。"""
import http.client
import importlib.util
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

_SERVER_PY = Path(__file__).resolve().parents[1] / "deploy" / "telemetry_server" / "server.py"


def _load_server(db_path):
    spec = importlib.util.spec_from_file_location("ha_telemetry_server", _SERVER_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.DB_PATH = str(db_path)          # 重定向到临时库（函数读模块全局 DB_PATH）
    mod._init_db()
    return mod


@pytest.fixture
def srv(tmp_path):
    return _load_server(tmp_path / "telemetry.db")


def _ev(eid, install="i1", kind="text-to-3d", credits=5):
    return {"event_id": eid, "install_id": install, "ts": 1, "version": "2.0.0",
            "kind": kind, "task_id": "t", "ai_model": "m", "mode": "preview",
            "status": "SUCCEEDED", "credits": credits, "prompt": "p"}


def test_ingest_counts_new_rows(srv):
    assert srv._ingest([_ev("e1"), _ev("e2")]) == 2


def test_ingest_dedups_by_event_id(srv):
    srv._ingest([_ev("e1")])
    assert srv._ingest([_ev("e1"), _ev("e3")]) == 1     # e1 已存在，只新增 e3


def test_ingest_skips_missing_event_id(srv):
    assert srv._ingest([{"install_id": "x", "credits": 1}]) == 0


def test_ingest_coerces_bad_credits(srv):
    srv._ingest([{"event_id": "e9", "credits": "oops"}])
    assert srv._stats()["total_credits"] == 0


def test_stats_aggregation(srv):
    srv._ingest([
        _ev("a", install="i1", kind="text-to-3d", credits=10),
        _ev("b", install="i1", kind="text-to-3d", credits=5),
        _ev("c", install="i2", kind="remesh", credits=2),
    ])
    st = srv._stats()
    assert st["total_credits"] == 17
    assert st["total_events"] == 3
    assert st["distinct_installs"] == 2
    assert st["by_kind"]["text-to-3d"] == {"events": 2, "credits": 15}
    assert st["by_kind"]["remesh"] == {"events": 1, "credits": 2}


# ---------------- HTTP 端到端 ----------------

@pytest.fixture
def http_srv(srv):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    yield srv, httpd.server_address
    httpd.shutdown()


def _conn(addr):
    return http.client.HTTPConnection(addr[0], addr[1], timeout=5)


def test_http_health(http_srv):
    _, addr = http_srv
    c = _conn(addr); c.request("GET", "/api/health")
    r = c.getresponse()
    assert r.status == 200
    assert json.loads(r.read())["ok"] is True


def test_http_post_and_stats(http_srv):
    _, addr = http_srv
    body = json.dumps({"events": [_ev("h1", credits=7), _ev("h2", credits=3)]})
    c = _conn(addr)
    c.request("POST", "/api/telemetry", body=body,
              headers={"Content-Type": "application/json"})
    r = c.getresponse()
    assert r.status == 200
    out = json.loads(r.read())
    assert out["ok"] is True and out["accepted"] == 2

    c2 = _conn(addr); c2.request("GET", "/api/telemetry/stats")
    st = json.loads(c2.getresponse().read())
    assert st["total_credits"] == 10 and st["total_events"] == 2


def test_http_rejects_bad_json(http_srv):
    _, addr = http_srv
    c = _conn(addr)
    c.request("POST", "/api/telemetry", body="{not json",
              headers={"Content-Type": "application/json"})
    assert c.getresponse().status == 400


def test_http_unknown_path_404(http_srv):
    _, addr = http_srv
    c = _conn(addr); c.request("GET", "/nope")
    assert c.getresponse().status == 404
