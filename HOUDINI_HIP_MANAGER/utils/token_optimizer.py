# -*- coding: utf-8 -*-
"""
Token 优化管理器
系统化减少 token 消耗的多种策略
"""

import json
import re
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum


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


class TokenOptimizer:
    """Token 优化器 - 系统化减少 token 消耗"""
    
    def __init__(self, budget: Optional[TokenBudget] = None):
        self.budget = budget or TokenBudget()
        self._compression_history: List[Dict[str, Any]] = []  # 压缩历史记录
    
    def estimate_tokens(self, text: str) -> int:
        """估算文本的 token 数量"""
        if not text:
            return 0
        
        # 中文约 1.5 字符/token，英文约 4 字符/token
        # 使用混合估算
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        other_chars = len(text) - chinese_chars
        
        tokens = chinese_chars / 1.5 + other_chars / 4
        return int(tokens) + 1  # +1 确保不为 0
    
    def calculate_message_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """计算消息列表的总 token 数（含 tool_calls）"""
        total = 0
        for msg in messages:
            content = msg.get('content', '') or ''
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
        
        # ⚠️ 将 role="tool" 消息转换为 assistant 格式（避免 API 400 错误）
        # API 要求 tool 消息必须有 tool_call_id，但历史缓存中的 tool 消息可能缺少该字段
        converted_messages = []
        for m in messages:
            if m.get('role') == 'tool':
                # 将 tool 消息转换为 assistant 上下文格式
                tool_name = m.get('name', 'unknown')
                content = m.get('content', '')
                converted_messages.append({
                    'role': 'assistant',
                    'content': f"[工具结果] {tool_name}: {content}"
                })
            else:
                converted_messages.append(m)
        
        # 分离旧消息和新消息
        old_messages = converted_messages[:-keep_recent] if len(converted_messages) > keep_recent else []
        recent_messages = converted_messages[-keep_recent:] if len(converted_messages) >= keep_recent else converted_messages
        
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
                    'role': 'system',
                    'content': summary
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
            content = msg.get('content', '')
            
            if role == 'user':
                # 提取用户请求（前150字符）
                req = content[:150].replace('\n', ' ').strip()
                if len(content) > 150:
                    req += "..."
                if req:
                    user_requests.append(req)
            
            elif role == 'assistant':
                # 提取 AI 回复的关键信息
                lines = [l.strip() for l in content.split('\n') if l.strip()]
                if lines:
                    # 取最后一行或前100字符
                    res = lines[-1][:100].replace('\n', ' ').strip()
                    if len(lines[-1]) > 100:
                        res += "..."
                    if res:
                        ai_responses.append(res)
            
            elif role == 'tool':
                # 提取工具调用信息
                tool_call_id = msg.get('tool_call_id', '')
                if tool_call_id:
                    tool_calls.append(f"工具调用: {tool_call_id[:50]}")
        
        # 合并摘要
        if user_requests:
            parts.append(f"\n用户请求 ({len(user_requests)} 条):")
            for i, req in enumerate(user_requests[:8], 1):  # 最多8条
                parts.append(f"  {i}. {req}")
            if len(user_requests) > 8:
                parts.append(f"  ... 还有 {len(user_requests) - 8} 条请求")
        
        if ai_responses:
            parts.append(f"\nAI 完成 ({len(ai_responses)} 条):")
            for i, res in enumerate(ai_responses[:8], 1):  # 最多8条
                parts.append(f"  {i}. {res}")
            if len(ai_responses) > 8:
                parts.append(f"  ... 还有 {len(ai_responses) - 8} 条结果")
        
        if tool_calls:
            parts.append(f"\n工具调用: {len(tool_calls)} 次")
        
        return "\n".join(parts)
    
    def _generate_aggressive_summary(self, messages: List[Dict[str, Any]]) -> str:
        """生成激进摘要（最大节省）"""
        parts = ["[历史对话摘要 - 激进压缩]"]
        
        # 只统计数量，不保留细节
        user_count = sum(1 for m in messages if m.get('role') == 'user')
        assistant_count = sum(1 for m in messages if m.get('role') == 'assistant')
        tool_count = sum(1 for m in messages if m.get('role') == 'tool')
        
        parts.append(f"用户请求: {user_count} 条")
        parts.append(f"AI 回复: {assistant_count} 条")
        if tool_count > 0:
            parts.append(f"工具调用: {tool_count} 次")
        
        # 提取最后几条的关键词
        if messages:
            last_user = next((m for m in reversed(messages) if m.get('role') == 'user'), None)
            if last_user:
                content = last_user.get('content', '')[:100]
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
                    # 保留更多行
                    res = " | ".join(lines[:3])[:200]
                    if len(lines) > 3:
                        res += "..."
                    if res:
                        ai_responses.append(res)
        
        if user_requests:
            parts.append(f"\n用户请求 ({len(user_requests)} 条):")
            for i, req in enumerate(user_requests[:12], 1):  # 最多12条
                parts.append(f"  {i}. {req}")
            if len(user_requests) > 12:
                parts.append(f"  ... 还有 {len(user_requests) - 12} 条")
        
        if ai_responses:
            parts.append(f"\nAI 完成 ({len(ai_responses)} 条):")
            for i, res in enumerate(ai_responses[:12], 1):  # 最多12条
                parts.append(f"  {i}. {res}")
            if len(ai_responses) > 12:
                parts.append(f"  ... 还有 {len(ai_responses) - 12} 条")
        
        return "\n".join(parts)
    
    def optimize_tool_results(
        self,
        tool_calls_history: List[Dict[str, Any]],
        max_result_length: int = 150
    ) -> List[Dict[str, Any]]:
        """优化工具调用历史，压缩结果
        
        Args:
            tool_calls_history: 工具调用历史
            max_result_length: 每个结果的最大长度
        
        Returns:
            优化后的工具调用历史
        """
        optimized = []
        
        for call in tool_calls_history:
            result = call.get('result', {})
            
            # 压缩结果
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
        """判断是否应该压缩
        
        Returns:
            (是否应该压缩, 原因说明)
        """
        limit = limit or self.budget.max_tokens
        
        if current_tokens >= limit * self.budget.emergency_threshold:
            return True, f"紧急压缩: Token 使用 {current_tokens}/{limit} ({current_tokens/limit*100:.1f}%)"
        
        if current_tokens >= limit * self.budget.compression_threshold:
            return True, f"建议压缩: Token 使用 {current_tokens}/{limit} ({current_tokens/limit*100:.1f}%)"
        
        if current_tokens >= limit * self.budget.warning_threshold:
            return False, f"警告: Token 使用 {current_tokens}/{limit} ({current_tokens/limit*100:.1f}%)"
        
        return False, ""
    
    def optimize_system_prompt(self, prompt: str, max_length: int = 2000) -> str:
        """优化系统提示，移除冗余内容
        
        Args:
            prompt: 原始系统提示
            max_length: 最大字符数
        
        Returns:
            优化后的系统提示
        """
        if len(prompt) <= max_length:
            return prompt
        
        # 移除多余的空行
        lines = [l for l in prompt.split('\n') if l.strip()]
        
        # 移除重复的说明
        seen = set()
        unique_lines = []
        for line in lines:
            # 简单的去重（基于前50字符）
            key = line[:50].strip()
            if key not in seen:
                seen.add(key)
                unique_lines.append(line)
        
        optimized = '\n'.join(unique_lines)
        
        # 如果还是太长，截断
        if len(optimized) > max_length:
            optimized = optimized[:max_length] + "...\n[系统提示已优化以节省 token]"
        
        return optimized
    
    def filter_redundant_messages(
        self,
        messages: List[Dict[str, Any]],
        keep_patterns: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """过滤冗余消息
        
        Args:
            messages: 消息列表
            keep_patterns: 保留模式（包含这些关键词的消息会被保留）
        
        Returns:
            过滤后的消息列表
        """
        if not keep_patterns:
            keep_patterns = ['错误', 'error', '成功', '完成', '创建', '删除']
        
        filtered = []
        
        for msg in messages:
            content = msg.get('content', '').lower()
            role = msg.get('role', '')
            
            # 总是保留 system 消息
            if role == 'system':
                filtered.append(msg)
                continue
            
            # ⚠️ 将 tool 消息转换为 assistant 格式（避免 API 400 错误）
            if role == 'tool':
                tool_name = msg.get('name', 'unknown')
                content = msg.get('content', '')
                filtered.append({
                    'role': 'assistant',
                    'content': f"[工具结果] {tool_name}: {content}"
                })
                continue
            
            # 检查是否包含重要关键词
            is_important = any(pattern.lower() in content for pattern in keep_patterns)
            
            # 保留重要消息或最近的几条
            if is_important or len(filtered) < 5:
                filtered.append(msg)
        
        return filtered
    
    def get_optimization_report(
        self,
        messages: List[Dict[str, Any]],
        current_tokens: int,
        limit: Optional[int] = None
    ) -> Dict[str, Any]:
        """生成优化报告
        
        Returns:
            优化建议和统计信息
        """
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
            # 估算压缩后的节省
            compressed, stats = self.compress_messages(messages)
            report['estimated_savings'] = stats.get('saved_tokens', 0)
            report['suggestions'].append(f"压缩后可节省约 {stats.get('saved_percent', 0):.1f}% token")
        
        # 其他建议
        if current_tokens > limit * 0.5:
            report['suggestions'].append("考虑使用缓存功能保存当前对话")
        
        tool_results = [m for m in messages if m.get('role') == 'tool']
        if len(tool_results) > 10:
            report['suggestions'].append(f"有 {len(tool_results)} 条工具结果，建议压缩")
        
        return report

