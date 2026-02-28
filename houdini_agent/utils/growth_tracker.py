# -*- coding: utf-8 -*-
"""
成长追踪 + 个性形成 (Growth Tracker + Personality Profile)

核心公式: Growth(t) = -d(Error)/dt
长期预测误差下降 = 成长

追踪指标（滚动窗口统计）：
- error_rate:  最近 N 个任务的错误率趋势
- success_rate: 成功率趋势
- avg_tool_calls: 平均工具调用次数趋势 (下降 = 更高效)
- avg_retries: 平均重试次数趋势
- skill_confidence: 各领域技能置信度

个性 = 策略强化的长期累积结果
"""

import json
import math
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from .memory_store import MemoryStore, get_memory_store

# ============================================================
# 持久化路径
# ============================================================

_GROWTH_FILE = Path(__file__).parent.parent.parent / "cache" / "memory" / "growth_profile.json"

# ============================================================
# 滚动窗口大小
# ============================================================

WINDOW_SIZE = 30  # 最近 N 个任务的滚动窗口


@dataclass
class TaskMetric:
    """单个任务的度量数据"""
    timestamp: float = 0.0
    success: bool = True
    error_count: int = 0
    retry_count: int = 0
    tool_call_count: int = 0
    reward: float = 0.0
    tags: List[str] = field(default_factory=list)


@dataclass
class PersonalityTraits:
    """个性特征（由 reward 偏向长期累积形成）"""
    efficiency_bias: float = 0.0     # >0 冷静理性, <0 探索创新
    risk_tolerance: float = 0.5      # 高=大胆尝试, 低=保守稳定
    verbosity: float = 0.5           # 回复详细度偏好
    proactivity: float = 0.5         # 主动提供建议 vs 只回答问题

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict) -> "PersonalityTraits":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class GrowthTracker:
    """成长追踪器 + 个性形成

    记录每个任务的度量指标，计算趋势，形成个性特征。
    """

    def __init__(self, store: Optional[MemoryStore] = None):
        self.store = store or get_memory_store()

        # 滚动窗口
        self._metrics: deque = deque(maxlen=WINDOW_SIZE * 2)  # 保留 2 倍以计算趋势

        # 技能置信度
        self._skill_confidence: Dict[str, float] = {
            "vex": 0.5,
            "node_creation": 0.5,
            "terrain": 0.5,
            "copernicus": 0.5,
            "general": 0.5,
        }

        # 个性特征
        self.personality = PersonalityTraits()

        # 总任务计数
        self._total_tasks: int = 0

        # 加载持久化数据
        self._load()

    # ==========================================================
    # 记录任务度量
    # ==========================================================

    def record_task(self, metric: TaskMetric):
        """记录一个任务的度量数据"""
        if metric.timestamp == 0.0:
            metric.timestamp = time.time()

        self._metrics.append(metric)
        self._total_tasks += 1

        # 更新技能置信度
        self._update_skill_confidence(metric)

        # 更新个性
        self._update_personality(metric)

        # 自动保存
        self._save()

    # ==========================================================
    # 趋势计算
    # ==========================================================

    def get_growth_metrics(self) -> Dict:
        """获取成长指标

        Returns:
            {
                "error_rate": float,           # 当前错误率
                "error_rate_trend": float,      # 错误率趋势 (负 = 改善)
                "success_rate": float,          # 当前成功率
                "success_rate_trend": float,    # 成功率趋势 (正 = 改善)
                "avg_tool_calls": float,        # 平均工具调用次数
                "avg_retries": float,           # 平均重试次数
                "growth_score": float,          # 综合成长分数
                "total_tasks": int,             # 总任务数
            }
        """
        if not self._metrics:
            return {
                "error_rate": 0.0,
                "error_rate_trend": 0.0,
                "success_rate": 1.0,
                "success_rate_trend": 0.0,
                "avg_tool_calls": 0.0,
                "avg_retries": 0.0,
                "growth_score": 0.0,
                "total_tasks": self._total_tasks,
            }

        metrics = list(self._metrics)
        n = len(metrics)
        half = n // 2

        # 当前窗口 (后半部分)
        recent = metrics[half:] if half > 0 else metrics
        # 历史窗口 (前半部分)
        older = metrics[:half] if half > 0 else []

        # 当前指标
        error_rate = sum(1 for m in recent if m.error_count > 0) / max(len(recent), 1)
        success_rate = sum(1 for m in recent if m.success) / max(len(recent), 1)
        avg_tool_calls = sum(m.tool_call_count for m in recent) / max(len(recent), 1)
        avg_retries = sum(m.retry_count for m in recent) / max(len(recent), 1)

        # 趋势 (与旧窗口对比)
        if older:
            old_error_rate = sum(1 for m in older if m.error_count > 0) / max(len(older), 1)
            old_success_rate = sum(1 for m in older if m.success) / max(len(older), 1)
            error_rate_trend = error_rate - old_error_rate   # 负 = 改善
            success_rate_trend = success_rate - old_success_rate  # 正 = 改善
        else:
            error_rate_trend = 0.0
            success_rate_trend = 0.0

        # 综合成长分数 = -d(Error)/dt (简化版)
        growth_score = -error_rate_trend + success_rate_trend

        return {
            "error_rate": round(error_rate, 3),
            "error_rate_trend": round(error_rate_trend, 3),
            "success_rate": round(success_rate, 3),
            "success_rate_trend": round(success_rate_trend, 3),
            "avg_tool_calls": round(avg_tool_calls, 1),
            "avg_retries": round(avg_retries, 1),
            "growth_score": round(growth_score, 3),
            "total_tasks": self._total_tasks,
        }

    # ==========================================================
    # 技能置信度
    # ==========================================================

    def _update_skill_confidence(self, metric: TaskMetric):
        """根据任务标签更新技能置信度"""
        alpha = 0.1  # 学习率

        # 根据 tags 判断涉及的技能领域
        skill_map = {
            "vex_related": "vex",
            "node_creation": "node_creation",
            "terrain_related": "terrain",
            "copernicus_related": "copernicus",
        }

        affected_skills = set()
        for tag in metric.tags:
            skill = skill_map.get(tag)
            if skill:
                affected_skills.add(skill)

        # 始终更新 general
        affected_skills.add("general")

        for skill in affected_skills:
            current = self._skill_confidence.get(skill, 0.5)
            target = 1.0 if metric.success else 0.0
            # 滑动平均
            new_val = (1 - alpha) * current + alpha * target
            self._skill_confidence[skill] = round(max(0.0, min(1.0, new_val)), 3)

    def update_skill_confidence_batch(self, updates: Dict[str, float]):
        """批量更新技能置信度（来自 LLM 反思）"""
        for skill, confidence in updates.items():
            # 与当前值做加权平均（避免 LLM 一次性大幅修改）
            current = self._skill_confidence.get(skill, 0.5)
            blended = 0.7 * current + 0.3 * confidence
            self._skill_confidence[skill] = round(max(0.0, min(1.0, blended)), 3)
        self._save()

    def get_skill_confidence(self) -> Dict[str, float]:
        """获取所有技能置信度"""
        return dict(self._skill_confidence)

    # ==========================================================
    # 个性形成
    # ==========================================================

    def _update_personality(self, metric: TaskMetric):
        """根据任务结果逐渐形成个性

        个性 = 策略强化的长期累积结果
        """
        alpha = 0.05  # 个性变化率（慢，需要长期积累）

        # 效率偏向
        if metric.success and metric.tool_call_count <= 3:
            # 高效成功 → 效率偏向增加
            self.personality.efficiency_bias += alpha
        elif not metric.success and metric.retry_count > 2:
            # 失败重试多 → 效率偏向降低（需要更多探索）
            self.personality.efficiency_bias -= alpha

        # 风险容忍度
        if "error_correction" in metric.tags:
            # 犯错后纠正 → 提高风险容忍度
            self.personality.risk_tolerance = min(1.0, self.personality.risk_tolerance + alpha)
        elif "unresolved_error" in metric.tags:
            # 未解决的错误 → 降低风险容忍度
            self.personality.risk_tolerance = max(0.0, self.personality.risk_tolerance - alpha)

        # 主动性
        if "complex_task" in metric.tags and metric.success:
            # 复杂任务成功 → 提高主动性
            self.personality.proactivity = min(1.0, self.personality.proactivity + alpha * 0.5)

        # 限制范围
        self.personality.efficiency_bias = max(-1.0, min(1.0, self.personality.efficiency_bias))

    def get_personality(self) -> PersonalityTraits:
        """获取当前个性特征"""
        return self.personality

    def get_personality_description(self) -> str:
        """生成个性描述文本（注入 system prompt）"""
        p = self.personality
        skills = self._skill_confidence

        # 效率偏向描述
        if p.efficiency_bias > 0.3:
            style = "效率优先, 偏向简洁直接的解决方案"
        elif p.efficiency_bias < -0.3:
            style = "探索创新, 偏向尝试多种方案"
        else:
            style = "均衡风格, 兼顾效率与探索"

        # 风险描述
        if p.risk_tolerance > 0.7:
            risk = "高风险容忍度"
        elif p.risk_tolerance < 0.3:
            risk = "低风险容忍度, 偏保守"
        else:
            risk = "中等风险容忍度"

        # 技能描述
        skill_parts = []
        for skill_name, conf in sorted(skills.items(), key=lambda x: -x[1]):
            if conf > 0.1:
                skill_parts.append(f"{skill_name}: {conf:.2f}")
        skills_text = ", ".join(skill_parts) if skill_parts else "暂无数据"

        return (
            f"[Self-Awareness] 当前风格偏好: {style}, {risk}。\n"
            f"技能置信度: {skills_text}"
        )

    # ==========================================================
    # 持久化
    # ==========================================================

    def _save(self):
        """保存成长数据到文件"""
        try:
            _GROWTH_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "total_tasks": self._total_tasks,
                "skill_confidence": self._skill_confidence,
                "personality": self.personality.to_dict(),
                "metrics": [
                    {
                        "timestamp": m.timestamp,
                        "success": m.success,
                        "error_count": m.error_count,
                        "retry_count": m.retry_count,
                        "tool_call_count": m.tool_call_count,
                        "reward": m.reward,
                        "tags": m.tags,
                    }
                    for m in self._metrics
                ],
            }
            with open(_GROWTH_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[GrowthTracker] 保存失败: {e}")

    def _load(self):
        """从文件加载成长数据"""
        if not _GROWTH_FILE.exists():
            return
        try:
            with open(_GROWTH_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            self._total_tasks = data.get("total_tasks", 0)
            self._skill_confidence.update(data.get("skill_confidence", {}))
            self.personality = PersonalityTraits.from_dict(data.get("personality", {}))

            for m_data in data.get("metrics", []):
                self._metrics.append(TaskMetric(
                    timestamp=m_data.get("timestamp", 0),
                    success=m_data.get("success", True),
                    error_count=m_data.get("error_count", 0),
                    retry_count=m_data.get("retry_count", 0),
                    tool_call_count=m_data.get("tool_call_count", 0),
                    reward=m_data.get("reward", 0),
                    tags=m_data.get("tags", []),
                ))

            print(f"[GrowthTracker] 已加载成长数据: {self._total_tasks} tasks, "
                  f"personality={self.personality.to_dict()}")
        except Exception as e:
            print(f"[GrowthTracker] 加载失败: {e}")

    # ==========================================================
    # 综合报告
    # ==========================================================

    def get_full_report(self) -> Dict:
        """获取完整的成长报告"""
        return {
            "growth_metrics": self.get_growth_metrics(),
            "skill_confidence": self.get_skill_confidence(),
            "personality": self.personality.to_dict(),
            "personality_description": self.get_personality_description(),
        }


# ============================================================
# 全局单例
# ============================================================

_tracker_instance: Optional[GrowthTracker] = None

def get_growth_tracker() -> GrowthTracker:
    """获取全局 GrowthTracker 实例"""
    global _tracker_instance
    if _tracker_instance is None:
        _tracker_instance = GrowthTracker()
    return _tracker_instance
