# -*- coding: utf-8 -*-
"""Detect Houdini 20.5+ installs and install the auto-load package."""

import json
import os
import re
import subprocess
from pathlib import Path


MIN_VERSION = (20, 5)


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


def user_pref_dir(major_minor):
    docs = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Documents"
    return docs / ("houdini%s" % major_minor)


def _bridge_python_root(repo_root):
    repo = Path(repo_root).resolve()
    payload = repo / "bridge_payload"
    if payload.exists():
        return payload.resolve()
    return repo


def install_package(repo_root, install):
    py_root = _bridge_python_root(repo_root)
    package_root = (py_root / "houdini_agent" / "houdini_package").resolve()
    pkg_dir = user_pref_dir(install["major_minor"]) / "packages"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "enable": True,
        "env": [
            {"HAGENT_REPO": str(py_root).replace("\\", "/")},
            {"PYTHONPATH": str(py_root).replace("\\", "/") + ";&"},
            {"HOUDINI_PATH": str(package_root).replace("\\", "/") + ";&"},
        ],
    }
    path = pkg_dir / "HoudiniAgent.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def launch_houdini(repo_root, install):
    install_package(repo_root, install)
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PYTHON")}
    env["HAGENT_REPO"] = str(_bridge_python_root(repo_root))
    env["HAGENT_EXTERNAL_LAUNCH"] = "1"
    return subprocess.Popen([install["exe"]], cwd=install["path"], env=env)
