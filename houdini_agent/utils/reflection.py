# -*- coding: utf-8 -*-
"""
反思模块 (Reflection Module)

混合反思策略：
1. 规则反思（每次任务后）：零成本，从工具调用链提取信号
2. LLM 深度反思（每 N 个任务或条件触发）：使用便宜模型生成抽象规则

这是 AI 的"成长引擎"——每次任务后自动运行。
"""

import json
import time
import traceback
from typing import Any, Callable, Dict, List, Optional, Tuple

from .memory_store import (
    MemoryStore,
    EpisodicRecord,
    SemanticRecord,
    ProceduralRecord,
    get_memory_store,
)
from .reward_engine import RewardEngine, get_reward_engine

# ============================================================
# 反思配置
# ============================================================

# LLM 深度反思间隔（每 N 个任务触发一次）
DEEP_REFLECT_INTERVAL = 5
# 错误率上升阈值（触发紧急反思）
ERROR_RATE_SPIKE_THRESHOLD = 0.5

# LLM 反思 Prompt 模板
REFLECTION_PROMPT = """你是一个自我改进的 AI 助手。请分析以下最近完成的任务记录，提取可复用的经验规则。

## 任务记录
{episodic_summaries}

## 要求
分析这些任务，提取：
1. **通用经验规则**：从成功和失败中总结出的可复用规则
2. **策略更新**：哪些问题解决策略应该调整优先级
3. **技能置信度**：评估各领域的掌握程度

请以 JSON 格式输出（不要包含 ```json 标记）：
{{
  "semantic_rules": [
    {{"rule": "规则描述", "category": "分类(error_handling/workflow/vex/nodes/general)", "confidence": 0.8}}
  ],
  "strategy_updates": [
    {{"name": "策略名", "priority_delta": 0.1, "reason": "调整原因"}}
  ],
  "skill_confidence": {{
    "vex": 0.8,
    "node_creation": 0.9,
    "terrain": 0.5,
    "general": 0.7
  }}
}}
"""


class ReflectionModule:
    """混合反思模块：规则反思 + 定期 LLM 深度反思"""

    def __init__(
        self,
        store: Optional[MemoryStore] = None,
        reward_engine: Optional[RewardEngine] = None,
    ):
        self.store = store or get_memory_store()
        self.reward_engine = reward_engine or get_reward_engine()
        self._task_count_since_reflect = 0
        self._recent_error_counts: List[int] = []  # 最近 N 个任务的错误次数
        self._max_recent = 10

    # ==========================================================
    # 规则反思（每次任务后，零成本）
    # ==========================================================

    def rule_reflect(self, episodic: EpisodicRecord, tool_calls: List[Dict]) -> EpisodicRecord:
        """规则反思：从工具调用链提取信号并更新 episodic tags

        Args:
            episodic: 事件记忆记录
            tool_calls: 工具调用序列 [{"name": ..., "success": ..., "error": ...}, ...]

        Returns:
            更新后的 episodic 记录
        """
        tags = list(episodic.tags)

        # 1. 检测重试次数
        retry_count = episodic.retry_count
        if retry_count > 2:
            tags.append("retry_heavy")

        # 2. 检测错误后成功（纠错行为）
        has_error = False
        has_success_after_error = False
        for tc in tool_calls:
            if tc.get("error") or not tc.get("success", True):
                has_error = True
            elif has_error and tc.get("success", True):
                has_success_after_error = True
                break

        if has_error and has_success_after_error and episodic.success:
            tags.append("error_correction")

        if has_error and not episodic.success:
            tags.append("unresolved_error")

        # 3. 检测复杂任务（工具调用 > 10）
        if len(tool_calls) > 10:
            tags.append("complex_task")

        # 4. 检测高效任务（工具调用 <= 3 且成功）
        if len(tool_calls) <= 3 and episodic.success:
            tags.append("efficient_task")

        # 5. 分析工具类型
        tool_names = [tc.get("name", "") for tc in tool_calls]
        if any("vex" in n.lower() or "wrangle" in n.lower() for n in tool_names):
            tags.append("vex_related")
        if any("create_node" in n for n in tool_names):
            tags.append("node_creation")
        if any("terrain" in n.lower() or "heightfield" in n.lower() for n in tool_names):
            tags.append("terrain_related")

        # 去重
        tags = list(dict.fromkeys(tags))
        episodic.tags = tags

        # 更新数据库
        self.store.update_episodic_tags(episodic.id, tags)

        return episodic

    # ==========================================================
    # 完整的任务后反思流程
    # ==========================================================

    def reflect_on_task(
        self,
        session_id: str,
        task_description: str,
        result_summary: str,
        success: bool,
        error_count: int,
        retry_count: int,
        tool_calls: List[Dict],
        ai_client: Any = None,
        model: str = "deepseek-chat",
        provider: str = "deepseek",
    ) -> Dict:
        """完整的任务后反思流程

        Args:
            session_id: 会话 ID
            task_description: 任务描述（用户请求摘要）
            result_summary: 结果摘要
            success: 是否成功
            error_count: 错误次数
            retry_count: 重试次数
            tool_calls: 工具调用序列
            ai_client: AI 客户端实例（用于 LLM 深度反思）
            model: 反思用的模型
            provider: 反思用的提供商

        Returns:
            反思结果字典
        """
        result = {
            "episodic_id": None,
            "reward": 0.0,
            "importance": 1.0,
            "tags": [],
            "deep_reflected": False,
            "new_rules": [],
        }

        try:
            # 1. 创建 episodic 记忆
            episodic = EpisodicRecord(
                session_id=session_id,
                task_description=task_description,
                actions=[
                    {"name": tc.get("name", ""), "success": tc.get("success", True)}
                    for tc in tool_calls
                ],
                result_summary=result_summary,
                success=success,
                error_count=error_count,
                retry_count=retry_count,
            )

            # 2. 规则反思（提取信号标签）
            episodic = self.rule_reflect(episodic, tool_calls)
            result["tags"] = episodic.tags

            # 3. 写入 episodic 记忆
            self.store.add_episodic(episodic)
            result["episodic_id"] = episodic.id

            # 4. Reward 计算 + importance 更新
            reward_result = self.reward_engine.process_task_completion(
                episodic_record=episodic,
                tool_call_count=len(tool_calls),
            )
            result["reward"] = reward_result["reward"]
            result["importance"] = reward_result["importance"]

            # 5. 更新统计
            self._task_count_since_reflect += 1
            self._recent_error_counts.append(error_count)
            if len(self._recent_error_counts) > self._max_recent:
                self._recent_error_counts = self._recent_error_counts[-self._max_recent:]

            # 6. 判断是否触发 LLM 深度反思
            should_deep_reflect = self._should_deep_reflect()
            if should_deep_reflect and ai_client is not None:
                try:
                    deep_result = self._deep_reflect(ai_client, model, provider)
                    result["deep_reflected"] = True
                    result["new_rules"] = deep_result.get("new_rules", [])
                except Exception as e:
                    print(f"[Reflection] LLM 深度反思失败: {e}")
                    traceback.print_exc()

        except Exception as e:
            print(f"[Reflection] 反思流程出错: {e}")
            traceback.print_exc()

        return result

    # ==========================================================
    # LLM 深度反思
    # ==========================================================

    def _should_deep_reflect(self) -> bool:
        """判断是否应该触发 LLM 深度反思"""
        # 1. 每 N 个任务
        if self._task_count_since_reflect >= DEEP_REFLECT_INTERVAL:
            return True

        # 2. 错误率突增
        if len(self._recent_error_counts) >= 3:
            recent = self._recent_error_counts[-3:]
            error_rate = sum(1 for e in recent if e > 0) / len(recent)
            if error_rate >= ERROR_RATE_SPIKE_THRESHOLD:
                return True

        return False

    def _deep_reflect(self, ai_client: Any, model: str, provider: str) -> Dict:
        """执行 LLM 深度反思

        输入：最近 N 条 episodic memory
        输出：新的 semantic rules + procedural strategy 更新

        Args:
            ai_client: AIClient 实例
            model: 模型名称
            provider: 提供商

        Returns:
            {"new_rules": [...], "strategy_updates": [...]}
        """
        # 重置计数器
        self._task_count_since_reflect = 0

        # 获取最近的 episodic 记忆
        recent_episodes = self.store.get_recent_episodic(limit=DEEP_REFLECT_INTERVAL * 2)
        if not recent_episodes:
            return {"new_rules": []}

        # 构建摘要
        summaries = []
        for i, ep in enumerate(recent_episodes[:10], 1):
            status = "✅ 成功" if ep.success else "❌ 失败"
            tags_str = ", ".join(ep.tags) if ep.tags else "无"
            summaries.append(
                f"{i}. [{status}] 任务: {ep.task_description}\n"
                f"   结果: {ep.result_summary}\n"
                f"   错误次数: {ep.error_count}, 重试: {ep.retry_count}, Reward: {ep.reward_score:.2f}\n"
                f"   标签: {tags_str}"
            )

        episodic_text = "\n\n".join(summaries)
        prompt = REFLECTION_PROMPT.format(episodic_summaries=episodic_text)

        # 调用 LLM
        messages = [
            {"role": "system", "content": "你是一个自我改进的 AI 助手。请用 JSON 格式回答。"},
            {"role": "user", "content": prompt},
        ]

        full_response = ""
        try:
            for chunk in ai_client.chat_stream(
                messages=messages,
                model=model,
                provider=provider,
                temperature=0.3,
                max_tokens=1500,
                tools=None,
                enable_thinking=False,
            ):
                if chunk.get("type") == "content":
                    full_response += chunk.get("content", "")
                elif chunk.get("type") == "error":
                    print(f"[Reflection] LLM 反思错误: {chunk.get('error')}")
                    return {"new_rules": []}
        except Exception as e:
            print(f"[Reflection] LLM 调用失败: {e}")
            return {"new_rules": []}

        # 解析 JSON 响应
        return self._parse_reflection_response(full_response, recent_episodes)

    def _parse_reflection_response(self, response: str, source_episodes: List[EpisodicRecord]) -> Dict:
        """解析 LLM 反思响应并写入记忆"""
        result = {"new_rules": [], "strategy_updates": []}

        # 清理 JSON
        text = response.strip()
        # 移除可能的 markdown 代码块
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:])
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # 尝试提取 JSON 部分
            import re
            json_match = re.search(r'\{[\s\S]*\}', text)
            if json_match:
                try:
                    data = json.loads(json_match.group())
                except json.JSONDecodeError:
                    print(f"[Reflection] 无法解析反思响应")
                    return result
            else:
                print(f"[Reflection] 反思响应中未找到 JSON")
                return result

        source_ids = [ep.id for ep in source_episodes]

        # 1. 处理 semantic rules
        for rule_data in data.get("semantic_rules", []):
            rule_text = rule_data if isinstance(rule_data, str) else rule_data.get("rule", "")
            if not rule_text:
                continue

            category = rule_data.get("category", "general") if isinstance(rule_data, dict) else "general"
            confidence = rule_data.get("confidence", 0.6) if isinstance(rule_data, dict) else 0.6

            # 检查是否已有高度相似的规则
            existing = self.store.find_duplicate_semantic(rule_text, threshold=0.80)
            if existing:
                # 增强已有规则的置信度
                new_conf = min(1.0, existing.confidence + 0.1)
                self.store.update_semantic_confidence(existing.id, new_conf)
                self.store.increment_semantic_activation(existing.id)
                print(f"[Reflection] 强化已有规则: {existing.rule[:50]}... (conf={new_conf:.2f})")
            else:
                # 创建新规则
                record = SemanticRecord(
                    rule=rule_text,
                    source_episodes=source_ids[:5],
                    confidence=confidence,
                    category=category,
                )
                self.store.add_semantic(record)
                result["new_rules"].append(rule_text)
                print(f"[Reflection] 新规则: {rule_text[:50]}...")

        # 2. 处理策略更新
        for update in data.get("strategy_updates", []):
            name = update.get("name", "")
            priority_delta = update.get("priority_delta", 0.0)
            if name and priority_delta != 0:
                existing = self.store.get_procedural_by_name(name)
                if existing:
                    self.store.update_procedural_priority(existing.id, priority_delta)
                    result["strategy_updates"].append(update)
                    print(f"[Reflection] 策略更新: {name} priority += {priority_delta}")

        # 3. 处理技能置信度（存入 growth tracker，通过外部调用）
        skill_conf = data.get("skill_confidence", {})
        if skill_conf:
            result["skill_confidence"] = skill_conf

        return result

    # ==========================================================
    # 工具方法
    # ==========================================================

    def get_reflection_stats(self) -> Dict:
        """获取反思统计信息"""
        return {
            "tasks_since_reflect": self._task_count_since_reflect,
            "recent_errors": self._recent_error_counts[-5:] if self._recent_error_counts else [],
            "next_deep_reflect_in": max(0, DEEP_REFLECT_INTERVAL - self._task_count_since_reflect),
        }


# ============================================================
# 全局单例
# ============================================================

_reflection_instance: Optional[ReflectionModule] = None

def get_reflection_module() -> ReflectionModule:
    """获取全局 ReflectionModule 实例"""
    global _reflection_instance
    if _reflection_instance is None:
        _reflection_instance = ReflectionModule()
    return _reflection_instance
