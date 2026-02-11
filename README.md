# Houdini Agent

**[English](README.md)** | **[中文](README_CN.md)**

An AI-powered assistant for SideFX Houdini, featuring autonomous multi-turn tool calling, web search, VEX/Python code execution, and a minimal dark UI.

Built on the **OpenAI Function Calling** protocol, the agent can read node networks, create/modify/connect nodes, run VEX wrangles, execute system shell commands, search the web, and query local documentation — all within an iterative agent loop.

## Core Features

### Agent Loop

The AI operates in an autonomous **agent loop**: it receives a user request, plans the steps, calls tools, inspects results, and iterates until the task is complete.

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

## Available Tools (30+)

### Node Operations

| Tool | Description |
|------|-------------|
| `create_wrangle_node` | **Priority tool** — create a Wrangle node with VEX code (point/prim/vertex/volume/detail) |
| `create_node` | Create a single node by type name |
| `create_nodes_batch` | Batch-create nodes with automatic connections |
| `connect_nodes` | Connect two nodes (with input index control) |
| `delete_node` | Delete a node by path |
| `copy_node` | Copy/clone a node to the same or another network |
| `set_node_parameter` | Set a single parameter value (with smart error hints — suggests similar parameter names on failure) |
| `batch_set_parameters` | Set the same parameter across multiple nodes |
| `set_display_flag` | Set display/render flags on a node |
| `save_hip` | Save the current HIP file |
| `undo_redo` | Undo or redo operations |

### Query & Inspection

| Tool | Description |
|------|-------------|
| `get_network_structure` | Get the full node network topology (names, types, connections, embedded VEX code) |
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
│   ├── nodes.zip                   # Node docs index (wiki markup)
│   ├── vex.zip                     # VEX function docs index
│   └── hom.zip                     # HOM class/method docs index
├── shared/                          # Shared utilities
│   └── common_utils.py             # Path & config helpers
├── trainData/                       # Exported training data (JSONL)
└── houdini_agent/                   # Main module
    ├── main.py                     # Module entry & window management
    ├── shelf_tool.py               # Houdini shelf tool integration
    ├── QUICK_SHELF_CODE.py         # Quick shelf code snippet
    ├── core/
    │   └── main_window.py          # Main window (workspace save/restore)
    ├── ui/
    │   ├── ai_tab.py              # AI Agent tab (agent loop, context management, streaming UI)
    │   └── cursor_widgets.py      # UI widgets (theme, chat blocks, todo, shells, token analytics)
    ├── skills/                     # Pre-built analysis scripts
    │   ├── __init__.py            # Skill registry & loader
    │   ├── analyze_normals.py     # Normal quality detection
    │   ├── analyze_point_attrib.py # Geometry attribute statistics
    │   ├── bounding_box_info.py   # Bounding box info
    │   ├── compare_attributes.py  # Attribute diff between nodes
    │   ├── connectivity_analysis.py # Connected components
    │   ├── find_attrib_references.py # Attribute usage search
    │   ├── find_dead_nodes.py     # Dead/orphan node finder
    │   └── trace_dependencies.py  # Dependency tree tracer
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
- **PySide6** (bundled with Houdini)
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

## Version History

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
