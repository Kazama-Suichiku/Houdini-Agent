# -*- coding: utf-8 -*-
"""
Houdini Agent — 集中式主题引擎

所有颜色、间距、字号、圆角、QSS 模板统一在此管理。
组件不再自行拼写 setStyleSheet()，而是调用 Theme.xxx_style() 获取。

兼容说明:
- CursorTheme 类保留全部原有属性（旧代码 from .cursor_widgets import CursorTheme 仍可用）
- 新增 Theme 工具类，提供语义化样式工厂
"""

from __future__ import annotations


# ============================================================
# 设计令牌 (Design Tokens)
# ============================================================

class CursorTheme:
    """Cursor 风格深色主题 — 设计令牌

    向后兼容: 保留所有旧属性名，外部 ``CursorTheme.BG_PRIMARY`` 等引用不受影响。
    """

    # ---- 背景色 ----
    BG_PRIMARY   = "#1e1e1e"
    BG_SECONDARY = "#252526"
    BG_TERTIARY  = "#2d2d30"
    BG_HOVER     = "#3c3c3c"

    # ---- 边框色 ----
    BORDER       = "#3c3c3c"
    BORDER_FOCUS = "#007acc"

    # ---- 文字色 ----
    TEXT_PRIMARY   = "#cccccc"
    TEXT_SECONDARY = "#858585"
    TEXT_MUTED     = "#6a6a6a"
    TEXT_BRIGHT    = "#ffffff"

    # ---- 语义强调色 (精简为 4 色) ----
    ACCENT       = "#007acc"       # 品牌主色 / 链接 / 选中态
    ACCENT_BLUE  = "#007acc"       # 兼容旧名
    SUCCESS      = "#4ec9b0"       # 成功 / 已完成
    ACCENT_GREEN = "#4ec9b0"       # 兼容旧名
    WARNING      = "#e0a458"       # 警告 / 工具调用 / 待确认
    ACCENT_ORANGE = "#ce9178"      # 兼容旧名
    ERROR        = "#f14c4c"       # 错误 / 危险操作
    ACCENT_RED   = "#f14c4c"       # 兼容旧名

    # ---- 保留旧强调色（兼容，但新代码不应使用）----
    ACCENT_PURPLE = "#c586c0"
    ACCENT_YELLOW = "#dcdcaa"
    ACCENT_BEIGE  = "#c8a882"

    # ---- 消息边界色 ----
    BORDER_USER = "#555555"
    BORDER_AI   = "#a0a0a0"

    # ---- 字体栈 ----
    FONT_BODY = "'Microsoft YaHei', 'SimSun', 'Segoe UI', sans-serif"
    FONT_CODE = "'Consolas', 'Monaco', 'Courier New', monospace"

    # ---- 标准化令牌 (新代码应使用这些) ----

    # 间距
    SP_XS  = 4
    SP_SM  = 8
    SP_MD  = 12
    SP_LG  = 16
    SP_XL  = 24

    # 字号
    FS_XS  = 11   # 微型标签
    FS_SM  = 12   # 辅助文字
    FS_MD  = 14   # 正文
    FS_LG  = 15   # 小标题
    FS_XL  = 16   # 大标题

    # 圆角
    R_SM   = 4    # 按钮、输入框
    R_MD   = 6    # 卡片、弹窗
    R_LG   = 8    # 大卡片
    R_FULL = 9999 # 药丸型


# ============================================================
# 样式工厂 (Style Factory)
# ============================================================

class Theme:
    """集中式 QSS 样式工厂

    用法示例::

        btn.setStyleSheet(Theme.button())
        btn.setStyleSheet(Theme.button('primary'))
        edit.setStyleSheet(Theme.input())
        frame.setStyleSheet(Theme.card())
    """

    T = CursorTheme  # 快捷引用

    # ---------- 按钮 ----------

    @classmethod
    def button(cls, variant: str = 'ghost', extra: str = '') -> str:
        """生成按钮 QSS

        variant:
            'ghost'   — 透明背景，hover 时显示背景 (默认)
            'primary' — 品牌色填充
            'danger'  — 红色填充
            'outline' — 边框按钮
        """
        T = cls.T
        base = f"""
            QPushButton {{
                font-size: {T.FS_SM}px;
                font-family: {T.FONT_BODY};
                border-radius: {T.R_SM}px;
                padding: 4px 12px;
                min-height: 24px;
                {extra}
            }}
        """
        if variant == 'primary':
            return base + f"""
                QPushButton {{
                    background: {T.ACCENT};
                    color: {T.TEXT_BRIGHT};
                    border: none;
                }}
                QPushButton:hover {{
                    background: #1a8cff;
                }}
                QPushButton:disabled {{
                    background: {T.BG_TERTIARY};
                    color: {T.TEXT_MUTED};
                }}
            """
        if variant == 'danger':
            return base + f"""
                QPushButton {{
                    background: {T.ERROR};
                    color: {T.TEXT_BRIGHT};
                    border: none;
                }}
                QPushButton:hover {{
                    background: #ff6b6b;
                }}
            """
        if variant == 'outline':
            return base + f"""
                QPushButton {{
                    background: transparent;
                    color: {T.TEXT_SECONDARY};
                    border: 1px solid {T.BORDER};
                }}
                QPushButton:hover {{
                    background: {T.BG_HOVER};
                    color: {T.TEXT_PRIMARY};
                    border-color: {T.TEXT_MUTED};
                }}
            """
        # ghost (default)
        return base + f"""
            QPushButton {{
                background: transparent;
                color: {T.TEXT_SECONDARY};
                border: none;
            }}
            QPushButton:hover {{
                background: {T.BG_HOVER};
                color: {T.TEXT_PRIMARY};
            }}
        """

    @classmethod
    def small_button(cls, variant: str = 'outline', extra: str = '') -> str:
        """紧凑型按钮 (header / toolbar 用)"""
        T = cls.T
        if variant == 'outline':
            return f"""
                QPushButton {{
                    background: {T.BG_TERTIARY};
                    color: {T.TEXT_SECONDARY};
                    border: 1px solid {T.BORDER};
                    border-radius: {T.R_SM}px;
                    font-size: {T.FS_XS}px;
                    font-family: {T.FONT_BODY};
                    padding: 2px 8px;
                    min-height: 22px;
                    {extra}
                }}
                QPushButton:hover {{
                    background: {T.BG_HOVER};
                    color: {T.TEXT_PRIMARY};
                }}
            """
        if variant == 'warning':
            return f"""
                QPushButton {{
                    background: {T.BG_TERTIARY};
                    color: {T.WARNING};
                    border: 1px solid {T.WARNING};
                    border-radius: {T.R_SM}px;
                    font-size: {T.FS_XS}px;
                    font-family: {T.FONT_BODY};
                    padding: 2px 8px;
                    min-height: 22px;
                    {extra}
                }}
                QPushButton:hover {{
                    background: {T.WARNING};
                    color: {T.BG_PRIMARY};
                }}
            """
        # ghost
        return f"""
            QPushButton {{
                background: transparent;
                color: {T.TEXT_MUTED};
                border: none;
                border-radius: {T.R_SM}px;
                font-size: {T.FS_XS}px;
                font-family: {T.FONT_BODY};
                padding: 2px 8px;
                min-height: 22px;
                {extra}
            }}
            QPushButton:hover {{
                background: {T.BG_HOVER};
                color: {T.TEXT_PRIMARY};
            }}
        """

    # ---------- 输入框 ----------

    @classmethod
    def input(cls, extra: str = '') -> str:
        T = cls.T
        return f"""
            QPlainTextEdit {{
                background-color: {T.BG_TERTIARY};
                color: {T.TEXT_PRIMARY};
                border: 1px solid {T.BORDER};
                border-radius: {T.R_SM}px;
                padding: {T.SP_SM}px;
                font-size: {T.FS_MD}px;
                font-family: {T.FONT_BODY};
                {extra}
            }}
            QPlainTextEdit:focus {{
                border-color: {T.ACCENT};
            }}
        """

    # ---------- 下拉框 ----------

    @classmethod
    def combo(cls, extra: str = '') -> str:
        T = cls.T
        return f"""
            QComboBox {{
                background: {T.BG_TERTIARY};
                color: {T.TEXT_PRIMARY};
                border: 1px solid {T.BORDER};
                border-radius: {T.R_SM}px;
                padding: 3px 8px;
                font-size: {T.FS_SM}px;
                font-family: {T.FONT_BODY};
                min-height: 24px;
                {extra}
            }}
            QComboBox::drop-down {{
                border: none;
                width: 16px;
            }}
            QComboBox QAbstractItemView {{
                background: {T.BG_TERTIARY};
                color: {T.TEXT_PRIMARY};
                border: 1px solid {T.BORDER};
                font-size: {T.FS_SM}px;
                selection-background-color: {T.BG_HOVER};
            }}
        """

    # ---------- 卡片 / 面板 ----------

    @classmethod
    def card(cls, border_color: str = '', extra: str = '') -> str:
        T = cls.T
        bc = border_color or T.BORDER
        return f"""
            QFrame {{
                background: {T.BG_SECONDARY};
                border: 1px solid {bc};
                border-radius: {T.R_MD}px;
                {extra}
            }}
        """

    # ---------- 复选框 (圆形 radio 风格) ----------

    @classmethod
    def radio_check(cls, active_color: str = '') -> str:
        T = cls.T
        ac = active_color or T.ACCENT
        return f"""
            QCheckBox {{
                color: {T.TEXT_SECONDARY};
                font-size: {T.FS_SM}px;
                spacing: 4px;
            }}
            QCheckBox::indicator {{
                width: 14px; height: 14px;
                border-radius: 7px;
                border: 1.5px solid {T.TEXT_MUTED};
                background: transparent;
            }}
            QCheckBox::indicator:checked {{
                background: {ac};
                border-color: {ac};
                image: none;
            }}
            QCheckBox::indicator:hover {{
                border-color: {ac};
            }}
        """

    # ---------- 标签页 ----------

    @classmethod
    def tab_bar(cls) -> str:
        T = cls.T
        return f"""
            QTabBar {{
                background: transparent;
            }}
            QTabBar::tab {{
                background: {T.BG_TERTIARY};
                color: {T.TEXT_MUTED};
                border: 1px solid {T.BORDER};
                border-bottom: none;
                padding: 5px 14px;
                margin-right: 2px;
                font-size: {T.FS_XS}px;
                font-family: {T.FONT_BODY};
                min-width: 60px;
                max-width: 200px;
                border-top-left-radius: {T.R_SM}px;
                border-top-right-radius: {T.R_SM}px;
            }}
            QTabBar::tab:selected {{
                background: {T.BG_PRIMARY};
                color: {T.TEXT_PRIMARY};
                border-bottom: 2px solid {T.ACCENT};
            }}
            QTabBar::tab:hover:!selected {{
                background: {T.BG_HOVER};
                color: {T.TEXT_SECONDARY};
            }}
        """

    # ---------- 滚动条 ----------

    @classmethod
    def scrollbar(cls) -> str:
        T = cls.T
        return f"""
            QScrollBar:vertical {{
                background: {T.BG_SECONDARY};
                width: 10px;
                margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: {T.BG_HOVER};
                border-radius: {T.R_SM}px;
                min-height: 30px;
                margin: 2px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {T.TEXT_MUTED};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: none;
            }}
        """

    # ---------- 右键菜单 ----------

    @classmethod
    def context_menu(cls) -> str:
        T = cls.T
        return f"""
            QMenu {{
                background: {T.BG_SECONDARY};
                color: {T.TEXT_PRIMARY};
                border: 1px solid {T.BORDER};
                border-radius: {T.R_MD}px;
                font-size: {T.FS_SM}px;
                font-family: {T.FONT_BODY};
                padding: 4px 0px;
            }}
            QMenu::item {{
                padding: 6px 24px;
            }}
            QMenu::item:selected {{
                background: {T.BG_HOVER};
            }}
            QMenu::separator {{
                height: 1px;
                background: {T.BORDER};
                margin: 4px 8px;
            }}
        """

    # ---------- 代码区域 ----------

    @classmethod
    def code_area(cls, bg: str = '#1a1a2e', extra: str = '') -> str:
        T = cls.T
        return f"""
            QTextEdit {{
                background: {bg};
                color: {T.TEXT_PRIMARY};
                border: none;
                padding: 6px 8px;
                font-family: {T.FONT_CODE};
                font-size: 13px;
                {extra}
            }}
            QTextEdit QScrollBar:vertical {{
                width: 5px; background: transparent;
            }}
            QTextEdit QScrollBar::handle:vertical {{
                background: #3c3c3c; border-radius: 2px;
            }}
            QTextEdit QScrollBar:horizontal {{
                height: 5px; background: transparent;
            }}
            QTextEdit QScrollBar::handle:horizontal {{
                background: #3c3c3c; border-radius: 2px;
            }}
        """

    # ---------- 标签文字 ----------

    @classmethod
    def label(cls, variant: str = 'body', extra: str = '') -> str:
        T = cls.T
        if variant == 'muted':
            return f"color: {T.TEXT_MUTED}; font-size: {T.FS_SM}px; font-family: {T.FONT_BODY}; {extra}"
        if variant == 'code':
            return f"color: {T.TEXT_MUTED}; font-size: {T.FS_SM}px; font-family: {T.FONT_CODE}; {extra}"
        if variant == 'heading':
            return f"color: {T.TEXT_PRIMARY}; font-size: {T.FS_LG}px; font-weight: bold; font-family: {T.FONT_BODY}; {extra}"
        # body
        return f"color: {T.TEXT_PRIMARY}; font-size: {T.FS_MD}px; font-family: {T.FONT_BODY}; {extra}"

    # ---------- 工具函数 ----------

    @classmethod
    def header_frame(cls) -> str:
        T = cls.T
        return f"""
            QFrame {{
                background-color: {T.BG_SECONDARY};
                border-bottom: 1px solid {T.BORDER};
            }}
        """

    @classmethod
    def divider(cls) -> str:
        return f"color: {cls.T.BORDER}; max-height: 1px;"
