# -*- coding: utf-8 -*-
"""
Hook 插件系统 (Plugin Hook System)

为 Houdini Agent 提供外部社区扩展能力：
  - HookManager: 单例，管理所有 hook 注册和事件分发
  - PluginLoader: 扫描 plugins/ 目录，加载插件模块
  - PluginContext: 传给每个插件的上下文对象（API 入口）
  - 装饰器 API: @hook, @tool, @ui_button

支持的事件:
  on_before_request  — AI API 请求前（可修改 messages）
  on_after_response  — AI 回复完成后
  on_before_tool     — 工具执行前
  on_after_tool      — 工具执行后
  on_content_chunk   — AI 输出文本 chunk
  on_session_start   — 新会话开始
  on_session_end     — 会话结束

插件约定:
  - 放在 plugins/ 目录下的 .py 文件
  - 以 _ 开头的文件不会被自动加载
  - 必须包含 PLUGIN_INFO dict 和 register(ctx) 函数
"""

from __future__ import annotations

import importlib.util
import json
import os
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


# ============================================================
# Hook 事件名称常量
# ============================================================

EVENT_BEFORE_REQUEST = "on_before_request"
EVENT_AFTER_RESPONSE = "on_after_response"
EVENT_BEFORE_TOOL = "on_before_tool"
EVENT_AFTER_TOOL = "on_after_tool"
EVENT_CONTENT_CHUNK = "on_content_chunk"
EVENT_SESSION_START = "on_session_start"
EVENT_SESSION_END = "on_session_end"

ALL_EVENTS = (
    EVENT_BEFORE_REQUEST,
    EVENT_AFTER_RESPONSE,
    EVENT_BEFORE_TOOL,
    EVENT_AFTER_TOOL,
    EVENT_CONTENT_CHUNK,
    EVENT_SESSION_START,
    EVENT_SESSION_END,
)


# ============================================================
# HookManager — 单例，管理所有 hook 注册和事件分发
# ============================================================

class HookManager:
    """全局 Hook 管理器（单例）

    - register / unregister: 注册 / 注销事件钩子
    - fire:        触发事件（通知型，不修改数据）
    - fire_filter: 管道式过滤（每个回调可修改并返回值）
    - register_tool / get_external_tools / execute_external_tool: 外部工具管理
    """

    _instance: Optional[HookManager] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        # event_name -> [(priority, callback), ...]  按 priority 升序
        self._hooks: Dict[str, List[Tuple[int, Callable]]] = {e: [] for e in ALL_EVENTS}
        # 外部工具: tool_name -> {"schema": {...}, "handler": callable, "plugin": str}
        self._external_tools: Dict[str, Dict[str, Any]] = {}
        # UI 按钮: [(plugin_name, icon, tooltip, callback), ...]
        self._ui_buttons: List[Tuple[str, str, str, Callable]] = []
        # UI Bridge 引用（由 AITab 初始化时设置）
        self._ui_bridge: Optional[PluginUIBridge] = None

    # ---------- 事件注册 ----------

    def register(self, event: str, callback: Callable, priority: int = 0):
        """注册钩子函数

        Args:
            event: 事件名称（见 ALL_EVENTS）
            callback: 回调函数
            priority: 优先级（数字越小越先执行，默认 0）
        """
        if event not in self._hooks:
            self._hooks[event] = []
        self._hooks[event].append((priority, callback))
        self._hooks[event].sort(key=lambda x: x[0])

    def unregister(self, event: str, callback: Callable):
        """注销钩子函数"""
        if event in self._hooks:
            self._hooks[event] = [
                (p, cb) for p, cb in self._hooks[event] if cb is not callback
            ]

    def unregister_all(self, plugin_name: str = ""):
        """注销所有钩子（或指定插件的钩子）"""
        if not plugin_name:
            for event in self._hooks:
                self._hooks[event] = []
        # 按插件名注销需要在 PluginContext 中跟踪

    # ---------- 事件触发 ----------

    def fire(self, event: str, **kwargs):
        """触发事件（通知型 — 所有回调依次调用，不修改数据）

        任何回调抛出异常不会中断后续回调或主流程。
        """
        for _priority, callback in self._hooks.get(event, []):
            try:
                callback(**kwargs)
            except Exception as e:
                print(f"[Hook] ⚠ {event} callback error: {e}")
                traceback.print_exc()

    def fire_filter(self, event: str, value: Any, **kwargs) -> Any:
        """管道式过滤事件（每个回调接收 value，返回修改后的 value）

        用于 on_before_request 等需要修改数据的场景。
        如果回调返回 None，保留原值。
        """
        for _priority, callback in self._hooks.get(event, []):
            try:
                result = callback(value, **kwargs)
                if result is not None:
                    value = result
            except Exception as e:
                print(f"[Hook] ⚠ {event} filter error: {e}")
                traceback.print_exc()
        return value

    # ---------- 外部工具管理 ----------

    def register_tool(self, name: str, schema: dict, description: str,
                      handler: Callable, plugin_name: str = ""):
        """注册外部工具

        Args:
            name: 工具名称（AI 调用时使用）
            schema: OpenAI Function Calling 的 parameters schema
            description: 工具描述
            handler: 执行函数，签名 (args: dict) -> dict
            plugin_name: 所属插件名
        """
        self._external_tools[name] = {
            "schema": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": f"[Plugin] {description}",
                    "parameters": schema,
                }
            },
            "handler": handler,
            "plugin": plugin_name,
        }
        print(f"[Hook] 注册外部工具: {name} (from {plugin_name or 'unknown'})")

    def unregister_tool(self, name: str):
        """注销外部工具"""
        if name in self._external_tools:
            del self._external_tools[name]

    def unregister_tools_by_plugin(self, plugin_name: str):
        """注销指定插件的所有工具"""
        to_remove = [n for n, v in self._external_tools.items()
                     if v.get("plugin") == plugin_name]
        for n in to_remove:
            del self._external_tools[n]

    def get_external_tools(self) -> List[dict]:
        """获取所有外部工具的 OpenAI schema 列表"""
        return [v["schema"] for v in self._external_tools.values()]

    def has_external_tool(self, name: str) -> bool:
        """检查是否存在指定外部工具"""
        return name in self._external_tools

    def execute_external_tool(self, name: str, args: dict) -> dict:
        """执行外部工具"""
        tool_info = self._external_tools.get(name)
        if not tool_info:
            return {"success": False, "error": f"外部工具不存在: {name}"}
        try:
            result = tool_info["handler"](args)
            if not isinstance(result, dict):
                result = {"success": True, "result": str(result)}
            return result
        except Exception as e:
            return {"success": False, "error": f"外部工具 {name} 执行失败: {e}"}

    # ---------- UI 按钮管理 ----------

    def register_button(self, plugin_name: str, icon: str, tooltip: str,
                        callback: Callable):
        """注册 UI 按钮"""
        self._ui_buttons.append((plugin_name, icon, tooltip, callback))

    def unregister_buttons_by_plugin(self, plugin_name: str):
        """注销指定插件的所有按钮"""
        self._ui_buttons = [b for b in self._ui_buttons if b[0] != plugin_name]

    def get_buttons(self) -> List[Tuple[str, str, str, Callable]]:
        """获取所有注册的 UI 按钮"""
        return list(self._ui_buttons)

    # ---------- UI Bridge ----------

    def set_ui_bridge(self, bridge: PluginUIBridge):
        """设置 UI 桥接（由 AITab 初始化时调用）"""
        self._ui_bridge = bridge

    def get_ui_bridge(self) -> Optional[PluginUIBridge]:
        """获取 UI 桥接"""
        return self._ui_bridge

    # ---------- 重置 ----------

    def reset(self):
        """完全重置（用于重载插件时）"""
        self._init()


# ============================================================
# PluginUIBridge — UI API 桥接层
# ============================================================

class PluginUIBridge:
    """桥接插件与 UI 层，提供线程安全的 UI 操作

    由 AITab.__init__ 创建并传入 chat_layout 引用。
    插件通过 PluginContext 间接使用这些能力。
    """

    def __init__(self):
        self._chat_layout = None         # QVBoxLayout 引用
        self._button_container = None    # QHBoxLayout 引用
        self._insert_card_signal = None  # Signal 引用（线程安全插入）
        self._ai_tab = None              # AITab 引用

    def set_chat_layout(self, layout):
        self._chat_layout = layout

    def set_button_container(self, container):
        self._button_container = container

    def set_insert_card_signal(self, signal):
        self._insert_card_signal = signal

    def set_ai_tab(self, ai_tab):
        self._ai_tab = ai_tab

    def insert_chat_card(self, widget):
        """在聊天区域插入自定义 QWidget（主线程安全）"""
        if self._insert_card_signal:
            # 通过信号调度到主线程
            self._insert_card_signal.emit(widget)
        elif self._chat_layout:
            # 直接插入（仅在主线程调用时安全）
            try:
                self._chat_layout.insertWidget(
                    self._chat_layout.count() - 1, widget)
            except Exception as e:
                print(f"[Hook] ⚠ insert_chat_card error: {e}")

    def mount_buttons(self):
        """将所有注册的插件按钮挂载到工具栏"""
        if not self._button_container:
            return
        manager = get_hook_manager()
        try:
            from houdini_agent.qt_compat import QtWidgets, QtCore
        except ImportError:
            return

        for plugin_name, icon, tooltip, callback in manager.get_buttons():
            btn = QtWidgets.QPushButton(icon)
            btn.setToolTip(tooltip)
            btn.setObjectName("pluginToolbarBtn")
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            btn.setFixedSize(28, 28)
            btn.clicked.connect(callback)
            self._button_container.addWidget(btn)


# ============================================================
# PluginContext — 传给每个插件 register() 的上下文对象
# ============================================================

class PluginContext:
    """插件的 API 入口对象

    每个插件在 register(ctx) 中通过 ctx 访问所有能力。
    """

    def __init__(self, plugin_name: str, manager: HookManager,
                 settings: dict, config_path: Path):
        self._plugin_name = plugin_name
        self._manager = manager
        self._settings = settings           # 该插件的设置值 dict
        self._config_path = config_path     # plugins.json 路径
        self._registered_hooks: List[Tuple[str, Callable]] = []  # 跟踪注册的钩子

    @property
    def plugin_name(self) -> str:
        return self._plugin_name

    # ---------- 事件 Hook ----------

    def on(self, event: str, callback: Callable, priority: int = 0):
        """注册事件钩子"""
        self._manager.register(event, callback, priority)
        self._registered_hooks.append((event, callback))

    # ---------- 工具注册 ----------

    def register_tool(self, name: str, description: str, schema: dict,
                      handler: Callable):
        """注册自定义工具（AI 可调用）"""
        self._manager.register_tool(
            name=name, schema=schema, description=description,
            handler=handler, plugin_name=self._plugin_name,
        )

    # ---------- UI ----------

    def register_button(self, icon: str, tooltip: str, callback: Callable):
        """注册工具栏按钮"""
        self._manager.register_button(
            self._plugin_name, icon, tooltip, callback)

    def insert_chat_card(self, widget):
        """在聊天区域插入自定义 QWidget"""
        bridge = self._manager.get_ui_bridge()
        if bridge:
            bridge.insert_chat_card(widget)
        else:
            print(f"[Hook] ⚠ UI bridge not available, cannot insert chat card")

    # ---------- 设置 ----------

    def get_setting(self, key: str, default: Any = None) -> Any:
        """读取插件设置"""
        return self._settings.get(key, default)

    def set_setting(self, key: str, value: Any):
        """写入插件设置（自动持久化）"""
        self._settings[key] = value
        _save_plugin_config(self._config_path)

    # ---------- 日志 ----------

    def log(self, msg: str):
        """插件日志输出"""
        print(f"[Plugin:{self._plugin_name}] {msg}")

    # ---------- 清理 ----------

    def _cleanup(self):
        """注销该插件注册的所有钩子和工具"""
        for event, callback in self._registered_hooks:
            self._manager.unregister(event, callback)
        self._registered_hooks.clear()
        self._manager.unregister_tools_by_plugin(self._plugin_name)
        self._manager.unregister_buttons_by_plugin(self._plugin_name)


# ============================================================
# PluginLoader — 扫描 plugins/ 目录，加载插件
# ============================================================

# 插件配置文件路径
_CONFIG_DIR = Path(__file__).parent.parent.parent / "config"
_CONFIG_FILE = _CONFIG_DIR / "plugins.json"

# 全局配置缓存
_plugin_config: Dict[str, Any] = {}

# 已加载的插件: plugin_name -> {"module": mod, "info": PLUGIN_INFO, "ctx": PluginContext, "enabled": bool, "file": Path}
_loaded_plugins: Dict[str, Dict[str, Any]] = {}
_plugins_loaded = False


def _load_plugin_config() -> Dict[str, Any]:
    """加载插件配置"""
    global _plugin_config
    try:
        if _CONFIG_FILE.exists():
            with open(_CONFIG_FILE, 'r', encoding='utf-8') as f:
                _plugin_config = json.load(f)
        else:
            _plugin_config = {}
    except Exception:
        _plugin_config = {}
    return _plugin_config


def _save_plugin_config(config_path: Optional[Path] = None):
    """保存插件配置"""
    path = config_path or _CONFIG_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(_plugin_config, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[Hook] ⚠ 保存插件配置失败: {e}")


def _get_plugins_dir() -> Path:
    """获取 plugins/ 目录路径"""
    return Path(__file__).parent.parent.parent / "plugins"


def load_all_plugins():
    """扫描并加载所有插件"""
    global _loaded_plugins, _plugins_loaded

    if _plugins_loaded:
        return

    plugins_dir = _get_plugins_dir()
    if not plugins_dir.exists():
        plugins_dir.mkdir(parents=True, exist_ok=True)
        _plugins_loaded = True
        return

    config = _load_plugin_config()
    disabled_list = config.get("disabled", [])
    manager = get_hook_manager()

    for f in sorted(plugins_dir.glob("*.py")):
        if f.name.startswith("_"):
            continue  # 下划线开头的不自动加载

        module_name = f.stem
        try:
            _load_single_plugin(f, module_name, disabled_list, manager)
        except Exception as e:
            print(f"[Hook] ✖ 加载插件 {module_name} 失败: {e}")
            traceback.print_exc()

    _plugins_loaded = True
    if _loaded_plugins:
        enabled = [n for n, v in _loaded_plugins.items() if v["enabled"]]
        print(f"[Hook] 已加载 {len(_loaded_plugins)} 个插件, "
              f"启用 {len(enabled)} 个: {', '.join(enabled) or '(无)'}")


def _load_single_plugin(filepath: Path, module_name: str,
                         disabled_list: List[str],
                         manager: HookManager):
    """加载单个插件"""
    spec = importlib.util.spec_from_file_location(
        f"houdini_plugins.{module_name}", str(filepath))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    info = getattr(mod, "PLUGIN_INFO", None)
    register_fn = getattr(mod, "register", None)

    if not info or not isinstance(info, dict):
        print(f"[Hook] ⚠ 插件 {module_name} 缺少 PLUGIN_INFO dict，跳过")
        return
    if not register_fn or not callable(register_fn):
        print(f"[Hook] ⚠ 插件 {module_name} 缺少 register(ctx) 函数，跳过")
        return

    plugin_name = info.get("name", module_name)
    is_disabled = plugin_name in disabled_list
    is_enabled = not is_disabled

    # 构建插件设置
    plugin_settings = _plugin_config.get("settings", {}).get(plugin_name, {})
    # 用 schema 中的 default 值填充未设置的项
    for setting_def in info.get("settings", []):
        key = setting_def.get("key")
        if key and key not in plugin_settings:
            plugin_settings[key] = setting_def.get("default")

    # 创建上下文
    ctx = PluginContext(
        plugin_name=plugin_name,
        manager=manager,
        settings=plugin_settings,
        config_path=_CONFIG_FILE,
    )

    # 注册插件记录
    _loaded_plugins[plugin_name] = {
        "module": mod,
        "info": info,
        "ctx": ctx,
        "enabled": is_enabled,
        "file": filepath,
    }

    # 如果启用，调用 register
    if is_enabled:
        try:
            register_fn(ctx)
            print(f"[Hook] ✔ 插件 {plugin_name} v{info.get('version', '?')} 已加载")
        except Exception as e:
            print(f"[Hook] ✖ 插件 {plugin_name} register() 失败: {e}")
            traceback.print_exc()
            _loaded_plugins[plugin_name]["enabled"] = False


def reload_plugin(plugin_name: str) -> bool:
    """重载指定插件"""
    plugin_data = _loaded_plugins.get(plugin_name)
    if not plugin_data:
        print(f"[Hook] ⚠ 插件 {plugin_name} 不存在")
        return False

    # 清理旧注册
    ctx = plugin_data["ctx"]
    ctx._cleanup()

    # 重新加载模块
    filepath = plugin_data["file"]
    module_name = filepath.stem
    manager = get_hook_manager()
    config = _load_plugin_config()
    disabled_list = config.get("disabled", [])

    try:
        # 移除旧记录
        del _loaded_plugins[plugin_name]
        # 重新加载
        _load_single_plugin(filepath, module_name, disabled_list, manager)
        # 重新挂载 UI 按钮
        bridge = manager.get_ui_bridge()
        if bridge:
            bridge.mount_buttons()
        print(f"[Hook] ↻ 插件 {plugin_name} 已重载")
        return True
    except Exception as e:
        print(f"[Hook] ✖ 重载插件 {plugin_name} 失败: {e}")
        traceback.print_exc()
        return False


def enable_plugin(plugin_name: str) -> bool:
    """启用插件"""
    plugin_data = _loaded_plugins.get(plugin_name)
    if not plugin_data:
        return False

    if plugin_data["enabled"]:
        return True  # 已启用

    manager = get_hook_manager()
    ctx = plugin_data["ctx"]
    register_fn = getattr(plugin_data["module"], "register", None)

    if register_fn:
        try:
            register_fn(ctx)
            plugin_data["enabled"] = True
            # 更新配置
            disabled = _plugin_config.get("disabled", [])
            if plugin_name in disabled:
                disabled.remove(plugin_name)
                _plugin_config["disabled"] = disabled
                _save_plugin_config()
            # 重新挂载按钮
            bridge = manager.get_ui_bridge()
            if bridge:
                bridge.mount_buttons()
            print(f"[Hook] ✔ 插件 {plugin_name} 已启用")
            return True
        except Exception as e:
            print(f"[Hook] ✖ 启用插件 {plugin_name} 失败: {e}")
            return False
    return False


def disable_plugin(plugin_name: str) -> bool:
    """禁用插件"""
    plugin_data = _loaded_plugins.get(plugin_name)
    if not plugin_data:
        return False

    if not plugin_data["enabled"]:
        return True  # 已禁用

    # 清理钩子和工具
    ctx = plugin_data["ctx"]
    ctx._cleanup()
    plugin_data["enabled"] = False

    # 更新配置
    disabled = _plugin_config.get("disabled", [])
    if plugin_name not in disabled:
        disabled.append(plugin_name)
        _plugin_config["disabled"] = disabled
        _save_plugin_config()

    # 重新挂载按钮（移除已禁用的）
    manager = get_hook_manager()
    bridge = manager.get_ui_bridge()
    if bridge:
        bridge.mount_buttons()

    print(f"[Hook] ✖ 插件 {plugin_name} 已禁用")
    return True


def get_plugin_setting(plugin_name: str, key: str, default: Any = None) -> Any:
    """获取插件设置值"""
    return _plugin_config.get("settings", {}).get(plugin_name, {}).get(key, default)


def set_plugin_setting(plugin_name: str, key: str, value: Any):
    """设置插件设置值"""
    if "settings" not in _plugin_config:
        _plugin_config["settings"] = {}
    if plugin_name not in _plugin_config["settings"]:
        _plugin_config["settings"][plugin_name] = {}
    _plugin_config["settings"][plugin_name][key] = value
    _save_plugin_config()


def list_plugins() -> List[Dict[str, Any]]:
    """列出所有插件的元数据"""
    load_all_plugins()
    result = []
    for name, data in _loaded_plugins.items():
        info = dict(data["info"])
        info["_enabled"] = data["enabled"]
        info["_file"] = str(data["file"])
        result.append(info)
    return result


def get_plugins_dir() -> Path:
    """获取 plugins 目录路径（供外部使用）"""
    return _get_plugins_dir()


def reload_all_plugins():
    """重载所有插件"""
    global _loaded_plugins, _plugins_loaded
    # 清理所有
    for name, data in _loaded_plugins.items():
        data["ctx"]._cleanup()
    _loaded_plugins.clear()
    _plugins_loaded = False
    # 重新加载
    load_all_plugins()
    # 重新挂载按钮
    manager = get_hook_manager()
    bridge = manager.get_ui_bridge()
    if bridge:
        bridge.mount_buttons()


# ============================================================
# 装饰器 API — 让插件代码更简洁
# ============================================================

# 装饰器收集器（用于自动注册模式）
_pending_hooks: List[Tuple[str, Callable, int]] = []
_pending_tools: List[Dict[str, Any]] = []
_pending_buttons: List[Dict[str, Any]] = []


def hook(event: str, priority: int = 0):
    """装饰器：注册事件钩子

    用法:
        @hook("on_after_tool")
        def my_callback(tool_name, args, result):
            print(f"Tool {tool_name} called")
    """
    def decorator(func):
        _pending_hooks.append((event, func, priority))
        return func
    return decorator


def tool(name: str, description: str, parameters: dict):
    """装饰器：注册自定义工具

    用法:
        @tool(name="my_tool", description="...", parameters={...})
        def handler(args):
            return {"success": True, "result": "..."}
    """
    def decorator(func):
        _pending_tools.append({
            "name": name,
            "description": description,
            "parameters": parameters,
            "handler": func,
        })
        return func
    return decorator


def ui_button(icon: str, tooltip: str):
    """装饰器：注册 UI 按钮

    用法:
        @ui_button(icon="📊", tooltip="Stats")
        def on_click(ctx):
            print("Clicked!")
    """
    def decorator(func):
        _pending_buttons.append({
            "icon": icon,
            "tooltip": tooltip,
            "callback": func,
        })
        return func
    return decorator


def _apply_decorators(ctx: PluginContext):
    """将装饰器收集的钩子/工具/按钮注册到 ctx"""
    global _pending_hooks, _pending_tools, _pending_buttons

    for event, callback, priority in _pending_hooks:
        ctx.on(event, callback, priority)
    for t in _pending_tools:
        ctx.register_tool(
            name=t["name"], description=t["description"],
            schema=t["parameters"], handler=t["handler"],
        )
    for b in _pending_buttons:
        ctx.register_button(
            icon=b["icon"], tooltip=b["tooltip"], callback=b["callback"],
        )

    # 清空收集器
    _pending_hooks = []
    _pending_tools = []
    _pending_buttons = []


# ============================================================
# 单例获取器
# ============================================================

def get_hook_manager() -> HookManager:
    """获取全局 HookManager 单例"""
    return HookManager()
