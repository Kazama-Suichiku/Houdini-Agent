# -*- coding: utf-8 -*-
"""Detect Houdini 19.5+ installs and install the auto-load package.

The external app talks to Houdini through the Bridge (pure Python, no Qt), so
it can drive Houdini versions older than the QML-capable 21 line. Floor is
19.5 (Python 3.9); 19.0 (Python 3.7) is intentionally excluded."""

import json
import os
import re
import subprocess
from pathlib import Path


MIN_VERSION = (19, 5)


def _version_tuple(text):
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", str(text or ""))
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def _version_ok(ver):
    if not ver:
        return False
    return (ver[0], ver[1]) >= MIN_VERSION


def _candidate_dirs():
    seen = set()
    roots = [
        Path(os.environ.get("HFS", "")),
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Side Effects Software",
        Path(os.environ.get("ProgramW6432", r"C:\Program Files")) / "Side Effects Software",
    ]
    for root in roots:
        if not str(root) or not root.exists():
            continue
        if root.name.lower().startswith("houdini"):
            dirs = [root]
        else:
            dirs = sorted(root.glob("Houdini*"), reverse=True)
        for d in dirs:
            key = str(d).lower()
            if d.is_dir() and key not in seen:
                seen.add(key)
                yield d, d.name


def _registry_dirs():
    try:
        import winreg
    except Exception:
        return
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Side Effects Software"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Side Effects Software"),
    ]
    for hive, key_path in roots:
        try:
            key = winreg.OpenKey(hive, key_path)
        except OSError:
            continue
        try:
            count = winreg.QueryInfoKey(key)[0]
            for i in range(count):
                try:
                    sub = winreg.EnumKey(key, i)
                    sk = winreg.OpenKey(key, sub)
                    version = None
                    try:
                        version, _ = winreg.QueryValueEx(sk, "Version")
                    except OSError:
                        version = sub
                    for value_name in ("InstallPath", "Path", "HFS"):
                        try:
                            val, _ = winreg.QueryValueEx(sk, value_name)
                            if val:
                                yield Path(val), version
                        except OSError:
                            pass
                except OSError:
                    pass
            # Some installs are stored under HKLM\...\Houdini as version -> path values.
            try:
                for i in range(winreg.QueryInfoKey(key)[1]):
                    name, val, _ = winreg.EnumValue(key, i)
                    if isinstance(val, str) and val:
                        yield Path(val), name
            except OSError:
                pass
        finally:
            try:
                winreg.CloseKey(key)
            except Exception:
                pass


def find_houdini_installs():
    installs = []
    seen = set()
    for item in list(_candidate_dirs()) + list(_registry_dirs() or []):
        d, ver_hint = item if isinstance(item, tuple) else (item, None)
        ver = _version_tuple(ver_hint) or _version_tuple(d.name) or _version_tuple(str(d))
        exe = None
        for rel in ("bin/houdini.exe", "bin/houdinifx.exe", "bin/houdinicore.exe",
                    "houdini.exe", "houdinifx.exe", "houdinicore.exe"):
            p = d / rel
            if p.exists():
                exe = p
                break
        if exe is None or not exe.exists() or not _version_ok(ver):
            continue
        key = str(exe).lower()
        if key in seen:
            continue
        seen.add(key)
        installs.append({
            "version": "%d.%d.%d" % ver,
            "major_minor": "%d.%d" % (ver[0], ver[1]),
            "path": str(d),
            "exe": str(exe),
        })
    installs.sort(key=lambda x: _version_tuple(x["version"]) or (0, 0, 0), reverse=True)
    return installs


def is_houdini_running():
    if os.name != "nt":
        return False
    try:
        out = subprocess.check_output(
            ["tasklist", "/NH"],
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).decode("mbcs", "ignore")
        low = out.lower()
        return any(name in low for name in ("houdini.exe", "houdinifx.exe", "houdinicore.exe"))
    except Exception:
        return False


def _documents_candidates():
    """按可信度排序的「文档」目录候选。Houdini 通过 shell API 解析 Documents，
    中文机常见把「文档」移到 D 盘 / 被 OneDrive 重定向——那时真实路径只有注册表知道，
    而 %USERPROFILE%\\Documents 是错的（此前只写这里导致部分用户集成包永远不加载）。"""
    out = []
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
        try:
            val, _t = winreg.QueryValueEx(key, "Personal")
        finally:
            winreg.CloseKey(key)
        p = os.path.expandvars(str(val)).strip()
        if p:
            out.append(Path(p))
    except Exception:
        pass
    up = os.environ.get("USERPROFILE")
    if up:
        out.append(Path(up) / "Documents")
    out.append(Path.home() / "Documents")
    seen, uniq = set(), []
    for p in out:
        k = str(p).lower()
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def user_pref_dirs(major_minor):
    """某 Houdini 版本所有候选用户目录（houdiniX.Y），去重保序。
    HOUDINI_USER_PREF_DIR 环境变量最优先（支持官方 __HVER__ 占位符）。"""
    dirs = []
    envd = (os.environ.get("HOUDINI_USER_PREF_DIR") or "").strip()
    if envd:
        p = envd.replace("__HVER__", major_minor).replace(
            "$HOME", os.environ.get("HOME") or os.environ.get("USERPROFILE") or "$HOME")
        p = os.path.expandvars(p)
        if "$" not in p and "%" not in p:   # 变量没展开干净就放弃该候选，别写进字面量路径
            dirs.append(Path(p))
    for doc in _documents_candidates():
        dirs.append(doc / ("houdini%s" % major_minor))
    seen, uniq = set(), []
    for p in dirs:
        k = str(p).lower()
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def user_pref_dir(major_minor):
    """向后兼容：返回首选候选（env 覆盖 > 注册表 Documents > USERPROFILE）。"""
    return user_pref_dirs(major_minor)[0]


def known_pref_versions():
    """扫描所有候选 Documents 下已存在的 houdiniX.Y 用户目录 → 版本号集合。
    覆盖「运行中的 Houdini 不在安装扫描结果里」（Steam 版/自定义安装/网络盘）的情况：
    用户目录存在 = 这个版本在本机真实跑过，就该给它装集成包。"""
    vers = set()
    for doc in _documents_candidates():
        try:
            for d in doc.glob("houdini*"):
                if not d.is_dir():
                    continue
                m = re.fullmatch(r"houdini(\d+)\.(\d+)", d.name)
                if not m:
                    continue
                ver = (int(m.group(1)), int(m.group(2)))
                if _version_ok(ver):
                    vers.add("%d.%d" % ver)
        except Exception:
            continue
    return vers


def _bridge_python_root(repo_root):
    repo = Path(repo_root).resolve()
    payload = repo / "bridge_payload"
    if payload.exists():
        return payload.resolve()
    return repo


def _package_json(repo_root):
    py_root = _bridge_python_root(repo_root)
    package_root = (py_root / "houdini_agent" / "houdini_package").resolve()
    return {
        "enable": True,
        "env": [
            {"HAGENT_REPO": str(py_root).replace("\\", "/")},
            {"PYTHONPATH": str(py_root).replace("\\", "/") + ";&"},
            {"HOUDINI_PATH": str(package_root).replace("\\", "/") + ";&"},
        ],
    }


def install_package_for_version(repo_root, major_minor):
    """给某个 Houdini 版本装集成包：写进【全部】候选用户目录（注册表 Documents /
    USERPROFILE / env 覆盖）。Houdini 只会读它实际解析出的那个，其余是几百字节的
    无害冗余——比猜错一个位置导致永远不加载好得多。返回写下的文件路径列表。"""
    data = _package_json(repo_root)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    written = []
    for pref in user_pref_dirs(major_minor):
        try:
            pkg_dir = pref / "packages"
            pkg_dir.mkdir(parents=True, exist_ok=True)
            path = pkg_dir / "HoudiniAgent.json"
            path.write_text(text, encoding="utf-8")
            written.append(str(path))
        except Exception:
            continue
    if not written:
        raise RuntimeError("no writable Houdini user pref dir for %s" % major_minor)
    return written


def install_package(repo_root, install):
    """向后兼容入口：按安装记录装包（现在会写全部候选目录）。返回首个文件路径。"""
    return install_package_for_version(repo_root, install["major_minor"])[0]


def install_all_packages(repo_root, installs=None):
    """给本机所有相关 Houdini 版本装集成包：已发现安装的版本 ∪ 用户目录里出现过的
    版本（Steam 版/自定义安装即使扫不到安装目录，跑过就会留下 houdiniX.Y）。
    返回 {version: [written...]}；单版本失败不阻断其它版本。"""
    if installs is None:
        installs = find_houdini_installs()
    versions = {it["major_minor"] for it in installs} | known_pref_versions()
    out = {}
    for mm in sorted(versions):
        try:
            out[mm] = install_package_for_version(repo_root, mm)
        except Exception:
            out[mm] = []
    return out


def launch_houdini(repo_root, install):
    install_package(repo_root, install)
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PYTHON")}
    env["HAGENT_REPO"] = str(_bridge_python_root(repo_root))
    env["HAGENT_EXTERNAL_LAUNCH"] = "1"
    return subprocess.Popen([install["exe"]], cwd=install["path"], env=env)
