# -*- coding: utf-8 -*-
"""
Token 优化器
在节省 token 的同时保留 AI 理解工具的关键信息
"""

import copy
import json
import re
from typing import List, Dict, Any, Optional


class UltraOptimizer:
    """Token 优化器 - 保留语义完整性"""
    
    @staticmethod
    def compress_system_prompt(prompt: str) -> str:
        """压缩系统提示：移除冗余但保留核心规则"""
        if not prompt:
            return ""
        # 移除多余空行
        prompt = re.sub(r'\n{3,}', '\n\n', prompt)
        # 移除注释性说明行
        prompt = re.sub(r'^\s*//.*$', '', prompt, flags=re.MULTILINE)
        return prompt.strip()
    
    @staticmethod
    def optimize_tool_definitions(tools: List[Dict]) -> List[Dict]:
        """优化工具定义 - 深拷贝后轻量精简，保留完整语义

        关键原则：
        - 绝不修改原始 tools 列表（深拷贝）
        - 保留全部描述文本（AI 需要理解工具用法）
        - 仅移除纯装饰性 emoji
        """
        optimized = []
        for tool in tools:
            # 深拷贝：绝不修改原始定义
            tool_copy = copy.deepcopy(tool)
            func = tool_copy.get('function', {})
            if not func:
                optimized.append(tool_copy)
                continue

            # 仅移除装饰性 emoji（保留【】标记和全部文字描述）
            desc = func.get('description', '')
            desc = re.sub(r'[🔥🎨💡✅❌🟡⚠️🔗]', '', desc)
            func['description'] = desc.strip()

            optimized.append(tool_copy)
        return optimized

    @staticmethod
    def compress_tool_result(result: Dict[str, Any], max_chars: int = 100) -> str:
        """压缩工具结果（用于 UI 显示摘要，不影响 AI 上下文）"""
        if not result.get('success'):
            error = result.get('error', '')
            return f"错误: {error[:80]}" if error else "失败"
        
        result_text = str(result.get('result', ''))
        if not result_text:
            return "OK"
        
        # 移除多余空白
        result_text = re.sub(r'\s+', ' ', result_text).strip()
        
        if len(result_text) <= max_chars:
            return result_text
        
        # 保留开头和结尾
        half = max_chars // 2
        return f"{result_text[:half]}...{result_text[-half:]}"
    
    @staticmethod
    def optimize_tool_result_message(tool_name: str, result: Dict[str, Any]) -> str:
        """优化工具结果消息格式（仅用于 UI 摘要）"""
        compressed = UltraOptimizer.compress_tool_result(result, max_chars=120)
        return f"{tool_name}: {compressed}"
    
    @staticmethod
    def compress_message_content(content: str, max_tokens: int = 80) -> str:
        """压缩消息内容（用于历史消息摘要）"""
        if not content:
            return ""
        
        # 估算 token（中文约1.5字符/token，英文约4字符/token）
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', content))
        other_chars = len(content) - chinese_chars
        estimated_tokens = int(chinese_chars / 1.5 + other_chars / 4)
        
        if estimated_tokens <= max_tokens:
            return content
        
        # 截断到合理长度
        max_chars = max_tokens * 3
        return content[:max_chars] + "..."
    
    @staticmethod
    def remove_formatting_overhead(text: str) -> str:
        """移除 Markdown 格式开销"""
        text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)  # **bold**
        text = re.sub(r'\*([^*]+)\*', r'\1', text)  # *italic*
        text = re.sub(r'`([^`]+)`', r'\1', text)  # `code`
        text = re.sub(r'#{1,6}\s+', '', text)  # headers
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)  # links
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()
