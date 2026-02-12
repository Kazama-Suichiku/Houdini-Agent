# Houdini Agent

**[English](README.md)** | **[中文](README_CN.md)**

基于 AI 的 SideFX Houdini 智能助手，支持自主多轮工具调用、联网搜索、VEX/Python 代码执行，配备极简深色 UI。

基于 **OpenAI Function Calling** 协议，Agent 可以读取节点网络、创建/修改/连接节点、编写 VEX Wrangle、执行系统命令、联网搜索、查询本地文档 —— 全部在自主循环中迭代完成。

## 核心特性

### Agent 循环

AI 以自主 **Agent 循环** 运行：接收用户请求 → 规划步骤 → 调用工具 → 检查结果 → 继续调用 → 直到任务完成。提供两种模式：

- **Agent 模式** — 完整权限，AI 可以创建、修改、连接、删除节点，设置参数，执行脚本，保存场景。
- **Ask 模式** — 只读模式，AI 只能查询场景结构、检查参数、搜索文档和提供分析。所有修改类工具被白名单守卫拦截。

```
用户请求 → AI 规划 → 调用工具 → 检查结果 → 调用更多工具 → … → 最终回复
```

- **多轮工具调用** — AI 自主决定调用哪些工具、以什么顺序执行
- **Todo 任务系统** — 复杂任务自动拆分为子任务，实时跟踪状态
- **流式输出** — 实时显示思考过程和回复内容
- **深度思考** — 原生支持推理模型（DeepSeek-R1、GLM-4.7、Claude `<think>` 标签）
- **随时中断** — 可在任意时刻停止正在运行的 Agent 循环
- **智能上下文管理** — 按轮次裁剪对话，永不截断用户/助手消息，仅压缩工具结果

### 支持的 AI 提供商

| 提供商 | 模型 | 说明 |
|--------|------|------|
| **DeepSeek** | `deepseek-chat`、`deepseek-reasoner` (R1) | 性价比高，响应快，支持 Function Calling 和推理 |
| **智谱 GLM** | `glm-4.7` | 国内访问稳定，原生推理与工具调用 |
| **OpenAI** | `gpt-5.2`、`gpt-5.3-codex` | 能力强大，完整 Function Calling 与 Vision 支持 |
| **Ollama**（本地） | `qwen2.5:14b`、任意本地模型 | 隐私优先，自动检测可用模型 |
| **拼好饭**（中转） | `claude-sonnet-4-5`、`claude-opus-4-6-kiro`、`gemini-3-pro-image-preview` 等 | 通过中转接口访问 Claude 和 Gemini 模型 |

### 图片/多模态输入

- **多模态消息** — 为支持视觉的模型附加图片（PNG/JPG/GIF/WebP）
- **粘贴与拖拽** — `Ctrl+V` 从剪贴板粘贴图片，或直接拖拽图片文件到输入框
- **文件选择器** — 点击「Img」按钮从磁盘选择图片
- **图片预览** — 发送前在输入框上方显示缩略图，支持单独移除；**点击缩略图可放大查看**（弹出全尺寸预览窗口）
- **模型感知** — 自动检测当前模型是否支持视觉；不支持的模型会给出明确提示
- 已支持：OpenAI GPT-5.2/5.3、Claude（所有变体）、Gemini

### 深色 UI

- 极简深色主题
- 思考过程、工具调用、执行结果均可折叠/展开
- 内置 **Python Shell** 和 **系统 Shell** 组件，支持语法高亮
- **可点击节点路径** — 回复中的 `/obj/geo1/box1` 等路径自动变为链接，点击即可跳转到对应节点
- **节点上下文栏**：实时显示当前选中的 Houdini 节点
- **Todo 列表**：显示在对话区域上方，带实时状态图标
- **Token 分析** — 实时显示 Token 用量、推理 Token、Cache 命中率和按模型计费的费用估算（点击查看详细分析面板）
- 多会话标签页 — 同时运行多个独立对话
- AI 回复一键复制
- `Ctrl+Enter` 发送消息

## 可用工具（35+）

### 节点操作

| 工具 | 说明 |
|------|------|
| `create_wrangle_node` | **优先使用** — 创建 Wrangle 节点并设置 VEX 代码（point/prim/vertex/volume/detail） |
| `create_node` | 创建单个节点 |
| `create_nodes_batch` | 批量创建节点并自动连接 |
| `connect_nodes` | 连接两个节点（支持指定输入端口） |
| `delete_node` | 删除节点 |
| `copy_node` | 复制/克隆节点到同一或其他网络 |
| `set_node_parameter` | 设置单个参数值（智能纠错、内联红绿 Diff 预览、一键撤销） |
| `batch_set_parameters` | 批量设置多个节点的同一参数 |
| `set_display_flag` | 设置节点的显示/渲染标志 |
| `save_hip` | 保存当前 HIP 文件 |
| `undo_redo` | 撤销或重做 |

### 查询与检查

| 工具 | 说明 |
|------|------|
| `get_network_structure` | 获取节点网络拓扑 — **NetworkBox 感知**：有分组时自动折叠为概览（名称+注释+节点数），传 `box_name` 可钻入查看详情；大型网络显著节省 Token |
| `get_node_parameters` | 获取节点参数 + 状态标志、错误信息、输入输出连接（已合并原 `get_node_details`） |
| `list_children` | 列出子节点及标志（类似 `ls`） |
| `read_selection` | 读取视口中当前选中的节点 |
| `search_node_types` | 按关键词搜索 Houdini 节点类型 |
| `semantic_search_nodes` | 自然语言搜索节点类型（如"在表面上随机分布点"） |
| `find_nodes_by_param` | 按参数值搜索节点（类似 `grep`） |
| `get_node_inputs` | 获取节点输入端口信息（210+ 常用节点已预缓存） |
| `check_errors` | 检查 Houdini 节点 cooking 错误和警告 |
| `verify_and_summarize` | 验证网络完整性并生成总结报告（已内置 `get_network_structure`，无需提前单独调用） |

### 代码执行

| 工具 | 说明 |
|------|------|
| `execute_python` | 在 Houdini Python Shell 中运行代码（可使用 `hou` 模块） |
| `execute_shell` | 执行系统命令（pip、git、ssh、scp、ffmpeg 等），带超时和安全检查 |

### 联网与文档

| 工具 | 说明 |
|------|------|
| `web_search` | 联网搜索（Brave/DuckDuckGo 自动降级，带缓存） |
| `fetch_webpage` | 获取网页正文内容（分页、编码自适应） |
| `search_local_doc` | 搜索本地 Houdini 文档索引（节点/VEX 函数/HOM 类） |
| `get_houdini_node_doc` | 获取节点帮助文档（本地帮助服务器 → SideFX 在线文档 → 节点类型信息） |

### Skill 脚本

| 工具 | 说明 |
|------|------|
| `run_skill` | 执行预定义 Skill 脚本 |
| `list_skills` | 列出所有可用 Skill |

### NetworkBox（节点分组）

| 工具 | 说明 |
|------|------|
| `create_network_box` | 创建 NetworkBox（分组框），支持语义颜色预设（input/processing/deform/output/simulation/utility），可在创建时直接包含指定节点 |
| `add_nodes_to_box` | 将节点添加到已有的 NetworkBox，支持自动调整大小 |
| `list_network_boxes` | 列出网络中所有 NetworkBox 及其内容和元数据 |

### 节点布局

| 工具 | 说明 |
|------|------|
| `layout_nodes` | 自动布局节点 — 支持 `auto`（智能）、`grid`（网格）、`columns`（按拓扑深度分列）策略，可调间距 |
| `get_node_positions` | 获取节点位置信息（x/y 坐标和类型），用于检查布局效果或手动微调 |

### 性能分析

| 工具 | 说明 |
|------|------|
| `perf_start_profile` | 启动 Houdini perfMon 性能分析 — 可选强制 cook 指定节点以触发完整 cook 链 |
| `perf_stop_and_report` | 停止性能分析并返回详细的 cook 时间 / 内存报告（分页） |

### 任务管理

| 工具 | 说明 |
|------|------|
| `add_todo` | 添加任务到 Todo 列表 |
| `update_todo` | 更新任务状态（pending / in_progress / done / error） |

## Skill 系统

Skill 是预优化的 Python 脚本，在 Houdini 环境中运行，用于可靠的几何体分析。涉及几何分析时优先使用 Skill，而非手写 `execute_python`。

| Skill | 说明 |
|-------|------|
| `analyze_geometry_attribs` | 属性统计（min/max/mean/std/NaN/Inf），支持 point/vertex/prim/detail |
| `analyze_normals` | 法线质量检测 — NaN、零向量、未归一化、翻转面 |
| `get_bounding_info` | 边界盒信息：中心、尺寸、对角线、体积、表面积、长宽比 |
| `analyze_connectivity` | 连通性分析（独立部分数量、每部分的点数/面数） |
| `compare_attributes` | 两个节点的属性差异对比（新增/缺失/类型变化） |
| `find_dead_nodes` | 查找孤立节点和未使用的链末端节点 |
| `trace_node_dependencies` | 追溯上游依赖树或下游影响范围 |
| `find_attribute_references` | 查找网络中所有引用指定属性的节点（VEX 代码、表达式、字符串参数） |
| `analyze_cook_performance` | **新增** — 全网络 cook 时间排名、几何体膨胀点检测、错误/警告节点、瓶颈识别 |

## 项目结构

```
Houdini-Agent/
├── launcher.py                      # 启动器（自动检测 Houdini）
├── README.md                        # 英文文档
├── README_CN.md                     # 中文文档
├── lib/                             # 内置依赖库（requests、urllib3、certifi、tiktoken 等）
├── config/                          # 运行时配置（自动创建，已 gitignore）
│   └── houdini_ai.ini              # API Key 及设置
├── cache/                           # 对话缓存、文档索引、HIP 预览
├── Doc/                             # 离线文档
│   ├── houdini_knowledge_base.txt  # Houdini 编程知识库
│   ├── vex_attributes_reference.txt
│   ├── vex_snippets_reference.txt
│   ├── labs_knowledge_base.txt     # SideFX Labs 节点知识库
│   ├── heightfields_knowledge_base.txt  # HeightField / 地形知识库
│   ├── copernicus_knowledge_base.txt    # Copernicus (COP) 知识库
│   ├── ml_knowledge_base.txt       # 机器学习知识库
│   ├── mpm_knowledge_base.txt      # MPM 求解器知识库
│   ├── copernicus/                  # Copernicus 原始文档
│   ├── heightfields/                # HeightField 原始文档
│   ├── ml/                          # ML 原始文档
│   ├── mpm/                         # MPM 原始文档
│   ├── nodes.zip                   # 节点文档索引（wiki 标记格式）
│   ├── vex.zip                     # VEX 函数文档索引
│   └── hom.zip                     # HOM 类/方法文档索引
├── shared/                          # 共享工具
│   └── common_utils.py             # 路径与配置工具
├── trainData/                       # 导出的训练数据（JSONL）
└── houdini_agent/                   # 主模块
    ├── main.py                     # 模块入口与窗口管理
    ├── shelf_tool.py               # Houdini 工具架集成
    ├── qt_compat.py                # PySide2/PySide6 兼容层
    ├── QUICK_SHELF_CODE.py         # 快速工具架代码片段
    ├── core/
    │   ├── main_window.py          # 主窗口（工作区保存/恢复）
    │   ├── agent_runner.py         # AgentRunnerMixin — Agent 循环辅助、确认模式、工具调度
    │   └── session_manager.py      # SessionManagerMixin — 多会话创建/切换/关闭
    ├── ui/
    │   ├── ai_tab.py              # AI Agent 标签页（Mixin 宿主、Agent 循环、上下文管理、流式 UI）
    │   ├── cursor_widgets.py      # UI 组件（主题、对话块、Todo、Shell、Token 分析面板）
    │   ├── header.py              # HeaderMixin — 顶部设置栏（提供商、模型、功能开关）
    │   ├── input_area.py          # InputAreaMixin — 输入区域、模式切换、@提及、确认模式
    │   └── chat_view.py           # ChatViewMixin — 对话显示、滚动控制、Toast 消息
    ├── skills/                     # 预构建分析脚本
    │   ├── __init__.py            # Skill 注册表与加载器
    │   ├── analyze_normals.py     # 法线质量检测
    │   ├── analyze_point_attrib.py # 几何属性统计
    │   ├── bounding_box_info.py   # 边界盒信息
    │   ├── compare_attributes.py  # 节点属性对比
    │   ├── connectivity_analysis.py # 连通性分析
    │   ├── find_attrib_references.py # 属性引用搜索
    │   ├── find_dead_nodes.py     # 死节点/孤立节点查找
    │   ├── trace_dependencies.py  # 依赖树追溯
    │   └── analyze_cook_performance.py # Cook 时间排名与瓶颈检测
    └── utils/
        ├── ai_client.py           # AI API 客户端（流式传输、Function Calling、联网搜索）
        ├── doc_rag.py             # 本地文档索引（节点/VEX/HOM O(1) 查找）
        ├── token_optimizer.py     # Token 预算与压缩策略（tiktoken 精准计数）
        ├── ultra_optimizer.py     # 系统提示词与工具定义优化器
        ├── training_data_exporter.py # 对话导出为训练数据 JSONL
        └── mcp/                   # Houdini MCP 层
            ├── client.py          # 工具执行器（节点操作、Shell、Skill 分发）
            ├── hou_core.py        # 底层 hou 模块封装
            ├── node_inputs.json   # 预缓存的输入端口信息（210+ 节点）
            ├── server.py          # MCP 服务端（预留）
            ├── settings.py        # MCP 设置
            └── logger.py          # 日志
```

## 快速开始

### 环境要求

- **Houdini 20.5+**（或 21+）
- **Python 3.9+**（Houdini 自带）
- **PySide2 或 PySide6**（Houdini 自带 — Houdini ≤20.5 为 PySide2，Houdini 21+ 为 PySide6）
- **Windows**（主要），Linux/macOS 理论上可支持

### 安装

无需 pip install — 所有依赖已内置在 `lib/` 目录中。

1. Clone 或下载本仓库
2. 放置到 Houdini 可访问的任意位置

### 在 Houdini 中启动

```python
import sys
sys.path.insert(0, r"C:\path\to\Houdini-Agent")
import launcher
launcher.show_tool()
```

也可以将此代码添加到 **Shelf Tool**（工具架按钮），实现一键启动。

### 配置 API Key

**方式一：环境变量（推荐）**

```powershell
# DeepSeek
[Environment]::SetEnvironmentVariable('DEEPSEEK_API_KEY', 'sk-xxx', 'User')

# 智谱 GLM
[Environment]::SetEnvironmentVariable('GLM_API_KEY', 'xxx.xxx', 'User')

# OpenAI
[Environment]::SetEnvironmentVariable('OPENAI_API_KEY', 'sk-xxx', 'User')

# 拼好饭（中转）
[Environment]::SetEnvironmentVariable('DUOJIE_API_KEY', 'xxx', 'User')
```

**方式二：工具内设置**

点击「设置 API Key…」按钮，勾选「保存到本机配置」。

## 架构

### Agent 循环流程

```
┌───────────────────────────────────────────────────────┐
│  用户发送消息                                           │
│  ↓                                                     │
│  系统提示词 + 对话历史 + RAG 文档                        │
│  ↓                                                     │
│  AI 模型（流式）→ 思考过程 + tool_calls                  │
│  ↓                                                     │
│  工具执行器分发每个工具调用：                              │
│    - Houdini 工具 → 主线程（Qt BlockingQueued）          │
│    - Shell / 联网 / 文档 → 后台线程（非阻塞）             │
│  ↓                                                     │
│  工具结果 → 以 tool 消息反馈给 AI                        │
│  ↓                                                     │
│  AI 继续（可能调用更多工具或生成最终回复）                  │
│  ↓                                                     │
│  循环直到 AI 完成或达到最大迭代次数                        │
└───────────────────────────────────────────────────────┘
```

### Mixin 架构

`AITab` 是核心组件，由五个聚焦的 Mixin 组合而成：

| Mixin | 模块 | 职责 |
|-------|------|------|
| `HeaderMixin` | `ui/header.py` | 顶部设置栏 — 提供商/模型选择器、Agent/Web/Think 开关 |
| `InputAreaMixin` | `ui/input_area.py` | 输入区域、发送/停止按钮、模式切换、@提及自动补全、确认模式 UI |
| `ChatViewMixin` | `ui/chat_view.py` | 对话显示、消息插入、滚动控制、Toast 通知 |
| `AgentRunnerMixin` | `core/agent_runner.py` | Agent 循环辅助、自动标题生成、确认模式拦截、工具分类常量 |
| `SessionManagerMixin` | `core/session_manager.py` | 多会话创建/切换/关闭、会话标签栏、状态保存/恢复 |

每个 Mixin 通过 `self` 访问 `AITab` 状态，实现职责分离同时共享状态。

### 上下文管理

- **原生工具消息链**：`assistant(tool_calls)` → `tool(result)` 消息直接传递给模型，保留结构化信息
- **严格的 user/assistant 交替**：确保跨提供商的 API 兼容性
- **按轮次裁剪**：对话按用户消息分割为轮次；超出 Token 预算时，先压缩旧轮次的工具结果，再整轮删除最早的轮次
- **永不截断 user/assistant**：仅压缩或移除 `tool` 结果内容
- **自动 RAG 注入**：根据用户查询自动检索相关的节点/VEX/HOM 文档
- **重复调用去重**：同一轮 Agent 循环中，相同参数的查询类工具调用会自动去重，节省 Token

### 线程安全

- Houdini 节点操作 **必须** 在 Qt 主线程运行 — 通过 `BlockingQueuedConnection` 分发
- 非 Houdini 工具（Shell、联网搜索、文档查询）在 **后台线程** 直接运行，保持 UI 响应
- 所有 UI 更新通过 Qt 信号实现线程安全的跨线程通信

### Token 计数与费用估算

- **tiktoken 集成** — 可用时使用 tiktoken 精准计数，否则使用改良估算
- **多模态 Token 估算** — 图片按约 765 Token 估算（低分辨率模式），确保预算跟踪准确
- **按模型计费** — 根据各提供商公布的定价（输入/输出/缓存费率）估算费用
- **推理 Token 追踪** — 单独统计推理/思考 Token（DeepSeek-R1、GLM-4.7 等）
- **多提供商缓存解析** — 统一处理 DeepSeek、OpenAI、Anthropic 和 Factory/拼好饭中转的 Cache 命中/未命中指标
- **Token 分析面板** — 每次请求的详细分解：输入、输出、推理、缓存、延迟和费用

### 智能错误恢复

- **参数纠错提示**：`set_node_parameter` 失败时，错误信息会列出相似的参数名或全部可用参数，帮助 AI 自我纠正
- **文档查阅建议**：节点创建或参数设置失败时，建议先查询文档（`search_node_types`、`get_houdini_node_doc`、`get_node_parameters`）再重试
- **连接重试**：网络瞬态错误（分块解码失败、连接中断等）自动指数退避重试

### 本地文档索引

`doc_rag.py` 模块提供基于 ZIP 归档的 O(1) 查找：

- **nodes.zip** — 全部 SOP/OBJ/DOP/VOP/COP 节点的文档（类型、描述、参数）
- **vex.zip** — VEX 函数签名和说明
- **hom.zip** — HOM（Houdini Object Model）类和方法文档
- **Doc/*.txt** — Houdini 编程知识库文章

相关文档会根据用户的查询自动注入到系统提示词中。

## 使用示例

**创建散点流程：**
```
用户：创建一个 box，在上面 scatter 500 个点，然后把小球复制到这些点上。
Agent：[add_todo: 规划 4 个步骤]
       [create_nodes_batch: box → scatter → sphere → copytopoints]
       [set_node_parameter: scatter npts=500, sphere radius=0.05]
       [connect_nodes: ...]
       [verify_and_summarize]
完成。创建了 box1 → scatter1 → copytopoints1，球体模板半径 0.05，500 个点。
```

**分析几何属性：**
```
用户：/obj/geo1/OUT 有哪些属性？
Agent：[run_skill: analyze_geometry_attribs, node_path=/obj/geo1/OUT]
该节点有 5 个 point 属性：P(vector3)、N(vector3)、Cd(vector3)、pscale(float)、id(int)。...
```

**搜索文档并应用：**
```
用户：heightfield noise 节点怎么用？
Agent：[search_local_doc: heightfield noise]
       [get_houdini_node_doc: heightfield_noise]
       [web_search: "SideFX Houdini heightfield noise parameters"]
根据文档，heightfield_noise 需要 HeightField 作为输入。...
```

**执行系统命令：**
```
用户：给 Houdini 的 Python 安装 numpy。
Agent：[execute_shell: "C:/Program Files/Side Effects Software/Houdini 21.0/bin/hython.exe" -m pip install numpy]
成功安装 numpy-1.26.4。
```

**编写 VEX 代码：**
```
用户：给所有点添加随机颜色。
Agent：[create_wrangle_node: vex_code="@Cd = set(rand(@ptnum), rand(@ptnum*13.37), rand(@ptnum*7.13));"]
已创建 attribwrangle1，为所有点设置了随机 Cd 属性。
```

## 常见问题

### API 连接问题
- 使用「测试连接」按钮进行诊断
- 检查 API Key 是否正确
- 确认网络可以访问 API 端点

### Agent 不调用工具
- 确认所选提供商支持 Function Calling
- DeepSeek、GLM-4.7、OpenAI、拼好饭（Claude）均支持工具调用
- Ollama 需要支持工具调用的模型（如 `qwen2.5`）

### 节点操作失败
- 确认在 Houdini 内运行（非独立 Python）
- 检查节点路径是否为绝对路径（如 `/obj/geo1/box1`）
- 查看工具执行结果中的具体错误信息

### UI 卡顿
- 非 Houdini 工具（Shell、联网）应在后台线程运行
- 如果执行 Shell 命令时 UI 卡顿，请更新到最新版本

### 更新
- 点击工具栏中的 **Update** 按钮检查新版本
- 插件启动时会静默检查 GitHub，有更新时按钮会高亮显示
- 更新时保留 `config/`、`cache/`、`trainData/` 目录
- 更新后插件自动重启

## 版本历史

- **v6.8.2** — **节点布局工具**：新增 `layout_nodes` 工具，支持 3 种策略 — `auto`（智能，使用 NetworkEditor.layoutNodes 或 moveToGoodPosition）、`grid`（固定宽度网格排列）、`columns`（基于拓扑深度的分列布局，可调间距）。新增 `get_node_positions` 查询节点坐标。**布局工作流规则**：System Prompt 强制执行顺序：创建节点 → 连接 → verify_and_summarize → layout_nodes → create_network_box（布局必须在 NetworkBox 之前，因为 fitAroundContents 依赖节点位置）。**Widget 闪烁修复**：`CollapsibleSection` 和 `ParamDiffWidget` 将 `setVisible` 调用移到 `addWidget` 之后，防止无 parent 窗口闪烁。
- **v6.8.1** — **英文 System Prompt 与裸节点名自动解析**：System Prompt 全面改写为英文以提升多模型兼容性（通过 `CRITICAL: You MUST reply in Simplified Chinese` 指令确保中文回复）。**裸节点名自动解析**：新增 `_resolve_bare_node_names()` 后处理器，自动将 AI 回复中的裸节点名（如 `box1`）替换为完整绝对路径（如 `/obj/geo1/box1`），数据来源为会话级节点路径映射（从工具结果中收集）。安全规则：仅替换以数字结尾的名称、仅在唯一路径映射时替换、跳过代码块、跳过已有路径成分。**Labs 目录英文标签**：`doc_rag.py` 中 Labs 分类名切换为英文。**NetworkBox 分组阈值**：调整为 6 个以上节点才创建 NetworkBox，避免小组过度封装。
- **v6.8** — **性能分析与扩展知识库**：新增 `perf_start_profile` / `perf_stop_and_report` 工具，基于 hou.perfMon 进行精确的 cook 时间和内存分析。新增 `analyze_cook_performance` Skill，快速诊断全网络 cook 时间排名和瓶颈节点（无需 perfMon）。**扩展知识库**：新增 5 个专题知识库 — SideFX Labs（301KB，含自动注入的节点分类目录）、HeightField/地形（249KB）、Copernicus/COP（87KB）、MPM 求解器（91KB）、机器学习（53KB）；知识库触发关键词从纯 VEX 扩展至全领域。**Labs 目录注入**：系统提示词动态注入 Labs 节点分类目录，AI 可主动推荐 Labs 工具用于游戏开发、纹理烘焙、地形生成、程序化创建等场景。**通用节点变更检测**：`execute_python`、`run_skill`、`copy_node` 等修改类工具执行前后自动快照网络子节点，检测到变更时生成 checkpoint 标签和撤销入口（之前仅 `create_node` / `set_node_parameter` 有此功能）。**连接端口名称**：`get_network_structure` 及所有连接关系显示中新增 `input_label`（如 `First Input(0)`），便于理解数据流方向。**思考区块默认展开**：`ThinkingSection` 默认展开且结束后保持展开状态（用户偏好）。**障碍协作规则**：系统提示词明确禁止 AI 在遇到障碍时放弃方案，要求暂停并清晰描述障碍和所需用户操作。**性能优化策略**：系统提示词内置 6 种常见优化手段（Cache 节点、避免 time dependent 表达式、VEX 替代 Python SOP、减少散点数量、Packed Primitives、for-each 循环审查）。**待确认操作清理**：清空对话时正确重置批量操作栏和待确认列表。
- **v6.7** — **PySide2/PySide6 兼容**：统一 `qt_compat.py` 兼容层自动检测 PySide 版本，所有模块从单一源导入。`invoke_on_main()` 辅助函数抽象 `QMetaObject.invokeMethod`+`Q_ARG`（PySide6）vs `QTimer.singleShot`（PySide2）差异。支持 Houdini 20.5（PySide2）到 Houdini 21+（PySide6）。**流式输出性能修复**：`AIResponse.content_label` 从 `QLabel.setText`（O(n) 全文重排）切换为 `QPlainTextEdit.insertPlainText`（O(1) 增量追加），彻底消除长回复流式输出卡顿。通过 `contentsChanged` 信号自动调整高度。缓冲刷新阈值提升至 200 字符 / 250ms。**图片内容剥离**：`AIClient` 新增 `_strip_image_content()` 方法，从旧消息中剥离 base64 `image_url`，防止 413 上下文溢出；集成到 `_progressive_trim`（按裁剪级别保留 2→1→0 张近期图片）和 `agent_loop_auto`/`agent_loop_json_mode`（非视觉模型预处理剥离）。**Cursor 风格图片生命周期**：仅当前轮次的用户消息为视觉模型保留图片，旧轮次自动转为纯文本。**@提及键盘导航**：上下箭头在补全列表中导航，Enter/Tab 选中，Escape 关闭，鼠标点击和失焦自动收起弹窗。**Token 分析面板**：记录改为倒序显示（最新优先）。**DeepSeek 上下文限制**：从 64K 更新为 128K（`deepseek-chat` 和 `deepseek-reasoner`）。**Wrangle class 参数映射**：系统提示词新增 run_over class 整数值对应关系（0=Detail, 1=Primitives, 2=Points, 3=Vertices, 4=Numbers），方便 `set_node_parameter` 设置。**渐进裁剪调优**：Level 2 保留 3 轮（原 5 轮），Level 3 保留 2 轮（原 3 轮）；`isinstance(c, str)` 类型守卫防止多模态 tool 内容导致崩溃。
- **v6.6** — **Mixin 架构拆分**：`ai_tab.py` 拆分为 5 个聚焦的 Mixin 模块（`HeaderMixin`、`InputAreaMixin`、`ChatViewMixin`、`AgentRunnerMixin`、`SessionManagerMixin`），提升可维护性。**NetworkBox 工具**：新增 3 个工具 — `create_network_box`（语义颜色预设：input 蓝/processing 绿/deform 橙/output 红/simulation 紫/utility 灰，可直接包含节点）、`add_nodes_to_box`、`list_network_boxes`；`get_network_structure` 增强支持 `box_name` 钻入模式和概览模式（自动折叠 box 节省 Token）。**NetworkBox 分组规范**：系统提示词要求 AI 在每个逻辑阶段完成后将节点打包到 NetworkBox 中（每组至少 6 个节点），并提供层级导航准则。**确认模式**：`AgentRunnerMixin` 为破坏性工具（创建/删除/修改）添加执行前确认对话框。**思考区块重构**：`ThinkingSection` 从 `QLabel` 切换为 `QPlainTextEdit`，自带滚动条和动态高度计算（与 `ChatInput` 同方案），最大高度 400px。**脉冲指示器**：`PulseIndicator` 动画透明度脉冲圆点，表示"进行中"状态。**工具状态栏**：`ToolStatusBar` 在输入框下方实时显示工具执行状态。**节点补全弹窗**：`NodeCompleterPopup` 支持 `@` 提及自动补全节点路径。**更新器重构**：改用 GitHub Releases API（而非基于 branch 的 VERSION 文件检查），缓存 `zipball_url`。**训练数据导出**：支持多模态消息内容提取（剥离图片，保留 list 格式中的文本）。**模块重载**：所有 Mixin 模块加入重载列表；`MainWindow` 引用重载后刷新；旧窗口调用 `deleteLater()` 干净销毁。
- **v6.5** — **Agent / Ask 模式**：输入区域下方 Radio 风格切换 — Agent 模式拥有全部工具权限；Ask 模式限制为只读/查询工具，带白名单守卫和系统提示词约束。**Undo All / Keep All**：批量操作栏追踪所有待确认的节点/参数变更，"Undo All" 按逆序撤销，"Keep All" 一键全部确认。**深度思考框架**：`<think>` 标签现在要求结构化 6 步流程（理解→现状→方案→决策→计划→风险），附带明确的思考原则。**自动更新器**：`VERSION` 文件用于语义版本追踪；启动时静默检查 GitHub；一键下载+覆盖+重启，带进度对话框；更新时保留 `config/`、`cache/`、`trainData/`。`tools_override` 参数支持模式级工具过滤。ParamDiff 默认展开。参数值未变时跳过 undo 快照。
- **v6.4** — **参数 Diff UI**：`set_node_parameter` 显示内联红绿 diff（标量值）和可折叠统一 diff（多行 VEX 代码），支持一键撤销恢复旧值（标量/元组/表达式）。**用户消息折叠**：超过 2 行的消息自动折叠，点击展开/收起。**场景感知 RAG**：用选中节点类型增强检索查询，根据对话长度动态调整注入量（400/800/1200 字符）。**持久化 HTTP Session**：连接池复用，消除每轮 TLS 握手开销。**预编译正则**：XML 标签清洗模式类级编译一次。**消息清洗脏标志**：无新 tool 消息时跳过 O(n) 遍历。**去除工具间延迟**：Houdini 工具执行之间不再 sleep。
- **v6.3** — **图片放大预览**：点击缩略图弹出全尺寸查看窗口。**`<think>` 标签强制规则升级**：系统提示词将缺失标签视为格式违规；工具执行后的后续回复同样要求标签。**健壮的 usage 解析**：统一处理 DeepSeek、OpenAI、Anthropic 和 Factory/拼好饭中转的缓存命中/未命中/写入指标（含首次诊断输出）。**精确节点路径提取**：`_extract_node_paths` 按工具类型使用专用正则，避免提取父网络等上下文路径。**多模态 Token 计算**：图片按 ~765 Token 估算，预算跟踪更准确。**Duojie 思考模式**：弃用 `reasoningEffort` 参数（实测无效），改为纯 `<think>` 标签提示。工具 Schema：数组参数值增加 `items` 类型提示。
- **v6.2** — **图片/多模态输入**：支持粘贴/拖拽/文件选择器附加图片，发送前缩略图预览，模型视觉能力自动检测。**Wrangle run_over 指导**：系统提示词新增 VEX 执行上下文选择规则（防止错误的 run_over 模式）。**新增模型**：`gpt-5.3-codex`、`claude-opus-4-6-normal`、`claude-opus-4-6-kiro`。**代理 tool_call 修复**：健壮拆分中转服务拼接的 `{...}{...}` arguments。**旧模块清理**：启动时自动清理 `HOUDINI_HIP_MANAGER` 残留模块。
- **v6.1** — 可点击节点路径、Token 费用追踪（tiktoken + 按模型计费）、Token 分析面板、参数智能纠错提示、`verify_and_summarize` 优化（内置网络检查）、重复调用去重、文档查阅建议、连接指数退避重试、模型默认值更新（GLM-4.7、GPT-5.2、Gemini-3-Pro）
- **v6.0** — **Houdini Agent**：原生工具链、按轮次上下文裁剪、合并 `get_node_details` 到 `get_node_parameters`、Skill 系统（8 个分析脚本）、`execute_shell` 工具、本地文档 RAG、拼好饭/Ollama 提供商、多会话标签页、线程安全工具分发、连接重试逻辑
- **v5.0** — 深色 UI 大更新：深色主题、可折叠区块、停止按钮、自动上下文压缩、代码高亮
- **v4.0** — Agent 模式：多轮工具调用、GLM-4 支持
- **v3.0** — 精简为纯 Houdini 工具
- **v2.0** — 多 DCC 架构
- **v1.0** — 初始版本

## 作者

KazamaSuichiku

## 许可证

MIT
