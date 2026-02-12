# -*- coding: utf-8 -*-
"""
Header UI 构建 — 顶部设置栏（模型选择、Provider、Web/Think 开关等）

从 ai_tab.py 中拆分出的 Mixin，所有方法通过 self 访问 AITab 实例状态。
"""

from houdini_agent.qt_compat import QtWidgets, QtCore
from .cursor_widgets import CursorTheme


class HeaderMixin:
    """顶部设置栏构建与交互逻辑"""

    def _build_header(self) -> QtWidgets.QWidget:
        """顶部设置栏 - 分两行：上行选择器，下行功能按钮"""
        header = QtWidgets.QFrame()
        header.setStyleSheet(f"""
            QFrame {{
                background-color: {CursorTheme.BG_SECONDARY};
                border-bottom: 1px solid {CursorTheme.BORDER};
            }}
        """)
        
        outer = QtWidgets.QVBoxLayout(header)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.setSpacing(3)
        
        # -------- 第一行：提供商 + 模型 + Agent/Web --------
        row1 = QtWidgets.QHBoxLayout()
        row1.setSpacing(4)
        
        # 提供商（缩短名称，省空间）
        self.provider_combo = QtWidgets.QComboBox()
        self.provider_combo.addItem("Ollama", 'ollama')
        self.provider_combo.addItem("DeepSeek", 'deepseek')
        self.provider_combo.addItem("GLM", 'glm')
        self.provider_combo.addItem("OpenAI", 'openai')
        self.provider_combo.addItem("Duojie", 'duojie')
        self.provider_combo.setStyleSheet(self._combo_style())
        self.provider_combo.setMinimumWidth(70)
        self.provider_combo.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        row1.addWidget(self.provider_combo)
        
        # 模型
        self.model_combo = QtWidgets.QComboBox()
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
        self.model_combo.setStyleSheet(self._combo_style())
        self.model_combo.setMinimumWidth(100)
        self.model_combo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        row1.addWidget(self.model_combo, 1)  # stretch=1 让模型框占满剩余宽度
        
        # Web / Think 开关（Agent/Ask 模式已移至输入区域下方）
        _chk_style = f"""
            QCheckBox {{ color: {CursorTheme.TEXT_SECONDARY}; font-size: 12px; }}
            QCheckBox::indicator {{ width: 11px; height: 11px; }}
        """
        self.web_check = QtWidgets.QCheckBox("Web")
        self.web_check.setChecked(True)
        self.web_check.setStyleSheet(_chk_style)
        row1.addWidget(self.web_check)
        
        self.think_check = QtWidgets.QCheckBox("Think")
        self.think_check.setChecked(True)
        self.think_check.setToolTip("启用思考模式：AI 会先分析再回答，并显示思考过程")
        self.think_check.setStyleSheet(_chk_style)
        row1.addWidget(self.think_check)
        
        outer.addLayout(row1)
        
        # -------- 第二行：Key 状态 + 功能按钮 --------
        row2 = QtWidgets.QHBoxLayout()
        row2.setSpacing(4)
        
        # API Key 状态
        self.key_status = QtWidgets.QLabel()
        self.key_status.setStyleSheet(f"color: {CursorTheme.TEXT_MUTED}; font-size: 11px;")
        row2.addWidget(self.key_status, 1)
        
        # 功能按钮（紧凑）
        self.btn_key = QtWidgets.QPushButton("Key")
        self.btn_key.setFixedHeight(24)
        self.btn_key.setStyleSheet(self._small_btn_style())
        row2.addWidget(self.btn_key)
        
        self.btn_clear = QtWidgets.QPushButton("Clear")
        self.btn_clear.setFixedHeight(24)
        self.btn_clear.setStyleSheet(self._small_btn_style())
        row2.addWidget(self.btn_clear)
        
        self.btn_cache = QtWidgets.QPushButton("Cache")
        self.btn_cache.setFixedHeight(24)
        self.btn_cache.setStyleSheet(self._small_btn_style())
        self.btn_cache.setToolTip("缓存管理：保存/加载对话历史")
        row2.addWidget(self.btn_cache)
        
        self.btn_optimize = QtWidgets.QPushButton("Opt")
        self.btn_optimize.setFixedHeight(24)
        self.btn_optimize.setStyleSheet(self._small_btn_style())
        self.btn_optimize.setToolTip("Token 优化：自动压缩和优化")
        row2.addWidget(self.btn_optimize)
        
        # ★ 更新按钮（黄色醒目）
        self.btn_update = QtWidgets.QPushButton("Update")
        self.btn_update.setFixedHeight(24)
        self.btn_update.setStyleSheet(f"""
            QPushButton {{
                background: {CursorTheme.BG_TERTIARY};
                color: {CursorTheme.ACCENT_YELLOW};
                border: 1px solid {CursorTheme.ACCENT_YELLOW};
                border-radius: 3px;
                font-size: 11px;
                padding: 2px 6px;
                min-height: 20px;
            }}
            QPushButton:hover {{
                background: {CursorTheme.ACCENT_YELLOW};
                color: {CursorTheme.BG_PRIMARY};
            }}
        """)
        self.btn_update.setToolTip("检查并更新到最新版本")
        row2.addWidget(self.btn_update)
        
        outer.addLayout(row2)
        
        return header

    def _combo_style(self) -> str:
        return f"""
            QComboBox {{
                background: {CursorTheme.BG_TERTIARY};
                color: {CursorTheme.TEXT_PRIMARY};
                border: 1px solid {CursorTheme.BORDER};
                border-radius: 3px;
                padding: 3px 6px;
                font-size: 12px;
                min-height: 22px;
            }}
            QComboBox::drop-down {{ border: none; width: 12px; }}
            QComboBox QAbstractItemView {{
                background: {CursorTheme.BG_TERTIARY};
                color: {CursorTheme.TEXT_PRIMARY};
                border: 1px solid {CursorTheme.BORDER};
                font-size: 12px;
            }}
        """
    
    def _small_btn_style(self) -> str:
        return f"""
            QPushButton {{
                background: {CursorTheme.BG_TERTIARY};
                color: {CursorTheme.TEXT_SECONDARY};
                border: 1px solid {CursorTheme.BORDER};
                border-radius: 3px;
                font-size: 11px;
                padding: 2px 6px;
                min-height: 20px;
            }}
            QPushButton:hover {{
                background: {CursorTheme.BG_HOVER};
                color: {CursorTheme.TEXT_PRIMARY};
            }}
        """
