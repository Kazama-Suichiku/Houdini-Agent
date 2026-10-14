# -*- coding: utf-8 -*-
"""上下文/用量统计：以 API 返回的真实 usage 为准（锚点 + 增量估算），并随会话保存。"""
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from houdini_agent.ui_qml.controller import Controller  # noqa: E402


class _Client:
    @staticmethod
    def _estimate_messages_tokens(msgs, tools):
        return 10 * len(msgs)


def _fake(history):
    sess = {"usage": {}}
    f = SimpleNamespace(_session=SimpleNamespace(history=history, client=_Client()),
                        _sessions=[sess], _active=0, _pending_images=[], _ctx_anchor=None,
                        _ctx_ratio=1.0, _new_token_stats=Controller._new_token_stats)
    return f, sess


def test_anchor_uses_last_call_and_drops_reasoning():
    f, sess = _fake([{}, {}, {}, {}])
    recs = [{"input_tokens": 100, "output_tokens": 50, "reasoning_tokens": 0},
            {"input_tokens": 900, "output_tokens": 300, "reasoning_tokens": 200}]
    Controller._update_ctx_anchor(f, recs)
    assert f._ctx_anchor == {"tokens": 1000, "hist_len": 4}
    assert sess["usage"]["ctx"] is f._ctx_anchor


def test_anchored_ctx_adds_only_new_messages():
    f, _ = _fake([{}, {}])
    f._ctx_anchor = {"tokens": 5000, "hist_len": 2}
    assert Controller._anchored_ctx(f) == 5000
    f._session.history += [{"role": "user", "content": "x"}]
    assert Controller._anchored_ctx(f) == 5010


def test_anchored_ctx_falls_back_when_history_shrinks():
    f, _ = _fake([{}])
    f._ctx_anchor = {"tokens": 5000, "hist_len": 3}
    assert Controller._anchored_ctx(f) is None


def test_bind_usage_is_per_session_and_restores_ratio():
    f, _ = _fake([])
    a = {"stats": {"input": 7, "total": 9}, "ctx": {"tokens": 42, "hist_len": 1, "ratio": 0.7}}
    s = {"usage": a}
    Controller._bind_usage(f, s)
    assert f._token_stats is a["stats"] and f._token_stats["output"] == 0
    assert f._ctx_anchor["tokens"] == 42 and f._ctx_ratio == 0.7
    Controller._bind_usage(f, s, reset=True)
    assert f._token_stats["total"] == 0 and f._ctx_anchor is None
