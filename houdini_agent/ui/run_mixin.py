# -*- coding: utf-8 -*-
"""
RunMixin — Agent run loop, tool execution dispatch, message normalization.
Extracted from ai_tab.py.
"""

import re
import threading

from houdini_agent.qt_compat import QtWidgets, QtCore

from .i18n import tr
from ..utils.ai_client import AIClient, HOUDINI_TOOLS
from ..utils.ultra_optimizer import UltraOptimizer
from ..utils.plan_manager import get_plan_manager, PLAN_TOOL_CREATE, PLAN_TOOL_UPDATE_STEP, PLAN_TOOL_ASK_QUESTION


class RunMixin:
    """Agent run loop, tool execution dispatch, message normalization."""

    def _on_agent_done(self, result: dict):
        stopped = bool(result.get('stopped'))
        stop_marker = "[Stopped by user]"
        # ★ Hook: on_session_end
        self._fire_session_hook('on_session_end', self._agent_session_id or self._session_id)

        # ★ 恢复 Houdini 更新模式 & 清除主线程忙标记
        self._main_thread_busy = False
        self._restore_update_mode()

        # ★ 停止思考指示条
        try:
            self.thinking_bar.stop()
        except (RuntimeError, AttributeError):
            pass

        # 使用 agent 锚定的引用（可能已切走 session）
        resp = self._agent_response or self._current_response
        history = self._agent_history if self._agent_history is not None else self._conversation_history
        stats = self._agent_token_stats or self._token_stats

        # 刷新标签解析缓冲区残余内容
        if self._tag_parse_buf:
            if self._in_think_block:
                if self._think_enabled:
                    self._addThinking.emit(self._tag_parse_buf)
                # Think 关闭时静默丢弃残余思考内容
            else:
                self._emit_normal_content(self._tag_parse_buf)
            self._tag_parse_buf = ""
            self._in_think_block = False

        # 刷新输出缓冲区（确保不丢失最后内容）
        if hasattr(self, '_output_buffer') and self._output_buffer:
            self._on_append_content(self._output_buffer)
            self._output_buffer = ""

        try:
            if resp:
                # ★ 后处理：将裸节点名自动解析为完整路径（防止长上下文中 AI 遗忘路径规范）
                if resp._content:
                    resp._content = self._resolve_bare_node_names(resp._content)
                resp.finalize()
                if stopped:
                    resp.add_status("Stopped")
        except RuntimeError:
            resp = None  # widget 已被 clear 销毁，跳过 UI 操作

        # ================================================================
        # Cursor 风格：保存原生消息链到对话历史
        # ================================================================
        # 格式：assistant(tool_calls) → tool → ... → assistant(reply)
        # 完整保留工具调用链和 AI 回复，不做任何压缩
        # 只有系统级上下文管理（_manage_context / _progressive_trim）才在超限时压缩

        tool_calls_history = result.get('tool_calls_history', [])
        new_messages = result.get('new_messages', [])
        assistant_history_start = len(history)

        # 1. 添加工具交互链（原生 OpenAI 格式）
        # new_messages 包含：assistant(tool_calls) + tool(results) + ...
        # ★ 只添加中间轮次（带 tool_calls 的 assistant 和 tool 回复），
        #   最终的纯文本 assistant 回复由下面步骤 2 统一构建，避免重复
        if new_messages:
            for nm in new_messages:
                if self._is_internal_viewport_message(nm):
                    clean = self._visible_viewport_message(nm)
                    history.append(clean)
                    if self._send_context is not None:
                        self._send_context.append(clean)
                    continue
                clean = nm.copy()
                clean.pop('reasoning_content', None)  # 推理模型专用，不需持久化
                # 跳过最后一条纯文本 assistant 消息（没有 tool_calls 的），
                # 它会在步骤 2 中作为 final_msg 添加
                if nm is new_messages[-1] and nm.get('role') == 'assistant' and not nm.get('tool_calls'):
                    continue
                history.append(clean)
                if self._send_context is not None:
                    self._send_context.append(clean)

        # 2. 提取并添加最终 AI 回复
        # 优先使用 final_content（最后一轮的纯文本），其次从 new_messages 提取
        final_content = result.get('final_content', '')
        if not final_content or not final_content.strip():
            # final_content 为空 → 尝试从 new_messages 中提取最后一个有 content 的 assistant 消息
            for nm in reversed(new_messages):
                if nm.get('role') == 'assistant' and nm.get('content'):
                    c = nm['content']
                    # 去掉 think 标签后还有内容吗？
                    stripped = re.sub(r'<think>[\s\S]*?</think>', '', c).strip()
                    if stripped:
                        final_content = c
                        break
            # 仍然为空 → 回退到 full_content
            if not final_content or not final_content.strip():
                final_content = result.get('content', '')

        thinking_text = ""
        clean_content = ""
        if final_content:
            thinking_parts = re.findall(r'<think>([\s\S]*?)</think>', final_content)
            thinking_text = '\n'.join(thinking_parts).strip() if thinking_parts else ''
            clean_content = re.sub(r'<think>[\s\S]*?</think>', '', final_content).strip()
            clean_content = self._strip_fake_tool_results(clean_content)
        if stopped:
            clean_content = (
                f"{clean_content}\n\n{stop_marker}".strip()
                if clean_content and stop_marker not in clean_content
                else (clean_content or stop_marker)
            )
        # 原生 thinking 协议（非 <think> 标签）：从 UI widget 获取已收集的 thinking
        if not thinking_text and resp and resp._has_thinking:
            try:
                sections = getattr(resp, '_thinking_sections', []) or []
                ui_thinking = '\n\n'.join(
                    s._thinking_text.strip() for s in sections
                    if getattr(s, '_thinking_text', '').strip()
                )
                if not ui_thinking and getattr(resp, 'thinking_section', None) is not None:
                    ui_thinking = resp.thinking_section._thinking_text.strip()
                if ui_thinking:
                    thinking_text = ui_thinking
            except (AttributeError, RuntimeError):
                pass

        # 确保历史以 assistant 消息结尾（维持 user→assistant 交替）
        # 只要有内容或有工具交互，都需要一条最终 assistant 消息
        need_final = bool(clean_content) or bool(new_messages) or not history or history[-1].get('role') != 'assistant'
        if need_final:
            final_msg = {'role': 'assistant', 'content': clean_content or (stop_marker if stopped else tr('ai.no_content'))}
            if thinking_text:
                final_msg['thinking'] = thinking_text
            # 提取 shell 执行记录，供历史恢复时重建 Shell 折叠面板
            py_shells = []
            sys_shells = []
            for tc in tool_calls_history:
                tn = tc.get('tool_name', '')
                ta = tc.get('arguments', {})
                tc_result = tc.get('result', {})
                if tn == 'execute_python' and ta.get('code'):
                    py_shells.append({
                        'code': ta['code'],
                        'output': tc_result.get('result', ''),
                        'error': tc_result.get('error', ''),
                        'success': bool(tc_result.get('success')),
                    })
                elif tn == 'execute_shell' and ta.get('command'):
                    sys_shells.append({
                        'command': ta['command'],
                        'output': tc_result.get('result', ''),
                        'error': tc_result.get('error', ''),
                        'success': bool(tc_result.get('success')),
                        'cwd': ta.get('cwd', ''),
                    })
            if py_shells:
                final_msg['python_shells'] = py_shells
            if sys_shells:
                final_msg['system_shells'] = sys_shells
            history.append(final_msg)
            if self._send_context is not None:
                self._send_context.append(final_msg)

        # 工作流经验候选自动沉淀：只进入审阅队列，不直接晋升。
        if resp and len(history) > assistant_history_start:
            try:
                resp.set_history_range(assistant_history_start, len(history))
            except RuntimeError:
                pass

        agent_sid_exp = self._agent_session_id or self._session_id
        if history and agent_sid_exp:
            history_snapshot = [m.copy() for m in history]
            def _do_experience_capture():
                self._queue_workflow_experience_candidate(agent_sid_exp, history_snapshot)
            exp_thread = threading.Thread(target=_do_experience_capture, daemon=True)
            exp_thread.start()

        # 管理上下文
        self._manage_context()

        # 更新 Token 统计（累积到 agent 所属 session 的 stats）—— 对齐 Cursor
        usage = result.get('usage', {})
        new_call_records = result.get('call_records', [])
        if usage:
            stats['input_tokens'] += usage.get('prompt_tokens', 0)
            stats['output_tokens'] += usage.get('completion_tokens', 0)
            stats['reasoning_tokens'] = stats.get('reasoning_tokens', 0) + usage.get('reasoning_tokens', 0)
            stats['cache_read'] += usage.get('cache_hit_tokens', 0)
            stats['cache_write'] += usage.get('cache_miss_tokens', 0)
            stats['total_tokens'] += usage.get('total_tokens', 0)
            stats['requests'] += 1

            # 计算本次费用并累积
            from houdini_agent.utils.token_optimizer import calculate_cost
            model_name = self.model_combo.currentText()
            this_cost = calculate_cost(
                model=model_name,
                input_tokens=usage.get('prompt_tokens', 0),
                output_tokens=usage.get('completion_tokens', 0),
                cache_hit=usage.get('cache_hit_tokens', 0),
                cache_miss=usage.get('cache_miss_tokens', 0),
                reasoning_tokens=usage.get('reasoning_tokens', 0),
            )
            stats['estimated_cost'] = stats.get('estimated_cost', 0.0) + this_cost

        # 合并 call_records
        if new_call_records:
            if not hasattr(self, '_call_records'):
                self._call_records = []
            self._call_records.extend(new_call_records)

        # 如果当前显示的就是 agent session，更新 UI
        if usage:
            if not self._agent_session_id or self._agent_session_id == self._session_id:
                self._update_token_stats_display()

            cache_hit = usage.get('cache_hit_tokens', 0)
            cache_miss = usage.get('cache_miss_tokens', 0)
            cache_rate = usage.get('cache_hit_rate', 0)

            if cache_hit > 0 or cache_miss > 0:
                rate_percent = cache_rate * 100
                self._addStatus.emit(f"Cache: {cache_hit}/{cache_hit+cache_miss} ({rate_percent:.0f}%)")

        # ★ 反思钩子：任务完成后触发长期记忆反思（后台线程，不阻塞 UI）
        if self._is_memory_active() and tool_calls_history:
            # 获取 agent_params（从最近的 _run_agent 调用中保存）
            _reflect_params = getattr(self, '_last_agent_params', {})
            def _do_reflect():
                self._reflect_after_task(result, _reflect_params)
            reflect_thread = threading.Thread(target=_do_reflect, daemon=True)
            reflect_thread.start()

        # 自动保存缓存（必须在 _set_running(False) 之前，因为此时 agent 引用还有效）
        agent_sid = self._agent_session_id
        if self._auto_save_cache and len(history) > 0 and agent_sid:
            # 临时将 history 同步到 sessions 字典，再保存
            if agent_sid in self._sessions:
                self._sessions[agent_sid]['conversation_history'] = history
                self._sessions[agent_sid]['token_stats'] = stats
            # 如果当前显示的恰好就是 agent session，直接保存
            if agent_sid == self._session_id:
                self._save_cache()
            else:
                # 不在当前 session 上，写入 session 字典即可（下次切换回来时再保存）
                pass

        self._set_running(False)

        # 隐藏工具状态
        self._hideToolStatus.emit()

        # 更新上下文统计
        self._update_context_stats()

        # ★ 异步生成会话标题（仅在首次 agent 完成时）
        self._maybe_generate_title(agent_sid, history)

    def _execute_tool_with_todo(self, tool_name: str, **kwargs) -> dict:
        """执行工具，包含 Todo 相关的工具

        注意：此方法在后台线程调用，Houdini 操作必须通过信号调度到主线程执行。
        不依赖 hou 模块的工具（execute_shell 等）直接在后台线程执行，避免阻塞 UI。
        """
        # ★ Stop 检测：用户请求停止时立即返回，不再排队新工具
        if self.client.is_stop_requested():
            return {"success": False, "error": "用户已请求停止"}

        # ★ 主线程忙保护：如果上一个工具超时了且主线程仍在 cook，
        #   不再堆积新的 BlockingQueuedConnection 信号（避免死锁）
        if getattr(self, '_main_thread_busy', False):
            if tool_name not in self._BG_SAFE_TOOLS:
                return {
                    "success": False,
                    "error": "主线程正忙（可能在进行耗时计算），请等待完成后重试。"
                            "建议：按停止按钮中断当前操作。"
                }

        # ★ Ask 模式安全守卫：拦截任何不在白名单的工具
        if not self._agent_mode and not self._plan_mode and tool_name not in self._ASK_MODE_TOOLS:
            # 额外检查 ToolRegistry（插件/Skill 工具可能注册了 ask 模式）
            _ask_allowed = False
            try:
                from ..utils.tool_registry import get_tool_registry
                _meta = get_tool_registry()._tools.get(tool_name)
                if _meta and _meta.enabled and "ask" in _meta.modes:
                    _ask_allowed = True
            except Exception:
                pass
            if not _ask_allowed:
                return {
                    "success": False,
                    "error": tr('ask.restricted', tool_name)
                }

        # ★ Plan 规划阶段安全守卫
        if self._plan_mode and self._plan_phase == 'planning':
            allowed = self._PLAN_PLANNING_TOOLS | {'create_plan'}
            if tool_name not in allowed:
                # 额外检查 ToolRegistry（插件/Skill 工具可能注册了 plan_planning 模式）
                _plan_allowed = False
                try:
                    from ..utils.tool_registry import get_tool_registry
                    _meta = get_tool_registry()._tools.get(tool_name)
                    if _meta and _meta.enabled and "plan_planning" in _meta.modes:
                        _plan_allowed = True
                except Exception:
                    pass
                if not _plan_allowed:
                    return {
                        "success": False,
                        "error": f"Plan 规划阶段不允许执行 {tool_name}，只能使用查询工具和 create_plan"
                    }

        # ★ 确认模式：对关键节点操作弹出预览确认
        if self._confirm_mode and tool_name in self._CONFIRM_TOOLS:
            confirmed = self._request_tool_confirmation(tool_name, kwargs)
            if not confirmed:
                return {
                    "success": False,
                    "error": tr('ask.user_cancel', tool_name)
                }

        # ★ 显示工具执行状态
        self._showToolStatus.emit(tool_name)

        try:
            # ★ Plan 模式专用工具处理
            if tool_name == "create_plan":
                return self._handle_create_plan(kwargs)

            elif tool_name == "update_plan_step":
                return self._handle_update_plan_step(kwargs)

            elif tool_name == "ask_question":
                return self._handle_ask_question(kwargs)

            # 处理 Todo 相关工具（纯 Python 操作，线程安全）
            if tool_name == "add_todo":
                todo_id = kwargs.get("todo_id", "")
                text = kwargs.get("text", "")
                status = kwargs.get("status", "pending")
                self._updateTodo.emit(todo_id, text, status)
                return {"success": True, "result": f"Added todo: {text}"}

            elif tool_name == "update_todo":
                todo_id = kwargs.get("todo_id", "")
                status = kwargs.get("status", "done")
                self._updateTodo.emit(todo_id, "", status)
                return {"success": True, "result": f"Updated todo {todo_id} to {status}"}

            elif tool_name == "verify_and_summarize":
                # 需要在主线程执行 Houdini 操作
                return self._execute_tool_in_main_thread(tool_name, kwargs)

            # 不依赖 hou 的工具 → 直接在后台线程执行（避免阻塞 UI）
            if tool_name in self._BG_SAFE_TOOLS:
                return self._execute_tool_in_bg(tool_name, kwargs)

            # 其他工具需要在主线程执行（Houdini hou 模块操作）
            return self._execute_tool_in_main_thread(tool_name, kwargs)
        finally:
            self._hideToolStatus.emit()

    @QtCore.Slot(str, dict)
    def _on_execute_tool_main_thread(self, tool_name: str, kwargs: dict):
        """在主线程执行工具（槽函数）

        注意：此方法在主线程中执行，直接操作 Houdini API 是安全的。
        所有修改操作包裹在 undo group 中，支持一键撤销整个 Agent 操作。
        ★ 对于未自带 checkpoint 的修改工具，会在执行前后快照网络子节点以检测变更。

        ★ macOS 线程安全说明：
        Houdini 的 hou 模块不是线程安全的。macOS 上 Cocoa/AppKit 要求 UI 和
        场景操作必须在主线程执行，否则会导致 EXC_BAD_ACCESS。
        此方法通过 BlockingQueuedConnection 信号从后台线程触发，保证在主线程执行。

        ★ Cook 保护（v1.4.3）：
        对可能触发 cook 的修改工具，在执行前临时切换为手动更新模式，
        执行完毕后恢复原模式。这样 setDisplayFlag/connect 等操作不会
        立即触发耗时的场景 cook，避免阻塞主线程导致死锁。
        """
        # ★ 主线程断言（调试辅助：如果在非主线程执行，输出警告）
        _app = QtWidgets.QApplication.instance()
        if _app and _app.thread() != QtCore.QThread.currentThread():
            print(f"[⚠️ THREAD SAFETY] _on_execute_tool_main_thread 不在主线程执行! "
                  f"tool={tool_name}, current_thread={QtCore.QThread.currentThread()}")

        result = {"success": False, "error": tr('ai.unknown_err')}

        # 判断是否为修改操作（需要 undo group）
        _MUTATING_TOOLS = {
            "create_node", "create_nodes_batch", "create_wrangle_node",
            "delete_node", "set_node_parameter", "connect_nodes",
            "copy_node", "batch_set_parameters", "set_display_flag",
            "execute_python", "save_hip", "run_skill",
        }
        use_undo_group = tool_name in _MUTATING_TOOLS

        # ★ Cook 策略（v1.6）：两种模式可在 Header 溢出菜单切换
        #   - 实时模式（默认 _cook_realtime_mode=True）：保持用户的 Auto 更新，
        #     不切 Manual；cook 在工具执行后同步测量并反馈（见 finally 中
        #     _realtime_cook_and_report）。便于用户实时看到视口变化。
        #   - 保护模式（v1.4.3 旧行为）：对 cook 类工具切 Manual，防止 cook
        #     阻塞主线程；模式恢复在 Agent 结束时统一处理（_restore_update_mode）。
        if tool_name in self._COOK_TRIGGERING_TOOLS and not self._cook_realtime_active():
            try:
                import hou  # type: ignore
                if hou.updateModeSetting() != hou.updateMode.Manual:
                    hou.setUpdateMode(hou.updateMode.Manual)
            except Exception:
                pass

        # ★ 读取前 Cook（v1.4.4）：当 Agent 处于 Manual 保护模式下，
        # 读取工具执行前先对当前显示节点做一次针对性 cook，
        # 确保 AI 能看到修改后的最新结果（而非 stale 数据）
        if tool_name in self._COOK_BEFORE_READ_TOOLS:
            self._cook_displayed_nodes_if_manual()

        # ★ 对不自带 checkpoint 追踪的修改工具，做 before/after 快照
        should_snapshot = (
            tool_name in _MUTATING_TOOLS
            and tool_name not in self._SELF_TRACKING_TOOLS
            and tool_name != 'save_hip'  # save 无需快照
        )
        before_children = self._snapshot_network_children() if should_snapshot else {}

        try:
            # 对修改操作开启 undo group
            if use_undo_group:
                try:
                    import hou  # type: ignore
                    hou.undos.beginGroup(f"AI Agent: {tool_name}")
                except Exception:
                    use_undo_group = False  # hou 不可用则跳过

            if tool_name == "verify_and_summarize":
                check_items = kwargs.get("check_items", [])
                expected = kwargs.get("expected_result", "")

                # 确保 check_items 是列表类型（防止 unhashable type: 'slice' 错误）
                if not isinstance(check_items, list):
                    if isinstance(check_items, str):
                        check_items = [check_items]
                    elif hasattr(check_items, '__iter__') and not isinstance(check_items, (dict, str)):
                        check_items = list(check_items)
                    else:
                        check_items = []

                # 获取当前网络结构进行验证
                ok, structure_data = self.mcp.get_network_structure()

                # 自动检测问题
                issues = []
                if ok and isinstance(structure_data, dict):
                    nodes = structure_data.get('nodes', [])
                    connections = structure_data.get('connections', [])

                    # 收集所有已连接的节点
                    connected_nodes = set()
                    for conn in connections:
                        from_path = conn.get('from', '')
                        to_path = conn.get('to', '')
                        if from_path:
                            connected_nodes.add(from_path.split('/')[-1])
                        if to_path:
                            connected_nodes.add(to_path.split('/')[-1])

                    # 检测问题
                    for node in nodes:
                        node_name = node.get('name', '')
                        # 检测错误节点
                        if node.get('has_errors'):
                            issues.append(tr('ai.err_issues', node_name))
                        # 检测孤立节点（非输出节点且未连接）
                        if node_name not in connected_nodes:
                            node_type = node.get('type', '').lower()
                            # 排除输出节点和根节点
                            if not any(x in node_type for x in ['output', 'null', 'out', 'merge']):
                                if not any(x in node_name.lower() for x in ['out', 'output', 'result']):
                                    issues.append(f"orphan:{node_name}")

                    # 检查是否有显示的输出节点
                    has_displayed = any(node.get('is_displayed') for node in nodes)
                    if not has_displayed and nodes:
                        issues.append(tr('ai.no_display'))

                # 生成验证结果
                if issues:
                    issues_str = ' | '.join(issues[:5])  # 最多显示5个问题
                    result = {
                        "success": True,
                        "result": tr('ai.check_fail', issues_str)
                    }
                else:
                    check_items_str = ', '.join(str(item) for item in check_items[:3]) if check_items else tr('ai.check_none')
                    result = {
                        "success": True,
                        "result": tr('ai.check_pass', expected[:30] if expected else 'done')
                    }
            else:
                # 其他工具交给 MCP 处理
                result = self.mcp.execute_tool(tool_name, kwargs)
        except Exception as e:
            result = {"success": False, "error": tr('ai.tool_exec_err', str(e))}
        finally:
            # ★ 执行后快照 & diff，检测节点变更
            if should_snapshot and result.get("success"):
                try:
                    after_children = self._snapshot_network_children()
                    changes = self._diff_network_children(before_children, after_children)
                    if changes:
                        result['_node_changes'] = changes
                except Exception:
                    pass  # 快照失败不影响工具结果

            # 关闭 undo group
            if use_undo_group:
                try:
                    import hou  # type: ignore
                    hou.undos.endGroup()
                except Exception:
                    pass

            # ★ Cook 保护恢复：不在单个工具 finally 中恢复更新模式
            # 而是在 Agent 结束时统一恢复（_restore_update_mode），
            # 避免中间工具恢复后触发耗时 cook 阻塞主线程

            # ★ 实时 Cook 模式（v1.6，默认）：保持 Auto，cook 类工具执行成功后
            # 同步 cook 当前 display 节点并测量耗时，把结果反馈给 Agent；
            # 用 hou.InterruptableOperation 包裹（Esc 可中断 + 进度提示）；
            # 慢 cook（>60s）追加警告，提醒 Agent 慎重并考虑切回 Manual 保护模式。
            if (self._cook_realtime_active()
                    and tool_name in self._COOK_TRIGGERING_TOOLS
                    and isinstance(result, dict) and result.get('success')):
                try:
                    self._realtime_cook_and_report(result)
                except Exception as _ce:
                    print(f"[Cook] 实时 cook 测量失败: {_ce}")

            # ★ 清除主线程忙标记
            # 无论工具执行成功或失败，主线程已经空闲
            self._main_thread_busy = False

            # ★ macOS 崩溃修复：不再在此处调用 processEvents()
            # ─────────────────────────────────────────────────────
            # 旧代码：QtWidgets.QApplication.processEvents()
            #
            # 为什么移除？
            # 1. 此槽函数通过 BlockingQueuedConnection 从后台线程触发，
            #    在 emit 返回前主线程事件循环不会处理新事件——这是设计意图。
            # 2. processEvents() 会在槽函数内部递归处理事件队列，可能导致：
            #    a) 递归触发另一个 _executeToolRequest 信号（死锁或重入）
            #    b) 触发 Houdini 场景事件、渲染回调等（与当前 hou 操作竞争）
            #    c) macOS Cocoa runloop 重入，导致 EXC_BAD_ACCESS 崩溃
            # 3. BlockingQueuedConnection 返回后，主线程事件循环自然会继续
            #    处理排队的事件——无需手动 processEvents。
            # ─────────────────────────────────────────────────────

            # 将结果放入队列（线程安全）
            self._tool_result_queue.put(result)

    @staticmethod
    def _fix_message_alternation(messages: list) -> list:
        """修复消息交替问题：合并连续的相同角色消息

        Cursor 风格消息格式支持：
        - user → assistant(tool_calls) → tool → assistant → user（正常格式）
        - 只合并连续的 user 或连续的 assistant（无 tool_calls 的）
        - 不合并带 tool_calls 的 assistant 消息（它们需要对应的 tool 结果）
        - tool 消息不参与合并
        """
        if not messages:
            return messages

        fixed = [messages[0]]
        for msg in messages[1:]:
            role = msg.get('role', '')
            prev_role = fixed[-1].get('role', '')

            # tool 消息永不合并（它们通过 tool_call_id 关联到 assistant）
            if role == 'tool' or prev_role == 'tool':
                fixed.append(msg)
                continue

            # 带 tool_calls 的 assistant 消息不合并（API 格式要求独立）
            if role == 'assistant' and msg.get('tool_calls'):
                fixed.append(msg)
                continue
            if prev_role == 'assistant' and fixed[-1].get('tool_calls'):
                fixed.append(msg)
                continue

            if role == prev_role and role in ('user', 'assistant'):
                # 合并连续的相同角色消息
                prev_content = fixed[-1].get('content')
                curr_content = msg.get('content')

                # ★ 多模态消息（content 是 list）不能直接用 + 拼接字符串
                # 策略：如果任一 content 是 list，提取文字部分再合并
                prev_text = prev_content
                curr_text = curr_content
                if isinstance(prev_content, list):
                    prev_text = '\n'.join(
                        p.get('text', '') for p in prev_content
                        if isinstance(p, dict) and p.get('type') == 'text'
                    ) or ''
                if isinstance(curr_content, list):
                    curr_text = '\n'.join(
                        p.get('text', '') for p in curr_content
                        if isinstance(p, dict) and p.get('type') == 'text'
                    ) or ''

                prev_text = prev_text or ''
                curr_text = curr_text or ''

                fixed[-1] = fixed[-1].copy()

                # 如果两边都是纯文本，直接拼接
                # 如果任一方是多模态 list，保留最后一个的图片部分 + 合并文字
                if isinstance(prev_content, list) or isinstance(curr_content, list):
                    # 合并为多模态格式：保留所有 text 和 image_url
                    merged_parts = []
                    combined_text = (prev_text + '\n\n' + curr_text).strip()
                    if combined_text:
                        merged_parts.append({'type': 'text', 'text': combined_text})
                    # 收集所有图片部分
                    for src in (prev_content, curr_content):
                        if isinstance(src, list):
                            for part in src:
                                if isinstance(part, dict) and part.get('type') == 'image_url':
                                    merged_parts.append(part)
                    fixed[-1]['content'] = merged_parts if merged_parts else combined_text
                else:
                    fixed[-1]['content'] = prev_text + '\n\n' + curr_text

                if 'thinking' in msg and msg['thinking']:
                    prev_thinking = fixed[-1].get('thinking', '')
                    fixed[-1]['thinking'] = (prev_thinking + '\n' + msg['thinking']).strip()
            else:
                fixed.append(msg)

        return fixed

    def _run_agent(self, agent_params: dict):
        """后台运行 Agent

        Args:
            agent_params: 从主线程获取的参数（避免在后台线程访问 Qt 控件）
                - provider: AI 提供商
                - model: 模型名称
                - use_web: 是否启用网页搜索
                - use_agent: 是否启用 Agent 模式
                - use_think: 是否启用思考模式
                - context_limit: 上下文限制
        """
        # ⚠️ 从参数获取值，不直接访问 Qt 控件（线程安全）
        provider = agent_params['provider']
        model = agent_params['model']
        use_web = agent_params['use_web']
        use_agent = agent_params['use_agent']
        use_think = agent_params.get('use_think', True)
        context_limit = agent_params['context_limit']
        scene_context = agent_params.get('scene_context', {})
        supports_vision = agent_params.get('supports_vision', True)
        plan_mode = agent_params.get('plan_mode', False)
        plan_executing = agent_params.get('plan_executing', False)

        # ★ 保存 agent_params 供反思钩子使用
        self._last_agent_params = agent_params

        # ★ 存储 Think 开关状态，供 _drain_tag_buffer / _on_thinking_chunk 使用
        self._think_enabled = use_think

        try:
            # ========================================
            # 🔥 Cache 优化：保持消息前缀稳定
            # ========================================
            # 消息结构：[系统提示] + [历史消息] + [上下文提醒+当前请求]
            # 前缀（系统提示+历史消息）保持稳定，提升 cache 命中率

            # 1. 系统提示词（根据思考模式选择版本）
            sys_prompt = self._cached_prompt_think if use_think else self._cached_prompt_no_think

            # ★ Ask 模式：追加只读约束
            if not use_agent and not plan_mode:
                sys_prompt = sys_prompt + tr('ai.ask_mode_prompt')

            # ★ Plan 模式：追加规划或执行阶段提示词
            if plan_mode:
                if plan_executing:
                    sys_prompt = sys_prompt + tr('ai.plan_mode_execution_prompt')
                else:
                    self._plan_phase = 'planning'
                    sys_prompt = sys_prompt + tr('ai.plan_mode_planning_prompt')

            # ★ Agent 模式：追加复杂任务建议切换 Plan 的提示
            if use_agent and not plan_mode:
                sys_prompt = sys_prompt + tr('ai.agent_suggest_plan_prompt')

            # ★ 个性注入：将成长系统形成的个性特征追加到 system prompt 末尾
            personality_text = self._get_personality_injection()
            if personality_text:
                sys_prompt = sys_prompt + "\n\n" + personality_text

            # ★ L0 核心记忆加载：全部加载到 sys_prompt（上限 5 条，按 confidence TopK）
            if self._is_memory_active():
                try:
                    core_mems = self._memory_store.get_core_memories(max_count=5)
                    if core_mems:
                        core_lines = [f"- {m.rule}" for m in core_mems]
                        sys_prompt = sys_prompt + (
                            "\n\n[Core Memory — 以下为核心记忆，仅供参考，请结合当前上下文判断]\n"
                            + "\n".join(core_lines)
                        )
                except Exception as e:
                    print(f"[Memory] L0 核心记忆加载失败: {e}")

            # ★ 用户自定义规则注入（类似 Cursor Rules）
            rules_text = self._get_user_rules_injection()
            if rules_text:
                sys_prompt = sys_prompt + "\n\n" + rules_text

            messages = [{'role': 'system', 'content': sys_prompt}]

            # ================================================================
            # 2. Cursor 风格历史消息：原生格式直通，不预压缩
            # ================================================================
            # 核心原则：
            # - assistant 消息完整保留（包括 content 和 tool_calls）
            # - tool 消息完整保留（包括 tool_call_id 和 content）
            # - user 消息完整保留
            # - 只清理内部元数据字段（thinking, python_shells 等）
            # - 压缩只在超限时由 _progressive_trim / auto_optimize 处理

            # 内部元数据字段列表（不发给 API）
            _INTERNAL_FIELDS = frozenset({
                '_reply_content', '_tool_summary', 'thinking',
                'python_shells', 'system_shells',
            })

            # ★ Cursor 风格：只保留当前轮次（最后一条 user 消息）的图片
            # 旧轮次的 image_url 剥离为纯文本，避免 base64 膨胀上下文
            # 使用 _send_context（已裁剪的工作副本），None 时降级到完整存档
            _ctx_src = self._send_context if self._send_context is not None else self._conversation_history
            _last_user_idx = None
            for _i in range(len(_ctx_src) - 1, -1, -1):
                if _ctx_src[_i].get('role') == 'user':
                    _last_user_idx = _i
                    break

            history_to_send = []
            for msg_idx, msg in enumerate(_ctx_src):
                role = msg.get('role', '')

                if role == 'tool':
                    # ★ 新格式（Cursor 风格）：保留原生 tool 消息 ★
                    # 必须有 tool_call_id 才能发给 API
                    if msg.get('tool_call_id'):
                        clean = {k: v for k, v in msg.items() if k not in _INTERNAL_FIELDS}
                        history_to_send.append(clean)
                    else:
                        # 旧格式 tool 消息（无 tool_call_id）→ 转为 assistant 文本
                        tool_name = msg.get('name', 'unknown')
                        content = msg.get('content', '')
                        history_to_send.append({
                            'role': 'assistant',
                            'content': tr('ai.tool_result', tool_name, content[:500])
                        })

                elif role == 'assistant':
                    # ★ 完整保留 assistant 消息 ★
                    clean = {}
                    for k, v in msg.items():
                        if k in _INTERNAL_FIELDS:
                            continue
                        clean[k] = v
                    # 如果是旧格式的 [工具执行结果] 文本，也原样保留
                    # content 完整传递，不做任何截断
                    # 同时保留 tool_calls（如果有的话 — 新格式）
                    history_to_send.append(clean)

                elif role == 'user':
                    # ★ Cursor 风格图片处理：
                    # - 当前轮次（最后一条 user）+ 视觉模型 → 保留图片
                    # - 旧轮次 或 非视觉模型 → 剥离 image_url，只保留文字
                    content = msg.get('content')
                    is_current_round = (msg_idx == _last_user_idx)

                    if isinstance(content, list):
                        if is_current_round and supports_vision:
                            # 当前轮 + 视觉模型：完整保留图片
                            history_to_send.append(msg)
                        else:
                            # 旧轮次 或 非视觉模型：剥离图片，只留文字
                            text_parts = []
                            for part in content:
                                if isinstance(part, dict) and part.get('type') == 'text':
                                    text_parts.append(part.get('text', ''))
                            text_only = '\n'.join(t for t in text_parts if t)
                            history_to_send.append({
                                'role': 'user',
                                'content': text_only or tr('ai.image_msg')
                            })
                    else:
                        # 纯文本消息：原样保留
                        history_to_send.append(msg)

                elif role == 'system':
                    # 系统消息（如历史摘要）保留
                    history_to_send.append(msg)

            # 修复 user/assistant 交替（仅处理连续的相同角色，不影响 tool 消息）
            history_to_send = self._fix_message_alternation(history_to_send)

            messages.extend(history_to_send)

            # 3. 自动 RAG 注入（从用户最新消息中提取关键词，检索相关文档）
            user_last_msg = ""
            if self._conversation_history:
                for msg in reversed(self._conversation_history):
                    if msg.get('role') == 'user':
                        raw_content = msg.get('content', '')
                        # 多模态内容（list）中提取文字部分
                        if isinstance(raw_content, list):
                            user_last_msg = ' '.join(
                                p.get('text', '') for p in raw_content if p.get('type') == 'text'
                            )
                        else:
                            user_last_msg = raw_content
                        break
            if user_last_msg:
                rag_context = self._auto_rag_retrieve(
                    user_last_msg,
                    scene_context=scene_context,
                    conversation_len=len(self._conversation_history),
                )
                if rag_context:
                    messages.append({'role': 'system', 'content': rag_context})

            # 4. ★ 长期记忆激活（"我想起来了"机制）
            # 在 RAG 文档之后、上下文提醒之前注入
            if user_last_msg:
                memory_context = self._activate_long_term_memory(
                    user_last_msg, scene_context=scene_context
                )
                if memory_context:
                    messages.append({'role': 'system', 'content': memory_context})

            # 5. ★ Plan 上下文注入（仅在 Plan 执行阶段 + 当前 session 匹配时）
            if plan_mode and plan_executing:
                try:
                    if self._plan_manager is None:
                        self._plan_manager = get_plan_manager()
                    plan_ctx = self._plan_manager.get_plan_for_context(self._session_id)
                    if plan_ctx:
                        messages.append({'role': 'system', 'content': plan_ctx})
                except Exception as e:
                    print(f"[Plan] Context injection error: {e}")

            # 6. 上下文提醒（放在最后，不破坏 cache 前缀）
            # ⚠️ Cache 优化：动态内容放在末尾，保持前缀稳定
            context_reminder = self._get_context_reminder()
            if context_reminder:
                # 将上下文提醒作为系统消息添加到末尾
                messages.append({'role': 'system', 'content': f"[Context] {context_reminder}"})

            # ================================================================
            # ★ 睡眠机制：浅睡眠（每 N 轮用户提问触发）
            # ================================================================
            if self._is_memory_active() and self._reflection_module:
                self._sleep_msg_counter += 1
                from ..utils.reflection import LIGHT_SLEEP_INTERVAL
                if self._sleep_msg_counter % LIGHT_SLEEP_INTERVAL == 0 and not self._sleep_in_progress:
                    # 收集最近 N 轮的消息用于浅睡眠总结
                    _sleep_messages = self._collect_recent_rounds(
                        self._conversation_history, LIGHT_SLEEP_INTERVAL
                    )
                    if _sleep_messages:
                        _sleep_sid = self._session_id
                        _sleep_model = model
                        _sleep_provider = provider
                        _sleep_client = self.client
                        _sleep_reflection = self._reflection_module
                        def _do_light_sleep():
                            self._sleep_in_progress = True
                            try:
                                result = _sleep_reflection.light_sleep(
                                    session_id=_sleep_sid,
                                    recent_messages=_sleep_messages,
                                    ai_client=_sleep_client,
                                    model=_sleep_model,
                                    provider=_sleep_provider,
                                )
                                if result.get("success"):
                                    self._addStatus.emit("💤 浅睡眠完成，经验已写入长期记忆")
                            finally:
                                self._sleep_in_progress = False
                        sleep_thread = threading.Thread(target=_do_light_sleep, daemon=True)
                        sleep_thread.start()

            # Cursor 风格预发送压缩：只压缩 tool 结果，保留 user/assistant 完整
            if self._auto_optimize:
                current_tokens = self.token_optimizer.calculate_message_tokens(messages)
                should_compress, _ = self.token_optimizer.should_compress(current_tokens, context_limit)

                if should_compress:
                    # ★ 深度睡眠：压缩前将完整上下文写入长期记忆
                    if self._is_memory_active() and self._reflection_module and not self._sleep_in_progress:
                        self._addStatus.emit("😴 深度睡眠：正在整理全部上下文为长期记忆...")
                        try:
                            self._sleep_in_progress = True
                            deep_result = self._reflection_module.deep_sleep(
                                session_id=self._session_id,
                                all_messages=self._conversation_history,
                                ai_client=self.client,
                                model=model,
                                provider=provider,
                            )
                            if deep_result.get("success"):
                                n_rules = len(deep_result.get("new_rules", []))
                                n_strats = len(deep_result.get("new_strategies", []))
                                self._addStatus.emit(
                                    f"😴 深度睡眠完成: {n_rules} 条经验 + {n_strats} 条策略已写入长期记忆"
                                )
                        except Exception as e:
                            print(f"[Sleep] 深度睡眠异常: {e}")
                        finally:
                            self._sleep_in_progress = False

                    old_tokens = current_tokens
                    # 分离系统提示和上下文提醒
                    first_system = messages[0] if messages and messages[0].get('role') == 'system' else None
                    last_context = messages[-1] if messages and ('[上下文]' in messages[-1].get('content', '') or '[Context]' in messages[-1].get('content', '')) else None
                    start_idx = 1 if first_system else 0
                    end_idx = -1 if last_context else len(messages)
                    body = messages[start_idx:end_idx] if end_idx != len(messages) else messages[start_idx:]

                    # 按 user 消息划分轮次
                    rounds = []
                    cur_rnd = []
                    for m in body:
                        if m.get('role') == 'user' and cur_rnd:
                            rounds.append(cur_rnd)
                            cur_rnd = []
                        cur_rnd.append(m)
                    if cur_rnd:
                        rounds.append(cur_rnd)

                    # 第一遍：压缩旧轮次 tool 结果
                    n_rounds = len(rounds)
                    protect_n = max(2, int(n_rounds * 0.6))
                    for r_idx in range(n_rounds - protect_n):
                        for m in rounds[r_idx]:
                            if m.get('role') == 'tool':
                                c = m.get('content') or ''
                                if len(c) > 200:
                                    m['content'] = self.client._summarize_tool_content(c, 200) if hasattr(self.client, '_summarize_tool_content') else c[:200] + '...[summary]'

                    compressed_body = [m for rnd in rounds for m in rnd]

                    # 如果仍超限，删除最早轮次
                    target = int(context_limit * 0.7)
                    while len(rounds) > 2:
                        test_body = [m for rnd in rounds for m in rnd]
                        test_msgs = ([first_system] if first_system else []) + test_body + ([last_context] if last_context else [])
                        if self.token_optimizer.calculate_message_tokens(test_msgs) <= target:
                            break
                        rounds.pop(0)

                    compressed_body = [m for rnd in rounds for m in rnd]

                    # 重组
                    messages = []
                    if first_system:
                        messages.append(first_system)
                    if n_rounds - len(rounds) > 0:
                        messages.append({
                            'role': 'system',
                            'content': tr('ai.old_rounds', n_rounds - len(rounds))
                        })
                    messages.extend(compressed_body)
                    if last_context:
                        messages.append(last_context)

                    new_tokens = self.token_optimizer.calculate_message_tokens(messages)
                    saved = old_tokens - new_tokens
                    if saved > 0:
                        self._addStatus.emit(tr('opt.auto_status', saved))

            # ⚠️ 使用从主线程传入的参数（不直接访问 Qt 控件）
            # provider, model, use_web, use_agent 已在方法开头从 agent_params 获取

            # 调试：显示正在请求
            self._addStatus.emit(f"Requesting {provider}/{model}...")

            # 推理模型兼容：清理消息格式
            is_reasoning_model = AIClient.is_reasoning_model(model)
            cleaned_messages = []
            for msg in messages:
                role = msg.get('role', 'user')
                content = msg.get('content')
                has_tool_calls = 'tool_calls' in msg

                clean_msg = {'role': role}

                # ★ Cursor 风格：assistant 有 tool_calls 时 content 可为 None ★
                # Claude/Anthropic 代理拒绝 content="" + tool_calls 共存
                if role == 'assistant' and has_tool_calls:
                    clean_msg['content'] = content  # 保留 None（不转为空字符串）
                else:
                    clean_msg['content'] = content if content is not None else ''

                # 推理模型：assistant 消息需要 reasoning_content 字段
                if is_reasoning_model and role == 'assistant':
                    clean_msg['reasoning_content'] = msg.get('reasoning_content', '')
                # 保留 tool_calls 字段
                if has_tool_calls:
                    clean_msg['tool_calls'] = msg['tool_calls']
                # 保留 tool_call_id 字段
                if 'tool_call_id' in msg:
                    clean_msg['tool_call_id'] = msg['tool_call_id']
                # 保留 name 字段（用于 tool 消息）
                if 'name' in msg:
                    clean_msg['name'] = msg['name']

                # ★ 清理 assistant content 中的 <think> 标签 ★
                # 历史中的 thinking 不需要发给 API（浪费 token）
                if role == 'assistant' and clean_msg.get('content'):
                    c = clean_msg['content']
                    if '<think>' in c:
                        c = re.sub(r'<think>[\s\S]*?</think>', '', c).strip()
                        clean_msg['content'] = c or None

                cleaned_messages.append(clean_msg)
            messages = cleaned_messages

            # 使用缓存的优化后工具定义（只计算一次）
            if plan_mode and not plan_executing:
                # ★ Plan 规划阶段：只读工具 + create_plan + ask_question
                plan_filtered = [t for t in HOUDINI_TOOLS
                                 if t['function']['name'] in self._PLAN_PLANNING_TOOLS]
                plan_filtered.append(PLAN_TOOL_CREATE)
                plan_filtered.append(PLAN_TOOL_ASK_QUESTION)
                if not use_web:
                    plan_filtered = [t for t in plan_filtered
                                     if t['function']['name'] not in ('web_search', 'fetch_webpage')]
                tools = UltraOptimizer.optimize_tool_definitions(plan_filtered)
            elif plan_mode and plan_executing:
                # ★ Plan 执行阶段：完整工具 + update_plan_step
                exec_tools = list(HOUDINI_TOOLS) + [PLAN_TOOL_UPDATE_STEP]
                if not use_web:
                    exec_tools = [t for t in exec_tools
                                  if t['function']['name'] not in ('web_search', 'fetch_webpage')]
                tools = UltraOptimizer.optimize_tool_definitions(exec_tools)
            elif not use_agent:
                # ★ Ask 模式：只保留只读/查询工具
                ask_filtered = [t for t in HOUDINI_TOOLS
                                if t['function']['name'] in self._ASK_MODE_TOOLS]
                if not use_web:
                    ask_filtered = [t for t in ask_filtered
                                    if t['function']['name'] not in ('web_search', 'fetch_webpage')]
                tools = UltraOptimizer.optimize_tool_definitions(ask_filtered)
            else:
                # ★ Agent 模式：使用全量工具
                # 注意：不做意图过滤。Agent 需要多轮迭代，可能先查询再创建再验证，
                # 意图过滤会导致后续迭代缺少必要工具（如 capture_viewport、create_node 等）。
                if use_web:
                    if self._cached_optimized_tools is None:
                        self._cached_optimized_tools = UltraOptimizer.optimize_tool_definitions(HOUDINI_TOOLS)
                    tools = self._cached_optimized_tools
                else:
                    if self._cached_optimized_tools_no_web is None:
                        filtered = [t for t in HOUDINI_TOOLS if t['function']['name'] not in ('web_search', 'fetch_webpage')]
                        self._cached_optimized_tools_no_web = UltraOptimizer.optimize_tool_definitions(filtered)
                    tools = self._cached_optimized_tools_no_web

            # ★ 合并外部工具（HookManager 插件工具 + ToolRegistry Skill 工具）
            try:
                from ..utils.hooks import get_hook_manager as _ghm_tools
                _ext = _ghm_tools().get_external_tools()
                if _ext:
                    tools = list(tools) + _ext
            except Exception:
                pass
            try:
                from ..utils.tool_registry import get_tool_registry
                _reg = get_tool_registry()
                # 获取 ToolRegistry 中 source=skill 的工具（避免与上面重复）
                _existing_names = {t.get('function', {}).get('name', '') for t in tools}
                for meta in _reg._tools.values():
                    if meta.source == "skill" and meta.enabled and meta.name not in _existing_names:
                        tools = list(tools) if not isinstance(tools, list) else tools
                        tools.append(meta.schema)
            except Exception:
                pass

            # ★ 记忆开关关闭时，从 tool schema 中剔除 search_memory，
            #   避免 LLM 在关闭长期记忆的情况下仍调用它读到污染性经验。
            if not self._is_memory_active():
                tools = [t for t in tools
                         if t.get('function', {}).get('name') != 'search_memory']

            # ★ 非视觉模型：capture_viewport 降级为仅保存文件（不注入图片）
            # 不再移除工具——AI 仍可截图保存让用户自行查看
            if not supports_vision:
                _degraded_tools = []
                for _t in tools:
                    if _t.get('function', {}).get('name') == 'capture_viewport':
                        import copy
                        _t_copy = copy.deepcopy(_t)
                        _t_copy['function']['description'] = (
                            "截取当前 Houdini 3D 视口快照并保存到文件。"
                            "当前模型不支持图片分析，截图将保存到 output_path 指定的路径供用户查看。"
                            "必须指定 output_path 参数。"
                        )
                        _degraded_tools.append(_t_copy)
                    else:
                        _degraded_tools.append(_t)
                tools = _degraded_tools

            # ★ Plan 模式的静默工具集合（不在 UI 中显示的工具）
            _silent = self._SILENT_TOOLS | self._PLAN_SILENT_TOOLS if plan_mode else self._SILENT_TOOLS

            # ★ 通用回调：每轮 API 迭代开始时显示 "Generating..." 状态
            # 第1轮也显示，填补 Send → 首字之间的空白
            _on_iter = lambda i: self._showGenerating.emit()

            if plan_mode:
                # ★ Plan 模式：使用 agent loop（规划或执行阶段均走此分支）
                _max_iter = 999 if plan_executing else 20

                # ★ Plan 续接回调：检测 AI 提前终止但 Plan 未完成的情况
                _plan_resume_callback = None
                _plan_resume_count = 0       # 防止无限续接
                _MAX_PLAN_RESUMES = 5        # 最多续接 5 次
                if plan_executing:
                    def _check_plan_incomplete():
                        nonlocal _plan_resume_count
                        if _plan_resume_count >= _MAX_PLAN_RESUMES:
                            print(f"[AI Client] Plan 续接次数已达上限 ({_MAX_PLAN_RESUMES})，停止续接")
                            return None
                        try:
                            if self._plan_manager is None:
                                from ..utils.plan_manager import get_plan_manager
                                self._plan_manager = get_plan_manager()
                            plan = self._plan_manager.load_plan(self._session_id)
                            if not plan:
                                return None
                            steps = plan.get('steps', [])
                            if not steps:
                                return None
                            done_count = sum(1 for s in steps if s.get('status') == 'done')
                            total = len(steps)
                            if done_count >= total:
                                return None  # 全部完成，正常结束

                            # 找到未完成的步骤
                            pending_steps = [s for s in steps if s.get('status') in ('pending', 'running')]
                            if not pending_steps:
                                return None

                            _plan_resume_count += 1
                            # 构造提醒消息
                            pending_names = ', '.join(
                                f'"{s.get("title", s.get("description", s["id"]))}"'
                                for s in pending_steps[:5]
                            )
                            # 获取最新的 Plan 上下文
                            plan_ctx = self._plan_manager.get_plan_for_context(self._session_id)
                            resume_msg = (
                                f"[Plan Incomplete] 计划尚未完成！已完成 {done_count}/{total} 步。\n"
                                f"未完成步骤: {pending_names}\n"
                                f"请立即继续执行下一个未完成的步骤。不要停止，不要总结，继续调用工具执行。\n"
                            )
                            if plan_ctx:
                                resume_msg += f"\n{plan_ctx}"
                            return resume_msg
                        except Exception as e:
                            print(f"[Plan] Incomplete check error: {e}")
                            return None
                    _plan_resume_callback = _check_plan_incomplete

                result = self.client.agent_loop_auto(
                    messages=messages,
                    model=model,
                    provider=provider,
                    max_iterations=_max_iter,
                    max_tokens=None,
                    enable_thinking=use_think,
                    supports_vision=supports_vision,
                    tools_override=tools,
                    context_limit=context_limit,
                    on_content=lambda c: self._on_content_with_limit(c),
                    on_thinking=lambda t: self._on_thinking_chunk(t),
                    on_tool_call=lambda n, a: (
                        None  # create_plan 已在 on_tool_args_delta 中处理
                        if n == 'create_plan' else
                        (self._addStatus.emit(f"[tool]{n}"), self._showToolStatus.emit(n))
                        if n not in _silent else None
                    ),
                    on_tool_result=lambda n, a, r: (
                        (self._add_tool_result(n, r, a), self._hideToolStatus.emit())
                        if n not in _silent else None
                    ),
                    on_tool_args_delta=lambda name, delta, acc: (
                        self._toolArgsDelta.emit(name, delta, acc)
                    ),
                    on_iteration_start=_on_iter,
                    on_plan_incomplete=_plan_resume_callback,
                )
            elif use_agent:
                # ★ Agent 模式：完整 agent loop，可创建/修改/删除节点
                result = self.client.agent_loop_auto(
                    messages=messages,
                    model=model,
                    provider=provider,
                    max_iterations=999,  # 不限制迭代次数
                    max_tokens=None,  # 不限制输出长度
                    enable_thinking=use_think,
                    supports_vision=supports_vision,
                    tools_override=tools,
                    context_limit=context_limit,
                    on_content=lambda c: self._on_content_with_limit(c),
                    on_thinking=lambda t: self._on_thinking_chunk(t),
                    on_tool_call=lambda n, a: (
                        (self._addStatus.emit(f"[tool]{n}"), self._showToolStatus.emit(n))
                        if n not in self._SILENT_TOOLS else None
                    ),
                    on_tool_result=lambda n, a, r: (
                        (self._add_tool_result(n, r, a), self._hideToolStatus.emit())
                        if n not in self._SILENT_TOOLS else None
                    ),
                    on_tool_args_delta=lambda name, delta, acc: (
                        self._toolArgsDelta.emit(name, delta, acc)
                    ),
                    on_iteration_start=_on_iter,
                )
            elif tools:
                # ★ Ask 模式：仍用 agent loop 但只提供只读工具
                result = self.client.agent_loop_auto(
                    messages=messages,
                    model=model,
                    provider=provider,
                    max_iterations=15,  # Ask 模式限制迭代（主要是查询）
                    max_tokens=None,
                    enable_thinking=use_think,
                    supports_vision=supports_vision,
                    tools_override=tools,  # ★ 只传入只读工具
                    context_limit=context_limit,
                    on_content=lambda c: self._on_content_with_limit(c),
                    on_thinking=lambda t: self._on_thinking_chunk(t),
                    on_tool_call=lambda n, a: (
                        (self._addStatus.emit(f"[tool]{n}"), self._showToolStatus.emit(n))
                        if n not in self._SILENT_TOOLS else None
                    ),
                    on_tool_result=lambda n, a, r: (
                        (self._add_tool_result(n, r, a), self._hideToolStatus.emit())
                        if n not in self._SILENT_TOOLS else None
                    ),
                    on_iteration_start=_on_iter,
                )
            else:
                # 无工具的纯对话模式（fallback）
                self._showGenerating.emit()  # ★ 显示 "Generating..." 等待首字
                result = {'ok': True, 'content': '', 'tool_calls_history': [], 'iterations': 1, 'usage': {}}
                for chunk in self.client.chat_stream(
                    messages=messages,
                    model=model,
                    provider=provider,
                    tools=None,
                    max_tokens=None,
                ):
                    if self.client.is_stop_requested():
                        result['ok'] = False
                        result['stopped'] = True
                        result['final_content'] = result.get('content', '')
                        self._agentDone.emit(result)
                        return

                    ctype = chunk.get('type')
                    if ctype == 'content':
                        content = chunk.get('content', '')
                        result['content'] += content
                        # 统一走 _on_content_with_limit（内含 <think> 解析）
                        self._on_content_with_limit(content)
                    elif ctype == 'thinking':
                        # 原生 reasoning_content
                        self._on_thinking_chunk(chunk.get('content', ''))
                    elif ctype == 'done':
                        # 收集 usage 统计
                        usage = chunk.get('usage', {})
                        if usage:
                            result['usage'] = usage
                    elif ctype == 'stopped':
                        result['ok'] = False
                        result['stopped'] = True
                        result['final_content'] = result.get('content', '')
                        self._agentDone.emit(result)
                        return
                    elif ctype == 'error':
                        result = {'ok': False, 'error': chunk.get('error')}
                        break

            if self.client.is_stop_requested():
                result['ok'] = False
                result['stopped'] = True
                result['final_content'] = result.get('content', '')
                self._agentDone.emit(result)
                return

            if result.get('ok') or result.get('stopped'):
                self._agentDone.emit(result)
            else:
                error_msg = result.get('error', 'Unknown error')
                # 显示更详细的错误
                self._agentError.emit(f"API Error: {error_msg}")

        except Exception as e:
            import traceback
            if self.client.is_stop_requested():
                self._agentDone.emit({
                    'ok': False,
                    'stopped': True,
                    'content': '',
                    'final_content': '',
                    'new_messages': [],
                    'tool_calls_history': [],
                    'call_records': [],
                    'iterations': 0,
                    'usage': {},
                })
            else:
                # 显示完整错误信息
                error_detail = f"{type(e).__name__}: {str(e)}"
                print(f"[AI Tab Error] {traceback.format_exc()}")  # 控制台输出
                self._agentError.emit(error_detail)
