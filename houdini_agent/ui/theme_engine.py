# -*- coding: utf-8 -*-
"""
Theme Engine — 管理 QSS 模板渲染与字号缩放

使用 style_template.qss 中的占位符 {FS_BODY} 等，
根据当前缩放比例生成最终 QSS 字符串。
缩放偏好持久化到 QSettings。
"""

from pathlib import Path
from houdini_agent.qt_compat import QtCore


class ThemeEngine:
    """主题引擎：加载 QSS 模板、字号缩放、持久化"""

    # 基准字号（px）
    BASE_SIZES = {
        "FS_MICRO": 10,
        "FS_XS": 11,
        "FS_SM": 12,
        "FS_BODY": 13,
        "FS_MD": 14,
        "FS_LG": 16,
        "FS_XL": 17,
    }

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

    # ---- 渲染 ----

    def render(self) -> str:
        """将模板中的占位符替换为当前缩放下的实际字号"""
        if not self._template:
            return ""
        qss = self._template
        for name, base in self.BASE_SIZES.items():
            qss = qss.replace("{" + name + "}", str(round(base * self._scale)))
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
