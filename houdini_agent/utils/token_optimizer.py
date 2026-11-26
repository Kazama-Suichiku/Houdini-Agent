# -*- coding: utf-8 -*-
"""
Token 优化管理器
系统化减少 token 消耗的多种策略

对齐 Cursor 的 token 统计：
- tiktoken 精准计数（可用时），否则改良估算
- 每模型定价（USD / 1M tokens）
- 费用计算 calculate_cost()
"""

import json
import re
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

# ============================================================
# tiktoken 精准计数（可选依赖）
# ============================================================
_tiktoken = None
_encoding_cache: Dict[str, Any] = {}

def _get_encoding(model: str):
    """获取 tiktoken 编码器（带缓存）"""
    global _tiktoken, _encoding_cache
    if _tiktoken is None:
        try:
            import tiktoken as _tk  # type: ignore
            _tiktoken = _tk
        except ImportError:
            _tiktoken = False
    if _tiktoken is False:
        return None
    # 模型名 → 编码映射
    try:
        key = model or 'gpt-5.2'
        if key not in _encoding_cache:
            try:
                _encoding_cache[key] = _tiktoken.encoding_for_model(key)
            except KeyError:
                # 未知模型回退 cl100k_base（GPT-4 / Claude 通用）
                if 'cl100k' not in _encoding_cache:
                    _encoding_cache['cl100k'] = _tiktoken.get_encoding('cl100k_base')
                _encoding_cache[key] = _encoding_cache['cl100k']
        return _encoding_cache[key]
    except Exception:
        return None


def count_tokens(text: str, model: str = '') -> int:
    """精准计算 token 数量
    
    优先使用 tiktoken（如果可用），否则使用改良估算。
    """
    if not text:
        return 0
    enc = _get_encoding(model)
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    # ---- 改良启发式估算 ----
    # JSON / 代码块中有大量 { } " , : 等占 1 token 的符号
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]', text))
    # 代码 / JSON 特征字符（单字符就是一个 token）
    code_chars = len(re.findall(r'[{}\[\]:,;()=<>+\-*/|&^~!@#$%]', text))
    other_chars = len(text) - chinese_chars - code_chars
    tokens = chinese_chars / 1.5 + code_chars + other_chars / 3.8
    return max(1, int(tokens))


# ============================================================
# 每模型定价（USD / 1M tokens）—— 对齐 Cursor
# ============================================================

# 格式: {model_pattern: {input, input_cache, output, reasoning(可选)}}
# input_cache: 缓存命中时的输入价格
# reasoning: 推理 token 的输出价格（若无则用 output）
MODEL_PRICING: Dict[str, Dict[str, float]] = {
    # ---- DeepSeek（官方标准时段价） ----
    'deepseek-v4-flash-vision-exp': {'input': 0.44, 'input_cache': 0.014, 'output': 1.32, 'reasoning': 1.32},
    'deepseek-v4.1-flash':  {'input': 0.30,  'input_cache': 0.006, 'output': 1.20, 'reasoning': 1.20},
    'deepseek-v4-flash':    {'input': 0.44,  'input_cache': 0.014, 'output': 1.32, 'reasoning': 1.32},
    'deepseek-v4-pro':      {'input': 1.32,  'input_cache': 0.044, 'output': 3.96, 'reasoning': 3.96},
    'deepseek-chat':        {'input': 0.27,  'input_cache': 0.07,  'output': 1.10},
    'deepseek-reasoner':    {'input': 0.55,  'input_cache': 0.14,  'output': 2.19, 'reasoning': 2.19},
    # ---- OpenAI ----
    'gpt-6.2-sol':          {'input': 2.00,  'input_cache': 0.10,  'output': 10.00, 'reasoning': 10.00},
    'gpt-6.2-astra':        {'input': 10.00, 'input_cache': 1.00,  'output': 50.00, 'reasoning': 50.00},
    'gpt-6-astra':          {'input': 10.00, 'input_cache': 1.00,  'output': 50.00, 'reasoning': 50.00},
    'gpt-6.1-sol':          {'input': 2.00,  'input_cache': 0.10,  'output': 10.00, 'reasoning': 10.00},
    'gpt-6.1-terra':        {'input': 2.00,  'input_cache': 0.20,  'output': 12.00, 'reasoning': 12.00},
    'gpt-6-sol':            {'input': 2.00,  'input_cache': 0.20,  'output': 10.00, 'reasoning': 10.00},
    'gpt-6-luna':           {'input': 0.10,  'input_cache': 0.01,  'output': 0.50,  'reasoning': 0.50},
    'gpt-5.6-sol-pro':      {'input': 2.00,  'input_cache': 0.20,  'output': 10.00, 'reasoning': 10.00},
    'gpt-5.6-sol':          {'input': 4.00,  'input_cache': 0.40,  'output': 20.00, 'reasoning': 20.00},
    'gpt-5.6-terra':        {'input': 2.00,  'input_cache': 0.20,  'output': 12.00, 'reasoning': 12.00},
    'gpt-5.6-luna':         {'input': 0.20,  'input_cache': 0.02,  'output': 1.20,  'reasoning': 1.20},
    'gpt-5.5':              {'input': 5.00,  'input_cache': 0.50,  'output': 30.00, 'reasoning': 30.00},
    'gpt-5.4':              {'input': 2.50,  'input_cache': 0.25,  'output': 15.00, 'reasoning': 15.00},
    'gpt-5.2':              {'input': 2.50,  'input_cache': 1.25,  'output': 10.00},
    'gpt-5.3-codex':        {'input': 3.00,  'input_cache': 1.50,  'output': 12.00},
    'o3':                   {'input': 10.00, 'input_cache': 2.50,  'output': 40.00, 'reasoning': 40.00},
    'o3-mini':              {'input': 1.10,  'input_cache': 0.55,  'output': 4.40,  'reasoning': 4.40},
    'o4-mini':              {'input': 1.10,  'input_cache': 0.275, 'output': 4.40,  'reasoning': 4.40},
    # ---- Claude (Duojie / OpenRouter / 订阅；OpenRouter 的 4.8 写法为 claude-opus-4.8) ----
    'claude-fable-5-2':     {'input': 8.00,  'input_cache': 0.80,  'input_cache_write': 10.00, 'output': 40.00, 'reasoning': 40.00},
    'claude-fable-5-1':     {'input': 10.00, 'input_cache': 1.00,  'input_cache_write': 12.50, 'output': 50.00, 'reasoning': 50.00},
    'claude-fable-5.1':     {'input': 10.00, 'input_cache': 1.00,  'input_cache_write': 12.50, 'output': 50.00, 'reasoning': 50.00},
    'claude-fable-5':       {'input': 10.00, 'input_cache': 1.00,  'input_cache_write': 12.50, 'output': 50.00, 'reasoning': 50.00},
    'claude-opus-5-5':      {'input': 4.00,  'input_cache': 0.20,  'input_cache_write': 5.00,  'output': 20.00, 'reasoning': 20.00},
    'claude-sonnet-5-5':    {'input': 2.00,  'input_cache': 0.10,  'input_cache_write': 2.50,  'output': 10.00, 'reasoning': 10.00},
    'claude-haiku-5-5':     {'input': 0.10,  'input_cache': 0.01,  'input_cache_write': 0.125, 'output': 0.50},
    'claude-opus-5':        {'input': 5.00,  'input_cache': 0.50,  'input_cache_write': 6.25,  'output': 25.00, 'reasoning': 25.00},
    'claude-sonnet-5':      {'input': 2.00,  'input_cache': 0.20,  'input_cache_write': 2.50,  'output': 10.00, 'reasoning': 10.00},
    'claude-opus-4-8':      {'input': 5.00,  'input_cache': 0.50,  'input_cache_write': 6.25,  'output': 25.00, 'reasoning': 25.00},
    'claude-opus-4.8':      {'input': 5.00,  'input_cache': 0.50,  'input_cache_write': 6.25,  'output': 25.00, 'reasoning': 25.00},
    'claude-opus-4-7':      {'input': 5.00,  'input_cache': 0.50,  'input_cache_write': 6.25,  'output': 25.00, 'reasoning': 25.00},
    'claude-opus-4-6':      {'input': 5.00,  'input_cache': 0.50,  'input_cache_write': 6.25,  'output': 25.00, 'reasoning': 25.00},
    'kiro-auto':            {'input': 2.00,  'input_cache': 0.20,  'input_cache_write': 2.50,  'output': 10.00, 'reasoning': 10.00},
    'claude-opus-4-5':      {'input': 15.00, 'input_cache': 1.50,  'input_cache_write': 18.75, 'output': 75.00, 'reasoning': 75.00},
    'claude-opus-4-5-kiro': {'input': 15.00, 'input_cache': 1.50,  'input_cache_write': 18.75, 'output': 75.00, 'reasoning': 75.00},
    'claude-opus-4-5-max':  {'input': 15.00, 'input_cache': 1.50,  'input_cache_write': 18.75, 'output': 75.00, 'reasoning': 75.00},
    'claude-opus-4-6-normal': {'input': 15.00, 'input_cache': 1.50, 'input_cache_write': 18.75, 'output': 75.00, 'reasoning': 75.00},
    'claude-opus-4-6-kiro': {'input': 15.00, 'input_cache': 1.50,  'input_cache_write': 18.75, 'output': 75.00, 'reasoning': 75.00},
    'claude-opus-4-6-gemini': {'input': 15.00, 'input_cache': 1.50, 'input_cache_write': 18.75, 'output': 75.00, 'reasoning': 75.00},
    'claude-opus-4-6-max': {'input': 15.00, 'input_cache': 1.50, 'input_cache_write': 18.75, 'output': 75.00, 'reasoning': 75.00},
    'claude-sonnet-4-5':    {'input': 3.00,  'input_cache': 0.30,  'input_cache_write': 3.75,  'output': 15.00, 'reasoning': 15.00},
    'claude-sonnet-4-6':    {'input': 3.00,  'input_cache': 0.30,  'input_cache_write': 3.75,  'output': 15.00, 'reasoning': 15.00},
    'claude-haiku-4-5':     {'input': 0.80,  'input_cache': 0.08,  'input_cache_write': 1.00,  'output': 4.00},
    # ---- Gemini ----
    'gemini-3-pro-image-preview': {'input': 1.25, 'input_cache': 0.30, 'output': 10.00},
    'gemini-3-flash':       {'input': 0.50,  'input_cache': 0.125, 'output': 3.00},
    'gemini-3.1-flash-lite': {'input': 0.15, 'input_cache': 0.04,  'output': 0.60},
    'gemini-3.5-flash-lite': {'input': 0.15, 'input_cache': 0.04,  'output': 0.60},
    'gemini-3.1-pro':       {'input': 2.00,  'input_cache': 0.20,  'output': 12.00},
    'gemini-3.8-flash':     {'input': 0.75,  'input_cache': 0.075, 'output': 3.75},
    'gemini-4-argon':       {'input': 2.00,  'input_cache': 0.10,  'output': 10.00, 'reasoning': 10.00},
    'gemini-4-carbon':      {'input': 2.00,  'input_cache': 0.10,  'output': 10.00, 'reasoning': 10.00},
    'gemini-4-hydrogen':    {'input': 2.00,  'input_cache': 0.10,  'output': 10.00, 'reasoning': 10.00},
    'gemini-4-neon':        {'input': 2.00,  'input_cache': 0.10,  'output': 10.00, 'reasoning': 10.00},
    # ---- GLM (智谱清言) ----
    'glm-4.7':              {'input': 0.50,  'input_cache': 0.50,  'output': 0.50},
    'glm-5-turbo':          {'input': 1.20,  'input_cache': 0.24,  'output': 4.00},
    'glm-5.1':              {'input': 0.97,  'input_cache': 0.18,  'output': 3.04},
    'glm-5.2':              {'input': 0.97,  'input_cache': 0.19,  'output': 3.04},
    'glm-5.3-flashx':       {'input': 0.07,  'input_cache': 0.015, 'output': 0.25},
    'glm-5.3-flash':        {'input': 0.07,  'input_cache': 0.015, 'output': 0.25},
    'glm-5.3':              {'input': 1.40,  'input_cache': 0.14,  'output': 4.40},
    # ---- Grok ----
    'grok-4.7':             {'input': 2.00,  'input_cache': 0.50,  'output': 6.00, 'reasoning': 6.00},
    'grok-4.6':             {'input': 2.00,  'input_cache': 0.50,  'output': 6.00, 'reasoning': 6.00},
    'grok-4.5':             {'input': 2.00,  'input_cache': 0.30,  'output': 6.00, 'reasoning': 6.00},
    # ---- Kimi ----
    'kimi-k3':              {'input': 0.50,  'input_cache': 0.30,  'output': 12.00},
    'kimi-k2.5':            {'input': 2.00,  'input_cache': 0.50,  'output': 8.00},
    # ---- MiniMax ----
    'minimax-m3':           {'input': 0.30,  'input_cache': 0.06,  'output': 1.20},
    'MiniMax-M2.5':         {'input': 1.00,  'input_cache': 0.25,  'output': 4.00},
    'MiniMax-M2.7':         {'input': 1.00,  'input_cache': 0.25,  'output': 4.00},
    'MiniMax-M2.7-highspeed': {'input': 1.00, 'input_cache': 0.25, 'output': 4.00},
    # ---- Qwen ----
    'qwen3.5-plus':         {'input': 0.80,  'input_cache': 0.20,  'output': 2.00},
    'qwen-plus':            {'input': 0.80,  'input_cache': 0.20,  'output': 2.00},
    'qwen-max':             {'input': 2.00,  'input_cache': 0.50,  'output': 6.00},
    'qwen-turbo':           {'input': 0.30,  'input_cache': 0.05,  'output': 0.60},
}

# 默认定价（无法匹配时使用，按 DeepSeek-chat 计价）
_DEFAULT_PRICING = {'input': 0.27, 'input_cache': 0.07, 'output': 1.10}


def _match_pricing(model: str) -> Dict[str, float]:
    """模型名 → 定价字典（支持模糊匹配）"""
    if not model:
        return _DEFAULT_PRICING
    m = model.lower().strip()
    # OpenRouter 形式 "vendor/model" → 去掉厂商前缀再匹配
    if '/' in m:
        m = m.rsplit('/', 1)[-1]
    # 精确匹配（表中 key 大小写不一，统一小写比较）
    lowered = {k.lower(): v for k, v in MODEL_PRICING.items()}
    if m in lowered:
        return lowered[m]
    # 版本号写法归一后的精确匹配（OpenRouter claude-opus-5.5 ↔ claude-opus-5-5）。
    # 必须先于前缀匹配，否则 claude-opus-5.5 会被前缀 claude-opus-5 抢走
    normalized = {k.replace('.', '-'): v for k, v in lowered.items()}
    mn = m.replace('.', '-')
    if mn in normalized:
        return normalized[mn]
    # 前缀匹配（如 claude-sonnet-4-5-xxx → claude-sonnet-4-5）
    for key in sorted(lowered.keys(), key=len, reverse=True):
        if m.startswith(key):
            return lowered[key]
    for key in sorted(normalized.keys(), key=len, reverse=True):
        if mn.startswith(key):
            return normalized[key]
    return _DEFAULT_PRICING


def calculate_cost(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_hit: int = 0,
    cache_miss: int = 0,
    reasoning_tokens: int = 0,
    cache_write: int = 0,
) -> float:
    """计算单次 API 调用费用（USD）

    Args:
        model: 模型名
        input_tokens: 总输入 token（prompt_tokens，包含缓存命中与缓存写入）
        output_tokens: 总输出 token（completion_tokens，含 reasoning）
        cache_hit: 缓存命中读取 token（按 input_cache 计价）
        cache_miss: 普通未命中输入 token（按 input 计价；缺省时由 input_tokens - cache_hit - cache_write 推算）
        reasoning_tokens: 推理 token（是 output_tokens 的子集）
        cache_write: 缓存写入 token（如 Anthropic cache_creation_input_tokens，按 input_cache_write 或 1.25× input 计价）
    """
    p = _match_pricing(model)
    M = 1_000_000.0

    inp = max(0, int(input_tokens or 0))
    chit = max(0, int(cache_hit or 0))
    cwrite = max(0, int(cache_write or 0))
    cmiss = max(0, int(cache_miss or 0))

    # 输入费用：确保 OpenAI / CPA 仅返回 cached_tokens（cache_miss=0）时，
    # 仍按正常输入价对 (input_tokens - cache_hit - cache_write) 计费，绝不漏算！
    if inp > 0:
        chit = min(chit, inp)
        cwrite = min(cwrite, max(0, inp - chit))
        uncached = max(0, inp - chit - cwrite)
    else:
        uncached = cmiss

    cache_read_price = p.get('input_cache', p['input'])
    cache_write_price = p.get('input_cache_write', p['input'] * 1.25)
    in_cost = (chit * cache_read_price + cwrite * cache_write_price + uncached * p['input']) / M

    # 输出费用
    reasoning_price = p.get('reasoning', p['output'])
    normal_out = max(0, int(output_tokens or 0) - int(reasoning_tokens or 0))
    out_cost = (normal_out * p['output'] + max(0, int(reasoning_tokens or 0)) * reasoning_price) / M

    return in_cost + out_cost


def calculate_cost_from_stats(model: str, stats: dict) -> float:
    """从聚合统计字典中计算费用"""
    return calculate_cost(
        model=model,
        input_tokens=stats.get('input_tokens', stats.get('input', 0)),
        output_tokens=stats.get('output_tokens', stats.get('output', 0)),
        cache_hit=stats.get('cache_read', stats.get('cache_hit', stats.get('cache_hit_tokens', 0))),
        cache_miss=stats.get('cache_miss', stats.get('cache_miss_tokens', 0)),
        cache_write=stats.get('cache_write', stats.get('cache_write_tokens', 0)),
        reasoning_tokens=stats.get('reasoning_tokens', stats.get('reasoning', 0)),
    )


# ============================================================
# 压缩策略 & Token 预算
# ============================================================

class CompressionStrategy(Enum):
    """压缩策略"""
    NONE = "none"  # 不压缩
    AGGRESSIVE = "aggressive"  # 激进压缩（最大节省）
    BALANCED = "balanced"  # 平衡压缩（默认）
    CONSERVATIVE = "conservative"  # 保守压缩（保留更多细节）


@dataclass
class TokenBudget:
    """Token 预算配置"""
    max_tokens: int = 128000  # 最大 token 数
    warning_threshold: float = 0.7  # 警告阈值（70%）
    compression_threshold: float = 0.8  # 压缩阈值（80%）
    emergency_threshold: float = 0.9  # 紧急压缩阈值（90%）
    keep_recent_messages: int = 4  # 保留最近 N 条消息
    strategy: CompressionStrategy = CompressionStrategy.BALANCED


def _content_to_text(content: Any) -> str:
    """安全提取消息文本（兼容纯字符串与多模态 list content）。"""
    if not content:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        img_n = 0
        for p in content:
            if isinstance(p, dict):
                if p.get("type") == "text":
                    t = p.get("text", "")
                    if t:
                        parts.append(str(t))
                elif p.get("type") in ("image_url", "image"):
                    img_n += 1
            elif isinstance(p, str):
                parts.append(p)
        if img_n:
            parts.append("[附图 %d 张]" % img_n)
        return "\n".join(parts)
    return str(content)


class TokenOptimizer:
    """Token 优化器 - 系统化减少 token 消耗"""
    
    def __init__(self, budget: Optional[TokenBudget] = None, model: str = ''):
        self.budget = budget or TokenBudget()
        self.model = model  # 用于 tiktoken
        self._compression_history: List[Dict[str, Any]] = []  # 压缩历史记录
    
    def estimate_tokens(self, text: str) -> int:
        """估算文本的 token 数量（优先 tiktoken）"""
        return count_tokens(text, self.model)
    
    def calculate_message_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """计算消息列表的总 token 数（含 tool_calls、多模态内容）"""
        total = 0
        for msg in messages:
            content = msg.get('content', '') or ''
            if isinstance(content, list):
                # 多模态消息：提取文字部分计算 token，图片按固定开销估算
                for part in content:
                    if isinstance(part, dict):
                        if part.get('type') == 'text':
                            total += self.estimate_tokens(part.get('text', ''))
                        elif part.get('type') in ('image_url', 'image'):
                            total += 765  # 图片固定约 765 token（低分辨率模式）
                    elif isinstance(part, str):
                        total += self.estimate_tokens(part)
            else:
                total += self.estimate_tokens(content)
            # tool_calls 中的函数名和参数也占 token
            tool_calls = msg.get('tool_calls')
            if tool_calls:
                for tc in tool_calls:
                    fn = tc.get('function', {})
                    total += self.estimate_tokens(fn.get('name', ''))
                    total += self.estimate_tokens(fn.get('arguments', ''))
                    total += 8  # tool_call 结构开销（id, type, function wrapper）
            # 消息格式开销（role, 格式字符等）
            total += 4
        return total
    
    def compress_tool_result(self, result: Dict[str, Any], max_length: int = 200) -> str:
        """压缩工具调用结果
        
        Args:
            result: 工具执行结果
            max_length: 最大字符数
        
        Returns:
            压缩后的结果摘要
        """
        if not result:
            return ""
        
        success = result.get('success', False)
        if not success:
            error = result.get('error', 'Unknown error')
            return f"错误: {error[:max_length]}"
        
        result_text = result.get('result', '')
        if not result_text:
            return "成功"
        
        # 如果结果很短，直接返回
        if len(result_text) <= max_length:
            return f"{result_text}"
        
        # 提取关键信息
        lines = [l.strip() for l in result_text.split('\n') if l.strip()]
        
        # 策略1: 提取第一行和最后一行
        if len(lines) >= 2:
            summary = f"{lines[0][:max_length//2]} ... {lines[-1][:max_length//2]}"
        elif len(lines) == 1:
            summary = f"{lines[0][:max_length]}"
        else:
            summary = f"{result_text[:max_length]}..."
        
        return summary
    
    def compress_messages(
        self,
        messages: List[Dict[str, Any]],
        keep_recent: Optional[int] = None,
        strategy: Optional[CompressionStrategy] = None
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """压缩消息列表
        
        Args:
            messages: 原始消息列表
            keep_recent: 保留最近 N 条消息（默认使用 budget 配置）
            strategy: 压缩策略（默认使用 budget 配置）
        
        Returns:
            (压缩后的消息列表, 压缩统计信息)
        """
        if not messages:
            return [], {'compressed': 0, 'saved_tokens': 0}
        
        keep_recent = keep_recent or self.budget.keep_recent_messages
        strategy = strategy or self.budget.strategy
        
        if len(messages) <= keep_recent:
            return messages, {'compressed': 0, 'saved_tokens': 0}
        
        # 分离旧消息和新消息（先在原始角色上摘要，避免先强转 tool→assistant 导致工具统计死代码）
        old_messages = messages[:-keep_recent] if len(messages) > keep_recent else []
        recent_messages = list(messages[-keep_recent:]) if len(messages) >= keep_recent else list(messages)
        # 确保保留的最近消息不以孤儿 tool 开头
        while recent_messages and recent_messages[0].get('role') == 'tool':
            old_messages.append(recent_messages.pop(0))

        # 计算原始 token
        original_tokens = self.calculate_message_tokens(messages)

        # 根据策略压缩
        compressed_messages = []
        if old_messages:
            if strategy == CompressionStrategy.AGGRESSIVE:
                summary = self._generate_aggressive_summary(old_messages)
            elif strategy == CompressionStrategy.CONSERVATIVE:
                summary = self._generate_conservative_summary(old_messages)
            else:  # BALANCED
                summary = self._generate_balanced_summary(old_messages)

            if summary:
                compressed_messages.append({
                    'role': 'user',
                    'content': summary
                })
                if recent_messages and recent_messages[0].get('role') == 'user':
                    compressed_messages.append({
                        'role': 'assistant',
                        'content': '已了解上述历史对话摘要，将继续协助你。'
                    })

        # 保留最近的消息
        compressed_messages.extend(recent_messages)
        
        # 计算节省的 token
        compressed_tokens = self.calculate_message_tokens(compressed_messages)
        saved_tokens = original_tokens - compressed_tokens
        
        stats = {
            'compressed': len(old_messages),
            'kept': len(recent_messages),
            'original_tokens': original_tokens,
            'compressed_tokens': compressed_tokens,
            'saved_tokens': saved_tokens,
            'saved_percent': (saved_tokens / original_tokens * 100) if original_tokens > 0 else 0
        }
        
        return compressed_messages, stats
    
    def _generate_balanced_summary(self, messages: List[Dict[str, Any]]) -> str:
        """生成平衡摘要（默认策略）"""
        parts = ["[历史对话摘要 - 已压缩以节省 token]"]
        
        user_requests = []
        ai_responses = []
        tool_calls = []
        
        for msg in messages:
            role = msg.get('role', '')
            content = _content_to_text(msg.get('content', ''))
            
            if role == 'user':
                req = content[:150].replace('\n', ' ').strip()
                if len(content) > 150:
                    req += "..."
                if req:
                    user_requests.append(req)
            
            elif role == 'assistant':
                lines = [l.strip() for l in content.split('\n') if l.strip()]
                if lines:
                    res = lines[-1][:100].replace('\n', ' ').strip()
                    if len(lines[-1]) > 100:
                        res += "..."
                    if res:
                        ai_responses.append(res)
            
            elif role == 'tool':
                tool_call_id = msg.get('tool_call_id', '') or msg.get('name', '')
                if tool_call_id:
                    tool_calls.append(f"工具调用: {tool_call_id[:50]}")
        
        if user_requests:
            parts.append(f"\n用户请求 ({len(user_requests)} 条):")
            for i, req in enumerate(user_requests[:8], 1):
                parts.append(f"  {i}. {req}")
            if len(user_requests) > 8:
                parts.append(f"  ... 还有 {len(user_requests) - 8} 条请求")
        
        if ai_responses:
            parts.append(f"\nAI 完成 ({len(ai_responses)} 条):")
            for i, res in enumerate(ai_responses[:8], 1):
                parts.append(f"  {i}. {res}")
            if len(ai_responses) > 8:
                parts.append(f"  ... 还有 {len(ai_responses) - 8} 条结果")
        
        if tool_calls:
            parts.append(f"\n工具调用: {len(tool_calls)} 次")
        
        return "\n".join(parts)
    
    def _generate_aggressive_summary(self, messages: List[Dict[str, Any]]) -> str:
        """生成激进摘要（最大节省）"""
        parts = ["[历史对话摘要 - 激进压缩]"]
        
        user_count = sum(1 for m in messages if m.get('role') == 'user')
        assistant_count = sum(1 for m in messages if m.get('role') == 'assistant')
        tool_count = sum(1 for m in messages if m.get('role') == 'tool')
        
        parts.append(f"用户请求: {user_count} 条")
        parts.append(f"AI 回复: {assistant_count} 条")
        if tool_count > 0:
            parts.append(f"工具调用: {tool_count} 次")
        
        if messages:
            last_user = next((m for m in reversed(messages) if m.get('role') == 'user'), None)
            if last_user:
                content = _content_to_text(last_user.get('content', ''))[:100]
                parts.append(f"\n最后请求: {content.replace(chr(10), ' ')}")
        
        return "\n".join(parts)
    
    def _generate_conservative_summary(self, messages: List[Dict[str, Any]]) -> str:
        """生成保守摘要（保留更多细节）"""
        parts = ["[历史对话摘要 - 保守压缩]"]
        
        user_requests = []
        ai_responses = []
        
        for msg in messages:
            role = msg.get('role', '')
            content = msg.get('content', '')
            
            if role == 'user':
                req = content[:250].replace('\n', ' ').strip()
                if len(content) > 250:
                    req += "..."
                if req:
                    user_requests.append(req)
            
            elif role == 'assistant':
                lines = [l.strip() for l in content.split('\n') if l.strip()]
                if lines:
                    res = " | ".join(lines[:3])[:200]
                    if len(lines) > 3:
                        res += "..."
                    if res:
                        ai_responses.append(res)
        
        if user_requests:
            parts.append(f"\n用户请求 ({len(user_requests)} 条):")
            for i, req in enumerate(user_requests[:12], 1):
                parts.append(f"  {i}. {req}")
            if len(user_requests) > 12:
                parts.append(f"  ... 还有 {len(user_requests) - 12} 条")
        
        if ai_responses:
            parts.append(f"\nAI 完成 ({len(ai_responses)} 条):")
            for i, res in enumerate(ai_responses[:12], 1):
                parts.append(f"  {i}. {res}")
            if len(ai_responses) > 12:
                parts.append(f"  ... 还有 {len(ai_responses) - 12} 条")
        
        return "\n".join(parts)
    
    def optimize_tool_results(
        self,
        tool_calls_history: List[Dict[str, Any]],
        max_result_length: int = 150
    ) -> List[Dict[str, Any]]:
        """优化工具调用历史，压缩结果"""
        optimized = []
        
        for call in tool_calls_history:
            result = call.get('result', {})
            
            if isinstance(result, dict):
                compressed_result = self.compress_tool_result(result, max_result_length)
                optimized_call = call.copy()
                optimized_call['result'] = {
                    'success': result.get('success', False),
                    'summary': compressed_result,
                    'original_length': len(str(result.get('result', '')))
                }
                optimized.append(optimized_call)
            else:
                optimized.append(call)
        
        return optimized
    
    def should_compress(self, current_tokens: int, limit: Optional[int] = None) -> Tuple[bool, str]:
        """判断是否应该压缩"""
        limit = limit or self.budget.max_tokens
        
        if current_tokens >= limit * self.budget.emergency_threshold:
            return True, f"紧急压缩: Token 使用 {current_tokens}/{limit} ({current_tokens/limit*100:.1f}%)"
        
        if current_tokens >= limit * self.budget.compression_threshold:
            return True, f"建议压缩: Token 使用 {current_tokens}/{limit} ({current_tokens/limit*100:.1f}%)"
        
        if current_tokens >= limit * self.budget.warning_threshold:
            return False, f"警告: Token 使用 {current_tokens}/{limit} ({current_tokens/limit*100:.1f}%)"
        
        return False, ""
    
    def optimize_system_prompt(self, prompt: str, max_length: int = 2000) -> str:
        """优化系统提示，移除冗余内容"""
        if len(prompt) <= max_length:
            return prompt
        
        lines = [l for l in prompt.split('\n') if l.strip()]
        
        seen = set()
        unique_lines = []
        for line in lines:
            key = line[:50].strip()
            if key not in seen:
                seen.add(key)
                unique_lines.append(line)
        
        optimized = '\n'.join(unique_lines)
        
        if len(optimized) > max_length:
            optimized = optimized[:max_length] + "...\n[系统提示已优化以节省 token]"
        
        return optimized
    
    def filter_redundant_messages(
        self,
        messages: List[Dict[str, Any]],
        keep_patterns: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """过滤冗余消息"""
        if not keep_patterns:
            keep_patterns = ['错误', 'error', '成功', '完成', '创建', '删除']
        
        filtered = []
        
        for msg in messages:
            content = _content_to_text(msg.get('content', '')).lower()
            role = msg.get('role', '')
            
            if role == 'system':
                filtered.append(msg)
                continue
            
            if role == 'tool':
                tool_name = msg.get('name', 'unknown')
                content = msg.get('content', '')
                filtered.append({
                    'role': 'assistant',
                    'content': f"[工具结果] {tool_name}: {content}"
                })
                continue
            
            is_important = any(pattern.lower() in content for pattern in keep_patterns)
            
            if is_important or len(filtered) < 5:
                filtered.append(msg)
        
        return filtered
    
    def get_optimization_report(
        self,
        messages: List[Dict[str, Any]],
        current_tokens: int,
        limit: Optional[int] = None
    ) -> Dict[str, Any]:
        """生成优化报告"""
        limit = limit or self.budget.max_tokens
        
        report = {
            'current_tokens': current_tokens,
            'limit': limit,
            'usage_percent': (current_tokens / limit * 100) if limit > 0 else 0,
            'should_compress': False,
            'compression_recommendation': '',
            'estimated_savings': 0,
            'suggestions': []
        }
        
        should_compress, reason = self.should_compress(current_tokens, limit)
        report['should_compress'] = should_compress
        report['compression_recommendation'] = reason
        
        if should_compress:
            compressed, stats = self.compress_messages(messages)
            report['estimated_savings'] = stats.get('saved_tokens', 0)
            report['suggestions'].append(f"压缩后可节省约 {stats.get('saved_percent', 0):.1f}% token")
        
        if current_tokens > limit * 0.5:
            report['suggestions'].append("考虑使用缓存功能保存当前对话")
        
        tool_results = [m for m in messages if m.get('role') == 'tool']
        if len(tool_results) > 10:
            report['suggestions'].append(f"有 {len(tool_results)} 条工具结果，建议压缩")
        
        return report


# ============================================================
# LLM 驱动的对话摘要器
# ============================================================

class LLMSummarizer:
    """Claude Agent SDK / Codex 风格的上下文压缩摘要器。

    当对话逼近上下文窗口阈值（或用户手动压缩）时，用当前模型将早期历史一次性
    提炼为结构化交接摘要，替代碎片化的正则截断，既保留完整语义又不破坏平时的 Prompt Cache。
    """

    # 对齐 Claude Agent SDK compaction_control 的结构化交接提示词
    SUMMARY_PROMPT = """You are performing context compaction for an ongoing Houdini technical artist agent session.
Summarize the conversation history below concisely in the same language as the conversation so the agent can seamlessly continue without losing critical state.

Structure your summary using these exact sections:

# Task Overview
The user's original goal, requirements, and acceptance criteria.

# Current State
The exact current state of the Houdini scene / project (preserve all node paths like /obj/..., modified parameter names/values, created/edited file paths, and completed milestones).

# Important Discoveries
Key technical findings, design decisions, constraints, and verified facts from tool outputs.

# Errors & Resolutions
Any errors encountered and how they were fixed, plus any still-unresolved issues.

# Next Steps
Concrete remaining tasks or the immediate next action to take.

# Context to Preserve
User preferences, naming conventions, important constants, or anything that must not be forgotten.

Rules:
- Preserve exact node paths, parameter names, VEX/Python identifiers, file paths, and numeric values.
- Omit raw verbose tool dumps, repetitive polling, and casual filler.
- Write directly as a factual handoff checkpoint (no meta-commentary).

Conversation to compact:
---
{conversation}
---"""

    @staticmethod
    def format_messages_for_summary(messages: list, max_total_chars: int = 48000) -> str:
        """将消息列表格式化为可供 LLM 压缩摘要的纯文本。"""
        lines = []
        total_chars = 0
        for msg in messages:
            role = msg.get('role', 'unknown')
            content = _content_to_text(msg.get('content', ''))
            if role == 'assistant' and msg.get('tool_calls'):
                tc_desc = []
                for tc in msg.get('tool_calls', []):
                    fn = tc.get('function', {})
                    t_name = fn.get('name', '?')
                    t_args = str(fn.get('arguments', ''))[:240]
                    tc_desc.append(f"{t_name}({t_args})")
                tc_str = f"[Called tools: {'; '.join(tc_desc)}]"
                content = f"{content}\n{tc_str}".strip() if content else tc_str
            if not content:
                continue

            if len(content) > 2000:
                content = content[:1400] + "\n...[omitted]...\n" + content[-400:]

            prefix = {'user': 'User', 'assistant': 'Assistant', 'tool': 'Tool', 'system': 'System'}.get(role, role)
            line = f"[{prefix}]: {content}"
            if total_chars + len(line) > max_total_chars:
                lines.append("... (remaining messages truncated to fit compaction input)")
                break
            lines.append(line)
            total_chars += len(line) + 1

        return '\n'.join(lines)

    @classmethod
    def format_rounds_for_summary(cls, rounds: list, max_total_chars: int = 48000) -> str:
        """将多轮对话格式化为可供摘要的纯文本。"""
        flat = [m for r in rounds for m in r]
        return cls.format_messages_for_summary(flat, max_total_chars=max_total_chars)

    @classmethod
    def summarize_messages(cls, ai_client, messages: list,
                           model: str = 'deepseek-v4-flash',
                           provider: str = 'deepseek',
                           usage_out: Optional[dict] = None) -> Optional[str]:
        """使用模型生成 Claude Agent SDK 风格的结构化压缩摘要。"""
        if not messages:
            return None

        try:
            conversation_text = cls.format_messages_for_summary(messages)
            if not conversation_text.strip():
                return None

            prompt = cls.SUMMARY_PROMPT.format(conversation=conversation_text)
            summary_messages = [{'role': 'user', 'content': prompt}]

            content_text = ""
            if hasattr(ai_client, 'chat_stream'):
                chunks = []
                for ch in ai_client.chat_stream(
                    messages=summary_messages,
                    model=model,
                    provider=provider,
                    temperature=0.1,
                    max_tokens=1500,
                    tools=None,
                    tool_choice=None,
                    enable_thinking=False,
                ):
                    if not isinstance(ch, dict):
                        continue
                    ctype = ch.get('type')
                    if ctype == 'content':
                        chunks.append(ch.get('content', ''))
                    elif ctype == 'done':
                        u = ch.get('usage')
                        if isinstance(usage_out, dict) and isinstance(u, dict) and u:
                            usage_out.clear()
                            usage_out.update(u)
                    elif ctype == 'error':
                        chunks = []
                        break
                content_text = ''.join(chunks).strip()

            if not content_text and hasattr(ai_client, 'chat'):
                result = ai_client.chat(
                    messages=summary_messages,
                    model=model,
                    provider=provider,
                    temperature=0.1,
                    max_tokens=1500,
                    timeout=30,
                )
                if result and result.get('ok') is not False and result.get('content'):
                    content_text = str(result['content']).strip()
                    u = result.get('usage')
                    if isinstance(usage_out, dict) and isinstance(u, dict) and u:
                        usage_out.clear()
                        usage_out.update(u)

            if content_text:
                content_text = re.sub(r'<think>[\s\S]*?</think>', '', content_text).strip()
            return content_text or None

        except Exception as e:
            print(f"[LLMSummarizer] 摘要生成失败: {e}")
            return None

    @classmethod
    def summarize_rounds(cls, ai_client, rounds: list,
                         model: str = 'deepseek-v4-flash',
                         provider: str = 'deepseek') -> Optional[str]:
        """兼容按轮次列表调用的摘要入口。"""
        if not rounds:
            return None
        flat = [m for r in rounds for m in r]
        return cls.summarize_messages(ai_client, flat, model=model, provider=provider)
