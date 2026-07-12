# -*- coding: utf-8 -*-
"""Houdini 官方启动脚本位置（HOUDINI_PATH/pythonX.Ylibs/pythonrc.py）的转发 shim。
真正的启动逻辑在 ../scripts/python/pythonrc.py（自带防重入哨兵，双位置执行也只跑一次）。"""
import os

_real = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "..", "scripts", "python", "pythonrc.py"))
if os.path.isfile(_real):
    with open(_real, "r", encoding="utf-8") as _f:
        exec(compile(_f.read(), _real, "exec"), {"__file__": _real, "__name__": "__main__"})