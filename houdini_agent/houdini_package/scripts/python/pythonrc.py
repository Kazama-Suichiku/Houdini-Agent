# -*- coding: utf-8 -*-
"""Auto-start the Houdini Agent bridge when Houdini loads the package."""

import os
import sys


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


def _start():
    try:
        from houdini_agent.bridge.server import start_bridge
        start_bridge()
    except Exception as exc:
        print("[Houdini Agent] Failed to start bridge:", exc)


try:
    from PySide6.QtCore import QTimer
except Exception:
    try:
        from PySide2.QtCore import QTimer
    except Exception:
        QTimer = None

if QTimer is not None:
    QTimer.singleShot(1500, _start)
else:
    _start()
