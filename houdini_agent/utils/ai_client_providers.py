# -*- coding: utf-8 -*-
import os
import sys
import ssl
import json
import re
from typing import List, Dict, Optional, Any
from urllib.parse import urlsplit, urlunsplit

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


# ============================================================
# Custom provider URL 规范化辅助（模块级，供本 mixin 内方法调用）
# 与 ai_client.py 中同名函数保持一致；放在此处避免循环导入。
# ============================================================

_CUSTOM_PROVIDER_ENDPOINTS = (
    '/chat/completions',
    '/messages',
    '/models',
)


def _custom_provider_base_path(path: str) -> str:
    base = (path or '').rstrip('/')
    lower_base = base.lower()
    for endpoint in _CUSTOM_PROVIDER_ENDPOINTS:
        if lower_base.endswith(endpoint):
            return base[:-len(endpoint)].rstrip('/')
    return base


def _join_endpoint(base_path: str, endpoint: str) -> str:
    base = _custom_provider_base_path(base_path)
    return f"{base}{endpoint}" if base else endpoint


def normalize_custom_chat_url(api_url: str) -> str:
    """Accept either an OpenAI-compatible base URL or a full chat endpoint."""
    raw = (api_url or '').strip()
    if not raw:
        return ''

    parts = urlsplit(raw)
    if not parts.scheme or not parts.netloc:
        return raw.rstrip('/')

    path = _join_endpoint(parts.path, '/chat/completions')

    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def normalize_custom_messages_url(api_url: str) -> str:
    """Accept either an Anthropic base URL or a full Messages endpoint."""
    raw = (api_url or '').strip()
    if not raw:
        return ''

    parts = urlsplit(raw)
    if not parts.scheme or not parts.netloc:
        return raw.rstrip('/')

    path = _join_endpoint(parts.path, '/messages')

    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def normalize_custom_models_url(api_url: str) -> str:
    """Return the OpenAI-compatible models endpoint for a custom provider URL."""
    raw = (api_url or '').strip()
    if not raw:
        return ''

    parts = urlsplit(raw)
    if not parts.scheme or not parts.netloc:
        base = _custom_provider_base_path(raw)
        return f"{base}/models" if base else 'models'

    path = _join_endpoint(parts.path, '/models')
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def normalize_custom_anthropic_models_url(api_url: str) -> str:
    """Return the Anthropic-compatible models endpoint for a custom provider URL."""
    raw = (api_url or '').strip()
    if not raw:
        return ''

    parts = urlsplit(raw)
    if not parts.scheme or not parts.netloc:
        base = _custom_provider_base_path(raw)
        return f"{base}/models" if base else 'models'

    path = _join_endpoint(parts.path, '/models')
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


class AIClientProvidersMixin:
    """API provider management: keys, URLs, model selection, usage parsing."""

    OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
    DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
    GLM_API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    OLLAMA_API_URL = "http://localhost:11434/v1/chat/completions"  # Ollama OpenAI 兼容接口
    DUOJIE_API_URL = "https://api.duojie.games/v1/chat/completions"  # 拼好饭中转站（OpenAI 协议）
    DUOJIE_ANTHROPIC_API_URL = "https://api.duojie.games/v1/messages"  # 拼好饭中转站（Anthropic 协议）
    OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"  # OpenRouter（OpenAI 兼容）

    # 使用 Anthropic 协议的 Duojie 模型（GLM 系等）
    _DUOJIE_ANTHROPIC_MODELS = frozenset({'glm-4.7', 'glm-5', 'glm-5-turbo', 'glm-5.1'})

    # Custom provider 运行时配置
    _CUSTOM_API_URL: str = ''
    _CUSTOM_SUPPORTS_FC: bool = True
    _CUSTOM_PROFILES: List[Dict[str, Any]] = []
    _CUSTOM_MODEL_ROUTES: Dict[str, Dict[str, Any]] = {}
    _CUSTOM_MODEL_NAMES: Dict[str, str] = {}

    _usage_keys_logged = False  # 类变量：只打印一次原始 usage 完整结构

    @staticmethod
    def _json_body(payload: Dict[str, Any]) -> bytes:
        """Serialize request JSON as UTF-8 bytes to avoid Windows locale encoders."""
        return json.dumps(payload, ensure_ascii=False).encode('utf-8')

    def set_retry_limit(self, retries: int):
        """Set user-configurable network/server retry limit."""
        retries = self.clamp_retry_limit(retries)
        self._max_retries = retries
        self._server_error_max_retries = retries

    def retry_limit(self) -> int:
        return self._server_error_max_retries

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

        # Ollama 不需要 API key
        if provider == 'ollama':
            return 'ollama'

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
        # Ollama 总是可用（本地服务）
        if provider == 'ollama':
            return True
        # Custom: 只要配置了 URL 就算可用（Key 可选）
        if provider == 'custom':
            return bool(self._CUSTOM_API_URL or self._CUSTOM_PROFILES)
        return bool(self._api_keys.get(provider))

    def _get_custom_profile(self, model: str = '') -> Dict[str, Any]:
        if model:
            profile = self._CUSTOM_MODEL_ROUTES.get(model)
            if profile:
                return profile
        if self._CUSTOM_PROFILES:
            return self._CUSTOM_PROFILES[0]
        return {
            'api_url': self._CUSTOM_API_URL,
            'api_key': self._api_keys.get('custom') or '',
            'protocol': 'openai',
            'supports_fc': self._CUSTOM_SUPPORTS_FC,
            'models': [],
        }

    def _get_custom_model_name(self, model: str = '') -> str:
        return self._CUSTOM_MODEL_NAMES.get(model, model)

    def _get_custom_protocol(self, model: str = '') -> str:
        profile = self._get_custom_profile(model)
        protocol = str(profile.get('protocol') or 'openai').strip().lower()
        if protocol in ('anthropic', 'messages', 'anthropic_messages'):
            return 'anthropic'
        return 'openai'

    def _get_api_key(self, provider: str, model: str = '') -> Optional[str]:
        provider = (provider or 'openai').lower()
        if provider == 'custom':
            profile = self._get_custom_profile(model)
            return profile.get('api_key') or self._api_keys.get('custom')
        return self._api_keys.get(provider)

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

    def get_masked_key(self, provider: str = 'openai', model: str = '') -> str:
        provider = (provider or 'openai').lower()
        # Ollama 显示本地状态
        if provider == 'ollama':
            return 'Local'
        # Custom: 显示 URL 缩略
        if provider == 'custom':
            profile = self._get_custom_profile(model)
            url = profile.get('api_url') or self._CUSTOM_API_URL
            if url:
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

    def get_route_info(self, provider: str = 'openai', model: str = '') -> Dict[str, Any]:
        """Return the effective endpoint and request model used for a selection."""
        provider = (provider or 'openai').lower()
        info = {
            'provider': provider,
            'profile': '',
            'api_url': self._get_api_url(provider, model),
            'protocol': 'anthropic' if self._is_anthropic_protocol(provider, model) else 'openai',
            'model': self._get_custom_model_name(model) if provider == 'custom' else model,
            'context_limit': None,
        }
        if provider == 'custom':
            profile = self._get_custom_profile(model)
            info['profile'] = str(profile.get('name') or 'Custom')
            try:
                info['context_limit'] = int(profile.get('context_limit') or 128000)
            except (TypeError, ValueError):
                info['context_limit'] = 128000
        return info

    def _is_anthropic_protocol(self, provider: str, model: str) -> bool:
        """判断是否应使用 Anthropic Messages 协议（而非 OpenAI 协议）"""
        provider = (provider or '').lower()
        if provider == 'duojie':
            return model.lower() in self._DUOJIE_ANTHROPIC_MODELS
        if provider == 'custom':
            return self._get_custom_protocol(model) == 'anthropic'
        return False

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

    def _get_api_url(self, provider: str, model: str = '') -> str:
        provider = (provider or 'openai').lower()
        if provider == 'deepseek':
            return self.DEEPSEEK_API_URL
        elif provider == 'glm':
            return self.GLM_API_URL
        elif provider == 'ollama':
            return self.OLLAMA_API_URL
        elif provider == 'duojie':
            if model and self._is_anthropic_protocol(provider, model):
                return self.DUOJIE_ANTHROPIC_API_URL
            return self.DUOJIE_API_URL
        elif provider == 'openrouter':
            return self.OPENROUTER_API_URL
        elif provider == 'custom':
            profile = self._get_custom_profile(model)
            raw = profile.get('api_url') or self._CUSTOM_API_URL or self.OPENAI_API_URL
            if self._get_custom_protocol(model) == 'anthropic':
                return normalize_custom_messages_url(raw)
            return normalize_custom_chat_url(raw)
        return self.OPENAI_API_URL

    def _get_vendor_name(self, provider: str) -> str:
        names = {
            'openai': 'OpenAI', 'deepseek': 'DeepSeek',
            'glm': 'GLM（智谱AI）', 'ollama': 'Ollama',
            'duojie': '拼好饭', 'openrouter': 'OpenRouter',
            'custom': 'Custom',
        }
        return names.get(provider, provider)

    def set_custom_provider(self, api_url: str, api_key: str = '', supports_fc: bool = True,
                            profiles: Optional[List[Dict[str, Any]]] = None):
        """设置 Custom Provider 的运行时配置

        Args:
            api_url: OpenAI 兼容的 API 端点 URL
            api_key: API Key（可为空）
            supports_fc: 是否支持原生 Function Calling
            profiles: 多组 Custom 配置，每组可包含独立 URL / API Key / 模型列表
        """
        normalized_profiles: List[Dict[str, Any]] = []
        for idx, profile in enumerate(profiles or [], start=1):
            if not isinstance(profile, dict):
                continue
            models = profile.get('models') or []
            if isinstance(models, str):
                models = [m.strip() for m in re.split(r'[,;\n]+', models) if m.strip()]
            else:
                models = [str(m).strip() for m in models if str(m).strip()]
            enabled_models = profile.get('enabled_models') or []
            if isinstance(enabled_models, str):
                enabled_models = [m.strip() for m in re.split(r'[,;\n]+', enabled_models) if m.strip()]
            else:
                enabled_models = [str(m).strip() for m in enabled_models if str(m).strip()]
            enabled_models = [m for m in enabled_models if m in models]
            try:
                context_limit = int(profile.get('context_limit') or 128000)
            except (TypeError, ValueError):
                context_limit = 128000
            protocol = str(profile.get('protocol') or 'openai').strip().lower()
            if protocol not in ('anthropic', 'messages', 'anthropic_messages'):
                protocol = 'openai'
            else:
                protocol = 'anthropic'
            raw_api_url = profile.get('api_url', '')
            normalized_profiles.append({
                'name': str(profile.get('name') or f'Custom {idx}').strip() or f'Custom {idx}',
                'api_url': normalize_custom_messages_url(raw_api_url) if protocol == 'anthropic' else normalize_custom_chat_url(raw_api_url),
                'api_key': str(profile.get('api_key') or '').strip(),
                'protocol': protocol,
                'models': models,
                'enabled_models': enabled_models,
                'context_limit': context_limit,
                'supports_vision': bool(profile.get('supports_vision', False)),
                'supports_fc': bool(profile.get('supports_fc', supports_fc)),
            })
        if not normalized_profiles:
            normalized_profiles = [{
                'name': 'Custom 1',
                'api_url': normalize_custom_chat_url(api_url),
                'api_key': (api_key or '').strip(),
                'protocol': 'openai',
                'models': [],
                'enabled_models': [],
                'context_limit': 128000,
                'supports_vision': False,
                'supports_fc': supports_fc,
            }]

        self._CUSTOM_PROFILES = normalized_profiles
        self._CUSTOM_MODEL_ROUTES = {}
        self._CUSTOM_MODEL_NAMES = {}

        for profile in normalized_profiles:
            profile_name = profile.get('name', 'Custom')
            visible_models = profile.get('enabled_models') or profile.get('models', [])
            for model in visible_models:
                label = f"{profile_name} / {model}"
                self._CUSTOM_MODEL_ROUTES[label] = profile
                self._CUSTOM_MODEL_NAMES[label] = model
                self._CUSTOM_MODEL_ROUTES.setdefault(model, profile)
                self._CUSTOM_MODEL_NAMES.setdefault(model, model)

        primary = normalized_profiles[0]
        self._CUSTOM_API_URL = primary.get('api_url', '')
        self._CUSTOM_SUPPORTS_FC = primary.get('supports_fc', supports_fc)
        primary_key = primary.get('api_key') or (api_key or '').strip()
        if primary_key:
            self._api_keys['custom'] = primary_key

    def set_ollama_url(self, base_url: str):
        """设置 Ollama 服务地址"""
        self._ollama_base_url = base_url.rstrip('/')
        self.OLLAMA_API_URL = f"{self._ollama_base_url}/v1/chat/completions"

    def get_ollama_models(self) -> List[str]:
        """获取 Ollama 可用的模型列表"""
        if not HAS_REQUESTS:
            return ['qwen2.5:14b']

        try:
            response = self._http_session.get(
                f"{self._ollama_base_url}/api/tags",
                timeout=5
            )
            if response.status_code == 200:
                data = response.json()
                models = [m.get('name', '') for m in data.get('models', [])]
                return models if models else ['qwen2.5:14b']
        except Exception:
            pass

        return ['qwen2.5:14b']  # 默认模型

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

        models_url = normalize_custom_models_url(api_url)
        if not models_url:
            return []

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

        # Ollama 特殊处理
        if provider == 'ollama':
            try:
                if HAS_REQUESTS:
                    response = self._http_session.get(
                        f"{self._ollama_base_url}/api/tags",
                        timeout=5
                    )
                    if response.status_code == 200:
                        return {'ok': True, 'url': self._ollama_base_url, 'status': 200}
                    return {'ok': False, 'error': f'Ollama 服务响应异常: {response.status_code}'}
            except Exception as e:
                return {'ok': False, 'error': f'无法连接 Ollama 服务: {str(e)}'}

        default_model = self._get_default_model(provider)
        api_key = self._get_api_key(provider, default_model)
        # Custom provider 允许无 API Key（本地服务等）
        if not api_key and provider != 'custom':
            return {'ok': False, 'error': f'缺少 API Key'}

        try:
            if HAS_REQUESTS:
                headers = {'Content-Type': 'application/json; charset=utf-8'}
                if api_key:
                    headers['Authorization'] = f'Bearer {api_key}'
                response = self._http_session.post(
                    self._get_api_url(provider, default_model),
                    data=self._json_body({'model': default_model, 'messages': [{'role': 'user', 'content': 'hi'}], 'max_tokens': 1}),
                    headers=headers,
                    timeout=15,
                    proxies={'http': None, 'https': None}
                )
                return {'ok': True, 'url': self._get_api_url(provider, default_model), 'status': response.status_code}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def _get_default_model(self, provider: str) -> str:
        if (provider or '').lower() == 'custom':
            profile = self._get_custom_profile()
            models = profile.get('enabled_models') or profile.get('models') or []
            if models:
                return models[0]
        defaults = {
            'openai': 'gpt-5.2',
            'deepseek': 'deepseek-v4-flash',
            'glm': 'glm-4.7',
            'ollama': 'qwen2.5:14b',
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
