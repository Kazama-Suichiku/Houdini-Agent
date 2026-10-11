# -*- coding: utf-8 -*-
"""
长期记忆管理窗口 — Episodic / Semantic / Procedural 全量 CRUD。

 独立于宿主：Qt 顶层窗口不设为 Houdini 控件的子窗口；无边框自绘标题栏；
 子树强制 Fusion + 全量 QSS，
 不使用 QMessageBox（避免宿主/系统原生弹窗），与 Houdini 内置 UI 样式隔离。
斜杠命令：/memories
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from houdini_agent.qt_compat import QtWidgets, QtCore, QtGui

from .i18n import tr, language_changed
from .theme_engine import ThemeEngine
from ..utils.memory_store import (
    get_memory_store,
    EpisodicRecord,
    SemanticRecord,
    ProceduralRecord,
    MEMORY_CATEGORIES,
    ABSTRACTION_LEVELS,
)


def _render_memory_qss() -> str:
    te = ThemeEngine()
    te.load_template(Path(__file__).parent / "style_template.qss")
    te.load_preference()
    return te.render()


def _apply_fusion_surface(widget: QtWidgets.QWidget, qss: Optional[str] = None) -> None:
    """子树强制 Fusion + QSS，避免继承宿主全局 QStyle / QPalette。"""
    if qss is None:
        qss = _render_memory_qss()
    fus = QtWidgets.QStyleFactory.create("Fusion")
    if fus:
        widget.setStyle(fus)
    widget.setStyleSheet(qss)


def _force_fusion_recursive(root: QtWidgets.QWidget) -> None:
    """逐个后代创建独立 Fusion 实例并 setStyle，彻底压住 Houdini 注入的全局样式。
    同时在末尾把顶层 QSS 重新 apply 一次（确保后创建的子控件也吃到 QSS）。"""
    for child in root.findChildren(QtWidgets.QWidget):
        fus = QtWidgets.QStyleFactory.create("Fusion")
        if fus:
            child.setStyle(fus)
    qss_text = root.styleSheet()
    if qss_text:
        root.setStyleSheet("")
        root.setStyleSheet(qss_text)


def _memory_plain_edit() -> QtWidgets.QPlainTextEdit:
    ed = QtWidgets.QPlainTextEdit()
    ed.setFrameShape(QtWidgets.QFrame.NoFrame)
    return ed


class _DraggableChromeFrame(QtWidgets.QFrame):
    """标题栏拖动（子控件需设 WA_TransparentForMouseEvents 以免抢事件）。"""

    def __init__(self, window: QtWidgets.QDialog, parent=None):
        super().__init__(parent)
        self._window = window
        self._drag_pos: Optional[QtCore.QPoint] = None

    def mousePressEvent(self, e: QtGui.QMouseEvent):
        if e.button() == QtCore.Qt.LeftButton:
            self._drag_pos = e.globalPos() - self._window.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QtGui.QMouseEvent):
        if self._drag_pos is not None and e.buttons() & QtCore.Qt.LeftButton:
            self._window.move(e.globalPos() - self._drag_pos)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QtGui.QMouseEvent):
        self._drag_pos = None
        super().mouseReleaseEvent(e)


class MemoryMgrSheet(QtWidgets.QDialog):
    """自绘轻量提示 / 确认框（无原生 QMessageBox）。"""

    def __init__(
        self,
        parent: Optional[QtWidgets.QWidget],
        title: str,
        body: str,
        *,
        question: bool = False,
    ):
        super().__init__(parent)
        self.setObjectName("memoryMgrSheetDlg")
        self.setWindowModality(QtCore.Qt.ApplicationModal if parent is None else QtCore.Qt.WindowModal)
        self.setWindowFlags(
            QtCore.Qt.Dialog | QtCore.Qt.FramelessWindowHint
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        _apply_fusion_surface(self)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)

        shell = QtWidgets.QFrame()
        shell.setObjectName("memoryMgrSheetShell")
        shell.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        sl = QtWidgets.QVBoxLayout(shell)
        sl.setContentsMargins(24, 20, 24, 20)
        sl.setSpacing(16)

        ttl = QtWidgets.QLabel(title)
        ttl.setObjectName("memoryMgrSheetTitle")
        sl.addWidget(ttl)

        msg = QtWidgets.QLabel(body)
        msg.setObjectName("memoryMgrSheetBody")
        msg.setWordWrap(True)
        msg.setMinimumWidth(300)
        msg.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        sl.addWidget(msg)

        row = QtWidgets.QHBoxLayout()
        row.addStretch()
        if question:
            btn_no = QtWidgets.QPushButton(tr("memory_mgr.sheet_cancel"))
            btn_no.setObjectName("memoryMgrSheetBtnSecondary")
            btn_no.clicked.connect(self.reject)
            btn_yes = QtWidgets.QPushButton(tr("memory_mgr.sheet_delete"))
            btn_yes.setObjectName("memoryMgrSheetBtnPrimary")
            btn_yes.clicked.connect(self.accept)
            row.addWidget(btn_no)
            row.addWidget(btn_yes)
        else:
            btn_ok = QtWidgets.QPushButton(tr("memory_mgr.sheet_ok"))
            btn_ok.setObjectName("memoryMgrSheetBtnPrimary")
            btn_ok.clicked.connect(self.accept)
            row.addWidget(btn_ok)
        sl.addLayout(row)

        outer.addWidget(shell)

        sh = QtWidgets.QGraphicsDropShadowEffect(shell)
        sh.setBlurRadius(50)
        sh.setOffset(0, 4)
        sh.setColor(QtGui.QColor(0, 0, 0, 90))
        shell.setGraphicsEffect(sh)

        QtWidgets.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Escape), self, self.reject)
        _force_fusion_recursive(self)


class MemoryManagerDialog(QtWidgets.QDialog):
    """记忆库 — 独立顶层窗口 + 自绘 chrome + 双栏卡片。"""

    def __init__(self, parent=None):
        # parent 仅用于 API 兼容，实际不参与窗口层级，避免挂到 Houdini QWidget。
        super().__init__(None)
        self.setObjectName("memoryManagerDlg")
        self._caller_parent = parent
        self._qss = _render_memory_qss()
        self._store = get_memory_store()
        self._creating_semantic = False
        self._creating_procedural = False

        self.setWindowModality(QtCore.Qt.ApplicationModal)
        self.setWindowFlags(
            QtCore.Qt.Dialog
            | QtCore.Qt.FramelessWindowHint
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        _apply_fusion_surface(self, self._qss)

        self._build_ui()
        _force_fusion_recursive(self)
        self._retranslate_ui()
        language_changed.changed.connect(self._retranslate_ui)

        self._reload_episodic_list()
        self._reload_semantic_list()
        self._reload_procedural_list()

    def closeEvent(self, event):
        try:
            language_changed.changed.disconnect(self._retranslate_ui)
        except TypeError:
            pass
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(0)

        self._shell = QtWidgets.QFrame()
        self._shell.setObjectName("memoryMgrShell")
        self._shell.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        sh = QtWidgets.QVBoxLayout(self._shell)
        sh.setContentsMargins(0, 0, 0, 0)
        sh.setSpacing(0)

        # ── Chrome (title bar) ──
        chrome = _DraggableChromeFrame(self)
        chrome.setObjectName("memoryMgrChrome")
        ch = QtWidgets.QHBoxLayout(chrome)
        ch.setContentsMargins(20, 14, 14, 14)
        ch.setSpacing(12)

        brand = QtWidgets.QLabel("◆")
        brand.setObjectName("memoryMgrBrand")
        brand.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        ch.addWidget(brand, 0, QtCore.Qt.AlignVCenter)

        title_col = QtWidgets.QVBoxLayout()
        title_col.setSpacing(1)
        self._title_lbl = QtWidgets.QLabel()
        self._title_lbl.setObjectName("memoryMgrChromeTitle")
        self._title_lbl.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self._subtitle_lbl = QtWidgets.QLabel()
        self._subtitle_lbl.setObjectName("memoryMgrChromeSub")
        self._subtitle_lbl.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        title_col.addWidget(self._title_lbl)
        title_col.addWidget(self._subtitle_lbl)
        ch.addLayout(title_col, 1)

        self._stats_lbl = QtWidgets.QLabel()
        self._stats_lbl.setObjectName("memoryMgrStats")
        self._stats_lbl.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        ch.addWidget(self._stats_lbl, 0, QtCore.Qt.AlignVCenter)

        self._btn_chrome_close = QtWidgets.QPushButton("✕")
        self._btn_chrome_close.setObjectName("memoryMgrChromeClose")
        self._btn_chrome_close.setFixedSize(28, 28)
        self._btn_chrome_close.setCursor(QtCore.Qt.PointingHandCursor)
        self._btn_chrome_close.clicked.connect(self.reject)
        ch.addWidget(self._btn_chrome_close, 0, QtCore.Qt.AlignVCenter)

        sh.addWidget(chrome)

        # ── Body ──
        body = QtWidgets.QFrame()
        body.setObjectName("memoryMgrBody")
        bl = QtWidgets.QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)

        # ── Navigation ──
        nav = QtWidgets.QFrame()
        nav.setObjectName("memoryMgrNav")
        nl = QtWidgets.QHBoxLayout(nav)
        nl.setContentsMargins(20, 8, 20, 8)
        nl.setSpacing(4)

        self._nav_group = QtWidgets.QButtonGroup(self)
        self._btn_nav_epi = QtWidgets.QPushButton()
        self._btn_nav_sem = QtWidgets.QPushButton()
        self._btn_nav_pro = QtWidgets.QPushButton()
        for i, btn in enumerate((self._btn_nav_epi, self._btn_nav_sem, self._btn_nav_pro)):
            btn.setCheckable(True)
            btn.setObjectName("memoryMgrNavBtn")
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            btn.setMinimumHeight(32)
            self._nav_group.addButton(btn, i)
            nl.addWidget(btn)
        nl.addStretch()
        self._btn_nav_epi.setChecked(True)
        self._nav_group.buttonClicked.connect(self._on_nav_clicked)
        bl.addWidget(nav)

        # ── Stack ──
        self._stack = QtWidgets.QStackedWidget()
        self._stack.setObjectName("memoryMgrStack")
        self._stack.addWidget(self._build_episodic_page())
        self._stack.addWidget(self._build_semantic_page())
        self._stack.addWidget(self._build_procedural_page())
        bl.addWidget(self._stack, 1)

        # ── Footer ──
        foot = QtWidgets.QFrame()
        foot.setObjectName("memoryMgrFooter")
        fl = QtWidgets.QHBoxLayout(foot)
        fl.setContentsMargins(20, 10, 20, 12)
        fl.addStretch()
        self._btn_close = QtWidgets.QPushButton()
        self._btn_close.setObjectName("memoryMgrBtnClose")
        self._btn_close.setCursor(QtCore.Qt.PointingHandCursor)
        self._btn_close.clicked.connect(self.accept)
        fl.addWidget(self._btn_close)
        bl.addWidget(foot)

        sh.addWidget(body, 1)
        root.addWidget(self._shell)

        shadow = QtWidgets.QGraphicsDropShadowEffect(self._shell)
        shadow.setBlurRadius(60)
        shadow.setOffset(0, 6)
        shadow.setColor(QtGui.QColor(0, 0, 0, 100))
        self._shell.setGraphicsEffect(shadow)

        QtWidgets.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Escape), self, self.reject)

        self.resize(980, 660)
        self.setMinimumSize(780, 520)

    def _sheet_warn(self, body: str) -> None:
        MemoryMgrSheet(self, tr("memory_mgr.title"), body, question=False).exec_()

    def _sheet_info(self, body: str) -> None:
        MemoryMgrSheet(self, tr("memory_mgr.title"), body, question=False).exec_()

    def _sheet_confirm(self, body: str) -> bool:
        return MemoryMgrSheet(self, tr("memory_mgr.title"), body, question=True).exec_() == QtWidgets.QDialog.Accepted

    @staticmethod
    def exec_centered(reference: Optional[QtWidgets.QWidget] = None) -> None:
        """打开模态记忆库；reference 仅用于几何对齐，不作为 Qt parent。"""
        dlg = MemoryManagerDialog(reference)
        dlg.exec_()

    def _position_near(self, reference: Optional[QtWidgets.QWidget]):
        screen = QtWidgets.QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        if reference is not None:
            win = reference.window()
            if win and win.isVisible():
                g = win.frameGeometry()
                x = g.center().x() - self.width() // 2
                y = g.center().y() - self.height() // 2
                self.move(max(geo.left(), x), max(geo.top(), y))
                return
        c = geo.center()
        self.move(c.x() - self.width() // 2, c.y() - self.height() // 2)

    def showEvent(self, e: QtGui.QShowEvent):
        super().showEvent(e)
        QtCore.QTimer.singleShot(0, lambda: self._position_near(self._caller_parent))

    def _on_nav_clicked(self, btn: QtWidgets.QAbstractButton):
        idx = self._nav_group.id(btn)
        if idx < 0:
            return
        self._stack.setCurrentIndex(idx)
        self._update_stats()
        if idx == 0:
            self._reload_episodic_list()
        elif idx == 1:
            self._reload_semantic_list()
        else:
            self._reload_procedural_list()

    def _make_card(self, title_label: QtWidgets.QLabel) -> Tuple[QtWidgets.QFrame, QtWidgets.QVBoxLayout]:
        card = QtWidgets.QFrame()
        card.setObjectName("memoryMgrCard")
        vl = QtWidgets.QVBoxLayout(card)
        vl.setContentsMargins(14, 14, 14, 14)
        vl.setSpacing(10)
        title_label.setObjectName("memoryMgrCardTitle")
        vl.addWidget(title_label)
        return card, vl

    def _toolbar_btn_secondary(self) -> QtWidgets.QPushButton:
        b = QtWidgets.QPushButton()
        b.setObjectName("memoryMgrBtnSecondary")
        b.setCursor(QtCore.Qt.PointingHandCursor)
        b.setMinimumHeight(30)
        return b

    def _toolbar_btn_primary(self) -> QtWidgets.QPushButton:
        b = QtWidgets.QPushButton()
        b.setObjectName("memoryMgrBtnPrimary")
        b.setCursor(QtCore.Qt.PointingHandCursor)
        b.setMinimumHeight(30)
        return b

    def _toolbar_btn_danger(self) -> QtWidgets.QPushButton:
        b = QtWidgets.QPushButton()
        b.setObjectName("memoryMgrBtnDanger")
        b.setCursor(QtCore.Qt.PointingHandCursor)
        b.setMinimumHeight(30)
        return b

    def _build_episodic_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        page.setObjectName("memoryMgrPage")
        outer = QtWidgets.QVBoxLayout(page)
        outer.setContentsMargins(16, 6, 16, 14)
        outer.setSpacing(0)

        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        split.setObjectName("memoryMgrSplitter")
        split.setChildrenCollapsible(False)
        split.setHandleWidth(3)

        # 左：列表卡片
        self._lbl_epi_list_title = QtWidgets.QLabel()
        list_card, list_lay = self._make_card(self._lbl_epi_list_title)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(6)
        self._btn_epi_refresh = self._toolbar_btn_secondary()
        self._btn_epi_refresh.clicked.connect(self._reload_episodic_list)
        row.addStretch()
        row.addWidget(self._btn_epi_refresh)
        list_lay.addLayout(row)
        self._filter_epi = QtWidgets.QLineEdit()
        self._filter_epi.setObjectName("memoryMgrFilter")
        self._filter_epi.setFrame(False)
        self._filter_epi.textChanged.connect(self._reload_episodic_list)
        list_lay.addWidget(self._filter_epi)
        self._list_epi = QtWidgets.QListWidget()
        self._list_epi.setObjectName("memoryMgrList")
        self._list_epi.setAlternatingRowColors(False)
        self._list_epi.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._list_epi.currentItemChanged.connect(self._on_epi_selected)
        list_lay.addWidget(self._list_epi, 1)
        split.addWidget(list_card)

        # 右：编辑卡片
        self._lbl_epi_detail_title = QtWidgets.QLabel()
        detail_card, detail_lay = self._make_card(self._lbl_epi_detail_title)
        scroll = QtWidgets.QScrollArea()
        scroll.setObjectName("memoryMgrScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        inner = QtWidgets.QWidget()
        inner.setObjectName("memoryMgrScrollInner")
        form = QtWidgets.QVBoxLayout(inner)
        form.setSpacing(6)
        form.setContentsMargins(2, 2, 2, 2)

        form.addWidget(self._section_label("memory_mgr.sec_meta"))
        meta = QtWidgets.QGridLayout()
        meta.setHorizontalSpacing(12)
        meta.setVerticalSpacing(8)
        r = 0
        self._lbl_epid, self._epi_id = self._ro_field(meta, r, 0, 3)
        r += 1
        self._lbl_epts, self._epi_ts = self._ro_field(meta, r, 0, 3)
        r += 1
        self._lbl_epsess, self._epi_session = self._editable_line_field(meta, r, 0, 3)
        form.addLayout(meta)

        form.addWidget(self._section_label("memory_mgr.sec_task"))
        self._lbl_eptask = QtWidgets.QLabel()
        self._lbl_eptask.setObjectName("memoryMgrFieldLabel")
        form.addWidget(self._lbl_eptask)
        self._epi_task = _memory_plain_edit()
        self._epi_task.setObjectName("memoryMgrCodeEdit")
        self._epi_task.setMinimumHeight(70)
        form.addWidget(self._epi_task)
        self._lbl_epsum = QtWidgets.QLabel()
        self._lbl_epsum.setObjectName("memoryMgrFieldLabel")
        form.addWidget(self._lbl_epsum)
        self._epi_summary = _memory_plain_edit()
        self._epi_summary.setObjectName("memoryMgrCodeEdit")
        self._epi_summary.setMinimumHeight(70)
        form.addWidget(self._epi_summary)

        form.addWidget(self._section_label("memory_mgr.sec_metrics"))
        met2 = QtWidgets.QGridLayout()
        met2.setHorizontalSpacing(12)
        met2.setVerticalSpacing(6)
        rr = 0
        self._lbl_epsucc, self._epi_success = self._check_field(met2, rr, 0)
        self._lbl_epimp, self._epi_importance = self._spin_double_field(met2, rr, 2, 0.01, 10.0, 0.05)
        rr += 1
        self._lbl_eprew, self._epi_reward = self._spin_double_field(met2, rr, 0, -100.0, 100.0, 0.1)
        self._lbl_eperr, self._epi_err = self._spin_int_field(met2, rr, 2, 0, 9999)
        rr += 1
        self._lbl_epretry, self._epi_retry = self._spin_int_field(met2, rr, 0, 0, 9999)
        rr += 1
        self._lbl_eptags, self._epi_tags = self._editable_line_field(met2, rr, 0, 4)
        form.addLayout(met2)

        form.addWidget(self._section_label("memory_mgr.sec_actions"))
        self._lbl_epact = QtWidgets.QLabel()
        self._lbl_epact.setObjectName("memoryMgrFieldLabel")
        form.addWidget(self._lbl_epact)
        self._epi_actions = _memory_plain_edit()
        self._epi_actions.setObjectName("memoryMgrJsonEdit")
        self._epi_actions.setMinimumHeight(100)
        form.addWidget(self._epi_actions)

        ab = QtWidgets.QHBoxLayout()
        ab.setSpacing(8)
        self._btn_epi_save = self._toolbar_btn_primary()
        self._btn_epi_save.clicked.connect(self._save_episodic)
        self._btn_epi_delete = self._toolbar_btn_danger()
        self._btn_epi_delete.clicked.connect(self._delete_episodic)
        ab.addWidget(self._btn_epi_save)
        ab.addWidget(self._btn_epi_delete)
        ab.addStretch()
        form.addLayout(ab)

        scroll.setWidget(inner)
        detail_lay.addWidget(scroll, 1)
        split.addWidget(detail_card)
        split.setSizes([280, 560])
        outer.addWidget(split, 1)
        return page

    def _section_label(self, i18n_key: str) -> QtWidgets.QLabel:
        lb = QtWidgets.QLabel()
        lb.setObjectName("memoryMgrSection")
        lb.setProperty("i18nKey", i18n_key)
        return lb

    def _ro_field(
        self, grid: QtWidgets.QGridLayout, row: int, col: int, colspan: int
    ) -> Tuple[QtWidgets.QLabel, QtWidgets.QLineEdit]:
        lbl = QtWidgets.QLabel()
        lbl.setObjectName("memoryMgrFieldLabel")
        ed = QtWidgets.QLineEdit()
        ed.setObjectName("memoryMgrLineReadOnly")
        ed.setReadOnly(True)
        ed.setFrame(False)
        grid.addWidget(lbl, row, col, 1, 1)
        grid.addWidget(ed, row, col + 1, 1, colspan - 1)
        return lbl, ed

    def _editable_line_field(
        self, grid: QtWidgets.QGridLayout, row: int, col: int, colspan: int
    ) -> Tuple[QtWidgets.QLabel, QtWidgets.QLineEdit]:
        lbl = QtWidgets.QLabel()
        lbl.setObjectName("memoryMgrFieldLabel")
        ed = QtWidgets.QLineEdit()
        ed.setObjectName("memoryMgrLineEdit")
        ed.setFrame(False)
        grid.addWidget(lbl, row, col, 1, 1)
        grid.addWidget(ed, row, col + 1, 1, colspan - 1)
        return lbl, ed

    def _check_field(
        self, grid: QtWidgets.QGridLayout, row: int, col: int
    ) -> Tuple[QtWidgets.QLabel, QtWidgets.QCheckBox]:
        lbl = QtWidgets.QLabel()
        lbl.setObjectName("memoryMgrFieldLabel")
        cb = QtWidgets.QCheckBox()
        cb.setObjectName("memoryMgrCheck")
        grid.addWidget(lbl, row, col, 1, 1)
        grid.addWidget(cb, row, col + 1, 1, 1)
        return lbl, cb

    def _spin_double_field(
        self, grid: QtWidgets.QGridLayout, row: int, col: int,
        lo: float, hi: float, step: float,
    ) -> Tuple[QtWidgets.QLabel, QtWidgets.QDoubleSpinBox]:
        lbl = QtWidgets.QLabel()
        lbl.setObjectName("memoryMgrFieldLabel")
        sp = QtWidgets.QDoubleSpinBox()
        sp.setObjectName("memoryMgrSpin")
        sp.setFrame(False)
        sp.setRange(lo, hi)
        sp.setSingleStep(step)
        grid.addWidget(lbl, row, col, 1, 1)
        grid.addWidget(sp, row, col + 1, 1, 1)
        return lbl, sp

    def _spin_int_field(
        self, grid: QtWidgets.QGridLayout, row: int, col: int, lo: int, hi: int
    ) -> Tuple[QtWidgets.QLabel, QtWidgets.QSpinBox]:
        lbl = QtWidgets.QLabel()
        lbl.setObjectName("memoryMgrFieldLabel")
        sp = QtWidgets.QSpinBox()
        sp.setObjectName("memoryMgrSpin")
        sp.setFrame(False)
        sp.setRange(lo, hi)
        grid.addWidget(lbl, row, col, 1, 1)
        grid.addWidget(sp, row, col + 1, 1, 1)
        return lbl, sp

    def _build_semantic_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        page.setObjectName("memoryMgrPage")
        outer = QtWidgets.QVBoxLayout(page)
        outer.setContentsMargins(16, 6, 16, 14)
        outer.setSpacing(0)

        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        split.setObjectName("memoryMgrSplitter")
        split.setChildrenCollapsible(False)
        split.setHandleWidth(3)

        self._lbl_sem_list_title = QtWidgets.QLabel()
        list_card, list_lay = self._make_card(self._lbl_sem_list_title)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(6)
        self._btn_sem_new = self._toolbar_btn_secondary()
        self._btn_sem_new.clicked.connect(self._new_semantic)
        self._btn_sem_refresh = self._toolbar_btn_secondary()
        self._btn_sem_refresh.clicked.connect(self._reload_semantic_list)
        row.addStretch()
        row.addWidget(self._btn_sem_new)
        row.addWidget(self._btn_sem_refresh)
        list_lay.addLayout(row)
        self._filter_sem = QtWidgets.QLineEdit()
        self._filter_sem.setObjectName("memoryMgrFilter")
        self._filter_sem.setFrame(False)
        self._filter_sem.textChanged.connect(self._reload_semantic_list)
        list_lay.addWidget(self._filter_sem)
        self._list_sem = QtWidgets.QListWidget()
        self._list_sem.setObjectName("memoryMgrList")
        self._list_sem.currentItemChanged.connect(self._on_sem_selected)
        list_lay.addWidget(self._list_sem, 1)
        split.addWidget(list_card)

        self._lbl_sem_detail_title = QtWidgets.QLabel()
        detail_card, detail_lay = self._make_card(self._lbl_sem_detail_title)
        scroll = QtWidgets.QScrollArea()
        scroll.setObjectName("memoryMgrScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        inner = QtWidgets.QWidget()
        inner.setObjectName("memoryMgrScrollInner")
        form = QtWidgets.QVBoxLayout(inner)
        form.setSpacing(6)
        form.setContentsMargins(2, 2, 2, 2)

        form.addWidget(self._section_label("memory_mgr.sec_meta"))
        self._lbl_semid, self._sem_id = self._editable_line_pair(form)
        self._sem_id.setReadOnly(True)
        self._sem_id.setObjectName("memoryMgrLineReadOnly")

        form.addWidget(self._section_label("memory_mgr.sec_rule"))
        self._lbl_semrule = QtWidgets.QLabel()
        self._lbl_semrule.setObjectName("memoryMgrFieldLabel")
        form.addWidget(self._lbl_semrule)
        self._sem_rule = _memory_plain_edit()
        self._sem_rule.setObjectName("memoryMgrCodeEdit")
        self._sem_rule.setMinimumHeight(96)
        form.addWidget(self._sem_rule)

        form.addWidget(self._section_label("memory_mgr.sec_classification"))
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        self._lbl_semcat = QtWidgets.QLabel()
        self._lbl_semcat.setObjectName("memoryMgrFieldLabel")
        self._sem_cat = QtWidgets.QComboBox()
        self._sem_cat.setObjectName("memoryMgrCombo")
        for c in MEMORY_CATEGORIES:
            self._sem_cat.addItem(c, c)
        grid.addWidget(self._lbl_semcat, 0, 0)
        grid.addWidget(self._sem_cat, 0, 1, 1, 3)

        self._lbl_semconf = QtWidgets.QLabel()
        self._lbl_semconf.setObjectName("memoryMgrFieldLabel")
        self._sem_conf = QtWidgets.QDoubleSpinBox()
        self._sem_conf.setObjectName("memoryMgrSpin")
        self._sem_conf.setFrame(False)
        self._sem_conf.setRange(0.0, 1.0)
        self._sem_conf.setSingleStep(0.05)
        grid.addWidget(self._lbl_semconf, 1, 0)
        grid.addWidget(self._sem_conf, 1, 1)

        self._lbl_semlevel = QtWidgets.QLabel()
        self._lbl_semlevel.setObjectName("memoryMgrFieldLabel")
        self._sem_level = QtWidgets.QSpinBox()
        self._sem_level.setObjectName("memoryMgrSpin")
        self._sem_level.setFrame(False)
        self._sem_level.setRange(0, 5)
        grid.addWidget(self._lbl_semlevel, 1, 2)
        grid.addWidget(self._sem_level, 1, 3)

        form.addLayout(grid)

        self._sem_level_hint = QtWidgets.QLabel()
        self._sem_level_hint.setObjectName("memoryMgrHint")
        self._sem_level_hint.setWordWrap(True)
        form.addWidget(self._sem_level_hint)

        self._lbl_semact = QtWidgets.QLabel()
        self._lbl_semact.setObjectName("memoryMgrFieldLabel")
        self._sem_act = QtWidgets.QSpinBox()
        self._sem_act.setObjectName("memoryMgrSpin")
        self._sem_act.setFrame(False)
        self._sem_act.setReadOnly(True)
        self._sem_act.setRange(0, 9999999)
        h_act = QtWidgets.QHBoxLayout()
        h_act.addWidget(self._lbl_semact)
        h_act.addWidget(self._sem_act)
        h_act.addStretch()
        form.addLayout(h_act)

        self._lbl_semeps = QtWidgets.QLabel()
        self._lbl_semeps.setObjectName("memoryMgrFieldLabel")
        form.addWidget(self._lbl_semeps)
        self._sem_episodes = _memory_plain_edit()
        self._sem_episodes.setObjectName("memoryMgrJsonEdit")
        self._sem_episodes.setMinimumHeight(72)
        form.addWidget(self._sem_episodes)

        sb = QtWidgets.QHBoxLayout()
        self._btn_sem_save = self._toolbar_btn_primary()
        self._btn_sem_save.clicked.connect(self._save_semantic)
        self._btn_sem_delete = self._toolbar_btn_danger()
        self._btn_sem_delete.clicked.connect(self._delete_semantic)
        sb.addWidget(self._btn_sem_save)
        sb.addWidget(self._btn_sem_delete)
        sb.addStretch()
        form.addLayout(sb)

        scroll.setWidget(inner)
        detail_lay.addWidget(scroll, 1)
        split.addWidget(detail_card)
        split.setSizes([280, 560])
        outer.addWidget(split, 1)
        return page

    def _editable_line_pair(
        self, form: QtWidgets.QVBoxLayout,
    ) -> Tuple[QtWidgets.QLabel, QtWidgets.QLineEdit]:
        wrap = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(wrap)
        h.setContentsMargins(0, 0, 0, 0)
        lbl = QtWidgets.QLabel()
        lbl.setObjectName("memoryMgrFieldLabel")
        ed = QtWidgets.QLineEdit()
        ed.setObjectName("memoryMgrLineEdit")
        ed.setFrame(False)
        h.addWidget(lbl)
        h.addWidget(ed, 1)
        form.addWidget(wrap)
        return lbl, ed

    def _build_procedural_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        page.setObjectName("memoryMgrPage")
        outer = QtWidgets.QVBoxLayout(page)
        outer.setContentsMargins(16, 6, 16, 14)
        outer.setSpacing(0)

        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        split.setObjectName("memoryMgrSplitter")
        split.setChildrenCollapsible(False)
        split.setHandleWidth(3)

        self._lbl_proc_list_title = QtWidgets.QLabel()
        list_card, list_lay = self._make_card(self._lbl_proc_list_title)
        row = QtWidgets.QHBoxLayout()
        self._btn_proc_new = self._toolbar_btn_secondary()
        self._btn_proc_new.clicked.connect(self._new_procedural)
        self._btn_proc_refresh = self._toolbar_btn_secondary()
        self._btn_proc_refresh.clicked.connect(self._reload_procedural_list)
        row.addStretch()
        row.addWidget(self._btn_proc_new)
        row.addWidget(self._btn_proc_refresh)
        list_lay.addLayout(row)
        self._filter_proc = QtWidgets.QLineEdit()
        self._filter_proc.setObjectName("memoryMgrFilter")
        self._filter_proc.setFrame(False)
        self._filter_proc.textChanged.connect(self._reload_procedural_list)
        list_lay.addWidget(self._filter_proc)
        self._list_proc = QtWidgets.QListWidget()
        self._list_proc.setObjectName("memoryMgrList")
        self._list_proc.currentItemChanged.connect(self._on_proc_selected)
        list_lay.addWidget(self._list_proc, 1)
        split.addWidget(list_card)

        self._lbl_proc_detail_title = QtWidgets.QLabel()
        detail_card, detail_lay = self._make_card(self._lbl_proc_detail_title)
        scroll = QtWidgets.QScrollArea()
        scroll.setObjectName("memoryMgrScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        inner = QtWidgets.QWidget()
        inner.setObjectName("memoryMgrScrollInner")
        form = QtWidgets.QVBoxLayout(inner)
        form.setSpacing(6)
        form.setContentsMargins(2, 2, 2, 2)

        form.addWidget(self._section_label("memory_mgr.sec_meta"))
        self._lbl_pid, self._proc_id = self._editable_line_pair(form)
        self._proc_id.setReadOnly(True)
        self._proc_id.setObjectName("memoryMgrLineReadOnly")

        form.addWidget(self._section_label("memory_mgr.sec_strategy"))
        self._lbl_pname = QtWidgets.QLabel()
        self._lbl_pname.setObjectName("memoryMgrFieldLabel")
        form.addWidget(self._lbl_pname)
        self._proc_name = QtWidgets.QLineEdit()
        self._proc_name.setObjectName("memoryMgrLineEdit")
        self._proc_name.setFrame(False)
        form.addWidget(self._proc_name)

        self._lbl_pdesc = QtWidgets.QLabel()
        self._lbl_pdesc.setObjectName("memoryMgrFieldLabel")
        form.addWidget(self._lbl_pdesc)
        self._proc_desc = _memory_plain_edit()
        self._proc_desc.setObjectName("memoryMgrCodeEdit")
        self._proc_desc.setMinimumHeight(88)
        form.addWidget(self._proc_desc)

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(12)
        self._lbl_ppri, self._proc_priority = self._spin_double_field(grid, 0, 0, 0.0, 1.0, 0.05)
        self._lbl_psr, self._proc_srate = self._spin_double_field(grid, 0, 2, 0.0, 1.0, 0.05)
        self._lbl_puse, self._proc_usage = self._spin_int_field(grid, 1, 0, 0, 99999999)
        self._proc_usage.setReadOnly(True)
        self._lbl_plast = QtWidgets.QLabel()
        self._lbl_plast.setObjectName("memoryMgrFieldLabel")
        self._proc_last = QtWidgets.QLineEdit()
        self._proc_last.setObjectName("memoryMgrLineReadOnly")
        self._proc_last.setReadOnly(True)
        self._proc_last.setFrame(False)
        grid.addWidget(self._lbl_plast, 1, 2)
        grid.addWidget(self._proc_last, 1, 3)
        form.addLayout(grid)

        form.addWidget(self._section_label("memory_mgr.sec_conditions"))
        self._lbl_pconds = QtWidgets.QLabel()
        self._lbl_pconds.setObjectName("memoryMgrFieldLabel")
        form.addWidget(self._lbl_pconds)
        self._proc_conds = _memory_plain_edit()
        self._proc_conds.setObjectName("memoryMgrJsonEdit")
        self._proc_conds.setMinimumHeight(88)
        form.addWidget(self._proc_conds)

        pb = QtWidgets.QHBoxLayout()
        self._btn_proc_save = self._toolbar_btn_primary()
        self._btn_proc_save.clicked.connect(self._save_procedural)
        self._btn_proc_delete = self._toolbar_btn_danger()
        self._btn_proc_delete.clicked.connect(self._delete_procedural)
        pb.addWidget(self._btn_proc_save)
        pb.addWidget(self._btn_proc_delete)
        pb.addStretch()
        form.addLayout(pb)

        scroll.setWidget(inner)
        detail_lay.addWidget(scroll, 1)
        split.addWidget(detail_card)
        split.setSizes([280, 560])
        outer.addWidget(split, 1)
        return page

    # ------------------------------------------------------------------
    # 数据与逻辑（与上一版相同）
    # ------------------------------------------------------------------

    @staticmethod
    def _fmt_ts(ts: float) -> str:
        try:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
        except (ValueError, OSError):
            return str(ts)

    def _update_stats(self):
        st = self._store.get_stats()
        self._stats_lbl.setText(
            tr(
                "memory_mgr.stats",
                st.get("episodic_count", 0),
                st.get("semantic_count", 0),
                st.get("procedural_count", 0),
            )
        )

    def _reload_episodic_list(self):
        self._list_epi.blockSignals(True)
        self._list_epi.clear()
        q = self._filter_epi.text().strip().lower()
        for rec in self._store.get_recent_episodic(3000):
            line = f"{self._fmt_ts(rec.timestamp)} | {rec.task_description or '—'}"
            if q and q not in line.lower() and q not in (rec.id or "").lower():
                continue
            item = QtWidgets.QListWidgetItem(line if len(line) <= 200 else line[:197] + "…")
            item.setData(QtCore.Qt.UserRole, rec.id)
            self._list_epi.addItem(item)
        self._list_epi.blockSignals(False)
        self._clear_epi_form()
        self._update_stats()

    def _clear_epi_form(self):
        for w in (
            self._epi_id, self._epi_ts, self._epi_session, self._epi_tags,
            self._epi_task, self._epi_summary, self._epi_actions,
        ):
            if isinstance(w, QtWidgets.QPlainTextEdit):
                w.clear()
            else:
                w.clear()
        self._epi_success.setChecked(True)
        self._epi_importance.setValue(1.0)
        self._epi_reward.setValue(0.0)
        self._epi_err.setValue(0)
        self._epi_retry.setValue(0)

    def _on_epi_selected(self, cur: Optional[QtWidgets.QListWidgetItem], prev):
        if not cur:
            self._clear_epi_form()
            return
        rid = cur.data(QtCore.Qt.UserRole)
        if not rid:
            return
        rec = self._store.get_episodic(rid)
        if not rec:
            return
        self._epi_id.setText(rec.id)
        self._epi_ts.setText(self._fmt_ts(rec.timestamp))
        self._epi_session.setText(rec.session_id or "")
        self._epi_task.setPlainText(rec.task_description or "")
        self._epi_summary.setPlainText(rec.result_summary or "")
        self._epi_success.setChecked(rec.success)
        self._epi_importance.setValue(float(rec.importance))
        self._epi_reward.setValue(float(rec.reward_score))
        self._epi_err.setValue(int(rec.error_count))
        self._epi_retry.setValue(int(rec.retry_count))
        self._epi_tags.setText(", ".join(rec.tags or []))
        try:
            self._epi_actions.setPlainText(
                json.dumps(rec.actions or [], ensure_ascii=False, indent=2)
            )
        except Exception:
            self._epi_actions.setPlainText(str(rec.actions))

    def _save_episodic(self):
        rid = self._epi_id.text().strip()
        if not rid:
            return
        try:
            actions_raw = self._epi_actions.toPlainText().strip()
            actions = json.loads(actions_raw) if actions_raw else []
            if not isinstance(actions, list):
                raise ValueError("actions must be a JSON array")
        except Exception as e:
            self._sheet_warn(tr("memory_mgr.err_invalid_json") + f"\n{e}")
            return
        old = self._store.get_episodic(rid)
        if not old:
            return
        tags_s = self._epi_tags.text().strip()
        tags = [t.strip() for t in tags_s.split(",") if t.strip()]
        rec = EpisodicRecord(
            id=rid,
            timestamp=old.timestamp,
            session_id=self._epi_session.text().strip(),
            task_description=self._epi_task.toPlainText().strip(),
            actions=actions,
            result_summary=self._epi_summary.toPlainText().strip(),
            success=self._epi_success.isChecked(),
            error_count=self._epi_err.value(),
            retry_count=self._epi_retry.value(),
            reward_score=self._epi_reward.value(),
            importance=self._epi_importance.value(),
            tags=tags,
            embedding=None,
        )
        self._store.add_episodic(rec)
        self._sheet_info(tr("memory_mgr.save_ok"))
        self._reload_episodic_list()
        self._select_list_id(self._list_epi, rid)

    def _delete_episodic(self):
        rid = self._epi_id.text().strip()
        if not rid:
            return
        if not self._sheet_confirm(tr("memory_mgr.delete_confirm_episodic")):
            return
        if self._store.delete_episodic(rid):
            self._reload_episodic_list()

    def _reload_semantic_list(self):
        self._list_sem.blockSignals(True)
        self._list_sem.clear()
        q = self._filter_sem.text().strip().lower()
        for rec in self._store.get_all_semantic(category=None):
            line = f"[L{rec.abstraction_level}][{rec.category}] {(rec.rule or '')[:80]}"
            if q and q not in line.lower() and q not in (rec.id or "").lower():
                continue
            it = QtWidgets.QListWidgetItem(line if len(line) <= 200 else line[:197] + "…")
            it.setData(QtCore.Qt.UserRole, rec.id)
            self._list_sem.addItem(it)
        self._list_sem.blockSignals(False)
        if not self._creating_semantic:
            self._clear_sem_form()
        self._update_stats()

    def _clear_sem_form(self):
        self._creating_semantic = False
        self._sem_id.clear()
        self._sem_rule.clear()
        self._sem_cat.setCurrentIndex(0)
        self._sem_conf.setValue(0.8)
        self._sem_level.setValue(2)
        self._sem_act.setValue(0)
        self._sem_episodes.clear()

    def _new_semantic(self):
        self._creating_semantic = True
        self._list_sem.clearSelection()
        self._sem_id.clear()
        self._sem_rule.clear()
        _gi = self._sem_cat.findData("general")
        self._sem_cat.setCurrentIndex(_gi if _gi >= 0 else 0)
        self._sem_conf.setValue(0.8)
        self._sem_level.setValue(2)
        self._sem_act.setValue(0)
        self._sem_episodes.clear()

    def _on_sem_selected(self, cur: Optional[QtWidgets.QListWidgetItem], prev):
        if not cur:
            if not self._creating_semantic:
                self._clear_sem_form()
            return
        self._creating_semantic = False
        rid = cur.data(QtCore.Qt.UserRole)
        rec = self._store.get_semantic(rid) if rid else None
        if not rec:
            return
        self._sem_id.setText(rec.id)
        self._sem_rule.setPlainText(rec.rule or "")
        idx = self._sem_cat.findData(rec.category)
        if idx >= 0:
            self._sem_cat.setCurrentIndex(idx)
        self._sem_conf.setValue(float(rec.confidence))
        self._sem_level.setValue(int(rec.abstraction_level))
        self._sem_act.setValue(int(rec.activation_count))
        try:
            self._sem_episodes.setPlainText(
                json.dumps(rec.source_episodes or [], ensure_ascii=False, indent=2)
            )
        except Exception:
            self._sem_episodes.setPlainText("[]")

    def _save_semantic(self):
        rule = self._sem_rule.toPlainText().strip()
        if not rule:
            self._sheet_warn(tr("memory_mgr.err_empty_rule"))
            return
        try:
            eps_raw = self._sem_episodes.toPlainText().strip()
            eps = json.loads(eps_raw) if eps_raw else []
            if not isinstance(eps, list):
                raise ValueError("source_episodes must be array")
        except Exception as e:
            self._sheet_warn(tr("memory_mgr.err_invalid_json") + f"\n{e}")
            return
        cat = self._sem_cat.currentData()
        now = time.time()
        if self._creating_semantic or not self._sem_id.text().strip():
            rec = SemanticRecord(
                rule=rule,
                category=cat,
                confidence=self._sem_conf.value(),
                abstraction_level=self._sem_level.value(),
                source_episodes=eps,
                activation_count=0,
                created_at=now,
                updated_at=now,
                embedding=None,
            )
            rid = self._store.add_semantic(rec)
        else:
            old = self._store.get_semantic(self._sem_id.text().strip())
            if not old:
                return
            rec = SemanticRecord(
                id=old.id,
                created_at=old.created_at,
                updated_at=now,
                rule=rule,
                source_episodes=eps,
                confidence=self._sem_conf.value(),
                activation_count=old.activation_count,
                category=cat,
                abstraction_level=self._sem_level.value(),
                embedding=None,
            )
            self._store.add_semantic(rec)
            rid = rec.id
        self._sheet_info(tr("memory_mgr.save_ok"))
        self._creating_semantic = False
        self._reload_semantic_list()
        self._select_list_id(self._list_sem, rid)

    def _delete_semantic(self):
        rid = self._sem_id.text().strip()
        if not rid:
            return
        if not self._sheet_confirm(tr("memory_mgr.delete_confirm_semantic")):
            return
        self._store.delete_semantic(rid)
        self._reload_semantic_list()

    def _reload_procedural_list(self):
        self._list_proc.blockSignals(True)
        self._list_proc.clear()
        q = self._filter_proc.text().strip().lower()
        for rec in self._store.get_all_procedural():
            line = f"{rec.strategy_name or '—'} | prio={rec.priority:.2f}"
            if q and q not in line.lower() and q not in (rec.id or "").lower():
                continue
            it = QtWidgets.QListWidgetItem(line[:200])
            it.setData(QtCore.Qt.UserRole, rec.id)
            self._list_proc.addItem(it)
        self._list_proc.blockSignals(False)
        if not self._creating_procedural:
            self._clear_proc_form()
        self._update_stats()

    def _clear_proc_form(self):
        self._creating_procedural = False
        for w in (self._proc_id, self._proc_name, self._proc_last):
            w.clear()
        self._proc_desc.clear()
        self._proc_priority.setValue(0.5)
        self._proc_srate.setValue(0.5)
        self._proc_usage.setValue(0)
        self._proc_conds.setPlainText("[]")

    def _new_procedural(self):
        self._creating_procedural = True
        self._list_proc.clearSelection()
        self._proc_id.clear()
        self._proc_name.clear()
        self._proc_desc.clear()
        self._proc_priority.setValue(0.5)
        self._proc_srate.setValue(0.5)
        self._proc_usage.setValue(0)
        self._proc_last.clear()
        self._proc_conds.setPlainText("[]")

    def _on_proc_selected(self, cur: Optional[QtWidgets.QListWidgetItem], prev):
        if not cur:
            if not self._creating_procedural:
                self._clear_proc_form()
            return
        self._creating_procedural = False
        rid = cur.data(QtCore.Qt.UserRole)
        rec = self._store.get_procedural(rid) if rid else None
        if not rec:
            return
        self._proc_id.setText(rec.id)
        self._proc_name.setText(rec.strategy_name or "")
        self._proc_desc.setPlainText(rec.description or "")
        self._proc_priority.setValue(float(rec.priority))
        self._proc_srate.setValue(float(rec.success_rate))
        self._proc_usage.setValue(int(rec.usage_count))
        self._proc_last.setText(self._fmt_ts(rec.last_used))
        try:
            self._proc_conds.setPlainText(
                json.dumps(rec.conditions or [], ensure_ascii=False, indent=2)
            )
        except Exception:
            self._proc_conds.setPlainText("[]")

    def _save_procedural(self):
        name = self._proc_name.text().strip()
        if not name:
            self._sheet_warn(tr("memory_mgr.err_empty_strategy"))
            return
        try:
            cr = self._proc_conds.toPlainText().strip()
            conds = json.loads(cr) if cr else []
            if not isinstance(conds, list):
                raise ValueError("conditions must be array")
        except Exception as e:
            self._sheet_warn(tr("memory_mgr.err_invalid_json") + f"\n{e}")
            return
        if self._creating_procedural or not self._proc_id.text().strip():
            rec = ProceduralRecord(
                strategy_name=name,
                description=self._proc_desc.toPlainText().strip(),
                priority=self._proc_priority.value(),
                success_rate=self._proc_srate.value(),
                usage_count=0,
                last_used=time.time(),
                conditions=conds,
                embedding=None,
            )
            rid = self._store.add_procedural(rec)
        else:
            old = self._store.get_procedural(self._proc_id.text().strip())
            if not old:
                return
            rec = ProceduralRecord(
                id=old.id,
                strategy_name=name,
                description=self._proc_desc.toPlainText().strip(),
                priority=self._proc_priority.value(),
                success_rate=self._proc_srate.value(),
                usage_count=old.usage_count,
                last_used=old.last_used,
                conditions=conds,
                embedding=None,
            )
            self._store.add_procedural(rec)
            rid = rec.id
        self._sheet_info(tr("memory_mgr.save_ok"))
        self._creating_procedural = False
        self._reload_procedural_list()
        self._select_list_id(self._list_proc, rid)

    def _delete_procedural(self):
        rid = self._proc_id.text().strip()
        if not rid:
            return
        if not self._sheet_confirm(tr("memory_mgr.delete_confirm_procedural")):
            return
        if self._store.delete_procedural(rid):
            self._reload_procedural_list()

    def _select_list_id(self, lst: QtWidgets.QListWidget, rid: str):
        for i in range(lst.count()):
            it = lst.item(i)
            if it and it.data(QtCore.Qt.UserRole) == rid:
                lst.setCurrentItem(it)
                break

    def _retranslate_ui(self):
        self.setWindowTitle(tr("memory_mgr.title"))
        self._title_lbl.setText(tr("memory_mgr.chrome_title"))
        self._subtitle_lbl.setText(tr("memory_mgr.chrome_tagline"))

        self._btn_nav_epi.setText(tr("memory_mgr.tab_episodic"))
        self._btn_nav_sem.setText(tr("memory_mgr.tab_semantic"))
        self._btn_nav_pro.setText(tr("memory_mgr.tab_procedural"))

        self._lbl_epi_list_title.setText(tr("memory_mgr.panel_list"))
        self._lbl_epi_detail_title.setText(tr("memory_mgr.panel_editor"))
        self._lbl_sem_list_title.setText(tr("memory_mgr.panel_list"))
        self._lbl_sem_detail_title.setText(tr("memory_mgr.panel_editor"))
        self._lbl_proc_list_title.setText(tr("memory_mgr.panel_list"))
        self._lbl_proc_detail_title.setText(tr("memory_mgr.panel_editor"))

        for w in self.findChildren(QtWidgets.QLabel):
            key = w.property("i18nKey")
            if key:
                w.setText(tr(str(key)))

        self._filter_epi.setPlaceholderText(tr("memory_mgr.filter_placeholder"))
        self._filter_sem.setPlaceholderText(tr("memory_mgr.filter_placeholder"))
        self._filter_proc.setPlaceholderText(tr("memory_mgr.filter_placeholder"))

        self._btn_epi_refresh.setText(tr("memory_mgr.refresh"))
        self._btn_sem_refresh.setText(tr("memory_mgr.refresh"))
        self._btn_proc_refresh.setText(tr("memory_mgr.refresh"))
        self._btn_sem_new.setText(tr("memory_mgr.new"))
        self._btn_proc_new.setText(tr("memory_mgr.new"))
        self._btn_epi_save.setText(tr("memory_mgr.save"))
        self._btn_epi_delete.setText(tr("memory_mgr.delete"))
        self._btn_sem_save.setText(tr("memory_mgr.save"))
        self._btn_sem_delete.setText(tr("memory_mgr.delete"))
        self._btn_proc_save.setText(tr("memory_mgr.save"))
        self._btn_proc_delete.setText(tr("memory_mgr.delete"))
        self._btn_close.setText(tr("btn.close"))

        self._lbl_epid.setText(tr("memory_mgr.field_id"))
        self._lbl_epts.setText(tr("memory_mgr.field_time"))
        self._lbl_epsess.setText(tr("memory_mgr.field_session"))
        self._lbl_eptask.setText(tr("memory_mgr.field_task"))
        self._lbl_epsum.setText(tr("memory_mgr.field_summary"))
        self._lbl_epsucc.setText(tr("memory_mgr.field_success"))
        self._lbl_epimp.setText(tr("memory_mgr.field_importance"))
        self._lbl_eprew.setText(tr("memory_mgr.field_reward"))
        self._lbl_eperr.setText(tr("memory_mgr.field_error_count"))
        self._lbl_epretry.setText(tr("memory_mgr.field_retry_count"))
        self._lbl_eptags.setText(tr("memory_mgr.field_tags"))
        self._lbl_epact.setText(tr("memory_mgr.field_actions_json"))

        self._lbl_semid.setText(tr("memory_mgr.field_id"))
        self._lbl_semrule.setText(tr("memory_mgr.field_rule"))
        self._lbl_semcat.setText(tr("memory_mgr.field_category"))
        self._lbl_semconf.setText(tr("memory_mgr.field_confidence"))
        self._lbl_semlevel.setText(tr("memory_mgr.field_abstraction"))
        self._lbl_semact.setText(tr("memory_mgr.field_activation"))
        self._lbl_semeps.setText(tr("memory_mgr.field_sources_json"))

        self._lbl_pid.setText(tr("memory_mgr.field_id"))
        self._lbl_pname.setText(tr("memory_mgr.field_strategy"))
        self._lbl_pdesc.setText(tr("memory_mgr.field_description"))
        self._lbl_ppri.setText(tr("memory_mgr.field_priority"))
        self._lbl_psr.setText(tr("memory_mgr.field_success_rate"))
        self._lbl_puse.setText(tr("memory_mgr.field_usage"))
        self._lbl_plast.setText(tr("memory_mgr.field_last_used"))
        self._lbl_pconds.setText(tr("memory_mgr.field_conditions_json"))

        lvl_lines = [f"{k}: {ABSTRACTION_LEVELS[k]}" for k in sorted(ABSTRACTION_LEVELS.keys())]
        self._sem_level_hint.setText(tr("memory_mgr.level_hint") + "\n" + "\n".join(lvl_lines))

        self._update_stats()
