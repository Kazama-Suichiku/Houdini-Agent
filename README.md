# Houdini Agent

**[English](README.md)** | **[中文](README_CN.md)**

An AI-powered assistant for SideFX Houdini, featuring autonomous multi-turn tool calling, web search, VEX/Python code execution, Plan mode for complex tasks, a brain-inspired long-term memory system, a plugin hook system for community extensions, user-defined context rules, and a modern dark UI with bilingual support.

Built on the **OpenAI Function Calling** protocol, the agent can read node networks, create/modify/connect nodes, run VEX wrangles, execute system shell commands, search the web, query local documentation, create structured execution plans, learn from past interactions, and be extended via plugins — all within an iterative agent loop. A centralized **ToolRegistry** unifies core tools, skills, and plugin tools with mode-based access control.

## Core Features

### Agent Loop

The AI operates in an autonomous **agent loop**: it receives a user request, plans the steps, calls tools, inspects results, and iterates until the task is complete. Three modes are available:

- **Agent mode** — Full access to all 40+ tools. The AI can create, modify, connect, and delete nodes, set parameters, execute scripts, and save the scene.
- **Ask mode** — Read-only. The AI can only query scene structure, inspect parameters, search documentation, and provide analysis. All mutating tools are blocked by a `ToolRegistry` mode guard.
- **Plan mode** — The AI enters a planning phase: it researches the current scene (read-only), clarifies requirements via `ask_question`, then generates a structured execution plan with DAG flow diagram. The user reviews and confirms before execution begins. An **auto-resume mechanism** detects premature AI termination and forces continuation until all steps are complete.

```
User request → AI plans → call tools → inspect results → call more tools → … → final reply
```

- **Multi-turn tool calling** — the AI decides which tools to call and in what order
- **Todo task system** — complex tasks are broken into tracked subtasks with live status updates
- **Streaming output** — real-time display of thinking process and responses
- **Extended Thinking** — native support for reasoning models (DeepSeek-R1, GLM-4.7, Claude with `<think>` tags)
- **Stop anytime** — interrupt the running agent loop at any point
- **Smart context management** — round-based conversation trimming that never truncates user/assistant messages, only compresses tool results
- **Long-term memory** — brain-inspired three-layer memory system (episodic, semantic, procedural) with reward-driven learning and automatic reflection
- **Plugin system** — external community extensions via `plugins/` directory with hook events, custom tools, UI buttons, and settings
- **User Rules** — Cursor-style persistent context rules that are automatically injected into every AI request

### Supported AI Providers

| Provider | Models | Notes |
|----------|--------|-------|
| **DeepSeek** | `deepseek-v4-flash`, `deepseek-v4-pro`, `deepseek-chat`*, `deepseek-reasoner`* | V4: explicit thinking param + reasoning_effort; *old models deprecated 2026/07/24 |
| **GLM (Zhipu AI)** | `glm-4.7` | Stable in China, native reasoning & tool calling |
| **OpenAI** | `gpt-5.2`, `gpt-5.3-codex` | Powerful, full Function Calling & Vision support |
| **Ollama** (local) | `qwen2.5:14b`, any local model | Privacy-first, auto-detects available models |
| **Duojie** (relay) | `claude-opus-4-6-gemini`, `claude-opus-4-6-max`, `claude-sonnet-4-5`, `claude-sonnet-4-6`, `gemini-3-flash`, `gemini-3.1-pro`, `glm-5-turbo`, `glm-5.1`, `MiniMax-M2.7`, `MiniMax-M2.7-highspeed` | Claude, Gemini, GLM, MiniMax via relay endpoint |
| **OpenRouter** | `claude-sonnet-4.6`, `claude-opus-4.6`, `gpt-5.2`, `gemini-2.5-pro`, `deepseek-r1`, `grok-4.1-fast`, `llama-4-maverick`, `qwen3-235b` + 8 more | 16 models from all major providers via single API key |
| **Custom** | User-configurable | Any OpenAI-compatible endpoint (LM Studio, vLLM, etc.); configurable URL, API Key, model name, context limit, vision & FC support |

### Vision / Image Input

- **Multimodal messages** — attach images (PNG/JPG/GIF/WebP) to your messages for vision-capable models
- **Paste & drag-drop** — `Ctrl+V` paste from clipboard, drag image files into the chat input
- **File picker** — click the "Img" button to select images from disk
- **Image preview** — thumbnails displayed above the input box before sending, with remove buttons; **click any thumbnail to enlarge** in a full-size preview dialog
- **Model-aware** — automatically checks if the current model supports vision; non-vision models show a clear warning
- Supported: OpenAI GPT-5.2/5.3, Claude (all variants), Gemini

### Dark UI

- Modern warm khaki dark theme with glassmorphism effects
- Collapsible blocks for thinking process, tool calls, and results
- Dedicated **Python Shell** and **System Shell** widgets with syntax highlighting
- **Clickable node paths** — paths like `/obj/geo1/box1` in AI responses become links that navigate to the node in Houdini
- **Node context bar** showing the currently selected Houdini node
- **Todo list** displayed above the chat area with live status icons
- **Token analytics** — real-time token count, reasoning tokens, cache hit rate, and per-model cost estimates (click for detailed breakdown)
- **AuroraBar** — animated silver-white flowing gradient bar during AI generation
- **Streaming VEX code preview** — real-time Cursor Apply-style code writing animation
- Multi-session tabs — run multiple independent conversations
- Copy button on AI responses
- `Ctrl+Enter` to send messages
- **Font scaling** — `Ctrl+=`/`Ctrl+-` to zoom, "Aa" button for slider control
- **Bilingual UI** — Chinese/English language switching via overflow menu, with all UI elements and system prompts dynamically retranslated
- **Update notification banner** — lightweight banner above the input area when a new version is detected, with "Update Now" and dismiss buttons
- **Plugin Manager** — tabbed dialog with Plugins, Tools, and Skills management (enable/disable, reload, settings)
- **Rules Editor** — dialog for creating and managing persistent user context rules
- **Memory Manager** — dialog for browsing, editing, and exporting long-term semantic memories
- **PySide2 IME support** — full Chinese/Japanese/Korean input method support on both Windows and macOS

## Available Tools (40+)

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

### Visualization

| Tool | Description |
|------|-------------|
| `capture_viewport` | Screenshot the 3D viewport — returns base64 JPEG for vision models, or saves to file; configurable resolution (up to 1920×1080) |

### Code Execution

| Tool | Description |
|------|-------------|
| `execute_python` | Run Python code in the Houdini Python Shell (`hou` module available), with stop-event protection and 30s timeout |
| `execute_shell` | Run system shell commands (pip, git, ssh, scp, ffmpeg, etc.) with timeout, safety checks, and process tree kill protection |

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

### Long-term Memory

| Tool | Description |
|------|-------------|
| `search_memory` | Search the semantic memory store — retrieve relevant past experiences, rules, and strategies by category, abstraction level, and confidence scoring |

### Plan Mode

| Tool | Description |
|------|-------------|
| `create_plan` | Create a structured execution plan with phases, steps, dependencies, risk assessment, and DAG flow diagram — displayed as an interactive card for user review and confirmation |
| `update_plan_step` | Update the status and result summary of a plan step during execution |
| `ask_question` | Ask the user a clarification question during the planning phase (with options and recommendations) |

## Skills System

Skills are pre-optimized Python scripts that run inside the Houdini environment for reliable geometry analysis. They are preferred over hand-written `execute_python` for common tasks. Skills are automatically registered in the `ToolRegistry` as `skill:xxx` tools. Users can also add custom skills via a **user skill directory** (configurable in settings).

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
├── VERSION                          # Semantic version file (e.g. 1.5.5)
├── lib/                             # Bundled dependencies (requests, urllib3, certifi, tiktoken, …)
├── config/                          # Runtime config (auto-created, gitignored)
│   ├── houdini_ai.ini              # API keys & settings
│   ├── plugins.json                # Plugin enable/disable state, tool disable list, plugin settings
│   └── user_rules.json             # User-defined context rules (UI rules)
├── cache/                           # Conversation cache, doc index, HIP previews
│   └── plans/                      # Plan mode data files (plan_{session_id}.json)
├── rules/                           # File-based user rules (*.md, *.txt auto-loaded)
├── plugins/                         # Community plugins directory
│   ├── __init__.py                 # Plugin package marker
│   ├── _example_plugin.py          # Example plugin template (not auto-loaded, starts with _)
│   └── PLUGIN_DEV_GUIDE.md         # Plugin development documentation
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
    │   ├── cursor_widgets.py      # UI widgets (theme, chat blocks, todo, shells, token analytics, plan viewer, plugin manager, rules editor)
    │   ├── header.py              # HeaderMixin — top settings bar (provider, model, toggles, Custom provider dialog)
    │   ├── input_area.py          # InputAreaMixin — input area, mode switches, @mention, confirm mode
    │   ├── chat_view.py           # ChatViewMixin — chat display, scrolling, toast messages
    │   ├── i18n.py                # Internationalization — bilingual support (Chinese/English)
    │   ├── theme_engine.py        # QSS template rendering & font-size scaling
    │   ├── font_settings_dialog.py # Font zoom slider dialog
    │   ├── memory_manager_dialog.py # Memory system UI — browse, edit, delete, export memories
    │   └── style_template.qss    # Centralized QSS theme stylesheet
    ├── skills/                     # Pre-built analysis scripts (auto-registered as skill:xxx tools)
    │   ├── __init__.py            # Skill registry, loader & ToolRegistry integration
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
        ├── updater.py             # Auto-updater (GitHub Releases, ETag caching, notification banner)
        ├── plan_manager.py        # Plan mode data model & persistence
        ├── hooks.py               # Plugin hook system (HookManager, PluginContext, PluginLoader, decorator API)
        ├── tool_registry.py       # Unified ToolRegistry — centralizes core/skill/plugin/user tools
        ├── rules_manager.py       # User Rules manager (UI rules + file rules, prompt injection)
        ├── memory_store.py        # Three-layer memory (episodic/semantic/procedural) with SQLite
        ├── embedding.py           # Local text embedding (sentence-transformers / fallback)
        ├── reward_engine.py       # Reward scoring & memory importance updates
        ├── reflection.py          # Rule-based + LLM deep reflection module
        ├── growth_tracker.py      # Growth metrics & personality trait formation
        └── mcp/                   # Houdini MCP (Model Context Protocol) layer
            ├── client.py          # Tool executor (node ops, shell, skills dispatch, ToolRegistry fallback)
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
- **Windows / macOS** (both tested), Linux support possible

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

### Plan Mode

Plan mode enables the AI to tackle complex tasks through a structured three-phase workflow:

1. **Deep Research** — Read-only scene investigation using query tools
2. **Clarify Requirements** — Interactive Q&A with the user via `ask_question` when ambiguity exists
3. **Structured Plan** — Generate an engineering-grade execution plan with phases, steps, dependencies, risk assessment, and estimated operations

The plan is displayed as an interactive `PlanViewer` card with a DAG flow diagram. The user can review each step's details, approve/reject the plan, and monitor execution progress. Plan data is persisted to `cache/plans/plan_{session_id}.json`.

### Brain-inspired Long-term Memory System

A five-module system that enables the agent to learn and improve over time:

| Module | Description |
|--------|-------------|
| `memory_store.py` | Three-layer SQLite storage — **Episodic** (specific task experiences), **Semantic** (abstracted rules from reflection), **Procedural** (problem-solving strategies with priority) |
| `embedding.py` | Local text embedding using `sentence-transformers/all-MiniLM-L6-v2` (384-dim) with fallback to character n-gram pseudo-vectors |
| `reward_engine.py` | Dopamine-inspired reward scoring — success, efficiency, novelty, error penalty; drives memory importance strengthening/weakening with time decay |
| `reflection.py` | Hybrid reflection — rule-based extraction after every task + periodic LLM deep reflection to generate semantic rules and strategy updates |
| `growth_tracker.py` | Rolling-window metrics (error rate, success rate, tool call efficiency) + personality trait formation (efficiency bias, risk tolerance, verbosity, proactivity) |

Memory is activated at query time: relevant episodic memories, semantic rules, and procedural strategies are retrieved via cosine similarity and injected into the system prompt.

### Plugin System

The agent supports external community extensions via a plugin architecture:

- **HookManager** (singleton) — manages event registration and dispatch with priority ordering
- **PluginLoader** — scans the `plugins/` directory for `.py` files, auto-loads enabled plugins
- **PluginContext** — API object passed to each plugin's `register(ctx)` function, providing:
  - `ctx.on(event, callback)` — register event hooks
  - `ctx.register_tool(name, description, schema, handler)` — register custom AI-callable tools
  - `ctx.register_button(icon, tooltip, callback)` — add toolbar buttons
  - `ctx.insert_chat_card(widget)` — insert custom UI into the chat area
  - `ctx.get_setting(key)` / `ctx.set_setting(key, value)` — persistent per-plugin settings
- **Decorator API** — `@hook`, `@tool`, `@ui_button` decorators for declarative plugin development
- **7 hook events**: `on_before_request`, `on_after_response`, `on_before_tool`, `on_after_tool`, `on_content_chunk`, `on_session_start`, `on_session_end`
- **Plugin Manager UI** — tabbed dialog (Plugins / Tools / Skills) for enabling/disabling plugins, managing tool visibility, configuring user skill directories, and editing plugin settings

Plugins are managed via `config/plugins.json`. See `plugins/PLUGIN_DEV_GUIDE.md` for development documentation.

### ToolRegistry

A centralized tool management system that unifies three capability sources:

| Source | Description |
|--------|-------------|
| **Core** | Built-in Houdini tools (40+), dispatched by MCP Client |
| **Skill** | Pre-built analysis scripts, auto-registered as `skill:xxx` |
| **Plugin** | Community plugin tools, registered via `PluginContext.register_tool()` |

Key features:
- **Mode-based access control** — tools are tagged with allowed modes (`agent`, `ask`, `plan_planning`, `plan_executing`); mode guards automatically filter tool availability
- **Tag classification** — tools auto-tagged as `readonly`, `geometry`, `network`, `system`, `docs`, `skill`, `task`, `plugin` for fine-grained filtering
- **Enable/disable** — individual tools can be toggled on/off via the Plugin Manager UI; state persisted to `config/plugins.json`
- **User skill directory** — users can point to a custom directory of skill scripts, which are auto-loaded and registered
- **Thread-safe** — all registration/query operations are lock-protected

### User Rules (Custom Context)

Similar to Cursor Rules, users can define persistent context that is automatically injected into every AI request:

- **UI Rules** — created and managed via the Rules Editor dialog; stored in `config/user_rules.json`; individually enable/disable
- **File Rules** — `.md` and `.txt` files placed in the `rules/` directory are auto-loaded (files starting with `_` are treated as drafts and excluded)
- **Prompt injection** — all enabled rules are merged and wrapped in `<user_rules>` tags, injected into the system prompt
- Rules Editor features a warm khaki theme matching the main UI, with a list/editor split layout and empty state guidance

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
- **macOS crash prevention** — removed dangerous `QApplication.processEvents()` from `BlockingQueuedConnection` slots to prevent reentrant crashes; main-thread assertions added for debugging
- Tool execution timeout set to 60 seconds to accommodate long-running operations

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

### Internationalization (i18n)

- **Bilingual support** — full Chinese/English interface with `tr()` translation function
- **Dynamic switching** — change language via overflow menu → Language; all UI elements, tooltips, and system prompts update instantly
- **Persistent preference** — language choice saved via `QSettings` and restored on startup
- **System prompt adaptation** — AI reply language enforced via system prompt rules that adapt to the selected UI language

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
- The plugin checks GitHub on startup (silently) and shows an **update notification banner** above the input area if a new version is available
- One-click "Update Now" from the banner or toolbar button
- Updates preserve your `config/`, `cache/`, `trainData/`, `plugins/`, and `rules/` directories
- After updating, the plugin restarts automatically

## Version History

- **v1.5.5** — **DeepSeek V4 API adaptation + JSON Output**: New `deepseek-v4-flash` / `deepseek-v4-pro` models with explicit `thinking` parameter and `reasoning_effort` support. Old models (`deepseek-chat` / `deepseek-reasoner`) retained for compatibility (deprecated 2026/07/24). Default model migrated to `deepseek-v4-flash`. `chat_stream()` / `chat()` gain `response_format` parameter; reflection module uses `json_object` mode for reliable JSON output. V4 model pricing, context limits, and feature configs added.
- **v1.5.4** — **Long-term memory global toggle**: Added enable/disable switch for the entire memory system. Multiple fixes.
- **v1.5.3** — **Memory Manager dialog**: New `MemoryManagerDialog` UI for browsing, editing, deleting, and exporting semantic memories. `/memories` command support.
- **v1.5.2** — **Progress UX & banner release notes**: Updated progress indicators and update notification banner content.
- **v1.5.1** — **Wrangle run_over fix**: Fixed wrangle `class` (run_over) mapping to match Houdini parameter menu values.
- **v1.5.0** — **Custom provider + capture_viewport**: New **Custom Model** provider — user-configurable URL, API Key, model name, context limit, vision & Function Calling support; works with any OpenAI-compatible endpoint (LM Studio, vLLM, etc.). New `capture_viewport` tool for visual verification — screenshots the 3D viewport as base64 JPEG for vision models, or saves to file for non-vision models. `execute_python` gains stop-event protection and 30s timeout. Intent-based tool filtering removed in Agent mode.
- **v1.4.3** — **Cook deadlock prevention**: Fixed cook-induced deadlock during agent tool execution by deferring Houdini scene evaluation.
- **v1.4.2** — **MCP client typing fix**: Minor type annotation fix in MCP client module.
- **v1.4.0** — **OpenRouter provider**: New OpenRouter integration with 16 models spanning Claude, GPT, Gemini, DeepSeek, Grok, Llama, Qwen, Mistral via single API key. Skill parameter type `float` mapped to JSON Schema `number`. Various stability fixes.
- **v1.3.4** — **ToolRegistry & plugin system overhaul**: New centralized `ToolRegistry` singleton unifying core tools, skills, and plugin tools with mode-based access control (`agent`/`ask`/`plan_planning`/`plan_executing`) and tag classification (`readonly`/`geometry`/`network`/`system`/`docs`/`skill`/`task`/`plugin`). Skills auto-registered as `skill:xxx` tools. User skill directory support (configurable in settings). Plugin Manager refactored to 3-tab UI (Plugins/Tools/Skills) with per-tool enable/disable toggles. Decorator API (`@hook`/`@tool`/`@ui_button`) now properly applied via `_apply_decorators`. MCP Client fallback dispatch to ToolRegistry for `skill:xxx` tools. Mode-based safety guards in `_execute_tool_with_todo` migrated to `ToolRegistry.is_tool_allowed_in_mode()`. macOS thread safety fix: removed `processEvents()` from `BlockingQueuedConnection` slot preventing reentrant crashes; added main-thread assertion; increased tool timeout to 60s. Rules Editor UI redesigned with `QStackedWidget` for empty/editor states, warm khaki theme, and polished layout.
- **v1.3.3** — **Plugin & IME fixes**: Fixed Plugin Manager "Open Plugins Folder" button (`import os` missing). Comprehensive PySide2 IME fix for macOS Chinese input — overrode `inputMethodQuery` to provide cursor rectangle/surrounding text/position to macOS NSTextInputClient; set `StrongFocus` policy and `ImhNone` hints; enhanced `focusInEvent` to force IME reactivation; added `commitString` fallback in `inputMethodEvent`. Warm khaki theme applied to Plugin Manager and Rules Editor dialogs (previously cold blue/gray). Added `PLUGIN_DEV_GUIDE.md` plugin development documentation.
- **v1.3.2** — **User Rules system**: Cursor-like custom context rules — UI rules via Rules Editor dialog (create/edit/delete/enable/disable, stored in `config/user_rules.json`) + file rules from `rules/` directory (`.md`/`.txt` auto-loaded). All enabled rules merged and injected as `<user_rules>` into system prompt. Rules integrated into system prompt construction pipeline.
- **v1.3.1** — **Plugin Hook System**: External community extension architecture — `HookManager` singleton with event registration/dispatch (7 events: before/after request, tool, content, session), `PluginLoader` scanning `plugins/` directory, `PluginContext` API (hooks, tools, buttons, settings, chat cards), external tool registration (AI Function Calling), `PluginManagerDialog` with enable/disable/reload/settings UI, `PluginSettingsPage` with auto-generated forms from schema, decorator API (`@hook`/`@tool`/`@ui_button`), example plugin template, glassmorphism styles, i18n support.
- **v1.3.0** — **Plan mode auto-resume**: Fixed AI prematurely terminating Plan execution. Added `on_plan_incomplete` callback in `agent_loop_stream` — when AI returns pure text but plan has pending steps, injects a "resume" user message with latest plan progress, forcing continuation. Enhanced Plan execution prompt with strict "never stop early" discipline. `update_plan_step` returns richer progress summary (e.g. "3/10 steps completed"). Max 3 resume attempts per session to prevent infinite loops.
- **v1.2.9** — **Update notification & Plan DAG fix**: New `UpdateNotificationBanner` widget — lightweight banner above input area (not in chat flow) with "Update Now" and dismiss buttons. Plan mode DAG architecture diagram now displays fully without height cap (removed 400px `setFixedHeight` limit).
- **v1.2.8** — **Undo snapshot fix**: Fixed container node undo — child nodes now fully restored by calling `createNode` with `run_init_scripts=False` and `load_contents=False`, then recursively restoring children from snapshot. Fixed "Undo All" creating duplicate nodes — removed redundant double-execution in `_undo_all_ops`.
- **v1.2.7** — **PySide2 IME support**: Enabled Chinese input method in `ChatInput` — explicitly set `WA_InputMethodEnabled`, track IME composing state via `_ime_composing` flag, prevent `keyPressEvent` from intercepting IME candidate confirmation.
- **v1.2.6** — **Streaming content fix**: Fixed multi-turn agent loop content sticking together — preserved newline-only chunks in streaming pipeline, auto-injected `\n\n` separators between AI iterations.
- **v1.2.5** — **README & Release update**: Comprehensive README overhaul — documented Plan mode (3 tools: `create_plan`, `update_plan_step`, `ask_question`; interactive PlanViewer with DAG flow diagram), brain-inspired long-term memory system (5 modules: memory store, embedding, reward engine, reflection, growth tracker), bilingual i18n system, updated tool count to 38+, expanded Duojie model list (13 models including Claude, Gemini, GLM, Kimi, MiniMax, Qwen), updated project structure with all new files, and added architecture sections for Plan Mode, Memory System, and i18n.
- **v1.2.4** — **Modern UI: warm khaki theme & compact layout**: Visual refresh — CursorTheme palette shifted to warm khaki tones with pill-style toggles. Header and input area redesigned for compact single-line layout. Provider/model selectors, Web/Think toggles, and overflow menu consolidated into one row. Hidden buttons moved to overflow menu for cleaner appearance.
- **v1.2.3** — **Bilingual i18n system & temperature tuning**: Full internationalization — `i18n.py` module with `tr()` function, 800+ translation entries for Chinese and English. Language toggle in overflow menu with instant UI retranslation (header, input area, session tabs, system prompts). Persistent language preference via QSettings. Temperature parameter tuning for different providers and models.
- **v1.2.2** — **Anthropic Messages protocol adapter & Think switch**: Full Anthropic Messages API compatibility layer for Duojie models (GLM-4.7, GLM-5) — complete message format conversion (system extraction, multimodal images, tool_use/tool_result blocks, strict role alternation), tool definition conversion (OpenAI function → Anthropic input_schema), streaming SSE parser with thinking/text/tool_use delta handling, and non-streaming fallback. New `DUOJIE_ANTHROPIC_API_URL` endpoint and `_DUOJIE_ANTHROPIC_MODELS` registry for automatic protocol routing. **Think switch actually works**: `_think_enabled` flag now controls whether `<think>` block content and native `reasoning_content` are displayed — when Think toggle is off, thinking content is silently discarded instead of being shown. Applies to both XML `<think>` tag parsing and native reasoning fields (`reasoning_content`, `thinking_content`, `reasoning`). **Thinking field unification**: OpenAI protocol branch now checks 3 possible field names for thinking content across different providers. **New models**: `glm-4.7`, `glm-5` added to Duojie provider with 200K context and prompt caching support.
- **v1.2.1** — **Streaming VEX code preview**: New Cursor Apply-style real-time code preview — when AI writes VEX code via `create_wrangle_node` or `set_node_parameter`, a `StreamingCodePreview` widget shows the code being written character-by-character before execution. Built on a new `tool_args_delta` SSE event that broadcasts tool_call argument increments during streaming. Includes partial JSON parser to extract VEX code from incomplete JSON strings. Preview auto-dismissed when tool execution completes and replaced by `ParamDiffWidget`. **AIResponse height fix**: `_auto_resize_content` now counts visual lines via `block.layout().lineCount()` instead of `doc.size().height()`, fixing stale height during streaming. **ParamDiffWidget collapse redesign**: Multi-line diffs default to collapsed with 120px preview window (QScrollArea) instead of fully hidden — users see a preview without clicking.
- **v1.2.0** — **Glassmorphism UI overhaul**: Complete visual redesign — `CursorTheme` palette shifted from VS Code gray (`#1e1e1e`) to deep blue-black (`#0f1019`) with `rgba()` translucent borders and more vibrant accent colors. **AuroraBar**: New streaming animation widget — a 3px silver-white flowing gradient bar on the left side of AI responses during generation, freezing to faint silver on completion. **Input glow**: Sine-wave breathing border animation on the input field while AI is running. **Glass panel shadows**: `QGraphicsDropShadowEffect` on header and input panels for depth. **Agent/Ask mode dropdown**: Replaced dual-checkbox mutual exclusion with a `QComboBox` placed left of the input field, with dynamic color theming via QSS property selectors. **SimpleMarkdown color adaptation**: All inline HTML colors in headings, tables, links, lists, blockquotes updated to match the new blue-black palette. **QSS template rewrite**: 599 insertions / 500 deletions in `style_template.qss` for full theme adaptation.
- **v1.1.4** — **Centralized QSS theme system & font scaling**: Major UI architecture refactor — all inline `setStyleSheet()` calls across 7 files replaced with `setObjectName()` selectors, now controlled by a single `style_template.qss` (1497 lines). New `ThemeEngine` manages QSS template rendering with font-size scaling tokens (`{FS_BODY}`, `{FS_SM}`, etc.). **Font zoom**: `Ctrl+=`/`Ctrl+-` to zoom in/out, `Ctrl+0` to reset, plus "Aa" button in header opens `FontSettingsDialog` with real-time slider preview. Scale preference persisted via `QSettings`. **Dynamic state styling**: Context label, key status, optimize button use QSS property selectors (`[state="warning"]`, `[state="critical"]`) instead of runtime `setStyleSheet` calls. **CursorTheme cleanup**: Removed direct `CursorTheme` imports from `main_window.py`, `session_manager.py`, `chat_view.py` — styles now fully QSS-driven.
- **v1.1.3** — **Updater ETag caching**: Auto-updater now uses HTTP ETag conditional requests — 304 responses don't count against GitHub API rate limits. New `cache/update_cache.json` stores ETag and release data. On 403 rate-limit or network errors, gracefully degrades to cached release data instead of failing. Better version parsing error handling. Removed premature `theme.py` (design tokens not yet integrated).
- **v1.1.2** — **Node layout tools**: New `layout_nodes` tool with 3 strategies — `auto` (smart, uses NetworkEditor.layoutNodes or moveToGoodPosition), `grid` (fixed-width grid arrangement), `columns` (topological depth-based column layout with adjustable spacing). New `get_node_positions` for querying node coordinates. **Layout workflow rule**: System prompt enforces execution order: create nodes → connect → verify_and_summarize → layout_nodes → create_network_box (layout must happen before NetworkBox because fitAroundContents depends on node positions). **Widget flash fix**: `CollapsibleSection` and `ParamDiffWidget` now call `setVisible` after `addWidget` to prevent parentless widget flash-as-window artifacts.
- **v1.1.1** — **English system prompt & bare node name auto-resolution**: System prompt fully rewritten in English for better multi-model compatibility (Chinese reply enforced via `CRITICAL: You MUST reply in Simplified Chinese`). **Bare node name auto-resolution**: New `_resolve_bare_node_names()` post-processor automatically replaces bare node names (e.g. `box1`) in AI replies with full absolute paths (e.g. `/obj/geo1/box1`) using a session-level node path map collected from tool results. Safety rules: only replaces names ending with digits, only when unique path mapping exists, skips code blocks, skips existing path components. **Labs catalog English labels**: Category names in `doc_rag.py` switched to English. **NetworkBox grouping threshold**: Raised to 6+ nodes per box; smaller groups are left ungrouped to reduce clutter.
- **v1.1.0** — **Performance profiling & expanded knowledge**: New `perf_start_profile` / `perf_stop_and_report` tools for precise Houdini perfMon-based cook-time and memory profiling. New `analyze_cook_performance` skill for quick network-wide cook-time ranking and bottleneck detection without perfMon. **Expanded knowledge bases**: 5 new domain-specific knowledge bases — SideFX Labs (301KB, with auto-injected node catalog in system prompt), HeightFields/Terrain (249KB), Copernicus/COP (87KB), MPM solver (91KB), Machine Learning (53KB); knowledge trigger keywords extended from VEX-only to all domains. **Labs catalog injection**: System prompt dynamically injects a categorized Labs node directory so the AI proactively recommends Labs tools for game dev, texture baking, terrain, procedural generation, etc. **Universal node-change detection**: `execute_python`, `run_skill`, `copy_node`, and other mutation tools now take before/after network snapshots to auto-generate checkpoint labels and undo entries — previously only `create_node` / `set_node_parameter` had this. **Connection port labels**: `get_network_structure` and all connection displays now show `input_label` (e.g. `First Input(0)`) alongside the index for clearer data-flow understanding. **Thinking section always expanded**: `ThinkingSection` defaults to expanded and stays open after finalization (user preference). **Obstacle collaboration rules**: System prompt now explicitly forbids the AI from abandoning a plan when encountering obstacles — instead it must pause, describe the blocker clearly, and request specific user action. **Performance optimization guidelines**: System prompt includes 6 common optimization strategies (cache nodes, avoid time-dependent expressions, VEX over Python SOP, reduce scatter counts, packed primitives, for-each loop audit). **Pending ops cleanup**: Chat clear now properly resets the batch operations bar and pending ops list.
- **v1.0.5** — **PySide2/PySide6 compatibility**: Unified `qt_compat.py` layer auto-detects PySide version; all modules import from this single source. `invoke_on_main()` helper abstracts `QMetaObject.invokeMethod`+`Q_ARG` (PySide6) vs `QTimer.singleShot` (PySide2). Supports Houdini 20.5 (PySide2) through Houdini 21+ (PySide6). **Streaming performance fix**: `AIResponse.content_label` switched from `QLabel.setText` (O(n) full re-render) to `QPlainTextEdit.insertPlainText` (O(1) incremental append) — eliminates long-reply streaming stutter. Dynamic height auto-resize via `contentsChanged` signal. Buffer flush threshold raised to 200 chars / 250ms. **Image content stripping**: New `_strip_image_content()` in `AIClient` strips base64 `image_url` from older messages to prevent 413 context overflow; integrated into `_progressive_trim` (level-aware: keeps 2→1→0 recent images) and `agent_loop_auto`/`agent_loop_json_mode` (pre-strip for non-vision models). **Cursor-style image lifecycle**: Only the current round's user message retains images for vision models; all older rounds are automatically stripped to plain text. **@-mention keyboard navigation**: Up/Down arrows navigate the completer list; Enter/Tab select; Escape closes; mouse click and focus-out auto-dismiss the popup. **Token Analytics**: Records now displayed newest-first (reversed order). **DeepSeek context limit**: Updated from 64K→128K for both `deepseek-chat` and `deepseek-reasoner`. **Wrangle class mapping**: System prompt now documents run_over class integer values (0=Detail, 1=Primitives, 2=Points, 3=Vertices, 4=Numbers) for `set_node_parameter`. **Progressive trim tuning**: Level 2 keeps 3 rounds (was 5); level 3 keeps 2 rounds (was 3); `isinstance(c, str)` guard prevents crashing on multimodal tool content.
- **v1.0.4** — **Mixin architecture**: `ai_tab.py` decomposed into 5 focused Mixin modules (`HeaderMixin`, `InputAreaMixin`, `ChatViewMixin`, `AgentRunnerMixin`, `SessionManagerMixin`) for better maintainability. **NetworkBox tools**: 3 new tools — `create_network_box` (semantic color presets: input/processing/deform/output/simulation/utility, auto-include nodes), `add_nodes_to_box`, `list_network_boxes`; `get_network_structure` enhanced with `box_name` drill-in and overview mode that auto-folds boxes to save tokens. **NetworkBox grouping rules**: System prompt requires AI to organize nodes into NetworkBoxes after each logical stage (min 6 nodes per group), with hierarchical navigation guidelines. **Confirm mode**: `AgentRunnerMixin` adds confirmation dialog for destructive tools (create/delete/modify) before execution. **ThinkingSection overhaul**: Switched from `QLabel` to `QPlainTextEdit` with scrollbar, dynamic height calculation matching `ChatInput` approach, max 400px. **PulseIndicator**: Animated opacity-pulsing dot widget for "in progress" status. **ToolStatusBar**: Real-time tool execution status display below input area. **NodeCompleterPopup**: `@`-mention autocomplete for node paths. **Updater refactored**: Now uses GitHub Releases API (not branch-based VERSION file), cached `zipball_url`. **Training data exporter**: Multimodal content extraction (strips images, keeps text from list-format messages). **Module reload**: All Mixin modules added to reload list; `MainWindow` reference refreshed after reload; `deleteLater()` on old window for clean teardown.
- **v1.0.3** — **Agent / Ask mode**: Radio-style toggle below the input area — Agent mode has full tool access; Ask mode restricts to read-only/query tools with a whitelist guard and system prompt constraint. **Undo All / Keep All**: Batch operations bar tracks all pending node/param changes; "Undo All" reverts in reverse order, "Keep All" confirms everything at once. **Deep thinking framework**: `<think>` tag now requires a structured 6-step process (Understand → Status → Options → Decision → Plan → Risk) with explicit thinking principles. **Auto-updater**: `VERSION` file for semver tracking; silent GitHub API check on startup; one-click download + apply + restart with a progress dialog; preserves `config/`, `cache/`, `trainData/` during update. **`tools_override`**: `agent_loop_stream` and `agent_loop_json_mode` accept custom tool lists for mode-specific filtering. ParamDiff defaults to expanded. Skip undo snapshot when parameter value is unchanged.
- **v1.0.2** — **Parameter Diff UI**: `set_node_parameter` now shows inline red/green diff for scalar changes and collapsible unified diff for multi-line VEX code, with one-click undo to restore old values (supports scalars, tuples, and expressions). **User message collapse**: messages longer than 2 lines auto-fold with "expand / collapse" toggle. **Scene-aware RAG**: auto-retrieval query enriched with selected node types from Houdini scene; dynamic `max_chars` (400/800/1200) based on conversation length. **Persistent HTTP Session**: `requests.Session` with connection pooling eliminates TLS renegotiation per turn. **Pre-compiled regex**: XML tag cleanup patterns compiled once at class level. **Sanitize dirty flag**: skip O(n) message sanitization when no new tool messages are added. **Removed inter-tool delays** (`time.sleep` eliminated between Houdini tool executions).
- **v1.0.1** — **Image preview dialog**: click thumbnails to enlarge in a full-size modal viewer. **Stricter `<think>` tag enforcement**: system prompt now treats missing tags as format violations; follow-up replies after tool execution also require tags. **Robust usage parsing**: unified cache hit/miss/write metrics across DeepSeek, OpenAI, Anthropic, and Factory/Duojie relay formats (with one-time diagnostic dump). **Precise node path extraction**: `_extract_node_paths` now uses tool-specific regex rules to avoid picking up parent/context paths. **Multimodal token counting**: images estimated at ~765 tokens for accurate budget tracking. **Duojie think mode**: abandoned `reasoningEffort` parameter (ineffective), relies on `<think>` tag prompting only. Tool schema: added `items` type hint for array parameter values.
- **v1.0.0** — **Vision/Image input**: multimodal messages with paste/drag-drop/file-picker, image preview with thumbnails, model-aware vision check. **Wrangle run_over guidance** in system prompt (prevents wrong VEX execution context). **New models**: `gpt-5.3-codex`, `claude-opus-4-6-normal`, `claude-opus-4-6-kiro`. **Proxy tool_call fix**: robust splitting of concatenated `{...}{...}` arguments from relay services. **Legacy module cleanup** on startup.
- **v0.6.1** *(dev)* — Clickable node paths, token cost tracking (tiktoken + per-model pricing), Token Analytics Panel, smart parameter error hints, streamlined `verify_and_summarize` (built-in network check), duplicate call dedup, doc-check error suggestions, connection retry with backoff, updated model defaults (GLM-4.7, GPT-5.2, Gemini-3-Pro)
- **v0.6.0** *(dev)* — **Houdini Agent**: Native tool chain, round-based context trimming, merged `get_node_details` into `get_node_parameters`, Skills system (8 analysis scripts), `execute_shell` tool, local doc RAG, Duojie/Ollama providers, multi-session tabs, thread-safe tool dispatch, connection retry logic
- **v0.5.0** *(dev)* — Dark UI overhaul: dark theme, collapsible blocks, stop button, auto context compression, code highlighting
- **v0.4.0** *(dev)* — Agent mode: multi-turn tool calling, GLM-4 support
- **v0.3.0** *(dev)* — Houdini-only tool (removed other DCC support)
- **v0.2.0** *(dev)* — Multi-DCC architecture
- **v0.1.0** *(dev)* — Initial prototype

## Author

KazamaSuichiku

## License

MIT
