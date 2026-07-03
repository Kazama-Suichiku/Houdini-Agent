#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Houdini Agent — Meshy 用量埋点接收后端（纯标准库，无任何 pip 依赖）。

监听 127.0.0.1:8000，由 Nginx 把 /api/ 反代过来。
统计目的：累计【通过 Houdini Agent 使用 Meshy API】消耗了多少 credits。

路由：
  POST /api/telemetry            接收 {"events":[ {event_id, install_id, ts, version,
                                 kind, task_id, ai_model, mode, status, credits, prompt, env}, ...]}
                                 按 event_id 去重（INSERT OR IGNORE），返回 {"ok":true,"accepted":N}
  GET  /api/telemetry/stats      公开累计统计（总 credits / 事件数 / 安装数 / 按能力分组）
  GET  /api/health               健康检查

  —— 以下需管理 token（HTTP 头 X-Admin-Token，或 ?token=），token 取环境变量
     HA_TELEMETRY_ADMIN_TOKEN，其次文件 <db 同目录>/admin_token；未配置则这些接口 403：
  GET  /api/telemetry/report     分析报表 JSON：概览 / 活跃用户(DAU/WAU/MAU) / 按天趋势 /
                                 按版本 / 按能力 / 按 env / Top 安装
  GET  /api/telemetry/dashboard  轻量看板页（HTML，前端用 token 拉 /report 渲染）

数据库：SQLite，路径取环境变量 HA_TELEMETRY_DB，默认 /var/lib/ha-telemetry/telemetry.db。
说明：本后端【不做任何内部/测试剔除】，report 会把 env 维度原样呈现，由使用者自行解读。
"""

import hmac
import json
import os
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = os.environ.get("HA_TELEMETRY_HOST", "127.0.0.1")
PORT = int(os.environ.get("HA_TELEMETRY_PORT", "8000"))
DB_PATH = os.environ.get("HA_TELEMETRY_DB", "/var/lib/ha-telemetry/telemetry.db")
MAX_BODY = 4 * 1024 * 1024      # 单次请求体上限 4MB
DAY = 86400

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
                env         TEXT,
                account_hash TEXT,
                channel     TEXT,
                os          TEXT,
                session_id  TEXT,
                received_at INTEGER
            )
        """)
        # 迁移：缺列则补（历史行为 NULL；env=NULL 在 report 里视为 prod）
        cols = [r[1] for r in conn.execute("PRAGMA table_info(events)")]
        for col in ("env", "account_hash", "channel", "os", "session_id"):
            if col not in cols:
                conn.execute("ALTER TABLE events ADD COLUMN %s TEXT" % col)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_install ON events(install_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_account ON events(account_hash)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)")
        conn.commit()


def _ingest(events):
    """按 event_id 去重写入。返回真正新增的条数。"""
    rows = []
    now = int(time.time())
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
            e.get("status"), credits, e.get("prompt"), e.get("env"),
            e.get("account_hash"), e.get("channel"), e.get("os"), e.get("session_id"), now,
        ))
    if not rows:
        return 0
    with _db_lock, _connect() as conn:
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO events "
            "(event_id, install_id, ts, version, kind, task_id, ai_model, mode, "
            " status, credits, prompt, env, account_hash, channel, os, session_id, received_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
        return conn.total_changes - before


def _stats():
    """公开累计统计（保持向后兼容）。"""
    with _db_lock, _connect() as conn:
        cur = conn.cursor()
        total_credits = cur.execute("SELECT COALESCE(SUM(credits),0) FROM events").fetchone()[0]
        total_events = cur.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        installs = cur.execute("SELECT COUNT(DISTINCT install_id) FROM events").fetchone()[0]
        by_kind = {}
        for kind, n, cr in cur.execute(
                "SELECT kind, COUNT(*), COALESCE(SUM(credits),0) FROM events GROUP BY kind"):
            by_kind[kind or "unknown"] = {"events": n, "credits": cr}
    return {
        "total_credits": total_credits,
        "total_events": total_events,
        "distinct_installs": installs,
        "by_kind": by_kind,
    }


def _report(days=30):
    """管理报表：概览 + 活跃用户 + 按天趋势 + 版本/能力/env + Top 安装。"""
    now = int(time.time())
    since = now - days * DAY
    out = {"now": now, "window_days": days}
    with _db_lock, _connect() as conn:
        cur = conn.cursor()
        # 概览（distinct_accounts 忽略无 key 的 NULL —— 即真实付费账号数）
        tc, te, ins, acc = cur.execute(
            "SELECT COALESCE(SUM(credits),0), COUNT(*), COUNT(DISTINCT install_id), "
            "COUNT(DISTINCT account_hash) FROM events"
        ).fetchone()
        out["overview"] = {"total_credits": tc, "total_events": te,
                           "distinct_installs": ins, "distinct_accounts": acc}

        # 活跃用户：按 ts 落在最近 N 天窗口内去重（install 维度 + account 维度）
        active, active_acc = {}, {}
        for label, win in (("dau", 1), ("wau", 7), ("mau", 30)):
            active[label] = cur.execute(
                "SELECT COUNT(DISTINCT install_id) FROM events WHERE ts >= ?",
                (now - win * DAY,)).fetchone()[0]
            active_acc[label] = cur.execute(
                "SELECT COUNT(DISTINCT account_hash) FROM events "
                "WHERE account_hash IS NOT NULL AND ts >= ?",
                (now - win * DAY,)).fetchone()[0]
        out["active_installs"] = active
        out["active_accounts"] = active_acc      # 更接近"真实活跃付费用户"

        # 按天趋势（最近 days 天）
        daily = []
        for day, ev, cr, ai in cur.execute(
                "SELECT strftime('%Y-%m-%d', ts, 'unixepoch') AS d, COUNT(*), "
                "COALESCE(SUM(credits),0), COUNT(DISTINCT install_id) "
                "FROM events WHERE ts >= ? GROUP BY d ORDER BY d", (since,)):
            daily.append({"day": day, "events": ev, "credits": cr, "active_installs": ai})
        out["daily"] = daily

        # 按版本
        by_version = []
        for ver, ev, cr, ins2 in cur.execute(
                "SELECT COALESCE(version,'?'), COUNT(*), COALESCE(SUM(credits),0), "
                "COUNT(DISTINCT install_id) FROM events GROUP BY version "
                "ORDER BY COUNT(*) DESC"):
            by_version.append({"version": ver, "events": ev, "credits": cr, "installs": ins2})
        out["by_version"] = by_version

        # 按能力
        by_kind = {}
        for kind, n, cr in cur.execute(
                "SELECT kind, COUNT(*), COALESCE(SUM(credits),0) FROM events GROUP BY kind"):
            by_kind[kind or "unknown"] = {"events": n, "credits": cr}
        out["by_kind"] = by_kind

        # 按 env（NULL 归为 prod；仅呈现，不剔除）
        by_env = {}
        for env, n, cr, ins3 in cur.execute(
                "SELECT COALESCE(env,'prod'), COUNT(*), COALESCE(SUM(credits),0), "
                "COUNT(DISTINCT install_id) FROM events GROUP BY COALESCE(env,'prod')"):
            by_env[env] = {"events": n, "credits": cr, "installs": ins3}
        out["by_env"] = by_env

        # 按渠道（frozen 打包版 / source 源码运行；帮助区分正式使用与开发自测）
        by_channel = {}
        for ch, n, cr, ins4 in cur.execute(
                "SELECT COALESCE(channel,'?'), COUNT(*), COALESCE(SUM(credits),0), "
                "COUNT(DISTINCT install_id) FROM events GROUP BY COALESCE(channel,'?')"):
            by_channel[ch] = {"events": n, "credits": cr, "installs": ins4}
        out["by_channel"] = by_channel

        # 按操作系统
        by_os = {}
        for osname, n in cur.execute(
                "SELECT COALESCE(os,'?'), COUNT(*) FROM events GROUP BY COALESCE(os,'?')"):
            by_os[osname] = n
        out["by_os"] = by_os

        # Top 安装（含各自最新版本）
        # 注意：先 fetchall 物化外层结果，再用独立游标做子查询——
        # 否则在同一游标上边遍历边执行子查询会重置外层结果集，只能拿到第一行。
        top = []
        top_rows = cur.execute(
            "SELECT install_id, COUNT(*), COALESCE(SUM(credits),0), MIN(ts), MAX(ts) "
            "FROM events GROUP BY install_id ORDER BY SUM(credits) DESC LIMIT 30").fetchall()
        vcur = conn.cursor()
        for iid, ev, cr, f, l in top_rows:
            ver = vcur.execute(
                "SELECT version FROM events WHERE install_id IS ? ORDER BY ts DESC LIMIT 1",
                (iid,)).fetchone()
            top.append({
                "install": (iid or "?")[:12],
                "events": ev, "credits": cr,
                "first_ts": f, "last_ts": l,
                "version": (ver[0] if ver else None) or "?",
            })
        out["top_installs"] = top
    return out


# ------------------------------------------------------------------ 鉴权

def _admin_token():
    t = os.environ.get("HA_TELEMETRY_ADMIN_TOKEN")
    if t:
        return t.strip()
    try:
        path = os.path.join(os.path.dirname(DB_PATH) or ".", "admin_token")
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def _authorized(handler):
    want = _admin_token()
    if not want:
        return False        # 未配置 token → 管理接口一律拒绝
    got = handler.headers.get("X-Admin-Token", "")
    if not got:
        q = handler.path.split("?", 1)
        if len(q) == 2:
            for kv in q[1].split("&"):
                if kv.startswith("token="):
                    got = kv[6:]
                    break
    return bool(got) and hmac.compare_digest(got, want)


# ------------------------------------------------------------------ HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "ha-telemetry/1.1"

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, code, html):
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
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
        if path in ("/api/telemetry/dashboard", "/telemetry/dashboard"):
            return self._send_html(200, DASHBOARD_HTML)
        if path in ("/api/telemetry/report", "/telemetry/report"):
            if not _authorized(self):
                return self._send(403, {"ok": False, "error": "forbidden"})
            try:
                return self._send(200, _report())
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
        pass        # 静默：避免污染 systemd 日志


DASHBOARD_HTML = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HA Telemetry</title>
<style>
  :root{--bg:#0f1115;--card:#171a21;--line:#262b36;--tx:#e6e8ec;--mut:#8b93a1;--acc:#6a9eff}
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.5 system-ui,Segoe UI,Arial}
  header{padding:16px 20px;border-bottom:1px solid var(--line);display:flex;gap:10px;align-items:center;flex-wrap:wrap}
  h1{font-size:15px;margin:0;font-weight:600;letter-spacing:.02em}
  input{background:#0b0d11;border:1px solid var(--line);color:var(--tx);border-radius:6px;padding:6px 10px;min-width:230px}
  button{background:var(--acc);border:0;color:#08111f;font-weight:600;border-radius:6px;padding:6px 14px;cursor:pointer}
  main{padding:20px;max-width:1100px;margin:0 auto}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:22px}
  .c{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
  .c .k{color:var(--mut);font-size:12px} .c .v{font-size:24px;font-weight:700;margin-top:4px}
  section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:18px;overflow-x:auto}
  section h2{font-size:13px;color:var(--mut);margin:0 0 10px;font-weight:600;letter-spacing:.03em;text-transform:uppercase}
  table{border-collapse:collapse;width:100%;white-space:nowrap} th,td{text-align:right;padding:6px 10px;border-bottom:1px solid var(--line)}
  th:first-child,td:first-child{text-align:left} th{color:var(--mut);font-weight:600}
  .bar{height:8px;background:var(--acc);border-radius:4px;display:inline-block;vertical-align:middle}
  .mut{color:var(--mut)} .err{color:#ff6b6b}
</style></head>
<body>
<header>
  <h1>Houdini Agent · Meshy 用量</h1>
  <input id="tok" type="password" placeholder="Admin token" autocomplete="off">
  <button onclick="load()">加载</button>
  <span id="msg" class="mut"></span>
</header>
<main id="root"><p class="mut">输入 admin token 后点「加载」。</p></main>
<script>
const $=s=>document.querySelector(s); const el=(t,p={})=>Object.assign(document.createElement(t),p);
function fmt(n){return (n==null?0:n).toLocaleString()}
function daysAgo(ts){if(!ts)return '-';const d=Math.floor((Date.now()/1000-ts)/86400);return d<=0?'今天':d+'天前'}
function tbl(cols,rows){const t=el('table');const h=el('tr');cols.forEach(c=>h.appendChild(el('th',{textContent:c})));t.appendChild(h);
  rows.forEach(r=>{const tr=el('tr');r.forEach(c=>{const td=el('td');if(c&&c.html)td.innerHTML=c.html;else td.textContent=c;tr.appendChild(td)});t.appendChild(tr)});return t}
function sect(title,node){const s=el('section');s.appendChild(el('h2',{textContent:title}));s.appendChild(node);return s}
async function load(){
  const tok=$('#tok').value.trim(); if(!tok){$('#msg').textContent='需要 token';return}
  localStorage.setItem('ha_tok',tok); $('#msg').textContent='加载中…';
  let r; try{ r=await fetch('/api/telemetry/report',{headers:{'X-Admin-Token':tok}}); }catch(e){$('#msg').innerHTML='<span class=err>请求失败</span>';return}
  if(r.status===403){$('#msg').innerHTML='<span class=err>token 错误</span>';return}
  if(!r.ok){$('#msg').innerHTML='<span class=err>HTTP '+r.status+'</span>';return}
  const d=await r.json(); $('#msg').textContent=''; render(d);
}
function render(d){
  const root=$('#root'); root.innerHTML='';
  const o=d.overview, a=d.active_installs, aa=d.active_accounts||{};
  const cards=el('div',{className:'cards'});
  [['总 credits',o.total_credits],['总任务',o.total_events],['安装数',o.distinct_installs],
   ['付费账号数',o.distinct_accounts],['DAU(账号)',aa.dau],['WAU(账号)',aa.wau],['MAU(账号)',aa.mau],
   ['DAU(安装)',a.dau],['WAU(安装)',a.wau],['MAU(安装)',a.mau]].forEach(([k,v])=>{
    const c=el('div',{className:'c'});c.appendChild(el('div',{className:'k',textContent:k}));
    c.appendChild(el('div',{className:'v',textContent:fmt(v)}));cards.appendChild(c)});
  root.appendChild(cards);

  const maxc=Math.max(1,...d.daily.map(x=>x.credits));
  root.appendChild(sect('按天趋势（近 '+d.window_days+' 天）', tbl(['日期','任务','credits','活跃安装','' ],
    d.daily.map(x=>[x.day,x.events,fmt(x.credits),x.active_installs,{html:'<span class="bar" style="width:'+(x.credits/maxc*180)+'px"></span>'}]))));

  root.appendChild(sect('按版本', tbl(['version','任务','credits','安装'],
    d.by_version.map(x=>[x.version,x.events,fmt(x.credits),x.installs]))));

  root.appendChild(sect('按 env（仅呈现，未剔除）', tbl(['env','任务','credits','安装'],
    Object.entries(d.by_env).map(([k,v])=>[k,v.events,fmt(v.credits),v.installs]))));

  if(d.by_channel) root.appendChild(sect('按渠道（frozen 打包 / source 源码）', tbl(['channel','任务','credits','安装'],
    Object.entries(d.by_channel).map(([k,v])=>[k,v.events,fmt(v.credits),v.installs]))));

  if(d.by_os) root.appendChild(sect('按系统', tbl(['os','任务'],
    Object.entries(d.by_os).map(([k,v])=>[k,v]))));

  root.appendChild(sect('按能力', tbl(['kind','任务','credits'],
    Object.entries(d.by_kind).map(([k,v])=>[k,v.events,fmt(v.credits)]))));

  root.appendChild(sect('Top 安装', tbl(['install','version','任务','credits','最近活跃'],
    d.top_installs.map(x=>[x.install,x.version,x.events,fmt(x.credits),daysAgo(x.last_ts)]))));
}
const saved=localStorage.getItem('ha_tok'); if(saved){$('#tok').value=saved; load();}
</script>
</body></html>"""


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
