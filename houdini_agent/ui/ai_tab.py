# -*- coding: utf-8 -*-
"""
Houdini Agent - AI Tab
Agent loop, multi-turn tool calling, streaming UI

模块拆分结构（逐步迁移中）:
  ui/header.py          — HeaderMixin: 顶部设置栏构建
  ui/input_area.py      — InputAreaMixin: 输入区域和模式切换
  ui/chat_view.py       — ChatViewMixin: 对话显示和滚动逻辑
  core/agent_runner.py  — AgentRunnerMixin: Agent 循环和工具调度
  core/session_manager.py — SessionManagerMixin: 多会话管理和缓存
"""

import json
import math
import os
import threading
import time
import uuid
import queue
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
)
import re

# Mixin 模块（从 ai_tab.py 拆分出的子模块）
from .header import HeaderMixin
from .input_area import InputAreaMixin
from .chat_view import ChatViewMixin
from ..core.agent_runner import AgentRunnerMixin
from ..core.session_manager import SessionManagerMixin

# ★ 大脑启发式长期记忆系统
from ..utils.memory_store import get_memory_store
from ..utils.reward_engine import get_reward_engine
from ..utils.reflection import get_reflection_module
from ..utils.growth_tracker import get_growth_tracker, TaskMetric

# ★ Plan 模式
from ..utils.plan_manager import get_plan_manager, PLAN_TOOL_CREATE, PLAN_TOOL_UPDATE_STEP, PLAN_TOOL_ASK_QUESTION


class AITab(
    HeaderMixin,
    InputAreaMixin,
    ChatViewMixin,
    AgentRunnerMixin,
    SessionManagerMixin,
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
        
        self.client = AIClient()
        self.mcp = HoudiniMCP()
        self.client.set_tool_executor(self._execute_tool_with_todo)
        
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
        self._init_memory_system()
        
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
        self._system_prompt_think = self._build_system_prompt(with_thinking=True)
        self._system_prompt_no_think = self._build_system_prompt(with_thinking=False)
        self._cached_prompt_think = self.token_optimizer.optimize_system_prompt(
            self._system_prompt_think, max_length=1800
        )
        self._cached_prompt_no_think = self.token_optimizer.optimize_system_prompt(
            self._system_prompt_no_think, max_length=1500
        )
        # 兼容旧引用
        self._system_prompt = self._system_prompt_think
        self._cached_optimized_system_prompt = self._cached_prompt_think
        self._build_ui()
        self._wire_events()
        self._load_model_preference(restore_provider=True)  # 恢复上次使用的提供商和模型
        self._update_key_status()
        self._update_context_stats()
        
        # ★ 启动时自动恢复上次的会话（从 sessions_manifest.json）
        self._restore_all_sessions()
        
        # 定期自动保存（每 60 秒），防止 Houdini 退出时丢失会话
        self._auto_save_timer = QtCore.QTimer(self)
        self._auto_save_timer.timeout.connect(self._periodic_save_all)
        self._auto_save_timer.start(60_000)  # 60 秒
        
        # 注册 atexit 回调和 QApplication.aboutToQuit 信号
        import atexit
        atexit.register(self._atexit_save)
        app = QtWidgets.QApplication.instance()
        if app:
            app.aboutToQuit.connect(self._periodic_save_all)
        
        # ★ 启动时静默检查更新（延迟 5 秒，不阻塞初始化）
        QtCore.QTimer.singleShot(5000, self._silent_update_check)
        
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

    def _init_memory_system(self):
        """初始化长期记忆系统（后台线程，不阻塞 UI）"""
        def _init():
            try:
                self._memory_store = get_memory_store()
                self._reward_engine = get_reward_engine()
                self._reflection_module = get_reflection_module()
                self._growth_tracker = get_growth_tracker()
                self._memory_initialized = True
                print(f"[Memory] 长期记忆系统已初始化: {self._memory_store.get_stats()}")
            except Exception as e:
                print(f"[Memory] 初始化失败 (非致命): {e}")
                self._memory_initialized = False

        thread = threading.Thread(target=_init, daemon=True)
        thread.start()

    def _activate_long_term_memory(self, user_message: str, scene_context: dict = None) -> str:
        """动态记忆激活 — "我想起来了"

        核心机制:
        1. 当前问题 embedding
        2. 检索相关 episodic (具体经历)
        3. 检索相关 semantic (抽象知识)
        4. 检索适用的 strategies (策略)
        5. 按 importance * relevance 排序，压缩注入

        Context = WorkingMemory + TopK(RelevanceMemory)
        """
        if not self._memory_initialized or not self._memory_store:
            return ""

        try:
            store = self._memory_store

            # 构建查询（用户消息 + 场景关键词）
            query = user_message
            if scene_context:
                selected_types = scene_context.get('selected_types', [])
                if selected_types:
                    query += ' ' + ' '.join(selected_types)

            parts = []

            # 1. 检索相关 episodic (具体经历) — TopK=3
            episodes = store.search_episodic(query, top_k=3, min_importance=0.2)
            for ep, score in episodes:
                if score > 0.3:
                    status = "✅" if ep.success else "❌"
                    parts.append(
                        f"[Past Experience] {status} {ep.task_description[:80]} "
                        f"→ {ep.result_summary[:60]}"
                    )
                    # 增加激活次数（通过 tags 追踪）
                    store.increment_semantic_activation(ep.id) if hasattr(store, 'increment_semantic_activation') else None

            # 2. 检索相关 semantic (抽象知识) — TopK=5
            rules = store.search_semantic(query, top_k=5, min_confidence=0.3)
            for rule, score in rules:
                if score > 0.25:
                    parts.append(f"[Learned Rule] {rule.rule[:100]}")
                    store.increment_semantic_activation(rule.id)

            # 3. 检索适用的 strategies — TopK=3
            strategies = store.search_procedural(query, top_k=3)
            for strat, score in strategies:
                if score > 0.2:
                    parts.append(f"[Strategy] {strat.description[:80]}")

            if not parts:
                return ""

            # 限制注入量（最多 500 字符，避免浪费 token）
            result = "[Long-Term Memory]\n" + "\n".join(parts)
            if len(result) > 500:
                result = result[:500] + "..."

            return result

        except Exception as e:
            print(f"[Memory] 记忆激活失败: {e}")
            return ""

    def _reflect_after_task(self, result: dict, agent_params: dict):
        """任务完成后的反思钩子 — 在后台线程执行

        从 agent result 中提取信号，创建 episodic 记忆，
        计算 reward，触发规则/LLM 反思。
        """
        if not self._memory_initialized or not self._reflection_module:
            return

        try:
            # 提取任务信息
            tool_calls_history = result.get('tool_calls_history', [])
            final_content = result.get('final_content', '') or result.get('content', '')
            new_messages = result.get('new_messages', [])

            # 构建工具调用序列
            tool_calls = []
            error_count = 0
            retry_count = 0
            for tc in tool_calls_history:
                tc_result = tc.get('result', {})
                success = bool(tc_result.get('success', True))
                has_error = bool(tc_result.get('error', ''))
                tool_calls.append({
                    "name": tc.get('tool_name', ''),
                    "success": success and not has_error,
                    "error": tc_result.get('error', ''),
                })
                if has_error or not success:
                    error_count += 1

            # 检测重试（连续相同工具调用）
            for i in range(1, len(tool_calls)):
                if (tool_calls[i]["name"] == tool_calls[i-1]["name"]
                        and not tool_calls[i-1]["success"]):
                    retry_count += 1

            # 提取用户请求
            history = self._agent_history if self._agent_history is not None else self._conversation_history
            task_description = ""
            for msg in reversed(history):
                if msg.get('role') == 'user':
                    content = msg.get('content', '')
                    if isinstance(content, list):
                        task_description = ' '.join(
                            p.get('text', '') for p in content if p.get('type') == 'text'
                        )
                    else:
                        task_description = content
                    task_description = task_description[:200]
                    break

            # 判断成功 / 失败
            success = result.get('ok', True) and error_count < len(tool_calls) * 0.5

            # 结果摘要
            result_summary = ""
            if final_content:
                # 去除 think 标签
                import re as _re
                clean = _re.sub(r'<think>[\s\S]*?</think>', '', final_content).strip()
                result_summary = clean[:150]

            session_id = self._agent_session_id or self._session_id

            # 执行反思
            reflect_result = self._reflection_module.reflect_on_task(
                session_id=session_id,
                task_description=task_description,
                result_summary=result_summary,
                success=success,
                error_count=error_count,
                retry_count=retry_count,
                tool_calls=tool_calls,
                ai_client=self.client,
                model=agent_params.get('model', 'deepseek-chat'),
                provider=agent_params.get('provider', 'deepseek'),
            )

            # 更新 Growth Tracker
            if self._growth_tracker:
                metric = TaskMetric(
                    success=success,
                    error_count=error_count,
                    retry_count=retry_count,
                    tool_call_count=len(tool_calls),
                    reward=reflect_result.get('reward', 0.0),
                    tags=reflect_result.get('tags', []),
                )
                self._growth_tracker.record_task(metric)

                # 如果 LLM 反思返回了技能置信度更新
                if reflect_result.get('deep_reflected') and 'skill_confidence' in reflect_result:
                    self._growth_tracker.update_skill_confidence_batch(
                        reflect_result.get('skill_confidence', {})
                    )

            if reflect_result.get('reward', 0) > 0:
                print(f"[Memory] 反思完成: reward={reflect_result['reward']:.2f}, "
                      f"tags={reflect_result.get('tags', [])}, "
                      f"deep_reflected={reflect_result.get('deep_reflected', False)}")

        except Exception as e:
            import traceback
            print(f"[Memory] 反思钩子异常: {e}")
            traceback.print_exc()

    def _get_personality_injection(self) -> str:
        """获取个性注入文本（附加到 system prompt 末尾）"""
        if not self._memory_initialized or not self._growth_tracker:
            return ""
        try:
            return self._growth_tracker.get_personality_description()
        except Exception:
            return ""

    def _build_system_prompt(self, with_thinking: bool = True) -> str:
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
        self._theme = ThemeEngine()
        self._theme.load_template(Path(__file__).parent / "style_template.qss")
        self._theme.load_preference()
        self.setStyleSheet(self._theme.render())
        
        self.setMinimumWidth(320)
        
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 顶部设置栏
        header = self._build_header()
        layout.addWidget(header)
        
        # 会话标签栏（多会话切换）
        session_tabs_bar = self._build_session_tabs()
        layout.addWidget(session_tabs_bar)
        
        # 节点上下文栏
        self.node_context_bar = NodeContextBar()
        self.node_context_bar.refreshRequested.connect(self._refresh_node_context)
        layout.addWidget(self.node_context_bar)
        
        # 对话区域（多会话 - 使用 QStackedWidget）
        self.session_stack = QtWidgets.QStackedWidget()
        layout.addWidget(self.session_stack, 1)
        
        # 创建第一个会话
        self._create_initial_session()

        # 输入区域
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

    def _apply_font_scale(self):
        """重新渲染 QSS 并应用到界面"""
        self.setStyleSheet(self._theme.render())
        self._theme.save_preference()

    def _zoom_in(self):
        self._theme.zoom_in()
        self._apply_font_scale()

    def _zoom_out(self):
        self._theme.zoom_out()
        self._apply_font_scale()

    def _zoom_reset(self):
        self._theme.zoom_reset()
        self._apply_font_scale()

    def _on_font_settings(self):
        """打开字号设置面板"""
        dlg = FontSettingsDialog(current_scale=self._theme.scale, parent=self)
        dlg.scaleChanged.connect(self._on_font_scale_preview)
        dlg.exec_()
        # 对话框关闭后保存最终结果
        self._theme.set_scale(dlg.scale)
        self._apply_font_scale()

    def _on_font_scale_preview(self, scale: float):
        """实时预览字号缩放"""
        self._theme.set_scale(scale)
        self.setStyleSheet(self._theme.render())

    # ===== 上下文统计 =====
    
    def _estimate_tokens(self, text: str) -> int:
        """估算文本的 token 数量（粗略估算）
        
        中文约 1.5 字符/token，英文约 4 字符/token
        这里使用简单的混合估算
        """
        if not text:
            return 0
        
        # 统计中文字符
        chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        other_chars = len(text) - chinese_chars
        
        # 中文约 1.5 字符/token，其他约 4 字符/token
        tokens = chinese_chars / 1.5 + other_chars / 4
        return int(tokens)
    
    def _calculate_context_tokens(self) -> int:
        """计算当前上下文的总 token 数（含工具定义）"""
        # 缓存工具定义 token 数（只算一次，因为工具定义不变）
        if not hasattr(self, '_tools_token_cache'):
            import json as _json
            from houdini_agent.utils.ai_client import HOUDINI_TOOLS
            tools_json = _json.dumps(HOUDINI_TOOLS, ensure_ascii=False)
            self._tools_token_cache = self.token_optimizer.estimate_tokens(tools_json)
        
        total = self._tools_token_cache
        
        # 系统提示词
        total += self.token_optimizer.estimate_tokens(self._system_prompt)
        
        # 上下文摘要
        if self._context_summary:
            total += self.token_optimizer.estimate_tokens(self._context_summary)
        
        # 对话历史
        total += self.token_optimizer.calculate_message_tokens(self._conversation_history)
        
        return total
    
    def _save_model_preference(self):
        """保存模型选择偏好"""
        settings = QSettings("HoudiniAI", "Assistant")
        provider = self._current_provider()
        model = self.model_combo.currentText()
        settings.setValue("last_provider", provider)
        settings.setValue("last_model", model)
        settings.setValue("use_think", self.think_check.isChecked())
    
    def _load_model_preference(self, restore_provider: bool = False):
        """加载模型选择偏好
        
        Args:
            restore_provider: 是否同时恢复提供商选择（仅在初始化时为 True）
        """
        settings = QSettings("HoudiniAI", "Assistant")
        last_provider = settings.value("last_provider", "")
        last_model = settings.value("last_model", "")
        
        # 恢复 Think 开关
        use_think = settings.value("use_think", True)
        # QSettings 可能返回字符串 "true"/"false"
        if isinstance(use_think, str):
            use_think = use_think.lower() == 'true'
        self.think_check.setChecked(bool(use_think))
        
        if not last_provider:
            return
        
        # 恢复提供商（仅在启动时调用一次）
        if restore_provider and last_provider != self._current_provider():
            for i in range(self.provider_combo.count()):
                if self.provider_combo.itemData(i) == last_provider:
                    # 暂时阻断信号，避免触发 _on_provider_changed 递归
                    self.provider_combo.blockSignals(True)
                    self.provider_combo.setCurrentIndex(i)
                    self.provider_combo.blockSignals(False)
                    # 手动刷新模型列表和状态
                    self._refresh_models(last_provider)
                    self._update_key_status()
                    break
        
        # 恢复模型
        current_provider = self._current_provider()
        if last_provider == current_provider and last_model:
            available_models = [self.model_combo.itemText(i) for i in range(self.model_combo.count())]
            if last_model in available_models:
                index = self.model_combo.findText(last_model)
                if index >= 0:
                    self.model_combo.setCurrentIndex(index)
    
    def _get_current_context_limit(self) -> int:
        """获取当前模型的上下文限制"""
        model = self.model_combo.currentText()
        return self._model_context_limits.get(model, 64000)
    
    def _update_context_stats(self):
        """更新上下文统计显示（包含优化状态）"""
        used = self._calculate_context_tokens()
        limit = self._get_current_context_limit()
        
        # 格式化显示
        if used >= 1000:
            used_str = f"{used / 1000:.1f}K"
        else:
            used_str = str(used)
        
        limit_str = f"{limit // 1000}K"
        
        # 计算百分比
        percent = (used / limit) * 100 if limit > 0 else 0
        
        # 优化状态指示
        optimize_indicator = ""
        if self._auto_optimize:
            should_compress, _ = self.token_optimizer.should_compress(used, limit)
            if should_compress:
                optimize_indicator = " *"  # 需要优化
            else:
                optimize_indicator = ""  # 已优化/正常
        
        # 根据使用比例设置颜色
        if percent < 50:
            color = CursorTheme.TEXT_MUTED
        elif percent < 80:
            color = CursorTheme.ACCENT_ORANGE
        else:
            color = CursorTheme.ACCENT_RED
        
        self.context_label.setText(f"{percent:.1f}% {used_str}/{limit_str}{optimize_indicator}")
        # 动态状态 → QSS 选择器 QLabel#contextLabel[state="..."]
        if percent < 50:
            ctx_state = ""
        elif percent < 80:
            ctx_state = "warning"
        else:
            ctx_state = "critical"
        self.context_label.setProperty("state", ctx_state)
        self.context_label.style().unpolish(self.context_label)
        self.context_label.style().polish(self.context_label)
        
        # 更新优化按钮状态（如果超过阈值，高亮显示）
        opt_state = "warning" if percent >= 80 else ""
        self.btn_optimize.setProperty("state", opt_state)
        self.btn_optimize.style().unpolish(self.btn_optimize)
        self.btn_optimize.style().polish(self.btn_optimize)

    def _update_token_stats_display(self):
        """更新 Token 统计按钮显示（对齐 Cursor：显示费用）"""
        total = self._token_stats['total_tokens']
        cost = self._token_stats.get('estimated_cost', 0.0)
        
        # 格式化 token 显示
        if total >= 1000000:
            tok_display = f"{total / 1000000:.1f}M"
        elif total >= 1000:
            tok_display = f"{total / 1000:.1f}K"
        else:
            tok_display = str(total)
        
        # 格式化费用显示（Cursor 风格：$0.12）
        if cost >= 1.0:
            cost_display = f"${cost:.2f}"
        elif cost >= 0.01:
            cost_display = f"${cost:.2f}"
        elif cost > 0:
            cost_display = f"${cost:.4f}"
        else:
            cost_display = ""
        
        # 按钮文本：token数 | $费用
        if cost_display:
            self.token_stats_btn.setText(f"{tok_display} | {cost_display}")
        else:
            self.token_stats_btn.setText(tok_display)
        
        # 计算 cache 命中率
        cache_read = self._token_stats['cache_read']
        cache_write = self._token_stats['cache_write']
        cache_total = cache_read + cache_write
        hit_rate_display = f"{(cache_read / cache_total * 100):.1f}%" if cache_total > 0 else "N/A"
        
        reasoning = self._token_stats.get('reasoning_tokens', 0)
        reasoning_line = tr('token.reasoning_line', reasoning) if reasoning > 0 else ""
        
        self.token_stats_btn.setToolTip(
            tr('token.summary',
               self._token_stats['requests'],
               self._token_stats['input_tokens'],
               self._token_stats['output_tokens'],
               reasoning_line,
               cache_read, cache_write, hit_rate_display,
               total, cost_display or '$0.00')
        )
    
    def _show_token_stats_dialog(self):
        """显示详细 Token 统计对话框（对齐 Cursor：使用 TokenAnalyticsPanel）"""
        from houdini_agent.ui.cursor_widgets import TokenAnalyticsPanel
        records = getattr(self, '_call_records', []) or []
        dialog = TokenAnalyticsPanel(records, self._token_stats, parent=self)
        dialog.exec_()
        if dialog.should_reset_stats:
            self._reset_token_stats()
    
    def _reset_token_stats(self):
        """重置 Token 统计"""
        self._token_stats = {
            'input_tokens': 0,
            'output_tokens': 0,
            'reasoning_tokens': 0,
            'cache_read': 0,
            'cache_write': 0,
            'total_tokens': 0,
            'requests': 0,
            'estimated_cost': 0.0,
        }
        self._call_records = []
        self._update_token_stats_display()
        
        # 显示提示
        if self._current_response:
            self._current_response.add_status(tr('status.stats_reset'))

    # ===== UI 辅助 =====
    
    def _current_provider(self) -> str:
        return self.provider_combo.currentData() or 'deepseek'

    def _refresh_models(self, provider: str):
        self.model_combo.clear()
        
        if provider == 'ollama':
            # 尝试动态获取 Ollama 模型列表
            try:
                models = self.client.get_ollama_models()
                if models:
                    self.model_combo.addItems(models)
                    return
            except Exception:
                pass
        
        # 使用预设的模型列表
        self.model_combo.addItems(self._model_map.get(provider, []))

    def _update_key_status(self):
        provider = self._current_provider()
        
        if provider == 'ollama':
            # 测试 Ollama 连接
            result = self.client.test_connection('ollama')
            if result.get('ok'):
                self.key_status.setText("Local")
                self.key_status.setProperty("state", "ok")
            else:
                self.key_status.setText("Offline")
                self.key_status.setProperty("state", "error")
        elif self.client.has_api_key(provider):
            masked = self.client.get_masked_key(provider)
            self.key_status.setText(masked)
            self.key_status.setProperty("state", "ok")
        else:
            self.key_status.setText("No Key")
            self.key_status.setProperty("state", "warning")
        self.key_status.style().unpolish(self.key_status)
        self.key_status.style().polish(self.key_status)

    def _on_provider_changed(self):
        provider = self._current_provider()
        self._refresh_models(provider)
        self._load_model_preference()  # 切换提供商时也尝试加载上次使用的模型
        self._update_key_status()

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
            
            if self._thinking_timer:
                self._thinking_timer.stop()
                self._thinking_timer = None
            
            # ★ 停止输入框呼吸光晕 + 停止活跃 response 的流光
            self._stop_input_glow()
            self._stop_active_aurora()
        
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
    
    def _on_append_content(self, text: str):
        """处理内容追加（主线程槽函数）
        
        注意：内容已经在 _on_content_with_limit → _drain_tag_buffer → 
        _emit_normal_content 中经过了 <think> 标签过滤和伪造检测。
        这里只负责将文本交给 UI 控件显示，不做额外过滤。
        """
        resp = self._agent_response or self._current_response
        if not text or not resp:
            return
        # ★ 修复：不丢弃包含换行符的 chunk
        # 纯换行符（\n\n）是 Markdown 段落分隔的关键信号，
        # 丢弃它们会导致多段内容粘连在一起
        if not text.strip() and '\n' not in text:
            return
        try:
            # ★ 内容开始流入 → 隐藏 "Generating..." 状态（如果正在显示）
            if hasattr(self, 'thinking_bar') and getattr(self.thinking_bar, '_mode', None) == 'generating':
                self.thinking_bar.stop()
            resp.append_content(text)
            self._scroll_agent_to_bottom(force=False)
        except RuntimeError:
            pass  # widget 已被 clear 销毁

    def _on_content_with_limit(self, text: str):
        """处理内容追加，解析 <think> 标签，分离思考和正式内容"""
        if not text:
            return

        # 初始化输出缓冲
        if not hasattr(self, '_output_buffer'):
            self._output_buffer = ""
            self._last_flush_time = time.time()
            self._adaptive_buf_size = 80
            self._adaptive_interval = 0.15
            self._last_render_duration = 0.0
            self._flush_count = 0
            self._is_first_content_chunk = True

        # 追加到标签解析缓冲区
        self._tag_parse_buf += text
        self._drain_tag_buffer()

    # ------------------------------------------------------------------
    # <think> 标签流式解析
    # ------------------------------------------------------------------

    @staticmethod
    def _partial_tag_at_end(text: str, tag: str) -> int:
        """检测 text 末尾是否有 tag 的不完整前缀，返回匹配长度 (0 = 无)"""
        for i in range(min(len(tag) - 1, len(text)), 0, -1):
            if tag[:i] == text[-i:]:
                return i
        return 0

    def _drain_tag_buffer(self):
        """处理 _tag_parse_buf，将内容分发到正式输出或思考面板"""
        buf = self._tag_parse_buf
        while buf:
            if not self._in_think_block:
                # ── 正常模式：寻找 <think> ──
                pos = buf.find('<think>')
                if pos >= 0:
                    if pos > 0:
                        self._emit_normal_content(buf[:pos])
                    buf = buf[pos + 7:]          # 跳过 <think>
                    self._in_think_block = True
                    # ★ Think 开关打开时才显示思考面板；关闭时静默丢弃 <think> 内容
                    if self._think_enabled:
                        self._thinking_needs_finalize = True  # 进入思考，标记需要 finalize
                        # 如果思考已 finalize，恢复为活跃状态并重启计时
                        self._resume_thinking()
                    continue
                # 检查末尾是否有不完整的 <think>
                hold = self._partial_tag_at_end(buf, '<think>')
                if hold:
                    self._emit_normal_content(buf[:-hold])
                    self._tag_parse_buf = buf[-hold:]
                    return
                # 全部是正常内容
                self._emit_normal_content(buf)
                self._tag_parse_buf = ""
                return
            else:
                # ── 思考模式：寻找 </think> ──
                pos = buf.find('</think>')
                if pos >= 0:
                    if self._think_enabled and pos > 0:
                        self._addThinking.emit(buf[:pos])
                    buf = buf[pos + 8:]          # 跳过 </think>
                    self._in_think_block = False
                    # 思考结束：立即 finalize 思考区块并停止计时器
                    if self._think_enabled:
                        self._finalize_thinking()
                    continue
                # 检查末尾是否有不完整的 </think>
                hold = self._partial_tag_at_end(buf, '</think>')
                if hold:
                    if self._think_enabled:
                        safe = buf[:-hold]
                        if safe:
                            self._addThinking.emit(safe)
                    self._tag_parse_buf = buf[-hold:]
                    return
                # 全部是思考内容
                if self._think_enabled:
                    self._addThinking.emit(buf)
                # ★ Think 关闭时：静默丢弃 <think> 块内的内容
                self._tag_parse_buf = ""
                return
        self._tag_parse_buf = ""

    def _finalize_thinking(self):
        """思考阶段结束（线程安全：自动分派到主线程）"""
        self._finalizeThinkingSignal.emit()

    def _resume_thinking(self):
        """新一轮 <think> 开始（线程安全：自动分派到主线程）"""
        self._resumeThinkingSignal.emit()

    @QtCore.Slot()
    def _finalize_thinking_main_thread(self):
        """[主线程] 实际执行 finalize 思考区块并停止计时器"""
        try:
            resp = self._agent_response or self._current_response
            if resp and resp._has_thinking:
                if not resp.thinking_section._finalized:
                    resp.thinking_section.finalize()
        except RuntimeError:
            pass  # widget 已被 clear 销毁
        if self._thinking_timer:
            self._thinking_timer.stop()
            self._thinking_timer = None
        # ★ 停止输入框上方的思考指示条
        try:
            self.thinking_bar.stop()
        except (RuntimeError, AttributeError):
            pass
    
    @QtCore.Slot()
    def _resume_thinking_main_thread(self):
        """[主线程] 实际执行恢复思考区块并重启计时器"""
        try:
            resp = self._agent_response or self._current_response
            if resp and resp._has_thinking:
                ts = resp.thinking_section
                if ts._finalized:
                    ts.resume()
        except RuntimeError:
            pass  # widget 已被 clear 销毁
        # 重启计时器（如果已停止）
        if not self._thinking_timer:
            self._thinking_timer = QtCore.QTimer(self)
            self._thinking_timer.timeout.connect(lambda: self._updateThinkingTime.emit())
            self._thinking_timer.start(1000)
        # ★ 重新启动输入框上方的思考指示条
        try:
            self.thinking_bar.start()
        except (RuntimeError, AttributeError):
            pass

    def _emit_normal_content(self, text: str):
        """发送正式内容（带 token 限制 + 自适应缓冲刷新）
        
        ★ 自适应策略（借鉴 markstream-vue 的时间预算机制）：
        - 首个 chunk 立即刷新，消除首字延迟
        - 后续根据上一次渲染耗时动态调整缓冲大小：
          渲染快 → 小缓冲、多刷新（流畅感）
          渲染慢 → 大缓冲、少刷新（避免卡顿）
        - 换行始终立即刷新（段落边界及时显示）
        """
        if not text:
            return
        # 首次正式内容到达时，确保思考区块已 finalize（适配 DeepSeek 原生 reasoning_content）
        # 使用标志位避免从后台线程访问 Qt 控件属性
        if self._in_think_block is False and getattr(self, '_thinking_needs_finalize', True):
            self._finalize_thinking()  # 通过信号分派到主线程
            self._thinking_needs_finalize = False

        # Token 限制仅对正式内容计数
        if not self._check_output_token_limit(text):
            if self._output_buffer:
                self._appendContent.emit(self._output_buffer)
                self._output_buffer = ""
            self._appendContent.emit(tr('ai.token_limit'))
            self._addStatus.emit(tr('ai.token_limit_status'))
            self.client.request_stop()
            return

        self._output_buffer += text

        # ★ 自适应缓冲刷新策略
        should_flush = False
        current_time = time.time()

        # 初始化自适应状态（首次调用）
        if not hasattr(self, '_adaptive_buf_size'):
            self._adaptive_buf_size = 80       # 初始缓冲大小（字符）
            self._adaptive_interval = 0.15     # 初始兜底间隔（秒）
            self._last_render_duration = 0.0   # 上次渲染耗时
            self._flush_count = 0              # flush 计数（性能追踪）
            self._is_first_content_chunk = True  # 首个 chunk 标志

        # 规则 1: 首个 chunk 立即刷新（消除首字延迟）
        if self._is_first_content_chunk:
            should_flush = True
            self._is_first_content_chunk = False
        # 规则 2: 缓冲区达到自适应阈值
        elif len(self._output_buffer) >= self._adaptive_buf_size:
            should_flush = True
        # 规则 3: 换行时立即刷新（段落边界及时显示）
        elif '\n' in text:
            should_flush = True
        # 规则 4: 自适应兜底间隔
        elif current_time - self._last_flush_time > self._adaptive_interval:
            should_flush = True

        if should_flush and self._output_buffer:
            flush_start = time.time()

            # 实时过滤伪造的工具调用行
            buf = self._output_buffer
            if '[ok]' in buf or '[err]' in buf or '[工具执行结果]' in buf or '[Tool Result]' in buf:
                lines = buf.split('\n')
                filtered = []
                has_fake = False
                for ln in lines:
                    s = ln.strip()
                    if s == '[工具执行结果]' or s == '[Tool Result]' or self._FAKE_TOOL_PATTERNS.match(s):
                        has_fake = True
                        continue
                    filtered.append(ln)
                buf = '\n'.join(filtered)
                if has_fake and not getattr(self, '_fake_warned', False):
                    self._addStatus.emit(tr('ai.fake_tool'))
                    self._fake_warned = True
            if buf.strip():
                self._appendContent.emit(buf)
            self._output_buffer = ""
            self._last_flush_time = current_time
            self._flush_count += 1

            # ★ 自适应调整：根据上次渲染耗时动态调整缓冲参数
            render_dur = time.time() - flush_start
            self._last_render_duration = render_dur
            if render_dur < 0.004:
                # 渲染很快 → 减小缓冲，更频繁刷新（流畅感）
                self._adaptive_buf_size = max(40, self._adaptive_buf_size - 20)
                self._adaptive_interval = max(0.08, self._adaptive_interval - 0.02)
            elif render_dur > 0.012:
                # 渲染较慢 → 增大缓冲，减少刷新（避免卡顿）
                self._adaptive_buf_size = min(500, self._adaptive_buf_size + 40)
                self._adaptive_interval = min(0.40, self._adaptive_interval + 0.05)

    def _check_output_token_limit(self, text: str) -> bool:
        """检查正式输出 token 是否超过限制（思考内容不计入）"""
        if not text:
            return True
        new_tokens = self.token_optimizer.estimate_tokens(text)
        self._current_output_tokens += new_tokens
        if self._current_output_tokens >= self._max_output_tokens:
            return False
        if (self._current_output_tokens >= self._output_token_warning
                and self._current_output_tokens < self._max_output_tokens):
            remaining = self._max_output_tokens - self._current_output_tokens
            if remaining < 400:
                self._addStatus.emit(
                    tr('ai.approaching_limit', self._current_output_tokens, self._max_output_tokens))
        return True

    def _on_thinking_chunk(self, text: str):
        """处理原生 reasoning_content（DeepSeek R1 等模型）
        
        ★ 受 Think 开关控制：关闭时静默丢弃
        """
        if text and self._think_enabled:
            self._addThinking.emit(text)
    
    @QtCore.Slot(str)
    def _on_add_thinking(self, text: str):
        """在主线程更新思考内容（槽函数）"""
        try:
            resp = self._agent_response or self._current_response
            if resp:
                resp.add_thinking(text)
                # ★ 首次思考内容 → 启动输入框上方思考指示条
                if hasattr(self, 'thinking_bar') and not self.thinking_bar.isVisible():
                    self.thinking_bar.start()
            self._scroll_agent_to_bottom(force=False)
        except RuntimeError:
            pass  # widget 已被 clear 销毁

    def _on_add_status(self, text: str):
        try:
            resp = self._agent_response or self._current_response
            if resp:
                resp.add_status(text)
                self._scroll_agent_to_bottom(force=False)
        except RuntimeError:
            pass  # widget 已被 clear 销毁

    def _on_update_thinking(self):
        try:
            resp = self._agent_response or self._current_response
            if resp:
                resp.update_thinking_time()
                # ★ 同步更新输入框上方思考指示条的时间
                if hasattr(self, 'thinking_bar') and self.thinking_bar.isVisible():
                    if resp._has_thinking:
                        self.thinking_bar.set_elapsed(resp.thinking_section._total_elapsed())
        except RuntimeError:
            pass  # 控件可能已销毁

    def _on_agent_done(self, result: dict):
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
        if self._memory_initialized and tool_calls_history:
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
        # ★ Ask 模式安全守卫：拦截任何不在白名单的工具
        if not self._agent_mode and not self._plan_mode and tool_name not in self._ASK_MODE_TOOLS:
            return {
                "success": False,
                "error": tr('ask.restricted', tool_name)
            }
        
        # ★ Plan 规划阶段安全守卫
        if self._plan_mode and self._plan_phase == 'planning':
            allowed = self._PLAN_PLANNING_TOOLS | {'create_plan'}
            if tool_name not in allowed:
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
    
    def _execute_tool_in_main_thread(self, tool_name: str, kwargs: dict) -> dict:
        """在主线程执行工具（线程安全）
        
        使用 BlockingQueuedConnection + Queue 确保：
        1. Houdini 操作在主线程执行
        2. 多个工具调用不会竞争
        3. 结果安全传递回调用线程
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
            try:
                result = self._tool_result_queue.get(timeout=30.0)
                return result
            except queue.Empty:
                return {"success": False, "error": tr('ai.main_exec_timeout')}
    
    # ------------------------------------------------------------------
    # Plan 模式工具处理
    # ------------------------------------------------------------------

    def _handle_create_plan(self, kwargs: dict) -> dict:
        """处理 create_plan 工具调用（后台线程）"""
        try:
            if self._plan_manager is None:
                self._plan_manager = get_plan_manager()
            plan_data = self._plan_manager.create_plan(self._session_id, kwargs)
            self._plan_phase = 'awaiting_confirmation'
            # 切换状态：Planning → Generating（Plan 已完成构建）
            self._showGenerating.emit()
            # 通过信号在主线程渲染 PlanViewer 卡片
            self._renderPlanViewer.emit(plan_data)
            return {
                "success": True,
                "result": f"Plan '{plan_data.get('title', '')}' created with {len(plan_data.get('steps', []))} steps. Waiting for user confirmation."
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to create plan: {e}"}

    def _handle_update_plan_step(self, kwargs: dict) -> dict:
        """处理 update_plan_step 工具调用（后台线程）"""
        try:
            if self._plan_manager is None:
                self._plan_manager = get_plan_manager()
            step_id = kwargs.get('step_id', '')
            status = kwargs.get('status', 'done')
            result_summary = kwargs.get('result_summary', '')
            plan = self._plan_manager.update_step(
                self._session_id, step_id, status, result_summary
            )
            if not plan:
                return {"success": False, "error": f"No active plan found for session {self._session_id}"}
            # 通过信号在主线程更新 PlanViewer 步骤状态
            self._updatePlanStep.emit(step_id, status, result_summary or '')
            # 检查是否全部完成
            if plan.get('status') == 'completed':
                self._plan_phase = 'completed'
            return {
                "success": True,
                "result": f"Step {step_id} updated to '{status}'"
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to update plan step: {e}"}

    def _handle_ask_question(self, kwargs: dict) -> dict:
        """处理 ask_question 工具调用（后台线程）
        
        复用 _request_tool_confirmation 的阻塞模式：
        1. 设置 pending 属性 → 发射信号 → 主线程渲染 AskQuestionCard
        2. 后台线程在 queue 上阻塞等待用户回答
        3. 用户提交后 queue.put(answers) → 后台线程继续
        """
        questions = kwargs.get('questions', [])
        if not questions:
            return {"success": False, "error": "No questions provided"}

        self._ask_question_result_queue = queue.Queue()
        self._pending_ask_questions = questions
        self._askQuestionRequest.emit()

        try:
            result = self._ask_question_result_queue.get(timeout=300.0)  # 5 分钟超时
            if result is None:
                return {"success": True, "result": "User skipped the questions."}
            # 格式化答案为可读文本
            answer_lines = []
            for q_id, selections in result.items():
                readable = []
                for sel in selections:
                    if sel.startswith("__free_text__:"):
                        readable.append(sel.replace("__free_text__:", ""))
                    else:
                        readable.append(sel)
                answer_lines.append(f"{q_id}: {', '.join(readable)}")
            return {
                "success": True,
                "result": f"User answered:\n" + "\n".join(answer_lines)
            }
        except queue.Empty:
            return {"success": True, "result": "User did not answer within the time limit."}

    @QtCore.Slot()
    def _on_render_ask_question(self):
        """主线程：在聊天流中插入 AskQuestionCard"""
        q = getattr(self, '_ask_question_result_queue', None)
        questions = getattr(self, '_pending_ask_questions', [])

        if not q:
            print("[AskQuestion] ⚠ _ask_question_result_queue 不存在")
            return

        try:
            card = AskQuestionCard(questions, parent=self.chat_container)
        except Exception as e:
            print(f"[AskQuestion] ✖ AskQuestionCard 创建失败: {e}")
            q.put(None)
            return

        def _on_answered(answers: dict):
            q.put(answers)

        def _on_cancelled():
            q.put(None)

        card.answered.connect(_on_answered)
        card.cancelled.connect(_on_cancelled)

        # 插入到对话流
        try:
            self.chat_layout.insertWidget(self.chat_layout.count() - 1, card)
        except Exception as e:
            print(f"[AskQuestion] ⚠ 插入失败: {e}")
            q.put(None)
            return

        card.setVisible(True)
        try:
            self._scroll_to_bottom(force=True)
        except Exception:
            pass

    @QtCore.Slot(dict)
    def _show_plan_generation_progress(self, accumulated: str):
        """从 create_plan 的流式参数中提取进度信息并显示 Planning... 状态"""
        import re as _re
        # 统计已出现的 step id
        step_ids = _re.findall(r'"id"\s*:\s*"(step-\d+)"', accumulated)
        # 尝试提取 title
        title_match = _re.search(r'"title"\s*:\s*"([^"]{1,30})', accumulated)
        title_part = title_match.group(1) if title_match else ""

        # 检查是否已进入 architecture 部分
        has_arch = '"architecture"' in accumulated
        arch_nodes = _re.findall(r'"id"\s*:\s*"(?!step-)([^"]+)"', accumulated)

        if has_arch and arch_nodes:
            progress = f"architecture ({len(arch_nodes)} nodes)"
        elif step_ids:
            progress = f"step {len(step_ids)}"
            if title_part:
                progress = f"「{title_part}」 {progress}"
        elif title_part:
            progress = f"「{title_part}」"
        else:
            progress = ""

        self._showPlanning.emit(progress)

    @QtCore.Slot()
    def _on_create_streaming_plan(self):
        """主线程：创建流式 Plan 预览卡片并插入聊天流"""
        try:
            # 如果已有旧的流式卡片则先移除
            if self._streaming_plan_card is not None:
                self._streaming_plan_card.setParent(None)
                self._streaming_plan_card.deleteLater()

            card = StreamingPlanCard(parent=self.chat_container)
            self._streaming_plan_card = card
            self.chat_layout.insertWidget(self.chat_layout.count() - 1, card)
            self._scroll_to_bottom(force=True)
        except Exception as e:
            print(f"[Plan] Create streaming card error: {e}")

    @QtCore.Slot(str)
    def _on_update_streaming_plan(self, accumulated: str):
        """主线程：将流式 JSON 碎片增量渲染到流式 Plan 卡片

        使用简单的节流策略：缓存最新数据，通过 singleShot 延迟处理，
        避免每个 token 都触发正则解析和 UI 更新。
        """
        self._streaming_plan_acc = accumulated
        if not getattr(self, '_streaming_plan_timer_active', False):
            self._streaming_plan_timer_active = True
            QtCore.QTimer.singleShot(150, self._flush_streaming_plan)

    def _flush_streaming_plan(self):
        """实际执行流式 Plan 卡片更新"""
        self._streaming_plan_timer_active = False
        if self._streaming_plan_card is None:
            return
        acc = getattr(self, '_streaming_plan_acc', '')
        if not acc:
            return
        try:
            old_count = self._streaming_plan_card._rendered_step_count
            self._streaming_plan_card.update_from_accumulated(acc)
            new_count = self._streaming_plan_card._rendered_step_count
            if new_count > old_count:
                self._scroll_to_bottom()
        except Exception as e:
            print(f"[Plan] Update streaming card error: {e}")

    def _on_render_plan_viewer(self, plan_data: dict):
        """主线程：将流式 Plan 卡片原地升级为完整交互卡片。

        如果流式卡片已存在 → finalize_with_data 原地补充完整数据。
        如果不存在（边缘情况）→ 创建新卡片。
        """
        try:
            if self._streaming_plan_card is not None:
                # ★ 原地升级：在流式骨架上补充 DAG + 按钮
                card = self._streaming_plan_card
                card.finalize_with_data(plan_data)
                card.planConfirmed.connect(self._on_plan_confirmed)
                card.planRejected.connect(self._on_plan_rejected)
                self._active_plan_viewer = card
                self._streaming_plan_card = None  # 不再追踪为流式卡片
            else:
                # 边缘情况：没有流式卡片时直接创建 PlanViewer
                viewer = PlanViewer(plan_data, parent=self.chat_container)
                viewer.planConfirmed.connect(self._on_plan_confirmed)
                viewer.planRejected.connect(self._on_plan_rejected)
                self._active_plan_viewer = viewer
                self.chat_layout.insertWidget(self.chat_layout.count() - 1, viewer)
            self._scroll_to_bottom(force=True)
        except Exception as e:
            print(f"[Plan] Render PlanViewer error: {e}")

    @QtCore.Slot(str, str, str)
    def _on_update_plan_step(self, step_id: str, status: str, result_summary: str):
        """主线程：更新 PlanViewer 卡片中的步骤状态"""
        if self._active_plan_viewer:
            try:
                self._active_plan_viewer.update_step_status(step_id, status, result_summary)
            except Exception as e:
                print(f"[Plan] Update step UI error: {e}")

    def _on_plan_confirmed(self, plan_data: dict):
        """用户点击 Confirm 按钮 → 启动执行阶段"""
        self._plan_phase = 'executing'
        # 禁用 PlanViewer 按钮（防止重复点击）
        if self._active_plan_viewer:
            self._active_plan_viewer.set_confirmed()
        
        # 构造执行提示消息
        exec_msg = tr('ai.plan_confirmed_msg', plan_data.get('title', 'Plan'))
        self._conversation_history.append({
            'role': 'user', 'content': exec_msg
        })
        
        # 创建新的 AI 回复块
        self._set_running(True)
        self._add_ai_response()
        self._agent_response = self._current_response
        self._start_active_aurora()
        
        # 构造 agent_params（复用上次的 provider/model 设置）
        agent_params = getattr(self, '_last_agent_params', {}).copy()
        agent_params['use_agent'] = True          # 执行阶段用完整工具
        agent_params['plan_mode'] = True
        agent_params['plan_executing'] = True     # 标记为 Plan 执行阶段
        agent_params['plan_data'] = plan_data
        
        # 后台线程执行
        thread = threading.Thread(
            target=self._run_agent, args=(agent_params,), daemon=True
        )
        thread.start()

    def _on_plan_rejected(self):
        """用户点击 Reject 按钮 → 丢弃 Plan"""
        self._plan_phase = 'idle'
        try:
            if self._plan_manager is None:
                self._plan_manager = get_plan_manager()
            self._plan_manager.delete_plan(self._session_id)
        except Exception:
            pass
        if self._active_plan_viewer:
            self._active_plan_viewer.set_rejected()
        self._active_plan_viewer = None

    # 已自带 checkpoint 追踪的工具（在 _on_add_node_operation 中有专用分支）
    _SELF_TRACKING_TOOLS = frozenset({
        'create_node', 'create_nodes_batch', 'create_wrangle_node',
        'delete_node', 'set_node_parameter',
    })

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

    @QtCore.Slot(str, dict)
    def _on_execute_tool_main_thread(self, tool_name: str, kwargs: dict):
        """在主线程执行工具（槽函数）
        
        注意：此方法在主线程中执行，直接操作 Houdini API 是安全的。
        所有修改操作包裹在 undo group 中，支持一键撤销整个 Agent 操作。
        ★ 对于未自带 checkpoint 的修改工具，会在执行前后快照网络子节点以检测变更。
        """
        result = {"success": False, "error": tr('ai.unknown_err')}
        
        # 判断是否为修改操作（需要 undo group）
        _MUTATING_TOOLS = {
            "create_node", "create_nodes_batch", "create_wrangle_node",
            "delete_node", "set_node_parameter", "connect_nodes",
            "copy_node", "batch_set_parameters", "set_display_flag",
            "execute_python", "save_hip", "run_skill",
        }
        use_undo_group = tool_name in _MUTATING_TOOLS
        
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
            # 给 Houdini 主线程处理 UI 事件的机会（防止事件堆积导致崩溃）
            try:
                QtWidgets.QApplication.processEvents()
            except Exception:
                pass
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
        if len(history) < 6:
            return  # 太少，不需管理
        
        current_tokens = self.token_optimizer.calculate_message_tokens(history)
        context_limit = self._get_current_context_limit()
        
        # 更新预算
        self.token_optimizer.budget.max_tokens = context_limit
        should_compress, reason = self.token_optimizer.should_compress(current_tokens, context_limit)
        
        if not (should_compress and self._auto_optimize):
            if reason and ('警告' in reason or 'warning' in reason.lower()):
                self._addStatus.emit(f"Note: {reason}")
            return
        
        old_tokens = current_tokens
        
        # --- 按 user 消息划分轮次 ---
        rounds = []       # [[msg, msg, ...], ...]
        current_round = []
        for m in history:
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
            # 压缩 tool 就够了
            history.clear()
            history.extend(compressed)
            saved = old_tokens - new_tokens
            if saved > 0:
                pct = saved / old_tokens * 100 if old_tokens else 0
                self._addStatus.emit(tr('opt.auto_status', saved))
            return
        
        # --- 第二遍：删除最早的完整轮次，直到低于阈值 ---
        target = int(context_limit * 0.65)  # 目标降到 65%
        while len(rounds) > 2:
            # 删除最早的轮次
            removed = rounds.pop(0)
            compressed = [m for rnd in rounds for m in rnd]
            new_tokens = self.token_optimizer.calculate_message_tokens(compressed)
            if new_tokens <= target:
                break
        
        # 在头部插入摘要提示
        summary_note = {
            'role': 'system',
            'content': tr('ai.old_rounds', n_rounds - len(rounds))
        }
        
        history.clear()
        history.append(summary_note)
        history.extend([m for rnd in rounds for m in rnd])
        
        saved = old_tokens - self.token_optimizer.calculate_message_tokens(history)
        if saved > 0:
            self._addStatus.emit(tr('opt.auto_status', saved))
            self._render_conversation_history()
    
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
            self._conversation_history.append({'role': 'user', 'content': msg_content})
        else:
            self._conversation_history.append({'role': 'user', 'content': processed_text})
        
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
            _last_user_idx = None
            for _i in range(len(self._conversation_history) - 1, -1, -1):
                if self._conversation_history[_i].get('role') == 'user':
                    _last_user_idx = _i
                    break
            
            history_to_send = []
            for msg_idx, msg in enumerate(self._conversation_history):
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
            
            # Cursor 风格预发送压缩：只压缩 tool 结果，保留 user/assistant 完整
            if self._auto_optimize:
                current_tokens = self.token_optimizer.calculate_message_tokens(messages)
                should_compress, _ = self.token_optimizer.should_compress(current_tokens, context_limit)
                
                if should_compress:
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
            elif use_web:
                if self._cached_optimized_tools is None:
                    self._cached_optimized_tools = UltraOptimizer.optimize_tool_definitions(HOUDINI_TOOLS)
                tools = self._cached_optimized_tools
            else:
                if self._cached_optimized_tools_no_web is None:
                    filtered = [t for t in HOUDINI_TOOLS if t['function']['name'] not in ('web_search', 'fetch_webpage')]
                    self._cached_optimized_tools_no_web = UltraOptimizer.optimize_tool_definitions(filtered)
                tools = self._cached_optimized_tools_no_web
            
            # ★ Plan 模式的静默工具集合（不在 UI 中显示的工具）
            _silent = self._SILENT_TOOLS | self._PLAN_SILENT_TOOLS if plan_mode else self._SILENT_TOOLS
            
            # ★ 通用回调：每轮 API 迭代开始时显示 "Generating..." 状态
            # 第1轮也显示，填补 Send → 首字之间的空白
            _on_iter = lambda i: self._showGenerating.emit()
            
            if plan_mode:
                # ★ Plan 模式：使用 agent loop（规划或执行阶段均走此分支）
                _max_iter = 999 if plan_executing else 20
                result = self.client.agent_loop_auto(
                    messages=messages,
                    model=model,
                    provider=provider,
                    max_iterations=_max_iter,
                    max_tokens=None,
                    enable_thinking=use_think,
                    supports_vision=supports_vision,
                    tools_override=tools,
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
        
        parent = _parent_override or hou.node(parent_path)
        if parent is None:
            return None
        
        # 1) 创建节点
        try:
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
        
        # 4) ★ 递归重建子节点
        children_map: dict = {}  # name → hou.Node  用于稍后恢复内部连接
        for child_snap in snapshot.get("children", []):
            child_node = self._restore_node_from_snapshot(hou, child_snap, _parent_override=new_node)
            if child_node:
                children_map[child_node.name()] = child_node
        
        # 5) ★ 恢复子节点间的内部连接
        for iconn in snapshot.get("internal_connections", []):
            try:
                src_node = children_map.get(iconn["src_name"])
                dest_node = children_map.get(iconn["dest_name"])
                if src_node and dest_node:
                    dest_node.setInput(iconn["dest_input"], src_node)
            except Exception:
                continue
        
        # 6) 恢复外部输入连接（仅顶层节点 — 子节点的外部连接由父级调用处理）
        if _parent_override is None:
            for conn in snapshot.get("input_connections", []):
                try:
                    src = hou.node(conn["source_path"])
                    if src:
                        new_node.setInput(conn["input_index"], src)
                except Exception:
                    continue
        
        # 7) 恢复外部输出连接（仅顶层节点）
        if _parent_override is None:
            for conn in snapshot.get("output_connections", []):
                try:
                    dest = hou.node(conn["dest_path"])
                    if dest:
                        dest.setInput(conn["dest_input_index"], new_node, conn.get("output_index", 0))
                except Exception:
                    continue
        
        # 8) 恢复标志位
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
            # 触发单条撤销
            label._on_undo()
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
        names = {'openai': 'OpenAI', 'deepseek': 'DeepSeek', 'glm': 'GLM（智谱AI）', 'ollama': 'Ollama'}
        
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

    def _on_read_network(self):
        ok, text = self.mcp.get_network_structure_text()
        if ok:
            # 添加到对话
            self._add_user_message("[Read network structure]")
            response = self._add_ai_response()
            response.add_status("Read network")
            response.add_collapsible("Network structure", text)
            response.finalize()
            self._conversation_history.append({'role': 'user', 'content': f"[Network structure]\n{text}"})
            self._update_context_stats()
            # 更新节点上下文栏
            self._refresh_node_context()
        else:
            self._add_ai_response().set_content(f"Error: {text}")

    # ============================================================
    # 图片输入支持
    # ============================================================
    
    def _current_model_supports_vision(self) -> bool:
        """检查当前选中的模型是否支持图片输入"""
        model = self.model_combo.currentText()
        features = self._model_features.get(model, {})
        return features.get('supports_vision', False)
    
    def _on_attach_image(self):
        """打开文件对话框选择图片"""
        if not self._current_model_supports_vision():
            model = self.model_combo.currentText()
            QtWidgets.QMessageBox.information(
                self, "不支持图片",
                f"当前模型 {model} 不支持图片输入。\n请切换到支持视觉的模型（如 Claude、GPT-5.2 等）。"
            )
            return
        
        file_paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "选择图片", "",
            "Images (*.png *.jpg *.jpeg *.gif *.webp *.bmp);;All Files (*)"
        )
        for fp in file_paths:
            self._add_image_from_path(fp)
    
    def _add_image_from_path(self, file_path: str):
        """从文件路径加载图片并添加到待发送列表（自动缩放过大图片）"""
        import base64
        try:
            # ★ 通过 QImage 加载，统一走缩放逻辑
            qimg = QtGui.QImage(file_path)
            if qimg.isNull():
                print(f"[AI Tab] 无法加载图片: {file_path}")
                return
            qimg = self._resize_image_if_needed(qimg, self._MAX_IMAGE_DIMENSION)
            
            ext = os.path.splitext(file_path)[1].lower()
            # 优先保持原始格式；BMP/GIF 等不适合直接发 API，统一转 PNG
            if ext in ('.jpg', '.jpeg'):
                fmt, media_type = 'JPEG', 'image/jpeg'
            elif ext == '.webp':
                fmt, media_type = 'WEBP', 'image/webp'
            else:
                fmt, media_type = 'PNG', 'image/png'
            
            buf = QtCore.QBuffer()
            buf.open(QtCore.QIODevice.WriteOnly)
            quality = 90 if fmt == 'JPEG' else -1
            qimg.save(buf, fmt, quality)
            raw_bytes = buf.data().data()
            buf.close()
            
            # ★ 过大时降级为 JPEG 压缩
            if len(raw_bytes) > self._MAX_IMAGE_BYTES and fmt != 'JPEG':
                buf2 = QtCore.QBuffer()
                buf2.open(QtCore.QIODevice.WriteOnly)
                qimg.save(buf2, 'JPEG', 85)
                raw_bytes = buf2.data().data()
                buf2.close()
                media_type = 'image/jpeg'
                print(f"[AI Tab] 图片过大，已转为 JPEG ({len(raw_bytes)//1024}KB)")
            
            b64 = base64.b64encode(raw_bytes).decode('utf-8')
            self._add_pending_image(b64, media_type)
        except Exception as e:
            print(f"[AI Tab] 加载图片失败: {e}")
    
    # ★ 图片尺寸限制：超过此分辨率的图片自动缩放（防止 base64 过大导致 API 400 错误）
    _MAX_IMAGE_DIMENSION = 2048  # 最长边不超过 2048px
    _MAX_IMAGE_BYTES = 5 * 1024 * 1024  # base64 前的原始字节数上限 ~5MB（编码后约 6.7MB）

    @staticmethod
    def _resize_image_if_needed(image: 'QtGui.QImage', max_dim: int = 2048) -> 'QtGui.QImage':
        """如果图片超过 max_dim，等比缩放。返回缩放后的 QImage。"""
        w, h = image.width(), image.height()
        if w <= max_dim and h <= max_dim:
            return image
        if w > h:
            new_w = max_dim
            new_h = int(h * max_dim / w)
        else:
            new_h = max_dim
            new_w = int(w * max_dim / h)
        print(f"[AI Tab] 图片过大 ({w}x{h})，自动缩放至 {new_w}x{new_h}")
        return image.scaled(new_w, new_h, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)

    def _on_image_dropped(self, image: 'QtGui.QImage'):
        """ChatInput 拖拽或粘贴图片的回调"""
        if not self._current_model_supports_vision():
            return
        import base64
        # ★ 自动缩放过大图片
        image = self._resize_image_if_needed(image, self._MAX_IMAGE_DIMENSION)
        buf = QtCore.QBuffer()
        buf.open(QtCore.QIODevice.WriteOnly)
        image.save(buf, "PNG")
        raw_bytes = buf.data().data()
        buf.close()
        # ★ 如果 PNG 仍然过大，改用 JPEG 压缩
        if len(raw_bytes) > self._MAX_IMAGE_BYTES:
            buf2 = QtCore.QBuffer()
            buf2.open(QtCore.QIODevice.WriteOnly)
            image.save(buf2, "JPEG", 85)
            raw_bytes = buf2.data().data()
            buf2.close()
            media_type = 'image/jpeg'
            print(f"[AI Tab] PNG 过大，已转为 JPEG (quality=85, {len(raw_bytes)//1024}KB)")
        else:
            media_type = 'image/png'
        b64 = base64.b64encode(raw_bytes).decode('utf-8')
        self._add_pending_image(b64, media_type)
    
    def _add_pending_image(self, b64_data: str, media_type: str):
        """添加图片到待发送列表并在预览区显示缩略图（点击可放大）"""
        # 创建缩略图和完整 pixmap
        img_bytes = __import__('base64').b64decode(b64_data)
        full_pixmap = QtGui.QPixmap()
        full_pixmap.loadFromData(img_bytes)
        thumb = full_pixmap.scaled(60, 60, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        
        # 存储
        idx = len(self._pending_images)
        self._pending_images.append((b64_data, media_type, thumb))
        
        # 创建预览 widget
        img_widget = QtWidgets.QWidget()
        img_layout = QtWidgets.QVBoxLayout(img_widget)
        img_layout.setContentsMargins(2, 2, 2, 2)
        img_layout.setSpacing(1)
        
        lbl = ClickableImageLabel(thumb, full_pixmap)
        lbl.setObjectName("imgThumb")
        img_layout.addWidget(lbl)
        
        # 删除按钮
        rm_btn = QtWidgets.QPushButton("x")
        rm_btn.setFixedSize(16, 16)
        rm_btn.setObjectName("imgRemoveBtn")
        rm_btn.clicked.connect(lambda checked=False, i=idx: self._remove_pending_image(i))
        img_layout.addWidget(rm_btn, alignment=QtCore.Qt.AlignCenter)
        
        # 插入到 stretch 之前
        count = self.image_preview_layout.count()
        self.image_preview_layout.insertWidget(count - 1, img_widget)
        self.image_preview_container.setVisible(True)
    
    def _remove_pending_image(self, index: int):
        """移除待发送图片"""
        if 0 <= index < len(self._pending_images):
            self._pending_images[index] = None  # 标记为已删除
            self._rebuild_image_preview()  # 过滤 None 后重建整个预览区
    
    def _rebuild_image_preview(self):
        """重新构建图片预览区"""
        # 清除所有 widget（保留 stretch）
        while self.image_preview_layout.count() > 1:
            item = self.image_preview_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        # 重新过滤并添加
        new_images = [(b64, mt, th) for entry in self._pending_images 
                      if entry is not None for b64, mt, th in [entry]]
        self._pending_images = list(new_images)
        
        if not self._pending_images:
            self.image_preview_container.setVisible(False)
            return
        
        for i, (b64, mt, thumb) in enumerate(self._pending_images):
            img_widget = QtWidgets.QWidget()
            img_layout = QtWidgets.QVBoxLayout(img_widget)
            img_layout.setContentsMargins(2, 2, 2, 2)
            img_layout.setSpacing(1)
            
            # 从 base64 还原完整 pixmap 用于放大预览
            full_pixmap = QtGui.QPixmap()
            full_pixmap.loadFromData(__import__('base64').b64decode(b64))
            lbl = ClickableImageLabel(thumb, full_pixmap)
            lbl.setObjectName("imgThumb")
            img_layout.addWidget(lbl)
            
            rm_btn = QtWidgets.QPushButton("x")
            rm_btn.setFixedSize(16, 16)
            rm_btn.setObjectName("imgRemoveBtn")
            rm_btn.clicked.connect(lambda checked=False, idx=i: self._remove_pending_image(idx))
            img_layout.addWidget(rm_btn, alignment=QtCore.Qt.AlignCenter)
            
            count = self.image_preview_layout.count()
            self.image_preview_layout.insertWidget(count - 1, img_widget)
    
    def _clear_pending_images(self):
        """清空所有待发送图片"""
        self._pending_images.clear()
        while self.image_preview_layout.count() > 1:
            item = self.image_preview_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.image_preview_container.setVisible(False)
    
    def _build_multimodal_content(self, text: str, images: list) -> list:
        """构建包含文字和图片的多模态消息内容（OpenAI Vision API 格式）
        
        Args:
            text: 用户文字消息
            images: List of (base64_data, media_type, thumbnail) tuples
            
        Returns:
            list: content 数组，包含 text 和 image_url 项
        """
        # ★ API 支持的 media type 白名单（BMP 等需要先转换）
        _SUPPORTED_MEDIA = {'image/png', 'image/jpeg', 'image/gif', 'image/webp'}
        
        content_parts = []
        # ★ 始终添加 text 部分（即使为空也提供占位符，某些 API 要求至少一个 text block）
        content_parts.append({"type": "text", "text": text or " "})
        # 添加图片
        for b64_data, media_type, _thumb in images:
            if not b64_data:
                continue  # 跳过空数据
            # ★ 不支持的 media type 降级为 image/png
            if media_type not in _SUPPORTED_MEDIA:
                media_type = 'image/png'
            content_parts.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{media_type};base64,{b64_data}"
                }
            })
        return content_parts
    
    def _on_read_selection(self):
        ok, text = self.mcp.describe_selection()
        if ok:
            self._add_user_message("[Read selected nodes]")
            response = self._add_ai_response()
            response.add_status("Read selection")
            response.add_collapsible("Node details", text)
            response.finalize()
            self._conversation_history.append({'role': 'user', 'content': f"[Selected nodes]\n{text}"})
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
    
    def _on_cache_menu(self):
        """显示缓存菜单"""
        menu = QtWidgets.QMenu(self)
        
        # 保存存档（独立文件）
        archive_action = menu.addAction("存档当前对话")
        archive_action.triggered.connect(self._archive_cache)
        
        # 加载对话
        load_action = menu.addAction("加载对话...")
        load_action.triggered.connect(self._load_cache_dialog)
        
        menu.addSeparator()
        
        # 压缩为摘要（减少 token）
        compress_action = menu.addAction("压缩旧对话为摘要")
        compress_action.triggered.connect(self._compress_to_summary)
        
        # 列出所有缓存
        list_action = menu.addAction("查看所有缓存")
        list_action.triggered.connect(self._list_caches)
        
        menu.addSeparator()
        
        # 自动保存开关
        auto_save_action = menu.addAction("[on] 自动保存" if self._auto_save_cache else "自动保存")
        auto_save_action.setCheckable(True)
        auto_save_action.setChecked(self._auto_save_cache)
        auto_save_action.triggered.connect(lambda: setattr(self, '_auto_save_cache', not self._auto_save_cache))
        
        # 显示菜单
        menu.exec_(self.btn_cache.mapToGlobal(QtCore.QPoint(0, self.btn_cache.height())))
    
    @staticmethod
    def _strip_images_for_cache(history: list) -> list:
        """剥离 conversation_history 中的 base64 图片数据，
        用占位文本替代，大幅减小缓存文件体积。
        返回一份深拷贝，不修改原始 history。
        """
        import copy
        stripped = []
        for msg in history:
            content = msg.get('content')
            if isinstance(content, list):
                # 多模态消息：content 是 [{type:text,...}, {type:image_url,...}, ...]
                new_parts = []
                for part in content:
                    if part.get('type') == 'image_url':
                        url = part.get('image_url', {}).get('url', '')
                        if url.startswith('data:'):
                            # 替换 base64 为占位符，保留 media type 信息
                            media_type = url.split(';')[0].replace('data:', '')
                            new_parts.append({
                                'type': 'text',
                                'text': f'[Image: {media_type}]',
                            })
                        else:
                            new_parts.append(copy.copy(part))
                    else:
                        new_parts.append(copy.copy(part))
                new_msg = msg.copy()
                new_msg['content'] = new_parts
                stripped.append(new_msg)
            else:
                stripped.append(msg)  # 非多模态消息直接引用（str/None 不可变）
        return stripped
    
    def _build_cache_data(self) -> dict:
        """构建缓存数据字典"""
        todo_data = []
        if hasattr(self, 'todo_list') and self.todo_list:
            todo_data = self.todo_list.get_todos_data()
        return {
            'version': '1.0',
            'session_id': self._session_id,
            'created_at': datetime.now().isoformat(),
            'message_count': len(self._conversation_history),
            'estimated_tokens': self._calculate_context_tokens(),
            'conversation_history': self._conversation_history,
            'context_summary': self._context_summary,
            'todo_summary': self.todo_list.get_todos_summary() if hasattr(self, 'todo_list') else "",
            'todo_data': todo_data,
            'token_stats': self._token_stats.copy(),
        }

    def _periodic_save_all(self):
        """定期保存所有会话（QTimer 触发 + aboutToQuit 触发）"""
        try:
            if not self._sessions:
                return
            # 只有存在对话时才保存
            has_any = False
            for sid, sdata in self._sessions.items():
                if sdata.get('conversation_history'):
                    has_any = True
                    break
            if not has_any:
                return
            self._save_all_sessions()
        except Exception as e:
            print(f"[Cache] 定期保存失败: {e}")
    
    def _atexit_save(self):
        """Python 退出时的最后保存机会（atexit 回调）
        
        ★ 此时 Qt widget 可能已被销毁，因此：
        - 使用 _tabs_backup（纯 Python 列表）代替遍历 QTabBar
        - 使用 try/except 包裹 todo_list 访问
        """
        try:
            if not hasattr(self, '_sessions') or not self._sessions:
                return
            # 尝试同步当前状态（Qt widget 可能已销毁）
            try:
                self._save_current_session_state()
            except (RuntimeError, AttributeError):
                pass
            
            # ★ 优先使用 _tabs_backup（纯 Python 数据，不依赖 Qt）
            tabs_info = getattr(self, '_tabs_backup', [])
            if not tabs_info:
                # 如果备份也为空，尝试从 _sessions 字典的 key 中获取
                tabs_info = [(sid, f"Chat") for sid in self._sessions]
            
            # 直接写文件，不依赖 Qt 事件循环
            manifest_tabs = []
            for sid, tab_label in tabs_info:
                if not sid or sid not in self._sessions:
                    continue
                sdata = self._sessions[sid]
                history = sdata.get('conversation_history', [])
                if not history:
                    continue
                # 收集 todo 数据（widget 可能已销毁）
                todo_data = []
                try:
                    todo_list_obj = sdata.get('todo_list')
                    todo_data = todo_list_obj.get_todos_data() if todo_list_obj else []
                except (RuntimeError, AttributeError, Exception):
                    pass
                cache_data = {
                    'version': '1.0',
                    'session_id': sid,
                    'message_count': len(history),
                    'conversation_history': self._strip_images_for_cache(history),
                    'context_summary': sdata.get('context_summary', ''),
                    'todo_data': todo_data,
                    'token_stats': sdata.get('token_stats', {}),
                }
                session_file = self._cache_dir / f"session_{sid}.json"
                with open(session_file, 'w', encoding='utf-8') as f:
                    json.dump(cache_data, f, ensure_ascii=False)
                manifest_tabs.append({
                    'session_id': sid,
                    'tab_label': tab_label,
                    'file': f"session_{sid}.json",
                })
            if manifest_tabs:
                manifest = {
                    'version': '1.0',
                    'active_session_id': self._session_id,
                    'tabs': manifest_tabs,
                }
                manifest_file = self._cache_dir / "sessions_manifest.json"
                with open(manifest_file, 'w', encoding='utf-8') as f:
                    json.dump(manifest, f, ensure_ascii=False)
        except Exception:
            pass  # atexit 中不能抛出异常

    def _save_cache(self) -> bool:
        """自动保存：覆写同 session 文件 + manifest"""
        if not self._conversation_history:
            return False
        try:
            # 同步当前会话状态到 _sessions
            self._save_current_session_state()
            # ★ 同步 tab 备份
            self._sync_tabs_backup()
            
            cache_data = self._build_cache_data()
            # ★ 剥离 base64 图片以减小缓存文件大小
            cache_data['conversation_history'] = self._strip_images_for_cache(
                cache_data.get('conversation_history', [])
            )

            # 1. 覆写固定的 session 文件（一个 session 只有一个文件）
            session_file = self._cache_dir / f"session_{self._session_id}.json"
            with open(session_file, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)

            # 2. 同步更新 sessions_manifest.json（确保所有 tab 信息都是最新的）
            # ★ 不再写 cache_latest.json — 恢复由 sessions_manifest + session_*.json 管理
            self._update_manifest()

            if self._workspace_dir:
                self._update_workspace_cache_info()
            return True
        except Exception as e:
            print(f"[Cache] 自动保存失败: {e}")
            return False
    
    def _update_manifest(self):
        """更新 sessions_manifest.json 以反映当前所有标签的状态"""
        try:
            manifest_tabs = []
            for i in range(self.session_tabs.count()):
                sid = self.session_tabs.tabData(i)
                if not sid:
                    continue
                tab_label = self.session_tabs.tabText(i)
                # 检查该 session 是否有对话文件存在
                session_file = self._cache_dir / f"session_{sid}.json"
                if not session_file.exists():
                    # 检查 _sessions 字典中是否有对话
                    sdata = self._sessions.get(sid, {})
                    history = sdata.get('conversation_history', [])
                    if not history:
                        continue
                manifest_tabs.append({
                    'session_id': sid,
                    'tab_label': tab_label,
                    'file': f"session_{sid}.json",
                })
            if manifest_tabs:
                manifest = {
                    'version': '1.0',
                    'active_session_id': self._session_id,
                    'tabs': manifest_tabs,
                }
                manifest_file = self._cache_dir / "sessions_manifest.json"
                with open(manifest_file, 'w', encoding='utf-8') as f:
                    json.dump(manifest, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Cache] 更新 manifest 失败: {e}")

    def _save_all_sessions(self) -> bool:
        """保存所有打开的会话到磁盘（关闭软件时调用）"""
        try:
            # 先保存当前活跃会话的状态到 _sessions 字典
            self._save_current_session_state()
            # ★ 同步 tab 备份（确保 atexit 时也能用）
            self._sync_tabs_backup()

            manifest_tabs = []
            active_session_id = self._session_id

            for i in range(self.session_tabs.count()):
                sid = self.session_tabs.tabData(i)
                tab_label = self.session_tabs.tabText(i)
                if not sid or sid not in self._sessions:
                    continue

                sdata = self._sessions[sid]
                history = sdata.get('conversation_history', [])
                if not history:
                    # ★ 空会话：清理其磁盘上的旧 session 文件（防止残留）
                    try:
                        old_file = self._cache_dir / f"session_{sid}.json"
                        if old_file.exists():
                            old_file.unlink()
                    except Exception:
                        pass
                    continue  # 空会话不保存

                # 收集 todo 数据（防御 widget 已销毁的情况）
                todo_data = []
                try:
                    todo_list_obj = sdata.get('todo_list')
                    todo_data = todo_list_obj.get_todos_data() if todo_list_obj else []
                except (RuntimeError, AttributeError):
                    pass

                # 写 session 文件（★ 剥离 base64 图片以减小文件大小）
                cache_data = {
                    'version': '1.0',
                    'session_id': sid,
                    'created_at': datetime.now().isoformat(),
                    'message_count': len(history),
                    'conversation_history': self._strip_images_for_cache(history),
                    'context_summary': sdata.get('context_summary', ''),
                    'todo_data': todo_data,
                    'token_stats': sdata.get('token_stats', {}),
                }
                session_file = self._cache_dir / f"session_{sid}.json"
                with open(session_file, 'w', encoding='utf-8') as f:
                    json.dump(cache_data, f, ensure_ascii=False, indent=2)

                manifest_tabs.append({
                    'session_id': sid,
                    'tab_label': tab_label,
                    'file': f"session_{sid}.json",
                })

            if not manifest_tabs:
                return False

            # 写 manifest 文件
            manifest = {
                'version': '1.0',
                'active_session_id': active_session_id,
                'tabs': manifest_tabs,
            }
            manifest_file = self._cache_dir / "sessions_manifest.json"
            with open(manifest_file, 'w', encoding='utf-8') as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)

            # ★ 不再写 cache_latest.json — 恢复由 sessions_manifest + session_*.json 管理
            # print(f"[Cache] 已保存 {len(manifest_tabs)} 个会话到磁盘")
            return True
        except Exception as e:
            print(f"[Cache] 保存所有会话失败: {e}")
            import traceback; traceback.print_exc()
            return False

    def _restore_all_sessions(self) -> bool:
        """从 sessions_manifest.json 恢复所有会话标签（启动时调用，幂等）"""
        # ★ 幂等保护：防止 __init__ 和 main_window 延迟回调重复恢复
        if getattr(self, '_sessions_restored', False):
            return True
        try:
            manifest_file = self._cache_dir / "sessions_manifest.json"
            if not manifest_file.exists():
                return False

            with open(manifest_file, 'r', encoding='utf-8') as f:
                manifest = json.load(f)

            tabs_info = manifest.get('tabs', [])
            if not tabs_info:
                return False

            active_sid = manifest.get('active_session_id', '')
            active_tab_index = 0
            first_tab = True

            for tab_info in tabs_info:
                sid = tab_info.get('session_id', '')
                tab_label = tab_info.get('tab_label', 'Chat')
                session_file = self._cache_dir / tab_info.get('file', '')

                if not session_file.exists():
                    continue

                with open(session_file, 'r', encoding='utf-8') as f:
                    cache_data = json.load(f)

                history = cache_data.get('conversation_history', [])
                if not history:
                    continue

                context_summary = cache_data.get('context_summary', '')
                todo_data = cache_data.get('todo_data', [])
                # ★ 从缓存中恢复 token 使用统计
                saved_token_stats = cache_data.get('token_stats', {
                    'input_tokens': 0, 'output_tokens': 0,
                    'reasoning_tokens': 0,
                    'cache_read': 0, 'cache_write': 0,
                    'total_tokens': 0, 'requests': 0,
                    'estimated_cost': 0.0,
                })

                if first_tab:
                    # 第一个 tab：加载到已有的初始会话中
                    first_tab = False
                    old_id = self._session_id

                    self._session_id = sid
                    self._conversation_history = history
                    self._context_summary = context_summary
                    self._token_stats = saved_token_stats

                    # 更新 sessions 字典
                    if old_id in self._sessions:
                        sdata = self._sessions.pop(old_id)
                        sdata['conversation_history'] = history
                        sdata['context_summary'] = context_summary
                        sdata['token_stats'] = saved_token_stats
                        self._sessions[sid] = sdata
                    elif sid not in self._sessions:
                        self._sessions[sid] = {
                            'scroll_area': self.scroll_area,
                            'chat_container': self.chat_container,
                            'chat_layout': self.chat_layout,
                            'todo_list': self.todo_list,
                            'conversation_history': history,
                            'context_summary': context_summary,
                            'current_response': None,
                            'token_stats': saved_token_stats,
                        }

                    # 恢复 todo 数据
                    if todo_data and hasattr(self, 'todo_list') and self.todo_list:
                        self.todo_list.restore_todos(todo_data)
                        self._ensure_todo_in_chat(self.todo_list, self.chat_layout)

                    # 更新标签
                    for i in range(self.session_tabs.count()):
                        if self.session_tabs.tabData(i) == old_id:
                            self.session_tabs.setTabData(i, sid)
                            self.session_tabs.setTabText(i, tab_label)
                            if sid == active_sid:
                                active_tab_index = i
                            break

                    self._render_conversation_history()
                else:
                    # 后续 tab：创建新标签
                    self._save_current_session_state()
                    self._session_counter += 1

                    scroll_area, chat_container, chat_layout = self._create_session_widgets()
                    self.session_stack.addWidget(scroll_area)

                    tab_index = self.session_tabs.addTab(tab_label)
                    self.session_tabs.setTabData(tab_index, sid)

                    todo = self._create_todo_list(chat_container)
                    # 恢复 todo 数据
                    if todo_data:
                        todo.restore_todos(todo_data)
                        self._ensure_todo_in_chat(todo, chat_layout)

                    self._sessions[sid] = {
                        'scroll_area': scroll_area,
                        'chat_container': chat_container,
                        'chat_layout': chat_layout,
                        'todo_list': todo,
                        'conversation_history': history,
                        'context_summary': context_summary,
                        'current_response': None,
                        'token_stats': saved_token_stats,
                    }

                    # 临时切换到该标签以渲染历史
                    old_scroll = self.scroll_area
                    old_chat_container = self.chat_container
                    old_chat_layout = self.chat_layout
                    old_todo = self.todo_list
                    old_history = self._conversation_history
                    old_summary = self._context_summary
                    old_stats = self._token_stats
                    old_sid = self._session_id

                    self._session_id = sid
                    self._conversation_history = history
                    self._context_summary = context_summary
                    self._token_stats = saved_token_stats
                    self.scroll_area = scroll_area
                    self.chat_container = chat_container
                    self.chat_layout = chat_layout
                    self.todo_list = todo

                    self._render_conversation_history()

                    # 恢复
                    self._session_id = old_sid
                    self._conversation_history = old_history
                    self._context_summary = old_summary
                    self._token_stats = old_stats
                    self.scroll_area = old_scroll
                    self.chat_container = old_chat_container
                    self.chat_layout = old_chat_layout
                    self.todo_list = old_todo

                    if sid == active_sid:
                        active_tab_index = tab_index

            # 切换到之前活跃的标签
            if self.session_tabs.count() > 0:
                self.session_tabs.blockSignals(True)
                self.session_tabs.setCurrentIndex(active_tab_index)
                self.session_tabs.blockSignals(False)

                target_sid = self.session_tabs.tabData(active_tab_index)
                if target_sid and target_sid in self._sessions:
                    self._load_session_state(target_sid)
                    self.session_stack.setCurrentWidget(
                        self._sessions[target_sid]['scroll_area']
                    )

            # ★ 恢复完成后同步 tab 备份并更新 UI 显示
            self._sync_tabs_backup()
            self._update_token_stats_display()
            self._update_context_stats()
            self._sessions_restored = True  # 标记已恢复，防止重复
            print(f"[Cache] 已恢复 {self.session_tabs.count()} 个会话标签")
            return True

        except Exception as e:
            print(f"[Cache] 恢复多会话失败: {e}")
            import traceback; traceback.print_exc()
            return False

    def _archive_cache(self) -> bool:
        """手动存档：创建带时间戳的独立文件（不会被覆写）"""
        if not self._conversation_history:
            QtWidgets.QMessageBox.information(self, "提示", "没有对话历史可存档")
            return False
        try:
            cache_data = self._build_cache_data()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"archive_{self._session_id}_{timestamp}.json"
            archive_file = self._cache_dir / filename
            with open(archive_file, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
            est = cache_data['estimated_tokens']
            self._addStatus.emit(f"已存档: {filename} (~{est} tokens)")
            return True
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "错误", f"存档失败: {str(e)}")
            return False
    
    def _update_workspace_cache_info(self):
        """更新工作区中的缓存信息（供主窗口保存工作区时使用）"""
        # 这个方法会被主窗口调用，用于更新工作区配置
        # 实际保存由主窗口的 _save_workspace 完成
        pass
    
    def _load_cache(self, cache_file: Path, silent: bool = False) -> bool:
        """从缓存文件加载对话历史（在新标签页中打开）
        
        Args:
            cache_file: 缓存文件路径
            silent: 是否静默加载（不显示确认对话框，用于工作区自动恢复）
        """
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            
            # 验证数据格式
            if 'conversation_history' not in cache_data:
                if not silent:
                    QtWidgets.QMessageBox.warning(self, "错误", "缓存文件格式无效")
                return False
            
            # 确认加载（静默模式下跳过）
            if not silent:
                msg_count = len(cache_data.get('conversation_history', []))
                reply = QtWidgets.QMessageBox.question(
                    self, "确认加载",
                    f"将在新标签页加载 {msg_count} 条对话记录。\n是否继续？",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
                )
                
                if reply != QtWidgets.QMessageBox.Yes:
                    return False
            
            history = cache_data.get('conversation_history', [])
            context_summary = cache_data.get('context_summary', '')
            todo_data = cache_data.get('todo_data', [])
            cached_session_id = cache_data.get('session_id', str(uuid.uuid4())[:8])
            # ★ 恢复 token 使用统计
            saved_token_stats = cache_data.get('token_stats', {
                'input_tokens': 0, 'output_tokens': 0,
                'reasoning_tokens': 0,
                'cache_read': 0, 'cache_write': 0,
                'total_tokens': 0, 'requests': 0,
                'estimated_cost': 0.0,
            })
            
            if silent and not self._conversation_history:
                # 静默恢复：当前会话为空时直接加载到当前标签
                self._conversation_history = history
                self._context_summary = context_summary
                self._session_id = cached_session_id
                self._token_stats = saved_token_stats
                # 恢复 todo 数据
                if todo_data and hasattr(self, 'todo_list') and self.todo_list:
                    self.todo_list.restore_todos(todo_data)
                    self._ensure_todo_in_chat(self.todo_list, self.chat_layout)
                # 更新 sessions 字典
                if self._session_id in self._sessions:
                    self._sessions[self._session_id]['conversation_history'] = self._conversation_history
                    self._sessions[self._session_id]['context_summary'] = self._context_summary
                    self._sessions[self._session_id]['token_stats'] = saved_token_stats
                elif self._sessions:
                    # 旧 session_id 已经变了，需要重新映射
                    old_id = list(self._sessions.keys())[0]
                    sdata = self._sessions.pop(old_id)
                    sdata['conversation_history'] = self._conversation_history
                    sdata['context_summary'] = self._context_summary
                    sdata['token_stats'] = saved_token_stats
                    self._sessions[self._session_id] = sdata
                    # 更新标签数据
                    for i in range(self.session_tabs.count()):
                        if self.session_tabs.tabData(i) == old_id:
                            self.session_tabs.setTabData(i, self._session_id)
                            break
                self._render_conversation_history()
                self._update_token_stats_display()
                self._update_context_stats()
                # 自动重命名标签
                if history:
                    for msg in history:
                        if msg.get('role') == 'user' and msg.get('content'):
                            self._auto_rename_tab(msg['content'])
                            break
                print(f"[Workspace] 自动恢复上下文: {len(self._conversation_history)} 条消息")
                return True
            
            # 非静默或当前会话非空：在新标签页中打开
            self._save_current_session_state()
            
            # 创建新标签
            self._session_counter += 1
            scroll_area, chat_container, chat_layout = self._create_session_widgets()
            self.session_stack.addWidget(scroll_area)
            
            # 用缓存文件名或首条用户消息作为标签名
            label = f"Chat {self._session_counter}"
            for msg in history:
                if msg.get('role') == 'user' and msg.get('content'):
                    short = msg['content'][:18].replace('\n', ' ').strip()
                    if len(msg['content']) > 18:
                        short += "..."
                    label = short
                    break
            
            tab_index = self.session_tabs.addTab(label)
            self.session_tabs.setTabData(tab_index, cached_session_id)
            
            todo = self._create_todo_list(chat_container)
            if todo_data:
                todo.restore_todos(todo_data)
                self._ensure_todo_in_chat(todo, chat_layout)
            
            self._sessions[cached_session_id] = {
                'scroll_area': scroll_area,
                'chat_container': chat_container,
                'chat_layout': chat_layout,
                'todo_list': todo,
                'conversation_history': history,
                'context_summary': context_summary,
                'current_response': None,
                'token_stats': saved_token_stats,
            }
            
            # 切换到新标签
            self._session_id = cached_session_id
            self._conversation_history = history
            self._context_summary = context_summary
            self._current_response = None
            self._token_stats = saved_token_stats
            self.scroll_area = scroll_area
            self.chat_container = chat_container
            self.chat_layout = chat_layout
            self.todo_list = todo
            
            self.session_tabs.blockSignals(True)
            self.session_tabs.setCurrentIndex(tab_index)
            self.session_tabs.blockSignals(False)
            self.session_stack.setCurrentWidget(scroll_area)
            
            self._render_conversation_history()
            self._update_token_stats_display()
            self._update_context_stats()
            
            if not silent:
                self._addStatus.emit(f"缓存已加载: {cache_file.name}")
            
            return True
            
        except Exception as e:
            if not silent:
                QtWidgets.QMessageBox.warning(self, "错误", f"加载缓存失败: {str(e)}")
            else:
                print(f"[Workspace] 加载缓存失败: {str(e)}")
            return False
    
    def _load_cache_silent(self, cache_file: Path) -> bool:
        """静默加载缓存（用于工作区自动恢复）"""
        return self._load_cache(cache_file, silent=True)
    
    def _load_cache_dialog(self):
        """显示加载缓存对话框"""
        cache_files = sorted(
            set(self._cache_dir.glob("session_*.json"))
            | set(self._cache_dir.glob("archive_*.json"))
            | set(self._cache_dir.glob("cache_*.json")),
            key=lambda p: p.stat().st_mtime, reverse=True
        )
        
        if not cache_files:
            QtWidgets.QMessageBox.information(self, "提示", "没有找到缓存文件")
            return
        
        # 创建选择对话框
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("选择缓存文件")
        dialog.setMinimumWidth(500)
        
        layout = QtWidgets.QVBoxLayout(dialog)
        
        # 文件列表
        list_widget = QtWidgets.QListWidget()
        for cache_file in cache_files:
            # 读取文件信息
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    msg_count = len(data.get('conversation_history', []))
                    estimated_tokens = data.get('estimated_tokens', 0)
                    created_at = data.get('created_at', '')
                    if created_at:
                        try:
                            dt = datetime.fromisoformat(created_at)
                            created_at = dt.strftime("%Y-%m-%d %H:%M:%S")
                        except:
                            pass
                    token_info = f" | ~{estimated_tokens:,} tokens" if estimated_tokens else ""
                    item_text = f"{cache_file.name}\n  {msg_count} 条消息{token_info} | {created_at}"
            except:
                item_text = cache_file.name
            
            item = QtWidgets.QListWidgetItem(item_text)
            item.setData(QtCore.Qt.UserRole, cache_file)
            list_widget.addItem(item)
        
        layout.addWidget(QtWidgets.QLabel("选择要加载的缓存文件:"))
        layout.addWidget(list_widget)
        
        # 按钮
        btn_layout = QtWidgets.QHBoxLayout()
        btn_load = QtWidgets.QPushButton("加载")
        btn_cancel = QtWidgets.QPushButton("取消")
        btn_layout.addWidget(btn_load)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)
        
        def on_load():
            current = list_widget.currentItem()
            if current:
                cache_file = current.data(QtCore.Qt.UserRole)
                if self._load_cache(cache_file):
                    dialog.accept()
        
        btn_load.clicked.connect(on_load)
        btn_cancel.clicked.connect(dialog.reject)
        
        dialog.exec_()
    
    def _list_caches(self):
        """列出所有缓存文件"""
        cache_files = sorted(
            set(self._cache_dir.glob("session_*.json"))
            | set(self._cache_dir.glob("archive_*.json"))
            | set(self._cache_dir.glob("cache_*.json")),
            key=lambda p: p.stat().st_mtime, reverse=True
        )
        
        if not cache_files:
            QtWidgets.QMessageBox.information(self, "提示", "没有找到缓存文件")
            return
        
        # 创建信息对话框
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("缓存文件列表")
        dialog.setMinimumSize(600, 400)
        
        layout = QtWidgets.QVBoxLayout(dialog)
        
        # 文本显示
        text_edit = QtWidgets.QTextEdit()
        text_edit.setReadOnly(True)
        
        lines = ["缓存文件列表:\n"]
        for cache_file in cache_files:
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    msg_count = len(data.get('conversation_history', []))
                    created_at = data.get('created_at', '')
                    session_id = data.get('session_id', '')
                    estimated_tokens = data.get('estimated_tokens', 0)
                    
                    if created_at:
                        try:
                            dt = datetime.fromisoformat(created_at)
                            created_at = dt.strftime("%Y-%m-%d %H:%M:%S")
                        except:
                            pass
                    
                    size_kb = cache_file.stat().st_size / 1024
                    lines.append(f"  {cache_file.name}")
                    lines.append(f"   会话ID: {session_id}")
                    lines.append(f"   消息数: {msg_count}")
                    if estimated_tokens:
                        lines.append(f"   估算Token: ~{estimated_tokens:,}")
                    lines.append(f"   创建时间: {created_at}")
                    lines.append(f"   文件大小: {size_kb:.1f} KB")
                    lines.append("")
            except Exception as e:
                lines.append(f"[err] {cache_file.name} (读取失败: {str(e)})")
                lines.append("")
        
        text_edit.setPlainText("\n".join(lines))
        layout.addWidget(text_edit)
        
        btn_close = QtWidgets.QPushButton("关闭")
        btn_close.clicked.connect(dialog.accept)
        layout.addWidget(btn_close)
        
        dialog.exec_()
    
    def _compress_to_summary(self):
        """将旧对话压缩为摘要，减少 token 消耗"""
        if len(self._conversation_history) <= 4:
            QtWidgets.QMessageBox.information(self, "提示", "对话历史太短，无需压缩")
            return
        
        # 确认操作
        reply = QtWidgets.QMessageBox.question(
            self, "确认压缩",
            f"将把前 {len(self._conversation_history) - 4} 条对话压缩为摘要，"
            f"保留最近 4 条完整对话。\n\n"
            f"这样可以大幅减少 token 消耗。是否继续？",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        
        if reply != QtWidgets.QMessageBox.Yes:
            return
        
        # 执行压缩
        old_messages = self._conversation_history[:-4]
        recent_messages = self._conversation_history[-4:]
        
        # 生成详细摘要
        summary_parts = ["[历史对话摘要 - 已压缩以节省 token]"]
        
        user_requests = []
        ai_results = []
        
        for msg in old_messages:
            role = msg.get('role', '')
            content = msg.get('content', '')
            
            if role == 'user':
                # 提取用户请求的核心（前200字符）
                user_request = content[:200].replace('\n', ' ')
                if len(content) > 200:
                    user_request += "..."
                user_requests.append(user_request)
            
            elif role == 'assistant' and content:
                # 提取 AI 回复的关键信息
                lines = [l.strip() for l in content.split('\n') if l.strip()]
                if lines:
                    # 取最后一行或前150字符
                    result_summary = lines[-1][:150].replace('\n', ' ')
                    if len(lines[-1]) > 150:
                        result_summary += "..."
                    ai_results.append(result_summary)
        
        # 合并摘要
        if user_requests:
            summary_parts.append(f"\n用户请求 ({len(user_requests)} 条):")
            for i, req in enumerate(user_requests[:10], 1):  # 最多显示10条
                summary_parts.append(f"  {i}. {req}")
            if len(user_requests) > 10:
                summary_parts.append(f"  ... 还有 {len(user_requests) - 10} 条请求")
        
        if ai_results:
            summary_parts.append(f"\nAI 完成的任务 ({len(ai_results)} 条):")
            for i, res in enumerate(ai_results[:10], 1):  # 最多显示10条
                summary_parts.append(f"  {i}. {res}")
            if len(ai_results) > 10:
                summary_parts.append(f"  ... 还有 {len(ai_results) - 10} 条结果")
        
        summary_text = "\n".join(summary_parts)
        
        # 更新历史：用摘要替换旧对话
        self._conversation_history = [
            {'role': 'system', 'content': summary_text}
        ] + recent_messages
        
        # 更新上下文摘要
        self._context_summary = summary_text
        
        # 重新渲染
        self._render_conversation_history()
        
        # 更新统计
        self._update_context_stats()
        
        # 计算节省的 token
        old_tokens = sum(self._estimate_tokens(json.dumps(msg)) for msg in old_messages)
        new_tokens = self._estimate_tokens(summary_text)
        saved_tokens = old_tokens - new_tokens
        
        QtWidgets.QMessageBox.information(
            self, "压缩完成",
            f"对话已压缩！\n\n"
            f"原始: ~{old_tokens} tokens\n"
            f"压缩后: ~{new_tokens} tokens\n"
            f"节省: ~{saved_tokens} tokens ({saved_tokens/old_tokens*100:.1f}%)"
        )
    
    # ---------- 历史渲染辅助 ----------
    _CONTEXT_HEADERS = ('[Network structure]', '[Selected nodes]',
                        '[网络结构]', '[选中节点]')

    # ★ 分批渲染常量（借鉴 markstream-vue 的批次策略）
    _BATCH_INITIAL = 30      # 首批渲染最后 N 条消息（用户最近看到的）
    _BATCH_SIZE = 15          # 后续每批渲染 N 条
    _BATCH_BUDGET_MS = 8      # 每批时间预算（毫秒）

    def _render_conversation_history(self):
        """重新渲染对话历史到 UI

        ★ 分批渲染策略（借鉴 markstream-vue）：
        1. 首批渲染最后 _BATCH_INITIAL 条消息（用户最近看到的）
        2. 用 QTimer.singleShot(0) 模拟 idle callback，逐批渲染剩余
        3. 每批设时间预算，超出则暂停让出主线程

        处理三种数据格式：
        1. role="user" 中嵌入 [Network structure] / [Selected nodes] 等上下文
           → 用户文字正常显示，上下文数据放入可折叠区域
        2. role="assistant" 以 [工具执行结果] 开头
           → 解析每一条 [ok]/[err]/✅/❌ 行，创建折叠式 ToolCallItem
        3. role="tool"（旧缓存格式）
           → 先 add_tool_call 再 set_tool_result（折叠式）
        """
        # 清空当前显示（保留末尾 stretch）
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # 取消之前的分批渲染定时器
        if hasattr(self, '_batch_render_timer') and self._batch_render_timer is not None:
            self._batch_render_timer.stop()
            self._batch_render_timer = None

        messages = self._conversation_history
        if not messages:
            return

        # ★ 预扫描：将消息分组为逻辑"轮次"（每轮 = 一组相关消息）
        groups = self._group_messages_into_turns(messages)
        total_groups = len(groups)

        if total_groups <= self._BATCH_INITIAL:
            # 消息量小，一次性渲染
            self._render_message_groups(groups, 0, total_groups)
        else:
            # ★ 分批渲染：先渲染最后 _BATCH_INITIAL 组（用户最近看到的）
            # 早期消息用占位符
            early_count = total_groups - self._BATCH_INITIAL

            # 插入占位符
            self._batch_placeholder = QtWidgets.QLabel(
                f"⏳ 加载历史消息 ({early_count} 轮)..."
            )
            self._batch_placeholder.setObjectName("batchPlaceholder")
            self._batch_placeholder.setStyleSheet(
                "color: #64748b; padding: 8px 12px; font-size: 12px; "
                "font-style: italic; background: transparent;"
            )
            self._batch_placeholder.setAlignment(QtCore.Qt.AlignCenter)
            # 插入到 stretch 之前
            self.chat_layout.insertWidget(self.chat_layout.count() - 1,
                                         self._batch_placeholder)

            # 渲染最后 _BATCH_INITIAL 组
            self._render_message_groups(groups, early_count, total_groups)

            # 用 QTimer 分批渲染早期消息
            self._batch_groups = groups
            self._batch_cursor = early_count  # 从 early_count 向 0 回退
            self._batch_insert_pos = 0  # 早期消息插入到布局头部
            self._batch_render_timer = QtCore.QTimer(self)
            self._batch_render_timer.setSingleShot(True)
            self._batch_render_timer.timeout.connect(self._render_next_batch)
            self._batch_render_timer.start(0)  # 下一帧开始

    def _group_messages_into_turns(self, messages: list) -> list:
        """将消息列表分组为逻辑轮次
        
        返回: list of (start_idx, end_idx) 元组
        """
        groups: list = []
        i = 0
        while i < len(messages):
            msg = messages[i]
            role = msg.get('role', '')

            if role == 'user':
                groups.append((i, i + 1))
                i += 1
            elif role == 'assistant':
                if msg.get('tool_calls'):
                    # 收集工具交互轮次
                    j = i + 1
                    while j < len(messages):
                        m = messages[j]
                        r = m.get('role', '')
                        if r == 'tool':
                            j += 1
                        elif r == 'assistant':
                            j += 1
                            if not m.get('tool_calls'):
                                break
                        else:
                            break
                    groups.append((i, j))
                    i = j
                else:
                    # 普通 assistant + 后续 tool 消息
                    j = i + 1
                    while j < len(messages) and messages[j].get('role') == 'tool':
                        j += 1
                    groups.append((i, j))
                    i = j
            elif role == 'system':
                groups.append((i, i + 1))
                i += 1
            else:
                groups.append((i, i + 1))
                i += 1
        return groups

    def _render_message_groups(self, groups: list, start: int, end: int):
        """渲染 [start, end) 范围内的消息组"""
        messages = self._conversation_history
        for gi in range(start, end):
            si, ei = groups[gi]
            try:
                self._render_single_group(messages, si, ei)
            except Exception:
                import traceback
                traceback.print_exc()

    def _render_single_group(self, messages: list, si: int, ei: int):
        """渲染一个消息组"""
        msg = messages[si]
        role = msg.get('role', '')
        raw_content = msg.get('content', '') or ''
        if isinstance(raw_content, list):
            content = '\n'.join(
                part.get('text', '') for part in raw_content
                if isinstance(part, dict) and part.get('type') == 'text'
            )
        else:
            content = raw_content

        if role == 'user':
            self._render_user_history(content)

        elif role == 'assistant':
            if msg.get('tool_calls'):
                turn_msgs = messages[si:ei]
                self._render_native_tool_turn(turn_msgs)
            else:
                tool_msgs = [messages[j] for j in range(si + 1, ei)
                             if messages[j].get('role') == 'tool']

                if content.lstrip().startswith('[工具执行结果]'):
                    self._render_tool_summary_history(content, msg)
                else:
                    response = self._add_ai_response()
                    thinking = msg.get('thinking', '')
                    if thinking:
                        response.add_thinking(thinking)
                        response.thinking_section.finalize()
                    self._render_old_tool_msgs(response, tool_msgs)
                    self._restore_shell_widgets(response, msg)
                    response.set_content(content)
                    response.status_label.setText("历史")
                    response.finalize()
                    parts = []
                    if thinking:
                        parts.append("思考")
                    if tool_msgs:
                        parts.append(f"{len(tool_msgs)}次调用")
                    label = f"历史 | {', '.join(parts)}" if parts else "历史"
                    response.status_label.setText(label)

        elif role == 'system' and '[历史对话摘要' in content:
            response = self._add_ai_response()
            response.add_collapsible("历史对话摘要", content)
            response.status_label.setText("历史摘要")
            response.finalize()
            response.status_label.setText("历史摘要")

    def _render_next_batch(self):
        """分批渲染回调 — 渲染下一批早期消息（从后向前，插入到布局头部）"""
        if not hasattr(self, '_batch_groups') or not self._batch_groups:
            return
        if self._batch_cursor <= 0:
            # 全部渲染完毕，移除占位符
            self._finish_batch_render()
            return

        batch_start = max(0, self._batch_cursor - self._BATCH_SIZE)
        batch_end = self._batch_cursor
        start_time = time.time()

        # ★ 早期消息需要插入到占位符之前（即布局的第 0 个位置开始）
        # 我们从 batch_start 到 batch_end 按顺序渲染，每个 widget 插入到
        # 占位符位置之前（insert_pos 递增）
        messages = self._conversation_history
        insert_pos = self._batch_insert_pos  # 在此位置之前插入
        rendered_count = 0

        for gi in range(batch_start, batch_end):
            si, ei = self._batch_groups[gi]
            try:
                widgets_before = self.chat_layout.count()
                self._render_single_group(messages, si, ei)
                widgets_after = self.chat_layout.count()
                added = widgets_after - widgets_before

                # 将新添加的 widget 移动到正确位置（占位符之前）
                if added > 0:
                    for _ in range(added):
                        # 取出最后添加的 widget（在 stretch 之前）
                        from_idx = self.chat_layout.count() - 2  # -1 是 stretch, -2 是新 widget
                        item = self.chat_layout.takeAt(from_idx)
                        if item and item.widget():
                            self.chat_layout.insertWidget(insert_pos, item.widget())
                            insert_pos += 1
                    rendered_count += added
            except Exception:
                import traceback
                traceback.print_exc()

            # 时间预算检查
            elapsed_ms = (time.time() - start_time) * 1000
            if elapsed_ms > self._BATCH_BUDGET_MS and gi < batch_end - 1:
                self._batch_cursor = gi + 1
                self._batch_insert_pos = insert_pos
                remaining = gi + 1
                if hasattr(self, '_batch_placeholder') and self._batch_placeholder:
                    try:
                        self._batch_placeholder.setText(
                            f"⏳ 加载历史消息 ({remaining} 轮)..."
                        )
                    except RuntimeError:
                        pass
                self._batch_render_timer.start(0)
                return

        self._batch_cursor = batch_start
        self._batch_insert_pos = insert_pos

        if self._batch_cursor > 0:
            if hasattr(self, '_batch_placeholder') and self._batch_placeholder:
                try:
                    self._batch_placeholder.setText(
                        f"⏳ 加载历史消息 ({self._batch_cursor} 轮)..."
                    )
                except RuntimeError:
                    pass
            self._batch_render_timer.start(0)
        else:
            self._finish_batch_render()

    def _finish_batch_render(self):
        """完成分批渲染，清理占位符"""
        if hasattr(self, '_batch_placeholder') and self._batch_placeholder:
            try:
                self._batch_placeholder.setVisible(False)
                self._batch_placeholder.deleteLater()
            except RuntimeError:
                pass
            self._batch_placeholder = None
        self._batch_groups = None
        self._batch_render_timer = None

    # ------------------------------------------------------------------
    def _replay_todo_from_tool_call(self, tool_name: str, arguments_str: str):
        """从历史工具调用中恢复 todo 项（不显示在 UI 执行列表中）
        
        注意：todo 数据现在通过 todo_data 字段在缓存中保存/恢复，
        此方法仅作为兼容旧缓存的后备方案。
        """
        try:
            if isinstance(arguments_str, str) and arguments_str:
                args = json.loads(arguments_str)
            elif isinstance(arguments_str, dict):
                args = arguments_str
            else:
                return
            if tool_name == 'add_todo':
                tid = args.get('todo_id', '')
                text = args.get('text', '')
                status = args.get('status', 'pending')
                if tid and text and hasattr(self, 'todo_list') and self.todo_list:
                    self.todo_list.add_todo(tid, text, status)
                    self._ensure_todo_in_chat(self.todo_list, self.chat_layout)
            elif tool_name == 'update_todo':
                tid = args.get('todo_id', '')
                status = args.get('status', 'done')
                if tid and hasattr(self, 'todo_list') and self.todo_list:
                    self.todo_list.update_todo(tid, status)
        except Exception:
            pass  # 解析失败忽略

    # ------------------------------------------------------------------
    def _render_native_tool_turn(self, turn_msgs: list):
        """渲染 Cursor 风格原生工具调用轮次
        
        turn_msgs 格式：
          assistant(tool_calls) → tool → [assistant(tool_calls) → tool →] ... → assistant(reply)
        静默工具（add_todo/update_todo）不显示在执行列表中，但会恢复 todo 数据。
        """
        response = self._add_ai_response()
        tool_count = 0
        final_content = ''
        thinking = ''
        final_msg = {}
        
        for m in turn_msgs:
            r = m.get('role', '')
            if r == 'assistant':
                tc_list = m.get('tool_calls', [])
                if tc_list:
                    # 工具调用 assistant 消息：注册每个工具调用
                    for tc in tc_list:
                        fn = tc.get('function', {})
                        name = fn.get('name', 'unknown')
                        # 静默工具：恢复 todo 但不显示在执行列表
                        if name in self._SILENT_TOOLS:
                            self._replay_todo_from_tool_call(name, fn.get('arguments', ''))
                            continue
                        response.add_status(f"[tool]{name}")
                        tool_count += 1
                else:
                    # 最终回复 assistant 消息
                    final_content = m.get('content', '') or ''
                    thinking = m.get('thinking', '')
                    final_msg = m
            elif r == 'tool':
                tc_id = m.get('tool_call_id', '')
                t_content = m.get('content', '') or ''
                # 从 tool_call_id 查找对应的工具名
                t_name = self._find_tool_name_by_id(turn_msgs, tc_id) or 'tool'
                # 静默工具的结果也不显示
                if t_name in self._SILENT_TOOLS:
                    continue
                success = not t_content.lstrip().startswith('[err]') and 'error' not in t_content[:50].lower()
                prefix = "[ok] " if success else "[err] "
                response.add_tool_result(t_name, f"{prefix}{t_content}")
        
        # 恢复 thinking
        if thinking:
            response.add_thinking(thinking)
            response.thinking_section.finalize()
        
        # 恢复 Shell 折叠面板
        self._restore_shell_widgets(response, final_msg)
        
        # AI 回复内容
        if final_content:
            response.set_content(final_content)
        
        # 状态标签
        parts = []
        if thinking:
            parts.append("思考")
        if tool_count > 0:
            parts.append(f"{tool_count}次调用")
        label = f"历史 | {', '.join(parts)}" if parts else "历史"
        response.status_label.setText(label)
        response.finalize()
        response.status_label.setText(label)

    @staticmethod
    def _find_tool_name_by_id(messages: list, tool_call_id: str) -> str:
        """从消息列表中根据 tool_call_id 查找对应的工具名"""
        if not tool_call_id:
            return ''
        for m in messages:
            if m.get('role') == 'assistant':
                for tc in m.get('tool_calls', []):
                    if tc.get('id') == tool_call_id:
                        return tc.get('function', {}).get('name', '')
        return ''

    # ------------------------------------------------------------------
    def _render_user_history(self, content: str):
        """渲染用户历史消息，长上下文自动折叠"""
        # 检查是否包含 [Network structure] 等上下文注入
        split_pos = -1
        header_tag = ''
        for tag in self._CONTEXT_HEADERS:
            pos = content.find(tag)
            if pos != -1:
                split_pos = pos
                header_tag = tag
                break

        if split_pos > 0 and len(content) > 300:
            # 用户实际输入 + 上下文注入
            user_text = content[:split_pos].strip()
            context_data = content[split_pos:]
            # 显示用户实际文字
            if user_text:
                self._add_user_message(user_text)
            # 上下文放进折叠区域
            resp = self._add_ai_response()
            resp.add_collapsible(header_tag.strip('[]'), context_data)
            resp.status_label.setText("上下文")
            resp.finalize()
            resp.status_label.setText("上下文")
        elif split_pos == 0 and len(content) > 300:
            # 纯上下文（无用户文字），整块折叠
            resp = self._add_ai_response()
            resp.add_collapsible(header_tag.strip('[]'), content)
            resp.status_label.setText("上下文")
            resp.finalize()
            resp.status_label.setText("上下文")
        else:
            self._add_user_message(content)

    # ------------------------------------------------------------------
    _TOOL_LINE_PREFIXES = ('[ok] ', '[err] ', '\u2705 ', '\u274c ')

    def _render_tool_summary_history(self, content: str, msg: dict = None):
        """渲染 [工具执行结果] 格式的 assistant 消息

        格式示例：
          [工具执行结果]
          [ok] get_network_structure: ## 网络结构: /obj
          网络类型: obj          ← 上一条的续行
          节点数量: 0            ← 上一条的续行
          [ok] create_node: /obj/geo1
        """
        if msg is None:
            msg = {}
        response = self._add_ai_response()

        # 先按行分组：以 [ok]/[err]/✅/❌ 开头的行开始新条目，
        # 其他行归到前一条目的续行
        entries = []  # [(first_line, [continuation_lines])]
        for line in content.split('\n'):
            stripped = line.strip()
            if not stripped or stripped == '[工具执行结果]':
                # 空行或标题→如果有上一条目，添加空行到续行（保留格式）
                if entries:
                    entries[-1][1].append('')
                continue
            is_new_entry = any(stripped.startswith(p) for p in self._TOOL_LINE_PREFIXES)
            if is_new_entry:
                entries.append((stripped, []))
            elif entries:
                entries[-1][1].append(stripped)
            # else: 没有前导条目的散行，忽略

        tool_count = 0
        for first_line, cont_lines in entries:
            t_name = 'unknown'
            success = True
            # 解析前缀
            rest = first_line
            for prefix in self._TOOL_LINE_PREFIXES:
                if first_line.startswith(prefix):
                    if 'err' in prefix or '\u274c' in prefix:
                        success = False
                    rest = first_line[len(prefix):]
                    break
            # 解析 tool_name: result
            if ':' in rest:
                parts = rest.split(':', 1)
                t_name = parts[0].strip()
                first_result = parts[1].strip() if len(parts) > 1 else ''
            else:
                first_result = rest

            # 合并续行
            all_parts = [first_result] + cont_lines
            t_result = '\n'.join(all_parts).strip()

            # 静默工具不显示在执行列表
            if t_name in self._SILENT_TOOLS:
                continue
            # 注册工具 + 设置结果
            response.add_status(f"[tool]{t_name}")
            tool_count += 1
            result_prefix = "[ok] " if success else "[err] "
            response.add_tool_result(t_name, f"{result_prefix}{t_result}")

        # 恢复 Shell 折叠面板
        self._restore_shell_widgets(response, msg)

        # 恢复 thinking
        thinking = msg.get('thinking', '')
        if thinking:
            response.add_thinking(thinking)
            response.thinking_section.finalize()

        # 恢复正文（[工具执行结果]之后可能还有 AI 正式回复）
        # 找到工具摘要之后的正文部分
        text_after_tools = ''
        parts = content.split('\n\n')
        for idx_p, part in enumerate(parts):
            if not part.strip().startswith('[工具执行结果]') and not any(
                part.strip().startswith(p) for p in self._TOOL_LINE_PREFIXES
            ):
                # 检查是否整段都是工具结果行
                is_tool_block = all(
                    any(line.strip().startswith(p) for p in self._TOOL_LINE_PREFIXES)
                    or not line.strip()
                    or line.strip() == '[工具执行结果]'
                    for line in part.split('\n')
                )
                if not is_tool_block and part.strip():
                    text_after_tools = '\n\n'.join(parts[idx_p:])
                    break
        if text_after_tools:
            response.set_content(text_after_tools)

        label_parts = []
        if thinking:
            label_parts.append("思考")
        label_parts.append(f"{tool_count}次调用")
        response.status_label.setText(f"历史 | {', '.join(label_parts)}")
        response.finalize()
        response.status_label.setText(f"历史 | {', '.join(label_parts)}")

    # ------------------------------------------------------------------
    def _restore_shell_widgets(self, response, msg: dict):
        """从历史消息中恢复 Python Shell / System Shell 折叠面板"""
        # 恢复 Python Shell
        for ps in msg.get('python_shells', []):
            code = ps.get('code', '')
            raw_output = ps.get('output', '')
            error = ps.get('error', '')
            success = ps.get('success', True)
            # 提取执行时间（和 _on_add_python_shell 相同逻辑）
            exec_time = 0.0
            clean_parts = []
            for line in raw_output.split('\n'):
                time_match = re.match(r'^执行时间:\s*([\d.]+)s$', line.strip())
                if time_match:
                    exec_time = float(time_match.group(1))
                    continue
                if line.strip() == '输出:':
                    continue
                clean_parts.append(line)
            clean_output = '\n'.join(clean_parts).strip()
            widget = PythonShellWidget(
                code=code, output=clean_output, error=error,
                exec_time=exec_time, success=success, parent=response
            )
            response.add_shell_widget(widget)

        # 恢复 System Shell
        for ss in msg.get('system_shells', []):
            command = ss.get('command', '')
            raw_output = ss.get('output', '')
            error = ss.get('error', '')
            success = ss.get('success', True)
            cwd = ss.get('cwd', '')
            exec_time = 0.0
            exit_code = 0
            stdout_parts = []
            for line in raw_output.split('\n'):
                tm = re.search(r'耗时:\s*([\d.]+)s', line)
                cm = re.search(r'退出码:\s*(\d+)', line)
                if tm:
                    exec_time = float(tm.group(1))
                if cm:
                    exit_code = int(cm.group(1))
                if tm or cm:
                    continue
                if line.strip() in ('--- stdout ---', '--- stderr ---'):
                    continue
                stdout_parts.append(line)
            clean_output = '\n'.join(stdout_parts).strip()
            widget = SystemShellWidget(
                command=command, output=clean_output, error=error,
                exit_code=exit_code, exec_time=exec_time,
                success=success, cwd=cwd, parent=response
            )
            response.add_sys_shell_widget(widget)

    # ------------------------------------------------------------------
    def _render_old_tool_msgs(self, response, tool_msgs: list):
        """渲染旧格式 role=tool 消息到 AIResponse"""
        for tm in tool_msgs:
            t_name = tm.get('name', 'unknown')
            t_content = tm.get('content', '')
            # 解析 tool_name:result_text
            if ':' in t_content:
                parts = t_content.split(':', 1)
                t_name = parts[0].strip() or t_name
                t_result = parts[1].strip() if len(parts) > 1 else t_content
            else:
                t_result = t_content
            # 静默工具不显示在执行列表
            if t_name in self._SILENT_TOOLS:
                continue
            success = not t_result.startswith('[err]') and not t_result.startswith('\u274c')
            # 先注册工具调用
            response.add_status(f"[tool]{t_name}")
            result_prefix = "[ok] " if success else "[err] "

            response.add_tool_result(t_name, f"{result_prefix}{t_result}")

    # ===== Token 优化管理 =====
    
    def _on_optimize_menu(self):
        """显示 Token 优化菜单"""
        menu = QtWidgets.QMenu(self)
        
        # 立即优化
        optimize_now_action = menu.addAction("立即压缩对话")
        optimize_now_action.triggered.connect(self._optimize_now)
        
        menu.addSeparator()
        
        # 自动优化开关
        auto_label = "自动压缩 [on]" if self._auto_optimize else "自动压缩"
        auto_opt_action = menu.addAction(auto_label)
        auto_opt_action.setCheckable(True)
        auto_opt_action.setChecked(self._auto_optimize)
        auto_opt_action.triggered.connect(lambda: setattr(self, '_auto_optimize', not self._auto_optimize))
        
        menu.addSeparator()
        
        # 压缩策略
        strategy_menu = menu.addMenu("压缩策略")
        for label, strat in [
            ("激进 (最大节省)", CompressionStrategy.AGGRESSIVE),
            ("平衡 (推荐)", CompressionStrategy.BALANCED),
            ("保守 (保留细节)", CompressionStrategy.CONSERVATIVE),
        ]:
            action = strategy_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(self._optimization_strategy == strat)
            action.triggered.connect(lambda _, s=strat: setattr(self, '_optimization_strategy', s))
        
        # 显示菜单
        menu.exec_(self.btn_optimize.mapToGlobal(QtCore.QPoint(0, self.btn_optimize.height())))
    
    def _optimize_now(self):
        """立即优化当前对话"""
        if len(self._conversation_history) <= 4:
            QtWidgets.QMessageBox.information(self, "提示", "对话历史太短，无需优化")
            return
        
        # 计算优化前
        before_tokens = self._calculate_context_tokens()
        
        # 执行优化
        compressed_messages, stats = self.token_optimizer.compress_messages(
            self._conversation_history,
            strategy=self._optimization_strategy
        )
        
        if stats['saved_tokens'] > 0:
            self._conversation_history = compressed_messages
            self._context_summary = compressed_messages[0].get('content', '') if compressed_messages and compressed_messages[0].get('role') == 'system' else self._context_summary
            
            # 重新渲染
            self._render_conversation_history()
            
            # 更新统计
            self._update_context_stats()
            
            # 显示结果
            saved_percent = stats.get('saved_percent', 0)
            QtWidgets.QMessageBox.information(
                self, "优化完成",
                f"对话已优化！\n\n"
                f"原始: ~{before_tokens:,} tokens\n"
                f"优化后: ~{stats['compressed_tokens']:,} tokens\n"
                f"节省: ~{stats['saved_tokens']:,} tokens ({saved_percent:.1f}%)\n\n"
                f"压缩了 {stats['compressed']} 条消息，保留 {stats['kept']} 条"
            )
        else:
            QtWidgets.QMessageBox.information(self, "提示", "无需优化，对话历史已经很精简")

    # ============================================================
    # 自动更新
    # ============================================================

    _updateCheckDone = QtCore.Signal(dict)   # 检查结果
    _updateApplyDone = QtCore.Signal(dict)   # 应用结果
    _updateProgress = QtCore.Signal(str, int)  # (stage, percent)

    def _silent_update_check(self):
        """启动时静默检查更新（不弹窗，只在有更新时高亮按钮）"""
        try:
            self._updateCheckDone.connect(self._on_silent_check_result, QtCore.Qt.UniqueConnection)
        except RuntimeError:
            pass
        threading.Thread(target=self._bg_check_update, daemon=True).start()

    @QtCore.Slot(dict)
    def _on_silent_check_result(self, result: dict):
        """[主线程] 静默检查结果 → 如果有更新，高亮 Update 按钮"""
        # 断开静默回调，防止和手动点击冲突
        try:
            self._updateCheckDone.disconnect(self._on_silent_check_result)
        except RuntimeError:
            pass
        
        if result.get('has_update') and result.get('remote_version'):
            remote_ver = result['remote_version']
            # 用醒目样式标记按钮
            self.btn_update.setText(f"🔄 v{remote_ver}")
            self.btn_update.setToolTip(f"发现新版本 v{remote_ver}，点击更新")
            self.btn_update.setProperty("state", "available")
            self.btn_update.style().unpolish(self.btn_update)
            self.btn_update.style().polish(self.btn_update)
            # 保存检查结果，供手动点击时直接使用
            self._cached_update_result = result

    def _on_check_update(self):
        """点击 Update 按钮 → 后台检查更新（如果有缓存结果直接使用）"""
        # 如果启动时已检测到新版本，直接显示结果
        cached = getattr(self, '_cached_update_result', None)
        if cached and cached.get('has_update'):
            self._on_update_check_result(cached)
            self._cached_update_result = None  # 用完清除
            return
        
        self.btn_update.setEnabled(False)
        self.btn_update.setText("检查中…")
        
        # 连接信号（只连一次，用 UniqueConnection 防重复）
        try:
            self._updateCheckDone.connect(self._on_update_check_result, QtCore.Qt.UniqueConnection)
        except RuntimeError:
            pass
        
        threading.Thread(target=self._bg_check_update, daemon=True).start()

    def _bg_check_update(self):
        """[后台线程] 调用 updater.check_update"""
        try:
            from ..utils.updater import check_update
            result = check_update()
        except Exception as e:
            result = {'has_update': False, 'error': str(e), 'local_version': '?', 'remote_version': ''}
        self._updateCheckDone.emit(result)

    @QtCore.Slot(dict)
    def _on_update_check_result(self, result: dict):
        """[主线程] 处理检查结果"""
        self.btn_update.setEnabled(True)
        self.btn_update.setText("Update")
        self.btn_update.setProperty("state", "")  # 恢复默认样式
        self.btn_update.style().unpolish(self.btn_update)
        self.btn_update.style().polish(self.btn_update)
        
        if result.get('error'):
            QtWidgets.QMessageBox.warning(self, "检查更新", f"检查更新失败:\n{result['error']}")
            return
        
        local_ver = result.get('local_version', '?')
        remote_ver = result.get('remote_version', '?')
        release_name = result.get('release_name', '')
        release_notes = result.get('release_notes', '')
        
        if not result.get('has_update'):
            QtWidgets.QMessageBox.information(
                self, "检查更新",
                f"当前已是最新版本 ✓\n\n"
                f"本地版本: v{local_ver}\n"
                f"最新 Release: v{remote_ver}"
            )
            return
        
        # ---- 有新版本，弹出确认对话框 ----
        detail = f"本地版本: v{local_ver}\n最新 Release: v{remote_ver}"
        if release_name:
            detail += f"\n版本名称: {release_name}"
        if release_notes:
            detail += f"\n更新说明: {release_notes}"
        detail += "\n\n⚠️ 更新后插件窗口将自动重启。\n（config、cache、trainData 目录不会被覆盖）"
        
        reply = QtWidgets.QMessageBox.question(
            self, "发现新版本",
            f"发现新版本 v{remote_ver}，是否立即更新？\n\n{detail}",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Cancel,
        )
        
        if reply == QtWidgets.QMessageBox.Yes:
            self._start_update()

    def _start_update(self):
        """开始下载并应用更新"""
        # 创建进度对话框
        self._update_progress_dlg = QtWidgets.QProgressDialog(
            "正在下载更新…", "取消", 0, 100, self
        )
        self._update_progress_dlg.setWindowTitle("更新 Houdini Agent")
        self._update_progress_dlg.setWindowModality(QtCore.Qt.WindowModal)
        self._update_progress_dlg.setAutoClose(False)
        self._update_progress_dlg.setAutoReset(False)
        self._update_progress_dlg.setMinimumDuration(0)
        self._update_progress_dlg.setValue(0)
        # QProgressDialog / QProgressBar 样式由全局 QSS 控制
        
        # 连接信号
        try:
            self._updateProgress.connect(self._on_update_progress, QtCore.Qt.UniqueConnection)
            self._updateApplyDone.connect(self._on_update_apply_result, QtCore.Qt.UniqueConnection)
        except RuntimeError:
            pass
        
        threading.Thread(target=self._bg_download_and_apply, daemon=True).start()

    def _bg_download_and_apply(self):
        """[后台线程] 下载并应用更新"""
        try:
            from ..utils.updater import download_and_apply
            result = download_and_apply(progress_callback=self._update_progress_cb)
        except Exception as e:
            result = {'success': False, 'error': str(e), 'updated_files': 0}
        self._updateApplyDone.emit(result)

    def _update_progress_cb(self, stage: str, percent: int):
        """进度回调（从后台线程调用 → 通过信号到主线程）"""
        self._updateProgress.emit(stage, percent)

    @QtCore.Slot(str, int)
    def _on_update_progress(self, stage: str, percent: int):
        """[主线程] 更新进度条"""
        if not hasattr(self, '_update_progress_dlg') or self._update_progress_dlg is None:
            return
        
        stage_labels = {
            'downloading': '正在下载…',
            'extracting': '正在解压…',
            'applying': '正在更新文件…',
            'done': '更新完成！',
        }
        label = stage_labels.get(stage, stage)
        self._update_progress_dlg.setLabelText(f"{label} ({percent}%)")
        self._update_progress_dlg.setValue(percent)

    @QtCore.Slot(dict)
    def _on_update_apply_result(self, result: dict):
        """[主线程] 更新完成后的处理"""
        # 关闭进度条
        if hasattr(self, '_update_progress_dlg') and self._update_progress_dlg:
            self._update_progress_dlg.close()
            self._update_progress_dlg = None
        
        if not result.get('success'):
            QtWidgets.QMessageBox.critical(
                self, "更新失败",
                f"更新过程中出现错误:\n{result.get('error', '未知错误')}"
            )
            return
        
        updated = result.get('updated_files', 0)
        
        # 更新成功 → 提示并重启
        reply = QtWidgets.QMessageBox.information(
            self, "更新成功",
            f"已成功更新 {updated} 个文件！\n\n点击 OK 立即重启插件。",
            QtWidgets.QMessageBox.Ok,
        )
        
        # 延迟重启（让对话框关闭后再执行）
        QtCore.QTimer.singleShot(200, self._do_restart)

    def _do_restart(self):
        """执行插件重启"""
        try:
            # 先保存当前工作区
            main_win = self.window()
            if hasattr(main_win, '_save_workspace'):
                main_win._save_workspace()
            
            # 关闭当前窗口
            main_win.force_quit = True
            main_win.close()
            
            # 延迟重新打开（让窗口完全关闭后再重建）
            # 注意：使用绝对导入的函数引用，避免模块被清除后相对导入失败
            from houdini_agent.utils.updater import restart_plugin as _restart_fn
            QtCore.QTimer.singleShot(500, _restart_fn)
        except Exception as e:
            print(f"[Updater] Restart error: {e}")
            QtWidgets.QMessageBox.warning(
                self, "重启失败",
                f"自动重启失败，请手动关闭并重新打开插件。\n\n错误: {e}"
            )
    
