# -*- coding: utf-8 -*-
"""
Theme Engine — 管理 QSS 模板渲染与字号缩放

使用 style_template.qss 中的占位符 {FS_BODY} 等，
根据当前缩放比例生成最终 QSS 字符串。
缩放偏好持久化到 QSettings。
"""

from pathlib import Path
from houdini_agent.qt_compat import QtCore, QtWidgets, QtGui


class _SystemTextScaleFilter(QtCore.QObject):
    """Scale fixed UI geometry once when widgets are first shown."""

    _SCALED_PROPERTY = "_houdiniAgentSystemTextScaled"
    _LAYOUT_SCALED_PROPERTY = "_houdiniAgentSystemTextLayoutScaled"
    _QT_MAX_SIZE = 16777215

    def __init__(self, parent=None, owned_only: bool = True):
        super().__init__(parent)
        self._owned_only = bool(owned_only)

    @staticmethod
    def _is_houdini_agent_widget(widget) -> bool:
        current = widget
        while current is not None:
            if current.property("houdiniAgentTextScaleRoot"):
                return True
            if type(current).__module__.startswith("houdini_agent."):
                return True
            current = current.parentWidget()
        return False

    def eventFilter(self, watched, event):
        if event.type() == QtCore.QEvent.Show and isinstance(watched, QtWidgets.QWidget):
            if not self._owned_only or self._is_houdini_agent_widget(watched):
                ThemeEngine.scale_widget_tree(watched)
        return False


class ThemeEngine:
    """主题引擎：加载 QSS 模板、字号缩放、持久化"""

    # 基准字号（px）
    BASE_SIZES = {
        "FS_TINY": 9,
        "FS_MICRO": 10,
        "FS_XS": 11,
        "FS_SM": 12,
        "FS_BASE": 13,
        "FS_BODY": 13,
        "FS_CHAT": 11,
        "FS_USER_MSG": 13,
        "FS_INPUT": 13,
        "FS_MD": 14,
        "FS_LG": 16,
        "FS_XL": 17,
    }

    SYSTEM_SCALE_MIN = 0.8
    SYSTEM_SCALE_MAX = 2.25
    SYSTEM_BASE_POINT_SIZE = 9.0
    SYSTEM_BASE_PIXEL_SIZE = 12.0

    SCALE_MIN = 0.7
    SCALE_MAX = 1.5
    SCALE_STEP = 0.1

    def __init__(self):
        self._scale: float = 1.0
        self._template: str = ""

    # ---- 模板加载 ----

    def load_template(self, path: Path):
        """从文件加载 QSS 模板"""
        try:
            self._template = path.read_text("utf-8")
        except Exception as e:
            print(f"[ThemeEngine] 加载模板失败: {e}")
            self._template = ""

    # ---- 缩放控制 ----

    @property
    def scale(self) -> float:
        return self._scale

    @classmethod
    def system_font_scale(cls) -> float:
        """Return the current OS/Houdini application font scale."""
        return max(cls.application_font_scale(), cls.windows_text_scale())

    @classmethod
    def application_font_scale(cls) -> float:
        """Return the scale already represented by QApplication's font."""
        scale = 1.0
        try:
            app = QtWidgets.QApplication.instance()
            font = app.font() if app is not None else QtGui.QGuiApplication.font()
            point_size = float(font.pointSizeF())
            if point_size > 0:
                scale = point_size / cls.SYSTEM_BASE_POINT_SIZE
            else:
                pixel_size = float(font.pixelSize())
                if pixel_size > 0:
                    scale = pixel_size / cls.SYSTEM_BASE_PIXEL_SIZE
        except Exception:
            pass
        return max(cls.SYSTEM_SCALE_MIN, min(cls.SYSTEM_SCALE_MAX, scale))

    @classmethod
    def windows_text_scale(cls) -> float:
        """Read Settings > Accessibility > Text size on Windows."""
        scale = 1.0
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Accessibility",
            ) as key:
                text_scale, _ = winreg.QueryValueEx(key, "TextScaleFactor")
            text_scale = float(text_scale) / 100.0
            if text_scale > 0:
                scale = text_scale
        except Exception:
            pass
        return max(cls.SYSTEM_SCALE_MIN, min(cls.SYSTEM_SCALE_MAX, scale))

    @classmethod
    def install_application_scaling(
        cls,
        app=None,
        *,
        owned_only: bool = True,
        scale_application_font: bool = False,
    ):
        """Install global scaling for fixed controls in every window/dialog."""
        app = app or QtWidgets.QApplication.instance()
        if app is None:
            return None
        existing = getattr(app, "_houdini_agent_text_scale_filter", None)
        if existing is not None:
            return existing
        if scale_application_font:
            cls._ensure_font_scale(app, cls.system_font_scale())
        event_filter = _SystemTextScaleFilter(app, owned_only=owned_only)
        app.installEventFilter(event_filter)
        app._houdini_agent_text_scale_filter = event_filter
        for widget in app.topLevelWidgets():
            if not owned_only or event_filter._is_houdini_agent_widget(widget):
                cls.scale_widget_tree(widget)
        return event_filter

    @classmethod
    def _ensure_font_scale(cls, widget_or_app, desired_scale: float):
        """Add only the text scale not already represented by the current font."""
        try:
            font = QtGui.QFont(widget_or_app.font())
            point_size = float(font.pointSizeF())
            if point_size > 0:
                current_scale = point_size / cls.SYSTEM_BASE_POINT_SIZE
                if desired_scale > current_scale * 1.01:
                    font.setPointSizeF(point_size * desired_scale / current_scale)
                    widget_or_app.setFont(font)
                return
            pixel_size = float(font.pixelSize())
            if pixel_size > 0:
                current_scale = pixel_size / cls.SYSTEM_BASE_PIXEL_SIZE
                if desired_scale > current_scale * 1.01:
                    font.setPixelSize(max(1, int(round(pixel_size * desired_scale / current_scale))))
                    widget_or_app.setFont(font)
        except Exception:
            pass

    @classmethod
    def scale_widget_tree(cls, root):
        """Scale fixed dimensions, icon sizes, and layout spacing exactly once."""
        scale = cls.system_font_scale()
        if scale <= 1.01 or root is None:
            return
        if root.isWindow():
            cls._ensure_font_scale(root, scale)

        widgets = [root]
        widgets.extend(root.findChildren(QtWidgets.QWidget))
        for widget in widgets:
            if widget.property(_SystemTextScaleFilter._SCALED_PROPERTY):
                continue
            widget.setProperty(_SystemTextScaleFilter._SCALED_PROPERTY, True)
            if widget.property("systemTextScaleOptOut"):
                continue

            is_top_level = widget.isWindow()
            min_w, min_h = widget.minimumWidth(), widget.minimumHeight()
            max_w, max_h = widget.maximumWidth(), widget.maximumHeight()
            fixed_w = min_w > 0 and min_w == max_w
            fixed_h = min_h > 0 and min_h == max_h

            if not is_top_level or fixed_w:
                if 0 < max_w < _SystemTextScaleFilter._QT_MAX_SIZE:
                    widget.setMaximumWidth(max(1, int(round(max_w * scale))))
                if min_w > 0:
                    widget.setMinimumWidth(max(1, int(round(min_w * scale))))
            if not is_top_level or fixed_h:
                if 0 < max_h < _SystemTextScaleFilter._QT_MAX_SIZE:
                    widget.setMaximumHeight(max(1, int(round(max_h * scale))))
                if min_h > 0:
                    widget.setMinimumHeight(max(1, int(round(min_h * scale))))

            if isinstance(widget, QtWidgets.QAbstractButton):
                icon_size = widget.iconSize()
                if icon_size.isValid() and not icon_size.isEmpty():
                    widget.setIconSize(QtCore.QSize(
                        max(1, int(round(icon_size.width() * scale))),
                        max(1, int(round(icon_size.height() * scale))),
                    ))

            layout = widget.layout()
            if layout is not None and not layout.property(_SystemTextScaleFilter._LAYOUT_SCALED_PROPERTY):
                layout.setProperty(_SystemTextScaleFilter._LAYOUT_SCALED_PROPERTY, True)
                left, top, right, bottom = layout.getContentsMargins()
                layout.setContentsMargins(
                    int(round(left * scale)),
                    int(round(top * scale)),
                    int(round(right * scale)),
                    int(round(bottom * scale)),
                )
                spacing = layout.spacing()
                if spacing >= 0:
                    layout.setSpacing(int(round(spacing * scale)))

    @property
    def effective_scale(self) -> float:
        return self._scale * self.system_font_scale()

    @classmethod
    def saved_user_scale(cls) -> float:
        try:
            settings = QtCore.QSettings("HoudiniAI", "Assistant")
            return float(settings.value("font_scale", 1.0))
        except Exception:
            return 1.0

    @classmethod
    def scaled_px(cls, base_px: float, user_scale: float = None) -> int:
        if user_scale is None:
            user_scale = cls.saved_user_scale()
        return max(1, int(round(float(base_px) * float(user_scale) * cls.system_font_scale())))

    @classmethod
    def font(cls, family: str = "", base_px: float = None, user_scale: float = None) -> QtGui.QFont:
        font = QtGui.QFont()
        if family:
            families = [part.strip().strip("'\"") for part in family.split(",") if part.strip()]
            if hasattr(font, "setFamilies") and families:
                font.setFamilies(families)
            elif families:
                font.setFamily(families[0])
        if base_px is not None:
            font.setPixelSize(cls.scaled_px(base_px, user_scale=user_scale))
        return font

    def set_scale(self, scale: float):
        """设置缩放比例（自动 clamp 到 [0.7, 1.5]）"""
        self._scale = max(self.SCALE_MIN, min(self.SCALE_MAX, round(scale, 2)))

    def zoom_in(self):
        self.set_scale(self._scale + self.SCALE_STEP)

    def zoom_out(self):
        self.set_scale(self._scale - self.SCALE_STEP)

    def zoom_reset(self):
        self.set_scale(1.0)

    @property
    def scale_percent(self) -> int:
        return int(round(self._scale * 100))

    @property
    def system_scale_percent(self) -> int:
        return int(round(self.system_font_scale() * 100))

    # ---- 渲染 ----

    def render(self) -> str:
        """将模板中的占位符替换为当前缩放下的实际字号"""
        if not self._template:
            return ""
        qss = self._template
        for name, base in self.BASE_SIZES.items():
            qss = qss.replace("{" + name + "}", str(round(base * self.effective_scale)))
        return qss

    # ---- 持久化 ----

    def save_preference(self):
        """保存缩放比例到 QSettings"""
        try:
            settings = QtCore.QSettings("HoudiniAI", "Assistant")
            settings.setValue("font_scale", self._scale)
        except Exception:
            pass

    def load_preference(self):
        """从 QSettings 加载缩放比例"""
        try:
            settings = QtCore.QSettings("HoudiniAI", "Assistant")
            val = settings.value("font_scale", 1.0)
            self.set_scale(float(val))
        except Exception:
            self._scale = 1.0
