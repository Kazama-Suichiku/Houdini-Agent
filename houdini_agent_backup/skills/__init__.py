# -*- coding: utf-8 -*-
"""
Skill 注册表 & 加载器

Skill 是预定义的 Python 代码片段，在 Houdini 环境中执行。
每个 skill 文件放在 skills/ 目录下，包含:
  - SKILL_INFO: dict  (name, description, parameters)
  - run(**kwargs) -> dict  入口函数
"""

import os
import importlib
import traceback
from typing import Dict, Any, Optional, List
from pathlib import Path


# 全局注册表：skill_name -> module
_registry: Dict[str, Any] = {}
_loaded = False


def _load_all():
    """扫描 skills/ 目录，加载所有 skill 模块"""
    global _registry, _loaded
    if _loaded:
        return

    skill_dir = Path(__file__).parent
    for f in sorted(skill_dir.glob("*.py")):
        if f.name.startswith("_"):
            continue
        module_name = f.stem
        try:
            # 相对导入不好用，用 importlib 动态加载
            spec = importlib.util.spec_from_file_location(
                f"houdini_skills.{module_name}", str(f))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            info = getattr(mod, "SKILL_INFO", None)
            run_fn = getattr(mod, "run", None)
            if info and run_fn and callable(run_fn):
                name = info.get("name", module_name)
                _registry[name] = mod
        except Exception as e:
            print(f"[Skills] 加载 {module_name} 失败: {e}")

    _loaded = True
    if _registry:
        print(f"[Skills] 已加载 {len(_registry)} 个 skill: {', '.join(_registry.keys())}")


def list_skills() -> List[Dict[str, Any]]:
    """返回所有已注册 skill 的元数据"""
    _load_all()
    result = []
    for name, mod in _registry.items():
        info = dict(getattr(mod, "SKILL_INFO", {}))
        info.setdefault("name", name)
        result.append(info)
    return result


def run_skill(skill_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """执行指定 skill

    Args:
        skill_name: skill 名称
        params: 传给 run() 的参数

    Returns:
        skill 返回的字典，或包含 error 的字典
    """
    _load_all()

    mod = _registry.get(skill_name)
    if mod is None:
        available = ", ".join(_registry.keys()) or "(无)"
        return {"error": f"Skill 不存在: {skill_name}\n可用 skill: {available}"}

    run_fn = getattr(mod, "run", None)
    if not callable(run_fn):
        return {"error": f"Skill '{skill_name}' 没有 run() 函数"}

    try:
        result = run_fn(**params)
        if not isinstance(result, dict):
            result = {"result": str(result)}
        return result
    except Exception as e:
        return {"error": f"Skill 执行失败: {e}\n{traceback.format_exc()[:500]}"}


def reload_skills():
    """重新加载所有 skill（开发调试用）"""
    global _registry, _loaded
    _registry.clear()
    _loaded = False
    _load_all()
