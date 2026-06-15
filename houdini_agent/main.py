import os
import sys
import hou
from houdini_agent.qt_compat import QtWidgets

# 模块源文件 mtime 缓存：{mod_name: mtime}，跨 show_tool() 调用持久化
_module_mtimes: dict = {}

# 强制重新加载模块，避免缓存问题
def _reload_modules():
    import importlib
    import inspect

    # ---- 清理旧包名残留（HOUDINI_HIP_MANAGER → houdini_agent 迁移） ----
    old_mods = [k for k in sys.modules if k.startswith('HOUDINI_HIP_MANAGER')]
    for k in old_mods:
        del sys.modules[k]

    modules_to_reload = [
        'houdini_agent.qt_compat',  # ★ Qt 兼容层最先重载
        'houdini_agent.utils.token_optimizer',
        'houdini_agent.utils.ultra_optimizer',
        'houdini_agent.utils.training_data_exporter',
        'houdini_agent.utils.updater',
        'houdini_agent.utils.hooks',
        'houdini_agent.utils.tool_registry',
        'houdini_agent.utils.rules_manager',
        'houdini_agent.utils.ai_client',
        'houdini_agent.utils.mcp.client',
        'houdini_agent.utils.mcp',
        'houdini_agent.ui.i18n',
        'houdini_agent.ui.cursor_widgets',
        # ★ 新增：拆分出的 mixin 模块也需要重载，否则引用旧类导致异常
        'houdini_agent.ui.font_settings_dialog',
        'houdini_agent.ui.header',
        'houdini_agent.ui.input_area',
        'houdini_agent.ui.chat_view',
        'houdini_agent.core.agent_runner',
        'houdini_agent.core.session_manager',
        'houdini_agent.ui.memory_mixin',
        'houdini_agent.ui.context_mixin',
        'houdini_agent.ui.streaming_mixin',
        'houdini_agent.ui.plan_mixin',
        'houdini_agent.ui.image_mixin',
        'houdini_agent.core.cache_mixin',
        'houdini_agent.ui.history_mixin',
        'houdini_agent.core.update_mixin',
        'houdini_agent.ui.ai_tab',
        'houdini_agent.core.main_window',
    ]
    # 第一次打开：模块刚被 import 进来，代码必然是最新的，无需 reload。
    # 只记录 mtime 基准，供后续调用做变动检测。
    if not _module_mtimes:
        for mod_name in modules_to_reload:
            if mod_name not in sys.modules:
                continue
            try:
                _module_mtimes[mod_name] = os.path.getmtime(
                    inspect.getfile(sys.modules[mod_name])
                )
            except (TypeError, OSError):
                pass
        print("[Houdini Agent] Startup: reload_modules skipped (fresh imports)")
        return

    # 后续打开：只 reload 源文件有变动的模块（开发热更新 / 插件升级场景）
    for mod_name in modules_to_reload:
        if mod_name not in sys.modules:
            continue
        try:
            src_file = inspect.getfile(sys.modules[mod_name])
            mtime = os.path.getmtime(src_file)
            if _module_mtimes.get(mod_name) == mtime:
                continue  # 未变动，跳过
            _module_mtimes[mod_name] = mtime
        except (TypeError, OSError):
            pass  # 取不到 mtime 时保底 reload
        try:
            print(f"[Houdini Agent] Startup: reload: {mod_name}")
            importlib.reload(sys.modules[mod_name])
        except Exception:
            pass

from houdini_agent.core.main_window import MainWindow

_main_window = None

def show_tool():
    global _main_window, MainWindow

    # 启动断点日志：用于诊断冷启动 freeze（参见 issue #9）。
    # 用户报错时贴出 Houdini Python Shell 输出即可定位卡死阶段。
    print("[Houdini Agent] Startup: reload_modules begin")
    _reload_modules()
    print("[Houdini Agent] Startup: reload_modules done")

    # ★ 重载后刷新 MainWindow 引用，避免使用旧类
    try:
        from houdini_agent.core.main_window import MainWindow as _MW
        MainWindow = _MW
    except Exception:
        pass

    if not QtWidgets.QApplication.instance():
        app = QtWidgets.QApplication([])
    else:
        app = QtWidgets.QApplication.instance()
    print("[Houdini Agent] Startup: QApplication ready")

    try:
        if _main_window is not None:
            if _main_window.isVisible():
                print("[Houdini Agent] Startup: reusing existing visible window")
                _main_window.raise_()
                _main_window.activateWindow()
                return _main_window
            else:
                print("[Houdini Agent] Startup: cleaning up stale window")
                # 清理旧实例的退出保存回调，防止覆盖新实例的数据
                try:
                    import atexit as _atexit
                    if hasattr(_main_window, 'ai_tab'):
                        _main_window.ai_tab._destroyed = True
                        _atexit.unregister(_main_window.ai_tab._atexit_save)
                    _atexit.unregister(_main_window._atexit_save)
                    app = QtWidgets.QApplication.instance()
                    if app:
                        try:
                            app.aboutToQuit.disconnect(_main_window._on_app_about_to_quit)
                        except (TypeError, RuntimeError):
                            pass
                except Exception:
                    pass
                _main_window.force_quit = True
                _main_window.close()
                _main_window.deleteLater()
                _main_window = None
    except Exception:
        _main_window = None

    try:
        print("[Houdini Agent] Startup: MainWindow() begin")
        _main_window = MainWindow()
        print("[Houdini Agent] Startup: MainWindow() done, calling show()")
        _main_window.show()
        _main_window.raise_()
        _main_window.activateWindow()
        print("[Houdini Agent] Startup: window shown")
        return _main_window
    except Exception as e:
        import traceback
        traceback.print_exc()
        QtWidgets.QMessageBox.critical(None, "Error", f"Failed to create Houdini Agent window:\n{e}", QtWidgets.QMessageBox.Ok)
        return None

if __name__ == "__main__":
    show_tool()
