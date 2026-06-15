# -*- coding: utf-8 -*-
"""Package-level runtime guards for Houdini Agent."""

from __future__ import annotations

import builtins
import sys


class _SafeTextStream:
    """Proxy text streams so non-encodable log characters cannot crash runtime code."""

    def __init__(self, wrapped):
        self._wrapped = wrapped

    def __getattr__(self, name):
        return getattr(self._wrapped, name)

    @property
    def encoding(self):
        return getattr(self._wrapped, "encoding", None)

    def write(self, text):
        try:
            return self._wrapped.write(text)
        except UnicodeEncodeError:
            encoding = getattr(self._wrapped, "encoding", None) or "utf-8"
            safe_text = str(text).encode(encoding, errors="replace").decode(encoding, errors="replace")
            return self._wrapped.write(safe_text)

    def flush(self):
        return self._wrapped.flush()


def _safe_for_stream(value, stream):
    text = str(value)
    encoding = getattr(stream, "encoding", None) or getattr(sys.stdout, "encoding", None) or "utf-8"
    return text.encode(encoding, errors="replace").decode(encoding, errors="replace")


def configure_text_output():
    """Make stdout/stderr tolerant of emoji and other non-GBK characters.

    Houdini on Windows may expose cp936/GBK text streams. A diagnostic print such
    as "[AI Client] <emoji> ..." must never abort the agent loop, so this guard is
    installed at package import time and works even when TextIOWrapper.reconfigure
    is unavailable in the host.
    """

    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None or getattr(stream, "_houdini_agent_safe", False):
            continue
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
        wrapped = _SafeTextStream(stream)
        wrapped._houdini_agent_safe = True
        setattr(sys, name, wrapped)

    if not getattr(builtins.print, "_houdini_agent_safe", False):
        original_print = builtins.print

        def safe_print(*args, **kwargs):
            stream = kwargs.get("file") or sys.stdout
            try:
                return original_print(*args, **kwargs)
            except UnicodeEncodeError:
                safe_args = tuple(_safe_for_stream(arg, stream) for arg in args)
                return original_print(*safe_args, **kwargs)

        safe_print._houdini_agent_safe = True
        builtins.print = safe_print


configure_text_output()

