# -*- coding: utf-8 -*-
"""
Houdini Agent - 主窗口
支持工作区保存/恢复（窗口状态 + 上下文缓存）
"""

import os
import json
import atexit
from pathlib import Path
from houdini_agent.qt_compat import QtWidgets, QtGui, QtCore
from houdini_agent.ui.ai_tab import AITab


def _get_hou():
    try:
        import hou  # type: ignore
        return hou
    except Exception:
        return None


class MainWindow(QtWidgets.QMainWindow):
    """Houdini Agent 主窗口"""
    
    def __init__(self, parent=None, embedded: bool = True, mcp_client=None, bridge_url: str = ""):
        self._embedded = bool(embedded)
        self._mcp_client = mcp_client
        self._bridge_url = bridge_url or ""
        self._hou = _get_hou() if self._embedded else None
        # 尝试获取 Houdini 主窗口作为父窗口
        if self._embedded and parent is None and self._hou is not None:
            try:
                parent = self._hou.qt.mainWindow()
            except:
                pass
        
        super().__init__(parent)
        self.setProperty("houdiniAgentTextScaleRoot", True)
        self.setWindowTitle("Houdini Agent")
        self.setMinimumSize(420, 600)
        
        # 工作区配置目录
        self._workspace_dir = Path(__file__).parent.parent.parent / "cache" / "workspace"
        self._workspace_dir.mkdir(parents=True, exist_ok=True)
        self._workspace_file = self._workspace_dir / "workspace.json"
        
        # 不使用 WindowStaysOnTopHint，让窗口与 Houdini 同层级
        self.setWindowFlags(QtCore.Qt.Window)
        
        # 深邃蓝黑背景（与 aiTab glassmorphism 主题匹配）
        self.setStyleSheet("QMainWindow { background-color: #0a0a12; }")
        
        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)
        
        self.force_quit = False
        self._already_saved = False  # 防止重复保存
        
        self.init_ui(central_widget)
        
        # 加载工作区（窗口状态 + 上下文）
        self._load_workspace()
        
        # 注册多重退出保存钩子（确保 Houdini 退出时能保存）
        # 1. QApplication.aboutToQuit（Qt 正常退出时触发）
        app = QtWidgets.QApplication.instance()
        if app:
            app.aboutToQuit.connect(self._on_app_about_to_quit)
        # 2. atexit（Python 解释器关闭时触发）
        atexit.register(self._atexit_save)
        # 3. Houdini 专用：监听 hipFile 事件（切换场景时也保存）
        if self._embedded and self._hou is not None:
            try:
                self._hou.hipFile.addEventCallback(self._on_hip_event)
            except Exception:
                pass

    def init_ui(self, central_widget):
        """初始化UI"""
        layout = QtWidgets.QVBoxLayout(central_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        self.ai_tab = AITab(
            workspace_dir=self._workspace_dir,
            embedded=self._embedded,
            mcp_client=self._mcp_client,
            bridge_url=self._bridge_url,
        )
        layout.addWidget(self.ai_tab)

    def force_quit_application(self):
        """强制退出应用程序"""
        self.force_quit = True
        self.close()

    def _save_workspace(self):
        """保存工作区（窗口状态 + 所有会话缓存）"""
        try:
            geometry = self.geometry()
            window_state = {
                'x': geometry.x(),
                'y': geometry.y(),
                'width': geometry.width(),
                'height': geometry.height(),
                'is_maximized': self.isMaximized()
            }
            
            has_sessions = False
            tab_count = 0
            if hasattr(self, 'ai_tab') and self.ai_tab:
                has_sessions = self.ai_tab._save_all_sessions()
                tab_count = self.ai_tab.session_tabs.count()
            
            workspace_data = {
                'version': '1.1',
                'window_state': window_state,
                'cache_info': {
                    'has_conversation': has_sessions,
                    'tab_count': tab_count,
                    'use_manifest': True,
                }
            }
            
            with open(self._workspace_file, 'w', encoding='utf-8') as f:
                json.dump(workspace_data, f, ensure_ascii=False, indent=2)
            
            print(f"[Workspace] Saved: window({window_state['width']}x{window_state['height']}), {tab_count} session tabs")
            
        except Exception as e:
            print(f"[Workspace] Save failed: {str(e)}")
    
    def _load_workspace(self):
        """加载工作区（窗口状态 + 上下文缓存）"""
        try:
            if not self._workspace_file.exists():
                self.resize(450, 700)
                return
            
            with open(self._workspace_file, 'r', encoding='utf-8') as f:
                workspace_data = json.load(f)
            
            window_state = workspace_data.get('window_state', {})
            if window_state:
                x = window_state.get('x', 100)
                y = window_state.get('y', 100)
                width = window_state.get('width', 450)
                height = window_state.get('height', 700)
                is_maximized = window_state.get('is_maximized', False)
                
                self.setGeometry(x, y, width, height)
                if is_maximized:
                    self.setWindowState(QtCore.Qt.WindowMaximized)
            
            cache_info = workspace_data.get('cache_info', {})
            # ★ 始终尝试恢复（不再依赖 has_conversation 标志，
            #   即使上次退出时所有会话为空，manifest 或 cache_latest 中可能仍有内容）
            if hasattr(self, 'ai_tab'):
                # 延迟 200ms 确保 UI 完全初始化完毕
                QtCore.QTimer.singleShot(200, self._load_workspace_cache)
            
            print(f"[Workspace] Loaded: {self._workspace_file}")
            
        except Exception as e:
            print(f"[Workspace] Load failed: {str(e)}")
            self.resize(450, 700)
    
    def _load_workspace_cache(self):
        """延迟加载工作区缓存"""
        try:
            if not hasattr(self, 'ai_tab'):
                return
            
            if self.ai_tab._restore_all_sessions():
                return
            
            cache_dir = self.ai_tab._cache_dir
            latest_cache = cache_dir / "cache_latest.json"
            if latest_cache.exists():
                self.ai_tab._load_cache_silent(latest_cache)
        except Exception as e:
            print(f"[Workspace] Cache load failed: {str(e)}")
    
    def _on_app_about_to_quit(self):
        self._save_workspace_once()
    
    def _atexit_save(self):
        self._save_workspace_once()
    
    def _on_hip_event(self, event_type):
        try:
            if not self._embedded or self._hou is None:
                return
            if event_type in (self._hou.hipFileEventType.BeforeClear,
                              self._hou.hipFileEventType.BeforeLoad):
                self._save_workspace_once()
        except Exception:
            pass
    
    def _save_workspace_once(self):
        """确保退出时只保存一次（aboutToQuit / atexit / closeEvent 都可能触发）"""
        if self._already_saved or self.force_quit:
            return
        self._already_saved = True
        try:
            self._save_workspace()
        except Exception as e:
            print(f"[Workspace] Exit save failed: {e}")
    
    def closeEvent(self, event):
        if not self.force_quit:
            self._save_workspace()
        event.accept()
        super().closeEvent(event)
