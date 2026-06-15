# -*- coding: utf-8 -*-
"""
Header UI 构建 — 顶部设置栏（模型选择、Provider、Web/Think 开关等）

从 ai_tab.py 中拆分出的 Mixin，所有方法通过 self 访问 AITab 实例状态。
样式由全局 style_template.qss 通过 objectName 选择器控制。
"""

import json
import math

from houdini_agent.qt_compat import QtWidgets, QtCore, QtGui
from houdini_agent.utils.ai_client import (
    AIClient,
    normalize_custom_anthropic_models_url,
    normalize_custom_chat_url,
    normalize_custom_messages_url,
    normalize_custom_models_url,
)
from .i18n import tr, get_language, set_language, language_changed
from .theme_engine import ThemeEngine


def _split_custom_models(models_text: str) -> list:
    chunks = (models_text or '').replace('\n', ',').replace(';', ',').split(',')
    models = []
    seen = set()
    for chunk in chunks:
        model = chunk.strip()
        if model and model not in seen:
            models.append(model)
            seen.add(model)
    return models


def _normalize_custom_model_list(values) -> list:
    if isinstance(values, str):
        values = _split_custom_models(values)
    elif values is None:
        values = []
    else:
        values = [str(value).strip() for value in values if str(value).strip()]

    unique = []
    seen = set()
    for name in values:
        if name and name not in seen:
            unique.append(name)
            seen.add(name)
    return unique


def _normalize_custom_model_state(profile: dict) -> dict:
    models = _normalize_custom_model_list(profile.get('models'))
    manual_models = _normalize_custom_model_list(profile.get('manual_models'))
    fetched_models = _normalize_custom_model_list(profile.get('fetched_models'))
    hidden_models = _normalize_custom_model_list(profile.get('hidden_models'))

    if not manual_models and not fetched_models and models:
        manual_models = list(models)

    hidden_set = set(hidden_models)
    manual_models = [name for name in manual_models if name not in hidden_set]
    fetched_models = [name for name in fetched_models if name not in hidden_set]

    merged = []
    seen = set()
    for name in manual_models + fetched_models + models:
        if name and name not in hidden_set and name not in seen:
            merged.append(name)
            seen.add(name)

    enabled_models = _normalize_custom_model_list(profile.get('enabled_models'))
    enabled_models = [name for name in enabled_models if name in merged]

    return {
        'models': merged,
        'manual_models': manual_models,
        'fetched_models': fetched_models,
        'hidden_models': [name for name in hidden_models if name not in manual_models and name not in fetched_models],
        'enabled_models': enabled_models,
    }


def _clean_custom_api_url(api_url: str) -> str:
    """Keep URL values single-line so the line-based ini parser stays valid."""
    return ''.join(str(api_url or '').split())


def _strip_generated_name_suffix(name: str) -> str:
    """Collapse repeated generated suffix chains like 'Name 2 2' back to 'Name'."""
    parts = str(name or '').strip().split()
    if len(parts) > 2 and parts[-1].isdigit() and parts[-2] == parts[-1]:
        repeated = parts[-1]
        while len(parts) > 1 and parts[-1] == repeated:
            parts.pop()
    return ' '.join(parts).strip()


def _normalize_custom_profiles(profiles: list) -> list:
    normalized = []
    for idx, profile in enumerate(profiles or [], start=1):
        if not isinstance(profile, dict):
            continue
        name = str(profile.get('name') or f'Custom {idx}').strip() or f'Custom {idx}'
        state = _normalize_custom_model_state(profile)
        try:
            context_limit = int(profile.get('context_limit') or 128000)
        except (TypeError, ValueError):
            context_limit = 128000
        protocol = str(profile.get('protocol') or 'openai').strip().lower()
        if protocol not in ('anthropic', 'messages', 'anthropic_messages'):
            protocol = 'openai'
        else:
            protocol = 'anthropic'
        normalized.append({
            'name': name,
            'api_url': _clean_custom_api_url(profile.get('api_url')),
            'api_key': str(profile.get('api_key') or '').strip(),
            'protocol': protocol,
            'models': state['models'],
            'manual_models': state['manual_models'],
            'fetched_models': state['fetched_models'],
            'hidden_models': state['hidden_models'],
            'enabled_models': state['enabled_models'],
            'context_limit': context_limit,
            'supports_vision': bool(profile.get('supports_vision', False)),
            'supports_fc': bool(profile.get('supports_fc', True)),
        })
    return normalized


def _visible_profile_models(profile: dict) -> list:
    models = profile.get('models', []) or []
    enabled_models = profile.get('enabled_models', []) or []
    if enabled_models:
        return [model for model in models if model in enabled_models]
    return models


def _dedupe_custom_profile_names(profiles: list) -> list:
    used = set()
    for idx, profile in enumerate(profiles or [], start=1):
        base = _strip_generated_name_suffix(profile.get('name')) or f'Custom {idx}'
        name = base
        suffix = 2
        while name in used:
            name = f"{base} {suffix}"
            suffix += 1
        profile['name'] = name
        used.add(name)
    return profiles


def _flatten_custom_models(profiles: list) -> list:
    models = []
    seen = set()
    for profile in profiles or []:
        profile_name = profile.get('name', 'Custom')
        for model in _visible_profile_models(profile):
            label = f"{profile_name} / {model}"
            if label and label not in seen:
                models.append(label)
                seen.add(label)
    return models


def _make_line_icon(kind: str, size: int = 18, color: str = "#d4c5b0") -> QtGui.QIcon:
    """Create small deterministic toolbar icons that do not depend on emoji fonts."""
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.transparent)

    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    pen = QtGui.QPen(QtGui.QColor(color), max(1, size // 9), QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(QtCore.Qt.NoBrush)
    s = float(size)

    if kind == "settings":
        center = QtCore.QPointF(s / 2, s / 2)
        painter.drawEllipse(center, s * 0.20, s * 0.20)
        for angle in range(0, 360, 45):
            rad = math.radians(angle)
            inner = QtCore.QPointF(center.x() + math.cos(rad) * s * 0.31, center.y() + math.sin(rad) * s * 0.31)
            outer = QtCore.QPointF(center.x() + math.cos(rad) * s * 0.42, center.y() + math.sin(rad) * s * 0.42)
            painter.drawLine(inner, outer)
    elif kind == "add":
        painter.drawEllipse(QtCore.QRectF(s * 0.18, s * 0.18, s * 0.64, s * 0.64))
        painter.drawLine(QtCore.QPointF(s * 0.50, s * 0.32), QtCore.QPointF(s * 0.50, s * 0.68))
        painter.drawLine(QtCore.QPointF(s * 0.32, s * 0.50), QtCore.QPointF(s * 0.68, s * 0.50))
    elif kind == "delete":
        painter.drawLine(QtCore.QPointF(s * 0.32, s * 0.38), QtCore.QPointF(s * 0.68, s * 0.38))
        painter.drawLine(QtCore.QPointF(s * 0.38, s * 0.38), QtCore.QPointF(s * 0.42, s * 0.78))
        painter.drawLine(QtCore.QPointF(s * 0.62, s * 0.38), QtCore.QPointF(s * 0.58, s * 0.78))
        painter.drawLine(QtCore.QPointF(s * 0.42, s * 0.78), QtCore.QPointF(s * 0.58, s * 0.78))
        painter.drawLine(QtCore.QPointF(s * 0.43, s * 0.26), QtCore.QPointF(s * 0.57, s * 0.26))
    elif kind == "eye":
        eye_path = QtGui.QPainterPath()
        eye_path.moveTo(s * 0.14, s * 0.50)
        eye_path.cubicTo(s * 0.28, s * 0.28, s * 0.72, s * 0.28, s * 0.86, s * 0.50)
        eye_path.cubicTo(s * 0.72, s * 0.72, s * 0.28, s * 0.72, s * 0.14, s * 0.50)
        painter.drawPath(eye_path)
        painter.drawEllipse(QtCore.QPointF(s * 0.50, s * 0.50), s * 0.11, s * 0.11)
    elif kind == "refresh":
        rect = QtCore.QRectF(s * 0.22, s * 0.22, s * 0.56, s * 0.56)
        painter.drawArc(rect, 30 * 16, 270 * 16)
        painter.drawLine(QtCore.QPointF(s * 0.72, s * 0.23), QtCore.QPointF(s * 0.78, s * 0.42))
        painter.drawLine(QtCore.QPointF(s * 0.72, s * 0.23), QtCore.QPointF(s * 0.54, s * 0.29))

    painter.end()
    return QtGui.QIcon(pixmap)


class HeaderMixin:
    """顶部设置栏构建与交互逻辑"""

    def _build_header(self) -> QtWidgets.QWidget:
        """顶部设置栏 — 单行：Provider + Model + keyStatus + Web + Think + ⋯ 溢出菜单"""
        header = QtWidgets.QFrame()
        header.setObjectName("headerFrame")
        
        outer = QtWidgets.QVBoxLayout(header)
        outer.setContentsMargins(8, 2, 8, 2)
        outer.setSpacing(0)
        
        # -------- 单行：Provider + Model + keyStatus + Web + Think + ⋯ --------
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        
        # 提供商
        self.provider_combo = QtWidgets.QComboBox()
        self.provider_combo.setObjectName("providerCombo")
        self.provider_combo.addItem("Ollama", 'ollama')
        self.provider_combo.addItem("DeepSeek", 'deepseek')
        self.provider_combo.addItem("GLM", 'glm')
        self.provider_combo.addItem("OpenAI", 'openai')
        self.provider_combo.addItem("Duojie", 'duojie')
        self.provider_combo.addItem("OpenRouter", 'openrouter')
        self.provider_combo.addItem("Custom", 'custom')
        self.provider_combo.setMinimumWidth(70)
        self.provider_combo.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        row.addWidget(self.provider_combo)
        
        # Custom 配置按钮（仅在 Custom provider 时可见）
        self.btn_custom_config = QtWidgets.QPushButton()
        self.btn_custom_config.setObjectName("btnCustomConfig")
        self.btn_custom_config.setFixedSize(22, 22)
        self.btn_custom_config.setIcon(_make_line_icon("settings", 16))
        self.btn_custom_config.setIconSize(QtCore.QSize(16, 16))
        self.btn_custom_config.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_custom_config.setToolTip("配置 Custom Model 的 URL、API Key 和模型名")
        self.btn_custom_config.setVisible(False)
        self.btn_custom_config.clicked.connect(self._open_custom_provider_dialog)
        row.addWidget(self.btn_custom_config)
        
        # 模型
        self.model_combo = QtWidgets.QComboBox()
        self.model_combo.setObjectName("modelCombo")
        self._model_map = {
            'ollama': ['qwen2.5:14b', 'qwen2.5:7b', 'llama3:8b', 'mistral:7b'],
            'deepseek': ['deepseek-v4-flash', 'deepseek-v4-pro', 'deepseek-chat', 'deepseek-reasoner'],
            'glm': ['glm-4.7'],
            'openai': ['gpt-5.2', 'gpt-5.3-codex'],
            'duojie': [
                'claude-opus-4-6-gemini',
                'claude-opus-4-6-max',
                'claude-sonnet-4-5',
                'claude-sonnet-4-6',
                'gemini-3-flash',
                'gemini-3.1-pro',
                'glm-5-turbo',
                'glm-5.1',
                'MiniMax-M2.7',
                'MiniMax-M2.7-highspeed',
            ],
            'openrouter': [
                'anthropic/claude-sonnet-4.6',
                'anthropic/claude-opus-4.6',
                'anthropic/claude-sonnet-4.5',
                'anthropic/claude-haiku-4.5',
                'openai/gpt-5.2',
                'openai/gpt-5.3-codex',
                'openai/o4-mini',
                'google/gemini-3-flash-preview',
                'google/gemini-2.5-pro',
                'google/gemini-2.5-flash',
                'deepseek/deepseek-v3.2',
                'deepseek/deepseek-r1',
                'x-ai/grok-4.1-fast',
                'meta-llama/llama-4-maverick',
                'qwen/qwen3-235b-a22b',
                'mistralai/mistral-large-2512',
            ],
            'custom': [],  # 由用户通过配置对话框动态填充
        }
        # Custom provider 的运行时配置（从持久化配置加载）
        self._custom_provider_config = {
            'api_url': '',
            'api_key': '',
            'protocol': 'openai',
            'models': [],           # 用户配置的模型名列表
            'manual_models': [],
            'fetched_models': [],
            'hidden_models': [],
            'context_limit': 128000,
            'supports_vision': False,
            'supports_fc': True,    # 是否支持 Function Calling
            'profiles': [],
        }
        self._load_custom_provider_config()
        self._model_context_limits = {
            'qwen2.5:14b': 32000, 'qwen2.5:7b': 32000, 'llama3:8b': 8000, 'mistral:7b': 32000,
            'deepseek-v4-flash': 1048576, 'deepseek-v4-pro': 1048576,
            'deepseek-chat': 1048576, 'deepseek-reasoner': 1048576,
            'glm-4.7': 200000,
            'gpt-5.2': 128000,
            'gpt-5.3-codex': 200000,
            # Duojie 模型
            'claude-opus-4-6-gemini': 200000,
            'claude-opus-4-6-max': 200000,
            'claude-sonnet-4-5': 200000,
            'claude-sonnet-4-6': 200000,
            'gemini-3-flash': 1048576,
            'gemini-3.1-pro': 1048576,
            'glm-5-turbo': 200000,
            'glm-5.1': 200000,
            'MiniMax-M2.7': 128000,
            'MiniMax-M2.7-highspeed': 128000,
            # OpenRouter 模型
            'anthropic/claude-sonnet-4.6': 1000000,
            'anthropic/claude-opus-4.6': 1000000,
            'anthropic/claude-sonnet-4.5': 1000000,
            'anthropic/claude-haiku-4.5': 200000,
            'openai/gpt-5.2': 400000,
            'openai/gpt-5.3-codex': 400000,
            'openai/o4-mini': 200000,
            'google/gemini-3-flash-preview': 1048576,
            'google/gemini-2.5-pro': 1048576,
            'google/gemini-2.5-flash': 1048576,
            'deepseek/deepseek-v3.2': 163840,
            'deepseek/deepseek-r1': 64000,
            'x-ai/grok-4.1-fast': 2000000,
            'meta-llama/llama-4-maverick': 1048576,
            'qwen/qwen3-235b-a22b': 131072,
            'mistralai/mistral-large-2512': 262144,
        }
        # 模型特性配置
        self._model_features = {
            # Ollama
            'qwen2.5:14b':               {'supports_prompt_caching': True, 'supports_vision': False},
            'qwen2.5:7b':                {'supports_prompt_caching': True, 'supports_vision': False},
            'llama3:8b':                  {'supports_prompt_caching': True, 'supports_vision': False},
            'mistral:7b':                 {'supports_prompt_caching': True, 'supports_vision': False},
            # DeepSeek
            'deepseek-v4-flash':          {'supports_prompt_caching': True, 'supports_vision': False},
            'deepseek-v4-pro':            {'supports_prompt_caching': True, 'supports_vision': False},
            'deepseek-chat':              {'supports_prompt_caching': True, 'supports_vision': False},
            'deepseek-reasoner':          {'supports_prompt_caching': True, 'supports_vision': False},
            # GLM
            'glm-4.7':                    {'supports_prompt_caching': True, 'supports_vision': False},
            # OpenAI
            'gpt-5.2':                    {'supports_prompt_caching': True, 'supports_vision': True},
            'gpt-5.3-codex':              {'supports_prompt_caching': True, 'supports_vision': True},
            # Duojie - Claude
            'claude-opus-4-6-gemini':    {'supports_prompt_caching': True, 'supports_vision': True},
            'claude-opus-4-6-max':        {'supports_prompt_caching': True, 'supports_vision': True},
            'claude-sonnet-4-5':          {'supports_prompt_caching': True, 'supports_vision': True},
            'claude-sonnet-4-6':          {'supports_prompt_caching': True, 'supports_vision': True},
            # Duojie - Gemini
            'gemini-3-flash':             {'supports_prompt_caching': True, 'supports_vision': True},
            'gemini-3.1-pro':             {'supports_prompt_caching': True, 'supports_vision': True},
            # Duojie - GLM (Anthropic 协议)
            'glm-5-turbo':                {'supports_prompt_caching': True, 'supports_vision': False},
            'glm-5.1':                    {'supports_prompt_caching': True, 'supports_vision': False},
            # Duojie - MiniMax
            'MiniMax-M2.7':               {'supports_prompt_caching': True, 'supports_vision': False},
            'MiniMax-M2.7-highspeed':     {'supports_prompt_caching': True, 'supports_vision': False},
            # OpenRouter 模型
            'anthropic/claude-sonnet-4.6':        {'supports_prompt_caching': True, 'supports_vision': True},
            'anthropic/claude-opus-4.6':          {'supports_prompt_caching': True, 'supports_vision': True},
            'anthropic/claude-sonnet-4.5':        {'supports_prompt_caching': True, 'supports_vision': True},
            'anthropic/claude-haiku-4.5':         {'supports_prompt_caching': True, 'supports_vision': True},
            'openai/gpt-5.2':                     {'supports_prompt_caching': True, 'supports_vision': True},
            'openai/gpt-5.3-codex':               {'supports_prompt_caching': True, 'supports_vision': True},
            'openai/o4-mini':                     {'supports_prompt_caching': True, 'supports_vision': True},
            'google/gemini-3-flash-preview':      {'supports_prompt_caching': True, 'supports_vision': True},
            'google/gemini-2.5-pro':              {'supports_prompt_caching': True, 'supports_vision': True},
            'google/gemini-2.5-flash':            {'supports_prompt_caching': True, 'supports_vision': True},
            'deepseek/deepseek-v3.2':             {'supports_prompt_caching': True, 'supports_vision': False},
            'deepseek/deepseek-r1':               {'supports_prompt_caching': True, 'supports_vision': False},
            'x-ai/grok-4.1-fast':                 {'supports_prompt_caching': True, 'supports_vision': True},
            'meta-llama/llama-4-maverick':        {'supports_prompt_caching': True, 'supports_vision': True},
            'qwen/qwen3-235b-a22b':               {'supports_prompt_caching': True, 'supports_vision': False},
            'mistralai/mistral-large-2512':       {'supports_prompt_caching': True, 'supports_vision': True},
        }
        self._register_custom_model_features(self._custom_provider_config.get('profiles', []))
        self._refresh_models('ollama')
        self.model_combo.setMinimumWidth(100)
        self.model_combo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.model_combo.setEditable(False)
        row.addWidget(self.model_combo, 1)
        
        # API Key 状态 — 紧凑指示（行内，限宽 + 省略号）
        self.key_status = QtWidgets.QLabel()
        self.key_status.setObjectName("keyStatus")
        self.key_status.setMaximumWidth(90)
        self.key_status.setMinimumWidth(0)
        from houdini_agent.qt_compat import QtCore as _qc
        self.key_status.setTextInteractionFlags(_qc.Qt.NoTextInteraction)
        row.addWidget(self.key_status)
        
        # Web / Think 开关
        self.web_check = QtWidgets.QCheckBox(tr("header.web"))
        self.web_check.setObjectName("chkWeb")
        self.web_check.setChecked(True)
        row.addWidget(self.web_check)
        
        self.think_check = QtWidgets.QCheckBox(tr("header.think"))
        self.think_check.setObjectName("chkThink")
        self.think_check.setChecked(True)
        self.think_check.setToolTip(tr('header.think.tooltip'))
        row.addWidget(self.think_check)
        
        # ⋯ 溢出菜单按钮
        self.btn_overflow = QtWidgets.QPushButton("···")
        self.btn_overflow.setObjectName("btnOverflow")
        self.btn_overflow.setFixedSize(24, 22)
        self.btn_overflow.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_overflow.clicked.connect(self._show_overflow_menu)
        row.addWidget(self.btn_overflow)
        
        outer.addLayout(row)
        
        # -------- 隐藏按钮（保持 self.btn_xxx 引用兼容 _wire_events）--------
        # 这些按钮不加入布局，仅用于信号连接
        self.btn_key = QtWidgets.QPushButton(tr("menu.api_key"))
        self.btn_key.setObjectName("btnSmall")
        self.btn_key.setVisible(False)
        
        self.btn_clear = QtWidgets.QPushButton(tr("btn.clear"))
        self.btn_clear.setObjectName("btnSmall")
        self.btn_clear.setVisible(False)
        
        self.btn_cache = QtWidgets.QPushButton(tr("menu.cache"))
        self.btn_cache.setObjectName("btnSmall")
        self.btn_cache.setVisible(False)
        
        self.btn_optimize = QtWidgets.QPushButton(tr("menu.optimize"))
        self.btn_optimize.setObjectName("btnOptimize")
        self.btn_optimize.setVisible(False)
        
        self.btn_update = QtWidgets.QPushButton(tr("menu.update"))
        self.btn_update.setObjectName("btnUpdate")
        self.btn_update.setVisible(False)
        
        self.btn_font_scale = QtWidgets.QPushButton("Aa")
        self.btn_font_scale.setObjectName("btnFontScale")
        self.btn_font_scale.setVisible(False)
        
        # 语言下拉框（隐藏，仅用于引用 + 信号）
        self.lang_combo = QtWidgets.QComboBox()
        self.lang_combo.setObjectName("langCombo")
        self.lang_combo.addItem("中文", "zh")
        self.lang_combo.addItem("EN", "en")
        self.lang_combo.setCurrentIndex(0 if get_language() == 'zh' else 1)
        self.lang_combo.currentIndexChanged.connect(self._on_language_changed)
        self.lang_combo.setVisible(False)
        
        return header

    def _register_custom_model_features(self, profiles: list):
        for profile in profiles or []:
            profile_name = profile.get('name', 'Custom')
            for model in _visible_profile_models(profile):
                label = f"{profile_name} / {model}"
                self._model_context_limits[label] = profile.get('context_limit', 128000)
                self._model_features[label] = {
                    'supports_prompt_caching': True,
                    'supports_vision': profile.get('supports_vision', False),
                }

    def _show_overflow_menu(self):
        """显示溢出菜单：低频功能集中在此"""
        menu = QtWidgets.QMenu(self)
        
        menu.addAction(tr("menu.api_key"), self.btn_key.click)
        menu.addAction(
            tr("menu.retry_limit", self._current_retry_limit()),
            self._open_retry_settings,
        )
        menu.addAction(tr("menu.clear_chat"), self.btn_clear.click)
        menu.addAction(tr("menu.cache"), self.btn_cache.click)
        menu.addAction(tr("menu.optimize"), self.btn_optimize.click)
        menu.addSeparator()
        menu.addAction(tr("menu.update"), self.btn_update.click)
        menu.addAction(tr("menu.font"), self.btn_font_scale.click)
        menu.addSeparator()
        menu.addAction(tr('rules.menu_label'), self._open_rules_editor)
        menu.addAction(tr('plugin.menu_label'), self._open_plugin_manager)
        menu.addAction(tr('experience.menu_label'), self._open_experience_review)

        # 长期记忆系统全局开关（默认关闭）—— checkable action
        act_memory = menu.addAction(tr('memory.menu_label'))
        act_memory.setCheckable(True)
        act_memory.setChecked(True)
        act_memory.setEnabled(False)
        act_memory.setToolTip(tr('memory.menu_tooltip'))
        act_memory.toggled.connect(self._on_memory_toggle_from_menu)

        menu.addSeparator()
        
        # 语言子菜单
        lang_menu = menu.addMenu(tr("menu.language"))
        act_zh = lang_menu.addAction("中文")
        act_en = lang_menu.addAction("EN")
        current_lang = get_language()
        act_zh.setCheckable(True)
        act_en.setCheckable(True)
        act_zh.setChecked(current_lang == 'zh')
        act_en.setChecked(current_lang == 'en')
        act_zh.triggered.connect(lambda: self._set_lang_from_menu('zh'))
        act_en.triggered.connect(lambda: self._set_lang_from_menu('en'))
        
        # 弹出位置：溢出按钮下方
        menu.exec_(self._overflow_anchor_pos())

    def _current_retry_limit(self) -> int:
        client = getattr(self, 'client', None)
        if client is not None and hasattr(client, 'retry_limit'):
            return client.retry_limit()
        return AIClient.DEFAULT_RETRY_LIMIT

    def _load_retry_preference(self):
        settings = QtCore.QSettings("HoudiniAI", "Assistant")
        retries = settings.value("retry_limit", AIClient.DEFAULT_RETRY_LIMIT)
        retries = AIClient.clamp_retry_limit(retries)
        client = getattr(self, 'client', None)
        if client is not None and hasattr(client, 'set_retry_limit'):
            client.set_retry_limit(retries)
        return retries

    def _save_retry_preference(self, retries: int):
        retries = AIClient.clamp_retry_limit(retries)
        settings = QtCore.QSettings("HoudiniAI", "Assistant")
        settings.setValue("retry_limit", retries)
        client = getattr(self, 'client', None)
        if client is not None and hasattr(client, 'set_retry_limit'):
            client.set_retry_limit(retries)
        return retries

    def _open_retry_settings(self):
        current = self._current_retry_limit()
        retries, ok = QtWidgets.QInputDialog.getInt(
            self,
            tr("retry.title"),
            tr("retry.prompt", AIClient.MAX_RETRY_LIMIT),
            current,
            AIClient.MIN_RETRY_LIMIT,
            AIClient.MAX_RETRY_LIMIT,
            1,
        )
        if not ok:
            return
        retries = self._save_retry_preference(retries)
        try:
            self._show_toast(tr("retry.applied", retries), 1800)
        except Exception:
            pass

    def _overflow_anchor_pos(self):
        """Return a stable global popup position near the visible overflow button."""
        anchor = getattr(self, "btn_overflow", None)
        try:
            if anchor is not None and anchor.isVisible():
                return anchor.mapToGlobal(QtCore.QPoint(0, anchor.height()))
        except RuntimeError:
            pass
        return self.mapToGlobal(QtCore.QPoint(max(0, self.width() - 24), 24))

    def _open_rules_editor(self):
        """打开用户自定义规则编辑器"""
        try:
            from .cursor_widgets import RulesEditorDialog
            dlg = RulesEditorDialog(parent=self)
            dlg.exec_()
        except Exception as e:
            print(f"[Header] Failed to open rules editor: {e}")

    def _open_plugin_manager(self):
        """打开插件管理面板"""
        try:
            from .cursor_widgets import PluginManagerDialog
            dlg = PluginManagerDialog(parent=self)
            dlg.pluginStateChanged.connect(self._on_plugin_state_changed)
            dlg.exec_()
        except Exception as e:
            print(f"[Header] Failed to open plugin manager: {e}")

    def _open_experience_review(self):
        """打开工作流经验沉淀审阅中心"""
        try:
            from .experience_review_dialog import ExperienceReviewDialog
            dlg = getattr(self, "_experience_review_dialog", None)
            try:
                if dlg is not None and dlg.isVisible():
                    dlg.raise_()
                    dlg.activateWindow()
                    return
            except RuntimeError:
                dlg = None

            dlg = ExperienceReviewDialog(self, parent=self)
            dlg.setModal(False)
            dlg.setWindowModality(QtCore.Qt.NonModal)
            dlg.destroyed.connect(lambda *_: setattr(self, "_experience_review_dialog", None))
            self._experience_review_dialog = dlg
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self,
                tr('experience.title'),
                tr('experience.open_failed', e),
            )

    def _on_plugin_state_changed(self):
        """插件状态变化后的回调（重新挂载按钮等）"""
        try:
            from ..utils.hooks import get_hook_manager
            bridge = get_hook_manager().get_ui_bridge()
            if bridge:
                bridge.mount_buttons()
        except Exception:
            pass

    def _on_memory_toggle_from_menu(self, checked: bool):
        """溢出菜单切换长期记忆系统开关"""
        try:
            self.set_memory_enabled(bool(checked))
        except Exception as e:
            print(f"[Header] Memory toggle failed: {e}")

    def _set_lang_from_menu(self, lang: str):
        """从溢出菜单切换语言"""
        if lang != get_language():
            set_language(lang)
            # 同步隐藏的 lang_combo（保持状态一致）
            expected_idx = 0 if lang == 'zh' else 1
            if self.lang_combo.currentIndex() != expected_idx:
                self.lang_combo.blockSignals(True)
                self.lang_combo.setCurrentIndex(expected_idx)
                self.lang_combo.blockSignals(False)

    def _on_language_changed(self, index: int):
        """语言下拉框切换"""
        lang = self.lang_combo.itemData(index)
        if lang and lang != get_language():
            set_language(lang)

    def _retranslate_header(self):
        """语言切换后更新 Header 区域所有翻译文本"""
        self.think_check.setToolTip(tr('header.think.tooltip'))
        self.web_check.setText(tr("header.web"))
        self.think_check.setText(tr("header.think"))
        self.btn_cache.setToolTip(tr('header.cache.tooltip'))
        self.btn_optimize.setToolTip(tr('header.optimize.tooltip'))
        self.btn_update.setToolTip(tr('header.update.tooltip'))
        self.btn_font_scale.setToolTip(tr('header.font.tooltip'))
        self.btn_key.setText(tr('menu.api_key'))
        self.btn_clear.setText(tr('btn.clear'))
        self.btn_cache.setText(tr('menu.cache'))
        self.btn_optimize.setText(tr('menu.optimize'))
        self.btn_update.setText(tr('menu.update'))
        # 同步下拉框选中项（防止外部调用 set_language 后不同步）
        lang = get_language()
        expected_idx = 0 if lang == 'zh' else 1
        if self.lang_combo.currentIndex() != expected_idx:
            self.lang_combo.blockSignals(True)
            self.lang_combo.setCurrentIndex(expected_idx)
            self.lang_combo.blockSignals(False)

    # ============================================================
    # Custom Provider 配置
    # ============================================================

    def _load_custom_provider_config(self):
        """从持久化配置文件加载 Custom Provider 设置"""
        try:
            from shared.common_utils import load_config
            cfg, _ = load_config('ai', dcc_type='houdini')
            if cfg:
                profiles = []
                profiles_raw = cfg.get('custom_profiles', '')
                if profiles_raw:
                    try:
                        profiles = json.loads(profiles_raw)
                    except Exception as e:
                        print(f"[Header] Failed to parse custom_profiles: {e}")

                models_str = cfg.get('custom_models', '')
                legacy_models = []
                if models_str:
                    legacy_models = _split_custom_models(models_str)
                try:
                    legacy_context_limit = int(cfg.get('custom_context_limit', '128000'))
                except (ValueError, TypeError):
                    legacy_context_limit = 128000

                legacy_profile = {
                    'name': cfg.get('custom_profile_name', 'Custom 1'),
                    'api_url': cfg.get('custom_api_url', ''),
                    'api_key': cfg.get('custom_api_key', ''),
                    'protocol': cfg.get('custom_protocol', 'openai'),
                    'models': legacy_models,
                    'manual_models': [],
                    'fetched_models': [],
                    'hidden_models': [],
                    'enabled_models': _split_custom_models(cfg.get('custom_enabled_models', '')),
                    'context_limit': legacy_context_limit,
                    'supports_vision': cfg.get('custom_supports_vision', 'false').lower() == 'true',
                    'supports_fc': cfg.get('custom_supports_fc', 'true').lower() != 'false',
                }
                normalized_profiles = _normalize_custom_profiles(profiles)
                if not normalized_profiles and (legacy_profile['api_url'] or legacy_profile['models']):
                    normalized_profiles = _normalize_custom_profiles([legacy_profile])
                if not normalized_profiles:
                    normalized_profiles = _normalize_custom_profiles([legacy_profile])

                primary = normalized_profiles[0]
                self._custom_provider_config.update(primary)
                self._custom_provider_config['profiles'] = normalized_profiles
                self._custom_provider_config['models'] = _flatten_custom_models(normalized_profiles)
                self._custom_provider_config['manual_models'] = primary.get('manual_models', [])
                self._custom_provider_config['fetched_models'] = primary.get('fetched_models', [])
                self._custom_provider_config['hidden_models'] = primary.get('hidden_models', [])
                # 更新模型列表
                self._model_map['custom'] = self._custom_provider_config['models']
                # 同步到 AIClient（如果已初始化）
                self._sync_custom_to_client()
        except Exception as e:
            print(f"[Header] 加载 Custom 配置失败: {e}")

    def _save_custom_provider_config(self):
        """将 Custom Provider 设置持久化到配置文件"""
        try:
            from shared.common_utils import load_config, save_config
            cfg, _ = load_config('ai', dcc_type='houdini')
            cfg = cfg or {}
            cc = self._custom_provider_config
            profiles = _normalize_custom_profiles(cc.get('profiles') or [cc])
            primary = profiles[0] if profiles else {}
            cfg['custom_profiles'] = json.dumps(profiles, ensure_ascii=False, separators=(',', ':'))
            cfg['custom_profile_name'] = primary.get('name', 'Custom 1')
            cfg['custom_api_url'] = primary.get('api_url', '')
            cfg['custom_api_key'] = primary.get('api_key', '')
            cfg['custom_protocol'] = primary.get('protocol', 'openai')
            cfg['custom_models'] = ','.join(_flatten_custom_models(profiles))
            cfg['custom_enabled_models'] = ','.join(primary.get('enabled_models', []))
            cfg['custom_context_limit'] = str(primary.get('context_limit', 128000))
            cfg['custom_supports_vision'] = 'true' if primary.get('supports_vision', False) else 'false'
            cfg['custom_supports_fc'] = 'true' if primary.get('supports_fc', True) else 'false'
            save_config('ai', cfg, dcc_type='houdini')
        except Exception as e:
            print(f"[Header] 保存 Custom 配置失败: {e}")

    def _sync_custom_to_client(self):
        """将 Custom 配置同步到 AIClient"""
        try:
            client = getattr(self, 'client', None)
            if client is None:
                return
            cc = self._custom_provider_config
            profiles = _normalize_custom_profiles(cc.get('profiles') or [cc])
            if profiles:
                client.set_custom_provider(
                    api_url=profiles[0].get('api_url', ''),
                    api_key=profiles[0].get('api_key', ''),
                    supports_fc=profiles[0].get('supports_fc', True),
                    profiles=profiles,
                )
        except Exception as e:
            print(f"[Header] 同步 Custom 配置到 Client 失败: {e}")

    def _on_provider_changed_custom_visibility(self):
        """Provider 切换时更新 Custom 配置按钮可见性。"""
        provider = self._current_provider()
        is_custom = (provider == 'custom')
        self.btn_custom_config.setVisible(is_custom)
        # 模型选择始终是枚举选择；自定义模型只在配置弹窗中维护。
        self.model_combo.setEditable(False)
        if is_custom and not self._custom_provider_config.get('api_url') and not self._custom_provider_config.get('profiles'):
            # 首次选择 Custom 且未配置，自动弹出配置对话框
            QtCore.QTimer.singleShot(100, self._open_custom_provider_dialog)

    def _open_custom_provider_dialog(self):
        """打开 Custom Provider 配置对话框"""
        dlg = _CustomProviderDialog(self._custom_provider_config, parent=self)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            new_cfg = dlg.get_config()
            self._custom_provider_config.update(new_cfg)
            # 更新模型列表
            self._model_map['custom'] = new_cfg['models']
            # 动态注册模型特性和上下文限制
            self._register_custom_model_features(new_cfg.get('profiles', []))
            # 同步到 AIClient
            self._sync_custom_to_client()
            # 持久化
            self._save_custom_provider_config()
            # 刷新 UI
            if self._current_provider() == 'custom':
                current_model = self.model_combo.currentText()
                self._refresh_models('custom')
                if current_model in new_cfg['models']:
                    self.model_combo.setCurrentText(current_model)
                self._update_key_status()
                self._update_context_stats()


class _CustomProviderDialog(QtWidgets.QDialog):
    """Custom Provider 配置对话框 — 配置 API URL、Key、模型名等"""

    def __init__(self, current_config: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("custom.title"))
        self.setMinimumWidth(460)
        self.setObjectName("customProviderDialog")
        self._profiles = self._profiles_from_config(current_config)
        self._profiles = _dedupe_custom_profile_names(self._profiles)
        self._profile_index = 0
        self._loading_profile = False
        self._build_ui(current_config)

    def _profiles_from_config(self, cfg: dict) -> list:
        profiles = _normalize_custom_profiles(cfg.get('profiles'))
        if profiles:
            return profiles
        legacy = {
            'name': cfg.get('name', 'Custom 1'),
            'api_url': cfg.get('api_url', ''),
            'api_key': cfg.get('api_key', ''),
            'protocol': cfg.get('protocol', 'openai'),
            'models': cfg.get('models', []),
            'enabled_models': cfg.get('enabled_models', []),
            'context_limit': cfg.get('context_limit', 128000),
            'supports_vision': cfg.get('supports_vision', False),
            'supports_fc': cfg.get('supports_fc', True),
        }
        return _normalize_custom_profiles([legacy])

    def _build_ui(self, cfg: dict):
        active_profile = self._profiles[0]
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        # 说明
        info = QtWidgets.QLabel(
            tr("custom.info")
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: #aaa; font-size: {ThemeEngine.scaled_px(12)}px; margin-bottom: 4px;")
        layout.addWidget(info)

        form = QtWidgets.QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)

        profile_row = QtWidgets.QHBoxLayout()
        profile_row.setSpacing(4)
        self._profile_combo = QtWidgets.QComboBox()
        self._profile_combo.setMinimumHeight(28)
        self._profile_combo.setEditable(False)
        self._profile_combo.setMinimumWidth(120)
        self._profile_combo.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        for profile in self._profiles:
            self._profile_combo.addItem(profile.get('name', 'Custom'))
        self._profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        profile_row.addWidget(self._profile_combo)

        self._profile_name_edit = QtWidgets.QLineEdit()
        self._profile_name_edit.setMinimumHeight(28)
        self._profile_name_edit.setText(active_profile.get('name', 'Custom 1'))
        self._profile_name_edit.setPlaceholderText("配置组名称")
        self._profile_name_edit.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self._profile_name_edit.textEdited.connect(self._on_profile_name_edited)
        self._profile_name_edit.editingFinished.connect(self._on_profile_name_editing_finished)
        profile_row.addWidget(self._profile_name_edit, 1)

        self._btn_add_profile = QtWidgets.QPushButton()
        self._btn_add_profile.setFixedSize(28, 28)
        self._btn_add_profile.setIcon(_make_line_icon("add"))
        self._btn_add_profile.setIconSize(QtCore.QSize(18, 18))
        self._btn_add_profile.setToolTip("新增一组 URL / API Key")
        self._btn_add_profile.clicked.connect(self._add_profile)
        profile_row.addWidget(self._btn_add_profile)

        self._btn_delete_profile = QtWidgets.QPushButton()
        self._btn_delete_profile.setFixedSize(28, 28)
        self._btn_delete_profile.setIcon(_make_line_icon("delete"))
        self._btn_delete_profile.setIconSize(QtCore.QSize(18, 18))
        self._btn_delete_profile.setToolTip("删除当前配置组")
        self._btn_delete_profile.clicked.connect(self._delete_profile)
        profile_row.addWidget(self._btn_delete_profile)
        form.addRow("配置组:", profile_row)

        # API URL
        self._url_edit = QtWidgets.QLineEdit()
        self._url_edit.setPlaceholderText(tr("custom.url_placeholder"))
        self._url_edit.setText(active_profile.get('api_url', ''))
        self._url_edit.setMinimumHeight(28)
        form.addRow("API URL:", self._url_edit)

        self._protocol_combo = QtWidgets.QComboBox()
        self._protocol_combo.setMinimumHeight(28)
        self._protocol_combo.addItem("OpenAI Compatible", 'openai')
        self._protocol_combo.addItem("Anthropic Messages", 'anthropic')
        protocol_index = self._protocol_combo.findData(active_profile.get('protocol', 'openai'))
        self._protocol_combo.setCurrentIndex(max(0, protocol_index))
        form.addRow("Protocol:", self._protocol_combo)

        # API Key
        self._key_edit = QtWidgets.QLineEdit()
        self._key_edit.setPlaceholderText(tr("custom.key_placeholder"))
        self._key_edit.setText(active_profile.get('api_key', ''))
        self._key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        self._key_edit.setMinimumHeight(28)
        # 显示/隐藏按钮
        key_row = QtWidgets.QHBoxLayout()
        key_row.setSpacing(4)
        key_row.addWidget(self._key_edit)
        self._btn_show_key = QtWidgets.QPushButton()
        self._btn_show_key.setFixedSize(28, 28)
        self._btn_show_key.setIcon(_make_line_icon("eye"))
        self._btn_show_key.setIconSize(QtCore.QSize(18, 18))
        self._btn_show_key.setToolTip("显示 / 隐藏 API Key")
        self._btn_show_key.setCheckable(True)
        self._btn_show_key.toggled.connect(
            lambda checked: self._key_edit.setEchoMode(
                QtWidgets.QLineEdit.Normal if checked else QtWidgets.QLineEdit.Password
            )
        )
        key_row.addWidget(self._btn_show_key)
        form.addRow("API Key:", key_row)

        # 模型列表：勾选项会显示在主界面；全不勾选表示全部显示。
        self._models_list = QtWidgets.QListWidget()
        self._models_list.setMinimumHeight(120)
        self._models_list.setToolTip("勾选后仅在主界面显示勾选模型；全不勾选则显示全部模型")
        self._models_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._models_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self._models_list.customContextMenuRequested.connect(self._show_model_list_menu)
        self._models_list.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding
        )
        self._set_model_names(
            active_profile.get('models', []),
            active_profile.get('enabled_models', []),
        )

        models_row_widget = QtWidgets.QWidget()
        models_row_widget.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding
        )
        models_row = QtWidgets.QHBoxLayout(models_row_widget)
        models_row.setSpacing(4)
        models_row.addWidget(self._models_list, 1)

        self._btn_fetch_models = QtWidgets.QPushButton()
        self._btn_fetch_models.setFixedSize(28, 28)
        self._btn_fetch_models.setIcon(_make_line_icon("refresh"))
        self._btn_fetch_models.setIconSize(QtCore.QSize(18, 18))
        self._btn_fetch_models.setToolTip(tr("custom.fetch_models"))
        self._btn_fetch_models.clicked.connect(self._fetch_models)
        models_row.addWidget(self._btn_fetch_models)
        models_row.setAlignment(self._btn_fetch_models, QtCore.Qt.AlignTop)

        self._btn_add_model = QtWidgets.QPushButton()
        self._btn_add_model.setFixedSize(28, 28)
        self._btn_add_model.setIcon(_make_line_icon("add"))
        self._btn_add_model.setIconSize(QtCore.QSize(18, 18))
        self._btn_add_model.setToolTip(tr("custom.add_model"))
        self._btn_add_model.clicked.connect(self._add_model_manually)
        models_row.addWidget(self._btn_add_model)
        models_row.setAlignment(self._btn_add_model, QtCore.Qt.AlignTop)

        self._btn_remove_model = QtWidgets.QPushButton()
        self._btn_remove_model.setFixedSize(28, 28)
        self._btn_remove_model.setIcon(_make_line_icon("delete"))
        self._btn_remove_model.setIconSize(QtCore.QSize(18, 18))
        self._btn_remove_model.setToolTip(tr("custom.remove_model"))
        self._btn_remove_model.clicked.connect(self._remove_selected_model)
        models_row.addWidget(self._btn_remove_model)
        models_row.setAlignment(self._btn_remove_model, QtCore.Qt.AlignTop)

        form.addRow(tr("custom.models"), models_row_widget)

        # 上下文长度
        self._ctx_spin = QtWidgets.QSpinBox()
        self._ctx_spin.setRange(1024, 10000000)
        self._ctx_spin.setSingleStep(1024)
        self._ctx_spin.setValue(active_profile.get('context_limit', 128000))
        self._ctx_spin.setSuffix(" tokens")
        self._ctx_spin.setMinimumHeight(28)
        form.addRow(tr("custom.context_length"), self._ctx_spin)

        # 特性开关
        features_row = QtWidgets.QHBoxLayout()
        features_row.setSpacing(12)
        self._chk_vision = QtWidgets.QCheckBox(tr("custom.supports_vision"))
        self._chk_vision.setChecked(active_profile.get('supports_vision', False))
        features_row.addWidget(self._chk_vision)
        self._chk_fc = QtWidgets.QCheckBox(tr("custom.supports_fc"))
        self._chk_fc.setChecked(active_profile.get('supports_fc', True))
        features_row.addWidget(self._chk_fc)
        features_row.addStretch()
        form.addRow(tr("custom.features"), features_row)

        layout.addLayout(form, 1)

        # 测试连接按钮
        test_row = QtWidgets.QHBoxLayout()
        test_row.addStretch()
        self._btn_test = QtWidgets.QPushButton(tr("custom.test"))
        self._btn_test.setMinimumWidth(100)
        self._btn_test.setMinimumHeight(28)
        self._btn_test.clicked.connect(self._test_connection)
        test_row.addWidget(self._btn_test)
        self._test_status = QtWidgets.QLabel("")
        self._test_status.setStyleSheet(f"font-size: {ThemeEngine.scaled_px(12)}px;")
        test_row.addWidget(self._test_status)
        test_row.addStretch()
        layout.addLayout(test_row)

        # 按钮
        btn_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        btn_box.button(QtWidgets.QDialogButtonBox.Ok).setText(tr("btn.ok"))
        btn_box.button(QtWidgets.QDialogButtonBox.Cancel).setText(tr("btn.cancel"))
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        # 样式
        self.setStyleSheet("""
            QDialog#customProviderDialog {
                background: #1e1e1e;
                color: #ddd;
            }
            QLabel { color: #ccc; }
            QLineEdit, QSpinBox, QComboBox, QListWidget {
                background: #2a2a2a;
                color: #eee;
                border: 1px solid #444;
                border-radius: 4px;
                padding: 4px 8px;
            }
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QListWidget:focus {
                border-color: #6a9eff;
            }
            QListWidget::item {
                min-height: 22px;
                padding: 1px 2px;
            }
            QListWidget::item:selected {
                background: rgba(58,90,138,90);
                color: #fff;
            }
            QComboBox::drop-down {
                border: none;
                width: 24px;
                subcontrol-position: center right;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 6px solid #aaa;
                margin-right: 6px;
            }
            QComboBox QAbstractItemView {
                background: #2a2a2a;
                color: #eee;
                border: 1px solid #555;
                selection-background-color: #3a5a8a;
                outline: none;
            }
            QCheckBox { color: #ccc; }
            QPushButton {
                background: #333;
                color: #ddd;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 4px 12px;
            }
            QPushButton:hover { background: #444; border-color: #6a9eff; }
        """)

    def _save_current_profile(self):
        if not self._profiles:
            return
        idx = max(0, min(self._profile_index, len(self._profiles) - 1))
        name = self._profile_name_edit.text().strip()
        name = self._unique_profile_name(name, idx)
        self._profiles[idx] = {
            'name': name,
            'api_url': _clean_custom_api_url(self._url_edit.text()),
            'api_key': self._key_edit.text().strip(),
            'protocol': self._protocol_combo.currentData() or 'openai',
            'models': self._model_names(),
            'manual_models': self._manual_model_names(),
            'fetched_models': self._fetched_model_names(),
            'hidden_models': self._hidden_model_names(),
            'enabled_models': self._enabled_model_names(),
            'context_limit': self._ctx_spin.value(),
            'supports_vision': self._chk_vision.isChecked(),
            'supports_fc': self._chk_fc.isChecked(),
        }
        self._profiles[idx].update(_normalize_custom_model_state(self._profiles[idx]))
        self._set_profile_combo_item_text(idx, name)
        self._set_profile_name_text(name)

    def _unique_profile_name(self, name: str, index: int) -> str:
        base = _strip_generated_name_suffix(name) or f'Custom {index + 1}'
        used = {
            str(profile.get('name') or '').strip()
            for i, profile in enumerate(self._profiles)
            if i != index
        }
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base} {suffix}"
            suffix += 1
        return candidate

    def _set_profile_combo_item_text(self, index: int, name: str):
        """Keep the profile selector item text in sync with the name editor."""
        if index < 0 or index >= self._profile_combo.count():
            return
        if self._profile_combo.itemText(index) == name:
            return
        self._profile_combo.blockSignals(True)
        try:
            self._profile_combo.setItemText(index, name)
        finally:
            self._profile_combo.blockSignals(False)

    def _set_profile_name_text(self, name: str):
        if self._profile_name_edit.text() == name:
            return
        self._profile_name_edit.blockSignals(True)
        try:
            self._profile_name_edit.setText(name)
        finally:
            self._profile_name_edit.blockSignals(False)

    def _refresh_profile_combo_items(self):
        """Rebuild combo items from _profiles after normalization/deduplication."""
        current_index = max(0, min(self._profile_index, len(self._profiles) - 1))
        self._profile_combo.blockSignals(True)
        try:
            self._profile_combo.clear()
            for idx, profile in enumerate(self._profiles):
                self._profile_combo.addItem(profile.get('name') or f'Custom {idx + 1}')
            if self._profiles:
                self._profile_combo.setCurrentIndex(current_index)
                self._set_profile_name_text(
                    self._profiles[current_index].get('name') or f'Custom {current_index + 1}'
                )
        finally:
            self._profile_combo.blockSignals(False)

    def _load_profile(self, index: int):
        if index < 0 or index >= len(self._profiles):
            return
        profile = self._profiles[index]
        self._loading_profile = True
        try:
            self._profile_combo.setCurrentIndex(index)
            self._set_profile_name_text(profile.get('name', f'Custom {index + 1}'))
            self._url_edit.setText(_clean_custom_api_url(profile.get('api_url', '')))
            self._key_edit.setText(profile.get('api_key', ''))
            protocol_index = self._protocol_combo.findData(profile.get('protocol', 'openai'))
            self._protocol_combo.setCurrentIndex(max(0, protocol_index))
            state = _normalize_custom_model_state(profile)
            profile.update(state)
            self._profiles[index] = profile
            self._set_model_names(state['models'], state['enabled_models'])
            self._ctx_spin.setValue(profile.get('context_limit', 128000))
            self._chk_vision.setChecked(profile.get('supports_vision', False))
            self._chk_fc.setChecked(profile.get('supports_fc', True))
        finally:
            self._loading_profile = False

    def _on_profile_name_edited(self, text: str):
        if self._loading_profile or not self._profiles:
            return
        idx = self._profile_index
        if idx < 0 or idx >= len(self._profiles):
            return
        name = (text or '').strip() or f'Custom {idx + 1}'
        self._set_profile_combo_item_text(idx, name)

    def _on_profile_name_editing_finished(self):
        if self._loading_profile or not self._profiles:
            return
        self._save_current_profile()

    def _on_profile_changed(self, index: int):
        if self._loading_profile or index < 0 or index == self._profile_index:
            return
        self._save_current_profile()
        self._profile_index = index
        self._load_profile(index)

    def _add_profile(self):
        self._save_current_profile()
        index = len(self._profiles) + 1
        existing_names = {str(p.get('name') or '').strip() for p in self._profiles}
        profile_name = f'Custom {index}'
        while profile_name in existing_names:
            index += 1
            profile_name = f'Custom {index}'
        profile = {
            'name': profile_name,
            'api_url': '',
            'api_key': '',
            'protocol': 'openai',
            'models': [],
            'enabled_models': [],
            'context_limit': 128000,
            'supports_vision': False,
            'supports_fc': True,
        }
        self._profiles.append(profile)
        self._loading_profile = True
        self._profile_combo.addItem(profile['name'])
        self._loading_profile = False
        self._profile_index = len(self._profiles) - 1
        self._load_profile(self._profile_index)

    def _delete_profile(self):
        if len(self._profiles) <= 1:
            self._profile_name_edit.setText('Custom 1')
            self._set_profile_combo_item_text(0, 'Custom 1')
            self._url_edit.clear()
            self._key_edit.clear()
            self._protocol_combo.setCurrentIndex(0)
            self._set_model_names([])
            self._ctx_spin.setValue(128000)
            self._chk_vision.setChecked(False)
            self._chk_fc.setChecked(True)
            self._save_current_profile()
            return
        idx = self._profile_index
        self._profiles.pop(idx)
        self._loading_profile = True
        self._profile_combo.removeItem(idx)
        self._loading_profile = False
        self._profile_index = min(idx, len(self._profiles) - 1)
        self._load_profile(self._profile_index)

    def _model_names(self) -> list:
        """Return all model names from the model list."""
        names = []
        seen = set()
        for row in range(self._models_list.count()):
            name = self._models_list.item(row).text().strip()
            if name and name not in seen:
                names.append(name)
                seen.add(name)
        return names

    def _manual_model_names(self) -> list:
        profile = self._profiles[self._profile_index] if self._profiles else {}
        return list(profile.get('manual_models') or [])

    def _fetched_model_names(self) -> list:
        profile = self._profiles[self._profile_index] if self._profiles else {}
        return list(profile.get('fetched_models') or [])

    def _hidden_model_names(self) -> list:
        profile = self._profiles[self._profile_index] if self._profiles else {}
        return list(profile.get('hidden_models') or [])

    def _enabled_model_names(self) -> list:
        """Return checked model names. Empty means all models are visible."""
        names = []
        for row in range(self._models_list.count()):
            item = self._models_list.item(row)
            if item.checkState() == QtCore.Qt.Checked:
                names.append(item.text().strip())
        return [name for name in names if name]

    def _test_model_names(self) -> list:
        enabled = self._enabled_model_names()
        return enabled or self._model_names()

    def _set_model_state(self, models: list = None, enabled_models: list = None,
                         manual_models: list = None, fetched_models: list = None,
                         hidden_models: list = None):
        if not self._profiles:
            return
        idx = max(0, min(self._profile_index, len(self._profiles) - 1))
        profile = self._profiles[idx]
        if models is not None:
            profile['models'] = _normalize_custom_model_list(models)
        if enabled_models is not None:
            profile['enabled_models'] = [name for name in _normalize_custom_model_list(enabled_models) if name in profile.get('models', [])]
        if manual_models is not None:
            profile['manual_models'] = _normalize_custom_model_list(manual_models)
        if fetched_models is not None:
            profile['fetched_models'] = _normalize_custom_model_list(fetched_models)
        if hidden_models is not None:
            profile['hidden_models'] = _normalize_custom_model_list(hidden_models)
        state = _normalize_custom_model_state(profile)
        profile.update(state)
        self._profiles[idx] = profile
        return profile

    def _merge_model_state(self, models: list = None, manual_models: list = None,
                           fetched_models: list = None, hidden_models: list = None):
        profile = self._profiles[self._profile_index] if self._profiles else {}
        current = _normalize_custom_model_state(profile)
        base_models = _normalize_custom_model_list(models if models is not None else current['models'])
        manual = _normalize_custom_model_list(manual_models if manual_models is not None else current['manual_models'])
        fetched = _normalize_custom_model_list(fetched_models if fetched_models is not None else current['fetched_models'])
        hidden = _normalize_custom_model_list(hidden_models if hidden_models is not None else current['hidden_models'])
        hidden_set = set(hidden)
        merged = []
        seen = set()
        for name in manual + fetched + base_models:
            if name and name not in hidden_set and name not in seen:
                merged.append(name)
                seen.add(name)
        enabled = [name for name in current['enabled_models'] if name in merged]
        return self._set_model_state(
            models=merged,
            enabled_models=enabled,
            manual_models=manual,
            fetched_models=fetched,
            hidden_models=hidden,
        )

    def _set_model_names(self, models: list, enabled_models: list = None):
        """Replace the model list and restore checked visible-model selections."""
        unique = _normalize_custom_model_list(models)
        enabled_set = set(_normalize_custom_model_list(enabled_models or []))
        self._models_list.clear()
        for name in unique:
            item = QtWidgets.QListWidgetItem(name)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked if name in enabled_set else QtCore.Qt.Unchecked)
            self._models_list.addItem(item)

    def _rebuild_model_list(self):
        if not self._profiles:
            self._models_list.clear()
            return
        profile = self._profiles[self._profile_index]
        state = _normalize_custom_model_state(profile)
        self._set_model_names(state['models'], state['enabled_models'])

    def _prompt_model_name(self, title: str, label: str, default: str = ''):
        text, ok = QtWidgets.QInputDialog.getText(self, title, label, text=default)
        if not ok:
            return ''
        return str(text or '').strip()

    def _add_model_manually(self):
        name = self._prompt_model_name(
            tr('custom.add_model'),
            tr('custom.add_model'),
            self._model_names()[0] if self._model_names() else '',
        )
        if not name:
            return
        profile = self._set_model_state(
            manual_models=self._manual_model_names() + [name],
            hidden_models=[m for m in self._hidden_model_names() if m != name],
        )
        if profile:
            self._rebuild_model_list()
            self._save_current_profile()

    def _remove_selected_model(self):
        item = self._models_list.currentItem()
        if item is None:
            return
        name = item.text().strip()
        if not name:
            return
        profile = self._profiles[self._profile_index] if self._profiles else {}
        current = _normalize_custom_model_state(profile)
        manual = [m for m in current['manual_models'] if m != name]
        fetched = [m for m in current['fetched_models'] if m != name]
        hidden = current['hidden_models'] + [name]
        self._set_model_state(manual_models=manual, fetched_models=fetched, hidden_models=hidden)
        self._rebuild_model_list()
        self._save_current_profile()

    def _show_model_list_menu(self, pos):
        menu = QtWidgets.QMenu(self)
        add_action = menu.addAction(tr('custom.add_model'))
        remove_action = menu.addAction(tr('custom.remove_model'))
        menu.addSeparator()
        refresh_action = menu.addAction(tr('custom.fetch_models'))
        action = menu.exec_(self._models_list.mapToGlobal(pos))
        if action == add_action:
            self._add_model_manually()
        elif action == remove_action:
            self._remove_selected_model()
        elif action == refresh_action:
            self._fetch_models()

    def _fetch_models(self):
        """从 API 获取可用模型列表并填充到模型列表"""
        url = _clean_custom_api_url(self._url_edit.text())
        key = self._key_edit.text().strip()
        protocol = self._protocol_combo.currentData() or 'openai'

        if not url:
            self._test_status.setText(tr("custom.need_url"))
            self._test_status.setStyleSheet(f"color: #f5a623; font-size: {ThemeEngine.scaled_px(12)}px;")
            return

        self._btn_fetch_models.setEnabled(False)
        self._test_status.setText(tr("custom.fetching_models"))
        self._test_status.setStyleSheet(f"color: #aaa; font-size: {ThemeEngine.scaled_px(12)}px;")

        try:
            import requests
            models_url = (
                normalize_custom_anthropic_models_url(url)
                if protocol == 'anthropic'
                else normalize_custom_models_url(url)
            )

            headers = {'Content-Type': 'application/json'}
            if protocol == 'anthropic':
                headers['anthropic-version'] = '2023-06-01'
                if key:
                    headers['x-api-key'] = key
            elif key:
                headers['Authorization'] = f'Bearer {key}'

            resp = requests.get(models_url, headers=headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                model_ids = [m.get('id', '') for m in data.get('data', []) if m.get('id')]
                if model_ids:
                    self._merge_model_state(
                        models=sorted(model_ids),
                        fetched_models=sorted(model_ids),
                        hidden_models=self._hidden_model_names(),
                    )
                    self._rebuild_model_list()
                    self._test_status.setText(tr("custom.models_found", len(model_ids)))
                    self._test_status.setStyleSheet(f"color: #4caf50; font-size: {ThemeEngine.scaled_px(12)}px;")
                else:
                    self._test_status.setText(tr("custom.no_models"))
                    self._test_status.setStyleSheet(f"color: #f5a623; font-size: {ThemeEngine.scaled_px(12)}px;")
            else:
                err = resp.text[:120]
                self._test_status.setText(f"❌ HTTP {resp.status_code}: {err}")
                self._test_status.setStyleSheet(f"color: #f44336; font-size: {ThemeEngine.scaled_px(12)}px;")
        except Exception as e:
            self._test_status.setText(f"❌ {str(e)[:100]}")
            self._test_status.setStyleSheet(f"color: #f44336; font-size: {ThemeEngine.scaled_px(12)}px;")
        finally:
            self._btn_fetch_models.setEnabled(True)

    def _test_connection(self):
        """测试 Custom API 连接"""
        url = _clean_custom_api_url(self._url_edit.text())
        key = self._key_edit.text().strip()
        protocol = self._protocol_combo.currentData() or 'openai'
        models = self._test_model_names()
        model = models[0] if models else 'test'

        if not url:
            self._test_status.setText(tr("custom.need_url"))
            self._test_status.setStyleSheet(f"color: #f5a623; font-size: {ThemeEngine.scaled_px(12)}px;")
            return

        test_url = normalize_custom_messages_url(url) if protocol == 'anthropic' else normalize_custom_chat_url(url)

        self._btn_test.setEnabled(False)
        self._test_status.setText(tr("custom.connecting"))
        self._test_status.setStyleSheet(f"color: #aaa; font-size: {ThemeEngine.scaled_px(12)}px;")

        try:
            import requests
            headers = {'Content-Type': 'application/json'}
            if protocol == 'anthropic':
                headers['anthropic-version'] = '2023-06-01'
                if key:
                    headers['x-api-key'] = key
            elif key:
                headers['Authorization'] = f'Bearer {key}'
            if protocol == 'anthropic':
                payload = {
                    'model': model,
                    'messages': [{'role': 'user', 'content': 'Hi'}],
                    'max_tokens': 5,
                }
            else:
                payload = {
                    'model': model,
                    'messages': [{'role': 'user', 'content': 'Hi'}],
                    'max_tokens': 5,
                    'stream': False,
                }
            resp = requests.post(test_url, json=payload, headers=headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                recv_model = data.get('model', model)
                self._merge_model_state(
                    manual_models=self._manual_model_names() + [recv_model],
                    hidden_models=[m for m in self._hidden_model_names() if m != recv_model],
                )
                self._rebuild_model_list()
                self._test_status.setText(tr("custom.connect_ok", recv_model))
                self._test_status.setStyleSheet(f"color: #4caf50; font-size: {ThemeEngine.scaled_px(12)}px;")
            else:
                err = resp.text[:120]
                self._test_status.setText(f"❌ HTTP {resp.status_code}: {err}")
                self._test_status.setStyleSheet(f"color: #f44336; font-size: {ThemeEngine.scaled_px(12)}px;")
        except Exception as e:
            self._test_status.setText(f"❌ {str(e)[:100]}")
            self._test_status.setStyleSheet(f"color: #f44336; font-size: {ThemeEngine.scaled_px(12)}px;")
        finally:
            self._btn_test.setEnabled(True)

    def _on_accept(self):
        """确认前校验必填项"""
        self._save_current_profile()
        profiles = _dedupe_custom_profile_names([
            p for p in _normalize_custom_profiles(self._profiles)
            if p.get('api_url') or p.get('models') or p.get('manual_models') or p.get('fetched_models')
        ])
        self._profiles = profiles
        self._refresh_profile_combo_items()
        if not profiles or any(not p.get('api_url') for p in profiles):
            QtWidgets.QMessageBox.warning(self, tr("dialog.notice"), tr("custom.need_url_plain"))
            return
        if any(not p.get('models') for p in profiles):
            QtWidgets.QMessageBox.warning(self, tr("dialog.notice"), tr("custom.need_model"))
            return
        self.accept()

    def get_config(self) -> dict:
        """返回用户配置的字典"""
        self._save_current_profile()
        profiles = _dedupe_custom_profile_names([
            p for p in _normalize_custom_profiles(self._profiles)
            if p.get('api_url') or p.get('models') or p.get('manual_models') or p.get('fetched_models')
        ])
        profiles = _dedupe_custom_profile_names([dict(p, **_normalize_custom_model_state(p)) for p in profiles])
        self._profiles = profiles
        self._refresh_profile_combo_items()
        primary = profiles[0] if profiles else {}
        models = _flatten_custom_models(profiles)
        return {
            'name': primary.get('name', 'Custom 1'),
            'api_url': primary.get('api_url', ''),
            'api_key': primary.get('api_key', ''),
            'protocol': primary.get('protocol', 'openai'),
            'models': models,
            'context_limit': primary.get('context_limit', 128000),
            'supports_vision': primary.get('supports_vision', False),
            'supports_fc': primary.get('supports_fc', True),
            'profiles': profiles,
        }
