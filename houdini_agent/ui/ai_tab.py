# -*- coding: utf-8 -*-
"""
Houdini Agent - AI Tab
Agent loop, multi-turn tool calling, streaming UI
"""

import json
import os
import threading
import time
import uuid
import queue
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

try:
    from PySide6 import QtWidgets, QtCore, QtGui
    from PySide6.QtCore import QSettings
except ImportError:
    from PySide2 import QtWidgets, QtCore, QtGui
    from PySide2.QtCore import QSettings

from ..utils.ai_client import AIClient, HOUDINI_TOOLS
from ..utils.mcp import HoudiniMCP
from ..utils.token_optimizer import TokenOptimizer, TokenBudget, CompressionStrategy
from ..utils.ultra_optimizer import UltraOptimizer
from .cursor_widgets import (
    CursorTheme,
    UserMessage,
    AIResponse,
    PlanBlock,
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
    ClickableImageLabel
)
import re


class AITab(QtWidgets.QWidget):
    """AI 助手 - 极简侧边栏风格"""
    
    # 信号（用于线程安全的 UI 更新）
    _appendContent = QtCore.Signal(str)
    _addStatus = QtCore.Signal(str)
    _updateThinkingTime = QtCore.Signal()
    _agentDone = QtCore.Signal(dict)
    _agentError = QtCore.Signal(str)
    _agentStopped = QtCore.Signal()
    _updateTodo = QtCore.Signal(str, str, str)  # (todo_id, text, status)
    _addNodeOperation = QtCore.Signal(str, str)  # (name, result_json)
    _addPythonShell = QtCore.Signal(str, str)  # (code, result_json)
    _addSystemShell = QtCore.Signal(str, str)  # (command, result_json)
    _executeToolRequest = QtCore.Signal(str, dict)  # 工具执行请求信号（线程安全）
    _addThinking = QtCore.Signal(str)  # 思考内容更新信号（线程安全）
    _finalizeThinkingSignal = QtCore.Signal()  # 结束思考区块（线程安全）
    _resumeThinkingSignal = QtCore.Signal()    # 恢复思考区块（线程安全）
    
    def __init__(self, parent=None, workspace_dir: Optional[Path] = None):
        super().__init__(parent)
        
        self.client = AIClient()
        self.mcp = HoudiniMCP()
        self.client.set_tool_executor(self._execute_tool_with_todo)
        
        # 状态
        self._conversation_history: List[Dict[str, Any]] = []
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
        
        # 静态内容缓存（只计算一次，节省 token 和计算时间）
        self._cached_optimized_system_prompt: Optional[str] = None
        self._cached_optimized_tools: Optional[List[dict]] = None
        self._cached_optimized_tools_no_web: Optional[List[dict]] = None
        
        # Token 优化器
        self.token_optimizer = TokenOptimizer()
        self._auto_optimize = True  # 自动优化
        self._optimization_strategy = CompressionStrategy.BALANCED
        
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

    def _build_system_prompt(self, with_thinking: bool = True) -> str:
        """构建系统提示
        
        Args:
            with_thinking: 是否包含 <think> 标签思考指令
        """
        base_prompt = """你是 Houdini 助手，擅长通过节点和 VEX 解决问题。
禁止在回复中使用任何 emoji 或图标符号，除非用户明确要求。使用纯文字表达。
"""
        if with_thinking:
            base_prompt += """
输出格式（最高优先级规则，违反即失败）:
你的**每一次**回复（无论第几轮、无论是否调用工具）都**必须**以 <think>...</think> 标签开头，没有任何例外。
即使是简短的确认或状态更新，也必须先写 <think> 标签再写正文。
跳过 <think> 标签等同于格式违规，是不可接受的。

<think> 标签内写出：
1. 对当前情况/上一步执行结果的分析
2. 下一步的推理和行动计划
3. 如果要调用工具，说明为什么调用以及参数选择依据

标签外的内容是展示给用户的正式回复——简洁、直接、以操作为主。

示例（纯文字回复）:
<think>
用户需要散点效果。方案：box -> scatter -> copytopoints。
copytopoints 第0输入=模板几何体，第1输入=目标点。需要一个小球作为复制模板。
</think>
已创建 box->scatter->copytopoints 流程，500 个点，球体半径 0.05。

示例（调用工具前）:
<think>
用户需要创建地形。步骤：1. 创建 grid 作为基础 2. 添加 noise wrangle 3. 验证网络结构。
</think>

示例（工具执行后的后续回复，仍然必须有 think 标签）:
<think>
上一步创建了 grid 节点，返回路径 /obj/geo1/grid1。接下来添加 noise wrangle。
</think>
"""
        else:
            base_prompt += """
输出格式: 简洁、直接、以操作为主。不要输出思考过程。
"""

        base_prompt += """
节点路径输出规范（回复中提及节点时必须遵守）:
-在回复文本中提及任何 Houdini 节点时，**必须**使用完整绝对路径，如 /obj/geo1/box1，而不是只写节点名 box1
-路径格式必须以根类别开头: /obj/..., /out/..., /ch/..., /shop/..., /stage/..., /mat/..., /tasks/...
-正确示例: "已创建节点 /obj/geo1/scatter1 并连接到 /obj/geo1/box1"
-错误示例: "已创建节点 scatter1 并连接到 box1"（缺少完整路径，用户无法点击跳转）
-列举多个节点时，每个都要写完整路径: "/obj/geo1/box1, /obj/geo1/transform1, /obj/geo1/merge1"
-节点路径会自动变为可点击链接，用户点击后可直接跳转到对应节点，所以路径的准确性至关重要

禁止伪造工具调用（最高优先级规则，违反等同失败）:
-你**绝对不能**在回复文本中编写看起来像工具执行结果的内容
-**绝对不能**在回复中出现"[ok] web_search:"、"[ok] fetch_webpage:"、"[工具执行结果]"等文本
-如果你需要搜索信息，**必须真正调用** web_search 工具，通过 function calling 机制发起
-如果你不确定某个信息，**必须调用工具查询**，不能凭记忆编造答案并伪装成搜索结果
-你的回复中只能包含：思考标签、自然语言文本、代码块，不能包含模拟的工具调用格式

工具调用参数规范（最高优先级，每次调用工具前必须检查）:
-调用工具前，**必须**逐一确认所有 required 参数都已填写，缺少必填参数会导致调用失败
-参数值必须使用正确的数据类型（string/number/boolean/array），不要把数字写成字符串，不要把路径遗漏引号
-node_path 参数必须是完整的绝对路径（如 "/obj/geo1/box1"），不能只写节点名（如 "box1"）
-不要凭记忆猜测参数名或参数值，先用查询类工具（get_node_parameters, get_node_inputs, search_node_types）确认
-如果工具调用返回了"缺少参数"或"参数错误"，说明是你的调用参数有误，直接修正参数后重试，不要调用 check_errors
-多次调用同一工具时，每次都要完整填写所有必填参数，不要假设系统会记住上次的参数

安全操作规则:
-首次需要了解网络时调用 get_network_structure 或 list_children，**但不要对已查询过的网络重复调用**（系统会自动缓存同轮内的查询结果）
-设置参数前**必须**先调用 get_node_parameters 查看该节点有哪些参数、参数名是什么、当前值和默认值，绝对不能凭记忆或猜测参数名
-如果需要修改多个参数，先一次性用 get_node_parameters 查完，再逐个 set_node_parameter
-execute_python 中必须检查 None: node=hou.node(path); if node: ...
-创建节点后用返回路径操作，不要猜测路径
-连接节点前确认两端节点都已存在
-**禁止重复查询**：同一 network_path 只需查一次，结果在本轮内保持有效。如果你已经查看过某个网络的结构，直接使用之前的结果即可

创建节点失败时的恢复流程（必须严格执行）:
-如果 create_node 返回错误（如"未识别的节点类型"），**禁止**直接重试或放弃
-**必须**立即调用 search_node_types 搜索正确的节点类型名称
-如果搜索结果不够明确，继续调用 search_local_doc 或 get_houdini_node_doc 查找详细文档
-根据查到的正确类型名重新创建节点
-如果多次搜索仍找不到，用 execute_python 直接查询: hou.nodeType(hou.sopNodeTypeCategory(), 'xxx')

理解现有网络的规则:
-调用 get_network_structure 时，如果返回结果中有标注 [含VEX代码] 或 [含Python代码] 的节点，**必须仔细阅读**其内嵌代码
-通过阅读 wrangle 节点的 VEX 代码可以理解该节点的具体逻辑（如属性计算、条件过滤等），这是理解现有网络实现的关键
-如果需要修改现有 wrangle 节点的代码，先用 get_node_parameters 读取完整的 snippet 参数，再用 set_node_parameter 设置新代码

Wrangle 节点 Run Over 模式（极其重要，每次创建 wrangle 必须考虑）:
-创建 wrangle 节点时，**必须根据 VEX 代码的实际操作对象选择正确的 run_over 模式**，不能一律使用默认的 Points
-run_over 决定了 VEX 代码的执行上下文：Points（逐点执行）、Primitives（逐面执行）、Vertices（逐顶点执行）、Detail（全局执行一次）
-选择错误的 run_over 会导致 VEX 代码完全无法正常工作，或产生错误结果
-判断规则:
  如果代码操作 @P, @N, @pscale, @Cd 等点属性，或用 @ptnum, @numpt → 使用 Points
  如果代码操作 @primnum, @numprim, prim() 函数，或按面片处理 → 使用 Primitives
  如果代码只需执行一次来设置全局属性（如 @Frame, detail()），或用 addpoint/addprim 手动创建几何体 → 使用 Detail
  如果代码操作顶点属性（如 UV）或用 @vtxnum → 使用 Vertices
-常见错误: 用 addpoint()/addprim() 创建几何体时使用 Points 模式会导致每个输入点都执行一次创建，产生大量重复几何体。此类代码**必须**使用 Detail 模式
-如果不确定应该使用哪种模式，优先根据 VEX 代码中访问的属性和函数来判断

任务完成前的强制验证（必须执行，不能跳过）:
1. 调用 verify_and_summarize 进行自动检测（孤立节点、错误节点、连接完整性、显示标志），传入你期望的节点列表和预期效果
2. 如果 verify_and_summarize 报告问题，修复后再次调用，直到通过
3. 注意：不需要在 verify_and_summarize 之前单独调用 get_network_structure —— verify_and_summarize 已内置网络检查
4. check_errors 只用于检查节点 cooking 错误，工具调用失败的错误信息已在返回结果中直接给出，无需调用 check_errors

工具优先级: create_wrangle_node(VEX优先) > create_nodes_batch > create_node
节点输入: 0=主输入, 1=第二输入 | from_path=上游, to_path=下游

系统 Shell 工具（execute_shell）:
-用于执行系统命令（pip、git、dir、ffmpeg、hython、scp、ssh 等），不限于 Houdini Python 环境
-适用场景: 安装 Python 包、查看文件系统、运行外部工具链、检查环境变量、远程文件传输（scp/sftp）
-execute_python 用于 Houdini 场景内操作（hou 模块），execute_shell 用于系统级操作
-命令有超时限制（默认 30 秒，最大 120 秒），危险命令会被拦截
-Shell 命令规范（必须遵守）:
  1.必须生成能立即运行的完整命令，不要用占位符（如 <your_path>）
  2.对于需要用户交互/确认的命令，必须传入非交互式参数（如 pip install --yes, apt -y, echo y |）
  3.优先使用单条命令完成任务；需要多步操作时用 && 连接（Linux）或分号;分隔（PowerShell）
  4.命令输出可能很长，优先使用精确命令减少输出量（如 find -maxdepth 2, dir /b, ls -la 特定路径）
  5.远程操作（ssh/scp/sftp）前提：需要已配置好密钥免密登录，不能依赖交互式密码输入
  6.大文件传输或长时间命令，设置合适的 timeout 参数（最大 120 秒）
  7.路径中有空格时必须用引号包裹；Windows 路径用反斜杠或引号包裹的正斜杠
  8.不要盲目猜测文件路径，先用 dir/ls/find 确认路径存在再操作
  9.安装包时指定版本号（pip install package==version），避免不兼容问题
  10.如果命令失败，先分析 stderr 输出的错误原因，针对性修复后重试，不要盲目重复执行

Skill 系统（几何分析必须优先使用）:
-Skill 是预定义的高级分析脚本，比手写代码更可靠、更高效
-涉及几何体信息（点数、面数、属性、边界盒、连通性等）时，必须优先用 run_skill 而非 execute_python
-常用 skill: analyze_geometry_attribs(属性统计), get_bounding_info(边界盒), analyze_connectivity(连通性), compare_attributes(属性对比), find_dead_nodes(死节点), trace_node_dependencies(依赖追溯), find_attribute_references(属性引用查找), analyze_normals(法线质量检测)
-不确定有哪些 skill 时先调用 list_skills 查看
-示例: run_skill(skill_name="analyze_geometry_attribs", params={"node_path": "/obj/geo1/box1"}) 可列出全部属性
-示例: run_skill(skill_name="get_bounding_info", params={"node_path": "/obj/geo1/box1"}) 获取边界盒
-示例: run_skill(skill_name="analyze_normals", params={"node_path": "/obj/geo1/box1"}) 检测法线质量

联网搜索策略（使用 web_search 前必须遵守）:
-将用户问题转化为精准搜索关键词，不要直接用原始问题当搜索词
-Houdini 相关问题优先加 "SideFX Houdini" 前缀
-中文问题如果首次搜索结果不佳，尝试用英文关键词重搜（最多重搜 2 次）
-搜索结果中如有有用链接，用 fetch_webpage 获取详细内容后再回答
-使用搜索结果中的信息时，**必须**在相关段落末尾标注来源，格式: [来源: 标题](URL)
-不要照搬搜索结果原文，用自己的语言综合整理后回答
-禁止用相同关键词重复搜索（缓存会返回相同结果）

Todo 管理规则（严格遵守）:
-收到复杂任务时，先用 add_todo 创建任务清单，拆分为具体步骤
-每完成一个步骤，**立即**调用 update_todo 将该步骤标记为 done
-每轮工具执行后，检查 Todo 列表，确认哪些已完成、哪些仍待处理
-所有步骤完成后，确保每个 todo 都已标记为 done，再进行最终验证"""
        
        # 使用极致优化器压缩（已缓存）
        return UltraOptimizer.compress_system_prompt(base_prompt)

    def _build_ui(self):
        self.setStyleSheet(f"""
            QWidget {{
                background-color: {CursorTheme.BG_PRIMARY};
                color: {CursorTheme.TEXT_PRIMARY};
                font-family: 'Microsoft YaHei', 'SimSun', 'Segoe UI', sans-serif;
            }}
            QScrollBar:vertical {{
                background: {CursorTheme.BG_SECONDARY};
                width: 12px;
                margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: {CursorTheme.BG_HOVER};
                border-radius: 4px;
                min-height: 30px;
                margin: 2px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {CursorTheme.TEXT_MUTED};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: none;
            }}
        """)
        
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
            'deepseek-chat': 64000, 'deepseek-reasoner': 64000,
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
        
        # Agent / Web 开关
        _chk_style = f"""
            QCheckBox {{ color: {CursorTheme.TEXT_SECONDARY}; font-size: 12px; }}
            QCheckBox::indicator {{ width: 11px; height: 11px; }}
        """
        self.agent_check = QtWidgets.QCheckBox("Agent")
        self.agent_check.setChecked(True)
        self.agent_check.setStyleSheet(_chk_style)
        row1.addWidget(self.agent_check)
        
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
        
        outer.addLayout(row2)
        
        return header

    # ===== 多会话管理 =====
    
    def _build_session_tabs(self) -> QtWidgets.QWidget:
        """会话标签栏 - 支持多个对话窗口"""
        container = QtWidgets.QFrame()
        container.setStyleSheet(f"""
            QFrame {{
                background-color: {CursorTheme.BG_SECONDARY};
                border-bottom: 1px solid {CursorTheme.BORDER};
            }}
        """)
        
        hl = QtWidgets.QHBoxLayout(container)
        hl.setContentsMargins(4, 2, 4, 2)
        hl.setSpacing(0)
        
        self.session_tabs = QtWidgets.QTabBar()
        self.session_tabs.setTabsClosable(False)
        self.session_tabs.setMovable(False)
        self.session_tabs.setExpanding(False)
        self.session_tabs.setDrawBase(False)
        self.session_tabs.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.session_tabs.customContextMenuRequested.connect(self._on_tab_context_menu)
        self.session_tabs.setStyleSheet(f"""
            QTabBar {{
                background: transparent;
            }}
            QTabBar::tab {{
                background: {CursorTheme.BG_TERTIARY};
                color: {CursorTheme.TEXT_MUTED};
                border: 1px solid {CursorTheme.BORDER};
                border-bottom: none;
                padding: 5px 12px;
                margin-right: 2px;
                font-size: 11px;
                font-family: {CursorTheme.FONT_BODY};
                min-width: 60px;
                max-width: 200px;
            }}
            QTabBar::tab:selected {{
                background: {CursorTheme.BG_PRIMARY};
                color: {CursorTheme.TEXT_PRIMARY};
                border-bottom: 2px solid {CursorTheme.ACCENT_BEIGE};
            }}
            QTabBar::tab:hover:!selected {{
                background: {CursorTheme.BG_HOVER};
                color: {CursorTheme.TEXT_SECONDARY};
            }}
        """)
        hl.addWidget(self.session_tabs, 1)
        
        # "+" 新建对话按钮
        self.btn_new_session = QtWidgets.QPushButton("+")
        self.btn_new_session.setFixedSize(22, 22)
        self.btn_new_session.setToolTip("新建对话")
        self.btn_new_session.setStyleSheet(f"""
            QPushButton {{
                background: {CursorTheme.BG_TERTIARY};
                color: {CursorTheme.TEXT_SECONDARY};
                border: 1px solid {CursorTheme.BORDER};
                border-radius: 3px;
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: {CursorTheme.BG_HOVER};
                color: {CursorTheme.TEXT_PRIMARY};
            }}
        """)
        hl.addWidget(self.btn_new_session)
        
        return container
    
    def _on_tab_context_menu(self, pos):
        """Tab 栏右键菜单：关闭 / 关闭其他"""
        tab_index = self.session_tabs.tabAt(pos)
        if tab_index < 0:
            return
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background: {CursorTheme.BG_SECONDARY};
                color: {CursorTheme.TEXT_PRIMARY};
                border: 1px solid {CursorTheme.BORDER};
                font-size: 12px;
                font-family: {CursorTheme.FONT_BODY};
                padding: 4px 0px;
            }}
            QMenu::item {{
                padding: 4px 20px;
            }}
            QMenu::item:selected {{
                background: {CursorTheme.BG_HOVER};
            }}
        """)
        close_action = menu.addAction("关闭此对话")
        close_others = menu.addAction("关闭其他对话")
        if self.session_tabs.count() <= 1:
            close_others.setEnabled(False)

        chosen = menu.exec_(self.session_tabs.mapToGlobal(pos))
        if chosen == close_action:
            self._close_session_tab(tab_index)
        elif chosen == close_others:
            # 从后往前关闭，跳过当前 tab
            for i in range(self.session_tabs.count() - 1, -1, -1):
                if i != tab_index:
                    self._close_session_tab(i)
    
    def _create_session_widgets(self) -> tuple:
        """创建单个会话的 scroll_area / chat_container / chat_layout"""
        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOn)
        scroll_area.setStyleSheet(f"QScrollArea {{ border: none; }}")
        
        chat_container = QtWidgets.QWidget()
        chat_layout = QtWidgets.QVBoxLayout(chat_container)
        chat_layout.setContentsMargins(4, 8, 4, 8)
        chat_layout.setSpacing(0)
        chat_layout.addStretch()
        
        chat_container.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Minimum
        )
        scroll_area.setWidget(chat_container)
        scroll_area.setWidgetResizable(True)
        
        return scroll_area, chat_container, chat_layout
    
    def _create_initial_session(self):
        """创建第一个（默认）会话"""
        self._session_counter = 1
        session_id = self._session_id  # __init__ 已生成
        
        scroll_area, chat_container, chat_layout = self._create_session_widgets()
        self.session_stack.addWidget(scroll_area)
        
        tab_index = self.session_tabs.addTab("Chat 1")
        self.session_tabs.setTabData(tab_index, session_id)
        
        # 设置当前引用
        self.scroll_area = scroll_area
        self.chat_container = chat_container
        self.chat_layout = chat_layout
        
        # 每个会话独立的 TodoList
        todo = self._create_todo_list(chat_container)
        self.todo_list = todo
        
        # 存入 sessions 字典
        self._sessions[session_id] = {
            'scroll_area': scroll_area,
            'chat_container': chat_container,
            'chat_layout': chat_layout,
            'todo_list': todo,
            'conversation_history': self._conversation_history,
            'context_summary': self._context_summary,
            'current_response': self._current_response,
            'token_stats': self._token_stats,
        }
    
    def _create_todo_list(self, parent=None) -> TodoList:
        """为会话创建 TodoList 控件（初始隐藏，首次使用时插入 chat_layout）"""
        return TodoList(parent)
    
    def _ensure_todo_in_chat(self, todo=None, layout=None):
        """确保 todo_list 已在 chat_layout 中（跟随对话流）
        
        Args:
            todo: 要插入的 TodoList，默认使用 self.todo_list
            layout: 目标 chat_layout，默认使用 self.chat_layout
        """
        todo = todo or self.todo_list
        layout = layout or self.chat_layout
        if not todo or not layout:
            return
        # 如果已在 layout 中，不要重复插入
        for i in range(layout.count()):
            if layout.itemAt(i).widget() is todo:
                return
        # 插入到当前最末的消息之后（stretch 之前）
        idx = layout.count() - 1  # -1 跳过末尾 stretch
        layout.insertWidget(idx, todo)
    
    def _new_session(self):
        """新建对话会话"""
        # 保存当前会话状态（如果当前 session 正在被 agent 写入则跳过，避免覆盖）
        if self._agent_session_id != self._session_id:
            self._save_current_session_state()
        
        # 自动保存旧会话缓存
        if self._auto_save_cache and self._conversation_history:
            self._save_cache()
        
        # 创建新会话
        self._session_counter += 1
        new_id = str(uuid.uuid4())[:8]
        label = f"Chat {self._session_counter}"
        
        scroll_area, chat_container, chat_layout = self._create_session_widgets()
        self.session_stack.addWidget(scroll_area)
        
        tab_index = self.session_tabs.addTab(label)
        self.session_tabs.setTabData(tab_index, new_id)
        
        # 初始化新会话状态
        new_token_stats = {
            'input_tokens': 0, 'output_tokens': 0,
            'cache_read': 0, 'cache_write': 0,
            'total_tokens': 0, 'requests': 0,
        }
        
        todo = self._create_todo_list(chat_container)
        
        self._sessions[new_id] = {
            'scroll_area': scroll_area,
            'chat_container': chat_container,
            'chat_layout': chat_layout,
            'todo_list': todo,
            'conversation_history': [],
            'context_summary': '',
            'current_response': None,
            'token_stats': new_token_stats,
        }
        
        # 切换到新会话
        self._session_id = new_id
        self._conversation_history = []
        self._context_summary = ''
        self._current_response = None
        self._token_stats = new_token_stats
        self.scroll_area = scroll_area
        self.chat_container = chat_container
        self.chat_layout = chat_layout
        self.todo_list = todo
        
        # 切换 UI
        self.session_tabs.blockSignals(True)
        self.session_tabs.setCurrentIndex(tab_index)
        self.session_tabs.blockSignals(False)
        self.session_stack.setCurrentWidget(scroll_area)
        
        self._update_context_stats()
    
    def _switch_session(self, tab_index: int):
        """切换到指定标签页的会话（运行中也允许切换）"""
        new_session_id = self.session_tabs.tabData(tab_index)
        if not new_session_id or new_session_id == self._session_id:
            return
        
        # 保存当前会话（如果当前不是 agent 正在写入的 session，正常保存）
        if self._agent_session_id != self._session_id:
            self._save_current_session_state()
        
        # 加载目标会话
        self._load_session_state(new_session_id)
        
        # 切换显示
        sdata = self._sessions[new_session_id]
        self.session_stack.setCurrentWidget(sdata['scroll_area'])
        
        # 更新按钮状态（取决于目标 session 是否就是正在运行的 session）
        self._update_run_buttons()
        self._update_context_stats()
    
    def _close_session_tab(self, tab_index: int):
        """关闭指定标签页"""
        sid = self.session_tabs.tabData(tab_index)
        # 禁止关闭正在运行的 session
        if sid and self._agent_session_id == sid:
            return
        
        session_id = self.session_tabs.tabData(tab_index)
        if not session_id:
            return
        
        # 如果只剩一个标签，不关闭，只清空
        if self.session_tabs.count() <= 1:
            self._on_clear()
            return
        
        # 如果关闭的是当前活动会话，先切到相邻标签
        if session_id == self._session_id:
            new_index = tab_index - 1 if tab_index > 0 else tab_index + 1
            new_sid = self.session_tabs.tabData(new_index)
            if new_sid:
                self._load_session_state(new_sid)
                sdata = self._sessions[new_sid]
                self.session_stack.setCurrentWidget(sdata['scroll_area'])
        
        # 移除标签和会话数据
        self.session_tabs.removeTab(tab_index)
        sdata = self._sessions.pop(session_id, None)
        if sdata and sdata.get('scroll_area'):
            self.session_stack.removeWidget(sdata['scroll_area'])
            sdata['scroll_area'].deleteLater()
        
        self._update_context_stats()
    
    def _save_current_session_state(self):
        """将当前瞬态状态存入 _sessions 字典"""
        if self._session_id not in self._sessions:
            return
        s = self._sessions[self._session_id]
        s['conversation_history'] = self._conversation_history
        s['context_summary'] = self._context_summary
        s['current_response'] = self._current_response
        s['token_stats'] = self._token_stats
    
    def _load_session_state(self, session_id: str):
        """从 _sessions 恢复指定会话的状态"""
        sdata = self._sessions.get(session_id)
        if not sdata:
            return
        
        self._session_id = session_id
        self._conversation_history = sdata.get('conversation_history', [])
        self._context_summary = sdata.get('context_summary', '')
        self._current_response = sdata.get('current_response')
        self._token_stats = sdata.get('token_stats', {
            'input_tokens': 0, 'output_tokens': 0,
            'cache_read': 0, 'cache_write': 0,
            'total_tokens': 0, 'requests': 0,
        })
        self.scroll_area = sdata['scroll_area']
        self.chat_container = sdata['chat_container']
        self.chat_layout = sdata['chat_layout']
        self.todo_list = sdata.get('todo_list') or self._create_todo_list(self.chat_container)
    
    def _auto_rename_tab(self, text: str):
        """根据用户首条消息自动重命名当前标签"""
        for i in range(self.session_tabs.count()):
            if self.session_tabs.tabData(i) == self._session_id:
                current_label = self.session_tabs.tabText(i)
                if current_label.startswith("Chat "):
                    short = text[:18].replace('\n', ' ').strip()
                    if len(text) > 18:
                        short += "..."
                    self.session_tabs.setTabText(i, short)
                break

    def _build_input_area(self) -> QtWidgets.QWidget:
        """输入区域"""
        container = QtWidgets.QFrame()
        container.setStyleSheet(f"""
            QFrame {{
                background-color: {CursorTheme.BG_SECONDARY};
                border-top: 1px solid {CursorTheme.BORDER};
            }}
        """)
        
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)
        
        # 图片附件预览区（输入框上方，默认隐藏）
        self._pending_images = []  # List[Tuple[str, str, QPixmap]]  (base64_data, media_type, thumbnail)
        self.image_preview_container = QtWidgets.QWidget()
        self.image_preview_container.setVisible(False)
        self.image_preview_layout = QtWidgets.QHBoxLayout(self.image_preview_container)
        self.image_preview_layout.setContentsMargins(4, 2, 4, 2)
        self.image_preview_layout.setSpacing(4)
        self.image_preview_layout.addStretch()
        layout.addWidget(self.image_preview_container)
        
        # 输入框（自适应高度）
        self.input_edit = ChatInput()
        self.input_edit.imageDropped.connect(self._on_image_dropped)
        layout.addWidget(self.input_edit)
        
        # 按钮行
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.setSpacing(6)
        
        # 图片附件按钮
        self.btn_attach_image = QtWidgets.QPushButton("Img")
        self.btn_attach_image.setStyleSheet(self._small_btn_style())
        self.btn_attach_image.setToolTip("添加图片附件（支持 PNG/JPG/GIF/WebP，也可直接粘贴/拖拽图片到输入框）")
        btn_layout.addWidget(self.btn_attach_image)
        
        # 快捷操作
        self.btn_network = QtWidgets.QPushButton("Read Network")
        self.btn_network.setStyleSheet(self._small_btn_style())
        btn_layout.addWidget(self.btn_network)
        
        self.btn_selection = QtWidgets.QPushButton("Read Selection")
        self.btn_selection.setStyleSheet(self._small_btn_style())
        btn_layout.addWidget(self.btn_selection)
        
        # 导出训练数据按钮
        self.btn_export_train = QtWidgets.QPushButton("Train")
        self.btn_export_train.setStyleSheet(self._small_btn_style())
        self.btn_export_train.setToolTip("导出当前对话为训练数据（用于大模型微调）")
        btn_layout.addWidget(self.btn_export_train)
        
        btn_layout.addStretch()
        
        # Token 统计按钮（可点击查看详情）
        self.token_stats_btn = QtWidgets.QPushButton("0")
        self.token_stats_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {CursorTheme.TEXT_MUTED};
                border: none;
                font-size: 12px;
                font-family: 'Consolas', 'Monaco', monospace;
                padding: 2px 6px;
            }}
            QPushButton:hover {{
                color: {CursorTheme.TEXT_PRIMARY};
                background: {CursorTheme.BG_HOVER};
                border-radius: 3px;
            }}
        """)
        self.token_stats_btn.setToolTip("点击查看详细 Token 统计")
        self.token_stats_btn.clicked.connect(self._show_token_stats_dialog)
        btn_layout.addWidget(self.token_stats_btn)
        
        # 上下文统计
        self.context_label = QtWidgets.QLabel("0K / 64K")
        self.context_label.setStyleSheet(f"""
            QLabel {{
                color: {CursorTheme.TEXT_MUTED};
                font-size: 12px;
                font-family: 'Consolas', 'Monaco', monospace;
                padding: 0 4px;
            }}
        """)
        btn_layout.addWidget(self.context_label)
        
        # 停止/发送
        self.btn_stop = StopButton()
        self.btn_stop.setVisible(False)
        self.btn_stop.setFixedHeight(32)
        btn_layout.addWidget(self.btn_stop)
        
        self.btn_send = SendButton()
        self.btn_send.setFixedHeight(32)
        btn_layout.addWidget(self.btn_send)
        
        layout.addLayout(btn_layout)
        
        return container

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
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        self.model_combo.currentIndexChanged.connect(self._update_context_stats)
        # 切换提供商或模型或 Think 时自动保存偏好
        self.provider_combo.currentIndexChanged.connect(self._save_model_preference)
        self.model_combo.currentIndexChanged.connect(self._save_model_preference)
        self.think_check.stateChanged.connect(self._save_model_preference)
        self.input_edit.sendRequested.connect(self._on_send)
        
        # 多会话标签
        self.session_tabs.currentChanged.connect(self._switch_session)
        self.btn_new_session.clicked.connect(self._new_session)

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
        self.context_label.setStyleSheet(f"""
            QLabel {{
                color: {color};
                font-size: 15px;
                font-family: 'Consolas', 'Monaco', monospace;
                padding: 0 4px;
            }}
        """)
        
        # 更新优化按钮状态（如果超过阈值，高亮显示）
        if percent >= 80:
            self.btn_optimize.setStyleSheet(self._small_btn_style() + f"""
                QPushButton {{
                    background-color: {CursorTheme.ACCENT_ORANGE};
                    color: white;
                }}
            """)
        else:
            self.btn_optimize.setStyleSheet(self._small_btn_style())

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
        reasoning_line = f"推理 Token: {reasoning:,}\n" if reasoning > 0 else ""
        
        self.token_stats_btn.setToolTip(
            f"累计统计 ({self._token_stats['requests']} 次请求)\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"输入: {self._token_stats['input_tokens']:,}\n"
            f"输出: {self._token_stats['output_tokens']:,}\n"
            f"{reasoning_line}"
            f"Cache 读取: {cache_read:,}\n"
            f"Cache 写入: {cache_write:,}\n"
            f"Cache 命中率: {hit_rate_display}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"总计: {total:,}\n"
            f"预估费用: {cost_display or '$0.00'}\n"
            f"点击查看详情"
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
            self._current_response.add_status("统计已重置")

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
                self.key_status.setStyleSheet(f"color: {CursorTheme.ACCENT_GREEN}; font-size: 10px;")
            else:
                self.key_status.setText("Offline")
                self.key_status.setStyleSheet(f"color: {CursorTheme.ACCENT_RED}; font-size: 10px;")
        elif self.client.has_api_key(provider):
            masked = self.client.get_masked_key(provider)
            self.key_status.setText(masked)
            self.key_status.setStyleSheet(f"color: {CursorTheme.ACCENT_GREEN}; font-size: 10px;")
        else:
            self.key_status.setText("No Key")
            self.key_status.setStyleSheet(f"color: {CursorTheme.ACCENT_ORANGE}; font-size: 10px;")

    def _on_provider_changed(self):
        provider = self._current_provider()
        self._refresh_models(provider)
        self._load_model_preference()  # 切换提供商时也尝试加载上次使用的模型
        self._update_key_status()

    def _add_user_message(self, text: str, images: list = None):
        """添加用户消息（可含图片缩略图，点击可放大）"""
        msg = UserMessage(text, self.chat_container)
        # 如果有图片，在消息下方添加可点击的缩略图
        if images:
            img_row = QtWidgets.QHBoxLayout()
            img_row.setSpacing(4)
            img_row.setContentsMargins(12, 0, 12, 4)
            for b64_data, _mt, thumb in images:
                # 从 base64 还原完整 pixmap 用于放大预览
                full_pixmap = QtGui.QPixmap()
                full_pixmap.loadFromData(__import__('base64').b64decode(b64_data))
                thumb_scaled = thumb.scaled(48, 48, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
                lbl = ClickableImageLabel(thumb_scaled, full_pixmap)
                lbl.setStyleSheet(f"border: 1px solid {CursorTheme.BORDER}; border-radius: 3px; padding: 1px;")
                img_row.addWidget(lbl)
            img_row.addStretch()
            msg.layout().addLayout(img_row)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, msg)
        self._scroll_to_bottom()

    def _add_ai_response(self) -> AIResponse:
        """添加 AI 回复块"""
        response = AIResponse(self.chat_container)
        response.createWrangleRequested.connect(self._on_create_wrangle)
        response.nodePathClicked.connect(self._navigate_to_node)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, response)
        self._current_response = response
        self._scroll_to_bottom(force=True)
        return response

    def _is_user_scrolled_up(self) -> bool:
        """检查用户是否在查看历史（滚动条不在底部）"""
        scrollbar = self.scroll_area.verticalScrollBar()
        # 如果滚动条位置距离底部超过 100 像素，认为用户在查看历史
        return scrollbar.maximum() - scrollbar.value() > 100

    def _scroll_to_bottom(self, force: bool = False):
        """滚动到底部，但尊重用户的查看位置（带节流防止事件循环过载）
        
        Args:
            force: 强制滚动（用于新消息）
        """
        if force or not self._is_user_scrolled_up():
            # 节流：如果已有待执行的滚动定时器，跳过本次
            if not hasattr(self, '_scroll_timer'):
                self._scroll_timer = QtCore.QTimer(self)
                self._scroll_timer.setSingleShot(True)
                self._scroll_timer.setInterval(60)
                self._scroll_timer.timeout.connect(self._do_scroll)
            if not self._scroll_timer.isActive():
                self._scroll_timer.start()
    
    def _do_scroll(self):
        """实际执行滚动"""
        try:
            sb = self.scroll_area.verticalScrollBar()
            sb.setValue(sb.maximum())
        except RuntimeError:
            pass  # 控件可能已销毁
    
    def _scroll_agent_to_bottom(self, force: bool = False):
        """滚动 agent 所在的 session（如果正在显示则滚动，否则跳过）"""
        # 只有当前显示的 session 就是 agent session 时才滚动
        if self._agent_session_id and self._agent_session_id != self._session_id:
            return  # agent 在后台 session 跑，不要干扰用户正在看的 session
        self._scroll_to_bottom(force=force)
    
    def _show_toast(self, text: str, duration_ms: int = 3000):
        """在聊天区域底部显示临时提示，自动消失"""
        toast = StatusLine(text)
        self.chat_layout.addWidget(toast)
        self._scroll_to_bottom(force=True)
        def _remove():
            try:
                toast.setParent(None)
                toast.deleteLater()
            except RuntimeError:
                pass
        QtCore.QTimer.singleShot(duration_ms, _remove)

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
            
            self.client.reset_stop()
            # 启动思考计时器
            self._thinking_timer = QtCore.QTimer(self)
            self._thinking_timer.timeout.connect(lambda: self._updateThinkingTime.emit())
            self._thinking_timer.start(1000)
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
        
        # 按当前显示的 session 更新按钮状态
        self._update_run_buttons()
    
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
        # 过滤纯空白（只含换行/空格的 chunk）
        if not text.strip():
            return
        try:
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
                    if pos > 0:
                        self._addThinking.emit(buf[:pos])
                    buf = buf[pos + 8:]          # 跳过 </think>
                    self._in_think_block = False
                    # 思考结束：立即 finalize 思考区块并停止计时器
                    self._finalize_thinking()
                    continue
                # 检查末尾是否有不完整的 </think>
                hold = self._partial_tag_at_end(buf, '</think>')
                if hold:
                    safe = buf[:-hold]
                    if safe:
                        self._addThinking.emit(safe)
                    self._tag_parse_buf = buf[-hold:]
                    return
                # 全部是思考内容
                self._addThinking.emit(buf)
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

    def _emit_normal_content(self, text: str):
        """发送正式内容（带 token 限制 + 缓冲刷新）"""
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
            self._appendContent.emit("\n\n[输出已达到 token 限制，已停止]")
            self._addStatus.emit("输出达到 token 限制，已停止")
            self.client.request_stop()
            return

        self._output_buffer += text

        # 缓冲刷新策略
        should_flush = False
        current_time = time.time()
        if len(self._output_buffer) >= 15:
            should_flush = True
        if any(c in text for c in ('。', '！', '？', '\n', '.', '!', '?', '：', ':')):
            should_flush = True
        if current_time - self._last_flush_time > 0.1:
            should_flush = True

        if should_flush and self._output_buffer:
            # 实时过滤伪造的工具调用行
            buf = self._output_buffer
            if '[ok]' in buf or '[err]' in buf or '[工具执行结果]' in buf:
                lines = buf.split('\n')
                filtered = []
                has_fake = False
                for ln in lines:
                    s = ln.strip()
                    if s == '[工具执行结果]' or self._FAKE_TOOL_PATTERNS.match(s):
                        has_fake = True
                        continue
                    filtered.append(ln)
                buf = '\n'.join(filtered)
                if has_fake and not getattr(self, '_fake_warned', False):
                    self._addStatus.emit("检测到AI伪造工具调用，已自动过滤")
                    self._fake_warned = True
            if buf.strip():
                self._appendContent.emit(buf)
            self._output_buffer = ""
            self._last_flush_time = current_time

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
                    f"输出接近限制: {self._current_output_tokens}/{self._max_output_tokens} tokens")
        return True

    def _on_thinking_chunk(self, text: str):
        """处理原生 reasoning_content（DeepSeek R1 等模型）"""
        if text:
            self._addThinking.emit(text)
    
    @QtCore.Slot(str)
    def _on_add_thinking(self, text: str):
        """在主线程更新思考内容（槽函数）"""
        try:
            resp = self._agent_response or self._current_response
            if resp:
                resp.add_thinking(text)
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
        except RuntimeError:
            pass  # 控件可能已销毁

    def _on_agent_done(self, result: dict):
        # 使用 agent 锚定的引用（可能已切走 session）
        resp = self._agent_response or self._current_response
        history = self._agent_history if self._agent_history is not None else self._conversation_history
        stats = self._agent_token_stats or self._token_stats
        
        # 刷新标签解析缓冲区残余内容
        if self._tag_parse_buf:
            if self._in_think_block:
                self._addThinking.emit(self._tag_parse_buf)
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
        if new_messages:
            for nm in new_messages:
                clean = nm.copy()
                clean.pop('reasoning_content', None)  # 推理模型专用，不需持久化
                history.append(clean)
        
        # 2. 提取并添加最终 AI 回复
        # 优先使用 final_content（最后一轮的纯文本），其次 full_content
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
        need_final = clean_content or new_messages or (not new_messages and not clean_content)
        if need_final:
            final_msg = {'role': 'assistant', 'content': clean_content or '(任务完成)'}
            if thinking_text:
                final_msg['thinking'] = thinking_text
            # 提取 shell 执行记录，供历史恢复时重建 Shell 折叠面板
            py_shells = []
            sys_shells = []
            for tc in tool_calls_history:
                tn = tc.get('tool_name', '')
                ta = tc.get('arguments', {})
                tr = tc.get('result', {})
                if tn == 'execute_python' and ta.get('code'):
                    py_shells.append({
                        'code': ta['code'],
                        'output': tr.get('result', ''),
                        'error': tr.get('error', ''),
                        'success': bool(tr.get('success')),
                    })
                elif tn == 'execute_shell' and ta.get('command'):
                    sys_shells.append({
                        'command': ta['command'],
                        'output': tr.get('result', ''),
                        'error': tr.get('error', ''),
                        'success': bool(tr.get('success')),
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
        
        # 更新上下文统计
        self._update_context_stats()

    def _on_agent_error(self, error: str):
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
        
        self._set_running(False)

    def _on_agent_stopped(self):
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
        
        self._set_running(False)

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

    # 不需要 Houdini 主线程的工具集合（纯 Python / 系统操作，可在后台线程直接执行）
    _BG_SAFE_TOOLS = frozenset({
        'execute_shell',       # subprocess.run，不依赖 hou
        'search_local_doc',    # 纯 Python 文本检索
        'list_skills',         # 纯 Python 列表
    })

    # 静默工具：不在执行列表 UI 中显示（AI 自行调用，用户无需感知）
    _SILENT_TOOLS = frozenset({
        'add_todo',
        'update_todo',
    })

    def _execute_tool_with_todo(self, tool_name: str, **kwargs) -> dict:
        """执行工具，包含 Todo 相关的工具
        
        注意：此方法在后台线程调用，Houdini 操作必须通过信号调度到主线程执行。
        不依赖 hou 模块的工具（execute_shell 等）直接在后台线程执行，避免阻塞 UI。
        """
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
    
    def _execute_tool_in_bg(self, tool_name: str, kwargs: dict) -> dict:
        """在后台线程直接执行工具（不阻塞 UI 主线程）
        
        仅用于不依赖 hou 模块的工具，如 execute_shell、search_local_doc 等。
        """
        try:
            return self.mcp.execute_tool(tool_name, kwargs)
        except Exception as e:
            import traceback
            return {"success": False, "error": f"后台执行异常: {e}\n{traceback.format_exc()[:300]}"}
    
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
                return {"success": False, "error": "工具执行超时（30秒）"}
    
    @QtCore.Slot(str, dict)
    def _on_execute_tool_main_thread(self, tool_name: str, kwargs: dict):
        """在主线程执行工具（槽函数）
        
        注意：此方法在主线程中执行，直接操作 Houdini API 是安全的。
        所有修改操作包裹在 undo group 中，支持一键撤销整个 Agent 操作。
        """
        result = {"success": False, "error": "未知错误"}
        
        # 判断是否为修改操作（需要 undo group）
        _MUTATING_TOOLS = {
            "create_node", "create_nodes_batch", "create_wrangle_node",
            "delete_node", "set_node_parameter", "connect_nodes",
            "copy_node", "batch_set_parameters", "set_display_flag",
            "execute_python", "save_hip",
        }
        use_undo_group = tool_name in _MUTATING_TOOLS
        
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
                            issues.append(f"错误节点:{node_name}")
                        # 检测孤立节点（非输出节点且未连接）
                        if node_name not in connected_nodes:
                            node_type = node.get('type', '').lower()
                            # 排除输出节点和根节点
                            if not any(x in node_type for x in ['output', 'null', 'out', 'merge']):
                                if not any(x in node_name.lower() for x in ['out', 'output', 'result']):
                                    issues.append(f"孤立节点:{node_name}")
                    
                    # 检查是否有显示的输出节点
                    has_displayed = any(node.get('is_displayed') for node in nodes)
                    if not has_displayed and nodes:
                        issues.append("无显示节点")
                
                # 生成验证结果
                if issues:
                    issues_str = ' | '.join(issues[:5])  # 最多显示5个问题
                    result = {
                        "success": True,
                        "result": f"发现问题需修复: {issues_str}"
                    }
                else:
                    check_items_str = ', '.join(str(item) for item in check_items[:3]) if check_items else "无"
                    result = {
                        "success": True,
                        "result": f"检查通过 | 节点连接正常,无错误 | 期望:{expected[:30] if expected else '完成'}"
                    }
            else:
                # 其他工具交给 MCP 处理
                result = self.mcp.execute_tool(tool_name, kwargs)
        except Exception as e:
            result = {"success": False, "error": f"工具执行异常: {str(e)}"}
        finally:
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
        if '[工具执行结果]' not in content and '[工具结果]' not in content:
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
            tool_text = '\n'.join(tool_lines[:1] + tool_lines[-4:]) + f'\n... 共 {len(tool_lines)-1} 次调用'
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
                # 合并连续的相同角色消息（仅限纯文本消息）
                prev_content = fixed[-1].get('content') or ''
                curr_content = msg.get('content') or ''
                fixed[-1] = fixed[-1].copy()
                fixed[-1]['content'] = prev_content + '\n\n' + curr_content
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
        if text.lstrip().startswith('[工具执行结果]'):
            # 整段就是伪造的工具摘要，移除头部和 [ok]/[err] 行
            lines = text.split('\n')
            real_lines = []
            for line in lines:
                stripped = line.strip()
                if stripped == '[工具执行结果]':
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
            if reason and '警告' in reason:
                self._addStatus.emit(f"注意: {reason}")
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
                        m['content'] = self.client._summarize_tool_content(c, 200) if hasattr(self.client, '_summarize_tool_content') else c[:200] + '...[摘要]'
        
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
                self._addStatus.emit(f"上下文优化: 压缩 tool 结果，节省 {pct:.0f}% ({saved:,} tokens)")
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
            'content': f'[上下文管理] 已裁剪 {n_rounds - len(rounds)} 个早期对话轮次以节省空间。请继续当前任务。'
        }
        
        history.clear()
        history.append(summary_note)
        history.extend([m for rnd in rounds for m in rnd])
        
        saved = old_tokens - self.token_optimizer.calculate_message_tokens(history)
        if saved > 0:
            pct = saved / old_tokens * 100 if old_tokens else 0
            self._addStatus.emit(f"上下文优化: 裁剪旧轮次，节省 {pct:.0f}% ({saved:,} tokens)")
            self._render_conversation_history()
    
    def _compress_context(self):
        """压缩上下文 - 极简摘要，保留关键信息"""
        if len(self._conversation_history) <= 4:
            return  # 太短不需要压缩
        
        # 将旧对话压缩成摘要
        old_messages = self._conversation_history[:-4]  # 保留最近 4 条
        recent_messages = self._conversation_history[-4:]
        
        # 极简摘要：只保留关键信息
        summary_parts = []
        
        # 提取用户请求和完成的任务（极简）
        user_requests = []
        completed_tasks = []
        
        for msg in old_messages:
            role = msg.get('role', '')
            content = msg.get('content', '')
            
            if role == 'user':
                # 提取关键词（前50字符）
                key = content[:50].replace('\n', ' ').strip()
                if key:
                    user_requests.append(key)
            elif role == 'assistant' and content:
                # 提取最后一句（最多30字符）
                lines = [l.strip() for l in content.split('\n') if l.strip()]
                if lines:
                    last_line = lines[-1][:30]
                    if last_line:
                        completed_tasks.append(last_line)
        
        # 极简格式：用户请求 | 完成项
        if user_requests:
            summary_parts.append(f"请求: {', '.join(user_requests[:3])}")  # 最多3个
        if completed_tasks:
            summary_parts.append(f"完成: {', '.join(completed_tasks[:3])}")  # 最多3个
        
        # 生成上下文摘要（极简）
        if summary_parts:
            self._context_summary = " | ".join(summary_parts)
        else:
            self._context_summary = ""
        
        # 更新历史（只保留最近的）
        self._conversation_history = recent_messages
        
        print(f"[Context] 压缩上下文: 保留 {len(recent_messages)} 条消息, 摘要 {len(self._context_summary)} 字符")
    
    def _get_context_reminder(self) -> str:
        """生成上下文提醒（极简，强调复用）"""
        parts = []
        
        # 添加压缩的历史摘要（极简）
        if self._context_summary:
            parts.append(f"[上下文缓存] {self._context_summary}")
        
        # 添加当前 Todo 状态（极简）
        todo_summary = self._get_todo_summary_safe()
        if todo_summary:
            # 只保留未完成的 todo
            if "0/" in todo_summary or "pending" in todo_summary.lower():
                parts.append(f"[待办] {todo_summary.split(':', 1)[-1] if ':' in todo_summary else todo_summary}")
        
        # 提醒复用上下文（极简）
        if len(self._conversation_history) > 2:
            parts.append(f"[已有{len(self._conversation_history)}条消息，优先复用上文信息]")
        
        return " | ".join(parts) if parts else ""

    def _auto_rag_retrieve(self, user_text: str) -> str:
        """自动 RAG: 从用户消息中提取关键词，检索 Houdini 文档并注入上下文

        在后台线程调用，不涉及 Qt 控件。
        返回空字符串表示无相关文档。
        """
        try:
            from ..utils.doc_rag import get_doc_index
            index = get_doc_index()
            return index.auto_retrieve(user_text, max_chars=1200)
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
        hint = f"\n\n[检测到 URL，请使用 fetch_webpage 获取内容：\n{url_list}]"
        
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
        
        # ⚠️ 在主线程中获取所有 Qt 控件的值（后台线程不能直接访问）
        agent_params = {
            'provider': self._current_provider(),
            'model': self.model_combo.currentText(),
            'use_web': self.web_check.isChecked(),
            'use_agent': self.agent_check.isChecked(),
            'use_think': self.think_check.isChecked(),
            'context_limit': self._get_current_context_limit(),  # 也在主线程获取
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
        
        try:
            # ========================================
            # 🔥 Cache 优化：保持消息前缀稳定
            # ========================================
            # 消息结构：[系统提示] + [历史消息] + [上下文提醒+当前请求]
            # 前缀（系统提示+历史消息）保持稳定，提升 cache 命中率
            
            # 1. 系统提示词（根据思考模式选择版本）
            sys_prompt = self._cached_prompt_think if use_think else self._cached_prompt_no_think
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
            
            history_to_send = []
            for msg in self._conversation_history:
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
                            'content': f"[工具结果] {tool_name}: {content[:500]}"
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
                    # 用户消息完整保留
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
                rag_context = self._auto_rag_retrieve(user_last_msg)
                if rag_context:
                    messages.append({'role': 'system', 'content': rag_context})
            
            # 4. 上下文提醒（放在最后，不破坏 cache 前缀）
            # ⚠️ Cache 优化：动态内容放在末尾，保持前缀稳定
            context_reminder = self._get_context_reminder()
            if context_reminder:
                # 将上下文提醒作为系统消息添加到末尾
                messages.append({'role': 'system', 'content': f"[上下文] {context_reminder}"})
            
            # Cursor 风格预发送压缩：只压缩 tool 结果，保留 user/assistant 完整
            if self._auto_optimize:
                current_tokens = self.token_optimizer.calculate_message_tokens(messages)
                should_compress, _ = self.token_optimizer.should_compress(current_tokens, context_limit)
                
                if should_compress:
                    old_tokens = current_tokens
                    # 分离系统提示和上下文提醒
                    first_system = messages[0] if messages and messages[0].get('role') == 'system' else None
                    last_context = messages[-1] if messages and '[上下文]' in messages[-1].get('content', '') else None
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
                                    m['content'] = self.client._summarize_tool_content(c, 200) if hasattr(self.client, '_summarize_tool_content') else c[:200] + '...[摘要]'
                    
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
                            'content': f'[上下文管理] 已裁剪 {n_rounds - len(rounds)} 个早期轮次。'
                        })
                    messages.extend(compressed_body)
                    if last_context:
                        messages.append(last_context)
                    
                    new_tokens = self.token_optimizer.calculate_message_tokens(messages)
                    saved = old_tokens - new_tokens
                    if saved > 0:
                        self._addStatus.emit(f"请求前优化: 节省 {saved:,} tokens (Cursor 风格)")
            
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
            if use_web:
                if self._cached_optimized_tools is None:
                    self._cached_optimized_tools = UltraOptimizer.optimize_tool_definitions(HOUDINI_TOOLS)
                tools = self._cached_optimized_tools
            else:
                if self._cached_optimized_tools_no_web is None:
                    filtered = [t for t in HOUDINI_TOOLS if t['function']['name'] not in ('web_search', 'fetch_webpage')]
                    self._cached_optimized_tools_no_web = UltraOptimizer.optimize_tool_definitions(filtered)
                tools = self._cached_optimized_tools_no_web
            
            if use_agent:
                result = self.client.agent_loop_auto(
                    messages=messages,
                    model=model,
                    provider=provider,
                    max_iterations=999,  # 不限制迭代次数
                    max_tokens=None,  # 不限制输出长度
                    enable_thinking=use_think,
                    on_content=lambda c: self._on_content_with_limit(c),
                    on_thinking=lambda t: self._on_thinking_chunk(t),
                    on_tool_call=lambda n, a: (
                        self._addStatus.emit(f"[tool]{n}") if n not in self._SILENT_TOOLS else None
                    ),
                    on_tool_result=lambda n, a, r: (
                        self._add_tool_result(n, r, a) if n not in self._SILENT_TOOLS else None
                    )
                )
            else:
                # 非 Agent 模式也要限制输出 + 解析 <think> 标签
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
                # 同时设置 ToolCallItem 结果（PySide2 无 Q_ARG，用 QTimer.singleShot 排队到主线程）
                short = f"[ok] Python ({len(code.splitlines())} lines)" if success else f"[err] {result_text[:50]}"
                QtCore.QTimer.singleShot(0, lambda n=name, s=short: self._add_tool_result_ui(n, s))
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
                QtCore.QTimer.singleShot(0, lambda n=name, s=short: self._add_tool_result_ui(n, s))
                return
        
        # 检查是否是节点操作，需要高亮显示
        # 但如果是失败的操作，也要显示错误信息
        if name in ('create_node', 'create_nodes_batch', 'create_wrangle_node', 'delete_node'):
            if result.get('success'):
                # 成功时使用节点操作标签
                self._addNodeOperation.emit(name, json.dumps(result))
                # 同时设置 ToolCallItem 结果（折叠式，可展开查看完整内容）
                ok_text = f"[ok] {result_text}"
                QtCore.QTimer.singleShot(0, lambda n=name, t=ok_text: self._add_tool_result_ui(n, t))
                return
            else:
                # 失败时显示错误信息（继续下面的逻辑）
                pass
        
        # 添加到执行流程（CollapsibleSection 风格，点击展开查看完整结果）
        if self._agent_response or self._current_response:
            prefix = "[err]" if not success else "[ok]"
            line = f"{prefix} {result_text}"
            QtCore.QTimer.singleShot(0, lambda n=name, l=line: self._add_tool_result_ui(n, l))
    
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
    
    @QtCore.Slot(str, str)
    def _on_add_node_operation(self, name: str, result_json: str):
        """处理节点操作高亮显示"""
        try:
            resp = self._agent_response or self._current_response
            if not resp:
                return
            
            try:
                result = json.loads(result_json)
            except Exception:
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
            
            if label:
                label.nodeClicked.connect(self._navigate_to_node)
                # 用 lambda 捕获当前操作的上下文，使撤销精确到这一条操作
                label.undoRequested.connect(
                    lambda _op=op_type, _paths=list(paths), _snap=undo_snapshot:
                        self._undo_node_operation(_op, _paths, _snap)
                )
                resp.details_layout.addWidget(label)
            
            self._scroll_agent_to_bottom()
        except RuntimeError:
            pass  # widget 已被 clear 销毁
    
    def _navigate_to_node(self, node_path: str):
        """点击节点标签时，跳转到该节点并选中"""
        try:
            import hou
            node = hou.node(node_path)
            if node is None:
                self._show_toast(f"节点不存在或已被删除: {node_path}")
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
            self._show_toast("Houdini 环境不可用")
        except Exception as e:
            self._show_toast(f"跳转失败: {e}")
    
    def _undo_node_operation(self, op_type: str = 'create',
                              node_paths: list = None,
                              undo_snapshot: dict = None):
        """精确撤销单次节点操作
        
        - create 操作 → 删除该节点（by path）
        - delete 操作 → 从快照重建该节点
        """
        try:
            import hou
        except ImportError:
            self._show_toast("Houdini 环境不可用")
            return
        
        try:
            if op_type == 'create':
                # ---- 撤销创建 = 删除节点 ----
                if not node_paths:
                    self._show_toast("缺少节点路径，无法撤销")
                    return
                deleted = 0
                for p in node_paths:
                    node = hou.node(p)
                    if node is not None:
                        node.destroy()
                        deleted += 1
                if deleted:
                    self._show_toast(f"已撤销创建（删除 {deleted} 个节点）")
                else:
                    self._show_toast("节点已不存在，无需撤销")
            
            elif op_type == 'delete' and undo_snapshot:
                # ---- 撤销删除 = 从快照重建 ----
                parent_path = undo_snapshot.get("parent_path", "")
                node_type = undo_snapshot.get("node_type", "")
                node_name = undo_snapshot.get("node_name", "")
                
                parent = hou.node(parent_path)
                if parent is None:
                    self._show_toast(f"父节点不存在: {parent_path}")
                    return
                
                # 创建节点
                new_node = parent.createNode(node_type, node_name)
                
                # 恢复位置
                pos = undo_snapshot.get("position")
                if pos and len(pos) == 2:
                    new_node.setPosition(hou.Vector2(pos[0], pos[1]))
                
                # 恢复参数
                params = undo_snapshot.get("params", {})
                for parm_name, val in params.items():
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
                
                # 恢复输入连接
                for conn in undo_snapshot.get("input_connections", []):
                    try:
                        src = hou.node(conn["source_path"])
                        if src:
                            new_node.setInput(conn["input_index"], src)
                    except Exception:
                        continue
                
                # 恢复输出连接
                for conn in undo_snapshot.get("output_connections", []):
                    try:
                        dest = hou.node(conn["dest_path"])
                        if dest:
                            dest.setInput(conn["dest_input_index"], new_node, conn.get("output_index", 0))
                    except Exception:
                        continue
                
                # 恢复标志
                try:
                    if undo_snapshot.get("display_flag") and hasattr(new_node, 'setDisplayFlag'):
                        new_node.setDisplayFlag(True)
                    if undo_snapshot.get("render_flag") and hasattr(new_node, 'setRenderFlag'):
                        new_node.setRenderFlag(True)
                except Exception:
                    pass
                
                self._show_toast(f"已恢复节点: {new_node.path()}")
            
            else:
                # 回退：使用 Houdini 原生 undo
                hou.undos.performUndo()
                self._show_toast("已撤销")
            
            self._refresh_node_context()
        
        except Exception as e:
            self._show_toast(f"撤销失败: {e}")

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
        """从文件路径加载图片并添加到待发送列表"""
        import base64
        try:
            with open(file_path, 'rb') as f:
                img_data = f.read()
            # 检测 MIME 类型
            ext = os.path.splitext(file_path)[1].lower()
            mime_map = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                        '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp'}
            media_type = mime_map.get(ext, 'image/png')
            b64 = base64.b64encode(img_data).decode('utf-8')
            self._add_pending_image(b64, media_type)
        except Exception as e:
            print(f"[AI Tab] 加载图片失败: {e}")
    
    def _on_image_dropped(self, image: 'QtGui.QImage'):
        """ChatInput 拖拽或粘贴图片的回调"""
        if not self._current_model_supports_vision():
            return
        import base64
        buf = QtCore.QBuffer()
        buf.open(QtCore.QIODevice.WriteOnly)
        image.save(buf, "PNG")
        b64 = base64.b64encode(buf.data().data()).decode('utf-8')
        buf.close()
        self._add_pending_image(b64, 'image/png')
    
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
        lbl.setStyleSheet(f"border: 1px solid {CursorTheme.BORDER}; border-radius: 3px;")
        img_layout.addWidget(lbl)
        
        # 删除按钮
        rm_btn = QtWidgets.QPushButton("x")
        rm_btn.setFixedSize(16, 16)
        rm_btn.setStyleSheet(f"""
            QPushButton {{ background: {CursorTheme.ACCENT_RED}; color: white; 
                          border: none; border-radius: 8px; font-size: 10px; }}
            QPushButton:hover {{ background: #ff6b6b; }}
        """)
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
            lbl.setStyleSheet(f"border: 1px solid {CursorTheme.BORDER}; border-radius: 3px;")
            img_layout.addWidget(lbl)
            
            rm_btn = QtWidgets.QPushButton("x")
            rm_btn.setFixedSize(16, 16)
            rm_btn.setStyleSheet(f"""
                QPushButton {{ background: {CursorTheme.ACCENT_RED}; color: white; 
                              border: none; border-radius: 8px; font-size: 10px; }}
                QPushButton:hover {{ background: #ff6b6b; }}
            """)
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
        content_parts = []
        # 先添加文字
        if text:
            content_parts.append({"type": "text", "text": text})
        # 添加图片
        for b64_data, media_type, _thumb in images:
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
        """Python 退出时的最后保存机会（atexit 回调）"""
        try:
            if not hasattr(self, '_sessions') or not self._sessions:
                return
            self._save_current_session_state()
            # 直接写文件，不依赖 Qt 事件循环
            manifest_tabs = []
            for i in range(self.session_tabs.count()):
                sid = self.session_tabs.tabData(i)
                tab_label = self.session_tabs.tabText(i)
                if not sid or sid not in self._sessions:
                    continue
                sdata = self._sessions[sid]
                history = sdata.get('conversation_history', [])
                if not history:
                    continue
                # 收集 todo 数据
                todo_list_obj = sdata.get('todo_list')
                todo_data = []
                try:
                    todo_data = todo_list_obj.get_todos_data() if todo_list_obj else []
                except Exception:
                    pass
                cache_data = {
                    'version': '1.0',
                    'session_id': sid,
                    'message_count': len(history),
                    'conversation_history': history,
                    'context_summary': sdata.get('context_summary', ''),
                    'todo_data': todo_data,
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
        """自动保存：覆写同 session 文件 + manifest + cache_latest.json"""
        if not self._conversation_history:
            return False
        try:
            # 同步当前会话状态到 _sessions
            self._save_current_session_state()
            
            cache_data = self._build_cache_data()

            # 1. 覆写固定的 session 文件（一个 session 只有一个文件）
            session_file = self._cache_dir / f"session_{self._session_id}.json"
            with open(session_file, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)

            # 2. 覆写 cache_latest.json（用于启动时自动恢复）
            latest_file = self._cache_dir / "cache_latest.json"
            with open(latest_file, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)

            # 3. 同步更新 sessions_manifest.json（确保所有 tab 信息都是最新的）
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
                    continue  # 空会话不保存

                # 收集 todo 数据
                todo_list_obj = sdata.get('todo_list')
                todo_data = todo_list_obj.get_todos_data() if todo_list_obj else []

                # 写 session 文件
                cache_data = {
                    'version': '1.0',
                    'session_id': sid,
                    'created_at': datetime.now().isoformat(),
                    'message_count': len(history),
                    'conversation_history': history,
                    'context_summary': sdata.get('context_summary', ''),
                    'todo_data': todo_data,
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

            # 同时维护 cache_latest.json 兼容性
            if self._conversation_history:
                cache_data = self._build_cache_data()
                latest_file = self._cache_dir / "cache_latest.json"
                with open(latest_file, 'w', encoding='utf-8') as f:
                    json.dump(cache_data, f, ensure_ascii=False, indent=2)

            print(f"[Cache] 已保存 {len(manifest_tabs)} 个会话到磁盘")
            return True
        except Exception as e:
            print(f"[Cache] 保存所有会话失败: {e}")
            return False

    def _restore_all_sessions(self) -> bool:
        """从 sessions_manifest.json 恢复所有会话标签（启动时调用）"""
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

                if first_tab:
                    # 第一个 tab：加载到已有的初始会话中
                    first_tab = False
                    old_id = self._session_id

                    self._session_id = sid
                    self._conversation_history = history
                    self._context_summary = context_summary

                    # 更新 sessions 字典
                    if old_id in self._sessions:
                        sdata = self._sessions.pop(old_id)
                        sdata['conversation_history'] = history
                        sdata['context_summary'] = context_summary
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
                            'token_stats': self._token_stats,
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

                    new_token_stats = {
                        'input_tokens': 0, 'output_tokens': 0,
                        'cache_read': 0, 'cache_write': 0,
                        'total_tokens': 0, 'requests': 0,
                    }

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
                        'token_stats': new_token_stats,
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
                    self._token_stats = new_token_stats
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

            self._update_context_stats()
            print(f"[Cache] 已恢复 {self.session_tabs.count()} 个会话标签")
            return True

        except Exception as e:
            print(f"[Cache] 恢复多会话失败: {e}")
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
            
            if silent and not self._conversation_history:
                # 静默恢复：当前会话为空时直接加载到当前标签
                self._conversation_history = history
                self._context_summary = context_summary
                self._session_id = cached_session_id
                # 恢复 todo 数据
                if todo_data and hasattr(self, 'todo_list') and self.todo_list:
                    self.todo_list.restore_todos(todo_data)
                    self._ensure_todo_in_chat(self.todo_list, self.chat_layout)
                # 更新 sessions 字典
                if self._session_id in self._sessions:
                    self._sessions[self._session_id]['conversation_history'] = self._conversation_history
                    self._sessions[self._session_id]['context_summary'] = self._context_summary
                elif self._sessions:
                    # 旧 session_id 已经变了，需要重新映射
                    old_id = list(self._sessions.keys())[0]
                    sdata = self._sessions.pop(old_id)
                    sdata['conversation_history'] = self._conversation_history
                    sdata['context_summary'] = self._context_summary
                    self._sessions[self._session_id] = sdata
                    # 更新标签数据
                    for i in range(self.session_tabs.count()):
                        if self.session_tabs.tabData(i) == old_id:
                            self.session_tabs.setTabData(i, self._session_id)
                            break
                self._render_conversation_history()
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
            
            new_token_stats = {
                'input_tokens': 0, 'output_tokens': 0,
                'cache_read': 0, 'cache_write': 0,
                'total_tokens': 0, 'requests': 0,
            }
            
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
                'token_stats': new_token_stats,
            }
            
            # 切换到新标签
            self._session_id = cached_session_id
            self._conversation_history = history
            self._context_summary = context_summary
            self._current_response = None
            self._token_stats = new_token_stats
            self.scroll_area = scroll_area
            self.chat_container = chat_container
            self.chat_layout = chat_layout
            self.todo_list = todo
            
            self.session_tabs.blockSignals(True)
            self.session_tabs.setCurrentIndex(tab_index)
            self.session_tabs.blockSignals(False)
            self.session_stack.setCurrentWidget(scroll_area)
            
            self._render_conversation_history()
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

    def _render_conversation_history(self):
        """重新渲染对话历史到 UI

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

        messages = self._conversation_history
        i = 0
        while i < len(messages):
            msg = messages[i]
            role = msg.get('role', '')
            raw_content = msg.get('content', '') or ''
            # 多模态消息的 content 可能是 list（含 text/image 部分），统一提取文本
            if isinstance(raw_content, list):
                content = '\n'.join(
                    part.get('text', '') for part in raw_content
                    if isinstance(part, dict) and part.get('type') == 'text'
                )
            else:
                content = raw_content

            # ─── 用户消息 ───
            if role == 'user':
                self._render_user_history(content)
                i += 1

            # ─── 助手消息 ───
            elif role == 'assistant':
                # ★ 新格式检测：assistant 带 tool_calls → Cursor 风格原生格式 ★
                if msg.get('tool_calls'):
                    # 收集整个工具交互轮次
                    # 模式：assistant(tc) → tool → [assistant(tc) → tool →] ... → assistant(reply)
                    turn_msgs = [msg]
                    j = i + 1
                    while j < len(messages):
                        m = messages[j]
                        r = m.get('role', '')
                        if r == 'tool':
                            turn_msgs.append(m)
                            j += 1
                        elif r == 'assistant':
                            turn_msgs.append(m)
                            j += 1
                            if not m.get('tool_calls'):
                                break  # 找到最终回复
                        else:
                            break
                    self._render_native_tool_turn(turn_msgs)
                    i = j
                    continue

                # ─── 旧格式处理（向后兼容） ───
                tool_msgs = []
                j = i + 1
                while j < len(messages) and messages[j].get('role') == 'tool':
                    tool_msgs.append(messages[j])
                    j += 1

                if content.lstrip().startswith('[工具执行结果]'):
                    self._render_tool_summary_history(content, msg)
                else:
                    response = self._add_ai_response()
                    thinking = msg.get('thinking', '')
                    if thinking:
                        response.add_thinking(thinking)
                        response.thinking_section.finalize()
                        if not response.thinking_section._collapsed:
                            response.thinking_section.toggle()
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

                i = j

            # ─── 摘要 ───
            elif role == 'system' and '[历史对话摘要' in content:
                response = self._add_ai_response()
                response.add_collapsible("历史对话摘要", content)
                response.status_label.setText("历史摘要")
                response.finalize()
                response.status_label.setText("历史摘要")
                i += 1
            else:
                i += 1

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
            if not response.thinking_section._collapsed:
                response.thinking_section.toggle()
        
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
            if not response.thinking_section._collapsed:
                response.thinking_section.toggle()

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
    
