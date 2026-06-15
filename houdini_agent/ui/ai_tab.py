# -*- coding: utf-8 -*-
"""
Houdini Agent - AI Tab
Agent loop, multi-turn tool calling, streaming UI

模块拆分结构:
  ui/header.py            — HeaderMixin: 顶部设置栏构建
  ui/input_area.py        — InputAreaMixin: 输入区域和模式切换
  ui/chat_view.py         — ChatViewMixin: 对话显示和滚动逻辑
  core/agent_runner.py    — AgentRunnerMixin: Agent 循环和工具调度
  core/session_manager.py — SessionManagerMixin: 多会话管理和缓存
  ui/memory_mixin.py      — MemoryMixin: 长期记忆 + 插件 Hook 系统
  ui/context_mixin.py     — ContextMixin: 字体缩放、上下文统计、模型管理
  ui/streaming_mixin.py   — StreamingMixin: 流式内容 + <think> 解析
  ui/plan_mixin.py        — PlanMixin: 计划模式 UI 逻辑
  ui/image_mixin.py       — ImageMixin: 多模态图片处理
  core/cache_mixin.py     — CacheMixin: 会话缓存保存/恢复/存档
  ui/history_mixin.py     — HistoryMixin: 对话历史渲染
  core/update_mixin.py    — UpdateMixin: Token 优化菜单 + 自动更新
"""

import json
import logging
import math
import os
import threading
import time
import uuid
import queue

logger = logging.getLogger(__name__)
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from houdini_agent.qt_compat import QtWidgets, QtCore, QtGui, QSettings, invoke_on_main

from .i18n import tr, get_language
from ..utils.ai_client import AIClient, HOUDINI_TOOLS
from ..utils.mcp import HoudiniMCP
from ..utils.token_optimizer import TokenOptimizer, TokenBudget, CompressionStrategy
from ..utils.ultra_optimizer import UltraOptimizer
from .theme_engine import ThemeEngine
from .font_settings_dialog import FontSettingsDialog
from .cursor_widgets import (
    CursorTheme,
    UserMessage,
    AIResponse,
    PlanBlock,
    PlanViewer,
    StreamingPlanCard,
    AskQuestionCard,
    CollapsibleContent,
    StatusLine,
    ChatInput,
    SendButton,
    StopButton,
    TodoList,
    NodeOperationLabel,
    NodeContextBar,
    PythonShellWidget,
    SystemShellWidget,
    ClickableImageLabel,
    ToolStatusBar,
    NodeCompleterPopup,
    StreamingCodePreview,
    UpdateNotificationBanner,
)
import re

# Mixin 模块（从 ai_tab.py 拆分出的子模块）
from .header import HeaderMixin
from .input_area import InputAreaMixin
from .chat_view import ChatViewMixin
from ..core.agent_runner import AgentRunnerMixin
from ..core.session_manager import SessionManagerMixin
from .memory_mixin import MemoryMixin
from .context_mixin import ContextMixin
from .streaming_mixin import StreamingMixin
from .plan_mixin import PlanMixin
from .image_mixin import ImageMixin
from ..core.cache_mixin import CacheMixin
from .history_mixin import HistoryMixin
from ..core.update_mixin import UpdateMixin
from .run_mixin import RunMixin
from .system_prompt_mixin import SystemPromptMixin
from .context_trim_mixin import ContextTrimMixin

# ★ Plan 模式常量（agent_runner.py 中也需要引用）
from ..utils.plan_manager import get_plan_manager, PLAN_TOOL_CREATE, PLAN_TOOL_UPDATE_STEP, PLAN_TOOL_ASK_QUESTION


class AITab(
    HeaderMixin,
    InputAreaMixin,
    ChatViewMixin,
    AgentRunnerMixin,
    SessionManagerMixin,
    MemoryMixin,
    ContextMixin,
    StreamingMixin,
    PlanMixin,
    ImageMixin,
    CacheMixin,
    HistoryMixin,
    UpdateMixin,
    RunMixin,
    SystemPromptMixin,
    ContextTrimMixin,
    QtWidgets.QWidget,
):
    """AI 助手 - 极简侧边栏风格（Mixin 架构）"""
    
    # 信号（用于线程安全的 UI 更新）
    _appendContent = QtCore.Signal(str)
    _addStatus = QtCore.Signal(str)
    _updateThinkingTime = QtCore.Signal()
    _agentDone = QtCore.Signal(dict)
    _agentError = QtCore.Signal(str)
    _agentStopped = QtCore.Signal()
    _updateTodo = QtCore.Signal(str, str, str)  # (todo_id, text, status)
    _addNodeOperation = QtCore.Signal(str, object)  # (name, result_dict) ★ 直接传 dict，避免 JSON 序列化/反序列化开销
    _addPythonShell = QtCore.Signal(str, str)  # (code, result_json)
    _addSystemShell = QtCore.Signal(str, str)  # (command, result_json)
    _executeToolRequest = QtCore.Signal(str, dict)  # 工具执行请求信号（线程安全）
    _executeToolBatchRequest = QtCore.Signal(list)   # 批量工具执行请求：[(tool_name, kwargs), ...]
    _addThinking = QtCore.Signal(str)  # 思考内容更新信号（线程安全）
    _finalizeThinkingSignal = QtCore.Signal()  # 结束思考区块（线程安全）
    _resumeThinkingSignal = QtCore.Signal()    # 恢复思考区块（线程安全）
    _showToolStatus = QtCore.Signal(str)       # 显示工具执行状态（线程安全）
    _hideToolStatus = QtCore.Signal()          # 隐藏工具执行状态
    _showGenerating = QtCore.Signal()          # 显示 "Generating..." 状态（线程安全）
    _autoTitleDone = QtCore.Signal(str, str)   # 自动标题生成完成: (session_id, title)
    _confirmToolRequest = QtCore.Signal()  # 确认模式：请求确认（参数通过属性传递，避免 QueuedConnection dict 问题）
    _confirmToolResult = QtCore.Signal(bool)        # 确认模式：结果 (True=执行, False=取消)
    _toolArgsDelta = QtCore.Signal(str, str, str)   # 流式 VEX 预览: (tool_name, delta, accumulated)
    _showPlanning = QtCore.Signal(str)              # 显示 "Planning..." 进度 (progress_text)
    _createStreamingPlan = QtCore.Signal()           # 创建流式 Plan 预览卡片
    _updateStreamingPlan = QtCore.Signal(str)        # 更新流式 Plan 预览卡片内容 (accumulated_json)
    _renderPlanViewer = QtCore.Signal(dict)          # Plan 模式：在主线程渲染 PlanViewer 卡片
    _updatePlanStep = QtCore.Signal(str, str, str)   # Plan 模式：更新步骤状态 (step_id, status, result_summary)
    _askQuestionRequest = QtCore.Signal()             # Plan 模式：ask_question 请求（参数通过属性传递）
    
    def __init__(self, parent=None, workspace_dir: Optional[Path] = None):
        super().__init__(parent)

        # 启动断点日志：用于诊断冷启动 freeze（参见 issue #9）
        print("[AITab] init: begin")
        self.client = AIClient()
        self.mcp = HoudiniMCP()
        self.mcp.set_stop_event(self.client._stop_event)  # 共享停止事件，使 shell/python 命令可被中断
        self.client.set_tool_executor(self._execute_tool_with_todo)
        self.client.set_batch_tool_executor(self._execute_tools_batch_in_main_thread)
        
        # 状态
        self._conversation_history: List[Dict[str, Any]] = []
        self._pending_ops: list = []  # 追踪未决操作: [(label, op_type, paths, snapshot), ...]
        self._current_response: Optional[AIResponse] = None
        self._is_running = False
        self._thinking_timer: Optional[QtCore.QTimer] = None
        
        # Agent 运行锚点：记录发起请求的 session，保证回调写入正确的会话
        self._agent_session_id: Optional[str] = None
        self._agent_response: Optional[AIResponse] = None
        self._agent_scroll_area = None  # 运行中 session 的 scroll_area
        self._agent_history: Optional[List[Dict[str, Any]]] = None
        self._agent_token_stats: Optional[Dict] = None
        self._agent_todo_list = None       # 运行中 session 的 TodoList
        self._agent_chat_layout = None     # 运行中 session 的 chat_layout
        
        # 上下文管理
        self._max_context_messages = 20
        self._context_summary = ""
        # ★ 发送给 API 的工作上下文（None = 直接用 _conversation_history）
        # _conversation_history 作为永久存档，只追加从不裁剪；
        # _send_context 在上下文超限时由 _manage_context 裁剪，不影响存档与显示。
        self._send_context: Optional[List[Dict[str, Any]]] = None
        
        # 缓存管理
        self._session_id = str(uuid.uuid4())[:8]  # 当前会话 ID
        self._cache_dir = Path(__file__).parent.parent.parent / "cache" / "conversations"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._auto_save_cache = True  # 自动保存缓存
        self._workspace_dir = workspace_dir  # 工作区目录
        
        # 多会话管理
        self._sessions: Dict[str, dict] = {}   # session_id -> session state
        self._session_counter = 0               # 用于生成 tab 标签
        # ★ 纯 Python 备份：tab 顺序和标签名（atexit 时 Qt widget 可能已销毁）
        self._tabs_backup: list = []  # [(session_id, tab_label), ...]
        self._sessions_saved = False  # _save_all_sessions 是否已成功执行过
        
        # 静态内容缓存（只计算一次，节省 token 和计算时间）
        self._cached_optimized_system_prompt: Optional[str] = None
        self._cached_optimized_tools: Optional[List[dict]] = None
        self._cached_optimized_tools_no_web: Optional[List[dict]] = None
        
        # Token 优化器
        self.token_optimizer = TokenOptimizer()
        self._auto_optimize = True  # 自动优化
        self._optimization_strategy = CompressionStrategy.BALANCED
        
        # ★ Plan 模式状态
        self._plan_phase = 'idle'          # idle | planning | awaiting_confirmation | executing | completed
        self._active_plan_viewer = None    # 当前活跃的 PlanViewer 组件引用
        self._streaming_plan_card = None   # 流式 Plan 预览卡片（生成中临时使用）
        self._plan_manager = None          # PlanManager 实例（延迟初始化）
        
        # ★ 大脑启发式长期记忆系统（延迟初始化，避免阻塞 UI）
        self._memory_store = None
        self._reward_engine = None
        self._reflection_module = None
        self._growth_tracker = None
        self._memory_initialized = False
        # 全局开关：默认关闭，避免长期记忆把 agent 锁死在某种工作方式上。
        # 用户在 Header 溢出菜单（···）中可显式启用，状态持久化到 QSettings。
        self._memory_enabled = self._load_memory_enabled_pref()

        # ★ Cook 模式（v1.6）：默认实时模式（保持 Auto + 同步测量 cook 耗时）。
        # 在 Header 溢出菜单（···）可切换为保护模式（Manual）。
        self._cook_realtime_mode = self._load_cook_realtime_pref()
        # 实时 cook 临时挂起标记：本次运行中 cook 被中断/超时后置 True，
        # 退化为保护模式直到本次 Agent 运行结束（每次运行开始时重置）。
        self._cook_realtime_suspended = False

        # ★ 睡眠机制计数器
        self._sleep_msg_counter = 0       # 当前 session 累计用户消息数
        self._sleep_in_progress = False   # 防止并发睡眠

        QtCore.QTimer.singleShot(2000, self._init_memory_system)
        
        # 思考长度限制（已禁用，允许完整思考）
        self._max_thinking_length = float('inf')  # 不限制思考长度
        self._thinking_length_warning = float('inf')  # 不警告
        
        # 输出 Token 限制（不限制）
        self._max_output_tokens = float('inf')
        self._output_token_warning = float('inf')
        self._current_output_tokens = 0
        
        # <think> 标签流式解析状态
        self._in_think_block = False
        self._tag_parse_buf = ""
        self._thinking_needs_finalize = False  # 标记是否需要 finalize 思考区块
        self._think_enabled = True  # 当前会话是否启用思考显示（由 Think 开关控制）
        
        # 会话级节点路径映射：name → set[path]，用于后处理裸节点名 → 完整路径
        self._session_node_map: dict[str, set[str]] = {}
        
        # Token 使用统计（累积值，每轮对话叠加）—— 对齐 Cursor
        self._token_stats = {
            'input_tokens': 0,      # 输入 token 总数
            'output_tokens': 0,     # 输出 token 总数
            'reasoning_tokens': 0,  # 推理 token（输出的子集）
            'cache_read': 0,        # Cache 读取（命中）token
            'cache_write': 0,       # Cache 写入（未命中）token
            'total_tokens': 0,      # 总 token 数
            'requests': 0,          # 请求次数
            'estimated_cost': 0.0,  # 预估费用（USD）
        }
        self._call_records: list = []  # 每次 API 调用的详细记录（对齐 Cursor）
        
        # 工具执行线程安全机制（使用队列和锁避免竞争）
        self._tool_result_queue: queue.Queue = queue.Queue()
        self._tool_lock = threading.Lock()  # 确保一次只有一个工具调用
        self._main_thread_busy = False  # ★ 主线程忙标记（防止超时后堆积信号死锁）
        
        # 连接信号
        self._appendContent.connect(self._on_append_content)
        self._addStatus.connect(self._on_add_status)
        self._updateThinkingTime.connect(self._on_update_thinking)
        self._agentDone.connect(self._on_agent_done)
        self._agentError.connect(self._on_agent_error)
        self._agentStopped.connect(self._on_agent_stopped)
        self._updateTodo.connect(self._on_update_todo)
        self._addNodeOperation.connect(self._on_add_node_operation)
        self._addPythonShell.connect(self._on_add_python_shell)
        self._addSystemShell.connect(self._on_add_system_shell)
        self._executeToolRequest.connect(self._on_execute_tool_main_thread, QtCore.Qt.BlockingQueuedConnection)
        self._executeToolBatchRequest.connect(self._on_execute_tool_batch_main_thread, QtCore.Qt.BlockingQueuedConnection)
        self._addThinking.connect(self._on_add_thinking)
        self._finalizeThinkingSignal.connect(self._finalize_thinking_main_thread)
        self._resumeThinkingSignal.connect(self._resume_thinking_main_thread)
        self._showToolStatus.connect(self._on_show_tool_status)
        self._hideToolStatus.connect(self._on_hide_tool_status)
        self._showGenerating.connect(self._on_show_generating)
        self._autoTitleDone.connect(self._on_auto_title_done)
        self._confirmToolRequest.connect(self._on_confirm_tool_request, QtCore.Qt.QueuedConnection)
        self._toolArgsDelta.connect(self._on_tool_args_delta)
        self._showPlanning.connect(self._on_show_planning)
        self._createStreamingPlan.connect(self._on_create_streaming_plan, QtCore.Qt.QueuedConnection)
        self._updateStreamingPlan.connect(self._on_update_streaming_plan)
        self._renderPlanViewer.connect(self._on_render_plan_viewer, QtCore.Qt.QueuedConnection)
        self._updatePlanStep.connect(self._on_update_plan_step, QtCore.Qt.QueuedConnection)
        self._askQuestionRequest.connect(self._on_render_ask_question, QtCore.Qt.QueuedConnection)
        
        # ── 流式 VEX 预览状态 ──
        self._streaming_preview = None          # 当前的 StreamingCodePreview widget
        self._streaming_preview_tool = ""       # 正在流式预览的工具名
        self._streaming_last_code = ""          # 上次解析出的完整代码（用于增量 diff）
        
        # 构建并缓存系统提示词（两个版本：有思考 / 无思考）
        # ★ 启动优化：先用不含 DocIndex 的轻量 prompt（跳过 JSON 加载），
        #   DocIndex 在后台加载完成后自动触发 _rebuild_system_prompts() 补全。
        self._doc_index_ready = False
        self._system_prompt_think = self._build_system_prompt(with_thinking=True, skip_doc_index=True)
        self._system_prompt_no_think = self._build_system_prompt(with_thinking=False, skip_doc_index=True)
        self._cached_prompt_think = self.token_optimizer.optimize_system_prompt(
            self._system_prompt_think, max_length=1800
        )
        self._cached_prompt_no_think = self.token_optimizer.optimize_system_prompt(
            self._system_prompt_no_think, max_length=1500
        )
        # 兼容旧引用
        self._system_prompt = self._system_prompt_think
        self._cached_optimized_system_prompt = self._cached_prompt_think
        # 后台加载 DocIndex，完成后重建完整 prompt
        QtCore.QTimer.singleShot(0, self._warm_doc_index)
        print("[AITab] init: _build_ui begin")
        self._build_ui()
        print("[AITab] init: _build_ui done")
        self._wire_events()
        self._load_model_preference(restore_provider=True)
        self._update_key_status()
        self._update_context_stats()

        # ★ 启动时自动恢复上次的会话（从 sessions_manifest.json）
        print("[AITab] init: _restore_all_sessions begin")
        self._restore_all_sessions()
        print("[AITab] init: _restore_all_sessions done")
        
        self._destroyed = False

        # 定期自动保存（每 60 秒），防止 Houdini 退出时丢失会话
        self._auto_save_timer = QtCore.QTimer(self)
        self._auto_save_timer.timeout.connect(self._periodic_save_all)
        self._auto_save_timer.start(60_000)  # 60 秒
        
        # 注册 atexit 回调和 QApplication.aboutToQuit 信号
        import atexit
        atexit.register(self._atexit_save)
        app = QtWidgets.QApplication.instance()
        if app:
            app.aboutToQuit.connect(self._save_all_sessions)
        self.destroyed.connect(self._on_destroyed)
        
        # ★ 启动时静默检查更新（延迟 5 秒，不阻塞初始化）
        QtCore.QTimer.singleShot(5000, self._silent_update_check)
        
        # ★ 插件系统初始化（延迟 3 秒，不阻塞 UI）
        QtCore.QTimer.singleShot(3000, self._init_plugin_system)
        
        # ★ 语言切换时重建系统提示词 + 重新翻译 UI
        from .i18n import language_changed
        language_changed.changed.connect(self._rebuild_system_prompts)
        language_changed.changed.connect(self._retranslateUi)

    def _rebuild_system_prompts(self, _lang: str = ''):
        """语言切换后重建系统提示词（含 Ask/Agent 模式强制语言规则）"""
        self._system_prompt_think = self._build_system_prompt(with_thinking=True)
        self._system_prompt_no_think = self._build_system_prompt(with_thinking=False)
        self._cached_prompt_think = self.token_optimizer.optimize_system_prompt(
            self._system_prompt_think, max_length=1800
        )
        self._cached_prompt_no_think = self.token_optimizer.optimize_system_prompt(
            self._system_prompt_no_think, max_length=1800
        )
        self._system_prompt = self._system_prompt_think
        self._cached_optimized_system_prompt = self._cached_prompt_think
        print(f"[i18n] System prompts rebuilt for language: {_lang or get_language()}")

    def _retranslateUi(self, _lang: str = ''):
        """语言切换后重新翻译所有静态 UI 文本"""
        # Header 区域
        self._retranslate_header()
        # 输入区域
        self._retranslate_input_area()
        # 会话标签栏
        self._retranslate_session_tabs()
        print(f"[i18n] UI retranslated for language: {_lang or get_language()}")

    # ==========================================================
    # ★ 大脑启发式长期记忆系统
    # ==========================================================

    def _get_personality_injection(self) -> str:
        """获取个性注入文本（附加到 system prompt 末尾）"""
        if not self._is_memory_active() or not self._growth_tracker:
            return ""
        try:
            return self._growth_tracker.get_personality_description()
        except Exception:
            return ""

    def _get_user_rules_injection(self) -> str:
        """获取用户自定义规则文本（附加到 system prompt 末尾）"""
        try:
            from ..utils.rules_manager import get_rules_for_prompt
            return get_rules_for_prompt()
        except Exception:
            return ""

    def _warm_doc_index(self):
        """后台加载 DocIndex 并重建完整系统提示词"""
        import threading
        def _load():
            try:
                from ..utils.doc_rag import get_doc_index
                get_doc_index()  # 触发单例加载（含 JSON 反序列化）
                self._doc_index_ready = True
                # 回主线程重建 prompt
                QtCore.QTimer.singleShot(0, self._rebuild_system_prompts)
            except Exception as e:
                print(f"[DocIndex] 后台加载失败: {e}")
        threading.Thread(target=_load, daemon=True).start()

    def _build_ui(self):
        # ---- 全局 QSS（由 ThemeEngine 从模板渲染） ----
        self.setObjectName("aiTab")
        print("[AITab] _build_ui: theme")
        self._theme = ThemeEngine()
        self._theme.load_template(Path(__file__).parent / "style_template.qss")
        self._theme.load_preference()
        self.setStyleSheet(self._theme.render())

        self.setMinimumWidth(320)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 顶部设置栏
        print("[AITab] _build_ui: header")
        header = self._build_header()
        layout.addWidget(header)

        # 会话标签栏（多会话切换）
        print("[AITab] _build_ui: session_tabs")
        session_tabs_bar = self._build_session_tabs()
        layout.addWidget(session_tabs_bar)

        # 节点上下文栏
        print("[AITab] _build_ui: node_context_bar")
        self.node_context_bar = NodeContextBar()
        self.node_context_bar.refreshRequested.connect(self._refresh_node_context)
        layout.addWidget(self.node_context_bar)

        # 对话区域（多会话 - 使用 QStackedWidget）
        self.session_stack = QtWidgets.QStackedWidget()
        layout.addWidget(self.session_stack, 1)

        # 创建第一个会话
        print("[AITab] _build_ui: initial_session")
        self._create_initial_session()

        # 输入区域
        print("[AITab] _build_ui: input_area")
        input_area = self._build_input_area()
        layout.addWidget(input_area)

    # ===================================================================
    # 以下方法已迁移到 Mixin 模块（通过继承自动可用）:
    #   HeaderMixin       → _build_header, _combo_style, _small_btn_style
    #   InputAreaMixin    → _build_input_area, mode toggles, @mention, tool status
    #   ChatViewMixin     → _add_user_message, _add_ai_response, scroll, toast
    #   AgentRunnerMixin  → title gen, confirm mode, tool constants
    #   SessionManagerMixin → session tabs, create/switch/close session
    # ===================================================================

    def _wire_events(self):
        self.btn_send.clicked.connect(self._on_send)
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_key.clicked.connect(self._on_set_key)
        self.btn_clear.clicked.connect(self._on_clear)
        self.btn_cache.clicked.connect(self._on_cache_menu)
        self.btn_optimize.clicked.connect(self._on_optimize_menu)
        self.btn_network.clicked.connect(self._on_read_network)
        self.btn_selection.clicked.connect(self._on_read_selection)
        self.btn_export_train.clicked.connect(self._on_export_training_data)
        self.btn_attach_image.clicked.connect(self._on_attach_image)
        self.btn_update.clicked.connect(self._on_check_update)
        self.btn_font_scale.clicked.connect(self._on_font_settings)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        self.model_combo.currentIndexChanged.connect(self._update_context_stats)
        
        # 字号缩放快捷键
        # QShortcut 在 PySide6 中位于 QtGui，PySide2 中位于 QtWidgets
        _QShortcut = getattr(QtWidgets, 'QShortcut', None) or QtGui.QShortcut
        _QShortcut(QtGui.QKeySequence("Ctrl+="), self, self._zoom_in)
        _QShortcut(QtGui.QKeySequence("Ctrl++"), self, self._zoom_in)
        _QShortcut(QtGui.QKeySequence("Ctrl+-"), self, self._zoom_out)
        _QShortcut(QtGui.QKeySequence("Ctrl+0"), self, self._zoom_reset)
        # 切换提供商或模型或 Think 时自动保存偏好
        self.provider_combo.currentIndexChanged.connect(self._save_model_preference)
        self.model_combo.currentIndexChanged.connect(self._save_model_preference)
        self.think_check.stateChanged.connect(self._save_model_preference)
        self.input_edit.sendRequested.connect(self._on_send)
        
        # 多会话标签
        self.session_tabs.currentChanged.connect(self._switch_session)
        self.btn_new_session.clicked.connect(self._new_session)

    # ===== 字号缩放 =====

    def _set_running(self, running: bool):
        self._is_running = running
        
        if running:
            # 锚定 agent 输出目标到当前 session
            self._agent_session_id = self._session_id
            self._agent_response = self._current_response
            self._agent_scroll_area = self.scroll_area
            self._agent_history = self._conversation_history
            self._agent_token_stats = self._token_stats
            self._agent_todo_list = self.todo_list
            self._agent_chat_layout = self.chat_layout
            
            # 重置缓冲区
            self._thinking_buffer = ""
            self._content_buffer = ""
            self._current_output_tokens = 0
            self._in_think_block = False
            self._tag_parse_buf = ""
            self._fake_warned = False
            # 重置自适应缓冲参数
            self._output_buffer = ""
            self._last_flush_time = time.time()
            self._adaptive_buf_size = 80
            self._adaptive_interval = 0.15
            self._last_render_duration = 0.0
            self._flush_count = 0
            self._is_first_content_chunk = True
            
            self.client.reset_stop()
            # 启动思考计时器
            self._thinking_timer = QtCore.QTimer(self)
            self._thinking_timer.timeout.connect(lambda: self._updateThinkingTime.emit())
            self._thinking_timer.start(1000)
            
            # ★ 启动输入框呼吸光晕
            self._start_input_glow()
        else:
            # ★ 先停止所有动效（此时 _agent_response 引用仍有效）
            if self._thinking_timer:
                self._thinking_timer.stop()
                self._thinking_timer = None
            self._stop_input_glow()
            self._stop_active_aurora()
            # ★ 强制停止 thinking_bar（防止延迟到达的 _showGenerating 信号重新启动）
            try:
                self.thinking_bar.stop()
            except (RuntimeError, AttributeError):
                pass
            
            # 将完成后的状态写回 session 字典
            if self._agent_session_id and self._agent_session_id in self._sessions:
                s = self._sessions[self._agent_session_id]
                s['current_response'] = self._agent_response
                if self._agent_history is not None:
                    s['conversation_history'] = self._agent_history
                if self._agent_token_stats is not None:
                    s['token_stats'] = self._agent_token_stats
                if self._agent_todo_list is not None:
                    s['todo_list'] = self._agent_todo_list
            
            self._agent_session_id = None
            self._agent_response = None
            self._agent_scroll_area = None
            self._agent_history = None
            self._agent_token_stats = None
            self._agent_todo_list = None
            self._agent_chat_layout = None
        
        # 按当前显示的 session 更新按钮状态
        self._update_run_buttons()
    
    # ===== 动效：输入框呼吸光晕 + AIResponse 流光边框 =====

    def _start_input_glow(self):
        """启动输入框边框呼吸光晕（AI 运行期间）"""
        self._glow_phase = 0.0
        if not hasattr(self, '_glow_timer') or self._glow_timer is None:
            self._glow_timer = QtCore.QTimer(self)
            self._glow_timer.setInterval(50)
            self._glow_timer.timeout.connect(self._update_input_glow)
        self._glow_timer.start()

    def _stop_input_glow(self):
        """停止输入框呼吸光晕，恢复默认边框"""
        if hasattr(self, '_glow_timer') and self._glow_timer is not None:
            self._glow_timer.stop()
        try:
            self.input_edit.setStyleSheet("")  # 清除覆盖，恢复全局 QSS
        except RuntimeError:
            pass

    def _update_input_glow(self):
        """定时器回调：正弦波驱动边框亮度在银灰/亮白之间柔和呼吸"""
        self._glow_phase += 0.04
        t = (math.sin(self._glow_phase) + 1.0) / 2.0  # 0~1
        # 暗银 → 亮银白 插值（简洁单色系）
        r = int(100 + (200 - 100) * t)
        g = int(116 + (210 - 116) * t)
        b = int(139 + (220 - 139) * t)
        a = int(60 + 70 * t)
        try:
            self.input_edit.setStyleSheet(
                f"QPlainTextEdit#chatInput {{ border: 1.5px solid rgba({r},{g},{b},{a}); }}"
            )
        except RuntimeError:
            pass

    def _start_active_aurora(self):
        """启动当前活跃 AIResponse 的流光边框"""
        try:
            resp = self._agent_response or self._current_response
            if resp and hasattr(resp, 'aurora_bar'):
                resp.start_aurora()
        except RuntimeError:
            pass

    def _stop_active_aurora(self):
        """停止当前活跃 AIResponse 的流光边框"""
        try:
            resp = self._agent_response or self._current_response
            if resp and hasattr(resp, 'aurora_bar'):
                resp.stop_aurora()
        except RuntimeError:
            pass

    _TAB_RUNNING_PREFIX = "\u25cf "  # ● 前缀表示正在运行
    
    def _update_run_buttons(self):
        """根据当前显示的 session 是否正在运行，更新 send/stop 按钮和 tab 指示器"""
        current_is_running = (self._agent_session_id is not None
                              and self._agent_session_id == self._session_id)
        any_running = self._agent_session_id is not None
        # 当前 session 在跑 → 显示 stop；否则显示 send（但若其他 session 在跑则 disable）
        self.btn_stop.setVisible(current_is_running)
        self.btn_send.setVisible(not current_is_running)
        self.btn_send.setEnabled(not any_running)
        
        # 更新所有 tab 的运行指示器
        for i in range(self.session_tabs.count()):
            sid = self.session_tabs.tabData(i)
            label = self.session_tabs.tabText(i)
            is_agent_tab = (sid == self._agent_session_id and self._agent_session_id is not None)
            has_prefix = label.startswith(self._TAB_RUNNING_PREFIX)
            if is_agent_tab and not has_prefix:
                self.session_tabs.setTabText(i, self._TAB_RUNNING_PREFIX + label)
            elif not is_agent_tab and has_prefix:
                self.session_tabs.setTabText(i, label[len(self._TAB_RUNNING_PREFIX):])

    # ===== 信号处理 =====
    
    def _cook_displayed_nodes_if_manual(self):
        """★ 在 Manual 保护模式下，对当前工作区的 display 节点做针对性 cook
        
        v1.4.4 修复：Agent 运行期间处于 Manual 模式时，修改工具不触发 cook，
        导致读取工具（get_network_structure、check_errors 等）返回 stale 数据，
        AI 误以为操作未生效。
        
        策略：只 cook 当前 /obj 下各 geo 容器中设置了 Display Flag 的节点。
        这是最小范围的 cook，只刷新 AI 关注的节点数据而不触发全场景 cook。
        """
        if getattr(self, '_pre_agent_update_mode', None) is None:
            return  # 不在 Agent cook 保护模式下，无需处理
        # ★ 实时模式因 cook 中断/超时而挂起时，绝不再主动 cook：
        # 此时 Houdini 已被切到 Manual，若在此 force cook 显示节点，
        # 会让随后的读取工具再次卡死在那个重型节点上（正是要避免的死循环）。
        if getattr(self, '_cook_realtime_suspended', False):
            return
        try:
            import hou  # type: ignore
            if hou.updateModeSetting() != hou.updateMode.Manual:
                return  # 当前不是 Manual 模式，无需处理
            
            # 收集所有需要 cook 的 display 节点
            cooked = 0
            for child in hou.node('/obj').children():
                # 只处理 geo 类型容器（SOP 网络）
                if child.type().name() not in ('geo', 'subnet'):
                    continue
                try:
                    display_node = child.displayNode()
                    if display_node is not None:
                        display_node.cook(force=True)
                        cooked += 1
                except Exception:
                    pass  # 单个节点 cook 失败不影响其他
            if cooked:
                print(f"[Cook Guard] Manual 模式下针对性 cook 了 {cooked} 个 display 节点")
        except Exception as e:
            print(f"[Cook Guard] 针对性 cook 失败: {e}")

    # ==================================================================
    # ★ 实时 Cook 模式（v1.6）
    # ==================================================================

    # 慢 cook 阈值（秒）：超过则在反馈中追加警告
    _SLOW_COOK_SEC = 60.0

    @staticmethod
    def _load_cook_realtime_pref() -> bool:
        """从 QSettings 加载 Cook 模式开关（默认 True = 实时模式）。"""
        try:
            s = QSettings("HoudiniAgent", "Settings")
            val = s.value("cook_realtime_mode", True)
            if isinstance(val, str):
                return val.lower() != 'false'
            return bool(val)
        except Exception:
            return True

    def _save_cook_realtime_pref(self, enabled: bool):
        try:
            s = QSettings("HoudiniAgent", "Settings")
            s.setValue("cook_realtime_mode", bool(enabled))
        except Exception:
            pass

    def _is_cook_realtime(self) -> bool:
        """当前是否为实时 Cook 模式（默认 True）。"""
        return bool(getattr(self, '_cook_realtime_mode', True))

    def _cook_realtime_active(self) -> bool:
        """本次工具执行是否应走实时 cook。

        = 实时模式开启 且 未因本次运行中的中断/超时而临时挂起。
        挂起后（_cook_realtime_suspended=True）退化为保护模式行为：
        Houdini 保持 Manual、后续工具不再同步 cook，避免重 cook 死循环。
        """
        return self._is_cook_realtime() and not getattr(self, '_cook_realtime_suspended', False)

    def set_cook_realtime_mode(self, enabled: bool):
        """切换 Cook 模式并持久化。

        True  = 实时模式：保持 Auto 更新，cook 同步测量并反馈给 Agent。
        False = 保护模式：Agent 运行期间切 Manual，防止 cook 阻塞主线程。
        """
        enabled = bool(enabled)
        if enabled == getattr(self, '_cook_realtime_mode', True):
            return
        self._cook_realtime_mode = enabled
        self._save_cook_realtime_pref(enabled)
        try:
            mode_label = tr('cook.mode_realtime') if enabled else tr('cook.mode_protect')
            self._addStatus.emit(tr('cook.mode_switched', mode_label))
        except Exception:
            pass

    def _realtime_cook_and_report(self, result: dict):
        """实时模式：同步 cook 当前 display 节点，测量耗时并写入 result。

        - 仅 cook 脏节点（force=False），测量真实新增计算耗时
        - 用 hou.InterruptableOperation 包裹：用户按 Esc 可中断 + 进度提示
        - 把简洁的耗时反馈写入 result['_cook_note']，由 _compress_tool_result
          统一追加到发给 LLM 的工具结果文本中
        - 慢 cook（> _SLOW_COOK_SEC）追加警告，提醒 Agent 慎重重复 cook 该节点，
          必要时切回 Manual 保护模式
        """
        import time as _t
        try:
            import hou  # type: ignore
        except Exception:
            return

        # 收集当前 /obj 下各 geo/subnet 容器的 display 节点
        targets = []
        try:
            obj = hou.node('/obj')
            if obj is None:
                return
            for child in obj.children():
                try:
                    if child.type().name() not in ('geo', 'subnet'):
                        continue
                    dn = child.displayNode()
                    if dn is not None:
                        targets.append(dn)
                except Exception:
                    pass
        except Exception:
            return
        if not targets:
            return

        per_node = []       # [(path, seconds)]
        cook_errors = []     # [(path, msg)]
        interrupted = False
        budget_exceeded = False   # 整体耗时超阈值，提前熔断剩余节点
        t_start = _t.time()

        try:
            with hou.InterruptableOperation(
                "Cooking", long_operation_name="Houdini Agent: cooking display nodes",
                open_interrupt_dialog=True
            ) as op:
                total = len(targets)
                for idx, dn in enumerate(targets):
                    try:
                        t0 = _t.time()
                        dn.cook(force=False)  # 仅在脏时计算，测量真实耗时
                        dt = _t.time() - t0
                        if dt >= 0.05:
                            per_node.append((dn.path(), dt))
                    except hou.OperationInterrupted:
                        interrupted = True
                        break
                    except hou.Error as he:
                        cook_errors.append((dn.path(), str(he).split('\n')[0][:80]))
                    except Exception:
                        pass
                    # ★ 整体耗时熔断：单次 cook 已超阈值则不再 cook 剩余节点，
                    # 避免多个重型网络叠加把界面拖死更久。
                    if (_t.time() - t_start) >= self._SLOW_COOK_SEC:
                        budget_exceeded = True
                        break
                    try:
                        op.updateProgress((idx + 1) / float(total))
                    except Exception:
                        pass
        except hou.OperationInterrupted:
            interrupted = True
        except Exception as e:
            print(f"[Cook] InterruptableOperation 失败: {e}")
            return

        elapsed = _t.time() - t_start
        slow = elapsed >= self._SLOW_COOK_SEC or budget_exceeded

        # ★ 关键修复：cook 被中断 或 超时 时，自动切换 Houdini 为 Manual 更新，
        # 并在本次 Agent 运行剩余阶段挂起"实时 cook"。
        # 原因：Auto 模式下控制权返回主线程后，视口重绘会立刻重新 cook 同一个
        # 脏的重型节点 →"中断后依然卡死"的根因。切 Manual 可阻断该重 cook 死循环；
        # 挂起实时 cook 则避免后续工具再次触发同步 cook 把界面拖死。
        # 同时把 Agent 结束时的恢复目标也设为 Manual（_pre_agent_update_mode），
        # 防止结束时恢复 Auto 又触发一次重 cook。用户可随时手动切回 Auto。
        switched_manual = False
        if interrupted or slow:
            try:
                if hou.updateModeSetting() != hou.updateMode.Manual:
                    hou.setUpdateMode(hou.updateMode.Manual)
                    switched_manual = True
                # 挂起本次运行的实时 cook（保护剩余工具不再同步 cook）
                self._cook_realtime_suspended = True
                # 让 Agent 结束时的统一恢复保持 Manual，避免末尾重 cook
                self._pre_agent_update_mode = hou.updateMode.Manual
            except Exception:
                pass

        # 结构化数据（供 UI 或其它逻辑使用）
        result['_cook_timing'] = {
            'elapsed': round(elapsed, 2),
            'slow': slow,
            'interrupted': interrupted,
            'switched_manual': switched_manual,
            'per_node': [(p, round(d, 2)) for p, d in per_node],
            'errors': cook_errors,
        }

        # 构建发给 LLM 的简洁反馈文本
        if interrupted:
            note = tr('cook.note_interrupted', f"{elapsed:.1f}")
        elif slow:
            heaviest = max(per_node, key=lambda x: x[1]) if per_node else None
            heavy_str = f"{heaviest[0]} ({heaviest[1]:.1f}s)" if heaviest else "-"
            note = tr('cook.note_slow', f"{elapsed:.1f}", heavy_str)
        else:
            note = tr('cook.note_done', f"{elapsed:.1f}")
        if interrupted or slow:
            # 追加"已自动切 Manual + 挂起实时 cook"的说明，让 Agent 知道
            # 后续视口不再自动更新、需谨慎，并告知用户如何恢复。
            note += " " + tr('cook.note_switched_manual')
        if cook_errors:
            err_paths = ', '.join(p for p, _ in cook_errors[:3])
            note += " " + tr('cook.note_errors', err_paths)
        result['_cook_note'] = note

        if slow or interrupted:
            # 防御性：note 可能含非 GBK 字符，中文 Windows 控制台 print 会抛
            # UnicodeEncodeError，这里兜底，避免影响工具结果回传。
            try:
                print(f"[Cook] {note}")
            except Exception:
                pass

    def _restore_update_mode(self):
        """★ 恢复 Houdini 更新模式（Agent 结束/错误/停止时调用）
        
        v1.4.3 Cook 保护策略：
        Agent 运行期间，修改工具会将 Houdini 切换为 Manual 模式以防止
        cook 阻塞主线程。Agent 结束后在此统一恢复用户原始的更新模式，
        此时 Houdini 会自动触发一次 cook 展示最终结果。
        """
        _user_mode = getattr(self, '_pre_agent_update_mode', None)
        if _user_mode is not None:
            try:
                import hou  # type: ignore
                hou.setUpdateMode(_user_mode)
            except Exception:
                pass
            self._pre_agent_update_mode = None
    

    def _on_agent_error(self, error: str):
        # ★ 恢复 Houdini 更新模式 & 清除主线程忙标记
        self._main_thread_busy = False
        self._restore_update_mode()
        # 停止思考指示条
        try:
            self.thinking_bar.stop()
        except (RuntimeError, AttributeError):
            pass
        # 刷新输出缓冲区
        if hasattr(self, '_output_buffer') and self._output_buffer:
            self._on_append_content(self._output_buffer)
            self._output_buffer = ""
        
        resp = self._agent_response or self._current_response
        try:
            if resp:
                resp.finalize()
                resp.add_status(f"Error: {error}")
        except RuntimeError:
            pass  # widget 已被 clear 销毁
        
        # ★ 确保历史以 assistant 结尾（防止连续 user 消息破坏结构）
        self._ensure_history_ends_with_assistant(f"[Error] {error}")
        
        self._set_running(False)

    def _on_agent_stopped(self):
        # ★ 恢复 Houdini 更新模式 & 清除主线程忙标记
        self._main_thread_busy = False
        self._restore_update_mode()
        # 停止思考指示条
        try:
            self.thinking_bar.stop()
        except (RuntimeError, AttributeError):
            pass
        # 刷新输出缓冲区
        if hasattr(self, '_output_buffer') and self._output_buffer:
            self._on_append_content(self._output_buffer)
            self._output_buffer = ""
        
        resp = self._agent_response or self._current_response
        try:
            if resp:
                resp.finalize()
                resp.add_status("Stopped")
        except RuntimeError:
            pass  # widget 已被 clear 销毁
        
        # ★ 确保历史以 assistant 结尾（防止连续 user 消息破坏结构）
        self._ensure_history_ends_with_assistant("[Stopped by user]")
        
        self._set_running(False)
        self._hideToolStatus.emit()
    
    def _ensure_history_ends_with_assistant(self, fallback_content: str):
        """确保 conversation_history 以 assistant 消息结尾
        
        当 agent 出错或被中断时，用户消息已追加但没有对应的 assistant 回复，
        这会破坏 user↔assistant 交替结构，导致下次 API 调用失败。
        """
        history = self._agent_history if self._agent_history is not None else self._conversation_history
        if history and history[-1].get('role') == 'user':
            history.append({'role': 'assistant', 'content': fallback_content})

    # ---------- 工具执行状态 ----------

    def _on_update_todo(self, todo_id: str, text: str, status: str):
        """更新 Todo 列表（跟随对话流内联显示）
        
        使用 agent 锚定的 todo_list / chat_layout，防止切换会话后
        写入错误的窗口。
        """
        try:
            # 优先使用 agent 锚定的目标（会话 A 运行时不受会话 B 影响）
            todo = self._agent_todo_list or self.todo_list
            layout = self._agent_chat_layout or self.chat_layout
            if not todo:
                return
            # 确保 todo_list 已在对应 chat_layout 中
            self._ensure_todo_in_chat(todo, layout)
        except RuntimeError:
            return  # widget 已被 clear 销毁
        if text:
            todo.add_todo(todo_id, text, status)
        else:
            todo.update_todo(todo_id, status)

    
    def _execute_tool_in_bg(self, tool_name: str, kwargs: dict) -> dict:
        """在后台线程直接执行工具（不阻塞 UI 主线程）
        
        仅用于不依赖 hou 模块的工具，如 execute_shell、search_local_doc 等。
        """
        try:
            return self.mcp.execute_tool(tool_name, kwargs)
        except Exception as e:
            import traceback
            return {"success": False, "error": tr('ai.bg_exec_err', f"{e}\n{traceback.format_exc()[:300]}")}
    
    # 主线程工具执行超时（秒）
    # 修改操作可能触发 Houdini cook，需要足够的超时时间
    _TOOL_MAIN_THREAD_TIMEOUT = 120.0

    def _execute_tool_in_main_thread(self, tool_name: str, kwargs: dict) -> dict:
        """在主线程执行工具（线程安全）
        
        使用 BlockingQueuedConnection + Queue 确保：
        1. Houdini 操作在主线程执行（hou 模块非线程安全，macOS 尤其严格）
        2. 多个工具调用不会竞争
        3. 结果安全传递回调用线程
        
        ★ macOS 崩溃修复说明：
        Houdini 嵌入 Qt 时，macOS 的 Cocoa 事件循环比 Windows 更严格。
        所有 hou API 调用必须在主线程执行，否则会导致段错误或 EXC_BAD_ACCESS。
        BlockingQueuedConnection 保证信号在目标线程（主线程）的事件循环中执行，
        且 emit 会阻塞调用线程直到槽函数返回，实现了线程安全的同步调用。
        
        ★ 防卡死机制（v1.4.3）：
        当 Houdini cook 耗时导致超时后，标记 _main_thread_busy，
        阻止后续工具调用堆积 BlockingQueuedConnection 信号（避免死锁）。
        主线程槽函数执行完毕后自动清除标记。
        """
        # 使用锁确保一次只有一个工具调用（避免并发竞争）
        with self._tool_lock:
            # 清空队列（防止残留数据）
            while not self._tool_result_queue.empty():
                try:
                    self._tool_result_queue.get_nowait()
                except queue.Empty:
                    break
            
            # 发送信号到主线程执行
            # BlockingQueuedConnection 会阻塞直到槽函数执行完成
            self._executeToolRequest.emit(tool_name, kwargs)
            
            # 从队列获取结果（有超时保护）
            # ★ 超时设为 120s，因为某些 Houdini 操作（如创建复杂节点、cook 高面数模型）
            #   可能需要较长时间。超时后标记主线程忙，防止后续信号堆积。
            try:
                result = self._tool_result_queue.get(timeout=self._TOOL_MAIN_THREAD_TIMEOUT)
                # 主线程正常返回 → 清除忙标记
                self._main_thread_busy = False
                return result
            except queue.Empty:
                # ★ 超时：主线程可能仍在执行 cook，标记为忙
                self._main_thread_busy = True
                print(f"[⚠️ TIMEOUT] 工具 {tool_name} 主线程执行超时 "
                      f"({self._TOOL_MAIN_THREAD_TIMEOUT}s)，"
                      f"可能 Houdini 正在进行耗时计算。后续工具调用将被暂停。")
                return {
                    "success": False,
                    "error": f"操作超时（{int(self._TOOL_MAIN_THREAD_TIMEOUT)}秒）：Houdini 主线程可能正在进行耗时计算（如 cook/渲染）。"
                             f"操作 {tool_name} 仍在后台执行中，请等待完成或按停止按钮中断。"
                }

    def _execute_tools_batch_in_main_thread(self, batch: list) -> list:
        """在主线程批量执行只读工具（减少 N 次信号往返为 1 次）

        Args:
            batch: [(tool_name, kwargs), ...]

        Returns:
            [result_dict, ...]（与 batch 顺序一致）
        """
        with self._tool_lock:
            while not self._tool_result_queue.empty():
                try:
                    self._tool_result_queue.get_nowait()
                except queue.Empty:
                    break

            self._executeToolBatchRequest.emit(batch)

            try:
                results = self._tool_result_queue.get(timeout=60.0)
                return results if isinstance(results, list) else [results]
            except queue.Empty:
                return [{"success": False, "error": tr('ai.main_exec_timeout')}] * len(batch)

    def _on_execute_tool_batch_main_thread(self, batch: list):
        """在主线程批量执行只读工具的槽函数

        所有工具在主线程依次执行（它们是快速的只读查询），
        然后将结果列表一次性放入队列返回给调用线程。
        """
        # ★ 读取前 Cook（v1.4.4）：批量读取也需要确保数据新鲜
        needs_cook = any(tn in self._COOK_BEFORE_READ_TOOLS for tn, _ in batch)
        if needs_cook:
            self._cook_displayed_nodes_if_manual()
        
        results = []
        for tool_name, kwargs in batch:
            try:
                result = self.mcp.execute_tool(tool_name, kwargs)
            except Exception as e:
                result = {"success": False, "error": str(e)}
            results.append(result)
        self._tool_result_queue.put(results)
    
    # ------------------------------------------------------------------
    # Plan 模式工具处理
    # ------------------------------------------------------------------

    @staticmethod
    def _snapshot_network_children() -> dict:
        """快照当前网络的子节点列表 {path: {name, type, path}}"""
        try:
            import hou  # type: ignore
            network = None
            try:
                editor = hou.ui.curDesktop().paneTabOfType(hou.paneTabType.NetworkEditor)
                if editor:
                    network = editor.pwd()
            except Exception:
                pass
            if not network:
                network = hou.node('/obj/geo1') or hou.node('/obj')
            if not network:
                return {}
            return {
                node.path(): {
                    'name': node.name(),
                    'type': node.type().name(),
                    'path': node.path(),
                }
                for node in network.children()
            }
        except Exception:
            return {}

    # ------------------------------------------------------------------
    #  后处理：自动将 AI 回复中的裸节点名解析为完整路径
    # ------------------------------------------------------------------

    _NODE_PATH_RE = re.compile(r'/(?:obj|out|shop|stage|tasks|ch|mat|img)/[\w/]+')

    def _collect_node_paths_from_tool(self, result: dict, arguments: dict = None):
        """从工具执行的结果和参数中提取 Houdini 节点路径，累积到 _session_node_map。"""
        import re
        paths: set[str] = set()

        # 从 result 和 arguments 中用正则提取所有形如 /obj/geo1/box1 的路径
        for source in (result, arguments):
            if not source:
                continue
            raw = json.dumps(source, default=str) if isinstance(source, dict) else str(source)
            paths.update(self._NODE_PATH_RE.findall(raw))

        # 从 _node_changes 中提取
        node_changes = result.get('_node_changes') if isinstance(result, dict) else None
        if node_changes:
            for n in node_changes.get('created', []):
                if n.get('path'):
                    paths.add(n['path'])
            for n in node_changes.get('deleted', []):
                if n.get('path'):
                    paths.add(n['path'])

        # 写入 _session_node_map: name → set[path]
        for p in paths:
            name = p.rsplit('/', 1)[-1]
            if name:
                self._session_node_map.setdefault(name, set()).add(p)

    def _resolve_bare_node_names(self, text: str) -> str:
        """将 AI 回复中的裸节点名（如 box1）自动替换为完整路径（如 /obj/geo1/box1）。

        数据来源：当前会话中 AI 工具调用涉及的节点路径（_session_node_map）。
        安全规则:
        - 只替换名称在会话中只对应 **唯一一个** 路径的节点（避免跨 subnet 歧义）。
        - 只处理以数字结尾的名称（box1, scatter2），避免误匹配普通英文单词。
        - 跳过代码块（```...``` 和 `...`）中的内容。
        - 跳过已经是完整路径一部分的名称（前面有 /）。
        - 长名称优先替换，避免子串冲突。
        """
        if not text or not self._session_node_map:
            return text

        import re

        # 构建 name → path 映射（仅以数字结尾 + 唯一路径的名称）
        name_to_path: dict[str, str] = {}
        for name, path_set in self._session_node_map.items():
            if len(path_set) == 1 and name and name[-1].isdigit():
                name_to_path[name] = next(iter(path_set))
        if not name_to_path:
            return text

        # 按名称长度降序排列（长名优先，避免 "box1" 误匹配 "networkbox1" 的子串）
        sorted_names = sorted(name_to_path.keys(), key=len, reverse=True)

        # 将文本拆分为 代码块 / 非代码块
        code_pattern = re.compile(r'(```[\s\S]*?```|`[^`\n]+`)')
        parts = code_pattern.split(text)

        for i, part in enumerate(parts):
            # 跳过代码块片段
            if part.startswith('`'):
                continue
            for name in sorted_names:
                full_path = name_to_path[name]
                # 负向后视：前面不能是 / 或 \w（已在路径中或更长名称的一部分）
                # 负向前瞻：后面不能是 \w（更长名称的一部分）
                pat = r'(?<![/\w])' + re.escape(name) + r'(?!\w)'
                parts[i] = re.sub(pat, full_path, parts[i])

        return ''.join(parts)

    @staticmethod
    def _diff_network_children(before: dict, after: dict):
        """对比前后子节点快照，返回 {created: [...], deleted: [...]} 或 None"""
        before_paths = set(before.keys())
        after_paths = set(after.keys())
        created = [after[p] for p in sorted(after_paths - before_paths)]
        deleted = [before[p] for p in sorted(before_paths - after_paths)]
        if not created and not deleted:
            return None
        return {'created': created, 'deleted': deleted}

    # ★ 会触发 Houdini cook 的工具集合
    # 这些工具执行时可能导致耗时的场景计算，需要特殊保护
    _COOK_TRIGGERING_TOOLS = frozenset({
        'create_node', 'create_nodes_batch', 'create_wrangle_node',
        'connect_nodes', 'set_display_flag', 'set_node_parameter',
        'batch_set_parameters', 'execute_python', 'run_skill',
    })

    # ★ 需要在 Manual 保护模式下做针对性 cook 的读取工具
    # 这些工具需要读取节点最新计算结果（几何体、错误状态等），
    # 如果不 cook，AI 会看到 stale 数据从而误判操作结果
    _COOK_BEFORE_READ_TOOLS = frozenset({
        'get_network_structure', 'get_node_parameters', 'list_children',
        'check_errors', 'verify_and_summarize',
        'capture_viewport',  # 截图前需确保几何体已 cook
    })


    # ------------------------------------------------------------------
    # 伪造工具调用检测
    # ------------------------------------------------------------------
    # 所有注册的工具名称（用于检测伪造）
    _ALL_TOOL_NAMES = (
        'create_wrangle_node|get_network_structure'
        '|get_node_parameters|set_node_parameter|create_node|create_nodes_batch'
        '|connect_nodes|delete_node|search_node_types|semantic_search_nodes'
        '|list_children|read_selection|set_display_flag'
        '|copy_node|batch_set_parameters|find_nodes_by_param|save_hip|undo_redo'
        '|web_search|fetch_webpage|search_local_doc|get_houdini_node_doc'
        '|execute_python|execute_shell|check_errors|get_node_inputs|add_todo|update_todo'
        '|verify_and_summarize|run_skill|list_skills'
        '|layout_nodes|get_node_positions'
        '|perf_start_profile|perf_stop_and_report'
    )
    _FAKE_TOOL_PATTERNS = re.compile(
        r'^\[(?:ok|err)\]\s*(?:' + _ALL_TOOL_NAMES + r')\s*[:\uff1a]',
        re.MULTILINE | re.IGNORECASE,
    )

    @staticmethod
    def _split_and_compress_assistant(content: str, max_reply: int = 1500) -> str:
        """分离工具摘要和 AI 回复并智能压缩
        
        用于旧格式 assistant 消息（没有 _reply_content 字段），
        尝试将 [工具执行结果] 段落和后续 AI 回复分开，
        压缩工具部分、保留回复部分。
        """
        # 查找工具结果段落结尾
        if '[工具执行结果]' not in content and '[工具结果]' not in content and '[Tool Result]' not in content:
            # 没有工具摘要，直接截断
            return content[:max_reply] + ('...' if len(content) > max_reply else '')
        
        # 找到最后一行 [ok] 或 [err]
        last_tool_line = max(content.rfind('\n[ok]'), content.rfind('\n[err]'))
        if last_tool_line <= 0:
            return content[:max_reply] + ('...' if len(content) > max_reply else '')
        
        # 找到该行结束位置
        next_nl = content.find('\n', last_tool_line + 1)
        if next_nl <= 0 or next_nl >= len(content) - 5:
            return content[:max_reply] + ('...' if len(content) > max_reply else '')
        
        tool_text = content[:next_nl]
        reply_text = content[next_nl:].strip()
        
        # 压缩工具部分
        tool_lines = tool_text.strip().split('\n')
        if len(tool_lines) > 6:
            tool_text = '\n'.join(tool_lines[:1] + tool_lines[-4:]) + f'\n... {len(tool_lines)-1} calls'
        elif len(tool_text) > 500:
            tool_text = tool_text[:500] + '...'
        
        # 保留回复部分
        if reply_text:
            reply_text = reply_text[:max_reply] + ('...' if len(reply_text) > max_reply else '')
        
        return tool_text + '\n\n' + reply_text if reply_text else tool_text


    @staticmethod
    def _format_tool_args_brief(tool_name: str, args: dict) -> str:
        """格式化工具参数摘要，保留关键参数让模型能参考上一轮调用
        
        对比 ChatGPT/Cursor：它们保留完整参数，但我们需要控制 token。
        折中方案：只保留最关键的参数，限制总长度。
        """
        if not args:
            return ""
        
        # 不同工具的关键参数（按重要性排序）
        _KEY_PARAMS = {
            'create_node': ['node_type', 'parent_path', 'node_name'],
            'create_wrangle_node': ['wrangle_type', 'node_name', 'run_over'],
            'create_nodes_batch': ['nodes'],
            'connect_nodes': ['from_path', 'to_path', 'input_index'],
            'set_node_parameter': ['node_path', 'param_name', 'value'],
            'get_node_parameters': ['node_path'],
            'get_network_structure': ['network_path'],
            'set_display_flag': ['node_path', 'display', 'render'],
            'execute_python': ['code'],
            'execute_shell': ['command'],
            'search_node_types': ['keyword'],
            'web_search': ['query'],
            'fetch_webpage': ['url'],
            'check_errors': ['node_path'],
            'run_skill': ['skill_name'],
        }
        
        key_params = _KEY_PARAMS.get(tool_name, list(args.keys())[:3])
        parts = []
        for k in key_params:
            if k in args:
                v = args[k]
                v_str = str(v)
                # 代码类参数只取前 60 字符
                if k in ('code', 'vex_code', 'command') and len(v_str) > 60:
                    v_str = v_str[:60] + '...'
                elif len(v_str) > 80:
                    v_str = v_str[:80] + '...'
                parts.append(f'{k}={v_str}')
        
        brief = ', '.join(parts)
        return brief[:200] if len(brief) > 200 else brief  # 总长度限制

    def _strip_fake_tool_results(self, text: str) -> str:
        """检测并移除 AI 伪造的工具调用结果文本。
        
        AI 有时会在回复中伪装成已经调用了工具，输出类似：
          [ok] web_search: 搜索 xxx
          [ok] fetch_webpage: 网页正文 xxx
        这些不是真正的工具调用，需要清除。
        """
        if not text:
            return text
        
        # 检测 [工具执行结果] 头部（这是系统自动生成的格式，AI 不应输出）
        if text.lstrip().startswith('[工具执行结果]') or text.lstrip().startswith('[Tool Result]'):
            # 整段就是伪造的工具摘要，移除头部和 [ok]/[err] 行
            lines = text.split('\n')
            real_lines = []
            for line in lines:
                stripped = line.strip()
                if stripped in ('[工具执行结果]', '[Tool Result]'):
                    continue
                if self._FAKE_TOOL_PATTERNS.match(stripped):
                    continue
                real_lines.append(line)
            text = '\n'.join(real_lines).strip()
        
        # 检测散布在正文中的伪造行
        lines = text.split('\n')
        cleaned = []
        for line in lines:
            if self._FAKE_TOOL_PATTERNS.match(line.strip()):
                continue
            cleaned.append(line)
        
        return '\n'.join(cleaned).strip()

    
    
    def _get_context_reminder(self) -> str:
        """生成上下文提醒（极简，强调复用）"""
        parts = []
        
        # 添加压缩的历史摘要（极简）
        if self._context_summary:
            parts.append(f"[Context Cache] {self._context_summary}")
        
        # 添加当前 Todo 状态（极简）
        todo_summary = self._get_todo_summary_safe()
        if todo_summary:
            # 只保留未完成的 todo
            if "0/" in todo_summary or "pending" in todo_summary.lower():
                parts.append(f"[TODO] {todo_summary.split(':', 1)[-1] if ':' in todo_summary else todo_summary}")
        
        # 提醒复用上下文（极简）
        if len(self._conversation_history) > 2:
            parts.append(f"[{len(self._conversation_history)} messages in context, reuse prior info]")
        
        return " | ".join(parts) if parts else ""

    def _auto_rag_retrieve(self, user_text: str,
                           scene_context: dict = None,
                           conversation_len: int = 0) -> str:
        """自动 RAG: 从用户消息 + Houdini 场景上下文检索文档并注入

        在后台线程调用，不涉及 Qt 控件。
        
        Args:
            user_text: 用户最新消息文本
            scene_context: 主线程收集的场景上下文 (network_path, selected_types, selected_names)
            conversation_len: 当前对话历史条数（用于动态调整注入量）
        """
        try:
            from ..utils.doc_rag import get_doc_index
            index = get_doc_index()
            
            # ★ 动态调整 RAG 注入量：对话越长越精简，避免浪费 token
            if conversation_len > 20:
                max_chars = 400   # 长对话：精简注入
            elif conversation_len > 10:
                max_chars = 800   # 中等对话
            else:
                max_chars = 1200  # 短对话：充分注入
            
            # ★ 场景上下文增强：把选中节点类型也加入检索查询
            enriched_query = user_text
            if scene_context:
                selected_types = scene_context.get('selected_types', [])
                if selected_types:
                    # 把选中节点的类型名加入查询，让 RAG 检索到相关文档
                    enriched_query += ' ' + ' '.join(selected_types)
            
            return index.auto_retrieve(enriched_query, max_chars=max_chars)
        except Exception:
            return ""

    def _get_todo_summary_safe(self) -> str:
        """线程安全地获取 Todo 摘要（优先使用 agent 锚定的 TodoList）"""
        todo = self._agent_todo_list or self.todo_list
        try:
            return todo.get_todos_summary() if todo else ""
        except Exception:
            return ""

    @QtCore.Slot(result=str)
    def _invoke_get_todo_summary(self) -> str:
        todo = self._agent_todo_list or self.todo_list
        return todo.get_todos_summary() if todo else ""

    # ===== URL 识别 =====
    
    def _extract_urls(self, text: str) -> list:
        """从文本中提取 URL"""
        # URL 正则表达式
        url_pattern = r'https?://[^\s<>"\'`\]\)]+[^\s<>"\'`\]\)\.,;:!?]'
        urls = re.findall(url_pattern, text)
        return urls
    
    def _process_urls_in_text(self, text: str) -> str:
        """处理文本中的 URL，添加提示让 AI 获取网页内容"""
        urls = self._extract_urls(text)
        
        if not urls:
            return text
        
        # 如果包含 URL，添加提示
        url_list = "\n".join(f"  - {url}" for url in urls)
        hint = tr('ai.detected_url', url_list)
        
        return text + hint

    # ===== 事件处理 =====
    
    def _on_send(self):
        text = self.input_edit.toPlainText().strip()
        # 任意 session 有 agent 在跑就阻止发送（AIClient 是共享的，不支持并行）
        if not text or self._agent_session_id is not None:
            return

        provider = self._current_provider()
        if not self.client.has_api_key(provider):
            self._on_set_key()
            return

        # ★ Hook: on_session_start
        self._fire_session_hook('on_session_start', self._session_id)

        # 收集待发送的图片（在 clear 之前）
        has_images = bool(self._pending_images) and self._current_model_supports_vision()
        pending_imgs = [img for img in self._pending_images if img is not None] if has_images else []

        # 显示用户消息（含图片缩略图）
        self._add_user_message(text, images=pending_imgs)
        self.input_edit.clear()
        self._clear_pending_images()
        
        # 自动重命名标签（首条消息时）
        self._auto_rename_tab(text)
        
        # 检测 URL 并添加提示
        processed_text = self._process_urls_in_text(text)
        
        # 构建消息内容（文字或多模态）
        if pending_imgs:
            msg_content = self._build_multimodal_content(processed_text, pending_imgs)
            _umsg = {'role': 'user', 'content': msg_content}
            self._conversation_history.append(_umsg)
            if self._send_context is not None:
                self._send_context.append(_umsg)
        else:
            _umsg = {'role': 'user', 'content': processed_text}
            self._conversation_history.append(_umsg)
            if self._send_context is not None:
                self._send_context.append(_umsg)
        
        # 更新上下文统计
        self._update_context_stats()
        
        # 开始运行（先设置状态，再创建回复块）
        self._set_running(True)
        
        # 创建 AI 回复块（必须在 _set_running 之后，否则会被清除）
        self._add_ai_response()
        # 同步 agent 锚点到刚创建的 response widget
        self._agent_response = self._current_response
        # ★ 启动流光边框动画
        self._start_active_aurora()
        
        # ★ 记录用户当前的 Houdini 更新模式（Agent 结束后恢复）
        try:
            import hou  # type: ignore
            self._pre_agent_update_mode = hou.updateModeSetting()
        except Exception:
            self._pre_agent_update_mode = None
        # ★ 重置实时 cook 挂起标记：新一轮运行重新允许实时 cook
        # （上一轮因中断/超时挂起的状态不应延续到本轮）。
        self._cook_realtime_suspended = False
        
        # ⚠️ 在主线程中获取所有 Qt 控件的值（后台线程不能直接访问）
        agent_params = {
            'provider': self._current_provider(),
            'model': self.model_combo.currentText(),
            'use_web': self.web_check.isChecked(),
            'use_agent': self._agent_mode,  # True=Agent(full), False=Ask(read-only)
            'use_think': self.think_check.isChecked(),
            'context_limit': self._get_current_context_limit(),  # 也在主线程获取
            'scene_context': self._collect_scene_context(),  # ★ 主线程收集 Houdini 场景上下文
            'supports_vision': self._current_model_supports_vision(),  # 模型是否支持图片
            'plan_mode': self._plan_mode,  # ★ Plan 模式标记
        }
        
        # 保存模型选择
        self._save_model_preference()
        
        # 后台执行（传递参数而不是直接访问控件）
        thread = threading.Thread(target=self._run_agent, args=(agent_params,), daemon=True)
        thread.start()


    def _add_tool_result(self, name: str, result: dict, arguments: dict = None):
        """添加工具结果到执行流程（自动压缩长结果）"""
        result_text = str(result.get('result', result.get('error', '')))
        success = result.get('success', True)
        
        # ★ 从工具结果和参数中提取节点路径，用于后处理裸节点名
        self._collect_node_paths_from_tool(result, arguments)
        
        # 压缩工具结果以节省 token（如果结果很长）
        if self._auto_optimize and len(result_text) > 300:
            compressed_summary = self.token_optimizer.compress_tool_result(result, max_length=200)
            # 在历史中使用压缩版本，但 UI 中显示完整版本
            # 注意：这里只影响显示，实际保存到历史时会使用压缩版本
        
        # === execute_python 专用展示 ===
        if name == 'execute_python' and arguments:
            code = arguments.get('code', '')
            if code:
                shell_data = {
                    'code': code,
                    'output': result.get('result', ''),
                    'error': result.get('error', ''),
                    'success': success,
                }
                self._addPythonShell.emit(code, json.dumps(shell_data))
                # 同时设置 ToolCallItem 结果
                short = f"[ok] Python ({len(code.splitlines())} lines)" if success else f"[err] {result_text[:50]}"
                invoke_on_main(self, "_add_tool_result_ui", name, short)
                # ★ 如果 execute_python 导致了节点变更，额外生成 checkpoint
                if result.get('_node_changes'):
                    self._addNodeOperation.emit(name, result)
                return
        
        # === execute_shell 专用展示 ===
        if name == 'execute_shell' and arguments:
            command = arguments.get('command', '')
            if command:
                shell_data = {
                    'command': command,
                    'output': result.get('result', ''),
                    'error': result.get('error', ''),
                    'success': success,
                    'cwd': arguments.get('cwd', ''),
                }
                self._addSystemShell.emit(command, json.dumps(shell_data))
                short = f"[ok] $ {command[:40]}" if success else f"[err] {result_text[:50]}"
                invoke_on_main(self, "_add_tool_result_ui", name, short)
                return
        
        # ★ 通用节点变更检测：任何工具如果通过 before/after 快照检测到节点变更，生成 checkpoint
        if result.get('_node_changes') and result.get('success'):
            self._addNodeOperation.emit(name, result)
        
        # 检查是否是节点操作，需要高亮显示
        # 但如果是失败的操作，也要显示错误信息
        if name in ('create_node', 'create_nodes_batch', 'create_wrangle_node', 'delete_node', 'set_node_parameter'):
            if result.get('success'):
                # 成功时使用节点操作标签（直接传 dict，避免 JSON 序列化开销）
                self._addNodeOperation.emit(name, result)
                # 同时设置 ToolCallItem 结果（折叠式，可展开查看完整内容）
                invoke_on_main(self, "_add_tool_result_ui", name, f"[ok] {result_text}")
                return
            else:
                # 失败时也结束流式预览
                if name in self._VEX_TOOLS:
                    self._finalize_streaming_preview()
                # 失败时显示错误信息（继续下面的逻辑）
                pass
        
        # 添加到执行流程（CollapsibleSection 风格，点击展开查看完整结果）
        if self._agent_response or self._current_response:
            prefix = "[err]" if not success else "[ok]"
            invoke_on_main(self, "_add_tool_result_ui", name, f"{prefix} {result_text}")
    
    @QtCore.Slot(str, str)
    def _add_tool_result_ui(self, name: str, result: str):
        """在 UI 线程中添加工具结果"""
        try:
            resp = self._agent_response or self._current_response
            if resp:
                resp.add_tool_result(name, result)
        except RuntimeError:
            pass  # widget 已被 clear 销毁

    @QtCore.Slot(str, str)
    def _add_collapsible_result(self, name: str, result: str):
        resp = self._agent_response or self._current_response
        if resp:
            resp.add_collapsible(f"Result: {name}", result)

    @staticmethod
    def _extract_node_paths(text: str, tool_name: str = '') -> list:
        """从工具返回的结果文本中提取 **实际操作** 的节点路径
        
        只提取真正被创建/删除的节点路径，忽略上下文信息
        （父网络、输入/输出连接等附属路径）。
        
        各工具的返回格式:
        - create_node:      "✓/obj/geo1/scatter1 (父网络: /obj/geo1, ...)"
        - create_nodes_batch:"已创建 3 个节点: /obj/geo1/a, /obj/geo1/b, /obj/geo1/c"
        - create_wrangle_node:"已创建 Wrangle 节点: /obj/geo1/attribwrangle1"
        - delete_node:      "已删除节点: /obj/geo1/scatter1 (父网络: ...)"
        """
        import re
        _PATH_RE = r'(/(?:obj|out|ch|shop|stage|mat|tasks)[/\w]*)'
        
        if tool_name == 'create_node':
            # 格式: "✓/obj/geo1/scatter1 (父网络: /obj/geo1, ...)"
            # 只取 ✓ 后面的第一个路径
            m = re.match(r'[✓\s]*' + _PATH_RE, text)
            return [m.group(1)] if m else []
        
        if tool_name == 'delete_node':
            # 格式: "已删除节点: /obj/geo1/scatter1 (父网络: ...)"
            # 只取 "已删除节点:" 后面的第一个路径
            m = re.search(r'已删除节点:\s*' + _PATH_RE, text)
            if m:
                return [m.group(1)]
            # fallback: 取文本中第一个路径
            m = re.search(_PATH_RE, text)
            return [m.group(1)] if m else []
        
        if tool_name == 'create_nodes_batch':
            # 格式: "已创建 3 个节点: /obj/geo1/a, /obj/geo1/b, /obj/geo1/c\n注意: ..."
            # 只解析 "个节点:" 后同一行内的逗号分隔路径
            m = re.search(r'个节点:\s*(.*)', text)
            if m:
                first_line = m.group(1).split('\n')[0]
                return re.findall(_PATH_RE, first_line)
            # fallback: 提取所有路径（批量创建格式未匹配时）
            return re.findall(_PATH_RE, text)
        
        if tool_name == 'create_wrangle_node':
            # 格式: "已创建 Wrangle 节点: /obj/geo1/attribwrangle1"
            m = re.search(r'节点:\s*' + _PATH_RE, text)
            return [m.group(1)] if m else []
        
        # 未知工具 → 保守策略：只取第一个路径
        m = re.search(_PATH_RE, text)
        return [m.group(1)] if m else []
    
    # ── 流式 VEX 预览 ─────────────────────────────────────
    # VEX 相关的工具名（只有这些才需要流式预览）
    _VEX_TOOLS = frozenset({'create_wrangle_node', 'set_node_parameter'})

    # 常见的 VEX/代码参数名（set_node_parameter 只有在设置这些参数时才做流式预览）
    _VEX_PARAM_NAMES = frozenset({
        'snippet', 'vex_code', 'code', 'script', 'python',
        'sopoutput', 'command', 'expr', 'expression',
    })

    @QtCore.Slot(str, str, str)
    def _on_tool_args_delta(self, tool_name: str, delta: str, accumulated: str):
        """主线程 slot：处理 tool_call 参数增量，流式预览 VEX 代码 / Plan 生成进度"""
        try:
            # ★ Plan 模式：create_plan 参数流式 → 创建/更新流式卡片
            if tool_name == 'create_plan':
                # 首次收到 create_plan 参数 → 立即创建流式卡片
                if self._streaming_plan_card is None:
                    self._on_create_streaming_plan()
                self._show_plan_generation_progress(accumulated)
                self._updateStreamingPlan.emit(accumulated)
                return

            if tool_name not in self._VEX_TOOLS:
                return

            # set_node_parameter 只对 VEX/代码参数做流式预览
            if tool_name == 'set_node_parameter':
                # 尝试从已累积的 JSON 中提取 param_name
                import re as _re
                m = _re.search(r'"param_name"\s*:\s*"([^"]*)"', accumulated)
                if m:
                    param_name = m.group(1).lower()
                    if param_name not in self._VEX_PARAM_NAMES:
                        return
                # 如果 param_name 还没出现，暂不创建预览（等到能确认是 VEX 参数再说）

            # 从不完整的 JSON 中增量提取 VEX 代码
            code = self._extract_vex_from_partial_json(tool_name, accumulated)
            if not code:
                return
            
            # 对于 set_node_parameter，只有代码超过一定长度才显示预览（避免为 "1.5" 这种值创建预览）
            if tool_name == 'set_node_parameter' and len(code) < 10 and '\n' not in code:
                return

            # 如果还没有 StreamingCodePreview，则创建
            if self._streaming_preview is None or self._streaming_preview_tool != tool_name:
                resp = self._agent_response or self._current_response
                if not resp:
                    return
                self._streaming_preview = StreamingCodePreview(tool_name, parent=resp)
                self._streaming_preview_tool = tool_name
                self._streaming_last_code = ""
                resp.details_layout.addWidget(self._streaming_preview)
                self._scroll_agent_to_bottom()

            # 更新预览（StreamingCodePreview 内部做增量追加）
            self._streaming_preview.update_code(code)
            self._streaming_last_code = code
        except RuntimeError:
            pass  # widget 已被销毁

    def _extract_vex_from_partial_json(self, tool_name: str, accumulated: str) -> str:
        """从不完整的 JSON 字符串中增量提取 VEX 代码字段
        
        create_wrangle_node → 提取 "vex_code" 字段
        set_node_parameter  → 提取 "value" 字段
        """
        import re as _re
        # 确定要提取的字段名
        if tool_name == 'create_wrangle_node':
            field_pattern = r'"vex_code"\s*:\s*"'
        else:
            field_pattern = r'"value"\s*:\s*"'

        m = _re.search(field_pattern, accumulated)
        if not m:
            return ""
        start = m.end()

        # 从 start 开始，解析 JSON 字符串内容（处理转义字符）
        result_chars = []
        i = start
        while i < len(accumulated):
            ch = accumulated[i]
            if ch == '\\' and i + 1 < len(accumulated):
                next_ch = accumulated[i + 1]
                if next_ch == 'n':
                    result_chars.append('\n')
                elif next_ch == 't':
                    result_chars.append('\t')
                elif next_ch == '"':
                    result_chars.append('"')
                elif next_ch == '\\':
                    result_chars.append('\\')
                elif next_ch == '/':
                    result_chars.append('/')
                elif next_ch == 'r':
                    result_chars.append('\r')
                else:
                    result_chars.append(next_ch)
                i += 2
            elif ch == '"':
                break  # 字符串字面量结束
            else:
                result_chars.append(ch)
                i += 1
        return ''.join(result_chars)

    def _finalize_streaming_preview(self):
        """流式预览结束：移除预览 widget（ParamDiffWidget 会接替展示正式 diff）"""
        if self._streaming_preview is not None:
            try:
                self._streaming_preview.setVisible(False)
                self._streaming_preview.deleteLater()
            except RuntimeError:
                pass
            self._streaming_preview = None
            self._streaming_preview_tool = ""
            self._streaming_last_code = ""

    @QtCore.Slot(str, str)
    def _on_add_node_operation(self, name: str, result: dict):
        """处理节点操作高亮显示"""
        try:
            # ★ 工具执行完毕 → 结束流式预览
            if name in self._VEX_TOOLS:
                self._finalize_streaming_preview()
            
            resp = self._agent_response or self._current_response
            if not resp:
                return
            
            if not isinstance(result, dict):
                result = {}
            
            label = None
            result_text = str(result.get('result', ''))
            undo_snapshot = result.get('_undo_snapshot')  # 仅 delete_node 时会有
            
            # ---- 收集路径 & 操作类型 ----
            op_type = 'create'
            paths: list = []
            
            if name == 'create_node':
                paths = self._extract_node_paths(result_text, 'create_node') or ([result_text] if result_text else [])
                label = NodeOperationLabel('create', 1, paths) if paths else None
            
            elif name in ('create_nodes_batch', 'create_wrangle_node'):
                paths = self._extract_node_paths(result_text, name) or ([result_text] if result_text else [])
                label = NodeOperationLabel('create', len(paths) or 1, paths) if paths else None
            
            elif name == 'delete_node':
                op_type = 'delete'
                paths = self._extract_node_paths(result_text, 'delete_node') or ([result_text] if result_text else [])
                label = NodeOperationLabel('delete', 1, paths) if paths else None
            
            elif name == 'set_node_parameter':
                op_type = 'modify'
                # undo_snapshot 包含 node_path, param_name, old_value, new_value
                # ★ 无 snapshot = 参数值未变化 → 不显示 checkpoint（避免用户困惑）
                if undo_snapshot:
                    node_path = undo_snapshot.get("node_path", "")
                    param_name = undo_snapshot.get("param_name", "")
                    old_val = undo_snapshot.get("old_value", "")
                    new_val = undo_snapshot.get("new_value", "")
                    paths = [node_path] if node_path else []
                    # 传 param_diff 给 NodeOperationLabel，展示红绿 diff
                    param_diff = {
                        "param_name": param_name,
                        "old_value": old_val,
                        "new_value": new_val,
                    }
                    label = NodeOperationLabel('modify', 1, paths, param_diff=param_diff) if paths else None
            
            # ★ 通用变更检测（execute_python, run_skill, copy_node 等通过 before/after 快照检测到的变更）
            node_changes = result.get('_node_changes')
            if node_changes and label is None:
                created = node_changes.get('created', [])
                deleted = node_changes.get('deleted', [])
                labels_to_add = []
                
                if created:
                    c_paths = [n['path'] for n in created]
                    op_type = 'create'
                    paths = c_paths
                    labels_to_add.append(
                        ('create', len(created), c_paths, None)
                    )
                if deleted:
                    d_paths = [n['path'] for n in deleted]
                    if not created:
                        op_type = 'delete'
                        paths = d_paths
                    labels_to_add.append(
                        ('delete', len(deleted), d_paths, None)
                    )
                
                # 为每种操作类型生成独立的 checkpoint label
                for l_op, l_count, l_paths, _ in labels_to_add:
                    l_label = NodeOperationLabel(l_op, l_count, l_paths)
                    l_label.nodeClicked.connect(self._navigate_to_node)
                    l_label.undoRequested.connect(
                        lambda _op=l_op, _paths=list(l_paths), _snap=None:
                            self._undo_node_operation(_op, _paths, _snap)
                    )
                    resp.details_layout.addWidget(l_label)
                    entry = (l_label, l_op, list(l_paths), None)
                    self._pending_ops.append(entry)
                    l_label.decided.connect(self._update_batch_bar)
                
                if labels_to_add:
                    self._update_batch_bar()
                    self._scroll_agent_to_bottom()
                    return  # 已处理，跳过下面的通用逻辑
            
            if label:
                label.nodeClicked.connect(self._navigate_to_node)
                # 用 lambda 捕获当前操作的上下文，使撤销精确到这一条操作
                label.undoRequested.connect(
                    lambda _op=op_type, _paths=list(paths), _snap=undo_snapshot:
                        self._undo_node_operation(_op, _paths, _snap)
                )
                resp.details_layout.addWidget(label)
                
                # ★ 追踪未决操作 → Undo All / Keep All 按钮可见
                entry = (label, op_type, list(paths), undo_snapshot)
                self._pending_ops.append(entry)
                label.decided.connect(self._update_batch_bar)
                self._update_batch_bar()
            
            self._scroll_agent_to_bottom()
        except RuntimeError:
            pass  # widget 已被 clear 销毁
    
    def _navigate_to_node(self, node_path: str):
        """点击节点标签时，跳转到该节点并选中"""
        try:
            import hou
            node = hou.node(node_path)
            if node is None:
                self._show_toast(tr('toast.node_not_exist', node_path))
                return
            
            # 选中节点
            node.setSelected(True, clear_all_selected=True)
            
            # 在网络编辑器中跳转到该节点
            try:
                editor = hou.ui.curDesktop().paneTabOfType(hou.paneTabType.NetworkEditor)
                if editor:
                    # 先切换到节点的父网络
                    parent = node.parent()
                    if parent:
                        editor.cd(parent.path())
                    editor.homeToSelection()
            except Exception:
                pass
            
            # 更新节点上下文栏
            self._refresh_node_context()
            
        except ImportError:
            self._show_toast(tr('toast.houdini_unavailable'))
        except Exception as e:
            self._show_toast(tr('toast.jump_failed', e))
    
    # ----------------------------------------------------------------
    # ★ 递归恢复节点树（用于 undo delete 操作）
    # ----------------------------------------------------------------
    def _restore_node_from_snapshot(self, hou, snapshot: dict, _parent_override=None):
        """从快照递归重建节点及其整棵子节点树
        
        Args:
            hou: Houdini 模块引用
            snapshot: _snapshot_node 生成的快照字典
            _parent_override: 若不为 None，则在此节点下创建（用于递归重建子节点）
        
        Returns:
            新建的 hou.Node，或 None（失败时）
        """
        if not snapshot:
            return None
        
        parent_path = snapshot.get("parent_path", "")
        node_type = snapshot.get("node_type", "")
        node_name = snapshot.get("node_name", "")
        has_children_snapshot = bool(snapshot.get("children"))
        
        parent = _parent_override or hou.node(parent_path)
        if parent is None:
            return None
        
        # 1) 创建节点
        # ★ 如果快照中有子节点数据，必须禁止自动创建默认子节点
        #   否则 geo 等容器节点会自动生成 file1 等默认子节点，
        #   与我们递归恢复的原始子节点冲突（名称冲突/多余节点）
        try:
            if has_children_snapshot:
                new_node = parent.createNode(
                    node_type, node_name,
                    run_init_scripts=False,
                    load_contents=False,
                )
            else:
                new_node = parent.createNode(node_type, node_name)
        except Exception:
            return None
        
        # 2) 恢复位置
        pos = snapshot.get("position")
        if pos and len(pos) == 2:
            try:
                new_node.setPosition(hou.Vector2(pos[0], pos[1]))
            except Exception:
                pass
        
        # 3) 恢复参数
        for parm_name, val in snapshot.get("params", {}).items():
            try:
                parm = new_node.parm(parm_name)
                if parm is None:
                    continue
                if isinstance(val, dict) and "expr" in val:
                    lang_str = val.get("lang", "Hscript")
                    lang = (hou.exprLanguage.Python
                            if "python" in lang_str.lower()
                            else hou.exprLanguage.Hscript)
                    parm.setExpression(val["expr"], lang)
                else:
                    parm.set(val)
            except Exception:
                continue
        
        # 4) ★ 清空可能残留的默认子节点（以防万一，确保干净恢复）
        if has_children_snapshot:
            try:
                for default_child in list(new_node.children()):
                    try:
                        default_child.destroy()
                    except Exception:
                        pass
            except Exception:
                pass
        
        # 5) ★ 递归重建子节点
        children_map: dict = {}  # name → hou.Node  用于稍后恢复内部连接
        for child_snap in snapshot.get("children", []):
            child_node = self._restore_node_from_snapshot(hou, child_snap, _parent_override=new_node)
            if child_node:
                children_map[child_node.name()] = child_node
        
        # 6) ★ 恢复子节点间的内部连接
        for iconn in snapshot.get("internal_connections", []):
            try:
                src_node = children_map.get(iconn["src_name"])
                dest_node = children_map.get(iconn["dest_name"])
                if src_node and dest_node:
                    dest_node.setInput(iconn["dest_input"], src_node)
            except Exception:
                continue
        
        # 7) 恢复外部输入连接（仅顶层节点 — 子节点的外部连接由父级调用处理）
        if _parent_override is None:
            for conn in snapshot.get("input_connections", []):
                try:
                    src = hou.node(conn["source_path"])
                    if src:
                        new_node.setInput(conn["input_index"], src)
                except Exception:
                    continue
        
        # 8) 恢复外部输出连接（仅顶层节点）
        if _parent_override is None:
            for conn in snapshot.get("output_connections", []):
                try:
                    dest = hou.node(conn["dest_path"])
                    if dest:
                        dest.setInput(conn["dest_input_index"], new_node, conn.get("output_index", 0))
                except Exception:
                    continue
        
        # 9) 恢复标志位
        try:
            if snapshot.get("display_flag") and hasattr(new_node, 'setDisplayFlag'):
                new_node.setDisplayFlag(True)
            if snapshot.get("render_flag") and hasattr(new_node, 'setRenderFlag'):
                new_node.setRenderFlag(True)
        except Exception:
            pass
        
        return new_node

    def _undo_node_operation(self, op_type: str = 'create',
                              node_paths: list = None,
                              undo_snapshot: dict = None):
        """精确撤销单次节点操作
        
        - create 操作 → 删除该节点（by path）
        - delete 操作 → 从快照递归重建该节点及所有子节点
        - modify 操作 → 恢复参数旧值
        """
        try:
            import hou
        except ImportError:
            self._show_toast(tr('toast.houdini_unavailable'))
            return
        
        try:
            if op_type == 'modify' and undo_snapshot:
                # ---- 撤销参数修改 = 恢复旧值 ----
                node_path = undo_snapshot.get("node_path", "")
                param_name = undo_snapshot.get("param_name", "")
                old_value = undo_snapshot.get("old_value")
                is_tuple = undo_snapshot.get("is_tuple", False)
                
                node = hou.node(node_path)
                if node is None:
                    self._show_toast(tr('toast.node_not_found', node_path))
                    return
                
                if is_tuple:
                    parm_tuple = node.parmTuple(param_name)
                    if parm_tuple is None:
                        self._show_toast(tr('toast.param_not_found', param_name))
                        return
                    parm_tuple.set(old_value)
                else:
                    parm = node.parm(param_name)
                    if parm is None:
                        self._show_toast(tr('toast.param_not_found', param_name))
                        return
                    if isinstance(old_value, dict) and "expr" in old_value:
                        lang_str = old_value.get("lang", "Hscript")
                        lang = (hou.exprLanguage.Python
                                if "python" in lang_str.lower()
                                else hou.exprLanguage.Hscript)
                        parm.setExpression(old_value["expr"], lang)
                    else:
                        parm.set(old_value)
                
                self._show_toast(tr('toast.param_restored', param_name))
            
            elif op_type == 'create':
                # ---- 撤销创建 = 删除节点 ----
                if not node_paths:
                    self._show_toast(tr('toast.missing_path'))
                    return
                deleted = 0
                for p in node_paths:
                    node = hou.node(p)
                    if node is not None:
                        node.destroy()
                        deleted += 1
                if deleted:
                    self._show_toast(tr('toast.undo_create', deleted))
                else:
                    self._show_toast(tr('toast.node_gone'))
            
            elif op_type == 'delete' and undo_snapshot:
                # ---- 撤销删除 = 从快照递归重建整棵节点树 ----
                new_node = self._restore_node_from_snapshot(hou, undo_snapshot)
                if new_node:
                    self._show_toast(tr('toast.node_restored', new_node.path()))
                else:
                    self._show_toast(tr('toast.undo_failed', 'snapshot restore returned None'))
            
            else:
                # 回退：使用 Houdini 原生 undo
                hou.undos.performUndo()
                self._show_toast(tr('toast.undone'))
            
            self._refresh_node_context()
        
        except Exception as e:
            self._show_toast(tr('toast.undo_failed', e))

    # ---------- Undo All / Keep All 批量操作 ----------

    def _update_batch_bar(self):
        """根据未决操作数量显示/隐藏批量操作栏"""
        # 清理已决的条目（label._decided == True）
        self._pending_ops = [
            entry for entry in self._pending_ops
            if entry[0] and not entry[0]._decided
        ]
        count = len(self._pending_ops)
        if count > 0:
            self._batch_count_label.setText(f"{count} 个操作待确认")
            self._batch_bar.setVisible(True)
        else:
            self._batch_bar.setVisible(False)

    def _undo_all_ops(self):
        """撤销所有未决操作（倒序执行，后创建的先撤销）"""
        # 清理已决条目
        self._pending_ops = [
            entry for entry in self._pending_ops
            if entry[0] and not entry[0]._decided
        ]
        if not self._pending_ops:
            self._batch_bar.setVisible(False)
            return
        
        count = 0
        # 倒序：后创建的先撤销（避免依赖冲突）
        for label, op_type, paths, snapshot in reversed(self._pending_ops):
            if label._decided:
                continue
            # ★ 直接执行撤销逻辑，不通过 label._on_undo() 的信号
            #   因为 label._on_undo() 会 emit undoRequested 信号，
            #   而该信号已连接了 _undo_node_operation，会导致双重执行。
            #   这里只更新 label 的 UI 状态，然后手动执行一次撤销。
            label._decided = True
            label._undo_btn.setVisible(False)
            label._keep_btn.setVisible(False)
            label._status_label.setText(tr('status.undone'))
            label._status_label.setProperty("state", "undone")
            label._status_label.style().unpolish(label._status_label)
            label._status_label.style().polish(label._status_label)
            label._status_label.setVisible(True)
            self._undo_node_operation(op_type, paths, snapshot)
            count += 1
        
        self._pending_ops.clear()
        self._batch_bar.setVisible(False)
        if count:
            self._show_toast(f"已撤销全部 {count} 个操作")

    def _keep_all_ops(self):
        """保留所有未决操作"""
        self._pending_ops = [
            entry for entry in self._pending_ops
            if entry[0] and not entry[0]._decided
        ]
        if not self._pending_ops:
            self._batch_bar.setVisible(False)
            return
        
        count = 0
        for label, op_type, paths, snapshot in self._pending_ops:
            if label._decided:
                continue
            label._on_keep()
            label.collapse_diff()  # ★ 自动折叠 diff 展示区
            count += 1
        
        self._pending_ops.clear()
        self._batch_bar.setVisible(False)
        if count:
            self._show_toast(f"已保留全部 {count} 个操作")

    @QtCore.Slot(str, str)
    def _on_add_python_shell(self, code: str, result_json: str):
        """处理 execute_python 的专用 UI 展示"""
        try:
            resp = self._agent_response or self._current_response
            if not resp:
                return
            
            try:
                data = json.loads(result_json)
            except Exception:
                data = {}
            
            raw_output = data.get('output', '')
            error = data.get('error', '')
            success = data.get('success', True)
            
            # 从格式化的输出中提取执行时间和清理内容
            # 格式: "输出:\n...\n返回值: ...\n执行时间: 0.123s"
            exec_time = 0.0
            clean_parts = []
            
            for line in raw_output.split('\n'):
                time_match = re.match(r'^执行时间:\s*([\d.]+)s$', line.strip())
                if time_match:
                    exec_time = float(time_match.group(1))
                    continue
                # 去掉 "输出:" 前缀
                if line.strip() == '输出:':
                    continue
                clean_parts.append(line)
            
            clean_output = '\n'.join(clean_parts).strip()
            
            widget = PythonShellWidget(
                code=code,
                output=clean_output,
                error=error,
                exec_time=exec_time,
                success=success,
                parent=resp
            )
            # 放入 Python Shell 折叠区块（而非 details_layout）
            resp.add_shell_widget(widget)
            self._scroll_agent_to_bottom()
        except RuntimeError:
            pass  # widget 已被 clear 销毁

    @QtCore.Slot(str, str)
    def _on_add_system_shell(self, command: str, result_json: str):
        """处理 execute_shell 的专用 UI 展示"""
        try:
            resp = self._agent_response or self._current_response
            if not resp:
                return

            try:
                data = json.loads(result_json)
            except Exception:
                data = {}

            raw_output = data.get('output', '')
            error = data.get('error', '')
            success = data.get('success', True)
            cwd = data.get('cwd', '')

            # 从输出中提取执行时间和退出码
            exec_time = 0.0
            exit_code = 0
            stdout_parts = []

            for line in raw_output.split('\n'):
                # 匹配 "退出码: 0, 耗时: 0.123s" 或 "⛔ 命令执行失败: 退出码: 1, 耗时: ..."
                time_match = re.search(r'耗时:\s*([\d.]+)s', line)
                code_match = re.search(r'退出码:\s*(\d+)', line)
                if time_match:
                    exec_time = float(time_match.group(1))
                if code_match:
                    exit_code = int(code_match.group(1))
                if time_match or code_match:
                    continue
                # 分离 stdout / stderr
                if line.strip() == '--- stdout ---':
                    continue
                if line.strip() == '--- stderr ---':
                    continue
                stdout_parts.append(line)

            clean_output = '\n'.join(stdout_parts).strip()

            widget = SystemShellWidget(
                command=command,
                output=clean_output,
                error=error,
                exit_code=exit_code,
                exec_time=exec_time,
                success=success,
                cwd=cwd,
                parent=resp
            )
            resp.add_sys_shell_widget(widget)
            self._scroll_agent_to_bottom()
        except RuntimeError:
            pass  # widget 已被 clear 销毁

    def _on_stop(self):
        self.client.request_stop()

    def _on_set_key(self):
        provider = self._current_provider()
        # Custom provider 使用专用配置对话框
        if provider == 'custom':
            self._open_custom_provider_dialog()
            return
        names = {'openai': 'OpenAI', 'deepseek': 'DeepSeek', 'glm': 'GLM（智谱AI）', 'ollama': 'Ollama', 'openrouter': 'OpenRouter'}
        
        key, ok = QtWidgets.QInputDialog.getText(
            self, f"Set {names.get(provider, provider)} API Key",
            "Enter API Key:",
            QtWidgets.QLineEdit.Password
        )
        
        if ok and key.strip():
            self.client.set_api_key(key.strip(), persist=True, provider=provider)
            self._update_key_status()

    def _on_clear(self):
        # ── 如果当前 session 正在运行 agent，先停止 ──
        if self._agent_session_id == self._session_id and self._agent_session_id is not None:
            # 1) 请求后端线程停止
            self.client.request_stop()
            # 2) 断开 agent 对已删除 widget 的引用（防止回调访问已销毁控件）
            self._agent_response = None
            self._agent_todo_list = None
            self._agent_chat_layout = None
            self._agent_scroll_area = None
            # 3) 重置运行状态和按钮
            self._set_running(False)
        
        self._conversation_history.clear()
        self._send_context = None
        self._context_summary = ""
        self._current_response = None
        self._token_stats = {
            'input_tokens': 0, 'output_tokens': 0,
            'reasoning_tokens': 0,
            'cache_read': 0, 'cache_write': 0,
            'total_tokens': 0, 'requests': 0,
            'estimated_cost': 0.0,
        }
        self._call_records = []
        
        # ── 清理待确认操作列表和批量操作栏 ──
        self._pending_ops.clear()
        self._batch_bar.setVisible(False)
        self._session_node_map.clear()
        
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        # 旧 todo_list 已被 deleteLater, 创建新的
        self.todo_list = self._create_todo_list(self.chat_container)
        if self._session_id in self._sessions:
            self._sessions[self._session_id]['todo_list'] = self.todo_list
        
        # 同步到 sessions 字典
        self._save_current_session_state()
        
        # ★ 清空后删除磁盘上的旧 session 文件（防止残留数据在重启后被恢复）
        try:
            old_session_file = self._cache_dir / f"session_{self._session_id}.json"
            if old_session_file.exists():
                old_session_file.unlink()
        except Exception:
            pass
        # ★ 立即更新 manifest（移除已清空的会话条目）
        try:
            self._update_manifest()
        except Exception:
            pass
        
        # 重置标签名
        for i in range(self.session_tabs.count()):
            if self.session_tabs.tabData(i) == self._session_id:
                self.session_tabs.setTabText(i, f"Chat {self._session_counter}")
                break
        
        # 更新统计显示
        self._update_token_stats_display()
        self._update_context_stats()

    # ============================================================
    # ★ 导出对话
    # ============================================================

    def _export_chat(self):
        """导出当前会话的完整原始对话内容（JSON 格式）到用户指定目录"""
        import json
        import datetime

        if not self._conversation_history:
            QtWidgets.QMessageBox.information(
                self, "Export Chat", "当前没有可导出的对话内容。"
            )
            return

        # 选择保存目录
        export_dir = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "选择导出目录",
            "",
            QtWidgets.QFileDialog.ShowDirsOnly | QtWidgets.QFileDialog.DontResolveSymlinks,
        )
        if not export_dir:
            return

        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"houdini_agent_chat_{ts}.json"
        filepath = f"{export_dir}/{filename}"

        # 构造导出数据：元信息 + 完整原始消息列表
        provider = self._current_provider() if hasattr(self, '_current_provider') else ""
        model = self.model_combo.currentText() if hasattr(self, 'model_combo') else ""
        export_data = {
            "meta": {
                "exported_at": datetime.datetime.now().isoformat(),
                "session_id": str(self._session_id),
                "provider": provider,
                "model": model,
                "message_count": len(self._conversation_history),
            },
            "messages": self._conversation_history,
        }

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(export_data, f, ensure_ascii=False, indent=2, default=str)
            QtWidgets.QMessageBox.information(
                self, "Export Chat", f"已导出到：\n{filepath}"
            )
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self, "Export Chat", f"导出失败：\n{e}"
            )

    # ============================================================
    # ★ 斜杠命令执行
    # ============================================================

    def _execute_slash_command(self, command: str):
        """执行斜杠命令 — 由 InputAreaMixin._on_slash_command_selected 调用"""
        handler = getattr(self, f'_slash_{command}', None)
        if handler:
            handler()
        else:
            print(f"[SlashCommand] 未知命令: /{command}")

    def _slash_clear(self):
        """/ clear — 清空当前对话"""
        self._on_clear()

    def _slash_new(self):
        """/new — 新建会话"""
        self._new_session()

    def _slash_memory(self):
        """/memory — 显示记忆系统状态"""
        from ..utils.memory_store import get_memory_store, ABSTRACTION_LEVELS, MEMORY_CATEGORIES
        try:
            store = get_memory_store()
            stats = store.get_stats()
            core_mems = store.get_core_memories(max_count=10)

            lines = ["📊 **长期记忆系统状态**\n"]
            lines.append(f"- 情景记忆 (Episodic): {stats.get('episodic_count', 0)} 条")
            lines.append(f"- 语义记忆 (Semantic): {stats.get('semantic_count', 0)} 条")
            lines.append(f"- 策略记忆 (Procedural): {stats.get('procedural_count', 0)} 条")
            lines.append(f"- 嵌入后端: {stats.get('backend', 'unknown')}")
            lines.append(f"- 向量维度: {stats.get('embedding_dim', 0)}")

            if core_mems:
                lines.append(f"\n🧠 **核心记忆 (L0)** — {len(core_mems)} 条:")
                for i, mem in enumerate(core_mems, 1):
                    conf = f"(conf={mem.confidence:.2f})" if hasattr(mem, 'confidence') else ""
                    lines.append(f"  {i}. [{mem.category}] {mem.rule} {conf}")
            else:
                lines.append("\n🧠 核心记忆 (L0): 暂无")

            # 显示成长指标
            if self._memory_initialized and self._growth_tracker:
                try:
                    gm = self._growth_tracker.get_growth_metrics()
                    lines.append(f"\n📈 **成长指标:**")
                    lines.append(f"  - 成功率: {gm.get('success_rate', 0):.1%}")
                    lines.append(f"  - 错误率: {gm.get('error_rate', 0):.1%}")
                    lines.append(f"  - 成长分: {gm.get('growth_score', 0):.2f}")
                    lines.append(f"  - 任务数: {gm.get('total_tasks', 0)}")
                except Exception:
                    pass

            content = "\n".join(lines)
            self._add_user_message("[/memory]")
            resp = self._add_ai_response()
            resp.set_content(content)
            resp.finalize()
        except Exception as e:
            self._add_user_message("[/memory]")
            resp = self._add_ai_response()
            resp.set_content(f"❌ 记忆系统未就绪: {e}")
            resp.finalize()

    def _slash_remember(self):
        """/remember — 弹出对话框让用户输入要记住的内容"""
        from ..utils.memory_store import get_memory_store, SemanticRecord

        text, ok = QtWidgets.QInputDialog.getText(
            self, "📌 记住偏好", "输入要永久记住的内容（将存为 L0 核心记忆）:"
        )
        if not ok or not text.strip():
            return

        try:
            store = get_memory_store()
            record = SemanticRecord(
                rule=text.strip(),
                confidence=1.0,
                category="preference",
                abstraction_level=0,
            )
            rid = store.add_semantic(record)
            self._add_user_message(f"[/remember] {text.strip()}")
            resp = self._add_ai_response()
            resp.set_content(f"✅ 已写入核心记忆 (L0): {text.strip()}\nID: `{rid}`")
            resp.finalize()
        except Exception as e:
            self._add_user_message(f"[/remember]")
            resp = self._add_ai_response()
            resp.set_content(f"❌ 写入记忆失败: {e}")
            resp.finalize()

    def _slash_forget(self):
        """/forget — 搜索并删除记忆"""
        from ..utils.memory_store import get_memory_store

        keyword, ok = QtWidgets.QInputDialog.getText(
            self, "🧹 清除记忆", "输入关键词搜索要删除的记忆:"
        )
        if not ok or not keyword.strip():
            return

        try:
            store = get_memory_store()
            results = store.search_all_levels(
                query=keyword.strip(), top_k=5, min_confidence=0.0
            )
            if not results:
                self._add_user_message(f"[/forget] {keyword.strip()}")
                resp = self._add_ai_response()
                resp.set_content("未找到匹配的记忆。")
                resp.finalize()
                return

            # 显示找到的记忆，让用户选择删除
            items = []
            for rec, score in results:
                display = f"[L{rec.abstraction_level}][{rec.category}] {rec.rule[:60]} (conf={rec.confidence:.2f})"
                items.append((rec.id, display))

            choices = [d for _, d in items]
            choice, ok2 = QtWidgets.QInputDialog.getItem(
                self, "选择要删除的记忆", "找到以下匹配记忆:", choices, 0, False
            )
            if not ok2:
                return

            idx = choices.index(choice)
            del_id = items[idx][0]
            store.delete_semantic(del_id)

            self._add_user_message(f"[/forget] {keyword.strip()}")
            resp = self._add_ai_response()
            resp.set_content(f"🗑 已删除记忆: {choice}")
            resp.finalize()
        except Exception as e:
            self._add_user_message(f"[/forget]")
            resp = self._add_ai_response()
            resp.set_content(f"❌ 操作失败: {e}")
            resp.finalize()

    def _slash_search_mem(self):
        """/search_mem — 搜索长期记忆"""
        from ..utils.memory_store import get_memory_store, ABSTRACTION_LEVELS

        keyword, ok = QtWidgets.QInputDialog.getText(
            self, "🔍 搜索记忆", "输入搜索关键词:"
        )
        if not ok or not keyword.strip():
            return

        try:
            store = get_memory_store()
            results = store.search_all_levels(
                query=keyword.strip(), top_k=10, min_confidence=0.0
            )

            self._add_user_message(f"[/search_mem] {keyword.strip()}")
            resp = self._add_ai_response()

            if not results:
                resp.set_content("未找到相关记忆。")
            else:
                lines = [f"🔍 **搜索结果** — 关键词: `{keyword.strip()}`  ({len(results)} 条)\n"]
                for i, (rec, score) in enumerate(results, 1):
                    level_name = ABSTRACTION_LEVELS.get(rec.abstraction_level, "unknown")
                    lines.append(
                        f"{i}. **[L{rec.abstraction_level} {level_name}]** [{rec.category}] "
                        f"conf={rec.confidence:.2f}  rel={score:.3f}\n"
                        f"   {rec.rule}"
                    )
                resp.set_content("\n".join(lines))
            resp.finalize()
        except Exception as e:
            self._add_user_message(f"[/search_mem]")
            resp = self._add_ai_response()
            resp.set_content(f"❌ 搜索失败: {e}")
            resp.finalize()

    def _slash_memories(self):
        """/memories — 打开记忆库管理窗口（情景 / 语义 / 策略 增删改查）"""
        try:
            from .memory_manager_dialog import MemoryManagerDialog
            # 直接 exec_，避免依赖 staticmethod exec_centered（旧版模块或热加载缺该方法时会报错）
            MemoryManagerDialog(self).exec_()
        except Exception as e:
            # 不在此处二次 import MemoryMgrSheet：模块未加载全或热加载残留时会再触发 ImportError
            QtWidgets.QMessageBox.critical(
                None,
                tr('memory_mgr.title'),
                f"{tr('memory_mgr.err_load')}\n{e}",
            )

    def _slash_network(self):
        """/network — 读取网络结构"""
        self._on_read_network()

    def _slash_selection(self):
        """/selection — 读取选中节点"""
        self._on_read_selection()

    def _slash_skills(self):
        """/skills — 列出所有技能"""
        result = self.mcp._tool_list_skills({})
        self._add_user_message("[/skills]")
        resp = self._add_ai_response()
        if result.get('success'):
            resp.set_content(result.get('result', '无可用 Skill'))
        else:
            resp.set_content(f"❌ {result.get('error', '未知错误')}")
        resp.finalize()

    def _slash_status(self):
        """/status — 显示系统综合状态"""
        lines = ["📊 **系统状态概览**\n"]

        # 上下文统计
        token_stats = self._token_stats
        lines.append("**Token 统计:**")
        lines.append(f"  - 输入: {token_stats.get('input_tokens', 0):,}")
        lines.append(f"  - 输出: {token_stats.get('output_tokens', 0):,}")
        lines.append(f"  - 总计: {token_stats.get('total_tokens', 0):,}")
        lines.append(f"  - 请求次数: {token_stats.get('requests', 0)}")
        cost = token_stats.get('estimated_cost', 0.0)
        if cost > 0:
            lines.append(f"  - 预估费用: ${cost:.4f}")
        lines.append(f"  - 对话轮数: {len(self._conversation_history)}")

        # 记忆统计
        if self._memory_initialized and self._memory_store:
            try:
                stats = self._memory_store.get_stats()
                lines.append(f"\n**记忆系统:**")
                lines.append(f"  - 情景: {stats.get('episodic_count', 0)}")
                lines.append(f"  - 语义: {stats.get('semantic_count', 0)}")
                lines.append(f"  - 策略: {stats.get('procedural_count', 0)}")
            except Exception:
                pass

        # 成长指标
        if self._memory_initialized and self._growth_tracker:
            try:
                gm = self._growth_tracker.get_growth_metrics()
                lines.append(f"\n**成长指标:**")
                lines.append(f"  - 成功率: {gm.get('success_rate', 0):.1%}")
                lines.append(f"  - 成长分: {gm.get('growth_score', 0):.2f}")
                lines.append(f"  - 累计任务: {gm.get('total_tasks', 0)}")
            except Exception:
                pass

        self._add_user_message("[/status]")
        resp = self._add_ai_response()
        resp.set_content("\n".join(lines))
        resp.finalize()

    def _slash_export(self):
        """/export — 导出训练数据"""
        self._on_export_training_data()

    def _slash_image(self):
        """/image — 附加图片"""
        self._on_attach_image()

    def _slash_help(self):
        """/help — 显示所有斜杠命令"""
        from .cursor_widgets import SLASH_COMMANDS
        from .i18n import get_language

        is_zh = (get_language() == 'zh')
        lines = ["❓ **可用斜杠命令**\n"]
        for cmd, icon, lbl_zh, lbl_en, desc_zh, desc_en, cat in SLASH_COMMANDS:
            label = lbl_zh if is_zh else lbl_en
            desc = desc_zh if is_zh else desc_en
            lines.append(f"  {icon} `/{cmd}` — {label}: {desc}")

        self._add_user_message("[/help]")
        resp = self._add_ai_response()
        resp.set_content("\n".join(lines))
        resp.finalize()

    def _on_read_network(self):
        ok, text = self.mcp.get_network_structure_text()
        if ok:
            # 添加到对话
            self._add_user_message("[Read network structure]")
            response = self._add_ai_response()
            response.add_status("Read network")
            response.add_collapsible("Network structure", text)
            response.finalize()
            _nmsg = {'role': 'user', 'content': f"[Network structure]\n{text}"}
            self._conversation_history.append(_nmsg)
            if self._send_context is not None:
                self._send_context.append(_nmsg)
            self._update_context_stats()
            # 更新节点上下文栏
            self._refresh_node_context()
        else:
            self._add_ai_response().set_content(f"Error: {text}")

    # ============================================================
    # 图片输入支持
    # ============================================================
    
    def _on_read_selection(self):
        ok, text = self.mcp.describe_selection()
        if ok:
            self._add_user_message("[Read selected nodes]")
            response = self._add_ai_response()
            response.add_status("Read selection")
            response.add_collapsible("Node details", text)
            response.finalize()
            _smsg = {'role': 'user', 'content': f"[Selected nodes]\n{text}"}
            self._conversation_history.append(_smsg)
            if self._send_context is not None:
                self._send_context.append(_smsg)
            self._update_context_stats()
            # 更新节点上下文栏
            self._refresh_node_context()
        else:
            self._add_ai_response().set_content(f"Error: {text}")

    def _refresh_node_context(self):
        """刷新节点上下文栏（显示当前网络路径和选中节点）"""
        try:
            import hou
            # 获取当前网络编辑器的工作路径
            path = "/obj"
            editors = [p for p in hou.ui.paneTabs()
                       if p.type() == hou.paneTabType.NetworkEditor]
            if editors:
                pwd = editors[0].pwd()
                if pwd:
                    path = pwd.path()
            # 获取选中节点
            selected = [n.path() for n in hou.selectedNodes()]
            self.node_context_bar.update_context(path, selected)
        except Exception:
            self.node_context_bar.update_context("/obj")

    def _collect_scene_context(self) -> dict:
        """[主线程] 收集 Houdini 场景上下文用于自动 RAG 增强
        
        返回场景上下文 dict，传给后台线程的 _auto_rag_retrieve 使用。
        包含：当前网络路径、选中节点类型、选中节点名。
        """
        ctx = {'network_path': '', 'selected_types': [], 'selected_names': []}
        try:
            import hou  # type: ignore
            # 当前网络路径
            editors = [p for p in hou.ui.paneTabs()
                       if p.type() == hou.paneTabType.NetworkEditor]
            if editors:
                pwd = editors[0].pwd()
                if pwd:
                    ctx['network_path'] = pwd.path()
            # 选中节点的类型和名称
            for n in hou.selectedNodes()[:5]:  # 最多 5 个，避免过多
                ctx['selected_types'].append(n.type().name())
                ctx['selected_names'].append(n.name())
        except Exception:
            pass
        return ctx

    def _on_create_wrangle(self, vex_code: str):
        """从代码块一键创建 Wrangle 节点"""
        result = self.mcp.execute_tool("create_wrangle_node", {"vex_code": vex_code})
        if result.get("success"):
            resp = self._add_ai_response()
            resp.set_content(f"{result.get('result', '已创建 Wrangle 节点')}")
            resp.finalize()
            self._refresh_node_context()
        else:
            resp = self._add_ai_response()
            resp.set_content(f"错误: {result.get('error', '创建 Wrangle 失败')}")
            resp.finalize()

    def _on_export_training_data(self):
        """导出当前对话为训练数据"""
        if not self._conversation_history:
            QtWidgets.QMessageBox.warning(self, "导出失败", "当前没有对话记录可导出")
            return
        
        # 统计对话信息
        user_count = sum(1 for m in self._conversation_history if m.get('role') == 'user')
        assistant_count = sum(1 for m in self._conversation_history if m.get('role') == 'assistant')
        
        if user_count == 0:
            QtWidgets.QMessageBox.warning(self, "导出失败", "对话中没有用户消息")
            return
        
        # 询问导出选项
        msg_box = QtWidgets.QMessageBox(self)
        msg_box.setWindowTitle("导出训练数据")
        msg_box.setText(f"当前对话包含 {user_count} 条用户消息，{assistant_count} 条 AI 回复。\n\n选择导出方式：")
        msg_box.setInformativeText(
            "• 分割模式：每轮对话生成一个训练样本（推荐，样本更多）\n"
            "• 完整模式：整个对话作为一个训练样本"
        )
        
        split_btn = msg_box.addButton("分割模式", QtWidgets.QMessageBox.ActionRole)
        full_btn = msg_box.addButton("完整模式", QtWidgets.QMessageBox.ActionRole)
        cancel_btn = msg_box.addButton("取消", QtWidgets.QMessageBox.RejectRole)
        
        msg_box.exec_()
        
        clicked = msg_box.clickedButton()
        if clicked == cancel_btn:
            return
        
        split_by_user = (clicked == split_btn)
        
        # 导出
        try:
            from ..utils.training_data_exporter import ChatTrainingExporter
            
            exporter = ChatTrainingExporter()
            filepath = exporter.export_conversation(
                self._conversation_history,
                system_prompt=self._system_prompt,
                split_by_user=split_by_user
            )
            
            # 显示成功消息
            response = self._add_ai_response()
            response.add_status("训练数据已导出")
            
            # 读取生成的样本数
            sample_count = 0
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    sample_count = sum(1 for _ in f)
            except:
                pass
            
            response.set_content(
                f"成功导出训练数据！\n\n"
                f"文件: {filepath}\n"
                f"训练样本数: {sample_count}\n"
                f"对话轮数: {user_count}\n"
                f"导出模式: {'分割模式' if split_by_user else '完整模式'}\n\n"
                f"提示: 文件为 JSONL 格式，可直接用于 OpenAI/DeepSeek 微调"
            )
            response.finalize()
            
            # 询问是否打开文件夹
            reply = QtWidgets.QMessageBox.question(
                self, 
                "导出成功",
                f"已生成 {sample_count} 个训练样本\n\n是否打开所在文件夹？",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
            )
            
            if reply == QtWidgets.QMessageBox.Yes:
                import os
                import subprocess
                folder = os.path.dirname(filepath)
                if os.name == 'nt':  # Windows
                    os.startfile(folder)
                else:  # macOS/Linux
                    subprocess.run(['open' if 'darwin' in __import__('sys').platform else 'xdg-open', folder])
        
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "导出错误", f"导出训练数据时发生错误：{str(e)}")

    # ===== 缓存管理 =====
    
