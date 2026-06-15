from houdini_agent.qt_compat import QtWidgets, QtCore, QtGui
from .theme import CursorTheme
from .plan_dag import PlanDAGWidget
from ..i18n import tr


class PlanViewer(QtWidgets.QWidget):
    """Plan 执行计划交互卡片。

    在聊天流中渲染为可折叠的卡片，包含：
    - 标题 + 状态
    - 概述
    - 步骤列表（含状态图标）
    - DAG 流程图（可展开/收起）
    - 进度条
    - Confirm / Reject 按钮（仅在 awaiting_confirmation 状态可见）
    """

    planConfirmed = QtCore.Signal(dict)   # 发射 plan_data
    planRejected = QtCore.Signal()

    _STATUS_ICONS = {
        "pending":  "○",
        "running":  "◎",
        "done":     "●",
        "error":    "✗",
    }

    def __init__(self, plan_data: dict, parent=None):
        super().__init__(parent)
        self._plan = plan_data
        self._step_labels = {}  # step_id -> QLabel
        self._confirmed = False
        self._rejected = False

        self.setObjectName("planViewerOuter")
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 6, 0, 6)
        outer.setSpacing(0)

        # ── 卡片容器 ──
        self._card = QtWidgets.QFrame(self)
        self._card.setObjectName("planViewerCard")
        card_lay = QtWidgets.QVBoxLayout(self._card)
        card_lay.setContentsMargins(14, 10, 14, 10)
        card_lay.setSpacing(6)

        # ── 标题行 ──
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(8)

        icon_lbl = QtWidgets.QLabel("📋")
        icon_lbl.setFixedWidth(18)
        header.addWidget(icon_lbl)

        self._title_lbl = QtWidgets.QLabel(plan_data.get("title", "Plan"))
        self._title_lbl.setObjectName("planViewerTitle")
        self._title_lbl.setWordWrap(True)
        header.addWidget(self._title_lbl, 1)

        self._status_badge = QtWidgets.QLabel("DRAFT")
        self._status_badge.setObjectName("planStatusBadge")
        self._status_badge.setAlignment(QtCore.Qt.AlignCenter)
        self._status_badge.setFixedHeight(20)
        self._status_badge.setMinimumWidth(60)
        header.addWidget(self._status_badge)

        card_lay.addLayout(header)

        # ── 概述 ──
        overview = plan_data.get("overview", "")
        if overview:
            ov_lbl = QtWidgets.QLabel(overview)
            ov_lbl.setObjectName("planOverview")
            ov_lbl.setWordWrap(True)
            card_lay.addWidget(ov_lbl)

        # ── 复杂度 & 预估操作数 ──
        complexity = plan_data.get("complexity", "")
        est_ops = plan_data.get("estimated_total_operations", 0)
        if complexity or est_ops:
            meta_parts = []
            if complexity:
                meta_parts.append(f"Complexity: {complexity.upper()}")
            if est_ops:
                meta_parts.append(f"Est. Operations: {est_ops}")
            meta_lbl = QtWidgets.QLabel("  |  ".join(meta_parts))
            meta_lbl.setObjectName("planMetaInfo")
            card_lay.addWidget(meta_lbl)

        # ── 分隔线 ──
        sep1 = QtWidgets.QFrame()
        sep1.setFrameShape(QtWidgets.QFrame.HLine)
        sep1.setObjectName("planSeparator")
        card_lay.addWidget(sep1)

        # ── 步骤列表（增强版：支持 phases 分组 + 子步骤 + 详情）──
        steps = plan_data.get("steps", [])
        phases = plan_data.get("phases", [])

        # 构建 step_id → phase 映射
        step_phase_map = {}
        for phase in phases:
            for sid in phase.get("step_ids", []):
                step_phase_map[sid] = phase.get("name", "")

        rendered_phases = set()
        for s in steps:
            step_id = s.get("id", "")

            # 如果此步骤属于某个 phase，且 phase 还未渲染过 → 插入 phase 标题
            phase_name = step_phase_map.get(step_id, "")
            if phase_name and phase_name not in rendered_phases:
                rendered_phases.add(phase_name)
                phase_sep = QtWidgets.QFrame()
                phase_sep.setFrameShape(QtWidgets.QFrame.HLine)
                phase_sep.setObjectName("planPhaseSeparator")
                card_lay.addWidget(phase_sep)
                phase_lbl = QtWidgets.QLabel(phase_name)
                phase_lbl.setObjectName("planPhaseHeader")
                card_lay.addWidget(phase_lbl)

            # ── 步骤标题行 ──
            step_row = QtWidgets.QHBoxLayout()
            step_row.setSpacing(6)
            step_row.setContentsMargins(4, 2, 0, 0)

            status = s.get("status", "pending")
            icon = self._STATUS_ICONS.get(status, "○")

            icon_w = QtWidgets.QLabel(icon)
            icon_w.setFixedWidth(14)
            icon_w.setObjectName("planStepIcon")
            icon_w.setProperty("state", status)
            step_row.addWidget(icon_w)

            sid_lbl = QtWidgets.QLabel(step_id)
            sid_lbl.setObjectName("planStepId")
            sid_lbl.setFixedWidth(50)
            step_row.addWidget(sid_lbl)

            # 使用 title 作为步骤列表显示文本，description 放在详情中
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

            # 依赖标记（紧凑格式）
            deps = s.get("depends_on", [])
            if deps:
                # 将 "step-1" 缩写为 "s1"，节省空间
                short_deps = [d.replace("step-", "s") for d in deps]
                dep_lbl = QtWidgets.QLabel(f"← {','.join(short_deps)}")
                dep_lbl.setObjectName("planStepDep")
                dep_lbl.setMaximumWidth(80)
                step_row.addWidget(dep_lbl)

            row_w = QtWidgets.QWidget()
            row_w.setLayout(step_row)
            card_lay.addWidget(row_w)

            # ── 步骤详情区域（sub_steps + tools + expected + fallback）──
            detail_w = QtWidgets.QWidget()
            detail_w.setObjectName("planStepDetail")
            detail_lay = QtWidgets.QVBoxLayout(detail_w)
            detail_lay.setContentsMargins(24, 0, 4, 4)
            detail_lay.setSpacing(2)

            # 子步骤
            sub_steps = s.get("sub_steps", [])
            for sub in sub_steps:
                sub_lbl = QtWidgets.QLabel(f"  ├ {sub}")
                sub_lbl.setObjectName("planSubStep")
                sub_lbl.setWordWrap(True)
                detail_lay.addWidget(sub_lbl)

            # 工具列表
            tools = s.get("tools", [])
            if tools:
                tools_lbl = QtWidgets.QLabel(f"Tools: {', '.join(tools)}")
                tools_lbl.setObjectName("planStepTools")
                detail_lay.addWidget(tools_lbl)

            # 预期结果
            expected = s.get("expected_result", "")
            if expected:
                exp_lbl = QtWidgets.QLabel(f"Expected: {expected}")
                exp_lbl.setObjectName("planStepExpected")
                exp_lbl.setWordWrap(True)
                detail_lay.addWidget(exp_lbl)

            # 回退策略
            fallback = s.get("fallback", "")
            if fallback:
                fb_lbl = QtWidgets.QLabel(f"Fallback: {fallback}")
                fb_lbl.setObjectName("planStepFallback")
                fb_lbl.setWordWrap(True)
                detail_lay.addWidget(fb_lbl)

            # 备注
            notes = s.get("notes", "")
            if notes:
                notes_lbl = QtWidgets.QLabel(f"Note: {notes}")
                notes_lbl.setObjectName("planStepNotes")
                notes_lbl.setWordWrap(True)
                detail_lay.addWidget(notes_lbl)

            if detail_lay.count() > 0:
                card_lay.addWidget(detail_w)

            self._step_labels[step_id] = (icon_w, title_lbl)

        # ── DAG 流程图区域 ──
        sep2 = QtWidgets.QFrame()
        sep2.setFrameShape(QtWidgets.QFrame.HLine)
        sep2.setObjectName("planSeparator")
        card_lay.addWidget(sep2)

        dag_header_row = QtWidgets.QHBoxLayout()

        # 根据数据类型决定标题
        arch_data = plan_data.get("architecture", {})
        has_real_arch = bool(arch_data and arch_data.get("nodes"))

        if not has_real_arch:
            # ── 回退：从 steps 的 depends_on 自动生成步骤依赖图 ──
            arch_data = self._build_step_dag(steps)

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
        card_lay.addLayout(dag_header_row)

        self._dag_widget = PlanDAGWidget(arch_data, self)
        self._dag_widget.set_collapsed(False)

        # ★ 用 QScrollArea 包裹 DAG，窗口窄时自动出横向滚动条
        self._dag_scroll = QtWidgets.QScrollArea()
        self._dag_scroll.setObjectName("planDAGScroll")
        self._dag_scroll.setWidgetResizable(False)  # 保持 DAG 原始尺寸
        self._dag_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self._dag_scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._dag_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._dag_scroll.setWidget(self._dag_widget)
        # ★ 高度完全跟随 DAG 内容，不设上限，确保架构图完整显示
        h = self._dag_widget._content_h
        scrollbar_h = 14  # 横向滚动条高度预留
        self._dag_scroll.setFixedHeight((h + scrollbar_h) if h > 0 else 200)
        card_lay.addWidget(self._dag_scroll)

        # ── 进度条 ──
        self._progress_bar = QtWidgets.QProgressBar()
        self._progress_bar.setObjectName("planProgress")
        self._progress_bar.setFixedHeight(8)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setRange(0, max(len(steps), 1))
        self._progress_bar.setValue(0)
        card_lay.addWidget(self._progress_bar)

        # ── 按钮行 ──
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

        card_lay.addWidget(self._btn_row)

        outer.addWidget(self._card)
        self._refresh_ui()

    # ----------------------------------------------------------
    # 公共方法
    # ----------------------------------------------------------

    def set_confirmed(self):
        """确认后禁用按钮"""
        self._confirmed = True
        self._plan["status"] = "confirmed"
        self._btn_confirm.setEnabled(False)
        self._btn_reject.setEnabled(False)
        self._btn_confirm.setText("✓ Confirmed")
        self._refresh_ui()

    def set_rejected(self):
        """拒绝后禁用按钮"""
        self._rejected = True
        self._plan["status"] = "rejected"
        self._btn_confirm.setEnabled(False)
        self._btn_reject.setEnabled(False)
        self._btn_reject.setText("✗ Rejected")
        self._refresh_ui()

    def update_step_status(self, step_id: str, status: str, result_summary: str = ""):
        """实时更新某个步骤的状态（执行阶段调用）"""
        # 更新内部数据
        for s in self._plan.get("steps", []):
            if s["id"] == step_id:
                s["status"] = status
                if result_summary:
                    s["result_summary"] = result_summary
                break

        # 更新步骤列表 UI
        if step_id in self._step_labels:
            icon_w, desc_lbl = self._step_labels[step_id]
            icon = self._STATUS_ICONS.get(status, "○")
            icon_w.setText(icon)
            icon_w.setProperty("state", status)
            icon_w.style().unpolish(icon_w)
            icon_w.style().polish(icon_w)

        # 架构图为静态蓝图，步骤状态变更时无需更新
        # self._dag_widget 展示的是最终节点网络拓扑

        # 更新进度条
        self._update_progress()

        # 检查是否全部完成
        all_done = all(
            s.get("status") in ("done", "error")
            for s in self._plan.get("steps", [])
        )
        if all_done:
            self._plan["status"] = "completed"
            self._refresh_ui()

    def get_plan_data(self) -> dict:
        return self._plan

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

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

    @staticmethod
    def _build_step_dag(steps: list) -> dict:
        """从 steps 的 depends_on 关系自动构建步骤依赖 DAG 数据。

        当 plan 没有 architecture 字段时作为回退方案，
        将步骤列表转换为 PlanDAGWidget 可接受的 architecture 格式。
        """
        nodes = []
        connections = []

        # 收集所有 depends_on 关系
        has_any_deps = any(s.get("depends_on") for s in steps)

        for s in steps:
            sid = s.get("id", "")
            title = s.get("title", s.get("description", sid))
            # 截取前 20 字符作为 label
            label = title[:20] + ("…" if len(title) > 20 else "")
            nodes.append({
                "id": sid,
                "label": label,
                "type": "sop",   # 默认类型
                "is_new": True,
                "params": ", ".join(s.get("tools", [])[:2]) if s.get("tools") else "",
            })

            # 依赖关系 → 连线
            for dep_id in (s.get("depends_on") or []):
                connections.append({"from": dep_id, "to": sid})

        # 没有依赖关系时，自动生成线性链
        if not has_any_deps and len(steps) > 1:
            for i in range(len(steps) - 1):
                connections.append({
                    "from": steps[i]["id"],
                    "to": steps[i + 1]["id"],
                })

        # 尝试从 phases 构建分组（如果有的话不会到这里，但兼容）
        return {
            "nodes": nodes,
            "connections": connections,
            "groups": [],
        }

    def _toggle_dag(self):
        collapsed = not self._dag_widget._collapsed
        self._dag_widget.set_collapsed(collapsed)
        self._dag_toggle.setText("▸ Expand" if collapsed else "▾ Collapse")
        # ★ 同步滚动区域高度
        if collapsed:
            self._dag_scroll.setFixedHeight(0)
        else:
            # DAG 内容高度 + 滚动条可能占用的空间
            h = self._dag_widget._content_h
            scrollbar_h = 14  # 横向滚动条高度预留
            self._dag_scroll.setFixedHeight(h + scrollbar_h)
            self._dag_scroll.setMinimumHeight(h)

    def _update_progress(self):
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
        # 按钮可见性
        show_buttons = status in ("draft", "confirmed") and not self._confirmed and not self._rejected
        self._btn_row.setVisible(show_buttons and status == "draft")
        self._update_progress()


# ============================================================
# AskQuestionCard — AI 主动提问交互卡片（Plan 规划阶段）
# ============================================================

class AskQuestionCard(QtWidgets.QFrame):
    """嵌入聊天流中的 AI 提问卡片。

    AI 在 Plan 规划阶段需要澄清信息时，通过 ask_question 工具发起提问。
    用户通过单选/多选/自由文本回答后，点击提交按钮。
    答案通过 answered 信号返回给后台线程。

    questions 结构示例:
        [
            {
                "id": "q1",
                "prompt": "你想用 HeightField 还是 Grid？",
                "options": [
                    {"id": "hf", "label": "HeightField (推荐)"},
                    {"id": "grid", "label": "Grid"}
                ],
                "allow_multiple": false,
                "allow_free_text": true
            }
        ]
    """

    answered = QtCore.Signal(dict)    # 发射答案 dict: {q_id: [selected_option_ids], ...}
    cancelled = QtCore.Signal()       # 用户取消

    def __init__(self, questions: list, parent=None):
        super().__init__(parent)
        self._questions = questions
        self._answered = False
        self._widgets = {}  # q_id -> {"buttons": [...], "group": QButtonGroup, "free_text": QLineEdit}

        self.setObjectName("askQuestionCard")
        self.setFrameShape(QtWidgets.QFrame.NoFrame)

        main_lay = QtWidgets.QVBoxLayout(self)
        main_lay.setContentsMargins(14, 10, 14, 10)
        main_lay.setSpacing(8)

        # ── 标题 ──
        title_row = QtWidgets.QHBoxLayout()
        title_row.setSpacing(6)
        icon_lbl = QtWidgets.QLabel("❓")
        icon_lbl.setFixedWidth(18)
        title_row.addWidget(icon_lbl)
        title_lbl = QtWidgets.QLabel("AI needs your input to proceed")
        title_lbl.setObjectName("askQuestionTitle")
        title_lbl.setWordWrap(True)
        title_row.addWidget(title_lbl, 1)
        main_lay.addLayout(title_row)

        # ── 各问题 ──
        for q in questions:
            q_id = q.get("id", "")
            prompt = q.get("prompt", "")
            options = q.get("options", [])
            allow_multiple = q.get("allow_multiple", False)
            allow_free_text = q.get("allow_free_text", False)

            # 问题分隔线
            sep = QtWidgets.QFrame()
            sep.setFrameShape(QtWidgets.QFrame.HLine)
            sep.setObjectName("askQuestionSep")
            main_lay.addWidget(sep)

            # 问题文本
            q_lbl = QtWidgets.QLabel(f"{q_id.upper()}: {prompt}")
            q_lbl.setObjectName("askQuestionPrompt")
            q_lbl.setWordWrap(True)
            main_lay.addWidget(q_lbl)

            # 选项
            btn_group = None
            buttons = []
            if not allow_multiple:
                btn_group = QtWidgets.QButtonGroup(self)
                btn_group.setExclusive(True)

            for opt in options:
                opt_id = opt.get("id", "")
                opt_label = opt.get("label", "")
                if allow_multiple:
                    btn = QtWidgets.QCheckBox(opt_label)
                else:
                    btn = QtWidgets.QRadioButton(opt_label)
                    btn_group.addButton(btn)
                btn.setObjectName("askQuestionOption")
                btn.setProperty("opt_id", opt_id)
                main_lay.addWidget(btn)
                buttons.append(btn)

            # 自由文本输入
            free_text = None
            if allow_free_text:
                free_text = QtWidgets.QLineEdit()
                free_text.setObjectName("askQuestionFreeText")
                free_text.setPlaceholderText("Or type your answer here...")
                main_lay.addWidget(free_text)

            self._widgets[q_id] = {
                "buttons": buttons,
                "group": btn_group,
                "free_text": free_text,
                "allow_multiple": allow_multiple,
            }

        # ── 按钮行 ──
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setContentsMargins(0, 6, 0, 0)
        btn_row.addStretch()

        self._btn_cancel = QtWidgets.QPushButton("Skip")
        self._btn_cancel.setObjectName("askQuestionBtnCancel")
        self._btn_cancel.setCursor(QtCore.Qt.PointingHandCursor)
        self._btn_cancel.setFixedHeight(28)
        self._btn_cancel.setMinimumWidth(60)
        self._btn_cancel.clicked.connect(self._do_cancel)
        btn_row.addWidget(self._btn_cancel)

        self._btn_submit = QtWidgets.QPushButton("Submit Answer")
        self._btn_submit.setObjectName("askQuestionBtnSubmit")
        self._btn_submit.setCursor(QtCore.Qt.PointingHandCursor)
        self._btn_submit.setFixedHeight(28)
        self._btn_submit.setMinimumWidth(100)
        self._btn_submit.clicked.connect(self._do_submit)
        btn_row.addWidget(self._btn_submit)

        main_lay.addLayout(btn_row)

    def _collect_answers(self) -> dict:
        """收集用户的回答"""
        answers = {}
        for q_id, w_info in self._widgets.items():
            selected = []
            for btn in w_info["buttons"]:
                if btn.isChecked():
                    selected.append(btn.property("opt_id"))
            # 自由文本
            free_text = w_info.get("free_text")
            if free_text and free_text.text().strip():
                selected.append(f"__free_text__:{free_text.text().strip()}")
            answers[q_id] = selected
        return answers

    def _do_submit(self):
        if self._answered:
            return
        self._answered = True
        answers = self._collect_answers()
        self._btn_submit.setEnabled(False)
        self._btn_cancel.setEnabled(False)
        self._btn_submit.setText("✓ Submitted")
        self.answered.emit(answers)

    def _do_cancel(self):
        if self._answered:
            return
        self._answered = True
        self._btn_submit.setEnabled(False)
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.setText("Skipped")
        self.cancelled.emit()
