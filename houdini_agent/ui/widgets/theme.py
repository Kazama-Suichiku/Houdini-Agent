import re
import html


def _fmt_duration(seconds: float) -> str:
    """格式化时长: <60s -> '18s', >=60s -> '1m43s'"""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m{s % 60:02d}s"


# ============================================================
# 节点路径 → 可点击链接
# ============================================================

# 匹配 Houdini 节点路径: /obj/..., /out/..., /ch/..., /shop/..., /stage/..., /mat/..., /tasks/...
_NODE_PATH_RE = re.compile(
    r'(?<!["\w/])'                          # 不在引号、字母或 / 之后
    r'(/(?:obj|out|ch|shop|stage|mat|tasks)(?:/[\w.]+)+)'   # 路径本体
    r'(?!["\w/])'                           # 不在引号、字母或 / 之前
)

_NODE_LINK_STYLE = "color:#10b981;text-decoration:none;font-family:Consolas,Monaco,monospace;"


def _linkify_node_paths(text: str) -> str:
    """将文本中的 Houdini 节点路径转换为可点击的 <a> 标签

    使用 houdini:// 协议，点击后由 Qt 的 linkActivated 信号处理跳转。
    """
    return _NODE_PATH_RE.sub(
        lambda m: f'<a href="houdini://{m.group(1)}" style="{_NODE_LINK_STYLE}">{m.group(1)}</a>',
        text,
    )


def _linkify_node_paths_plain(text: str) -> str:
    """将纯文本中的节点路径转换为富文本 HTML（含可点击链接）

    先 html.escape 再 linkify，保证安全。
    """
    escaped = html.escape(text)
    return _linkify_node_paths(escaped).replace('\n', '<br>')


# ============================================================
# 颜色主题 (深色主题)
# ============================================================

class CursorTheme:
    """Glassmorphism 深色主题 — 蓝紫底色 + 玻璃质感"""
    # 背景色（深邃蓝黑）
    BG_PRIMARY = "#0f1019"
    BG_SECONDARY = "#0c0e19"
    BG_TERTIARY = "#101224"
    BG_HOVER = "#1c1e36"

    # 边框色（玻璃边缘）
    BORDER = "rgba(255,255,255,12)"
    BORDER_FOCUS = "#3b82f6"

    # 文字色（更明亮）
    TEXT_PRIMARY = "#e2e8f0"
    TEXT_SECONDARY = "#94a3b8"
    TEXT_MUTED = "#64748b"
    TEXT_BRIGHT = "#ffffff"

    # 强调色（更鲜艳）
    ACCENT_BLUE = "#3b82f6"
    ACCENT_GREEN = "#10b981"
    ACCENT_ORANGE = "#f59e0b"
    ACCENT_RED = "#ef4444"
    ACCENT_PURPLE = "#a78bfa"
    ACCENT_YELLOW = "#fbbf24"
    ACCENT_BEIGE = "#d4a574"       # 暖色 — 工具调用/折叠区

    # 消息左边界
    BORDER_USER = "rgba(148,163,184,120)"   # 用户消息 — 柔和银灰
    BORDER_AI = "rgba(167,139,250,100)"     # AI 回复 — 淡紫光晕

    # 字体
    FONT_BODY = "'Microsoft YaHei', 'SimSun', 'Segoe UI', sans-serif"
    FONT_CODE = "'Consolas', 'Monaco', 'Courier New', monospace"
