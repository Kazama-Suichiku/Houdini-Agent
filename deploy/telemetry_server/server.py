#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Houdini Agent — Meshy 用量埋点接收后端（纯标准库，无任何 pip 依赖）。

监听 127.0.0.1:8000，由 Nginx 把 /api/ 反代过来。
统计目的：累计【通过 Houdini Agent 使用 Meshy API】消耗了多少 credits。

路由：
  POST /api/telemetry        接收 {"events":[ {event_id, install_id, ts, version,
                             kind, task_id, ai_model, mode, status, credits, prompt}, ... ]}
                             按 event_id 去重（INSERT OR IGNORE），返回 {"ok":true,"accepted":N}
  GET  /api/telemetry/stats  返回累计统计（总 credits / 事件数 / 安装数 / 按能力分组）
  GET  /api/health           健康检查

数据库：SQLite，路径取环境变量 HA_TELEMETRY_DB，默认 /var/lib/ha-telemetry/telemetry.db。
"""

import json
import os
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = os.environ.get("HA_TELEMETRY_HOST", "127.0.0.1")
PORT = int(os.environ.get("HA_TELEMETRY_PORT", "8000"))
DB_PATH = os.environ.get("HA_TELEMETRY_DB", "/var/lib/ha-telemetry/telemetry.db")
MAX_BODY = 4 * 1024 * 1024      # 单次请求体上限 4MB

_db_lock = threading.Lock()


def _connect():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db():
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                event_id    TEXT PRIMARY KEY,
                install_id  TEXT,
                ts          INTEGER,
                version     TEXT,
                kind        TEXT,
                task_id     TEXT,
                ai_model    TEXT,
                mode        TEXT,
                status      TEXT,
                credits     INTEGER,
                prompt      TEXT,
                received_at INTEGER
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_install ON events(install_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind)")
        conn.commit()


def _ingest(events):
    """按 event_id 去重写入。返回真正新增的条数。"""
    rows = []
    import time as _t
    now = int(_t.time())
    for e in events:
        if not isinstance(e, dict):
            continue
        eid = str(e.get("event_id") or "").strip()
        if not eid:
            continue
        try:
            credits = int(e.get("credits") or 0)
        except Exception:
            credits = 0
        rows.append((
            eid, e.get("install_id"), e.get("ts"), e.get("version"),
            e.get("kind"), e.get("task_id"), e.get("ai_model"), e.get("mode"),
            e.get("status"), credits, e.get("prompt"), now,
        ))
    if not rows:
        return 0
    with _db_lock, _connect() as conn:
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO events "
            "(event_id, install_id, ts, version, kind, task_id, ai_model, mode, "
            " status, credits, prompt, received_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
        return conn.total_changes - before


def _stats():
    with _db_lock, _connect() as conn:
        cur = conn.cursor()
        total_credits = cur.execute(
            "SELECT COALESCE(SUM(credits),0) FROM events").fetchone()[0]
        total_events = cur.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        installs = cur.execute(
            "SELECT COUNT(DISTINCT install_id) FROM events").fetchone()[0]
        by_kind = {}
        for kind, n, cr in cur.execute(
                "SELECT kind, COUNT(*), COALESCE(SUM(credits),0) "
                "FROM events GROUP BY kind"):
            by_kind[kind or "unknown"] = {"events": n, "credits": cr}
    return {
        "total_credits": total_credits,
        "total_events": total_events,
        "distinct_installs": installs,
        "by_kind": by_kind,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "ha-telemetry/1.0"

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        if path in ("/api/health", "/health"):
            return self._send(200, {"ok": True})
        if path in ("/api/telemetry/stats", "/telemetry/stats"):
            try:
                return self._send(200, _stats())
            except Exception as e:
                return self._send(500, {"ok": False, "error": str(e)})
        return self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        if path not in ("/api/telemetry", "/telemetry"):
            return self._send(404, {"ok": False, "error": "not found"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except Exception:
            length = 0
        if length <= 0 or length > MAX_BODY:
            return self._send(400, {"ok": False, "error": "bad length"})
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._send(400, {"ok": False, "error": "invalid json"})
        events = payload.get("events") if isinstance(payload, dict) else None
        if not isinstance(events, list):
            return self._send(400, {"ok": False, "error": "missing events[]"})
        try:
            n = _ingest(events)
            return self._send(200, {"ok": True, "accepted": n, "received": len(events)})
        except Exception as e:
            return self._send(500, {"ok": False, "error": str(e)})

    def log_message(self, fmt, *args):
        pass        # 静默：避免污染 systemd 日志（如需访问日志可在此打开）


def main():
    _init_db()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print("ha-telemetry listening on %s:%d  db=%s" % (HOST, PORT, DB_PATH))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
