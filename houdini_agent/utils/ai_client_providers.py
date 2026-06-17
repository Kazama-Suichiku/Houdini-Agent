# -*- coding: utf-8 -*-
import os
import sys
import ssl
import json
from typing import List, Dict, Optional, Any

from shared.common_utils import load_config, save_config

# 强制使用本地 lib 目录中的依赖库（与 ai_client.py 一致，保证 mixin 模块自洽）
_lib_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'lib')
if os.path.exists(_lib_path):
    if _lib_path in sys.path:
        sys.path.remove(_lib_path)
    sys.path.insert(0, _lib_path)

# 导入 requests（这些方法原属 ai_client.py，依赖模块级 requests / HAS_REQUESTS）
HAS_REQUESTS = False
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    pass


class AIClientProvidersMixin:
    """API provider management: keys, URLs, model selection, usage parsing."""

    OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
    DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
    GLM_API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    DUOJIE_API_URL = "https://api.duojie.games/v1/chat/completions"  # 拼好饭中转站（OpenAI 协议）
    DUOJIE_ANTHROPIC_API_URL = "https://api.duojie.games/v1/messages"  # 拼好饭中转站（Anthropic 协议）
    OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"  # OpenRouter（OpenAI 兼容）

    # 使用 Anthropic 协议的 Duojie 模型（GLM 系等）
    _DUOJIE_ANTHROPIC_MODELS = frozenset({'glm-4.7', 'glm-5', 'glm-5-turbo', 'glm-5.1'})

    # Custom provider 运行时配置
    _CUSTOM_API_URL: str = ''
    _CUSTOM_SUPPORTS_FC: bool = True
    _CUSTOM_ANTHROPIC_PROTOCOL: bool = False

    _usage_keys_logged = False  # 类变量：只打印一次原始 usage 完整结构

    def _create_ssl_context(self):
        """创建 SSL 上下文。验证失败时回退到未验证模式（带警告）。"""
        try:
            context = ssl.create_default_context()
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            return context
        except Exception as e:
            print(f"[AI Client] ⚠️ SSL 证书验证失败 ({e})，回退到未验证模式。这可能存在安全风险。")
            try:
                return ssl._create_unverified_context()
            except Exception:
                return None

    def _read_api_key(self, provider: str) -> Optional[str]:
        provider = (provider or 'openai').lower()

        env_map = {
            'openai': ['OPENAI_API_KEY', 'DCC_AI_OPENAI_API_KEY'],
            'deepseek': ['DEEPSEEK_API_KEY', 'DCC_AI_DEEPSEEK_API_KEY'],
            'glm': ['GLM_API_KEY', 'ZHIPU_API_KEY', 'DCC_AI_GLM_API_KEY'],
            'duojie': ['DUOJIE_API_KEY', 'DCC_AI_DUOJIE_API_KEY'],
            'openrouter': ['OPENROUTER_API_KEY', 'DCC_AI_OPENROUTER_API_KEY'],
            'custom': ['CUSTOM_API_KEY', 'DCC_AI_CUSTOM_API_KEY'],
        }
        for env_var in env_map.get(provider, []):
            key = os.environ.get(env_var)
            if key:
                return key
        cfg, _ = load_config('ai', dcc_type='houdini')
        if cfg:
            key_map = {
                'openai': 'openai_api_key', 'deepseek': 'deepseek_api_key',
                'glm': 'glm_api_key', 'duojie': 'duojie_api_key',
                'openrouter': 'openrouter_api_key',
                'custom': 'custom_api_key',
            }
            return cfg.get(key_map.get(provider, '')) or None
        return None

    def has_api_key(self, provider: str = 'openai') -> bool:
        provider = (provider or 'openai').lower()
        # Custom: 只要配置了 URL 就算可用（Key 可选）
        if provider == 'custom':
            return bool(self._CUSTOM_API_URL)
        return bool(self._api_keys.get(provider))

    def _get_api_key(self, provider: str) -> Optional[str]:
        return self._api_keys.get((provider or 'openai').lower())

    def set_api_key(self, key: str, persist: bool = False, provider: str = 'openai') -> bool:
        provider = (provider or 'openai').lower()
        key = (key or '').strip()
        if not key:
            return False
        self._api_keys[provider] = key
        if persist:
            cfg, _ = load_config('ai', dcc_type='houdini')
            cfg = cfg or {}
            key_map = {'openai': 'openai_api_key', 'deepseek': 'deepseek_api_key', 'glm': 'glm_api_key',
                       'openrouter': 'openrouter_api_key', 'custom': 'custom_api_key'}
            cfg[key_map.get(provider, f'{provider}_api_key')] = key
            ok, _ = save_config('ai', cfg, dcc_type='houdini')
            return ok
        return True

    def get_masked_key(self, provider: str = 'openai') -> str:
        provider = (provider or 'openai').lower()
        # Custom: 显示 URL 缩略
        if provider == 'custom':
            if self._CUSTOM_API_URL:
                url = self._CUSTOM_API_URL
                # 提取域名部分作为显示
                try:
                    from urllib.parse import urlparse
                    parsed = urlparse(url)
                    host = parsed.hostname or url[:20]
                    return host[:16] + ('...' if len(host) > 16 else '')
                except Exception:
                    return url[:16] + '...'
            return 'Not Set'
        key = self._get_api_key(provider)
        if not key:
            return ''
        if len(key) <= 10:
            return '*' * len(key)
        return key[:5] + '...' + key[-4:]

    def _is_anthropic_protocol(self, provider: str, model: str) -> bool:
        """判断是否应使用 Anthropic Messages 协议（而非 OpenAI 协议）"""
        if provider == 'custom':
            return bool(self._CUSTOM_ANTHROPIC_PROTOCOL)
        return provider == 'duojie' and model.lower() in self._DUOJIE_ANTHROPIC_MODELS

    @staticmethod
    def _build_image_block(img_b64: str, img_mt: str, use_anthropic: bool) -> dict:
        """根据协议返回正确格式的图片内容块。"""
        if use_anthropic:
            # Anthropic Messages: {"type":"image","source":{"type":"base64",...}}
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img_mt,
                    "data": img_b64,
                }
            }
        # OpenAI Vision: {"type":"image_url","image_url":{"url":"data:..."}}
        return {"type": "image_url", "image_url": {"url": f"data:{img_mt};base64,{img_b64}"}}

    def _normalize_chat_url(self, url: str) -> str:
        """将基础 URL 规范化为 chat completions 端点

        支持的输入格式：
          - https://integrate.api.nvidia.com/v1          -> 追加 /chat/completions
          - https://example.com/v1/chat/completions      -> 保持不变
          - http://localhost:1234/v1                      -> 追加 /chat/completions
        """
        url = url.rstrip('/')
        if url.endswith('/chat/completions'):
            return url
        if url.endswith('/v1'):
            return url + '/chat/completions'
        return url

    def _get_api_url(self, provider: str, model: str = '') -> str:
        provider = (provider or 'openai').lower()
        if provider == 'deepseek':
            return self.DEEPSEEK_API_URL
        elif provider == 'glm':
            return self.GLM_API_URL
        elif provider == 'duojie':
            if model and self._is_anthropic_protocol(provider, model):
                return self.DUOJIE_ANTHROPIC_API_URL
            return self.DUOJIE_API_URL
        elif provider == 'openrouter':
            return self.OPENROUTER_API_URL
        elif provider == 'custom':
            raw = self._CUSTOM_API_URL or self.OPENAI_API_URL
            return self._normalize_chat_url(raw)
        return self.OPENAI_API_URL

    def _get_vendor_name(self, provider: str) -> str:
        names = {
            'openai': 'OpenAI', 'deepseek': 'DeepSeek',
            'glm': 'GLM（智谱AI）',
            'duojie': '拼好饭', 'openrouter': 'OpenRouter',
            'custom': 'Custom',
        }
        return names.get(provider, provider)

    def set_custom_provider(self, api_url: str, api_key: str = '', supports_fc: bool = True,
                            anthropic_protocol: bool = False):
        """设置 Custom Provider 的运行时配置

        Args:
            api_url: API 端点 URL（OpenAI 兼容或 Anthropic Messages）
            api_key: API Key（可为空）
            supports_fc: 是否支持原生 Function Calling
            anthropic_protocol: True 则使用 Anthropic Messages 格式，False 使用 OpenAI Chat Completions 格式
        """
        self._CUSTOM_API_URL = api_url.strip()
        self._CUSTOM_SUPPORTS_FC = supports_fc
        self._CUSTOM_ANTHROPIC_PROTOCOL = bool(anthropic_protocol)
        if api_key:
            self._api_keys['custom'] = api_key.strip()

    def get_custom_models(self, api_url: str, api_key: str = '') -> List[str]:
        """获取 Custom Provider 可用的模型列表（通过 OpenAI 兼容的 /v1/models 端点）

        Args:
            api_url: 用户配置的 API URL（可以是基础 URL 或完整 chat/completions URL）
            api_key: API Key（可为空）

        Returns:
            模型 ID 列表，失败时返回空列表
        """
        if not HAS_REQUESTS:
            return []

        base = api_url.rstrip('/')
        if base.endswith('/chat/completions'):
            base = base[:-len('/chat/completions')]
        models_url = base + '/models'

        headers = {'Content-Type': 'application/json'}
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'

        try:
            resp = self._http_session.get(models_url, headers=headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                return [m.get('id', '') for m in data.get('data', []) if m.get('id')]
        except Exception:
            pass

        return []

    def test_connection(self, provider: str = 'deepseek') -> Dict[str, Any]:
        """测试连接"""
        provider = (provider or 'deepseek').lower()

        api_key = self._get_api_key(provider)
        # Custom provider 允许无 API Key（本地服务等）
        if not api_key and provider != 'custom':
            return {'ok': False, 'error': f'缺少 API Key'}

        try:
            if HAS_REQUESTS:
                headers = {'Content-Type': 'application/json'}
                if api_key:
                    headers['Authorization'] = f'Bearer {api_key}'
                response = self._http_session.post(
                    self._get_api_url(provider),
                    json={'model': self._get_default_model(provider), 'messages': [{'role': 'user', 'content': 'hi'}], 'max_tokens': 1},
                    headers=headers,
                    timeout=15,
                    proxies={'http': None, 'https': None}
                )
                return {'ok': True, 'url': self._get_api_url(provider), 'status': response.status_code}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def _get_default_model(self, provider: str) -> str:
        defaults = {
            'openai': 'gpt-5.2',
            'deepseek': 'deepseek-v4-flash',
            'glm': 'glm-4.7',
            'openrouter': 'anthropic/claude-sonnet-4.6',
        }
        return defaults.get(provider, 'gpt-5.2')

    @staticmethod
    def is_reasoning_model(model: str) -> bool:
        """判断模型是否为原生推理模型（API 返回 reasoning_content 字段）

        仅限明确通过 reasoning_content 字段返回推理的模型：
        DeepSeek V4 (flash/pro), DeepSeek-R1/Reasoner, GLM-4.7
        注：Duojie 模型思考模式通过系统提示词 <think> 标签实现，不依赖 API 参数
        """
        m = model.lower()
        return (
            'deepseek-v4' in m
            or 'reasoner' in m or 'r1' in m
            or m == 'glm-4.7'
        )

    @staticmethod
    def is_glm47(model: str) -> bool:
        """判断是否为 GLM-4.7 模型"""
        return model.lower() == 'glm-4.7'

    @staticmethod
    def _parse_usage(usage: dict) -> dict:
        """解析 API 返回的 usage 数据为统一格式（含 reasoning tokens 和缓存指标）

        缓存字段兼容多种 API 返回格式：
        - DeepSeek/OpenAI: prompt_cache_hit_tokens / prompt_cache_miss_tokens
        - Anthropic 原生: cache_read_input_tokens / cache_creation_input_tokens
        - Factory/Duojie 代理: claude_cache_creation_*_tokens, input_tokens_details 内嵌
        """
        if not usage:
            return {}

        # 诊断：首次收到 usage 时打印完整结构（含嵌套 details）
        if not AIClientProvidersMixin._usage_keys_logged:
            AIClientProvidersMixin._usage_keys_logged = True
            print(f"[AI Client] Raw usage keys (首次): {sorted(usage.keys())}")
            for k in ('input_tokens_details', 'prompt_tokens_details', 'completion_tokens_details'):
                v = usage.get(k)
                if v:
                    print(f"[AI Client]   {k}: {v}")

        prompt_tokens = usage.get('prompt_tokens', 0) or usage.get('input_tokens', 0)

        # ── 缓存读取（hit）：从多级来源查找 ──
        # 优先从 details 子字段中提取（Factory/Anthropic 风格）
        input_details = usage.get('input_tokens_details') or usage.get('prompt_tokens_details') or {}
        if isinstance(input_details, dict):
            cache_hit = (
                input_details.get('cached_tokens')           # OpenAI 新格式
                or input_details.get('cache_read_input_tokens')  # Anthropic
                or input_details.get('cache_read_tokens')
                or 0
            )
        else:
            cache_hit = 0
        # 顶级字段后备
        if not cache_hit:
            cache_hit = (
                usage.get('prompt_cache_hit_tokens')
                or usage.get('cache_read_input_tokens')
                or usage.get('cache_read_tokens')
                or usage.get('cache_hit_tokens')
                or 0
            )

        # ── 缓存写入（miss/creation） ──
        # Factory 特有: claude_cache_creation_1_h_tokens / claude_cache_creation_5_m_tokens
        cache_write_1h = usage.get('claude_cache_creation_1_h_tokens', 0) or 0
        cache_write_5m = usage.get('claude_cache_creation_5_m_tokens', 0) or 0
        factory_cache_write = cache_write_1h + cache_write_5m

        if isinstance(input_details, dict):
            cache_miss_from_details = (
                input_details.get('cache_creation_input_tokens')
                or input_details.get('cache_creation_tokens')
                or 0
            )
        else:
            cache_miss_from_details = 0

        cache_miss = (
            cache_miss_from_details
            or usage.get('prompt_cache_miss_tokens')
            or usage.get('cache_creation_input_tokens')
            or usage.get('cache_write_tokens')
            or usage.get('cache_miss_tokens')
            or factory_cache_write
            or 0
        )

        completion = usage.get('completion_tokens', 0) or usage.get('output_tokens', 0)
        total = usage.get('total_tokens', 0) or (prompt_tokens + completion)

        # ── 提取 reasoning / thinking tokens ──
        # OpenAI/DeepSeek: completion_tokens_details.reasoning_tokens
        # Anthropic: 可能在 output_tokens_details.thinking 中
        reasoning_tokens = 0
        comp_details = usage.get('completion_tokens_details') or {}
        if isinstance(comp_details, dict):
            reasoning_tokens = comp_details.get('reasoning_tokens', 0) or 0
        if not reasoning_tokens:
            reasoning_tokens = usage.get('reasoning_tokens', 0) or 0

        return {
            'prompt_tokens': prompt_tokens,
            'completion_tokens': completion,
            'reasoning_tokens': reasoning_tokens,
            'total_tokens': total,
            'cache_hit_tokens': cache_hit,
            'cache_miss_tokens': cache_miss,
            'cache_hit_rate': (cache_hit / prompt_tokens) if prompt_tokens > 0 else 0,
        }
