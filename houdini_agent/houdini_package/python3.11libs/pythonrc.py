# -*- coding: utf-8 -*-
"""Houdini 官方启动脚本位置（HOUDINI_PATH/pythonX.Ylibs/pythonrc.py）的转发 shim。
真正的启动逻辑在 ../scripts/python/pythonrc.py（自带防重入哨兵，双位置执行也只跑一次）。
注意：Houdini exec 启动脚本时不提供 __file__，定位自身必须退回 co_filename。"""
import os
import sys


def _here():
    try:
        return os.path.abspath(__file__)              # 常规 Python 执行
    except NameError:
        import inspect                                # Houdini exec：无 __file__
        return os.path.abspath(inspect.currentframe().f_code.co_filename)


def _run():
    if getattr(sys, "_hagent_pythonrc_ran", False):
        return                                        # 另一个加载位置已执行过
    real = os.path.abspath(os.path.join(os.path.dirname(_here()),
                                        "..", "scripts", "python", "pythonrc.py"))
    if not os.path.isfile(real):
        return
    with open(real, "r", encoding="utf-8") as f:
        src = f.read()
    exec(compile(src, real, "exec"), {"__file__": real, "__name__": "__main__"})


_run()