# -*- coding: utf-8 -*-
"""model_catalog 离线测试：过滤规则、静态表、transport 注入拉取 + 缓存、
merge/enrich 合并、元数据回退、TTL、key 解析。全程无网络、无真实 config 写入。"""
import json
import time

import pytest

from houdini_agent.utils import model_catalog as mc

# duojie /v1/models 实测 payload（2026-11 全集；kiro-auto 的 supported_endpoint_types
# 按实测只走 anthropic/openai，图片模型带 image-generation 端点）
DUOJIE_PAYLOAD = {
    "data": [
        {"id": "claude-opus-5-5", "object": "model", "created": 1626777600, "owned_by": "claude",
         "supported_endpoint_types": ["anthropic", "openai", "openai-response",
                                      "openai-response-compact", "gemini", "openai-alpha-search"]},
        {"id": "claude-sonnet-5-5", "object": "model", "created": 1626777600, "owned_by": "claude",
         "supported_endpoint_types": ["anthropic", "openai"]},
        {"id": "kiro-auto", "object": "model", "created": 1626777600, "owned_by": "claude",
         "supported_endpoint_types": ["anthropic", "openai"]},
        {"id": "gpt-image-2.5-sunburst", "object": "model", "created": 1626777600, "owned_by": "openai",
         "supported_endpoint_types": ["image-generation", "openai"]},
        {"id": "gpt-6.1-sol", "object": "model", "created": 1626777600, "owned_by": "openai",
         "supported_endpoint_types": ["openai"]},
        {"id": "gpt-6-sol", "object": "model", "created": 1626777600, "owned_by": "openai",
         "supported_endpoint_types": ["openai"]},
        {"id": "glm-5.3", "object": "model", "created": 1626777600, "owned_by": "glm",
         "supported_endpoint_types": ["openai"]},
        {"id": "gpt-image-2.5", "object": "model", "created": 1626777600, "owned_by": "openai",
         "supported_endpoint_types": ["image-generation", "openai"]},
        {"id": "glm-5.3-flashx", "object": "model", "created": 1626777600, "owned_by": "glm",
         "supported_endpoint_types": ["openai"]},
        {"id": "gpt-image-2.5-flare", "object": "model", "created": 1626777600, "owned_by": "openai",
         "supported_endpoint_types": ["image-generation", "openai"]},
        {"id": "gpt-6-astra", "object": "model", "created": 1626777600, "owned_by": "openai",
         "supported_endpoint_types": ["openai"]},
        {"id": "claude-sonnet-5", "object": "model", "created": 1626777600, "owned_by": "claude",
         "supported_endpoint_types": ["anthropic", "openai"]},
        {"id": "grok-4.7", "object": "model", "created": 1626777600, "owned_by": "xai",
         "supported_endpoint_types": ["openai"]},
        {"id": "glm-5.3-flash", "object": "model", "created": 1626777600, "owned_by": "glm",
         "supported_endpoint_types": ["openai"]},
    ],
    "object": "list",
}


class _FakeResponse:
    """urllib 响应的最小桩。status=200 时返回 payload JSON，否则模拟 HTTP 错误对象。"""

    def __init__(self, payload=None, status=200):
        self._payload = payload
        self.status = status

    def getcode(self):
        return self.status

    def read(self):
        if self._payload is None:
            return b""
        return json.dumps(self._payload).encode("utf-8")

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _isolated_catalog_env(monkeypatch):
    """每个用例重置 transport，并注入假 API Key。

    不能依赖本机环境变量：CI（或干净机器）没有 DUOJIE_API_KEY，
    fetch_provider_models 会在鉴权检查处提前返回 None，导致 transport 注入失效。
    """
    mc.reset_transport()
    for var in ("DUOJIE_API_KEY", "DCC_AI_DUOJIE_API_KEY", "OPENAI_API_KEY",
                "DEEPSEEK_API_KEY", "GLM_API_KEY", "ZHIPU_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    for var in ("DUOJIE_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY",
                "GLM_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.setenv(var, "test-key")
    yield
    mc.reset_transport()


def _cache_file(tmp_path):
    return str(tmp_path / "model_catalog.json")


def _set_transport_response(payload=None, status=200):
    calls = []

    def fake(url, headers, timeout):
        calls.append({"url": url, "headers": dict(headers), "timeout": timeout})
        if status == -1:
            raise OSError("network down")
        return _FakeResponse(payload=payload, status=status)

    mc.set_transport(fake)
    return calls


# ---------------------------------------------------------------- 1. is_chat_model 家族过滤

@pytest.mark.parametrize("mid", ["glm-5.3-flash", "glm-5.3-flashx", "deepseek-v4-flash-vision-exp"])
def test_is_chat_model_keeps_flash_family(mid):
    # "flash" 不能被子串匹配误伤
    assert mc.is_chat_model(mid) is True


@pytest.mark.parametrize("mid", ["gpt-image-2.5", "gpt-image-2.5-sunburst", "text-embedding-3-large",
                                 "whisper-1", "dall-e-3", "foo/batch:batch"])
def test_is_chat_model_rejects_non_chat(mid):
    assert mc.is_chat_model(mid) is False


def test_is_chat_model_empty_and_blank():
    assert mc.is_chat_model("") is False
    assert mc.is_chat_model("   ") is False


def test_is_chat_model_variant_suffixes():
    assert mc.is_chat_model("openai/gpt-5.6-sol:batch") is False
    assert mc.is_chat_model("openai/gpt-5.6-sol:thinking") is False
    assert mc.is_chat_model("deepseek/deepseek-v4.1-flash:free") is True


# ---------------------------------------------------------------- 2. endpoint types 逻辑

def test_endpoint_types_image_generation_rejected():
    # endpoint 只剩 image-generation 时即使家族词放行也被拒
    assert mc.is_chat_model("gpt-image-2.5", ["image-generation", "openai"]) is False


def test_endpoint_types_anthropic_openai_kept():
    assert mc.is_chat_model("kiro-auto", ["anthropic", "openai"]) is True


def test_endpoint_types_no_intersection_rejected():
    assert mc.is_chat_model("kiro-auto", ["image-generation"]) is False
    assert mc.is_chat_model("kiro-auto", ["openai-alpha-search"]) is False


# ---------------------------------------------------------------- 3. 静态表内容

def test_static_dujie_updated():
    models = mc.STATIC_MODELS["duojie"]
    assert "kiro-auto" in models
    assert "glm-5.3-flashx" in models
    assert "kimi-k3" not in models
    assert "deepseek-v4-pro" not in models
    assert "deepseek-v4.1-flash" not in models


def test_static_tables_keys_and_new_entries():
    assert set(mc.STATIC_MODELS) == {"duojie", "openrouter", "openai", "codex",
                                     "claude_sub", "deepseek", "glm", "custom"}
    assert mc.STATIC_MODELS["custom"] == []
    assert mc.STATIC_CONTEXT["glm-5.3-flashx"] == 1048576
    assert mc.STATIC_CONTEXT["kiro-auto"] == 200000
    assert "glm-5.3-flashx" in mc.STATIC_VISION
    assert "kiro-auto" in mc.STATIC_VISION
    feats = mc.STATIC_FEATURES["kiro-auto"]
    assert feats == {"supports_prompt_caching": True, "supports_vision": True}


def test_provider_sources_registry():
    assert set(mc.PROVIDER_SOURCES) == {"duojie", "openrouter", "openai", "deepseek", "glm"}
    assert mc.PROVIDER_SOURCES["openrouter"]["policy"] == "enrich"
    assert mc.PROVIDER_SOURCES["openrouter"]["auth_required"] is False
    assert mc.PROVIDER_SOURCES["duojie"]["auth_required"] is True
    for p in ("duojie", "openai", "deepseek", "glm"):
        assert mc.PROVIDER_SOURCES[p]["policy"] == "merge"


# ---------------------------------------------------------------- 4. transport 注入拉取 + 缓存落盘

def test_fetch_dujie_with_injected_transport(tmp_path):
    cp = _cache_file(tmp_path)
    calls = _set_transport_response(payload=DUOJIE_PAYLOAD)
    result = mc.fetch_provider_models("duojie", cache_path=cp)
    assert result is not None
    ids = [m["id"] for m in result]
    assert all("gpt-image" not in i for i in ids)      # 图片模型被过滤
    assert "glm-5.3-flashx" in ids and "kiro-auto" in ids
    assert len(ids) == 11                                # 14 条 - 3 条 gpt-image
    for m in result:
        assert set(m) == {"id", "context", "vision"}
        assert m["context"] is None and m["vision"] is None   # merge provider 不解析元数据
    # 请求头：UA + Bearer key（auth_required=True）
    assert len(calls) == 1
    assert calls[0]["headers"]["User-Agent"] == "HoudiniAgent/2.0"
    assert calls[0]["headers"]["Authorization"].startswith("Bearer ")
    assert calls[0]["url"] == "https://api.duojie.games/v1/models"
    # 缓存文件已写
    data = json.loads(tmp_path.joinpath("model_catalog.json").read_text(encoding="utf-8"))
    assert data["version"] == 1
    entry = data["providers"]["duojie"]
    assert entry["models"] == ids
    assert entry["meta"]["kiro-auto"] == {"context": None, "vision": None}
    assert entry["fetched_at"] > 0


def test_fetch_openrouter_parses_metadata(tmp_path):
    cp = _cache_file(tmp_path)
    payload = {"data": [
        {"id": "anthropic/claude-opus-5.5", "context_length": 1000000,
         "architecture": {"input_modalities": ["text", "image"]}, "pricing": {}},
        {"id": "z-ai/glm-5.3", "context_length": 1048576,
         "architecture": {"input_modalities": ["text"]}, "pricing": {}},
    ]}
    _set_transport_response(payload=payload)
    result = mc.fetch_provider_models("openrouter", cache_path=cp)
    assert result is not None
    by_id = {m["id"]: m for m in result}
    assert by_id["anthropic/claude-opus-5.5"]["context"] == 1000000
    assert by_id["anthropic/claude-opus-5.5"]["vision"] is True
    assert by_id["z-ai/glm-5.3"]["vision"] is False


# ---------------------------------------------------------------- 5. refresh_provider 合并顺序

def test_refresh_provider_merge_order(tmp_path):
    cp = _cache_file(tmp_path)
    _set_transport_response(payload=DUOJIE_PAYLOAD)
    merged = mc.refresh_provider("duojie", cache_path=cp)
    assert merged is not None
    # 存活 static 项保持原顺序在前
    expected_kept = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-sonnet-5",
                     "gpt-6.1-sol", "gpt-6-sol", "gpt-6-astra",
                     "grok-4.7", "glm-5.3", "glm-5.3-flash", "glm-5.3-flashx", "kiro-auto"]
    assert merged == expected_kept        # 本例 live == static，无追加项；下线项已被剔除
    assert "kimi-k3" not in merged and "deepseek-v4-pro" not in merged


def test_refresh_provider_new_models_sorted_appended(tmp_path):
    cp = _cache_file(tmp_path)
    payload = {"data": [
        {"id": "glm-5.3-flash"}, {"id": "brand-new-b"}, {"id": "brand-new-a"}, {"id": "glm-5.3"},
    ]}
    _set_transport_response(payload=payload)
    merged = mc.refresh_provider("duojie", cache_path=cp)
    # static 中存活的按原顺序（glm-5.3 在 glm-5.3-flash 前），新模型排序后追加
    assert merged == ["glm-5.3", "glm-5.3-flash", "brand-new-a", "brand-new-b"]


# ---------------------------------------------------------------- 6. 失败路径

def test_fetch_transport_exception_returns_none_and_cache_intact(tmp_path):
    cp = _cache_file(tmp_path)
    # 先成功拉一次，落一份缓存
    _set_transport_response(payload=DUOJIE_PAYLOAD)
    assert mc.fetch_provider_models("duojie", cache_path=cp) is not None
    before = tmp_path.joinpath("model_catalog.json").read_text(encoding="utf-8")
    # 再让 transport 抛异常
    _set_transport_response(status=-1)
    assert mc.fetch_provider_models("duojie", cache_path=cp) is None
    assert mc.refresh_provider("duojie", cache_path=cp) is None
    assert tmp_path.joinpath("model_catalog.json").read_text(encoding="utf-8") == before


def test_fetch_401_returns_none(tmp_path):
    cp = _cache_file(tmp_path)
    _set_transport_response(payload={"error": "unauthorized"}, status=401)
    assert mc.fetch_provider_models("duojie", cache_path=cp) is None
    assert not tmp_path.joinpath("model_catalog.json").exists()


def test_fetch_empty_data_returns_none_and_keeps_cache(tmp_path):
    cp = _cache_file(tmp_path)
    _set_transport_response(payload=DUOJIE_PAYLOAD)
    assert mc.fetch_provider_models("duojie", cache_path=cp) is not None
    before = tmp_path.joinpath("model_catalog.json").read_text(encoding="utf-8")
    _set_transport_response(payload={"data": []})
    assert mc.fetch_provider_models("duojie", cache_path=cp) is None
    assert tmp_path.joinpath("model_catalog.json").read_text(encoding="utf-8") == before


def test_fetch_unknown_provider_returns_none(tmp_path):
    assert mc.fetch_provider_models("codex", cache_path=_cache_file(tmp_path)) is None
    assert mc.fetch_provider_models("nope", cache_path=_cache_file(tmp_path)) is None


def test_missing_key_aborts_without_network(tmp_path):
    cp = _cache_file(tmp_path)
    calls = _set_transport_response(payload=DUOJIE_PAYLOAD)

    def no_key(provider):
        return None

    orig = mc.resolve_api_key
    mc.resolve_api_key = no_key
    try:
        assert mc.fetch_provider_models("duojie", cache_path=cp) is None
        assert calls == []                       # 缺 key 时不发请求
    finally:
        mc.resolve_api_key = orig


# ---------------------------------------------------------------- 7. merged_models 回退 / enrich

def test_merged_models_fallback_to_static(tmp_path):
    assert mc.merged_models("duojie", cache_path=_cache_file(tmp_path)) == mc.STATIC_MODELS["duojie"]
    # codex / claude_sub / custom 不做网络合并，始终静态
    assert mc.merged_models("codex", cache_path=_cache_file(tmp_path)) == mc.STATIC_MODELS["codex"]
    assert mc.merged_models("claude_sub", cache_path=_cache_file(tmp_path)) == mc.STATIC_MODELS["claude_sub"]
    assert mc.merged_models("custom", cache_path=_cache_file(tmp_path)) == []


def test_merged_models_openrouter_enrich_keeps_static_order(tmp_path, monkeypatch):
    cp = _cache_file(tmp_path)
    payload = {"data": [
        {"id": "z-ai/glm-5.3", "context_length": 1048576, "architecture": {"input_modalities": ["text"]}},
    ]}
    for i in range(20):   # live 里塞一堆静态清单外的变体
        payload["data"].append({"id": "vendor/model-%d" % i, "context_length": 65536,
                                "architecture": {"input_modalities": ["text"]}})
    _set_transport_response(payload=payload)
    assert mc.fetch_provider_models("openrouter", cache_path=cp) is not None
    # enrich：无论 live 有多少条，始终返回 STATIC 顺序
    assert mc.merged_models("openrouter", cache_path=cp) == mc.STATIC_MODELS["openrouter"]
    # 元数据仍然生效
    assert mc.context_limit("vendor/model-0", "openrouter", cache_path=cp) == 65536
    # OpenRouter 公开接口不需要 key 也能拉（autouse 夹具注入了假 key，这里清掉）
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    calls = _set_transport_response(payload=payload)
    assert mc.fetch_provider_models("openrouter", cache_path=cp) is not None
    assert "Authorization" not in calls[0]["headers"]


def test_provider_model_ids_reads_through_memo(tmp_path):
    cp = _cache_file(tmp_path)
    assert mc.provider_model_ids("duojie", cache_path=cp) == mc.STATIC_MODELS["duojie"]
    _set_transport_response(payload=DUOJIE_PAYLOAD)
    mc.fetch_provider_models("duojie", cache_path=cp)
    # mtime 变了 → 记忆化失效 → 读到新 merge 结果
    assert mc.provider_model_ids("duojie", cache_path=cp) == mc.merged_models("duojie", cache_path=cp)
    assert mc.provider_model_ids("openrouter", cache_path=cp) == mc.STATIC_MODELS["openrouter"]


# ---------------------------------------------------------------- 8. 元数据优先级

def test_metadata_priorities(tmp_path):
    cp = _cache_file(tmp_path)
    _set_transport_response(payload=DUOJIE_PAYLOAD)
    mc.fetch_provider_models("duojie", cache_path=cp)
    # static 命中
    assert mc.context_limit("kiro-auto") == 200000
    assert mc.context_limit("glm-5.3-flashx") == 1048576
    # 家族推断
    assert mc.context_limit("claude-x") == 200000
    assert mc.context_limit("gemini-9") == 1048576
    assert mc.context_limit("gpt-6-turbo") == 1050000
    assert mc.context_limit("gpt-5-mini") == 1000000
    assert mc.context_limit("grok-9") == 500000
    assert mc.context_limit("kimi-k9") == 1048576
    assert mc.context_limit("minimax-m9") == 1048576
    # 兜底
    assert mc.context_limit("zzz") == 128000
    assert mc.supports_vision("zzz") is False
    # 家族 vision 推断
    assert mc.supports_vision("gemini-9") is True
    assert mc.supports_vision("claude-x") is True
    assert mc.supports_vision("gpt-7") is True
    assert mc.supports_vision("grok-9") is True
    assert mc.supports_vision("kimi-k9") is True
    assert mc.supports_vision("minimax-m9") is True
    assert mc.supports_vision("vendor/claude-fable-9") is True     # fable
    assert mc.supports_vision("deepseek-v9") is False              # deepseek 默认无视觉
    assert mc.supports_vision("deepseek-v4-flash-vision-exp") is True   # id 含 vision 才 True
    assert mc.supports_vision("glm-9.9") is False                  # glm 家族 False
    assert mc.supports_vision("glm-5.3-flashx") is True            # STATIC_VISION 命中


def test_metadata_cache_overrides_static(tmp_path):
    cp = _cache_file(tmp_path)
    _set_transport_response(payload=DUOJIE_PAYLOAD)
    mc.fetch_provider_models("duojie", cache_path=cp)
    # openrouter enrich 写 context 元数据后可覆盖 static
    payload = {"data": [{"id": "x-ai/grok-4.7", "context_length": 123456,
                         "architecture": {"input_modalities": ["text"]}}]}
    _set_transport_response(payload=payload)
    mc.fetch_provider_models("openrouter", cache_path=cp)
    assert mc.context_limit("x-ai/grok-4.7", "openrouter", cache_path=cp) == 123456
    assert mc.supports_vision("x-ai/grok-4.7", "openrouter", cache_path=cp) is False


def test_model_features(tmp_path):
    cp = _cache_file(tmp_path)
    # 有静态项：副本 + 不被调用方修改影响
    feats = mc.model_features("claude-opus-5-5", cache_path=cp)
    assert feats == {"supports_prompt_caching": True, "supports_vision": True}
    feats["supports_vision"] = False
    assert mc.STATIC_FEATURES["claude-opus-5-5"]["supports_vision"] is True
    # 无静态项：回退 supports_vision 推断
    assert mc.model_features("gemini-9", cache_path=cp) == \
        {"supports_prompt_caching": True, "supports_vision": True}
    assert mc.model_features("zzz", cache_path=cp) == \
        {"supports_prompt_caching": True, "supports_vision": False}


# ---------------------------------------------------------------- 9. TTL

def test_ttl_fresh_and_stale(tmp_path):
    cp = _cache_file(tmp_path)
    assert mc.catalog_age("duojie", cache_path=cp) is None
    assert mc.is_stale("duojie", cache_path=cp) is True      # 无缓存即过期
    _set_transport_response(payload=DUOJIE_PAYLOAD)
    mc.fetch_provider_models("duojie", cache_path=cp)
    age = mc.catalog_age("duojie", cache_path=cp)
    assert age is not None and age < 60
    assert mc.is_stale("duojie", cache_path=cp) is False
    # 手写旧 fetched_at → 过期
    data = json.loads(tmp_path.joinpath("model_catalog.json").read_text(encoding="utf-8"))
    data["providers"]["duojie"]["fetched_at"] = time.time() - mc.MODEL_CATALOG_TTL_SECONDS - 300
    tmp_path.joinpath("model_catalog.json").write_text(
        json.dumps(data), encoding="utf-8")
    assert mc.is_stale("duojie", cache_path=cp) is True


# ---------------------------------------------------------------- 10. resolve_api_key 环境变量优先

def test_resolve_api_key_env_priority(monkeypatch):
    monkeypatch.setenv("DUOJIE_API_KEY", "env-duojie-key")
    assert mc.resolve_api_key("duojie") == "env-duojie-key"
    monkeypatch.setenv("DCC_AI_DUOJIE_API_KEY", "env2-duojie-key")
    assert mc.resolve_api_key("duojie") == "env-duojie-key"     # 第一个 env 变量优先


def test_resolve_api_key_multi_env_and_ini_fallback(monkeypatch, isolated_paths):
    # 环境变量兜底链 + ini 回退（conftest 已把 config 目录重定向到 tmp）
    from shared import common_utils as cu
    monkeypatch.delenv("DUOJIE_API_KEY", raising=False)
    monkeypatch.delenv("DCC_AI_DUOJIE_API_KEY", raising=False)
    monkeypatch.setenv("DCC_AI_DUOJIE_API_KEY", "second-env-key")
    assert mc.resolve_api_key("duojie") == "second-env-key"
    monkeypatch.delenv("DCC_AI_DUOJIE_API_KEY", raising=False)
    cu.save_config("ai", {"duojie_api_key": "ini-duojie-key"}, dcc_type="houdini")
    assert mc.resolve_api_key("duojie") == "ini-duojie-key"
    # glm 的 ZHIPU_API_KEY 别名（autouse 夹具注入了 GLM_API_KEY，这里清掉才能测到别名）
    monkeypatch.delenv("GLM_API_KEY", raising=False)
    monkeypatch.setenv("ZHIPU_API_KEY", "zhipu-key")
    assert mc.resolve_api_key("glm") == "zhipu-key"


def test_resolve_api_key_none_when_missing(monkeypatch, isolated_paths):
    for var in ("DUOJIE_API_KEY", "DCC_AI_DUOJIE_API_KEY", "GLM_API_KEY", "ZHIPU_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert mc.resolve_api_key("glm") is None


# ---------------------------------------------------------------- 缓存健壮性

def test_load_cache_corrupt_file(tmp_path):
    cp = tmp_path / "model_catalog.json"
    cp.write_text("{not json", encoding="utf-8")
    assert mc.load_cache(str(cp)) == {"version": 1, "providers": {}}
    assert mc.load_cache(str(tmp_path / "missing.json")) == {"version": 1, "providers": {}}


def test_save_cache_roundtrip(tmp_path):
    cp = _cache_file(tmp_path)
    cache = {"version": 1, "providers": {"duojie": {"fetched_at": 1.0, "models": ["a"], "meta": {}}}}
    assert mc.save_cache(cache, cache_path=cp) is True
    assert mc.load_cache(cp) == cache
