# -*- coding: utf-8 -*-
"""QML Controller (modularized via mixins under houdini_agent/ui_qml/mixins/)."""

from .controller_shared import *  # noqa: F401,F403 — preserve all public exports
from .controller_shared import (
    _BRIDGE_LOST_MSG, _MESHY_URLS, _MESHY_UTM, _TPL_HOU, _TPL_MESHY,
    _block_fp, _esc, _meshy, _to_html,
)
from .mixins import (
    ControllerBridgeMixin,
    ControllerManagementMixin,
    ControllerMarkdownMixin,
    ControllerMeshyMixin,
    ControllerPlanMixin,
    ControllerSubscriptionMixin,
    ControllerUpdaterMixin,
)


class Controller(
    ControllerBridgeMixin,
    ControllerManagementMixin,
    ControllerMarkdownMixin,
    ControllerMeshyMixin,
    ControllerPlanMixin,
    ControllerSubscriptionMixin,
    ControllerUpdaterMixin,
    QObject,
):
    providerChanged = Signal()
    modelChanged = Signal()
    effortChanged = Signal()       # 推理强度可选项 / 当前值变化（换供应商、换模型、手动选择）
    sceneChanged = Signal()
    showThinkingChanged = Signal()
    runningChanged = Signal()
    sessionsChanged = Signal()
    confirmModeChanged = Signal()
    memoryChanged = Signal()
    imagesChanged = Signal()
    fontScaleChanged = Signal()
    langChanged = Signal()
    askDeleteSessionChanged = Signal()
    cookRealtimeChanged = Signal()
    customProvidersChanged = Signal()
    appearanceChanged = Signal()
    templatesChanged = Signal()
    openTemplates = Signal()              # 打开模板库浮层
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
    _sigUpdateDialog = Signal(str, str)   # worker -> main: 检查更新发现新版本（带「立即更新」按钮的对话框）
    _sigSub = Signal(str, str)            # worker -> main: ChatGPT 订阅状态事件 (kind, json)
    _sigModels = Signal(str)              # worker -> main: 模型目录刷新结果 (json -> _on_models_refreshed)
    _sigOptimizeDone = Signal(str)        # worker -> main: 手动上下文压缩结果 (json -> _on_optimize_done)
    _sigCompaction = Signal(str)          # worker -> main: 自动上下文压缩提示 (json -> _ui_compaction)
    tokensChanged = Signal()
    pendingOpsChanged = Signal()
    batchResolved = Signal(str)           # "kept"/"reverted" -> resolve all pending node-op rows
    statusChanged = Signal()              # running phase label
    updateAvailableChanged = Signal()
    autoSilentUpdateChanged = Signal()
    toast = Signal(str)                   # transient message
    prefillComposer = Signal(str)         # text -> 预填输入框并聚焦（库里"+用 Meshy 生成"）
    requestOpenHoudini = Signal()         # 断开时请求外部协调器弹启动器/重连 Houdini
    requestLaunchAnotherHoudini = Signal()  # 多开：请求外部协调器再启动另一个 Houdini
    houdiniInstancesChanged = Signal()    # 在线 Houdini 实例列表或当前活跃实例变化
    _sigInstances = Signal(str)           # worker -> main: 扫描到的实例列表 JSON
    openFontDialog = Signal()             # request the font-size slider popup
    openTokenDialog = Signal()            # request the token analytics popup (QML)
    openInfoDialog = Signal(str, str)      # title, body (QML)
    openUpdateDialog = Signal(str, str)    # title, body (QML)：检查更新发现新版本，对话框里直接给「立即更新」
    openSubscriptionDialog = Signal()      # 打开「ChatGPT 订阅」登录对话框
    subscriptionChanged = Signal()         # 订阅状态变化（QML 重新取 subscriptionStatus()）
    openApiKeyDialog = Signal(str)         # provider (QML)
    openCustomProviderDialog = Signal(str, str, str, bool, str, bool)  # url, key, model, anthropic, context_limit, supports_vision
    openConfirmDialog = Signal(str, str, str)  # title, body, token
    planExecutionStarted = Signal()
    planConfirmFailed = Signal(str)
    managementChanged = Signal()
    openRulesDialog = Signal()
    openPluginsDialog = Signal()
    openMemoryDialog = Signal()
    openSettingsDialog = Signal()         # request the unified settings window
    modelsChanged = Signal()              # 模型目录已更新（MODEL_MAP 被后台刷新改写）
    modelsRefreshingChanged = Signal()    # 刷新开始/结束
    imageModelChanged = Signal()          # 默认生图模型或 Codex 生图模型目录变化
    codexImageRefreshingChanged = Signal()
    _sigCodexImageRefresh = Signal(str)   # worker -> main: Codex 生图模型同步结果 JSON

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
    # worker -> main thread: 连接自愈/修复成功后刷新上下文条（场景路径/选择）
    _sigCtxRefresh = Signal()
    # worker -> main thread: 应用内更新状态机 (state, percent, message)
    _sigUpdateState = Signal(str, int, str)
    # worker -> main thread: 检查到新版本，显示更新横幅。必须走信号：在 Python 后台线程里
    # 调 QTimer.singleShot 回调永远不会执行（该线程没有事件循环），横幅因此从未出现过。
    _sigUpdateFound = Signal(str)

    def __init__(self, model, use_backend=False, parent=None):
        super().__init__(parent)
        self._model = model
        self._provider = "duojie"
        self._model_name = MODEL_MAP["duojie"][0]
        # 自定义供应商列表（每个含 name/base_url/api_key/anthropic/models[]，
        # 每个模型带 name/context/vision）。激活键形如 "custom:<id>"。
        self._custom_providers = []
        self._mode = "Agent"
        self._show_thinking = True
        self._confirm_mode = False
        self._memory_enabled = False
        self._ask_delete_session = True   # 删除会话前弹确认（可在设置里关）
        self._cook_realtime = True
        self._auto_silent_update = True   # 后台自动静默下载更新（类似 Claude 桌面版，重启即生效）
        self._staged_update = None        # 已后台下载校验就绪的暂存更新元数据
        self._update_applying = False     # 防止退出时重复拉起更新器
        self._silent_checking = False
        self._cook_suspended = False     # set when a cook is interrupted/too slow (per run)
        self._font_scale = 1.0
        # 外观（主题/强调色/字体方案/密度）——驱动 Theme.qml 换肤
        self._app_theme = "noir"          # noir | graphite | midnight | day
        self._accent_key = "warm"         # warm | steel | celadon | clay | neutral
        self._font_family_key = "editorial"  # editorial | modern | mono
        self._density_key = "normal"      # compact | normal | roomy
        self._default_image_model = ""    # ""=自动（未配 Meshy Key 且已登录 Codex 时自动走 Codex，否则 nano-banana）
        self._codex_image_refreshing = False
        self._houdini_instances = []      # 多实例列表：[{port, pid, houdini, hip_name, hip_path, path, selection, active, label}]
        self._scanning_instances = False
        self._my_templates = []           # 用户自存模板（templates.json）
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
        # 用量按会话记（随会话持久化），_load_active 时绑定到当前会话的 usage
        self._token_stats = self._new_token_stats()
        self._ctx_anchor = None     # 最近一次请求的真实上下文：{"tokens", "hist_len"}
        self._ctx_ratio = 1.0       # 字符估算的校准系数（真实 / 估算），每轮结束更新
        self._call_records = []
        self._ctx_text = "0 / 200k"
        self._token_text = "0 tokens"
        self._status_phase = ""      # "", thinking, generating, tool:<name>, planning
        self._pending_ops = 0
        self._update_info = None
        self._update_state = ""        # "" | available | downloading | ready | failed
        self._update_progress = 0
        self._update_installer_path = ""
        # 模型目录刷新（refreshModels）：_models_updated_at 从磁盘缓存推断（"HH:MM" 或 ""）
        self._models_refreshing = False
        self._models_refresh_quiet = False
        self._models_updated_at = self._cached_models_updated_at()
        self._node_name_map = {}     # bare name -> full path (for resolve)
        try:
            from houdini_agent.ui.i18n import get_language
            self._lang = get_language()
        except Exception:
            self._lang = "zh"

        # 模型目录：同步、无网络地从磁盘缓存回填（merge 策略下静态表 + live 缓存合并；
        # 无缓存/坏缓存时 catalog 返回静态表，此处回填等价于静态表本身）
        try:
            for _p in catalog.PROVIDER_SOURCES:
                _ids = catalog.provider_model_ids(_p)
                if _ids:
                    MODEL_MAP[_p] = list(_ids)
        except Exception:
            pass

        self._load_custom_providers()
        self._load_prefs()
        self._load_my_templates()

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

        # re-apply persisted custom-provider config; avoid a stranded provider state
        self._load_custom()
        if self._is_custom_key(self._provider):
            p = self._custom_provider(self._provider)
            names = self._provider_model_names(self._provider)
            if p is None or not names:
                # 该自定义供应商已被删除/无模型：回退到内置默认
                self._provider = "duojie"
                self._model_name = MODEL_MAP["duojie"][0]
            elif self._model_name not in names:
                self._model_name = names[0]

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
        self._saved_refs = {}   # session id -> 上次写盘时的签名（只重写有变化的会话）
        self._migrate_legacy_sessions()
        self._sessions = []     # [{id, title, rows, history}]
        self._active = 0
        self._add_session()
        self._bind_usage(self._sessions[0])

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
        self._sigUpdateDialog.connect(self._show_update_dialog)
        self._sigSub.connect(self._on_sub_event)
        self._sigModels.connect(self._on_models_refreshed)
        self._sigOptimizeDone.connect(self._on_optimize_done)
        self._sigCompaction.connect(self._ui_compaction)
        self._optimizing = False
        self._sigCodexImageRefresh.connect(self._on_codex_image_refreshed)
        self._sigInstances.connect(self._on_instances_scanned)
        self.subscriptionChanged.connect(self.imageModelChanged)
        self.langChanged.connect(self.imageModelChanged)
        self.langChanged.connect(self.houdiniInstancesChanged)
        self.meshyAccountChanged.connect(self.imageModelChanged)
        self.providerChanged.connect(self.effortChanged)
        self.modelChanged.connect(self.effortChanged)
        self.langChanged.connect(self.effortChanged)
        # 换供应商 / 模型后立刻刷新底部上下文上限（以前要等一轮结束才更新，会显示旧模型的上限）
        self.providerChanged.connect(self._schedule_ctx_refresh)
        self.modelChanged.connect(self._schedule_ctx_refresh)
        self._effort_by_model = {}      # model -> 用户选的推理强度（QSettings 持久化）
        try:
            _raw = QSettings("HoudiniAI", "Assistant").value("reasoning_effort", "")
            _d = json.loads(_raw) if _raw else {}
            if isinstance(_d, dict):
                self._effort_by_model = {str(k): str(v) for k, v in _d.items()}
        except Exception:
            pass
        self._sub = {"busy": False, "phase": "", "message": "", "progress": 0,
                     "loginUrl": "", "deviceUrl": "", "deviceCode": ""}
        self._sigCtxRefresh.connect(self.refreshContext)
        self._sigUpdateState.connect(self._on_update_state)
        self._sigUpdateFound.connect(self._set_update)

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

        # 启动后若模型目录缓存过期（>6h 或无缓存），在事件循环里静默后台刷新一次；
        # 不在构造函数里做网络请求，避免阻塞启动
        QTimer.singleShot(4000, self._auto_refresh_models_if_stale)

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

    def attach_backend_session(self, session, announce=True):
        """Attach a late-created backend session, used by the external launcher.
        announce=False：启动时静默挂载（此时 Bridge 未必已连上，不该提示"已连接"）。"""
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
        self.houdiniInstancesChanged.emit()
        self.scanHoudiniInstances()
        self.refreshContext()
        self._refresh_tokens()      # 后端是异步挂上的：之前算出的上下文是 0，这里补算
        if announce:
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
            p = str(s.value("last_provider", "") or "")
            m = s.value("last_model", "")
            # 自定义供应商（custom:<id>）或内置 provider 都允许作为上次选择
            if (self._is_custom_key(p) and self._custom_provider(p)) or p in MODEL_MAP:
                self._provider = p
            names = self._provider_model_names(self._provider)
            if m and m in names:
                self._model_name = m
            elif names:
                self._model_name = names[0]
            # persisted toggles / settings (same class of bug as custom)
            self._show_thinking = self._qbool(s.value("use_think", True), True)
            self._confirm_mode = self._qbool(s.value("confirm_mode", False), False)
            self._memory_enabled = self._qbool(s.value("memory_enabled", False), False)
            self._ask_delete_session = self._qbool(s.value("ask_delete_session", True), True)
            self._cook_realtime = self._qbool(s.value("cook_realtime", True), True)
            self._auto_silent_update = self._qbool(s.value("auto_silent_update", True), True)
            try:
                self._font_scale = float(s.value("font_scale", 1.0))
            except Exception:
                pass
            # 外观（带白名单校验，挡掉脏值）
            t = str(s.value("app_theme", "noir") or "noir")
            if t in ("noir", "graphite", "midnight", "day"):
                self._app_theme = t
            a = str(s.value("accent_key", "warm") or "warm")
            if a in ("warm", "steel", "celadon", "clay", "neutral"):
                self._accent_key = a
            f = str(s.value("font_family_key", "editorial") or "editorial")
            if f in ("editorial", "modern", "mono"):
                self._font_family_key = f
            d = str(s.value("density_key", "normal") or "normal")
            if d in ("compact", "normal", "roomy"):
                self._density_key = d
            img_m = str(s.value("default_image_model", "") or "").strip()
            if img_m and (img_m in ("nano-banana", "nano-banana-2", "nano-banana-pro", "gpt-image-2")
                          or (img_m.startswith("codex:") and len(img_m) > 6)):
                self._default_image_model = img_m
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
        return self._active_model_vision()

    # ---- properties ----
    def _get_provider(self): return self._provider
    provider = Property(str, _get_provider, notify=providerChanged)

    def _get_provider_label(self):
        p = self._custom_provider(self._provider)
        if p is not None:
            return p.get("name", "Custom")
        return self.tr(PROVIDER_LABELS.get(self._provider, self._provider))
    providerLabel = Property(str, _get_provider_label, notify=providerChanged)

    def _get_model(self): return self._model_name
    model = Property(str, _get_model, notify=modelChanged)

    def _get_show_thinking(self): return self._show_thinking
    showThinking = Property(bool, _get_show_thinking, notify=showThinkingChanged)

    # ---- 推理强度（目前只有订阅渠道 ChatGPT / Claude 支持选择） ----
    _EFFORT_LABELS = {"low": "低", "medium": "中", "high": "高", "xhigh": "很高", "max": "最高", "ultra": "极限"}

    def _sub_kind(self, provider=None):
        """供应商是否是订阅渠道；是则返回 CPA 凭据类型（codex / claude）。"""
        from houdini_agent.subscription import kind_of
        return kind_of(self._provider if provider is None else provider)

    def _effort_levels(self):
        kind = self._sub_kind()
        if not kind:
            return []
        from houdini_agent.subscription import effort_levels
        return effort_levels(kind, self._model_name)

    def _current_effort(self):
        levels = self._effort_levels()
        if not levels:
            return ""
        from houdini_agent.subscription import default_effort
        v = self._effort_by_model.get(self._model_name) or default_effort(self._sub_kind(), self._model_name)
        return v if v in levels else (levels[1] if len(levels) > 1 else levels[0])

    def _get_reasoning_effort(self):
        return self._current_effort()

    def _get_reasoning_effort_label(self):
        v = self._current_effort()
        return ("%s · %s" % (self.tr("推理"), self.tr(self._EFFORT_LABELS.get(v, v)))) if v else ""

    reasoningEffort = Property(str, _get_reasoning_effort, notify=effortChanged)
    reasoningEffortLabel = Property(str, _get_reasoning_effort_label, notify=effortChanged)

    @Slot(result="QVariantList")
    def effortItems(self):
        from houdini_agent.subscription import default_effort
        cur = self._current_effort()
        dflt = default_effort(self._sub_kind(), self._model_name)
        en = self._lang == "en"
        out = []
        for v in self._effort_levels():
            label = self.tr(self._EFFORT_LABELS.get(v, v))   # 只显示当前界面语言
            if v == dflt:
                label += (" (%s)" if en else "（%s）") % self.tr("默认")
            out.append({"label": label, "val": v, "checked": v == cur})
        return out

    def _schedule_ctx_refresh(self):
        if not self._session:
            return
        t = getattr(self, "_ctx_refresh_timer", None)
        if t is None:
            t = self._ctx_refresh_timer = QTimer(self)
            t.setSingleShot(True)
            t.setInterval(0)
            t.timeout.connect(self._refresh_tokens)
        t.start()

    @Slot(str)
    def setReasoningEffort(self, v):
        if v not in self._effort_levels():
            return
        self._effort_by_model[self._model_name] = v
        try:
            QSettings("HoudiniAI", "Assistant").setValue("reasoning_effort",
                                                         json.dumps(self._effort_by_model))
        except Exception:
            pass
        self.effortChanged.emit()

    def _get_running(self): return self._running
    running = Property(bool, _get_running, notify=runningChanged)

    def _get_confirm(self): return self._confirm_mode
    confirmMode = Property(bool, _get_confirm, notify=confirmModeChanged)

    def _get_ask_delete(self): return self._ask_delete_session
    askDeleteSession = Property(bool, _get_ask_delete, notify=askDeleteSessionChanged)

    def _get_cook_realtime(self): return self._cook_realtime
    cookRealtime = Property(bool, _get_cook_realtime, notify=cookRealtimeChanged)

    def _get_auto_silent_update(self): return self._auto_silent_update
    autoSilentUpdate = Property(bool, _get_auto_silent_update, notify=autoSilentUpdateChanged)

    @Slot(bool)
    def setAutoSilentUpdate(self, on):
        on = bool(on)
        if on != self._auto_silent_update:
            self._auto_silent_update = on
            self._set_pref("auto_silent_update", on)
            self.autoSilentUpdateChanged.emit()
            if on:
                self.silentUpdateCheck()

    def _get_app_version(self):
        try:
            from houdini_agent.utils.updater import get_local_version
            return get_local_version()
        except Exception:
            return ""
    appVersion = Property(str, _get_app_version, constant=True)

    def _get_memory(self): return self._memory_enabled
    memoryEnabled = Property(bool, _get_memory, notify=memoryChanged)

    def _get_image_count(self): return len(self._pending_images)
    imageCount = Property(int, _get_image_count, notify=imagesChanged)

    def _get_font_scale(self): return self._font_scale
    fontScale = Property(float, _get_font_scale, notify=fontScaleChanged)

    # ---- appearance ----
    def _get_app_theme(self): return self._app_theme
    appTheme = Property(str, _get_app_theme, notify=appearanceChanged)

    def _get_accent_key(self): return self._accent_key
    accentKey = Property(str, _get_accent_key, notify=appearanceChanged)

    def _get_font_family_key(self): return self._font_family_key
    fontFamilyKey = Property(str, _get_font_family_key, notify=appearanceChanged)

    def _get_density_key(self): return self._density_key
    densityKey = Property(str, _get_density_key, notify=appearanceChanged)

    # ---- 生图默认模型（Meshy / ChatGPT Codex 订阅） ----
    _MESHY_IMAGE_CHOICES = (
        ("nano-banana", "Nano Banana（默认 · 快）"),
        ("nano-banana-2", "Nano Banana 2"),
        ("nano-banana-pro", "Nano Banana Pro（高质量）"),
        ("gpt-image-2", "GPT Image 2"),
    )

    def _codex_logged_in(self):
        return self._sub_logged_in("codex")

    def _codex_image_models(self):
        try:
            from houdini_agent.subscription import manager
            return list(manager.cached_image_models("codex"))
        except Exception:
            return []

    def _effective_default_image_model(self):
        """当前生效的默认生图模型。
        - 用户显式选过则严格沿用；
        - 未显式选择时：若未配置 Meshy Key 且已登录 Codex，自动默认走 Codex 首选生图模型；
          否则沿用 Meshy nano-banana。"""
        if self._default_image_model:
            return self._default_image_model
        if self._codex_logged_in():
            has_meshy = False
            try:
                has_meshy = bool(_meshy and _meshy.has_api_key())
            except Exception:
                pass
            if not has_meshy:
                cmodels = self._codex_image_models()
                if cmodels:
                    return "codex:" + cmodels[0]
        return "nano-banana"

    def _get_default_image_model(self):
        return self._effective_default_image_model()
    defaultImageModel = Property(str, _get_default_image_model, notify=imageModelChanged)

    def _get_codex_logged_in(self):
        return self._codex_logged_in()
    codexLoggedIn = Property(bool, _get_codex_logged_in, notify=imageModelChanged)

    def _get_codex_image_refreshing(self):
        return self._codex_image_refreshing
    codexImageRefreshing = Property(bool, _get_codex_image_refreshing, notify=codexImageRefreshingChanged)

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
    def _get_update_state(self): return self._update_state
    updateState = Property(str, _get_update_state, notify=updateAvailableChanged)
    def _get_update_progress(self): return self._update_progress
    updateProgress = Property(int, _get_update_progress, notify=updateAvailableChanged)

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

    # ---- 模型目录动态刷新（后台线程 + _sigModels 回主线程） ----
    def _get_models_refreshing(self): return self._models_refreshing
    modelsRefreshing = Property(bool, _get_models_refreshing, notify=modelsRefreshingChanged)

    def _get_models_updated_at(self): return self._models_updated_at
    modelsUpdatedAt = Property(str, _get_models_updated_at, notify=modelsChanged)


    def _get_scene_path(self): return self._scene_path
    scenePath = Property(str, _get_scene_path, notify=sceneChanged)

    def _get_scene_sel(self): return self._scene_sel
    sceneSelection = Property(str, _get_scene_sel, notify=sceneChanged)

    # ---- 多 Houdini 实例管理（仅独立 Bridge 模式启用） ----


    def _get_active_instance_label(self):
        if not self._is_bridge_mode():
            return ""
        inst = self._active_houdini_instance()
        if inst:
            return str(inst.get("label") or ("Houdini :%s" % inst.get("port", "")))
        return self.tr("未连接 Houdini")
    activeInstanceLabel = Property(str, _get_active_instance_label, notify=houdiniInstancesChanged)

    def _get_houdini_instance_count(self):
        return len(self._houdini_instances)
    houdiniInstanceCount = Property(int, _get_houdini_instance_count, notify=houdiniInstancesChanged)

    def _get_houdini_connected_prop(self):
        return bool(self._houdini_instances)
    houdiniConnected = Property(bool, _get_houdini_connected_prop, notify=houdiniInstancesChanged)


    # ---- menu data for QML ----
    @Slot(result="QVariantList")
    def providerItems(self):
        # 内置 provider（不含已废弃的单一 "custom"）+ 自定义供应商
        items = []
        for k in MODEL_MAP.keys():
            if k == "custom":
                continue
            label = self.tr(PROVIDER_LABELS.get(k, k))
            if self._sub_kind(k) and not self._sub_logged_in(k):
                label += " · " + self.tr("未登录")
            items.append({"label": label, "val": k, "checked": k == self._provider})
        if self._custom_providers:
            items.append({"sep": True})
            for p in self._custom_providers:
                key = "custom:" + p.get("id", "")
                items.append({"label": p.get("name", "Custom"), "val": key,
                              "checked": key == self._provider})
        return items

    @Slot(result="QVariantList")
    def modelItems(self):
        models = self._provider_model_names(self._provider)
        items = [{"label": m, "val": m, "checked": m == self._model_name} for m in models]
        kind = self._sub_kind()
        if kind:
            accs = []
            try:
                from houdini_agent.subscription import manager
                accs = manager.accounts(kind)
            except Exception:
                pass
            brand = {"claude": "Claude", "antigravity": "Google 反重力"}.get(kind, "ChatGPT")
            login_lbl = (self.tr("登录 Google 反重力…") if kind == "antigravity"
                         else self.tr("登录 %s 订阅…" % brand))
            label = ((self.tr("账号：") + (accs[0].get("email") or self.tr(brand))) if accs
                     else login_lbl)
            items += [{"sep": True}, {"label": label, "val": "__sub_account__", "checked": False}]
            if accs:
                items.append({"label": self.tr("退出登录"), "val": "__sub_logout__", "checked": False})
        return items

    # ---- slots from QML ----
    @Slot(str, result=bool)
    def setProvider(self, v):
        if self._running:
            self.toast.emit("当前正在运行，模型切换将在本轮结束后再操作")
            return False
        is_custom = self._is_custom_key(v)
        if v == self._provider:
            return False
        if not is_custom and v not in MODEL_MAP:
            return False
        if is_custom and self._custom_provider(v) is None:
            return False
        self._provider = v
        models = self._provider_model_names(v)
        if self._model_name not in models and models:
            self._model_name = models[0]
            self.modelChanged.emit()
        if is_custom:
            self._apply_custom_provider(self._custom_provider(v))
        self.providerChanged.emit()
        self.tokensChanged.emit()
        self._save_prefs()
        if self._sub_kind(v) and not self._sub_logged_in(v):
            self._sub_dialog_provider = v
            self.openSubscriptionDialog.emit()
        return True

    @Slot(str, result=bool)
    def setModel(self, v):
        if v == "__sub_account__":
            self._sub_dialog_provider = self._provider
            self.openSubscriptionDialog.emit()
            return False
        if v == "__sub_logout__":
            self._confirm_sub_logout()
            return False
        if self._running:
            self.toast.emit("当前正在运行，模型切换将在本轮结束后再操作")
            return False
        if v and v != self._model_name:
            self._model_name = v
            self.modelChanged.emit()
            self._save_prefs()
            return True
        return False

    # ---- 自定义供应商管理（设置页「模型与 Provider」用） ----
    @Slot(result="QVariantList")
    def customProviderItems(self):
        out = []
        for p in self._custom_providers:
            pid = p.get("id", "")
            out.append({
                "id": pid, "name": p.get("name", ""),
                "base_url": p.get("base_url", ""), "api_key": p.get("api_key", ""),
                "anthropic": bool(p.get("anthropic")),
                "models": [{"name": m.get("name", ""),
                            "context": self._to_int(m.get("context"), 128000) or 128000,
                            "vision": bool(m.get("vision"))} for m in (p.get("models") or [])],
                "active": self._provider == ("custom:" + pid),
                "configured": bool(p.get("base_url")) and bool(p.get("models")),
            })
        return out

    @Slot(result="QVariantList")
    def builtinProviderItems(self):
        out = []
        for k in MODEL_MAP.keys():
            if k == "custom":
                continue
            out.append({"key": k, "name": PROVIDER_LABELS.get(k, k),
                        "active": self._provider == k, "models": list(MODEL_MAP.get(k, []))})
        return out

    @Slot(str, result="QVariantMap")
    def saveCustomProvider(self, payload_json):
        try:
            d = json.loads(payload_json) if payload_json else {}
        except Exception:
            return {"ok": False, "error": self.tr("无效数据")}
        name = (d.get("name") or "").strip()
        url = (d.get("base_url") or "").strip()
        if not name:
            return {"ok": False, "error": self.tr("请填写名称")}
        if not url or self._is_placeholder_custom_url(url):
            return {"ok": False, "error": self.tr("请填写有效的 Base URL")}
        models = []
        for m in (d.get("models") or []):
            nm = (m.get("name") or "").strip()
            if not nm:
                continue
            models.append({"name": nm,
                           "context": self._to_int(m.get("context"), 128000) or 128000,
                           "vision": bool(m.get("vision"))})
        if not models:
            return {"ok": False, "error": self.tr("请至少添加一个模型")}
        pid = (d.get("id") or "").strip() or uuid.uuid4().hex[:8]
        entry = {"id": pid, "name": name, "base_url": url,
                 "api_key": (d.get("api_key") or "").strip(),
                 "anthropic": bool(d.get("anthropic")), "models": models}
        found = False
        for i, p in enumerate(self._custom_providers):
            if p.get("id") == pid:
                self._custom_providers[i] = entry
                found = True
                break
        if not found:
            self._custom_providers.append(entry)
        self._save_custom_providers()
        if self._provider == ("custom:" + pid):
            self._apply_custom_provider(entry)
            names = [m["name"] for m in models]
            if self._model_name not in names:
                self._model_name = names[0]
                self.modelChanged.emit()
            self.providerChanged.emit()
            self.tokensChanged.emit()
        self.customProvidersChanged.emit()
        return {"ok": True, "id": pid}

    @Slot(str)
    def deleteCustomProvider(self, pid):
        pid = (pid or "").strip()
        self._custom_providers = [p for p in self._custom_providers if p.get("id") != pid]
        self._save_custom_providers()
        if self._provider == ("custom:" + pid):
            self._revert_provider_from_custom()
        self.customProvidersChanged.emit()

    @Slot(str, str)
    def selectProviderModel(self, provider_key, model_name):
        if provider_key and provider_key != self._provider:
            self.setProvider(provider_key)
        if model_name and model_name != self._model_name:
            self.setModel(model_name)

    @Slot(str)
    def openProviderApiKey(self, provider):
        if self._sub_kind(provider or self._provider):
            self._sub_dialog_provider = provider or self._provider
            self.openSubscriptionDialog.emit()
            return
        try:
            self.openApiKeyDialog.emit(provider or self._provider)
        except Exception as e:
            print("[controller] open provider api key failed:", e)

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

    @Slot(str)
    def setAppTheme(self, v):
        v = str(v or "")
        if v in ("noir", "graphite", "midnight", "day") and v != self._app_theme:
            self._app_theme = v
            self._set_pref("app_theme", v)
            self.appearanceChanged.emit()

    @Slot(str)
    def setAccentKey(self, v):
        v = str(v or "")
        if v in ("warm", "steel", "celadon", "clay", "neutral") and v != self._accent_key:
            self._accent_key = v
            self._set_pref("accent_key", v)
            self.appearanceChanged.emit()

    @Slot(str)
    def setFontFamilyKey(self, v):
        v = str(v or "")
        if v in ("editorial", "modern", "mono") and v != self._font_family_key:
            self._font_family_key = v
            self._set_pref("font_family_key", v)
            self.appearanceChanged.emit()

    @Slot(str)
    def setDensityKey(self, v):
        v = str(v or "")
        if v in ("compact", "normal", "roomy") and v != self._density_key:
            self._density_key = v
            self._set_pref("density_key", v)
            self.appearanceChanged.emit()

    # ---- 起手式 / 模板库 ----


    # ---- multi-session ----
    def _add_session(self, title=None):
        self._sessions.append({"id": uuid.uuid4().hex[:8], "title": title or "新对话",
                               "rows": [], "history": [], "updated": time.time(),
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
        s["updated"] = time.time()

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

    # ---- 图片外置 ----
    # 截图 / 附图以前以完整 base64 data URI 存在消息行里：单张数百 KB，每次刷新都整串
    # 转给 QML，每轮结束整体写盘（实测长会话的会话文件达数十 MB）。现在写到缓存目录
    # 的 images/ 下，消息行只存 file:/// 地址；data URI 无法落盘时原样保留（兼容）。
    _IMG_EXT = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
                "image/webp": ".webp", "image/gif": ".gif", "image/bmp": ".bmp"}

    def _image_dir(self):
        return (self._cache_dir / "images") if self._cache_dir else None

    def _externalize_image(self, src):
        """data:image/...;base64,... → 缓存文件的 file:/// URL；其它输入原样返回（同一对象）。"""
        if not isinstance(src, str) or not src.startswith("data:image/"):
            return src
        d = self._image_dir()
        if d is None:
            return src
        try:
            head, b64 = src.split(",", 1)
            mt = head[5:].split(";", 1)[0].lower()
            raw = base64.b64decode(b64)
            p = d / (hashlib.sha1(raw).hexdigest()[:24] + self._IMG_EXT.get(mt, ".img"))
            if not p.exists():
                d.mkdir(parents=True, exist_ok=True)
                tmp = p.with_name(p.name + ".tmp")
                tmp.write_bytes(raw)
                os.replace(str(tmp), str(p))
            return p.resolve().as_uri()
        except Exception as e:
            print("[controller] externalize image failed:", e)
            return src

    def _externalize_rows(self, rows):
        """把行里的 data URI 图片外置（就地修改）。返回是否有改动。"""
        changed = False
        for it in rows or []:
            pay = it.get("payload") if isinstance(it, dict) else None
            if not isinstance(pay, dict):
                continue
            if it.get("type") == "ai":
                for b in pay.get("blocks", []) or []:
                    if isinstance(b, dict) and b.get("kind") == "image":
                        src = b.get("src")
                        new = self._externalize_image(src)
                        if new is not src:
                            b["src"] = new
                            changed = True
            elif it.get("type") == "user":
                imgs = pay.get("images")
                if imgs:
                    new = [self._externalize_image(x) for x in imgs]
                    if any(a is not b for a, b in zip(new, imgs)):
                        pay["images"] = new
                        changed = True
        return changed

    def _gc_images(self, grace_days=3):
        """删掉没有任何会话引用、且超过宽限期的外置图片（删会话后残留的）。"""
        d = self._image_dir()
        if d is None or not d.is_dir():
            return
        used = set()
        for sess in self._sessions:
            for it in sess.get("rows", []) or []:
                pay = it.get("payload") if isinstance(it, dict) else None
                if not isinstance(pay, dict):
                    continue
                srcs = [b.get("src") for b in (pay.get("blocks", []) or [])
                        if isinstance(b, dict) and b.get("kind") == "image"]
                srcs += list(pay.get("images") or [])
                for u in srcs:
                    if isinstance(u, str) and u.startswith("file:"):
                        used.add(u.rsplit("/", 1)[-1])
        cutoff = time.time() - grace_days * 86400
        try:
            for f in d.iterdir():
                try:
                    if f.name not in used and f.stat().st_mtime < cutoff:
                        f.unlink()
                except Exception:
                    pass
        except Exception:
            pass

    def _upgrade_stored_blocks(self, blocks):
        """历史消息是保存时就转好的富文本：加载时补上后来才有的渲染（就地修改）。
        - 回答里的 LaTeX 公式（加公式渲染之前保存的会话）
        - 思考块的 **标题** 加粗（以前只存了纯文本）"""
        try:
            from houdini_agent.ui_qml.mathrender import rerender_html
        except Exception:
            rerender_html = None
        for b in blocks or []:
            if not isinstance(b, dict):
                continue
            kind = b.get("kind")
            if kind == "prose" and rerender_html and b.get("html"):
                try:
                    b["html"] = rerender_html(b["html"])
                except Exception as e:
                    print("[controller] re-render math failed:", e)
            elif kind == "thinking" and b.get("text") and not b.get("html"):
                b["html"] = self._think_html(b["text"])

    def _load_active(self):
        # 切换/新建会话时清掉上一会话残留的附带图片，避免它泄漏到新会话的图生图里。
        self._last_attached_images = []
        s = self._sessions[self._active]
        rows = copy.deepcopy(s.get("rows", []))
        self._sanitize_rows(rows)
        for it in rows:
            if it.get("type") == "ai":
                blocks = (it.get("payload", {}) or {}).get("blocks", [])
                self._upgrade_stored_blocks(blocks)
                it["payload"] = {"bm": BlockModel(blocks)}
        self._model.load(rows)
        p, m = s.get("provider"), s.get("model")
        p_ok = (self._is_custom_key(p) and self._custom_provider(p) is not None) or (p in MODEL_MAP)
        names = self._provider_model_names(p) if p_ok else []
        if p_ok and (not m or m in names):
            changed_p = p != self._provider
            changed_m = bool(m and m != self._model_name)
            self._provider = p
            if m:
                self._model_name = m
            if self._is_custom_key(p):
                self._apply_custom_provider(self._custom_provider(p))
            if changed_p:
                self.providerChanged.emit()
            if changed_m:
                self.modelChanged.emit()
        if self._session:
            self._session.history = copy.deepcopy(s.get("history", []))
        self._bind_usage(s)
        self._reset_run_state()
        self._plan_row = None
        self._plan_payload = None
        self._plan_idmap = {}
        self._refresh_tokens()

    @staticmethod
    def _new_token_stats():
        return {"input": 0, "output": 0, "reasoning": 0,
                "cache_read": 0, "cache_write": 0, "cache_miss": 0,
                "total": 0, "requests": 0}

    def _bind_usage(self, s, reset=False):
        """让 _token_stats / _ctx_anchor 指向会话 s 自己的用量记录（原地累加，随会话保存）。"""
        u = s.get("usage")
        if reset or not isinstance(u, dict):
            u = s["usage"] = {}
        stats = u.get("stats")
        if not isinstance(stats, dict):
            stats = u["stats"] = {}
        for k, v in self._new_token_stats().items():
            if not isinstance(stats.get(k), (int, float)):
                stats[k] = v
        self._token_stats = stats
        a = u.get("ctx")
        self._ctx_anchor = a if isinstance(a, dict) and a.get("tokens") else None
        if self._ctx_anchor and self._ctx_anchor.get("ratio"):
            self._ctx_ratio = float(self._ctx_anchor["ratio"])

    @Slot(result="QVariantList")
    def sessionItems(self):
        return [{"title": (self.tr(s["title"]) if s["title"] == "新对话" else s["title"]),
                 "active": (i == self._active),
                 "time": self._rel_time(s.get("updated"))}
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

    @Slot(bool)
    def setAskDeleteSession(self, on):
        on = bool(on)
        if on != self._ask_delete_session:
            self._ask_delete_session = on
            self._set_pref("ask_delete_session", on)
            self.askDeleteSessionChanged.emit()

    @Slot(int)
    def deleteSession(self, idx):
        """删除一个会话（不再弹内置确认——确认由 QML 侧的删除弹窗负责）。"""
        if self._running:
            self.toast.emit(self.tr("当前正在运行，不能关闭会话"))
            return
        if idx < 0 or idx >= len(self._sessions) or len(self._sessions) <= 1:
            return
        self._close_session_now(idx)

    def _rel_time(self, ts):
        """把 epoch 秒转成简短的相对时间标签（随语言切换中/英）。"""
        try:
            d = time.time() - float(ts)
        except (TypeError, ValueError):
            return ""
        if d < 0:
            d = 0
        zh = self._lang != "en"
        if d < 60:
            return "刚刚" if zh else "just now"
        if d < 3600:
            n = int(d // 60)
            return ("%d 分钟前" % n) if zh else ("%dm ago" % n)
        if d < 86400:
            n = int(d // 3600)
            return ("%d 小时前" % n) if zh else ("%dh ago" % n)
        days = int(d // 86400)
        if days < 7:
            return ("%d 天前" % days) if zh else ("%dd ago" % days)
        return ("%d 周前" % (days // 7)) if zh else ("%dw ago" % (days // 7))

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
        self._bind_usage(self._sessions[self._active], reset=True)
        self._reset_run_state()
        self._plan_row = None
        self._refresh_tokens()
        self._save_all()
        self.toast.emit(self.tr("清空对话"))

    @Slot(result="QVariantList")
    def overflowItems(self):
        # ⋯ 菜单只保留「一次性动作」+「设置…」入口；所有配置/开关/管理器已迁到设置浮窗，
        # 不在此重复出现（迁移 = 移过去，不是复制）。
        t = self.tr
        return [
            {"label": t("设置…"), "val": "settings"},
            {"sep": True},
            {"label": self.tr("Meshy 资产库"), "val": "library"},
            {"label": t("导出对话"), "val": "export"},
            {"label": t("Token 分析"), "val": "tokens"},
            {"label": t("压缩上下文"), "val": "optimize"},
            {"label": t("缓存位置"), "val": "cache"},
            {"sep": True},
            {"label": t("清空对话"), "val": "clear"},
        ]

    @Slot(str)
    def menuAction(self, action):
        if action == "settings":
            self.openSettingsDialog.emit()
        elif action == "clear":
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
            self.cookRealtimeChanged.emit()
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
            self._start_optimize_context()

    def _start_optimize_context(self):
        if self._running:
            self.toast.emit("正在生成回复，请稍后再压缩上下文")
            return
        if self._optimizing:
            self.toast.emit("正在压缩上下文，请稍候...")
            return
        if not self._session or not self._session.history:
            self.toast.emit("上下文无需压缩")
            return

        sess_id = self._sessions[self._active]["id"] if self._sessions else ""
        hist_snapshot = copy.deepcopy(self._session.history)
        model = self._model_name
        prov_arg = self._provider
        if self._is_custom_key(self._provider):
            self._apply_custom_provider(self._custom_provider(self._provider))
            prov_arg = "custom"
        supports_vision = self._current_model_supports_vision()
        ctx_limit = self._active_model_context()
        state_ctx = self._active_task_state_summary()

        self._optimizing = True
        self.toast.emit("正在智能压缩上下文...")

        def work():
            try:
                new_hist, stats = self._session.client.compress_history(
                    hist_snapshot,
                    context_limit=ctx_limit,
                    model=model,
                    provider=prov_arg,
                    supports_vision=supports_vision,
                    use_llm=True,
                    state_context=state_ctx,
                )
                payload = {"ok": True, "session_id": sess_id, "history": new_hist, "stats": stats}
            except Exception as e:
                payload = {"ok": False, "session_id": sess_id, "error": str(e)}
            self._sigOptimizeDone.emit(json.dumps(payload, ensure_ascii=False, default=str))

        threading.Thread(target=work, daemon=True).start()

    def _active_task_state_summary(self):
        """提取当前活跃的 Plan / Todo 进度摘要，供压缩检查点保活。"""
        lines = []
        plan = getattr(self, "_plan_payload", None)
        if isinstance(plan, dict) and plan.get("steps"):
            title = str(plan.get("title") or "Current Plan").strip()
            phase = str(getattr(self, "_plan_phase", "") or "")
            lines.append(f"Plan: {title}" + (f" (phase: {phase})" if phase else ""))
            for idx, st in enumerate(plan.get("steps") or [], 1):
                if not isinstance(st, dict):
                    continue
                status = st.get("status") or st.get("state") or "pending"
                step_title = str(st.get("title") or st.get("text") or st.get("description") or "").strip()
                if step_title:
                    lines.append(f"  {idx}. [{status}] {step_title}")
        todo_blk = getattr(self, "_todo_block", None)
        if isinstance(todo_blk, dict) and todo_blk.get("items"):
            lines.append("Todos:")
            for it in todo_blk.get("items") or []:
                if not isinstance(it, dict):
                    continue
                status = it.get("status") or "pending"
                text = str(it.get("text") or "").strip()
                if text:
                    lines.append(f"  - [{status}] {text}")
        return "\n".join(lines)

    @Slot(str)
    def _ui_compaction(self, raw):
        try:
            d = json.loads(raw)
        except Exception:
            return
        before_k = self._fmt_k(d.get("before_tokens", 0))
        after_k = self._fmt_k(d.get("after_tokens", 0))
        used_llm = bool(d.get("used_llm", False))
        label = "%s → %s tokens%s" % (before_k, after_k, " · LLM" if used_llm else "")
        self._blocks.append({
            "kind": "compaction",
            "before": before_k,
            "after": after_k,
            "used_llm": used_llm,
            "label": label,
        })
        self._flush()
        self.toast.emit("已自动压缩上下文：%s" % label)

    def _on_optimize_done(self, raw):
        self._optimizing = False
        try:
            data = json.loads(raw)
        except Exception:
            self.toast.emit("上下文压缩失败")
            return
        if not data.get("ok"):
            self.toast.emit("压缩失败：%s" % (data.get("error") or "未知错误"))
            return
        # 若压缩期间用户切走了会话或发起了新请求，则丢弃过时结果
        cur_id = self._sessions[self._active]["id"] if self._sessions else ""
        if self._running or not self._session or data.get("session_id") != cur_id:
            return
        stats = data.get("stats") or {}
        # 将手动压缩的 LLM 摘要 token 计入会话统计
        u = stats.get("usage")
        if isinstance(u, dict) and u:
            self._accumulate_usage({"usage": u, "call_records": []})
        if not stats.get("compressed"):
            self.toast.emit(stats.get("reason") or "上下文无需压缩")
            return
        self._session.history = data.get("history") or []
        self._ctx_anchor = None     # 真实用量对应的是压缩前的 history
        self._sessions[self._active].setdefault("usage", {}).pop("ctx", None)
        self._refresh_tokens()
        self._save_all()
        before_k = self._fmt_k(stats.get("before_tokens", 0))
        after_k = self._fmt_k(stats.get("after_tokens", 0))
        mode_tag = "（LLM 摘要）" if stats.get("used_llm") else ""
        self.toast.emit("已压缩上下文%s：%s → %s tokens" % (mode_tag, before_k, after_k))

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
            self.toast.emit("AI 后端尚未就绪，请稍候几秒再试")
            return
        if self._sub_kind():
            self._sub_dialog_provider = self._provider
            self.openSubscriptionDialog.emit()
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
            self.toast.emit("AI 后端尚未就绪，请稍候几秒再试")
            return False
        try:
            self._session.client.set_api_key(key, persist=True, provider=provider)
            self.toast.emit("已保存 %s 的 API Key。" % provider)
            return True
        except Exception as e:
            self._info("API Key", "保存失败：%s" % e)
            return False

    def _export_chat(self):
        # 优先用活动会话的实时历史；未连接 / 重启后会话未挂载时，回退到已恢复/已保存的会话历史，
        # 这样"看得到历史却导不出"（_session 尚为空）的情况也能正常导出。
        hist = list(self._session.history) if (self._session and getattr(self._session, "history", None)) else []
        if not hist and self._sessions and 0 <= self._active < len(self._sessions):
            hist = self._sessions[self._active].get("history", []) or []
        if not hist:
            self._info("导出对话", "当前会话没有可导出的内容。")
            return
        try:
            from houdini_agent.utils.training_data_exporter import export_chat_training_data
            # 用会话真实的系统提示导出（带 thinking 版本，与历史里注入的 <think> 引导一致）；
            # 取不到时传 None，由导出器回退到默认提示。
            sys_prompt = getattr(self._session, "_sys_think", None) if self._session else None
            path = export_chat_training_data(hist, system_prompt=sys_prompt)
            self._info("导出对话", "已导出到：\n%s" % path)
        except Exception as e:
            self._info("导出对话", "导出失败：%s" % e)


    # ---- 安装阶段（主线程） ----
    # 1) Agent 正在回答或还有后台 Meshy 任务时不安装（安装要退出应用，会打断它们），
    #    进入 waiting，空闲后自动继续；
    # 2) 免安装器原子替换（mode='swap'）或用户目录静默安装直接保存退出，1 秒内无窗口换新重启；
    # 3) 若装在 Program Files 需要 UAC，等授权通过再退出，拒绝授权时保持运行并可重试。


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

    # ---- 订阅登录（本机 CPA：ChatGPT 走 Codex OAuth，Claude 走 Claude Code OAuth） ----
    # 后台线程只发 (str, str) 信号回主线程（PySide6 跨线程 Signal(dict) 会崩，见 memory）。


    @Slot(str)
    def openExternalUrl(self, url):
        try:
            import webbrowser
            if url:
                webbrowser.open(url)
        except Exception:
            pass


    def _open_token_panel(self):
        # rendered by a native QML popup (see Main.qml), not a QtWidgets dialog
        self.openTokenDialog.emit()

    @Slot(result="QVariantMap")
    def tokenStats(self):
        s = self._token_stats
        ctx_used = self._estimate_ctx()
        ctx_limit = self._context_limit()
        self._ctx_text = "%s / %s" % (self._fmt_k(ctx_used), self._fmt_k(ctx_limit))
        # reasoning 是 output 的子集，回退计算 total 时绝不能重复累加 reasoning
        total = s["total"] or (s["input"] + s["output"])
        requests = max(0, s["requests"])
        cache_read = int(s.get("cache_read", 0) or 0)
        cache_write = int(s.get("cache_write", 0) or 0)
        cache_miss = int(s.get("cache_miss", 0) or 0)
        cache_denom = int(s.get("input", 0) or 0) or (cache_read + cache_write + cache_miss)
        hit_rate = (min(float(cache_read), float(cache_denom)) / float(cache_denom)) if cache_denom > 0 else 0.0
        return {
            "requests": s["requests"], "input": s["input"], "output": s["output"],
            "reasoning": s["reasoning"], "cache_read": cache_read,
            "cache_write": cache_write, "cache_miss": cache_miss, "total": total,
            "avg_per_request": int(total / requests) if requests else 0,
            "ctx_used": ctx_used, "ctx_limit": ctx_limit, "ctx_text": self._ctx_text,
            "ctx_pct": float(ctx_used) / float(ctx_limit or 1),
            "cache_total": cache_denom,
            "cache_hit_rate": hit_rate,
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

    # ---- 多自定义供应商：数据模型 / 持久化 / 运行时应用 ----
    @staticmethod
    def _is_custom_key(key):
        return bool(key) and isinstance(key, str) and key.startswith("custom:")

    def _custom_provider(self, key):
        """按激活键 'custom:<id>' 取得供应商 dict；非自定义或未找到返回 None。"""
        if not self._is_custom_key(key):
            return None
        pid = key[len("custom:"):]
        for p in self._custom_providers:
            if p.get("id") == pid:
                return p
        return None

    def _provider_model_names(self, key):
        p = self._custom_provider(key)
        if p is not None:
            return [m.get("name") for m in (p.get("models") or []) if m.get("name")]
        kind = self._sub_kind(key)
        if kind:
            try:
                from houdini_agent.subscription import manager
                return manager.cached_models(kind)
            except Exception:
                pass
        return MODEL_MAP.get(key, [])

    def _model_meta(self, key, model):
        p = self._custom_provider(key)
        if p is not None:
            for m in (p.get("models") or []):
                if m.get("name") == model:
                    return m
        return None

    def _active_model_context(self):
        meta = self._model_meta(self._provider, self._model_name)
        if meta is not None:
            return self._to_int(meta.get("context"), 128000) or 128000
        kind = self._sub_kind()
        if kind:
            # 订阅渠道的窗口与同名模型的 API 直连不同（Codex 多为 272K），按订阅目录取
            from houdini_agent.subscription import model_context
            return model_context(kind, self._model_name)
        # 静态表未命中时回落到 catalog 的推断（缓存 meta → 静态表 → 家族推断）
        return CONTEXT_LIMITS.get(self._model_name) or catalog.context_limit(self._model_name, self._provider)

    def _active_model_vision(self):
        meta = self._model_meta(self._provider, self._model_name)
        if meta is not None:
            return bool(meta.get("vision"))
        if self._sub_kind():
            return True     # Codex / Claude 订阅目录里的模型都支持图片输入
        if self._model_name in VISION_MODELS:
            return True
        return catalog.supports_vision(self._model_name, self._provider)

    def _apply_custom_provider(self, p):
        """把某自定义供应商的 url/key/协议应用到 client（被选中或运行前调用）。"""
        if not (self._session and p):
            return
        try:
            self._session.client.set_custom_provider(
                str(p.get("base_url", "")), str(p.get("api_key", "")),
                True, bool(p.get("anthropic")))
        except Exception as e:
            print("[controller] apply custom provider failed:", e)

    def _save_custom_providers(self):
        try:
            QSettings("HoudiniAI", "Assistant").setValue(
                "custom_providers", json.dumps(self._custom_providers, ensure_ascii=False))
        except Exception as e:
            print("[controller] save custom providers failed:", e)

    def _load_custom_providers(self):
        """从 QSettings 读取自定义供应商列表；空且存在旧版单一 custom 配置时自动迁移。"""
        s = QSettings("HoudiniAI", "Assistant")
        out = []
        try:
            raw = s.value("custom_providers", "")
            if raw:
                data = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(data, list):
                    for p in data:
                        if isinstance(p, dict) and p.get("id") and p.get("base_url"):
                            p.setdefault("name", p.get("id"))
                            p.setdefault("api_key", "")
                            p.setdefault("anthropic", False)
                            p.setdefault("models", [])
                            out.append(p)
        except Exception as e:
            print("[controller] load custom providers failed:", e)
        # 旧版单一 custom 迁移成一个供应商条目，老用户不丢配置
        if not out:
            url = str(s.value("custom_url", "") or "")
            model = str(s.value("custom_model", "") or "")
            if url and model and not self._is_placeholder_custom_url(url):
                ctx = self._to_int(s.value("custom_context_limit", 128000), 128000)
                pid = uuid.uuid4().hex[:8]
                out.append({
                    "id": pid, "name": model, "base_url": url,
                    "api_key": str(s.value("custom_key", "") or ""),
                    "anthropic": str(s.value("custom_proto", "openai")) == "anthropic",
                    "models": [{"name": model, "context": ctx,
                                "vision": self._qbool(s.value("custom_supports_vision", False), False)}],
                })
                if str(s.value("last_provider", "")) == "custom":
                    s.setValue("last_provider", "custom:" + pid)
        self._custom_providers = out

    def _load_custom(self):
        """会话建立/切换后，如果当前激活的是某自定义供应商，把它的配置应用到 client。"""
        p = self._custom_provider(self._provider)
        if p is not None:
            self._apply_custom_provider(p)

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
        # 配置自定义 LLM Provider 只需要一个承载 client 配置的后端会话即可，
        # 与 Houdini 场景/Bridge 是否连通【无关】。此前用 _houdini_connected()
        # （依赖一次 Bridge ping）做门禁：Houdini 明明已连接、仅 ping 抖动/时机不对时
        # 会被误判为"未连接"，把用户错误地引去重开 Houdini。改为只看后端会话是否存在——
        # 会话在（哪怕 bridge 暂时不可达），配置都能写入 client，下次即生效。
        if self._session is None:
            # 后端会话随应用启动异步构建（不依赖 Bridge）；走到这说明仍在初始化或构建失败，
            # 与 Houdini 是否连接无关——不要把用户误导去开启动器。
            self._revert_provider_from_custom()
            self.toast.emit(self.tr("AI 后端尚未就绪，请稍候几秒再试"))
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
            self.toast.emit(self.tr("AI 后端尚未就绪，请稍候几秒再试"))
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

    # ---- external browser / Meshy 网页入口 ----
    def _open_browser(self, url):
        """在系统默认浏览器打开一个 URL（PySide6/2 兼容）。"""
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
            try:
                self.toast.emit(self.tr("无法打开链接：") + str(url))
            except Exception:
                pass


    # ---- node focus ----

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
            # 只重写有变化的会话：rows / history 每次快照都是新对象，比较身份即可。
            # 以前每轮结束把所有会话整体重新序列化写盘，长会话下这一步就要几百毫秒。
            saved = self._saved_refs
            for s in self._sessions:
                sig = self._session_sig(s)
                old = saved.get(s["id"])
                if old is not None and old[0] is sig[0] and old[1] is sig[1] and old[2:] == sig[2:]:
                    continue
                data = {"id": s["id"], "title": s["title"], "rows": s["rows"], "history": s["history"],
                        "provider": s.get("provider", self._provider),
                        "model": s.get("model", self._model_name),
                        "usage": s.get("usage") or {}}
                (self._cache_dir / ("session_%s.json" % s["id"])).write_text(
                    json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
                saved[s["id"]] = sig
            live = {s["id"] for s in self._sessions}
            for k in [k for k in saved if k not in live]:
                del saved[k]
        except Exception as e:
            print("[controller] save sessions failed:", e)

    def _session_sig(self, s):
        return (s.get("rows"), s.get("history"), s.get("title"),
                s.get("provider", self._provider), s.get("model", self._model_name))

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
                                   "model": d.get("model", entry.get("model", self._model_name)),
                                   "usage": d.get("usage") if isinstance(d.get("usage"), dict) else {}})
            if loaded:
                self._sessions = loaded
                self._active = min(int(manifest.get("active", 0)), len(loaded) - 1)
                # 磁盘内容即已保存状态；旧会话里内联的 base64 图片顺手外置，
                # 被迁移的会话不记签名，下面立即重写一次把文件瘦下来。
                migrated = False
                for sess in loaded:
                    if self._externalize_rows(sess["rows"]):
                        sess["rows"] = list(sess["rows"])
                        migrated = True
                    else:
                        self._saved_refs[sess["id"]] = self._session_sig(sess)
                self._load_active()
                self.sessionsChanged.emit()
                if migrated:
                    self._save_all()
                self._gc_images()
        except Exception as e:
            print("[controller] restore failed:", e)


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
        if self._sub_kind() and self._session and not self._sub_logged_in():
            self.toast.emit(self.tr("请先登录 %s" % PROVIDER_LABELS.get(self._provider, "")))
            self._sub_dialog_provider = self._provider
            self.openSubscriptionDialog.emit()
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
            payload["images"] = [self._externalize_image("data:%s;base64,%s" % (mt, b64))
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
        # 自定义供应商：跑之前把它的 url/key/协议应用到 client，并以 "custom" 走运行路由
        prov_arg = self._provider
        if self._is_custom_key(self._provider):
            self._apply_custom_provider(self._custom_provider(self._provider))
            prov_arg = "custom"
        if self._sub_kind():
            try:
                self._session.client.set_reasoning(self._current_effort(), summary=self._show_thinking)
            except Exception as e:
                print("[controller] set reasoning failed:", e)
        ctx_limit = self._active_model_context()
        initial_ctx = int(self._anchored_ctx() or 0)
        state_ctx = self._active_task_state_summary()
        threading.Thread(
            target=self._worker,
            args=(user_text, self._model_name, prov_arg, self._mode, tools, max_iter,
                  images, supports_vision, ctx_limit, initial_ctx, state_ctx),
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
        self._resolve_cache = {}
        self._in_think = False
        self._cbuf = ""
        self._turn_bridge_target = None

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
        # 人在环等待中（画廊挑选 / 确认卡 / ask_question）：是在等用户操作，不是卡死，
        # 别弹"无进展，可点 Stop"误导用户。
        if getattr(self, "_interactive", None):
            self._last_activity = time.monotonic()
            return
        idle = time.monotonic() - self._last_activity
        if idle > 45 and not self._watchdog_warned:
            self._watchdog_warned = True
            self.toast.emit("执行中超过 45 秒无进展，可点 Stop 中断。若在 cook，请按 Esc。")
        if idle > 180 and self._plan_phase == "executing" and self._session:
            self._session.stop()
            self.toast.emit("Plan 执行长时间无进展，已请求停止。")

    # ---- worker thread ----
    def _worker(self, text, model, provider, mode, tools, max_iter, images=None,
                supports_vision=False, context_limit=None,
                initial_actual_tokens=0, state_context=""):
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

        def cb_compaction(meta):
            self._last_activity = time.monotonic()
            try:
                self._sigCompaction.emit(json.dumps({
                    "before_tokens": int((meta or {}).get("before_tokens") or 0),
                    "after_tokens": int((meta or {}).get("after_tokens") or 0),
                    "used_llm": bool((meta or {}).get("used_llm", False)),
                }, ensure_ascii=False))
            except Exception:
                pass

        try:
            self._log_external("Worker start provider=%s model=%s mode=%s" % (provider, model, mode))
            # 本轮开始时在后台线程锁定当前 Houdini 实例/场景指纹，防止中途关窗重连误写到另一份 .hip
            if getattr(self._session, "bridge", None) is not None:
                self._turn_bridge_target = self._probe_current_bridge_target()
            result = self._session.run(
                text, model, provider, mode,
                {"on_content": cb_content, "on_thinking": cb_think,
                 "on_tool_call": cb_tool_call, "on_tool_result": cb_tool_result,
                 "on_tool_args_delta": cb_args_delta,
                 "on_iteration_start": lambda i: self._sigStatus.emit("generating"),
                 "on_compaction": cb_compaction},
                context_limit=(context_limit if context_limit else CONTEXT_LIMITS.get(model, 128000)),
                enable_thinking=True,
                # 订阅渠道（ChatGPT / Claude）走原生推理，不再要求模型在正文里写 <think>
                think_tags=(self._sub_kind(provider) is None),
                supports_vision=supports_vision,
                tools=tools, max_iter=max_iter,
                images=images, rag=True, memory=self._memory_enabled,
                initial_actual_tokens=initial_actual_tokens,
                state_context=state_context,
            )
            if isinstance(result, dict) and not result.get("ok", True):
                err = str(result.get("error") or result.get("final_content") or "AI request failed")
                self._log_external("Worker result error: " + err[:1000])
                self._accumulate_usage(result)      # 出错前已完成的请求也照实计入
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
        s["cache_write"] += u.get("cache_write_tokens", 0)
        s["cache_miss"] = s.get("cache_miss", 0) + u.get("cache_miss_tokens", 0)
        s["total"] += u.get("total_tokens", 0)
        recs = result.get("call_records")
        if isinstance(recs, list):
            self._call_records.extend(recs)
            s["requests"] += len(recs) if recs else (1 if u else 0)
        elif u:
            s["requests"] += 1
        self._update_ctx_anchor(recs)
        # note: _refresh_tokens() is invoked on the main thread in _ui_done

    def _update_ctx_anchor(self, recs):
        """用本轮最后一次 API 调用的真实用量锚定上下文占用：
        prompt_tokens 是模型实际看到的全部输入（系统提示/工具定义/历史/本轮工具结果），
        再加上它的回复（推理 token 不回传，扣掉），就是此刻 history 的真实 token 数。"""
        last = None
        for r in reversed(recs or []):
            if isinstance(r, dict) and r.get("input_tokens"):
                last = r
                break
        if not last or not self._session or not self._sessions:
            return
        out = int(last.get("output_tokens") or 0)
        reason = int(last.get("reasoning_tokens") or 0)
        if 0 < reason <= out:
            out -= reason
        anchor = {"tokens": int(last["input_tokens"]) + out,
                  "hist_len": len(getattr(self._session, "history", []) or [])}
        self._ctx_anchor = anchor
        self._sessions[self._active].setdefault("usage", {})["ctx"] = anchor

    # ---- tool executor (called by AIClient on the worker thread) ----
    _CONNECTION_TOOLS = frozenset({
        "check_houdini_connection", "repair_houdini_connection",
        "list_houdini_instances", "switch_houdini_instance",
        "run_in_houdini_instance", "transfer_nodes_between_instances",
    })

    def _dispatch_ui_local_tool(self, tool_name, kwargs):
        """统一分派 UI 本地工具（不经过 Houdini/Bridge/网络）。未命中返回 None。"""
        handlers = {
            "create_plan": lambda kw: self._handle_create_plan(kw),
            "update_plan_step": lambda kw: self._handle_update_plan_step(kw),
            "ask_question": lambda kw: self._handle_ask_question(kw),
            "add_todo": lambda kw: self._handle_todo("add_todo", kw),
            "update_todo": lambda kw: self._handle_todo("update_todo", kw),
            "meshy_task_status": lambda kw: self._meshy_task_status(kw),
        }
        fn = handlers.get(tool_name)
        if fn is not None:
            return fn(kwargs)
        if tool_name in MESHY_LOCAL_TOOLS and _meshy is not None:
            try:
                return _meshy.search_animations(kwargs)
            except Exception as e:
                return {"success": False, "error": "动作库检索失败: %s" % e}
        return None

    def _tool_executor(self, tool_name, **kwargs):
        self._last_activity = time.monotonic()
        if self._session and self._session.client.is_stop_requested():
            return {"success": False, "error": "stopped"}

        local_res = self._dispatch_ui_local_tool(tool_name, kwargs)
        if local_res is not None:
            return local_res

        # Meshy 非交互网络工具：同轮去重命中时直接返回旧结果，且【放在确认门之前】——
        # 否则会让用户为一个其实只返回缓存、不会真正生成/扣费的重复调用再确认一次。
        is_meshy_net = tool_name in MESHY_NETWORK_TOOLS and _meshy is not None
        is_meshy_interactive = tool_name in MESHY_INTERACTIVE_TOOLS
        meshy_sig = None
        if is_meshy_net and not is_meshy_interactive:
            try:
                meshy_sig = tool_name + "|" + json.dumps(kwargs, sort_keys=True, default=str)
            except Exception:
                meshy_sig = tool_name
            _cache = getattr(self, "_meshy_turn_cache", None)
            if _cache is not None and meshy_sig in _cache:
                print("[meshy] 同轮去重（确认前）：跳过重复调用 %s" % tool_name)
                return _cache[meshy_sig]

        # per-tool approval (confirm mode)
        if self._confirm_mode and tool_name in CONFIRM_TOOLS:
            if not self._await_confirm(tool_name, kwargs):
                return {"success": False, "error": "用户取消了该操作"}

        # 连接诊断/修复与多 Houdini 实例协作：必须在 app 侧执行，绝不走单一默认 bridge
        if tool_name in self._CONNECTION_TOOLS:
            return self._run_connection_tool(tool_name, kwargs)

        # Meshy 网络工具：app 侧后台线程执行，绝不进 Houdini/bridge
        if is_meshy_net:
            cache = getattr(self, "_meshy_turn_cache", None)
            if is_meshy_interactive:
                mode = "concept" if tool_name == "meshy_concept_to_3d" else "image"
                res = self._run_meshy_concept(kwargs, mode=mode, tool_name=tool_name)
            else:
                cnt = int(kwargs.get("count", 1) or 1)
                if cnt > 1 and tool_name in MESHY_BATCH_TOOLS:
                    res = self._run_meshy_batch(tool_name, kwargs, cnt)
                else:
                    res = self._run_meshy_network(tool_name, kwargs)
            # 只缓存成功结果：取消/失败不缓存，便于用户重试；交互工具不缓存。
            if cache is not None and meshy_sig and isinstance(res, dict) and res.get("success"):
                cache[meshy_sig] = res
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
            # 断连自愈 + 多实例/场景写锁：自动重连保留，但如果目标 PID 或 .hip 文件已变，
            # 拦截写操作以防把 A.hip 的修改误发到 B.hip。
            ok, guard_err = self._check_bridge_write_target(tool_name)
            if not ok:
                return guard_err
            try:
                res = self._session.mcp.execute_tool(tool_name, kwargs)
                self._last_activity = time.monotonic()
                # save_hip 会在同一 Houdini 进程内改变 .hip 路径，成功后刷新目标锁
                if tool_name == "save_hip" and isinstance(res, dict) and res.get("success"):
                    updated = self._probe_current_bridge_target()
                    if updated:
                        self._turn_bridge_target = updated
                return res
            except Exception as e:
                if self._session.bridge.ping() is None and not self._heal_bridge():
                    return {"success": False, "error": _BRIDGE_LOST_MSG}
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


    # ---- node-operation rows + Keep/Undo ----


    @Slot(str)
    def _ui_image(self, data_uri):
        self._blocks.append({"kind": "image", "src": self._externalize_image(data_uri)})
        self._flush()

    @Slot(str)
    def _ui_status(self, phase):
        self._touch_activity()
        if phase != self._status_phase:
            self._status_phase = phase
            self.statusChanged.emit()

    # ---- todo card ----


    # ---- Meshy generation card ----

    # ---- Meshy concept-gallery card ----


    # ---- Meshy 云资产库（侧滑抽屉） ----


    # ---- Meshy 账号（API Key 即登录凭证；公开 API 无 OAuth 登录） ----


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


    # ---- batch undo / keep ----


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
        return self._active_model_context()

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
        actual = self._anchored_ctx()
        if actual is not None:
            return actual
        return int(self._heuristic_ctx() * self._ctx_ratio)

    def _calibrate_ctx(self):
        """刚拿到真实用量时，用"真实/估算"比例校准估算（没有锚点的新会话、压缩后都靠它）。"""
        a = self._ctx_anchor
        if not a or a.get("calibrated") or not self._session:
            return
        a["calibrated"] = True
        if len(getattr(self._session, "history", []) or []) != a.get("hist_len"):
            return
        h = self._heuristic_ctx()
        if h > 0:
            self._ctx_ratio = a["ratio"] = round(min(2.0, max(0.2, float(a["tokens"]) / h)), 3)

    def _heuristic_ctx(self):
        """按字符估算整份请求（系统提示 + 工具 + 历史 + 检索上下文 + 待发图片）的 token 数。"""
        try:
            msgs = self._session.build_messages(self._show_thinking and not self._sub_kind())
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

    def _anchored_ctx(self):
        """有真实用量锚点时：锚点 + 之后新增消息/待发图片的估算；history 被裁剪或清空则返回 None。"""
        a = self._ctx_anchor
        if not a:
            return None
        hist = getattr(self._session, "history", None) or []
        n = int(a.get("hist_len") or 0)
        if n <= 0 or len(hist) < n:
            return None
        extra = list(hist[n:])
        if self._pending_images:
            extra.append({"role": "user", "content": [
                {"type": "text", "text": ""},
                *({"type": "image_url", "image_url": {"url": "data:%s;base64,<pending>" % mt}}
                  for _b64, mt in self._pending_images),
            ]})
        add = 0
        if extra:
            try:
                add = self._session.client._estimate_messages_tokens(extra, None)
            except Exception:
                add = sum(len(json.dumps(m, ensure_ascii=False, default=str)) // 3 + 4 for m in extra)
        return max(0, int(a["tokens"]) + int(add))

    @staticmethod
    def _fmt_k(n):
        return ("%.1fk" % (n / 1000.0)) if n >= 1000 else str(int(n))

    def _refresh_tokens(self):
        limit = self._context_limit()
        s = self._token_stats
        self._ctx_text = "%s / %s" % (self._fmt_k(self._estimate_ctx()), self._fmt_k(limit))
        self._token_text = "%s tokens" % self._fmt_k(s["total"] or (s["input"] + s["output"]))
        self.tokensChanged.emit()

    @staticmethod
    def _short_val(v, n=28):
        s = str(v)
        return s if len(s) <= n else s[:n - 1] + "…"

    def _dec_pending(self):
        if self._pending_ops > 0:
            self._pending_ops -= 1
            self.pendingOpsChanged.emit()


    # ---- Plan mode ----


    # ---- confirm mode + ask_question (blocking UI cards) ----


    # ---- <think> splitter ----


    def _confirm_preview(self, tool_name, kwargs):
        """确认卡片的副标题：尽量给出"生成数量 + 预计 credits 花费"，
        让用户在点同意前清楚知道这次会生成几个、烧多少（尤其 count>1 的批量）。"""
        base = self._arg_preview(kwargs)
        a = kwargs if isinstance(kwargs, dict) else {}
        extra = ""
        if tool_name == "meshy_rig":
            extra = "约 5 credits"
        elif tool_name == "meshy_animate":
            acts = a.get("actions") or []
            n = len(acts) if isinstance(acts, (list, tuple)) else 1
            n = max(1, min(n, 10))
            extra = "%d 个动作 · 约 %d credits" % (n, n * 3)
        elif tool_name in ("meshy_text_to_3d", "meshy_image_to_3d",
                           "meshy_retexture", "meshy_remesh"):
            # 这些是可 count>1 批量的计费工具：把数量显式标出来，避免用户以为只生成 1 个。
            try:
                cnt = max(1, min(int(a.get("count", 1) or 1), 4))
            except (TypeError, ValueError):
                cnt = 1
            extra = ("将生成 %d 个，均消耗 credits" % cnt) if cnt > 1 else "将消耗 credits"
        if extra:
            return (base + " · " + extra) if base else extra
        return base

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
        """Re-parse the accumulated answer into prose/code segments (no flush).

        Segments are reused in place (same dict per position/kind) so the block
        model keeps the same rows and only the segment that actually changed —
        normally the last one — is re-pushed to QML, instead of destroying and
        re-creating every prose/code delegate of the answer on each tick."""
        segs = self._parse_answer(self._prose_text) if self._prose_text.strip() else []
        cache = getattr(self, "_resolve_cache", None)
        if cache is None:
            cache = self._resolve_cache = {}
        for seg in segs:
            if seg.get("kind") == "prose":
                raw = seg["html"]
                hit = cache.get(raw)
                if hit is None:
                    if len(cache) > 256:
                        cache.clear()
                    hit = cache[raw] = self._resolve_bare(raw)
                seg["html"] = hit
        old = self._answer_blocks
        merged = []
        for i, seg in enumerate(segs):
            if i < len(old) and old[i].get("kind") == seg.get("kind"):
                cur = old[i]
                if cur != seg:
                    cur.clear()
                    cur.update(seg)
                merged.append(cur)
            else:
                merged.append(seg)
        prev = set(id(b) for b in old)
        self._blocks = [b for b in self._blocks if id(b) not in prev]
        self._answer_blocks = merged
        self._blocks.extend(merged)

    @Slot(str)
    def _ui_think(self, t):
        self._touch_activity()
        self._think_text += t
        self._ensure_think()
        self._think_block["text"] = self._think_text
        self._think_block["html"] = self._think_html(self._think_text)
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
        self._calibrate_ctx()
        self._refresh_tokens()
        if err:
            self._log_chat("ERROR", err)
            self._plan_revision_mode = False
            if self._pending_plan_confirm:
                self._pending_plan_confirm = False
                self.planConfirmFailed.emit(str(err))
            hint = self._sub_error_hint(err)
            self._blocks.append({"kind": "prose",
                                 "html": "<span style='color:#dd9999'>错误：" + _esc(err) + "</span>"
                                         + ("<br><span style='color:#c9b27c'>" + _esc(hint) + "</span>" if hint else "")})
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
        # 最终渲染重新解析节点链接（流式期间缓存的解析结果可能早于新建节点）
        self._resolve_cache = {}
        self._answer_dirty = True


    # ---- fallback reply (backend not ready) ----
    def _simulate(self, text):
        """无后端时的兜底回复。正常运行不该走到这里——外部 exe 启动即异步构建聊天后端
        （不依赖 Houdini/Bridge），仅在后端仍在初始化/初始化失败或 UI 预览时出现。"""
        def step_done():
            self._blocks.append({"kind": "prose",
                                 "html": "AI 后端尚未就绪，本条消息未处理。请稍等几秒后重试；"
                                         "若重启应用后仍然如此，请反馈日志文件："
                                         "%LOCALAPPDATA%\\HoudiniAgent\\launcher.log"})
            self._do_flush(); self._set_running(False)

        QTimer.singleShot(200, step_done)
