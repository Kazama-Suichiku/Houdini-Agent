# -*- coding: utf-8 -*-
"""
示例插件 — 展示 Houdini Agent Hook 系统的全部能力

文件名以 _ 开头，不会被自动加载。
去掉 _ 前缀（重命名为 example_plugin.py）即可启用。

插件约定:
  1. PLUGIN_INFO dict — 插件元数据
  2. register(ctx) 函数 — 入口，ctx 是 PluginContext 实例
"""

PLUGIN_INFO = {
    "name": "Example Plugin",
    "version": "1.0.0",
    "author": "Houdini Agent Community",
    "description": "Demonstrates all plugin capabilities: hooks, tools, buttons, settings",
    "settings": [
        {
            "key": "log_level",
            "type": "string",
            "label": "Log Level",
            "default": "info",
            "options": ["debug", "info", "warning", "error"],
        },
        {
            "key": "enable_greeting",
            "type": "bool",
            "label": "Enable Greeting Tool",
            "default": True,
        },
        {
            "key": "greeting_prefix",
            "type": "string",
            "label": "Greeting Prefix",
            "default": "Hello",
        },
    ],
}


def register(ctx):
    """插件入口 — ctx 是 PluginContext 实例

    可用 API:
      ctx.on(event, callback)          — 注册事件钩子
      ctx.register_tool(...)           — 注册自定义工具（AI 可调用）
      ctx.register_button(icon, ...)   — 注册工具栏按钮
      ctx.insert_chat_card(widget)     — 在聊天区域插入自定义 QWidget
      ctx.get_setting(key)             — 读取插件设置
      ctx.set_setting(key, value)      — 写入插件设置
      ctx.log(msg)                     — 输出日志
    """

    # ─────────────────────────────────────────────
    # 1. 监听事件 — 工具调用后记录日志
    # ─────────────────────────────────────────────
    log_level = ctx.get_setting("log_level", "info")

    def on_tool_done(tool_name, args, result):
        if log_level == "debug":
            ctx.log(f"Tool {tool_name}({args}) -> {result}")
        else:
            ctx.log(f"Tool {tool_name} -> success={result.get('success')}")

    ctx.on("on_after_tool", on_tool_done)

    # ─────────────────────────────────────────────
    # 2. 注册自定义工具 — AI 可以调用此工具
    # ─────────────────────────────────────────────
    if ctx.get_setting("enable_greeting", True):
        prefix = ctx.get_setting("greeting_prefix", "Hello")

        def greet_handler(args):
            name = args.get("name", "World")
            return {
                "success": True,
                "result": f"{prefix}, {name}! This is from Example Plugin."
            }

        ctx.register_tool(
            name="example_greeting",
            description="Say hello to someone (example plugin tool)",
            schema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The person to greet",
                    }
                },
                "required": ["name"],
            },
            handler=greet_handler,
        )

    # ─────────────────────────────────────────────
    # 3. 注册工具栏按钮
    # ─────────────────────────────────────────────
    def on_button_click():
        ctx.log("Example button clicked!")

    ctx.register_button(
        icon="👋",
        tooltip="Example Plugin Greeting",
        callback=on_button_click,
    )

    # ─────────────────────────────────────────────
    # 4. 管道式过滤 prompt（on_before_request）
    # ─────────────────────────────────────────────
    def add_custom_instruction(messages):
        """在 system prompt 末尾追加自定义指令"""
        if messages and messages[0].get("role") == "system":
            messages[0]["content"] += (
                "\n\n[Example Plugin] "
                "You have access to example_greeting tool from Example Plugin."
            )
        return messages

    ctx.on("on_before_request", add_custom_instruction)

    # ─────────────────────────────────────────────
    # 5. 会话开始/结束 钩子
    # ─────────────────────────────────────────────
    ctx.on("on_session_start", lambda session_id:
           ctx.log(f"Session started: {session_id}"))
    ctx.on("on_session_end", lambda session_id:
           ctx.log(f"Session ended: {session_id}"))

    ctx.log("Example Plugin registered successfully!")
