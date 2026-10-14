# -*- coding: utf-8 -*-
"""
订阅登录集成包 —— 用 ChatGPT 订阅（Codex 额度）驱动 Houdini Agent。

实现：在本机托管一个 CLIProxyAPI（CPA，MIT，https://github.com/router-for-me/CLIProxyAPI）
进程，它用 Codex 的 OAuth 登录拿到订阅凭据，并对外暴露 OpenAI 兼容接口；Houdini Agent
把内置供应商 "codex" 的请求发到这个本机接口。

自包含设计：本包只依赖标准库 + requests。核心文件只需：
  - ai_client_providers：provider == "codex" 时取 chat_url() / client_key()
  - controller：MODEL_MAP 增加 "codex"，订阅对话框的槽函数调本包

为什么不做 Claude 订阅登录：Anthropic 不允许第三方产品提供 claude.ai 登录或使用订阅额度
（见 Claude Agent SDK 文档），Claude 请继续用 API key 渠道。
"""

from .cpa import (  # noqa: F401
    CPA_VERSION,
    CODEX_MODELS,
    CODEX_CONTEXT,
    manager,
)
