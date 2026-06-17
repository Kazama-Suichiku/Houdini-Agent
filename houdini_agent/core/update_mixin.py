# -*- coding: utf-8 -*-
"""
Update Mixin — Token 优化菜单 + 自动更新

从 ai_tab.py 拆分：
  - _on_optimize_menu / _optimize_now
  - _updateCheckDone / _updateApplyDone / _updateProgress 信号
  - _silent_update_check / _on_silent_check_result / _on_banner_update
  - _on_check_update / _bg_check_update / _on_update_check_result
  - _UPDATE_FUNNY_MESSAGES / _start_update / _bg_download_and_apply
  - _update_progress_cb / _stop_update_msg_timer / _rotate_update_message
  - _on_update_progress / _on_update_apply_result / _do_restart
"""

import threading

from houdini_agent.qt_compat import QtWidgets, QtCore

from ..utils.token_optimizer import CompressionStrategy
from ..ui.cursor_widgets import UpdateNotificationBanner


class UpdateMixin:
    """Token 压缩菜单与插件自动更新"""

    def _on_optimize_menu(self):
        """显示 Token 优化菜单"""
        menu = QtWidgets.QMenu(self)

        # 立即优化
        optimize_now_action = menu.addAction("立即压缩对话")
        optimize_now_action.triggered.connect(self._optimize_now)

        menu.addSeparator()

        # 自动优化开关
        auto_label = "自动压缩 [on]" if self._auto_optimize else "自动压缩"
        auto_opt_action = menu.addAction(auto_label)
        auto_opt_action.setCheckable(True)
        auto_opt_action.setChecked(self._auto_optimize)
        auto_opt_action.triggered.connect(lambda: setattr(self, '_auto_optimize', not self._auto_optimize))

        menu.addSeparator()

        # 压缩策略
        strategy_menu = menu.addMenu("压缩策略")
        for label, strat in [
            ("激进 (最大节省)", CompressionStrategy.AGGRESSIVE),
            ("平衡 (推荐)", CompressionStrategy.BALANCED),
            ("保守 (保留细节)", CompressionStrategy.CONSERVATIVE),
        ]:
            action = strategy_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(self._optimization_strategy == strat)
            action.triggered.connect(lambda _, s=strat: setattr(self, '_optimization_strategy', s))

        # 显示菜单
        menu.exec_(self.btn_optimize.mapToGlobal(QtCore.QPoint(0, self.btn_optimize.height())))

    def _optimize_now(self):
        """立即优化当前对话"""
        if len(self._conversation_history) <= 4:
            QtWidgets.QMessageBox.information(self, "提示", "对话历史太短，无需优化")
            return

        # 计算优化前
        before_tokens = self._calculate_context_tokens()

        # 执行优化
        compressed_messages, stats = self.token_optimizer.compress_messages(
            self._conversation_history,
            strategy=self._optimization_strategy
        )

        if stats['saved_tokens'] > 0:
            self._conversation_history = compressed_messages
            self._context_summary = compressed_messages[0].get('content', '') if compressed_messages and compressed_messages[0].get('role') == 'system' else self._context_summary

            # 重新渲染
            self._render_conversation_history()

            # 更新统计
            self._update_context_stats()

            # 显示结果
            saved_percent = stats.get('saved_percent', 0)
            QtWidgets.QMessageBox.information(
                self, "优化完成",
                f"对话已优化！\n\n"
                f"原始: ~{before_tokens:,} tokens\n"
                f"优化后: ~{stats['compressed_tokens']:,} tokens\n"
                f"节省: ~{stats['saved_tokens']:,} tokens ({saved_percent:.1f}%)\n\n"
                f"压缩了 {stats['compressed']} 条消息，保留 {stats['kept']} 条"
            )
        else:
            QtWidgets.QMessageBox.information(self, "提示", "无需优化，对话历史已经很精简")

    # ============================================================
    # 自动更新
    # ============================================================

    # 无参信号 + 属性传参：dict 跨线程排队投递在 Houdini 21/PySide6 下会触发
    # PyObjectWrapper::toInt 段错误（signal 11 / access violation）。沿用本库
    # _confirmToolRequest 既有的规避方案。payload 见 _update_check_payload /
    # _update_apply_payload。_updateProgress 用原生 (str, int)，安全。
    _updateCheckDone = QtCore.Signal()       # 检查结果（payload: _update_check_payload）
    _updateApplyDone = QtCore.Signal()       # 应用结果（payload: _update_apply_payload）
    _updateProgress = QtCore.Signal(str, int)  # (stage, percent)

    def _silent_update_check(self):
        """启动时静默检查更新（不弹窗，只在有更新时高亮按钮）"""
        try:
            self._updateCheckDone.connect(self._on_silent_check_result, QtCore.Qt.UniqueConnection)
        except RuntimeError:
            pass
        threading.Thread(target=self._bg_check_update, daemon=True).start()

    @QtCore.Slot()
    def _on_silent_check_result(self):
        """[主线程] 静默检查结果 → 如果有更新，高亮按钮 + 显示通知横幅"""
        result = getattr(self, '_update_check_payload', None) or {}
        # 断开静默回调，防止和手动点击冲突
        try:
            self._updateCheckDone.disconnect(self._on_silent_check_result)
        except (RuntimeError, TypeError):
            pass

        if result.get('has_update') and result.get('remote_version'):
            remote_ver = result['remote_version']
            local_ver = result.get('local_version', '?')
            release_name = result.get('release_name', '')

            # 1) 用醒目样式标记按钮
            self.btn_update.setText(self._tr_update_new_ver(remote_ver))
            self.btn_update.setToolTip(self._tr_update_new_ver_tip(remote_ver))
            self.btn_update.setProperty("state", "available")
            self.btn_update.style().unpolish(self.btn_update)
            self.btn_update.style().polish(self.btn_update)

            # 2) 保存检查结果，供手动点击时直接使用
            self._cached_update_result = result

            # 3) ★ 在输入区域上方显示更新通知横幅（含更新摘要）
            try:
                if hasattr(self, '_update_banner') and self._update_banner:
                    self._update_banner.setVisible(True)
                else:
                    release_notes = result.get('release_notes', '').strip()
                    self._update_banner = UpdateNotificationBanner(
                        remote_version=remote_ver,
                        release_name=release_name,
                        local_version=local_ver,
                        release_notes=release_notes,
                    )
                    self._update_banner.updateClicked.connect(self._on_banner_update)
                    # 插入到输入区域布局的最顶部（batch_bar 之前）
                    input_layout = self._batch_bar.parent().layout()
                    if input_layout:
                        input_layout.insertWidget(0, self._update_banner)
                    self._update_banner.setVisible(True)
            except Exception:
                pass  # 横幅创建失败不影响主流程

    def _tr_update_new_ver(self, ver: str) -> str:
        from ..ui.i18n import tr
        return tr('update.new_ver', ver)

    def _tr_update_new_ver_tip(self, ver: str) -> str:
        from ..ui.i18n import tr
        return tr('update.new_ver_tip', ver)

    def _on_banner_update(self):
        """通知横幅的"立即更新"按钮被点击"""
        # 隐藏横幅
        if hasattr(self, '_update_banner') and self._update_banner:
            self._update_banner.setVisible(False)
        # 触发更新流程
        cached = getattr(self, '_cached_update_result', None)
        if cached and cached.get('has_update'):
            self._on_update_check_result(cached)
            self._cached_update_result = None
        else:
            self._on_check_update()

    def _on_check_update(self):
        """点击 Update 按钮 → 后台检查更新（如果有缓存结果直接使用）"""
        # 如果启动时已检测到新版本，直接显示结果
        cached = getattr(self, '_cached_update_result', None)
        if cached and cached.get('has_update'):
            self._on_update_check_result(cached)
            self._cached_update_result = None  # 用完清除
            return

        self.btn_update.setEnabled(False)
        self.btn_update.setText("检查中…")

        # 连接信号（只连一次，用 UniqueConnection 防重复）
        try:
            self._updateCheckDone.connect(self._on_update_check_result, QtCore.Qt.UniqueConnection)
        except RuntimeError:
            pass

        threading.Thread(target=self._bg_check_update, daemon=True).start()

    def _bg_check_update(self):
        """[后台线程] 调用 updater.check_update"""
        try:
            from ..utils.updater import check_update
            result = check_update()
        except Exception as e:
            result = {'has_update': False, 'error': str(e), 'local_version': '?', 'remote_version': ''}
        self._update_check_payload = result
        self._updateCheckDone.emit()

    @QtCore.Slot()
    def _on_update_check_result(self, result: dict = None):
        """[主线程] 处理检查结果（无参信号时取自 _update_check_payload；也兼容直接带参调用）"""
        if result is None:
            result = getattr(self, '_update_check_payload', None) or {}
        self.btn_update.setEnabled(True)
        self.btn_update.setText("Update")
        self.btn_update.setProperty("state", "")  # 恢复默认样式
        self.btn_update.style().unpolish(self.btn_update)
        self.btn_update.style().polish(self.btn_update)

        if result.get('error'):
            QtWidgets.QMessageBox.warning(self, "检查更新", f"检查更新失败:\n{result['error']}")
            return

        local_ver = result.get('local_version', '?')
        remote_ver = result.get('remote_version', '?')
        release_name = result.get('release_name', '')
        release_notes = result.get('release_notes', '')

        if not result.get('has_update'):
            QtWidgets.QMessageBox.information(
                self, "检查更新",
                f"当前已是最新版本 ✓\n\n"
                f"本地版本: v{local_ver}\n"
                f"最新 Release: v{remote_ver}"
            )
            return

        # ---- 有新版本，弹出确认对话框 ----
        detail = f"本地版本: v{local_ver}\n最新 Release: v{remote_ver}"
        if release_name:
            detail += f"\n版本名称: {release_name}"
        if release_notes:
            detail += f"\n更新说明: {release_notes}"
        detail += "\n\n⚠️ 更新后插件窗口将自动重启。\n（config、cache、trainData 目录不会被覆盖）"

        reply = QtWidgets.QMessageBox.question(
            self, "发现新版本",
            f"发现新版本 v{remote_ver}，是否立即更新？\n\n{detail}",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Cancel,
        )

        if reply == QtWidgets.QMessageBox.Yes:
            self._start_update()

    # 更新进度文案轮播（下载阶段无百分比时使用）
    _UPDATE_FUNNY_MESSAGES = [
        "文件正在赶来的路上…",
        "数据正在穿越互联网…",
        "服务器正在翻箱倒柜找数据…",
        "正在和后端同学对齐信息…",
        "正在同步相关服务…",
        "进度条正在努力工作…",
        "正在从云里把数据拽下来…",
        "服务器正在回忆文件放在哪…",
        "正在构建本次请求的最佳实践…",
        "数据已经发车，很快到站…",
    ]

    def _start_update(self):
        """开始下载并应用更新"""
        # 创建进度对话框，初始即用第一条搞怪文案 + 不确定进度条（动效）
        first_msg = self._UPDATE_FUNNY_MESSAGES[0]
        self._update_progress_dlg = QtWidgets.QProgressDialog(
            first_msg, "取消", 0, 100, self
        )
        self._update_progress_dlg.setWindowTitle("更新 Houdini Agent")
        self._update_progress_dlg.setWindowModality(QtCore.Qt.WindowModal)
        self._update_progress_dlg.setAutoClose(False)
        self._update_progress_dlg.setAutoReset(False)
        self._update_progress_dlg.setMinimumDuration(0)
        self._update_progress_dlg.setValue(0)
        # 无 Content-Length 时只动不显示百分比：用不确定进度条
        self._update_progress_dlg.setRange(0, 0)
        # 文案轮播定时器（在 _on_update_progress 收到 downloading 0 时启动）
        self._update_msg_index = 0
        self._update_msg_timer = None
        self._update_fade_anim = None
        # QProgressDialog / QProgressBar 样式由全局 QSS 控制

        # 连接信号
        try:
            self._updateProgress.connect(self._on_update_progress, QtCore.Qt.UniqueConnection)
            self._updateApplyDone.connect(self._on_update_apply_result, QtCore.Qt.UniqueConnection)
        except RuntimeError:
            pass

        threading.Thread(target=self._bg_download_and_apply, daemon=True).start()

    def _bg_download_and_apply(self):
        """[后台线程] 下载并应用更新"""
        try:
            from ..utils.updater import download_and_apply
            result = download_and_apply(progress_callback=self._update_progress_cb)
        except Exception as e:
            result = {'success': False, 'error': str(e), 'updated_files': 0}
        self._update_apply_payload = result
        self._updateApplyDone.emit()

    def _update_progress_cb(self, stage: str, percent: int):
        """进度回调（从后台线程调用 → 通过信号到主线程）"""
        self._updateProgress.emit(stage, percent)

    def _stop_update_msg_timer(self):
        """停止更新文案轮播定时器"""
        if getattr(self, '_update_msg_timer', None) is not None:
            self._update_msg_timer.stop()
            self._update_msg_timer.deleteLater()
            self._update_msg_timer = None
        if getattr(self, '_update_fade_anim', None) is not None:
            try:
                self._update_fade_anim.stop()
            except Exception:
                pass
            self._update_fade_anim = None

    def _rotate_update_message(self):
        """轮播搞怪文案并做淡入动效"""
        if not hasattr(self, '_update_progress_dlg') or self._update_progress_dlg is None:
            return
        msgs = self._UPDATE_FUNNY_MESSAGES
        if not msgs:
            return
        self._update_msg_index = (self._update_msg_index + 1) % len(msgs)
        new_text = msgs[self._update_msg_index]
        self._update_progress_dlg.setLabelText(new_text)
        # 淡入动效：找到对话框里的 QLabel，用 QGraphicsOpacityEffect + QPropertyAnimation
        label = self._update_progress_dlg.findChild(QtWidgets.QLabel)
        if label is not None:
            effect = label.graphicsEffect()
            if effect is None:
                effect = QtWidgets.QGraphicsOpacityEffect(label)
                label.setGraphicsEffect(effect)
            effect.setOpacity(0.28)
            if getattr(self, '_update_fade_anim', None) is not None:
                try:
                    self._update_fade_anim.stop()
                except Exception:
                    pass
            anim = QtCore.QPropertyAnimation(effect, b"opacity")
            anim.setDuration(380)
            anim.setStartValue(0.28)
            anim.setEndValue(1.0)
            anim.start(QtCore.QAbstractAnimation.DeleteWhenStopped)
            self._update_fade_anim = anim

    @QtCore.Slot(str, int)
    def _on_update_progress(self, stage: str, percent: int):
        """[主线程] 更新进度条（无 Content-Length 时不确定进度条 + 搞怪文案轮播）"""
        if not hasattr(self, '_update_progress_dlg') or self._update_progress_dlg is None:
            return

        if stage == 'downloading':
            if percent == 0:
                self._update_progress_dlg.setRange(0, 0)
                self._update_progress_dlg.setLabelText(self._UPDATE_FUNNY_MESSAGES[0])
                self._update_msg_index = 0
                if getattr(self, '_update_msg_timer', None) is None:
                    self._update_msg_timer = QtCore.QTimer(self)
                    self._update_msg_timer.timeout.connect(self._rotate_update_message)
                    self._update_msg_timer.start(2200)
            elif 1 <= percent <= 99:
                self._stop_update_msg_timer()
                self._update_progress_dlg.setRange(0, 100)
                self._update_progress_dlg.setValue(percent)
                self._update_progress_dlg.setLabelText(f"正在下载… {percent}%")
            else:
                self._stop_update_msg_timer()
                self._update_progress_dlg.setRange(0, 100)
                self._update_progress_dlg.setValue(100)
                self._update_progress_dlg.setLabelText("下载完成")
        elif stage == 'extracting':
            self._stop_update_msg_timer()
            self._update_progress_dlg.setRange(0, 0)
            self._update_progress_dlg.setLabelText("正在解压…")
            self._update_progress_dlg.setValue(0)
        elif stage == 'applying':
            self._update_progress_dlg.setRange(0, 0)
            self._update_progress_dlg.setLabelText("正在更新文件…")
        elif stage == 'done':
            self._stop_update_msg_timer()
            self._update_progress_dlg.setRange(0, 100)
            self._update_progress_dlg.setValue(100)
            self._update_progress_dlg.setLabelText("更新完成！")
        else:
            self._update_progress_dlg.setValue(percent)
            self._update_progress_dlg.setLabelText(f"{stage} ({percent}%)")

    @QtCore.Slot()
    def _on_update_apply_result(self, result: dict = None):
        """[主线程] 更新完成后的处理（payload 取自 _update_apply_payload）"""
        if result is None:
            result = getattr(self, '_update_apply_payload', None) or {}
        self._stop_update_msg_timer()
        # 关闭进度条
        if hasattr(self, '_update_progress_dlg') and self._update_progress_dlg:
            self._update_progress_dlg.close()
            self._update_progress_dlg = None

        if not result.get('success'):
            QtWidgets.QMessageBox.critical(
                self, "更新失败",
                f"更新过程中出现错误:\n{result.get('error', '未知错误')}"
            )
            return

        updated = result.get('updated_files', 0)

        # 更新成功 → 提示并重启
        reply = QtWidgets.QMessageBox.information(
            self, "更新成功",
            f"已成功更新 {updated} 个文件！\n\n点击 OK 立即重启插件。",
            QtWidgets.QMessageBox.Ok,
        )

        # 延迟重启（让对话框关闭后再执行）
        QtCore.QTimer.singleShot(200, self._do_restart)

    def _do_restart(self):
        """执行插件重启"""
        try:
            # 先保存当前工作区
            main_win = self.window()
            if hasattr(main_win, '_save_workspace'):
                main_win._save_workspace()

            # 关闭当前窗口
            main_win.force_quit = True
            main_win.close()

            # 延迟重新打开（让窗口完全关闭后再重建）
            # 注意：使用绝对导入的函数引用，避免模块被清除后相对导入失败
            from houdini_agent.utils.updater import restart_plugin as _restart_fn
            QtCore.QTimer.singleShot(500, _restart_fn)
        except Exception as e:
            print(f"[Updater] Restart error: {e}")
            QtWidgets.QMessageBox.warning(
                self, "重启失败",
                f"自动重启失败，请手动关闭并重新打开插件。\n\n错误: {e}"
            )
