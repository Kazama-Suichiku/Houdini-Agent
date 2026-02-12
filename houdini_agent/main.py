import os
import sys
import hou
from PySide6 import QtWidgets

# 强制重新加载模块，避免缓存问题
def _reload_modules():
    # ---- 清理旧包名残留（HOUDINI_HIP_MANAGER → houdini_agent 迁移） ----
    old_mods = [k for k in sys.modules if k.startswith('HOUDINI_HIP_MANAGER')]
    for k in old_mods:
        del sys.modules[k]
    
    modules_to_reload = [
        'houdini_agent.utils.token_optimizer',
        'houdini_agent.utils.ultra_optimizer',
        'houdini_agent.utils.training_data_exporter',
        'houdini_agent.utils.updater',
        'houdini_agent.utils.ai_client',
        'houdini_agent.utils.mcp.client',
        'houdini_agent.utils.mcp',
        'houdini_agent.ui.cursor_widgets',
        # ★ 新增：拆分出的 mixin 模块也需要重载，否则引用旧类导致异常
        'houdini_agent.ui.header',
        'houdini_agent.ui.input_area',
        'houdini_agent.ui.chat_view',
        'houdini_agent.core.agent_runner',
        'houdini_agent.core.session_manager',
        'houdini_agent.ui.ai_tab',
        'houdini_agent.core.main_window',
    ]
    for mod_name in modules_to_reload:
        if mod_name in sys.modules:
            try:
                import importlib
                importlib.reload(sys.modules[mod_name])
            except Exception:
                pass

from houdini_agent.core.main_window import MainWindow

_main_window = None

def show_tool():
    global _main_window, MainWindow
    
    # 每次调用时强制重新加载模块
    _reload_modules()
    
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

    try:
        if _main_window is not None:
            if _main_window.isVisible():
                _main_window.raise_()
                _main_window.activateWindow()
                return _main_window
            else:
                _main_window.force_quit = True
                _main_window.close()
                _main_window.deleteLater()
                _main_window = None
                # ★ 不要 processEvents()，它会触发队列中残留的事件导致窗口闪烁
    except Exception:
        _main_window = None

    try:
        _main_window = MainWindow()
        _main_window.show()
        _main_window.raise_()
        _main_window.activateWindow()
        return _main_window
    except Exception as e:
        QtWidgets.QMessageBox.critical(None, "Error", f"Failed to create Houdini Agent window:\n{e}", QtWidgets.QMessageBox.Ok)
        return None

if __name__ == "__main__":
    show_tool()
