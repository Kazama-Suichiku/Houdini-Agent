# -*- coding: utf-8 -*-
"""
托管本机 CLIProxyAPI（CPA）进程，用 ChatGPT 订阅（Codex OAuth）提供 OpenAI 兼容接口。

目录（用户级，不随安装目录变化）：
    <LOCALAPPDATA>/HoudiniAgent/cpa/
        bin/<版本>/cli-proxy-api.exe     下载并校验 sha256 后解压
        config.yaml                       每次启动前按本模块设置重写
        auth/                             CPA 的 OAuth 凭据目录（只在本机，不上传）
        state.json                        本机端口、客户端 key、模型缓存
        cpa.log                           服务日志

安全：服务只监听 127.0.0.1，并要求随机生成的客户端 key；关闭管理接口与管理面板下载。
凭据文件只读取 type / email / 套餐等非敏感字段用于显示，token 不离开 auth 目录。

线程：install / ensure_running / 登录等待均为阻塞调用，调用方需放在后台线程。
"""

import atexit
import base64
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import zipfile
from pathlib import Path

CPA_VERSION = "8.1.4"
_REPO = "router-for-me/CLIProxyAPI"

# 当前版本各平台发布包的 sha256（取自官方 release 的 checksums.txt），下载后逐一校验
_SHA256 = {
    "CLIProxyAPI_8.1.4_windows_amd64.zip": "665b670d42a5ceeadd33a6f03dffaa362cde920b726ab6b117c4def94ff81b09",
    "CLIProxyAPI_8.1.4_windows_aarch64.zip": "7fe1c3259c88e9bd0b4a7dfdad67105a1dc43ddca0896ce94c7d648ec1364713",
    "CLIProxyAPI_8.1.4_darwin_aarch64.tar.gz": "9d9e2350d4af9c2cd1c3636a5892e3b669829284f3a54b252fe1bdfb6b49d93a",
    "CLIProxyAPI_8.1.4_darwin_amd64.tar.gz": "733f276d73df67724f5dee338a0e8db552d308525d27e614984b372061aec37f",
    "CLIProxyAPI_8.1.4_linux_amd64.tar.gz": "d36d5304eded3884cd0c4168c41a7638a3234631a64a54644069b963ae287cea",
    "CLIProxyAPI_8.1.4_linux_aarch64.tar.gz": "1ac19c4468969a3fa85c483ef7e34c5646d6e0d610a520a0dd51af13613d6619",
}
# 下载来源：官网镜像（国内更稳）→ GitHub Release。sha256 固定，任一来源都安全。
_MIRROR_URL = "https://houdini-agent.com/download/cpa/{asset}"
_GITHUB_URL = "https://github.com/" + _REPO + "/releases/download/v{ver}/{asset}"

# Codex 订阅下可见的模型（CPA 内置 Codex 目录，visibility=list），按推荐顺序
CODEX_MODELS = [
    "gpt-6.2-sol", "gpt-6.2-astra", "gpt-6.1-sol", "gpt-6.1-terra",
    "gpt-6-astra", "gpt-6-sol", "gpt-6-luna",
    "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5",
]
# Codex 订阅下内置生图模型兜底表（实际列表会随 CPA /v1/models 自动同步更新，包括未来新版本）
CODEX_IMAGE_MODELS = [
    "gpt-image-2.6",
    "gpt-image-2.6-sunburst",
    "gpt-image-2.6-flare",
    "gpt-image-2.5",
    "gpt-image-2.5-sunburst",
    "gpt-image-2.5-flare",
    "gpt-image-2",
    "gpt-image-1.5",
]
# Codex 渠道的上下文窗口与 API 直连不同（同名模型直连多为 1M），单独维护
CODEX_CONTEXT = {"gpt-6.2-sol": 500000, "gpt-6.1-sol": 500000, "gpt-6.1-terra": 500000}
CODEX_DEFAULT_CONTEXT = 272000

# 各模型支持的推理强度与默认档（CPA 内置 Codex 目录的 supported_reasoning_levels / default_reasoning_level）
_EFFORT_ORDER = ["low", "medium", "high", "xhigh", "max", "ultra"]
CODEX_EFFORTS = {
    "gpt-6.2-sol": _EFFORT_ORDER, "gpt-6.2-astra": _EFFORT_ORDER,
    "gpt-6.1-sol": _EFFORT_ORDER, "gpt-6.1-terra": _EFFORT_ORDER,
    "gpt-6-astra": _EFFORT_ORDER, "gpt-6-sol": _EFFORT_ORDER,
    "gpt-5.6-sol": _EFFORT_ORDER, "gpt-5.6-terra": _EFFORT_ORDER,
    "gpt-6-luna": _EFFORT_ORDER[:5], "gpt-5.6-luna": _EFFORT_ORDER[:5],
    "gpt-5.5": _EFFORT_ORDER[:4],
}
CODEX_DEFAULT_EFFORT = {
    "gpt-6.2-sol": "low", "gpt-6.2-astra": "low",
    "gpt-6.1-sol": "low", "gpt-6.1-terra": "low", "gpt-5.6-sol": "low",
}


def codex_efforts(model) -> list:
    """模型支持的推理强度（目录外的新模型保守给 low / medium / high）。"""
    return list(CODEX_EFFORTS.get(str(model or ""), _EFFORT_ORDER[:3]))


def codex_default_effort(model) -> str:
    return CODEX_DEFAULT_EFFORT.get(str(model or ""), "medium")


# Claude 订阅（CPA 的 Claude Code OAuth）。注意：Anthropic 不允许第三方产品使用 Claude 订阅，
# 服务端可能拒绝或限制账号；界面上会向用户说明，是否使用由用户自己决定。
CLAUDE_MODELS = [
    "claude-opus-5-5", "claude-fable-5-2", "claude-sonnet-5-5", "claude-haiku-5-5",
    "claude-fable-5-1", "claude-opus-5", "claude-sonnet-5", "claude-opus-4-8",
]
CLAUDE_CONTEXT = {m: 1000000 for m in CLAUDE_MODELS}     # CPA 内置目录 context_length
CLAUDE_CONTEXT.update({"claude-opus-4-7": 1000000, "claude-opus-4-6": 1000000, "claude-fable-5": 1000000})
CLAUDE_DEFAULT_CONTEXT = 200000
CLAUDE_EFFORTS = ["low", "medium", "high", "xhigh", "max"]

# Google 反重力（CPA 的 -antigravity-login，使用 Google 账号 OAuth 接入 Gemini / Claude 模型）
ANTIGRAVITY_MODELS = [
    "gemini-4-neon-xhigh", "gemini-4-hydrogen-xhigh",
    "gemini-4-argon-high", "gemini-4-carbon-high",
    "claude-opus-5-5-high", "claude-sonnet-5-5-high",
    "gemini-3.8-flash-high", "gemini-3.8-flash-tiered",
    "gemini-3-flash", "gemini-3.5-flash-lite",
    "claude-opus-4-6-thinking", "claude-sonnet-4-6",
    "gpt-oss-120b-medium",
]
ANTIGRAVITY_CONTEXT = {
    "gemini-4-neon-xhigh": 1048576, "gemini-4-hydrogen-xhigh": 1048576,
    "gemini-4-argon-high": 1048576, "gemini-4-carbon-high": 1048576,
    "gemini-3.8-flash-high": 1048576, "gemini-3.8-flash-tiered": 1048576,
    "gemini-3-flash": 1048576, "gemini-3.5-flash-lite": 1048576, "gemini-3.1-flash-lite": 1048576,
    "claude-opus-5-5-high": 1000000, "claude-sonnet-5-5-high": 1000000,
    "claude-opus-4-6-thinking": 200000, "claude-sonnet-4-6": 200000,
    "gpt-oss-120b-medium": 114000,
}
ANTIGRAVITY_DEFAULT_CONTEXT = 1048576
ANTIGRAVITY_EFFORTS = ["low", "medium", "high"]
ANTIGRAVITY_XHIGH_EFFORTS = ["low", "medium", "high", "xhigh"]

# 订阅类型：provider key（界面里的供应商）→ CPA 凭据类型
KINDS = {"codex": "codex", "claude_sub": "claude", "antigravity": "antigravity"}


def kind_of(provider):
    """供应商是否是订阅类型；是则返回 CPA 凭据类型（codex / claude / antigravity），否则 None。"""
    return KINDS.get(str(provider or ""))


def default_models(kind) -> list:
    if kind == "claude":
        return list(CLAUDE_MODELS)
    if kind == "antigravity":
        return list(ANTIGRAVITY_MODELS)
    return list(CODEX_MODELS)


def model_context(kind, model) -> int:
    m = str(model or "")
    if kind == "claude":
        return CLAUDE_CONTEXT.get(m, CLAUDE_DEFAULT_CONTEXT)
    if kind == "antigravity":
        return ANTIGRAVITY_CONTEXT.get(m, ANTIGRAVITY_DEFAULT_CONTEXT)
    return CODEX_CONTEXT.get(m, CODEX_DEFAULT_CONTEXT)


def effort_levels(kind, model) -> list:
    if kind == "claude":
        return list(CLAUDE_EFFORTS)
    if kind == "antigravity":
        m = str(model or "").lower()
        if "gpt-oss" in m:
            return []
        return list(ANTIGRAVITY_XHIGH_EFFORTS if "xhigh" in m else ANTIGRAVITY_EFFORTS)
    return codex_efforts(model)


def default_effort(kind, model) -> str:
    if kind in ("claude", "antigravity"):
        return "medium"
    return codex_default_effort(model)


_HIDDEN_MODELS = {"gpt-reserve", "codex-auto-review", "gpt-6-chat-thinking"}
# Anthropic 已退役的旧版 Claude 3.x 与 4.0/4.1 带日期快照，不再塞进下拉列表干扰选择
_RETIRED_CLAUDE_RE = re.compile(
    r"^claude-3-|^claude-(?:opus|sonnet|haiku)-4(?:-[01])?-\d{8}$", re.I
)
_CLAUDE_VER_RE = re.compile(r"^claude-([a-z]+)-(\d+)(?:[-.](\d+))?", re.I)
_CLAUDE_TIER_ORDER = {"fable": 0, "mythos": 0, "opus": 1, "sonnet": 2, "haiku": 3}
_IMAGE_TOKEN_RE = re.compile(r"(?:^|[-_/])image(?:$|[-_/.0-9])", re.I)
_VERSION_RE = re.compile(r"(\d+(?:\.\d+)*)")
_IMAGE_VARIANT_ORDER = {"": 0, "sunburst": 1, "flare": 2, "pro": 3, "mini": 4}
_OPENAI_IMAGE_SIZES = {"1024x1024", "1536x1024", "1024x1536", "auto"}


def _model_kind(model_id, owned_by="") -> str:
    """CPA 把所有已登录渠道的模型合在一个 /v1/models 里，优先按 owned_by，其次按名字分回各订阅。"""
    ob = str(owned_by or "").strip().lower()
    if ob == "antigravity":
        return "antigravity"
    if ob in ("anthropic", "claude"):
        return "claude"
    if ob in ("openai", "codex"):
        return "codex"
    low = str(model_id or "").strip().lower()
    if low.startswith(("gemini-", "gpt-oss-")):
        return "antigravity"
    if low.startswith("claude-"):
        if low.endswith(("-high", "-xhigh", "-thinking")):
            return "antigravity"
        return "claude"
    return "codex"


def is_image_model(model_id) -> bool:
    """判断 CPA /v1/models 里的模型 ID 是否是 Codex 生图模型（自动兼容未来 gpt-image-* 等新系列）。"""
    m = str(model_id or "").strip()
    if not m or m in _HIDDEN_MODELS or _model_kind(m) != "codex":
        return False
    low = m.lower()
    return (low.startswith(("gpt-image", "chatgpt-image", "dall-e"))
            or bool(_IMAGE_TOKEN_RE.search(low)))


def _is_any_image_model(model_id) -> bool:
    """任意渠道的图片生成模型（含 Codex gpt-image-* 与 Antigravity gemini-*-image），均不列为对话模型。"""
    low = str(model_id or "").strip().lower()
    return bool(low) and (low.startswith(("gpt-image", "chatgpt-image", "dall-e"))
                          or bool(_IMAGE_TOKEN_RE.search(low)))


def _is_chat_model(model_id) -> bool:
    """过滤非对话模型（gpt-image-* / gemini-*-image）、隐藏模型及已退役的旧版 Claude 3.x / 4.0 快照。"""
    m = str(model_id or "").strip()
    if not m or m in _HIDDEN_MODELS or _is_any_image_model(m):
        return False
    if _RETIRED_CLAUDE_RE.match(m):
        return False
    return True


def _version_desc_key(model_id: str):
    """按版本号降序排序新发现的对话模型（如 claude-opus-6 / claude-fable-5-3 / gpt-6.3-sol 排在前面）。"""
    low = str(model_id or "").strip().lower()
    mc = _CLAUDE_VER_RE.match(low)
    if mc:
        tier = _CLAUDE_TIER_ORDER.get(mc.group(1), 5)
        ver = (int(mc.group(2)), int(mc.group(3) or 0))
        dated = 1 if re.search(r"-\d{8}$", low) else 0
        return (dated, -ver[0], -ver[1], tier, low)
    mv = _VERSION_RE.search(low)
    if mv:
        ver = tuple(int(p) for p in mv.group(1).split("."))
        padded = tuple(-x for x in (ver + (0, 0, 0))[:4])
        return (0, padded[0], padded[1], padded[2], low)
    return (1, 0, 0, 0, low)


def _image_model_sort_key(model_id: str):
    """按版本号降序排列生图模型，同版本基础款排在变体前（如 2.6 > 2.5 > 2.5-sunburst > 2 > 1.5）。"""
    low = str(model_id or "").strip().lower()
    m = _VERSION_RE.search(low)
    if m:
        ver = tuple(int(p) for p in m.group(1).split("."))
        tail = low[m.end():].lstrip("-_.")
    else:
        ver = (0,)
        tail = low
    # 负版本号实现降序；补齐长度避免 (2, 5) 与 (2,) 比较歧义
    padded = tuple(-x for x in (ver + (0, 0, 0))[:4])
    is_gpt_image = 0 if low.startswith("gpt-image") else 1
    var_rank = _IMAGE_VARIANT_ORDER.get(tail, 10)
    return (is_gpt_image, padded, var_rank, tail, low)


def _sort_image_models(ids) -> list:
    uniq = list(dict.fromkeys(str(i).strip() for i in (ids or []) if str(i or "").strip()))
    return sorted(uniq, key=_image_model_sort_key)


def aspect_ratio_to_size(aspect_ratio) -> str:
    """把画幅比例（如 1:1 / 16:9 / 9:16）映射到 OpenAI / Codex /v1/images/* 支持的 size。"""
    s = str(aspect_ratio or "").strip().lower()
    if not s:
        return "1024x1024"
    if s in _OPENAI_IMAGE_SIZES:
        return s
    m = re.match(r"^(\d+(?:\.\d+)?)\s*[:/x]\s*(\d+(?:\.\d+)?)$", s)
    if m:
        w, h = float(m.group(1)), float(m.group(2))
        if w > h:
            return "1536x1024"
        if h > w:
            return "1024x1536"
    return "1024x1024"

_DEFAULT_PORT = 18317          # 避开 CPA 默认 8317，免得和用户自己跑的 CPA 冲突
_LOGIN_TIMEOUT = 15 * 60

_IS_WIN = os.name == "nt"
_NO_WINDOW = 0x08000000 if _IS_WIN else 0          # CREATE_NO_WINDOW


class CPAError(Exception):
    pass


def _base_dir() -> Path:
    if _IS_WIN:
        root = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(root) / "HoudiniAgent" / "cpa"
    return Path.home() / ".houdini_agent" / "cpa"


def _platform_asset():
    mach = platform.machine().lower()
    arm = mach in ("arm64", "aarch64")
    if _IS_WIN:
        osname, ext = "windows", "zip"
    elif sys.platform == "darwin":
        osname, ext = "darwin", "tar.gz"
    else:
        osname, ext = "linux", "tar.gz"
    arch = "aarch64" if arm else "amd64"
    return "CLIProxyAPI_%s_%s_%s.%s" % (CPA_VERSION, osname, arch, ext)


def _requests():
    try:
        import requests  # type: ignore
    except ImportError:
        lib = str(Path(__file__).resolve().parents[2] / "lib")
        if lib not in sys.path:
            sys.path.insert(0, lib)
        import requests  # type: ignore
    return requests


def system_proxy() -> str:
    """系统代理（Windows 读注册表）。CPA 是 Go 程序，在 Windows 上不会自己读系统代理，
    国内用户开着系统代理时必须显式写进 CPA 配置，否则连不上 OpenAI。"""
    try:
        import urllib.request
        px = urllib.request.getproxies()
    except Exception:
        return ""
    url = px.get("https") or px.get("http") or ""
    if url and "://" not in url:
        url = "http://" + url
    return url


def _decode_jwt_claims(token):
    try:
        payload = str(token).split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
    except Exception:
        return {}


def _port_open(port) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.5):
            return True
    except OSError:
        return False


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class CPAManager:
    def __init__(self, base=None):
        self.base = Path(base) if base else _base_dir()
        self._lock = threading.RLock()
        self._proc = None              # 我们拉起的服务进程（只停自己拉起的）
        self._login = None             # 当前登录会话 dict
        self._state = None

    # ---------------------------------------------------------------- paths / state
    @property
    def bin_dir(self) -> Path:
        return self.base / "bin" / CPA_VERSION

    @property
    def exe(self) -> Path:
        return self.bin_dir / ("cli-proxy-api.exe" if _IS_WIN else "cli-proxy-api")

    @property
    def auth_dir(self) -> Path:
        return self.base / "auth"

    @property
    def config_path(self) -> Path:
        return self.base / "config.yaml"

    def _load_state(self) -> dict:
        if self._state is None:
            try:
                self._state = json.loads((self.base / "state.json").read_text(encoding="utf-8"))
            except Exception:
                self._state = {}
        st = self._state
        created = False
        if not st.get("key"):
            st["key"] = "ha-" + secrets.token_hex(16)
            created = True
        if not st.get("port"):
            st["port"] = _DEFAULT_PORT
            created = True
        if created:
            self._save_state()
        return st

    def _save_state(self):
        try:
            self.base.mkdir(parents=True, exist_ok=True)
            (self.base / "state.json").write_text(json.dumps(self._state, ensure_ascii=False, indent=2),
                                                  encoding="utf-8")
        except Exception:
            pass

    @property
    def port(self) -> int:
        return int(self._load_state()["port"])

    def client_key(self) -> str:
        return self._load_state()["key"]

    def base_url(self) -> str:
        return "http://127.0.0.1:%d/v1" % self.port

    def chat_url(self) -> str:
        return self.base_url() + "/chat/completions"

    def images_generations_url(self) -> str:
        return self.base_url() + "/images/generations"

    def images_edits_url(self) -> str:
        return self.base_url() + "/images/edits"

    # ---------------------------------------------------------------- install
    def installed(self) -> bool:
        return self.exe.is_file()

    def install(self, progress=None):
        """下载并校验 CPA 发布包，解压出可执行文件（已安装则直接返回）。progress(percent)。"""
        with self._lock:
            if self.installed():
                return
            asset = _platform_asset()
            sha = _SHA256.get(asset)
            if not sha:
                raise CPAError("当前平台暂不支持：%s" % asset)
            requests = _requests()
            urls = [_MIRROR_URL.format(asset=asset), _GITHUB_URL.format(ver=CPA_VERSION, asset=asset)]
            errors = []
            self.base.mkdir(parents=True, exist_ok=True)
            for url in urls:
                tmp = self.base / (asset + ".part")
                try:
                    resp = requests.get(url, stream=True, timeout=60)
                    resp.raise_for_status()
                    total = int(resp.headers.get("content-length", 0))
                    h = hashlib.sha256()
                    done = 0
                    with open(tmp, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=1 << 16):
                            if chunk:
                                f.write(chunk)
                                h.update(chunk)
                                done += len(chunk)
                                if progress and total:
                                    progress(int(done * 100 / total))
                    if h.hexdigest() != sha:
                        raise CPAError("校验值不符")
                    self._extract(tmp, asset)
                    return
                except Exception as e:
                    errors.append("%s：%s" % (url.split("/")[2], e))
                finally:
                    try:
                        tmp.unlink()
                    except Exception:
                        pass
            raise CPAError("下载订阅组件失败：" + "；".join(errors))

    def _extract(self, archive: Path, asset: str):
        out = Path(tempfile.mkdtemp(prefix="ha_cpa_", dir=str(self.base)))
        try:
            if asset.endswith(".zip"):
                with zipfile.ZipFile(archive) as z:
                    for name in z.namelist():
                        base = os.path.basename(name)
                        if base in ("cli-proxy-api.exe", "cli-proxy-api", "LICENSE"):
                            (out / base).write_bytes(z.read(name))
            else:
                with tarfile.open(archive, "r:gz") as t:
                    for m in t.getmembers():
                        base = os.path.basename(m.name)
                        if m.isfile() and base in ("cli-proxy-api", "LICENSE"):
                            f = t.extractfile(m)
                            (out / base).write_bytes(f.read())
            exe_name = self.exe.name
            if not (out / exe_name).is_file():
                raise CPAError("发布包里没有 %s" % exe_name)
            if not _IS_WIN:
                os.chmod(out / exe_name, 0o755)
            self.bin_dir.parent.mkdir(parents=True, exist_ok=True)
            if self.bin_dir.exists():
                shutil.rmtree(self.bin_dir, ignore_errors=True)
            os.replace(str(out), str(self.bin_dir))
        finally:
            if out.exists():
                shutil.rmtree(out, ignore_errors=True)

    # ---------------------------------------------------------------- config / server
    def write_config(self):
        st = self._load_state()
        self.auth_dir.mkdir(parents=True, exist_ok=True)
        proxy = system_proxy()
        q = json.dumps   # JSON 字符串即合法的 YAML 双引号字符串
        lines = [
            "# 由 Houdini Agent 生成，每次启动前重写；手动修改会被覆盖。",
            "config-version: 8",
            "server:",
            "  host: %s" % q("127.0.0.1"),
            "  port: %d" % int(st["port"]),
            "management:",
            "  secret-key: \"\"",
            "  disable-control-panel: true",
            "access:",
            "  api-keys:",
            "    - %s" % q(st["key"]),
            "oauth:",
            "  auth-dir: %s" % q(self.auth_dir.as_posix()),
            # 通常只有一个账号：上游偶发断流（empty_stream / 5xx）时 CPA 默认把它冷却 60 秒，
            # 期间所有请求本地直接 503 "no auth available"。关掉临时错误冷却，改为原地重试。
            # 配额（429）冷却保留——那是真的用完了。
            "routing:",
            "  retry:",
            "    request-retry: 2",
            "    max-retry-interval: 0",
            "  cooldown:",
            "    transient-error-cooldown-seconds: -1",
            "requests:",
            "  proxy-url: %s" % q(proxy),
            "  streaming:",
            "    bootstrap-retries: 2",       # 首字节前断开（empty_stream）自动重发
        ]
        self.config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self._save_state()

    def _subprocess_env(self):
        env = dict(os.environ)
        proxy = system_proxy()
        if proxy:
            env.setdefault("HTTPS_PROXY", proxy)
            env.setdefault("HTTP_PROXY", proxy)
        return env

    def healthy(self, timeout=1.5) -> bool:
        try:
            r = _requests().get(self.base_url() + "/models", timeout=timeout,
                                headers={"Authorization": "Bearer " + self.client_key()},
                                proxies={"http": None, "https": None})
            return r.status_code == 200
        except Exception:
            return False

    def _fallback_exe(self):
        """离线兜底：若目标 CPA_VERSION 尚未下载，查找历史已安装的任意版本可执行文件。"""
        bin_root = self.base / "bin"
        if not bin_root.is_dir():
            return None
        name = self.exe.name
        cands = sorted(bin_root.glob("*/" + name), reverse=True)
        for p in cands:
            if p.is_file():
                return p
        return None

    def ensure_running(self, timeout=20.0) -> bool:
        """服务没在跑就拉起来并等到可用。CPA 升级后自动下载新版并切换，不再停留在旧版静态目录。"""
        with self._lock:
            st = self._load_state()
            target_exe = self.exe
            if not self.installed():
                # 已登录用户升级到新版 Houdini Agent 时自动下载新版 CPA；离线则回退旧版二进制
                if any(self.logged_in(k) for k in KINDS.values()):
                    try:
                        self.install()
                    except Exception:
                        fb = self._fallback_exe()
                        if fb is not None:
                            target_exe = fb
                        else:
                            raise
                else:
                    raise CPAError("订阅组件尚未安装，请先在订阅供应商里登录")
            using_ver = target_exe.parent.name
            if self.healthy() and st.get("cpa_version") == using_ver:
                return True
            # 若旧版 CPA 还在运行：先停掉自己拉起的；若外部遗留旧实例占着端口，顺延换空闲端口启动新版
            if self._proc is not None and self._proc.poll() is None:
                self.stop()
            if _port_open(st["port"]):
                # 端口被别的程序（或旧版本遗留 CPA）占着：换一个空闲端口
                st["port"] = _free_port()
            st["cpa_version"] = using_ver
            self.write_config()
            log = open(self.base / "cpa.log", "ab")
            try:
                # 不传 -local-model：允许 CPA 启动后在后台自动拉取最新 models.json（离线自动回退内置目录）
                self._proc = subprocess.Popen(
                    [str(target_exe), "-config", str(self.config_path)],
                    cwd=str(self.base), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    env=self._subprocess_env(), creationflags=_NO_WINDOW)
            finally:
                log.close()
            t_end = time.time() + timeout
            while time.time() < t_end:
                if self._proc.poll() is not None:
                    raise CPAError("订阅服务启动失败（退出码 %s），详见 %s" % (self._proc.returncode, self.base / "cpa.log"))
                if self.healthy(timeout=1.0):
                    return True
                time.sleep(0.3)
            raise CPAError("订阅服务启动超时")

    def stop(self):
        with self._lock:
            p, self._proc = self._proc, None
        if p is not None and p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=3)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    # ---------------------------------------------------------------- accounts
    def accounts(self, kind="codex") -> list:
        """已登录的账号（kind = codex / claude）：[{file, email, plan}]，不含任何 token。"""
        out = []
        if not self.auth_dir.is_dir():
            return out
        for f in sorted(self.auth_dir.glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(d, dict):
                continue
            ftype = str(d.get("type", "")).lower()
            if ftype != kind and not (not ftype and f.name.lower().startswith(kind)):
                continue
            claims = _decode_jwt_claims(d.get("id_token", ""))
            auth = claims.get("https://api.openai.com/auth") or {}
            out.append({
                "file": f.name,
                "email": d.get("email") or claims.get("email") or "",
                "plan": auth.get("chatgpt_plan_type") or d.get("plan_type") or d.get("subscription_type") or "",
            })
        return out

    def logged_in(self, kind="codex") -> bool:
        return bool(self.accounts(kind))

    def logout(self, file_name=None, kind="codex") -> int:
        n = 0
        for acc in self.accounts(kind):
            if file_name and acc["file"] != file_name:
                continue
            try:
                (self.auth_dir / acc["file"]).unlink()
                n += 1
            except Exception:
                pass
        return n

    # ---------------------------------------------------------------- models
    def _models_key(self, kind):
        return "models" if kind == "codex" else "models_" + kind     # codex 沿用旧键，兼容已有缓存

    def cached_models(self, kind="codex") -> list:
        st = self._load_state()
        raw = st.get(self._models_key(kind)) or []
        if kind == "antigravity":
            # antigravity 渠道自带 claude-sonnet-4-6 等同名模型，缓存已按 owned_by 隔离，这里只做聊天模型过滤
            models = [m for m in raw if _is_chat_model(m)]
        else:
            models = [m for m in raw if _is_chat_model(m) and _model_kind(m) == kind]
        return models or default_models(kind)

    def cached_image_models(self, kind="codex") -> list:
        """已缓存的订阅生图模型（Codex 渠道自动从 /v1/models 同步；无缓存时回退兜底表）。"""
        if kind != "codex":
            return []
        st = self._load_state()
        models = [m for m in (st.get("image_models") or [])
                  if is_image_model(m) and _model_kind(m) == "codex"]
        return _sort_image_models(models) if models else list(CODEX_IMAGE_MODELS)

    def image_models_stale(self, max_age=12 * 3600) -> bool:
        """生图模型缓存尚未同步或已超过 max_age 秒。"""
        st = self._load_state()
        if not st.get("image_models"):
            return True
        ts = st.get("image_models_updated_at")
        if not isinstance(ts, (int, float)):
            return True
        return (time.time() - float(ts)) >= max_age

    def models_stale(self, kind="codex", max_age=6 * 3600) -> bool:
        """该订阅渠道的模型缓存尚未刷新、超过 max_age 秒，或 CPA 版本已升级。"""
        st = self._load_state()
        if st.get("cpa_version") != CPA_VERSION:
            return True
        if not st.get(self._models_key(kind)):
            return True
        ts = st.get("models_updated_at_" + kind)
        if not isinstance(ts, (int, float)):
            return True
        return (time.time() - float(ts)) >= max_age

    def refresh_models(self, kind="codex") -> list:
        """从本机服务拉取模型（需已登录且服务在跑），按 owned_by 拆分更新各订阅的对话与生图模型缓存。"""
        r = _requests().get(self.base_url() + "/models", timeout=5,
                            headers={"Authorization": "Bearer " + self.client_key()},
                            proxies={"http": None, "https": None})
        r.raise_for_status()
        items = [m for m in (r.json().get("data") or []) if isinstance(m, dict) and m.get("id")]
        ids = [
            m["id"] for m in items
            if _is_chat_model(m["id"]) and _model_kind(m["id"], m.get("owned_by", "")) == kind
        ]
        # 去重保序
        ids = list(dict.fromkeys(ids))
        prefer = default_models(kind)
        extras = sorted((i for i in ids if i not in prefer), key=_version_desc_key)
        top_key = min((_version_desc_key(m)[:3] for m in prefer), default=(0, 0, 0))
        newer = [i for i in extras if _version_desc_key(i)[:3] < top_key]
        older = [i for i in extras if _version_desc_key(i)[:3] >= top_key]
        # 未来刚发布、高于静态表版本的新模型自动置顶；已推荐模型保序居中；其余按版本号降序排后
        ordered = newer + [m for m in prefer if m in ids] + older
        st = self._load_state()
        changed = False
        if ordered:
            st[self._models_key(kind)] = ordered
            st["models_updated_at_" + kind] = time.time()
            changed = True
        if kind == "codex":
            img_ids = _sort_image_models(
                m["id"] for m in items
                if is_image_model(m["id"]) and _model_kind(m["id"], m.get("owned_by", "")) == "codex"
            )
            if img_ids:
                st["image_models"] = img_ids
                st["image_models_updated_at"] = time.time()
                changed = True
        if changed:
            self._save_state()
        return ordered or self.cached_models(kind)

    def refresh_image_models(self, kind="codex") -> list:
        """刷新并返回订阅生图模型列表。"""
        self.refresh_models(kind)
        return self.cached_image_models(kind)

    # ---------------------------------------------------------------- image generation
    def _load_image_part(self, ref, index=0):
        """把本地路径 / data URI / http(s) URL 转为 multipart 文件元组 (filename, bytes, mime)。"""
        s = str(ref or "").strip()
        if not s:
            raise CPAError("参考图为空")
        if s.startswith("data:"):
            m = re.match(r"^data:([^;,]+)?;base64,(.+)$", s, re.S)
            if not m:
                raise CPAError("无法解析参考图 data URI")
            mime = (m.group(1) or "image/png").strip().lower()
            raw = base64.b64decode(m.group(2))
            ext = {"image/jpeg": "jpg", "image/jpg": "jpg",
                   "image/webp": "webp", "image/png": "png"}.get(mime, "png")
            return ("ref_%d.%s" % (index, ext), raw, mime)
        if s.lower().startswith(("http://", "https://")):
            resp = _requests().get(s, timeout=30)
            resp.raise_for_status()
            raw = resp.content
            mime = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
            if not mime.startswith("image/"):
                mime = _detect_image_mime(raw)
            ext = {"image/jpeg": "jpg", "image/webp": "webp"}.get(mime, "png")
            return ("ref_%d.%s" % (index, ext), raw, mime)
        if os.path.isfile(s):
            raw = Path(s).read_bytes()
            ext = os.path.splitext(s)[1].lower().lstrip(".")
            mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                    "webp": "image/webp", "png": "image/png"}.get(ext) or _detect_image_mime(raw)
            fname = os.path.basename(s) or ("ref_%d.png" % index)
            return (fname, raw, mime)
        raise CPAError("无法读取参考图：%s" % s)

    def generate_image(self, prompt, model="gpt-image-2.5", aspect_ratio="1:1",
                       reference_images=None, timeout=180, should_stop=None) -> dict:
        """通过本机 CPA 调 Codex /v1/images/generations（文生图）或 /v1/images/edits（图生图）。
        返回 {"bytes": bytes, "ext": ".png", "revised_prompt": str, "model": str}。"""
        if not self.logged_in("codex"):
            raise CPAError("尚未登录 ChatGPT 订阅，请在「设置 → 模型与 Provider → ChatGPT 订阅」登录后使用 Codex 生图")
        if should_stop and should_stop():
            raise CPAError("已取消")
        self.ensure_running()
        if should_stop and should_stop():
            raise CPAError("已取消")

        prompt = str(prompt or "").strip()
        if not prompt:
            raise CPAError("提示词不能为空")
        model = str(model or "").strip() or (self.cached_image_models("codex") or CODEX_IMAGE_MODELS)[0]
        size = aspect_ratio_to_size(aspect_ratio)
        requests = _requests()
        headers = {"Authorization": "Bearer " + self.client_key()}
        proxies = {"http": None, "https": None}

        refs = [r for r in (reference_images or []) if r]
        if refs:
            parts = [self._load_image_part(r, i) for i, r in enumerate(refs[:5])]
            field = "image[]" if len(parts) > 1 else "image"
            files = [(field, p) for p in parts]
            data = {"model": model, "prompt": prompt, "size": size}
            resp = requests.post(self.images_edits_url(), headers=headers,
                                 data=data, files=files, proxies=proxies, timeout=timeout)
        else:
            payload = {"model": model, "prompt": prompt, "size": size}
            resp = requests.post(self.images_generations_url(),
                                 headers=dict(headers, **{"Content-Type": "application/json"}),
                                 json=payload, proxies=proxies, timeout=timeout)

        if resp.status_code < 200 or resp.status_code >= 300:
            raise CPAError(_friendly_image_error(resp.status_code, resp.text))
        try:
            body = resp.json()
        except Exception as e:
            raise CPAError("Codex 生图返回了非 JSON 响应：%s" % e)
        img_bytes, ext, revised = _extract_image_from_response(body, requests)
        return {"bytes": img_bytes, "ext": ext, "revised_prompt": revised, "model": model}

    # ---------------------------------------------------------------- login
    def login(self, device=False, on_event=None, kind="codex"):
        """阻塞执行一次登录（调用方放后台线程）。kind = codex / claude / antigravity（仅 Codex 有设备码登录）。
        on_event(event, data)：event = "url"（浏览器登录链接）/ "device"（{"url","code"}）。
        返回 {"ok": bool, "code": ..., "message": str}。"""
        if not self.installed():
            raise CPAError("订阅组件尚未安装")
        self.write_config()
        before = {a["file"] for a in self.accounts(kind)}
        before_mtime = {f.name: f.stat().st_mtime for f in self.auth_dir.glob("*.json")}
        if kind == "claude":
            flags = ["-claude-login", "-no-browser"]
            callback_port = "54545"
        elif kind == "antigravity":
            flags = ["-antigravity-login", "-no-browser"]
            callback_port = "51121"
        else:
            flags = ["-codex-device-login", "-no-browser"] if device else ["-codex-login", "-no-browser"]
            callback_port = "1455"
        # 用 PIPE 保持 stdin 打开：Antigravity 等 OAuth 流程 15 秒后会异步提示粘贴回调 URL，
        # 若传 DEVNULL 会立刻读到 EOF 导致登录被 15 秒中断！
        proc = subprocess.Popen(
            [str(self.exe), "-config", str(self.config_path)] + flags,
            cwd=str(self.base), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.PIPE,
            env=self._subprocess_env(), creationflags=_NO_WINDOW)
        with self._lock:
            self._login = {"proc": proc, "cancelled": False}
        tail = []
        dev = {}

        def emit(kind, data):
            if on_event:
                try:
                    on_event(kind, data)
                except Exception:
                    pass

        def reader():
            for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                tail.append(line)
                del tail[:-20]
                # 授权链接：Codex 在 auth.openai.com，Claude 在 claude.ai，Google 反重力在 accounts.google.com/o/oauth2/
                if line.startswith("https://") and (
                    "authorize" in line or "/oauth2/" in line or "accounts.google.com" in line
                ):
                    emit("url", line)
                m = re.search(r"Codex device URL:\s*(\S+)", line)
                if m:
                    dev["url"] = m.group(1)
                m = re.search(r"Codex device code:\s*(\S+)", line)
                if m:
                    dev["code"] = m.group(1)
                    emit("device", {"url": dev.get("url", "https://auth.openai.com/codex/device"),
                                    "code": dev["code"]})

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        try:
            proc.wait(timeout=_LOGIN_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            return {"ok": False, "code": "timeout", "message": "登录超时（15 分钟内未完成授权）"}
        finally:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except Exception:
                pass
            t.join(timeout=2)
            with self._lock:
                cancelled = bool(self._login and self._login.get("cancelled"))
                self._login = None
        if cancelled:
            return {"ok": False, "code": "cancelled", "message": "已取消登录"}
        after = self.accounts(kind)
        changed = [a for a in after if a["file"] not in before or
                   (self.auth_dir / a["file"]).stat().st_mtime != before_mtime.get(a["file"])]
        if changed:
            a = changed[0]
            who = a["email"] or a["file"]
            return {"ok": True, "code": "ok", "who": who, "plan": a["plan"],
                    "message": "已登录 %s%s" % (who, ("（%s）" % a["plan"]) if a["plan"] else "")}
        text = "\n".join(tail[-6:])
        if re.search(callback_port + r"|address already in use|bind", text, re.I):
            return {"ok": False, "code": "port", "port": callback_port,
                    "message": "本机 %s 端口被占用，请关闭占用它的程序后重试" % callback_port}
        detail = "exit %s%s" % (proc.returncode, (": " + tail[-1]) if tail else "")
        return {"ok": False, "code": "failed", "detail": detail, "message": "登录没有完成（%s）" % detail}

    def cancel_login(self):
        with self._lock:
            s = self._login
            if not s:
                return False
            s["cancelled"] = True
            p = s["proc"]
        try:
            p.kill()
        except Exception:
            pass
        return True

    def login_in_progress(self) -> bool:
        with self._lock:
            return self._login is not None


def _detect_image_mime(raw: bytes) -> str:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def _ext_from_format(fmt: str, raw: bytes) -> str:
    low = str(fmt or "").strip().lower().lstrip(".")
    if low in ("jpg", "jpeg"):
        return ".jpg"
    if low == "webp":
        return ".webp"
    if low == "png":
        return ".png"
    mime = _detect_image_mime(raw)
    return {"image/jpeg": ".jpg", "image/webp": ".webp"}.get(mime, ".png")


def _extract_image_from_response(body: dict, requests_mod):
    items = (body or {}).get("data") if isinstance(body, dict) else None
    if not items or not isinstance(items, list):
        raise CPAError("Codex 生图未返回图片数据")
    first = items[0] or {}
    revised = str(first.get("revised_prompt") or "")
    out_fmt = str((body or {}).get("output_format") or "")
    b64 = first.get("b64_json")
    if b64:
        raw = base64.b64decode(b64)
        return raw, _ext_from_format(out_fmt, raw), revised
    url = str(first.get("url") or "").strip()
    if url.startswith("data:"):
        m = re.match(r"^data:([^;,]+)?;base64,(.+)$", url, re.S)
        if not m:
            raise CPAError("Codex 生图返回了无效的 data URI")
        raw = base64.b64decode(m.group(2))
        mime = (m.group(1) or "").lower()
        ext = {"image/jpeg": ".jpg", "image/webp": ".webp", "image/png": ".png"}.get(mime) or _ext_from_format(out_fmt, raw)
        return raw, ext, revised
    if url.lower().startswith(("http://", "https://")):
        r = requests_mod.get(url, timeout=60)
        r.raise_for_status()
        raw = r.content
        return raw, _ext_from_format(out_fmt, raw), revised
    raise CPAError("Codex 生图响应缺少 b64_json / url")


def _friendly_image_error(status_code: int, text: str) -> str:
    msg = ""
    try:
        j = json.loads(text or "")
        if isinstance(j, dict):
            err = j.get("error")
            if isinstance(err, dict):
                msg = str(err.get("message") or "")
            elif isinstance(err, str):
                msg = err
    except Exception:
        pass
    if not msg:
        msg = (text or "").strip()[:300]
    low = msg.lower()
    if "auth_unavailable" in low or "no auth available" in low or status_code in (401, 403):
        return "ChatGPT 订阅凭据不可用或已过期，请在「ChatGPT 订阅」里重新登录后重试（%s）" % (msg or ("HTTP %d" % status_code))
    if status_code == 429 or "rate limit" in low or "quota" in low:
        return "ChatGPT 订阅生图额度暂时受限，请稍后再试（%s）" % (msg or "HTTP 429")
    return "Codex 生图失败（HTTP %d）：%s" % (status_code, msg or "未知错误")


manager = CPAManager()
atexit.register(manager.stop)
