"""
Houdini Agent - Launcher
"""

import sys
import os

# ============================================================
# 强制使用本地 lib 目录中的依赖库
# ============================================================
_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB_DIR = os.path.join(_ROOT_DIR, 'lib')

if os.path.exists(_LIB_DIR):
    if _LIB_DIR in sys.path:
        sys.path.remove(_LIB_DIR)
    sys.path.insert(0, _LIB_DIR)

# ============================================================

def detect_dcc():
    """检测当前运行的 DCC 软件"""
    try:
        import hou
        return "houdini"
    except ImportError:
        pass
    
    return None

def launch_houdini_agent():
    """启动 Houdini Agent"""
    tool_path = os.path.join(os.path.dirname(__file__), "houdini_agent")
    if tool_path not in sys.path:
        sys.path.insert(0, tool_path)
    
    try:
        if 'main' in sys.modules:
            import importlib
            import main
            importlib.reload(main)
        else:
            import main
        
        return main.show_tool()
    except Exception as e:
        print(f"Failed to launch Houdini Agent: {e}")
        import traceback
        traceback.print_exc()
        return None

def launch():
    """自动检测并启动"""
    dcc = detect_dcc()
    
    if dcc == "houdini":
        print("Houdini detected, launching Houdini Agent...")
        return launch_houdini_agent()
    else:
        print("Error: Houdini not detected.")
        print("Please run this tool inside Houdini.")
        return None

# 全局变量存储窗口实例
_agent_window = None

def show_tool():
    """统一入口函数"""
    global _agent_window
    _agent_window = launch()
    return _agent_window

if __name__ == "__main__":
    show_tool()
