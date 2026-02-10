# -*- coding: utf-8 -*-
"""
交互式大窗口聊天界面 ChatWindow
- 复用 AITab 的 OpenAIClient 与 HoudiniMCP 实例
- 支持实时发送、接收与 MCP 指令解析
- 增加操作区域（删除节点、创建节点、查询文档等）
"""
from __future__ import annotations

from typing import List, Dict, Any, Optional

from PySide6 import QtWidgets, QtCore, QtGui
from .widgets import LoadingSpinner


class ChatWindow(QtWidgets.QDialog):
    # 定义信号用于跨线程通信
    _responseReady = QtCore.Signal(dict)
    
    def __init__(self, client, mcp, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI 对话 - 全屏模式")
        self.resize(1000, 720)
        # 使用非模态顶层窗口，确保不阻塞其他区域交互
        self.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.WindowMaximizeButtonHint | QtCore.Qt.WindowCloseButtonHint)
        self.setWindowModality(QtCore.Qt.NonModal)
        self.client = client
        self.mcp = mcp
        self._history: List[Dict[str, str]] = []
        
        # 连接响应信号
        self._responseReady.connect(self._handle_response)
        
        self._build_ui()
        self._wire_events()

    # --- lifecycle ---
    def load_history(self, messages: List[Dict[str, str]]):
        self._history = list(messages or [])
        # 渲染到视图
        self.chat_view.clear()
        for msg in self._history:
            role = msg.get('role')
            content = msg.get('content') or ''
            self._append(role, content)

    # --- UI ---
    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # 顶部操作条：常用 MCP 操作
        op_layout = QtWidgets.QHBoxLayout()
        self.btn_show_selection = QtWidgets.QPushButton("读取选中节点")
        self.include_all_params_check = QtWidgets.QCheckBox("包含所有参数")
        self.include_all_params_check.setToolTip("勾选后读取节点的所有参数（包括默认值）")
        self.btn_new_topic = QtWidgets.QPushButton("开启新话题")
        self.btn_new_topic.setToolTip("清空当前对话并开始新话题")
        op_layout.addWidget(self.btn_show_selection)
        op_layout.addWidget(self.include_all_params_check)
        op_layout.addStretch()
        op_layout.addWidget(self.btn_new_topic)

        layout.addLayout(op_layout)

        # 对话视图
        self.chat_view = QtWidgets.QTextBrowser()
        self.chat_view.setOpenExternalLinks(True)
        layout.addWidget(self.chat_view, 1)

        # 输入区
        bottom = QtWidgets.QHBoxLayout()
        self.input_edit = QtWidgets.QTextEdit()
        self.input_edit.setPlaceholderText("输入你的问题（Ctrl+Enter 发送）…")
        self.input_edit.setFixedHeight(140)
        bottom.addWidget(self.input_edit, 1)
        right = QtWidgets.QVBoxLayout()
        self.btn_send = QtWidgets.QPushButton("发送")
        self.btn_send.setMinimumHeight(48)
        right.addWidget(self.btn_send)
        
        # 旋转加载动画（初始隐藏）
        loading_container = QtWidgets.QHBoxLayout()
        loading_container.addStretch()
        self.loading_spinner = LoadingSpinner()
        self.loading_spinner.setVisible(False)
        loading_container.addWidget(self.loading_spinner)
        self.loading_label = QtWidgets.QLabel("正在请求...")
        self.loading_label.setStyleSheet("color:#1a73e8; font-weight:bold; margin-left:8px; padding:5px;")
        self.loading_label.setVisible(False)
        loading_container.addWidget(self.loading_label)
        loading_container.addStretch()
        right.addLayout(loading_container)
        
        right.addStretch()
        bottom.addLayout(right)
        layout.addLayout(bottom)

        # 主题：更大留白、更清晰按钮
        self.setStyleSheet("""
            QTextBrowser { font-size: 15px; }
            QTextEdit { font-size: 15px; }
            QPushButton { padding: 8px 12px; }
        """)

    def _wire_events(self):
        self.btn_send.clicked.connect(self._on_send)
        self.input_edit.installEventFilter(self)
        self.btn_show_selection.clicked.connect(self._on_show_selection)
        self.btn_new_topic.clicked.connect(self._on_new_topic)

    def eventFilter(self, obj, event):
        if obj is self.input_edit and event.type() == QtCore.QEvent.KeyPress:
            if (event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter)) and (event.modifiers() & QtCore.Qt.ControlModifier):
                self._on_send()
                return True
        return super().eventFilter(obj, event)

    # --- helpers ---
    def _append(self, role: str, text: str):
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M")
        if role == 'user':
            who = f"<b style='color:#2d7a2d'>You</b> <span style='color:#999;font-size:13px'>{ts}</span>"
        elif role == 'assistant':
            who = f"<b style='color:#1a73e8'>Assistant</b> <span style='color:#999;font-size:13px'>{ts}</span>"
        else:
            who = f"<b style='color:#555'>{role or 'Context'}</b> <span style='color:#999;font-size:13px'>{ts}</span>"
        doc = QtGui.QTextDocument()
        doc.setPlainText(text)
        self.chat_view.append(f"{who}: {doc.toHtml()}")
        self.chat_view.verticalScrollBar().setValue(self.chat_view.verticalScrollBar().maximum())

    def _send_and_render(self, text: str):
        # 先追加用户输入
        self._history.append({'role': 'user', 'content': text})
        self._append('user', text)
        # 禁用发送按钮，避免重复提交
        self.btn_send.setEnabled(False)
        self.loading_spinner.start()  # 启动旋转动画
        self.loading_label.setVisible(True)
        
        # 使用 Python 线程而不是 QThread
        import threading
        
        def _run_in_background():
            try:
                # 过滤掉 Context 消息，只发送 user/assistant/system 给 API
                filtered_history = [
                    msg for msg in self._history 
                    if msg.get('role') in ('user', 'assistant', 'system')
                ]
                # 设置合理的 token 上限和更长的超时时间,避免生成中断
                res = self.client.chat(
                    filtered_history, 
                    provider='deepseek', 
                    model='deepseek-chat', 
                    max_tokens=2048,  # 设置合理上限
                    timeout=120  # 增加超时时间
                )
            except Exception as e:
                res = {'ok': False, 'content': None, 'error': str(e), 'raw': None}
            
            # 通过信号发送结果
            self._responseReady.emit(res)
        
        thread = threading.Thread(target=_run_in_background, daemon=True)
        thread.start()
    
    def _handle_response(self, res: dict):
        """处理响应（在主线程）"""
        self.btn_send.setEnabled(True)
        self.loading_spinner.stop()  # 停止旋转动画
        self.loading_label.setVisible(False)
        if res.get('ok'):
            content = res.get('content') or ''
            self._history.append({'role': 'assistant', 'content': content})
            self._append('assistant', content)
            self._handle_mcp_commands(content)
            
            # 如果有重试信息，显示在上下文中
            info = res.get('info')
            if info:
                self._append('Context', info)
        else:
            QtWidgets.QMessageBox.warning(self, "错误", res.get('error') or '请求失败')

    # --- actions ---
    def _on_send(self):
        text = (self.input_edit.toPlainText() or '').strip()
        if not text:
            return
        self.input_edit.clear()
        self._send_and_render(text)

    def _on_show_selection(self):
        include_all_params = self.include_all_params_check.isChecked()
        ok, msg = self.mcp.describe_selection(include_all_params=include_all_params)
        if ok:
            self._history.append({'role': 'user', 'content': f"[节点信息]\n{msg}"})
            self._append('Context', msg)
        else:
            QtWidgets.QMessageBox.information(self, "提示", msg)

    # 已移除的手动操作：删除选中/创建节点/查询文档（改由 AI/MCP 指令驱动），故不再保留对应槽函数。

    # --- MCP 解析 ---
    def _handle_mcp_commands(self, content: str):
        if not content:
            return
        import json, re
        blocks = re.findall(r"```mcp\s*([\s\S]*?)```", content)
        if not blocks:
            return
        for blk in blocks:
            try:
                cmd = json.loads(blk.strip())
            except json.JSONDecodeError as e:
                # 提供更详细的错误信息
                error_msg = f"MCP 指令 JSON 解析失败：{e}\n"
                error_msg += f"错误位置：第 {e.lineno} 行，第 {e.colno} 列\n"
                error_msg += f"常见错误：字符串未用双引号、数组未用方括号[]、多余逗号"
                self._append('Context', error_msg)
                continue
            except Exception as e:
                self._append('Context', f"MCP 指令解析失败：{e}")
                continue
            act = (cmd.get('action') or '').lower()
            if act == 'create_node':
                # 支持 "parameters" 和 "parms" 两种写法
                params_dict = cmd.get('parameters') or cmd.get('parms')
                params = params_dict if isinstance(params_dict, dict) else None
                ok, msg = self.mcp.create_node(
                    cmd.get('type') or cmd.get('node_type') or '', 
                    cmd.get('name'),
                    params
                )
                self._append('Context', msg)
            elif act in ('create_nodes','create_network'):
                ok, msg = self.mcp.create_network(cmd.get('plan') if isinstance(cmd.get('plan'), dict) else cmd)
                self._append('Context', msg)
            elif act == 'connect_nodes':
                output_path = (
                    cmd.get('output_node_path')
                    or cmd.get('from')
                    or cmd.get('src')
                    or cmd.get('output')
                )
                input_path = (
                    cmd.get('input_node_path')
                    or cmd.get('to')
                    or cmd.get('dst')
                    or cmd.get('input_node')
                )
                input_index = cmd.get('input_index', cmd.get('input', 0))
                if not output_path or not input_path:
                    self._append('Context', "连接失败：缺少输出或输入节点路径（from/to）。")
                else:
                    ok, msg = self.mcp.connect_nodes(str(output_path), str(input_path), int(input_index or 0))
                    self._append('Context', msg)
            elif act in ('set_parameter', 'set_param', 'update_parameter'):
                node_path = cmd.get('node_path') or cmd.get('node') or cmd.get('path')
                param_name = cmd.get('param_name') or cmd.get('parameter') or cmd.get('param')
                value = cmd.get('value')
                if not node_path or not param_name or value is None:
                    self._append('Context', "设置参数失败：缺少 node_path、param_name 或 value。")
                else:
                    ok, msg = self.mcp.set_parameter(str(node_path), str(param_name), value)
                    self._append('Context', msg)
            elif act == 'delete_node':
                node_path = cmd.get('node_path') or cmd.get('path')
                if not node_path:
                    self._append('Context', "删除失败：缺少 node_path。")
                else:
                    ok, msg, _snapshot = self.mcp.delete_node_by_path(str(node_path))
                    self._append('Context', msg)
            elif act in ('delete_selection','delete_selected'):
                ok, msg = self.mcp.delete_selected()
                self._append('Context', msg)
            elif act == 'delete_nodes':
                node_paths = cmd.get('node_paths') or cmd.get('paths')
                node_ids = cmd.get('node_ids') or cmd.get('names')
                parent_path = cmd.get('parent_path')
                if node_paths and isinstance(node_paths, list):
                    ok, msg = self.mcp.delete_nodes_by_paths([str(p) for p in node_paths])
                elif node_ids and isinstance(node_ids, list):
                    ok, msg = self.mcp.delete_nodes_by_names([str(n) for n in node_ids], parent_path=parent_path)
                else:
                    ok, msg = False, "删除失败：请提供 node_paths（完整路径）或 node_ids（名称）。"
                self._append('Context', msg)
            elif act in ('delete_all','clear_children','clear_network'):
                ok, msg = self.mcp.delete_all_children(parent_path=cmd.get('parent_path'))
                self._append('Context', msg)
            else:
                # 友好的错误提示
                error_msg = f"错误: 未知的 MCP 动作：{act}\n\n"
                error_msg += "可用的 MCP 动作只有以下 4 个（必须精确匹配）：\n"
                error_msg += "1. create_nodes - 创建节点网络\n"
                error_msg += "2. set_parameter - 修改节点参数（完整拼写！）\n"
                error_msg += "3. connect_nodes - 连接节点\n"
                error_msg += "4. delete_node - 删除节点\n\n"
                
                # 常见错误提示
                if act in ('set_parm', 'set_parms', 'set_param', 'set_params', 'update_param', 'modify_param', 'update_parm'):
                    error_msg += "💡 提示：您使用了错误的拼写！正确的是 'set_parameter'（完整单词，不能缩写）"
                elif act in ('create_node', 'add_node'):
                    error_msg += "💡 提示：您可能想使用 'create_nodes'（创建节点）"
                elif act in ('connect', 'link_nodes'):
                    error_msg += "💡 提示：您可能想使用 'connect_nodes'（连接节点）"
                elif act in ('delete', 'remove_node'):
                    error_msg += "💡 提示：您可能想使用 'delete_node'（删除节点）"
                
                self._append('Context', error_msg)

    def _on_new_topic(self):
        if not self._history:
            QtWidgets.QMessageBox.information(self, "提示", "当前没有对话历史。")
            return
        reply = QtWidgets.QMessageBox.question(
            self,
            "确认清空",
            "确定要清空当前对话历史并开启新话题吗？\n这将无法恢复当前对话。",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        if reply == QtWidgets.QMessageBox.Yes:
            self._history.clear()
            self.chat_view.clear()
            self.input_edit.clear()
            self._append('Context', "已清空对话历史，开始新话题...")
