# -*- coding: utf-8 -*-
"""
Header UI 构建 — 顶部设置栏（模型选择、Provider、Web/Think 开关等）

从 ai_tab.py 中拆分出的 Mixin，所有方法通过 self 访问 AITab 实例状态。
样式由全局 style_template.qss 通过 objectName 选择器控制。
"""

from houdini_agent.qt_compat import QtWidgets, QtCore


class HeaderMixin:
    """顶部设置栏构建与交互逻辑"""

    def _build_header(self) -> QtWidgets.QWidget:
        """顶部设置栏 - 分两行：上行选择器，下行功能按钮"""
        header = QtWidgets.QFrame()
        header.setObjectName("headerFrame")
        
        outer = QtWidgets.QVBoxLayout(header)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.setSpacing(3)
        
        # -------- 第一行：提供商 + 模型 + Agent/Web --------
        row1 = QtWidgets.QHBoxLayout()
        row1.setSpacing(4)
        
        # 提供商（缩短名称，省空间）
        self.provider_combo = QtWidgets.QComboBox()
        self.provider_combo.setObjectName("providerCombo")
        self.provider_combo.addItem("Ollama", 'ollama')
        self.provider_combo.addItem("DeepSeek", 'deepseek')
        self.provider_combo.addItem("GLM", 'glm')
        self.provider_combo.addItem("OpenAI", 'openai')
        self.provider_combo.addItem("Duojie", 'duojie')
        self.provider_combo.setMinimumWidth(70)
        self.provider_combo.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        row1.addWidget(self.provider_combo)
        
        # 模型
        self.model_combo = QtWidgets.QComboBox()
        self.model_combo.setObjectName("modelCombo")
        self._model_map = {
            'ollama': ['qwen2.5:14b', 'qwen2.5:7b', 'llama3:8b', 'mistral:7b'],
            'deepseek': ['deepseek-chat', 'deepseek-reasoner'],
            'glm': ['glm-4.7'],
            'openai': ['gpt-5.2', 'gpt-5.3-codex'],
            'duojie': [
                'claude-sonnet-4-5',
                'claude-opus-4-5-kiro',
                'claude-opus-4-5-max',
                'claude-opus-4-6-normal',
                'claude-opus-4-6-kiro',
                'claude-haiku-4-5',
                'gemini-3-pro-image-preview',
                'gpt-5.3-codex',
            ],
        }
        self._model_context_limits = {
            'qwen2.5:14b': 32000, 'qwen2.5:7b': 32000, 'llama3:8b': 8000, 'mistral:7b': 32000,
            'deepseek-chat': 128000, 'deepseek-reasoner': 128000,
            'glm-4.7': 200000,
            'gpt-5.2': 128000,
            'gpt-5.3-codex': 200000,
            # Duojie 模型
            'claude-sonnet-4-5': 200000,
            'claude-opus-4-5-kiro': 200000,
            'claude-opus-4-5-max': 200000,
            'claude-opus-4-6-normal': 200000,
            'claude-opus-4-6-kiro': 200000,
            'claude-haiku-4-5': 200000,
            'gemini-3-pro-image-preview': 128000,
        }
        # 模型特性配置
        # supports_prompt_caching: 是否支持提示缓存（保持消息前缀稳定可自动命中）
        # supports_vision: 是否支持图片识别（可在消息中发送图片）
        self._model_features = {
            # Ollama
            'qwen2.5:14b':               {'supports_prompt_caching': True, 'supports_vision': False},
            'qwen2.5:7b':                {'supports_prompt_caching': True, 'supports_vision': False},
            'llama3:8b':                  {'supports_prompt_caching': True, 'supports_vision': False},
            'mistral:7b':                 {'supports_prompt_caching': True, 'supports_vision': False},
            # DeepSeek
            'deepseek-chat':              {'supports_prompt_caching': True, 'supports_vision': False},
            'deepseek-reasoner':          {'supports_prompt_caching': True, 'supports_vision': False},
            # GLM
            'glm-4.7':                    {'supports_prompt_caching': True, 'supports_vision': False},
            # OpenAI
            'gpt-5.2':                    {'supports_prompt_caching': True, 'supports_vision': True},
            'gpt-5.3-codex':              {'supports_prompt_caching': True, 'supports_vision': True},
            # Duojie - Claude
            'claude-sonnet-4-5':          {'supports_prompt_caching': True, 'supports_vision': True},
            'claude-opus-4-5-kiro':       {'supports_prompt_caching': True, 'supports_vision': True},
            'claude-opus-4-5-max':        {'supports_prompt_caching': True, 'supports_vision': True},
            'claude-opus-4-6-normal':     {'supports_prompt_caching': True, 'supports_vision': True},
            'claude-opus-4-6-kiro':       {'supports_prompt_caching': True, 'supports_vision': True},
            'claude-haiku-4-5':           {'supports_prompt_caching': True, 'supports_vision': True},
            # Duojie - Gemini
            'gemini-3-pro-image-preview': {'supports_prompt_caching': True, 'supports_vision': True},
        }
        self._refresh_models('ollama')
        self.model_combo.setMinimumWidth(100)
        self.model_combo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        row1.addWidget(self.model_combo, 1)  # stretch=1 让模型框占满剩余宽度
        
        # Web / Think 开关（Agent/Ask 模式已移至输入区域下方）
        self.web_check = QtWidgets.QCheckBox("Web")
        self.web_check.setObjectName("chkWeb")
        self.web_check.setChecked(True)
        row1.addWidget(self.web_check)
        
        self.think_check = QtWidgets.QCheckBox("Think")
        self.think_check.setObjectName("chkThink")
        self.think_check.setChecked(True)
        self.think_check.setToolTip("启用思考模式：AI 会先分析再回答，并显示思考过程")
        row1.addWidget(self.think_check)
        
        outer.addLayout(row1)
        
        # -------- 第二行：Key 状态 + 功能按钮 --------
        row2 = QtWidgets.QHBoxLayout()
        row2.setSpacing(4)
        
        # API Key 状态
        self.key_status = QtWidgets.QLabel()
        self.key_status.setObjectName("keyStatus")
        row2.addWidget(self.key_status, 1)
        
        # 功能按钮（紧凑）
        self.btn_key = QtWidgets.QPushButton("Key")
        self.btn_key.setObjectName("btnSmall")
        self.btn_key.setFixedHeight(24)
        row2.addWidget(self.btn_key)
        
        self.btn_clear = QtWidgets.QPushButton("Clear")
        self.btn_clear.setObjectName("btnSmall")
        self.btn_clear.setFixedHeight(24)
        row2.addWidget(self.btn_clear)
        
        self.btn_cache = QtWidgets.QPushButton("Cache")
        self.btn_cache.setObjectName("btnSmall")
        self.btn_cache.setFixedHeight(24)
        self.btn_cache.setToolTip("缓存管理：保存/加载对话历史")
        row2.addWidget(self.btn_cache)
        
        self.btn_optimize = QtWidgets.QPushButton("Opt")
        self.btn_optimize.setObjectName("btnOptimize")
        self.btn_optimize.setFixedHeight(24)
        self.btn_optimize.setToolTip("Token 优化：自动压缩和优化")
        row2.addWidget(self.btn_optimize)
        
        # ★ 更新按钮（黄色醒目）
        self.btn_update = QtWidgets.QPushButton("Update")
        self.btn_update.setObjectName("btnUpdate")
        self.btn_update.setFixedHeight(24)
        self.btn_update.setToolTip("检查并更新到最新版本")
        row2.addWidget(self.btn_update)
        
        # Aa 字号缩放按钮
        self.btn_font_scale = QtWidgets.QPushButton("Aa")
        self.btn_font_scale.setObjectName("btnFontScale")
        self.btn_font_scale.setFixedHeight(24)
        self.btn_font_scale.setToolTip("字体大小 (Ctrl+/Ctrl-)")
        row2.addWidget(self.btn_font_scale)
        
        outer.addLayout(row2)
        
        return header
