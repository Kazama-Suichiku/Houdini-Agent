# -*- coding: utf-8 -*-
"""
Plan Mixin — 计划模式 UI 逻辑

从 ai_tab.py 拆分：
  - _handle_create_plan / _handle_update_plan_step / _handle_ask_question
  - _on_render_ask_question / _show_plan_generation_progress
  - _on_create_streaming_plan / _on_update_streaming_plan / _flush_streaming_plan
  - _on_render_plan_viewer / _on_update_plan_step
  - _on_plan_confirmed / _on_plan_rejected
  - _SELF_TRACKING_TOOLS
"""

import queue
import threading

from houdini_agent.qt_compat import QtCore

from .cursor_widgets import AskQuestionCard, PlanViewer, StreamingPlanCard
from .i18n import tr
from ..utils.plan_manager import get_plan_manager


class PlanMixin:
    """Plan 模式：创建计划、流式预览、用户确认与执行"""

    def _handle_create_plan(self, kwargs: dict) -> dict:
        """处理 create_plan 工具调用（后台线程）"""
        try:
            if self._plan_manager is None:
                self._plan_manager = get_plan_manager()
            plan_data = self._plan_manager.create_plan(self._session_id, kwargs)
            self._plan_phase = 'awaiting_confirmation'
            # 切换状态：Planning → Generating（Plan 已完成构建）
            self._showGenerating.emit()
            # 通过信号在主线程渲染 PlanViewer 卡片
            self._renderPlanViewer.emit(plan_data)
            return {
                "success": True,
                "result": f"Plan '{plan_data.get('title', '')}' created with {len(plan_data.get('steps', []))} steps. Waiting for user confirmation."
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to create plan: {e}"}

    def _handle_update_plan_step(self, kwargs: dict) -> dict:
        """处理 update_plan_step 工具调用（后台线程）"""
        try:
            if self._plan_manager is None:
                self._plan_manager = get_plan_manager()
            step_id = kwargs.get('step_id', '')
            status = kwargs.get('status', 'done')
            result_summary = kwargs.get('result_summary', '')
            plan = self._plan_manager.update_step(
                self._session_id, step_id, status, result_summary
            )
            if not plan:
                return {"success": False, "error": f"No active plan found for session {self._session_id}"}
            # 通过信号在主线程更新 PlanViewer 步骤状态
            self._updatePlanStep.emit(step_id, status, result_summary or '')
            # 检查是否全部完成
            all_steps = plan.get('steps', [])
            done_count = sum(1 for s in all_steps if s.get('status') == 'done')
            error_count = sum(1 for s in all_steps if s.get('status') == 'error')
            total = len(all_steps)

            if plan.get('status') == 'completed':
                self._plan_phase = 'completed'
                return {
                    "success": True,
                    "result": f"Step {step_id} updated to '{status}'. Plan complete! ({done_count}/{total} done, {error_count} errors)"
                }

            # 返回进度信息，让 AI 知道还有多少步骤要做
            pending_steps = [s for s in all_steps if s.get('status') == 'pending']
            next_step_info = ""
            if pending_steps:
                ns = pending_steps[0]
                next_step_info = f" Next: {ns['id']} \"{ns.get('title', ns.get('description', ns['id']))}\""

            return {
                "success": True,
                "result": f"Step {step_id} updated to '{status}'. Progress: {done_count}/{total} done.{next_step_info}"
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to update plan step: {e}"}

    def _handle_ask_question(self, kwargs: dict) -> dict:
        """处理 ask_question 工具调用（后台线程）

        复用 _request_tool_confirmation 的阻塞模式：
        1. 设置 pending 属性 → 发射信号 → 主线程渲染 AskQuestionCard
        2. 后台线程在 queue 上阻塞等待用户回答
        3. 用户提交后 queue.put(answers) → 后台线程继续
        """
        questions = kwargs.get('questions', [])
        if not questions:
            return {"success": False, "error": "No questions provided"}

        self._ask_question_result_queue = queue.Queue()
        self._pending_ask_questions = questions
        self._askQuestionRequest.emit()

        try:
            result = self._ask_question_result_queue.get(timeout=300.0)  # 5 分钟超时
            if result is None:
                return {"success": True, "result": "User skipped the questions."}
            # 格式化答案为可读文本
            answer_lines = []
            for q_id, selections in result.items():
                readable = []
                for sel in selections:
                    if sel.startswith("__free_text__:"):
                        readable.append(sel.replace("__free_text__:", ""))
                    else:
                        readable.append(sel)
                answer_lines.append(f"{q_id}: {', '.join(readable)}")
            return {
                "success": True,
                "result": f"User answered:\n" + "\n".join(answer_lines)
            }
        except queue.Empty:
            return {"success": True, "result": "User did not answer within the time limit."}

    @QtCore.Slot()
    def _on_render_ask_question(self):
        """主线程：在聊天流中插入 AskQuestionCard"""
        q = getattr(self, '_ask_question_result_queue', None)
        questions = getattr(self, '_pending_ask_questions', [])

        if not q:
            print("[AskQuestion] ⚠ _ask_question_result_queue 不存在")
            return

        try:
            card = AskQuestionCard(questions, parent=self.chat_container)
        except Exception as e:
            print(f"[AskQuestion] ✖ AskQuestionCard 创建失败: {e}")
            q.put(None)
            return

        def _on_answered(answers: dict):
            q.put(answers)

        def _on_cancelled():
            q.put(None)

        card.answered.connect(_on_answered)
        card.cancelled.connect(_on_cancelled)

        # 插入到对话流
        try:
            self._insert_chat_widget(card)
        except Exception as e:
            print(f"[AskQuestion] ⚠ 插入失败: {e}")
            q.put(None)
            return

        card.setVisible(True)
        try:
            self._scroll_to_bottom(force=True)
        except Exception:
            pass

    @QtCore.Slot(dict)
    def _show_plan_generation_progress(self, accumulated: str):
        """从 create_plan 的流式参数中提取进度信息并显示 Planning... 状态"""
        import re as _re
        # 统计已出现的 step id
        step_ids = _re.findall(r'"id"\s*:\s*"(step-\d+)"', accumulated)
        # 尝试提取 title
        title_match = _re.search(r'"title"\s*:\s*"([^"]{1,30})', accumulated)
        title_part = title_match.group(1) if title_match else ""

        # 检查是否已进入 architecture 部分
        has_arch = '"architecture"' in accumulated
        arch_nodes = _re.findall(r'"id"\s*:\s*"(?!step-)([^"]+)"', accumulated)

        if has_arch and arch_nodes:
            progress = f"architecture ({len(arch_nodes)} nodes)"
        elif step_ids:
            progress = f"step {len(step_ids)}"
            if title_part:
                progress = f"「{title_part}」 {progress}"
        elif title_part:
            progress = f"「{title_part}」"
        else:
            progress = ""

        self._showPlanning.emit(progress)

    @QtCore.Slot()
    def _on_create_streaming_plan(self):
        """主线程：创建流式 Plan 预览卡片并插入聊天流"""
        try:
            # 如果已有旧的流式卡片则先移除
            if self._streaming_plan_card is not None:
                self._streaming_plan_card.setParent(None)
                self._streaming_plan_card.deleteLater()

            card = StreamingPlanCard(parent=self.chat_container)
            self._streaming_plan_card = card
            self._insert_chat_widget(card)
            self._scroll_to_bottom(force=True)
        except Exception as e:
            print(f"[Plan] Create streaming card error: {e}")

    @QtCore.Slot(str)
    def _on_update_streaming_plan(self, accumulated: str):
        """主线程：将流式 JSON 碎片增量渲染到流式 Plan 卡片

        使用简单的节流策略：缓存最新数据，通过 singleShot 延迟处理，
        避免每个 token 都触发正则解析和 UI 更新。
        """
        self._streaming_plan_acc = accumulated
        if not getattr(self, '_streaming_plan_timer_active', False):
            self._streaming_plan_timer_active = True
            QtCore.QTimer.singleShot(150, self._flush_streaming_plan)

    def _flush_streaming_plan(self):
        """实际执行流式 Plan 卡片更新"""
        self._streaming_plan_timer_active = False
        if self._streaming_plan_card is None:
            return
        acc = getattr(self, '_streaming_plan_acc', '')
        if not acc:
            return
        try:
            old_count = self._streaming_plan_card._rendered_step_count
            self._streaming_plan_card.update_from_accumulated(acc)
            new_count = self._streaming_plan_card._rendered_step_count
            if new_count > old_count:
                self._scroll_to_bottom()
        except Exception as e:
            print(f"[Plan] Update streaming card error: {e}")

    def _on_render_plan_viewer(self, plan_data: dict):
        """主线程：将流式 Plan 卡片原地升级为完整交互卡片。

        如果流式卡片已存在 → finalize_with_data 原地补充完整数据。
        如果不存在（边缘情况）→ 创建新卡片。
        """
        try:
            if self._streaming_plan_card is not None:
                # ★ 原地升级：在流式骨架上补充 DAG + 按钮
                card = self._streaming_plan_card
                card.finalize_with_data(plan_data)
                card.planConfirmed.connect(self._on_plan_confirmed)
                card.planRejected.connect(self._on_plan_rejected)
                self._active_plan_viewer = card
                self._streaming_plan_card = None  # 不再追踪为流式卡片
            else:
                # 边缘情况：没有流式卡片时直接创建 PlanViewer
                viewer = PlanViewer(plan_data, parent=self.chat_container)
                viewer.planConfirmed.connect(self._on_plan_confirmed)
                viewer.planRejected.connect(self._on_plan_rejected)
                self._active_plan_viewer = viewer
                self._insert_chat_widget(viewer)
            self._scroll_to_bottom(force=True)
        except Exception as e:
            print(f"[Plan] Render PlanViewer error: {e}")

    @QtCore.Slot(str, str, str)
    def _on_update_plan_step(self, step_id: str, status: str, result_summary: str):
        """主线程：更新 PlanViewer 卡片中的步骤状态"""
        if self._active_plan_viewer:
            try:
                self._active_plan_viewer.update_step_status(step_id, status, result_summary)
            except Exception as e:
                print(f"[Plan] Update step UI error: {e}")

    def _on_plan_confirmed(self, plan_data: dict):
        """用户点击 Confirm 按钮 → 启动执行阶段"""
        self._plan_phase = 'executing'
        # 禁用 PlanViewer 按钮（防止重复点击）
        if self._active_plan_viewer:
            self._active_plan_viewer.set_confirmed()

        # 构造执行提示消息
        exec_msg = tr('ai.plan_confirmed_msg', plan_data.get('title', 'Plan'))
        _pmsg = {'role': 'user', 'content': exec_msg}
        self._conversation_history.append(_pmsg)
        if self._send_context is not None:
            self._send_context.append(_pmsg)

        # 创建新的 AI 回复块
        self._set_running(True)
        self._add_ai_response()
        self._agent_response = self._current_response
        self._start_active_aurora()

        # 构造 agent_params（复用上次的 provider/model 设置）
        agent_params = getattr(self, '_last_agent_params', {}).copy()
        agent_params['use_agent'] = True          # 执行阶段用完整工具
        agent_params['plan_mode'] = True
        agent_params['plan_executing'] = True     # 标记为 Plan 执行阶段
        agent_params['plan_data'] = plan_data

        # 后台线程执行
        thread = threading.Thread(
            target=self._run_agent, args=(agent_params,), daemon=True
        )
        thread.start()

    def _on_plan_rejected(self):
        """用户点击 Reject 按钮 → 丢弃 Plan"""
        self._plan_phase = 'idle'
        try:
            if self._plan_manager is None:
                self._plan_manager = get_plan_manager()
            self._plan_manager.delete_plan(self._session_id)
        except Exception:
            pass
        if self._active_plan_viewer:
            self._active_plan_viewer.set_rejected()
        self._active_plan_viewer = None

    # 已自带 checkpoint 追踪的工具（在 _on_add_node_operation 中有专用分支）
    _SELF_TRACKING_TOOLS = frozenset({
        'create_node', 'create_nodes_batch', 'create_wrangle_node',
        'delete_node', 'set_node_parameter',
    })
