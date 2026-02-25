# -*- coding: utf-8 -*-
"""
Font Settings Dialog — 字号缩放设置面板

通过 QSlider 实时预览字号缩放效果。
"""

from houdini_agent.qt_compat import QtWidgets, QtCore
from .i18n import tr


class FontSettingsDialog(QtWidgets.QDialog):
    """字号设置面板 — Header 区 "Aa" 按钮弹出"""

    scaleChanged = QtCore.Signal(float)  # 实时通知缩放变化

    def __init__(self, current_scale: float = 1.0, parent=None):
        super().__init__(parent)
        self.setObjectName("fontSettingsDlg")
        self.setWindowTitle(tr('font.title'))
        self.setFixedSize(280, 120)
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowContextHelpButtonHint)

        self._scale = current_scale

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        # 标题 + 百分比
        top = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel(tr('font.scale'))
        title.setObjectName("fontScaleLabel")
        top.addWidget(title)
        top.addStretch()

        self._pct_label = QtWidgets.QLabel(f"{int(round(current_scale * 100))}%")
        self._pct_label.setObjectName("fontScaleLabel")
        top.addWidget(self._pct_label)
        layout.addLayout(top)

        # Slider: 70% ~ 150%
        self._slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._slider.setObjectName("fontScaleSlider")
        self._slider.setRange(70, 150)
        self._slider.setSingleStep(5)
        self._slider.setPageStep(10)
        self._slider.setValue(int(round(current_scale * 100)))
        self._slider.valueChanged.connect(self._on_slider)
        layout.addWidget(self._slider)

        # 底部：重置 + 关闭
        btns = QtWidgets.QHBoxLayout()
        reset_btn = QtWidgets.QPushButton(tr('font.reset'))
        reset_btn.setObjectName("btnSmall")
        reset_btn.clicked.connect(self._reset)
        btns.addWidget(reset_btn)
        btns.addStretch()

        close_btn = QtWidgets.QPushButton(tr('font.close'))
        close_btn.setObjectName("btnSmall")
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        layout.addLayout(btns)

    def _on_slider(self, value: int):
        self._scale = value / 100.0
        self._pct_label.setText(f"{value}%")
        self.scaleChanged.emit(self._scale)

    def _reset(self):
        self._slider.setValue(100)

    @property
    def scale(self) -> float:
        return self._scale
