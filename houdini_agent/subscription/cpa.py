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

CPA_VERSION = "8.0.28"
_REPO = "router-for-me/CLIProxyAPI"

# 当前版本各平台发布包的 sha256（取自官方 release 的 checksums.txt），下载后逐一校验
_SHA256 = {
    "CLIProxyAPI_8.0.28_windows_amd64.zip": "c4a4466c23ae0493ea2db784334bfeb2ed19b00514b90241fd49e0d858a9f935",
    "CLIProxyAPI_8.0.28_windows_aarch64.zip": "9a117fd2fc2d726f53423de560122e545f3fe24f9a6a17afd2c52adaeb49aac0",
    "CLIProxyAPI_8.0.28_darwin_aarch64.tar.gz": "b70932382334e5388dc25265078cbcdf2e951b3b5fa7cd23d8ab3efb0a9fceef",
    "CLIProxyAPI_8.0.28_darwin_amd64.tar.gz": "619e6892b56832d964d989e5be4a23e2277b22e39c160de7ba3ca60d66b2c5fd",
    "CLIProxyAPI_8.0.28_linux_amd64.tar.gz": "1644171946913c858842081053abb6f67234bb41ce2691602bf834361293c17f",
    "CLIProxyAPI_8.0.28_linux_aarch64.tar.gz": "b1956286131e7295d5d0347dc1caff96d2135813b2af0fdb82c3b00119abfa3a",
}
# 下载来源：官网镜像（国内更稳）→ GitHub Release。sha256 固定，任一来源都安全。
_MIRROR_URL = "https://houdini-agent.com/download/cpa/{asset}"
_GITHUB_URL = "https://github.com/" + _REPO + "/releases/download/v{ver}/{asset}"

# Codex 订阅下可见的模型（CPA 内置 Codex 目录，visibility=list），按推荐顺序
CODEX_MODELS = ["gpt-6.1-sol", "gpt-6-astra", "gpt-6-sol", "gpt-6-luna",
                "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5"]
# Codex 渠道的上下文窗口与 API 直连不同（同名模型直连多为 1M），单独维护
CODEX_CONTEXT = {"gpt-6.1-sol": 500000}
CODEX_DEFAULT_CONTEXT = 272000

# 各模型支持的推理强度与默认档（CPA 内置 Codex 目录的 supported_reasoning_levels / default_reasoning_level）
_EFFORT_ORDER = ["low", "medium", "high", "xhigh", "max", "ultra"]
CODEX_EFFORTS = {
    "gpt-6.1-sol": _EFFORT_ORDER, "gpt-6-astra": _EFFORT_ORDER, "gpt-6-sol": _EFFORT_ORDER,
    "gpt-5.6-sol": _EFFORT_ORDER, "gpt-5.6-terra": _EFFORT_ORDER,
    "gpt-6-luna": _EFFORT_ORDER[:5], "gpt-5.6-luna": _EFFORT_ORDER[:5],
    "gpt-5.5": _EFFORT_ORDER[:4],
}
CODEX_DEFAULT_EFFORT = {"gpt-6.1-sol": "low", "gpt-5.6-sol": "low"}


def codex_efforts(model) -> list:
    """模型支持的推理强度（目录外的新模型保守给 low / medium / high）。"""
    return list(CODEX_EFFORTS.get(str(model or ""), _EFFORT_ORDER[:3]))


def codex_default_effort(model) -> str:
    return CODEX_DEFAULT_EFFORT.get(str(model or ""), "medium")
_HIDDEN_MODELS = {"gpt-reserve", "codex-auto-review"}


def _is_chat_model(model_id) -> bool:
    """CPA 的 /v1/models 还会列出 gpt-image-* 等图片生成模型，不能用来对话。"""
    m = str(model_id or "")
    return bool(m) and m not in _HIDDEN_MODELS and not m.startswith("gpt-image")

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
            "requests:",
            "  proxy-url: %s" % q(proxy),
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

    def ensure_running(self, timeout=20.0) -> bool:
        """服务没在跑就拉起来并等到可用。已有我们的实例在跑（含上次异常退出遗留的）直接复用。"""
        with self._lock:
            if self.healthy():
                return True
            if not self.installed():
                raise CPAError("订阅组件尚未安装，请先在「ChatGPT 订阅」里登录")
            st = self._load_state()
            if _port_open(st["port"]):
                # 端口被别的程序（或用户自己跑的 CPA）占着：换一个空闲端口
                st["port"] = _free_port()
            self.write_config()
            log = open(self.base / "cpa.log", "ab")
            try:
                self._proc = subprocess.Popen(
                    [str(self.exe), "-config", str(self.config_path), "-local-model"],
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
    def accounts(self) -> list:
        """已登录的 Codex 账号：[{file, email, plan}]，不含任何 token。"""
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
            if str(d.get("type", "")).lower() != "codex" and not f.name.lower().startswith("codex"):
                continue
            claims = _decode_jwt_claims(d.get("id_token", ""))
            auth = claims.get("https://api.openai.com/auth") or {}
            out.append({
                "file": f.name,
                "email": d.get("email") or claims.get("email") or "",
                "plan": auth.get("chatgpt_plan_type") or d.get("plan_type") or "",
            })
        return out

    def logged_in(self) -> bool:
        return bool(self.accounts())

    def logout(self, file_name=None) -> int:
        n = 0
        for acc in self.accounts():
            if file_name and acc["file"] != file_name:
                continue
            try:
                (self.auth_dir / acc["file"]).unlink()
                n += 1
            except Exception:
                pass
        return n

    # ---------------------------------------------------------------- models
    def cached_models(self) -> list:
        st = self._load_state()
        models = [m for m in (st.get("models") or []) if _is_chat_model(m)]
        return models or list(CODEX_MODELS)

    def refresh_models(self) -> list:
        """从本机服务拉取模型（需已登录且服务在跑），按推荐顺序排列并缓存。"""
        r = _requests().get(self.base_url() + "/models", timeout=5,
                            headers={"Authorization": "Bearer " + self.client_key()},
                            proxies={"http": None, "https": None})
        r.raise_for_status()
        ids = [m.get("id") for m in (r.json().get("data") or []) if m.get("id")]
        ids = [i for i in ids if _is_chat_model(i)]
        ordered = [m for m in CODEX_MODELS if m in ids] + sorted(i for i in ids if i not in CODEX_MODELS)
        if ordered:
            st = self._load_state()
            st["models"] = ordered
            self._save_state()
        return ordered or self.cached_models()

    # ---------------------------------------------------------------- login
    def login(self, device=False, on_event=None):
        """阻塞执行一次 Codex 登录（调用方放后台线程）。
        on_event(kind, data)：kind = "url"（浏览器登录链接）/ "device"（{"url","code"}）/ "log"。
        返回 {"ok": bool, "message": str}。"""
        if not self.installed():
            raise CPAError("订阅组件尚未安装")
        self.write_config()
        before = {a["file"] for a in self.accounts()}
        before_mtime = {f.name: f.stat().st_mtime for f in self.auth_dir.glob("*.json")}
        flags = ["-codex-device-login", "-no-browser"] if device else ["-codex-login", "-no-browser"]
        proc = subprocess.Popen(
            [str(self.exe), "-config", str(self.config_path)] + flags,
            cwd=str(self.base), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
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
                if line.startswith("https://auth.openai.com/") and "authorize" in line:
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
            t.join(timeout=2)
            with self._lock:
                cancelled = bool(self._login and self._login.get("cancelled"))
                self._login = None
        if cancelled:
            return {"ok": False, "code": "cancelled", "message": "已取消登录"}
        after = self.accounts()
        changed = [a for a in after if a["file"] not in before or
                   (self.auth_dir / a["file"]).stat().st_mtime != before_mtime.get(a["file"])]
        if changed:
            a = changed[0]
            who = a["email"] or a["file"]
            return {"ok": True, "code": "ok", "who": who, "plan": a["plan"],
                    "message": "已登录 %s%s" % (who, ("（%s）" % a["plan"]) if a["plan"] else "")}
        text = "\n".join(tail[-6:])
        if re.search(r"1455|address already in use|bind", text, re.I):
            return {"ok": False, "code": "port", "message": "本机 1455 端口被占用（可能有 Codex 正在登录），请关闭后重试，或改用设备码登录"}
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


manager = CPAManager()
atexit.register(manager.stop)
