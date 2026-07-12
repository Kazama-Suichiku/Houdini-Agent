# -*- coding: utf-8 -*-
"""Auto-start the Houdini Agent bridge when Houdini loads the package."""

import os
import sys


def _diag(msg):
    """诊断日志：pythonrc 是否被执行、bridge 启动是否失败，都落到 bridge.log。"""
    try:
        import time
        from pathlib import Path
        p = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "HoudiniAgent" / "bridge.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + "[pythonrc] " + str(msg).rstrip() + "\n")
    except Exception:
        pass


def _repo_root():
    root = os.environ.get("HAGENT_REPO")
    if root and os.path.isdir(root):
        return root
    # pythonrc.py 在 <root>/houdini_agent/houdini_package/scripts/python/ 下，
    # 需向上 5 层才到加载根 <root>（含 houdini_agent + shared），之前少了一层。
    here = os.path.abspath(__file__)
    return os.path.abspath(os.path.join(here, "..", "..", "..", "..", ".."))


root = _repo_root()
if root not in sys.path:
    sys.path.insert(0, root)

_diag("loaded; HAGENT_REPO=%r root=%r exists=%s" % (
    os.environ.get("HAGENT_REPO"), root, os.path.isdir(root)))


def _start():
    try:
        from houdini_agent.bridge.server import start_bridge
        start_bridge()
    except Exception as exc:
        import traceback
        _diag("Failed to start bridge: %s\n%s" % (exc, traceback.format_exc()))
        print("[Houdini Agent] Failed to start bridge:", exc)


try:
    from PySide6.QtCore import QTimer
except Exception:
    try:
        from PySide2.QtCore import QTimer
    except Exception:
        QTimer = None

if QTimer is not None:
    _diag("scheduling _start via QTimer(1500ms)")
    QTimer.singleShot(1500, _start)
else:
    _diag("no QTimer; calling _start() directly")
    _start()
