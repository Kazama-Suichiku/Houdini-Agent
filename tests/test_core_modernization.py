# -*- coding: utf-8 -*-
"""核心现代化改造回归测试（Phase 1-4）。"""
import json
import threading

import pytest

from houdini_agent.core.system_prompt import build_system_prompt
from houdini_agent.utils.ai_client_agent import AIClientAgentMixin
from houdini_agent.utils.ai_client_context import AIClientContextMixin
from houdini_agent.utils.ai_client_providers import AIClientProvidersMixin
from houdini_agent.utils.ai_client_streaming import AIClientStreamingMixin
from houdini_agent.utils.ultra_optimizer import UltraOptimizer


# ── 1. Anthropic thinking + temperature & thinking_block round-trip ─────────

def test_anthropic_convert_roundtrips_thinking_blocks():
    msgs = [
        {'role': 'user', 'content': 'build box'},
        {
            'role': 'assistant',
            'content': 'creating box',
            '_anthropic_thinking_blocks': [
                {'type': 'thinking', 'thinking': 'plan box', 'signature': 'sig_abc'},
            ],
            'tool_calls': [{
                'id': 'toolu_1',
                'type': 'function',
                'function': {'name': 'create_node', 'arguments': '{"node_type": "box"}'},
            }],
        },
        {'role': 'tool', 'tool_call_id': 'toolu_1', 'content': '/obj/geo1/box1'},
    ]
    _sys, anth = AIClientStreamingMixin._convert_messages_to_anthropic(msgs)
    blocks = anth[1]['content']
    assert blocks[0] == {'type': 'thinking', 'thinking': 'plan box', 'signature': 'sig_abc'}
    assert blocks[1]['type'] == 'text'
    assert blocks[2]['type'] == 'tool_use'


def test_anthropic_stream_omits_temperature_when_thinking_enabled():
    captured = {}

    class _FakeResp:
        status_code = 200
        encoding = 'utf-8'
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def iter_content(self, chunk_size=1024):
            yield b'event: message_stop\ndata: {"type":"message_stop"}\n\n'

    class _FakeSession:
        def post(self, url, json=None, headers=None, **kw):
            captured['payload'] = json
            return _FakeResp()

    class _Client(AIClientProvidersMixin, AIClientStreamingMixin):
        def __init__(self):
            self._http_session = _FakeSession()
            self._max_retries = 1
            self._chunk_timeout = 10
            self._stop_event = threading.Event()
        def _get_api_url(self, provider, model):
            return 'https://api.anthropic.com/v1/messages'

    c = _Client()
    list(c._chat_stream_anthropic(
        messages=[{'role': 'user', 'content': 'hi'}],
        model='claude-fable-5-2', provider='anthropic',
        temperature=0.17, max_tokens=4096, enable_thinking=True, api_key='k',
    ))
    p = captured['payload']
    assert 'thinking' in p
    assert p['thinking']['budget_tokens'] < p['max_tokens']
    assert 'temperature' not in p


def test_openai_o_series_omits_temperature():
    captured = {}

    class _FakeResp:
        status_code = 200
        encoding = 'utf-8'
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def iter_content(self, chunk_size=1024):
            yield b'data: [DONE]\n\n'

    class _FakeSession:
        def post(self, url, json=None, headers=None, **kw):
            captured['payload'] = json
            return _FakeResp()

    class _Client(AIClientProvidersMixin, AIClientStreamingMixin):
        def __init__(self):
            self._http_session = _FakeSession()
            self._max_retries = 1
            self._chunk_timeout = 10
            self._stop_event = threading.Event()
        def _get_api_key(self, provider): return 'sk-test'
        def _get_api_url(self, provider, model): return 'https://api.openai.com/v1/chat/completions'
        def _is_anthropic_protocol(self, provider, model): return False
        def _sub_kind(self, provider): return None

    c = _Client()
    list(c.chat_stream(
        messages=[{'role': 'user', 'content': 'hi'}],
        model='o3-mini', provider='openai', temperature=0.17,
    ))
    assert 'temperature' not in captured['payload']


# ── 2. Dedup cache invalidation on scene mutation & no perf_stop dedup ──────

def test_turn_dedup_cache_invalidates_params_on_set_and_never_caches_perf_stop():
    class _DedupAgent(AIClientContextMixin, AIClientAgentMixin):
        _QUERY_TOOLS = frozenset({'get_node_parameters'})
        _OP_TOOLS = frozenset({'set_node_parameter'})

        def __init__(self):
            self._stop_event = threading.Event()
            self._RE_CLEAN_PATTERNS = []
            self.step = 0
            self.param_val = 1.0
            self.exec_log = []
            self._tool_executor = self._exec
            self._batch_tool_executor = None

        def is_reasoning_model(self, model): return False

        def _exec(self, tool_name, **kw):
            self.exec_log.append(tool_name)
            if tool_name == 'get_node_parameters':
                return {'success': True, 'result': f'tx={self.param_val}'}
            if tool_name == 'set_node_parameter':
                self.param_val = 5.0
                return {'success': True, 'result': 'ok'}
            if tool_name == 'perf_stop_and_report':
                return {'success': True, 'result': f'report #{len(self.exec_log)}'}
            return {'success': True, 'result': 'doc_ok'}

        def chat_stream(self, **kwargs):
            self.step += 1
            if self.step == 1:
                # Query param + static doc + perf_stop
                yield {'type': 'tool_call', 'tool_call': {
                    'id': 'c1', 'type': 'function',
                    'function': {'name': 'get_node_parameters', 'arguments': '{"node_path": "/obj/geo1"}'},
                }}
                yield {'type': 'tool_call', 'tool_call': {
                    'id': 'c2', 'type': 'function',
                    'function': {'name': 'search_local_doc', 'arguments': '{"query": "scatter"}'},
                }}
                yield {'type': 'tool_call', 'tool_call': {
                    'id': 'c3', 'type': 'function',
                    'function': {'name': 'perf_stop_and_report', 'arguments': '{}'},
                }}
            elif self.step == 2:
                # Mutate param
                yield {'type': 'tool_call', 'tool_call': {
                    'id': 'c4', 'type': 'function',
                    'function': {'name': 'set_node_parameter', 'arguments': '{"node_path": "/obj/geo1", "parm": "tx", "val": 5}'},
                }}
            elif self.step == 3:
                # Re-query param (MUST NOT be stale!), static doc (SHOULD hit cache), perf_stop (MUST NOT hit cache)
                yield {'type': 'tool_call', 'tool_call': {
                    'id': 'c5', 'type': 'function',
                    'function': {'name': 'get_node_parameters', 'arguments': '{"node_path": "/obj/geo1"}'},
                }}
                yield {'type': 'tool_call', 'tool_call': {
                    'id': 'c6', 'type': 'function',
                    'function': {'name': 'search_local_doc', 'arguments': '{"query": "scatter"}'},
                }}
                yield {'type': 'tool_call', 'tool_call': {
                    'id': 'c7', 'type': 'function',
                    'function': {'name': 'perf_stop_and_report', 'arguments': '{}'},
                }}
            else:
                yield {'type': 'content', 'content': 'done'}
            yield {'type': 'done', 'usage': {}}

    a = _DedupAgent()
    res = a.agent_loop_stream(
        messages=[{'role': 'system', 'content': 's'}, {'role': 'user', 'content': 'u'}],
        model='gpt-5.2', provider='openai', tools_override=[], enable_thinking=False,
    )
    assert res['ok'] is True
    # get_node_parameters called TWICE (cache invalidated by set_node_parameter!)
    assert a.exec_log.count('get_node_parameters') == 2
    # search_local_doc called ONCE (static doc stays cached!)
    assert a.exec_log.count('search_local_doc') == 1
    # perf_stop_and_report called TWICE (never deduplicated!)
    assert a.exec_log.count('perf_stop_and_report') == 2
    # Verify the second get_node_parameters returned updated tx=5.0
    param_msgs = [m['content'] for m in res['new_messages'] if m.get('tool_call_id') == 'c5']
    assert param_msgs == ['tx=5.0']


# ── 3. _GUIDANCE_THINK skipped for think_tags=False & native reasoning ──────

def test_guidance_think_not_injected_when_think_tags_false():
    seen_tool_contents = []

    class _Agent(AIClientContextMixin, AIClientAgentMixin):
        _QUERY_TOOLS = frozenset()
        _OP_TOOLS = frozenset()

        def __init__(self):
            self._stop_event = threading.Event()
            self._RE_CLEAN_PATTERNS = []
            self._tool_executor = lambda n, **kw: {'success': True, 'result': 'ok_val'}
            self.turn = 0

        def is_reasoning_model(self, model): return False

        def chat_stream(self, **kwargs):
            self.turn += 1
            for m in kwargs['messages']:
                if m.get('role') == 'tool':
                    seen_tool_contents.append(m['content'])
            if self.turn == 1:
                yield {'type': 'tool_call', 'tool_call': {
                    'id': 't1', 'type': 'function', 'function': {'name': 'list_children', 'arguments': '{}'},
                }}
            else:
                yield {'type': 'content', 'content': 'finished'}
            yield {'type': 'done', 'usage': {}}

    a = _Agent()
    a.agent_loop_stream(
        messages=[{'role': 'system', 'content': 's'}, {'role': 'user', 'content': 'u'}],
        model='gpt-6.2-sol', provider='codex',
        enable_thinking=True, think_tags=False, tools_override=[],
    )
    assert seen_tool_contents == ['ok_val']


# ── 4. UltraOptimizer preserves VEX // comments & slimmed system prompt ─────

def test_ultra_optimizer_preserves_vex_comments():
    sample = "Use VEX:\n// compute point offset\n@P.y += 1.0;\n\n\n\nDone."
    out = UltraOptimizer.compress_system_prompt(sample)
    assert '// compute point offset' in out
    assert '\n\n\n' not in out


def test_canonical_system_prompt_is_slim_and_complete():
    p_think = build_system_prompt(with_thinking=True, language='zh')
    p_no = build_system_prompt(with_thinking=False, language='en')
    assert AIClientContextMixin._estimate_text_tokens(p_think) < 1500
    assert '<think>' in p_think and '<think>' not in p_no
    for must_have in ('/obj/', '0=Detail', 'verify_and_summarize', 'layout_nodes', 'create_network_box', 'labs::'):
        assert must_have in p_think, must_have


# ── 5. Modularized Controller smoke test ────────────────────────────────────

def test_modularized_controller_exposes_all_mixin_slots_and_local_dispatch():
    pytest.importorskip('PySide6')
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import QCoreApplication
    _app = QCoreApplication.instance() or QCoreApplication([])
    from houdini_agent.ui_qml.controller import Controller, ChatModel

    ctl = Controller(ChatModel(), use_backend=False)
    mo = ctl.metaObject()
    slots = {mo.method(i).name().data().decode() for i in range(mo.methodCount())}
    for name in (
        'send', 'tokenStats', 'refreshModels', 'subscriptionLogin',
        'confirmPlan', 'rulesItems', 'scanHoudiniInstances',
        'toggleLibrary', 'startUpdate',
    ):
        assert name in slots, name

    # Verify declarative UI-local tool dispatch works
    res = ctl._dispatch_ui_local_tool('add_todo', {'todo_id': '1', 'text': 'step 1', 'status': 'pending'})
    assert res and res['success'] is True
    assert ctl._dispatch_ui_local_tool('create_node', {}) is None


# ── 6. Multi-instance Bridge write-target lock (A.hip -> B.hip protection) ──

def test_bridge_write_target_blocks_write_after_auto_heal_to_different_hip():
    from types import SimpleNamespace
    from houdini_agent.ui_qml.mixins.bridge_mixin import ControllerBridgeMixin

    class _FakeBridge:
        def __init__(self, target):
            self.target = target
        @property
        def port(self):
            return self.target['port'] if self.target else 45172
        def scene_context(self):
            return dict(self.target) if self.target else None
        def ping(self):
            return {'status': 'ok', **self.target} if self.target else None

    target_a = {'port': 45172, 'pid': 1001, 'houdini': '21.0.500', 'hip_path': 'C:/proj/A.hip', 'hip_name': 'A.hip', 'path': '/obj'}
    target_b = {'port': 45173, 'pid': 2002, 'houdini': '21.0.500', 'hip_path': 'C:/proj/B.hip', 'hip_name': 'B.hip', 'path': '/obj'}

    class _Host(ControllerBridgeMixin):
        def __init__(self):
            self.bridge = _FakeBridge(target_a)
            self._session = SimpleNamespace(bridge=self.bridge)
            self._turn_bridge_target = None
            self.toasts = []
            self.toast = SimpleNamespace(emit=lambda msg: self.toasts.append(msg))
            self.healed = 0
        def _heal_bridge(self):
            self.healed += 1
            self.bridge.target = target_b
            return True

    h = _Host()
    # 1. First call on A.hip locks the turn to A.hip
    ok, err = h._check_bridge_write_target('get_network_structure')
    assert ok is True and err is None
    assert h._turn_bridge_target['hip_name'] == 'A.hip'

    # 2. Write on same A.hip succeeds
    ok, err = h._check_bridge_write_target('create_node')
    assert ok is True and err is None

    # 3. User closes Houdini A; auto-heal reconnects to Houdini B (B.hip)
    h.bridge.target = None
    ok, err = h._check_bridge_write_target('delete_node')
    assert h.healed == 1
    assert ok is False
    assert err['success'] is False
    assert 'A.hip' in err['error'] and 'B.hip' in err['error']
    assert '写入保护已暂停' in err['error']

    # 4. Read-only inspection on B.hip is still allowed without moving the write lock
    ok, err = h._check_bridge_write_target('get_network_structure')
    assert ok is True and err is None
    assert h._turn_bridge_target['hip_name'] == 'A.hip'

    # 5. Another write still blocked until explicit switch
    ok, err = h._check_bridge_write_target('set_node_parameter')
    assert ok is False

    # 6. Same PID opening a different .hip file (C.hip) also blocks pending writes!
    h.bridge.target = {'port': 45172, 'pid': 1001, 'houdini': '21.0.500', 'hip_path': 'C:/proj/C.hip', 'hip_name': 'C.hip', 'path': '/obj'}
    ok, err = h._check_bridge_write_target('execute_python')
    assert ok is False and 'C.hip' in err['error']

