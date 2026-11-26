# -*- coding: utf-8 -*-
"""
Houdini Agent - 自动更新模块

检查新版本：优先读官网的版本清单 latest.json（国内可访问、无 API 限流，且与官网
安装包同步发布），失败再退回 GitHub Releases API。打包版下载新安装包并校验
大小 / sha256 后静默安装；源码模式（Houdini 内旧界面）下载源码覆盖。

线程安全：check / download / apply 均可在后台线程调用，
UI 回调通过 Qt Signal 回到主线程。
"""

import os
import sys
import json
import hashlib
import shutil
import zipfile
import tempfile
from pathlib import Path
from typing import Tuple

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


def _valid_manifest(m) -> bool:
    return (isinstance(m, dict) and isinstance(m.get("version"), str) and m["version"].strip()
            and isinstance(m.get("installer"), dict) and m["installer"].get("url"))


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


def _download_one(requests, src, target, _p):
    """下载单个来源并校验。成功返回 ''，失败返回原因。"""
    resp = requests.get(src["url"], stream=True, timeout=60)
    resp.raise_for_status()
    total = int(resp.headers.get("content-length", 0)) or src.get("size") or 0
    h = hashlib.sha256()
    done = 0
    with open(target, "wb") as f:
        for chunk in resp.iter_content(chunk_size=65536):
            if chunk:
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if total > 0:
                    _p(min(99, done * 100 / total))
    if done < 1024 * 1024:      # 装不进 1MB 的一定不是安装包（挡 404 页面之类）
        return "下载内容异常（%d 字节）" % done
    if src.get("size") and done != src["size"]:
        return "文件大小不符（%d / %d）" % (done, src["size"])
    if src.get("sha256") and h.hexdigest() != src["sha256"]:
        return "校验值不符（可能下载到了旧版安装包）"
    return ""


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


def app_zip_sources(cache=None) -> list:
    """列出可用的应用静默更新包（HoudiniAgent-App-<ver>.zip）来源。"""
    cache = cache if cache is not None else _load_etag_cache()
    m = cache.get("manifest") if _valid_manifest(cache.get("manifest")) else None
    if not m:
        return []
    az = m.get("app_zip")
    if not isinstance(az, dict) or not az.get("url"):
        return []
    return [{
        "url": str(az["url"]),
        "size": int(az.get("size") or 0),
        "sha256": str(az.get("sha256") or "").lower(),
        "version": str(m.get("version") or "").strip().lstrip("vV"),
        "label": "官网更新包",
    }]


def _cleanup_old_staged(keep_ver=None):
    """清理 updates/staged 下不再需要的旧版本暂存文件。"""
    sdir = _updates_dir() / "staged"
    if not sdir.is_dir():
        return
    keep_names = set()
    if keep_ver:
        keep_names.add(str(keep_ver))
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


def stage_update(progress_callback=None, cache=None) -> dict:
    """后台静默下载并预解压/校验新版更新包，供重启或关闭应用时秒级切换。
    - 安装目录可写且清单提供 app_zip → 走 mode='swap'（预解压目录，重启/关闭直接原子替换，免安装程序、免 UAC）
    - 否则回退 mode='installer'（预下载校验好的安装程序到 updates/staged/，点击重启时无需等待下载）"""
    def _p(pct):
        if progress_callback:
            try:
                progress_callback(int(pct))
            except Exception:
                pass

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

    # 1) 优先走免安装器静默原子替换（用户可写目录 + manifest 含 app_zip）
    zips = app_zip_sources(cache) if writable else []
    if zips and zips[0].get("version") == target_ver:
        src = zips[0]
        tmp_dir = _updates_dir() / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        zip_path = tmp_dir / ("HoudiniAgent-App-%s.zip" % target_ver)
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
        except Exception:
            pass
        finally:
            try:
                if zip_path.exists():
                    zip_path.unlink()
            except OSError:
                pass

    # 2) 回退：静默预下载并校验安装包到 staged 目录（点击重启无需再等下载）
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
        try:
            if part_target.exists():
                part_target.unlink()
        except OSError:
            pass
    return {"success": False, "cached": False, "staged": None,
            "error": "；".join(errors) or "没有可用的下载地址"}


def can_apply_silently_on_exit(staged=None) -> bool:
    """关闭应用时能否完全无感、免 UAC 地在后台自动完成更新。"""
    if not getattr(sys, "frozen", False):
        return False
    st = staged if staged is not None else get_staged_update()
    if not isinstance(st, dict):
        return False
    if st.get("mode") == "swap":
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
    [string]$LogFile
)
$ErrorActionPreference = "Stop"
function Log($m) {
    try { Add-Content -Path $LogFile -Value ("[" + (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + "] " + $m) -Encoding UTF8 } catch {}
}
Log ("start wait_pid=" + $WaitPid + " app=" + $AppDir + " staged=" + $StagedDir + " relaunch=" + $Relaunch)
# 1. 等待旧主进程完全退出（最长 30 秒）
for ($i = 0; $i -lt 120; $i++) {
    $p = Get-Process -Id $WaitPid -ErrorAction SilentlyContinue
    if (-not $p) { break }
    Start-Sleep -Milliseconds 250
}
Start-Sleep -Milliseconds 350

$preserve = @("config", "cache", "trainData", "plugins", "rules", "_update_bak")
$bakDir = Join-Path $AppDir "_update_bak"
try {
    if (Test-Path $bakDir) { Remove-Item -Recurse -Force $bakDir -ErrorAction SilentlyContinue }
    New-Item -ItemType Directory -Path $bakDir -Force | Out-Null

    # 2. 移走旧版应用文件到备份目录（保留用户数据目录）
    $moved = @()
    foreach ($item in (Get-ChildItem -LiteralPath $AppDir -Force)) {
        if ($preserve -contains $item.Name) { continue }
        $dst = Join-Path $bakDir $item.Name
        Move-Item -LiteralPath $item.FullName -Destination $dst -Force
        $moved += $item.Name
    }

    # 3. 移入已预解压校验的新版文件
    foreach ($item in (Get-ChildItem -LiteralPath $StagedDir -Force)) {
        if ($preserve -contains $item.Name) { continue }
        $dst = Join-Path $AppDir $item.Name
        if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
        Move-Item -LiteralPath $item.FullName -Destination $dst -Force
    }

    # 4. 清理备份与暂存标记
    Remove-Item -Recurse -Force $bakDir -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $StagedDir -ErrorAction SilentlyContinue
    if (Test-Path $MetaFile) { Remove-Item -Force $MetaFile -ErrorAction SilentlyContinue }
    Log "swap succeeded"
} catch {
    Log ("swap failed, rolling back: " + $_.Exception.Message)
    try {
        foreach ($item in (Get-ChildItem -LiteralPath $AppDir -Force)) {
            if ($preserve -contains $item.Name) { continue }
            Remove-Item -Recurse -Force $item.FullName -ErrorAction SilentlyContinue
        }
        if (Test-Path $bakDir) {
            foreach ($item in (Get-ChildItem -LiteralPath $bakDir -Force)) {
                Move-Item -LiteralPath $item.FullName -Destination (Join-Path $AppDir $item.Name) -Force
            }
            Remove-Item -Recurse -Force $bakDir -ErrorAction SilentlyContinue
        }
        Log "rollback complete"
    } catch {
        Log ("rollback error: " + $_.Exception.Message)
    }
}

if ($Relaunch -eq 1) {
    $exe = Join-Path $AppDir "Houdini Agent.exe"
    if (Test-Path $exe) {
        Log ("relaunching " + $exe)
        Start-Process -FilePath $exe -WorkingDirectory $AppDir
    }
}
"""


def _launch_swap_helper(staged_dir, relaunch=True):
    """拉起完全隐藏的后台 PowerShell 脚本：等当前进程退出 → 秒级原子替换文件 → 按需自动重启。"""
    import subprocess
    udir = _updates_dir()
    udir.mkdir(parents=True, exist_ok=True)
    script = udir / "apply_update.ps1"
    script.write_text(_SWAP_SCRIPT_TEMPLATE, encoding="utf-8")
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
    ]
    flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
             | getattr(subprocess, "DETACHED_PROCESS", 0)
             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    return subprocess.Popen(cmd, close_fds=True, creationflags=flags)


def apply_staged_update(staged=None, relaunch=True) -> dict:
    """应用已暂存的更新。
    - mode='swap': 拉起隐藏替换脚本，退出后 <1 秒完成替换，无安装程序、无弹窗；
    - mode='installer': 拉起 /VERYSILENT 无窗口静默安装（relaunch=False 时带 /NORELAUNCH=1）。"""
    st = staged if staged is not None else get_staged_update()
    if not isinstance(st, dict):
        return {"started": False, "error": "没有已就绪的暂存更新"}
    mode = st.get("mode")
    if mode == "swap":
        proc = _launch_swap_helper(st["staged_dir"], relaunch=relaunch)
        return {"started": True, "mode": "swap", "proc": proc, "needs_watch": False}
    if mode == "installer":
        proc = launch_installer(st["path"], relaunch=relaunch)
        return {"started": True, "mode": "installer", "proc": proc,
                "needs_watch": bool(proc is not None and relaunch and not st.get("writable"))}
    return {"started": False, "error": "未知暂存模式: %s" % mode}


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
