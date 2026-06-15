import time
from houdini_agent.qt_compat import QtWidgets, QtCore, QtGui
from .theme import _fmt_duration
from .plan_dag import PlanDAGWidget
from ..i18n import tr


class StreamingPlanCard(QtWidgets.QWidget):
    """流式 Plan 卡片 — 生成阶段逐步构建，完成后原地升级为完整交互卡片。

    生命周期：
    1. 创建时只有标题骨架 + STREAMING 标签
    2. on_tool_args_delta 驱动 update_from_accumulated()，逐步渲染标题 → 概述 → 步骤
    3. 工具执行完毕后，调用 finalize_with_data(plan_data) 原地补充：
       - 步骤详情（sub_steps, tools, risk, deps, expected, fallback, notes）
       - DAG 架构图
       - 进度条
       - Confirm / Reject 按钮
    4. 后续 update_step_status / set_confirmed / set_rejected 等方法与旧 PlanViewer 完全兼容
    """

    planConfirmed = QtCore.Signal(dict)
    planRejected = QtCore.Signal()

    _STATUS_ICONS = {
        "pending": "○", "running": "◎", "done": "●", "error": "✗",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._plan = {}
        self._step_labels = {}   # step_id -> (icon_w, title_lbl)
        self._confirmed = False
        self._rejected = False
        self._finalized = False

        self.setObjectName("planViewerOuter")
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 6, 0, 6)
        outer.setSpacing(0)

        self._card = QtWidgets.QFrame(self)
        self._card.setObjectName("planViewerCard")
        self._card_lay = QtWidgets.QVBoxLayout(self._card)
        self._card_lay.setContentsMargins(14, 10, 14, 10)
        self._card_lay.setSpacing(6)

        # ── 标题行 ──
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(8)
        icon_lbl = QtWidgets.QLabel("📋")
        icon_lbl.setFixedWidth(18)
        header.addWidget(icon_lbl)

        self._title_lbl = QtWidgets.QLabel("Planning...")
        self._title_lbl.setObjectName("planViewerTitle")
        self._title_lbl.setWordWrap(True)
        header.addWidget(self._title_lbl, 1)

        self._status_badge = QtWidgets.QLabel("STREAMING")
        self._status_badge.setObjectName("planStatusBadge")
        self._status_badge.setAlignment(QtCore.Qt.AlignCenter)
        self._status_badge.setFixedHeight(20)
        self._status_badge.setMinimumWidth(60)
        header.addWidget(self._status_badge)
        self._card_lay.addLayout(header)

        # ── 概述 ──
        self._overview_lbl = QtWidgets.QLabel("")
        self._overview_lbl.setObjectName("planOverview")
        self._overview_lbl.setWordWrap(True)
        self._overview_lbl.setVisible(False)
        self._card_lay.addWidget(self._overview_lbl)

        # ── 分隔线 ──
        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        sep.setObjectName("planSeparator")
        self._card_lay.addWidget(sep)

        # ── 步骤容器（流式填充） ──
        self._steps_container = QtWidgets.QWidget()
        self._steps_lay = QtWidgets.QVBoxLayout(self._steps_container)
        self._steps_lay.setContentsMargins(0, 0, 0, 0)
        self._steps_lay.setSpacing(2)
        self._card_lay.addWidget(self._steps_container)

        # ── 正在生成指示器 ──
        self._loading_lbl = QtWidgets.QLabel("  ⋯ generating steps...")
        self._loading_lbl.setObjectName("planStepDep")
        self._card_lay.addWidget(self._loading_lbl)

        # ── 以下区域在 finalize_with_data 时动态添加 ──
        # DAG, 进度条, 按钮 → 预留 placeholder
        self._dag_widget = None
        self._dag_scroll = None
        self._dag_toggle = None
        self._progress_bar = None
        self._btn_row = None
        self._btn_confirm = None
        self._btn_reject = None

        outer.addWidget(self._card)

        # ── 流式跟踪状态 ──
        self._rendered_step_count = 0
        self._current_title = ""
        self._current_overview = ""

    # ==================================================================
    # 流式阶段 API — 由 on_tool_args_delta 驱动
    # ==================================================================

    def update_from_accumulated(self, accumulated: str):
        """从 create_plan 的不完整 JSON 中增量提取并渲染内容。"""
        if self._finalized:
            return
        import re as _re

        # 提取 title
        m_title = _re.search(r'"title"\s*:\s*"((?:[^"\\]|\\.)*)"', accumulated)
        if m_title and m_title.group(1) != self._current_title:
            self._current_title = m_title.group(1)
            self._title_lbl.setText(self._current_title)

        # 提取 overview
        m_ov = _re.search(r'"overview"\s*:\s*"((?:[^"\\]|\\.)*)"', accumulated)
        if m_ov and m_ov.group(1) != self._current_overview:
            self._current_overview = m_ov.group(1)
            self._overview_lbl.setText(self._current_overview)
            self._overview_lbl.setVisible(True)

        # 匹配 steps 数组中的每个 step 对象
        steps_match = _re.search(r'"steps"\s*:\s*\[', accumulated)
        if not steps_match:
            return

        steps_json_start = steps_match.end()
        step_pattern = _re.compile(
            r'\{\s*"id"\s*:\s*"(step-\d+)"\s*,\s*'
            r'"(?:title|description)"\s*:\s*"((?:[^"\\]|\\.)*)"',
        )
        all_steps = list(step_pattern.finditer(accumulated, steps_json_start))

        # 仅渲染新出现的 step
        for i in range(self._rendered_step_count, len(all_steps)):
            m = all_steps[i]
            self._add_streaming_step(m.group(1), m.group(2))
            self._rendered_step_count += 1

        # 检查是否进入 architecture 部分
        if '"architecture"' in accumulated:
            self._loading_lbl.setText("  ⋯ generating architecture...")

    def _add_streaming_step(self, step_id: str, text: str):
        """流式阶段：添加一行简化版步骤"""
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(6)
        row.setContentsMargins(4, 2, 0, 0)

        icon_w = QtWidgets.QLabel("○")
        icon_w.setFixedWidth(14)
        icon_w.setObjectName("planStepIcon")
        icon_w.setProperty("state", "pending")
        row.addWidget(icon_w)

        sid_lbl = QtWidgets.QLabel(step_id)
        sid_lbl.setObjectName("planStepId")
        sid_lbl.setFixedWidth(50)
        row.addWidget(sid_lbl)

        title_lbl = QtWidgets.QLabel(text)
        title_lbl.setObjectName("planStepTitle")
        title_lbl.setWordWrap(True)
        row.addWidget(title_lbl, 1)

        w = QtWidgets.QWidget()
        w.setLayout(row)
        self._steps_lay.addWidget(w)

        # 记录引用以便 finalize 时更新
        self._step_labels[step_id] = (icon_w, title_lbl)

    # ==================================================================
    # 完成阶段 API — 工具执行结束后调用
    # ==================================================================

    def finalize_with_data(self, plan_data: dict):
        """用完整的 plan_data 原地升级卡片 — 补充详情、DAG、进度条、按钮。

        此方法只会被调用一次。调用后卡片与旧 PlanViewer 功能完全等价。
        """
        if self._finalized:
            return
        self._finalized = True
        self._plan = plan_data

        # 隐藏加载指示器
        self._loading_lbl.setVisible(False)

        # 用完整数据刷新标题 + 概述（覆盖流式阶段的可能不完整内容）
        self._title_lbl.setText(plan_data.get("title", self._current_title or "Plan"))
        overview = plan_data.get("overview", "")
        if overview:
            self._overview_lbl.setText(overview)
            self._overview_lbl.setVisible(True)

        # ── 清空流式步骤，用完整步骤重建（含详情、deps 等） ──
        while self._steps_lay.count():
            item = self._steps_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._step_labels.clear()

        steps = plan_data.get("steps", [])
        phases = plan_data.get("phases", [])
        step_phase_map = {}
        for phase in phases:
            for sid in phase.get("step_ids", []):
                step_phase_map[sid] = phase.get("name", "")

        rendered_phases = set()
        for s in steps:
            step_id = s.get("id", "")

            # Phase 标题
            phase_name = step_phase_map.get(step_id, "")
            if phase_name and phase_name not in rendered_phases:
                rendered_phases.add(phase_name)
                phase_sep = QtWidgets.QFrame()
                phase_sep.setFrameShape(QtWidgets.QFrame.HLine)
                phase_sep.setObjectName("planPhaseSeparator")
                self._steps_lay.addWidget(phase_sep)
                phase_lbl = QtWidgets.QLabel(phase_name)
                phase_lbl.setObjectName("planPhaseHeader")
                self._steps_lay.addWidget(phase_lbl)

            # 步骤主行
            step_row = QtWidgets.QHBoxLayout()
            step_row.setSpacing(6)
            step_row.setContentsMargins(4, 2, 0, 0)

            status = s.get("status", "pending")
            icon_w = QtWidgets.QLabel(self._STATUS_ICONS.get(status, "○"))
            icon_w.setFixedWidth(14)
            icon_w.setObjectName("planStepIcon")
            icon_w.setProperty("state", status)
            step_row.addWidget(icon_w)

            sid_lbl = QtWidgets.QLabel(step_id)
            sid_lbl.setObjectName("planStepId")
            sid_lbl.setFixedWidth(50)
            step_row.addWidget(sid_lbl)

            title_text = s.get("title", s.get("description", ""))
            title_lbl = QtWidgets.QLabel(title_text)
            title_lbl.setObjectName("planStepTitle")
            title_lbl.setWordWrap(True)
            step_row.addWidget(title_lbl, 1)

            # 风险标记
            risk = s.get("risk", "")
            if risk and risk != "low":
                risk_lbl = QtWidgets.QLabel(f"⚠ {risk.upper()}")
                risk_lbl.setObjectName("planStepRisk")
                risk_lbl.setProperty("risk", risk)
                step_row.addWidget(risk_lbl)

            # 依赖标记
            deps = s.get("depends_on", [])
            if deps:
                short_deps = [d.replace("step-", "s") for d in deps]
                dep_lbl = QtWidgets.QLabel(f"← {','.join(short_deps)}")
                dep_lbl.setObjectName("planStepDep")
                dep_lbl.setMaximumWidth(80)
                step_row.addWidget(dep_lbl)

            row_w = QtWidgets.QWidget()
            row_w.setLayout(step_row)
            self._steps_lay.addWidget(row_w)

            # 步骤详情
            detail_w = QtWidgets.QWidget()
            detail_w.setObjectName("planStepDetail")
            detail_lay = QtWidgets.QVBoxLayout(detail_w)
            detail_lay.setContentsMargins(24, 0, 4, 4)
            detail_lay.setSpacing(2)

            for sub in s.get("sub_steps", []):
                lbl = QtWidgets.QLabel(f"  ├ {sub}")
                lbl.setObjectName("planSubStep")
                lbl.setWordWrap(True)
                detail_lay.addWidget(lbl)
            step_tools = s.get("tools", [])
            if step_tools:
                detail_lay.addWidget(QtWidgets.QLabel(f"Tools: {', '.join(step_tools)}"))
            expected = s.get("expected_result", "")
            if expected:
                lbl = QtWidgets.QLabel(f"Expected: {expected}")
                lbl.setObjectName("planStepExpected")
                lbl.setWordWrap(True)
                detail_lay.addWidget(lbl)
            fallback = s.get("fallback", "")
            if fallback:
                lbl = QtWidgets.QLabel(f"Fallback: {fallback}")
                lbl.setObjectName("planStepFallback")
                lbl.setWordWrap(True)
                detail_lay.addWidget(lbl)
            notes = s.get("notes", "")
            if notes:
                lbl = QtWidgets.QLabel(f"Note: {notes}")
                lbl.setObjectName("planStepNotes")
                lbl.setWordWrap(True)
                detail_lay.addWidget(lbl)

            if detail_lay.count() > 0:
                self._steps_lay.addWidget(detail_w)

            self._step_labels[step_id] = (icon_w, title_lbl)

        # ── DAG 架构图 ──
        sep2 = QtWidgets.QFrame()
        sep2.setFrameShape(QtWidgets.QFrame.HLine)
        sep2.setObjectName("planSeparator")
        self._card_lay.addWidget(sep2)

        dag_header_row = QtWidgets.QHBoxLayout()
        arch_data = plan_data.get("architecture", {})
        has_real_arch = bool(arch_data and arch_data.get("nodes"))
        if not has_real_arch:
            from .plan_viewer import PlanViewer
            arch_data = PlanViewer._build_step_dag(steps)

        dag_title = "Architecture" if has_real_arch else "Flow"
        dag_label = QtWidgets.QLabel(dag_title)
        dag_label.setObjectName("planSectionHeader")
        dag_header_row.addWidget(dag_label)
        dag_header_row.addStretch()

        self._dag_toggle = QtWidgets.QPushButton("▾ Collapse")
        self._dag_toggle.setObjectName("planDAGToggle")
        self._dag_toggle.setCursor(QtCore.Qt.PointingHandCursor)
        self._dag_toggle.setFixedHeight(20)
        self._dag_toggle.clicked.connect(self._toggle_dag)
        dag_header_row.addWidget(self._dag_toggle)
        self._card_lay.addLayout(dag_header_row)

        self._dag_widget = PlanDAGWidget(arch_data, self)
        self._dag_widget.set_collapsed(False)

        self._dag_scroll = QtWidgets.QScrollArea()
        self._dag_scroll.setObjectName("planDAGScroll")
        self._dag_scroll.setWidgetResizable(False)
        self._dag_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self._dag_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._dag_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._dag_scroll.setWidget(self._dag_widget)
        # ★ 高度完全跟随 DAG 内容，不设上限，确保架构图完整显示
        h = self._dag_widget._content_h
        scrollbar_h = 14  # 横向滚动条高度预留
        self._dag_scroll.setFixedHeight((h + scrollbar_h) if h > 0 else 200)
        self._card_lay.addWidget(self._dag_scroll)

        # ── 进度条 ──
        self._progress_bar = QtWidgets.QProgressBar()
        self._progress_bar.setObjectName("planProgress")
        self._progress_bar.setFixedHeight(8)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setRange(0, max(len(steps), 1))
        self._progress_bar.setValue(0)
        self._card_lay.addWidget(self._progress_bar)

        # ── Confirm / Reject 按钮 ──
        self._btn_row = QtWidgets.QWidget()
        btn_lay = QtWidgets.QHBoxLayout(self._btn_row)
        btn_lay.setContentsMargins(0, 4, 0, 0)
        btn_lay.setSpacing(8)
        btn_lay.addStretch()

        self._btn_reject = QtWidgets.QPushButton("Reject")
        self._btn_reject.setObjectName("planBtnReject")
        self._btn_reject.setCursor(QtCore.Qt.PointingHandCursor)
        self._btn_reject.setFixedHeight(28)
        self._btn_reject.setMinimumWidth(80)
        self._btn_reject.clicked.connect(self._do_reject)
        btn_lay.addWidget(self._btn_reject)

        self._btn_confirm = QtWidgets.QPushButton("Confirm")
        self._btn_confirm.setObjectName("planBtnConfirm")
        self._btn_confirm.setCursor(QtCore.Qt.PointingHandCursor)
        self._btn_confirm.setFixedHeight(28)
        self._btn_confirm.setMinimumWidth(80)
        self._btn_confirm.clicked.connect(self._do_confirm)
        btn_lay.addWidget(self._btn_confirm)

        self._card_lay.addWidget(self._btn_row)

        # 刷新状态
        self._refresh_ui()

    # ==================================================================
    # PlanViewer 兼容 API — finalize 后可直接使用
    # ==================================================================

    def set_confirmed(self):
        self._confirmed = True
        self._plan["status"] = "confirmed"
        if self._btn_confirm:
            self._btn_confirm.setEnabled(False)
            self._btn_reject.setEnabled(False)
            self._btn_confirm.setText("✓ Confirmed")
        self._refresh_ui()

    def set_rejected(self):
        self._rejected = True
        self._plan["status"] = "rejected"
        if self._btn_confirm:
            self._btn_confirm.setEnabled(False)
            self._btn_reject.setEnabled(False)
            self._btn_reject.setText("✗ Rejected")
        self._refresh_ui()

    def update_step_status(self, step_id: str, status: str, result_summary: str = ""):
        for s in self._plan.get("steps", []):
            if s["id"] == step_id:
                s["status"] = status
                if result_summary:
                    s["result_summary"] = result_summary
                break
        if step_id in self._step_labels:
            icon_w, _ = self._step_labels[step_id]
            icon_w.setText(self._STATUS_ICONS.get(status, "○"))
            icon_w.setProperty("state", status)
            icon_w.style().unpolish(icon_w)
            icon_w.style().polish(icon_w)
        if self._progress_bar:
            self._update_progress()
        all_done = all(
            s.get("status") in ("done", "error")
            for s in self._plan.get("steps", [])
        )
        if all_done:
            self._plan["status"] = "completed"
            self._refresh_ui()

    def get_plan_data(self) -> dict:
        return self._plan

    # ==================================================================
    # 内部方法
    # ==================================================================

    def _do_confirm(self):
        if self._confirmed or self._rejected:
            return
        self.set_confirmed()
        self.planConfirmed.emit(dict(self._plan))

    def _do_reject(self):
        if self._confirmed or self._rejected:
            return
        self.set_rejected()
        self.planRejected.emit()

    def _toggle_dag(self):
        if not self._dag_widget:
            return
        collapsed = not self._dag_widget._collapsed
        self._dag_widget.set_collapsed(collapsed)
        self._dag_toggle.setText("▸ Expand" if collapsed else "▾ Collapse")
        if collapsed:
            self._dag_scroll.setFixedHeight(0)
        else:
            # ★ 高度完全跟随 DAG 内容，不设上限
            h = self._dag_widget._content_h
            scrollbar_h = 14
            self._dag_scroll.setFixedHeight((h + scrollbar_h) if h > 0 else 200)

    def _update_progress(self):
        if not self._progress_bar:
            return
        steps = self._plan.get("steps", [])
        done = sum(1 for s in steps if s.get("status") == "done")
        self._progress_bar.setValue(done)

    def _refresh_ui(self):
        status = self._plan.get("status", "draft")
        badge_map = {
            "draft":     ("DRAFT",     "#64748b"),
            "confirmed": ("CONFIRMED", "#a78bfa"),
            "executing": ("EXECUTING", "#3b82f6"),
            "completed": ("COMPLETED", "#10b981"),
            "rejected":  ("REJECTED",  "#ef4444"),
        }
        text, color = badge_map.get(status, ("DRAFT", "#64748b"))
        self._status_badge.setText(text)
        self._status_badge.setStyleSheet(
            f"color: {color}; background: rgba(0,0,0,0.3); "
            f"border: 1px solid {color}; border-radius: 4px; "
            f"font-size: 10px; padding: 1px 8px; font-weight: bold;"
        )
        if self._btn_row:
            show = status == "draft" and not self._confirmed and not self._rejected
            self._btn_row.setVisible(show)
        if self._progress_bar:
            self._update_progress()
