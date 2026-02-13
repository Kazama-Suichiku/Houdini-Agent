# -*- coding: utf-8 -*-
"""
Input Area UI 构建 — 输入区域和模式切换

从 ai_tab.py 中拆分出的 Mixin，所有方法通过 self 访问 AITab 实例状态。
样式由全局 style_template.qss 通过 objectName 选择器控制。
"""

from houdini_agent.qt_compat import QtWidgets, QtCore
from .cursor_widgets import (
    CursorTheme,
    ChatInput,
    SendButton,
    StopButton,
    ToolStatusBar,
    NodeCompleterPopup,
    ThinkingBar,
)


class InputAreaMixin:
    """输入区域构建、模式切换、@提及、确认模式"""

    def _build_input_area(self) -> QtWidgets.QWidget:
        """输入区域"""
        container = QtWidgets.QFrame()
        container.setObjectName("inputArea")
        
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)
        
        # -------- Undo All / Keep All 批量操作栏（默认隐藏）--------
        self._batch_bar = QtWidgets.QFrame()
        self._batch_bar.setObjectName("batchBar")
        self._batch_bar.setVisible(False)
        batch_layout = QtWidgets.QHBoxLayout(self._batch_bar)
        batch_layout.setContentsMargins(8, 3, 8, 3)
        batch_layout.setSpacing(6)
        
        self._batch_count_label = QtWidgets.QLabel("")
        self._batch_count_label.setObjectName("batchCountLabel")
        batch_layout.addWidget(self._batch_count_label)
        batch_layout.addStretch()
        
        self._btn_undo_all = QtWidgets.QPushButton("Undo All")
        self._btn_undo_all.setObjectName("btnUndoAll")
        self._btn_undo_all.setCursor(QtCore.Qt.PointingHandCursor)
        self._btn_undo_all.clicked.connect(self._undo_all_ops)
        batch_layout.addWidget(self._btn_undo_all)
        
        self._btn_keep_all = QtWidgets.QPushButton("Keep All")
        self._btn_keep_all.setObjectName("btnKeepAll")
        self._btn_keep_all.setCursor(QtCore.Qt.PointingHandCursor)
        self._btn_keep_all.clicked.connect(self._keep_all_ops)
        batch_layout.addWidget(self._btn_keep_all)
        
        layout.addWidget(self._batch_bar)
        
        # -------- 思考中指示条（流光动画，默认隐藏）--------
        self.thinking_bar = ThinkingBar()
        layout.addWidget(self.thinking_bar)
        
        # 工具执行状态栏（显示当前正在调用的工具名，默认隐藏）
        self.tool_status_bar = ToolStatusBar()
        layout.addWidget(self.tool_status_bar)
        
        # 图片附件预览区（输入框上方，默认隐藏）
        self._pending_images = []  # List[Tuple[str, str, QPixmap]]  (base64_data, media_type, thumbnail)
        self.image_preview_container = QtWidgets.QWidget()
        self.image_preview_container.setVisible(False)
        self.image_preview_layout = QtWidgets.QHBoxLayout(self.image_preview_container)
        self.image_preview_layout.setContentsMargins(4, 2, 4, 2)
        self.image_preview_layout.setSpacing(4)
        self.image_preview_layout.addStretch()
        layout.addWidget(self.image_preview_container)
        
        # 输入框（自适应高度）
        self.input_edit = ChatInput()
        self.input_edit.imageDropped.connect(self._on_image_dropped)
        self.input_edit.atTriggered.connect(self._on_at_triggered)
        layout.addWidget(self.input_edit)
        
        # 节点路径补全弹出框（传入 parent 防止创建时短暂闪烁）
        self._node_completer = NodeCompleterPopup(parent=self.input_edit)
        self._node_completer.pathSelected.connect(self._on_node_path_selected)
        # ★ 让输入框持有弹出框引用，支持键盘导航和自动关闭
        self.input_edit.set_completer_popup(self._node_completer)
        
        # 按钮行
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.setSpacing(6)
        
        # 图片附件按钮
        self.btn_attach_image = QtWidgets.QPushButton("Img")
        self.btn_attach_image.setObjectName("btnSmall")
        self.btn_attach_image.setToolTip("添加图片附件（支持 PNG/JPG/GIF/WebP，也可直接粘贴/拖拽图片到输入框）")
        btn_layout.addWidget(self.btn_attach_image)
        
        # 快捷操作
        self.btn_network = QtWidgets.QPushButton("Read Network")
        self.btn_network.setObjectName("btnSmall")
        btn_layout.addWidget(self.btn_network)
        
        self.btn_selection = QtWidgets.QPushButton("Read Selection")
        self.btn_selection.setObjectName("btnSmall")
        btn_layout.addWidget(self.btn_selection)
        
        # 导出训练数据按钮
        self.btn_export_train = QtWidgets.QPushButton("Train")
        self.btn_export_train.setObjectName("btnSmall")
        self.btn_export_train.setToolTip("导出当前对话为训练数据（用于大模型微调）")
        btn_layout.addWidget(self.btn_export_train)
        
        btn_layout.addStretch()
        
        # Token 统计按钮（可点击查看详情）
        self.token_stats_btn = QtWidgets.QPushButton("0")
        self.token_stats_btn.setObjectName("tokenStats")
        self.token_stats_btn.setToolTip("点击查看详细 Token 统计")
        self.token_stats_btn.clicked.connect(self._show_token_stats_dialog)
        btn_layout.addWidget(self.token_stats_btn)
        
        # 上下文统计
        self.context_label = QtWidgets.QLabel("0K / 64K")
        self.context_label.setObjectName("contextLabel")
        btn_layout.addWidget(self.context_label)
        
        # 停止/发送
        self.btn_stop = StopButton()
        self.btn_stop.setVisible(False)
        self.btn_stop.setFixedHeight(32)
        btn_layout.addWidget(self.btn_stop)
        
        self.btn_send = SendButton()
        self.btn_send.setFixedHeight(32)
        btn_layout.addWidget(self.btn_send)
        
        layout.addLayout(btn_layout)
        
        # -------- 模式切换行：Agent / Ask（互斥 radio 风格复选框）--------
        mode_layout = QtWidgets.QHBoxLayout()
        mode_layout.setContentsMargins(0, 2, 0, 0)
        mode_layout.setSpacing(8)
        
        self._agent_mode = True  # True=Agent, False=Ask
        
        # Agent 复选框 — 灰色圆形指示器
        self.chk_mode_agent = QtWidgets.QCheckBox("Agent")
        self.chk_mode_agent.setObjectName("chkAgent")
        self.chk_mode_agent.setChecked(True)
        self.chk_mode_agent.setCursor(QtCore.Qt.PointingHandCursor)
        self.chk_mode_agent.setToolTip("Agent 模式：AI 可以自主创建、修改、删除节点，执行完整操作")
        self.chk_mode_agent.toggled.connect(self._on_agent_toggled)
        mode_layout.addWidget(self.chk_mode_agent)
        
        # Ask 复选框 — 绿色圆形指示器
        self.chk_mode_ask = QtWidgets.QCheckBox("Ask")
        self.chk_mode_ask.setObjectName("chkAsk")
        self.chk_mode_ask.setChecked(False)
        self.chk_mode_ask.setCursor(QtCore.Qt.PointingHandCursor)
        self.chk_mode_ask.setToolTip("Ask 模式：AI 只能查询和分析，不会修改场景（只读）")
        self.chk_mode_ask.toggled.connect(self._on_ask_toggled)
        mode_layout.addWidget(self.chk_mode_ask)
        
        # 确认模式开关 — 橙色
        self.chk_confirm_mode = QtWidgets.QCheckBox("确认")
        self.chk_confirm_mode.setObjectName("chkConfirm")
        self.chk_confirm_mode.setChecked(False)
        self.chk_confirm_mode.setCursor(QtCore.Qt.PointingHandCursor)
        self.chk_confirm_mode.setToolTip("确认模式：创建节点/VEX 前先预览确认，而非自动执行")
        self._confirm_mode = False
        self.chk_confirm_mode.toggled.connect(self._on_confirm_mode_toggled)
        mode_layout.addWidget(self.chk_confirm_mode)
        
        mode_layout.addStretch()
        layout.addLayout(mode_layout)
        
        return container

    # ---------- 确认模式切换 ----------
    
    def _on_confirm_mode_toggled(self, checked: bool):
        self._confirm_mode = checked

    # ---------- Agent / Ask 模式互斥切换 ----------

    def _on_agent_toggled(self, checked: bool):
        """Agent 复选框状态改变"""
        if self._agent_mode == checked:
            # 防止取消勾选自己（至少保持一个选中）
            if not checked:
                self.chk_mode_agent.blockSignals(True)
                self.chk_mode_agent.setChecked(True)
                self.chk_mode_agent.blockSignals(False)
            return
        self._agent_mode = checked
        # 互斥：勾选 Agent → 取消 Ask
        self.chk_mode_ask.blockSignals(True)
        self.chk_mode_ask.setChecked(not checked)
        self.chk_mode_ask.blockSignals(False)

    def _on_ask_toggled(self, checked: bool):
        """Ask 复选框状态改变"""
        is_agent = not checked
        if self._agent_mode == is_agent:
            # 防止取消勾选自己
            if not checked:
                self.chk_mode_ask.blockSignals(True)
                self.chk_mode_ask.setChecked(True)
                self.chk_mode_ask.blockSignals(False)
            return
        self._agent_mode = is_agent
        # 互斥：勾选 Ask → 取消 Agent
        self.chk_mode_agent.blockSignals(True)
        self.chk_mode_agent.setChecked(not checked)
        self.chk_mode_agent.blockSignals(False)

    # ---------- @提及节点自动补全 ----------

    def _on_at_triggered(self, prefix: str, cursor_rect):
        """用户在输入框键入 @，刷新节点列表并显示补全弹出框"""
        try:
            paths = self._collect_node_paths()
            if not paths:
                self._node_completer.setVisible(False)
                return
            self._node_completer.set_node_paths(paths)
            self._node_completer.show_filtered(prefix, self.input_edit, cursor_rect)
        except Exception:
            self._node_completer.setVisible(False)

    def _on_node_path_selected(self, path: str):
        """用户从补全弹出框中选择了节点路径"""
        self.input_edit.insert_at_completion(path)
        self._node_completer.setVisible(False)

    def _collect_node_paths(self) -> list:
        """收集当前场景中的节点路径列表（用于 @ 补全）"""
        try:
            import hou  # type: ignore
            paths = []
            # 收集常用上下文的节点路径
            for ctx in ['/obj', '/out', '/shop', '/mat', '/stage']:
                try:
                    node = hou.node(ctx)
                    if node:
                        for child in node.allSubChildren():
                            paths.append(child.path())
                except Exception:
                    continue
            return paths
        except ImportError:
            return []

    # ---------- 工具执行状态 ----------

    def _on_show_tool_status(self, tool_name: str):
        """在输入区域状态栏显示当前正在执行的工具"""
        try:
            self.tool_status_bar.show_tool(tool_name)
        except RuntimeError:
            pass

    def _on_hide_tool_status(self):
        """隐藏工具状态"""
        try:
            self.tool_status_bar.hide_tool()
        except RuntimeError:
            pass
