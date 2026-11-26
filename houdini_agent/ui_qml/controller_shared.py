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
import hashlib
import json
import os
import re
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

from houdini_agent.utils import model_catalog as catalog


ROLE_TYPE = Qt.UserRole + 1
ROLE_PAYLOAD = Qt.UserRole + 2

# Real provider keys + model ids (kept in sync with ui/header.py _model_map)
# 静态兜底取自 model_catalog；三份目录保持可变 —— refreshModels() 后台拉取
# 各 provider 的 /v1/models 后就地更新（custom / codex / claude_sub 不参与拉取）
MODEL_MAP = {k: list(v) for k, v in catalog.STATIC_MODELS.items()}
PROVIDER_LABELS = {
    "duojie": "Duojie", "openrouter": "OpenRouter", "openai": "OpenAI",
    "codex": "ChatGPT 订阅", "claude_sub": "Claude 订阅", "antigravity": "Google 反重力",
    "deepseek": "DeepSeek", "glm": "GLM", "custom": "Custom",
}
CONTEXT_LIMITS = dict(catalog.STATIC_CONTEXT)
VISION_MODELS = set(catalog.STATIC_VISION)
# tools safe to run off the Qt main thread (no hou.* access)
BG_SAFE = {"web_search", "fetch_webpage", "search_local_doc", "get_houdini_node_doc",
           "list_skills", "search_memory", "execute_shell"}

# 经 bridge 调 Houdini 侧工具时，若 Houdini 已关闭/断开（且自动扫描重连也失败），
# 返回这条而不是卡超时或抛原始 socket 错误——并直接告诉模型有哪些自救工具可用。
_BRIDGE_LOST_MSG = ("Houdini 连接已断开（可能已关闭 Houdini 或 Bridge 未运行），当前无法读取或修改场景。"
                    "可先调用 check_houdini_connection 排查原因，再按其 advice 用 repair_houdini_connection "
                    "修复（reconnect / reinstall_package / launch_houdini）。聊天与 Meshy 生成不受影响。")

# read-only tools available during the Plan planning phase
PLAN_READONLY = {"get_network_structure", "get_node_parameters", "list_children",
                 "get_node_inputs", "search_node_types", "semantic_search_nodes",
                 "read_selection", "check_errors", "search_local_doc",
                 "get_houdini_node_doc", "web_search", "fetch_webpage",
                 "search_memory", "list_skills", "capture_viewport",
                 "check_houdini_connection"}

try:
    from houdini_agent.utils.plan_manager import (
        PLAN_TOOL_CREATE, PLAN_TOOL_UPDATE_STEP, PLAN_TOOL_ASK_QUESTION)
except Exception:
    PLAN_TOOL_CREATE = PLAN_TOOL_UPDATE_STEP = PLAN_TOOL_ASK_QUESTION = None

# tools that require user approval when confirm mode is on
# （repair_houdini_connection 可能启动 Houdini 进程，确认模式下应征得同意）
CONFIRM_TOOLS = {"create_node", "create_nodes_batch", "create_wrangle_node", "delete_node",
                 "set_node_parameter", "batch_set_parameters", "connect_nodes", "copy_node",
                 "execute_python", "execute_shell", "save_hip", "run_skill",
                 "repair_houdini_connection", "transfer_nodes_between_instances"}

# Meshy 集成（自包含包，import 即自注册到 ToolRegistry）。
# 网络工具在 app 侧后台线程执行；生成类工具消耗 credits，纳入确认门。
try:
    from houdini_agent import meshy as _meshy
    MESHY_NETWORK_TOOLS = set(_meshy.NETWORK_TOOLS)
    MESHY_INTERACTIVE_TOOLS = set(_meshy.INTERACTIVE_TOOLS)
    MESHY_BATCH_TOOLS = set(_meshy.BATCH_TOOLS)
    MESHY_MUTATING = set(_meshy.MUTATING_TOOLS)
    MESHY_LOCAL_TOOLS = set(getattr(_meshy, "LOCAL_TOOLS", set()))
    CONFIRM_TOOLS = CONFIRM_TOOLS | set(_meshy.CONFIRM_TOOLS)
except Exception as _meshy_err:
    _meshy = None
    MESHY_NETWORK_TOOLS = set()
    MESHY_INTERACTIVE_TOOLS = set()
    MESHY_BATCH_TOOLS = set()
    MESHY_MUTATING = set()
    MESHY_LOCAL_TOOLS = set()
    print("[controller] meshy integration unavailable:", _meshy_err)

# 各 Meshy 工具的中文标签（卡片标题、后台任务通知用）
MESHY_LABELS = {
    "meshy_text_to_3d": "文生3D", "meshy_image_to_3d": "图生3D",
    "meshy_text_to_image": "文生图", "meshy_image_to_image": "图生图",
    "meshy_retexture": "重打材质", "meshy_remesh": "重拓扑",
    "meshy_concept_to_3d": "概念图转3D",
    "meshy_rig": "自动绑定", "meshy_animate": "套动作",
}

# Meshy 网页快捷入口。均为公开、面向用户的页面 URL（不含任何内部地址/机密）。
# QML 只传 key（home/workspace/apikey/pricing/docs），URL 集中在此一处便于维护。
_MESHY_UTM = "utm_source=houdini-agent&utm_medium=plugin"
_MESHY_URLS = {
    "home":      "https://www.meshy.ai",
    "workspace": "https://www.meshy.ai/workspace",
    "apikey":    "https://www.meshy.ai/settings/api",
    "pricing":   "https://www.meshy.ai/pricing",
    "docs":      "https://docs.meshy.ai",
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
    # Meshy 网页快捷入口
    "工作台": "Workspace", "充值": "Top up",
    "打开 Meshy 官网": "Open Meshy.ai", "我的工作台": "My Workspace",
    "充值 / 定价": "Top up / Pricing", "API Key 设置": "API Key settings",
    "API 文档": "API docs", "无法打开链接：": "Couldn't open link: ",
    "+ 生成": "+ Generate", "+ 用 Meshy 生成": "+ Generate with Meshy",
    "用 Meshy 生成一个 ": "Generate with Meshy: ", "去 Meshy 工作台": "Open Meshy Workspace",
    "在 Meshy 网页管理全部资产 →": "Manage all assets on Meshy →", "Meshy 定价": "Pricing",
    "登录 Meshy 同步你的资产": "Sign in to Meshy to sync your assets",
    "模型": "Model", "Nano Banana（默认 · 快）": "Nano Banana (default · fast)",
    "Nano Banana Pro（高质量）": "Nano Banana Pro (high quality)",
    # 生图模型设置（Meshy / Codex 订阅）
    "图片生成": "Image generation",
    "默认生图模型": "Default image model",
    "选择文生图、图生图与概念图默认使用的生图后端。登录 ChatGPT 订阅后可直接使用 Codex 生图模型，无需 Meshy API Key。":
        "Choose the default backend for text-to-image, image-to-image, and concept art. "
        "Sign in to your ChatGPT plan to use Codex image models without a Meshy API key.",
    "同步模型": "Sync models",
    "同步中…": "Syncing…",
    "自动同步自本机 ChatGPT 订阅（Codex）": "Auto-synced from your local ChatGPT plan (Codex)",
    "登录 ChatGPT 订阅后，即可自动同步并使用 Codex 生图模型（如 gpt-image-*）。":
        "Sign in to your ChatGPT plan to automatically sync and use Codex image models (e.g. gpt-image-*).",
    "登录 ChatGPT 订阅": "Sign in to ChatGPT plan",
    "Codex 生图模型已更新": "Codex image models updated",
    "同步 Codex 生图模型失败：": "Failed to sync Codex image models: ",
    "请先登录 ChatGPT 订阅后再选择 Codex 生图模型": "Sign in to your ChatGPT plan before choosing a Codex image model",
    # 多 Houdini 实例管理
    "刷新实例列表": "Refresh Houdini instances",
    "启动另一个 Houdini…": "Launch another Houdini…",
    "未连接 Houdini": "No Houdini connected",
    "没有已连接的 Houdini 实例": "No connected Houdini instances",
    "已切换到 ": "Switched to ",
    # 规则编辑器
    "新建": "New", "未命名": "Untitled", "打开规则目录": "Open rules folder",
    "还没有规则": "No rules yet",
    "规则会注入到每次对话、长期生效。点左上角「新建」创建第一条。":
        "Rules are injected into every chat and persist. Click New (top-left) to create your first.",
    "启用": "Enabled", "停用": "Disabled",
    "文件规则 · 只读": "File rule · read-only", "有未保存修改": "Unsaved changes", "UI 规则": "UI rule",
    "写下这条规则的内容…": "Write the rule content…",
    "当前规则有未保存修改，请先提交或取消": "This rule has unsaved changes — submit or cancel first",
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
    "重置": "Reset", "关闭": "Close", "稍后": "Later", "立即更新": "Update now",
    "立即重启": "Restart now", "立即重启更新": "Restart to update",
    "后台自动静默更新": "Silent background updates",
    "在后台静默下载并校验新版本，重启或重新打开应用即可自动生效，无需等待下载或安装向导。":
        "Download and verify new versions quietly in the background. Restart or reopen the app to apply instantly with no installer wizard.",
    "ChatGPT 订阅": "ChatGPT plan", "订阅登录": "Sign in", "浏览器登录": "Sign in with browser",
    "设备码登录": "Sign in with code", "退出登录": "Sign out", "取消登录": "Cancel sign-in",
    "刷新模型": "Refresh models", "复制": "Copy",
    "模型列表已更新": "Model list updated",
    "模型列表刷新失败，已沿用本地列表": "Failed to refresh models; using the local list",
    "刷新中…": "Refreshing…", "模型更新于": "Models updated at", "尚未自动刷新": "Not refreshed yet",
    "推理": "Effort", "低": "Low", "中": "Medium", "高": "High", "很高": "Very high",
    "最高": "Max", "极限": "Ultra", "默认": "default", "未登录": "signed out",
    "登录 ChatGPT 订阅…": "Sign in to ChatGPT plan…", "账号：": "Account: ",
    "请先登录 ChatGPT 订阅": "Sign in to your ChatGPT plan first",
    "登录后即可使用订阅额度，无需 API key。": "Sign in to use your plan's allowance. No API key needed.",
    "Claude 订阅": "Claude plan", "登录 Claude 订阅…": "Sign in to Claude plan…",
    "请先登录 Claude 订阅": "Sign in to your Claude plan first",
    "退出 Claude 订阅登录？": "Sign out of Claude plan?",
    "Google 反重力": "Google Antigravity", "登录 Google 反重力…": "Sign in to Google Antigravity…",
    "请先登录 Google 反重力": "Sign in to Google Antigravity first",
    "退出 Google 反重力登录？": "Sign out of Google Antigravity?",
    "Google 账号": "Google account",
    "已在浏览器打开登录页，登录并授权后会自动完成。": "The sign-in page is open in your browser. Sign in and authorize to finish.",
    "本机端口被占用，请关闭占用它的程序后重试：": "A local port is in use. Close the program using it and retry: ",
    "将删除这台电脑上保存的登录凭据：": "This removes the sign-in credentials saved on this computer: ",
    "Anthropic 不允许第三方工具使用 Claude 订阅，账号可能被限制，风险自负。":
        "Anthropic does not allow third-party tools to use Claude plans. Your account may be restricted; use at your own risk.",
    "订阅账号暂时不可用：通常是额度用完或被限流，等几分钟再试；如果一直这样，退出登录后重新登录。":
        "The subscription account is temporarily unavailable, usually because the quota is used up or rate-limited. "
        "Try again in a few minutes; if it persists, sign out and sign in again.",
    "上游连接在开始输出前断开了，一般是对方服务临时波动，重新发送即可。":
        "The upstream connection closed before any output, usually a temporary service hiccup. Just send again.",
    "用 ChatGPT Plus / Pro 订阅里的 Codex 额度驱动 Houdini Agent，不需要 API key。登录由本机运行的开源组件 CLIProxyAPI 完成，凭据只保存在这台电脑上。":
        "Use the Codex allowance in your ChatGPT Plus / Pro plan to power Houdini Agent, no API key needed. "
        "Sign-in is handled by CLIProxyAPI, an open-source component running on this computer; credentials stay on it.",
    "组件": "Component", "账号": "Account", "已安装": "installed", "ChatGPT 账号": "ChatGPT account",
    "未安装（首次登录时自动下载，约 24 MB）": "Not installed (downloaded on first sign-in, about 24 MB)",
    "打开页面": "Open page", "浏览器没有打开？": "Browser didn't open?", "复制登录链接": "Copy sign-in link",
    "再次打开": "Open again", "再登录一个账号": "Add another account",
    "用量计入你的 ChatGPT 订阅额度。OpenAI 目前允许在第三方工具里使用订阅，但这不是合同条款，政策可能变化。":
        "Usage counts against your ChatGPT plan. OpenAI currently allows plans in third-party tools, "
        "but this is not a contractual term and may change.",
    "正在下载订阅组件…": "Downloading the subscription component…",
    "已在浏览器打开 OpenAI 登录页，登录并授权后会自动完成。":
        "The OpenAI sign-in page is open in your browser. Sign in and authorize to finish.",
    "在浏览器打开的页面里输入下面的验证码，授权后会自动完成。":
        "Enter the code below on the page opened in your browser. It finishes once you authorize.",
    "正在准备订阅组件…": "Preparing the subscription component…", "正在发起登录…": "Starting sign-in…",
    "登录成功，正在启动本机订阅服务…": "Signed in, starting the local subscription service…",
    "已退出登录": "Signed out", "可用模型：": "Available models: ", "刷新模型失败：": "Failed to refresh models: ",
    "已登录 ": "Signed in as ", "已登录": "Signed in", "已取消登录": "Sign-in cancelled",
    "登录超时（15 分钟内未完成授权）": "Sign-in timed out (not authorized within 15 minutes)",
    "本机 1455 端口被占用（可能有 Codex 正在登录），请关闭后重试，或改用设备码登录":
        "Local port 1455 is in use (maybe another Codex sign-in). Close it and retry, or sign in with a code.",
    "登录没有完成": "Sign-in did not complete", "出错了：": "Error: ",
    "退出 ChatGPT 订阅登录？": "Sign out of ChatGPT plan?",
    "将删除这台电脑上保存的 ChatGPT 订阅登录凭据：": "This removes the ChatGPT plan credentials saved on this computer: ",
    "OpenAI 账号本身不受影响；要彻底撤销授权，请在 ChatGPT 网页的安全设置里移除 Codex。":
        "Your OpenAI account is not affected. To fully revoke access, remove Codex in ChatGPT's security settings on the web.",
    "请求次数": "Requests", "输入 token": "Input tokens", "输出 token": "Output tokens",
    "推理 token": "Reasoning tokens", "缓存命中": "Cache read", "缓存写入": "Cache write",
    "总计": "Total", "平均/请求": "Avg/request", "上下文": "Context",
    "Token 结构": "Token mix", "缓存效率": "Cache efficiency", "本轮统计": "Session stats",
    "开": "On", "关": "Off",
    "（可多选）": "(multi-select)", "已复制": "Copied",
    "删除": "Delete", "打开插件目录": "Open plugins folder",
    # 设置浮窗 + 会话管理（方案A）
    "设置…": "Settings…", "设置": "Settings", "常规": "General", "扩展": "Extensions",
    "外观与字号": "Appearance & font", "会话": "Sessions", "模型与 Provider": "Model & Provider",
    "规则": "Rules", "插件": "Plugins", "记忆": "Memory", "关于": "About",
    "搜索会话…": "Search sessions…", "没有匹配的会话": "No matching sessions",
    "删除会话": "Delete session", "此操作无法撤销。": "This can't be undone.",
    "确定删除会话": "Delete session", "吗？": "?",
    "以后删除不再询问（可在 设置 › 会话 中恢复）":
        "Don't ask again (re-enable in Settings › Sessions)",
    "删除会话前需确认": "Confirm before deleting a session",
    "关闭后删除会话不再弹确认框。在删除弹窗里勾选「不再询问」会自动关掉这里——随时可重新打开。":
        "When off, deleting a session won't prompt. Ticking \"don't ask again\" turns this off — re-enable anytime.",
    "每步操作需批准（确认模式）": "Approve each step (confirm mode)",
    "执行会修改场景或计费的工具前逐个弹确认卡。":
        "Prompt before each tool that changes the scene or spends credits.",
    "界面字号": "Interface font size",
    "调整整个面板的字号缩放。": "Scale the whole panel's font size.",
    "当前 Provider": "Current provider", "管理…": "Manage…", "查看余额": "Balance",
    "Meshy 账号": "Meshy account", "版本": "Version", "流式输出自动滚动": "Auto-scroll while streaming",
    "把现有面板嵌进这里。": "The existing panel will be embedded here.",
    "打开": "Open", "会话管理": "Sessions",
    "行为": "Behavior", "显示思考过程": "Show thinking",
    "在回答里显示模型的思考过程（<think> 内容）。": "Show the model's reasoning (<think>) in replies.",
    "执行修改场景的工具后实时重算（cook）。关闭可在批量操作时提速。":
        "Cook the scene live after scene-changing tools. Turn off to speed up batch ops.",
    "让助手跨会话记住你的偏好与项目信息。可在「记忆」分区查看与删除。":
        "Let the assistant remember your preferences and project info across chats. View & delete in the Memory section.",
    # 内嵌的规则/插件/记忆面板里用到的文案
    "编辑": "Edit", "浏览": "Browse", "选择用户脚本目录": "Choose user script folder",
    "用户脚本目录未设置，仅使用内置脚本": "No user script folder set — built-in scripts only",
    "插件设置": "Plugin settings", "这个插件没有可配置项": "This plugin has no configurable options",
    # 多供应商管理（模型设置）
    "管理自定义模型供应商，配置后可在聊天时选择使用。":
        "Manage custom model providers; once configured they can be picked in chat.",
    "内置供应商": "Built-in providers", "自定义供应商": "Custom providers",
    "添加供应商": "Add provider", "添加模型供应商": "Add model provider",
    "配置一个自定义 API 端点和它的模型。": "Configure a custom API endpoint and its models.",
    "名称": "Name", "API 格式": "API format", "模型列表": "Models",
    "添加模型": "Add model", "上下文窗口": "Context window", "图片": "Vision",
    "使用": "Use", "当前": "Active", "保存": "Save", "模型名": "Model name",
    "还没有自定义供应商": "No custom providers yet",
    "请填写名称": "Please enter a name", "请填写有效的 Base URL": "Enter a valid Base URL",
    "请至少添加一个模型": "Add at least one model", "无效数据": "Invalid data", "已保存": "Saved",
    # 外观系统
    "主题": "Theme", "强调色": "Accent", "字体方案": "Typeface", "界面密度": "Density",
    "深色三档，外加日光（浅色）。选日光会整体翻成浅色，强调色自动加深保证可读。":
        "Three dark tones plus Daylight (light). Daylight flips everything light and darkens the accent so it stays readable.",
    "选中态、指示点、用户气泡与按钮描边的颜色。":
        "Color for selection, dots, your message bubble and button outlines.",
    "标题 / 正文 / 等宽三件套的整体气质。": "The display / body / mono pairing for the whole UI.",
    "间距与行高的松紧，不改字号。": "How tight the spacing and line height feel — font size unchanged.",
    "极夜": "Noir", "石墨": "Graphite", "午夜蓝": "Midnight", "日光": "Daylight",
    "暖米": "Warm", "冷钢": "Steel", "青瓷": "Celadon", "暖砂": "Clay", "中性": "Neutral",
    "编辑体": "Editorial", "现代": "Modern", "等宽": "Mono",
    "紧凑": "Compact", "标准": "Standard", "宽松": "Roomy",
    # 起手式 / 模板库
    "起手式": "Starters", "先出一个模型，再接着做。": "Start with a model, then build on it.",
    "点下面的起手式填进输入框，参数改完直接发；也可以不用，自己输入。":
        "Tap a starter to drop it in the input, tweak the blanks, and send — or just type your own.",
    "Meshy · 3D 生成": "Meshy · 3D generation",
    "从文字、图片或概念图生成，直接进场景": "From text, an image or a concept — straight into the scene",
    "Houdini 程序化": "Houdini procedural", "有了模型，用 Houdini 接着做": "Got a model? Build on it in Houdini",
    "有了模型，还能：": "With a model, you can also: ", "都在": "all in", "模板库里": "the library",
    "浏览全部模板": "Browse all templates", "模板库": "Templates", "返回对话": "Back to chat",
    "新建模板": "New template", "插入": "Insert", "我的模板": "My templates",
    "内容": "Content", "分类": "Category",
    "想留个能改的空，就写 {{名称:默认值}}，插入时点一下就能改。":
        "Leave an editable blank with {{name:default}} — you can change it after inserting.",
    "比如：生成一个 {{物体:宝箱}}，{{风格:写实}}风格，导进场景。":
        "e.g. Generate a {{object:chest}}, {{style:realistic}} style, into the scene.",
    "例如：我的破碎参数": "e.g. My fracture settings", "例如：建模": "e.g. Modeling",
    "请填写模板内容": "Please add some content", "未命名模板": "Untitled template",
    "内容格式有误": "Malformed payload", "还没有模板，点右上「新建模板」。": "No templates yet — use New template.",
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


def _block_fp(v):
    """Structural fingerprint of a block: containers are copied, leaves are shared.
    Blocks are mutated in place (e.g. exec["tools"][i]["state"] = "ok"), so object
    identity alone can't tell what changed; comparing fingerprints with == can
    (leaf compare short-circuits on identity, so unchanged strings cost nothing)."""
    if isinstance(v, dict):
        return {k: _block_fp(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_block_fp(x) for x in v]
    return v


class BlockModel(QAbstractListModel):
    """Per-AI-message model of block dicts. Lives for the lifetime of the message
    so QML's Repeater never recreates all delegates — only changed/added/removed
    rows update (sync() applies the minimal diff against a working list)."""

    def __init__(self, blocks=None, parent=None):
        super().__init__(parent)
        self._b = list(blocks) if blocks else []
        self._fps = [_block_fp(b) for b in self._b]

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
        Common prefix (same dict identity) → dataChanged only for rows whose content
        actually changed (fingerprint diff) — re-pushing unchanged rows makes QML
        re-set and re-layout every block of the message on each streaming tick;
        differing suffix → remove old tail + insert new tail."""
        old = self._b
        p = 0
        while p < len(old) and p < len(new) and old[p] is new[p]:
            p += 1
        if len(old) > p:
            self.beginRemoveRows(QModelIndex(), p, len(old) - 1)
            del self._b[p:]
            del self._fps[p:]
            self.endRemoveRows()
        if len(new) > p:
            self.beginInsertRows(QModelIndex(), p, len(new) - 1)
            tail = list(new[p:])
            self._b.extend(tail)
            self._fps.extend(_block_fp(b) for b in tail)
            self.endInsertRows()
        start = None
        for i in range(p):
            fp = _block_fp(self._b[i])
            if fp != self._fps[i]:
                self._fps[i] = fp
                if start is None:
                    start = i
            elif start is not None:
                self.dataChanged.emit(self.index(start, 0), self.index(i - 1, 0), [ROLE_BLOCK])
                start = None
        if start is not None:
            self.dataChanged.emit(self.index(start, 0), self.index(p - 1, 0), [ROLE_BLOCK])


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


# ============================================================
# 起手式 / 模板库 内置模板（中英两份；{{名称:默认值}} 为可改填空格）
#   scratch=True  : 空会话可直接用（从零生成）
#   scratch=False : 需场景里已有模型（后处理），只进模板库不上空会话
# 能力边界：只放 Agent 能可靠执行的（无模拟缓存 / 无渲染——见 memory）。
# ============================================================
_TPL_MESHY = [
    {"sc": True, "ty_zh": "文生 3D", "ty_en": "Text → 3D", "t_zh": "文字出模型", "t_en": "From a prompt",
     "b_zh": "生成一个 {{物体:中世纪宝箱}}，{{风格:写实}}风格，做好导进场景。",
     "b_en": "Generate a {{object:medieval chest}}, {{style:realistic}} style, then bring it into the scene."},
    {"sc": True, "ty_zh": "图生 3D", "ty_en": "Image → 3D", "t_zh": "照参考图建模", "t_en": "From a reference",
     "note_zh": "记得先贴一张参考图", "note_en": "Attach a reference image first",
     "b_zh": "照这张参考图做个模型，导进场景。",
     "b_en": "Build a model from this reference image and bring it into the scene."},
    {"sc": True, "ty_zh": "概念 → 3D", "ty_en": "Concept → 3D", "t_zh": "先看概念图再选", "t_en": "Concept first, then pick",
     "b_zh": "先出几张 {{物体:石窟雕像}} 的概念图，我挑一张再转 3D。",
     "b_en": "Sketch a few concept images of a {{object:grotto statue}}, I'll pick one to turn into 3D."},
    {"sc": True, "ty_zh": "批量 · 变体", "ty_en": "Batch · variants", "t_zh": "一次出几版挑", "t_en": "A few to choose from",
     "b_zh": "{{物体:宝箱}} 出 4 个款式，我挑一个导进来。",
     "b_en": "Make 4 variants of a {{object:chest}} so I can pick one to import."},
    {"sc": True, "ty_zh": "余额", "ty_en": "Balance", "t_zh": "还够做几个", "t_en": "How much is left",
     "b_zh": "查下 Meshy 余额，还够生成几个模型？",
     "b_en": "Check my Meshy balance — how many models can I still generate?"},
    {"sc": False, "ty_zh": "贴图", "ty_en": "Texture", "t_zh": "重做贴图", "t_en": "Re-texture",
     "note_zh": "需要场景里已有模型", "note_en": "Needs a model already in the scene",
     "b_zh": "给选中的模型重做贴图，{{风格:做旧金属}}。",
     "b_en": "Re-texture the selected model, {{style:weathered metal}}."},
    {"sc": False, "ty_zh": "重拓扑", "ty_en": "Remesh", "t_zh": "减面 / 转四边", "t_en": "Reduce / to quads",
     "note_zh": "需要场景里已有模型", "note_en": "Needs a model already in the scene",
     "b_zh": "把选中模型重拓扑到 {{面数:1万}} 面上下，走四边面。",
     "b_en": "Remesh the selected model to around {{faces:10k}} faces, quad-dominant."},
    {"sc": False, "ty_zh": "绑定 · 动画", "ty_en": "Rig · animate", "t_zh": "人形绑骨", "t_en": "Rig a humanoid",
     "note_zh": "仅限有贴图的双足人形", "note_en": "Textured bipedal humanoids only",
     "b_zh": "给这个人形绑骨，套一个 {{动作:行走}} 循环。",
     "b_en": "Rig this humanoid and apply a {{action:walk}} loop."},
]
_TPL_HOU = [
    {"cat_zh": "建模", "cat_en": "Modeling", "t_zh": "程序化楼梯", "t_en": "Procedural stairs",
     "b_zh": "做一段程序化楼梯，{{台阶:12}} 阶，总高 {{高度:3米}}。",
     "b_en": "Build a procedural staircase, {{steps:12}} steps, {{height:3m}} tall."},
    {"cat_zh": "布局", "cat_en": "Layout", "t_zh": "地面散布", "t_en": "Scatter on ground",
     "b_zh": "地面上撒 {{数量:200}} 个 {{对象:石头}}，旋转和大小都打乱。",
     "b_en": "Scatter {{count:200}} {{object:rocks}} on the ground, randomize rotation and scale."},
    {"cat_zh": "布局", "cat_en": "Layout", "t_zh": "复制到点", "t_en": "Copy to points",
     "b_zh": "把选中的物体复制到 {{行列:10x10}} 网格的点上，带点随机。",
     "b_en": "Copy the selected object onto a {{grid:10x10}} grid of points, with some randomness."},
    {"cat_zh": "优化", "cat_en": "Optimize", "t_zh": "减面", "t_en": "Polyreduce",
     "b_zh": "把选中的几何减面，减到 {{目标:一半}} 左右。",
     "b_en": "Polyreduce the selected geometry to about {{target:half}}."},
    {"cat_zh": "灯光", "cat_en": "Lighting", "t_zh": "三点光", "t_en": "Three-point light",
     "b_zh": "搭一个三点光，主光、补光、轮廓光，都对着原点。",
     "b_en": "Set up a three-point light rig — key, fill, rim — all aimed at the origin."},
]


