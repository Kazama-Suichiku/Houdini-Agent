# -*- coding: utf-8 -*-
"""
SystemPromptMixin — System prompt construction.
Extracted from ai_tab.py.
"""

from .i18n import get_language
from ..core.system_prompt import build_system_prompt


class SystemPromptMixin:
    """System prompt construction (thin compatibility wrapper around core.system_prompt)."""

    def _build_system_prompt(self, with_thinking: bool = True, skip_doc_index: bool = False) -> str:
        return build_system_prompt(
            with_thinking=with_thinking,
            skip_doc_index=skip_doc_index,
            language=get_language(),
        )
