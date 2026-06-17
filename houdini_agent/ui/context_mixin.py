# -*- coding: utf-8 -*-
"""
Context Mixin — 字体缩放、上下文统计、模型/提供商管理

从 ai_tab.py 拆分：
  - _apply_font_scale / _zoom_in/out/reset / _on_font_settings / _on_font_scale_preview
  - _estimate_tokens / _calculate_context_tokens
  - _save_model_preference / _load_model_preference / _get_current_context_limit
  - _update_context_stats / _update_token_stats_display / _show_token_stats_dialog / _reset_token_stats
  - _current_provider / _refresh_models / _update_key_status / _on_provider_changed
"""

import threading

from houdini_agent.qt_compat import QtCore, QSettings

from .cursor_widgets import CursorTheme
from .font_settings_dialog import FontSettingsDialog
from .i18n import tr


class ContextMixin:
    """字体缩放、上下文统计与模型管理"""

    def _apply_font_scale(self):
        """重新渲染 QSS 并应用到界面"""
        self.setStyleSheet(self._theme.render())
        self._theme.save_preference()

    def _zoom_in(self):
        self._theme.zoom_in()
        self._apply_font_scale()

    def _zoom_out(self):
        self._theme.zoom_out()
        self._apply_font_scale()

    def _zoom_reset(self):
        self._theme.zoom_reset()
        self._apply_font_scale()

    def _on_font_settings(self):
        """打开字号设置面板"""
        dlg = FontSettingsDialog(current_scale=self._theme.scale, parent=self)
        dlg.scaleChanged.connect(self._on_font_scale_preview)
        dlg.exec_()
        # 对话框关闭后保存最终结果
        self._theme.set_scale(dlg.scale)
        self._apply_font_scale()

    def _on_font_scale_preview(self, scale: float):
        """实时预览字号缩放"""
        self._theme.set_scale(scale)
        self.setStyleSheet(self._theme.render())

    # ===== 上下文统计 =====

    def _estimate_tokens(self, text: str) -> int:
        """估算文本的 token 数量（粗略估算）

        中文约 1.5 字符/token，英文约 4 字符/token
        这里使用简单的混合估算
        """
        if not text:
            return 0

        # 统计中文字符
        chinese_chars = sum(1 for c in text if '一' <= c <= '鿿')
        other_chars = len(text) - chinese_chars

        # 中文约 1.5 字符/token，其他约 4 字符/token
        tokens = chinese_chars / 1.5 + other_chars / 4
        return int(tokens)

    def _calculate_context_tokens(self) -> int:
        """计算当前上下文的总 token 数（含工具定义）"""
        # 缓存工具定义 token 数（只算一次，因为工具定义不变）
        if not hasattr(self, '_tools_token_cache'):
            import json as _json
            from houdini_agent.utils.ai_client import HOUDINI_TOOLS
            tools_json = _json.dumps(HOUDINI_TOOLS, ensure_ascii=False)
            self._tools_token_cache = self.token_optimizer.estimate_tokens(tools_json)

        total = self._tools_token_cache

        # 系统提示词
        total += self.token_optimizer.estimate_tokens(self._system_prompt)

        # 上下文摘要
        if self._context_summary:
            total += self.token_optimizer.estimate_tokens(self._context_summary)

        # 对话历史
        total += self.token_optimizer.calculate_message_tokens(self._conversation_history)

        return total

    def _save_model_preference(self):
        """保存模型选择偏好"""
        settings = QSettings("HoudiniAI", "Assistant")
        provider = self._current_provider()
        model = self.model_combo.currentText()
        settings.setValue("last_provider", provider)
        settings.setValue("last_model", model)
        settings.setValue("use_think", self.think_check.isChecked())

    def _load_model_preference(self, restore_provider: bool = False):
        """加载模型选择偏好

        Args:
            restore_provider: 是否同时恢复提供商选择（仅在初始化时为 True）
        """
        settings = QSettings("HoudiniAI", "Assistant")
        last_provider = settings.value("last_provider", "")
        last_model = settings.value("last_model", "")

        # 恢复 Think 开关
        use_think = settings.value("use_think", True)
        # QSettings 可能返回字符串 "true"/"false"
        if isinstance(use_think, str):
            use_think = use_think.lower() == 'true'
        self.think_check.setChecked(bool(use_think))

        if not last_provider:
            return

        # 恢复提供商（仅在启动时调用一次）
        if restore_provider and last_provider != self._current_provider():
            for i in range(self.provider_combo.count()):
                if self.provider_combo.itemData(i) == last_provider:
                    # 暂时阻断信号，避免触发 _on_provider_changed 递归
                    self.provider_combo.blockSignals(True)
                    self.provider_combo.setCurrentIndex(i)
                    self.provider_combo.blockSignals(False)
                    # 手动刷新模型列表和状态
                    self._refresh_models(last_provider)
                    self._update_key_status()
                    break

        # 恢复模型
        current_provider = self._current_provider()
        if last_provider == current_provider and last_model:
            available_models = [self.model_combo.itemText(i) for i in range(self.model_combo.count())]
            if last_model in available_models:
                index = self.model_combo.findText(last_model)
                if index >= 0:
                    self.model_combo.setCurrentIndex(index)

    def _get_current_context_limit(self) -> int:
        """获取当前模型的上下文限制"""
        model = self.model_combo.currentText()
        return self._model_context_limits.get(model, 64000)

    def _update_context_stats(self):
        """更新上下文统计显示（包含优化状态）"""
        used = self._calculate_context_tokens()
        limit = self._get_current_context_limit()

        # 格式化显示
        if used >= 1000:
            used_str = f"{used / 1000:.1f}K"
        else:
            used_str = str(used)

        limit_str = f"{limit // 1000}K"

        # 计算百分比
        percent = (used / limit) * 100 if limit > 0 else 0

        # 优化状态指示
        optimize_indicator = ""
        if self._auto_optimize:
            should_compress, _ = self.token_optimizer.should_compress(used, limit)
            if should_compress:
                optimize_indicator = " *"  # 需要优化
            else:
                optimize_indicator = ""  # 已优化/正常

        # 根据使用比例设置颜色
        if percent < 50:
            color = CursorTheme.TEXT_MUTED
        elif percent < 80:
            color = CursorTheme.ACCENT_ORANGE
        else:
            color = CursorTheme.ACCENT_RED

        self.context_label.setText(f"{percent:.1f}% {used_str}/{limit_str}{optimize_indicator}")
        # 动态状态 → QSS 选择器 QLabel#contextLabel[state="..."]
        if percent < 50:
            ctx_state = ""
        elif percent < 80:
            ctx_state = "warning"
        else:
            ctx_state = "critical"
        self.context_label.setProperty("state", ctx_state)
        self.context_label.style().unpolish(self.context_label)
        self.context_label.style().polish(self.context_label)

        # 更新优化按钮状态（如果超过阈值，高亮显示）
        opt_state = "warning" if percent >= 80 else ""
        self.btn_optimize.setProperty("state", opt_state)
        self.btn_optimize.style().unpolish(self.btn_optimize)
        self.btn_optimize.style().polish(self.btn_optimize)

    def _update_token_stats_display(self):
        """更新 Token 统计按钮显示（对齐 Cursor：显示费用）"""
        total = self._token_stats['total_tokens']
        cost = self._token_stats.get('estimated_cost', 0.0)

        # 格式化 token 显示
        if total >= 1000000:
            tok_display = f"{total / 1000000:.1f}M"
        elif total >= 1000:
            tok_display = f"{total / 1000:.1f}K"
        else:
            tok_display = str(total)

        # 格式化费用显示（Cursor 风格：$0.12）
        if cost >= 1.0:
            cost_display = f"${cost:.2f}"
        elif cost >= 0.01:
            cost_display = f"${cost:.2f}"
        elif cost > 0:
            cost_display = f"${cost:.4f}"
        else:
            cost_display = ""

        # 按钮文本：token数 | $费用
        if cost_display:
            self.token_stats_btn.setText(f"{tok_display} | {cost_display}")
        else:
            self.token_stats_btn.setText(tok_display)

        # 计算 cache 命中率
        cache_read = self._token_stats['cache_read']
        cache_write = self._token_stats['cache_write']
        cache_total = cache_read + cache_write
        hit_rate_display = f"{(cache_read / cache_total * 100):.1f}%" if cache_total > 0 else "N/A"

        reasoning = self._token_stats.get('reasoning_tokens', 0)
        reasoning_line = tr('token.reasoning_line', reasoning) if reasoning > 0 else ""

        self.token_stats_btn.setToolTip(
            tr('token.summary',
               self._token_stats['requests'],
               self._token_stats['input_tokens'],
               self._token_stats['output_tokens'],
               reasoning_line,
               cache_read, cache_write, hit_rate_display,
               total, cost_display or '$0.00')
        )

    def _show_token_stats_dialog(self):
        """显示详细 Token 统计对话框（对齐 Cursor：使用 TokenAnalyticsPanel）"""
        from houdini_agent.ui.cursor_widgets import TokenAnalyticsPanel
        records = getattr(self, '_call_records', []) or []
        dialog = TokenAnalyticsPanel(records, self._token_stats, parent=self)
        dialog.exec_()
        if dialog.should_reset_stats:
            self._reset_token_stats()

    def _reset_token_stats(self):
        """重置 Token 统计"""
        self._token_stats = {
            'input_tokens': 0,
            'output_tokens': 0,
            'reasoning_tokens': 0,
            'cache_read': 0,
            'cache_write': 0,
            'total_tokens': 0,
            'requests': 0,
            'estimated_cost': 0.0,
        }
        self._call_records = []
        self._update_token_stats_display()

        # 显示提示
        if self._current_response:
            self._current_response.add_status(tr('status.stats_reset'))

    # ===== UI 辅助 =====

    def _current_provider(self) -> str:
        return self.provider_combo.currentData() or 'deepseek'

    def _refresh_models(self, provider: str):
        self.model_combo.clear()
        # 使用预设的模型列表
        self.model_combo.addItems(self._model_map.get(provider, []))

    def _update_key_status(self):
        provider = self._current_provider()

        if self.client.has_api_key(provider):
            masked = self.client.get_masked_key(provider)
            self.key_status.setText(masked)
            self.key_status.setProperty("state", "ok")
        else:
            self.key_status.setText("No Key")
            self.key_status.setProperty("state", "warning")
        self.key_status.style().unpolish(self.key_status)
        self.key_status.style().polish(self.key_status)

    def _on_provider_changed(self):
        provider = self._current_provider()
        self._refresh_models(provider)
        self._load_model_preference()  # 切换提供商时也尝试加载上次使用的模型
        self._update_key_status()
        self._on_provider_changed_custom_visibility()  # Custom ⚙ 按钮可见性
