# -*- coding: utf-8 -*-
"""
Meshy 集成 — 配置与密钥管理（自包含，不污染聊天 LLM 的 provider 体系）。

密钥来源（按优先级）：
  1. 环境变量 MESHY_API_KEY / DCC_AI_MESHY_API_KEY
  2. config/houdini_ai.ini 中的 meshy_api_key
密钥只读取，绝不打印明文。
"""

import os

from shared.common_utils import load_config, save_config, get_cache_dir

API_BASE = "https://api.meshy.ai"

# 各能力对应的 endpoint 前缀（注意 text-to-3d 是 v2，其余是 v1）
ENDPOINTS = {
    "image-to-3d":   "/openapi/v1/image-to-3d",
    "text-to-3d":    "/openapi/v2/text-to-3d",
    "text-to-image": "/openapi/v1/text-to-image",
    "image-to-image": "/openapi/v1/image-to-image",
    "retexture":     "/openapi/v1/retexture",
    "remesh":        "/openapi/v1/remesh",
    # 绑定/动画（自动 rig 人形角色 + 套预设动作；rig→animate 两阶段）
    "rigging":       "/openapi/v1/rigging",
    "animation":     "/openapi/v1/animations",
    "balance":       "/openapi/v1/balance",
}

_ENV_VARS = ("MESHY_API_KEY", "DCC_AI_MESHY_API_KEY")
_CONFIG_KEY = "meshy_api_key"


def get_api_key():
    """读取 Meshy API Key，未配置返回 None。"""
    for var in _ENV_VARS:
        v = os.environ.get(var)
        if v:
            return v.strip()
    cfg, _ = load_config("ai", dcc_type="houdini")
    if cfg:
        v = cfg.get(_CONFIG_KEY)
        if v:
            return v.strip()
    return None


def has_api_key():
    return bool(get_api_key())


def set_api_key(key, persist=True):
    """写入内存环境变量；persist 时落盘到 houdini_ai.ini。返回是否成功。"""
    key = (key or "").strip()
    if not key:
        return False
    os.environ["MESHY_API_KEY"] = key
    if persist:
        cfg, _ = load_config("ai", dcc_type="houdini")
        cfg = cfg or {}
        cfg[_CONFIG_KEY] = key
        ok, _ = save_config("ai", cfg, dcc_type="houdini")
        return ok
    return True


def clear_api_key():
    """清除已保存的 Meshy API Key（内存环境变量 + 落盘配置）——用于"退出登录"。
    返回是否已真正登出（若 key 仍能从某个进程级环境变量解析到，返回 False，
    让 UI 提示"该 key 来自系统环境变量，需在系统层面移除"）。"""
    for var in _ENV_VARS:
        os.environ.pop(var, None)
    try:
        cfg, _ = load_config("ai", dcc_type="houdini")
        if cfg and _CONFIG_KEY in cfg:
            cfg.pop(_CONFIG_KEY, None)
            save_config("ai", cfg, dcc_type="houdini")
    except Exception:
        pass
    return not has_api_key()


def masked_key():
    """返回脱敏后的 key 供 UI 显示，例如 msy_****abcd。"""
    k = get_api_key()
    if not k:
        return ""
    if len(k) <= 8:
        return "*" * len(k)
    return k[:4] + "*" * 4 + k[-4:]


def assets_dir(task_id=None):
    """资产缓存目录：<repo>/cache/meshy[/<task_id>]，自动创建。"""
    base = os.path.join(get_cache_dir(), "meshy")
    if task_id:
        base = os.path.join(base, str(task_id))
    os.makedirs(base, exist_ok=True)
    return base


def asset_path(task_id, *parts):
    """拼出某个资产文件路径，但【不创建】任何目录（用于探测本地缓存是否存在，
    避免给每个云端任务都建一个空目录）。"""
    return os.path.join(get_cache_dir(), "meshy", str(task_id), *parts)
