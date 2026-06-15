# -*- coding: utf-8 -*-
"""
cursor_widgets — re-export shim.

All widget classes now live in houdini_agent/ui/widgets/. This module
re-exports every public name so that existing imports remain unchanged:

    from houdini_agent.ui.cursor_widgets import AIResponse, CursorTheme, ...

"""

# Theme / utilities
from .widgets.theme import (
    CursorTheme,
    _fmt_duration,
    _linkify_node_paths,
    _linkify_node_paths_plain,
    _NODE_PATH_RE,
    _NODE_LINK_STYLE,
)

# Base widgets
from .widgets.base import (
    AuroraBar,
    CollapsibleSection,
    CollapsibleContent,
    PulseIndicator,
    TurnTraceHeader,
)

# Thinking / VEX
from .widgets.thinking import (
    ThinkingSection,
    ThinkingBar,
    VEXPreviewInline,
    VEXPreviewDialog,
)

# Tool call / execution
from .widgets.tool_call import (
    ToolCallItem,
    ExecutionSection,
)

# Messages
from .widgets.message import (
    ImagePreviewDialog,
    ClickableImageLabel,
    UserMessage,
    AIResponse,
    StatusLine,
)

# Node / param widgets
from .widgets.node_widgets import (
    NodeOperationLabel,
    StreamingCodePreview,
    ParamDiffWidget,
)

# Plan widgets
from .widgets.plan_dag import (
    PlanBlock,
    PlanDAGWidget,
)
from .widgets.plan_stream import StreamingPlanCard
from .widgets.plan_viewer import (
    PlanViewer,
    AskQuestionCard,
)

# Markdown / syntax
from .widgets.markdown import SimpleMarkdown
from .widgets.syntax import SyntaxHighlighter

# Shell / code
from .widgets.shell_widgets import (
    _CollapsibleShellOutput,
    PythonShellWidget,
    SystemShellWidget,
)
from .widgets.code_block import (
    CodeBlockWidget,
    RichContentWidget,
)

# Status bars
from .widgets.status_bar import (
    NodeContextBar,
    ToolStatusBar,
    UnifiedStatusBar,
)

# Chat input
from .widgets.chat_input import (
    SLASH_COMMANDS,
    _SLASH_CATEGORY_LABELS,
    NodeCompleterPopup,
    SlashCommandPopup,
    InputResizeHandle,
    ChatInput,
    StopButton,
    SendButton,
)

# Todo
from .widgets.todo import (
    TodoItem,
    TodoList,
)

# Analytics / update / dialogs
from .widgets.analytics import (
    _BarWidget,
    TokenAnalyticsPanel,
)
from .widgets.update_banner import UpdateNotificationBanner
from .widgets.plugin_manager import (
    PluginManagerDialog,
    PluginSettingsPage,
)
from .widgets.rules_editor import RulesEditorDialog

__all__ = [
    # Theme
    'CursorTheme', '_fmt_duration', '_linkify_node_paths', '_linkify_node_paths_plain',
    '_NODE_PATH_RE', '_NODE_LINK_STYLE',
    # Base
    'AuroraBar', 'CollapsibleSection', 'CollapsibleContent', 'PulseIndicator', 'TurnTraceHeader',
    # Thinking
    'ThinkingSection', 'ThinkingBar', 'VEXPreviewInline', 'VEXPreviewDialog',
    # Tool call
    'ToolCallItem', 'ExecutionSection',
    # Messages
    'ImagePreviewDialog', 'ClickableImageLabel', 'UserMessage', 'AIResponse', 'StatusLine',
    # Node widgets
    'NodeOperationLabel', 'StreamingCodePreview', 'ParamDiffWidget',
    # Plan
    'PlanBlock', 'PlanDAGWidget', 'StreamingPlanCard', 'PlanViewer', 'AskQuestionCard',
    # Markdown / syntax
    'SimpleMarkdown', 'SyntaxHighlighter',
    # Shell / code
    '_CollapsibleShellOutput', 'PythonShellWidget', 'SystemShellWidget',
    'CodeBlockWidget', 'RichContentWidget',
    # Status bars
    'NodeContextBar', 'ToolStatusBar', 'UnifiedStatusBar',
    # Chat input
    'SLASH_COMMANDS', '_SLASH_CATEGORY_LABELS',
    'NodeCompleterPopup', 'SlashCommandPopup', 'InputResizeHandle', 'ChatInput', 'StopButton', 'SendButton',
    # Todo
    'TodoItem', 'TodoList',
    # Analytics / update / dialogs
    '_BarWidget', 'TokenAnalyticsPanel', 'UpdateNotificationBanner',
    'PluginManagerDialog', 'PluginSettingsPage', 'RulesEditorDialog',
]
