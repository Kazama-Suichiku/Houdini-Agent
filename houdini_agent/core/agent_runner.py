# -*- coding: utf-8 -*-
"""
Agent Runner — Agent 循环辅助：标题生成、确认模式、工具调度常量

从 ai_tab.py 中拆分出的 Mixin，负责：
- 自动 AI 标题生成
- 确认模式拦截
- 工具分类常量（Ask 模式白名单、后台安全工具、静默工具）
"""

import threading
import queue
from PySide6 import QtWidgets, QtCore
from ..ui.cursor_widgets import VEXPreviewInline


class AgentRunnerMixin:
    """Agent 循环辅助、工具调度常量"""

    # 需要用户确认的工具（确认模式下）
    _CONFIRM_TOOLS = frozenset({
        # 创建
        'create_wrangle_node',
        'create_node',
        'create_nodes_batch',
        # 删除 / 修改
        'delete_node',
        'set_node_parameter',
        'batch_set_parameters',
        'connect_nodes',
        'copy_node',
        'set_display_flag',
        # 代码执行
        'execute_python',
        'execute_shell',
        # 保存
        'save_hip',
        # NetworkBox（会修改场景）
        'create_network_box',
        'add_nodes_to_box',
    })

    # 不需要 Houdini 主线程的工具集合（纯 Python / 系统操作，可在后台线程直接执行）
    _BG_SAFE_TOOLS = frozenset({
        'execute_shell',       # subprocess.run，不依赖 hou
        'search_local_doc',    # 纯 Python 文本检索
        'list_skills',         # 纯 Python 列表
    })

    # 静默工具：不在执行列表 UI 中显示（AI 自行调用，用户无需感知）
    _SILENT_TOOLS = frozenset({
        'add_todo',
        'update_todo',
    })

    # ★ Ask 模式白名单：只读 / 查询 / 分析工具（不包含任何修改场景的操作）
    _ASK_MODE_TOOLS = frozenset({
        # 查询 & 检查
        'get_network_structure',
        'get_node_parameters',
        'list_children',
        'read_selection',
        'search_node_types',
        'semantic_search_nodes',
        'find_nodes_by_param',
        'get_node_inputs',
        'check_errors',
        'verify_and_summarize',
        # 文档 & 搜索
        'web_search',
        'fetch_webpage',
        'search_local_doc',
        'get_houdini_node_doc',
        # Skill（只读查看）
        'list_skills',
        # 任务管理
        'add_todo',
        'update_todo',
        # NetworkBox（只读查看）
        'list_network_boxes',
    })

    # ---------- 自动 AI 标题生成 ----------

    def _maybe_generate_title(self, session_id: str, history: list):
        """在 agent 完成后异步生成会话标题（仅首次）"""
        if not session_id:
            return
        # 检查该 session 是否已有 AI 生成的标题
        sdata = self._sessions.get(session_id)
        if not sdata:
            return
        if sdata.get('_ai_title_generated'):
            return
        
        # 仅在有足够上下文时生成（至少有 user + assistant）
        user_msgs = [m for m in history if m.get('role') == 'user']
        if not user_msgs:
            return
        
        # 取第一条用户消息和第一条助手回复作为标题生成的输入
        first_user = ''
        first_assistant = ''
        for m in history:
            if m.get('role') == 'user' and not first_user:
                c = m.get('content', '')
                first_user = c if isinstance(c, str) else str(c)
            elif m.get('role') == 'assistant' and not first_assistant:
                c = m.get('content', '')
                first_assistant = c if isinstance(c, str) else str(c)
            if first_user and first_assistant:
                break
        
        sdata['_ai_title_generated'] = True  # 标记防止重复
        
        # 后台线程异步生成标题
        def _gen():
            try:
                title = self._generate_short_title(first_user, first_assistant)
                if title:
                    self._autoTitleDone.emit(session_id, title)
            except Exception:
                pass
        
        t = threading.Thread(target=_gen, daemon=True)
        t.start()

    def _generate_short_title(self, user_msg: str, assistant_msg: str) -> str:
        """调用 LLM 生成 ≤10 字的对话标题"""
        # 截取前 200 字作为上下文
        ctx = f"用户: {user_msg[:200]}\nAI: {assistant_msg[:200]}"
        messages = [
            {'role': 'system', 'content': '你是一个标题生成器。根据对话内容生成一个简短的中文标题（≤10个字），只输出标题本身，不要引号、句号或其他多余内容。'},
            {'role': 'user', 'content': ctx}
        ]
        try:
            result = ''
            for chunk in self.client.chat_stream(messages):
                delta = chunk.get('content', '')
                if delta:
                    result += delta
            title = result.strip().strip('"\'""''。，.').strip()
            if title and len(title) <= 20:
                return title
            return title[:10] if title else ''
        except Exception:
            return ''

    @QtCore.Slot(str, str)
    def _on_auto_title_done(self, session_id: str, title: str):
        """AI 标题生成完成 — 更新 tab 标签"""
        if not title:
            return
        for i in range(self.session_tabs.count()):
            if self.session_tabs.tabData(i) == session_id:
                self.session_tabs.setTabText(i, title)
                break

    # ---------- 确认模式 — 内联预览确认 ----------

    @QtCore.Slot()
    def _on_confirm_tool_request(self):
        """主线程：在对话流中插入内联预览卡片，用户确认/取消后写入 _confirm_result_queue。
        
        参数通过 self._pending_confirm_* 属性传递（避免 PySide6 QueuedConnection 传 dict 的兼容性问题）。
        """
        q = getattr(self, '_confirm_result_queue', None)
        tool_name = getattr(self, '_pending_confirm_tool', 'unknown')
        args = getattr(self, '_pending_confirm_args', {})

        if not q:
            print(f"[ConfirmMode] ⚠ _confirm_result_queue 不存在")
            return

        # 确保 args 是 dict
        if not isinstance(args, dict):
            args = {"raw": str(args)}

        try:
            preview = VEXPreviewInline(tool_name, args, parent=self)
        except Exception as e:
            print(f"[ConfirmMode] ✖ VEXPreviewInline 创建失败: {e}")
            q.put(False)
            return

        def _accept():
            q.put(True)

        def _reject():
            q.put(False)

        preview.confirmed.connect(_accept)
        preview.cancelled.connect(_reject)

        # 插入到对话流
        resp = getattr(self, '_agent_response', None) or getattr(self, '_current_response', None)
        inserted = False
        if resp and hasattr(resp, 'details_layout'):
            try:
                resp.details_layout.addWidget(preview)
                inserted = True
            except Exception as e:
                print(f"[ConfirmMode] ⚠ details_layout 插入失败: {e}")

        if not inserted:
            try:
                self.chat_layout.insertWidget(self.chat_layout.count() - 1, preview)
                inserted = True
            except Exception as e:
                print(f"[ConfirmMode] ⚠ chat_layout 插入失败: {e}")

        if not inserted:
            # 最终降级：作为独立弹窗
            print("[ConfirmMode] ⚠ 所有布局插入失败，使用独立弹窗")
            preview.setParent(None)
            preview.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.WindowStaysOnTopHint)
            preview.resize(400, 120)
            preview.show()

        preview.setVisible(True)
        try:
            self._scroll_to_bottom(force=True)
        except Exception:
            pass

    def _request_tool_confirmation(self, tool_name: str, kwargs: dict) -> bool:
        """在确认模式下，在对话中插入内联预览让用户确认或取消。
        
        从后台线程调用，通过 QueuedConnection 信号在主线程创建预览控件。
        后台线程在 queue 上阻塞等待用户决策。
        ★ 参数通过属性传递，信号不携带参数（规避 PySide6 dict 序列化问题）。
        """
        self._confirm_result_queue = queue.Queue()
        self._pending_confirm_tool = tool_name
        self._pending_confirm_args = dict(kwargs) if kwargs else {}
        self._confirmToolRequest.emit()
        try:
            return self._confirm_result_queue.get(timeout=120.0)
        except queue.Empty:
            return False
