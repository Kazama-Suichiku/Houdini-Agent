# -*- coding: utf-8 -*-
"""
Houdini Agent 插件目录

将 .py 插件文件放在此目录中即可自动加载。
以 _ 开头的文件不会被自动加载（可用于示例/模板）。

每个插件需要包含:
  - PLUGIN_INFO: dict  (name, version, author, description, settings)
  - register(ctx): 入口函数，ctx 是 PluginContext 实例
"""
