# Houdini Agent — QML / Qt Quick UI (Mono Editorial)

Declarative rewrite of the Houdini Agent frontend. Replaces the PySide
QtWidgets god-class (`ui/ai_tab.py` + 16 mixins) with a QML view + a thin
Python bridge. **Backend (AIClient / HoudiniMCP / memory / plan) is untouched** —
the UI talks to it through one `Controller` seam.

## Run

Standalone live preview (no Houdini needed, uses mock conversation):
```
cd <repo root>            # C:\Users\Administrator\Desktop\Houdini-Agent
python -m houdini_agent.ui_qml.preview
```

Headless validation (loads QML offscreen, non-zero exit on QML error):
```
python -m houdini_agent.ui_qml.preview --selftest
```

Render to PNG (real fonts via software raster):
```
set QT_QPA_PLATFORM=windows
set QT_QUICK_BACKEND=software
python -m houdini_agent.ui_qml.preview --capture out.png
```
A reference render lives at `design-preview/qml_editorial_reference.png`.

## Layout

```
ui_qml/
  qml/
    Main.qml                  root: Header / SessionTabs / ContextBar / ChatView / Composer
    HAgent/
      qmldir                  module manifest (lists every component + Theme singleton)
      Theme.qml               *** single source of truth for all design tokens ***
      Pill.qml  MenuPopup.qml  reusable primitives (chip/toggle/tab, dropdown)
      Header.qml  SessionTabs.qml  ContextBar.qml  Composer.qml
      ChatView.qml            ListView; delegate dispatch by row type (user/ai/plan)
      MessageUser.qml  MessageAI.qml
      ThinkingBlock.qml  ExecBlock.qml  NodeOpRow.qml  CodeBlock.qml  ProseBlock.qml
      PlanCard.qml
  controller.py               ChatModel (QAbstractListModel) + Controller (QObject bridge)
  host.py                     create_view() -> QQuickWidget (embed in Houdini PySide tree)
  preview.py                  standalone launcher + mock data + selftest/capture
  fonts/                      (drop Fraunces / Newsreader / Space Mono / Noto Serif SC TTFs here)
```

## Design tokens

Everything visual is in `Theme.qml` (colors, syntax palette, square radii,
font families, scale-aware sizes). Re-theming = editing one file. `Theme.scale`
drives font zoom. This mirrors the chosen "Mono Editorial" web direction 1:1.

## Data model (ChatModel rows)

Each row: `{ "type": "user"|"ai"|"plan", "payload": {...} }`
- user  → `{ text }`
- ai    → `{ blocks: [...] }` where each block is one of:
  - `{ kind:"thinking", dur, text }`
  - `{ kind:"exec", label, tools:[{state:"ok"|"warn"|"run", name, arg, time, detail}] }`
  - `{ kind:"nodeop", badge, text, paths }`
  - `{ kind:"code", lang, html }`   (html = pre-highlighted rich text)
  - `{ kind:"prose", html }`
- plan  → `{ title, badge, steps:[{label,state,detail}], dag:[{name,kind}] }`

Streaming = mutate the last row's payload + `ChatModel.update_payload(row, payload)`.

## Houdini integration

```python
from houdini_agent.ui_qml.host import create_view
view = create_view(parent=some_qwidget)   # returns a QQuickWidget
layout.addWidget(view)
```
`create_view` registers font fallbacks, injects `controller` + `chatModel` as
context properties, and loads `Main.qml`. Targets PySide6 (Qt6); falls back to
PySide2 imports for Houdini <= 20.5.

## Status

Done: full Mono Editorial UI; **real agent loop wired** via `agent_session.py`
(AIClient.agent_loop_auto on a background thread) — streamed content/thinking,
`<think>`-tag splitting, tool calls rendered as exec rows, tool results
(ok/warn), Stop button, real provider/model menus (from the same model map +
QSettings preference as the old UI), Houdini tool execution marshalled to the Qt
main thread via a BlockingQueuedConnection (mirrors old RunMixin). `hou.*` only
runs on the main thread. Falls back to a simulated reply if the backend can't be
constructed (standalone preview).

Architecture: `Controller` (Qt) ←→ `AgentSession` (pure backend; owns AIClient +
HoudiniMCP + history + system prompt). Tool dispatch uses
`HoudiniMCP.execute_tool(name, args)` (the backend's generic dispatcher).

Answer rendering: on stream end the final answer is parsed into markdown prose
(headings/lists/bold/italic/inline-code/links) + fenced code blocks, with code
syntax-highlighted in the Editorial palette (VEX/Python/generic) via
`Controller._highlight`. Thinking blocks show real elapsed time.

Node operations: mutating tools run inside a `hou.undos` group on the main
thread; the target network is snapshotted before/after to detect created/deleted
nodes (locale-independent), and `set_node_parameter` uses the mcp `_undo_snapshot`
for a param diff. Each change becomes a NodeOpRow (`+N` / `-N` / `~`) with
Keep/Undo — Undo really reverts (create→destroy, modify→restore param,
delete→native undo) via `Controller.undoNodeOp` on the main thread.

Plan mode (Composer → Plan): planning phase runs read-only tools + `create_plan`
→ a PlanCard row appears (steps + DAG + 驳回/确认执行); Confirm launches the
execution phase (full tools + `update_plan_step`) which drives step states live
(active/done/error); Reject cancels. Plan tools are intercepted in
`Controller._tool_executor` (never hit Houdini). `ask_question` is excluded for
now (no AskQuestionCard yet).

Multi-session: each tab is an independent conversation (its own ChatModel rows +
AgentSession history). SessionTabs is controller-driven (`sessionItems()` +
`sessionsChanged`); switch/new snapshot the active session and swap. Persisted to
`cache/qml_sessions/` (manifest.json + session_<id>.json holding rows + history)
on each finished turn / switch / new; `Controller.restore()` reloads them at
startup (called from app.py before the view loads). Sessions auto-name from the
first user message. Separate dir from the legacy UI cache — no interference.

Also implemented:
- **Confirm mode** (⋯ menu → 执行前确认): dangerous tools show an inline ConfirmCard
  and block the worker on a queue until 取消/确认执行 (`resolveConfirm`).
- **ask_question** (Plan): AskQuestionCard with options; blocks until 提交
  (`resolveQuestion`). Re-enabled in planning tools.
- **RAG / memory**: `doc_rag.auto_retrieve` injected each turn; long-term memory
  (`get_memory_store` core+episodic) behind the ⋯ 长期记忆 toggle (default off).
- **Multimodal images**: Composer `+` → FileDialog → base64; sent as OpenAI
  vision content (`supports_vision`); "已附 N 图" indicator with clear.
- **Close tab** (× on tabs), **click node path to focus** (NodeOpRow + answer
  prose links → `focusNode`), **cook timing** (realtime cook measure + slow
  warning), **bundled fonts** (Fraunces/Newsreader/Space Mono in `fonts/`,
  loaded via `QFontDatabase.addApplicationFont`).

Overflow (⋯) menu restored to full parity with the old UI: API Key, Export chat,
Token analytics, Cache location, Check update, Font +/− (drives Theme.scale),
Rules editor, Plugins, Memory manager (existing QtWidgets dialogs launched from
the QML host), Confirm-mode / Long-term-memory / Realtime-cook toggles, Language
中文/English, Clear chat. MenuPopup supports separators.

Also done: **ParamDiff** red/green chips (old→new) on modify rows; **streaming
code** — the answer re-parses into prose+highlighted-code segments on every chunk
(code renders live, not only at end); **viewport image** — `_viewport_image` tool
results render as an ImageBlock; **token analytics** — usage accumulated per run,
opens the existing TokenAnalyticsPanel; **English i18n** — `Controller.tr` + UI_EN
map; QML chrome wrapped with a `loc()` helper bound to `controller.lang`.

Feature-complete vs the original plan.

Parity pass (everything else from the old QtWidgets UI):
- **Todo card** (add_todo/update_todo intercepted → TodoBlock).
- **Real-time token / context / cost** in the composer meta (accumulated per run).
- **Python / System shell** rich blocks (code + output, collapsible).
- **@ node-path completer** + **/ slash commands** in the input.
- **Image** drag-drop + clipboard paste + thumbnails + fullscreen preview.
- **Animated status line** (thinking/generating/tool/planning); **streaming code
  preview** (on_tool_args_delta); **Undo All / Keep All** batch bar.
- **Update banner**; **Custom provider** config;
  **Compress context**; code-block **＋Wrangle**; **node-path links** (incl. bare
  name resolution); **toast**; **window geometry** save/restore + hipFile save hook.

Not ported (intentional):
- **Plugin UI button mounting** — the plugin bridge injects QtWidgets QPushButtons,
  incompatible with the QML scene graph. Plugin *management* (enable/disable/settings)
  still works via the Plugins dialog; rendering plugin buttons would need a QML-native
  plugin button API.
- **Reflection / reward / growth write-back** (post-task learning) — memory is *read*
  (RAG + core/episodic injection) but not *written* yet; needs the memory subsystem's
  write API wired in. Long-term-memory toggle gates the read path today.
