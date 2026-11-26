# -*- coding: utf-8 -*-
"""Token 统计与上下文压缩全链路回归测试。"""
import copy
import threading

import pytest

from houdini_agent.utils.ai_client_providers import AIClientProvidersMixin
from houdini_agent.utils.ai_client_context import AIClientContextMixin
from houdini_agent.utils.ai_client_agent import AIClientAgentMixin
from houdini_agent.utils.token_optimizer import (
    TokenOptimizer,
    CompressionStrategy,
    calculate_cost,
    calculate_cost_from_stats,
    _match_pricing,
    _DEFAULT_PRICING,
)


class _DummyContextClient(AIClientContextMixin):
    _QUERY_TOOLS = frozenset({
        'get_network_structure', 'get_node_parameters', 'list_children',
        'check_errors', 'get_node_inputs', 'read_selection',
    })
    _OP_TOOLS = frozenset({'create_node', 'set_node_parameter'})

    def __init__(self, summary_reply=None):
        self.summary_calls = []
        self._summary_reply = summary_reply

    def _get_api_key(self, provider):
        return ''

    def chat_stream(self, **kwargs):
        self.summary_calls.append((kwargs.get('provider'), kwargs.get('model')))
        if self._summary_reply:
            yield {'type': 'content', 'content': self._summary_reply}
            yield {'type': 'done', 'usage': {}}
        else:
            yield {'type': 'error', 'error': 'offline'}


# ── 1. Usage parsing & cost calculation ─────────────────────────────────────

def test_parse_usage_anthropic_native_includes_cache_in_prompt_tokens():
    raw = {
        'input_tokens': 500,
        'cache_read_input_tokens': 80000,
        'cache_creation_input_tokens': 19500,
        'output_tokens': 1200,
    }
    parsed = AIClientProvidersMixin._parse_usage(raw)
    assert parsed['prompt_tokens'] == 100000
    assert parsed['cache_hit_tokens'] == 80000
    assert parsed['cache_write_tokens'] == 19500
    assert parsed['cache_miss_tokens'] == 500
    assert parsed['completion_tokens'] == 1200
    assert parsed['total_tokens'] == 101200
    assert abs(parsed['cache_hit_rate'] - 0.8) < 1e-6


def test_parse_usage_openai_cached_tokens_computes_miss_and_rate():
    raw = {
        'prompt_tokens': 10000,
        'completion_tokens': 500,
        'total_tokens': 10500,
        'prompt_tokens_details': {'cached_tokens': 2500},
        'completion_tokens_details': {'reasoning_tokens': 200},
    }
    parsed = AIClientProvidersMixin._parse_usage(raw)
    assert parsed['prompt_tokens'] == 10000
    assert parsed['cache_hit_tokens'] == 2500
    assert parsed['cache_write_tokens'] == 0
    assert parsed['cache_miss_tokens'] == 7500
    assert parsed['reasoning_tokens'] == 200
    assert abs(parsed['cache_hit_rate'] - 0.25) < 1e-6


def test_parse_usage_deepseek_keeps_cache_write_zero():
    raw = {
        'prompt_tokens': 10000,
        'completion_tokens': 300,
        'prompt_cache_hit_tokens': 6000,
        'prompt_cache_miss_tokens': 4000,
    }
    parsed = AIClientProvidersMixin._parse_usage(raw)
    assert parsed['cache_hit_tokens'] == 6000
    assert parsed['cache_write_tokens'] == 0
    assert parsed['cache_miss_tokens'] == 4000
    assert abs(parsed['cache_hit_rate'] - 0.6) < 1e-6


def test_calculate_cost_does_not_undercharge_when_cache_miss_omitted():
    # OpenAI / CPA style: 1M prompt tokens, 200k cached, cache_miss=0
    # Uncached 800k must still be billed at full input rate ($2.00/M), cached 200k at $0.10/M
    cost = calculate_cost(
        model='gpt-6.2-sol',
        input_tokens=1_000_000,
        output_tokens=0,
        cache_hit=200_000,
        cache_miss=0,
    )
    expected = (200_000 * 0.10 + 800_000 * 2.00) / 1_000_000
    assert abs(cost - expected) < 1e-6


def test_calculate_cost_bills_anthropic_cache_write_at_creation_rate():
    # claude-fable-5-2: input=8, cache_read=0.8, cache_write=10
    cost = calculate_cost(
        model='claude-fable-5-2',
        input_tokens=1_000_000,
        output_tokens=0,
        cache_hit=500_000,
        cache_write=300_000,
        cache_miss=200_000,
    )
    expected = (500_000 * 0.80 + 300_000 * 10.00 + 200_000 * 8.00) / 1_000_000
    assert abs(cost - expected) < 1e-6

    stats_cost = calculate_cost_from_stats('claude-fable-5-2', {
        'input': 1_000_000, 'output': 0,
        'cache_read': 500_000, 'cache_write': 300_000, 'cache_miss': 200_000,
    })
    assert abs(stats_cost - expected) < 1e-6


def test_newest_models_match_pricing():
    for model in (
        'claude-fable-5-2',
        'gpt-6.2-sol',
        'gpt-6.2-astra',
        'gpt-6.1-terra',
        'gemini-4-neon-xhigh',
        'gemini-4-hydrogen-xhigh',
        'glm-5.3-flashx',
        'kiro-auto',
    ):
        assert _match_pricing(model) is not _DEFAULT_PRICING, model


# ── 2. Token estimation accuracy ────────────────────────────────────────────

def test_estimate_messages_tokens_handles_cjk_and_chinese_tool_schema():
    c = _DummyContextClient()
    cn_text = '在胡迪尼里创建一个程序化地形节点网络并添加噪声与侵蚀效果' * 200  # ~5400 Chinese chars
    est_cn = c._estimate_messages_tokens([{'role': 'user', 'content': cn_text}])
    # ~0.75 tokens/char -> ~4050 tokens (old // 3 gave only ~1800)
    assert est_cn > 3500

    tool = [{
        'type': 'function',
        'function': {
            'name': 'create_node',
            'description': '在指定网络路径中创建节点',
            'parameters': {
                'type': 'object',
                'properties': {'path': {'type': 'string', 'description': '父网络路径'}},
            },
        },
    }]
    est_tool = c._estimate_messages_tokens([], tool)
    assert 30 < est_tool < 150


# ── 3. Compaction threshold & Claude Agent SDK-style compaction ─────────────

def test_compaction_threshold_reserves_buffer_on_large_windows():
    c = _DummyContextClient()
    # 128k -> 85% is 108.8k, 128k - 16k is 112k -> min is 108.8k
    assert c._compaction_threshold(128000) == int(128000 * 0.85)
    # 200k -> 85% is 170k, 200k - 16k is 184k -> min is 170k
    assert c._compaction_threshold(200000) == int(200000 * 0.85)
    # 40k -> 85% is 34k, 40k - 16k is 24k -> reserves 16k buffer
    assert c._compaction_threshold(40000) == 24000


def test_smart_compress_preserves_prefix_when_below_target():
    c = _DummyContextClient()
    msgs = [
        {'role': 'system', 'content': 'sys'},
        {'role': 'user', 'content': 'hi'},
        {'role': 'assistant', 'content': '', 'tool_calls': [
            {'id': 'c1', 'type': 'function', 'function': {'name': 'list_children', 'arguments': '{"path": "/obj"}'}},
        ]},
        {'role': 'tool', 'tool_call_id': 'c1', 'content': 'old_result'},
        {'role': 'assistant', 'content': '', 'tool_calls': [
            {'id': 'c2', 'type': 'function', 'function': {'name': 'list_children', 'arguments': '{"path": "/obj"}'}},
        ]},
        {'role': 'tool', 'tool_call_id': 'c2', 'content': 'new_result'},
    ]
    before = copy.deepcopy(msgs)
    out = c._smart_compress_in_loop(msgs, [], context_limit=100000)
    assert out is msgs
    assert msgs == before  # zero mutation when below compaction threshold (100% Prompt Cache)


def test_smart_compress_shrinks_single_long_round_without_breaking_pairs():
    c = _DummyContextClient()  # offline -> exercises clean atomic-step fallback
    msgs = [
        {'role': 'system', 'content': 'base sys'},
        {'role': 'system', 'content': 'rag context'},
        {'role': 'user', 'content': 'build complex procedural city'},
    ]
    for i in range(25):
        tc_id = f'call_{i}'
        msgs.append({
            'role': 'assistant', 'content': f'step {i}',
            'tool_calls': [{'id': tc_id, 'type': 'function',
                            'function': {'name': 'execute_python', 'arguments': '{"code": "x=1"}'}}],
        })
        msgs.append({
            'role': 'tool', 'tool_call_id': tc_id,
            'content': f'output {i} ' + ('line detail text ' * 80),
        })

    out = c._smart_compress_in_loop(msgs, [], context_limit=4000, model='gpt-6.2-sol', provider='codex')
    assert c._estimate_messages_tokens(out) < c._estimate_messages_tokens(msgs)
    # Leading system messages preserved; NO mid/trailing system messages!
    assert out[0]['role'] == 'system' and out[1]['role'] == 'system'
    for m in out[2:]:
        assert m['role'] != 'system'
    # Every tool message has a preceding matching assistant tool_call, and vice versa
    tc_ids_seen = set()
    tool_ids_seen = set()
    for m in out:
        if m['role'] == 'assistant' and m.get('tool_calls'):
            for tc in m['tool_calls']:
                tc_ids_seen.add(tc['id'])
        elif m['role'] == 'tool':
            assert m['tool_call_id'] in tc_ids_seen
            tool_ids_seen.add(m['tool_call_id'])
    assert tc_ids_seen == tool_ids_seen


def test_smart_compress_passes_active_model_and_provider_to_llm_summary():
    c = _DummyContextClient(summary_reply='# Task Overview\nBuild terrain\n# Current State\nCreated /obj/geo1')
    msgs = [{'role': 'system', 'content': 'sys'}]
    for i in range(6):
        msgs.append({'role': 'user', 'content': f'request {i} ' + ('中文需求说明' * 120)})
        msgs.append({'role': 'assistant', 'content': f'answer {i} ' + ('中文详细回复' * 120)})

    out = c._smart_compress_in_loop(msgs, [], context_limit=2000, model='gpt-6.2-sol', provider='codex')
    assert ('codex', 'gpt-6.2-sol') in c.summary_calls
    assert any('[Compacted Context Summary]' in str(m.get('content', '')) for m in out)
    assert any('/obj/geo1' in str(m.get('content', '')) for m in out)
    # Summary is NOT role='system'
    for m in out[1:]:
        assert m['role'] != 'system'


def test_smart_compress_triggers_from_actual_api_token_count():
    c = _DummyContextClient(summary_reply='# Task Overview\nCompact checkpoint')
    msgs = [
        {'role': 'system', 'content': 'sys'},
        {'role': 'user', 'content': 'turn 1'},
        {'role': 'assistant', 'content': 'reply 1'},
        {'role': 'user', 'content': 'turn 2'},
        {'role': 'assistant', 'content': 'reply 2'},
        {'role': 'user', 'content': 'turn 3'},
        {'role': 'assistant', 'content': 'reply 3'},
    ]
    # Local estimate is tiny, but real API usage reports 110,000 >= threshold(128000)
    out = c._smart_compress_in_loop(
        msgs, [], context_limit=128000, model='claude-fable-5-2', provider='anthropic',
        current_tokens=110000,
    )
    assert ('anthropic', 'claude-fable-5-2') in c.summary_calls
    assert any('[Compacted Context Summary]' in str(m.get('content', '')) for m in out)


def test_progressive_trim_has_no_trailing_system_and_keeps_valid_pairs():
    c = _DummyContextClient()
    msgs = [{'role': 'system', 'content': 'sys'}]
    for i in range(6):
        msgs.append({'role': 'user', 'content': f'u{i}'})
        msgs.append({
            'role': 'assistant', 'content': '',
            'tool_calls': [{'id': f'tc_{i}', 'type': 'function',
                            'function': {'name': 'execute_python', 'arguments': '{}'}}],
        })
        msgs.append({'role': 'tool', 'tool_call_id': f'tc_{i}', 'content': 'x' * 500})
        msgs.append({'role': 'assistant', 'content': f'done {i}'})

    out = c._progressive_trim(msgs, [], trim_level=3, supports_vision=False)
    assert out[0]['role'] == 'system'
    assert all(m['role'] != 'system' for m in out[1:])
    assert out[-1]['role'] != 'system'


# ── 5. Manual compress_history & TokenOptimizer safety ──────────────────────

def test_manual_compress_history_handles_short_but_heavy_history():
    c = _DummyContextClient(summary_reply='# Task Overview\nOptimize node\n# Current State\nCreated /obj/geo1')
    history = [
        {'role': 'user', 'content': [
            {'type': 'text', 'text': '看下这张参考图'},
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,AAAA'}},
        ]},
        {'role': 'assistant', 'content': '', 'tool_calls': [
            {'id': 't1', 'type': 'function', 'function': {'name': 'execute_python', 'arguments': '{}'}},
        ]},
        {'role': 'tool', 'tool_call_id': 't1', 'content': 'verbose output\n' * 300},
        {'role': 'assistant', 'content': '创建完成'},
        {'role': 'user', 'content': '继续优化'},
        {'role': 'assistant', 'content': '好的'},
    ]
    new_hist, stats = c.compress_history(
        history, context_limit=128000, model='gpt-6.2-sol', provider='codex', use_llm=True
    )
    assert stats['compressed'] is True
    assert stats['used_llm'] is True
    assert stats['after_tokens'] < stats['before_tokens']
    assert all(m['role'] != 'system' for m in new_hist)
    assert any('[Compacted Context Summary]' in str(m.get('content', '')) for m in new_hist)


def test_token_optimizer_compress_messages_handles_multimodal_and_tools():
    opt = TokenOptimizer()
    msgs = [
        {'role': 'user', 'content': [{'type': 'text', 'text': 'hello'}, {'type': 'image_url', 'image_url': {'url': 'data:x'}}]},
        {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'a1', 'type': 'function', 'function': {'name': 'f', 'arguments': '{}'}}]},
        {'role': 'tool', 'tool_call_id': 'a1', 'name': 'f', 'content': 'ok'},
        {'role': 'assistant', 'content': 'done 1'},
        {'role': 'user', 'content': 'next'},
        {'role': 'assistant', 'content': 'done 2'},
    ]
    out, stats = opt.compress_messages(msgs, strategy=CompressionStrategy.BALANCED, keep_recent=2)
    assert stats['compressed'] == 4
    assert stats['kept'] == 2
    assert len(out) <= len(msgs)
    assert all(m['role'] != 'system' for m in out)


# ── 6. Agent loop iteration-1 proactive compress + history write-back ───────

class _CompressingLoopAgent(AIClientContextMixin, AIClientAgentMixin):
    _QUERY_TOOLS = frozenset({'list_children'})
    _OP_TOOLS = frozenset({'create_node'})

    def __init__(self):
        self._stop_event = threading.Event()
        self._RE_CLEAN_PATTERNS = []
        self._tool_executor = lambda name, **kw: {'success': True, 'result': 'ok'}
        self.seen_msg_counts = []

    def is_reasoning_model(self, model):
        return False

    def chat_stream(self, **kwargs):
        self.seen_msg_counts.append(len(kwargs['messages']))
        yield {'type': 'content', 'content': '收到，已处理完成。'}
        yield {'type': 'done', 'usage': {'prompt_tokens': 1000, 'completion_tokens': 50, 'total_tokens': 1050}}


def test_agent_loop_proactively_compresses_on_iteration_1_and_writes_back():
    from houdini_agent.ui_qml.agent_session import AgentSession

    agent = _CompressingLoopAgent()
    messages = [{'role': 'system', 'content': 'sys'}]
    for i in range(8):
        messages.append({'role': 'user', 'content': f'old user {i} ' + ('中文内容' * 150)})
        messages.append({'role': 'assistant', 'content': f'old assistant {i} ' + ('中文回复' * 150)})
    messages.append({'role': 'user', 'content': 'latest question'})

    res = agent.agent_loop_stream(
        messages=messages,
        model='gpt-6.2-sol',
        provider='codex',
        context_limit=2000,
        tools_override=[],
        enable_thinking=False,
    )
    assert res['ok'] is True
    # Triggered BEFORE first API call (iteration == 1)!
    assert agent.seen_msg_counts[0] < len(messages)
    assert isinstance(res.get('compressed_history'), list)
    assert len(res['compressed_history']) < len(messages) - 1

    # Verify AgentSession._absorb replaces self.history with compressed_history
    sess = object.__new__(AgentSession)
    sess.history = copy.deepcopy(messages[1:])
    sess._absorb(res)
    assert len(sess.history) == len(res['compressed_history']) + 1
    assert sess.history[-1] == {'role': 'assistant', 'content': '收到，已处理完成。'}


# ── 7. Controller tokenStats cache hit rate & total fallback ────────────────

def test_controller_token_stats_hit_rate_and_no_reasoning_double_count():
    pytest.importorskip('PySide6')
    from types import SimpleNamespace
    from houdini_agent.ui_qml.controller import Controller

    fake = SimpleNamespace(
        _token_stats={
            'input': 10000,
            'output': 500,
            'reasoning': 300,
            'cache_read': 2500,
            'cache_write': 0,
            'cache_miss': 7500,
            'total': 0,  # force fallback
            'requests': 2,
        },
        _model_name='gpt-6.2-sol',
        _ctx_text='',
        _estimate_ctx=lambda: 1000,
        _context_limit=lambda: 128000,
        _fmt_k=Controller._fmt_k,
    )
    st = Controller.tokenStats(fake)
    assert st['total'] == 10500  # NOT 10800 (reasoning is subset of output)
    assert abs(st['cache_hit_rate'] - 0.25) < 1e-6  # NOT 1.0!


# ── 8. Claude Agent SDK alignment (Items 1-4) ───────────────────────────────

def test_ephemeral_rag_appends_to_last_user_preserving_cache_prefix():
    from houdini_agent.ui_qml.agent_session import AgentSession

    msgs = [
        {'role': 'system', 'content': 'base sys'},
        {'role': 'user', 'content': 'turn 1'},
        {'role': 'assistant', 'content': 'ans 1'},
        {'role': 'user', 'content': 'turn 2'},
    ]
    prefix_before = copy.deepcopy(msgs[:3])
    AgentSession._append_ephemeral_context(msgs, [{'role': 'system', 'content': 'doc snippet'}])
    # Prefix [sys, turn 1, ans 1] is 100% byte-identical!
    assert msgs[:3] == prefix_before
    assert '<retrieved_context>' in msgs[-1]['content']
    assert 'doc snippet' in msgs[-1]['content']

    # Persisted history strips ephemeral <retrieved_context> cleanly
    agent = _CompressingLoopAgent()
    persisted = agent._extract_persisted_history(msgs)
    assert '<retrieved_context>' not in persisted[-1]['content']
    assert persisted[-1]['content'] == 'turn 2'


def test_anthropic_cache_control_marks_system_tools_and_messages():
    from houdini_agent.utils.ai_client_streaming import AIClientStreamingMixin

    payload = {
        'system': 'You are a Houdini TA.',
        'tools': [{'name': 't1', 'input_schema': {}}, {'name': 't2', 'input_schema': {}}],
        'messages': [
            {'role': 'user', 'content': 'u1'},
            {'role': 'assistant', 'content': [
                {'type': 'thinking', 'thinking': 'hmm'},
                {'type': 'text', 'text': 'a1'},
            ]},
            {'role': 'user', 'content': 'u2'},
            {'role': 'assistant', 'content': [
                {'type': 'text', 'text': 'a2'},
                {'type': 'thinking', 'thinking': 'trailing think'},
            ]},
        ],
    }
    AIClientStreamingMixin._apply_anthropic_cache_control(payload)
    assert payload['system'] == [{
        'type': 'text', 'text': 'You are a Houdini TA.',
        'cache_control': {'type': 'ephemeral'},
    }]
    assert 'cache_control' not in payload['tools'][0]
    assert payload['tools'][1]['cache_control'] == {'type': 'ephemeral'}
    # messages[-1]: skipped trailing thinking block, marked text block 'a2'
    assert payload['messages'][-1]['content'][0]['cache_control'] == {'type': 'ephemeral'}
    assert 'cache_control' not in payload['messages'][-1]['content'][1]
    # messages[-3] (index 1): marked text block 'a1', not thinking
    assert payload['messages'][1]['content'][1]['cache_control'] == {'type': 'ephemeral'}
    assert 'cache_control' not in payload['messages'][1]['content'][0]


def test_compaction_preserves_state_accounts_usage_notifies_and_guards_loop():
    class _UsageLoopAgent(AIClientContextMixin, AIClientAgentMixin):
        _QUERY_TOOLS = frozenset({'list_children'})
        _OP_TOOLS = frozenset({'create_node'})

        def __init__(self):
            self._stop_event = threading.Event()
            self._RE_CLEAN_PATTERNS = []
            self._tool_executor = lambda name, **kw: {'success': True, 'result': 'ok'}
            self.calls = 0

        def is_reasoning_model(self, model):
            return False

        def chat_stream(self, **kwargs):
            self.calls += 1
            # First call is compaction LLM summary (prompt contains 'Conversation to compact:')
            if 'Conversation to compact:' in str(kwargs['messages'][0].get('content', '')):
                yield {'type': 'content', 'content': '# Task Overview\nTerrain done'}
                yield {'type': 'done', 'usage': {
                    'prompt_tokens': 500, 'completion_tokens': 80,
                    'total_tokens': 580, 'cache_hit_tokens': 0,
                    'cache_write_tokens': 0, 'cache_miss_tokens': 500,
                }}
                return
            # Second call is actual agent reply
            yield {'type': 'content', 'content': '继续完成步骤 2。'}
            yield {'type': 'done', 'usage': {
                'prompt_tokens': 1200, 'completion_tokens': 60,
                'total_tokens': 1260, 'cache_hit_tokens': 0,
                'cache_write_tokens': 0, 'cache_miss_tokens': 1200,
            }}

    agent = _UsageLoopAgent()
    events = []
    messages = [
        {'role': 'system', 'content': 'sys'},
        {'role': 'user', 'content': 'u1'},
        {'role': 'assistant', 'content': 'a1'},
        {'role': 'user', 'content': 'u2'},
        {'role': 'assistant', 'content': 'a2'},
        {'role': 'user', 'content': 'u3'},
        {'role': 'assistant', 'content': 'a3'},
        {'role': 'user', 'content': 'u4'},
    ]
    res = agent.agent_loop_stream(
        messages=messages,
        model='claude-fable-5-2',
        provider='anthropic',
        context_limit=128000,
        tools_override=[],
        enable_thinking=False,
        initial_actual_tokens=115000,  # cross-turn real token anchor triggers compaction on iter 1!
        state_context='Plan: Build Terrain\n  1. [done] Base heightfield\n  2. [pending] Erosion',
        on_compaction=lambda m: events.append(m),
    )
    assert res['ok'] is True
    assert len(events) == 1
    assert events[0]['used_llm'] is True
    # Compaction's own 500+80 tokens accounted in total_usage & call_records!
    assert res['usage']['prompt_tokens'] == 500 + 1200
    assert res['usage']['completion_tokens'] == 80 + 60
    assert any(r.get('is_compaction') for r in res['call_records'])
    # Active Plan state preserved inside compacted history!
    joined = '\n'.join(str(m.get('content', '')) for m in res['compressed_history'])
    assert '# Active Plan / Todo State' in joined
    assert 'Erosion' in joined

