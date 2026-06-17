# -*- coding: utf-8 -*-
"""
QML bridge for the Houdini Agent UI.

ChatModel  — QAbstractListModel exposed to the ListView (rows: user / ai / plan).
Controller — QObject exposed as `controller`. Owns UI state + drives the REAL
             agent loop via AgentSession (AIClient.agent_loop_auto on a background
             thread). All Houdini tool calls are marshalled to the Qt main thread
             through a BlockingQueuedConnection signal (mirrors the old RunMixin).

If the backend can't be constructed (e.g. running the standalone preview without
Houdini/API keys), Controller falls back to a simulated streamed reply.
"""

import base64
import copy
import json
import os
import queue
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path

try:
    from PySide6.QtCore import (
        QAbstractListModel, QModelIndex, QObject, Qt, Signal, Slot, Property, QTimer, QSettings
    )
    import random
except ImportError:  # Houdini <= 20.5 (Qt5)
    from PySide2.QtCore import (
        QAbstractListModel, QModelIndex, QObject, Qt, Signal, Slot, Property, QTimer, QSettings
    )
    import random


ROLE_TYPE = Qt.UserRole + 1
ROLE_PAYLOAD = Qt.UserRole + 2

# Real provider keys + model ids (kept in sync with ui/header.py _model_map)
MODEL_MAP = {
    "duojie": ["claude-opus-4-6-max", "claude-opus-4-6-gemini", "claude-sonnet-4-6",
               "claude-sonnet-4-5", "gemini-3.1-pro", "gemini-3-flash", "glm-5.1", "MiniMax-M2.7"],
    "openrouter": ["anthropic/claude-opus-4.6", "anthropic/claude-sonnet-4.6",
                   "anthropic/claude-haiku-4.5", "openai/gpt-5.2", "google/gemini-3-flash-preview",
                   "deepseek/deepseek-v3.2", "x-ai/grok-4.1-fast"],
    "openai": ["gpt-5.2", "gpt-5.3-codex"],
    "deepseek": ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat", "deepseek-reasoner"],
    "glm": ["glm-4.7"],
    "custom": [],
}
PROVIDER_LABELS = {
    "duojie": "Duojie", "openrouter": "OpenRouter", "openai": "OpenAI",
    "deepseek": "DeepSeek", "glm": "GLM", "custom": "Custom",
}
CONTEXT_LIMITS = {
    "claude-opus-4-6-max": 200000, "claude-opus-4-6-gemini": 200000,
    "claude-sonnet-4-6": 200000, "claude-sonnet-4-5": 200000,
    "gemini-3.1-pro": 1048576, "gemini-3-flash": 1048576, "glm-5.1": 200000, "MiniMax-M2.7": 128000,
    "anthropic/claude-opus-4.6": 1000000, "anthropic/claude-sonnet-4.6": 1000000,
    "anthropic/claude-haiku-4.5": 200000, "openai/gpt-5.2": 400000,
    "google/gemini-3-flash-preview": 1048576, "deepseek/deepseek-v3.2": 163840,
    "x-ai/grok-4.1-fast": 2000000, "gpt-5.2": 128000, "gpt-5.3-codex": 200000,
    "deepseek-v4-flash": 1048576, "deepseek-v4-pro": 1048576, "deepseek-chat": 1048576,
    "deepseek-reasoner": 1048576, "glm-4.7": 200000,
}
VISION_MODELS = {
    "claude-opus-4-6-max", "claude-opus-4-6-gemini", "claude-sonnet-4-6",
    "claude-sonnet-4-5", "gemini-3.1-pro", "gemini-3-flash",
    "anthropic/claude-opus-4.6", "anthropic/claude-sonnet-4.6",
    "anthropic/claude-haiku-4.5", "openai/gpt-5.2",
    "google/gemini-3-flash-preview", "x-ai/grok-4.1-fast",
    "gpt-5.2", "gpt-5.3-codex",
}
# tools safe to run off the Qt main thread (no hou.* access)
BG_SAFE = {"web_search", "fetch_webpage", "search_local_doc", "get_houdini_node_doc",
           "list_skills", "search_memory", "execute_shell"}

# read-only tools available during the Plan planning phase
PLAN_READONLY = {"get_network_structure", "get_node_parameters", "list_children",
                 "get_node_inputs", "search_node_types", "semantic_search_nodes",
                 "read_selection", "check_errors", "search_local_doc",
                 "get_houdini_node_doc", "web_search", "fetch_webpage",
                 "search_memory", "list_skills", "capture_viewport"}

try:
    from houdini_agent.utils.plan_manager import (
        PLAN_TOOL_CREATE, PLAN_TOOL_UPDATE_STEP, PLAN_TOOL_ASK_QUESTION)
except Exception:
    PLAN_TOOL_CREATE = PLAN_TOOL_UPDATE_STEP = PLAN_TOOL_ASK_QUESTION = None

# tools that require user approval when confirm mode is on
CONFIRM_TOOLS = {"create_node", "create_nodes_batch", "create_wrangle_node", "delete_node",
                 "set_node_parameter", "batch_set_parameters", "connect_nodes", "copy_node",
                 "execute_python", "execute_shell", "save_hip", "run_skill"}

# Meshy 集成（自包含包，import 即自注册到 ToolRegistry）。
# 网络工具在 app 侧后台线程执行；生成类工具消耗 credits，纳入确认门。
try:
    from houdini_agent import meshy as _meshy
    MESHY_NETWORK_TOOLS = set(_meshy.NETWORK_TOOLS)
    MESHY_INTERACTIVE_TOOLS = set(_meshy.INTERACTIVE_TOOLS)
    MESHY_BATCH_TOOLS = set(_meshy.BATCH_TOOLS)
    MESHY_MUTATING = set(_meshy.MUTATING_TOOLS)
    CONFIRM_TOOLS = CONFIRM_TOOLS | set(_meshy.CONFIRM_TOOLS)
except Exception as _meshy_err:
    _meshy = None
    MESHY_NETWORK_TOOLS = set()
    MESHY_INTERACTIVE_TOOLS = set()
    MESHY_BATCH_TOOLS = set()
    MESHY_MUTATING = set()
    print("[controller] meshy integration unavailable:", _meshy_err)

# 各 Meshy 工具的中文标签（卡片标题、后台任务通知用）
MESHY_LABELS = {
    "meshy_text_to_3d": "文生3D", "meshy_image_to_3d": "图生3D",
    "meshy_text_to_image": "文生图", "meshy_image_to_image": "图生图",
    "meshy_retexture": "重打材质", "meshy_remesh": "重拓扑",
    "meshy_concept_to_3d": "概念图转3D",
}

# QML UI strings — Chinese -> English (used by Controller.tr when lang == 'en')
UI_EN = {
    "描述你想在场景里做的事…  (Enter 发送)": "Describe what to build in the scene…  (Enter to send)",
    "问一个关于场景的问题…  (只读模式)": "Ask about the scene…  (read-only)",
    "描述目标，先生成可确认的执行计划…": "Describe the goal — a plan will be proposed for approval…",
    "驳回": "Reject", "确认执行": "Confirm", "取消": "Cancel", "提交": "Submit",
    "执行前确认": "Confirm before run", "已确认": "Confirmed", "已取消": "Cancelled",
    "已回答": "Answered", "需要你的确认": "Your input needed",
    "计划已确认 · 开始执行": "Plan confirmed · executing", "计划已驳回": "Plan rejected",
    "导出对话": "Export chat", "缓存位置": "Cache location", "检查更新": "Check update",
    "Meshy 资产库": "Meshy Library", "刷新": "Refresh", "加载更多": "Load more",
    "导入": "Import", "在 Meshy 打开": "Open in Meshy", "已过期": "Expired",
    "已缓存": "Cached", "暂无资产": "No assets yet", "加载中…": "Loading…",
    "请先配置 Meshy API Key": "Set a Meshy API Key first",
    "未配置 Meshy API Key": "No Meshy API Key configured",
    "Meshy 集成不可用": "Meshy integration unavailable",
    "资产库加载失败": "Failed to load library", "没有更多了": "No more items",
    "正在导入": "Importing", "已导入": "Imported", "找不到该资产": "Asset not found",
    "导入失败": "Import failed", "未获得 glb（云端链接可能已过期）": "No glb (cloud link may have expired)",
    "云端 · 可导入": "Cloud · importable", "云资产 · 直接拉到 Houdini": "Cloud assets · pull into Houdini",
    "已连接": "Connected", "未连接": "Not connected", "余额": "Balance",
    "登录 / 配置 Key": "Sign in / set key", "切换账号": "Switch account", "退出": "Sign out",
    "登录 Meshy 同步你的资产与额度": "Sign in to Meshy to sync your assets & credits",
    "Meshy 账号校验失败": "Meshy account check failed", "已退出 Meshy 账号": "Signed out of Meshy",
    "已连接 Meshy 账号。": "Connected to Meshy.", "保存 Meshy Key 失败": "Failed to save Meshy key",
    "下载中…": "Downloading…", "导入中…": "Importing…",
    "提示词（重生留空=沿用原词；二次编辑必填改动）": "Prompt (regenerate: blank=keep; edit: required)",
    "描述想要的改动…": "Describe the change you want…",
    "二次编辑选中图": "Edit selected (img2img)", "请先选中要编辑的图片": "Select an image to edit first",
    "请先在上方填写想要的改动": "Type the change above first",
    # 概念图/图片画廊（旧+新）
    "资产库": "Library", "概念图": "Concepts", "已选": "selected", "生成中…": "Generating…",
    "完成": "Done", "确认": "Confirm", "换提示词重新生成": "Regenerate (new prompt)",
    "生成选中的 3D": "Make 3D from selected", "选中的做成 3D": "Selected → 3D",
    "请先选择至少一张概念图": "Select at least one image first", "重载": "Reload",
    # 自定义 Provider · 图片输入开关
    "图片输入": "Image input", "支持": "Supported", "不支持": "Not supported",
    # 后台任务
    "转入后台": "Run in background", "已转入后台，完成后自动通知": "Moved to background; you'll be notified when done",
    "后台任务完成": "Background task done", "耗时较久？可转后台，完成后自动通知": "Taking a while? Run in background — auto-notified when done",
    "耗时较久？可转后台": "Taking a while? Run in background", "已转入后台，生成完成后把结果发给 Agent…": "Moved to background; results will be sent to the agent when ready…",
    "字号 +": "Font +", "字号 −": "Font −", "规则编辑器": "Rules editor",
    "插件管理": "Plugins", "记忆管理": "Memory manager", "长期记忆": "Long-term memory",
    "实时 Cook": "Realtime cook", "语言：中文": "Language: 中文",
    "语言：English": "Language: English", "清空对话": "Clear chat", "新对话": "New chat",
    "Token 分析": "Token analytics", "Token 用量": "Token usage",
    " 个操作待确认": " ops pending", "全部撤销": "Undo all", "全部保留": "Keep all",
    "选择图片": "Choose image", "自定义 Provider…": "Custom provider…",
    "压缩上下文": "Compress context", "已压缩上下文": "Context compressed",
    "新建会话": "New session", "字号…": "Font size…", "字号": "Font size",
    "重置": "Reset", "关闭": "Close",
    "请求次数": "Requests", "输入 token": "Input tokens", "输出 token": "Output tokens",
    "推理 token": "Reasoning tokens", "缓存命中": "Cache read", "缓存写入": "Cache write",
    "总计": "Total", "平均/请求": "Avg/request", "上下文": "Context",
    "Token 结构": "Token mix", "缓存效率": "Cache efficiency", "本轮统计": "Session stats",
    "开": "On", "关": "Off",
    "（可多选）": "(multi-select)", "已复制": "Copied",
    "删除": "Delete", "打开插件目录": "Open plugins folder", "未命名": "Untitled",
}


class ChatModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._items)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._items)):
            return None
        it = self._items[index.row()]
        if role == ROLE_TYPE:
            return it.get("type", "ai")
        if role == ROLE_PAYLOAD:
            return it.get("payload", {})
        return None

    def roleNames(self):
        return {ROLE_TYPE: b"rtype", ROLE_PAYLOAD: b"payload"}

    def append(self, item):
        row = len(self._items)
        self.beginInsertRows(QModelIndex(), row, row)
        self._items.append(item)
        self.endInsertRows()
        return row

    def update_payload(self, row, payload):
        if 0 <= row < len(self._items):
            self._items[row]["payload"] = payload
            idx = self.index(row, 0)
            self.dataChanged.emit(idx, idx, [ROLE_PAYLOAD])

    def load(self, items):
        self.beginResetModel(); self._items = list(items); self.endResetModel()

    def clear(self):
        self.beginResetModel(); self._items = []; self.endResetModel()

    def items_copy(self):
        return copy.deepcopy(self._items)


ROLE_BLOCK = Qt.UserRole + 1


class BlockModel(QAbstractListModel):
    """Per-AI-message model of block dicts. Lives for the lifetime of the message
    so QML's Repeater never recreates all delegates — only changed/added/removed
    rows update (sync() applies the minimal diff against a working list)."""

    def __init__(self, blocks=None, parent=None):
        super().__init__(parent)
        self._b = list(blocks) if blocks else []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._b)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._b)):
            return None
        if role == ROLE_BLOCK:
            return self._b[index.row()]
        return None

    def roleNames(self):
        return {ROLE_BLOCK: b"block"}

    def to_list(self):
        return copy.deepcopy(self._b)

    def sync(self, new):
        """Reconcile to `new` (working list) with minimal model ops.
        Common prefix (same dict identity) → dataChanged (covers in-place edits);
        differing suffix → remove old tail + insert new tail."""
        old = self._b
        p = 0
        while p < len(old) and p < len(new) and old[p] is new[p]:
            p += 1
        if len(old) > p:
            self.beginRemoveRows(QModelIndex(), p, len(old) - 1)
            del self._b[p:]
            self.endRemoveRows()
        if len(new) > p:
            self.beginInsertRows(QModelIndex(), p, len(new) - 1)
            self._b.extend(new[p:])
            self.endInsertRows()
        if p > 0:
            self.dataChanged.emit(self.index(0, 0), self.index(p - 1, 0), [ROLE_BLOCK])


def wrap_ai_rows(rows):
    """Wrap any AI row's plain block list into a BlockModel (for mock/preview)."""
    for it in rows:
        if it.get("type") == "ai":
            pay = it.get("payload", {}) or {}
            if "bm" not in pay:
                it["payload"] = {"bm": BlockModel(pay.get("blocks", []))}
    return rows


def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _to_html(text):
    """minimal: escape + newlines + **bold** + `code` (markdown rendering is a TODO)."""
    import re as _re
    h = _esc(text)
    h = _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", h)
    h = _re.sub(r"`([^`]+)`", r"<span style='font-family:monospace;color:#d4a373'>\1</span>", h)
    return h.replace("\n", "<br>")


class Controller(QObject):
    providerChanged = Signal()
    modelChanged = Signal()
    sceneChanged = Signal()
    showThinkingChanged = Signal()
    runningChanged = Signal()
    sessionsChanged = Signal()
    confirmModeChanged = Signal()
    memoryChanged = Signal()
    imagesChanged = Signal()
    fontScaleChanged = Signal()
    langChanged = Signal()
    _sigConfirm = Signal(str, str, str)   # cid, tool_name, arg_preview
    _sigAskQ = Signal(str, str)           # qid, questions_json
    _sigResolveCard = Signal(str, str)    # cid/qid, new_state  (decided UI)
    _sigImage = Signal(str)               # data-uri -> append an image block
    _sigTodo = Signal(str)                # json -> add/update a todo card
    _sigShell = Signal(str)               # json -> add a python/system shell block
    _sigStatus = Signal(str)              # phase -> status bar (queued from worker)
    _sigPreview = Signal(str, str)        # tool_name, code -> streaming code preview
    _sigPlanStream = Signal(str)          # accumulated create_plan json -> live plan card
    _sigInfo = Signal(str, str)           # title, body -> QML info dialog
    tokensChanged = Signal()
    pendingOpsChanged = Signal()
    batchResolved = Signal(str)           # "kept"/"reverted" -> resolve all pending node-op rows
    statusChanged = Signal()              # running phase label
    updateAvailableChanged = Signal()
    toast = Signal(str)                   # transient message
    openFontDialog = Signal()             # request the font-size slider popup
    openTokenDialog = Signal()            # request the token analytics popup (QML)
    openInfoDialog = Signal(str, str)      # title, body (QML)
    openApiKeyDialog = Signal(str)         # provider (QML)
    openCustomProviderDialog = Signal(str, str, str, bool, str, bool)  # url, key, model, anthropic, context_limit, supports_vision
    openConfirmDialog = Signal(str, str, str)  # title, body, token
    planExecutionStarted = Signal()
    planConfirmFailed = Signal(str)
    managementChanged = Signal()
    openRulesDialog = Signal()
    openPluginsDialog = Signal()
    openMemoryDialog = Signal()

    # worker -> main thread (queued)
    _sigThink = Signal(str)
    _sigContent = Signal(str)
    _sigToolCall = Signal(str, str)
    _sigToolResult = Signal(str, bool, str)
    _sigDone = Signal(str)
    _sigNodeOp = Signal(str)   # opId -> append a node-operation row
    _sigPlan = Signal(str)     # plan payload json -> append a plan card row
    _sigPlanStep = Signal(str) # {step_id,status,summary} json -> update plan step
    _sigMeshyProgress = Signal(str)  # json -> create/update a Meshy generation card
    _sigConcept = Signal(str)        # json -> create/update the concept-gallery card
    _sigLibrary = Signal(str)        # json {items, append} -> store on main thread
    _sigMeshyAccount = Signal(str)   # json {connected, balance, error} -> main thread
    _sigDeliverBg = Signal()         # a backgrounded Meshy task finished -> feed agent
    _sigShowBgGallery = Signal(str)  # token -> pop an interactive gallery after a bg concept run
    libraryOpenChanged = Signal()
    libraryChanged = Signal()        # items list changed
    libraryLoadingChanged = Signal()
    meshyAccountChanged = Signal()   # connection / balance changed
    # worker -> main thread, BLOCKING (Houdini tool execution)
    _sigToolExec = Signal(str, str)

    def __init__(self, model, use_backend=False, parent=None):
        super().__init__(parent)
        self._model = model
        self._provider = "duojie"
        self._model_name = "claude-opus-4-6-max"
        self._mode = "Agent"
        self._show_thinking = True
        self._confirm_mode = False
        self._memory_enabled = False
        self._cook_realtime = True
        self._cook_suspended = False     # set when a cook is interrupted/too slow (per run)
        self._font_scale = 1.0
        self._scene_path = "/obj"
        self._scene_sel = "no selection"
        self._running = False
        self._pending_images = []   # [(b64, media_type)]
        self._last_attached_images = []  # 本轮用户附带图片(data URI)，供 meshy 图生图回退
        self._interactive = {}      # cid/qid -> queue.Queue (confirm / ask_question)
        self._confirm_actions = {}  # token -> callable
        self._int_seq = 0
        # Meshy 云资产库（侧滑抽屉）
        self._library_open = False
        self._library_items = []
        self._library_loading = False
        self._library_page = 1
        self._library_busy = False
        # Meshy 账号（API Key 即登录凭证）
        self._meshy_balance = -1        # -1 = 未知/未校验
        self._meshy_account_busy = False
        # Meshy 后台任务
        self._meshy_bg = {}             # op -> 任务进度 dict（供 meshy_task_status 查询）
        self._meshy_bg_requests = {}    # op -> threading.Event（请求转入后台）
        self._meshy_bg_feedback = []    # 已完成、待投递给 agent 的结果文本
        self._meshy_bg_lock = threading.Lock()
        self._bg_galleries = {}         # token -> 后台跑完待呈现/可交互的概念图画廊数据
        self._pending_bg_galleries = [] # agent 忙时排队待弹出的画廊 token
        self._token_stats = {"input": 0, "output": 0, "reasoning": 0,
                             "cache_read": 0, "cache_write": 0, "total": 0, "requests": 0}
        self._call_records = []
        self._ctx_text = "0 / 200k"
        self._token_text = "0 tokens"
        self._status_phase = ""      # "", thinking, generating, tool:<name>, planning
        self._pending_ops = 0
        self._update_info = None
        self._node_name_map = {}     # bare name -> full path (for resolve)
        try:
            from houdini_agent.ui.i18n import get_language
            self._lang = get_language()
        except Exception:
            self._lang = "zh"

        self._load_prefs()

        # Meshy 用量埋点：app 进程启动后台上传线程，补传上次遗留的 spool
        try:
            from houdini_agent.meshy import telemetry as _meshy_telemetry
            _meshy_telemetry.start()
        except Exception:
            pass

        # backend
        self._session = None
        if use_backend:
            try:
                from houdini_agent.ui_qml.agent_session import AgentSession
                self._session = AgentSession()
                self._session.set_tool_executor(self._tool_executor)
            except Exception as e:
                print("[controller] backend unavailable, using simulated replies:", e)
                self._session = None

        # re-apply persisted custom-provider config; avoid a stranded 'custom' state
        self._load_custom()
        if self._provider == "custom":
            if MODEL_MAP.get("custom"):
                if self._model_name not in MODEL_MAP["custom"]:
                    self._model_name = MODEL_MAP["custom"][0]
            else:
                self._provider = "duojie"
                self._model_name = MODEL_MAP["duojie"][0]

        # tool-exec marshalling
        self._tool_q = queue.Queue()
        self._tool_lock = threading.Lock()
        self._sigToolExec.connect(self._on_tool_exec_main, Qt.BlockingQueuedConnection)

        # node-operation undo contexts: opId -> {op, paths, snapshot}
        self._op_ctx = {}
        self._op_seq = 0

        # plan mode state
        self._plan_phase = "idle"   # idle | planning | awaiting | executing
        self._plan_row = None
        self._plan_payload = None
        self._plan_idmap = {}
        self._pending_plan_confirm = False
        self._plan_revision_mode = False

        # multi-session + persistence
        self._cache_dir = self._session_cache_dir()
        self._migrate_legacy_sessions()
        self._sessions = []     # [{id, title, rows, history}]
        self._active = 0
        self._add_session()

        # streaming UI updates (queued onto main thread)
        self._sigThink.connect(self._ui_think)
        self._sigContent.connect(self._ui_content)
        self._sigToolCall.connect(self._ui_tool_call)
        self._sigToolResult.connect(self._ui_tool_result)
        self._sigDone.connect(self._ui_done)
        self._sigNodeOp.connect(self._ui_node_op)
        self._sigPlan.connect(self._ui_plan)
        self._sigPlanStep.connect(self._ui_plan_step)
        self._sigConfirm.connect(self._ui_confirm)
        self._sigAskQ.connect(self._ui_askq)
        self._sigResolveCard.connect(self._ui_resolve_card)
        self._sigImage.connect(self._ui_image)
        self._sigTodo.connect(self._ui_todo)
        self._sigMeshyProgress.connect(self._ui_meshy_progress)
        self._sigConcept.connect(self._ui_concept)
        self._sigLibrary.connect(self._ui_library)
        self._sigMeshyAccount.connect(self._ui_meshy_account)
        self._sigDeliverBg.connect(self._flush_bg_feedback)
        self._sigShowBgGallery.connect(self._show_bg_gallery)
        self._sigShell.connect(self._ui_shell)
        self._sigStatus.connect(self._ui_status)
        self._sigPreview.connect(self._ui_preview)
        self._sigPlanStream.connect(self._ui_plan_stream)
        self._sigInfo.connect(self._info)

        # coalesce UI flushes to ~25fps (avoids O(N^2) re-render on long runs)
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(40)
        self._flush_timer.timeout.connect(self._do_flush)
        self._answer_dirty = False

        self._last_activity = time.monotonic()
        self._watchdog_warned = False
        self._watchdog_timer = QTimer(self)
        self._watchdog_timer.setInterval(5000)
        self._watchdog_timer.timeout.connect(self._watchdog_tick)
        self._watchdog_timer.start()

        # per-run state
        self._ai_row = None
        self._bm = None
        self._reset_run_state()

    @staticmethod
    def _session_cache_dir():
        try:
            here = Path(__file__).resolve()
            if getattr(sys, "frozen", False) or "_internal" in here.parts:
                base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "HoudiniAgent"
                return base / "qml_sessions"
            return here.parents[2] / "cache" / "qml_sessions"
        except Exception:
            return None

    def _migrate_legacy_sessions(self):
        try:
            if not self._cache_dir or (self._cache_dir / "manifest.json").exists():
                return
            here = Path(__file__).resolve()
            legacy = here.parents[2] / "cache" / "qml_sessions"
            if legacy == self._cache_dir or not (legacy / "manifest.json").exists():
                return
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            for p in legacy.glob("*.json"):
                dst = self._cache_dir / p.name
                if not dst.exists():
                    shutil.copy2(str(p), str(dst))
        except Exception as e:
            self._log_external("Session migration failed: %s" % e)

    def attach_backend_session(self, session):
        """Attach a late-created backend session, used by the external launcher."""
        self._session = session
        try:
            self._session.set_tool_executor(self._tool_executor)
        except Exception:
            pass
        try:
            self._load_custom()
        except Exception:
            pass
        try:
            if self._sessions:
                self._session.history = copy.deepcopy(self._sessions[self._active].get("history", []))
        except Exception:
            pass
        self.refreshContext()
        self.toast.emit("已连接 Houdini Bridge")

    @staticmethod
    def _log_external(msg):
        try:
            p = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "HoudiniAgent" / "launcher.log"
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(str(msg).rstrip() + "\n")
        except Exception:
            pass

    @staticmethod
    def _log_chat(role, text):
        try:
            p = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "HoudiniAgent" / "chat.log"
            p.parent.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            body = str(text or "").strip()
            if not body:
                return
            with p.open("a", encoding="utf-8") as f:
                f.write("\n[%s] %s\n%s\n" % (stamp, role, body))
        except Exception:
            pass

    # ---- prefs ----
    @staticmethod
    def _qbool(v, default):
        if isinstance(v, bool):
            return v
        if v is None:
            return default
        return str(v).lower() in ("true", "1", "yes")

    def _set_pref(self, key, val):
        try:
            QSettings("HoudiniAI", "Assistant").setValue(key, val)
        except Exception:
            pass

    def _load_prefs(self):
        try:
            s = QSettings("HoudiniAI", "Assistant")
            p = s.value("last_provider", "")
            m = s.value("last_model", "")
            if p == "custom":
                cm = str(s.value("custom_model", "") or "")
                cu = str(s.value("custom_url", "") or "")
                if cm and cu and not self._is_placeholder_custom_url(cu):
                    MODEL_MAP["custom"] = [cm]
                    CONTEXT_LIMITS[cm] = self._to_int(s.value("custom_context_limit", 128000), 128000)
            if p in MODEL_MAP:
                self._provider = p
            if m and m in MODEL_MAP.get(self._provider, []):
                self._model_name = m
            elif self._provider == "custom" and MODEL_MAP.get("custom"):
                self._model_name = MODEL_MAP["custom"][0]
            # persisted toggles / settings (same class of bug as custom)
            self._show_thinking = self._qbool(s.value("use_think", True), True)
            self._confirm_mode = self._qbool(s.value("confirm_mode", False), False)
            self._memory_enabled = self._qbool(s.value("memory_enabled", False), False)
            self._cook_realtime = self._qbool(s.value("cook_realtime", True), True)
            try:
                self._font_scale = float(s.value("font_scale", 1.0))
            except Exception:
                pass
        except Exception:
            pass

    def _save_prefs(self):
        try:
            s = QSettings("HoudiniAI", "Assistant")
            s.setValue("last_provider", self._provider)
            s.setValue("last_model", self._model_name)
        except Exception:
            pass

    def _current_model_supports_vision(self):
        if self._provider == "custom":
            try:
                v = QSettings("HoudiniAI", "Assistant").value("custom_supports_vision", False)
                return self._qbool(v, False)
            except Exception:
                return False
        return self._model_name in VISION_MODELS

    # ---- properties ----
    def _get_provider(self): return self._provider
    provider = Property(str, _get_provider, notify=providerChanged)

    def _get_provider_label(self): return PROVIDER_LABELS.get(self._provider, self._provider)
    providerLabel = Property(str, _get_provider_label, notify=providerChanged)

    def _get_model(self): return self._model_name
    model = Property(str, _get_model, notify=modelChanged)

    def _get_show_thinking(self): return self._show_thinking
    showThinking = Property(bool, _get_show_thinking, notify=showThinkingChanged)

    def _get_running(self): return self._running
    running = Property(bool, _get_running, notify=runningChanged)

    def _get_confirm(self): return self._confirm_mode
    confirmMode = Property(bool, _get_confirm, notify=confirmModeChanged)

    def _get_memory(self): return self._memory_enabled
    memoryEnabled = Property(bool, _get_memory, notify=memoryChanged)

    def _get_image_count(self): return len(self._pending_images)
    imageCount = Property(int, _get_image_count, notify=imagesChanged)

    def _get_font_scale(self): return self._font_scale
    fontScale = Property(float, _get_font_scale, notify=fontScaleChanged)

    def _get_lang(self): return self._lang
    lang = Property(str, _get_lang, notify=langChanged)

    def _get_ctx_text(self): return self._ctx_text
    ctxText = Property(str, _get_ctx_text, notify=tokensChanged)

    def _get_token_text(self): return self._token_text
    tokenText = Property(str, _get_token_text, notify=tokensChanged)

    def _get_status(self): return self._status_phase
    statusPhase = Property(str, _get_status, notify=statusChanged)

    def _get_pending(self): return self._pending_ops
    pendingOps = Property(int, _get_pending, notify=pendingOpsChanged)

    def _get_update_text(self): return self._update_info or ""
    updateText = Property(str, _get_update_text, notify=updateAvailableChanged)

    def _get_library_open(self): return self._library_open
    libraryOpen = Property(bool, _get_library_open, notify=libraryOpenChanged)

    def _get_library_loading(self): return self._library_loading
    libraryLoading = Property(bool, _get_library_loading, notify=libraryLoadingChanged)

    @Slot(str, result=str)
    def tr(self, zh):
        """Translate a Chinese UI string to English when lang == 'en'."""
        if self._lang == "en":
            return UI_EN.get(zh, zh)
        return zh

    @Slot(str)
    def showToast(self, msg):
        self.toast.emit(str(msg or ""))

    def _get_scene_path(self): return self._scene_path
    scenePath = Property(str, _get_scene_path, notify=sceneChanged)

    def _get_scene_sel(self): return self._scene_sel
    sceneSelection = Property(str, _get_scene_sel, notify=sceneChanged)

    # ---- menu data for QML ----
    @Slot(result="QVariantList")
    def providerItems(self):
        return [{"label": PROVIDER_LABELS.get(k, k), "val": k, "checked": k == self._provider}
                for k in MODEL_MAP.keys()]

    @Slot(result="QVariantList")
    def modelItems(self):
        models = MODEL_MAP.get(self._provider, [])
        return [{"label": m, "val": m, "checked": m == self._model_name} for m in models]

    # ---- slots from QML ----
    @Slot(str, result=bool)
    def setProvider(self, v):
        if self._running:
            self.toast.emit("当前正在运行，模型切换将在本轮结束后再操作")
            return False
        if v not in MODEL_MAP or v == self._provider:
            return False
        self._provider = v
        models = MODEL_MAP[v]
        if self._model_name not in models and models:
            self._model_name = models[0]
            self.modelChanged.emit()
        self.providerChanged.emit()
        self._save_prefs()
        # Custom needs a one-time config; prompt if not set up yet
        if v == "custom" and not MODEL_MAP.get("custom"):
            self._open_custom_provider()
        return True

    @Slot(str, result=bool)
    def setModel(self, v):
        if self._running:
            self.toast.emit("当前正在运行，模型切换将在本轮结束后再操作")
            return False
        if v and v != self._model_name:
            self._model_name = v
            self.modelChanged.emit()
            self._save_prefs()
            return True
        return False

    @Slot(bool)
    def setThink(self, on):
        on = bool(on)
        if on != self._show_thinking:
            self._show_thinking = on
            self._set_pref("use_think", on)
            self.showThinkingChanged.emit()

    @Slot(str, result=bool)
    def setMode(self, m):
        if self._running:
            self.toast.emit("当前正在运行，模式切换将在本轮结束后再操作")
            return False
        self._mode = m
        return True

    @Slot(float)
    def setFontScale(self, v):
        try:
            v = round(max(0.7, min(1.6, float(v))), 2)
        except Exception:
            return
        if v != self._font_scale:
            self._font_scale = v
            self._set_pref("font_scale", v)
            self.fontScaleChanged.emit()

    # ---- multi-session ----
    def _add_session(self, title=None):
        self._sessions.append({"id": uuid.uuid4().hex[:8], "title": title or "新对话",
                               "rows": [], "history": [],
                               "provider": self._provider, "model": self._model_name})

    def _serialize_rows(self):
        """Plain (JSON-safe) rows: AI block models -> block lists."""
        out = []
        for it in self._model._items:
            pay = it.get("payload", {})
            if it.get("type") == "ai" and isinstance(pay, dict) and "bm" in pay:
                bm = pay.get("bm")
                out.append({"type": "ai", "payload": {"blocks": bm.to_list() if bm else []}})
            else:
                out.append(copy.deepcopy(it))
        return out

    def _snapshot_active(self):
        if not self._sessions:
            return
        s = self._sessions[self._active]
        s["rows"] = self._serialize_rows()
        s["history"] = copy.deepcopy(self._session.history) if self._session else []
        s["provider"] = self._provider
        s["model"] = self._model_name

    @staticmethod
    def _sanitize_rows(rows):
        # resolve any interactive cards left "pending" in a restored session
        for it in rows:
            if it.get("type") != "ai":
                continue
            for b in (it.get("payload", {}) or {}).get("blocks", []):
                k, st = b.get("kind"), b.get("state")
                if k == "confirm" and st == "pending":
                    b["state"] = "cancelled"
                elif k == "askq" and st == "pending":
                    b["state"] = "answered"

    def _load_active(self):
        s = self._sessions[self._active]
        rows = copy.deepcopy(s.get("rows", []))
        self._sanitize_rows(rows)
        for it in rows:
            if it.get("type") == "ai":
                blocks = (it.get("payload", {}) or {}).get("blocks", [])
                it["payload"] = {"bm": BlockModel(blocks)}
        self._model.load(rows)
        p, m = s.get("provider"), s.get("model")
        if p == "custom":
            self._load_custom()
        if p in MODEL_MAP and (not m or m in MODEL_MAP.get(p, [])):
            changed_p = p != self._provider
            changed_m = bool(m and m != self._model_name)
            self._provider = p
            if m:
                self._model_name = m
            if changed_p:
                self.providerChanged.emit()
            if changed_m:
                self.modelChanged.emit()
        if self._session:
            self._session.history = copy.deepcopy(s.get("history", []))
        self._reset_run_state()
        self._plan_row = None
        self._plan_payload = None
        self._plan_idmap = {}
        self._refresh_tokens()

    @Slot(result="QVariantList")
    def sessionItems(self):
        return [{"title": (self.tr(s["title"]) if s["title"] == "新对话" else s["title"]),
                 "active": (i == self._active)}
                for i, s in enumerate(self._sessions)]

    @Slot(int)
    def switchSession(self, idx):
        if self._running:
            self.toast.emit("当前正在运行，不能切换会话")
            return
        if idx < 0 or idx >= len(self._sessions) or idx == self._active:
            return
        self._snapshot_active()
        self._active = idx
        self._load_active()
        self.sessionsChanged.emit()
        self._save_all()

    @Slot()
    def newSession(self):
        if self._running:
            self.toast.emit("当前正在运行，不能新建会话")
            return
        self._snapshot_active()
        self._add_session()
        self._active = len(self._sessions) - 1
        self._load_active()
        self.sessionsChanged.emit()
        self._save_all()

    @Slot()
    def clearChat(self):
        if self._running:
            self.toast.emit("当前正在运行，不能清空对话")
            return
        self._request_confirm("清空对话", "确定要清空当前会话吗？此操作会删除当前会话的消息历史。", self._clear_chat_now)

    def _clear_chat_now(self):
        self._model.clear()
        if self._session:
            self._session.reset()
        self._sessions[self._active]["rows"] = []
        self._sessions[self._active]["history"] = []
        self._reset_run_state()
        self._plan_row = None
        self._save_all()
        self.toast.emit(self.tr("清空对话"))

    @Slot(result="QVariantList")
    def overflowItems(self):
        t = self.tr
        return [
            {"label": "API Key…", "val": "apikey"},
            {"label": "Meshy API Key…", "val": "meshy_key"},
            {"label": self.tr("Meshy 资产库"), "val": "library"},
            {"label": t("自定义 Provider…"), "val": "custom_cfg"},
            {"label": t("导出对话"), "val": "export"},
            {"label": t("Token 分析"), "val": "tokens"},
            {"label": t("压缩上下文"), "val": "optimize"},
            {"label": t("缓存位置"), "val": "cache"},
            {"sep": True},
            {"label": t("检查更新"), "val": "update"},
            {"label": t("字号…"), "val": "font"},
            {"sep": True},
            {"label": t("规则编辑器"), "val": "rules"},
            {"label": t("插件管理"), "val": "plugins"},
            {"label": t("记忆管理"), "val": "memory_mgr"},
            {"sep": True},
            {"label": t("执行前确认"), "val": "confirm", "checked": self._confirm_mode},
            {"label": t("长期记忆"), "val": "memory", "checked": self._memory_enabled},
            {"label": t("实时 Cook"), "val": "cook", "checked": self._cook_realtime},
            {"sep": True},
            {"label": "语言 / Language: " + ("中文" if self._lang == "zh" else "English"), "val": "lang_toggle"},
            {"sep": True},
            {"label": t("清空对话"), "val": "clear"},
        ]

    @Slot(str)
    def menuAction(self, action):
        if action == "clear":
            self.clearChat()
        elif action == "confirm":
            self._confirm_mode = not self._confirm_mode
            self._set_pref("confirm_mode", self._confirm_mode)
            self.confirmModeChanged.emit()
            self.toast.emit(self.tr("执行前确认") + (" ON" if self._confirm_mode else " OFF"))
        elif action == "memory":
            self._memory_enabled = not self._memory_enabled
            self._set_pref("memory_enabled", self._memory_enabled)
            self.memoryChanged.emit()
            self.toast.emit(self.tr("长期记忆") + (" ON" if self._memory_enabled else " OFF"))
        elif action == "cook":
            self._cook_realtime = not self._cook_realtime
            self._set_pref("cook_realtime", self._cook_realtime)
            self.toast.emit(self.tr("实时 Cook") + (" ON" if self._cook_realtime else " OFF"))
        elif action == "font":
            self.openFontDialog.emit()
        elif action in ("lang_zh", "lang_en"):
            self._set_language("zh" if action == "lang_zh" else "en")
        elif action == "lang_toggle":
            self._set_language("en" if self._lang == "zh" else "zh")
        elif action == "apikey":
            self._open_api_key()
        elif action == "meshy_key":
            try:
                self.openApiKeyDialog.emit("meshy")
            except Exception as e:
                print("[controller] open meshy key dialog failed:", e)
        elif action == "library":
            self.setLibraryOpen(True)
        elif action == "export":
            self._export_chat()
        elif action == "tokens":
            self._open_token_panel()
        elif action == "cache":
            self._info("缓存位置", str(self._cache_dir or "(unavailable)"))
        elif action == "update":
            self._check_update()
        elif action == "rules":
            self.openRulesDialog.emit()
        elif action == "plugins":
            self.openPluginsDialog.emit()
        elif action == "memory_mgr":
            self.openMemoryDialog.emit()
        elif action == "custom_cfg":
            self._open_custom_provider()
        elif action == "optimize":
            if self._session and len(self._session.history) > 24:
                self._session.history = self._session.history[-24:]
                self._refresh_tokens()
                self.toast.emit(self.tr("已压缩上下文"))
            else:
                self.toast.emit("上下文无需压缩")

    # ---- overflow helpers ----
    def _info(self, title, text):
        try:
            self.openInfoDialog.emit(self.tr(title), str(text)[:3000])
        except Exception as e:
            print("[controller] info:", title, text, e)

    def _request_confirm(self, title, body, fn):
        token = uuid.uuid4().hex[:10]
        self._confirm_actions[token] = fn
        self.openConfirmDialog.emit(self.tr(title), str(body), token)

    @Slot(str)
    def acceptDialogConfirm(self, token):
        fn = self._confirm_actions.pop(token, None)
        if fn:
            try:
                fn()
            except Exception as e:
                self._info("操作失败", str(e))

    @Slot(str)
    def cancelDialogConfirm(self, token):
        self._confirm_actions.pop(token, None)

    @Slot(result="QVariantList")
    def rulesItems(self):
        try:
            from houdini_agent.utils.rules_manager import get_all_rules
            out = []
            for r in get_all_rules(force_reload=True):
                src = r.get("source", "ui")
                out.append({
                    "id": r.get("id", ""),
                    "title": r.get("title", "") or self.tr("未命名"),
                    "content": r.get("content", ""),
                    "enabled": bool(r.get("enabled", True)),
                    "source": src,
                    "readonly": src == "file",
                    "path": r.get("file_path", ""),
                })
            return out
        except Exception as e:
            return [{"id": "", "title": "Load failed", "content": str(e), "enabled": False,
                     "source": "error", "readonly": True, "path": ""}]

    @Slot(result="QVariantMap")
    def addRule(self):
        try:
            from houdini_agent.utils.rules_manager import add_rule
            r = add_rule(self.tr("未命名"), "")
            return {"id": r.get("id", ""), "ok": True}
        except Exception as e:
            self._info("规则编辑器", "新增失败：%s" % e)
            return {"id": "", "ok": False}

    @Slot(str, str, str, bool, result=bool)
    def saveRule(self, rule_id, title, content, enabled):
        try:
            from houdini_agent.utils.rules_manager import update_rule
            if not rule_id.startswith("file:"):
                update_rule(rule_id, title=title, content=content, enabled=bool(enabled))
                self.toast.emit("规则已保存")
                self.managementChanged.emit()
                return True
        except Exception as e:
            self._info("规则编辑器", "保存失败：%s" % e)
        return False

    @Slot(str)
    def deleteRule(self, rule_id):
        if not rule_id:
            return
        self._request_confirm("删除规则", "确定要删除这条规则吗？", lambda rid=rule_id: self._delete_rule_now(rid))

    def _delete_rule_now(self, rule_id):
        try:
            from houdini_agent.utils.rules_manager import delete_rule
            if not rule_id.startswith("file:"):
                delete_rule(rule_id)
                self.toast.emit("规则已删除")
                self.managementChanged.emit()
        except Exception as e:
            self._info("规则编辑器", "删除失败：%s" % e)

    @Slot()
    def openRulesFolder(self):
        try:
            from houdini_agent.utils.rules_manager import get_rules_dir, ensure_rules_dir
            ensure_rules_dir()
            self._open_path(str(get_rules_dir()))
        except Exception as e:
            self._info("规则编辑器", "打开目录失败：%s" % e)

    @staticmethod
    def _open_path(path):
        import os
        import subprocess
        import sys as _sys
        if _sys.platform == "win32":
            os.startfile(path)
        elif _sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    @Slot(result="QVariantList")
    def pluginItems(self):
        try:
            from houdini_agent.utils.hooks import list_plugins
            return [{
                "name": p.get("name", ""),
                "version": p.get("version", ""),
                "author": p.get("author", ""),
                "description": p.get("description", ""),
                "enabled": bool(p.get("_enabled", False)),
                "settings": bool(p.get("settings")),
            } for p in list_plugins()]
        except Exception as e:
            return [{"name": "Load failed", "version": "", "author": "",
                     "description": str(e), "enabled": False, "settings": False}]

    @Slot(str, result="QVariantList")
    def pluginSettings(self, name):
        try:
            from houdini_agent.utils.hooks import list_plugins, get_plugin_setting
            for p in list_plugins():
                if p.get("name") != name:
                    continue
                out = []
                for item in (p.get("settings") or []):
                    key = item.get("key", "")
                    if not key:
                        continue
                    default = item.get("default")
                    out.append({
                        "key": key,
                        "label": item.get("label", key),
                        "type": item.get("type", "string"),
                        "value": get_plugin_setting(name, key, default),
                        "default": default,
                        "options": item.get("options") or [],
                    })
                return out
        except Exception as e:
            self._info("插件设置", "加载失败：%s" % e)
        return []

    @Slot(str, str)
    def savePluginSettings(self, name, settings_json):
        try:
            from houdini_agent.utils.hooks import set_plugin_setting
            settings = json.loads(settings_json or "[]")
            for item in settings:
                key = item.get("key")
                if not key:
                    continue
                value = item.get("value")
                if item.get("type") == "bool":
                    value = bool(value)
                set_plugin_setting(name, key, value)
            self.toast.emit("插件设置已保存")
        except Exception as e:
            self._info("插件设置", "保存失败：%s" % e)

    @Slot(str, bool)
    def setPluginEnabled(self, name, enabled):
        try:
            from houdini_agent.utils.hooks import enable_plugin, disable_plugin
            (enable_plugin if enabled else disable_plugin)(name)
            self.toast.emit(("已启用 " if enabled else "已禁用 ") + name)
        except Exception as e:
            self._info("插件管理", "切换失败：%s" % e)

    @Slot(str)
    def reloadPlugin(self, name):
        try:
            from houdini_agent.utils.hooks import reload_plugin
            reload_plugin(name)
            self.toast.emit("已重载 " + name)
        except Exception as e:
            self._info("插件管理", "重载失败：%s" % e)

    @Slot()
    def reloadAllPlugins(self):
        try:
            from houdini_agent.utils.hooks import reload_all_plugins
            reload_all_plugins()
            self.toast.emit("已重载全部插件")
        except Exception as e:
            self._info("插件管理", "重载失败：%s" % e)

    @Slot()
    def openPluginsFolder(self):
        try:
            from houdini_agent.utils.hooks import get_plugins_dir
            p = get_plugins_dir()
            p.mkdir(parents=True, exist_ok=True)
            self._open_path(str(p))
        except Exception as e:
            self._info("插件管理", "打开目录失败：%s" % e)

    @Slot(result="QVariantList")
    def toolItems(self):
        try:
            from houdini_agent.utils.tool_registry import get_tool_registry
            return get_tool_registry().list_all()
        except Exception as e:
            return [{"name": "Load failed", "description": str(e), "source": "error",
                     "enabled": False, "modes": [], "tags": []}]

    @Slot(str, bool)
    def setToolEnabled(self, name, enabled):
        try:
            from houdini_agent.utils.tool_registry import get_tool_registry
            reg = get_tool_registry()
            reg.set_enabled(name, bool(enabled))
            reg.save_disabled_to_config()
        except Exception as e:
            self._info("插件管理", "工具切换失败：%s" % e)

    @Slot(result="QVariantList")
    def skillItems(self):
        try:
            from houdini_agent.skills import list_skills
            return list_skills()
        except Exception as e:
            return [{"name": "Load failed", "description": str(e), "parameters": {}}]

    @Slot(result=str)
    def userSkillDir(self):
        try:
            from houdini_agent.skills import _get_user_skill_dir
            p = _get_user_skill_dir()
            return str(p) if p else ""
        except Exception:
            return ""

    @Slot(str)
    def setUserSkillDir(self, path):
        try:
            if path.startswith("file:///"):
                path = path[8:] if os.name == "nt" else path[7:]
            elif path.startswith("file://"):
                path = path[7:]
            import configparser
            config_dir = Path(__file__).resolve().parents[2] / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            ini_path = config_dir / "houdini_ai.ini"
            cfg = configparser.ConfigParser()
            if ini_path.exists():
                cfg.read(str(ini_path), encoding="utf-8")
            if not cfg.has_section("skills"):
                cfg.add_section("skills")
            cfg.set("skills", "user_skill_dir", path)
            with open(ini_path, "w", encoding="utf-8") as f:
                cfg.write(f)
            try:
                from houdini_agent.skills import reload_skills
                reload_skills()
            except Exception:
                pass
            self.toast.emit("用户技能目录已设置")
        except Exception as e:
            self._info("插件管理", "设置技能目录失败：%s" % e)

    @Slot(result="QVariantMap")
    def memoryStats(self):
        try:
            from houdini_agent.utils.memory_store import get_memory_store
            s = get_memory_store()
            return {"episodic": s.count_episodic(), "semantic": s.count_semantic(),
                    "procedural": s.count_procedural()}
        except Exception as e:
            return {"episodic": 0, "semantic": 0, "procedural": 0, "error": str(e)}

    @Slot(str, result="QVariantList")
    def memoryItems(self, kind):
        try:
            from houdini_agent.utils.memory_store import get_memory_store
            s = get_memory_store()
            out = []
            if kind == "semantic":
                for r in s.get_all_semantic()[:80]:
                    out.append({"id": r.id, "title": r.category or "Semantic",
                                "body": r.rule, "meta": "confidence %.2f · level %s" % (r.confidence, r.abstraction_level)})
            elif kind == "procedural":
                for r in s.get_all_procedural()[:80]:
                    out.append({"id": r.id, "title": r.strategy_name,
                                "body": r.description, "meta": "priority %.2f · success %.0f%% · %d conditions" % (r.priority, r.success_rate * 100, len(r.conditions or []))})
            else:
                for r in s.get_recent_episodic(80):
                    out.append({"id": r.id, "title": r.task_description,
                                "body": r.result_summary, "meta": time.strftime("%Y-%m-%d %H:%M", time.localtime(r.timestamp))})
            return out
        except Exception as e:
            return [{"id": "", "title": "Load failed", "body": str(e), "meta": ""}]

    @Slot(str, str)
    def deleteMemory(self, kind, record_id):
        if not record_id:
            return
        self._request_confirm("删除记忆", "确定要删除这条长期记忆吗？", lambda k=kind, rid=record_id: self._delete_memory_now(k, rid))

    def _delete_memory_now(self, kind, record_id):
        try:
            from houdini_agent.utils.memory_store import get_memory_store
            s = get_memory_store()
            if kind == "semantic":
                s.delete_semantic(record_id)
            elif kind == "procedural":
                s.delete_procedural(record_id)
            else:
                s.delete_episodic(record_id)
            self.toast.emit("记忆已删除")
            self.managementChanged.emit()
        except Exception as e:
            self._info("记忆管理", "删除失败：%s" % e)

    def _set_language(self, lang):
        self._lang = lang
        try:
            from houdini_agent.ui.i18n import set_language
            set_language(lang)
        except Exception:
            pass
        self.langChanged.emit()

    def _open_api_key(self):
        if not self._session:
            self.toast.emit("后端不可用，无法配置 API Key")
            return
        try:
            self.openApiKeyDialog.emit(self._provider)
        except Exception as e:
            print("[controller] set api key failed:", e)

    @Slot(str, str, result=bool)
    def submitApiKey(self, provider, key):
        key = (key or "").strip()
        provider = provider or self._provider
        if not key:
            self.toast.emit("API Key 不能为空")
            return False
        # Meshy 不是聊天 provider，单独路由到自包含的 meshy 模块（不依赖聊天后端）
        if provider == "meshy":
            if _meshy is None:
                self.toast.emit(self.tr("Meshy 集成不可用"))
                return False
            ok = _meshy.set_api_key(key, persist=True)
            self.toast.emit(self.tr("已连接 Meshy 账号。") if ok else self.tr("保存 Meshy Key 失败"))
            if ok:
                # 校验并同步账号信息（余额 + 资产）
                self.syncMeshyAccount()
                self.refreshLibrary()
            return bool(ok)
        if not self._session:
            self.toast.emit("后端不可用，无法保存 API Key")
            return False
        try:
            self._session.client.set_api_key(key, persist=True, provider=provider)
            self.toast.emit("已保存 %s 的 API Key。" % provider)
            return True
        except Exception as e:
            self._info("API Key", "保存失败：%s" % e)
            return False

    def _export_chat(self):
        hist = self._session.history if self._session else []
        if not hist:
            self._info("导出对话", "当前会话没有可导出的内容。")
            return
        try:
            from houdini_agent.utils.training_data_exporter import export_chat_training_data
            path = export_chat_training_data(hist)
            self._info("导出对话", "已导出到：\n%s" % path)
        except Exception as e:
            self._info("导出对话", "导出失败：%s" % e)

    def _set_update(self, info):
        self._update_info = info
        self.updateAvailableChanged.emit()

    @Slot()
    def dismissUpdate(self):
        self._update_info = None
        self.updateAvailableChanged.emit()

    @Slot()
    def silentUpdateCheck(self):
        def work():
            try:
                from houdini_agent.utils.updater import check_update
                r = check_update()
                if isinstance(r, dict) and r.get("has_update"):
                    info = "发现新版本 %s — 在 ⋯ 菜单点「检查更新」升级" % r.get("remote_version", "")
                    QTimer.singleShot(0, lambda: self._set_update(info))
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    @Slot(str, result=bool)
    def copyToClipboard(self, text):
        try:
            try:
                from PySide6.QtGui import QGuiApplication
            except ImportError:
                from PySide2.QtGui import QGuiApplication
            QGuiApplication.clipboard().setText(text or "")
            self.toast.emit("已复制")
            return True
        except Exception as e:
            print("[controller] copy failed:", e)
            self.toast.emit("复制失败：%s" % e)
            return False

    @Slot(str, result=bool)
    def createWrangle(self, code):
        if not self._session or not (code or "").strip():
            self.toast.emit("无法创建 Wrangle：后端不可用或代码为空")
            return False
        try:
            res = self._session.mcp.execute_tool("create_wrangle_node", {"vex_code": code})
            ok = isinstance(res, dict) and res.get("success")
            self.toast.emit("已创建 Wrangle 节点" if ok else "创建 Wrangle 失败")
            self.refreshContext()
            return bool(ok)
        except Exception as e:
            self.toast.emit("创建 Wrangle 失败：%s" % e)
            return False

    def _check_update(self):
        self.toast.emit("正在检查更新…")
        def work():
            try:
                from houdini_agent.utils.updater import check_update
                r = check_update()
                if isinstance(r, dict):
                    if r.get("error"):
                        msg = "检查更新失败：%s" % r.get("error")
                    elif r.get("has_update"):
                        notes = r.get("release_notes") or r.get("release_name") or ""
                        msg = (
                            "发现新版本：%s\n"
                            "当前版本：%s\n\n"
                            "%s\n\n"
                            "自动下载应用暂未接入 QML 面板，请先使用测试环境验证后再更新正式目录。"
                        ) % (r.get("remote_version", "?"), r.get("local_version", "?"), notes)
                    else:
                        msg = "已是最新版本。\n\n当前版本：%s\n最新 Release：%s" % (
                            r.get("local_version", "?"), r.get("remote_version", "?"))
                else:
                    msg = str(r)
            except Exception as e:
                msg = "检查失败：%s" % e
            self._sigInfo.emit("检查更新", msg)
        threading.Thread(target=work, daemon=True).start()

    def _open_token_panel(self):
        # rendered by a native QML popup (see Main.qml), not a QtWidgets dialog
        self.openTokenDialog.emit()

    @Slot(result="QVariantMap")
    def tokenStats(self):
        s = self._token_stats
        ctx_used = self._estimate_ctx()
        ctx_limit = self._context_limit()
        self._ctx_text = "%s / %s" % (self._fmt_k(ctx_used), self._fmt_k(ctx_limit))
        total = s["total"] or (s["input"] + s["output"] + s["reasoning"])
        requests = max(0, s["requests"])
        cache_total = s["cache_read"] + s["cache_write"]
        return {
            "requests": s["requests"], "input": s["input"], "output": s["output"],
            "reasoning": s["reasoning"], "cache_read": s["cache_read"],
            "cache_write": s["cache_write"], "total": total,
            "avg_per_request": int(total / requests) if requests else 0,
            "ctx_used": ctx_used, "ctx_limit": ctx_limit, "ctx_text": self._ctx_text,
            "ctx_pct": float(ctx_used) / float(ctx_limit or 1),
            "cache_total": cache_total,
            "cache_hit_rate": float(s["cache_read"]) / float(cache_total or 1),
            "model": self._model_name,
        }

    @Slot(int)
    def closeSession(self, idx):
        if self._running:
            self.toast.emit("当前正在运行，不能关闭会话")
            return
        if idx < 0 or idx >= len(self._sessions) or len(self._sessions) <= 1:
            return
        title = self._sessions[idx].get("title", "当前会话")
        self._request_confirm("关闭会话", "确定要关闭「%s」吗？该会话会从本地历史中移除。" % title,
                              lambda idx=idx: self._close_session_now(idx))

    def _close_session_now(self, idx):
        if idx < 0 or idx >= len(self._sessions) or len(self._sessions) <= 1:
            return
        try:
            if self._cache_dir:
                p = self._cache_dir / ("session_%s.json" % self._sessions[idx]["id"])
                if p.exists():
                    p.unlink()
        except Exception:
            pass
        if idx == self._active:
            del self._sessions[idx]
            self._active = max(0, idx - 1)
            self._load_active()
        else:
            self._snapshot_active()
            del self._sessions[idx]
            if self._active > idx:
                self._active -= 1
        self.sessionsChanged.emit()
        self._save_all()

    # ---- images (multimodal) ----
    @Slot(str)
    def attachImage(self, url):
        if not self._current_model_supports_vision():
            self.toast.emit("当前模型不支持图片输入，已忽略图片")
            return
        try:
            path = url
            if path.startswith("file:///"):
                path = path[8:] if os.name == "nt" else path[7:]
            elif path.startswith("file://"):
                path = path[7:]
            with open(path, "rb") as f:
                raw = f.read()
            ext = os.path.splitext(path)[1].lower().lstrip(".")
            mt = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                  "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp"}.get(ext)
            if mt is None:
                self.toast.emit("不支持的图片格式：.%s" % ext)
                return
            self._pending_images.append((base64.b64encode(raw).decode("ascii"), mt))
            self.imagesChanged.emit()
        except Exception as e:
            print("[controller] attachImage failed:", e)
            self.toast.emit("添加图片失败：%s" % e)

    @Slot()
    def clearImages(self):
        self._pending_images = []
        self.imagesChanged.emit()

    @Slot(result="QVariantList")
    def pendingImageUris(self):
        return ["data:%s;base64,%s" % (mt, b64) for (b64, mt) in self._pending_images]

    @Slot(int)
    def removeImage(self, idx):
        if 0 <= idx < len(self._pending_images):
            del self._pending_images[idx]
            self.imagesChanged.emit()

    @Slot()
    def pasteImage(self):
        if not self._current_model_supports_vision():
            self.toast.emit("当前模型不支持图片输入，已忽略图片")
            return
        try:
            try:
                from PySide6.QtGui import QGuiApplication
                from PySide6.QtCore import QBuffer, QByteArray, QIODevice
            except ImportError:
                from PySide2.QtGui import QGuiApplication
                from PySide2.QtCore import QBuffer, QByteArray, QIODevice
            img = QGuiApplication.clipboard().image()
            if img.isNull():
                self.toast.emit("剪贴板中没有图片")
                return
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.WriteOnly)
            img.save(buf, "PNG")
            self._pending_images.append((base64.b64encode(bytes(ba)).decode("ascii"), "image/png"))
            self.imagesChanged.emit()
        except Exception as e:
            print("[controller] pasteImage failed:", e)
            self.toast.emit("粘贴图片失败：%s" % e)

    # ---- input completers ----
    @Slot(result="QVariantList")
    def nodePaths(self):
        try:
            import hou
        except Exception:
            return []
        out = []
        for root in ("/obj", "/out", "/mat", "/stage", "/ch"):
            n = hou.node(root)
            if not n:
                continue
            try:
                for c in n.allSubChildren():
                    out.append(c.path())
                    if len(out) > 500:
                        return out
            except Exception:
                pass
        return out

    @Slot(result="QVariantList")
    def slashCommands(self):
        return [
            {"cmd": "/clear", "desc": self.tr("清空对话")},
            {"cmd": "/new", "desc": self.tr("新建会话")},
        ]

    @Slot(str)
    def runSlash(self, cmd):
        if cmd == "/clear":
            self.clearChat()
        elif cmd == "/new":
            self.newSession()

    def _load_custom(self):
        """Re-apply persisted custom-provider config to the client on startup."""
        try:
            s = QSettings("HoudiniAI", "Assistant")
            url = s.value("custom_url", "")
            model = s.value("custom_model", "")
            if url and model:
                if self._is_placeholder_custom_url(str(url)):
                    print("[controller] ignoring placeholder custom provider URL:", url)
                    return
                ctx = self._to_int(s.value("custom_context_limit", 128000), 128000)
                MODEL_MAP["custom"] = [model]
                CONTEXT_LIMITS[model] = ctx
                if self._session:
                    key = s.value("custom_key", "")
                    proto = str(s.value("custom_proto", "openai")) == "anthropic"
                    self._session.client.set_custom_provider(url, key, True, proto)
        except Exception as e:
            print("[controller] load custom failed:", e)

    @staticmethod
    def _is_placeholder_custom_url(url):
        try:
            from urllib.parse import urlparse
            p = urlparse((url or "").strip())
            host = (p.hostname or "").lower()
            if p.scheme not in ("http", "https") or not host:
                return True
            return host in ("example.com", "example.org", "example.net")
        except Exception:
            return True

    def _revert_provider_from_custom(self):
        # custom not configured -> fall back to a real provider
        self._provider = "duojie"
        self._model_name = MODEL_MAP["duojie"][0]
        self.providerChanged.emit()
        self.modelChanged.emit()
        self._save_prefs()

    def _open_custom_provider(self):
        if not self._session:
            self._revert_provider_from_custom()
            return
        try:
            cur = QSettings("HoudiniAI", "Assistant")
            self.openCustomProviderDialog.emit(
                str(cur.value("custom_url", "")),
                str(cur.value("custom_key", "")),
                str(cur.value("custom_model", "")),
                str(cur.value("custom_proto", "openai")) == "anthropic",
                str(cur.value("custom_context_limit", "128000")),
                self._qbool(cur.value("custom_supports_vision", False), False),
            )
        except Exception as e:
            self._info("Custom Provider", "设置失败：%s" % e)
            if not MODEL_MAP.get("custom"):
                self._revert_provider_from_custom()

    @Slot(str, str, str, bool, str, bool, result=bool)
    def submitCustomProvider(self, url, key, model, anthropic, context_limit, supports_vision=False):
        if not self._session:
            self._revert_provider_from_custom()
            return False
        url = (url or "").strip()
        key = (key or "").strip()
        model = (model or "").strip()
        ctx = self._to_int(context_limit, 128000)
        if ctx <= 0:
            ctx = 128000
        if not url or not model:
            if not MODEL_MAP.get("custom"):
                self._revert_provider_from_custom()
            self._info("Custom Provider", "API URL 和 Model name 不能为空。")
            return False
        if self._is_placeholder_custom_url(url):
            self._info("Custom Provider", "API URL 不能使用 example.com/example.org 等示例地址，请填写真实 API 地址。")
            return False
        try:
            self._session.client.set_custom_provider(url, key, True, bool(anthropic))
            MODEL_MAP["custom"] = [model]
            CONTEXT_LIMITS[model] = ctx
            self._provider = "custom"
            self._model_name = model
            s = QSettings("HoudiniAI", "Assistant")
            s.setValue("custom_url", url)
            s.setValue("custom_key", key)
            s.setValue("custom_model", model)
            s.setValue("custom_proto", "anthropic" if anthropic else "openai")
            s.setValue("custom_context_limit", str(ctx))
            s.setValue("custom_supports_vision", bool(supports_vision))
            self.providerChanged.emit()
            self.modelChanged.emit()
            self.tokensChanged.emit()
            self._save_prefs()
            self.toast.emit("已配置 Custom Provider：%s · %s ctx" % (model, self._fmt_k(ctx)))
            return True
        except Exception as e:
            self._info("Custom Provider", "设置失败：%s" % e)
            if not MODEL_MAP.get("custom"):
                self._revert_provider_from_custom()
            return False

    @Slot()
    def cancelCustomProvider(self):
        if self._provider == "custom" and not MODEL_MAP.get("custom"):
            self._revert_provider_from_custom()

    # ---- node focus ----
    @Slot(str)
    def focusNode(self, path):
        p = str(path or "").strip()
        # web links are not node paths — open them in the browser
        if p.startswith(("http://", "https://", "www.")):
            url = p if "://" in p else ("https://" + p)
            try:
                try:
                    from PySide6.QtGui import QDesktopServices
                    from PySide6.QtCore import QUrl
                except ImportError:
                    from PySide2.QtGui import QDesktopServices
                    from PySide2.QtCore import QUrl
                QDesktopServices.openUrl(QUrl(url))
            except Exception as e:
                print("[controller] open url failed:", e)
                self.toast.emit("无法打开链接：" + url)
            return
        try:
            import hou
        except Exception:
            self.toast.emit("当前不在 Houdini 环境，无法定位节点")
            return
        try:
            n = hou.node(path)
            if not n:
                self.toast.emit("找不到节点：" + str(path))
                return
            n.setCurrent(True, clear_all_selected=True)
            ed = hou.ui.curDesktop().paneTabOfType(hou.paneTabType.NetworkEditor)
            if ed and n.parent():
                ed.cd(n.parent().path())
                try:
                    ed.homeToSelection()
                except Exception:
                    pass
        except Exception as e:
            print("[controller] focusNode failed:", e)
            self.toast.emit("定位节点失败：%s" % e)

    # ---- persistence ----
    def _save_all(self):
        if not self._cache_dir:
            return
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            manifest = {"active": self._active,
                        "sessions": [{"id": s["id"], "title": s["title"],
                                      "provider": s.get("provider", self._provider),
                                      "model": s.get("model", self._model_name)}
                                     for s in self._sessions]}
            (self._cache_dir / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            for s in self._sessions:
                data = {"id": s["id"], "title": s["title"], "rows": s["rows"], "history": s["history"],
                        "provider": s.get("provider", self._provider),
                        "model": s.get("model", self._model_name)}
                (self._cache_dir / ("session_%s.json" % s["id"])).write_text(
                    json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
        except Exception as e:
            print("[controller] save sessions failed:", e)

    @Slot()
    def restore(self):
        if not self._cache_dir:
            return
        try:
            mpath = self._cache_dir / "manifest.json"
            if not mpath.exists():
                return
            manifest = json.loads(mpath.read_text(encoding="utf-8"))
            loaded = []
            for entry in manifest.get("sessions", []):
                sid = entry.get("id")
                p = self._cache_dir / ("session_%s.json" % sid)
                if p.exists():
                    d = json.loads(p.read_text(encoding="utf-8"))
                    loaded.append({"id": d.get("id", sid), "title": d.get("title", "新对话"),
                                   "rows": d.get("rows", []), "history": d.get("history", []),
                                   "provider": d.get("provider", entry.get("provider", self._provider)),
                                   "model": d.get("model", entry.get("model", self._model_name))})
            if loaded:
                self._sessions = loaded
                self._active = min(int(manifest.get("active", 0)), len(loaded) - 1)
                self._load_active()
                self.sessionsChanged.emit()
        except Exception as e:
            print("[controller] restore failed:", e)

    @Slot()
    def refreshContext(self):
        try:
            import hou
            net = None
            try:
                net = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor).pwd()
            except Exception:
                pass
            sel = hou.selectedNodes()
            self._scene_path = net.path() if net else "/obj"
            self._scene_sel = ("%d node%s selected" % (len(sel), "" if len(sel) == 1 else "s")) if sel else "no selection"
        except Exception:
            ctx = None
            try:
                bridge = getattr(getattr(self._session, "bridge", None), "scene_context", None)
                ctx = bridge() if bridge else None
            except Exception:
                ctx = None
            if isinstance(ctx, dict):
                self._scene_path = ctx.get("path") or "/obj"
                self._scene_sel = ctx.get("selection") or "no selection"
            else:
                self._scene_sel = "Houdini 未连接"
        self.sceneChanged.emit()

    @Slot()
    def stop(self):
        if self._session:
            self._session.stop()
        self.toast.emit("正在请求停止…当前工具完成/超时后会停下")

    @Slot(str, result=bool)
    def send(self, text):
        text = (text or "").strip()
        if not text or self._running:
            if self._running:
                self.toast.emit("当前正在运行，先停止或等待完成后再发送")
            return False
        # name the session from its first user message
        if self._sessions and self._sessions[self._active]["title"] == "新对话":
            self._sessions[self._active]["title"] = text[:14]
            self.sessionsChanged.emit()
        supports_vision = self._current_model_supports_vision()
        # 记下最近一次附带的图片(data URI)，供 meshy_image_to_image 做图生图时引用。
        # 只在【本轮确实有附图】时更新——这样"先传图、下一轮再说改图"也能拿到图，
        # 不会因为后续纯文字轮把它清空（之前的 bug 就是这样丢图的）。
        if self._pending_images:
            self._last_attached_images = [
                "data:%s;base64,%s" % (mt, b64) for (b64, mt) in self._pending_images
            ]
        images = self._pending_images if supports_vision else []
        if self._pending_images and not supports_vision:
            self.toast.emit("当前模型不支持图片输入，本次只发送文本（但图片仍可用于 Meshy 图生图）")
        payload = {"text": text}
        # 在用户气泡里显示附带的缩略图（无论模型是否支持视觉，用户都该看到自己上传了什么）
        if self._pending_images:
            payload["images"] = ["data:%s;base64,%s" % (mt, b64)
                                 for (b64, mt) in self._pending_images]
        self._model.append({"type": "user", "payload": payload})
        self._log_chat("USER", text)
        self._pending_images = []
        if images or not supports_vision:
            self.imagesChanged.emit()
        if self._session and self._mode == "Plan":
            self._plan_phase = "planning"
            self._start_run(text, tools=self._planning_tools(), max_iter=20, images=images)
        elif self._session and self._mode == "Ask":
            # Ask = read-only: restrict to non-mutating tools
            self._plan_phase = "idle"
            self._start_run(text, tools=self._ask_tools(), max_iter=15, images=images)
        else:
            self._plan_phase = "idle"
            self._start_run(text, tools=None, max_iter=None, images=images)
        return True

    def _start_run(self, user_text, tools, max_iter, images=None):
        self._bm = BlockModel()
        self._ai_row = self._model.append({"type": "ai", "payload": {"bm": self._bm}})
        self._reset_run_state()
        self._cook_suspended = False
        self._t0 = time.monotonic()
        self._last_activity = self._t0
        self._watchdog_warned = False
        self._set_running(True)
        if not self._session:
            self._simulate(user_text)
            return
        supports_vision = self._current_model_supports_vision()
        threading.Thread(
            target=self._worker,
            args=(user_text, self._model_name, self._provider, self._mode, tools, max_iter, images, supports_vision),
            daemon=True,
        ).start()

    def _planning_tools(self):
        base = [t for t in self._session.tools
                if t.get("function", {}).get("name") in PLAN_READONLY]
        if PLAN_TOOL_ASK_QUESTION:
            base.append(PLAN_TOOL_ASK_QUESTION)
        if PLAN_TOOL_CREATE:
            base.append(PLAN_TOOL_CREATE)
        return base

    def _execution_tools(self):
        base = list(self._session.tools)
        if PLAN_TOOL_UPDATE_STEP:
            base.append(PLAN_TOOL_UPDATE_STEP)
        return base

    def _ask_tools(self):
        # read-only tools only (no create/delete/set/execute)
        return [t for t in self._session.tools
                if t.get("function", {}).get("name") in PLAN_READONLY]

    # ---- run state ----
    def _reset_run_state(self):
        self._blocks = []
        self._think_block = None
        self._answer_blocks = []   # prose/code segments (rebuilt live)
        self._exec_block = None
        self._todo_block = None
        self._meshy_ops = {}            # op id -> Meshy progress block dict
        self._concept_blocks = {}       # token -> concept-gallery block dict
        self._meshy_turn_cache = {}     # tool+args signature -> result (per-run dedup)
        self._preview_block = None      # streaming VEX preview
        self._planstream_block = None   # streaming plan preview
        self._think_text = ""
        self._prose_text = ""
        self._answer_dirty = False
        self._in_think = False
        self._cbuf = ""

    def _set_running(self, on):
        if on != self._running:
            self._running = on
            self.runningChanged.emit()

    def _touch_activity(self):
        self._last_activity = time.monotonic()
        self._watchdog_warned = False

    def _watchdog_tick(self):
        if not self._running:
            return
        idle = time.monotonic() - self._last_activity
        if idle > 45 and not self._watchdog_warned:
            self._watchdog_warned = True
            self.toast.emit("执行中超过 45 秒无进展，可点 Stop 中断。若在 cook，请按 Esc。")
        if idle > 180 and self._plan_phase == "executing" and self._session:
            self._session.stop()
            self.toast.emit("Plan 执行长时间无进展，已请求停止。")

    # ---- worker thread ----
    def _worker(self, text, model, provider, mode, tools, max_iter, images=None, supports_vision=False):
        def cb_content(t):
            self._last_activity = time.monotonic()
            vis, thk = self._split(t)
            if thk:
                self._sigThink.emit(thk)
            if vis:
                self._sigStatus.emit("generating")
                self._sigContent.emit(vis)

        def cb_think(t):
            if t:
                self._last_activity = time.monotonic()
                self._sigStatus.emit("thinking")
                self._sigThink.emit(t)

        def cb_tool_call(n, a):
            self._last_activity = time.monotonic()
            self._sigStatus.emit("tool:" + n)
            self._sigToolCall.emit(n, self._arg_preview(a))

        def cb_args_delta(name, delta, acc):
            self._last_activity = time.monotonic()
            if name == "create_plan":
                self._sigStatus.emit("planning")
                self._sigPlanStream.emit(acc)
            else:
                self._sigPreview.emit(name, acc)

        def cb_tool_result(n, a, r):
            self._last_activity = time.monotonic()
            ok = bool(r.get("success")) if isinstance(r, dict) else False
            detail = ""
            if isinstance(r, dict):
                detail = str(r.get("result") or r.get("error") or "")
            self._sigToolResult.emit(n, ok, detail[:400])
            self._collect_node_names(a, r)
            if n in ("execute_python", "execute_shell") and isinstance(r, dict):
                self._sigShell.emit(json.dumps({
                    "kind": n, "code": (a.get("code") or a.get("command") or ""),
                    "output": str(r.get("result", "")), "error": str(r.get("error", "")),
                    "success": ok,
                }, default=str))
            if ok and isinstance(r, dict):
                self._emit_node_ops(n, r)
                img = r.get("_viewport_image")
                if img:
                    mt = r.get("_image_media_type", "image/jpeg")
                    self._sigImage.emit("data:%s;base64,%s" % (mt, img))

        try:
            self._log_external("Worker start provider=%s model=%s mode=%s" % (provider, model, mode))
            result = self._session.run(
                text, model, provider, mode,
                {"on_content": cb_content, "on_thinking": cb_think,
                 "on_tool_call": cb_tool_call, "on_tool_result": cb_tool_result,
                 "on_tool_args_delta": cb_args_delta,
                 "on_iteration_start": lambda i: self._sigStatus.emit("generating")},
                context_limit=CONTEXT_LIMITS.get(model, 128000),
                enable_thinking=True,
                supports_vision=supports_vision,
                tools=tools, max_iter=max_iter,
                images=images, rag=True, memory=self._memory_enabled,
            )
            if isinstance(result, dict) and not result.get("ok", True):
                err = str(result.get("error") or result.get("final_content") or "AI request failed")
                self._log_external("Worker result error: " + err[:1000])
                self._sigDone.emit(err)
                return
            self._accumulate_usage(result)
            self._sigDone.emit("")
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(tb)
            self._log_external("Worker exception: %s\n%s" % (e, tb))
            self._sigDone.emit(str(e))

    def _accumulate_usage(self, result):
        if not isinstance(result, dict):
            return
        u = result.get("usage") or {}
        s = self._token_stats
        s["input"] += u.get("prompt_tokens", 0)
        s["output"] += u.get("completion_tokens", 0)
        s["reasoning"] += u.get("reasoning_tokens", 0)
        s["cache_read"] += u.get("cache_hit_tokens", 0)
        s["cache_write"] += u.get("cache_miss_tokens", 0)
        s["total"] += u.get("total_tokens", 0)
        recs = result.get("call_records")
        if isinstance(recs, list):
            self._call_records.extend(recs)
            s["requests"] += max(1, len(recs))
        else:
            s["requests"] += 1
        # note: _refresh_tokens() is invoked on the main thread in _ui_done

    # ---- tool executor (called by AIClient on the worker thread) ----
    def _tool_executor(self, tool_name, **kwargs):
        self._last_activity = time.monotonic()
        if self._session and self._session.client.is_stop_requested():
            return {"success": False, "error": "stopped"}
        # plan / ask tools are UI-driven, never hit Houdini
        if tool_name == "create_plan":
            return self._handle_create_plan(kwargs)
        if tool_name == "update_plan_step":
            return self._handle_update_plan_step(kwargs)
        if tool_name == "ask_question":
            return self._handle_ask_question(kwargs)
        if tool_name in ("add_todo", "update_todo"):
            return self._handle_todo(tool_name, kwargs)
        # 后台任务进度查询（本地、免费，不进 API）
        if tool_name == "meshy_task_status":
            return self._meshy_task_status(kwargs)
        # per-tool approval (confirm mode)
        if self._confirm_mode and tool_name in CONFIRM_TOOLS:
            if not self._await_confirm(tool_name, kwargs):
                return {"success": False, "error": "用户取消了该操作"}
        # Meshy 网络工具：app 侧后台线程执行，绝不进 Houdini/bridge
        if tool_name in MESHY_NETWORK_TOOLS and _meshy is not None:
            # 同一轮内重复的生成调用去重：防止模型/中转站把同一调用发两次
            # 导致重复弹画廊、重复生成、重复扣费。
            try:
                sig = tool_name + "|" + json.dumps(kwargs, sort_keys=True, default=str)
            except Exception:
                sig = tool_name
            cache = getattr(self, "_meshy_turn_cache", None)
            if cache is not None and sig in cache:
                print("[meshy] 同轮去重：跳过重复调用 %s" % tool_name)
                return cache[sig]
            if tool_name in MESHY_INTERACTIVE_TOOLS:
                mode = "concept" if tool_name == "meshy_concept_to_3d" else "image"
                res = self._run_meshy_concept(kwargs, mode=mode, tool_name=tool_name)
            else:
                cnt = int(kwargs.get("count", 1) or 1)
                if cnt > 1 and tool_name in MESHY_BATCH_TOOLS:
                    res = self._run_meshy_batch(tool_name, kwargs, cnt)
                else:
                    res = self._run_meshy_network(tool_name, kwargs)
            # 只缓存成功结果：取消/失败不缓存，便于用户重试
            if cache is not None and isinstance(res, dict) and res.get("success"):
                cache[sig] = res
            # 生成成功后若资产库开着，自动刷新（新模型已落到 Meshy 云端）
            if (isinstance(res, dict) and res.get("success")
                    and self._library_open and tool_name != "meshy_balance"
                    and not self._library_busy):
                try:
                    self.refreshLibrary()
                except Exception:
                    pass
            return res
        if getattr(self._session, "bridge", None) is not None:
            try:
                res = self._session.mcp.execute_tool(tool_name, kwargs)
                self._last_activity = time.monotonic()
                return res
            except Exception as e:
                return {"success": False, "error": str(e)}
        if tool_name in BG_SAFE:
            try:
                res = self._session.mcp.execute_tool(tool_name, kwargs)
                self._last_activity = time.monotonic()
                return res
            except Exception as e:
                return {"success": False, "error": str(e)}
        # Houdini tools → main thread (blocking)
        import json
        with self._tool_lock:
            while not self._tool_q.empty():
                try:
                    self._tool_q.get_nowait()
                except queue.Empty:
                    break
            self._sigToolExec.emit(tool_name, json.dumps(kwargs, ensure_ascii=False))
            try:
                res = self._tool_q.get(timeout=75)
                self._last_activity = time.monotonic()
                return res
            except queue.Empty:
                if self._session:
                    self._session.stop()
                return {"success": False, "error": "tool execution timed out (75s); 已请求停止以避免卡死"}

    def _run_meshy_network(self, tool_name, kwargs):
        """Run a Meshy network tool, streaming progress to a MeshyCard. Runs the
        actual polling on a sub-thread so the user can hit 转入后台: the tool then
        returns to the agent immediately and the result is delivered as a new
        message when it finishes. Never touches Houdini / the bridge."""
        import uuid
        op = uuid.uuid4().hex
        label = MESHY_LABELS.get(tool_name, tool_name)
        bg_evt = threading.Event()
        self._meshy_bg_requests[op] = bg_evt
        self._meshy_bg[op] = {"op": op, "tool": tool_name, "label": label,
                              "stage": "提交", "progress": 0, "status": "PENDING",
                              "done": False, "ok": None, "summary": ""}

        def emit(stage, progress, status, done=False, ok=True, thumb="", summary="",
                 background=False):
            t = self._meshy_bg.get(op)
            if t is not None:
                t.update(stage=stage, progress=int(progress), status=status,
                         done=done, ok=ok, summary=summary or t.get("summary", ""))
            try:
                self._sigMeshyProgress.emit(json.dumps(
                    {"op": op, "tool": tool_name, "stage": stage,
                     "progress": int(progress), "status": status, "done": done,
                     "ok": ok, "thumb": thumb, "summary": summary,
                     "background": background, "backgroundable": not done}, default=str))
            except Exception:
                pass

        def on_progress(stage, progress, status):
            emit(stage, progress, status)
            try:
                self._sigStatus.emit("tool:%s · %s %d%%" % (tool_name, stage, progress))
            except Exception:
                pass

        def should_stop():
            if bg_evt.is_set():
                return False   # 已转后台：不随 agent 的停止 / 新一轮而中断
            try:
                return bool(self._session and self._session.client.is_stop_requested())
            except Exception:
                return False

        emit("提交", 0, "PENDING")
        holder = {}
        done_evt = threading.Event()

        def work():
            try:
                res = _meshy.run_network(tool_name, kwargs,
                                         on_progress=on_progress, should_stop=should_stop)
            except Exception as e:
                res = {"success": False, "error": "Meshy 执行异常: %s" % e}
            holder["res"] = res
            if bg_evt.is_set():
                # 已转后台：不再更新卡片（避免与新一轮的 block 错位），结果只发给 agent
                self._deliver_bg_result(op, tool_name, res)
            else:
                ok = bool(res.get("success"))
                data = res.get("data") or {}
                summary = res.get("result") if ok else res.get("error", "")
                emit("完成" if ok else "失败", 100 if ok else 0,
                     "SUCCEEDED" if ok else "FAILED", done=True, ok=ok,
                     thumb=data.get("thumbnail") or "", summary=summary or "")
            done_evt.set()

        threading.Thread(target=work, daemon=True).start()

        # 在 agent 线程上等待：要么任务完成（内联返回），要么用户点了"转入后台"
        while True:
            if done_evt.wait(timeout=0.15):
                self._meshy_bg_requests.pop(op, None)
                if not bg_evt.is_set():
                    self._meshy_bg.pop(op, None)   # 内联完成的不留在后台列表里
                self._last_activity = time.monotonic()
                return holder.get("res", {"success": False, "error": "未知错误"})
            if bg_evt.is_set():
                self._meshy_bg_requests.pop(op, None)
                # 卡片立刻冻结为"已转入后台"终态：不再更新、按钮消失、无法重复点击
                prog = int((self._meshy_bg.get(op) or {}).get("progress", 0))
                emit("已转入后台", prog, "后台", done=True, ok=True, background=True,
                     summary="已转入后台运行，完成后结果会自动发给 Agent（此卡片不再更新）。")
                self._last_activity = time.monotonic()
                return {"success": True, "error": "",
                        "result": ("已将【%s】转入后台运行（任务号 %s）。你现在可以继续做"
                                   "别的事；该任务完成后，我会把结果自动作为新消息发给你。"
                                   "期间如需确认进度可调用 meshy_task_status。"
                                   % (label, op[:8])),
                        "data": {"background": True, "op": op}}

    @Slot(str)
    def backgroundMeshyTask(self, op):
        """QML -> 把某个正在跑的 Meshy 任务转入后台（不再阻塞 agent）。"""
        evt = self._meshy_bg_requests.get(op)
        if evt is not None:
            evt.set()
            self.toast.emit(self.tr("已转入后台，完成后自动通知"))

    def _enqueue_bg_feedback(self, text, label=""):
        """把一条后台完成结果排队，并通知主线程投递给 agent。"""
        with self._meshy_bg_lock:
            self._meshy_bg_feedback.append(text)
        try:
            self.toast.emit(self.tr("后台任务完成") + (("：" + label) if label else ""))
        except Exception:
            pass
        try:
            self._sigDeliverBg.emit()   # 切到主线程投递
        except Exception:
            pass

    def _deliver_bg_result(self, op, tool_name, res):
        """后台单任务完成：组织反馈文本并排队（agent 空闲时作为新一轮投递）。"""
        label = MESHY_LABELS.get(tool_name, tool_name)
        ok = bool(res.get("success"))
        summary = (res.get("result") if ok else res.get("error", "")) or ""
        text = ("【后台任务完成】%s（任务号 %s）%s\n%s"
                % (label, op[:8], "成功" if ok else "失败", summary))
        if ok:
            text += "\n请根据上面的结果继续后续步骤（例如调用 import_3d_asset 导入到 Houdini）。"
        t = self._meshy_bg.get(op)
        if t is not None:
            t["done"] = True
            t["ok"] = ok
        self._enqueue_bg_feedback(text, label)

    def _await_or_background(self, op, runnable, on_bg_complete):
        """在子线程跑 runnable()，在 agent 线程上等待：要么完成（返回
        ('done', result, error)），要么用户点了"转入后台"（立即返回 ('bg', None, None)，
        子线程继续，完成时回调 on_bg_complete(result, error)）。"""
        holder = {}
        done_evt = threading.Event()
        evt = self._meshy_bg_requests.get(op) or threading.Event()

        def work():
            try:
                holder["res"] = runnable()
            except Exception as e:
                holder["err"] = e
            done_evt.set()
            if evt.is_set():
                try:
                    on_bg_complete(holder.get("res"), holder.get("err"))
                except Exception as ex:
                    print("[meshy] gallery bg deliver failed:", ex)

        threading.Thread(target=work, daemon=True).start()
        while True:
            if done_evt.wait(timeout=0.15):
                return ("done", holder.get("res"), holder.get("err"))
            if evt.is_set():
                return ("bg", None, None)

    @Slot()
    def _flush_bg_feedback(self):
        """主线程：若 agent 空闲，则把已完成的后台任务结果作为新一轮发给它继续。"""
        if self._running:
            return   # 当前还在跑——等这轮结束后由 _ui_done 再调用一次
        with self._meshy_bg_lock:
            if not self._meshy_bg_feedback:
                return
            batch = list(self._meshy_bg_feedback)
            self._meshy_bg_feedback = []
            # 清掉已完成且已投递的后台任务
            for k in [k for k, v in self._meshy_bg.items() if v.get("done")]:
                self._meshy_bg.pop(k, None)
        text = "\n\n".join(batch)
        if not self._session:
            # 无后端（预览）时只提示
            self.toast.emit(self.tr("后台任务完成"))
            return
        # 静默喂给 agent：不往聊天窗口里塞这条原始后台反馈（避免困扰用户）。
        # agent 收到后据此继续，它后续的动作/回复会正常显示。
        self._log_chat("BG", text)
        self._start_run(text, tools=None, max_iter=None, images=None)

    @Slot(result="QVariantMap")
    def _meshy_task_status(self, kwargs):
        op = (kwargs or {}).get("op") or (kwargs or {}).get("task_id")
        items = list(self._meshy_bg.values())
        if op:
            op = str(op)
            items = [t for t in items if t.get("op", "").startswith(op) or t.get("op") == op]
            if not items:
                return {"success": True, "error": "",
                        "result": "没找到任务 %s（可能已完成并已把结果发给你了）。" % op}
        if not items:
            return {"success": True, "error": "",
                    "result": "当前没有正在后台运行的 Meshy 任务。", "data": {"tasks": []}}
        lines = ["当前后台 Meshy 任务："]
        for t in items:
            lines.append("- %s · 任务号 %s · %s · %d%% · %s%s"
                         % (t.get("label"), (t.get("op") or "")[:8], t.get("stage"),
                            int(t.get("progress", 0)), t.get("status"),
                            "（已完成，结果即将发给你）" if t.get("done") else ""))
        return {"success": True, "error": "", "result": "\n".join(lines),
                "data": {"tasks": items}}

    def _run_meshy_concept(self, kwargs, mode="concept", tool_name=""):
        """Concept/image gallery flow (human-in-the-loop, worker thread): generate
        N images in parallel -> gallery (multi-select + editable prompt + regenerate
        + 二次图生图编辑) -> finish with images (mode 'image') or image-to-3d for the
        chosen ones (mode 'concept'). Loops in-place (no LLM round-trip). If reference
        images are supplied (meshy_image_to_image, or the user picks 'edit'),
        generation goes through Meshy image-to-image."""
        maxp = getattr(_meshy, "MAX_PARALLEL", 4)
        prompt = (kwargs.get("prompt") or "").strip()
        prompts_in = kwargs.get("prompts")
        if isinstance(prompts_in, (list, tuple)) and any(str(p).strip() for p in prompts_in):
            # 多个不同提示词，各并行出一张
            prompt_list = [str(p).strip() for p in prompts_in if str(p).strip()][:maxp]
        elif prompt:
            # 单一提示词的 count 个变体
            cnt = max(1, min(int(kwargs.get("count", 2) or 2), maxp))
            prompt_list = [prompt] * cnt
        else:
            return {"success": False, "error": "缺少 prompt 或 prompts"}
        card_prompt = prompt or prompt_list[0]
        ai_model = kwargs.get("ai_model", "nano-banana")
        aspect_ratio = kwargs.get("aspect_ratio", "1:1")
        enable_pbr = bool(kwargs.get("enable_pbr", True))
        topology = kwargs.get("topology", "triangle")
        poly = int(kwargs.get("target_polycount", 30000) or 30000)

        # 初始参考图（meshy_image_to_image 用 image/images）：有则首轮即走图生图
        refs_in = kwargs.get("images")
        if not (isinstance(refs_in, (list, tuple)) and refs_in):
            single = kwargs.get("image")
            refs_in = [single] if single else []
        base_refs = [r for r in refs_in if r] or None
        # 图生图：用户附带的图就是要编辑的对象，优先用它——即使模型自己瞎填/编造了
        # 一个 image 路径或 URL（实测模型常编一个不存在的 URL）。只有在完全没有附图时，
        # 才采用模型显式给的 image（比如让它编辑之前生成的某张概念图的本地路径）。
        if tool_name == "meshy_image_to_image":
            attached = list(getattr(self, "_last_attached_images", []) or [])
            if attached:
                base_refs = attached[:5]
            if base_refs is None:
                return {"success": False,
                        "error": "meshy_image_to_image 需要参考图：请在对话中附带一张图片，"
                                 "或用 image 参数给出图片的本地路径/URL。"}

        self._int_seq += 1
        token = "concept%d" % self._int_seq
        q = queue.Queue()
        self._interactive[token] = q

        # 注册为可后台化任务（用 token 当 op）：长生成阶段可「转入后台」
        label = MESHY_LABELS.get(tool_name, "图片生成")
        bg_evt = threading.Event()
        self._meshy_bg_requests[token] = bg_evt
        self._meshy_bg[token] = {"op": token, "tool": tool_name or "meshy_image_to_image",
                                 "label": label, "stage": "生成图片", "progress": 0,
                                 "status": "IN_PROGRESS", "done": False, "ok": None, "summary": ""}
        _STAGE = {"gen": "生成图片", "making3d": "生成3D", "pick": "等待选择",
                  "done": "完成", "cancelled": "已取消", "background": "已转入后台"}

        def show(**fields):
            t = self._meshy_bg.get(token)
            if t is not None:
                if "progress" in fields:
                    try:
                        t["progress"] = int(fields["progress"])
                    except Exception:
                        pass
                ph = fields.get("phase")
                if ph:
                    t["stage"] = _STAGE.get(ph, ph)
            payload = {"token": token, "mode": mode, "op": token}
            payload.update(fields)
            try:
                self._sigConcept.emit(json.dumps(payload, default=str))
            except Exception:
                pass

        def should_stop():
            if bg_evt.is_set():
                return False   # 已转后台：不随 agent 的停止 / 新一轮而中断
            try:
                return bool(self._session and self._session.client.is_stop_requested())
            except Exception:
                return False

        def imgs(concepts):
            return [{"index": c["index"], "image": c["image"], "prompt": c.get("prompt", "")}
                    for c in concepts]

        # 待生成请求 {prompts, refs}：首轮取初始提示词（图生图工具带初始参考图）；
        # 之后由 regenerate / edit 重新设置。refs 非空 → 走图生图。
        pending = {"prompts": prompt_list, "refs": base_refs}
        concepts = []

        try:
            while True:
                if should_stop():
                    show(phase="cancelled", note="已停止")
                    return {"success": False, "error": "stopped"}

                if pending is not None:
                    gen_prompts = pending["prompts"]
                    gen_refs = pending.get("refs")
                    gen_per_refs = pending.get("per_refs")
                    gen_count = max(1, len(gen_prompts))
                    pending = None
                    editing = bool(gen_refs or gen_per_refs)
                    note0 = (("图生图编辑中 · %d 张…" % gen_count) if editing
                             else ("并行生成 %d 张图片…" % gen_count))
                    # render N empty slots up-front so parallel generation is visible
                    show(phase="gen", prompt=card_prompt, count=gen_count, progress=0,
                         images=[], selected=[], note=note0)
                    done_imgs = []

                    def on_img(c, _n=gen_count):
                        done_imgs.append({"index": c["index"], "image": c["image"],
                                          "prompt": c.get("prompt", "")})
                        show(phase="gen", prompt=card_prompt, count=_n,
                             images=list(done_imgs), progress=len(done_imgs) * 100 // _n,
                             note="%d/%d 完成" % (len(done_imgs), _n))

                    def deliver_gen_bg(res, err):
                        # 后台生成完成：在独立消息行弹出完整可交互画廊，让用户照常挑选
                        if err or not res:
                            self._enqueue_bg_feedback(
                                "【后台任务】%s 失败：%s" % (label, err or "未知"), label)
                            return
                        concepts_full = [{"index": c["index"], "image": c["image"],
                                          "prompt": c.get("prompt", "")} for c in res]
                        with self._meshy_bg_lock:
                            self._bg_galleries[token] = {
                                "concepts": concepts_full, "mode": mode,
                                "card_prompt": card_prompt, "tool": tool_name, "label": label}
                            self._meshy_bg.pop(token, None)   # 不再算"运行中"
                        self._sigShowBgGallery.emit(token)

                    status, concepts, gerr = self._await_or_background(
                        token,
                        lambda: _meshy.generate_concepts(
                            gen_prompts, ai_model, aspect_ratio=aspect_ratio,
                            reference_images=gen_refs, per_prompt_refs=gen_per_refs,
                            on_progress=lambda s, p, st: None,
                            on_image=on_img, should_stop=should_stop),
                        deliver_gen_bg)
                    if status == "bg":
                        # 转后台：这张卡片定格（显示已生成的部分），完成后会另弹一张可交互画廊
                        show(phase="background", prompt=card_prompt,
                             images=list(done_imgs),
                             note="已转入后台运行中…完成后会在下方弹出可挑选的画廊")
                        return {"success": True, "error": "",
                                "result": ("已将【%s】的概念图生成转入后台（任务号 %s）。完成后会"
                                           "【自动弹出可交互画廊让用户挑选】——你无需处理这批图，"
                                           "继续响应用户的其他需求即可。" % (label, token[:8])),
                                "data": {"background": True, "op": token}}
                    if gerr:
                        show(phase="cancelled", note="生成失败: %s" % gerr)
                        return {"success": False, "error": "图片生成失败: %s" % gerr}

                show(phase="pick", prompt=card_prompt, count=len(concepts),
                     images=imgs(concepts), selected=[], progress=100, note="")
                try:
                    decision = q.get(timeout=1800)
                except queue.Empty:
                    decision = {"action": "cancel"}
                action = (decision or {}).get("action")

                if action == "regenerate":
                    # 不直接拿用户文字调 API：把反馈交回 Agent，让它理解意图、改写提示词后重做
                    fb = (decision.get("prompt") or "").strip()
                    gen_tool = "meshy_image_to_image" if base_refs else "meshy_text_to_image"
                    show(phase="done", images=imgs(concepts), progress=100,
                         note="已把你的意见交给 Agent，优化提示词后重做…")
                    lines = ["【用户反馈 · 要求重做这批图】"]
                    lines.append("用户意见：%s" % fb if fb
                                 else "用户没满意但未给具体意见——请你判断如何改进。")
                    lines.append("当前提示词：%s" % card_prompt)
                    lines.append("请【理解用户意图、自行改写/优化提示词】（不要原样照搬用户字面），"
                                 "然后重新调用 %s 生成新的一批。" % gen_tool)
                    return {"success": True, "result": "\n".join(lines), "error": "",
                            "data": {"action": "regenerate", "feedback": fb,
                                     "prev_prompt": card_prompt}}

                if action == "edit":
                    # 不直接调 API：把"选中的图 + 用户改动意见"交回 Agent，由它写出更好的
                    # 图生图提示词，再调用 meshy_image_to_image(image=该图路径) 重做。
                    fb = (decision.get("prompt") or "").strip()
                    sel = [int(i) for i in (decision.get("selected") or [])]
                    sel_imgs = [c["image"] for c in concepts if c["index"] in sel]
                    if not sel_imgs:
                        show(phase="pick", prompt=card_prompt, count=len(concepts),
                             images=imgs(concepts), note="请先选中要编辑的图片，再点二次编辑")
                        continue
                    if not fb:
                        show(phase="pick", prompt=card_prompt, count=len(concepts),
                             images=imgs(concepts), note="请在提示词框写明想要的改动，再点二次编辑")
                        continue
                    show(phase="done", images=imgs(concepts), selected=sel, progress=100,
                         note="已把你的反馈交给 Agent，做二次编辑…")
                    lines = ["【用户反馈 · 二次编辑选中图】",
                             "用户希望的改动：%s" % fb,
                             "选中的参考图（本地路径）："]
                    lines += ["- %s" % p for p in sel_imgs]
                    lines.append("请理解意图、写出更好的英文编辑提示词，然后调用 "
                                 "meshy_image_to_image(image=\"<上面某个路径>\", prompt=\"<优化后的编辑提示>\") 重做"
                                 "（要同时参考多张就用 images=[...]）。")
                    return {"success": True, "result": "\n".join(lines), "error": "",
                            "data": {"action": "edit", "feedback": fb, "images": sel_imgs}}

                if action == "done":
                    # 2D-only: finish with the (selected, or all) images, no 3D
                    selset = set(int(i) for i in (decision.get("selected") or []))
                    kept = [c for c in concepts if c["index"] in selset] or concepts
                    show(phase="done", images=imgs(concepts),
                         selected=sorted(selset), progress=100,
                         note="已保留 %d 张图片" % len(kept))
                    lines = ["已生成 %d 张图片：" % len(kept)]
                    for c in kept:
                        lines.append("- %s" % c["image"])
                    lines.append("若要把某张做成 3D，调用 meshy_image_to_3d(image=\"<本地路径>\")；"
                                 "若要在某张基础上继续改图，调用 meshy_image_to_image(image=\"<本地路径>\", prompt=\"…\")。")
                    return {"success": True, "result": "\n".join(lines), "error": "",
                            "data": {"images": kept}}

                if action != "submit":
                    show(phase="cancelled", note="已取消")
                    return {"success": False, "error": "用户取消了图片选择"}

                sel = [int(i) for i in (decision.get("selected") or [])]
                chosen = [c for c in concepts if c["index"] in sel] or concepts[:1]
                show(phase="making3d", prompt=card_prompt, images=imgs(concepts),
                     selected=sel, progress=0, note="生成 %d 个 3D 模型中…" % len(chosen))

                _PROC_NOTE = (
                    "【流程说明 · 概念图→3D 已完成】用户在画廊里选中了图片，并选择把它们"
                    "【直接升级成 3D 模型】——这是预期内的流程。下面是最终产物：【3D 模型】"
                    "（不是概念图）。请直接用 import_3d_asset 导入，"
                    "【不要再重新生成概念图，也不要重做 2D 图】。\n")

                def _models_summary(results):
                    out = ["3D 生成完成，共 %d 个模型：" % len(results)]
                    for r in results:
                        g = r.get("glb"); td = r.get("texture_dir")
                        out.append("- glb: %s%s" % (g, (" · texture_dir: %s" % td) if td else ""))
                    out.append("请对每个 glb 调用 import_3d_asset 导入 Houdini。")
                    return "\n".join(out)

                def deliver_3d_bg(results, err):
                    # 后台 3D 完成：不再更新卡片（已冻结），只把模型路径 + 流程说明发给 agent
                    if err or not results:
                        self._enqueue_bg_feedback("【后台任务】%s 3D 生成失败：%s"
                                                  % (label, err or "未知"), label)
                        return
                    self._enqueue_bg_feedback("【后台任务完成】" + _PROC_NOTE + _models_summary(results), label)

                status, results, m3err = self._await_or_background(
                    token,
                    lambda: _meshy.concepts_to_3d(
                        chosen, enable_pbr=enable_pbr, topology=topology,
                        target_polycount=poly,
                        on_progress=lambda s, p, st: show(phase="making3d", progress=p,
                                                          note="%s %s" % (s, st),
                                                          images=imgs(concepts), selected=sel),
                        should_stop=should_stop),
                    deliver_3d_bg)
                if status == "bg":
                    # 冻结卡片为"已转入后台"终态
                    show(phase="background", prompt=card_prompt, images=imgs(concepts),
                         selected=sel, note="已转入后台运行中…3D 完成后结果会自动发给 Agent")
                    return {"success": True, "error": "",
                            "result": ("已将【%s 的 3D 生成】转入后台（任务号 %s）。完成后我会把"
                                       "模型路径自动发给你以便导入。" % (label, token[:8])),
                            "data": {"background": True, "op": token}}
                if m3err:
                    show(phase="cancelled", note="3D 生成失败: %s" % m3err)
                    return {"success": False, "error": "3D 生成失败: %s" % m3err}

                res_thumbs = [{"index": r.get("concept_index", i),
                               "image": r.get("thumbnail") or ""}
                              for i, r in enumerate(results)]
                show(phase="done", images=imgs(concepts), selected=sel,
                     results=res_thumbs, progress=100,
                     note="生成的 3D 模型 · %d" % len(results))
                return {"success": True, "result": _PROC_NOTE + _models_summary(results),
                        "error": "", "data": {"models": results}}
        finally:
            self._interactive.pop(token, None)
            self._meshy_bg_requests.pop(token, None)
            # 内联完成（未转后台）的画廊任务不留在后台列表里
            if not bg_evt.is_set():
                self._meshy_bg.pop(token, None)
            self._last_activity = time.monotonic()

    def _run_meshy_batch(self, tool_name, kwargs, count):
        """Parallel multi-generation for a batch-capable Meshy tool (count>1):
        generate N variants concurrently, fill gallery slots as each finishes,
        then show the result thumbnails. Returns all GLBs for the agent to import.
        Non-interactive (no pick step) — reuses the gallery card via _sigConcept."""
        count = max(1, min(int(count or 1), getattr(_meshy, "MAX_PARALLEL", 4)))
        self._int_seq += 1
        token = "batch%d" % self._int_seq
        labels = {"meshy_text_to_3d": "文生3D", "meshy_image_to_3d": "图生3D",
                  "meshy_retexture": "重打材质", "meshy_remesh": "重拓扑"}
        title = "%s ×%d" % (labels.get(tool_name, tool_name), count)

        def show(**fields):
            payload = {"token": token, "mode": "batch", "title": title}
            payload.update(fields)
            try:
                self._sigConcept.emit(json.dumps(payload, default=str))
            except Exception:
                pass

        def should_stop():
            try:
                return bool(self._session and self._session.client.is_stop_requested())
            except Exception:
                return False

        show(phase="gen", count=count, images=[], progress=0,
             note="并行生成 %d 个…" % count)
        done = []

        def on_item(i, data):
            done.append({"index": i, "image": data.get("thumbnail") or ""})
            show(phase="gen", count=count, images=list(done),
                 progress=len(done) * 100 // count,
                 note="%d/%d 完成" % (len(done), count))

        try:
            results = _meshy.run_network_parallel(
                tool_name, kwargs, count, on_item=on_item, should_stop=should_stop)
        except Exception as e:
            show(phase="cancelled", note="失败: %s" % e)
            return {"success": False, "error": "并行生成失败: %s" % e}

        thumbs = [{"index": i, "image": r.get("thumbnail") or ""}
                  for i, r in enumerate(results)]
        show(phase="done", images=thumbs, results=thumbs, progress=100,
             note="生成结果 · %d" % len(results))
        lines = ["并行生成完成，共 %d 个：" % len(results)]
        for r in results:
            g = r.get("glb")
            t = r.get("texture_dir")
            lines.append("- glb: %s%s" % (g, (" · texture_dir: %s" % t) if t else ""))
        lines.append("请对每个 glb 调用 import_3d_asset 导入 Houdini。")
        self._last_activity = time.monotonic()
        return {"success": True, "result": "\n".join(lines), "error": "",
                "data": {"models": results}}

    # tools that mutate the scene (need an undo group + node-change snapshot)
    _MUTATING = {"create_node", "create_nodes_batch", "create_wrangle_node", "delete_node",
                 "set_node_parameter", "connect_nodes", "copy_node", "batch_set_parameters",
                 "set_display_flag", "execute_python", "run_skill"}
    _MUTATING = _MUTATING | MESHY_MUTATING   # + import_3d_asset（导入会建节点）
    # tools that can trigger a scene cook (measure + report timing)
    _COOK_TRIGGERING = {"create_node", "create_nodes_batch", "create_wrangle_node",
                        "connect_nodes", "set_display_flag", "set_node_parameter",
                        "batch_set_parameters", "execute_python", "run_skill"}

    @Slot(str, str)
    def _on_tool_exec_main(self, name, kwargs_json):
        """Runs on the Qt main thread → safe to call hou.* / mcp.execute_tool.
        Wraps mutating tools in an undo group and snapshots the target network
        before/after to detect created/deleted nodes (locale-independent)."""
        import json
        self._touch_activity()
        try:
            kwargs = json.loads(kwargs_json) if kwargs_json else {}
        except Exception:
            kwargs = {}

        result = {"success": False, "error": "unknown"}
        use_grp = name in self._MUTATING
        # set_node_parameter changes no nodes (uses _undo_snapshot instead)
        should_snap = name in self._MUTATING and name not in ("save_hip", "set_node_parameter")
        try:
            import hou
        except Exception:
            hou = None

        target = self._target_network(kwargs) if (should_snap and hou) else None
        before = self._snapshot_children(target) if should_snap else {}

        try:
            if use_grp and hou:
                try:
                    hou.undos.beginGroup("AI Agent: %s" % name)
                except Exception:
                    use_grp = False
            res = self._session.mcp.execute_tool(name, kwargs)
            result = res if isinstance(res, dict) else {"success": False, "error": "tool returned non-dict"}
        except Exception as e:
            result = {"success": False, "error": str(e)}
        finally:
            if should_snap and result.get("success"):
                try:
                    after = self._snapshot_children(target)
                    ch = self._diff_children(before, after)
                    if ch:
                        result["_node_changes"] = ch
                except Exception:
                    pass
            if use_grp and hou:
                try:
                    hou.undos.endGroup()
                except Exception:
                    pass
            if (hou and self._cook_realtime and name in self._COOK_TRIGGERING
                    and isinstance(result, dict) and result.get("success")):
                try:
                    self._realtime_cook(result)
                except Exception:
                    pass
        self._touch_activity()
        self._tool_q.put(result)

    _SLOW_COOK_SEC = 12.0

    def _realtime_cook(self, result):
        """Cook display nodes with an interruptible progress dialog (Esc to cancel),
        measure time, and report it. On interrupt/slow cook, switch to Manual and
        suspend realtime cook for the rest of the run (avoids re-cook deadlock)."""
        if self._cook_suspended:
            return
        import time as _t
        try:
            import hou
        except Exception:
            return
        obj = hou.node("/obj")
        if obj is None:
            return
        targets = []
        for child in obj.children():
            try:
                if child.type().name() not in ("geo", "subnet"):
                    continue
                dn = child.displayNode()
                if dn is not None:
                    targets.append(dn)
            except Exception:
                pass
        if not targets:
            return

        interrupted = False
        budget = False
        cooked = False
        t0 = _t.time()
        try:
            with hou.InterruptableOperation("Cooking", long_operation_name="Houdini Agent: cooking",
                                            open_interrupt_dialog=True) as op:
                total = len(targets)
                for i, dn in enumerate(targets):
                    try:
                        a = _t.time()
                        dn.cook(force=False)
                        if _t.time() - a >= 0.05:
                            cooked = True
                    except hou.OperationInterrupted:
                        interrupted = True
                        break
                    except Exception:
                        pass
                    if (_t.time() - t0) >= self._SLOW_COOK_SEC:
                        budget = True
                        break
                    try:
                        op.updateProgress((i + 1) / float(total))
                    except Exception:
                        pass
        except hou.OperationInterrupted:
            interrupted = True
        except Exception as e:
            print("[Cook] interruptable op failed:", e)
            return

        elapsed = _t.time() - t0
        slow = elapsed >= self._SLOW_COOK_SEC or budget

        if interrupted or slow:
            try:
                if hou.updateModeSetting() != hou.updateMode.Manual:
                    hou.setUpdateMode(hou.updateMode.Manual)
            except Exception:
                pass
            self._cook_suspended = True

        note = ""
        if interrupted:
            note = "cook 已中断 (%.1fs) · 已切手动更新" % elapsed
        elif slow:
            note = "cook %.1fs · 较慢，已切手动更新（⋯ 可切回实时）" % elapsed
        elif cooked:
            note = "cook %.2fs" % elapsed
        if note:
            result["result"] = (str(result.get("result", "")) + " · " + note).strip(" ·")

    @staticmethod
    def _target_network(kwargs):
        try:
            import hou
        except Exception:
            return None
        p = kwargs.get("parent_path") or kwargs.get("parent")
        if p:
            return hou.node(p)
        np = kwargs.get("node_path") or kwargs.get("path")
        if np:
            n = hou.node(np)
            if n and n.parent():
                return n.parent()
        try:
            ed = hou.ui.curDesktop().paneTabOfType(hou.paneTabType.NetworkEditor)
            if ed:
                return ed.pwd()
        except Exception:
            pass
        return hou.node("/obj/geo1") or hou.node("/obj")

    @staticmethod
    def _snapshot_children(network):
        if network is None:
            return {}
        try:
            return {n.path(): {"name": n.name(), "type": n.type().name(), "path": n.path()}
                    for n in network.children()}
        except Exception:
            return {}

    @staticmethod
    def _diff_children(before, after):
        bp, ap = set(before), set(after)
        created = [after[p] for p in sorted(ap - bp)]
        deleted = [before[p] for p in sorted(bp - ap)]
        if not created and not deleted:
            return None
        return {"created": created, "deleted": deleted}

    # ---- node-operation rows + Keep/Undo ----
    def _emit_node_ops(self, name, r):
        ops = []
        nc = r.get("_node_changes")
        if isinstance(nc, dict):
            cre = [x.get("path") for x in nc.get("created", []) if x.get("path")]
            dele = [x.get("path") for x in nc.get("deleted", []) if x.get("path")]
            if cre:
                ops.append({"op": "create", "paths": cre})
            if dele:
                ops.append({"op": "delete", "paths": dele})
        if name == "set_node_parameter":
            snap = r.get("_undo_snapshot")
            if isinstance(snap, dict) and snap.get("node_path"):
                ops.append({"op": "modify", "paths": [snap["node_path"]], "snapshot": snap})
        for op in ops:
            self._op_seq += 1
            oid = str(self._op_seq)
            self._op_ctx[oid] = op
            self._sigNodeOp.emit(oid)

    @Slot(str)
    def _ui_node_op(self, oid):
        ctx = self._op_ctx.get(oid)
        if not ctx:
            return
        op = ctx.get("op")
        paths = ctx.get("paths", []) or []
        if op == "create":
            block = {"kind": "nodeop", "opId": oid, "badge": "+%d" % len(paths),
                     "text": "nodes created", "paths": " · ".join(paths)}
        elif op == "delete":
            block = {"kind": "nodeop", "opId": oid, "badge": "-%d" % len(paths),
                     "text": "nodes deleted", "paths": " · ".join(paths)}
        elif op == "modify":
            snap = ctx.get("snapshot") or {}
            block = {"kind": "nodeop", "opId": oid, "badge": "~",
                     "text": snap.get("param_name", ""),
                     "old": self._short_val(snap.get("old_value"), 60),
                     "new": self._short_val(snap.get("new_value"), 60),
                     "paths": " · ".join(paths)}
        else:
            return
        self._blocks.append(block)
        self._pending_ops += 1
        self.pendingOpsChanged.emit()
        self._flush()

    @Slot(str)
    def _ui_image(self, data_uri):
        self._blocks.append({"kind": "image", "src": data_uri})
        self._flush()

    @Slot(str)
    def _ui_status(self, phase):
        self._touch_activity()
        if phase != self._status_phase:
            self._status_phase = phase
            self.statusChanged.emit()

    # ---- todo card ----
    def _handle_todo(self, tool_name, kwargs):
        self._sigTodo.emit(json.dumps({
            "id": kwargs.get("todo_id", ""), "text": kwargs.get("text", ""),
            "status": kwargs.get("status", "pending")}, default=str))
        return {"success": True, "result": "todo %s updated" % kwargs.get("todo_id", "")}

    @Slot(str)
    def _ui_todo(self, js):
        try:
            d = json.loads(js)
        except Exception:
            return
        if self._todo_block is None:
            self._todo_block = {"kind": "todo", "items": []}
            self._blocks.append(self._todo_block)
        tid = d.get("id")
        for it in self._todo_block["items"]:
            if it["id"] == tid:
                if d.get("status"):
                    it["status"] = d["status"]
                if d.get("text"):
                    it["text"] = d["text"]
                break
        else:
            self._todo_block["items"].append(
                {"id": tid, "text": d.get("text", ""), "status": d.get("status", "pending")})
        self._flush()

    # ---- Meshy generation card ----
    @Slot(str)
    def _ui_meshy_progress(self, js):
        try:
            d = json.loads(js)
        except Exception:
            return
        op = d.get("op")
        block = self._meshy_ops.get(op)
        if block is None:
            block = {"kind": "meshy", "op": op, "tool": d.get("tool", ""),
                     "stage": "", "progress": 0, "status": "", "done": False,
                     "ok": True, "thumb": "", "summary": ""}
            self._meshy_ops[op] = block
            self._blocks.append(block)
        block["stage"] = d.get("stage", block["stage"])
        block["progress"] = d.get("progress", block["progress"])
        block["status"] = d.get("status", block["status"])
        if d.get("done"):
            block["done"] = True
            block["ok"] = bool(d.get("ok", True))
            block["thumb"] = d.get("thumb", "")
            block["summary"] = d.get("summary", "")
        self._flush()

    # ---- Meshy concept-gallery card ----
    @Slot(str)
    def _ui_concept(self, js):
        try:
            d = json.loads(js)
        except Exception:
            return
        token = d.get("token")
        if not token:
            return
        block = self._concept_blocks.get(token)
        if block is None:
            block = {"kind": "concept", "token": token, "phase": "gen",
                     "prompt": "", "images": [], "selected": [], "progress": 0,
                     "note": "", "count": 2}
            self._concept_blocks[token] = block
            self._blocks.append(block)
        for k, v in d.items():
            if k == "token":
                continue
            block[k] = v
        self._flush()

    @Slot(str, str)
    def resolveConcept(self, token, decision_json):
        """QML -> worker: {action:'submit',selected:[..]} | {action:'regenerate',prompt} | {action:'cancel'}"""
        try:
            d = json.loads(decision_json) if decision_json else {}
        except Exception:
            d = {}
        q = self._interactive.get(token)
        if q is not None:
            q.put(d)
            return
        # 后台跑完弹出的画廊：原阻塞流程已结束，挑选结果转成给 Agent 的指令
        if token in self._bg_galleries:
            self._handle_bg_gallery_decision(token, d)

    @Slot(str)
    def _show_bg_gallery(self, token):
        """后台概念图跑完：在一条独立消息行里弹出完整可交互画廊（agent 忙时排队）。"""
        g = self._bg_galleries.get(token)
        if not g:
            return
        if self._running:
            if token not in self._pending_bg_galleries:
                self._pending_bg_galleries.append(token)
            return
        concepts = g["concepts"]
        # 独立系统消息行承载这张画廊（不调 _start_run，避免触发 agent 轮次）
        self._bm = BlockModel()
        self._ai_row = self._model.append({"type": "ai", "payload": {"bm": self._bm}})
        block = {"kind": "concept", "token": token, "phase": "pick",
                 "mode": g.get("mode", "concept"), "prompt": g.get("card_prompt", ""),
                 "images": list(concepts), "selected": [], "progress": 100,
                 "count": len(concepts),
                 "note": "后台已生成 %d 张 · 勾选后可生成 3D / 二次编辑 / 换提示词" % len(concepts)}
        self._blocks = [block]
        self._concept_blocks = {token: block}
        self._do_flush()
        self.toast.emit("后台已生成 %d 张概念图，请在画廊里挑选" % len(concepts))

    def _drain_pending_bg_galleries(self):
        if self._running or not self._pending_bg_galleries:
            return
        pend = self._pending_bg_galleries
        self._pending_bg_galleries = []
        for tok in pend:
            if tok in self._bg_galleries:
                self._show_bg_gallery(tok)

    def _handle_bg_gallery_decision(self, token, d):
        """后台画廊（原阻塞已结束）里的挑选：统一转成给 Agent 的指令并发起新一轮。"""
        g = self._bg_galleries.get(token)
        if not g:
            return
        action = (d or {}).get("action")
        concepts = g["concepts"]
        card_prompt = g.get("card_prompt", "")

        def _blk(**fields):
            blk = self._concept_blocks.get(token)
            if blk is not None:
                blk.update(fields)
                self._do_flush()

        if action == "submit":
            sel = [int(i) for i in (d.get("selected") or [])]
            chosen = [c for c in concepts if c["index"] in sel] or concepts[:1]
            paths = [c["image"] for c in chosen]
            _blk(phase="done", selected=sel, note="已交给 Agent 生成 3D…")
            self._bg_galleries.pop(token, None)
            lines = ["【用户操作 · 把选中的概念图做成 3D】",
                     "用户在后台完成的画廊里选了这 %d 张图，要【直接升级成 3D 模型】"
                     "（不是要你重做 2D 图）：" % len(paths)]
            lines += ["- %s" % p for p in paths]
            lines.append("请对每张调用 meshy_image_to_3d(image=\"<上面的本地路径>\") 生成，"
                         "再用 import_3d_asset 导入 Houdini。")
            self._start_run("\n".join(lines), tools=None, max_iter=None, images=None)
            return

        if action == "edit":
            fb = (d.get("prompt") or "").strip()
            sel = [int(i) for i in (d.get("selected") or [])]
            sel_imgs = [c["image"] for c in concepts if c["index"] in sel]
            if not sel_imgs or not fb:
                _blk(note="请先选中图片并在提示词框写明想要的改动，再点二次编辑")
                return
            _blk(phase="done", selected=sel, note="已把反馈交给 Agent，做二次编辑…")
            self._bg_galleries.pop(token, None)
            lines = ["【用户反馈 · 二次编辑选中图】", "用户希望的改动：%s" % fb,
                     "选中的参考图（本地路径）："]
            lines += ["- %s" % p for p in sel_imgs]
            lines.append("请理解意图、写出更好的英文编辑提示词，再调用 "
                         "meshy_image_to_image(image=\"<上面某个路径>\", prompt=\"<优化后的编辑提示>\") 重做。")
            self._start_run("\n".join(lines), tools=None, max_iter=None, images=None)
            return

        if action == "regenerate":
            fb = (d.get("prompt") or "").strip()
            gen_tool = ("meshy_image_to_image" if g.get("tool") == "meshy_image_to_image"
                        else "meshy_text_to_image")
            _blk(phase="done", note="已把你的意见交给 Agent，优化提示词后重做…")
            self._bg_galleries.pop(token, None)
            lines = ["【用户反馈 · 要求重做这批图】"]
            lines.append("用户意见：%s" % fb if fb
                         else "用户没满意但未给具体意见——请你判断如何改进。")
            lines.append("当前提示词：%s" % card_prompt)
            lines.append("请【理解用户意图、自行改写/优化提示词】（不要原样照搬用户字面），"
                         "再重新调用 %s 生成新的一批。" % gen_tool)
            self._start_run("\n".join(lines), tools=None, max_iter=None, images=None)
            return

        if action == "done":
            selset = sorted(set(int(i) for i in (d.get("selected") or [])))
            _blk(phase="done", selected=selset,
                 note="已保留 %d 张图片" % (len(selset) or len(concepts)))
            self._bg_galleries.pop(token, None)
            return

        # cancel / 其他
        _blk(phase="cancelled", note="已取消")
        self._bg_galleries.pop(token, None)

    # ---- Meshy 云资产库（侧滑抽屉） ----
    def _exec_houdini_tool(self, name, kwargs):
        """从后台线程执行一个 Houdini 侧工具（走 bridge 或主线程阻塞队列）。
        与 _tool_executor 的 Houdini 分支同路径，但跳过确认门/停止门（用户已显式触发）。"""
        sess = self._session
        if sess is None:
            return {"success": False, "error": "无会话"}
        if getattr(sess, "bridge", None) is not None:
            try:
                return sess.mcp.execute_tool(name, kwargs)
            except Exception as e:
                return {"success": False, "error": str(e)}
        with self._tool_lock:
            while not self._tool_q.empty():
                try:
                    self._tool_q.get_nowait()
                except queue.Empty:
                    break
            self._sigToolExec.emit(name, json.dumps(kwargs, ensure_ascii=False))
            try:
                return self._tool_q.get(timeout=120)
            except queue.Empty:
                return {"success": False, "error": "导入超时（120s）"}

    @Slot()
    def toggleLibrary(self):
        self.setLibraryOpen(not self._library_open)

    @Slot(bool)
    def setLibraryOpen(self, on):
        on = bool(on)
        if on == self._library_open:
            return
        self._library_open = on
        self.libraryOpenChanged.emit()
        if on:
            # 每次打开都校验账号（拉余额）并重新拉取资产，保证看到的是最新状态
            self.syncMeshyAccount()
            if not self._library_busy:
                self.refreshLibrary()

    @Slot(result="QVariantList")
    def libraryItems(self):
        return list(self._library_items)

    @Slot()
    def refreshLibrary(self):
        self._library_page = 1
        self._load_library(page=1, append=False)

    @Slot()
    def loadMoreLibrary(self):
        self._load_library(page=self._library_page + 1, append=True)

    def _load_library(self, page, append):
        if _meshy is None:
            self.toast.emit(self.tr("Meshy 集成不可用"))
            return
        if self._library_busy:
            return
        try:
            from houdini_agent.meshy import config as _mcfg
            if not _mcfg.has_api_key():
                self.toast.emit(self.tr("请先配置 Meshy API Key"))
                return
        except Exception:
            pass
        self._library_busy = True
        self._library_loading = True
        self.libraryLoadingChanged.emit()

        def work():
            items, err = [], ""
            try:
                items = _meshy.list_library(page_num=page, page_size=40)
            except Exception as e:
                err = str(e)
            try:
                self._sigLibrary.emit(json.dumps(
                    {"items": items, "append": append, "page": page, "error": err},
                    default=str))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    @Slot(str)
    def _ui_library(self, js):
        try:
            d = json.loads(js)
        except Exception:
            d = {}
        self._library_busy = False
        if self._library_loading:
            self._library_loading = False
            self.libraryLoadingChanged.emit()
        err = d.get("error")
        if err:
            self.toast.emit(self.tr("资产库加载失败") + ": " + str(err)[:120])
            return
        items = d.get("items") or []
        if d.get("append"):
            seen = set(x.get("id") for x in self._library_items)
            added = 0
            for it in items:
                if it.get("id") not in seen:
                    self._library_items.append(it)
                    added += 1
            if added:
                self._library_page = int(d.get("page") or self._library_page)
            else:
                self.toast.emit(self.tr("没有更多了"))
        else:
            self._library_items = items
            self._library_page = int(d.get("page") or 1)
        self.libraryChanged.emit()

    @Slot(str)
    def importLibraryItem(self, task_id):
        item = next((x for x in self._library_items if x.get("id") == task_id), None)
        if not item:
            self.toast.emit(self.tr("找不到该资产"))
            return
        if _meshy is None:
            return
        if item.get("importing"):
            return   # 已在导入中，忽略重复点击

        def stage(label):
            # 在卡片上就地更新导入阶段（worker 线程安全：libraryChanged 跨线程排队）
            item["importing"] = True
            item["import_stage"] = label
            self.libraryChanged.emit()

        def finish(ok, msg=""):
            item["importing"] = False
            item["import_stage"] = ""
            self.libraryChanged.emit()
            if msg:
                (self.toast.emit if ok else
                 (lambda m: self._sigInfo.emit(self.tr("导入失败"), m)))(msg)

        stage(self.tr("下载中…"))

        def work():
            try:
                data = _meshy.fetch_library_asset(item)
            except Exception as e:
                finish(False, str(e)[:600])
                return
            glb = data.get("glb")
            if not glb:
                finish(False, self.tr("未获得 glb（云端链接可能已过期）"))
                return
            stage(self.tr("导入中…"))
            args = {"glb_path": glb}
            if data.get("texture_dir"):
                args["texture_dir"] = data["texture_dir"]
            res = self._exec_houdini_tool("import_3d_asset", args)
            if isinstance(res, dict) and res.get("success"):
                item["cached"] = True
                item["local_glb"] = glb
                finish(True, self.tr("已导入") + ": " + os.path.basename(glb))
            else:
                finish(False, str((res or {}).get("error", "未知错误"))[:600])

        threading.Thread(target=work, daemon=True).start()

    # ---- Meshy 账号（API Key 即登录凭证；公开 API 无 OAuth 登录） ----
    @Slot(result="QVariantMap")
    def meshyAccount(self):
        """返回账号连接状态供 UI 展示。balance: -1 表示未校验/未知。"""
        connected = False
        masked = ""
        if _meshy is not None:
            try:
                connected = _meshy.has_api_key()
                masked = _meshy.masked_key() if connected else ""
            except Exception:
                pass
        return {"connected": bool(connected), "key": masked,
                "balance": int(self._meshy_balance)}

    @Slot()
    def syncMeshyAccount(self):
        """后台校验 Key 并拉取账号余额（credits）。完成后发 meshyAccountChanged。"""
        if _meshy is None or self._meshy_account_busy:
            self.meshyAccountChanged.emit()
            return
        if not _meshy.has_api_key():
            self._meshy_balance = -1
            self.meshyAccountChanged.emit()
            return
        self._meshy_account_busy = True

        def work():
            res = {"connected": True, "balance": -1, "error": ""}
            try:
                r = _meshy.run_network("meshy_balance", {})
                if isinstance(r, dict) and r.get("success"):
                    res["balance"] = int((r.get("data") or {}).get("balance", -1))
                else:
                    res["error"] = (r or {}).get("error", "校验失败")
            except Exception as e:
                res["error"] = str(e)
            try:
                self._sigMeshyAccount.emit(json.dumps(res, default=str))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    @Slot(str)
    def _ui_meshy_account(self, js):
        try:
            d = json.loads(js)
        except Exception:
            d = {}
        self._meshy_account_busy = False
        bal = d.get("balance", -1)
        try:
            self._meshy_balance = int(bal)
        except Exception:
            self._meshy_balance = -1
        err = d.get("error")
        if err:
            self.toast.emit(self.tr("Meshy 账号校验失败") + ": " + str(err)[:120])
        self.meshyAccountChanged.emit()

    @Slot()
    def openMeshyLogin(self):
        """打开 API Key 对话框（登录 = 填入账号的 API Key）。"""
        try:
            self.openApiKeyDialog.emit("meshy")
        except Exception as e:
            print("[controller] open meshy login failed:", e)

    @Slot()
    def meshyLogout(self):
        """退出登录：清除 Key，重置账号与资产库状态。"""
        if _meshy is not None:
            try:
                _meshy.clear_api_key()
            except Exception as e:
                print("[controller] meshy logout failed:", e)
        self._meshy_balance = -1
        self._library_items = []
        self._library_page = 1
        self.meshyAccountChanged.emit()
        self.libraryChanged.emit()
        self.toast.emit(self.tr("已退出 Meshy 账号"))

    # ---- python / system shell block ----
    @Slot(str)
    def _ui_shell(self, js):
        try:
            d = json.loads(js)
        except Exception:
            return
        self._ensure_exec()
        self._exec_block.setdefault("shells", []).append({
            "shellKind": d.get("kind"),
            "code": d.get("code", ""),
            "output": d.get("output", ""),
            "error": d.get("error", ""),
            "success": d.get("success", True),
        })
        self._exec_block["label"] = "Completed · %d tools" % len(self._exec_block.get("tools", []))
        self._flush()

    # ---- streaming code preview (on_tool_args_delta) ----
    @Slot(str, str)
    def _ui_preview(self, name, code):
        if self._preview_block is None:
            self._preview_block = {"kind": "codepreview", "code": code}
            self._blocks.append(self._preview_block)
        else:
            self._preview_block["code"] = code
        self._flush()

    def _clear_preview(self):
        if self._preview_block is not None:
            try:
                self._blocks.remove(self._preview_block)
            except ValueError:
                pass
            self._preview_block = None

    @staticmethod
    def _json_string(s):
        try:
            return json.loads('"%s"' % s)
        except Exception:
            return s.replace('\\"', '"').replace("\\n", "\n")

    def _partial_plan_payload(self, acc):
        import re as _re
        try:
            d = json.loads(acc)
            if isinstance(d, dict):
                return self._build_plan_payload(d)
        except Exception:
            pass

        titles = [self._json_string(m) for m in _re.findall(r'"title"\s*:\s*"((?:[^"\\]|\\.)*)"', acc or "")]
        ids = [self._json_string(m) for m in _re.findall(r'"id"\s*:\s*"((?:[^"\\]|\\.)*)"', acc or "")]
        overview_m = _re.search(r'"overview"\s*:\s*"((?:[^"\\]|\\.)*)"', acc or "")
        overview = self._json_string(overview_m.group(1)) if overview_m else ""
        plan_title = titles[0] if titles else "Plan"
        step_titles = titles[1:]
        steps = []
        for i, title in enumerate(step_titles):
            sid = ids[i] if i < len(ids) and str(ids[i]).startswith("step") else "step-%d" % (i + 1)
            steps.append({
                "id": sid, "label": title or sid, "title": title or sid,
                "state": "active" if i == len(step_titles) - 1 else "pending",
                "status": "running" if i == len(step_titles) - 1 else "pending",
                "detail": "", "description": "", "sub_steps": [], "tools": [],
                "depends_on": ["step-%d" % i] if i > 0 else [],
                "expected_result": "", "risk": "low", "estimated_operations": 1,
                "fallback": "", "notes": "", "result_summary": "",
            })
        arch = self._build_step_architecture(steps, [])
        return {
            "title": plan_title, "overview": overview, "complexity": "",
            "estimated_total_operations": 0, "phases": [], "status": "streaming",
            "badge": "%d steps · generating" % len(steps),
            "steps": steps, "architecture": arch, "dag": arch.get("nodes", []),
        }

    @Slot(str)
    def _ui_plan_stream(self, acc):
        payload = self._partial_plan_payload(acc)
        payload["kind"] = "planstream"
        if self._planstream_block is None:
            self._planstream_block = payload
            self._blocks.append(self._planstream_block)
        else:
            self._planstream_block.clear()
            self._planstream_block.update(payload)
        self._flush()

    def _clear_planstream(self):
        if self._planstream_block is not None:
            try:
                self._blocks.remove(self._planstream_block)
            except ValueError:
                pass
            self._planstream_block = None
            self._flush()

    # ---- batch undo / keep ----
    @Slot()
    def undoAll(self):
        if not self._op_ctx:
            return
        self._request_confirm("全部撤销", "确定要撤销本轮所有待确认的节点操作吗？", self._undo_all_now)

    def _undo_all_now(self):
        failed = 0
        for oid in reversed(list(self._op_ctx.keys())):
            if self._do_undo(self._op_ctx.get(oid)):
                self._op_ctx.pop(oid, None)
            else:
                failed += 1
        if failed:
            self.toast.emit("部分操作撤销失败，仍保留待处理")
            self._pending_ops = len(self._op_ctx)
            self.pendingOpsChanged.emit()
            self.refreshContext()
            return
        self._pending_ops = 0
        self.pendingOpsChanged.emit()
        self.batchResolved.emit("reverted")
        self.refreshContext()

    @Slot()
    def keepAll(self):
        self._op_ctx.clear()
        self._pending_ops = 0
        self.pendingOpsChanged.emit()
        self.batchResolved.emit("kept")

    def _do_undo(self, ctx):
        if not ctx:
            return False
        bridge = getattr(getattr(self._session, "bridge", None), "undo_node_op", None)
        if bridge is not None:
            try:
                res = bridge(ctx)
                ok = bool(res.get("success")) if isinstance(res, dict) else bool(res)
                if not ok:
                    err = res.get("error") if isinstance(res, dict) else "unknown bridge undo failure"
                    self._log_external("Bridge undo failed: %s ctx=%s" % (err, ctx))
                    self.toast.emit("撤销失败：%s" % err)
                return ok
            except Exception as e:
                self._log_external("Bridge undo exception: %s ctx=%s" % (e, ctx))
                self.toast.emit("撤销失败：%s" % e)
                return False
        try:
            import hou
        except Exception:
            return False
        op = ctx.get("op")
        try:
            if op == "create":
                for p in ctx.get("paths", []):
                    n = hou.node(p)
                    if n is not None:
                        n.destroy()
            elif op == "modify":
                snap = ctx.get("snapshot") or {}
                n = hou.node(snap.get("node_path", ""))
                if n is not None:
                    pn = snap.get("param_name", "")
                    old = snap.get("old_value")
                    if snap.get("is_tuple"):
                        pt = n.parmTuple(pn)
                        if pt is not None:
                            pt.set(old)
                    else:
                        pm = n.parm(pn)
                        if pm is not None:
                            if isinstance(old, dict) and "expr" in old:
                                lang = (hou.exprLanguage.Python
                                        if "python" in str(old.get("lang", "")).lower()
                                        else hou.exprLanguage.Hscript)
                                pm.setExpression(old["expr"], lang)
                            else:
                                pm.set(old)
            elif op == "delete":
                hou.undos.performUndo()
            return True
        except Exception as e:
            print("[undo] failed:", e)
            self.toast.emit("撤销失败：%s" % e)
            return False

    # ---- bare node-name resolution ----
    def _collect_node_names(self, a, r):
        import re as _re
        try:
            text = json.dumps([a, r], default=str)
        except Exception:
            text = str(a) + str(r)
        for p in _re.findall(r"/(?:obj|out|mat|stage|ch|shop|tasks)/[\w/]+", text):
            nm = p.rsplit("/", 1)[-1]
            if nm and nm not in self._node_name_map:
                self._node_name_map[nm] = p

    def _resolve_bare(self, html):
        import re as _re
        if not self._node_name_map:
            return html
        for nm in sorted(self._node_name_map, key=len, reverse=True):
            if not nm[-1:].isdigit():
                continue
            path = self._node_name_map[nm]
            html = _re.sub(r"(?<![/\w>])" + _re.escape(nm) + r"(?![\w/])",
                           "<a href='%s' style='color:#c9b896;text-decoration:none'>%s</a>" % (path, nm),
                           html)
        return html

    # ---- token display ----
    def _context_limit(self):
        return CONTEXT_LIMITS.get(self._model_name, 128000)

    def _active_tools_for_context(self):
        if not self._session:
            return []
        try:
            if self._mode == "Ask":
                return self._ask_tools()
            if self._mode == "Plan":
                return self._execution_tools() if self._plan_phase == "executing" else self._planning_tools()
            return list(getattr(self._session, "tools", []) or [])
        except Exception:
            return list(getattr(self._session, "tools", []) or [])

    def _latest_user_text(self):
        try:
            for msg in reversed(getattr(self._session, "history", []) or []):
                if msg.get("role") != "user":
                    continue
                c = msg.get("content", "")
                if isinstance(c, list):
                    parts = []
                    for p in c:
                        if isinstance(p, dict) and p.get("type") == "text":
                            parts.append(str(p.get("text", "")))
                    return "\n".join(parts)
                return str(c)
        except Exception:
            pass
        return ""

    def _estimate_ctx(self):
        if not self._session:
            return 0
        try:
            msgs = self._session.build_messages(self._show_thinking)
        except Exception:
            msgs = copy.deepcopy(getattr(self._session, "history", []) or [])
        try:
            query = self._latest_user_text()
            extra_fn = getattr(self._session, "_context_messages", None)
            extra = extra_fn(query, True, self._memory_enabled) if extra_fn and query else []
            if extra:
                insert_at = 1 if msgs and msgs[0].get("role") == "system" else 0
                msgs[insert_at:insert_at] = extra
        except Exception:
            pass
        if self._pending_images:
            msgs.append({"role": "user", "content": [
                {"type": "text", "text": ""},
                *({"type": "image_url", "image_url": {"url": "data:%s;base64,<pending>" % mt}}
                  for _b64, mt in self._pending_images),
            ]})
        tools = self._active_tools_for_context()
        try:
            est = self._session.client._estimate_messages_tokens(msgs, tools)
        except Exception:
            est = 0
            for m in msgs:
                est += len(json.dumps(m, ensure_ascii=False, default=str)) // 3 + 4
            for t in tools:
                est += len(json.dumps(t, ensure_ascii=False, default=str)) // 4 + 30
        return max(0, int(est))

    @staticmethod
    def _fmt_k(n):
        return ("%.1fk" % (n / 1000.0)) if n >= 1000 else str(int(n))

    def _refresh_tokens(self):
        limit = self._context_limit()
        s = self._token_stats
        self._ctx_text = "%s / %s" % (self._fmt_k(self._estimate_ctx()), self._fmt_k(limit))
        self._token_text = "%s tokens" % self._fmt_k(s["total"] or (s["input"] + s["output"] + s["reasoning"]))
        self.tokensChanged.emit()

    @staticmethod
    def _short_val(v, n=28):
        s = str(v)
        return s if len(s) <= n else s[:n - 1] + "…"

    def _dec_pending(self):
        if self._pending_ops > 0:
            self._pending_ops -= 1
            self.pendingOpsChanged.emit()

    @Slot(str, result=bool)
    def keepNodeOp(self, oid):
        if self._op_ctx.pop(oid, None) is not None:
            self._dec_pending()
            return True
        return False

    @Slot(str, result=bool)
    def undoNodeOp(self, oid):
        ctx = self._op_ctx.get(oid)
        if not ctx:
            self.toast.emit("该操作已不可撤销或已处理")
            return False
        if not self._do_undo(ctx):
            return False
        self._op_ctx.pop(oid, None)
        self._dec_pending()
        self.refreshContext()
        return True

    # ---- Plan mode ----
    def _handle_create_plan(self, kwargs):
        import json
        payload = self._build_plan_payload(kwargs)
        self._plan_phase = "awaiting"
        if self._plan_revision_mode:
            self._plan_revision_mode = False
            payload["revision"] = self._to_int((self._plan_payload or {}).get("revision"), 1) + 1
            payload["revised"] = True
            payload["status"] = "revised"
            self._sigPlan.emit(json.dumps(payload, default=str))
            return {"success": True,
                    "result": "已基于原计划完成局部修订，并更新同一张计划卡片。请用一句话说明修改完成。"}
        self._sigPlan.emit(json.dumps(payload, default=str))
        return {"success": True,
                "result": "计划已创建并展示给用户（含步骤与 DAG）。等待用户点击「确认执行」后再开始执行。"
                          "现在用一句话简短告知用户计划已就绪。"}

    def _handle_update_plan_step(self, kwargs):
        import json
        self._sigPlanStep.emit(json.dumps({
            "step_id": kwargs.get("step_id", ""),
            "status": kwargs.get("status", ""),
            "summary": kwargs.get("result_summary", ""),
        }, default=str))
        return {"success": True,
                "result": "step %s -> %s" % (kwargs.get("step_id"), kwargs.get("status"))}

    @staticmethod
    def _as_list(v):
        return v if isinstance(v, list) else []

    @staticmethod
    def _to_int(v, default=0):
        try:
            return int(v)
        except Exception:
            return default

    @staticmethod
    def _build_step_architecture(steps, phases=None):
        nodes, connections = [], []
        has_deps = any(s.get("depends_on") for s in steps)
        for i, s in enumerate(steps):
            sid = s.get("id") or ("step-%d" % (i + 1))
            tools = s.get("tools") or []
            nodes.append({
                "id": sid,
                "label": (s.get("title") or s.get("label") or sid),
                "type": "other",
                "group": "",
                "is_new": True,
                "params": ", ".join(tools[:2]) if isinstance(tools, list) else "",
            })
            for dep in (s.get("depends_on") or []):
                connections.append({"from": dep, "to": sid, "label": ""})
        if not has_deps and len(steps) > 1:
            for i in range(len(steps) - 1):
                connections.append({"from": steps[i]["id"], "to": steps[i + 1]["id"], "label": ""})
        groups = []
        for p in (phases or []):
            name = p.get("name", "")
            ids = p.get("step_ids", [])
            if name and ids:
                groups.append({"name": name, "node_ids": ids, "color": "blue"})
        return {"nodes": nodes, "connections": connections, "groups": groups}

    def _build_plan_payload(self, k):
        allowed_types = {"sop", "obj", "mat", "vop", "rop", "dop", "lop", "cop",
                         "chop", "out", "subnet", "null", "other"}
        steps = self._as_list(k.get("steps"))
        phases = self._as_list(k.get("phases"))
        step_phase = {}
        out_phases = []
        for p in phases:
            if not isinstance(p, dict):
                continue
            name = str(p.get("name") or "")
            ids = [str(x) for x in self._as_list(p.get("step_ids"))]
            if not name or not ids:
                continue
            out_phases.append({"name": name, "step_ids": ids})
            for sid in ids:
                step_phase[sid] = name

        out_steps, idmap = [], {}
        for i, s in enumerate(steps):
            if not isinstance(s, dict):
                continue
            sid = str(s.get("id") or ("step-%d" % (i + 1)))
            idmap[sid] = len(out_steps)
            status = s.get("status") or "pending"
            state = {"running": "active", "done": "done", "error": "error"}.get(status, "pending")
            detail = s.get("description") or s.get("detail") or ""
            out_steps.append({
                "id": sid,
                "label": s.get("title") or sid,
                "title": s.get("title") or sid,
                "state": state,
                "status": status,
                "detail": detail,
                "description": detail,
                "sub_steps": self._as_list(s.get("sub_steps")),
                "tools": self._as_list(s.get("tools")),
                "depends_on": self._as_list(s.get("depends_on")),
                "expected_result": s.get("expected_result") or "",
                "risk": s.get("risk") or "low",
                "estimated_operations": self._to_int(s.get("estimated_operations"), 1),
                "fallback": s.get("fallback") or "",
                "notes": s.get("notes") or "",
                "phase": step_phase.get(sid, ""),
                "result_summary": s.get("result_summary") or "",
            })

        arch = k.get("architecture") if isinstance(k.get("architecture"), dict) else {}
        nodes = []
        for n in self._as_list(arch.get("nodes")):
            if not isinstance(n, dict):
                continue
            nid = str(n.get("id") or n.get("name") or "")
            if not nid:
                continue
            ntype = str(n.get("type") or "other").lower()
            nodes.append({
                "id": nid,
                "label": str(n.get("label") or nid),
                "type": ntype if ntype in allowed_types else "other",
                "group": str(n.get("group") or ""),
                "is_new": bool(n.get("is_new", True)),
                "params": str(n.get("params") or ""),
            })
        node_ids = {n["id"] for n in nodes}
        connections = []
        for c in self._as_list(arch.get("connections")):
            if not isinstance(c, dict):
                continue
            src, dst = str(c.get("from") or ""), str(c.get("to") or "")
            if src and dst and (not node_ids or (src in node_ids and dst in node_ids)):
                connections.append({"from": src, "to": dst, "label": str(c.get("label") or "")})
        groups = []
        for g in self._as_list(arch.get("groups")):
            if not isinstance(g, dict):
                continue
            name = str(g.get("name") or "")
            ids = [str(x) for x in self._as_list(g.get("node_ids"))]
            if name and ids:
                groups.append({"name": name, "node_ids": ids, "color": str(g.get("color") or "")})
        architecture = {"nodes": nodes, "connections": connections, "groups": groups}
        if not nodes:
            architecture = self._build_step_architecture(out_steps, out_phases)

        complexity = k.get("complexity") or ""
        badge = "%d steps" % len(out_steps)
        if complexity:
            badge += " · " + str(complexity)
        self._plan_idmap = idmap
        revision = self._to_int(k.get("revision"), 1)
        return {
            "title": k.get("title") or "Plan",
            "overview": k.get("overview") or "",
            "complexity": complexity,
            "estimated_total_operations": self._to_int(k.get("estimated_total_operations"), 0),
            "phases": out_phases,
            "status": k.get("status") or "draft",
            "badge": (badge + (" · rev %d" % revision if revision > 1 else "")),
            "revision": revision,
            "revision_notes": self._as_list(k.get("revision_notes")),
            "steps": out_steps,
            "architecture": architecture,
            "dag": architecture.get("nodes", []),
        }

    @Slot(str)
    def _ui_plan(self, js):
        import json
        try:
            payload = json.loads(js)
        except Exception:
            return
        self._clear_planstream()
        self._plan_payload = payload
        if self._plan_row is not None and payload.get("revised"):
            self._model.update_payload(self._plan_row, copy.deepcopy(payload))
            self.toast.emit("计划已更新，可继续修改或确认执行")
        else:
            self._plan_row = self._model.append({"type": "plan", "payload": payload})

    @Slot(str)
    def _ui_plan_step(self, js):
        import json
        try:
            d = json.loads(js)
        except Exception:
            return
        if self._plan_row is None or not self._plan_payload:
            return
        idx = self._plan_idmap.get(d.get("step_id"))
        if idx is None:
            return
        state = {"running": "active", "done": "done", "error": "error"}.get(d.get("status"), "pending")
        try:
            self._plan_payload["steps"][idx]["state"] = state
            self._plan_payload["steps"][idx]["status"] = d.get("status") or "pending"
            if d.get("summary"):
                self._plan_payload["steps"][idx]["result_summary"] = d["summary"]
                self._plan_payload["steps"][idx]["detail"] = d["summary"]
            self._model.update_payload(self._plan_row, copy.deepcopy(self._plan_payload))
        except Exception:
            pass

    def _begin_plan_execution(self):
        if not self._session:
            return
        self._plan_phase = "executing"
        self._model.append({"type": "user", "payload": {"text": "▶ 执行计划"}})
        plan_json = ""
        try:
            plan_json = json.dumps(self._plan_payload or {}, ensure_ascii=False, default=str)
        except Exception:
            plan_json = ""
        instr = ("用户已确认上述计划。现在按计划逐步执行：每一步开始前调用 "
                 "update_plan_step(step_id, status='running')，完成后调用 "
                 "update_plan_step(step_id, status='done')（失败用 'error'）。"
                 "严格按 depends_on 顺序执行，全部完成后给出简短总结。"
                 "\n\n以下是用户最终确认的修订版计划 JSON，请以它为准，不要执行旧版本：\n"
                 + plan_json)
        self._start_run(instr, tools=self._execution_tools(), max_iter=80)
        # Plan 执行通常会连续创建/连接节点，自动 cook 最容易让 Houdini 主线程假死。
        # 执行结束后用户仍可手动 cook/刷新视口，避免每一步都强制 cook。
        self._cook_suspended = True
        self.toast.emit("开始执行计划 · 已暂停自动 Cook 以避免卡死")
        self.planExecutionStarted.emit()

    @Slot(str, result=str)
    def confirmPlan(self, plan_id):
        if not self._session:
            return ""
        if self._running:
            if self._plan_phase == "awaiting":
                self._pending_plan_confirm = True
                self.toast.emit("计划生成收尾中，完成后会自动开始执行")
                return "pending"
            self.toast.emit("当前仍在运行，请等待或先 Stop")
            return ""
        self._pending_plan_confirm = False
        self._begin_plan_execution()
        return "confirmed"

    @Slot(str, result=bool)
    def rejectPlan(self, plan_id):
        if self._running and self._plan_phase != "awaiting":
            self.toast.emit("当前仍在运行，请等待或先 Stop")
            return False
        if self._running and self._plan_phase == "awaiting" and self._session:
            self._session.stop()
        self._pending_plan_confirm = False
        self._plan_phase = "idle"
        if self._session:
            try:
                self._session.history.append({"role": "user", "content": "用户驳回了该计划，不要执行。"})
            except Exception:
                pass
        return True

    def _ensure_plan_state(self, payload=None):
        if self._plan_row is not None and 0 <= self._plan_row < len(getattr(self._model, "_items", [])):
            if self._model._items[self._plan_row].get("type") == "plan":
                if payload is not None:
                    self._plan_payload = payload
                elif not self._plan_payload:
                    self._plan_payload = self._model._items[self._plan_row].get("payload") or {}
                return True
        for i in range(len(getattr(self._model, "_items", [])) - 1, -1, -1):
            item = self._model._items[i]
            if item.get("type") == "plan":
                self._plan_row = i
                self._plan_payload = payload if payload is not None else (item.get("payload") or {})
                self._plan_idmap = {s.get("id"): j for j, s in enumerate((self._plan_payload or {}).get("steps", []))}
                return True
        return False

    @Slot(str, result=bool)
    def applyPlanEdit(self, payload_json):
        if self._running:
            self.toast.emit("当前仍在运行，请等待或先 Stop")
            return False
        try:
            data = json.loads(payload_json or "{}")
            if not isinstance(data, dict):
                self.toast.emit("计划修改失败：无效数据")
                return False
            if not self._ensure_plan_state(data):
                self.toast.emit("计划修改失败：找不到当前计划卡片")
                return False
            data["revision"] = self._to_int((self._plan_payload or {}).get("revision"), 1) + 1
            data["revised"] = True
            data["status"] = "revised"
            payload = self._build_plan_payload(data)
            payload["revised"] = True
            payload["status"] = "revised"
            self._plan_payload = payload
            self._plan_idmap = {s.get("id"): i for i, s in enumerate(payload.get("steps", []))}
            self._model.update_payload(self._plan_row, copy.deepcopy(payload))
            self.toast.emit("手动修改已保存到当前计划")
            return True
        except Exception as e:
            self.toast.emit("计划修改失败：%s" % e)
            return False

    @Slot(str, str, result=bool)
    def revisePlan(self, instruction, payload_json):
        instruction = (instruction or "").strip()
        if not instruction:
            self.toast.emit("请先输入修改要求")
            return False
        if self._running:
            self.toast.emit("当前仍在运行，请等待或先 Stop")
            return False
        if not self._session:
            self.toast.emit("当前后端不可用，无法让 AI 修改计划")
            return False
        try:
            base = json.loads(payload_json or "{}")
            if not isinstance(base, dict):
                base = self._plan_payload
        except Exception:
            base = self._plan_payload
        if not base or not self._ensure_plan_state(base):
            self.toast.emit("计划修改失败：找不到当前计划")
            return False
        self._plan_revision_mode = True
        self._plan_phase = "planning"
        self._model.append({"type": "user", "payload": {"text": "✎ 修改计划：" + instruction}})
        prompt = (
            "用户对当前计划提出局部修改要求。请不要重新发散设计，不要生成多份方案。"
            "你必须基于原计划做最小必要修改，保留不相关步骤、step id、依赖关系和 DAG。"
            "修改完成后只调用 create_plan 返回修订后的完整计划 JSON。\n\n"
            "用户修改要求：\n%s\n\n当前计划 JSON：\n%s"
        ) % (instruction, json.dumps(base, ensure_ascii=False, default=str))
        self._start_run(prompt, tools=self._planning_tools(), max_iter=8)
        return True

    # ---- confirm mode + ask_question (blocking UI cards) ----
    def _await_confirm(self, tool_name, kwargs):
        self._int_seq += 1
        cid = "c%d" % self._int_seq
        q = queue.Queue()
        self._interactive[cid] = q
        self._sigConfirm.emit(cid, tool_name, self._arg_preview(kwargs))
        try:
            return bool(q.get(timeout=120))
        except queue.Empty:
            return False
        finally:
            self._interactive.pop(cid, None)

    def _handle_ask_question(self, kwargs):
        self._int_seq += 1
        qid = "q%d" % self._int_seq
        q = queue.Queue()
        self._interactive[qid] = q
        questions = kwargs.get("questions", []) or []
        self._sigAskQ.emit(qid, json.dumps(questions, default=str))
        try:
            ans = q.get(timeout=300)
        except queue.Empty:
            ans = {}
        finally:
            self._interactive.pop(qid, None)
        return {"success": True, "result": "用户回答：" + json.dumps(ans, ensure_ascii=False)}

    @Slot(str, bool)
    def resolveConfirm(self, cid, ok):
        q = self._interactive.get(cid)
        if q is not None:
            q.put(bool(ok))
        self._sigResolveCard.emit(cid, "confirmed" if ok else "cancelled")

    @Slot(str, str)
    def resolveQuestion(self, qid, answers_json):
        q = self._interactive.get(qid)
        if q is not None:
            try:
                ans = json.loads(answers_json) if answers_json else {}
            except Exception:
                ans = {}
            q.put(ans)
        self._sigResolveCard.emit(qid, "answered")

    @Slot(str, str, str)
    def _ui_confirm(self, cid, tool_name, arg):
        self._blocks.append({"kind": "confirm", "cid": cid, "name": tool_name,
                             "arg": arg, "state": "pending"})
        self._flush()

    @Slot(str, str)
    def _ui_askq(self, qid, questions_json):
        try:
            questions = json.loads(questions_json)
        except Exception:
            questions = []
        self._blocks.append({"kind": "askq", "qid": qid, "questions": questions, "state": "pending"})
        self._flush()

    @Slot(str, str)
    def _ui_resolve_card(self, cid, state):
        for b in self._blocks:
            if b.get("cid") == cid or b.get("qid") == cid:
                b["state"] = state
                break
        self._flush()

    # ---- <think> splitter ----
    def _split(self, chunk):
        self._cbuf += chunk
        OPEN, CLOSE = "<think>", "</think>"
        vis, thk = [], []
        while True:
            if not self._in_think:
                i = self._cbuf.find(OPEN)
                if i < 0:
                    keep = self._tail_keep(self._cbuf, OPEN)
                    cut = len(self._cbuf) - keep
                    vis.append(self._cbuf[:cut]); self._cbuf = self._cbuf[cut:]
                    break
                vis.append(self._cbuf[:i]); self._cbuf = self._cbuf[i + len(OPEN):]; self._in_think = True
            else:
                j = self._cbuf.find(CLOSE)
                if j < 0:
                    keep = self._tail_keep(self._cbuf, CLOSE)
                    cut = len(self._cbuf) - keep
                    thk.append(self._cbuf[:cut]); self._cbuf = self._cbuf[cut:]
                    break
                thk.append(self._cbuf[:j]); self._cbuf = self._cbuf[j + len(CLOSE):]; self._in_think = False
        return "".join(vis), "".join(thk)

    @staticmethod
    def _tail_keep(s, tag):
        m = min(len(tag) - 1, len(s))
        for k in range(m, 0, -1):
            if s.endswith(tag[:k]):
                return k
        return 0

    @staticmethod
    def _arg_preview(a):
        if not isinstance(a, dict) or not a:
            return ""
        keys = ("node_type", "node_name", "name", "param_name", "value", "path",
                "parent_path", "node_path", "code", "command", "keyword", "description", "query")
        parts = []
        for k in keys:
            if k in a and a[k] not in (None, ""):
                parts.append(str(a[k]).replace("\n", " ")[:42])
            if len(parts) >= 2:
                break
        if not parts:
            for v in a.values():
                parts.append(str(v).replace("\n", " ")[:42]); break
        return " · ".join(parts)

    # ---- main-thread UI mutations ----
    def _flush(self):
        # coalesced: schedule a single render at ~25fps instead of per-callback
        if self._ai_row is None:
            return
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _do_flush(self):
        if self._bm is None:
            return
        if self._answer_dirty:
            self._recompute_answer()
            self._answer_dirty = False
        self._bm.sync(self._blocks)

    def _ensure_think(self):
        if self._think_block is None:
            self._think_block = {"kind": "thinking", "dur": "…", "text": ""}
            self._blocks.insert(0, self._think_block)

    def _ensure_exec(self):
        if self._exec_block is None:
            self._exec_block = {"kind": "exec", "label": "Executing…", "tools": [], "shells": []}
            self._blocks.append(self._exec_block)

    def _recompute_answer(self):
        """Re-parse the accumulated answer into prose/code segments (no flush)."""
        segs = self._parse_answer(self._prose_text) if self._prose_text.strip() else []
        for s in segs:
            if s.get("kind") == "prose":
                s["html"] = self._resolve_bare(s["html"])
        prev = set(id(b) for b in self._answer_blocks)
        self._blocks = [b for b in self._blocks if id(b) not in prev]
        self._answer_blocks = segs
        self._blocks.extend(segs)

    @Slot(str)
    def _ui_think(self, t):
        self._touch_activity()
        self._think_text += t
        self._ensure_think()
        self._think_block["text"] = self._think_text
        self._flush()

    @Slot(str)
    def _ui_content(self, t):
        self._touch_activity()
        self._prose_text += t
        self._answer_dirty = True
        self._flush()

    @Slot(str, str)
    def _ui_tool_call(self, name, arg):
        self._touch_activity()
        self._ensure_exec()
        self._exec_block["tools"].append({"state": "run", "name": name, "arg": arg, "time": "", "detail": ""})
        self._exec_block["label"] = "Executing… (%d)" % len(self._exec_block["tools"])
        self._flush()

    @Slot(str, bool, str)
    def _ui_tool_result(self, name, ok, detail):
        self._touch_activity()
        if self._exec_block:
            for row in reversed(self._exec_block["tools"]):
                if row["name"] == name and row["state"] == "run":
                    row["state"] = "ok" if ok else "warn"
                    row["detail"] = detail
                    break
            self._exec_block["label"] = "Completed · %d tools" % len(self._exec_block["tools"])
        self._clear_preview()
        self._flush()

    @Slot(str)
    def _ui_done(self, err):
        self._set_running(False)
        self._cook_suspended = False
        self._status_phase = ""
        self.statusChanged.emit()
        self._clear_preview()
        if self._think_block is not None:
            try:
                self._think_block["dur"] = "%.1fs" % (time.monotonic() - self._t0)
            except Exception:
                self._think_block["dur"] = ""
        # re-render the final answer as markdown + highlighted code blocks
        self._finalize_answer()
        self._refresh_tokens()
        if err:
            self._log_chat("ERROR", err)
            self._plan_revision_mode = False
            if self._pending_plan_confirm:
                self._pending_plan_confirm = False
                self.planConfirmFailed.emit(str(err))
            self._blocks.append({"kind": "prose",
                                 "html": "<span style='color:#dd9999'>错误：" + _esc(err) + "</span>"})
        elif self._plan_revision_mode:
            self._plan_revision_mode = False
            self.toast.emit("未收到修订后的计划，请换一种修改要求再试")
        self._do_flush()   # immediate final render
        if self._prose_text.strip():
            self._log_chat("ASSISTANT", self._prose_text)
        # persist the finished turn
        self._snapshot_active()
        self._save_all()
        if self._pending_plan_confirm:
            self._pending_plan_confirm = False
            if not err and self._plan_phase == "awaiting":
                QTimer.singleShot(0, self._begin_plan_execution)
        # 本轮结束后，若有已完成的后台 Meshy 任务结果在排队，投递给 agent 继续
        if self._meshy_bg_feedback:
            QTimer.singleShot(0, self._flush_bg_feedback)
        # 本轮结束后，弹出排队中的后台概念图画廊（供用户挑选）
        if self._pending_bg_galleries:
            QTimer.singleShot(0, self._drain_pending_bg_galleries)

    # ---- markdown + code rendering of the final answer ----
    def _finalize_answer(self):
        self._answer_dirty = True

    @classmethod
    def _parse_answer(cls, text):
        """Split markdown into prose/code segments (rendered by ProseBlock / CodeBlock)."""
        import re as _re
        segs = []
        parts = _re.split(r"```", text)
        for i, part in enumerate(parts):
            if i % 2 == 0:
                if part.strip():
                    segs.append({"kind": "prose", "html": cls._md_html(part.strip("\n"))})
            else:
                lines = part.split("\n")
                first = lines[0].strip()
                if first and " " not in first and len(first) <= 16:
                    lang = first
                    code = "\n".join(lines[1:])
                else:
                    lang = ""
                    code = part
                code = code.strip("\n")
                segs.append({"kind": "code", "lang": lang or "code", "code": code,
                             "html": cls._highlight(code, lang)})
        return segs or [{"kind": "prose", "html": cls._md_html(text)}]

    @staticmethod
    def _inline(s):
        import re as _re
        h = _esc(s)
        h = _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", h)
        h = _re.sub(r"(?<!\*)\*(?!\*)(.+?)\*(?!\*)", r"<i>\1</i>", h)
        h = _re.sub(r"`([^`]+)`", r"<span style='font-family:monospace;color:#d4a373'>\1</span>", h)
        h = _re.sub(r"\[(.+?)\]\((https?://[^)]+)\)", r"<a href='\2' style='color:#e8e2d4'>\1</a>", h)
        h = _re.sub(r"(?<![\w/])(/(?:obj|out|mat|stage|ch|shop|tasks)/[\w/]+)",
                    r"<a href='\1' style='color:#c9b896;text-decoration:none'>\1</a>", h)
        return h

    @classmethod
    def _table_html(cls, header, body):
        th = "".join("<th align='left' style='color:#ffffff;padding:3px 8px'>%s</th>" % cls._inline(h) for h in header)
        rows = "<tr bgcolor='#1a1a1a'>" + th + "</tr>"
        for r in body:
            rows += "<tr>" + "".join("<td style='color:#e7e4db;padding:3px 8px'>%s</td>" % cls._inline(c) for c in r) + "</tr>"
        return ("<table border='1' width='100%' cellspacing='0' cellpadding='4' "
                "style='border-color:#3a3a3a'>" + rows + "</table>")

    @classmethod
    def _md_html(cls, text):
        import re as _re
        lines = text.split("\n")
        out = []
        i, n = 0, len(lines)

        def cells(row):
            return [c.strip() for c in row.strip().strip("|").split("|")]

        def is_sep(row):
            return bool(_re.match(r"^\s*\|?[\s:\-|]+\|?\s*$", row)) and "-" in row and "|" in row

        while i < n:
            line = lines[i].rstrip()
            # markdown table: header row + separator row + body rows
            if "|" in line and i + 1 < n and is_sep(lines[i + 1]):
                header = cells(line)
                i += 2
                body = []
                while i < n and lines[i].strip() and "|" in lines[i]:
                    body.append(cells(lines[i]))
                    i += 1
                out.append(cls._table_html(header, body))
                continue
            if not line.strip():
                if out and out[-1] != "":
                    out.append("")
                i += 1
                continue
            s = line.lstrip()
            if s.startswith("### "):
                out.append("<div style='color:#fafafa;font-size:14px'><b>%s</b></div>" % cls._inline(s[4:]))
            elif s.startswith("## "):
                out.append("<div style='color:#fafafa;font-size:15px'><b>%s</b></div>" % cls._inline(s[3:]))
            elif s.startswith("# "):
                out.append("<div style='color:#fafafa;font-size:16px'><b>%s</b></div>" % cls._inline(s[2:]))
            elif _re.match(r"^[-*]\s+", s):
                out.append("&nbsp;•&nbsp;" + cls._inline(_re.sub(r"^[-*]\s+", "", s)))
            elif _re.match(r"^\d+\.\s+", s):
                out.append("&nbsp;" + cls._inline(s))
            else:
                out.append(cls._inline(line))
            i += 1
        return "<br>".join(out)

    @staticmethod
    def _highlight(code, lang):
        import re as _re
        C = {"com": "#7d786b", "kw": "#ece6d8", "fn": "#d3c4a2",
             "attr": "#e0b083", "num": "#b3c19a", "str": "#cda988"}
        is_py = (lang or "").lower().startswith("py")
        if is_py:
            kw = set("def class return if elif else for while import from as with try except "
                     "finally lambda None True False and or not in is pass break continue global "
                     "yield raise assert del print".split())
            comment = r"#[^\n]*"
        else:
            kw = set("float int vector vector2 vector4 matrix matrix3 string void if else for "
                     "while foreach do return struct function detail point prim vertex chi chf "
                     "chv chs export const".split())
            comment = r"//[^\n]*|/\*[\s\S]*?\*/"
        pat = _re.compile(
            r"(?P<com>%s)"
            r"|(?P<str>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')"
            r"|(?P<attr>[vfisp24]?@[A-Za-z_]\w*)"
            r"|(?P<num>\b\d+\.?\d*\b)"
            r"|(?P<id>[A-Za-z_]\w*)"
            r"|(?P<ws>[ \t\n]+)"
            r"|(?P<oth>.)" % comment)

        def esc(t):
            return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        def sp(c, t):
            return "<span style='color:%s'>%s</span>" % (C[c], esc(t))

        out = []
        for m in pat.finditer(code):
            k = m.lastgroup
            t = m.group()
            if k == "com":
                out.append(sp("com", t))
            elif k == "str":
                out.append(sp("str", t))
            elif k == "attr":
                out.append(sp("attr", t))
            elif k == "num":
                out.append(sp("num", t))
            elif k == "id":
                nxt = code[m.end():m.end() + 1]
                if t in kw:
                    out.append(sp("kw", t))
                elif nxt == "(":
                    out.append(sp("fn", t))
                else:
                    out.append(esc(t))
            elif k == "ws":
                out.append(t.replace("\t", "&nbsp;&nbsp;&nbsp;&nbsp;").replace(" ", "&nbsp;").replace("\n", "<br>"))
            else:
                out.append(esc(t))
        return "".join(out)

    # ---- simulated reply (fallback when no backend) ----
    def _simulate(self, text):
        short = text[:24] + ("…" if len(text) > 24 else "")

        def step_think():
            self._blocks.append({"kind": "thinking", "dur": "2.1s",
                                 "text": "（模拟）分析请求：“%s”。未连接后端，仅演示流式。" % short})
            self._do_flush(); QTimer.singleShot(450, step_done)

        def step_done():
            self._blocks.append({"kind": "prose",
                                 "html": "（模拟回复）未连接真实 agent loop。在 Houdini 中通过 launcher.show_tool() 启动即接真实后端。"})
            self._do_flush(); self._set_running(False)

        QTimer.singleShot(350, step_think)
