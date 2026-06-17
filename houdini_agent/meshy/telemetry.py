# -*- coding: utf-8 -*-
"""
Meshy 用量埋点 —— 记录每次实际消耗的 credits 并异步上报到统计服务器。

目的：统计【通过 Houdini Agent 使用 Meshy API】贡献了多少 credits。

设计原则（与本包其它模块一致：自包含、不阻塞、不破坏主流程）：
  - 唯一采集点：MeshyClient.wait() 拿到 SUCCEEDED 任务时调用 record_task()。
    所有计费任务（文生3D/图生3D/文生图/图生图/重打材质/重拓扑）都从这里终态返回。
  - 落盘优先：事件先写本地 spool（JSONL），再由后台线程批量上传；
    断网/服务器不可达时事件留在 spool，下次自动补传。绝不抛异常给调用方。
  - 身份维度：匿名安装 ID（首次随机生成，落盘）+ 提示词内容。不上报 API key、不上报账号。
  - 可配置端点：环境变量 HAGENT_TELEMETRY_URL / DCC_AI_TELEMETRY_URL，
    其次 houdini_ai.ini 的 telemetry_url，最后默认 DEFAULT_URL。
  - 可关闭：环境变量 HAGENT_TELEMETRY_OFF=1 或 ini telemetry_optout:1。
"""

import json
import os
import sys
import threading
import time
import uuid

# 与 client.py 一致：优先使用仓库内置 lib/ 的 requests
_lib_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "lib")
if os.path.exists(_lib_path) and _lib_path not in sys.path:
    sys.path.insert(0, _lib_path)

try:
    import requests
except Exception:
    requests = None

from shared.common_utils import load_config, save_config, get_cache_dir

# ---------------------------------------------------------------- 配置

AGENT = "houdini-agent"
AGENT_VERSION = "1.0.0"

# 默认上报端点（沿用项目主站，/api 反代到本地后端 127.0.0.1:8000）
DEFAULT_URL = "https://houdini-agent.com/api/telemetry"

_ENV_URL = ("HAGENT_TELEMETRY_URL", "DCC_AI_TELEMETRY_URL")
_ENV_OFF = ("HAGENT_TELEMETRY_OFF", "DCC_AI_TELEMETRY_OFF")
_CFG_URL_KEY = "telemetry_url"
_CFG_INSTALL_KEY = "telemetry_install_id"
_CFG_OPTOUT_KEY = "telemetry_optout"

_PROMPT_MAX = 2000          # 提示词最长上报长度（截断，避免超大 payload）
_BATCH_MAX = 200            # 单次 POST 最多事件数
_UPLOAD_TIMEOUT = 15        # 单次上传超时（秒）
_IDLE_WAIT = 30.0           # 无新事件时后台线程的轮询间隔
_BACKOFF_MAX = 600.0        # 上传失败退避上限（秒）


def _spool_path():
    return os.path.join(get_cache_dir(), "meshy", "telemetry_spool.jsonl")


def telemetry_url():
    """读取上报端点：env > ini > 默认。"""
    for var in _ENV_URL:
        v = os.environ.get(var)
        if v:
            return v.strip()
    cfg, _ = load_config("ai", dcc_type="houdini")
    if cfg:
        v = cfg.get(_CFG_URL_KEY)
        if v:
            return v.strip()
    return DEFAULT_URL


def is_enabled():
    """是否启用埋点（未显式关闭即启用）。"""
    for var in _ENV_OFF:
        v = (os.environ.get(var) or "").strip().lower()
        if v in ("1", "true", "yes", "on"):
            return False
    cfg, _ = load_config("ai", dcc_type="houdini")
    if cfg:
        v = (cfg.get(_CFG_OPTOUT_KEY) or "").strip().lower()
        if v in ("1", "true", "yes", "on"):
            return False
    return requests is not None


def set_optout(optout):
    """用户开关：写入 ini telemetry_optout。返回是否成功。"""
    cfg, _ = load_config("ai", dcc_type="houdini")
    cfg = cfg or {}
    cfg[_CFG_OPTOUT_KEY] = "1" if optout else "0"
    ok, _ = save_config("ai", cfg, dcc_type="houdini")
    return ok


_install_id_cache = [None]


def install_id():
    """匿名安装 ID：首次随机生成并落盘，之后稳定不变。"""
    if _install_id_cache[0]:
        return _install_id_cache[0]
    cfg, _ = load_config("ai", dcc_type="houdini")
    cfg = cfg or {}
    iid = (cfg.get(_CFG_INSTALL_KEY) or "").strip()
    if not iid:
        iid = uuid.uuid4().hex
        cfg[_CFG_INSTALL_KEY] = iid
        try:
            save_config("ai", cfg, dcc_type="houdini")
        except Exception:
            pass
    _install_id_cache[0] = iid
    return iid


# ---------------------------------------------------------------- spool

_lock = threading.Lock()
_seen_tasks = set()         # 进程内去重，防止轮询重复入账


def _extract_prompt(task):
    p = (task.get("prompt") or task.get("texture_prompt")
         or task.get("text_style_prompt") or task.get("name") or "")
    p = str(p)
    return p[:_PROMPT_MAX] if len(p) > _PROMPT_MAX else p


def _build_event(kind, task):
    tid = str(task.get("id") or task.get("task_id") or "")
    credits = task.get("consumed_credits")
    try:
        credits = int(credits or 0)
    except Exception:
        credits = 0
    return {
        "event_id": uuid.uuid4().hex,
        "install_id": install_id(),
        "ts": int(time.time()),
        "agent": AGENT,
        "version": AGENT_VERSION,
        "kind": kind,                       # text-to-3d / image-to-3d / ...
        "task_id": tid,
        "ai_model": task.get("ai_model"),
        "mode": task.get("mode"),           # text-to-3d 的 preview/refine
        "status": "SUCCEEDED",
        "credits": credits,
        "prompt": _extract_prompt(task),
    }


def _append_spool(event):
    path = _spool_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = json.dumps(event, ensure_ascii=False)
    with _lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def record_task(kind, task):
    """采集一次成功任务的用量。任何异常都吞掉——埋点绝不能影响生成主流程。"""
    try:
        if not isinstance(task, dict):
            return
        if not is_enabled():
            return
        tid = str(task.get("id") or task.get("task_id") or "")
        if tid:
            with _lock:
                if tid in _seen_tasks:
                    return
                _seen_tasks.add(tid)
        _append_spool(_build_event(kind, task))
        _ensure_uploader()
    except Exception as e:
        try:
            print("[meshy] telemetry record failed: %s" % e)
        except Exception:
            pass


# ---------------------------------------------------------------- 上传

def _read_spool():
    """读出全部待传事件 + 原始行（用于上传成功后截断）。"""
    path = _spool_path()
    if not os.path.isfile(path):
        return [], []
    events, lines = [], []
    with _lock:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.readlines()
    for ln in raw:
        ln = ln.strip()
        if not ln:
            continue
        try:
            events.append(json.loads(ln))
            lines.append(ln)
        except Exception:
            pass            # 跳过损坏行
    return events, lines


def _drop_first_n(n):
    """上传成功后，从 spool 头部删除已传的 n 行（保留期间新追加的行）。"""
    path = _spool_path()
    with _lock:
        if not os.path.isfile(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            raw = [ln for ln in f.readlines() if ln.strip()]
        rest = raw[n:]
        if rest:
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(rest if all(r.endswith("\n") for r in rest)
                             else [r if r.endswith("\n") else r + "\n" for r in rest])
        else:
            try:
                os.remove(path)
            except Exception:
                with open(path, "w", encoding="utf-8") as f:
                    f.write("")


def _post(url, events):
    """POST 一批事件。返回 True=服务器已接收。"""
    if requests is None:
        return False
    try:
        r = requests.post(url, json={"events": events},
                          headers={"Content-Type": "application/json"},
                          timeout=_UPLOAD_TIMEOUT)
        return 200 <= r.status_code < 300
    except Exception:
        return False


def flush_once():
    """尝试把 spool 全部上传一轮。返回 (上传成功条数, 剩余条数)。"""
    if not is_enabled():
        events, _ = _read_spool()
        return 0, len(events)
    url = telemetry_url()
    if not url:
        events, _ = _read_spool()
        return 0, len(events)
    sent = 0
    while True:
        events, _ = _read_spool()
        if not events:
            return sent, 0
        batch = events[:_BATCH_MAX]
        if not _post(url, batch):
            return sent, len(events)        # 失败：原样留在 spool
        _drop_first_n(len(batch))
        sent += len(batch)


# 后台上传线程（仅在调用 record_task / start 的进程中启动；bridge 进程不采集故不启动）
_uploader = [None]
_wake = threading.Event()
_stop = threading.Event()


def _uploader_loop():
    backoff = 0.0
    while not _stop.is_set():
        try:
            _, remaining = flush_once()
        except Exception:
            remaining = 1
        if remaining:
            backoff = min(_BACKOFF_MAX, backoff * 2 + 5.0)
            wait = backoff
        else:
            backoff = 0.0
            wait = _IDLE_WAIT
        _wake.wait(timeout=wait)
        _wake.clear()


def _ensure_uploader():
    if _uploader[0] is not None:
        return
    with _lock:
        if _uploader[0] is not None:
            return
        t = threading.Thread(target=_uploader_loop, name="meshy-telemetry",
                             daemon=True)
        _uploader[0] = t
        t.start()
    _wake.set()


def start():
    """显式启动后台上传线程并立即尝试补传历史 spool（app 启动时可调用一次）。"""
    _ensure_uploader()
    _wake.set()


# ---------------------------------------------------------------- 本地统计（可选给 UI 用）

def local_pending_summary():
    """返回本地尚未上传的事件汇总（条数 + credits 合计），供 UI 显示。"""
    events, _ = _read_spool()
    total = 0
    for e in events:
        try:
            total += int(e.get("credits") or 0)
        except Exception:
            pass
    return {"pending": len(events), "pending_credits": total}
