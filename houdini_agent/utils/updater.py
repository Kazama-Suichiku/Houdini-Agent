# -*- coding: utf-8 -*-
"""
Houdini Agent - 自动更新模块

检查新版本：优先读官网的版本清单 latest.json（国内可访问、无 API 限流，且与官网
安装包同步发布），失败再退回 GitHub Releases API。打包版下载新安装包并校验
大小 / sha256 后静默安装；源码模式（Houdini 内旧界面）下载源码覆盖。

线程安全：check / download / apply 均可在后台线程调用，
UI 回调通过 Qt Signal 回到主线程。
"""

import base64
import os
import sys
import json
import time
import hashlib
import shutil
import threading
import zipfile
import tempfile
from pathlib import Path
from typing import Optional, Tuple

# ---------- 常量 ----------

GITHUB_OWNER = "Kazama-Suichiku"
GITHUB_REPO = "Houdini-Agent"

# GitHub API 端点 — 基于 Release（而非 branch）。未登录每 IP 每小时仅 60 次，
# 国内共用出口 / 代理下很容易被限流或超时，所以只作备用来源。
_API_LATEST_RELEASE = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"

# 官网版本清单（主来源）：发版时由 deploy/publish_manifest.py 在安装包上传之后写入，
# 内容含版本号、说明首行、安装包直链及其 size / sha256。
_MANIFEST_URL = "https://houdini-agent.com/download/latest.json"

# 项目根目录（VERSION 文件所在目录）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_VERSION_FILE = _PROJECT_ROOT / "VERSION"


def _version_candidates():
    """可能放置 VERSION 的位置（兼容源码运行与 PyInstaller 打包）。
    打包后 __file__ 推导未必可靠，需优先用 _MEIPASS / 可执行文件目录兜底。"""
    roots = []
    mei = getattr(sys, "_MEIPASS", None)        # PyInstaller：VERSION 打到 _internal 根
    if mei:
        roots.append(Path(mei))
    roots.append(_PROJECT_ROOT)                  # 源码：<repo>/VERSION
    try:                                         # 独立程序：exe 同级 / exe 下 _internal
        exe_dir = Path(sys.executable).resolve().parent
        roots.append(exe_dir)
        roots.append(exe_dir / "_internal")
    except Exception:
        pass
    return roots

def _user_state_dir() -> Path:
    """用户级可写目录。打包版装在 Program Files 时安装目录不可写，缓存必须放这里。"""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "HoudiniAgent"


# 更新检查缓存（GitHub ETag + release 数据 + 官网清单）。以前放在安装目录的 cache/ 下，
# 装在 Program Files 时写不进去，缓存永远为空。
_ETAG_CACHE_FILE = _user_state_dir() / "update_cache.json"

# 更新时需要保留（不覆盖）的路径
_PRESERVE_PATHS = frozenset({
    "config",           # 用户 API key 等配置
    "cache",            # 对话缓存、文档索引
    "trainData",        # 训练数据
    ".git",             # git 仓库
})


# ==========================================================
# 版本工具
# ==========================================================

def get_local_version() -> str:
    """读取本地 VERSION 文件，返回版本字符串，失败返回 '0.0.0'。
    依次尝试多个候选位置，兼容源码运行与打包（避免打包后误报 0.0.0）。"""
    for root in _version_candidates():
        try:
            f = root / "VERSION"
            if f.is_file():
                txt = f.read_text(encoding="utf-8").strip()
                if txt:
                    return txt
        except Exception:
            continue
    return "0.0.0"


def _parse_version(v: str) -> Tuple[int, ...]:
    """把 '1.2.1' 解析为 (1, 2, 1) 用于比较"""
    parts = []
    for seg in v.strip().split("."):
        try:
            parts.append(int(seg))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _is_legacy_internal_version(v: str) -> bool:
    """检测旧的内部版本号（major >= 5，如 7.0.1, 6.8.3 等）
    
    项目历史中 v1.0.0 之前使用了内部版本号 v5.0~v7.0.1，
    这些数值大于正式 Release 版本号（1.x.x），会导致更新器
    误判本地版本更新，需要特殊处理强制更新。
    """
    parts = _parse_version(v)
    return len(parts) > 0 and parts[0] >= 5


def _version_gt(remote: str, local: str) -> bool:
    """remote > local ?
    
    特殊处理: 如果本地是旧内部版本号（major >= 5），
    而远程是正式 Release 版本号（major < 5），强制视为有更新。
    """
    local_parts = _parse_version(local)
    remote_parts = _parse_version(remote)
    
    # 旧内部版本号 → 正式版本号: 强制更新
    if _is_legacy_internal_version(local) and not _is_legacy_internal_version(remote):
        return True
    
    return remote_parts > local_parts


# ==========================================================
# ETag 缓存
# ==========================================================

def _load_etag_cache() -> dict:
    """加载 ETag 缓存（包含上次的 ETag 和 release 数据）"""
    try:
        if _ETAG_CACHE_FILE.exists():
            with open(_ETAG_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_etag_cache(data: dict):
    """保存 ETag 缓存"""
    try:
        _ETAG_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_ETAG_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ==========================================================
# 检查更新
# ==========================================================

# 模块级缓存：最新版源码包地址（check_update 写入，download_and_apply 读取）
_cached_zipball_url: str = ""


def _requests():
    try:
        import requests  # type: ignore
    except ImportError:
        lib_dir = str(_PROJECT_ROOT / "lib")
        if lib_dir not in sys.path:
            sys.path.insert(0, lib_dir)
        import requests  # type: ignore
    return requests


def _first_line(text) -> str:
    """说明的首个非空行，去掉 Markdown 标题符号（旧对话框会原样显示 "## xxx"）。"""
    for line in str(text or "").splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line
    return ""


# 可选 Ed25519 清单验签公钥（Base64 编码的 32 字节 raw public key）。
# 留空时兼容未签名清单；一旦配置（或设环境变量 HA_UPDATE_PUBKEY_B64），
# 客户端会强制校验 latest.json 的 signature 字段，防 CDN/镜像篡改。
_MANIFEST_ED25519_PUBKEY_B64: str = ""


def _canonical_manifest_bytes(m: dict) -> bytes:
    """生成用于 Ed25519 签名/验签的规范化 JSON 字节串（剔除 signature 字段）。"""
    clean = {k: v for k, v in m.items() if k != "signature"}
    return json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign_manifest(manifest: dict, private_key_b64: str) -> str:
    """使用 Ed25519 私钥（Base64 32 字节 raw seed 或 PEM）为 manifest 生成签名。"""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    raw_in = private_key_b64.strip()
    if raw_in.startswith("-----BEGIN"):
        priv = load_pem_private_key(raw_in.encode("utf-8"), password=None)
    else:
        priv = Ed25519PrivateKey.from_private_bytes(base64.b64decode(raw_in))
    sig = priv.sign(_canonical_manifest_bytes(manifest))
    return base64.b64encode(sig).decode("ascii")


def verify_manifest_signature(manifest: dict, pubkey_b64: Optional[str] = None) -> bool:
    """校验 latest.json 的 Ed25519 签名。
    - 未配置公钥时返回 True（向后兼容未签名清单）；
    - 已配置公钥时必须存在合法 signature 且通过 Ed25519 验证。"""
    pk = (pubkey_b64 if pubkey_b64 is not None else (
        os.environ.get("HA_UPDATE_PUBKEY_B64") or _MANIFEST_ED25519_PUBKEY_B64
    )).strip()
    if not pk:
        return True
    sig_b64 = manifest.get("signature") if isinstance(manifest, dict) else None
    if not isinstance(sig_b64, str) or not sig_b64.strip():
        return False
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.hazmat.primitives.serialization import load_pem_public_key

        if pk.startswith("-----BEGIN"):
            pub = load_pem_public_key(pk.encode("utf-8"))
        else:
            pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(pk))
        pub.verify(base64.b64decode(sig_b64.strip()), _canonical_manifest_bytes(manifest))
        return True
    except Exception:
        return False


def _valid_manifest(m) -> bool:
    if not (isinstance(m, dict) and isinstance(m.get("version"), str) and m["version"].strip()
            and isinstance(m.get("installer"), dict) and m["installer"].get("url")):
        return False
    return verify_manifest_signature(m)


def _fetch_manifest(requests, timeout):
    """官网版本清单。返回 (manifest | None, 错误描述)。"""
    try:
        resp = requests.get(_MANIFEST_URL, timeout=timeout,
                            headers={"Cache-Control": "no-cache", "Accept": "application/json"})
        if resp.status_code != 200:
            return None, "官网版本清单返回 %s" % resp.status_code
        m = resp.json()
        if not _valid_manifest(m):
            return None, "官网版本清单格式异常"
        return m, ""
    except Exception as e:
        return None, "官网版本清单不可达（%s）" % type(e).__name__


def _fetch_github(requests, cache, timeout):
    """GitHub latest release。返回 (release_data | None, 错误描述)。
    304 = 确认未变化，返回缓存数据（视为最新结果）；403 / 超时等返回 None，交给上层判断。"""
    headers = {"Accept": "application/vnd.github.v3+json"}
    if cache.get("etag") and cache.get("release_data"):
        headers["If-None-Match"] = cache["etag"]
    try:
        resp = requests.get(_API_LATEST_RELEASE, headers=headers, timeout=timeout)
    except Exception as e:
        if "Timeout" in type(e).__name__:
            return None, "连接 GitHub 超时"
        return None, "连接 GitHub 失败（%s）" % type(e).__name__
    if resp.status_code == 304:
        return cache.get("release_data") or None, ""
    if resp.status_code == 403:
        return None, "GitHub API 限流 (403)"
    if resp.status_code == 404:
        return None, "暂无 Release 版本"
    if resp.status_code != 200:
        return None, "GitHub API 返回 %s" % resp.status_code
    data = resp.json()
    cache["etag"] = resp.headers.get("ETag", "")
    cache["release_data"] = data
    return data, ""


def _release_version(data) -> str:
    return str((data or {}).get("tag_name", "") or "").lstrip("vV")


def _fill_from_manifest(result, m):
    global _cached_zipball_url
    result["remote_version"] = m["version"].strip().lstrip("vV")
    result["release_name"] = m.get("name") or ("v" + result["remote_version"])
    result["release_notes"] = _first_line(m.get("notes", ""))
    result["source"] = "manifest"
    _cached_zipball_url = m.get("source_zip") or _cached_zipball_url


def _fill_from_release(result, data):
    global _cached_zipball_url
    result["remote_version"] = _release_version(data)
    result["release_name"] = data.get("name", "") or data.get("tag_name", "")
    result["release_notes"] = _first_line(data.get("body", ""))
    result["source"] = "github"
    _cached_zipball_url = data.get("zipball_url", "") or _cached_zipball_url


def check_update(timeout: float = 8.0) -> dict:
    """检查是否有新版本：官网清单 → GitHub API → 本地缓存。

    两个来源都失败时才用缓存：缓存里有更新的版本就照常提示（标记 stale）；
    否则如实报错，不再拿旧数据声称"已是最新版本"。

    Returns:
        {
            'has_update': bool,
            'local_version': str,
            'remote_version': str,
            'release_name': str,
            'release_notes': str,    # 说明首行（已去掉 Markdown 标题符号）
            'error': str,            # 出错信息（成功为 ''）
            'source': str,           # manifest | github | cache
            'stale': bool,           # True = 网络失败，结果来自缓存
        }
    """
    result = {
        'has_update': False,
        'local_version': get_local_version(),
        'remote_version': '',
        'release_name': '',
        'release_notes': '',
        'error': '',
        'source': '',
        'stale': False,
    }
    requests = _requests()
    cache = _load_etag_cache()
    errors = []

    m, err = _fetch_manifest(requests, timeout)
    if m:
        cache["manifest"] = m
        _save_etag_cache(cache)
        _fill_from_manifest(result, m)
    else:
        errors.append(err)
        data, err = _fetch_github(requests, cache, timeout)
        if data:
            _save_etag_cache(cache)
            _fill_from_release(result, data)
        else:
            errors.append(err)

    if not result["remote_version"]:
        # 两个来源都失败：退回缓存里版本更高的那份
        cand = []
        if _valid_manifest(cache.get("manifest")):
            cand.append(("m", cache["manifest"]["version"].strip().lstrip("vV")))
        if _release_version(cache.get("release_data")):
            cand.append(("g", _release_version(cache.get("release_data"))))
        cand.sort(key=lambda c: _parse_version(c[1]), reverse=True)
        if cand and _version_gt(cand[0][1], result["local_version"]):
            if cand[0][0] == "m":
                _fill_from_manifest(result, cache["manifest"])
            else:
                _fill_from_release(result, cache["release_data"])
            result["source"] = "cache"
            result["stale"] = True
        else:
            result["error"] = "无法连接更新服务器：%s。暂时无法确认是否有新版本，请稍后重试" % (
                "；".join(e for e in errors if e) or "未知错误")
            return result

    if not result["remote_version"]:
        result["error"] = "无法解析远程版本号"
        return result
    result["has_update"] = _version_gt(result["remote_version"], result["local_version"])
    return result


# ==========================================================
# 应用内更新（打包 exe）：下载安装包资产 → 拉起 Inno 静默覆盖安装
# ==========================================================

# 官网稳定直链（release 资产取不到时的兜底，始终指向最新版）
_STABLE_INSTALLER_URL = "https://houdini-agent.com/download/HoudiniAgent-Setup.exe"


def get_installer_url(release_data=None) -> str:
    """从 release 数据里取 Windows 安装包资产直链；取不到退回官网稳定链接。
    release_data=None 时读 check_update 留下的 ETag 缓存（含完整 release JSON）。"""
    data = release_data if release_data is not None else _load_etag_cache().get("release_data", {})
    for a in (data.get("assets") or []):
        name = str(a.get("name") or "")
        if name.startswith("HoudiniAgent-Setup") and name.endswith(".exe"):
            url = a.get("browser_download_url")
            if url:
                return str(url)
    return _STABLE_INSTALLER_URL


def installer_sources(cache=None) -> list:
    """按优先级列出可下载的安装包来源 [{'url','size','sha256','label'}]，只包含"最新已知版本"的来源：
    官网清单（带 sha256）→ GitHub 资产（带 size）→ 官网稳定链接（用清单的 sha256 校验，
    挡住稳定链接还没换成新版的情况）。"""
    cache = cache if cache is not None else _load_etag_cache()
    m = cache.get("manifest") if _valid_manifest(cache.get("manifest")) else None
    rel = cache.get("release_data") or {}
    mv = m["version"].strip().lstrip("vV") if m else ""
    rv = _release_version(rel)
    target = mv if _parse_version(mv or "0") >= _parse_version(rv or "0") else rv
    out = []
    if m and mv == target:
        inst = m["installer"]
        out.append({"url": inst["url"], "size": int(inst.get("size") or 0),
                    "sha256": str(inst.get("sha256") or "").lower(), "label": "官网"})
    if rv and rv == target:
        for a in (rel.get("assets") or []):
            name = str(a.get("name") or "")
            if name.startswith("HoudiniAgent-Setup") and name.endswith(".exe") and a.get("browser_download_url"):
                sha = ""
                if m and mv == rv:
                    sha = str(m["installer"].get("sha256") or "").lower()
                out.append({"url": str(a["browser_download_url"]), "size": int(a.get("size") or 0),
                            "sha256": sha, "label": "GitHub"})
                break
    if not any(o["url"] == _STABLE_INSTALLER_URL for o in out):
        sha = str(m["installer"].get("sha256") or "").lower() if (m and mv == target) else ""
        size = int(m["installer"].get("size") or 0) if (m and mv == target) else 0
        out.append({"url": _STABLE_INSTALLER_URL, "size": size, "sha256": sha, "label": "官网稳定链接"})
    return out


def _file_sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download_one(requests, src, target, _p, min_size=1024 * 1024, max_retries=3):
    """下载单个来源并校验，支持 HTTP Range 断点续传与瞬断重试。成功返回 ''，失败返回原因。"""
    exp_size = int(src.get("size") or 0)
    exp_sha = str(src.get("sha256") or "").lower()
    last_err = ""

    for attempt in range(max_retries):
        existing = 0
        try:
            if os.path.isfile(target):
                existing = os.path.getsize(target)
        except OSError:
            existing = 0

        # 本地残留分片已达到/超过预期大小：先校验，直接命中则免重下，损坏则清空重下
        if exp_size and existing >= exp_size:
            if existing == exp_size and (not exp_sha or _file_sha256(target) == exp_sha):
                _p(99)
                return ""
            try:
                os.remove(target)
            except OSError:
                pass
            existing = 0

        headers = {"Range": f"bytes={existing}-"} if existing > 0 else {}
        try:
            resp = requests.get(src["url"], stream=True, timeout=60, headers=headers)
            status = getattr(resp, "status_code", 200)
            if status == 416:
                # 服务端范围不合法（分片过期/已下完），清掉从头下
                try:
                    os.remove(target)
                except OSError:
                    pass
                existing = 0
                resp = requests.get(src["url"], stream=True, timeout=60)
                status = getattr(resp, "status_code", 200)
            resp.raise_for_status()

            resuming = (existing > 0 and status == 206)
            mode = "ab" if resuming else "wb"
            done = existing if resuming else 0
            clen = int(resp.headers.get("content-length", 0) or 0)
            total = (done + clen) if (resuming and clen > 0) else (clen or exp_size or 0)

            with open(target, mode) as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        done += len(chunk)
                        if total > 0:
                            _p(min(99, done * 100 / total))

            if exp_size and done != exp_size:
                last_err = "文件大小不符（%d / %d）" % (done, exp_size)
                # 网络提前截断时保留分片，下次携带 Range 头断点续传
                if attempt + 1 < max_retries and 0 < done < exp_size:
                    time.sleep(0.3 * (attempt + 1))
                    continue
                try:
                    os.remove(target)
                except OSError:
                    pass
                return last_err
            if done < min_size:
                last_err = "下载内容异常（%d 字节）" % done
                try:
                    os.remove(target)
                except OSError:
                    pass
                return last_err
            if exp_sha and _file_sha256(target) != exp_sha:
                try:
                    os.remove(target)
                except OSError:
                    pass
                return "校验值不符（可能下载到了旧版安装包）"
            return ""
        except Exception as e:
            last_err = str(e) or type(e).__name__
            if attempt + 1 < max_retries:
                time.sleep(0.6 * (attempt + 1))
                continue
            return last_err

    return last_err or "下载失败"


def download_installer(progress_callback=None, url=None) -> dict:
    """把新版安装包下载到 %TEMP% 并校验。progress_callback(percent: int)。
    url=None 时按 installer_sources() 依次尝试，前一个失败或校验不通过就换下一个。
    Returns: {'success': bool, 'path': str, 'error': str}"""
    requests = _requests()

    def _p(pct):
        if progress_callback:
            try:
                progress_callback(int(pct))
            except Exception:
                pass

    sources = [{"url": url, "size": 0, "sha256": "", "label": url}] if url else installer_sources()
    target = os.path.join(tempfile.gettempdir(), "HoudiniAgent-Setup-Update.exe")
    errors = []
    for src in sources:
        _p(0)
        try:
            err = _download_one(requests, src, target, _p)
        except Exception as e:
            err = str(e) or type(e).__name__
        if not err:
            _p(100)
            return {'success': True, 'path': target, 'error': ''}
        errors.append("%s：%s" % (src.get("label") or src["url"], err))
        try:
            os.remove(target)
        except Exception:
            pass
    return {'success': False, 'path': '', 'error': "；".join(errors) or "没有可用的下载地址"}


def _updates_dir() -> Path:
    return _user_state_dir() / "updates"


def _staged_meta_file() -> Path:
    return _updates_dir() / "staged.json"


def app_install_dir() -> Path:
    """返回当前应用安装根目录（打包版 = exe 所在目录；源码模式 = 仓库根）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return _PROJECT_ROOT


def is_dir_writable(path) -> bool:
    """探测目录当前权限是否可直接写入/替换文件（区分用户目录安装与 Program Files 只读安装）。"""
    p = Path(path)
    if not p.is_dir():
        return False
    probe = p / (".ha_write_probe_%d" % os.getpid())
    try:
        probe.write_bytes(b"ok")
        probe.unlink()
        return True
    except OSError:
        try:
            if probe.exists():
                probe.unlink()
        except OSError:
            pass
        return False


# 轻量化业务差异包（HoudiniAgent-Patch-<ver>.zip）覆盖范围：
# 仅打包频繁变动的 Python/QML/bridge_payload/VERSION（~1.5 MB），
# 其余 ~108 MB 的 PySide6/Python/exe 冻结运行时通过 RUNTIME_HASH 校验一致性。
PATCH_LAYER_ITEMS = (
    "_internal/VERSION",
    "_internal/RUNTIME_HASH",
    "_internal/houdini_agent",
    "_internal/bridge_payload",
    "_internal/shared",
    "_internal/rules",
    "_internal/plugins",
)

_PATCH_SKIP_PREFIXES = (
    "_internal/houdini_agent/",
    "_internal/bridge_payload/",
    "_internal/shared/",
    "_internal/rules/",
    "_internal/plugins/",
    "_internal/config/",
    "_update_bak/",
    "_update_new/",
)
_PATCH_SKIP_EXACT = frozenset({
    "_internal/VERSION",
    "_internal/RUNTIME_HASH",
})


def compute_runtime_hash(app_dir) -> str:
    """计算冻结运行时指纹（排除业务层与卸载/更新临时文件）。
    两个版本的 runtime_hash 相同，即可直接下 ~1.5 MB 差异包，免下 110 MB 全量包！"""
    root = Path(app_dir)
    if not root.is_dir():
        return ""
    entries = []
    for dp, dns, fns in os.walk(root):
        dns.sort()
        fns.sort()
        for fn in fns:
            full = Path(dp) / fn
            rel = full.relative_to(root).as_posix()
            if rel in _PATCH_SKIP_EXACT or any(rel.startswith(p) for p in _PATCH_SKIP_PREFIXES):
                continue
            low = fn.lower()
            if low.startswith("unins") and low.endswith((".exe", ".dat", ".msg")):
                continue
            if rel.startswith(("config/", "cache/", "trainData/", "plugins/", "rules/")):
                continue
            try:
                sz = full.stat().st_size
                sha = _file_sha256(full)
            except OSError:
                continue
            entries.append(f"{rel}|{sz}|{sha}")
    h = hashlib.sha256()
    for line in sorted(entries):
        h.update((line + "\n").encode("utf-8"))
    return h.hexdigest()


def get_local_runtime_hash() -> str:
    """读取本机已安装的运行时指纹（_internal/RUNTIME_HASH）。旧版无此文件时返回空串。"""
    p = app_install_dir() / "_internal" / "RUNTIME_HASH"
    try:
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def patch_zip_sources(cache=None) -> list:
    """列出可用的轻量化差异更新包（HoudiniAgent-Patch-<ver>.zip）来源（官网 + GitHub 镜像）。"""
    cache = cache if cache is not None else _load_etag_cache()
    m = cache.get("manifest") if _valid_manifest(cache.get("manifest")) else None
    rel = cache.get("release_data") or {}
    mv = m["version"].strip().lstrip("vV") if m else ""
    rv = _release_version(rel)
    target = mv if _parse_version(mv or "0") >= _parse_version(rv or "0") else rv
    out = []
    pz = (m or {}).get("patch_zip") if (m and mv == target) else None
    pz_sha = str(pz.get("sha256") or "").lower() if isinstance(pz, dict) else ""
    pz_rt = str(pz.get("runtime_hash") or "").strip().lower() if isinstance(pz, dict) else ""
    if isinstance(pz, dict) and pz.get("url") and pz_rt:
        out.append({
            "url": str(pz["url"]),
            "size": int(pz.get("size") or 0),
            "sha256": pz_sha,
            "runtime_hash": pz_rt,
            "version": target,
            "label": "官网增量补丁",
        })
    if rv and rv == target and pz_rt:
        expected_name = f"HoudiniAgent-Patch-{target}.zip"
        for a in (rel.get("assets") or []):
            if str(a.get("name") or "") == expected_name and a.get("browser_download_url"):
                out.append({
                    "url": str(a["browser_download_url"]),
                    "size": int(a.get("size") or 0),
                    "sha256": pz_sha,
                    "runtime_hash": pz_rt,
                    "version": target,
                    "label": "GitHub 增量补丁",
                })
                break
    return out


def app_zip_sources(cache=None) -> list:
    """列出可用的应用静默更新包（HoudiniAgent-App-<ver>.zip）来源（官网 + GitHub 镜像兜底）。"""
    cache = cache if cache is not None else _load_etag_cache()
    m = cache.get("manifest") if _valid_manifest(cache.get("manifest")) else None
    rel = cache.get("release_data") or {}
    mv = m["version"].strip().lstrip("vV") if m else ""
    rv = _release_version(rel)
    target = mv if _parse_version(mv or "0") >= _parse_version(rv or "0") else rv
    out = []
    az = (m or {}).get("app_zip") if (m and mv == target) else None
    az_sha = str(az.get("sha256") or "").lower() if isinstance(az, dict) else ""
    if isinstance(az, dict) and az.get("url"):
        out.append({
            "url": str(az["url"]),
            "size": int(az.get("size") or 0),
            "sha256": az_sha,
            "version": target,
            "label": "官网更新包",
        })
    if rv and rv == target:
        expected_name = f"HoudiniAgent-App-{target}.zip"
        for a in (rel.get("assets") or []):
            if str(a.get("name") or "") == expected_name and a.get("browser_download_url"):
                out.append({
                    "url": str(a["browser_download_url"]),
                    "size": int(a.get("size") or 0),
                    "sha256": az_sha,
                    "version": target,
                    "label": "GitHub 更新包",
                })
                break
    return out


def _apply_lock_file() -> Path:
    return _updates_dir() / "applying.lock"


def _pending_verify_file() -> Path:
    return _updates_dir() / "pending_verify.json"


def _rollback_dir() -> Path:
    return _updates_dir() / "rollback"


def _cleanup_old_staged(keep_ver=None):
    """清理 updates/staged 下不再需要的旧版本暂存文件。"""
    sdir = _updates_dir() / "staged"
    if not sdir.is_dir():
        return
    keep_names = set()
    if keep_ver:
        keep_names.add(str(keep_ver))
        keep_names.add(f"{keep_ver}-patch")
        keep_names.add("HoudiniAgent-Setup-%s.exe" % keep_ver)
    for item in sdir.iterdir():
        if item.name in keep_names:
            continue
        try:
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                item.unlink()
        except OSError:
            pass


def clear_staged_update():
    """清除已暂存的更新元数据与文件。"""
    try:
        mf = _staged_meta_file()
        if mf.is_file():
            mf.unlink()
    except OSError:
        pass
    _cleanup_old_staged(keep_ver=None)


def _read_staged_version_file(staged_dir: Path) -> str:
    for rel in ("_internal/VERSION", "VERSION"):
        f = staged_dir / rel
        if f.is_file():
            try:
                return f.read_text(encoding="utf-8").strip()
            except Exception:
                pass
    return ""


def get_staged_update():
    """读取并校验已后台下载就绪的暂存更新。
    若本机版本已追平或超过暂存版本，自动清理暂存并返回 None。"""
    mf = _staged_meta_file()
    if not mf.is_file():
        return None
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    ver = str(data.get("version") or "").strip().lstrip("vV")
    if not ver or not _version_gt(ver, get_local_version()):
        clear_staged_update()
        return None
    mode = str(data.get("mode") or "")
    if mode == "patch":
        sd = Path(str(data.get("staged_dir") or ""))
        if not sd.is_dir():
            return None
        sver = _read_staged_version_file(sd)
        if not sver or sver != ver:
            return None
        req_rt = str(data.get("runtime_hash") or "").strip().lower()
        cur_rt = get_local_runtime_hash().lower()
        if req_rt and cur_rt != req_rt:
            return None
        return data
    if mode == "swap":
        sd = Path(str(data.get("staged_dir") or ""))
        exe = sd / "Houdini Agent.exe"
        if not (sd.is_dir() and exe.is_file()):
            return None
        sver = _read_staged_version_file(sd)
        if sver and sver != ver:
            return None
        return data
    if mode == "installer":
        ip = Path(str(data.get("path") or ""))
        if not ip.is_file():
            return None
        exp_sz = int(data.get("size") or 0)
        if exp_sz and ip.stat().st_size != exp_sz:
            return None
        return data
    return None


# 进程级单飞下载锁：后台 silentUpdateCheck 正在下载时，用户手动点「立即更新」
# 会直接挂载到同一个下载任务接收进度并共享结果，彻底杜绝双线程写同一文件踩踏。
_STAGE_LOCK = threading.Lock()
_INFLIGHT_STAGE: Optional[dict] = None


def stage_update(progress_callback=None, cache=None) -> dict:
    """后台静默下载并预解压/校验新版更新包（单飞去重），供重启或关闭应用时秒级切换。
    优先级：
    1. mode='patch'：安装目录可写 + 本机 RUNTIME_HASH 与清单 patch_zip.runtime_hash 一致
       → 仅下载 ~1.5 MB 业务层增量补丁（秒下、抗 Houdini 文件锁、免 UAC）；
    2. mode='swap'：安装目录可写 + 有 app_zip（官网或 GitHub 镜像）
       → 下载全量应用包并预解压，支持跨盘先预拷贝再同盘原子替换；
    3. mode='installer'：只读目录或上述均不可用 → 预下载校验好的 .exe 安装包。"""
    global _INFLIGHT_STAGE

    with _STAGE_LOCK:
        if _INFLIGHT_STAGE is not None:
            flight = _INFLIGHT_STAGE
            if progress_callback:
                flight["callbacks"].append(progress_callback)
                try:
                    progress_callback(int(flight["last_pct"]))
                except Exception:
                    pass
            wait_event = flight["event"]
            is_owner = False
        else:
            flight = {
                "event": threading.Event(),
                "callbacks": [progress_callback] if progress_callback else [],
                "last_pct": 0,
                "result": None,
            }
            _INFLIGHT_STAGE = flight
            wait_event = flight["event"]
            is_owner = True

    if not is_owner:
        wait_event.wait(timeout=600)
        return dict(flight["result"] or {"success": False, "cached": False, "staged": None, "error": "等待后台下载超时"})

    def _p(pct):
        val = int(pct)
        with _STAGE_LOCK:
            flight["last_pct"] = val
            cbs = list(flight["callbacks"])
        for cb in cbs:
            try:
                cb(val)
            except Exception:
                pass

    try:
        res = _stage_update_impl(_p, cache=cache)
    except Exception as e:
        res = {"success": False, "cached": False, "staged": None, "error": str(e)}
    finally:
        with _STAGE_LOCK:
            flight["result"] = res
            flight["event"].set()
            if _INFLIGHT_STAGE is flight:
                _INFLIGHT_STAGE = None
    return res


def _stage_update_impl(_p, cache=None) -> dict:
    cache = cache if cache is not None else _load_etag_cache()
    m = cache.get("manifest") if _valid_manifest(cache.get("manifest")) else None
    rel = cache.get("release_data") or {}
    mv = m["version"].strip().lstrip("vV") if m else ""
    rv = _release_version(rel)
    target_ver = mv if _parse_version(mv or "0") >= _parse_version(rv or "0") else rv
    if not target_ver or not _version_gt(target_ver, get_local_version()):
        clear_staged_update()
        return {"success": False, "up_to_date": True, "staged": None, "error": ""}

    existing = get_staged_update()
    if existing and existing.get("version") == target_ver:
        _p(100)
        return {"success": True, "cached": True, "staged": existing, "error": ""}

    requests = _requests()
    notes = _first_line((m or {}).get("notes", "") or (rel or {}).get("body", ""))
    rel_name = (m or {}).get("name") or (rel or {}).get("name") or ("v" + target_ver)
    writable = is_dir_writable(app_install_dir())
    staged_root = _updates_dir() / "staged"
    staged_root.mkdir(parents=True, exist_ok=True)
    tmp_dir = _updates_dir() / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # 1) 最快路径：轻量化增量补丁包（~1.5 MB），仅当本机 RUNTIME_HASH 与清单一致且目录可写
    local_rt = get_local_runtime_hash().lower() if writable else ""
    if writable and local_rt:
        patches = [p for p in patch_zip_sources(cache)
                   if p.get("version") == target_ver and p.get("runtime_hash") == local_rt]
        for src in patches:
            zip_path = tmp_dir / ("HoudiniAgent-Patch-%s.zip.part" % target_ver)
            try:
                err = _download_one(requests, src, str(zip_path), lambda pct: _p(int(pct * 0.85)), min_size=128)
                if not err:
                    extract_tmp = staged_root / ("%s-patch.tmp" % target_ver)
                    final_dir = staged_root / ("%s-patch" % target_ver)
                    if extract_tmp.exists():
                        shutil.rmtree(extract_tmp, ignore_errors=True)
                    with zipfile.ZipFile(str(zip_path), "r") as zf:
                        zf.extractall(str(extract_tmp))
                    if _read_staged_version_file(extract_tmp) == target_ver:
                        if final_dir.exists():
                            shutil.rmtree(final_dir, ignore_errors=True)
                        os.replace(str(extract_tmp), str(final_dir))
                        meta = {
                            "version": target_ver,
                            "name": rel_name,
                            "notes": notes,
                            "mode": "patch",
                            "staged_dir": str(final_dir),
                            "runtime_hash": local_rt,
                            "sha256": src.get("sha256") or "",
                            "writable": True,
                        }
                        _staged_meta_file().write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
                        _cleanup_old_staged(keep_ver=target_ver)
                        _p(100)
                        return {"success": True, "cached": False, "staged": meta, "error": ""}
                    shutil.rmtree(extract_tmp, ignore_errors=True)
            except Exception:
                pass
            finally:
                try:
                    if zip_path.exists():
                        zip_path.unlink()
                except OSError:
                    pass

    # 2) 全量静默原子替换（支持官网 + GitHub 镜像兜底，带 HTTP Range 断点续传）
    zips = [z for z in app_zip_sources(cache) if z.get("version") == target_ver] if writable else []
    for src in zips:
        zip_path = tmp_dir / ("HoudiniAgent-App-%s.zip.part" % target_ver)
        try:
            err = _download_one(requests, src, str(zip_path), lambda pct: _p(int(pct * 0.85)))
            if not err:
                extract_tmp = staged_root / ("%s.tmp" % target_ver)
                final_dir = staged_root / target_ver
                if extract_tmp.exists():
                    shutil.rmtree(extract_tmp, ignore_errors=True)
                with zipfile.ZipFile(str(zip_path), "r") as zf:
                    zf.extractall(str(extract_tmp))
                # 兼容 zip 顶层带一层 "Houdini Agent/" 目录或直接平铺文件
                cand = extract_tmp
                if not (cand / "Houdini Agent.exe").is_file():
                    sub = extract_tmp / "Houdini Agent"
                    if (sub / "Houdini Agent.exe").is_file():
                        cand = sub
                if (cand / "Houdini Agent.exe").is_file():
                    if final_dir.exists():
                        shutil.rmtree(final_dir, ignore_errors=True)
                    if cand == extract_tmp:
                        os.replace(str(extract_tmp), str(final_dir))
                    else:
                        os.replace(str(cand), str(final_dir))
                        shutil.rmtree(extract_tmp, ignore_errors=True)
                    meta = {
                        "version": target_ver,
                        "name": rel_name,
                        "notes": notes,
                        "mode": "swap",
                        "staged_dir": str(final_dir),
                        "sha256": src.get("sha256") or "",
                        "writable": True,
                    }
                    _staged_meta_file().write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
                    _cleanup_old_staged(keep_ver=target_ver)
                    _p(100)
                    return {"success": True, "cached": False, "staged": meta, "error": ""}
                shutil.rmtree(extract_tmp, ignore_errors=True)
                try:
                    if zip_path.exists():
                        zip_path.unlink()
                except OSError:
                    pass
        except Exception:
            pass
        finally:
            # 仅当完整解包后才删 zip；若网络中途断开则保留 .part 供下次断点续传
            if (staged_root / target_ver / "Houdini Agent.exe").is_file():
                try:
                    if zip_path.exists():
                        zip_path.unlink()
                except OSError:
                    pass

    # 3) 回退：静默预下载并校验安装包到 staged 目录（点击重启无需再等下载）
    sources = installer_sources(cache)
    inst_target = staged_root / ("HoudiniAgent-Setup-%s.exe" % target_ver)
    part_target = staged_root / ("HoudiniAgent-Setup-%s.exe.part" % target_ver)
    errors = []
    for src in sources:
        _p(0)
        try:
            err = _download_one(requests, src, str(part_target), _p)
        except Exception as e:
            err = str(e) or type(e).__name__
        if not err:
            os.replace(str(part_target), str(inst_target))
            meta = {
                "version": target_ver,
                "name": rel_name,
                "notes": notes,
                "mode": "installer",
                "path": str(inst_target),
                "size": inst_target.stat().st_size,
                "sha256": src.get("sha256") or "",
                "writable": writable,
            }
            _staged_meta_file().write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            _cleanup_old_staged(keep_ver=target_ver)
            _p(100)
            return {"success": True, "cached": False, "staged": meta, "error": ""}
        errors.append("%s：%s" % (src.get("label") or src["url"], err))
    return {"success": False, "cached": False, "staged": None,
            "error": "；".join(errors) or "没有可用的下载地址"}


def can_apply_silently_on_exit(staged=None) -> bool:
    """关闭应用时能否完全无感、免 UAC 地在后台自动完成更新。"""
    if not getattr(sys, "frozen", False):
        return False
    st = staged if staged is not None else get_staged_update()
    if not isinstance(st, dict):
        return False
    if st.get("mode") in ("patch", "swap"):
        return is_dir_writable(app_install_dir())
    if st.get("mode") == "installer":
        return bool(st.get("writable")) and is_dir_writable(app_install_dir())
    return False


_SWAP_SCRIPT_TEMPLATE = r"""param(
    [int]$WaitPid,
    [string]$AppDir,
    [string]$StagedDir,
    [string]$MetaFile,
    [int]$Relaunch,
    [string]$LogFile,
    [string]$Mode = "swap",
    [string]$LockFile = "",
    [string]$VerifyFile = "",
    [string]$RollbackDir = "",
    [string]$FromVer = "",
    [string]$ToVer = ""
)
$ErrorActionPreference = "Stop"

function Log($m) {
    try {
        Add-Content -Path $LogFile -Value ("[" + (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + "] " + $m) -Encoding UTF8
    } catch {}
}

function ShouldPreserve($name) {
    $keep = @("config", "cache", "trainData", "plugins", "rules", "_update_bak", "_update_new")
    if ($keep -contains $name) { return $true }
    if ($name -match '^unins\d+\.(exe|dat|msg)$') { return $true }
    return $false
}

function UpdateRegistryVersion($ver) {
    if (-not $ver) { return }
    try {
        $regPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{B7E4B9C2-3A6D-4F18-9C2E-1D5A7F0E9C44}_is1"
        if (Test-Path -LiteralPath $regPath) {
            Set-ItemProperty -LiteralPath $regPath -Name "DisplayVersion" -Value $ver -ErrorAction SilentlyContinue
            Set-ItemProperty -LiteralPath $regPath -Name "DisplayName" -Value ("Houdini Agent " + $ver) -ErrorAction SilentlyContinue
            Log ("updated HKCU DisplayVersion=" + $ver)
        }
    } catch {
        Log ("registry update skipped: " + $_.Exception.Message)
    }
}

function WriteVerifyMeta($bakPath) {
    if (-not $VerifyFile -or -not $bakPath) { return }
    try {
        $obj = @{
            from_version = $FromVer
            to_version   = $ToVer
            mode         = $Mode
            app_dir      = $AppDir
            rollback_dir = $bakPath
            attempts     = 0
            timestamp    = [int][double]::Parse((Get-Date -UFormat %s))
        }
        $json = $obj | ConvertTo-Json -Compress
        Set-Content -LiteralPath $VerifyFile -Value $json -Encoding UTF8
    } catch {
        Log ("write verify meta failed: " + $_.Exception.Message)
    }
}

Log ("start mode=" + $Mode + " wait_pid=" + $WaitPid + " app=" + $AppDir + " staged=" + $StagedDir + " relaunch=" + $Relaunch)

# Acquire lock file so reopening app immediately waits for swap completion
if ($LockFile) {
    try {
        $lockParent = Split-Path -Parent $LockFile
        if ($lockParent -and -not (Test-Path -LiteralPath $lockParent)) {
            New-Item -ItemType Directory -Path $lockParent -Force | Out-Null
        }
        Set-Content -LiteralPath $LockFile -Value ($PID.ToString()) -Encoding UTF8
    } catch {}
}

$bakDir = Join-Path $AppDir "_update_bak"
$newDir = Join-Path $AppDir "_update_new"
$swapped = $false

try {
    # 1. Wait for old app process to exit (up to 30s)
    if ($WaitPid -gt 0) {
        for ($i = 0; $i -lt 120; $i++) {
            $p = Get-Process -Id $WaitPid -ErrorAction SilentlyContinue
            if (-not $p) { break }
            Start-Sleep -Milliseconds 250
        }
    }
    Start-Sleep -Milliseconds 350

    if (-not (Test-Path -LiteralPath $StagedDir)) {
        throw ("StagedDir not found: " + $StagedDir)
    }

    if ($Mode -eq "patch") {
        # Delta patch mode (~1.5 MB business files): per-file backup & overwrite.
        # Avoids renaming _internal/ so running Houdini holding bridge_payload cwd never blocks update!
        $rbPath = if ($RollbackDir) { $RollbackDir } else { $bakDir }
        if (Test-Path -LiteralPath $rbPath) {
            Remove-Item -LiteralPath $rbPath -Recurse -Force -ErrorAction SilentlyContinue
        }
        New-Item -ItemType Directory -Path $rbPath -Force | Out-Null

        $stagedNorm = (Resolve-Path -LiteralPath $StagedDir).Path.TrimEnd('\', '/')
        $files = Get-ChildItem -LiteralPath $stagedNorm -Recurse -File -Force
        foreach ($f in $files) {
            $rel = $f.FullName.Substring($stagedNorm.Length).TrimStart('\', '/')
            $dstFile = Join-Path $AppDir $rel
            $dstParent = Split-Path -Parent $dstFile
            if ($dstParent -and -not (Test-Path -LiteralPath $dstParent)) {
                New-Item -ItemType Directory -Path $dstParent -Force | Out-Null
            }
            if (Test-Path -LiteralPath $dstFile) {
                $rbFile = Join-Path $rbPath $rel
                $rbParent = Split-Path -Parent $rbFile
                if ($rbParent -and -not (Test-Path -LiteralPath $rbParent)) {
                    New-Item -ItemType Directory -Path $rbParent -Force | Out-Null
                }
                Copy-Item -LiteralPath $dstFile -Destination $rbFile -Force
            }
            $ok = $false
            $lastErr = $null
            for ($retry = 0; $retry -lt 15; $retry++) {
                try {
                    Copy-Item -LiteralPath $f.FullName -Destination $dstFile -Force
                    $ok = $true
                    break
                } catch {
                    $lastErr = $_
                    Start-Sleep -Milliseconds 250
                }
            }
            if (-not $ok) { throw $lastErr }
        }

        Remove-Item -LiteralPath $StagedDir -Recurse -Force -ErrorAction SilentlyContinue
        if ($MetaFile -and (Test-Path -LiteralPath $MetaFile)) {
            Remove-Item -LiteralPath $MetaFile -Force -ErrorAction SilentlyContinue
        }
        WriteVerifyMeta $rbPath
        UpdateRegistryVersion $ToVer
        $swapped = $true
        Log ("patch succeeded (" + $files.Count + " files)")
    } else {
        # Full atomic swap mode: pre-copy to _update_new if cross-drive (C:\ -> D:\)
        $srcRoot = [System.IO.Path]::GetPathRoot($StagedDir).ToUpperInvariant()
        $dstRoot = [System.IO.Path]::GetPathRoot($AppDir).ToUpperInvariant()
        $swapSrc = $StagedDir
        if ($srcRoot -ne $dstRoot) {
            Log ("cross-volume detected (" + $srcRoot + " -> " + $dstRoot + "), pre-copying to _update_new")
            if (Test-Path -LiteralPath $newDir) {
                Remove-Item -LiteralPath $newDir -Recurse -Force -ErrorAction SilentlyContinue
            }
            New-Item -ItemType Directory -Path $newDir -Force | Out-Null
            foreach ($item in (Get-ChildItem -LiteralPath $StagedDir -Force)) {
                if (ShouldPreserve $item.Name) { continue }
                Copy-Item -LiteralPath $item.FullName -Destination (Join-Path $newDir $item.Name) -Recurse -Force
            }
            $swapSrc = $newDir
        }

        if (Test-Path -LiteralPath $bakDir) {
            Remove-Item -LiteralPath $bakDir -Recurse -Force -ErrorAction SilentlyContinue
        }
        New-Item -ItemType Directory -Path $bakDir -Force | Out-Null

        foreach ($item in (Get-ChildItem -LiteralPath $AppDir -Force)) {
            if (ShouldPreserve $item.Name) { continue }
            $dst = Join-Path $bakDir $item.Name
            $ok = $false
            $lastErr = $null
            for ($retry = 0; $retry -lt 15; $retry++) {
                try {
                    Move-Item -LiteralPath $item.FullName -Destination $dst -Force
                    $ok = $true
                    break
                } catch {
                    $lastErr = $_
                    Start-Sleep -Milliseconds 300
                }
            }
            if (-not $ok) { throw $lastErr }
        }

        foreach ($item in (Get-ChildItem -LiteralPath $swapSrc -Force)) {
            if (ShouldPreserve $item.Name) { continue }
            $dst = Join-Path $AppDir $item.Name
            if (Test-Path -LiteralPath $dst) {
                Remove-Item -LiteralPath $dst -Recurse -Force
            }
            Move-Item -LiteralPath $item.FullName -Destination $dst -Force
        }

        Remove-Item -LiteralPath $newDir -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $StagedDir -Recurse -Force -ErrorAction SilentlyContinue
        if ($MetaFile -and (Test-Path -LiteralPath $MetaFile)) {
            Remove-Item -LiteralPath $MetaFile -Force -ErrorAction SilentlyContinue
        }
        if ($VerifyFile) {
            # Keep _update_bak until the new version boots cleanly (mark_update_healthy cleans it)
            WriteVerifyMeta $bakDir
        } else {
            Remove-Item -LiteralPath $bakDir -Recurse -Force -ErrorAction SilentlyContinue
        }
        UpdateRegistryVersion $ToVer
        $swapped = $true
        Log "swap succeeded"
    }
} catch {
    Log ("update failed, rolling back: " + $_.Exception.Message)
    try {
        if ($Mode -eq "patch") {
            $rbPath = if ($RollbackDir) { $RollbackDir } else { $bakDir }
            if (Test-Path -LiteralPath $rbPath) {
                $rbNorm = (Resolve-Path -LiteralPath $rbPath).Path.TrimEnd('\', '/')
                foreach ($f in (Get-ChildItem -LiteralPath $rbNorm -Recurse -File -Force)) {
                    $rel = $f.FullName.Substring($rbNorm.Length).TrimStart('\', '/')
                    $dstFile = Join-Path $AppDir $rel
                    Copy-Item -LiteralPath $f.FullName -Destination $dstFile -Force
                }
                Remove-Item -LiteralPath $rbPath -Recurse -Force -ErrorAction SilentlyContinue
            }
        } else {
            foreach ($item in (Get-ChildItem -LiteralPath $AppDir -Force)) {
                if (ShouldPreserve $item.Name) { continue }
                Remove-Item -LiteralPath $item.FullName -Recurse -Force -ErrorAction SilentlyContinue
            }
            if (Test-Path -LiteralPath $bakDir) {
                foreach ($item in (Get-ChildItem -LiteralPath $bakDir -Force)) {
                    Move-Item -LiteralPath $item.FullName -Destination (Join-Path $AppDir $item.Name) -Force
                }
                Remove-Item -LiteralPath $bakDir -Recurse -Force -ErrorAction SilentlyContinue
            }
            if (Test-Path -LiteralPath $newDir) {
                Remove-Item -LiteralPath $newDir -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
        Log "rollback complete"
    } catch {
        Log ("rollback error: " + $_.Exception.Message)
    }
} finally {
    if ($LockFile -and (Test-Path -LiteralPath $LockFile)) {
        Remove-Item -LiteralPath $LockFile -Force -ErrorAction SilentlyContinue
    }
}

if ($Relaunch -eq 1) {
    $exe = Join-Path $AppDir "Houdini Agent.exe"
    if (Test-Path -LiteralPath $exe) {
        Log ("relaunching " + $exe)
        try {
            Start-Process -FilePath $exe -WorkingDirectory $AppDir
        } catch {
            Log ("relaunch error: " + $_.Exception.Message)
        }
    } else {
        Log ("relaunch skipped, exe missing: " + $exe)
    }
}
"""


def _launch_swap_helper(staged_dir, relaunch=True, mode="swap", to_version=""):
    """拉起完全隐藏的后台 PowerShell 脚本：等当前进程退出 → 秒级替换/补丁 → 按需自动重启。

    注意：Windows 上 CREATE_NO_WINDOW 与 DETACHED_PROCESS 互斥，同时传入会导致
    powershell.exe 直接以 0 退出且不执行任何脚本行！因此仅用
    CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP，并重定向标准流到 DEVNULL。
    """
    import subprocess
    udir = _updates_dir()
    udir.mkdir(parents=True, exist_ok=True)
    script = udir / "apply_update.ps1"
    script.write_text(_SWAP_SCRIPT_TEMPLATE, encoding="utf-8-sig")
    log_file = _user_state_dir() / "update_apply.log"
    cmd = [
        "powershell.exe", "-NoProfile", "-NonInteractive",
        "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
        "-File", str(script),
        "-WaitPid", str(os.getpid()),
        "-AppDir", str(app_install_dir()),
        "-StagedDir", str(staged_dir),
        "-MetaFile", str(_staged_meta_file()),
        "-Relaunch", "1" if relaunch else "0",
        "-LogFile", str(log_file),
        "-Mode", str(mode or "swap"),
        "-LockFile", str(_apply_lock_file()),
        "-VerifyFile", str(_pending_verify_file()),
        "-RollbackDir", str(_rollback_dir()),
        "-FromVer", str(get_local_version()),
        "-ToVer", str(to_version or ""),
    ]
    flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    return subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=flags,
    )


def apply_staged_update(staged=None, relaunch=True) -> dict:
    """应用已暂存的更新。
    - mode='patch': ~1.5 MB 业务层增量补丁，逐文件备份并覆盖（Houdini 开着也不冲突）；
    - mode='swap': 全量预解压替换（跨盘先预拷贝再同盘原子换）；
    - mode='installer': 拉起 /VERYSILENT 无窗口静默安装（relaunch=False 时带 /NORELAUNCH=1）。"""
    st = staged if staged is not None else get_staged_update()
    if not isinstance(st, dict):
        return {"started": False, "error": "没有已就绪的暂存更新"}
    mode = st.get("mode")
    if mode in ("patch", "swap"):
        proc = _launch_swap_helper(
            st["staged_dir"],
            relaunch=relaunch,
            mode=mode,
            to_version=st.get("version") or "",
        )
        return {"started": True, "mode": mode, "proc": proc, "needs_watch": False}
    if mode == "installer":
        proc = launch_installer(st["path"], relaunch=relaunch)
        return {"started": True, "mode": "installer", "proc": proc,
                "needs_watch": bool(proc is not None and relaunch and not st.get("writable"))}
    return {"started": False, "error": "未知暂存模式: %s" % mode}


# ==========================================================
# 启动期互斥等待 + 健康检查自动回滚（防止刚关又秒开撞车或新版闪退）
# ==========================================================

def wait_for_pending_update(timeout: float = 15.0) -> bool:
    """若后台替换脚本正持有 applying.lock（例如用户刚关掉应用又立刻双击图标），
    最多等待 timeout 秒让替换完成，避免新进程与正在搬运文件的脚本撞车。
    返回 True 表示确实等待过替换完成。"""
    lock = _apply_lock_file()
    if not lock.is_file():
        return False
    # 超过 120 秒视为僵尸锁，直接清理不阻塞启动
    try:
        if time.time() - lock.stat().st_mtime > 120:
            lock.unlink()
            return False
    except OSError:
        return False
    deadline = time.monotonic() + float(timeout)
    waited = False
    while lock.is_file() and time.monotonic() < deadline:
        waited = True
        time.sleep(0.2)
    return waited


def check_startup_health(max_failures: int = 2) -> dict:
    """启动健康检查：
    - 刚完成静默升级后，pending_verify.json 会记录 attempts。
    - 正常情况下主窗口启动 5 秒后会调用 mark_update_healthy() 清掉它和备份；
    - 如果新版连续 max_failures 次在到达健康状态前闪退，自动从 rollback_dir 恢复上一版本！"""
    vf = _pending_verify_file()
    if not vf.is_file():
        return {"pending": False, "rolled_back": False}
    try:
        meta = json.loads(vf.read_text(encoding="utf-8-sig"))
    except Exception:
        try:
            vf.unlink()
        except OSError:
            pass
        return {"pending": False, "rolled_back": False}
    if not isinstance(meta, dict):
        return {"pending": False, "rolled_back": False}

    attempts = int(meta.get("attempts") or 0) + 1
    from_ver = str(meta.get("from_version") or "")
    to_ver = str(meta.get("to_version") or "")
    rb_dir = Path(str(meta.get("rollback_dir") or ""))
    app_dir = Path(str(meta.get("app_dir") or app_install_dir()))
    mode = str(meta.get("mode") or "swap")

    if attempts > max_failures and rb_dir.is_dir():
        # 新版连续多次未能健康启动 → 自动回滚！
        ok = False
        try:
            if mode == "patch":
                for dp, _, fns in os.walk(rb_dir):
                    for fn in fns:
                        src = Path(dp) / fn
                        rel = src.relative_to(rb_dir)
                        dst = app_dir / rel
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(src), str(dst))
                ok = True
            else:
                preserve = {"config", "cache", "trainData", "plugins", "rules", "_update_bak", "_update_new"}
                for item in app_dir.iterdir():
                    if item.name in preserve or item.name.lower().startswith("unins"):
                        continue
                    try:
                        if item.is_dir():
                            shutil.rmtree(item, ignore_errors=True)
                        else:
                            item.unlink()
                    except OSError:
                        pass
                for item in rb_dir.iterdir():
                    dst = app_dir / item.name
                    if dst.exists():
                        if dst.is_dir():
                            shutil.rmtree(dst, ignore_errors=True)
                        else:
                            dst.unlink()
                    shutil.move(str(item), str(dst))
                ok = True
        except Exception:
            ok = False
        try:
            vf.unlink()
        except OSError:
            pass
        try:
            if rb_dir.is_dir():
                shutil.rmtree(rb_dir, ignore_errors=True)
        except OSError:
            pass
        return {
            "pending": False,
            "rolled_back": ok,
            "from_version": from_ver,
            "to_version": to_ver,
        }

    meta["attempts"] = attempts
    try:
        vf.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return {"pending": True, "rolled_back": False, "attempts": attempts,
            "from_version": from_ver, "to_version": to_ver}


def mark_update_healthy() -> bool:
    """新版主窗口健康运行数秒后调用：确认升级成功，清理 pending_verify.json 与回滚备份。"""
    vf = _pending_verify_file()
    if not vf.is_file():
        return False
    rb_dir = None
    try:
        meta = json.loads(vf.read_text(encoding="utf-8-sig"))
        if isinstance(meta, dict) and meta.get("rollback_dir"):
            rb_dir = Path(str(meta["rollback_dir"]))
    except Exception:
        pass
    try:
        vf.unlink()
    except OSError:
        pass
    for d in (rb_dir, _rollback_dir(), app_install_dir() / "_update_bak"):
        if d and d.is_dir():
            try:
                shutil.rmtree(d, ignore_errors=True)
            except OSError:
                pass
    return True


def launch_installer(path, relaunch=True):
    """拉起 Inno 安装器静默覆盖安装（/VERYSILENT 无向导窗口）。返回可 poll() 的进程对象；
    只能走 ShellExecute 兜底时返回 None。relaunch=False 时传 /NORELAUNCH=1（关闭应用静默更新不重开）。"""
    import subprocess
    args = "/VERYSILENT /NORESTART /SUPPRESSMSGBOXES"
    if not relaunch:
        args += " /NORELAUNCH=1"
    try:
        return subprocess.Popen(
            [str(path)] + args.split(),
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except OSError:
        # WinError 740（需要提权）等：改走 ShellExecute，由系统弹 UAC
        os.startfile(str(path), "open", args)   # noqa: S606
        return None


# Inno 安装器开始安装时出现的进度窗口（实测 /SILENT 下类名 TWizardForm、
# 标题 "Setup - Houdini Agent <版本>"）。需要管理员时它在 UAC 通过后才出现。
_INSTALLER_WINDOW_CLASS = "TWizardForm"
_INSTALLER_WINDOW_TITLE = "Setup - Houdini Agent"


def installer_window_visible() -> bool:
    """当前是否有我们的安装器进度窗口（= 已过授权、正在安装）。非 Windows 恒为 False。"""
    if os.name != "nt":
        return False
    try:
        import ctypes
        import ctypes.wintypes as wt
        user32 = ctypes.windll.user32
        found = [False]
        proc_t = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

        def cb(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, cls, 64)
            if cls.value != _INSTALLER_WINDOW_CLASS:
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value.startswith(_INSTALLER_WINDOW_TITLE):
                found[0] = True
                return False
            return True

        user32.EnumWindows(proc_t(cb), 0)
        return found[0]
    except Exception:
        return False


def install_watch_step(window_visible: bool, exit_code):
    """安装器拉起后的一次轮询判断。
    返回 ("quit", "") —— 已开始安装（或已装完），应用应保存并退出让安装器替换文件；
         ("failed", 原因) —— 安装器没开始安装就退出了（取消授权 / 出错），应用保持运行；
         ("wait", "") —— 还在等（例如 UAC 弹窗尚未处理）。"""
    if window_visible:
        return "quit", ""
    if exit_code is None:
        return "wait", ""
    if exit_code == 0:
        return "quit", ""
    if exit_code in (2, 5):
        return "failed", "安装已取消（可能拒绝了管理员授权）"
    return "failed", "安装程序异常退出（退出码 %s）" % exit_code


# ==========================================================
# 下载 & 应用更新（旧版：源码树覆盖，仅供 Houdini 内源码模式）
# ==========================================================

def download_and_apply(progress_callback=None) -> dict:
    """下载最新 Release 版本并覆盖本地文件
    
    必须先调用 check_update() 以缓存 zipball_url。
    
    Args:
        progress_callback: 可选回调 (stage: str, percent: int) -> None
            stage: 'downloading' | 'extracting' | 'applying' | 'done'
            percent: 0-100
    
    Returns:
        {'success': bool, 'error': str, 'updated_files': int}
    """
    global _cached_zipball_url
    
    def _progress(stage: str, pct: int):
        if progress_callback:
            try:
                progress_callback(stage, pct)
            except Exception:
                pass
    
    if not _cached_zipball_url:
        return {'success': False, 'error': '未找到下载地址，请先检查更新', 'updated_files': 0}
    
    try:
        import requests  # type: ignore
    except ImportError:
        lib_dir = str(_PROJECT_ROOT / "lib")
        if lib_dir not in sys.path:
            sys.path.insert(0, lib_dir)
        import requests  # type: ignore
    
    tmp_dir = None
    try:
        # ---- 1. 下载 Release ZIP ----
        _progress('downloading', 0)
        resp = requests.get(_cached_zipball_url, stream=True, timeout=60)
        resp.raise_for_status()
        
        total_size = int(resp.headers.get('content-length', 0))
        
        tmp_dir = tempfile.mkdtemp(prefix="houdini_agent_update_")
        zip_path = os.path.join(tmp_dir, "update.zip")
        
        downloaded = 0
        with open(zip_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        _progress('downloading', min(95, int(downloaded / total_size * 95)))
        
        _progress('downloading', 100)
        
        # ---- 2. 解压 ----
        _progress('extracting', 0)
        extract_dir = os.path.join(tmp_dir, "extracted")
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(extract_dir)
        _progress('extracting', 100)
        
        # GitHub ZIP 解压后有一个顶层目录，如 Houdini-Agent-main/
        entries = os.listdir(extract_dir)
        if len(entries) == 1 and os.path.isdir(os.path.join(extract_dir, entries[0])):
            source_root = os.path.join(extract_dir, entries[0])
        else:
            source_root = extract_dir
        
        # ---- 3. 覆盖文件 ----
        _progress('applying', 0)
        updated_count = 0
        target_root = str(_PROJECT_ROOT)
        
        for dirpath, dirnames, filenames in os.walk(source_root):
            # 计算相对路径
            rel_dir = os.path.relpath(dirpath, source_root)
            
            # 跳过需要保留的目录
            top_dir = rel_dir.split(os.sep)[0] if rel_dir != '.' else ''
            if top_dir in _PRESERVE_PATHS:
                continue
            
            # 过滤子目录（不递归进入需要保留的目录）
            dirnames[:] = [d for d in dirnames if d not in _PRESERVE_PATHS]
            
            # 确保目标目录存在
            target_dir = os.path.join(target_root, rel_dir) if rel_dir != '.' else target_root
            os.makedirs(target_dir, exist_ok=True)
            
            for fname in filenames:
                src_file = os.path.join(dirpath, fname)
                dst_file = os.path.join(target_dir, fname)
                
                try:
                    shutil.copy2(src_file, dst_file)
                    updated_count += 1
                except PermissionError:
                    # .pyd / .dll 可能被锁定，跳过
                    pass
                except Exception:
                    pass
        
        _progress('applying', 100)
        _progress('done', 100)
        
        return {'success': True, 'error': '', 'updated_files': updated_count}
        
    except Exception as e:
        return {'success': False, 'error': str(e), 'updated_files': 0}
    
    finally:
        # 清理临时目录
        if tmp_dir and os.path.exists(tmp_dir):
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass


# ==========================================================
# 重启插件
# ==========================================================

def restart_plugin():
    """重启 Houdini Agent 插件窗口
    
    通过重新加载模块并调用 show_tool() 来实现"重启"效果。
    必须在 Qt 主线程调用。
    """
    try:
        import importlib
        
        # 强制清除所有已加载的 houdini_agent 模块
        mods_to_remove = [k for k in sys.modules if k.startswith('houdini_agent')]
        for k in mods_to_remove:
            del sys.modules[k]
        
        # 重新导入并启动
        # 注意：main.py 中的 _reload_modules 会处理模块重新加载
        if 'houdini_agent.main' in sys.modules:
            del sys.modules['houdini_agent.main']
        
        from houdini_agent.main import show_tool
        return show_tool()
        
    except Exception as e:
        print(f"[Updater] Restart failed: {e}")
        import traceback
        traceback.print_exc()
        return None
