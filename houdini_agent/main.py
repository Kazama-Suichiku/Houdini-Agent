import os
import sys
import hou
from PySide6 import QtWidgets

# 强制重新加载模块，避免缓存问题
def _reload_modules():
    modules_to_reload = [
        'houdini_agent.ui.ai_tab',
        'houdini_agent.ui.cursor_widgets',
        'houdini_agent.core.main_window',
        'houdini_agent.utils.ai_client',
        'houdini_agent.utils.mcp',
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
    global _main_window
    
    # 每次调用时强制重新加载模块
    _reload_modules()
    
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
                _main_window = None
    except Exception:
        pass

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
