# -*- coding: utf-8 -*-
"""
Input Area UI 构建 — 输入区域和模式切换

从 ai_tab.py 中拆分出的 Mixin，所有方法通过 self 访问 AITab 实例状态。
样式由全局 style_template.qss 通过 objectName 选择器控制。
"""

from houdini_agent.qt_compat import QtWidgets, QtCore
from .i18n import tr
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
        
        # -------- 输入行：模式下拉 + 输入框 --------
        input_row = QtWidgets.QHBoxLayout()
        input_row.setSpacing(6)
        
        # 模式下拉框（Agent / Ask）— 放在输入框左侧
        self._agent_mode = True  # True=Agent, False=Ask
        self._confirm_mode = False
        
        # 左侧垂直容器：模式下拉 + 确认开关
        left_col = QtWidgets.QVBoxLayout()
        left_col.setSpacing(4)
        left_col.setContentsMargins(0, 0, 0, 0)
        
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.setObjectName("modeCombo")
        self.mode_combo.addItem("Agent")
        self.mode_combo.addItem("Ask")
        self.mode_combo.setCurrentIndex(0)
        self.mode_combo.setProperty("mode", "agent")
        self.mode_combo.setCursor(QtCore.Qt.PointingHandCursor)
        self.mode_combo.setToolTip(tr('mode.tooltip'))
        self.mode_combo.setFixedWidth(100)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        left_col.addWidget(self.mode_combo)
        
        # 确认模式开关
        self.chk_confirm_mode = QtWidgets.QCheckBox(tr('confirm'))
        self.chk_confirm_mode.setObjectName("chkConfirm")
        self.chk_confirm_mode.setChecked(False)
        self.chk_confirm_mode.setCursor(QtCore.Qt.PointingHandCursor)
        self.chk_confirm_mode.setToolTip(tr('confirm.tooltip'))
        self.chk_confirm_mode.toggled.connect(self._on_confirm_mode_toggled)
        left_col.addWidget(self.chk_confirm_mode)
        
        input_row.addLayout(left_col)
        
        # 输入框（自适应高度）
        self.input_edit = ChatInput()
        self.input_edit.imageDropped.connect(self._on_image_dropped)
        self.input_edit.atTriggered.connect(self._on_at_triggered)
        input_row.addWidget(self.input_edit, 1)  # stretch=1 让输入框占满
        
        # 节点路径补全弹出框（传入 parent 防止创建时短暂闪烁）
        self._node_completer = NodeCompleterPopup(parent=self.input_edit)
        self._node_completer.pathSelected.connect(self._on_node_path_selected)
        # ★ 让输入框持有弹出框引用，支持键盘导航和自动关闭
        self.input_edit.set_completer_popup(self._node_completer)
        
        layout.addLayout(input_row)
        
        # 按钮行
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.setSpacing(6)
        
        # 图片附件按钮
        self.btn_attach_image = QtWidgets.QPushButton("Img")
        self.btn_attach_image.setObjectName("btnSmall")
        self.btn_attach_image.setToolTip(tr('attach_image.tooltip'))
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
        self.btn_export_train.setToolTip(tr('train.tooltip'))
        btn_layout.addWidget(self.btn_export_train)
        
        btn_layout.addStretch()
        
        # Token 统计按钮（可点击查看详情）
        self.token_stats_btn = QtWidgets.QPushButton("0")
        self.token_stats_btn.setObjectName("tokenStats")
        self.token_stats_btn.setToolTip(tr('header.token_stats.tooltip'))
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
        
        return container

    # ---------- 确认模式切换 ----------
    
    def _on_confirm_mode_toggled(self, checked: bool):
        self._confirm_mode = checked

    # ---------- Agent / Ask 模式切换（下拉框）----------

    def _on_mode_changed(self, index: int):
        """模式下拉框切换：0=Agent, 1=Ask"""
        self._agent_mode = (index == 0)
        mode = "agent" if self._agent_mode else "ask"
        # 更新下拉框颜色
        self.mode_combo.setProperty("mode", mode)
        self.mode_combo.style().unpolish(self.mode_combo)
        self.mode_combo.style().polish(self.mode_combo)
        # 更新发送按钮颜色
        self.btn_send.setProperty("mode", mode)
        self.btn_send.style().unpolish(self.btn_send)
        self.btn_send.style().polish(self.btn_send)

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

    def _retranslate_input_area(self):
        """语言切换后更新输入区域所有翻译文本"""
        self.mode_combo.setToolTip(tr('mode.tooltip'))
        self.chk_confirm_mode.setText(tr('confirm'))
        self.chk_confirm_mode.setToolTip(tr('confirm.tooltip'))
        self.input_edit.setPlaceholderText(tr('placeholder'))
        self.btn_attach_image.setToolTip(tr('attach_image.tooltip'))
        self.btn_export_train.setToolTip(tr('train.tooltip'))
        self.token_stats_btn.setToolTip(tr('header.token_stats.tooltip'))
