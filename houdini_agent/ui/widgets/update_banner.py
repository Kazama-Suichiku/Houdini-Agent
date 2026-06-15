from houdini_agent.qt_compat import QtWidgets, QtCore
from .theme import CursorTheme
from ..i18n import tr


class UpdateNotificationBanner(QtWidgets.QFrame):
    """更新通知横幅 — 在输入区域上方显示新版本提示

    轻量横幅，不打断聊天对话流。
    用户可点击"立即更新"或关闭横幅。
    支持显示更新摘要（release_notes 首行）。
    """

    updateClicked = QtCore.Signal()   # 点击"立即更新"
    dismissClicked = QtCore.Signal()  # 点击"关闭"

    def __init__(self, remote_version: str, release_name: str = "",
                 local_version: str = "", release_notes: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("updateNotifyBanner")
        self.setVisible(False)  # 默认隐藏，由外部调用 show()

        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(10, 5, 6, 5)
        row.setSpacing(8)

        # 图标
        icon_lbl = QtWidgets.QLabel("🚀")
        icon_lbl.setFixedWidth(18)
        icon_lbl.setStyleSheet("background: transparent; border: none;")
        row.addWidget(icon_lbl)

        # 左侧：版本 + 摘要（垂直堆叠）
        text_widget = QtWidgets.QWidget()
        text_layout = QtWidgets.QVBoxLayout(text_widget)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)

        # 版本信息文字
        info_text = tr('update.notify_banner', local_version, remote_version)
        if release_name:
            info_text += f"  —  {release_name}"
        info_lbl = QtWidgets.QLabel(info_text)
        info_lbl.setObjectName("updateNotifyInfo")
        info_lbl.setWordWrap(False)
        text_layout.addWidget(info_lbl)

        # 更新摘要（首行，小字）
        if release_notes and release_notes.strip():
            notes_lbl = QtWidgets.QLabel(release_notes.strip())
            notes_lbl.setObjectName("updateNotifyNotes")
            notes_lbl.setWordWrap(True)
            notes_lbl.setStyleSheet("color: inherit; opacity: 0.85; font-size: 0.92em;")
            text_layout.addWidget(notes_lbl)

        row.addWidget(text_widget, 1)

        # "立即更新" 按钮
        update_btn = QtWidgets.QPushButton(tr('update.notify_update_now'))
        update_btn.setObjectName("updateNotifyBtn")
        update_btn.setCursor(QtCore.Qt.PointingHandCursor)
        update_btn.setFixedHeight(22)
        update_btn.clicked.connect(self.updateClicked.emit)
        row.addWidget(update_btn)

        # 关闭按钮
        dismiss_btn = QtWidgets.QPushButton("✕")
        dismiss_btn.setObjectName("updateNotifyDismiss")
        dismiss_btn.setFixedSize(18, 18)
        dismiss_btn.setCursor(QtCore.Qt.PointingHandCursor)
        dismiss_btn.setToolTip(tr('update.notify_dismiss_tip'))
        dismiss_btn.clicked.connect(self._on_dismiss)
        row.addWidget(dismiss_btn)

    def _on_dismiss(self):
        self.setVisible(False)
        self.dismissClicked.emit()
