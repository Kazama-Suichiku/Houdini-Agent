# -*- coding: utf-8 -*-
"""
Workflow experience review center.

Candidates are shown as a review board: candidate -> promoted / rejected /
later. The card surface only shows distilled reusable knowledge; source context
is kept in the detail panel as supporting evidence.
"""

import time
from typing import Dict, Optional

from houdini_agent.qt_compat import QtWidgets, QtCore

from .i18n import tr, language_changed
from .theme_engine import ThemeEngine
from ..utils.experience_store import ExperienceCandidate, get_experience_store


class ExperienceReviewDialog(QtWidgets.QDialog):
    """Review board for workflow experience candidates."""

    _LANES = ("candidate", "promoted", "rejected", "later")
    _LANE_ACCENTS = {
        "candidate": "#4f8cff",
        "promoted": "#45d18f",
        "rejected": "#f87171",
        "later": "#f7c948",
    }

    def __init__(self, ai_tab, parent=None):
        super().__init__(parent or ai_tab)
        self._ai_tab = ai_tab
        self._store = get_experience_store()
        self._items = []
        self._items_by_id: Dict[str, ExperienceCandidate] = {}
        self._current_id = ""
        self._lane_lists: Dict[str, QtWidgets.QListWidget] = {}
        self._lane_frames: Dict[str, QtWidgets.QFrame] = {}
        self._lane_headers: Dict[str, QtWidgets.QLabel] = {}
        self._detail_sections: Dict[str, QtWidgets.QLabel] = {}
        settings = QtCore.QSettings("HoudiniAgent", "Settings")
        self._show_rejected = settings.value("experience/show_rejected", True, type=bool)

        self.setObjectName("experienceReviewDlg")
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        self.setWindowFlags(
            QtCore.Qt.Window
            | QtCore.Qt.WindowTitleHint
            | QtCore.Qt.WindowSystemMenuHint
            | QtCore.Qt.WindowMinMaxButtonsHint
            | QtCore.Qt.WindowCloseButtonHint
        )
        self.setWindowModality(QtCore.Qt.NonModal)
        self.resize(1320, 760)
        self.setMinimumSize(980, 600)

        self._build_ui()
        self._retranslate_ui()
        self._reload()
        language_changed.changed.connect(self._retranslate_ui)
        self.finished.connect(self._disconnect_language_signal)

    def _disconnect_language_signal(self, *_):
        try:
            language_changed.changed.disconnect(self._retranslate_ui)
        except Exception:
            pass

    def closeEvent(self, event):  # noqa: N802
        self._disconnect_language_signal()
        event.accept()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(10)
        title_box = QtWidgets.QVBoxLayout()
        title_box.setSpacing(2)
        self._title = QtWidgets.QLabel()
        self._title.setObjectName("expReviewTitle")
        self._subtitle = QtWidgets.QLabel()
        self._subtitle.setObjectName("expReviewSubtitle")
        self._subtitle.setWordWrap(True)
        title_box.addWidget(self._title)
        title_box.addWidget(self._subtitle)
        header.addLayout(title_box, 1)

        self._always_on = QtWidgets.QLabel()
        self._always_on.setObjectName("expAlwaysOn")
        header.addWidget(self._always_on)

        self._scope_combo = QtWidgets.QComboBox()
        self._scope_combo.currentIndexChanged.connect(self._reload)
        header.addWidget(self._scope_combo)

        self._show_rejected_check = QtWidgets.QCheckBox()
        self._show_rejected_check.setChecked(self._show_rejected)
        self._show_rejected_check.toggled.connect(self._toggle_rejected_lane)
        header.addWidget(self._show_rejected_check)

        self._btn_reload = QtWidgets.QPushButton()
        self._btn_reload.clicked.connect(self._reload)
        header.addWidget(self._btn_reload)

        self._btn_extract = QtWidgets.QPushButton()
        self._btn_extract.clicked.connect(self._extract_current_session)
        header.addWidget(self._btn_extract)

        self._btn_export_curated = QtWidgets.QPushButton()
        self._btn_export_curated.clicked.connect(self._export_curated_experiences)
        header.addWidget(self._btn_export_curated)

        self._btn_title_close = QtWidgets.QPushButton("×")
        self._btn_title_close.setObjectName("expTitleClose")
        self._btn_title_close.setFixedSize(30, 30)
        self._btn_title_close.clicked.connect(self.close)
        header.addWidget(self._btn_title_close)
        root.addLayout(header)

        self._summary_bar = QtWidgets.QLabel()
        self._summary_bar.setObjectName("expSummaryBar")
        root.addWidget(self._summary_bar)

        body = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        body.setChildrenCollapsible(False)
        root.addWidget(body, 1)

        board = QtWidgets.QFrame()
        board.setObjectName("expBoard")
        board_lay = QtWidgets.QHBoxLayout(board)
        board_lay.setContentsMargins(0, 0, 0, 0)
        board_lay.setSpacing(8)
        for status in self._LANES:
            board_lay.addWidget(self._create_lane(status), 1)
        body.addWidget(board)

        detail = QtWidgets.QFrame()
        detail.setObjectName("expDetailPanel")
        detail_lay = QtWidgets.QVBoxLayout(detail)
        detail_lay.setContentsMargins(12, 12, 12, 12)
        detail_lay.setSpacing(8)

        self._detail_title = QtWidgets.QLabel()
        self._detail_title.setObjectName("expDetailTitle")
        self._detail_title.setWordWrap(True)
        self._detail_meta = QtWidgets.QLabel()
        self._detail_meta.setObjectName("expDetailMeta")
        self._detail_meta.setWordWrap(True)
        detail_lay.addWidget(self._detail_title)
        detail_lay.addWidget(self._detail_meta)

        detail_scroll = QtWidgets.QScrollArea()
        detail_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        detail_scroll.setWidgetResizable(True)
        detail_body = QtWidgets.QWidget()
        self._detail_layout = QtWidgets.QVBoxLayout(detail_body)
        self._detail_layout.setContentsMargins(0, 0, 0, 0)
        self._detail_layout.setSpacing(8)
        self._detail_sections["decision"] = self._add_detail_section("experience.sec_decision")
        self._detail_sections["rule"] = self._add_detail_section("experience.sec_rule")
        self._detail_sections["summary"] = self._add_detail_section("experience.sec_summary")
        self._detail_sections["evidence"] = self._add_detail_section("experience.sec_evidence")
        self._detail_sections["context"] = self._add_detail_section("experience.sec_context")
        self._detail_layout.addStretch()
        detail_scroll.setWidget(detail_body)
        detail_lay.addWidget(detail_scroll, 1)

        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(6)
        self._btn_promote = QtWidgets.QPushButton()
        self._btn_promote.setObjectName("expPrimaryButton")
        self._btn_promote.clicked.connect(self._promote_current)
        actions.addWidget(self._btn_promote)
        self._btn_later = QtWidgets.QPushButton()
        self._btn_later.clicked.connect(lambda: self._set_current_status("later"))
        actions.addWidget(self._btn_later)
        self._btn_reject = QtWidgets.QPushButton()
        self._btn_reject.clicked.connect(lambda: self._set_current_status("rejected"))
        actions.addWidget(self._btn_reject)
        self._btn_delete = QtWidgets.QPushButton()
        self._btn_delete.clicked.connect(self._delete_current)
        actions.addWidget(self._btn_delete)
        actions.addStretch()
        self._btn_close = QtWidgets.QPushButton()
        self._btn_close.clicked.connect(self.close)
        actions.addWidget(self._btn_close)
        detail_lay.addLayout(actions)

        self._notice = QtWidgets.QLabel()
        self._notice.setObjectName("expNotice")
        self._notice.setWordWrap(True)
        detail_lay.addWidget(self._notice)

        body.addWidget(detail)
        body.setSizes([900, 360])

        self._apply_styles()

    def _create_lane(self, status: str) -> QtWidgets.QFrame:
        lane = QtWidgets.QFrame()
        lane.setObjectName("expLane")
        lane.setProperty("lane_status", status)
        lay = QtWidgets.QVBoxLayout(lane)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(7)

        header = QtWidgets.QLabel()
        header.setObjectName("expLaneTitle")
        header.setProperty("lane_status", status)
        header.setStyleSheet(f"color: {self._LANE_ACCENTS.get(status, '#d8e2ee')}; font-weight: 700;")
        lay.addWidget(header)
        self._lane_headers[status] = header

        lst = QtWidgets.QListWidget()
        lst.setObjectName("expLaneList")
        lst.setWordWrap(True)
        lst.itemClicked.connect(lambda item, s=status: self._select_from_item(item, s))
        lay.addWidget(lst, 1)
        self._lane_lists[status] = lst
        self._lane_frames[status] = lane
        return lane

    def _add_detail_section(self, title_key: str) -> QtWidgets.QLabel:
        section = QtWidgets.QFrame()
        section.setObjectName("expSection")
        lay = QtWidgets.QVBoxLayout(section)
        lay.setContentsMargins(10, 9, 10, 9)
        lay.setSpacing(5)
        title = QtWidgets.QLabel()
        title.setObjectName("expSectionTitle")
        title.setProperty("title_key", title_key)
        body = QtWidgets.QLabel()
        body.setObjectName("expSectionBody")
        body.setWordWrap(True)
        body.setTextFormat(QtCore.Qt.PlainText)
        body.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        lay.addWidget(title)
        lay.addWidget(body)
        self._detail_layout.addWidget(section)
        return body

    def _apply_styles(self):
        title_px = ThemeEngine.scaled_px(17)
        body_px = ThemeEngine.scaled_px(13)
        small_px = ThemeEngine.scaled_px(12)
        self.setStyleSheet(f"""
            QDialog#experienceReviewDlg {{
                background: #0f1319;
                color: #d8e2ee;
            }}
            QLabel#expReviewTitle {{
                color: #f5f8fb;
                font-size: {title_px}px;
                font-weight: 700;
            }}
            QLabel#expReviewSubtitle, QLabel#expSummaryBar, QLabel#expDetailMeta, QLabel#expNotice {{
                color: #96a7ba;
                font-size: {small_px}px;
            }}
            QLabel#expAlwaysOn {{
                color: #45d18f;
                background: rgba(69, 209, 143, 24);
                border: 1px solid rgba(69, 209, 143, 95);
                border-radius: 5px;
                padding: 5px 9px;
                font-size: {small_px}px;
                font-weight: 700;
            }}
            QFrame#expBoard {{
                background: transparent;
                border: none;
            }}
            QFrame#expLane, QFrame#expDetailPanel {{
                background: #161b22;
                border: 1px solid #2a3542;
                border-radius: 7px;
            }}
            QLabel#expLaneTitle, QLabel#expDetailTitle {{
                color: #f5f8fb;
                font-size: {body_px}px;
                font-weight: 700;
            }}
            QListWidget#expLaneList {{
                background: #0d1218;
                border: 1px solid #25303d;
                border-radius: 6px;
                padding: 4px;
                color: #d8e2ee;
                font-size: {body_px}px;
            }}
            QListWidget#expLaneList::item {{
                padding: 8px 7px;
                margin: 2px 0;
                border-radius: 5px;
                border: 1px solid transparent;
            }}
            QListWidget#expLaneList::item:selected {{
                background: rgba(79, 140, 255, 32);
                border: 1px solid rgba(79, 140, 255, 100);
            }}
            QFrame#expSection {{
                background: #0d1218;
                border: 1px solid #25303d;
                border-radius: 6px;
            }}
            QLabel#expSectionTitle {{
                color: #9fb3c9;
                font-size: {small_px}px;
                font-weight: 700;
            }}
            QLabel#expSectionBody {{
                color: #d8e2ee;
                font-size: {body_px}px;
                line-height: 1.35;
            }}
            QComboBox, QPushButton {{
                background: #1c2532;
                color: #d8e2ee;
                border: 1px solid #344357;
                border-radius: 5px;
                padding: 5px 10px;
                font-size: {body_px}px;
            }}
            QCheckBox {{
                color: #d8e2ee;
                font-size: {body_px}px;
                spacing: 6px;
            }}
            QCheckBox::indicator {{
                width: 15px;
                height: 15px;
            }}
            QPushButton:hover {{
                background: #263346;
                border-color: #5a708b;
            }}
            QPushButton#expPrimaryButton {{
                background: #1f7650;
                border-color: #45d18f;
                color: #ffffff;
                font-weight: 700;
            }}
            QPushButton#expTitleClose {{
                background: transparent;
                border: 1px solid transparent;
                color: #9aaabc;
                font-size: {title_px}px;
                padding: 0px;
            }}
            QPushButton#expTitleClose:hover {{
                color: #ffffff;
                background: rgba(248, 113, 113, 45);
                border-color: rgba(248, 113, 113, 100);
            }}
            QPushButton:disabled {{
                color: #657588;
                background: #141a22;
                border-color: #27313d;
            }}
            QScrollArea {{
                background: transparent;
            }}
            QScrollBar:vertical, QScrollBar:horizontal {{
                background: transparent;
                border: none;
            }}
            QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
                background: #344357;
                border-radius: 4px;
                min-height: 32px;
                min-width: 32px;
            }}
            QScrollBar::add-line, QScrollBar::sub-line {{
                width: 0px;
                height: 0px;
            }}
        """)

    def _retranslate_ui(self, *_):
        self.setWindowTitle(tr("experience.title"))
        self._title.setText(tr("experience.title"))
        self._subtitle.setText(tr("experience.subtitle"))
        self._always_on.setText(tr("experience.always_on"))
        self._btn_title_close.setToolTip(tr("btn.close"))

        current = self._scope_combo.currentData() or "all"
        self._scope_combo.blockSignals(True)
        self._scope_combo.clear()
        self._scope_combo.addItem(tr("experience.scope_all"), "all")
        self._scope_combo.addItem(tr("experience.scope_current"), "current")
        idx = max(0, self._scope_combo.findData(current))
        self._scope_combo.setCurrentIndex(idx)
        self._scope_combo.blockSignals(False)

        for status, label in self._lane_headers.items():
            label.setText(self._lane_title(status, 0))

        for i in range(self._detail_layout.count()):
            item = self._detail_layout.itemAt(i)
            widget = item.widget() if item else None
            if not widget:
                continue
            title = widget.findChild(QtWidgets.QLabel, "expSectionTitle")
            if title:
                title.setText(tr(title.property("title_key")))

        self._btn_reload.setText(tr("experience.refresh"))
        self._btn_extract.setText(tr("experience.extract"))
        self._btn_export_curated.setText(tr("experience.export_curated"))
        self._show_rejected_check.setText(tr("experience.show_rejected"))
        self._btn_promote.setText(tr("experience.promote"))
        self._btn_later.setText(tr("experience.later"))
        self._btn_reject.setText(tr("experience.reject"))
        self._btn_delete.setText(tr("experience.delete_rejected"))
        self._btn_close.setText(tr("btn.close"))
        self._render_detail(self._selected_candidate())

    # ------------------------------------------------------------------
    # Data actions
    # ------------------------------------------------------------------

    def _reload(self):
        current_id = self._current_id
        scoped_items = self._scoped_items()
        counts = {status: 0 for status in self._LANES}
        for cand in scoped_items:
            status = cand.status if cand.status in self._LANES else "candidate"
            counts[status] += 1

        self._lane_frames["rejected"].setVisible(self._show_rejected)
        self._items = scoped_items if self._show_rejected else [c for c in scoped_items if c.status != "rejected"]
        self._items_by_id = {c.id: c for c in self._items}

        for lst in self._lane_lists.values():
            lst.blockSignals(True)
            lst.clear()
            lst.blockSignals(False)

        for cand in self._items:
            status = cand.status if cand.status in self._LANES else "candidate"
            item = QtWidgets.QListWidgetItem(self._card_text(cand))
            item.setToolTip(cand.summary or cand.proposed_rule)
            item.setData(QtCore.Qt.UserRole, cand.id)
            self._lane_lists[status].addItem(item)

        for status, count in counts.items():
            self._lane_headers[status].setText(self._lane_title(status, count))

        suggested = sum(1 for c in self._items if c.status in ("candidate", "later") and c.quality_score >= 0.72)
        self._summary_bar.setText(
            tr(
                "experience.board_summary",
                len(self._items),
                counts["candidate"],
                suggested,
                counts["promoted"],
                counts["rejected"],
                counts["later"],
            )
        )

        if current_id and current_id in self._items_by_id:
            self._select_candidate(current_id)
        elif self._items:
            self._select_candidate(self._items[0].id)
        else:
            self._current_id = ""
            self._render_detail(None)
        self._update_buttons()

    def _scoped_items(self):
        items = self._store.list_candidates(status="all", limit=300)
        if self._scope_combo.currentData() == "current":
            sid = getattr(self._ai_tab, "_session_id", "")
            items = [c for c in items if c.session_id == sid]
        return items

    def _toggle_rejected_lane(self, checked: bool):
        self._show_rejected = bool(checked)
        settings = QtCore.QSettings("HoudiniAgent", "Settings")
        settings.setValue("experience/show_rejected", self._show_rejected)
        cand = self._selected_candidate()
        if cand and cand.status == "rejected" and not self._show_rejected:
            self._current_id = ""
        self._reload()

    def _lane_title(self, status: str, count: int) -> str:
        return f"{self._status_label(status).upper()} · {count}"

    def _card_text(self, cand: ExperienceCandidate) -> str:
        kind = f"{cand.signal_type} → {cand.category}/L{cand.abstraction_level}"
        title = cand.title or cand.id
        rule = self._first_rule_line(cand)
        stamp = time.strftime("%m-%d %H:%M", time.localtime(cand.created_at))
        return (
            f"{cand.id}   {kind}\n"
            f"{title}\n"
            f"Q {cand.quality_score:.2f} · C {cand.confidence:.2f} · {stamp}\n"
            f"{rule}"
        )

    @staticmethod
    def _first_rule_line(cand: ExperienceCandidate) -> str:
        text = cand.summary or cand.proposed_rule or ""
        for line in text.splitlines():
            line = line.strip()
            if line:
                return line[:92]
        return ""

    def _select_from_item(self, item: QtWidgets.QListWidgetItem, lane_status: str):
        cand_id = item.data(QtCore.Qt.UserRole)
        if cand_id:
            self._select_candidate(cand_id, source_status=lane_status)

    def _select_candidate(self, cand_id: str, source_status: Optional[str] = None):
        self._current_id = cand_id
        for status, lst in self._lane_lists.items():
            if source_status is not None and status != source_status:
                lst.blockSignals(True)
                lst.clearSelection()
                lst.blockSignals(False)
                continue
            for row in range(lst.count()):
                item = lst.item(row)
                if item and item.data(QtCore.Qt.UserRole) == cand_id:
                    lst.blockSignals(True)
                    lst.setCurrentRow(row)
                    lst.blockSignals(False)
                elif source_status is None:
                    lst.blockSignals(True)
                    item.setSelected(False)
                    lst.blockSignals(False)
        self._render_detail(self._selected_candidate())
        self._update_buttons()

    def _selected_candidate(self) -> Optional[ExperienceCandidate]:
        return self._items_by_id.get(self._current_id)

    def _render_detail(self, cand: Optional[ExperienceCandidate]):
        if not cand:
            self._detail_title.setText(tr("experience.empty_title"))
            self._detail_meta.setText(tr("experience.empty"))
            for label in self._detail_sections.values():
                label.setText("")
            self._notice.setText("")
            return

        created = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(cand.created_at))
        self._detail_title.setText(cand.title or cand.id)
        self._detail_meta.setText(
            f"{cand.id} | {self._status_label(cand.status)} | {created}\n"
            f"{cand.signal_type} → {cand.category}/L{cand.abstraction_level} | "
            f"{tr('experience.field_quality')} {cand.quality_score:.2f} | "
            f"{tr('experience.field_confidence')} {cand.confidence:.2f}"
        )
        self._detail_sections["decision"].setText(cand.decision or cand.promotion_notes or "-")
        self._detail_sections["rule"].setText(cand.proposed_rule or "-")
        self._detail_sections["summary"].setText(cand.summary or "-")
        self._detail_sections["evidence"].setText(cand.evidence or "-")
        context_lines = [cand.context or "-"]
        if cand.promoted_memory_id:
            context_lines.append(f"{tr('experience.field_memory')}: {cand.promoted_memory_id}")
        if cand.detail_path:
            context_lines.append(f"{tr('experience.field_detail')}: {cand.detail_path}")
        self._detail_sections["context"].setText("\n\n".join(context_lines))
        self._notice.setText("")

    @staticmethod
    def _status_label(status: str) -> str:
        return tr(f"experience.status_{status}") if status in ("candidate", "promoted", "rejected", "later") else status

    def _update_buttons(self):
        cand = self._selected_candidate()
        has = cand is not None
        can_review = has and cand.status in ("candidate", "later")
        self._btn_promote.setEnabled(can_review)
        self._btn_later.setEnabled(has and cand.status == "candidate")
        self._btn_reject.setEnabled(can_review)
        self._btn_delete.setEnabled(has and cand.status == "rejected")

    def _extract_current_session(self):
        history = getattr(self._ai_tab, "_conversation_history", [])
        session_id = getattr(self._ai_tab, "_session_id", "")
        candidates = self._store.create_many_from_history(session_id, history)
        if not candidates:
            self._notice.setText(tr("experience.no_history"))
            return
        self._current_id = candidates[0].id
        self._reload()
        self._notice.setText(tr("experience.extracted_count", len(candidates)))

    def _promote_current(self):
        cand = self._selected_candidate()
        if not cand:
            return
        try:
            memory_id, detail_path = self._store.promote(cand.id)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, tr("experience.title"), tr("experience.promote_failed", e))
            return
        self._current_id = cand.id
        self._reload()
        self._notice.setText(tr("experience.promoted_inline", memory_id, detail_path or "-"))

    def _set_current_status(self, status: str):
        cand = self._selected_candidate()
        if not cand:
            return
        self._store.update_status(cand.id, status)
        self._current_id = cand.id
        self._reload()
        self._notice.setText(tr("experience.status_changed", self._status_label(status)))

    def _delete_current(self):
        cand = self._selected_candidate()
        if not cand or cand.status != "rejected":
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            tr("experience.delete_confirm_title"),
            tr("experience.delete_confirm_text", cand.title or cand.id),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        self._store.delete_candidate(cand.id)
        self._current_id = ""
        self._reload()
        self._notice.setText(tr("experience.deleted"))

    def _export_curated_experiences(self):
        stamp = time.strftime("%Y%m%d", time.localtime())
        default_path = str(self._store.db_path.parent / "exports" / f"curated_experiences_{stamp}.md")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            tr("experience.export_curated_title"),
            default_path,
            "Markdown (*.md);;Text (*.txt)",
        )
        if not path:
            return
        try:
            stats = self._store.export_curated_experiences(path)
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self,
                tr("experience.export_curated_title"),
                tr("experience.export_curated_failed", e),
            )
            return
        self._notice.setText(
            tr(
                "experience.export_curated_done",
                stats.get("semantic_count", 0),
                stats.get("procedural_count", 0),
                path,
            )
        )
