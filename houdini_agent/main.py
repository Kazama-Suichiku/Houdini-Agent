import os
import sys
import hou
from houdini_agent import configure_text_output
from houdini_agent.qt_compat import QtWidgets

configure_text_output()

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
        'houdini_agent.utils.experience_store',
        'houdini_agent.utils.ai_client',
        'houdini_agent.utils.bridge.remote_client',
        'houdini_agent.utils.bridge.server',
        'houdini_agent.utils.bridge',
        'houdini_agent.utils.mcp.client',
        'houdini_agent.utils.mcp',
        'houdini_agent.ui.i18n',
        'houdini_agent.ui.theme_engine',
        'houdini_agent.ui.cursor_widgets',
        # ★ 新增：拆分出的 mixin 模块也需要重载，否则引用旧类导致异常
        'houdini_agent.ui.font_settings_dialog',
        'houdini_agent.ui.experience_review_dialog',
        'houdini_agent.ui.header',
        'houdini_agent.ui.input_area',
        'houdini_agent.ui.chat_view',
        'houdini_agent.core.agent_runner',
        'houdini_agent.core.session_manager',
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
                continue
            _module_mtimes[mod_name] = mtime
        except (TypeError, OSError):
            pass
        try:
            print(f"[Houdini Agent] Startup: reload: {mod_name}")
            importlib.reload(sys.modules[mod_name])
        except Exception:
            pass

from houdini_agent.core.main_window import MainWindow

_main_window = None
_standalone_process = None

def _iter_standalone_commands():
    """Yield candidate commands for the external UI, never houdini.exe."""
    import shutil

    hfs = os.environ.get("HFS", "")
    candidates = []

    override = os.environ.get("HOUDINI_AGENT_STANDALONE_PYTHON", "").strip()
    if override:
        candidates.append([override])

    if hfs:
        try:
            for name in os.listdir(hfs):
                if name.lower().startswith("python"):
                    candidates.append([os.path.join(hfs, name, "pythonw.exe")])
                    candidates.append([os.path.join(hfs, name, "python.exe")])
        except Exception:
            pass
        for py_dir in ("python311", "python310", "python39", "python37"):
            candidates.append([os.path.join(hfs, py_dir, "pythonw.exe")])
            candidates.append([os.path.join(hfs, py_dir, "python.exe")])
        candidates.append([os.path.join(hfs, "bin", "pythonw.exe")])
        candidates.append([os.path.join(hfs, "bin", "python.exe")])

    base = os.path.basename(sys.executable or "").lower()
    if base not in {"houdini.exe", "houdinifx.exe", "houdinicore.exe", "houdiniindie.exe"}:
        candidates.append([sys.executable])

    for name in ("pythonw.exe", "python.exe"):
        exe = shutil.which(name)
        if exe:
            candidates.append([exe])

    py_launcher = shutil.which("py.exe") or shutil.which("py")
    if py_launcher:
        candidates.append([py_launcher, "-3"])

    seen = set()
    for cmd in candidates:
        exe = cmd[0]
        if not exe:
            continue
        key = tuple(cmd)
        if key in seen:
            continue
        seen.add(key)
        if len(cmd) == 1 and exe.lower() not in {"py", "py.exe"} and not os.path.exists(exe):
            continue
        yield cmd

def _launch_standalone_ui(bridge_url: str):
    """Start the standalone UI process. Returns subprocess handle or None."""
    try:
        import subprocess
        import time
        import platform
        from pathlib import Path
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = os.environ.copy()
        paths = [root_dir, os.path.join(root_dir, "lib")]
        hfs = os.environ.get("HFS", "")
        if hfs:
            for py_lib in (
                os.path.join(hfs, "houdini", "python3.11libs"),
                os.path.join(hfs, "houdini", "python3.10libs"),
                os.path.join(hfs, "houdini", "python3.9libs"),
                os.path.join(hfs, "houdini", "python3.7libs"),
                os.path.join(hfs, "python311", "Lib", "site-packages"),
                os.path.join(hfs, "python310", "Lib", "site-packages"),
                os.path.join(hfs, "python39", "Lib", "site-packages"),
                os.path.join(hfs, "python37", "Lib", "site-packages"),
                os.path.join(hfs, "python311", "Lib", "site-packages-forced"),
                os.path.join(hfs, "python310", "Lib", "site-packages-forced"),
                os.path.join(hfs, "python39", "Lib", "site-packages-forced"),
                os.path.join(hfs, "python37", "Lib", "site-packages-forced"),
            ):
                if os.path.exists(py_lib):
                    paths.append(py_lib)
            houdini_bin = os.path.join(hfs, "bin")
            if os.path.exists(houdini_bin):
                env["PATH"] = os.pathsep.join([houdini_bin, env.get("PATH", "")])
            for dll_dir in (
                os.path.join(hfs, "python311", "Lib", "site-packages-forced", "PySide6"),
                os.path.join(hfs, "python311", "Lib", "site-packages-forced", "shiboken6"),
                os.path.join(hfs, "python310", "Lib", "site-packages-forced", "PySide6"),
                os.path.join(hfs, "python310", "Lib", "site-packages-forced", "shiboken6"),
                os.path.join(hfs, "python39", "Lib", "site-packages-forced", "PySide2"),
                os.path.join(hfs, "python39", "Lib", "site-packages-forced", "shiboken2"),
                os.path.join(hfs, "python37", "Lib", "site-packages-forced", "PySide2"),
                os.path.join(hfs, "python37", "Lib", "site-packages-forced", "shiboken2"),
            ):
                if os.path.exists(dll_dir):
                    env["PATH"] = os.pathsep.join([dll_dir, env.get("PATH", "")])
        old_pp = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = os.pathsep.join([p for p in paths if os.path.exists(p)] + ([old_pp] if old_pp else []))
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        create_no_window = platform.system().lower() == "windows"
        log_dir = Path(root_dir) / "cache" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        if create_no_window:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            creationflags = 0

        last_error = None
        for cmd_prefix in _iter_standalone_commands():
            check_cmd = list(cmd_prefix) + [
                "-c",
                "from houdini_agent.qt_compat import PYSIDE_VERSION; print('Qt runtime OK: PySide%s' % PYSIDE_VERSION)",
            ]
            try:
                check = subprocess.run(
                    check_cmd,
                    cwd=root_dir,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=8.0,
                    creationflags=creationflags,
                )
                check_output = (check.stdout or "").strip()
                if check.returncode != 0:
                    print(f"[Houdini Agent] Startup: standalone runtime check failed ({check.returncode}): {check_cmd}")
                    if check_output:
                        print(f"[Houdini Agent] Startup: runtime check output:\n{check_output[-1200:]}")
                    continue
                if check_output:
                    print(f"[Houdini Agent] Startup: {check_output}")
            except Exception as e:
                last_error = e
                print(f"[Houdini Agent] Startup: standalone runtime check error: {e}")
                continue

            cmd = list(cmd_prefix) + ["-m", "houdini_agent.standalone", "--bridge-url", bridge_url]
            print(f"[Houdini Agent] Startup: launching standalone UI: {cmd}")
            log_path = log_dir / f"standalone_{int(time.time())}.log"
            log_file = None
            try:
                log_file = open(log_path, "w", encoding="utf-8", errors="replace")
                log_file.write(f"Command: {cmd}\n\n")
                log_file.flush()
                proc = subprocess.Popen(
                    cmd,
                    cwd=root_dir,
                    env=env,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    creationflags=creationflags,
                )
            except Exception as e:
                last_error = e
                try:
                    if log_file is not None:
                        log_file.close()
                except Exception:
                    pass
                continue
            try:
                log_file.close()
            except Exception:
                pass
            time.sleep(1.5)
            if proc.poll() is None:
                print(f"[Houdini Agent] Startup: standalone UI log: {log_path}")
                return proc
            print(f"[Houdini Agent] Startup: standalone UI exited early: {proc.returncode}")
            try:
                if log_path.exists():
                    text = log_path.read_text(encoding="utf-8", errors="replace")
                    if text.strip():
                        print(f"[Houdini Agent] Startup: standalone UI log tail:\n{text[-1600:]}")
            except Exception:
                pass
        if last_error is not None:
            print(f"[Houdini Agent] Startup: standalone launch failed: {last_error}")
        print("[Houdini Agent] Startup: no usable standalone Qt runtime found; using embedded fallback")
        print("[Houdini Agent] Startup: set HOUDINI_AGENT_STANDALONE_PYTHON to a python.exe/pythonw.exe with PySide6/PySide2 to force a runtime")
        return None
    except Exception as e:
        print(f"[Houdini Agent] Startup: standalone launch failed: {e}")
        return None


def show_tool():
    """Default shelf entry: bridge + standalone UI, with embedded fallback."""
    global _standalone_process
    try:
        if _standalone_process is not None and _standalone_process.poll() is None:
            print("[Houdini Agent] Startup: standalone UI already running")
            return _standalone_process
    except Exception:
        _standalone_process = None

    try:
        from houdini_agent.utils.bridge import ensure_bridge_running
        ok, msg, bridge_url = ensure_bridge_running()
        print(f"[Houdini Agent] Bridge: {msg} {bridge_url}")
        if ok and bridge_url:
            proc = _launch_standalone_ui(bridge_url)
            if proc is not None:
                _standalone_process = proc
                return proc
    except Exception as e:
        print(f"[Houdini Agent] Bridge startup failed, falling back to embedded UI: {e}")

    return show_embedded_tool()


def show_embedded_tool():
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
    try:
        from houdini_agent.ui.theme_engine import ThemeEngine
        ThemeEngine.install_application_scaling(app)
    except Exception as e:
        print(f"[Houdini Agent] Startup: text scaling setup failed: {e}")
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
