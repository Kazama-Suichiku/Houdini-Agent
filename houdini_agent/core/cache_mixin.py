# -*- coding: utf-8 -*-
"""
Cache Mixin — 会话缓存的保存、恢复与管理

从 ai_tab.py 拆分：
  - _on_cache_menu / _strip_images_for_cache / _build_cache_data
  - _on_destroyed / _periodic_save_all / _atexit_save
  - _save_cache / _update_manifest / _save_all_sessions
  - _restore_all_sessions / _archive_cache / _update_workspace_cache_info
  - _load_cache / _load_cache_silent / _load_cache_dialog / _list_caches
"""

import json
import uuid
import logging
from datetime import datetime
from pathlib import Path

from houdini_agent.qt_compat import QtWidgets, QtCore

logger = logging.getLogger(__name__)


class CacheMixin:
    """会话历史的磁盘缓存：保存、恢复、存档与列表"""

    def _on_cache_menu(self):
        """显示缓存菜单"""
        menu = QtWidgets.QMenu(self)

        # 保存存档（独立文件）
        archive_action = menu.addAction("存档当前对话")
        archive_action.triggered.connect(self._archive_cache)

        # 加载对话
        load_action = menu.addAction("加载对话...")
        load_action.triggered.connect(self._load_cache_dialog)

        menu.addSeparator()

        # 压缩为摘要（减少 token）
        compress_action = menu.addAction("压缩旧对话为摘要")
        compress_action.triggered.connect(self._compress_to_summary)

        # 列出所有缓存
        list_action = menu.addAction("查看所有缓存")
        list_action.triggered.connect(self._list_caches)

        menu.addSeparator()

        # 自动保存开关
        auto_save_action = menu.addAction("[on] 自动保存" if self._auto_save_cache else "自动保存")
        auto_save_action.setCheckable(True)
        auto_save_action.setChecked(self._auto_save_cache)
        auto_save_action.triggered.connect(lambda: setattr(self, '_auto_save_cache', not self._auto_save_cache))

        # 显示菜单
        menu.exec_(self.btn_cache.mapToGlobal(QtCore.QPoint(0, self.btn_cache.height())))

    @staticmethod
    def _strip_images_for_cache(history: list) -> list:
        """剥离 conversation_history 中的 base64 图片数据，
        用占位文本替代，大幅减小缓存文件体积。
        返回一份深拷贝，不修改原始 history。
        """
        import copy
        stripped = []
        for msg in history:
            content = msg.get('content')
            if isinstance(content, list):
                # 多模态消息：content 是 [{type:text,...}, {type:image_url,...}, ...]
                new_parts = []
                for part in content:
                    if part.get('type') == 'image_url':
                        url = part.get('image_url', {}).get('url', '')
                        if url.startswith('data:'):
                            # 替换 base64 为占位符，保留 media type 信息
                            media_type = url.split(';')[0].replace('data:', '')
                            new_parts.append({
                                'type': 'text',
                                'text': f'[Image: {media_type}]',
                            })
                        else:
                            new_parts.append(copy.copy(part))
                    else:
                        new_parts.append(copy.copy(part))
                new_msg = msg.copy()
                new_msg['content'] = new_parts
                stripped.append(new_msg)
            else:
                stripped.append(msg)  # 非多模态消息直接引用（str/None 不可变）
        return stripped

    def _build_cache_data(self) -> dict:
        """构建缓存数据字典"""
        todo_data = []
        if hasattr(self, 'todo_list') and self.todo_list:
            todo_data = self.todo_list.get_todos_data()
        return {
            'version': '1.0',
            'session_id': self._session_id,
            'created_at': datetime.now().isoformat(),
            'message_count': len(self._conversation_history),
            'estimated_tokens': self._calculate_context_tokens(),
            'conversation_history': self._conversation_history,
            'context_summary': self._context_summary,
            'todo_summary': self.todo_list.get_todos_summary() if hasattr(self, 'todo_list') else "",
            'todo_data': todo_data,
            'token_stats': self._token_stats.copy(),
        }

    def _on_destroyed(self):
        """Widget 被销毁时标记，防止旧实例的 atexit/aboutToQuit 回调覆盖新数据"""
        self._destroyed = True
        try:
            app = QtWidgets.QApplication.instance()
            if app:
                try:
                    app.aboutToQuit.disconnect(self._save_all_sessions)
                except (TypeError, RuntimeError):
                    pass
        except Exception:
            pass

    def _periodic_save_all(self):
        """定期保存所有会话（QTimer 触发 + aboutToQuit 触发）"""
        try:
            if not self._sessions:
                return
            # 只有存在对话时才保存
            has_any = False
            for sid, sdata in self._sessions.items():
                if sdata.get('conversation_history'):
                    has_any = True
                    break
            if not has_any:
                return
            self._save_all_sessions()
        except Exception as e:
            logger.warning("[Cache] 定期保存失败: %s", e)

    def _atexit_save(self):
        """Python 退出时的最后保存机会（atexit 回调）

        ★ 此时 Qt widget 可能已被销毁，因此：
        - 使用 _tabs_backup（纯 Python 列表）代替遍历 QTabBar
        - 使用 try/except 包裹 todo_list 访问
        - 如果 aboutToQuit 已成功保存过，则跳过（避免用不完整数据覆盖）
        - 如果 widget 已被销毁（旧实例），跳过以免覆盖新实例的数据
        """
        try:
            if getattr(self, '_destroyed', False):
                return
            if getattr(self, '_sessions_saved', False):
                return
            if not hasattr(self, '_sessions') or not self._sessions:
                return
            logger.debug("[Cache] atexit: 开始保存 (sessions=%d, backup=%d)", len(self._sessions), len(getattr(self, '_tabs_backup', [])))
            try:
                self._save_current_session_state()
            except (RuntimeError, AttributeError):
                pass

            tabs_info = getattr(self, '_tabs_backup', [])
            if not tabs_info:
                tabs_info = [(sid, f"Chat") for sid in self._sessions]

            manifest_tabs = []
            for sid, tab_label in tabs_info:
                if not sid or sid not in self._sessions:
                    continue
                sdata = self._sessions[sid]
                history = sdata.get('conversation_history', [])
                if not history:
                    manifest_tabs.append({
                        'session_id': sid,
                        'tab_label': tab_label,
                        'file': '',
                        'empty': True,
                    })
                    continue
                todo_data = []
                try:
                    todo_list_obj = sdata.get('todo_list')
                    todo_data = todo_list_obj.get_todos_data() if todo_list_obj else []
                except (RuntimeError, AttributeError, Exception):
                    pass
                cache_data = {
                    'version': '1.0',
                    'session_id': sid,
                    'message_count': len(history),
                    'conversation_history': self._strip_images_for_cache(history),
                    'context_summary': sdata.get('context_summary', ''),
                    'todo_data': todo_data,
                    'token_stats': sdata.get('token_stats', {}),
                }
                session_file = self._cache_dir / f"session_{sid}.json"
                with open(session_file, 'w', encoding='utf-8') as f:
                    json.dump(cache_data, f, ensure_ascii=False)
                manifest_tabs.append({
                    'session_id': sid,
                    'tab_label': tab_label,
                    'file': f"session_{sid}.json",
                })
            if not manifest_tabs:
                return
            # 防止用更少的 tab 数据覆盖已有的完整 manifest
            manifest_file = self._cache_dir / "sessions_manifest.json"
            try:
                if manifest_file.exists():
                    import json as _json
                    with open(manifest_file, 'r', encoding='utf-8') as f:
                        existing = _json.load(f)
                    existing_count = len(existing.get('tabs', []))
                    if existing_count > len(manifest_tabs):
                        return
            except Exception:
                pass
            manifest = {
                'version': '1.0',
                'active_session_id': self._session_id,
                'tabs': manifest_tabs,
            }
            with open(manifest_file, 'w', encoding='utf-8') as f:
                json.dump(manifest, f, ensure_ascii=False)
        except Exception:
            pass

    def _save_cache(self) -> bool:
        """自动保存：覆写同 session 文件 + manifest"""
        if not self._conversation_history:
            return False
        try:
            # 同步当前会话状态到 _sessions
            self._save_current_session_state()
            # ★ 同步 tab 备份
            self._sync_tabs_backup()

            cache_data = self._build_cache_data()
            # ★ 剥离 base64 图片以减小缓存文件大小
            cache_data['conversation_history'] = self._strip_images_for_cache(
                cache_data.get('conversation_history', [])
            )

            # 1. 覆写固定的 session 文件（一个 session 只有一个文件）
            session_file = self._cache_dir / f"session_{self._session_id}.json"
            with open(session_file, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)

            # 2. 同步更新 sessions_manifest.json（确保所有 tab 信息都是最新的）
            # ★ 不再写 cache_latest.json — 恢复由 sessions_manifest + session_*.json 管理
            self._update_manifest()

            if self._workspace_dir:
                self._update_workspace_cache_info()
            return True
        except Exception as e:
            logger.warning("[Cache] 自动保存失败: %s", e)
            return False

    def _update_manifest(self):
        """更新 sessions_manifest.json 以反映当前所有标签的状态"""
        try:
            manifest_tabs = []
            for i in range(self.session_tabs.count()):
                sid = self.session_tabs.tabData(i)
                if not sid:
                    continue
                tab_label = self.session_tabs.tabText(i)
                session_file = self._cache_dir / f"session_{sid}.json"
                if session_file.exists():
                    manifest_tabs.append({
                        'session_id': sid,
                        'tab_label': tab_label,
                        'file': f"session_{sid}.json",
                    })
                else:
                    sdata = self._sessions.get(sid, {})
                    history = sdata.get('conversation_history', [])
                    if history:
                        manifest_tabs.append({
                            'session_id': sid,
                            'tab_label': tab_label,
                            'file': f"session_{sid}.json",
                        })
                    else:
                        manifest_tabs.append({
                            'session_id': sid,
                            'tab_label': tab_label,
                            'file': '',
                            'empty': True,
                        })
            if manifest_tabs:
                manifest = {
                    'version': '1.0',
                    'active_session_id': self._session_id,
                    'tabs': manifest_tabs,
                }
                manifest_file = self._cache_dir / "sessions_manifest.json"
                with open(manifest_file, 'w', encoding='utf-8') as f:
                    json.dump(manifest, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("[Cache] 更新 manifest 失败: %s", e)

    def _save_all_sessions(self) -> bool:
        """保存所有打开的会话到磁盘（关闭软件时调用）"""
        if getattr(self, '_destroyed', False):
            return False
        try:
            # 先保存当前活跃会话的状态到 _sessions 字典
            try:
                self._save_current_session_state()
            except (RuntimeError, AttributeError):
                pass
            # ★ 同步 tab 备份（确保 atexit 时也能用）
            try:
                self._sync_tabs_backup()
            except (RuntimeError, AttributeError):
                pass

            manifest_tabs = []
            active_session_id = self._session_id

            # 从 QTabBar 获取 tab 列表；如果 Qt widget 已销毁则回退到纯 Python 备份
            tabs_list = []
            try:
                for i in range(self.session_tabs.count()):
                    sid = self.session_tabs.tabData(i)
                    tab_label = self.session_tabs.tabText(i)
                    if sid:
                        tabs_list.append((sid, tab_label))
            except (RuntimeError, AttributeError):
                tabs_list = getattr(self, '_tabs_backup', [])

            for sid, tab_label in tabs_list:
                if not sid or sid not in self._sessions:
                    continue

                sdata = self._sessions[sid]
                history = sdata.get('conversation_history', [])
                if not history:
                    # 空会话：清理磁盘残留，但仍记录到 manifest 以保留标签布局
                    try:
                        old_file = self._cache_dir / f"session_{sid}.json"
                        if old_file.exists():
                            old_file.unlink()
                    except Exception:
                        pass
                    manifest_tabs.append({
                        'session_id': sid,
                        'tab_label': tab_label,
                        'file': '',
                        'empty': True,
                    })
                    continue

                # 收集 todo 数据（防御 widget 已销毁的情况）
                todo_data = []
                try:
                    todo_list_obj = sdata.get('todo_list')
                    todo_data = todo_list_obj.get_todos_data() if todo_list_obj else []
                except (RuntimeError, AttributeError):
                    pass

                # 写 session 文件（★ 剥离 base64 图片以减小文件大小）
                cache_data = {
                    'version': '1.0',
                    'session_id': sid,
                    'created_at': datetime.now().isoformat(),
                    'message_count': len(history),
                    'conversation_history': self._strip_images_for_cache(history),
                    'context_summary': sdata.get('context_summary', ''),
                    'todo_data': todo_data,
                    'token_stats': sdata.get('token_stats', {}),
                }
                session_file = self._cache_dir / f"session_{sid}.json"
                with open(session_file, 'w', encoding='utf-8') as f:
                    json.dump(cache_data, f, ensure_ascii=False, indent=2)

                manifest_tabs.append({
                    'session_id': sid,
                    'tab_label': tab_label,
                    'file': f"session_{sid}.json",
                })

            if not manifest_tabs:
                return False

            manifest = {
                'version': '1.0',
                'active_session_id': active_session_id,
                'tabs': manifest_tabs,
            }
            manifest_file = self._cache_dir / "sessions_manifest.json"
            with open(manifest_file, 'w', encoding='utf-8') as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)

            self._sessions_saved = True
            logger.debug("[Cache] 已保存 %d 个会话标签 (tabs_list=%d, sessions=%d)", len(manifest_tabs), len(tabs_list), len(self._sessions))
            return True
        except Exception:
            logger.exception("[Cache] 保存所有会话失败")
            return False

    def _restore_all_sessions(self) -> bool:
        """从 sessions_manifest.json 恢复所有会话标签（启动时调用，幂等）"""
        # ★ 幂等保护：防止 __init__ 和 main_window 延迟回调重复恢复
        if getattr(self, '_sessions_restored', False):
            return True
        try:
            manifest_file = self._cache_dir / "sessions_manifest.json"
            if not manifest_file.exists():
                return False

            with open(manifest_file, 'r', encoding='utf-8') as f:
                manifest = json.load(f)

            tabs_info = manifest.get('tabs', [])
            if not tabs_info:
                return False

            active_sid = manifest.get('active_session_id', '')
            active_tab_index = 0
            first_tab = True

            for tab_info in tabs_info:
                sid = tab_info.get('session_id', '')
                tab_label = tab_info.get('tab_label', 'Chat')
                is_empty = tab_info.get('empty', False)

                history = []
                context_summary = ''
                todo_data = []
                saved_token_stats = {
                    'input_tokens': 0, 'output_tokens': 0,
                    'reasoning_tokens': 0,
                    'cache_read': 0, 'cache_write': 0,
                    'total_tokens': 0, 'requests': 0,
                    'estimated_cost': 0.0,
                }

                if not is_empty:
                    file_name = tab_info.get('file', '')
                    if not file_name:
                        continue
                    session_file = self._cache_dir / file_name
                    if not session_file.exists():
                        continue
                    with open(session_file, 'r', encoding='utf-8') as f:
                        cache_data = json.load(f)
                    history = cache_data.get('conversation_history', [])
                    if not history:
                        is_empty = True
                    else:
                        context_summary = cache_data.get('context_summary', '')
                        todo_data = cache_data.get('todo_data', [])
                        saved_token_stats = cache_data.get('token_stats', saved_token_stats)

                if first_tab:
                    # 第一个 tab：加载到已有的初始会话中
                    first_tab = False
                    old_id = self._session_id

                    self._session_id = sid
                    self._conversation_history = history
                    self._context_summary = context_summary
                    self._token_stats = saved_token_stats

                    if old_id in self._sessions:
                        sdata = self._sessions.pop(old_id)
                        sdata['conversation_history'] = history
                        sdata['context_summary'] = context_summary
                        sdata['token_stats'] = saved_token_stats
                        self._sessions[sid] = sdata
                    elif sid not in self._sessions:
                        self._sessions[sid] = {
                            'scroll_area': self.scroll_area,
                            'chat_container': self.chat_container,
                            'chat_layout': self.chat_layout,
                            'todo_list': self.todo_list,
                            'conversation_history': history,
                            'context_summary': context_summary,
                            'current_response': None,
                            'token_stats': saved_token_stats,
                        }

                    if todo_data and hasattr(self, 'todo_list') and self.todo_list:
                        self.todo_list.restore_todos(todo_data)
                        self._ensure_todo_in_chat(self.todo_list, self.chat_layout)

                    for i in range(self.session_tabs.count()):
                        if self.session_tabs.tabData(i) == old_id:
                            self.session_tabs.setTabData(i, sid)
                            self.session_tabs.setTabText(i, tab_label)
                            if sid == active_sid:
                                active_tab_index = i
                            break

                    if not is_empty:
                        self._render_conversation_history()
                else:
                    # 后续 tab：创建新标签
                    self._save_current_session_state()
                    self._session_counter += 1

                    scroll_area, chat_container, chat_layout = self._create_session_widgets()
                    self.session_stack.addWidget(scroll_area)

                    tab_index = self.session_tabs.addTab(tab_label)
                    self.session_tabs.setTabData(tab_index, sid)

                    todo = self._create_todo_list(chat_container)
                    if todo_data:
                        todo.restore_todos(todo_data)
                        self._ensure_todo_in_chat(todo, chat_layout)

                    self._sessions[sid] = {
                        'scroll_area': scroll_area,
                        'chat_container': chat_container,
                        'chat_layout': chat_layout,
                        'todo_list': todo,
                        'conversation_history': history,
                        'context_summary': context_summary,
                        'current_response': None,
                        'token_stats': saved_token_stats,
                    }

                    if not is_empty:
                        # 临时切换到该标签以渲染历史
                        old_scroll = self.scroll_area
                        old_chat_container = self.chat_container
                        old_chat_layout = self.chat_layout
                        old_todo = self.todo_list
                        old_history = self._conversation_history
                        old_summary = self._context_summary
                        old_stats = self._token_stats
                        old_sid = self._session_id

                        self._session_id = sid
                        self._conversation_history = history
                        self._context_summary = context_summary
                        self._token_stats = saved_token_stats
                        self.scroll_area = scroll_area
                        self.chat_container = chat_container
                        self.chat_layout = chat_layout
                        self.todo_list = todo

                        self._render_conversation_history()

                        self._session_id = old_sid
                        self._conversation_history = old_history
                        self._context_summary = old_summary
                        self._token_stats = old_stats
                        self.scroll_area = old_scroll
                        self.chat_container = old_chat_container
                        self.chat_layout = old_chat_layout
                        self.todo_list = old_todo

                    if sid == active_sid:
                        active_tab_index = tab_index

            # 切换到之前活跃的标签
            if self.session_tabs.count() > 0:
                self.session_tabs.blockSignals(True)
                self.session_tabs.setCurrentIndex(active_tab_index)
                self.session_tabs.blockSignals(False)

                target_sid = self.session_tabs.tabData(active_tab_index)
                if target_sid and target_sid in self._sessions:
                    self._load_session_state(target_sid)
                    self.session_stack.setCurrentWidget(
                        self._sessions[target_sid]['scroll_area']
                    )

            # ★ 恢复完成后同步 tab 备份并更新 UI 显示
            self._sync_tabs_backup()
            self._update_token_stats_display()
            self._update_context_stats()
            self._sessions_restored = True  # 标记已恢复，防止重复
            logger.debug("[Cache] 已恢复 %d 个会话标签", self.session_tabs.count())
            return True

        except Exception:
            logger.exception("[Cache] 恢复多会话失败")
            return False

    def _archive_cache(self) -> bool:
        """手动存档：创建带时间戳的独立文件（不会被覆写）"""
        if not self._conversation_history:
            QtWidgets.QMessageBox.information(self, "提示", "没有对话历史可存档")
            return False
        try:
            cache_data = self._build_cache_data()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"archive_{self._session_id}_{timestamp}.json"
            archive_file = self._cache_dir / filename
            with open(archive_file, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
            est = cache_data['estimated_tokens']
            self._addStatus.emit(f"已存档: {filename} (~{est} tokens)")
            return True
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "错误", f"存档失败: {str(e)}")
            return False

    def _update_workspace_cache_info(self):
        """更新工作区中的缓存信息（供主窗口保存工作区时使用）"""
        # 这个方法会被主窗口调用，用于更新工作区配置
        # 实际保存由主窗口的 _save_workspace 完成
        pass

    def _load_cache(self, cache_file: Path, silent: bool = False) -> bool:
        """从缓存文件加载对话历史（在新标签页中打开）

        Args:
            cache_file: 缓存文件路径
            silent: 是否静默加载（不显示确认对话框，用于工作区自动恢复）
        """
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)

            # 验证数据格式
            if 'conversation_history' not in cache_data:
                if not silent:
                    QtWidgets.QMessageBox.warning(self, "错误", "缓存文件格式无效")
                return False

            # 确认加载（静默模式下跳过）
            if not silent:
                msg_count = len(cache_data.get('conversation_history', []))
                reply = QtWidgets.QMessageBox.question(
                    self, "确认加载",
                    f"将在新标签页加载 {msg_count} 条对话记录。\n是否继续？",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
                )

                if reply != QtWidgets.QMessageBox.Yes:
                    return False

            history = cache_data.get('conversation_history', [])
            context_summary = cache_data.get('context_summary', '')
            todo_data = cache_data.get('todo_data', [])
            cached_session_id = cache_data.get('session_id', str(uuid.uuid4())[:8])
            # ★ 恢复 token 使用统计
            saved_token_stats = cache_data.get('token_stats', {
                'input_tokens': 0, 'output_tokens': 0,
                'reasoning_tokens': 0,
                'cache_read': 0, 'cache_write': 0,
                'total_tokens': 0, 'requests': 0,
                'estimated_cost': 0.0,
            })

            if silent and not self._conversation_history:
                # 静默恢复：当前会话为空时直接加载到当前标签
                self._conversation_history = history
                self._context_summary = context_summary
                self._session_id = cached_session_id
                self._token_stats = saved_token_stats
                # 恢复 todo 数据
                if todo_data and hasattr(self, 'todo_list') and self.todo_list:
                    self.todo_list.restore_todos(todo_data)
                    self._ensure_todo_in_chat(self.todo_list, self.chat_layout)
                # 更新 sessions 字典
                if self._session_id in self._sessions:
                    self._sessions[self._session_id]['conversation_history'] = self._conversation_history
                    self._sessions[self._session_id]['context_summary'] = self._context_summary
                    self._sessions[self._session_id]['token_stats'] = saved_token_stats
                elif self._sessions:
                    # 旧 session_id 已经变了，需要重新映射
                    old_id = list(self._sessions.keys())[0]
                    sdata = self._sessions.pop(old_id)
                    sdata['conversation_history'] = self._conversation_history
                    sdata['context_summary'] = self._context_summary
                    sdata['token_stats'] = saved_token_stats
                    self._sessions[self._session_id] = sdata
                    # 更新标签数据
                    for i in range(self.session_tabs.count()):
                        if self.session_tabs.tabData(i) == old_id:
                            self.session_tabs.setTabData(i, self._session_id)
                            break
                self._render_conversation_history()
                self._update_token_stats_display()
                self._update_context_stats()
                # 自动重命名标签
                if history:
                    for msg in history:
                        if msg.get('role') == 'user' and msg.get('content'):
                            self._auto_rename_tab(msg['content'])
                            break
                print(f"[Workspace] 自动恢复上下文: {len(self._conversation_history)} 条消息")
                return True

            # 非静默或当前会话非空：在新标签页中打开
            self._save_current_session_state()

            # 创建新标签
            self._session_counter += 1
            scroll_area, chat_container, chat_layout = self._create_session_widgets()
            self.session_stack.addWidget(scroll_area)

            # 用缓存文件名或首条用户消息作为标签名
            label = f"Chat {self._session_counter}"
            for msg in history:
                if msg.get('role') == 'user' and msg.get('content'):
                    short = msg['content'][:18].replace('\n', ' ').strip()
                    if len(msg['content']) > 18:
                        short += "..."
                    label = short
                    break

            tab_index = self.session_tabs.addTab(label)
            self.session_tabs.setTabData(tab_index, cached_session_id)

            todo = self._create_todo_list(chat_container)
            if todo_data:
                todo.restore_todos(todo_data)
                self._ensure_todo_in_chat(todo, chat_layout)

            self._sessions[cached_session_id] = {
                'scroll_area': scroll_area,
                'chat_container': chat_container,
                'chat_layout': chat_layout,
                'todo_list': todo,
                'conversation_history': history,
                'context_summary': context_summary,
                'current_response': None,
                'token_stats': saved_token_stats,
            }

            # 切换到新标签
            self._session_id = cached_session_id
            self._conversation_history = history
            self._context_summary = context_summary
            self._current_response = None
            self._token_stats = saved_token_stats
            self.scroll_area = scroll_area
            self.chat_container = chat_container
            self.chat_layout = chat_layout
            self.todo_list = todo

            self.session_tabs.blockSignals(True)
            self.session_tabs.setCurrentIndex(tab_index)
            self.session_tabs.blockSignals(False)
            self.session_stack.setCurrentWidget(scroll_area)

            self._render_conversation_history()
            self._update_token_stats_display()
            self._update_context_stats()

            if not silent:
                self._addStatus.emit(f"缓存已加载: {cache_file.name}")

            return True

        except Exception as e:
            if not silent:
                QtWidgets.QMessageBox.warning(self, "错误", f"加载缓存失败: {str(e)}")
            else:
                print(f"[Workspace] 加载缓存失败: {str(e)}")
            return False

    def _load_cache_silent(self, cache_file: Path) -> bool:
        """静默加载缓存（用于工作区自动恢复）"""
        return self._load_cache(cache_file, silent=True)

    def _load_cache_dialog(self):
        """显示加载缓存对话框"""
        cache_files = sorted(
            set(self._cache_dir.glob("session_*.json"))
            | set(self._cache_dir.glob("archive_*.json"))
            | set(self._cache_dir.glob("cache_*.json")),
            key=lambda p: p.stat().st_mtime, reverse=True
        )

        if not cache_files:
            QtWidgets.QMessageBox.information(self, "提示", "没有找到缓存文件")
            return

        # 创建选择对话框
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("选择缓存文件")
        dialog.setMinimumWidth(500)

        layout = QtWidgets.QVBoxLayout(dialog)

        # 文件列表
        list_widget = QtWidgets.QListWidget()
        for cache_file in cache_files:
            # 读取文件信息
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    msg_count = len(data.get('conversation_history', []))
                    estimated_tokens = data.get('estimated_tokens', 0)
                    created_at = data.get('created_at', '')
                    if created_at:
                        try:
                            dt = datetime.fromisoformat(created_at)
                            created_at = dt.strftime("%Y-%m-%d %H:%M:%S")
                        except Exception:
                            pass
                    token_info = f" | ~{estimated_tokens:,} tokens" if estimated_tokens else ""
                    item_text = f"{cache_file.name}\n  {msg_count} 条消息{token_info} | {created_at}"
            except Exception:
                item_text = cache_file.name

            item = QtWidgets.QListWidgetItem(item_text)
            item.setData(QtCore.Qt.UserRole, cache_file)
            list_widget.addItem(item)

        layout.addWidget(QtWidgets.QLabel("选择要加载的缓存文件:"))
        layout.addWidget(list_widget)

        # 按钮
        btn_layout = QtWidgets.QHBoxLayout()
        btn_load = QtWidgets.QPushButton("加载")
        btn_cancel = QtWidgets.QPushButton("取消")
        btn_layout.addWidget(btn_load)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)

        def on_load():
            current = list_widget.currentItem()
            if current:
                cache_file = current.data(QtCore.Qt.UserRole)
                if self._load_cache(cache_file):
                    dialog.accept()

        btn_load.clicked.connect(on_load)
        btn_cancel.clicked.connect(dialog.reject)

        dialog.exec_()

    def _list_caches(self):
        """列出所有缓存文件"""
        cache_files = sorted(
            set(self._cache_dir.glob("session_*.json"))
            | set(self._cache_dir.glob("archive_*.json"))
            | set(self._cache_dir.glob("cache_*.json")),
            key=lambda p: p.stat().st_mtime, reverse=True
        )

        if not cache_files:
            QtWidgets.QMessageBox.information(self, "提示", "没有找到缓存文件")
            return

        # 创建信息对话框
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("缓存文件列表")
        dialog.setMinimumSize(600, 400)

        layout = QtWidgets.QVBoxLayout(dialog)

        # 文本显示
        text_edit = QtWidgets.QTextEdit()
        text_edit.setReadOnly(True)

        lines = ["缓存文件列表:\n"]
        for cache_file in cache_files:
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    msg_count = len(data.get('conversation_history', []))
                    created_at = data.get('created_at', '')
                    session_id = data.get('session_id', '')
                    estimated_tokens = data.get('estimated_tokens', 0)

                    if created_at:
                        try:
                            dt = datetime.fromisoformat(created_at)
                            created_at = dt.strftime("%Y-%m-%d %H:%M:%S")
                        except Exception:
                            pass

                    size_kb = cache_file.stat().st_size / 1024
                    lines.append(f"  {cache_file.name}")
                    lines.append(f"   会话ID: {session_id}")
                    lines.append(f"   消息数: {msg_count}")
                    if estimated_tokens:
                        lines.append(f"   估算Token: ~{estimated_tokens:,}")
                    lines.append(f"   创建时间: {created_at}")
                    lines.append(f"   文件大小: {size_kb:.1f} KB")
                    lines.append("")
            except Exception as e:
                lines.append(f"[err] {cache_file.name} (读取失败: {str(e)})")
                lines.append("")

        text_edit.setPlainText("\n".join(lines))
        layout.addWidget(text_edit)

        btn_close = QtWidgets.QPushButton("关闭")
        btn_close.clicked.connect(dialog.accept)
        layout.addWidget(btn_close)

        dialog.exec_()
