# -*- coding: utf-8 -*-
"""
训练数据导出器
将当前聊天对话记录转换为大模型微调格式的训练数据
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional


class ChatTrainingExporter:
    """聊天记录训练数据导出器
    
    将对话历史转换为 OpenAI 微调格式。
    支持多轮对话、工具调用等复杂场景。
    """
    
    def __init__(self, output_dir: Optional[Path] = None):
        """初始化导出器
        
        Args:
            output_dir: 输出目录，默认为项目根目录下的 trainData
        """
        if output_dir is None:
            current_file = Path(__file__)
            project_root = current_file.parent.parent.parent
            output_dir = project_root / "trainData"
        
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def export_conversation(
        self, 
        conversation_history: List[Dict[str, Any]],
        system_prompt: Optional[str] = None,
        split_by_user: bool = True
    ) -> str:
        """导出对话历史为训练数据
        
        Args:
            conversation_history: 对话历史列表
            system_prompt: 系统提示词（可选）
            split_by_user: 是否按用户消息分割成多个训练样本
        
        Returns:
            导出的文件路径
        """
        if not conversation_history:
            raise ValueError("对话历史为空")
        
        # 根据策略生成训练样本
        if split_by_user:
            samples = self._split_by_user_turns(conversation_history, system_prompt)
        else:
            samples = [self._create_single_sample(conversation_history, system_prompt)]
        
        # 过滤空样本
        samples = [s for s in samples if s and s.get("messages")]
        
        if not samples:
            raise ValueError("无法生成有效的训练样本")
        
        # 生成文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"chat_train_{timestamp}_{len(samples)}samples.jsonl"
        filepath = self.output_dir / filename
        
        # 写入 JSONL 文件
        with open(filepath, 'w', encoding='utf-8') as f:
            for sample in samples:
                f.write(json.dumps(sample, ensure_ascii=False) + '\n')
        
        return str(filepath)
    
    def _split_by_user_turns(
        self, 
        history: List[Dict[str, Any]], 
        system_prompt: Optional[str]
    ) -> List[Dict[str, Any]]:
        """按用户消息分割成多个训练样本
        
        每个用户请求 + 对应的 AI 响应 = 一个训练样本
        这样可以生成多个高质量的短样本
        """
        samples = []
        
        # 基础系统消息
        base_system = {
            "role": "system",
            "content": system_prompt or self._get_default_system_prompt()
        }
        
        # 累积上下文（用于多轮对话）
        context_messages = []
        current_sample_messages = []
        
        i = 0
        while i < len(history):
            msg = history[i]
            role = msg.get("role", "")
            
            if role == "user":
                # 开始新的样本
                if current_sample_messages:
                    # 保存之前的样本
                    sample = self._finalize_sample(base_system, context_messages, current_sample_messages)
                    if sample:
                        samples.append(sample)
                    # 将之前的消息加入上下文
                    context_messages.extend(current_sample_messages)
                    # 限制上下文长度
                    context_messages = self._trim_context(context_messages)
                
                current_sample_messages = [self._clean_message(msg)]
                
            elif role == "assistant":
                # 清理 assistant 消息
                cleaned = self._clean_assistant_message(msg)
                if cleaned:
                    current_sample_messages.append(cleaned)
                    
            elif role == "tool":
                # 转换 tool 消息格式（确保有 tool_call_id）
                tool_msg = self._convert_tool_message(msg)
                if tool_msg:
                    current_sample_messages.append(tool_msg)
            
            i += 1
        
        # 处理最后一个样本
        if current_sample_messages:
            sample = self._finalize_sample(base_system, context_messages, current_sample_messages)
            if sample:
                samples.append(sample)
        
        return samples
    
    def _create_single_sample(
        self, 
        history: List[Dict[str, Any]], 
        system_prompt: Optional[str]
    ) -> Dict[str, Any]:
        """创建单个完整的训练样本"""
        messages = []
        
        # 添加系统消息
        messages.append({
            "role": "system",
            "content": system_prompt or self._get_default_system_prompt()
        })
        
        # 处理历史消息
        for msg in history:
            cleaned = self._clean_message(msg)
            if cleaned:
                messages.append(cleaned)
        
        return {"messages": messages} if len(messages) > 1 else None
    
    def _finalize_sample(
        self, 
        system_msg: Dict, 
        context: List[Dict], 
        current: List[Dict]
    ) -> Optional[Dict[str, Any]]:
        """完成一个训练样本
        
        确保样本有效：
        - 必须有用户消息
        - 必须有 AI 响应
        - 工具调用和响应必须配对
        """
        if not current:
            return None
        
        # 检查是否有用户消息和 AI 响应
        has_user = any(m.get("role") == "user" for m in current)
        has_assistant = any(m.get("role") == "assistant" for m in current)
        
        if not has_user or not has_assistant:
            return None
        
        # 构建消息列表
        messages = [system_msg.copy()]
        
        # 添加上下文（如果有）
        if context:
            # 只添加最近的上下文，避免过长
            recent_context = context[-6:]  # 最多3轮对话
            messages.extend(recent_context)
        
        # 添加当前对话
        messages.extend(current)
        
        # 验证工具调用配对
        messages = self._validate_tool_calls(messages)
        
        return {"messages": messages}
    
    def _clean_message(self, msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """清理消息格式"""
        role = msg.get("role", "")
        content = msg.get("content", "")
        
        if role == "user":
            if not content or not content.strip():
                return None
            return {"role": "user", "content": content.strip()}
        
        elif role == "assistant":
            return self._clean_assistant_message(msg)
        
        elif role == "tool":
            return self._convert_tool_message(msg)
        
        elif role == "system":
            # 跳过系统消息（我们会添加自己的）
            return None
        
        return None
    
    def _clean_assistant_message(self, msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """清理 assistant 消息"""
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls")
        
        result = {"role": "assistant"}
        
        if tool_calls:
            # 有工具调用
            result["content"] = None
            result["tool_calls"] = self._clean_tool_calls(tool_calls)
        elif content and content.strip():
            # 纯文本响应
            result["content"] = content.strip()
        else:
            return None
        
        return result
    
    def _clean_tool_calls(self, tool_calls: List[Dict]) -> List[Dict]:
        """清理工具调用格式"""
        cleaned = []
        for tc in tool_calls:
            if isinstance(tc, dict):
                tool_id = tc.get("id") or f"call_{uuid.uuid4().hex[:12]}"
                function = tc.get("function", {})
                
                cleaned.append({
                    "id": tool_id,
                    "type": "function",
                    "function": {
                        "name": function.get("name", ""),
                        "arguments": function.get("arguments", "{}")
                    }
                })
        return cleaned
    
    def _convert_tool_message(self, msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """转换 tool 消息格式
        
        旧格式: {"role": "tool", "name": "xxx", "content": "..."}
        新格式: {"role": "tool", "tool_call_id": "xxx", "content": "..."}
        """
        content = msg.get("content", "")
        tool_call_id = msg.get("tool_call_id")
        name = msg.get("name", "")
        
        if not content:
            return None
        
        # 如果没有 tool_call_id，生成一个
        if not tool_call_id:
            tool_call_id = f"call_{uuid.uuid4().hex[:12]}"
        
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content[:500]  # 限制长度
        }
    
    def _validate_tool_calls(self, messages: List[Dict]) -> List[Dict]:
        """验证并修复工具调用配对
        
        确保每个 tool 消息都有对应的 assistant tool_calls
        """
        result = []
        pending_tool_calls = {}  # {tool_call_id: tool_call}
        
        for msg in messages:
            role = msg.get("role")
            
            if role == "assistant" and msg.get("tool_calls"):
                # 记录工具调用 ID
                for tc in msg.get("tool_calls", []):
                    tc_id = tc.get("id")
                    if tc_id:
                        pending_tool_calls[tc_id] = tc
                result.append(msg)
                
            elif role == "tool":
                tc_id = msg.get("tool_call_id")
                if tc_id in pending_tool_calls:
                    # 有对应的 assistant tool_calls
                    result.append(msg)
                    del pending_tool_calls[tc_id]
                else:
                    # 没有对应的 tool_calls，创建一个
                    # 从内容中提取工具名
                    content = msg.get("content", "")
                    tool_name = "execute_tool"
                    if ":" in content:
                        tool_name = content.split(":")[0].strip()
                    
                    # 添加 assistant 消息
                    new_tc_id = f"call_{uuid.uuid4().hex[:12]}"
                    assistant_msg = {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": new_tc_id,
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": "{}"
                            }
                        }]
                    }
                    result.append(assistant_msg)
                    
                    # 修正 tool 消息的 ID
                    tool_msg = msg.copy()
                    tool_msg["tool_call_id"] = new_tc_id
                    result.append(tool_msg)
            else:
                result.append(msg)
        
        return result
    
    def _trim_context(self, context: List[Dict], max_messages: int = 10) -> List[Dict]:
        """限制上下文长度"""
        if len(context) <= max_messages:
            return context
        return context[-max_messages:]
    
    def _get_default_system_prompt(self) -> str:
        """获取默认系统提示词"""
        return """你是Houdini执行器。直接执行操作，不解释。

规则:
-直接调用工具执行
-不输出思考过程
-先检查节点存在再操作
-VEX代码优先使用create_wrangle_node
-完成后调用verify_and_summarize验证"""


def export_chat_training_data(
    conversation_history: List[Dict[str, Any]],
    system_prompt: Optional[str] = None,
    split_by_user: bool = True
) -> str:
    """导出聊天训练数据的便捷函数"""
    exporter = ChatTrainingExporter()
    return exporter.export_conversation(conversation_history, system_prompt, split_by_user)
