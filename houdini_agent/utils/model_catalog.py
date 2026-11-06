# -*- coding: utf-8 -*-
"""
动态模型目录：静态兜底 + 磁盘缓存 + 自动拉新（merge / enrich）。

本模块为 ``ui_qml/controller.py`` 与 ``ui/header.py`` 提供统一的模型目录数据源：

- **静态兜底**：``STATIC_MODELS`` / ``STATIC_CONTEXT`` / ``STATIC_VISION`` / ``STATIC_FEATURES``
  是人工维护的目录（离线永远可用，顺序即推荐顺序，OpenRouter 保持精挑清单）。
- **自动拉新**：``fetch_provider_models`` / ``refresh_provider`` / ``refresh_all`` 用
  stdlib ``urllib.request``（不引入 requests）拉取各 provider 的 ``/v1/models``，
  经 ``is_chat_model`` 过滤掉图片生成 / 语音 / 嵌入等非对话模型后，
  连同元数据（context / vision）写入 ``<config>/model_catalog.json``，TTL 6 小时。
  任何失败（缺 key、网络异常、非 200、JSON 坏、data 为空）都静默返回 ``None``，绝不抛异常。
- **merge 策略**（duojie / openai / deepseek / glm）：缓存里有 live 清单时，
  存活的静态项保持原顺序在前，新发现的模型按字母序追加，已下线的静态项被剔除；
  无缓存或拉取失败则原样回退静态表。
- **enrich 策略**（openrouter）：始终返回人工挑选的静态顺序（455 条 live 不直接铺开），
  live 数据只用于补元数据（context_length / input_modalities）。
- **元数据三级回退**：``context_limit`` / ``supports_vision`` / ``model_features``
  按「缓存 meta → 静态表 → 家族推断」取值。

可测试性：模块级 ``_TRANSPORT`` 钩子（``set_transport`` / ``reset_transport``）、
所有公开函数都接受 ``cache_path``、``resolve_api_key`` 纯函数；import 时不联网、不落盘。
"""

import json
import os
import re
import threading
import time
import urllib.request
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from shared.common_utils import get_cache_dir, load_config

# ------------------------------------------------------------------ 常量

_USER_AGENT = "HoudiniAgent/2.0"
CACHE_FILE_NAME = "model_catalog.json"
CACHE_VERSION = 1
MODEL_CATALOG_TTL_SECONDS = 6 * 3600

# ------------------------------------------------------------------ 静态表（从 controller.py / header.py 搬运并更新）

STATIC_MODELS: Dict[str, List[str]] = {
    # 2026-11 对齐 /v1/models 实测：kimi-k3 / deepseek-v4-pro / deepseek-v4.1-flash 已下线；
    # 新增 glm-5.3-flashx 与 kiro-auto（新模型一律追加在人工清单末尾，保留原有顺序）
    "duojie": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-sonnet-5",
               "gpt-6.1-sol", "gpt-6-sol", "gpt-6-astra",
               "grok-4.7", "glm-5.3", "glm-5.3-flash", "glm-5.3-flashx", "kiro-auto"],
    # OpenRouter 必须保留人工精挑的顺序，不能直接铺 455 条 live 数据
    "openrouter": ["anthropic/claude-opus-5.5", "anthropic/claude-sonnet-5.5", "anthropic/claude-haiku-5.5",
                   "anthropic/claude-fable-5.1", "anthropic/claude-opus-5",
                   "openai/gpt-6.1-sol", "openai/gpt-6-astra", "openai/gpt-6-luna", "openai/gpt-5.6-sol",
                   "google/gemini-4-argon", "google/gemini-3.8-flash",
                   "deepseek/deepseek-v4.1-flash", "deepseek/deepseek-v4-pro",
                   "z-ai/glm-5.3", "x-ai/grok-4.7", "moonshotai/kimi-k3", "minimax/minimax-m3"],
    "openai": ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5"],
    # ChatGPT 订阅（本机 CPA + Codex OAuth）；实际列表由 subscription 模块单独刷新，这里只做兜底
    "codex": ["gpt-6.1-sol", "gpt-6-astra", "gpt-6-sol", "gpt-6-luna",
              "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5"],
    # Claude 订阅（本机 CPA + Claude Code OAuth）；Anthropic 不允许第三方使用订阅，同样只做兜底
    "claude_sub": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1",
                   "claude-opus-5", "claude-sonnet-5", "claude-opus-4-8"],
    "deepseek": ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-flash-vision-exp"],
    "glm": ["glm-4.7"],
    "custom": [],
}

# 两份现有上下文表（controller.CONTEXT_LIMITS / header._model_context_limits）的并集；
# 同一模型取值不一致时以 controller.py 为准（实测两表一致）
STATIC_CONTEXT: Dict[str, int] = {
    # Duojie（中转；Claude 走 200K 保守值）
    "claude-opus-5-5": 200000, "claude-sonnet-5-5": 200000, "claude-sonnet-5": 200000,
    "gpt-6.1-sol": 1000000, "gpt-6-sol": 1000000, "gpt-6-astra": 1000000,
    "kimi-k3": 1048576, "grok-4.7": 500000,
    "glm-5.3": 1048576, "glm-5.3-flash": 1048576, "deepseek-v4.1-flash": 1048576,
    # OpenAI 直连
    "gpt-5.6-sol": 1000000, "gpt-5.6-terra": 1000000, "gpt-5.6-luna": 1000000, "gpt-5.5": 1000000,
    # OpenRouter
    "anthropic/claude-opus-5.5": 1000000, "anthropic/claude-sonnet-5.5": 1000000,
    "anthropic/claude-haiku-5.5": 1000000, "anthropic/claude-fable-5.1": 1000000,
    "anthropic/claude-opus-5": 1000000, "openai/gpt-6.1-sol": 1050000,
    "openai/gpt-6-astra": 1050000, "openai/gpt-6-luna": 1050000,
    "openai/gpt-5.6-sol": 1050000, "google/gemini-4-argon": 1048576,
    "google/gemini-3.8-flash": 1048576, "deepseek/deepseek-v4.1-flash": 1048576,
    "deepseek/deepseek-v4-pro": 1048576, "z-ai/glm-5.3": 1048576,
    "x-ai/grok-4.7": 500000, "moonshotai/kimi-k3": 1048576,
    "minimax/minimax-m3": 1048576,
    # DeepSeek 直连（deepseek-v4-pro 也在 Duojie 上）
    "deepseek-v4-flash": 1048576, "deepseek-v4-pro": 1048576, "deepseek-v4-flash-vision-exp": 1048576,
    # GLM 直连
    "glm-4.7": 200000,
    # 2026-11 新增目录条目
    "glm-5.3-flashx": 1048576,
    "kiro-auto": 200000,  # Claude 系路由模型，沿用其它 Claude 条目的保守 200K 约定
}

# 现有 VISION_MODELS + 新增 glm-5.3-flashx / kiro-auto
STATIC_VISION: Set[str] = {
    # Duojie
    "claude-opus-5-5", "claude-sonnet-5-5", "claude-sonnet-5",
    "gpt-6.1-sol", "gpt-6-sol", "gpt-6-astra",
    "kimi-k3", "grok-4.7", "glm-5.3-flash", "deepseek-v4.1-flash",
    "glm-5.3-flashx", "kiro-auto",
    # OpenAI 直连
    "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5",
    # OpenRouter（deepseek-v4-pro / glm-5.3 只收文本）
    "anthropic/claude-opus-5.5", "anthropic/claude-sonnet-5.5", "anthropic/claude-haiku-5.5",
    "anthropic/claude-fable-5.1", "anthropic/claude-opus-5",
    "openai/gpt-6.1-sol", "openai/gpt-6-astra", "openai/gpt-6-luna", "openai/gpt-5.6-sol",
    "google/gemini-4-argon", "google/gemini-3.8-flash", "deepseek/deepseek-v4.1-flash",
    "x-ai/grok-4.7", "moonshotai/kimi-k3", "minimax/minimax-m3",
    # DeepSeek 直连
    "deepseek-v4-flash-vision-exp",
}

# header._model_features 搬运 + 为新增 id 补齐（prompt caching 全 True；vision 与 STATIC_VISION 一致）
_FEAT_VIS: Dict[str, bool] = {"supports_prompt_caching": True, "supports_vision": True}
_FEAT_TXT: Dict[str, bool] = {"supports_prompt_caching": True, "supports_vision": False}

STATIC_FEATURES: Dict[str, Dict[str, bool]] = {
    # DeepSeek
    "deepseek-v4-flash":            dict(_FEAT_TXT),
    "deepseek-v4-pro":              dict(_FEAT_TXT),
    "deepseek-v4-flash-vision-exp": dict(_FEAT_VIS),
    # GLM
    "glm-4.7":                      dict(_FEAT_TXT),
    # OpenAI
    "gpt-5.6-sol":                  dict(_FEAT_VIS),
    "gpt-5.6-terra":                dict(_FEAT_VIS),
    "gpt-5.6-luna":                 dict(_FEAT_VIS),
    "gpt-5.5":                      dict(_FEAT_VIS),
    # Duojie - Claude
    "claude-opus-5-5":              dict(_FEAT_VIS),
    "claude-sonnet-5-5":            dict(_FEAT_VIS),
    "claude-sonnet-5":              dict(_FEAT_VIS),
    # Duojie - GPT
    "gpt-6.1-sol":                  dict(_FEAT_VIS),
    "gpt-6-sol":                    dict(_FEAT_VIS),
    "gpt-6-astra":                  dict(_FEAT_VIS),
    # Duojie - Kimi / Grok
    "kimi-k3":                      dict(_FEAT_VIS),
    "grok-4.7":                     dict(_FEAT_VIS),
    # Duojie - GLM
    "glm-5.3":                      dict(_FEAT_TXT),
    "glm-5.3-flash":                dict(_FEAT_VIS),
    "glm-5.3-flashx":               dict(_FEAT_VIS),
    # Duojie - Kiro（Claude 系路由）
    "kiro-auto":                    dict(_FEAT_VIS),
    # Duojie - DeepSeek
    "deepseek-v4.1-flash":          dict(_FEAT_VIS),
    # OpenRouter 模型
    "anthropic/claude-opus-5.5":           dict(_FEAT_VIS),
    "anthropic/claude-sonnet-5.5":         dict(_FEAT_VIS),
    "anthropic/claude-haiku-5.5":          dict(_FEAT_VIS),
    "anthropic/claude-fable-5.1":          dict(_FEAT_VIS),
    "anthropic/claude-opus-5":             dict(_FEAT_VIS),
    "openai/gpt-6.1-sol":                  dict(_FEAT_VIS),
    "openai/gpt-6-astra":                  dict(_FEAT_VIS),
    "openai/gpt-6-luna":                   dict(_FEAT_VIS),
    "openai/gpt-5.6-sol":                  dict(_FEAT_VIS),
    "google/gemini-4-argon":               dict(_FEAT_VIS),
    "google/gemini-3.8-flash":             dict(_FEAT_VIS),
    "deepseek/deepseek-v4.1-flash":        dict(_FEAT_VIS),
    "deepseek/deepseek-v4-pro":            dict(_FEAT_TXT),
    "z-ai/glm-5.3":                        dict(_FEAT_TXT),
    "x-ai/grok-4.7":                       dict(_FEAT_VIS),
    "moonshotai/kimi-k3":                  dict(_FEAT_VIS),
    "minimax/minimax-m3":                  dict(_FEAT_VIS),
}

# ------------------------------------------------------------------ provider 源注册表（可做网络拉取的 provider）

PROVIDER_SOURCES: Dict[str, Dict[str, Any]] = {
    "duojie": {
        "models_url": "https://api.duojie.games/v1/models",
        "key_provider": "duojie",
        "policy": "merge",
        "auth_required": True,
    },
    "openrouter": {
        "models_url": "https://openrouter.ai/api/v1/models",
        "key_provider": "openrouter",
        "policy": "enrich",
        "auth_required": False,
    },
    "openai": {
        "models_url": "https://api.openai.com/v1/models",
        "key_provider": "openai",
        "policy": "merge",
        "auth_required": True,
    },
    "deepseek": {
        "models_url": "https://api.deepseek.com/v1/models",
        "key_provider": "deepseek",
        "policy": "merge",
        "auth_required": True,
    },
    "glm": {
        "models_url": "https://open.bigmodel.cn/api/paas/v4/models",
        "key_provider": "glm",
        "policy": "merge",
        "auth_required": True,
    },
    # codex / claude_sub（订阅侧由 subscription 模块单独刷新）与 custom 不做网络拉取，故不在注册表内
}

# ------------------------------------------------------------------ 过滤规则

# 与对话协议相关的 endpoint 类型（与 endpoint 无关的信息如 openai-alpha-search 不算）
_CHAT_ENDPOINTS = frozenset(
    {"openai", "anthropic", "gemini", "openai-response", "openai-response-compact"})

# 非对话模型家族：词边界 + 忽略大小写匹配（embedding 放在 embed 前，保证回退正确；
# 注意 "flash" 不在排除列表——glm-5.3-flash / glm-5.3-flashx 是对话模型）
_NON_CHAT_FAMILIES: Tuple[str, ...] = (
    "gpt-image", "dall-e", "flux", "stable-diffusion", "sdxl",
    "whisper", "tts", "embedding", "embed", "rerank", "moderation",
    "ocr", "sora", "veo", "kling", "midjourney", "video",
)
_NON_CHAT_RE = re.compile(
    r"\b(?:%s)\b" % "|".join(re.escape(f) for f in _NON_CHAT_FAMILIES), re.IGNORECASE)

# OpenRouter 变体后缀：:batch / :thinking 排除，:free 保留
_EXCLUDED_VARIANT_SUFFIXES = (":batch", ":thinking")


def is_chat_model(model_id: str, supported_endpoint_types: Optional[List[str]] = None) -> bool:
    """判断一个模型 id 是否为对话模型。

    规则：
    1. ``supported_endpoint_types`` 非空时，必须与支持的对话 endpoint 集合有交集；
    2. 无论如何都按「词边界 + 忽略大小写」排除图片/语音/嵌入等非对话家族；
    3. 排除 OpenRouter 变体后缀 ``:batch`` / ``:thinking``（保留 ``:free``）；
    4. 空 id / 纯空白返回 False。
    """
    mid = str(model_id or "").strip()
    if not mid:
        return False
    if supported_endpoint_types:
        types = {str(t).lower() for t in supported_endpoint_types if t}
        if not (types & _CHAT_ENDPOINTS):
            return False
    if mid.endswith(_EXCLUDED_VARIANT_SUFFIXES):
        return False
    if _NON_CHAT_RE.search(mid):
        return False
    return True


# ------------------------------------------------------------------ key 解析（纯函数，测试可 monkeypatch 环境变量）

_ENV_KEY_VARS: Dict[str, List[str]] = {
    "duojie": ["DUOJIE_API_KEY", "DCC_AI_DUOJIE_API_KEY"],
    "openai": ["OPENAI_API_KEY"],
    "deepseek": ["DEEPSEEK_API_KEY"],
    "glm": ["GLM_API_KEY", "ZHIPU_API_KEY"],
    "openrouter": ["OPENROUTER_API_KEY"],
}
_INI_KEY_FIELDS: Dict[str, str] = {
    "duojie": "duojie_api_key",
    "openai": "openai_api_key",
    "deepseek": "deepseek_api_key",
    "glm": "glm_api_key",
    "openrouter": "openrouter_api_key",
}


def resolve_api_key(provider: str) -> Optional[str]:
    """按「环境变量 → config/houdini_ai.ini」解析 provider 的 API key。"""
    p = (provider or "").lower()
    for env_var in _ENV_KEY_VARS.get(p, []):
        value = os.environ.get(env_var)
        if value and value.strip():
            return value.strip()
    field = _INI_KEY_FIELDS.get(p)
    if field:
        cfg, _ = load_config("ai", dcc_type="houdini")
        value = cfg.get(field)
        if value and value.strip():
            return value.strip()
    return None


# ------------------------------------------------------------------ 磁盘缓存

# 缓存读-改-写锁：允许后台线程调用 fetch / refresh 而不互相覆盖
_CACHE_LOCK = threading.Lock()

# provider_model_ids 的记忆化缓存：key = (provider, 缓存文件路径, mtime_ns)
_PROVIDER_IDS_CACHE: Dict[Tuple[str, str, int], List[str]] = {}

# 可测试性钩子：设置后 fetch_provider_models 用 _TRANSPORT(url, headers, timeout) 代替 urllib
_TRANSPORT: Optional[Callable[[str, Dict[str, str], float], Any]] = None


def set_transport(fn: Callable[[str, Dict[str, str], float], Any]) -> None:
    """注入替代网络传输函数（测试用），签名 ``fn(url, headers, timeout) -> file-like``。"""
    global _TRANSPORT
    _TRANSPORT = fn


def reset_transport() -> None:
    """清除注入的传输函数，恢复 stdlib urllib。"""
    global _TRANSPORT
    _TRANSPORT = None


def _debug(message: str) -> None:
    # 调试输出只允许在显式开启时打印
    if os.environ.get("HAGENT_MODEL_CATALOG_DEBUG"):
        print("[model_catalog] " + message)


def default_cache_path() -> str:
    """默认缓存文件路径：<cache>/model_catalog.json（运行时缓存，不入库）。"""
    return os.path.join(get_cache_dir(), CACHE_FILE_NAME)


def load_cache(cache_path: Optional[str] = None) -> Dict[str, Any]:
    """读取缓存；缺失/损坏时安全返回空结构 ``{"version": 1, "providers": {}}``。"""
    path = cache_path or default_cache_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"version": CACHE_VERSION, "providers": {}}
    if not isinstance(data, dict) or not isinstance(data.get("providers"), dict):
        return {"version": CACHE_VERSION, "providers": {}}
    return data


def save_cache(cache: Dict[str, Any], cache_path: Optional[str] = None) -> bool:
    """先写临时文件再 ``os.replace`` 原子落盘；``OSError`` 时返回 False，不抛。"""
    path = cache_path or default_cache_path()
    tmp = path + ".tmp"
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True
    except OSError:
        return False
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def _cache_entry(cache: Dict[str, Any], provider: str) -> Dict[str, Any]:
    entry = cache.get("providers", {}).get(provider) if isinstance(cache, dict) else None
    return entry if isinstance(entry, dict) else {}


def _cache_meta(cache: Dict[str, Any], provider: str, model: str) -> Dict[str, Any]:
    meta = _cache_entry(cache, provider).get("meta")
    if not isinstance(meta, dict):
        return {}
    item = meta.get(model)
    return item if isinstance(item, dict) else {}


def _http_get(url: str, headers: Dict[str, str], timeout: float) -> Any:
    req = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(req, timeout=timeout)


def _response_status(resp: Any) -> int:
    getter = getattr(resp, "getcode", None)
    if callable(getter):
        try:
            code = getter()
        except Exception:
            code = None
        if isinstance(code, int):
            return code
    code = getattr(resp, "status", None)
    if isinstance(code, int):
        return code
    return 200


def fetch_provider_models(provider: str, timeout: float = 10.0,
                          cache_path: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
    """拉取 provider 的在线模型目录，过滤后写入缓存并返回。

    返回 ``[{"id": str, "context": int|None, "vision": bool|None}]``；
    任何失败（缺 key、网络异常、非 200、JSON 坏、data 为空、未知 provider）返回 ``None``，绝不抛异常。
    """
    source = PROVIDER_SOURCES.get(provider)
    if source is None:
        return None
    headers: Dict[str, str] = {"User-Agent": _USER_AGENT}
    api_key = resolve_api_key(source["key_provider"])
    if source["auth_required"] and not api_key:
        _debug("%s: 缺少 API key，跳过拉取" % provider)
        return None
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    try:
        transport = _TRANSPORT if _TRANSPORT is not None else _http_get
        resp = transport(source["models_url"], headers, timeout)
        if resp is None or _response_status(resp) != 200:
            _debug("%s: 非 200 响应" % provider)
            return None
        raw = resp.read()
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        payload = json.loads(raw)
        close = getattr(resp, "close", None)
        if callable(close):
            close()
    except Exception as exc:  # 网络异常 / JSON 坏 —— 一律静默失败
        _debug("%s: 拉取失败 %r" % (provider, exc))
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or not data:
        _debug("%s: data 为空" % provider)
        return None

    enrich = source["policy"] == "enrich"
    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        mid = item.get("id")
        if not isinstance(mid, str) or not mid.strip() or mid in seen:
            continue
        endpoints = item.get("supported_endpoint_types")
        if endpoints is not None and not isinstance(endpoints, list):
            endpoints = None
        if not is_chat_model(mid, endpoints):
            continue
        seen.add(mid)
        context: Optional[int] = None
        vision: Optional[bool] = None
        if enrich:
            # OpenRouter：解析 context_length 与 architecture.input_modalities
            cl = item.get("context_length")
            if isinstance(cl, int) and not isinstance(cl, bool):
                context = cl
            arch = item.get("architecture")
            modalities = arch.get("input_modalities") if isinstance(arch, dict) else None
            vision = isinstance(modalities, list) and "image" in [str(m) for m in modalities]
        out.append({"id": mid, "context": context, "vision": vision})

    # 读-改-写在锁内完成，允许后台线程并发调用
    with _CACHE_LOCK:
        cache = load_cache(cache_path)
        providers = cache.setdefault("providers", {})
        providers[provider] = {
            "fetched_at": time.time(),
            "models": [m["id"] for m in out],
            "meta": {m["id"]: {"context": m["context"], "vision": m["vision"]} for m in out},
        }
        save_cache(cache, cache_path)
    return out


def refresh_provider(provider: str, timeout: float = 10.0,
                     cache_path: Optional[str] = None) -> Optional[List[str]]:
    """拉取 → 写缓存 → 返回 merge 后的模型名列表；失败返回 ``None`` 且不动缓存。"""
    models = fetch_provider_models(provider, timeout=timeout, cache_path=cache_path)
    if models is None:
        return None
    return merged_models(provider, cache_path=cache_path)


def refresh_all(providers: Optional[Iterable[str]] = None, timeout: float = 10.0,
                cache_path: Optional[str] = None) -> Dict[str, Optional[List[str]]]:
    """逐个刷新 provider；单个失败（``None``）不影响其它。"""
    targets = list(providers) if providers is not None else list(PROVIDER_SOURCES)
    results: Dict[str, Optional[List[str]]] = {}
    for p in targets:
        try:
            results[p] = refresh_provider(p, timeout=timeout, cache_path=cache_path)
        except Exception as exc:
            _debug("%s: refresh_all 单项失败 %r" % (p, exc))
            results[p] = None
    return results


# ------------------------------------------------------------------ 合并策略（"自动拉新"的核心）

def merged_models(provider: str, cache_path: Optional[str] = None) -> List[str]:
    """静态表 + live 缓存合并后的模型名列表。

    - ``enrich``（openrouter）：始终返回静态顺序，live 数据只用于补元数据；
    - ``merge``：live 非空时 = 存活静态项（原顺序）+ 新模型（字母序）追加；否则原样返回静态表；
    - codex / claude_sub / custom（不在注册表内）：始终返回静态表。
    """
    static = STATIC_MODELS.get(provider)
    if static is None:
        return []
    source = PROVIDER_SOURCES.get(provider)
    if source is None or source["policy"] != "merge":
        return list(static)
    live = _cache_entry(load_cache(cache_path), provider).get("models")
    if not isinstance(live, list) or not live:
        return list(static)
    live_ids = [m for m in live if isinstance(m, str) and m]
    if not live_ids:
        return list(static)
    live_set = set(live_ids)
    static_set = set(static)
    kept = [m for m in static if m in live_set]
    extra = sorted(m for m in live_set if m not in static_set)
    return kept + extra


def provider_model_ids(provider: str, cache_path: Optional[str] = None) -> List[str]:
    """``merged_models`` 的记忆化版本：按（缓存路径 + mtime）缓存结果，
    供 QML 菜单频繁调用时不反复读盘。"""
    path = cache_path or default_cache_path()
    try:
        mtime_ns = os.stat(path).st_mtime_ns
    except OSError:
        mtime_ns = -1
    key = (provider, path, mtime_ns)
    hit = _PROVIDER_IDS_CACHE.get(key)
    if hit is None:
        if len(_PROVIDER_IDS_CACHE) > 64:
            _PROVIDER_IDS_CACHE.clear()
        hit = merged_models(provider, cache_path=path)
        _PROVIDER_IDS_CACHE[key] = hit
    return list(hit)


# ------------------------------------------------------------------ 元数据解析（缓存 meta → 静态表 → 家族推断）

# 家族推断：按顺序子串匹配（gpt-6 必须在 gpt-5 之前检查）
_FAMILY_CONTEXT: List[Tuple[str, int]] = [
    ("claude", 200000), ("gpt-6", 1050000), ("gpt-5", 1000000),
    ("gemini", 1048576), ("deepseek", 1048576), ("glm", 1048576),
    ("grok", 500000), ("kimi", 1048576), ("minimax", 1048576),
]
_FAMILY_VISION: Tuple[str, ...] = ("claude", "gpt", "gemini", "grok", "kimi", "minimax", "fable")
_DEFAULT_CONTEXT = 128000


def context_limit(model: str, provider: Optional[str] = None,
                  cache_path: Optional[str] = None) -> int:
    """上下文窗口：缓存 meta.context → STATIC_CONTEXT → 家族推断 → 128000。"""
    mid = str(model or "")
    if provider:
        cached = _cache_meta(load_cache(cache_path), provider, mid).get("context")
        if isinstance(cached, int) and not isinstance(cached, bool):
            return cached
    static = STATIC_CONTEXT.get(mid)
    if isinstance(static, int):
        return static
    low = mid.lower()
    for family, limit in _FAMILY_CONTEXT:
        if family in low:
            return limit
    return _DEFAULT_CONTEXT


def supports_vision(model: str, provider: Optional[str] = None,
                    cache_path: Optional[str] = None) -> bool:
    """是否支持图像输入：缓存 meta.vision → STATIC_VISION → 家族推断。"""
    mid = str(model or "")
    if provider:
        cached = _cache_meta(load_cache(cache_path), provider, mid).get("vision")
        if isinstance(cached, bool):
            return cached
    if mid in STATIC_VISION:
        return True
    low = mid.lower()
    if "deepseek" in low:
        return "vision" in low
    if "glm" in low:
        return False
    for family in _FAMILY_VISION:
        if family in low:
            return True
    return False


def model_features(model: str, provider: Optional[str] = None,
                   cache_path: Optional[str] = None) -> Dict[str, bool]:
    """模型特性（副本，调用方可安全修改）：STATIC_FEATURES 副本 + 缓存 meta.vision 覆盖。"""
    mid = str(model or "")
    cached_vision: Optional[bool] = None
    if provider:
        v = _cache_meta(load_cache(cache_path), provider, mid).get("vision")
        if isinstance(v, bool):
            cached_vision = v
    feats = STATIC_FEATURES.get(mid)
    if feats is None:
        if cached_vision is not None:
            vision = cached_vision
        else:
            vision = supports_vision(mid, provider=provider, cache_path=cache_path)
        return {"supports_prompt_caching": True, "supports_vision": vision}
    out = dict(feats)
    if cached_vision is not None:
        out["supports_vision"] = cached_vision
    return out


def catalog_age(provider: str, cache_path: Optional[str] = None) -> Optional[float]:
    """缓存距上次拉取的秒数；无缓存返回 ``None``。"""
    fetched_at = _cache_entry(load_cache(cache_path), provider).get("fetched_at")
    if isinstance(fetched_at, (int, float)) and not isinstance(fetched_at, bool):
        return max(0.0, time.time() - float(fetched_at))
    return None


def is_stale(provider: str, cache_path: Optional[str] = None) -> bool:
    """无缓存或缓存超过 TTL（6 小时）即视为过期。"""
    age = catalog_age(provider, cache_path=cache_path)
    return age is None or age > MODEL_CATALOG_TTL_SECONDS
