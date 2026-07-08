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

import hashlib
import hmac
import json
import os
import platform
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
AGENT_VERSION = "1.0.0"          # 兜底版本（读不到 VERSION 文件时用）

# 每次进程启动生成一次，用于区分同一安装的不同启动会话
_SESSION_ID = uuid.uuid4().hex
# account_hash 的盐：把 Meshy API Key 单向哈希成稳定去重标识用，绝不上报 key 本身
_ACCOUNT_SALT = b"ha-telemetry/account/v1"

# 默认上报端点（沿用项目主站，/api 反代到本地后端 127.0.0.1:8000）
DEFAULT_URL = "https://houdini-agent.com/api/telemetry"

_ENV_URL = ("HAGENT_TELEMETRY_URL", "DCC_AI_TELEMETRY_URL")
_ENV_OFF = ("HAGENT_TELEMETRY_OFF", "DCC_AI_TELEMETRY_OFF")
_ENV_SEND_PROMPT = ("HAGENT_TELEMETRY_SEND_PROMPT", "DCC_AI_TELEMETRY_SEND_PROMPT")
_CFG_URL_KEY = "telemetry_url"
_CFG_INSTALL_KEY = "telemetry_install_id"
_CFG_OPTOUT_KEY = "telemetry_optout"
_CFG_SEND_PROMPT_KEY = "telemetry_send_prompt"   # 默认不上报提示词原文（隐私）

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


def send_prompt_enabled():
    """是否上报提示词原文。默认【关闭】——只统计 credits 用量，不外传用户的提示词内容。
    需用户/部署方显式开启（env HAGENT_TELEMETRY_SEND_PROMPT=1 或 ini telemetry_send_prompt:1）。"""
    for var in _ENV_SEND_PROMPT:
        v = (os.environ.get(var) or "").strip().lower()
        if v in ("1", "true", "yes", "on"):
            return True
    cfg, _ = load_config("ai", dcc_type="houdini")
    if cfg:
        v = (cfg.get(_CFG_SEND_PROMPT_KEY) or "").strip().lower()
        if v in ("1", "true", "yes", "on"):
            return True
    return False


_app_version_cache = [None]


def app_version():
    """真实 app 版本（读 VERSION 文件；打包后取 _MEIPASS，源码取仓库根）。
    此前这里写死 '1.0.0'，导致埋点分不出版本——现在上报真实版本。"""
    if _app_version_cache[0]:
        return _app_version_cache[0]
    v = ""
    roots = []
    mei = getattr(sys, "_MEIPASS", None)
    if mei:
        roots.append(mei)
    roots.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    for r in roots:
        try:
            with open(os.path.join(r, "VERSION"), "r", encoding="utf-8") as f:
                s = f.read().strip()
            if s:
                v = s.splitlines()[0].strip()
                break
        except Exception:
            pass
    v = v or AGENT_VERSION
    _app_version_cache[0] = v
    return v


def _channel():
    """运行渠道：打包 exe(frozen) 还是源码(source)。"""
    return "frozen" if (getattr(sys, "frozen", False) or getattr(sys, "_MEIPASS", None)) else "source"


def _env():
    """环境维度：显式 env 覆盖 > 源码=dev > 打包=prod。仅作维度呈现，不做任何剔除。"""
    for var in ("HAGENT_TELEMETRY_ENV", "DCC_AI_TELEMETRY_ENV"):
        v = (os.environ.get(var) or "").strip().lower()
        if v:
            return v
    return "prod" if _channel() == "frozen" else "dev"


_account_hash_cache = [None]


def account_hash():
    """把 Meshy API Key 单向哈希成稳定去重标识（跨机器/重装识别同一付费账号）。
    只上报 16 位哈希前缀，【绝不上报 key 本身】；无 key 时返回 None。"""
    if _account_hash_cache[0]:
        return _account_hash_cache[0]
    key = ""
    try:
        from . import config as _mcfg
        key = (_mcfg.get_api_key() or "").strip()
    except Exception:
        key = (os.environ.get("MESHY_API_KEY") or "").strip()
    h = ""
    if key:
        try:
            h = hmac.new(_ACCOUNT_SALT, key.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
        except Exception:
            h = ""
    if h:
        _account_hash_cache[0] = h        # 只缓存成功结果；无 key 时不缓存，下次可重算
    return h or None


def _user_state_dir():
    """用户级状态目录（跨"源码/打包/重装"稳定，不随构建目录变化）。"""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "HoudiniAgent")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "houdini-agent")


_install_id_cache = [None]


def install_id():
    """匿名安装 ID：存用户级目录，跨源码/打包/重装稳定。
    迁移：用户级文件不存在时，沿用旧 ini 里的 install_id，保证老用户身份连续（不被算成新用户）。"""
    if _install_id_cache[0]:
        return _install_id_cache[0]
    path = os.path.join(_user_state_dir(), "install_id")
    iid = ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            iid = f.read().strip()
    except Exception:
        iid = ""
    if not iid:
        # 迁移旧的 ini 内 install_id（保持身份连续），没有则新生成
        cfg, _ = load_config("ai", dcc_type="houdini")
        iid = ((cfg or {}).get(_CFG_INSTALL_KEY) or "").strip() or uuid.uuid4().hex
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(iid)
        except Exception:
            pass
    _install_id_cache[0] = iid
    return iid


# ---------------------------------------------------------------- spool

_lock = threading.Lock()
_seen_tasks = set()         # 进程内去重，防止轮询重复入账
_seen_order = []            # 与 _seen_tasks 配套的插入顺序，用于有界淘汰（避免长跑内存泄漏）
_SEEN_MAX = 5000
_write_threads = []         # 挂起的异步落盘线程（供 _wait_writes 在测试/关闭时 join）


def _wait_writes(timeout=5.0):
    """等待挂起的埋点落盘线程完成。供测试同步断言、或进程退出前确保落盘。"""
    for th in list(_write_threads):
        try:
            th.join(timeout)
        except Exception:
            pass
    with _lock:
        _write_threads[:] = [x for x in _write_threads if x.is_alive()]


def _mark_seen_locked(tid):
    """在持 _lock 时把 tid 记入去重集合，并做有界淘汰。"""
    if tid in _seen_tasks:
        return
    _seen_tasks.add(tid)
    _seen_order.append(tid)
    while len(_seen_order) > _SEEN_MAX:
        old = _seen_order.pop(0)
        _seen_tasks.discard(old)


def _extract_prompt(task):
    p = (task.get("prompt") or task.get("texture_prompt")
         or task.get("text_style_prompt") or task.get("name") or "")
    p = str(p)
    return p[:_PROMPT_MAX] if len(p) > _PROMPT_MAX else p


def _build_event(kind, task, status="SUCCEEDED"):
    tid = str(task.get("id") or task.get("task_id") or "")
    credits = task.get("consumed_credits")
    try:
        credits = int(credits or 0)
    except Exception:
        credits = 0
    prompt_text = _extract_prompt(task)
    ev = {
        "event_id": uuid.uuid4().hex,
        "install_id": install_id(),
        "account_hash": account_hash(),     # 同一付费账号稳定去重（不含 key 本身）
        "ts": int(time.time()),
        "agent": AGENT,
        "version": app_version(),           # 真实 app 版本（不再写死 1.0.0）
        "env": _env(),                      # dev / prod（仅维度，不剔除）
        "channel": _channel(),              # frozen / source
        "session_id": _SESSION_ID,          # 本次启动会话
        "os": platform.system() or None,    # Windows / Darwin / Linux
        "kind": kind,                       # text-to-3d / image-to-3d / ...
        "task_id": tid,
        "ai_model": task.get("ai_model"),
        "mode": task.get("mode"),           # text-to-3d 的 preview/refine
        "status": status,                   # SUCCEEDED / FAILED / CANCELED
        "credits": credits,
        "prompt_len": len(prompt_text),     # 始终上报长度（无隐私），便于粗略分析
    }
    # 提示词原文默认不外传；仅在用户/部署方显式开启时才带上。
    if send_prompt_enabled():
        ev["prompt"] = prompt_text
    return ev


def _append_spool(event):
    path = _spool_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = json.dumps(event, ensure_ascii=False)
    with _lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def record_task(kind, task, status="SUCCEEDED"):
    """采集一次任务的用量（终态）。status 默认 SUCCEEDED，失败/取消也可记以便看成功率。
    任何异常都吞掉——埋点绝不能影响生成主流程。

    本函数在 client.wait() 的生成主流程线程里被同步调用，因此只做最廉价的内存
    去重判断，落盘/上传放到后台线程，避免磁盘 IO 拖慢生成结果返回（#32）。"""
    try:
        if not isinstance(task, dict):
            return
        tid = str(task.get("id") or task.get("task_id") or "")
        if tid:
            with _lock:
                if tid in _seen_tasks:
                    return
        th = threading.Thread(target=_persist_task, args=(kind, task, tid, status),
                              name="meshy-telemetry-write", daemon=True)
        with _lock:
            _write_threads.append(th)
            _write_threads[:] = [x for x in _write_threads if x.is_alive() or x is th]
        th.start()
    except Exception as e:
        try:
            print("[meshy] telemetry record failed: %s" % e)
        except Exception:
            pass


def _persist_task(kind, task, tid, status="SUCCEEDED"):
    """后台线程：判定开关 → 落盘 → 落盘成功后再标记 seen（#30：落盘失败不标记，
    下次仍可重试，不会永久漏报）→ 唤起上传线程。"""
    try:
        if not is_enabled():
            return
        _append_spool(_build_event(kind, task, status))
        if tid:
            with _lock:
                _mark_seen_locked(tid)
        _ensure_uploader()
    except Exception as e:
        try:
            print("[meshy] telemetry persist failed: %s" % e)
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
