# Houdini Agent

**[English](README.md)** | **[中文](README_CN.md)**

An AI-powered assistant for SideFX Houdini, featuring autonomous multi-turn tool calling, web search, VEX/Python code execution, and a minimal dark UI.

Built on the **OpenAI Function Calling** protocol, the agent can read node networks, create/modify/connect nodes, run VEX wrangles, execute system shell commands, search the web, and query local documentation — all within an iterative agent loop.

## Core Features

### Agent Loop

The AI operates in an autonomous **agent loop**: it receives a user request, plans the steps, calls tools, inspects results, and iterates until the task is complete. Two modes are available:

- **Agent mode** — Full access to all 37+ tools. The AI can create, modify, connect, and delete nodes, set parameters, execute scripts, and save the scene.
- **Ask mode** — Read-only. The AI can only query scene structure, inspect parameters, search documentation, and provide analysis. All mutating tools are blocked by a whitelist guard.

```
User request → AI plans → call tools → inspect results → call more tools → … → final reply
```

- **Multi-turn tool calling** — the AI decides which tools to call and in what order
- **Todo task system** — complex tasks are broken into tracked subtasks with live status updates
- **Streaming output** — real-time display of thinking process and responses
- **Extended Thinking** — native support for reasoning models (DeepSeek-R1, GLM-4.7, Claude with `<think>` tags)
- **Stop anytime** — interrupt the running agent loop at any point
- **Smart context management** — round-based conversation trimming that never truncates user/assistant messages, only compresses tool results

### Supported AI Providers

| Provider | Models | Notes |
|----------|--------|-------|
| **DeepSeek** | `deepseek-chat`, `deepseek-reasoner` (R1) | Cost-effective, fast, supports Function Calling & reasoning |
| **GLM (Zhipu AI)** | `glm-4.7` | Stable in China, native reasoning & tool calling |
| **OpenAI** | `gpt-5.2`, `gpt-5.3-codex` | Powerful, full Function Calling & Vision support |
| **Ollama** (local) | `qwen2.5:14b`, any local model | Privacy-first, auto-detects available models |
| **Duojie** (relay) | `claude-sonnet-4-5`, `claude-opus-4-6-kiro`, `gemini-3-pro-image-preview`, etc. | Access to Claude & Gemini models via relay endpoint |

### Vision / Image Input

- **Multimodal messages** — attach images (PNG/JPG/GIF/WebP) to your messages for vision-capable models
- **Paste & drag-drop** — `Ctrl+V` paste from clipboard, drag image files into the chat input
- **File picker** — click the "Img" button to select images from disk
- **Image preview** — thumbnails displayed above the input box before sending, with remove buttons; **click any thumbnail to enlarge** in a full-size preview dialog
- **Model-aware** — automatically checks if the current model supports vision; non-vision models show a clear warning
- Supported: OpenAI GPT-5.2/5.3, Claude (all variants), Gemini

### Dark UI

- Minimal dark theme
- Collapsible blocks for thinking process, tool calls, and results
- Dedicated **Python Shell** and **System Shell** widgets with syntax highlighting
- **Clickable node paths** — paths like `/obj/geo1/box1` in AI responses become links that navigate to the node in Houdini
- **Node context bar** showing the currently selected Houdini node
- **Todo list** displayed above the chat area with live status icons
- **Token analytics** — real-time token count, reasoning tokens, cache hit rate, and per-model cost estimates (click for detailed breakdown)
- Multi-session tabs — run multiple independent conversations
- Copy button on AI responses
- `Ctrl+Enter` to send messages

## Available Tools (37+)

### Node Operations

| Tool | Description |
|------|-------------|
| `create_wrangle_node` | **Priority tool** — create a Wrangle node with VEX code (point/prim/vertex/volume/detail) |
| `create_node` | Create a single node by type name |
| `create_nodes_batch` | Batch-create nodes with automatic connections |
| `connect_nodes` | Connect two nodes (with input index control) |
| `delete_node` | Delete a node by path |
| `copy_node` | Copy/clone a node to the same or another network |
| `set_node_parameter` | Set a single parameter value (with smart error hints, inline red/green diff preview, and one-click undo) |
| `batch_set_parameters` | Set the same parameter across multiple nodes |
| `set_display_flag` | Set display/render flags on a node |
| `save_hip` | Save the current HIP file |
| `undo_redo` | Undo or redo operations |

### Query & Inspection

| Tool | Description |
|------|-------------|
| `get_network_structure` | Get the node network topology — **NetworkBox-aware**: auto-folds boxes into overview (name + comment + node count) when present, use `box_name` to drill into a specific box; saves significant tokens on large networks |
| `get_node_parameters` | Get node parameters **plus** node status, flags, errors, inputs, and outputs (replaces old `get_node_details`) |
| `list_children` | List child nodes with flags (like `ls`) |
| `read_selection` | Read the currently selected node(s) in the viewport |
| `search_node_types` | Keyword search for Houdini node types |
| `semantic_search_nodes` | Natural-language search for node types (e.g. "scatter points on surface") |
| `find_nodes_by_param` | Search nodes by parameter value (like `grep`) |
| `get_node_inputs` | Get input port info (210+ common nodes pre-cached) |
| `check_errors` | Check Houdini node cooking errors and warnings |
| `verify_and_summarize` | Validate the network and generate a summary report (includes `get_network_structure` — no need to call separately) |

### Code Execution

| Tool | Description |
|------|-------------|
| `execute_python` | Run Python code in the Houdini Python Shell (`hou` module available) |
| `execute_shell` | Run system shell commands (pip, git, ssh, scp, ffmpeg, etc.) with timeout and safety checks |

### Web & Documentation

| Tool | Description |
|------|-------------|
| `web_search` | Search the web via Brave/DuckDuckGo (auto-fallback, cached) |
| `fetch_webpage` | Fetch and extract webpage content (paginated, encoding-aware) |
| `search_local_doc` | Search the local Houdini doc index (nodes, VEX functions, HOM classes) |
| `get_houdini_node_doc` | Get detailed node documentation (local help server → SideFX online → node type info) |

### Skills (Pre-built Analysis Scripts)

| Tool | Description |
|------|-------------|
| `run_skill` | Execute a named skill with parameters |
| `list_skills` | List all available skills |

### NetworkBox (Node Organization)

| Tool | Description |
|------|-------------|
| `create_network_box` | Create a NetworkBox (grouping frame) with semantic color presets (input/processing/deform/output/simulation/utility) and optionally include specified nodes |
| `add_nodes_to_box` | Add nodes to an existing NetworkBox with optional auto-fit |
| `list_network_boxes` | List all NetworkBoxes in a network with their contents and metadata |

### Node Layout

| Tool | Description |
|------|-------------|
| `layout_nodes` | Auto-layout nodes — supports `auto` (smart), `grid`, and `columns` (topological depth) strategies with adjustable spacing |
| `get_node_positions` | Get node positions (x, y coordinates and type) for layout verification or manual fine-tuning |

### Performance Profiling

| Tool | Description |
|------|-------------|
| `perf_start_profile` | Start Houdini perfMon profiling — optionally force-cook a node to trigger the full chain |
| `perf_stop_and_report` | Stop profiling and return a detailed cook-time / memory report (paginated) |

### Task Management

| Tool | Description |
|------|-------------|
| `add_todo` | Add a task to the Todo list |
| `update_todo` | Update task status (pending / in_progress / done / error) |

## Skills System

Skills are pre-optimized Python scripts that run inside the Houdini environment for reliable geometry analysis. They are preferred over hand-written `execute_python` for common tasks.

| Skill | Description |
|-------|-------------|
| `analyze_geometry_attribs` | Attribute statistics (min/max/mean/std/NaN/Inf) for point/vertex/prim/detail |
| `analyze_normals` | Normal quality detection — NaN, zero-length, non-normalized, flipped faces |
| `get_bounding_info` | Bounding box, center, size, diagonal, volume, surface area, aspect ratio |
| `analyze_connectivity` | Connected components analysis (piece count, point/prim per piece) |
| `compare_attributes` | Diff attributes between two nodes (added/removed/type-changed) |
| `find_dead_nodes` | Find orphan and unused end-of-chain nodes |
| `trace_node_dependencies` | Trace upstream dependencies or downstream impacts |
| `find_attribute_references` | Find all nodes referencing a given attribute (VEX code, expressions, string params) |
| `analyze_cook_performance` | **New** — Network-wide cook-time ranking, geometry-inflation detection, error/warning nodes, bottleneck identification |

## Project Structure

```
Houdini-Agent/
├── launcher.py                      # Entry point (auto-detects Houdini)
├── README.md
├── README_CN.md
├── lib/                             # Bundled dependencies (requests, urllib3, certifi, tiktoken, …)
├── config/                          # Runtime config (auto-created, gitignored)
│   └── houdini_ai.ini              # API keys & settings
├── cache/                           # Conversation cache, doc index, HIP previews
├── Doc/                             # Offline documentation
│   ├── houdini_knowledge_base.txt  # Houdini programming knowledge base
│   ├── vex_attributes_reference.txt
│   ├── vex_snippets_reference.txt
│   ├── labs_knowledge_base.txt     # SideFX Labs nodes knowledge base
│   ├── heightfields_knowledge_base.txt  # HeightField / Terrain knowledge base
│   ├── copernicus_knowledge_base.txt    # Copernicus (COP) knowledge base
│   ├── ml_knowledge_base.txt       # Machine Learning knowledge base
│   ├── mpm_knowledge_base.txt      # MPM solver knowledge base
│   ├── copernicus/                  # Copernicus raw docs
│   ├── heightfields/                # HeightField raw docs
│   ├── ml/                          # ML raw docs
│   ├── mpm/                         # MPM raw docs
│   ├── nodes.zip                   # Node docs index (wiki markup)
│   ├── vex.zip                     # VEX function docs index
│   └── hom.zip                     # HOM class/method docs index
├── shared/                          # Shared utilities
│   └── common_utils.py             # Path & config helpers
├── trainData/                       # Exported training data (JSONL)
└── houdini_agent/                   # Main module
    ├── main.py                     # Module entry & window management
    ├── shelf_tool.py               # Houdini shelf tool integration
    ├── qt_compat.py                # PySide2/PySide6 compatibility layer
    ├── QUICK_SHELF_CODE.py         # Quick shelf code snippet
    ├── core/
    │   ├── main_window.py          # Main window (workspace save/restore)
    │   ├── agent_runner.py         # AgentRunnerMixin — agent loop helpers, confirm mode, tool scheduling
    │   └── session_manager.py      # SessionManagerMixin — multi-session create/switch/close
    ├── ui/
    │   ├── ai_tab.py              # AI Agent tab (Mixin host, agent loop, context management, streaming UI)
    │   ├── cursor_widgets.py      # UI widgets (theme, chat blocks, todo, shells, token analytics)
    │   ├── header.py              # HeaderMixin — top settings bar (provider, model, toggles)
    │   ├── input_area.py          # InputAreaMixin — input area, mode switches, @mention, confirm mode
    │   └── chat_view.py           # ChatViewMixin — chat display, scrolling, toast messages
    ├── skills/                     # Pre-built analysis scripts
    │   ├── __init__.py            # Skill registry & loader
    │   ├── analyze_normals.py     # Normal quality detection
    │   ├── analyze_point_attrib.py # Geometry attribute statistics
    │   ├── bounding_box_info.py   # Bounding box info
    │   ├── compare_attributes.py  # Attribute diff between nodes
    │   ├── connectivity_analysis.py # Connected components
    │   ├── find_attrib_references.py # Attribute usage search
    │   ├── find_dead_nodes.py     # Dead/orphan node finder
    │   ├── trace_dependencies.py  # Dependency tree tracer
    │   └── analyze_cook_performance.py # Cook-time ranking & bottleneck detection
    └── utils/
        ├── ai_client.py           # AI API client (streaming, Function Calling, web search)
        ├── doc_rag.py             # Local doc index (nodes/VEX/HOM O(1) lookup)
        ├── token_optimizer.py     # Token budget & compression (tiktoken-powered)
        ├── ultra_optimizer.py     # System prompt & tool definition optimizer
        ├── training_data_exporter.py # Export conversations as training JSONL
        └── mcp/                   # Houdini MCP (Model Context Protocol) layer
            ├── client.py          # Tool executor (node ops, shell, skills dispatch)
            ├── hou_core.py        # Low-level hou module wrappers
            ├── node_inputs.json   # Pre-cached input port info (210+ nodes)
            ├── server.py          # MCP server (reserved)
            ├── settings.py        # MCP settings
            └── logger.py          # Logging
```

## Quick Start

### Requirements

- **Houdini 20.5+** (or 21+)
- **Python 3.9+** (bundled with Houdini)
- **PySide2 or PySide6** (bundled with Houdini — PySide2 for Houdini ≤20.5, PySide6 for Houdini 21+)
- **Windows** (primary), Linux/macOS support possible

### Installation

No pip install needed — all dependencies are bundled in the `lib/` directory.

1. Clone or download this repository
2. Place it anywhere accessible from Houdini

### Launch in Houdini

```python
import sys
sys.path.insert(0, r"C:\path\to\Houdini-Agent")
import launcher
launcher.show_tool()
```

Or add this to a **Shelf Tool** for one-click access.

### Configure API Keys

**Option A: Environment Variables (recommended)**

   ```powershell
   # DeepSeek
[Environment]::SetEnvironmentVariable('DEEPSEEK_API_KEY', 'sk-xxx', 'User')
   
# GLM (Zhipu AI)
[Environment]::SetEnvironmentVariable('GLM_API_KEY', 'xxx.xxx', 'User')
   
   # OpenAI
[Environment]::SetEnvironmentVariable('OPENAI_API_KEY', 'sk-xxx', 'User')

# Duojie (relay)
[Environment]::SetEnvironmentVariable('DUOJIE_API_KEY', 'xxx', 'User')
```

**Option B: In-app settings**

Click the "Set API Key…" button and check "Save to local config".

## Architecture

### Agent Loop Flow

```
┌─────────────────────────────────────────────────────────┐
│  User sends message                                      │
│  ↓                                                       │
│  System prompt + conversation history + RAG docs         │
│  ↓                                                       │
│  AI model (streaming) → thinking + tool_calls            │
│  ↓                                                       │
│  Tool executor dispatches each tool:                     │
│    - Houdini tools → main thread (Qt BlockingQueued)     │
│    - Shell / web / doc → background thread (non-blocking)│
│  ↓                                                       │
│  Tool results → fed back to AI as tool messages          │
│  ↓                                                       │
│  AI continues (may call more tools or produce final text)│
│  ↓                                                       │
│  Loop until AI finishes or max iterations reached        │
└─────────────────────────────────────────────────────────┘
```

### Mixin Architecture

`AITab` is the central widget, composed from five focused Mixins:

| Mixin | Module | Responsibility |
|-------|--------|---------------|
| `HeaderMixin` | `ui/header.py` | Top settings bar — provider/model selectors, Agent/Web/Think toggles |
| `InputAreaMixin` | `ui/input_area.py` | Input area, send/stop buttons, mode switches, @-mention autocomplete, confirm mode UI |
| `ChatViewMixin` | `ui/chat_view.py` | Chat display, message insertion, scroll control, toast notifications |
| `AgentRunnerMixin` | `core/agent_runner.py` | Agent loop helpers, auto title generation, confirm mode interception, tool category constants |
| `SessionManagerMixin` | `core/session_manager.py` | Multi-session create/switch/close, session tab bar, state save/restore |

Each Mixin accesses `AITab` state via `self`, enabling clean separation without breaking shared state.

### Context Management

- **Native tool message chain**: `assistant(tool_calls)` → `tool(result)` messages are passed directly to the model, preserving structured information
- **Strict user/assistant alternation**: Ensures API compatibility across providers
- **Round-based trimming**: Conversations are split into rounds (by user messages); when token budget is exceeded, older rounds' tool results are compressed first, then entire rounds are removed
- **Never truncate user/assistant**: Only `tool` result content is compressed or removed
- **Automatic RAG injection**: Relevant node/VEX/HOM documentation is automatically retrieved based on the user's query
- **Duplicate call dedup**: Identical query-tool calls within the same agent turn are deduplicated to save tokens

### Thread Safety

- Houdini node operations **must** run on the Qt main thread — dispatched via `BlockingQueuedConnection`
- Non-Houdini tools (shell, web search, doc lookup) run directly in the **background thread** to keep the UI responsive
- All UI updates use Qt signals for thread-safe cross-thread communication

### Token Counting & Cost Estimation

- **tiktoken integration** — accurate token counting when available, with improved fallback estimation
- **Multimodal token estimation** — images are estimated at ~765 tokens each (low-res mode) for accurate budget tracking
- **Per-model pricing** — estimated costs based on each provider's published pricing (input/output/cache rates)
- **Reasoning token tracking** — separate count for reasoning/thinking tokens (DeepSeek-R1, GLM-4.7, etc.)
- **Multi-provider cache parsing** — unified handling of cache hit/miss metrics across DeepSeek, OpenAI, Anthropic, and Factory/Duojie relay formats
- **Token Analytics Panel** — detailed breakdown per request: input, output, reasoning, cache, latency, and cost

### Smart Error Recovery

- **Parameter hints**: When `set_node_parameter` fails, the error message includes similar parameter names or a list of available parameters to help the AI self-correct
- **Doc-check suggestions**: When node creation or parameter setting fails, the error suggests querying documentation (`search_node_types`, `get_houdini_node_doc`, `get_node_parameters`) before retrying blindly
- **Connection retry**: Transient network errors (chunk decoding, connection drops) are automatically retried with exponential backoff

### Local Documentation Index

The `doc_rag.py` module provides O(1) lookup from bundled ZIP archives:

- **nodes.zip** — Node documentation (type, description, parameters) for all SOP/OBJ/DOP/VOP/COP nodes
- **vex.zip** — VEX function signatures and descriptions
- **hom.zip** — HOM (Houdini Object Model) class and method docs
- **Doc/*.txt** — Knowledge base articles on Houdini programming

Relevant docs are automatically injected into the system prompt based on the user's query.

## Usage Examples

**Create a scatter setup:**
```
User: Create a box, scatter 500 points on it, and copy small spheres to the points.
Agent: [add_todo: plan 4 steps]
       [create_nodes_batch: box → scatter → sphere → copytopoints]
       [set_node_parameter: scatter npts=500, sphere radius=0.05]
       [connect_nodes: ...]
       [verify_and_summarize]
Done. Created box1 → scatter1 → copytopoints1 with a sphere template. 500 points, radius 0.05.
```

**Analyze geometry attributes:**
```
User: What attributes does /obj/geo1/OUT have?
Agent: [run_skill: analyze_geometry_attribs, node_path=/obj/geo1/OUT]
The node has 5 point attributes: P(vector3), N(vector3), Cd(vector3), pscale(float), id(int). ...
```

**Search and apply from documentation:**
```
User: How do I use the heightfield noise node?
Agent: [search_local_doc: heightfield noise]
       [get_houdini_node_doc: heightfield_noise]
       [web_search: "SideFX Houdini heightfield noise parameters"]
Based on the documentation, heightfield_noise requires a HeightField input. ...
```

**Execute shell commands:**
```
User: Install numpy for Houdini's Python.
Agent: [execute_shell: "C:/Program Files/Side Effects Software/Houdini 21.0/bin/hython.exe" -m pip install numpy]
Successfully installed numpy-1.26.4.
```

**Run VEX code:**
```
User: Add random colors to all points.
Agent: [create_wrangle_node: vex_code="@Cd = set(rand(@ptnum), rand(@ptnum*13.37), rand(@ptnum*7.13));"]
Created attribwrangle1 with random Cd attribute on all points.
```

## Troubleshooting

### API Connection Issues
- Use the "Test Connection" button to diagnose
- Check that your API key is correct
- Verify network access to the API endpoint

### Agent Not Calling Tools
- Ensure the selected provider supports Function Calling
- DeepSeek, GLM-4.7, OpenAI, and Duojie (Claude) all support tool calling
- Ollama requires models with tool-calling support (e.g. `qwen2.5`)

### Node Operations Fail
- Confirm you are running inside Houdini (not standalone Python)
- Check that node paths are absolute (e.g. `/obj/geo1/box1`)
- Review the tool execution result for specific error messages

### UI Freezing
- Non-Houdini tools (shell, web) should run in the background thread
- If the UI freezes during shell commands, update to the latest version

### Updating
- Click the **Update** button in the toolbar to check for new versions
- The plugin checks GitHub on startup (silently) and highlights the button if an update is available
- Updates preserve your `config/`, `cache/`, and `trainData/` directories
- After updating, the plugin restarts automatically

## Version History

- **v6.8.2** — **Node layout tools**: New `layout_nodes` tool with 3 strategies — `auto` (smart, uses NetworkEditor.layoutNodes or moveToGoodPosition), `grid` (fixed-width grid arrangement), `columns` (topological depth-based column layout with adjustable spacing). New `get_node_positions` for querying node coordinates. **Layout workflow rule**: System prompt enforces execution order: create nodes → connect → verify_and_summarize → layout_nodes → create_network_box (layout must happen before NetworkBox because fitAroundContents depends on node positions). **Widget flash fix**: `CollapsibleSection` and `ParamDiffWidget` now call `setVisible` after `addWidget` to prevent parentless widget flash-as-window artifacts.
- **v6.8.1** — **English system prompt & bare node name auto-resolution**: System prompt fully rewritten in English for better multi-model compatibility (Chinese reply enforced via `CRITICAL: You MUST reply in Simplified Chinese`). **Bare node name auto-resolution**: New `_resolve_bare_node_names()` post-processor automatically replaces bare node names (e.g. `box1`) in AI replies with full absolute paths (e.g. `/obj/geo1/box1`) using a session-level node path map collected from tool results. Safety rules: only replaces names ending with digits, only when unique path mapping exists, skips code blocks, skips existing path components. **Labs catalog English labels**: Category names in `doc_rag.py` switched to English. **NetworkBox grouping threshold**: Raised to 6+ nodes per box; smaller groups are left ungrouped to reduce clutter.
- **v6.8** — **Performance profiling & expanded knowledge**: New `perf_start_profile` / `perf_stop_and_report` tools for precise Houdini perfMon-based cook-time and memory profiling. New `analyze_cook_performance` skill for quick network-wide cook-time ranking and bottleneck detection without perfMon. **Expanded knowledge bases**: 5 new domain-specific knowledge bases — SideFX Labs (301KB, with auto-injected node catalog in system prompt), HeightFields/Terrain (249KB), Copernicus/COP (87KB), MPM solver (91KB), Machine Learning (53KB); knowledge trigger keywords extended from VEX-only to all domains. **Labs catalog injection**: System prompt dynamically injects a categorized Labs node directory so the AI proactively recommends Labs tools for game dev, texture baking, terrain, procedural generation, etc. **Universal node-change detection**: `execute_python`, `run_skill`, `copy_node`, and other mutation tools now take before/after network snapshots to auto-generate checkpoint labels and undo entries — previously only `create_node` / `set_node_parameter` had this. **Connection port labels**: `get_network_structure` and all connection displays now show `input_label` (e.g. `First Input(0)`) alongside the index for clearer data-flow understanding. **Thinking section always expanded**: `ThinkingSection` defaults to expanded and stays open after finalization (user preference). **Obstacle collaboration rules**: System prompt now explicitly forbids the AI from abandoning a plan when encountering obstacles — instead it must pause, describe the blocker clearly, and request specific user action. **Performance optimization guidelines**: System prompt includes 6 common optimization strategies (cache nodes, avoid time-dependent expressions, VEX over Python SOP, reduce scatter counts, packed primitives, for-each loop audit). **Pending ops cleanup**: Chat clear now properly resets the batch operations bar and pending ops list.
- **v6.7** — **PySide2/PySide6 compatibility**: Unified `qt_compat.py` layer auto-detects PySide version; all modules import from this single source. `invoke_on_main()` helper abstracts `QMetaObject.invokeMethod`+`Q_ARG` (PySide6) vs `QTimer.singleShot` (PySide2). Supports Houdini 20.5 (PySide2) through Houdini 21+ (PySide6). **Streaming performance fix**: `AIResponse.content_label` switched from `QLabel.setText` (O(n) full re-render) to `QPlainTextEdit.insertPlainText` (O(1) incremental append) — eliminates long-reply streaming stutter. Dynamic height auto-resize via `contentsChanged` signal. Buffer flush threshold raised to 200 chars / 250ms. **Image content stripping**: New `_strip_image_content()` in `AIClient` strips base64 `image_url` from older messages to prevent 413 context overflow; integrated into `_progressive_trim` (level-aware: keeps 2→1→0 recent images) and `agent_loop_auto`/`agent_loop_json_mode` (pre-strip for non-vision models). **Cursor-style image lifecycle**: Only the current round's user message retains images for vision models; all older rounds are automatically stripped to plain text. **@-mention keyboard navigation**: Up/Down arrows navigate the completer list; Enter/Tab select; Escape closes; mouse click and focus-out auto-dismiss the popup. **Token Analytics**: Records now displayed newest-first (reversed order). **DeepSeek context limit**: Updated from 64K→128K for both `deepseek-chat` and `deepseek-reasoner`. **Wrangle class mapping**: System prompt now documents run_over class integer values (0=Detail, 1=Primitives, 2=Points, 3=Vertices, 4=Numbers) for `set_node_parameter`. **Progressive trim tuning**: Level 2 keeps 3 rounds (was 5); level 3 keeps 2 rounds (was 3); `isinstance(c, str)` guard prevents crashing on multimodal tool content.
- **v6.6** — **Mixin architecture**: `ai_tab.py` decomposed into 5 focused Mixin modules (`HeaderMixin`, `InputAreaMixin`, `ChatViewMixin`, `AgentRunnerMixin`, `SessionManagerMixin`) for better maintainability. **NetworkBox tools**: 3 new tools — `create_network_box` (semantic color presets: input/processing/deform/output/simulation/utility, auto-include nodes), `add_nodes_to_box`, `list_network_boxes`; `get_network_structure` enhanced with `box_name` drill-in and overview mode that auto-folds boxes to save tokens. **NetworkBox grouping rules**: System prompt requires AI to organize nodes into NetworkBoxes after each logical stage (min 6 nodes per group), with hierarchical navigation guidelines. **Confirm mode**: `AgentRunnerMixin` adds confirmation dialog for destructive tools (create/delete/modify) before execution. **ThinkingSection overhaul**: Switched from `QLabel` to `QPlainTextEdit` with scrollbar, dynamic height calculation matching `ChatInput` approach, max 400px. **PulseIndicator**: Animated opacity-pulsing dot widget for "in progress" status. **ToolStatusBar**: Real-time tool execution status display below input area. **NodeCompleterPopup**: `@`-mention autocomplete for node paths. **Updater refactored**: Now uses GitHub Releases API (not branch-based VERSION file), cached `zipball_url`. **Training data exporter**: Multimodal content extraction (strips images, keeps text from list-format messages). **Module reload**: All Mixin modules added to reload list; `MainWindow` reference refreshed after reload; `deleteLater()` on old window for clean teardown.
- **v6.5** — **Agent / Ask mode**: Radio-style toggle below the input area — Agent mode has full tool access; Ask mode restricts to read-only/query tools with a whitelist guard and system prompt constraint. **Undo All / Keep All**: Batch operations bar tracks all pending node/param changes; "Undo All" reverts in reverse order, "Keep All" confirms everything at once. **Deep thinking framework**: `<think>` tag now requires a structured 6-step process (Understand → Status → Options → Decision → Plan → Risk) with explicit thinking principles. **Auto-updater**: `VERSION` file for semver tracking; silent GitHub API check on startup; one-click download + apply + restart with a progress dialog; preserves `config/`, `cache/`, `trainData/` during update. **`tools_override`**: `agent_loop_stream` and `agent_loop_json_mode` accept custom tool lists for mode-specific filtering. ParamDiff defaults to expanded. Skip undo snapshot when parameter value is unchanged.
- **v6.4** — **Parameter Diff UI**: `set_node_parameter` now shows inline red/green diff for scalar changes and collapsible unified diff for multi-line VEX code, with one-click undo to restore old values (supports scalars, tuples, and expressions). **User message collapse**: messages longer than 2 lines auto-fold with "expand / collapse" toggle. **Scene-aware RAG**: auto-retrieval query enriched with selected node types from Houdini scene; dynamic `max_chars` (400/800/1200) based on conversation length. **Persistent HTTP Session**: `requests.Session` with connection pooling eliminates TLS renegotiation per turn. **Pre-compiled regex**: XML tag cleanup patterns compiled once at class level. **Sanitize dirty flag**: skip O(n) message sanitization when no new tool messages are added. **Removed inter-tool delays** (`time.sleep` eliminated between Houdini tool executions).
- **v6.3** — **Image preview dialog**: click thumbnails to enlarge in a full-size modal viewer. **Stricter `<think>` tag enforcement**: system prompt now treats missing tags as format violations; follow-up replies after tool execution also require tags. **Robust usage parsing**: unified cache hit/miss/write metrics across DeepSeek, OpenAI, Anthropic, and Factory/Duojie relay formats (with one-time diagnostic dump). **Precise node path extraction**: `_extract_node_paths` now uses tool-specific regex rules to avoid picking up parent/context paths. **Multimodal token counting**: images estimated at ~765 tokens for accurate budget tracking. **Duojie think mode**: abandoned `reasoningEffort` parameter (ineffective), relies on `<think>` tag prompting only. Tool schema: added `items` type hint for array parameter values.
- **v6.2** — **Vision/Image input**: multimodal messages with paste/drag-drop/file-picker, image preview with thumbnails, model-aware vision check. **Wrangle run_over guidance** in system prompt (prevents wrong VEX execution context). **New models**: `gpt-5.3-codex`, `claude-opus-4-6-normal`, `claude-opus-4-6-kiro`. **Proxy tool_call fix**: robust splitting of concatenated `{...}{...}` arguments from relay services. **Legacy module cleanup** on startup.
- **v6.1** — Clickable node paths, token cost tracking (tiktoken + per-model pricing), Token Analytics Panel, smart parameter error hints, streamlined `verify_and_summarize` (built-in network check), duplicate call dedup, doc-check error suggestions, connection retry with backoff, updated model defaults (GLM-4.7, GPT-5.2, Gemini-3-Pro)
- **v6.0** — **Houdini Agent**: Native tool chain, round-based context trimming, merged `get_node_details` into `get_node_parameters`, Skills system (8 analysis scripts), `execute_shell` tool, local doc RAG, Duojie/Ollama providers, multi-session tabs, thread-safe tool dispatch, connection retry logic
- **v5.0** — Dark UI overhaul: dark theme, collapsible blocks, stop button, auto context compression, code highlighting
- **v4.0** — Agent mode: multi-turn tool calling, GLM-4 support
- **v3.0** — Houdini-only tool (removed other DCC support)
- **v2.0** — Multi-DCC architecture
- **v1.0** — Initial release

## Author

KazamaSuichiku

## License

MIT
