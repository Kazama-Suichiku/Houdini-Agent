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
    """启动 Houdini Agent

    默认打开新的 QML / Qt Quick 界面（Mono Editorial 重构）。
    设环境变量 HAGENT_UI=legacy 可回退到旧的 QtWidgets 界面。
    """
    repo_root = os.path.dirname(os.path.abspath(__file__))
    tool_path = os.path.join(repo_root, "houdini_agent")
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    if tool_path not in sys.path:
        sys.path.insert(0, tool_path)

    # 清理旧包名残留（HOUDINI_HIP_MANAGER → houdini_agent 迁移）
    old_mods = [k for k in sys.modules if k.startswith('HOUDINI_HIP_MANAGER')]
    for k in old_mods:
        del sys.modules[k]

    ui = os.environ.get("HAGENT_UI", "qml").strip().lower()

    try:
        import importlib
        try:
            from houdini_agent.bridge.server import start_bridge
            start_bridge()
        except Exception:
            pass
        if ui == "legacy":
            # 旧的 QtWidgets 界面
            if 'main' in sys.modules:
                import main
                importlib.reload(main)
            else:
                import main
            return main.show_tool()

        # 新的 QML 界面（默认）
        from houdini_agent.ui_qml import app as qml_app
        importlib.reload(qml_app)
        return qml_app.show_tool()
    except Exception as e:
        print(f"Failed to launch Houdini Agent: {e}")
        import traceback
        traceback.print_exc()
        return None

def launch_external_agent():
    """启动外置 UI。双击入口会走这里，并通过 Bridge 连接 Houdini。"""
    repo_root = os.path.dirname(os.path.abspath(__file__))
    tool_path = os.path.join(repo_root, "houdini_agent")
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    if tool_path not in sys.path:
        sys.path.insert(0, tool_path)
    try:
        from houdini_agent.ui_qml import external_app
        return external_app.main()
    except Exception as e:
        print(f"Failed to launch external Houdini Agent: {e}")
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
        print("Houdini not detected, launching standalone Houdini Agent...")
        return launch_external_agent()

# 全局变量存储窗口实例
_agent_window = None

def show_tool():
    """统一入口函数"""
    global _agent_window
    _agent_window = launch()
    return _agent_window

if __name__ == "__main__":
    show_tool()
