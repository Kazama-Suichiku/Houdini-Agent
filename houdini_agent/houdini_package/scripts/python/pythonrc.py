# -*- coding: utf-8 -*-
"""Auto-start the Houdini Agent bridge when Houdini loads the package.

本文件可能被执行两次：scripts/python/（Houdini 21 实测生效）与 pythonX.Ylibs/
（官方文档的启动脚本位置，老版本靠它）都指向这里——用进程级哨兵保证只跑一次。"""

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


def _start():
    try:
        from houdini_agent.bridge.server import start_bridge
        start_bridge()
    except Exception as exc:
        import traceback
        _diag("Failed to start bridge: %s\n%s" % (exc, traceback.format_exc()))
        print("[Houdini Agent] Failed to start bridge:", exc)


def _main():
    root = _repo_root()
    if root not in sys.path:
        sys.path.insert(0, root)

    _diag("loaded from %s; HAGENT_REPO=%r root=%r exists=%s" % (
        os.path.dirname(os.path.abspath(__file__)),
        os.environ.get("HAGENT_REPO"), root, os.path.isdir(root)))

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


# 防重入：两个加载位置（scripts/python 与 pythonX.Ylibs）都可能执行本文件
if not getattr(sys, "_hagent_pythonrc_ran", False):
    sys._hagent_pythonrc_ran = True
    _main()
