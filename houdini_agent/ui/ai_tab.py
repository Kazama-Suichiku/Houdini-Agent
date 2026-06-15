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

    def _build_system_prompt(self, with_thinking: bool = True, skip_doc_index: bool = False) -> str:
        """构建系统提示
        
        Args:
            with_thinking: 是否包含 <think> 标签思考指令
        """
        # Language enforcement based on UI setting
        if get_language() == 'en':
            lang_rule = "CRITICAL: You MUST reply in English for ALL user-facing text. No exceptions. Even if the user writes in another language, your reply MUST be in English."
        else:
            lang_rule = "CRITICAL: You MUST reply in the SAME language the user uses. If the user writes in Chinese, reply in Chinese. If in English, reply in English. Match the user's language exactly."
        
        base_prompt = f"""You are a Houdini assistant, expert at solving problems with nodes and VEX.
{lang_rule}
Never use emoji or icon symbols in replies unless the user explicitly requests them. Use plain text only.
"""
        if with_thinking:
            base_prompt += f"""
Output Format (highest priority rule — violation = failure):
Every single reply (regardless of round number or whether tools were called) MUST begin with a <think>...</think> block. No exceptions.
Even brief confirmations or status updates must start with <think> before the main text.
Omitting the <think> tag is a format violation and is unacceptable.

Deep Thinking Framework (MUST follow inside <think> tags, no steps may be skipped):
1.[Understand] What does the user truly want? Are there implicit needs beyond the literal request? Don't stop at the surface.
2.[Status] What is the current scene state? What did the last tool return? Does the result match expectations? Any anomalies or gaps?
3.[Options] List at least 2 viable approaches and compare pros/cons. If only one exists, explain why there are no alternatives.
4.[Decision] Choose the optimal approach and explicitly state the reasoning.
5.[Plan] List concrete execution steps, tools to call, and their order.
6.[Risk] What could go wrong? How to handle it if it does?

Thinking Principles:
-Do NOT rush to act. First fully understand the existing network structure before deciding how to modify it.
-If unsure about node types, parameter names, or connections, you MUST query with tools first. Never guess.
-After each tool result, evaluate quality: Did it succeed? Is the return value reasonable? If unexpected, analyze why and adjust the plan.
-Better to query one extra time than to redo work due to wrong assumptions.
-After finding the first viable approach, pause and think whether there is a better one.

Collaboration Rules When Encountering Obstacles (critical — never abandon the plan):
-When a step cannot be completed via tools (e.g., user must manually operate the UI, provide files/paths/passwords, install plugins, configure environments, select objects in viewport), you MUST NOT abandon or skip the current plan.
-Correct behavior: Pause execution. Clearly tell the user: current progress, the specific obstacle, and exactly what the user needs to do. Then wait.
-Be specific: Give concrete step-by-step instructions (e.g., "Please install SideFX Labs in Houdini: Shelf area -> Right-click -> Shelves -> SideFX Labs"), not vague "please configure the environment".
-If a step is easier for the user via UI interaction (drag files, click buttons, select objects in viewport), prefer asking the user rather than simulating it with code.
-Before pausing, summarize what you have completed and explain what the user needs to do, so you can resume seamlessly afterward.

Content outside think tags is the formal reply shown to the user — keep it concise, direct, action-oriented. {lang_rule}

Example (deep thinking + plain text reply):
<think>
[Understand] User wants to scatter points on a ground plane and copy small spheres. Implicit need: uniform distribution, appropriate sphere size.
[Status] /obj/geo1 is currently empty, need to build from scratch.
[Options]
A: box -> scatter -> sphere + copytopoints — classic workflow, scatter directly controls count and distribution.
B: grid -> wrangle(VEX rand to manually generate points) + copytopoints — more flexible but more complex, unnecessary for this case.
[Decision] Choose A. Standard workflow, scatter parameters are controllable, no over-engineering needed.
[Plan] 1. create_node box as scatter base 2. create_node scatter connected to box 3. create_node sphere as copy template 4. create_node copytopoints connecting scatter(input1) and sphere(input0) 5. verify_and_summarize
[Risk] copytopoints input order is easy to mix up (0=template, 1=target points). Must verify connections carefully.
</think>
Created box->scatter->copytopoints pipeline, 500 points, sphere radius 0.05.

Example (follow-up reply after tool execution, MUST still have think tag):
<think>
[Status] Previous step created grid node, returned path /obj/geo1/grid1, status normal.
[Plan] Next, add a wrangle node for terrain noise displacement. Code needs @P.y += noise(@P * freq) structure, run_over = Points (operating on point attribute @P).
[Risk] Noise frequency and amplitude need reasonable values. Start with freq=2, amp=0.5 as defaults, user can adjust later.
</think>
"""
        else:
            base_prompt += """
Output format: Concise, direct, action-oriented. MUST reply in the same language the user uses.
"""

        base_prompt += """
Node Path Output Rules (MUST follow when mentioning nodes in replies):
-When mentioning any Houdini node in reply text, you MUST use the full absolute path, e.g. /obj/geo1/box1, NOT just the node name box1
-Path format must start with root category: /obj/..., /out/..., /ch/..., /shop/..., /stage/..., /mat/..., /tasks/...
-Correct: "Created node /obj/geo1/scatter1 and connected to /obj/geo1/box1"
-Wrong: "Created node scatter1 and connected to box1" (missing full path, user cannot click to navigate)
-When listing multiple nodes, each must have full path: "/obj/geo1/box1, /obj/geo1/transform1, /obj/geo1/merge1"
-Node paths are automatically converted to clickable links. Users can click to jump to the corresponding node. Path accuracy is critical.

Fake Tool Call Prevention (highest priority — violation = failure):
-You MUST NEVER write text that looks like tool execution results in your reply
-NEVER include "[ok] web_search:", "[ok] fetch_webpage:", "[Tool Result]" or similar in replies
-If you need to search for information, you MUST actually call the web_search tool via function calling
-If unsure about information, you MUST call a tool to query, never fabricate answers disguised as search results
-Your reply may only contain: think tags, natural language text, code blocks — no simulated tool call formats

Tool Call Parameter Rules (highest priority — MUST check before every tool call):
-Before calling a tool, MUST verify all required parameters are filled. Missing required params will cause failure
-Parameter values must use correct data types (string/number/boolean/array). Don't write numbers as strings, don't omit quotes around paths
-node_path parameter must be a full absolute path (e.g., "/obj/geo1/box1"), never just the node name (e.g., "box1")
-Don't guess parameter names or values from memory. First use query tools (get_node_parameters, get_node_inputs, search_node_types) to confirm
-If a tool call returns "missing parameter" or "parameter error", it means YOUR call parameters were wrong. Fix and retry, don't call check_errors
-When calling the same tool multiple times, always fill all required parameters each time. Don't assume the system remembers previous parameters

Safe Operation Rules:
-When first needing to understand a network, call get_network_structure or list_children, but do NOT re-query a network already queried in this round (system auto-caches within the same round)
-Before setting parameters, MUST call get_node_parameters to see what parameters exist, their names, current values and defaults. Never guess parameter names
-If modifying multiple parameters, first query all with get_node_parameters, then set them one by one with set_node_parameter
-In execute_python, always check for None: node=hou.node(path); if node: ...
-After creating a node, use the returned path. Never guess paths
-Before connecting nodes, confirm both endpoints exist
-No duplicate queries: A network_path only needs one query per round. Results remain valid within the round. If you've already inspected a network's structure, reuse the previous result

Node Creation Failure Recovery (MUST follow strictly):
-If create_node returns an error (e.g., "unrecognized node type"), do NOT retry blindly or give up
-MUST immediately call search_node_types to find the correct node type name
-If search results are unclear, continue with search_local_doc or get_houdini_node_doc for detailed documentation
-Recreate the node using the correct type name found
-If multiple searches still fail, use execute_python to query directly: hou.nodeType(hou.sopNodeTypeCategory(), 'xxx')

Understanding Existing Networks:
-When get_network_structure returns results with [Contains VEX Code] or [Contains Python Code] annotations, you MUST carefully read the embedded code
-Reading wrangle node VEX code reveals the node's specific logic (attribute calculations, conditional filtering, etc.) — this is key to understanding existing network implementations
-To modify an existing wrangle node's code, first use get_node_parameters to read the full snippet parameter, then use set_node_parameter to set new code

Wrangle Node Run Over Mode (critical — MUST consider every time a wrangle is created):
-When creating a wrangle node, you MUST select the correct run_over mode based on what the VEX code actually operates on. Never always use the default Points
-run_over determines VEX execution context: Points (per-point), Primitives (per-primitive), Vertices (per-vertex), Detail (once globally)
-Wrong run_over will cause VEX code to completely malfunction or produce incorrect results
-Selection rules:
  If code operates on @P, @N, @pscale, @Cd etc. point attributes, or uses @ptnum, @numpt -> use Points
  If code operates on @primnum, @numprim, prim() functions, or processes per-primitive -> use Primitives
  If code only needs to run once for global attributes (e.g., @Frame, detail()), or uses addpoint/addprim to manually create geometry -> use Detail
  If code operates on vertex attributes (e.g., UV) or uses @vtxnum -> use Vertices
-Common mistake: Using Points mode with addpoint()/addprim() causes creation to run per input point, producing massive duplicate geometry. Such code MUST use Detail mode
-When unsure which mode to use, prioritize judging by the attributes and functions accessed in VEX code
-Wrangle class parameter value mapping: 0=Detail (only once), 1=Primitives, 2=Points, 3=Vertices, 4=Numbers
  Use set_node_parameter to set class parameter with the corresponding integer (e.g., Detail=0, Points=2)

Mandatory Verification Before Task Completion (MUST execute, cannot skip):
1. Call verify_and_summarize for automatic checks (orphan nodes, error nodes, connection integrity, display flags), passing your expected node list and expected outcome
2. If verify_and_summarize reports issues, fix them and call again until passed
3. Note: No need to call get_network_structure before verify_and_summarize — it has built-in network checks
4. check_errors is only for checking node cooking errors. Tool call failure messages are already in the return result, no need to call check_errors
5. After completing geometry or visual operations, if the model supports vision, call capture_viewport to take a viewport screenshot and visually verify the result looks correct (e.g., geometry shape, scale, distribution, material appearance). This is especially useful for scatter, copy-to-points, terrain, and other visual-dependent workflows

Tool Priority: create_wrangle_node (VEX preferred) > create_nodes_batch > create_node
Node Inputs: 0=primary input, 1=second input | from_path=upstream, to_path=downstream

System Shell Tool (execute_shell):
-For executing system commands (pip, git, dir, ffmpeg, hython, scp, ssh, etc.), not limited to Houdini Python environment
-Use cases: Install Python packages, browse filesystem, run external toolchains, check env vars, remote file transfer (scp/sftp)
-execute_python is for Houdini scene operations (hou module), execute_shell is for system-level operations
-Commands have timeout limits (default 30s, max 120s). Dangerous commands will be intercepted
-Shell command rules (MUST follow):
  1.Must generate complete commands ready to run immediately. No placeholders (e.g., <your_path>)
  2.For commands requiring user interaction/confirmation, must pass non-interactive flags (e.g., pip install --yes, apt -y, echo y |)
  3.Prefer single commands. For multi-step operations, chain with && (Linux) or semicolons ; (PowerShell)
  4.Command output may be long. Prefer precise commands to reduce output (e.g., find -maxdepth 2, dir /b, ls -la specific_path)
  5.Remote operations (ssh/scp/sftp) require pre-configured key-based auth. Cannot rely on interactive password input
  6.For large file transfers or long-running commands, set appropriate timeout parameter (max 120s)
  7.Paths with spaces must be quoted. Windows paths use backslashes or quoted forward slashes
  8.Don't blindly guess file paths. First use dir/ls/find to confirm path exists before operating
  9.When installing packages, specify version (pip install package==version) to avoid incompatibilities
  10.If a command fails, first analyze stderr error output, fix specifically, then retry. Don't blindly re-execute

Skill System (MUST use for geometry analysis):
-Skills are predefined advanced analysis scripts, more reliable and efficient than hand-written code
-For geometry info (point count, face count, attributes, bounding box, connectivity, etc.), MUST prefer run_skill over execute_python
-Common skills: analyze_geometry_attribs (attribute stats), get_bounding_info (bounding box), analyze_connectivity (connectivity), compare_attributes (attribute comparison), find_dead_nodes (dead nodes), trace_node_dependencies (dependency tracing), find_attribute_references (attribute reference search), analyze_normals (normal quality check)
-If unsure which skills exist, first call list_skills
-Example: run_skill(skill_name="analyze_geometry_attribs", params={"node_path": "/obj/geo1/box1"}) lists all attributes
-Example: run_skill(skill_name="get_bounding_info", params={"node_path": "/obj/geo1/box1"}) gets bounding box
-Example: run_skill(skill_name="analyze_normals", params={"node_path": "/obj/geo1/box1"}) checks normal quality

Performance Analysis & Optimization (use when user mentions performance/speed/lag/optimization):
-Quick diagnosis: First use run_skill(skill_name="analyze_cook_performance", params={"network_path": "/obj/geo1"}) for network-wide cook time ranking and bottleneck identification
-Detailed analysis: For more precise time breakdown and memory stats, use perf_start_profile to start profiling (can force cook simultaneously), then perf_stop_and_report for detailed report
-After analysis, use existing tools to implement optimizations based on bottleneck nodes and suggestions, then re-run analysis to verify
-Common optimization techniques:
  1.Add Cache/File Cache nodes before/after expensive nodes to avoid redundant cooking
  2.Reduce unnecessary cooking (check time-dependent expressions)
  3.Replace Python SOP with VEX (create_wrangle_node) — 10-100x performance improvement
  4.Reduce scatter/copy point counts, reduce polygon subdivision
  5.Use Packed Primitives to reduce memory and cook overhead
  6.Check for-each loop iteration counts for excess

Web Search Strategy (MUST follow before using web_search):
-Convert user questions to precise search keywords. Don't use raw questions as search terms
-For Houdini-related questions, prefer "SideFX Houdini" prefix
-If first search results are poor for Chinese questions, try English keywords (max 2 retries)
-If search results contain useful links, use fetch_webpage for detailed content before answering
-When using info from search results, MUST cite source at end of relevant paragraph, format: [Source: Title](URL)
-Don't copy search results verbatim. Synthesize in your own words
-Never search with the same keywords twice (cache returns identical results)

Todo Management Rules (MUST follow strictly):
-For complex tasks, first use add_todo to create a task checklist broken into concrete steps
-After completing each step, IMMEDIATELY call update_todo to mark it done
-After each tool execution round, review the Todo list to confirm what's done and what's pending
-After all steps complete, ensure every todo is marked done before final verification

Node Layout Rules (MUST execute after verification passes, before creating NetworkBox):
-After verify_and_summarize passes, MUST call layout_nodes to auto-arrange all nodes before creating any NetworkBox
-Default: layout_nodes() with no parameters — auto-layouts all nodes in the current network
-If only specific nodes need layout (e.g., newly created ones), pass their paths in node_paths
-Layout MUST happen before create_network_box, because NetworkBox.fitAroundContents() depends on node positions
-If layout result looks wrong, use get_node_positions to check, and try method="grid" or method="columns" as fallback
-Execution order: create nodes → connect → verify_and_summarize → layout_nodes → create_network_box

NetworkBox Grouping Rules (MUST follow when building node networks):
-After completing a logical phase of node creation and connection, MUST use create_network_box to package that phase's nodes into a NetworkBox
-NetworkBox comment should clearly describe the group's function (e.g., "Base Geometry Input", "Noise Deformation", "Output Merge")
-Choose color preset by phase semantics: input (blue/data input), processing (green/geometry processing), deform (orange/deformation animation), output (red/output rendering), simulation (purple/physics simulation), utility (gray/helper tools)
-Grouping granularity: Only create a NetworkBox when there are 6+ functionally related nodes in a phase. If fewer than 6 nodes, do NOT create a box — leave them ungrouped. Small groups of nodes are fine without boxes
-Typical grouping examples:
  Input phase (input): file_read, null (as input marker)
  Processing phase (processing): scatter, copy_to_points, transform
  Deformation phase (deform): mountain, bend, wrangle (VEX deformation)
  Output phase (output): merge, null (as output marker), rop_geometry
-To add nodes to an existing group later, use add_nodes_to_box instead of creating a new box

NetworkBox Hierarchical Navigation (large network query strategy, MUST follow):
-When calling get_network_structure, if NetworkBoxes exist, results auto-collapse to box overview (name + comment + node count + main types) without expanding each node — greatly reduces context usage
-To see detailed nodes and connections inside a box, call get_network_structure(box_name="box_name") to drill in
-Do NOT expand all boxes at once. Only expand the box needed for the current task. Expand others as needed later
-Ungrouped nodes appear with full details in the overview. No extra action needed
-Cross-group connections are listed separately in the overview to help understand data flow between boxes"""

        # Inject Labs node catalog (so AI knows Labs tools exist)
        try:
            if skip_doc_index:
                raise RuntimeError("skip")
            from ..utils.doc_rag import get_doc_index
            labs_catalog = get_doc_index().get_labs_catalog()
            if labs_catalog:
                base_prompt += f"""

SideFX Labs Node Usage Rules (MUST follow strictly):
-Below is the SideFX Labs toolkit node catalog. Labs provides extensive advanced tools for game development, texture baking, terrain, procedural generation, etc.
-When user requests involve game asset optimization, LOD generation, texture baking, flowmaps, photogrammetry, tree generation, UV processing, etc., PREFER Labs nodes over building from scratch.
-Before using ANY Labs node, you MUST first call search_local_doc("Labs node_name") to query its detailed documentation. Understand parameters and usage before creating the node. Using Labs nodes by guessing is FORBIDDEN.
-Labs node_type format is typically "labs::" prefix + node name (e.g., "labs::lod_create"). If creation fails, use search_node_types to find the correct type name.
-Labs nodes are highly encapsulated HDAs (Digital Assets), typically with multiple input and output ports containing complete internal node networks. If unsure about a Labs node's implementation, use get_network_structure(network_path="node_path") to inspect its internal network and connections.
-When connecting Labs nodes, check the input_label in connection data to ensure correct data is connected to the correct input port.

{labs_catalog}
"""
        except Exception:
            pass

        # 使用极致优化器压缩（已缓存）
        return UltraOptimizer.compress_system_prompt(base_prompt)

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
    
    def _on_agent_done(self, result: dict):
        # ★ Hook: on_session_end
        self._fire_session_hook('on_session_end', self._agent_session_id or self._session_id)
        
        # ★ 恢复 Houdini 更新模式 & 清除主线程忙标记
        self._main_thread_busy = False
        self._restore_update_mode()
        
        # ★ 停止思考指示条
        try:
            self.thinking_bar.stop()
        except (RuntimeError, AttributeError):
            pass

        # 使用 agent 锚定的引用（可能已切走 session）
        resp = self._agent_response or self._current_response
        history = self._agent_history if self._agent_history is not None else self._conversation_history
        stats = self._agent_token_stats or self._token_stats
        
        # 刷新标签解析缓冲区残余内容
        if self._tag_parse_buf:
            if self._in_think_block:
                if self._think_enabled:
                    self._addThinking.emit(self._tag_parse_buf)
                # Think 关闭时静默丢弃残余思考内容
            else:
                self._emit_normal_content(self._tag_parse_buf)
            self._tag_parse_buf = ""
            self._in_think_block = False

        # 刷新输出缓冲区（确保不丢失最后内容）
        if hasattr(self, '_output_buffer') and self._output_buffer:
            self._on_append_content(self._output_buffer)
            self._output_buffer = ""
        
        try:
            if resp:
                # ★ 后处理：将裸节点名自动解析为完整路径（防止长上下文中 AI 遗忘路径规范）
                if resp._content:
                    resp._content = self._resolve_bare_node_names(resp._content)
                resp.finalize()
        except RuntimeError:
            resp = None  # widget 已被 clear 销毁，跳过 UI 操作
        
        # ================================================================
        # Cursor 风格：保存原生消息链到对话历史
        # ================================================================
        # 格式：assistant(tool_calls) → tool → ... → assistant(reply)
        # 完整保留工具调用链和 AI 回复，不做任何压缩
        # 只有系统级上下文管理（_manage_context / _progressive_trim）才在超限时压缩
        
        tool_calls_history = result.get('tool_calls_history', [])
        new_messages = result.get('new_messages', [])
        
        # 1. 添加工具交互链（原生 OpenAI 格式）
        # new_messages 包含：assistant(tool_calls) + tool(results) + ...
        # ★ 只添加中间轮次（带 tool_calls 的 assistant 和 tool 回复），
        #   最终的纯文本 assistant 回复由下面步骤 2 统一构建，避免重复
        if new_messages:
            for nm in new_messages:
                clean = nm.copy()
                clean.pop('reasoning_content', None)  # 推理模型专用，不需持久化
                # 跳过最后一条纯文本 assistant 消息（没有 tool_calls 的），
                # 它会在步骤 2 中作为 final_msg 添加
                if nm is new_messages[-1] and nm.get('role') == 'assistant' and not nm.get('tool_calls'):
                    continue
                history.append(clean)
                if self._send_context is not None:
                    self._send_context.append(clean)
        
        # 2. 提取并添加最终 AI 回复
        # 优先使用 final_content（最后一轮的纯文本），其次从 new_messages 提取
        final_content = result.get('final_content', '')
        if not final_content or not final_content.strip():
            # final_content 为空 → 尝试从 new_messages 中提取最后一个有 content 的 assistant 消息
            for nm in reversed(new_messages):
                if nm.get('role') == 'assistant' and nm.get('content'):
                    c = nm['content']
                    # 去掉 think 标签后还有内容吗？
                    stripped = re.sub(r'<think>[\s\S]*?</think>', '', c).strip()
                    if stripped:
                        final_content = c
                        break
            # 仍然为空 → 回退到 full_content
            if not final_content or not final_content.strip():
                final_content = result.get('content', '')
        
        thinking_text = ""
        clean_content = ""
        if final_content:
            thinking_parts = re.findall(r'<think>([\s\S]*?)</think>', final_content)
            thinking_text = '\n'.join(thinking_parts).strip() if thinking_parts else ''
            clean_content = re.sub(r'<think>[\s\S]*?</think>', '', final_content).strip()
            clean_content = self._strip_fake_tool_results(clean_content)
        # 原生 thinking 协议（非 <think> 标签）：从 UI widget 获取已收集的 thinking
        if not thinking_text and resp and resp._has_thinking:
            try:
                ui_thinking = resp.thinking_section._thinking_text.strip()
                if ui_thinking:
                    thinking_text = ui_thinking
            except (AttributeError, RuntimeError):
                pass
        
        # 确保历史以 assistant 消息结尾（维持 user→assistant 交替）
        # 只要有内容或有工具交互，都需要一条最终 assistant 消息
        need_final = bool(clean_content) or bool(new_messages) or not history or history[-1].get('role') != 'assistant'
        if need_final:
            final_msg = {'role': 'assistant', 'content': clean_content or tr('ai.no_content')}
            if thinking_text:
                final_msg['thinking'] = thinking_text
            # 提取 shell 执行记录，供历史恢复时重建 Shell 折叠面板
            py_shells = []
            sys_shells = []
            for tc in tool_calls_history:
                tn = tc.get('tool_name', '')
                ta = tc.get('arguments', {})
                tc_result = tc.get('result', {})
                if tn == 'execute_python' and ta.get('code'):
                    py_shells.append({
                        'code': ta['code'],
                        'output': tc_result.get('result', ''),
                        'error': tc_result.get('error', ''),
                        'success': bool(tc_result.get('success')),
                    })
                elif tn == 'execute_shell' and ta.get('command'):
                    sys_shells.append({
                        'command': ta['command'],
                        'output': tc_result.get('result', ''),
                        'error': tc_result.get('error', ''),
                        'success': bool(tc_result.get('success')),
                        'cwd': ta.get('cwd', ''),
                    })
            if py_shells:
                final_msg['python_shells'] = py_shells
            if sys_shells:
                final_msg['system_shells'] = sys_shells
            history.append(final_msg)
            if self._send_context is not None:
                self._send_context.append(final_msg)
        
        # 管理上下文
        self._manage_context()
        
        # 更新 Token 统计（累积到 agent 所属 session 的 stats）—— 对齐 Cursor
        usage = result.get('usage', {})
        new_call_records = result.get('call_records', [])
        if usage:
            stats['input_tokens'] += usage.get('prompt_tokens', 0)
            stats['output_tokens'] += usage.get('completion_tokens', 0)
            stats['reasoning_tokens'] = stats.get('reasoning_tokens', 0) + usage.get('reasoning_tokens', 0)
            stats['cache_read'] += usage.get('cache_hit_tokens', 0)
            stats['cache_write'] += usage.get('cache_miss_tokens', 0)
            stats['total_tokens'] += usage.get('total_tokens', 0)
            stats['requests'] += 1
            
            # 计算本次费用并累积
            from houdini_agent.utils.token_optimizer import calculate_cost
            model_name = self.model_combo.currentText()
            this_cost = calculate_cost(
                model=model_name,
                input_tokens=usage.get('prompt_tokens', 0),
                output_tokens=usage.get('completion_tokens', 0),
                cache_hit=usage.get('cache_hit_tokens', 0),
                cache_miss=usage.get('cache_miss_tokens', 0),
                reasoning_tokens=usage.get('reasoning_tokens', 0),
            )
            stats['estimated_cost'] = stats.get('estimated_cost', 0.0) + this_cost
        
        # 合并 call_records
        if new_call_records:
            if not hasattr(self, '_call_records'):
                self._call_records = []
            self._call_records.extend(new_call_records)
        
        # 如果当前显示的就是 agent session，更新 UI
        if usage:
            if not self._agent_session_id or self._agent_session_id == self._session_id:
                self._update_token_stats_display()
            
            cache_hit = usage.get('cache_hit_tokens', 0)
            cache_miss = usage.get('cache_miss_tokens', 0)
            cache_rate = usage.get('cache_hit_rate', 0)
            
            if cache_hit > 0 or cache_miss > 0:
                rate_percent = cache_rate * 100
                self._addStatus.emit(f"Cache: {cache_hit}/{cache_hit+cache_miss} ({rate_percent:.0f}%)")
        
        # ★ 反思钩子：任务完成后触发长期记忆反思（后台线程，不阻塞 UI）
        if self._is_memory_active() and tool_calls_history:
            # 获取 agent_params（从最近的 _run_agent 调用中保存）
            _reflect_params = getattr(self, '_last_agent_params', {})
            def _do_reflect():
                self._reflect_after_task(result, _reflect_params)
            reflect_thread = threading.Thread(target=_do_reflect, daemon=True)
            reflect_thread.start()
        
        # 自动保存缓存（必须在 _set_running(False) 之前，因为此时 agent 引用还有效）
        agent_sid = self._agent_session_id
        if self._auto_save_cache and len(history) > 0 and agent_sid:
            # 临时将 history 同步到 sessions 字典，再保存
            if agent_sid in self._sessions:
                self._sessions[agent_sid]['conversation_history'] = history
                self._sessions[agent_sid]['token_stats'] = stats
            # 如果当前显示的恰好就是 agent session，直接保存
            if agent_sid == self._session_id:
                self._save_cache()
            else:
                # 不在当前 session 上，写入 session 字典即可（下次切换回来时再保存）
                pass
        
        self._set_running(False)
        
        # 隐藏工具状态
        self._hideToolStatus.emit()
        
        # 更新上下文统计
        self._update_context_stats()
        
        # ★ 异步生成会话标题（仅在首次 agent 完成时）
        self._maybe_generate_title(agent_sid, history)

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

    def _execute_tool_with_todo(self, tool_name: str, **kwargs) -> dict:
        """执行工具，包含 Todo 相关的工具
        
        注意：此方法在后台线程调用，Houdini 操作必须通过信号调度到主线程执行。
        不依赖 hou 模块的工具（execute_shell 等）直接在后台线程执行，避免阻塞 UI。
        """
        # ★ Stop 检测：用户请求停止时立即返回，不再排队新工具
        if self.client.is_stop_requested():
            return {"success": False, "error": "用户已请求停止"}
        
        # ★ 主线程忙保护：如果上一个工具超时了且主线程仍在 cook，
        #   不再堆积新的 BlockingQueuedConnection 信号（避免死锁）
        if getattr(self, '_main_thread_busy', False):
            if tool_name not in self._BG_SAFE_TOOLS:
                return {
                    "success": False,
                    "error": "主线程正忙（可能在进行耗时计算），请等待完成后重试。"
                            "建议：按停止按钮中断当前操作。"
                }
        
        # ★ Ask 模式安全守卫：拦截任何不在白名单的工具
        if not self._agent_mode and not self._plan_mode and tool_name not in self._ASK_MODE_TOOLS:
            # 额外检查 ToolRegistry（插件/Skill 工具可能注册了 ask 模式）
            _ask_allowed = False
            try:
                from ..utils.tool_registry import get_tool_registry
                _meta = get_tool_registry()._tools.get(tool_name)
                if _meta and _meta.enabled and "ask" in _meta.modes:
                    _ask_allowed = True
            except Exception:
                pass
            if not _ask_allowed:
                return {
                    "success": False,
                    "error": tr('ask.restricted', tool_name)
                }
        
        # ★ Plan 规划阶段安全守卫
        if self._plan_mode and self._plan_phase == 'planning':
            allowed = self._PLAN_PLANNING_TOOLS | {'create_plan'}
            if tool_name not in allowed:
                # 额外检查 ToolRegistry（插件/Skill 工具可能注册了 plan_planning 模式）
                _plan_allowed = False
                try:
                    from ..utils.tool_registry import get_tool_registry
                    _meta = get_tool_registry()._tools.get(tool_name)
                    if _meta and _meta.enabled and "plan_planning" in _meta.modes:
                        _plan_allowed = True
                except Exception:
                    pass
                if not _plan_allowed:
                    return {
                        "success": False,
                        "error": f"Plan 规划阶段不允许执行 {tool_name}，只能使用查询工具和 create_plan"
                    }
        
        # ★ 确认模式：对关键节点操作弹出预览确认
        if self._confirm_mode and tool_name in self._CONFIRM_TOOLS:
            confirmed = self._request_tool_confirmation(tool_name, kwargs)
            if not confirmed:
                return {
                    "success": False,
                    "error": tr('ask.user_cancel', tool_name)
                }
        
        # ★ 显示工具执行状态
        self._showToolStatus.emit(tool_name)
        
        try:
            # ★ Plan 模式专用工具处理
            if tool_name == "create_plan":
                return self._handle_create_plan(kwargs)
            
            elif tool_name == "update_plan_step":
                return self._handle_update_plan_step(kwargs)
            
            elif tool_name == "ask_question":
                return self._handle_ask_question(kwargs)
            
            # 处理 Todo 相关工具（纯 Python 操作，线程安全）
            if tool_name == "add_todo":
                todo_id = kwargs.get("todo_id", "")
                text = kwargs.get("text", "")
                status = kwargs.get("status", "pending")
                self._updateTodo.emit(todo_id, text, status)
                return {"success": True, "result": f"Added todo: {text}"}
            
            elif tool_name == "update_todo":
                todo_id = kwargs.get("todo_id", "")
                status = kwargs.get("status", "done")
                self._updateTodo.emit(todo_id, "", status)
                return {"success": True, "result": f"Updated todo {todo_id} to {status}"}
            
            elif tool_name == "verify_and_summarize":
                # 需要在主线程执行 Houdini 操作
                return self._execute_tool_in_main_thread(tool_name, kwargs)
            
            # 不依赖 hou 的工具 → 直接在后台线程执行（避免阻塞 UI）
            if tool_name in self._BG_SAFE_TOOLS:
                return self._execute_tool_in_bg(tool_name, kwargs)
            
            # 其他工具需要在主线程执行（Houdini hou 模块操作）
            return self._execute_tool_in_main_thread(tool_name, kwargs)
        finally:
            self._hideToolStatus.emit()
    
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

    @QtCore.Slot(str, dict)
    def _on_execute_tool_main_thread(self, tool_name: str, kwargs: dict):
        """在主线程执行工具（槽函数）
        
        注意：此方法在主线程中执行，直接操作 Houdini API 是安全的。
        所有修改操作包裹在 undo group 中，支持一键撤销整个 Agent 操作。
        ★ 对于未自带 checkpoint 的修改工具，会在执行前后快照网络子节点以检测变更。
        
        ★ macOS 线程安全说明：
        Houdini 的 hou 模块不是线程安全的。macOS 上 Cocoa/AppKit 要求 UI 和
        场景操作必须在主线程执行，否则会导致 EXC_BAD_ACCESS。
        此方法通过 BlockingQueuedConnection 信号从后台线程触发，保证在主线程执行。
        
        ★ Cook 保护（v1.4.3）：
        对可能触发 cook 的修改工具，在执行前临时切换为手动更新模式，
        执行完毕后恢复原模式。这样 setDisplayFlag/connect 等操作不会
        立即触发耗时的场景 cook，避免阻塞主线程导致死锁。
        """
        # ★ 主线程断言（调试辅助：如果在非主线程执行，输出警告）
        _app = QtWidgets.QApplication.instance()
        if _app and _app.thread() != QtCore.QThread.currentThread():
            print(f"[⚠️ THREAD SAFETY] _on_execute_tool_main_thread 不在主线程执行! "
                  f"tool={tool_name}, current_thread={QtCore.QThread.currentThread()}")
        
        result = {"success": False, "error": tr('ai.unknown_err')}
        
        # 判断是否为修改操作（需要 undo group）
        _MUTATING_TOOLS = {
            "create_node", "create_nodes_batch", "create_wrangle_node",
            "delete_node", "set_node_parameter", "connect_nodes",
            "copy_node", "batch_set_parameters", "set_display_flag",
            "execute_python", "save_hip", "run_skill",
        }
        use_undo_group = tool_name in _MUTATING_TOOLS
        
        # ★ Cook 保护（v1.4.3）：对可能触发 cook 的工具，
        # 在 Agent 运行期间保持 Manual 模式，防止 cook 阻塞主线程
        # 模式恢复在 Agent 结束时统一处理（_restore_update_mode）
        if tool_name in self._COOK_TRIGGERING_TOOLS:
            try:
                import hou  # type: ignore
                if hou.updateModeSetting() != hou.updateMode.Manual:
                    hou.setUpdateMode(hou.updateMode.Manual)
            except Exception:
                pass
        
        # ★ 读取前 Cook（v1.4.4）：当 Agent 处于 Manual 保护模式下，
        # 读取工具执行前先对当前显示节点做一次针对性 cook，
        # 确保 AI 能看到修改后的最新结果（而非 stale 数据）
        if tool_name in self._COOK_BEFORE_READ_TOOLS:
            self._cook_displayed_nodes_if_manual()
        
        # ★ 对不自带 checkpoint 追踪的修改工具，做 before/after 快照
        should_snapshot = (
            tool_name in _MUTATING_TOOLS
            and tool_name not in self._SELF_TRACKING_TOOLS
            and tool_name != 'save_hip'  # save 无需快照
        )
        before_children = self._snapshot_network_children() if should_snapshot else {}
        
        try:
            # 对修改操作开启 undo group
            if use_undo_group:
                try:
                    import hou  # type: ignore
                    hou.undos.beginGroup(f"AI Agent: {tool_name}")
                except Exception:
                    use_undo_group = False  # hou 不可用则跳过
            
            if tool_name == "verify_and_summarize":
                check_items = kwargs.get("check_items", [])
                expected = kwargs.get("expected_result", "")
                
                # 确保 check_items 是列表类型（防止 unhashable type: 'slice' 错误）
                if not isinstance(check_items, list):
                    if isinstance(check_items, str):
                        check_items = [check_items]
                    elif hasattr(check_items, '__iter__') and not isinstance(check_items, (dict, str)):
                        check_items = list(check_items)
                    else:
                        check_items = []
                
                # 获取当前网络结构进行验证
                ok, structure_data = self.mcp.get_network_structure()
                
                # 自动检测问题
                issues = []
                if ok and isinstance(structure_data, dict):
                    nodes = structure_data.get('nodes', [])
                    connections = structure_data.get('connections', [])
                    
                    # 收集所有已连接的节点
                    connected_nodes = set()
                    for conn in connections:
                        from_path = conn.get('from', '')
                        to_path = conn.get('to', '')
                        if from_path:
                            connected_nodes.add(from_path.split('/')[-1])
                        if to_path:
                            connected_nodes.add(to_path.split('/')[-1])
                    
                    # 检测问题
                    for node in nodes:
                        node_name = node.get('name', '')
                        # 检测错误节点
                        if node.get('has_errors'):
                            issues.append(tr('ai.err_issues', node_name))
                        # 检测孤立节点（非输出节点且未连接）
                        if node_name not in connected_nodes:
                            node_type = node.get('type', '').lower()
                            # 排除输出节点和根节点
                            if not any(x in node_type for x in ['output', 'null', 'out', 'merge']):
                                if not any(x in node_name.lower() for x in ['out', 'output', 'result']):
                                    issues.append(f"orphan:{node_name}")
                    
                    # 检查是否有显示的输出节点
                    has_displayed = any(node.get('is_displayed') for node in nodes)
                    if not has_displayed and nodes:
                        issues.append(tr('ai.no_display'))
                
                # 生成验证结果
                if issues:
                    issues_str = ' | '.join(issues[:5])  # 最多显示5个问题
                    result = {
                        "success": True,
                        "result": tr('ai.check_fail', issues_str)
                    }
                else:
                    check_items_str = ', '.join(str(item) for item in check_items[:3]) if check_items else tr('ai.check_none')
                    result = {
                        "success": True,
                        "result": tr('ai.check_pass', expected[:30] if expected else 'done')
                    }
            else:
                # 其他工具交给 MCP 处理
                result = self.mcp.execute_tool(tool_name, kwargs)
        except Exception as e:
            result = {"success": False, "error": tr('ai.tool_exec_err', str(e))}
        finally:
            # ★ 执行后快照 & diff，检测节点变更
            if should_snapshot and result.get("success"):
                try:
                    after_children = self._snapshot_network_children()
                    changes = self._diff_network_children(before_children, after_children)
                    if changes:
                        result['_node_changes'] = changes
                except Exception:
                    pass  # 快照失败不影响工具结果

            # 关闭 undo group
            if use_undo_group:
                try:
                    import hou  # type: ignore
                    hou.undos.endGroup()
                except Exception:
                    pass

            # ★ Cook 保护恢复：不在单个工具 finally 中恢复更新模式
            # 而是在 Agent 结束时统一恢复（_restore_update_mode），
            # 避免中间工具恢复后触发耗时 cook 阻塞主线程

            # ★ 清除主线程忙标记
            # 无论工具执行成功或失败，主线程已经空闲
            self._main_thread_busy = False

            # ★ macOS 崩溃修复：不再在此处调用 processEvents()
            # ─────────────────────────────────────────────────────
            # 旧代码：QtWidgets.QApplication.processEvents()
            #
            # 为什么移除？
            # 1. 此槽函数通过 BlockingQueuedConnection 从后台线程触发，
            #    在 emit 返回前主线程事件循环不会处理新事件——这是设计意图。
            # 2. processEvents() 会在槽函数内部递归处理事件队列，可能导致：
            #    a) 递归触发另一个 _executeToolRequest 信号（死锁或重入）
            #    b) 触发 Houdini 场景事件、渲染回调等（与当前 hou 操作竞争）
            #    c) macOS Cocoa runloop 重入，导致 EXC_BAD_ACCESS 崩溃
            # 3. BlockingQueuedConnection 返回后，主线程事件循环自然会继续
            #    处理排队的事件——无需手动 processEvents。
            # ─────────────────────────────────────────────────────

            # 将结果放入队列（线程安全）
            self._tool_result_queue.put(result)

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
    def _fix_message_alternation(messages: list) -> list:
        """修复消息交替问题：合并连续的相同角色消息
        
        Cursor 风格消息格式支持：
        - user → assistant(tool_calls) → tool → assistant → user（正常格式）
        - 只合并连续的 user 或连续的 assistant（无 tool_calls 的）
        - 不合并带 tool_calls 的 assistant 消息（它们需要对应的 tool 结果）
        - tool 消息不参与合并
        """
        if not messages:
            return messages
        
        fixed = [messages[0]]
        for msg in messages[1:]:
            role = msg.get('role', '')
            prev_role = fixed[-1].get('role', '')
            
            # tool 消息永不合并（它们通过 tool_call_id 关联到 assistant）
            if role == 'tool' or prev_role == 'tool':
                fixed.append(msg)
                continue
            
            # 带 tool_calls 的 assistant 消息不合并（API 格式要求独立）
            if role == 'assistant' and msg.get('tool_calls'):
                fixed.append(msg)
                continue
            if prev_role == 'assistant' and fixed[-1].get('tool_calls'):
                fixed.append(msg)
                continue
            
            if role == prev_role and role in ('user', 'assistant'):
                # 合并连续的相同角色消息
                prev_content = fixed[-1].get('content')
                curr_content = msg.get('content')
                
                # ★ 多模态消息（content 是 list）不能直接用 + 拼接字符串
                # 策略：如果任一 content 是 list，提取文字部分再合并
                prev_text = prev_content
                curr_text = curr_content
                if isinstance(prev_content, list):
                    prev_text = '\n'.join(
                        p.get('text', '') for p in prev_content
                        if isinstance(p, dict) and p.get('type') == 'text'
                    ) or ''
                if isinstance(curr_content, list):
                    curr_text = '\n'.join(
                        p.get('text', '') for p in curr_content
                        if isinstance(p, dict) and p.get('type') == 'text'
                    ) or ''
                
                prev_text = prev_text or ''
                curr_text = curr_text or ''
                
                fixed[-1] = fixed[-1].copy()
                
                # 如果两边都是纯文本，直接拼接
                # 如果任一方是多模态 list，保留最后一个的图片部分 + 合并文字
                if isinstance(prev_content, list) or isinstance(curr_content, list):
                    # 合并为多模态格式：保留所有 text 和 image_url
                    merged_parts = []
                    combined_text = (prev_text + '\n\n' + curr_text).strip()
                    if combined_text:
                        merged_parts.append({'type': 'text', 'text': combined_text})
                    # 收集所有图片部分
                    for src in (prev_content, curr_content):
                        if isinstance(src, list):
                            for part in src:
                                if isinstance(part, dict) and part.get('type') == 'image_url':
                                    merged_parts.append(part)
                    fixed[-1]['content'] = merged_parts if merged_parts else combined_text
                else:
                    fixed[-1]['content'] = prev_text + '\n\n' + curr_text
                
                if 'thinking' in msg and msg['thinking']:
                    prev_thinking = fixed[-1].get('thinking', '')
                    fixed[-1]['thinking'] = (prev_thinking + '\n' + msg['thinking']).strip()
            else:
                fixed.append(msg)
        
        return fixed

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

    def _manage_context(self):
        """管理上下文长度 — Cursor 风格轮次裁剪
        
        核心原则（与 _progressive_trim 一致）：
        - **永不截断 user / assistant 消息**
        - 只压缩 tool 结果（role='tool' 的 content）
        - 按「轮次」（以 user 消息为分界）裁剪，保护最近 N 轮
        - 如果仅压缩 tool 仍不够，整轮删除最早的轮次
        - 保持 assistant(tool_calls) ↔ tool 的原生链不被打破
        """
        # ★ 使用 agent 锚定的 history（避免压缩错误 session）
        history = self._agent_history if self._agent_history is not None else self._conversation_history

        # ★ 工作上下文：_send_context 优先（已裁剪的副本），否则用 history
        # _conversation_history 作为永久存档，压缩只改写 _send_context，显示不受影响。
        work = self._send_context if self._send_context is not None else history

        if len(work) < 6:
            return  # 太少，不需管理

        current_tokens = self.token_optimizer.calculate_message_tokens(work)
        context_limit = self._get_current_context_limit()

        # 更新预算
        self.token_optimizer.budget.max_tokens = context_limit
        should_compress, reason = self.token_optimizer.should_compress(current_tokens, context_limit)

        if not (should_compress and self._auto_optimize):
            if reason and ('警告' in reason or 'warning' in reason.lower()):
                self._addStatus.emit(f"Note: {reason}")
            return

        # ★ 深度睡眠：_manage_context 压缩前整理全部上下文为长期记忆
        if self._is_memory_active() and self._reflection_module and not self._sleep_in_progress:
            _params = getattr(self, '_last_agent_params', {})
            if _params:
                self._addStatus.emit("😴 深度睡眠：正在整理全部上下文为长期记忆...")
                try:
                    self._sleep_in_progress = True
                    deep_result = self._reflection_module.deep_sleep(
                        session_id=self._session_id,
                        all_messages=list(work),
                        ai_client=self.client,
                        model=_params.get('model', 'deepseek-v4-flash'),
                        provider=_params.get('provider', 'deepseek'),
                    )
                    if deep_result.get("success"):
                        n_rules = len(deep_result.get("new_rules", []))
                        n_strats = len(deep_result.get("new_strategies", []))
                        self._addStatus.emit(
                            f"😴 深度睡眠完成: {n_rules} 条经验 + {n_strats} 条策略已写入长期记忆"
                        )
                except Exception as e:
                    print(f"[Sleep] _manage_context 深度睡眠异常: {e}")
                finally:
                    self._sleep_in_progress = False

        old_tokens = current_tokens

        # --- 按 user 消息划分轮次（在工作副本上操作）---
        # 先做一份独立副本，以便压缩 tool content 时不污染 _conversation_history
        work_copy = [m.copy() for m in work]
        rounds = []
        current_round = []
        for m in work_copy:
            if m.get('role') == 'user' and current_round:
                rounds.append(current_round)
                current_round = []
            current_round.append(m)
        if current_round:
            rounds.append(current_round)

        if len(rounds) <= 2:
            return  # 只有 1-2 轮，不裁剪

        # --- 第一遍：压缩旧轮次的 tool 结果（保留最近 60%）---
        n_rounds = len(rounds)
        protect_n = max(2, int(n_rounds * 0.6))
        for r_idx in range(n_rounds - protect_n):
            for m in rounds[r_idx]:
                if m.get('role') == 'tool':
                    c = m.get('content') or ''
                    if len(c) > 200:
                        m['content'] = self.client._summarize_tool_content(c, 200) if hasattr(self.client, '_summarize_tool_content') else c[:200] + '...[summary]'

        # 重新计算
        compressed = [m for rnd in rounds for m in rnd]
        new_tokens = self.token_optimizer.calculate_message_tokens(compressed)

        if new_tokens < context_limit * self.token_optimizer.budget.compression_threshold:
            # 压缩 tool 就够了 — 写入 _send_context，不动 _conversation_history
            self._send_context = compressed
            saved = old_tokens - new_tokens
            if saved > 0:
                self._addStatus.emit(tr('opt.auto_status', saved))
            return

        # --- 第二遍：删除最早的完整轮次，直到低于阈值 ---
        target = int(context_limit * 0.65)  # 目标降到 65%
        while len(rounds) > 2:
            rounds.pop(0)
            compressed = [m for rnd in rounds for m in rnd]
            new_tokens = self.token_optimizer.calculate_message_tokens(compressed)
            if new_tokens <= target:
                break

        # 在头部插入摘要提示 — 写入 _send_context，不动 _conversation_history
        summary_note = {
            'role': 'system',
            'content': tr('ai.old_rounds', n_rounds - len(rounds))
        }
        self._send_context = [summary_note] + [m for rnd in rounds for m in rnd]

        saved = old_tokens - self.token_optimizer.calculate_message_tokens(self._send_context)
        if saved > 0:
            self._addStatus.emit(tr('opt.auto_status', saved))
    
    def _compress_context(self):
        """压缩上下文 — 智能摘要，保留关键信息

        改进策略:
        1. 按轮次（user→assistant 对）提取信息，而非简单截取
        2. 提取用户意图、工具操作、关键结果、节点路径
        3. 识别错误和纠正行为
        4. 生成结构化摘要
        """
        if len(self._conversation_history) <= 4:
            return  # 太短不需要压缩

        # 将旧对话压缩成摘要
        old_messages = self._conversation_history[:-4]  # 保留最近 4 条
        recent_messages = self._conversation_history[-4:]

        # 按轮次分组
        rounds_info = []
        current_round = {"user": "", "assistant": "", "tools": [], "errors": []}

        for msg in old_messages:
            role = msg.get('role', '')
            content = msg.get('content', '')

            if isinstance(content, list):
                # 多模态内容 → 提取文字
                content = ' '.join(
                    p.get('text', '') for p in content if isinstance(p, dict) and p.get('type') == 'text'
                )

            if role == 'user':
                if current_round["user"]:
                    rounds_info.append(current_round)
                    current_round = {"user": "", "assistant": "", "tools": [], "errors": []}
                current_round["user"] = content[:120].replace('\n', ' ').strip()
            elif role == 'assistant' and content:
                # 去除 think 标签
                clean = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
                if clean:
                    # 提取关键句（最后两行通常是结论）
                    lines = [l.strip() for l in clean.split('\n') if l.strip()]
                    summary_lines = lines[-2:] if len(lines) > 2 else lines
                    current_round["assistant"] = ' '.join(summary_lines)[:100]
                # 提取工具调用
                tool_calls = msg.get('tool_calls', [])
                if tool_calls:
                    for tc in tool_calls:
                        fn = tc.get('function', {})
                        current_round["tools"].append(fn.get('name', ''))
            elif role == 'tool':
                tool_content = content or ''
                if 'error' in tool_content.lower() or 'fail' in tool_content.lower():
                    current_round["errors"].append(tool_content[:60])

        if current_round["user"]:
            rounds_info.append(current_round)

        # 生成结构化摘要
        summary_parts = []
        for i, rnd in enumerate(rounds_info[-5:], 1):  # 最多保留最近 5 轮
            parts = []
            if rnd["user"]:
                parts.append(f"Q: {rnd['user'][:60]}")
            if rnd["assistant"]:
                parts.append(f"A: {rnd['assistant'][:60]}")
            if rnd["tools"]:
                unique_tools = list(dict.fromkeys(rnd["tools"]))[:3]
                parts.append(f"Tools: {','.join(unique_tools)}")
            if rnd["errors"]:
                parts.append(f"⚠ {rnd['errors'][0][:40]}")
            if parts:
                summary_parts.append(f"R{i}: " + " | ".join(parts))

        # 提取提到的节点路径
        all_text = ' '.join(msg.get('content', '') for msg in old_messages if isinstance(msg.get('content'), str))
        node_paths = list(set(re.findall(r'/obj/[a-zA-Z0-9_/]+', all_text)))
        if node_paths:
            summary_parts.append(f"Nodes: {', '.join(node_paths[:5])}")

        # 生成上下文摘要
        if summary_parts:
            self._context_summary = "\n".join(summary_parts)
        else:
            self._context_summary = ""

        # 更新历史（只保留最近的）
        self._conversation_history = recent_messages

        print(f"[Context] 压缩上下文: 保留 {len(recent_messages)} 条消息, "
              f"摘要 {len(self._context_summary)} 字符 ({len(rounds_info)} 轮提取)")
    
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

    def _run_agent(self, agent_params: dict):
        """后台运行 Agent
        
        Args:
            agent_params: 从主线程获取的参数（避免在后台线程访问 Qt 控件）
                - provider: AI 提供商
                - model: 模型名称
                - use_web: 是否启用网页搜索
                - use_agent: 是否启用 Agent 模式
                - use_think: 是否启用思考模式
                - context_limit: 上下文限制
        """
        # ⚠️ 从参数获取值，不直接访问 Qt 控件（线程安全）
        provider = agent_params['provider']
        model = agent_params['model']
        use_web = agent_params['use_web']
        use_agent = agent_params['use_agent']
        use_think = agent_params.get('use_think', True)
        context_limit = agent_params['context_limit']
        scene_context = agent_params.get('scene_context', {})
        supports_vision = agent_params.get('supports_vision', True)
        plan_mode = agent_params.get('plan_mode', False)
        plan_executing = agent_params.get('plan_executing', False)
        
        # ★ 保存 agent_params 供反思钩子使用
        self._last_agent_params = agent_params
        
        # ★ 存储 Think 开关状态，供 _drain_tag_buffer / _on_thinking_chunk 使用
        self._think_enabled = use_think
        
        try:
            # ========================================
            # 🔥 Cache 优化：保持消息前缀稳定
            # ========================================
            # 消息结构：[系统提示] + [历史消息] + [上下文提醒+当前请求]
            # 前缀（系统提示+历史消息）保持稳定，提升 cache 命中率
            
            # 1. 系统提示词（根据思考模式选择版本）
            sys_prompt = self._cached_prompt_think if use_think else self._cached_prompt_no_think
            
            # ★ Ask 模式：追加只读约束
            if not use_agent and not plan_mode:
                sys_prompt = sys_prompt + tr('ai.ask_mode_prompt')
            
            # ★ Plan 模式：追加规划或执行阶段提示词
            if plan_mode:
                if plan_executing:
                    sys_prompt = sys_prompt + tr('ai.plan_mode_execution_prompt')
                else:
                    self._plan_phase = 'planning'
                    sys_prompt = sys_prompt + tr('ai.plan_mode_planning_prompt')
            
            # ★ Agent 模式：追加复杂任务建议切换 Plan 的提示
            if use_agent and not plan_mode:
                sys_prompt = sys_prompt + tr('ai.agent_suggest_plan_prompt')
            
            # ★ 个性注入：将成长系统形成的个性特征追加到 system prompt 末尾
            personality_text = self._get_personality_injection()
            if personality_text:
                sys_prompt = sys_prompt + "\n\n" + personality_text
            
            # ★ L0 核心记忆加载：全部加载到 sys_prompt（上限 5 条，按 confidence TopK）
            if self._is_memory_active():
                try:
                    core_mems = self._memory_store.get_core_memories(max_count=5)
                    if core_mems:
                        core_lines = [f"- {m.rule}" for m in core_mems]
                        sys_prompt = sys_prompt + (
                            "\n\n[Core Memory — 以下为核心记忆，仅供参考，请结合当前上下文判断]\n"
                            + "\n".join(core_lines)
                        )
                except Exception as e:
                    print(f"[Memory] L0 核心记忆加载失败: {e}")
            
            # ★ 用户自定义规则注入（类似 Cursor Rules）
            rules_text = self._get_user_rules_injection()
            if rules_text:
                sys_prompt = sys_prompt + "\n\n" + rules_text
            
            messages = [{'role': 'system', 'content': sys_prompt}]
            
            # ================================================================
            # 2. Cursor 风格历史消息：原生格式直通，不预压缩
            # ================================================================
            # 核心原则：
            # - assistant 消息完整保留（包括 content 和 tool_calls）
            # - tool 消息完整保留（包括 tool_call_id 和 content）
            # - user 消息完整保留
            # - 只清理内部元数据字段（thinking, python_shells 等）
            # - 压缩只在超限时由 _progressive_trim / auto_optimize 处理
            
            # 内部元数据字段列表（不发给 API）
            _INTERNAL_FIELDS = frozenset({
                '_reply_content', '_tool_summary', 'thinking',
                'python_shells', 'system_shells',
            })
            
            # ★ Cursor 风格：只保留当前轮次（最后一条 user 消息）的图片
            # 旧轮次的 image_url 剥离为纯文本，避免 base64 膨胀上下文
            # 使用 _send_context（已裁剪的工作副本），None 时降级到完整存档
            _ctx_src = self._send_context if self._send_context is not None else self._conversation_history
            _last_user_idx = None
            for _i in range(len(_ctx_src) - 1, -1, -1):
                if _ctx_src[_i].get('role') == 'user':
                    _last_user_idx = _i
                    break

            history_to_send = []
            for msg_idx, msg in enumerate(_ctx_src):
                role = msg.get('role', '')
                
                if role == 'tool':
                    # ★ 新格式（Cursor 风格）：保留原生 tool 消息 ★
                    # 必须有 tool_call_id 才能发给 API
                    if msg.get('tool_call_id'):
                        clean = {k: v for k, v in msg.items() if k not in _INTERNAL_FIELDS}
                        history_to_send.append(clean)
                    else:
                        # 旧格式 tool 消息（无 tool_call_id）→ 转为 assistant 文本
                        tool_name = msg.get('name', 'unknown')
                        content = msg.get('content', '')
                        history_to_send.append({
                            'role': 'assistant',
                            'content': tr('ai.tool_result', tool_name, content[:500])
                        })
                
                elif role == 'assistant':
                    # ★ 完整保留 assistant 消息 ★
                    clean = {}
                    for k, v in msg.items():
                        if k in _INTERNAL_FIELDS:
                            continue
                        clean[k] = v
                    # 如果是旧格式的 [工具执行结果] 文本，也原样保留
                    # content 完整传递，不做任何截断
                    # 同时保留 tool_calls（如果有的话 — 新格式）
                    history_to_send.append(clean)
                
                elif role == 'user':
                    # ★ Cursor 风格图片处理：
                    # - 当前轮次（最后一条 user）+ 视觉模型 → 保留图片
                    # - 旧轮次 或 非视觉模型 → 剥离 image_url，只保留文字
                    content = msg.get('content')
                    is_current_round = (msg_idx == _last_user_idx)
                    
                    if isinstance(content, list):
                        if is_current_round and supports_vision:
                            # 当前轮 + 视觉模型：完整保留图片
                            history_to_send.append(msg)
                        else:
                            # 旧轮次 或 非视觉模型：剥离图片，只留文字
                            text_parts = []
                            for part in content:
                                if isinstance(part, dict) and part.get('type') == 'text':
                                    text_parts.append(part.get('text', ''))
                            text_only = '\n'.join(t for t in text_parts if t)
                            history_to_send.append({
                                'role': 'user',
                                'content': text_only or tr('ai.image_msg')
                            })
                    else:
                        # 纯文本消息：原样保留
                        history_to_send.append(msg)
                
                elif role == 'system':
                    # 系统消息（如历史摘要）保留
                    history_to_send.append(msg)
            
            # 修复 user/assistant 交替（仅处理连续的相同角色，不影响 tool 消息）
            history_to_send = self._fix_message_alternation(history_to_send)
            
            messages.extend(history_to_send)
            
            # 3. 自动 RAG 注入（从用户最新消息中提取关键词，检索相关文档）
            user_last_msg = ""
            if self._conversation_history:
                for msg in reversed(self._conversation_history):
                    if msg.get('role') == 'user':
                        raw_content = msg.get('content', '')
                        # 多模态内容（list）中提取文字部分
                        if isinstance(raw_content, list):
                            user_last_msg = ' '.join(
                                p.get('text', '') for p in raw_content if p.get('type') == 'text'
                            )
                        else:
                            user_last_msg = raw_content
                        break
            if user_last_msg:
                rag_context = self._auto_rag_retrieve(
                    user_last_msg,
                    scene_context=scene_context,
                    conversation_len=len(self._conversation_history),
                )
                if rag_context:
                    messages.append({'role': 'system', 'content': rag_context})
            
            # 4. ★ 长期记忆激活（"我想起来了"机制）
            # 在 RAG 文档之后、上下文提醒之前注入
            if user_last_msg:
                memory_context = self._activate_long_term_memory(
                    user_last_msg, scene_context=scene_context
                )
                if memory_context:
                    messages.append({'role': 'system', 'content': memory_context})
            
            # 5. ★ Plan 上下文注入（仅在 Plan 执行阶段 + 当前 session 匹配时）
            if plan_mode and plan_executing:
                try:
                    if self._plan_manager is None:
                        self._plan_manager = get_plan_manager()
                    plan_ctx = self._plan_manager.get_plan_for_context(self._session_id)
                    if plan_ctx:
                        messages.append({'role': 'system', 'content': plan_ctx})
                except Exception as e:
                    print(f"[Plan] Context injection error: {e}")
            
            # 6. 上下文提醒（放在最后，不破坏 cache 前缀）
            # ⚠️ Cache 优化：动态内容放在末尾，保持前缀稳定
            context_reminder = self._get_context_reminder()
            if context_reminder:
                # 将上下文提醒作为系统消息添加到末尾
                messages.append({'role': 'system', 'content': f"[Context] {context_reminder}"})
            
            # ================================================================
            # ★ 睡眠机制：浅睡眠（每 N 轮用户提问触发）
            # ================================================================
            if self._is_memory_active() and self._reflection_module:
                self._sleep_msg_counter += 1
                from ..utils.reflection import LIGHT_SLEEP_INTERVAL
                if self._sleep_msg_counter % LIGHT_SLEEP_INTERVAL == 0 and not self._sleep_in_progress:
                    # 收集最近 N 轮的消息用于浅睡眠总结
                    _sleep_messages = self._collect_recent_rounds(
                        self._conversation_history, LIGHT_SLEEP_INTERVAL
                    )
                    if _sleep_messages:
                        _sleep_sid = self._session_id
                        _sleep_model = model
                        _sleep_provider = provider
                        _sleep_client = self.client
                        _sleep_reflection = self._reflection_module
                        def _do_light_sleep():
                            self._sleep_in_progress = True
                            try:
                                result = _sleep_reflection.light_sleep(
                                    session_id=_sleep_sid,
                                    recent_messages=_sleep_messages,
                                    ai_client=_sleep_client,
                                    model=_sleep_model,
                                    provider=_sleep_provider,
                                )
                                if result.get("success"):
                                    self._addStatus.emit("💤 浅睡眠完成，经验已写入长期记忆")
                            finally:
                                self._sleep_in_progress = False
                        sleep_thread = threading.Thread(target=_do_light_sleep, daemon=True)
                        sleep_thread.start()
            
            # Cursor 风格预发送压缩：只压缩 tool 结果，保留 user/assistant 完整
            if self._auto_optimize:
                current_tokens = self.token_optimizer.calculate_message_tokens(messages)
                should_compress, _ = self.token_optimizer.should_compress(current_tokens, context_limit)
                
                if should_compress:
                    # ★ 深度睡眠：压缩前将完整上下文写入长期记忆
                    if self._is_memory_active() and self._reflection_module and not self._sleep_in_progress:
                        self._addStatus.emit("😴 深度睡眠：正在整理全部上下文为长期记忆...")
                        try:
                            self._sleep_in_progress = True
                            deep_result = self._reflection_module.deep_sleep(
                                session_id=self._session_id,
                                all_messages=self._conversation_history,
                                ai_client=self.client,
                                model=model,
                                provider=provider,
                            )
                            if deep_result.get("success"):
                                n_rules = len(deep_result.get("new_rules", []))
                                n_strats = len(deep_result.get("new_strategies", []))
                                self._addStatus.emit(
                                    f"😴 深度睡眠完成: {n_rules} 条经验 + {n_strats} 条策略已写入长期记忆"
                                )
                        except Exception as e:
                            print(f"[Sleep] 深度睡眠异常: {e}")
                        finally:
                            self._sleep_in_progress = False
                    
                    old_tokens = current_tokens
                    # 分离系统提示和上下文提醒
                    first_system = messages[0] if messages and messages[0].get('role') == 'system' else None
                    last_context = messages[-1] if messages and ('[上下文]' in messages[-1].get('content', '') or '[Context]' in messages[-1].get('content', '')) else None
                    start_idx = 1 if first_system else 0
                    end_idx = -1 if last_context else len(messages)
                    body = messages[start_idx:end_idx] if end_idx != len(messages) else messages[start_idx:]
                    
                    # 按 user 消息划分轮次
                    rounds = []
                    cur_rnd = []
                    for m in body:
                        if m.get('role') == 'user' and cur_rnd:
                            rounds.append(cur_rnd)
                            cur_rnd = []
                        cur_rnd.append(m)
                    if cur_rnd:
                        rounds.append(cur_rnd)
                    
                    # 第一遍：压缩旧轮次 tool 结果
                    n_rounds = len(rounds)
                    protect_n = max(2, int(n_rounds * 0.6))
                    for r_idx in range(n_rounds - protect_n):
                        for m in rounds[r_idx]:
                            if m.get('role') == 'tool':
                                c = m.get('content') or ''
                                if len(c) > 200:
                                    m['content'] = self.client._summarize_tool_content(c, 200) if hasattr(self.client, '_summarize_tool_content') else c[:200] + '...[summary]'
                    
                    compressed_body = [m for rnd in rounds for m in rnd]
                    
                    # 如果仍超限，删除最早轮次
                    target = int(context_limit * 0.7)
                    while len(rounds) > 2:
                        test_body = [m for rnd in rounds for m in rnd]
                        test_msgs = ([first_system] if first_system else []) + test_body + ([last_context] if last_context else [])
                        if self.token_optimizer.calculate_message_tokens(test_msgs) <= target:
                            break
                        rounds.pop(0)
                    
                    compressed_body = [m for rnd in rounds for m in rnd]
                    
                    # 重组
                    messages = []
                    if first_system:
                        messages.append(first_system)
                    if n_rounds - len(rounds) > 0:
                        messages.append({
                            'role': 'system',
                            'content': tr('ai.old_rounds', n_rounds - len(rounds))
                        })
                    messages.extend(compressed_body)
                    if last_context:
                        messages.append(last_context)
                    
                    new_tokens = self.token_optimizer.calculate_message_tokens(messages)
                    saved = old_tokens - new_tokens
                    if saved > 0:
                        self._addStatus.emit(tr('opt.auto_status', saved))
            
            # ⚠️ 使用从主线程传入的参数（不直接访问 Qt 控件）
            # provider, model, use_web, use_agent 已在方法开头从 agent_params 获取
            
            # 调试：显示正在请求
            self._addStatus.emit(f"Requesting {provider}/{model}...")
            
            # 推理模型兼容：清理消息格式
            is_reasoning_model = AIClient.is_reasoning_model(model)
            cleaned_messages = []
            for msg in messages:
                role = msg.get('role', 'user')
                content = msg.get('content')
                has_tool_calls = 'tool_calls' in msg
                
                clean_msg = {'role': role}
                
                # ★ Cursor 风格：assistant 有 tool_calls 时 content 可为 None ★
                # Claude/Anthropic 代理拒绝 content="" + tool_calls 共存
                if role == 'assistant' and has_tool_calls:
                    clean_msg['content'] = content  # 保留 None（不转为空字符串）
                else:
                    clean_msg['content'] = content if content is not None else ''
                
                # 推理模型：assistant 消息需要 reasoning_content 字段
                if is_reasoning_model and role == 'assistant':
                    clean_msg['reasoning_content'] = msg.get('reasoning_content', '')
                # 保留 tool_calls 字段
                if has_tool_calls:
                    clean_msg['tool_calls'] = msg['tool_calls']
                # 保留 tool_call_id 字段
                if 'tool_call_id' in msg:
                    clean_msg['tool_call_id'] = msg['tool_call_id']
                # 保留 name 字段（用于 tool 消息）
                if 'name' in msg:
                    clean_msg['name'] = msg['name']
                
                # ★ 清理 assistant content 中的 <think> 标签 ★
                # 历史中的 thinking 不需要发给 API（浪费 token）
                if role == 'assistant' and clean_msg.get('content'):
                    c = clean_msg['content']
                    if '<think>' in c:
                        c = re.sub(r'<think>[\s\S]*?</think>', '', c).strip()
                        clean_msg['content'] = c or None
                
                cleaned_messages.append(clean_msg)
            messages = cleaned_messages
            
            # 使用缓存的优化后工具定义（只计算一次）
            if plan_mode and not plan_executing:
                # ★ Plan 规划阶段：只读工具 + create_plan + ask_question
                plan_filtered = [t for t in HOUDINI_TOOLS
                                 if t['function']['name'] in self._PLAN_PLANNING_TOOLS]
                plan_filtered.append(PLAN_TOOL_CREATE)
                plan_filtered.append(PLAN_TOOL_ASK_QUESTION)
                if not use_web:
                    plan_filtered = [t for t in plan_filtered
                                     if t['function']['name'] not in ('web_search', 'fetch_webpage')]
                tools = UltraOptimizer.optimize_tool_definitions(plan_filtered)
            elif plan_mode and plan_executing:
                # ★ Plan 执行阶段：完整工具 + update_plan_step
                exec_tools = list(HOUDINI_TOOLS) + [PLAN_TOOL_UPDATE_STEP]
                if not use_web:
                    exec_tools = [t for t in exec_tools
                                  if t['function']['name'] not in ('web_search', 'fetch_webpage')]
                tools = UltraOptimizer.optimize_tool_definitions(exec_tools)
            elif not use_agent:
                # ★ Ask 模式：只保留只读/查询工具
                ask_filtered = [t for t in HOUDINI_TOOLS
                                if t['function']['name'] in self._ASK_MODE_TOOLS]
                if not use_web:
                    ask_filtered = [t for t in ask_filtered
                                    if t['function']['name'] not in ('web_search', 'fetch_webpage')]
                tools = UltraOptimizer.optimize_tool_definitions(ask_filtered)
            else:
                # ★ Agent 模式：使用全量工具
                # 注意：不做意图过滤。Agent 需要多轮迭代，可能先查询再创建再验证，
                # 意图过滤会导致后续迭代缺少必要工具（如 capture_viewport、create_node 等）。
                if use_web:
                    if self._cached_optimized_tools is None:
                        self._cached_optimized_tools = UltraOptimizer.optimize_tool_definitions(HOUDINI_TOOLS)
                    tools = self._cached_optimized_tools
                else:
                    if self._cached_optimized_tools_no_web is None:
                        filtered = [t for t in HOUDINI_TOOLS if t['function']['name'] not in ('web_search', 'fetch_webpage')]
                        self._cached_optimized_tools_no_web = UltraOptimizer.optimize_tool_definitions(filtered)
                    tools = self._cached_optimized_tools_no_web
            
            # ★ 合并外部工具（HookManager 插件工具 + ToolRegistry Skill 工具）
            try:
                from ..utils.hooks import get_hook_manager as _ghm_tools
                _ext = _ghm_tools().get_external_tools()
                if _ext:
                    tools = list(tools) + _ext
            except Exception:
                pass
            try:
                from ..utils.tool_registry import get_tool_registry
                _reg = get_tool_registry()
                # 获取 ToolRegistry 中 source=skill 的工具（避免与上面重复）
                _existing_names = {t.get('function', {}).get('name', '') for t in tools}
                for meta in _reg._tools.values():
                    if meta.source == "skill" and meta.enabled and meta.name not in _existing_names:
                        tools = list(tools) if not isinstance(tools, list) else tools
                        tools.append(meta.schema)
            except Exception:
                pass

            # ★ 记忆开关关闭时，从 tool schema 中剔除 search_memory，
            #   避免 LLM 在关闭长期记忆的情况下仍调用它读到污染性经验。
            if not self._is_memory_active():
                tools = [t for t in tools
                         if t.get('function', {}).get('name') != 'search_memory']

            # ★ 非视觉模型：capture_viewport 降级为仅保存文件（不注入图片）
            # 不再移除工具——AI 仍可截图保存让用户自行查看
            if not supports_vision:
                _degraded_tools = []
                for _t in tools:
                    if _t.get('function', {}).get('name') == 'capture_viewport':
                        import copy
                        _t_copy = copy.deepcopy(_t)
                        _t_copy['function']['description'] = (
                            "截取当前 Houdini 3D 视口快照并保存到文件。"
                            "当前模型不支持图片分析，截图将保存到 output_path 指定的路径供用户查看。"
                            "必须指定 output_path 参数。"
                        )
                        _degraded_tools.append(_t_copy)
                    else:
                        _degraded_tools.append(_t)
                tools = _degraded_tools
            
            # ★ Plan 模式的静默工具集合（不在 UI 中显示的工具）
            _silent = self._SILENT_TOOLS | self._PLAN_SILENT_TOOLS if plan_mode else self._SILENT_TOOLS
            
            # ★ 通用回调：每轮 API 迭代开始时显示 "Generating..." 状态
            # 第1轮也显示，填补 Send → 首字之间的空白
            _on_iter = lambda i: self._showGenerating.emit()
            
            if plan_mode:
                # ★ Plan 模式：使用 agent loop（规划或执行阶段均走此分支）
                _max_iter = 999 if plan_executing else 20
                
                # ★ Plan 续接回调：检测 AI 提前终止但 Plan 未完成的情况
                _plan_resume_callback = None
                _plan_resume_count = 0       # 防止无限续接
                _MAX_PLAN_RESUMES = 5        # 最多续接 5 次
                if plan_executing:
                    def _check_plan_incomplete():
                        nonlocal _plan_resume_count
                        if _plan_resume_count >= _MAX_PLAN_RESUMES:
                            print(f"[AI Client] Plan 续接次数已达上限 ({_MAX_PLAN_RESUMES})，停止续接")
                            return None
                        try:
                            if self._plan_manager is None:
                                from ..utils.plan_manager import get_plan_manager
                                self._plan_manager = get_plan_manager()
                            plan = self._plan_manager.load_plan(self._session_id)
                            if not plan:
                                return None
                            steps = plan.get('steps', [])
                            if not steps:
                                return None
                            done_count = sum(1 for s in steps if s.get('status') == 'done')
                            total = len(steps)
                            if done_count >= total:
                                return None  # 全部完成，正常结束
                            
                            # 找到未完成的步骤
                            pending_steps = [s for s in steps if s.get('status') in ('pending', 'running')]
                            if not pending_steps:
                                return None
                            
                            _plan_resume_count += 1
                            # 构造提醒消息
                            pending_names = ', '.join(
                                f'"{s.get("title", s.get("description", s["id"]))}"'
                                for s in pending_steps[:5]
                            )
                            # 获取最新的 Plan 上下文
                            plan_ctx = self._plan_manager.get_plan_for_context(self._session_id)
                            resume_msg = (
                                f"[Plan Incomplete] 计划尚未完成！已完成 {done_count}/{total} 步。\n"
                                f"未完成步骤: {pending_names}\n"
                                f"请立即继续执行下一个未完成的步骤。不要停止，不要总结，继续调用工具执行。\n"
                            )
                            if plan_ctx:
                                resume_msg += f"\n{plan_ctx}"
                            return resume_msg
                        except Exception as e:
                            print(f"[Plan] Incomplete check error: {e}")
                            return None
                    _plan_resume_callback = _check_plan_incomplete
                
                result = self.client.agent_loop_auto(
                    messages=messages,
                    model=model,
                    provider=provider,
                    max_iterations=_max_iter,
                    max_tokens=None,
                    enable_thinking=use_think,
                    supports_vision=supports_vision,
                    tools_override=tools,
                    context_limit=context_limit,
                    on_content=lambda c: self._on_content_with_limit(c),
                    on_thinking=lambda t: self._on_thinking_chunk(t),
                    on_tool_call=lambda n, a: (
                        None  # create_plan 已在 on_tool_args_delta 中处理
                        if n == 'create_plan' else
                        (self._addStatus.emit(f"[tool]{n}"), self._showToolStatus.emit(n))
                        if n not in _silent else None
                    ),
                    on_tool_result=lambda n, a, r: (
                        (self._add_tool_result(n, r, a), self._hideToolStatus.emit())
                        if n not in _silent else None
                    ),
                    on_tool_args_delta=lambda name, delta, acc: (
                        self._toolArgsDelta.emit(name, delta, acc)
                    ),
                    on_iteration_start=_on_iter,
                    on_plan_incomplete=_plan_resume_callback,
                )
            elif use_agent:
                # ★ Agent 模式：完整 agent loop，可创建/修改/删除节点
                result = self.client.agent_loop_auto(
                    messages=messages,
                    model=model,
                    provider=provider,
                    max_iterations=999,  # 不限制迭代次数
                    max_tokens=None,  # 不限制输出长度
                    enable_thinking=use_think,
                    supports_vision=supports_vision,
                    tools_override=tools,
                    context_limit=context_limit,
                    on_content=lambda c: self._on_content_with_limit(c),
                    on_thinking=lambda t: self._on_thinking_chunk(t),
                    on_tool_call=lambda n, a: (
                        (self._addStatus.emit(f"[tool]{n}"), self._showToolStatus.emit(n))
                        if n not in self._SILENT_TOOLS else None
                    ),
                    on_tool_result=lambda n, a, r: (
                        (self._add_tool_result(n, r, a), self._hideToolStatus.emit())
                        if n not in self._SILENT_TOOLS else None
                    ),
                    on_tool_args_delta=lambda name, delta, acc: (
                        self._toolArgsDelta.emit(name, delta, acc)
                    ),
                    on_iteration_start=_on_iter,
                )
            elif tools:
                # ★ Ask 模式：仍用 agent loop 但只提供只读工具
                result = self.client.agent_loop_auto(
                    messages=messages,
                    model=model,
                    provider=provider,
                    max_iterations=15,  # Ask 模式限制迭代（主要是查询）
                    max_tokens=None,
                    enable_thinking=use_think,
                    supports_vision=supports_vision,
                    tools_override=tools,  # ★ 只传入只读工具
                    context_limit=context_limit,
                    on_content=lambda c: self._on_content_with_limit(c),
                    on_thinking=lambda t: self._on_thinking_chunk(t),
                    on_tool_call=lambda n, a: (
                        (self._addStatus.emit(f"[tool]{n}"), self._showToolStatus.emit(n))
                        if n not in self._SILENT_TOOLS else None
                    ),
                    on_tool_result=lambda n, a, r: (
                        (self._add_tool_result(n, r, a), self._hideToolStatus.emit())
                        if n not in self._SILENT_TOOLS else None
                    ),
                    on_iteration_start=_on_iter,
                )
            else:
                # 无工具的纯对话模式（fallback）
                self._showGenerating.emit()  # ★ 显示 "Generating..." 等待首字
                result = {'ok': True, 'content': '', 'tool_calls_history': [], 'iterations': 1, 'usage': {}}
                for chunk in self.client.chat_stream(
                    messages=messages, 
                    model=model, 
                    provider=provider, 
                    tools=None,
                    max_tokens=None,
                ):
                    if self.client.is_stop_requested():
                        self._agentStopped.emit()
                        return
                    
                    ctype = chunk.get('type')
                    if ctype == 'content':
                        content = chunk.get('content', '')
                        result['content'] += content
                        # 统一走 _on_content_with_limit（内含 <think> 解析）
                        self._on_content_with_limit(content)
                    elif ctype == 'thinking':
                        # 原生 reasoning_content
                        self._on_thinking_chunk(chunk.get('content', ''))
                    elif ctype == 'done':
                        # 收集 usage 统计
                        usage = chunk.get('usage', {})
                        if usage:
                            result['usage'] = usage
                    elif ctype == 'stopped':
                        self._agentStopped.emit()
                        return
                    elif ctype == 'error':
                        result = {'ok': False, 'error': chunk.get('error')}
                        break
            
            if self.client.is_stop_requested():
                self._agentStopped.emit()
                return
            
            if result.get('ok'):
                self._agentDone.emit(result)
            else:
                error_msg = result.get('error', 'Unknown error')
                # 显示更详细的错误
                self._agentError.emit(f"API Error: {error_msg}")
                
        except Exception as e:
            import traceback
            if self.client.is_stop_requested():
                self._agentStopped.emit()
            else:
                # 显示完整错误信息
                error_detail = f"{type(e).__name__}: {str(e)}"
                print(f"[AI Tab Error] {traceback.format_exc()}")  # 控制台输出
                self._agentError.emit(error_detail)

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
    
