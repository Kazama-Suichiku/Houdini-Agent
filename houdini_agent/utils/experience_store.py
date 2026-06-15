# -*- coding: utf-8 -*-
"""
Experience candidate store.

This module adds a lightweight review queue on top of the existing memory DB:
conversation snippets become candidates first, then the user can promote,
reject, or keep them for later. Promotion writes into the existing semantic /
procedural memory tables.
"""

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .memory_store import (
    SemanticRecord,
    ProceduralRecord,
    get_memory_store,
)


VALID_STATUSES = ("candidate", "promoted", "rejected", "later")
CANDIDATE_COLUMNS = (
    "id",
    "created_at",
    "session_id",
    "title",
    "signal_type",
    "summary",
    "context",
    "decision",
    "proposed_rule",
    "category",
    "abstraction_level",
    "confidence",
    "status",
    "source_range",
    "promoted_memory_id",
    "detail_path",
    "evidence",
    "promotion_notes",
    "quality_score",
)


@dataclass
class ExperienceCandidate:
    id: str = ""
    created_at: float = 0.0
    session_id: str = ""
    title: str = ""
    signal_type: str = "workflow"
    summary: str = ""
    context: str = ""
    decision: str = ""
    proposed_rule: str = ""
    category: str = "workflow"
    abstraction_level: int = 3
    confidence: float = 0.7
    status: str = "candidate"
    source_range: Dict[str, int] = field(default_factory=dict)
    promoted_memory_id: str = ""
    detail_path: str = ""
    evidence: str = ""
    promotion_notes: str = ""
    quality_score: float = 0.0

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())[:8]
        if self.created_at == 0.0:
            self.created_at = time.time()
        if self.status not in VALID_STATUSES:
            self.status = "candidate"


class ExperienceCandidateStore:
    """SQLite-backed candidate queue using the same DB connection as memory."""

    def __init__(self):
        self.memory_store = get_memory_store()
        self.db_path = self.memory_store.db_path
        self.recipes_dir = self.db_path.parent / "recipes"
        self.recipes_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self):
        return self.memory_store._get_conn()

    def _init_db(self):
        conn = self._conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS experience_candidates (
                id TEXT PRIMARY KEY,
                created_at REAL,
                session_id TEXT,
                title TEXT,
                signal_type TEXT,
                summary TEXT,
                context TEXT,
                decision TEXT,
                proposed_rule TEXT,
                category TEXT,
                abstraction_level INTEGER,
                confidence REAL,
                status TEXT,
                source_range TEXT,
                promoted_memory_id TEXT,
                detail_path TEXT,
                evidence TEXT,
                promotion_notes TEXT,
                quality_score REAL
            );
            CREATE INDEX IF NOT EXISTS idx_exp_candidates_status
                ON experience_candidates(status);
            CREATE INDEX IF NOT EXISTS idx_exp_candidates_session
                ON experience_candidates(session_id);
            CREATE INDEX IF NOT EXISTS idx_exp_candidates_created
                ON experience_candidates(created_at);
        """)
        self._migrate_columns(conn)
        conn.commit()

    @staticmethod
    def _migrate_columns(conn):
        cols = {row[1] for row in conn.execute("PRAGMA table_info(experience_candidates)").fetchall()}
        migrations = {
            "evidence": "ALTER TABLE experience_candidates ADD COLUMN evidence TEXT",
            "promotion_notes": "ALTER TABLE experience_candidates ADD COLUMN promotion_notes TEXT",
            "quality_score": "ALTER TABLE experience_candidates ADD COLUMN quality_score REAL",
        }
        for name, sql in migrations.items():
            if name not in cols:
                conn.execute(sql)

    @staticmethod
    def _columns_sql() -> str:
        return ", ".join(CANDIDATE_COLUMNS)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add_candidate(self, candidate: ExperienceCandidate) -> str:
        conn = self._conn()
        conn.execute(
            """INSERT OR REPLACE INTO experience_candidates
               (id, created_at, session_id, title, signal_type, summary,
                context, decision, proposed_rule, category, abstraction_level,
                confidence, status, source_range, promoted_memory_id, detail_path,
                evidence, promotion_notes, quality_score)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                candidate.id,
                candidate.created_at,
                candidate.session_id,
                candidate.title,
                candidate.signal_type,
                candidate.summary,
                candidate.context,
                candidate.decision,
                candidate.proposed_rule,
                candidate.category,
                int(candidate.abstraction_level),
                float(candidate.confidence),
                candidate.status,
                json.dumps(candidate.source_range or {}, ensure_ascii=False),
                candidate.promoted_memory_id,
                candidate.detail_path,
                candidate.evidence,
                candidate.promotion_notes,
                float(candidate.quality_score),
            ),
        )
        conn.commit()
        return candidate.id

    def get_candidate(self, candidate_id: str) -> Optional[ExperienceCandidate]:
        row = self._conn().execute(
            f"SELECT {self._columns_sql()} FROM experience_candidates WHERE id=?", (candidate_id,)
        ).fetchone()
        return self._row_to_candidate(row) if row else None

    def list_candidates(self, status: str = "active", limit: int = 200) -> List[ExperienceCandidate]:
        conn = self._conn()
        if status == "active":
            rows = conn.execute(
                f"""SELECT {self._columns_sql()} FROM experience_candidates
                   WHERE status IN ('candidate','later')
                   ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        elif status == "all":
            rows = conn.execute(
                f"SELECT {self._columns_sql()} FROM experience_candidates ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        elif status in VALID_STATUSES:
            rows = conn.execute(
                f"""SELECT {self._columns_sql()} FROM experience_candidates
                   WHERE status=? ORDER BY created_at DESC LIMIT ?""",
                (status, limit),
            ).fetchall()
        else:
            rows = []
        return [self._row_to_candidate(r) for r in rows]

    def update_status(self, candidate_id: str, status: str) -> bool:
        if status not in VALID_STATUSES:
            return False
        cur = self._conn().execute(
            "UPDATE experience_candidates SET status=? WHERE id=?",
            (status, candidate_id),
        )
        self._conn().commit()
        return cur.rowcount > 0

    def delete_candidate(self, candidate_id: str) -> bool:
        cur = self._conn().execute(
            "DELETE FROM experience_candidates WHERE id=?",
            (candidate_id,),
        )
        self._conn().commit()
        return cur.rowcount > 0

    def export_curated_experiences(self, output_path: str) -> Dict[str, int]:
        """Export git-friendly reviewed/reflected experience without raw chat context."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        allowed_categories = {"command", "debug", "pitfall", "workflow", "knowledge", "general"}
        semantic_records = [
            rec for rec in self.memory_store.get_all_semantic()
            if rec.category in allowed_categories
            and int(rec.abstraction_level or 0) in (2, 3)
            and float(rec.confidence or 0.0) >= 0.55
        ]
        semantic_records.sort(
            key=lambda r: (
                0 if r.category in ("workflow", "pitfall", "debug") else 1,
                -float(r.confidence or 0.0),
                str(r.rule or ""),
            )
        )

        promoted_candidates = {
            c.promoted_memory_id: c
            for c in self.list_candidates(status="promoted", limit=1000)
            if c.promoted_memory_id
        }

        procedural_records = [
            rec for rec in self.memory_store.get_all_procedural()
            if "review_promoted" in (rec.conditions or [])
        ]

        generated_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        lines = [
            "# Houdini Agent Curated Experiences",
            "",
            f"- Generated: {generated_at}",
            f"- Semantic experiences: {len(semantic_records)}",
            f"- Reviewed workflow strategies: {len(procedural_records)}",
            "",
            "> This export intentionally excludes raw chat context, embeddings, episodic action logs, user profile, and personal preferences.",
            "",
            "## Experience Rules",
            "",
        ]

        current_category = ""
        for rec in semantic_records:
            if rec.category != current_category:
                current_category = rec.category
                lines.extend([f"### {current_category}", ""])
            cand = promoted_candidates.get(rec.id)
            title = cand.title if cand and cand.title else self._rule_title(rec.rule)
            lines.extend([
                f"#### {self._clean_markdown_title(title)}",
                "",
                f"- Category: `{rec.category}`",
                f"- Level: `L{int(rec.abstraction_level or 0)}`",
                f"- Confidence: `{float(rec.confidence or 0.0):.2f}`",
                "",
                self._clean_export_text(rec.rule),
                "",
            ])
            if cand and cand.summary:
                lines.extend(["Summary:", "", self._clean_export_text(cand.summary), ""])
            if cand and cand.evidence:
                lines.extend(["Evidence:", "", self._clean_export_text(cand.evidence), ""])

        if procedural_records:
            lines.extend(["## Reviewed Workflow Strategies", ""])
            for rec in procedural_records:
                lines.extend([
                    f"### {self._clean_markdown_title(rec.strategy_name)}",
                    "",
                    f"- Priority: `{float(rec.priority or 0.0):.2f}`",
                    f"- Success Rate: `{float(rec.success_rate or 0.0):.2f}`",
                    "",
                    self._clean_export_text(rec.description),
                    "",
                ])

        path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
        return {
            "semantic_count": len(semantic_records),
            "procedural_count": len(procedural_records),
        }

    @classmethod
    def _clean_export_text(cls, text: str) -> str:
        text = cls._strip_process_noise(text or "")
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text or "-"

    @classmethod
    def _rule_title(cls, rule: str) -> str:
        text = cls._clean_export_text(rule)
        text = re.sub(r"^(?:Houdini\s*)?(?:调试经验|工作流经验|工作流|经验)[:：]\s*", "", text)
        for sep in ("。", "\n", "；", ";"):
            if sep in text:
                text = text.split(sep, 1)[0]
                break
        return cls._limit_text(text, 56) or "Experience"

    @staticmethod
    def _clean_markdown_title(text: str) -> str:
        text = re.sub(r"[#`*_<>]+", "", text or "").strip()
        text = re.sub(r"\s+", " ", text)
        return text[:80] or "Experience"

    # ------------------------------------------------------------------
    # Candidate extraction
    # ------------------------------------------------------------------

    def create_from_history(self, session_id: str, history: List[dict]) -> Optional[ExperienceCandidate]:
        candidates = self.create_many_from_history(session_id, history)
        return candidates[0] if candidates else None

    def create_many_from_history(self, session_id: str, history: List[dict]) -> List[ExperienceCandidate]:
        messages = [m for m in (history or []) if m.get("role") in ("user", "assistant", "tool")]
        if not messages:
            return []

        distilled_items = self._distill_experiences(messages)
        if not distilled_items:
            return []

        created: List[ExperienceCandidate] = []
        for distilled in distilled_items:
            proposed_rule = distilled["proposed_rule"]
            existing = self._find_existing(session_id, proposed_rule)
            if existing:
                created.append(existing)
                continue

            candidate = ExperienceCandidate(
                session_id=session_id,
                title=distilled["title"],
                signal_type=distilled["signal_type"],
                summary=distilled["summary"],
                context=distilled["context"],
                decision=distilled["decision"],
                proposed_rule=proposed_rule,
                category=distilled["category"],
                abstraction_level=distilled["abstraction_level"],
                confidence=distilled["confidence"],
                status="later" if distilled["quality_score"] < 0.55 else "candidate",
                source_range={"start": max(0, len(messages) - 10), "end": len(messages) - 1},
                evidence=distilled["evidence"],
                promotion_notes=distilled["promotion_notes"],
                quality_score=distilled["quality_score"],
            )
            self.add_candidate(candidate)
            created.append(candidate)
        return created

    def _distill_experiences(self, messages: List[dict]) -> List[Dict[str, object]]:
        base = self._distill_experience(messages)
        if not base:
            return []

        round_messages = self._latest_round(messages)
        context = self._messages_to_text(round_messages[-14:], max_chars=5200)
        atomic_rules = self._extract_atomic_experience_rules(context)
        if not atomic_rules:
            return [base]

        items = []
        seen = set()
        for idx, rule_text in enumerate(atomic_rules[:6], 1):
            abstract_rule = self._abstract_experience_text(rule_text)
            if len(abstract_rule) < 24:
                continue
            signal_type, category, level, inferred_confidence = self._infer_signal(abstract_rule)
            if signal_type == "pattern":
                signal_type = base["signal_type"]
                category = base["category"]
                level = base["abstraction_level"]
            validation = self._extract_labeled_value(context, ("验证", "结果", "完成", "verified", "validation", "success"))
            proposed_rule = self._compose_rule(
                "Houdini 调试经验" if signal_type == "correction" else "Houdini 工作流经验",
                problem=base.get("title", "") or base.get("summary", ""),
                cause="",
                action=abstract_rule,
                validation=validation,
            )
            proposed_rule = self._strip_process_noise(proposed_rule)
            if self._looks_like_raw_reasoning(proposed_rule):
                continue
            key = re.sub(r"\W+", "", proposed_rule.lower())[:180]
            if key in seen:
                continue
            seen.add(key)
            item = dict(base)
            item.update(
                {
                    "signal_type": signal_type,
                    "category": category,
                    "abstraction_level": min(level, 3),
                    "confidence": min(0.96, max(base["confidence"], inferred_confidence)),
                    "title": self._build_title(abstract_rule, abstract_rule),
                    "summary": self._compose_atomic_summary(abstract_rule, validation),
                    "decision": self._build_distilled_decision(
                        signal_type,
                        category,
                        min(level, 3),
                        min(0.96, max(base["confidence"], inferred_confidence)),
                        True,
                        max(base["quality_score"], 0.66),
                        ["原子经验候选", "已从会话过程抽象为可复用原则"],
                    ),
                    "proposed_rule": proposed_rule,
                    "context": self._compose_context("", [rule_text], context),
                    "evidence": self._limit_text(rule_text, 320),
                    "promotion_notes": "按单条经验审阅；拒绝后保留在已拒绝列表，可回看并手动删除。",
                    "quality_score": max(base["quality_score"], 0.66),
                }
            )
            items.append(item)

        return items or [base]

    def _distill_experience(self, messages: List[dict]) -> Optional[Dict[str, object]]:
        round_messages = self._latest_round(messages)
        last_user = self._clean_experience_text(self._last_text(round_messages, "user"), max_chars=900)
        last_assistant = self._clean_experience_text(self._last_text(round_messages, "assistant"), max_chars=1800)
        if not (last_user or last_assistant):
            return None

        context = self._messages_to_text(round_messages[-14:], max_chars=5200)
        signal_type, category, level, base_confidence = self._infer_signal("\n".join([last_user, last_assistant, context]))

        evidence = self._extract_evidence_lines("\n".join([last_assistant, context]))
        if not evidence and len(last_assistant) < 100:
            return None

        problem = self._summarize_problem(last_user, context)
        cause = self._extract_labeled_value(context, ("原因", "根因", "问题", "失败原因", "错误原因", "because", "cause"))
        fix = self._extract_labeled_value(context, ("修复", "解决", "改为", "正确做法", "建议", "fix", "use", "set", "change"))
        validation = self._extract_labeled_value(context, ("验证", "结果", "当前状态", "检查通过", "完成", "verified", "validation", "success"))

        if not cause and signal_type == "correction":
            cause = self._first_matching_line(evidence, ("原因", "根因", "错误", "失败", "wrong", "failed"))
        if not fix:
            fix = self._first_matching_line(evidence, ("修复", "改为", "正确", "应", "使用", "设置", "fix", "connect", "set", "use"))
        if not fix:
            fix = self._derive_action(problem, last_assistant, evidence)
        if not validation:
            validation = self._first_matching_line(evidence, ("验证", "完成", "成功", "保存", "0 个错误", "无错误", "verified", "validation", "success", "done"))
        if not validation:
            validation = self._derive_validation(evidence, context)

        quality_score, quality_reasons = self._score_candidate(
            signal_type=signal_type,
            problem=problem,
            cause=cause,
            action=fix,
            validation=validation,
            evidence=evidence,
            raw_text=context,
        )
        if quality_score < 0.42:
            return None

        confidence = min(0.96, max(0.35, base_confidence + (quality_score - 0.55) * 0.28))
        action_source = fix or self._join_evidence(evidence, 3)
        abstract_action = self._abstract_experience_text(action_source)
        if not abstract_action and problem:
            abstract_action = (
                "Summarize the reusable principle for this scenario, then keep only "
                "the scenario, repair principle, and validation method."
            )
        if signal_type == "preference":
            proposed_rule = self._limit_text(f"用户偏好：{problem}", 520)
        elif signal_type == "correction":
            proposed_rule = self._compose_rule(
                "Houdini 调试经验",
                problem=problem,
                cause=cause,
                action=abstract_action,
                validation=validation,
            )
        else:
            proposed_rule = self._compose_rule(
                "Houdini 工作流经验",
                problem=problem,
                cause=cause,
                action=abstract_action,
                validation=validation,
            )

        proposed_rule = self._strip_process_noise(proposed_rule)
        if len(proposed_rule) < 32 or self._looks_like_raw_reasoning(proposed_rule):
            return None

        title = self._build_title(problem, fix or cause or proposed_rule)
        summary = self._compose_summary(problem, cause, abstract_action, validation, evidence)
        promotion_notes = self._build_promotion_notes(quality_score, quality_reasons)
        decision = self._build_distilled_decision(
            signal_type,
            category,
            level,
            confidence,
            bool(fix or evidence),
            quality_score,
            quality_reasons,
        )
        distilled_context = self._compose_context(problem, evidence, context)
        distilled_evidence = self._join_evidence(evidence, 6)

        return {
            "signal_type": signal_type,
            "category": category,
            "abstraction_level": level,
            "confidence": confidence,
            "title": title,
            "summary": summary,
            "decision": decision,
            "proposed_rule": proposed_rule,
            "context": distilled_context,
            "evidence": distilled_evidence,
            "promotion_notes": promotion_notes,
            "quality_score": quality_score,
        }

    @classmethod
    def _extract_atomic_experience_rules(cls, text: str) -> List[str]:
        """Extract review units as reusable lessons, not whole chat sessions."""
        text = cls._strip_process_noise(text or "")
        lines = []
        label_re = re.compile(
            r"(?:经验|原则|原理|复盘|反省|教训|修复原理|正确做法|通用做法|避免|以后|lesson|principle|rule|takeaway|fix principle)\s*[:：\-]\s*(.+)",
            flags=re.I,
        )
        for raw in re.split(r"[\n\r]+|(?<=[。；;.!?])\s+", text):
            line = cls._clean_evidence_line(raw)
            if len(line) < 12 or len(line) > 360:
                continue
            m = label_re.search(line)
            if m:
                lines.append(m.group(1).strip())
                continue
            lower = line.lower()
            if any(k in lower for k in ("should", "must", "avoid", "prefer", "verify", "validate")):
                if any(k in lower for k in ("workflow", "houdini", "node", "vex", "sop", "error", "bug", "fix")):
                    lines.append(line)
                    continue
            if any(k in line for k in ("应当", "必须", "不要", "避免", "优先", "先验证", "再验证", "正确做法")):
                lines.append(line)

        unique = []
        seen = set()
        for line in lines:
            rule = cls._abstract_experience_text(line)
            key = re.sub(r"\W+", "", rule.lower())[:140]
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(rule)
        return unique

    @classmethod
    def _abstract_experience_text(cls, text: str) -> str:
        text = cls._strip_process_noise(text or "")
        text = re.sub(r"^(?:assistant|user|tool)\s*:\s*", "", text.strip(), flags=re.I)
        text = re.sub(r"^\s*(?:[-*•]|\d+[.)、])\s*", "", text)
        text = re.sub(r"\[(?:Plan Confirmed|auto visual checkpoint)[^\]]*\]\s*", "", text, flags=re.I)
        text = re.sub(r"\bI\s+(?:will|would|should|need to|have to)\b", "The workflow should", text, flags=re.I)
        text = re.sub(r"\bI\s+(?:created|changed|fixed|verified|used)\b", "Use", text, flags=re.I)
        text = re.sub(r"(?:我已经|我会|我先|我需要|我们已经|这里已经)", "", text)
        text = re.sub(r"\s+", " ", text).strip(" ：:-")
        noise_markers = ("[first principles]", "[understand]", "[plan]", "<think>", "updated todo")
        lower = text.lower()
        if any(m in lower for m in noise_markers):
            return ""
        return cls._limit_text(text, 260)

    @classmethod
    def _compose_atomic_summary(cls, rule: str, validation: str) -> str:
        lines = [f"经验原则：{cls._limit_text(rule, 260)}"]
        if validation:
            lines.append(f"验证方式：{cls._limit_text(validation, 220)}")
        return "\n".join(lines)

    @staticmethod
    def _content_to_text(content) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append(str(item.get("text", "")))
                    elif "text" in item:
                        parts.append(str(item.get("text", "")))
                else:
                    parts.append(str(item))
            return "\n".join(p for p in parts if p)
        return str(content or "")

    @classmethod
    def _last_text(cls, messages: List[dict], role: str) -> str:
        for msg in reversed(messages):
            if msg.get("role") == role:
                return cls._content_to_text(msg.get("content", ""))
        return ""

    @classmethod
    def _messages_to_text(cls, messages: List[dict], max_chars: int = 3600) -> str:
        lines = []
        for msg in messages:
            role = msg.get("role", "")
            text = cls._content_to_text(msg.get("content", ""))
            text = cls._strip_process_noise(text).strip()
            if text:
                lines.append(f"{role}: {text}")
        out = "\n\n".join(lines)
        return out[-max_chars:]

    @staticmethod
    def _clean_text(text: str, max_chars: int = 260) -> str:
        text = re.sub(r"<think>[\s\S]*?</think>", "", text or "")
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]

    @classmethod
    def _short_title(cls, text: str) -> str:
        text = cls._clean_text(text, 80)
        return text[:42] + ("..." if len(text) > 42 else "")

    @staticmethod
    def _infer_signal(text: str) -> Tuple[str, str, int, float]:
        lower = text.lower()
        if any(k in lower for k in ("preference", "prefer", "偏好", "习惯", "以后都", "默认")):
            return "preference", "preference", 1, 0.78
        correction_probe = re.sub(r"\bno errors?\b|无错误|没有错误|没有报错", "", lower)
        if any(k in correction_probe for k in (
            "error", "failed", "wrong", "bug", "pitfall", "corrected", "correction",
            "misdiagnosis", "misdiagnosed", "mistake", "fix", "修复", "失败",
            "错误", "踩坑", "纠正", "误判",
        )):
            return "correction", "pitfall", 2, 0.76
        if any(k in lower for k in ("workflow", "node", "vex", "sop", "solver", "wrangle", "connect", "工作流", "节点", "连接", "流程")):
            return "workflow", "workflow", 3, 0.72
        return "pattern", "workflow", 3, 0.68

    @staticmethod
    def _build_decision(signal_type: str, summary: str) -> str:
        if signal_type == "preference":
            return "将用户偏好沉淀为可复用交互规则。"
        if signal_type == "correction":
            return "将修正过程沉淀为踩坑/调试经验。"
        return "将当前 Houdini 操作流程沉淀为可复用工作流。"

    @staticmethod
    def _build_rule(signal_type: str, summary: str) -> str:
        prefix = {
            "preference": "用户偏好：",
            "correction": "Houdini 调试经验：",
            "workflow": "Houdini 工作流：",
            "pattern": "Houdini 工作流模式：",
        }.get(signal_type, "Houdini 经验：")
        return (prefix + summary)[:220]

    @staticmethod
    def _latest_round(messages: List[dict]) -> List[dict]:
        if not messages:
            return []
        last_user_idx = -1
        for idx in range(len(messages) - 1, -1, -1):
            if messages[idx].get("role") == "user":
                last_user_idx = idx
                break
        if last_user_idx < 0:
            return messages[-10:]
        return messages[last_user_idx:]

    @classmethod
    def _clean_experience_text(cls, text: str, max_chars: int = 1200) -> str:
        text = cls._strip_process_noise(text or "")
        text = re.sub(r"\[Tool Result\][\s\S]*?(?=\n\S|$)", "", text)
        text = re.sub(r"```(?:text|json|python|vex)?", "```", text, flags=re.I)
        text = re.sub(r"\s+", " ", text).strip()
        return cls._limit_text(text, max_chars)

    @staticmethod
    def _strip_process_noise(text: str) -> str:
        text = re.sub(r"<think>[\s\S]*?</think>", "", text or "", flags=re.I)
        lines = []
        noisy_prefixes = (
            "[first principles]",
            "[understand]",
            "[status]",
            "[options]",
            "[decision]",
            "[plan]",
            "[risk]",
            "first principles",
            "thinking",
            "思考",
            "我先",
            "我会",
        )
        noisy_fragments = (
            "你的下一条回复必须以",
            "必须以 <think>",
            "必须以<think>",
            "不要跳过 <think>",
            "不要跳过<think>",
            "在标签内分析以上执行结果",
            "检查 todo 列表中哪些步骤已完成",
            "update_todo 标记为 done",
            "确认下一步计划后再继续执行",
            "output format",
            "deep thinking framework",
            "[context reminder]",
            "[上下文提醒]",
        )
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            lower = line.lower()
            if lower.startswith(noisy_prefixes):
                continue
            if any(fragment in lower for fragment in noisy_fragments):
                continue
            if lower.startswith(("tool: updated todo", "tool: todo", "tool: update_todo")):
                continue
            if re.match(r"^(?:step|步骤)\s*\d+[:：]", lower):
                continue
            lines.append(line)
        return "\n".join(lines).strip()

    @staticmethod
    def _looks_like_raw_reasoning(text: str) -> bool:
        lower = (text or "").lower()
        markers = (
            "[first principles]",
            "[understand]",
            "[options]",
            "[risk]",
            "<think>",
            "我需要先",
            "我应该",
        )
        return any(m in lower for m in markers)

    @classmethod
    def _extract_evidence_lines(cls, text: str) -> List[str]:
        text = cls._strip_process_noise(text or "")
        candidates = []
        keywords = (
            "原因", "根因", "问题", "错误", "失败", "修复", "解决", "改为", "正确",
            "验证", "结果", "完成", "成功", "无错误", "检查通过", "实际", "应该",
            "节点工作正常", "networkbox", "viewport", "frame set", "已创建", "已保存",
            "because", "cause", "error", "failed", "fix", "use", "set", "connect",
            "verified", "success", "done", "passed", "verify",
        )
        chunks = re.split(r"[\n。；;]+|(?<=[.!?])\s+(?=[A-Z][A-Za-z ]{0,24}:)", text)
        for raw in chunks:
            line = cls._clean_evidence_line(re.sub(r"\s+", " ", raw).strip(" -\t"))
            if len(line) < 8 or len(line) > 220:
                continue
            lower = line.lower()
            if "updated todo" in lower or "update_todo" in lower:
                continue
            if "你的下一条回复必须" in line or "<think>" in line:
                continue
            if any(k in lower for k in keywords):
                candidates.append(line)
        unique = []
        seen = set()
        for line in candidates:
            key = line.lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(line)
            if len(unique) >= 10:
                break
        return sorted(unique, key=cls._evidence_priority)

    @staticmethod
    def _evidence_priority(line: str) -> int:
        lower = line.lower()
        if "networkbox" in lower or "已创建" in lower or "created " in lower or "connect" in lower:
            return 0
        if "check passed" in lower or "检查通过" in lower or "节点工作正常" in lower or "no errors" in lower or "无错误" in lower:
            return 1
        if "verified" in lower or "验证" in lower or "frame set" in lower or "viewport" in lower:
            return 2
        return 3

    @staticmethod
    def _clean_evidence_line(line: str) -> str:
        line = re.sub(r"^(?:assistant|user|tool)\s*:\s*", "", line.strip(), flags=re.I)
        line = re.sub(r"^\[重要[^\]]*\]\s*", "", line)
        line = re.sub(r"\s*##\s*.+$", "", line)
        line = re.sub(r"\s+", " ", line).strip()
        return line

    @classmethod
    def _summarize_problem(cls, last_user: str, context: str) -> str:
        source = last_user or context
        source = cls._strip_process_noise(source)
        source = re.sub(r"^(请|帮我|麻烦|需求|用户要求)[:：\s]+", "", source).strip()
        chunks = re.split(r"[。！？!?]\s*|\n+", source)
        for chunk in chunks:
            chunk = re.sub(r"\s+", " ", chunk).strip()
            if len(chunk) >= 8:
                return cls._limit_text(chunk, 180)
        return cls._limit_text(source, 180)

    @classmethod
    def _extract_labeled_value(cls, text: str, labels: Tuple[str, ...]) -> str:
        text = cls._strip_process_noise(text or "")
        for label in labels:
            pattern = (
                rf"(?:^|[\n。；;]|[.!?]\s+)\s*(?:(?:assistant|user|tool)\s*:\s*)?"
                rf"(?:\*\*)?{re.escape(label)}(?:\*\*)?\s*[:：\-]\s*(.+?)(?=$|[\n。；;])"
            )
            m = re.search(pattern, text, flags=re.I)
            if m:
                value = m.group(1).strip()
                value = re.split(
                    r"\s+(?:原因|根因|问题|失败原因|错误原因|修复|解决|改为|正确做法|"
                    r"验证|结果|当前状态|检查通过|完成|because|cause|fix|use|set|"
                    r"change|verified|validation|result|success)\s*[:：\-]",
                    value,
                    maxsplit=1,
                    flags=re.I,
                )[0]
                return cls._limit_text(value.strip(), 220)
        return ""

    @classmethod
    def _derive_action(cls, problem: str, assistant_text: str, evidence: List[str]) -> str:
        candidates = []
        for line in evidence:
            lower = line.lower()
            if "networkbox" in lower or "已创建" in lower or "connect" in lower:
                candidates.append(line)
        if candidates:
            return cls._limit_text(candidates[0], 240)

        assistant_text = cls._strip_process_noise(assistant_text)
        for raw in re.split(r"[\n。；;]+", assistant_text):
            line = cls._clean_evidence_line(raw)
            lower = line.lower()
            if len(line) < 8:
                continue
            if any(k in lower for k in ("验证", "verified", "frame set", "progress", "截图", "viewport")):
                continue
            if any(k in lower for k in ("构建完成", "完成", "workflow", "connect", "创建", "生成", "setup", "build")):
                candidates.append(line)
        if candidates:
            return cls._limit_text(candidates[0], 240)

        problem = cls._limit_text(problem, 160)
        if problem:
            return f"围绕“{problem}”保留可复用的节点流程、关键控制参数和最终验证方式。"
        return ""

    @classmethod
    def _derive_validation(cls, evidence: List[str], context: str) -> str:
        joined = "\n".join(evidence) + "\n" + cls._strip_process_noise(context)
        validation_lines = []
        for raw in re.split(r"[\n。；;]+", joined):
            line = cls._clean_evidence_line(raw)
            lower = line.lower()
            if any(k in lower for k in ("检查通过", "节点工作正常", "无错误", "check passed", "no errors", "success", "passed", "verify_and_summarize")):
                validation_lines.append(line)
            elif "frame set" in lower or "viewport" in lower or "截图" in lower:
                validation_lines.append(line)
        if not validation_lines:
            return ""
        return cls._limit_text("；".join(validation_lines[:2]), 220)

    @classmethod
    def _first_matching_line(cls, lines: List[str], keywords: Tuple[str, ...]) -> str:
        for line in lines:
            lower = line.lower()
            if any(k in lower for k in ("verified", "验证", "frame set", "check passed", "检查通过", "no errors", "无错误")):
                if not any(k in lower for k in ("fix", "修复", "改为", "正确做法", "connect", "连接", "已创建", "networkbox")):
                    continue
            if any(k in lower for k in keywords):
                return cls._limit_text(line, 220)
        return ""

    @classmethod
    def _join_evidence(cls, evidence: List[str], limit: int = 4) -> str:
        selected = [cls._limit_text(x, 220) for x in (evidence or []) if x]
        return "\n".join(f"- {x}" for x in selected[:limit])

    @classmethod
    def _compose_rule(
        cls,
        label: str,
        problem: str,
        cause: str,
        action: str,
        validation: str,
    ) -> str:
        parts = [f"{label}："]
        if problem:
            parts.append(f"适用场景：{cls._limit_text(problem, 160)}。")
        if cause:
            parts.append(f"有效判断：{cls._limit_text(cause, 180)}。")
        if action:
            action_text = re.sub(
                r"^\s*(?:[-•]\s*)?(?:workflow|steps?|做法|步骤)\s*[:：\-]\s*",
                "",
                action.strip(),
                flags=re.I,
            )
            action_text = cls._normalize_action_text(action_text)
            parts.append(f"正确做法：{cls._limit_text(action_text, 240)}。")
        if validation:
            parts.append(f"验证方式：{cls._limit_text(validation, 160)}。")
        return cls._limit_text("".join(parts), 760)

    @classmethod
    def _compose_summary(
        cls,
        problem: str,
        cause: str,
        fix: str,
        validation: str,
        evidence: List[str],
    ) -> str:
        lines = []
        if fix:
            lines.append(f"结论：{cls._limit_text(cls._normalize_action_text(fix), 260)}")
        if problem:
            lines.append(f"适用场景：{cls._limit_text(problem, 180)}")
        if cause:
            lines.append(f"关键判断：{cls._limit_text(cause, 220)}")
        if validation:
            lines.append(f"验证方式：{cls._limit_text(validation, 220)}")
        return "\n".join(lines)

    @classmethod
    def _build_title(cls, problem: str, knowledge: str) -> str:
        base = problem or knowledge or "Houdini 经验"
        base = re.sub(r"\s+", " ", base).strip()
        if len(base) > 34:
            base = base[:34].rstrip() + "..."
        return base

    @staticmethod
    def _normalize_action_text(text: str) -> str:
        text = re.sub(r"^(?:tool|assistant)\s*:\s*", "", text.strip(), flags=re.I)
        m = re.search(r"created\s+NetworkBox:\s*([^,，]+)[,，]\s*contains\s*(\d+)\s*nodes?", text, flags=re.I)
        if m:
            return f"Organize the completed workflow into NetworkBox `{m.group(1).strip()}` and keep {m.group(2)} key nodes for reuse and review."
        m = re.search(r"已创建\s+NetworkBox:\s*([^，,]+)[，,]\s*包含\s*(\d+)\s*个节点", text, flags=re.I)
        if m:
            return f"将完成的流程打包为 NetworkBox `{m.group(1).strip()}`，保留 {m.group(2)} 个关键节点，便于复用和复查。"
        m = re.search(r"NetworkBox:\s*([^，,]+)[，,]\s*包含\s*(\d+)\s*个节点", text, flags=re.I)
        if m:
            return f"将相关节点组织进 NetworkBox `{m.group(1).strip()}`，保留 {m.group(2)} 个关键节点作为可复用流程。"
        return text

    @classmethod
    def _build_distilled_decision(
        cls,
        signal_type: str,
        category: str,
        level: int,
        confidence: float,
        has_actionable_content: bool,
        quality_score: float,
        quality_reasons: List[str],
    ) -> str:
        if quality_score >= 0.72:
            verdict = "建议晋升：上下文里有明确场景、可复用做法和验证信号。"
        elif quality_score >= 0.55:
            verdict = "建议人工复核后晋升：已有可复用知识，但证据或验证信号不够完整。"
        else:
            verdict = "建议稍后：候选里仍有上下文缺口，暂不应直接写入长期记忆。"
        if not has_actionable_content:
            verdict = "建议稍后：尚未提取到明确可执行做法。"
        reasons = "；".join(quality_reasons[:4]) or "无明确质量信号"
        return (
            f"{verdict}\n"
            f"目标分类：{category} / L{level}；置信度：{confidence:.2f}；质量分：{quality_score:.2f}。\n"
            f"判断依据：{reasons}"
        )

    @staticmethod
    def _build_promotion_notes(quality_score: float, reasons: List[str]) -> str:
        if quality_score >= 0.72:
            action = "优先晋升"
        elif quality_score >= 0.55:
            action = "复核后晋升"
        else:
            action = "暂存稍后"
        reason_text = "；".join(reasons[:5]) if reasons else "缺少明确证据"
        return f"{action}｜质量分 {quality_score:.2f}｜{reason_text}"

    @classmethod
    def _compose_context(cls, problem: str, evidence: List[str], context: str) -> str:
        lines = []
        if problem:
            lines.append(f"来源问题：{cls._limit_text(problem, 220)}")
        if evidence:
            lines.append("关键依据：")
            lines.extend(f"- {cls._limit_text(line, 220)}" for line in evidence[:6])
        source = cls._strip_process_noise(context)
        snippets = []
        for raw in source.splitlines():
            line = cls._clean_evidence_line(raw)
            if len(line) < 8:
                continue
            lower = line.lower()
            if "updated todo" in lower or "update_todo" in lower:
                continue
            snippets.append(line)
            if len(snippets) >= 5:
                break
        if snippets:
            lines.append("去噪上下文片段：")
            lines.extend(f"- {cls._limit_text(line, 180)}" for line in snippets)
        return "\n".join(lines)

    @staticmethod
    def _score_candidate(
        signal_type: str,
        problem: str,
        cause: str,
        action: str,
        validation: str,
        evidence: List[str],
        raw_text: str,
    ) -> Tuple[float, List[str]]:
        score = 0.18
        reasons = []
        if problem and len(problem) >= 8:
            score += 0.14
            reasons.append("有明确场景")
        if cause:
            score += 0.16
            reasons.append("包含原因/判断")
        if action:
            score += 0.24
            reasons.append("包含正确做法")
        if validation:
            score += 0.18
            reasons.append("包含验证信号")
        if len(evidence or []) >= 2:
            score += 0.10
            reasons.append("有多条上下文证据")
        if signal_type in ("correction", "workflow", "pattern"):
            score += 0.06
            reasons.append("类型可复用")
        if ExperienceCandidateStore._looks_like_raw_reasoning(raw_text):
            score -= 0.20
            reasons.append("含过程噪声")
        if not (action or evidence):
            score -= 0.22
        return max(0.0, min(1.0, score)), reasons

    @staticmethod
    def _limit_text(text: str, max_chars: int = 260) -> str:
        text = re.sub(r"\s+", " ", text or "").strip()
        if len(text) <= max_chars:
            return text
        return text[: max(0, max_chars - 3)].rstrip() + "..."

    def _find_existing(self, session_id: str, proposed_rule: str) -> Optional[ExperienceCandidate]:
        row = self._conn().execute(
            f"""SELECT {self._columns_sql()} FROM experience_candidates
               WHERE session_id=? AND proposed_rule=? AND status!='rejected'
               ORDER BY created_at DESC LIMIT 1""",
            (session_id, proposed_rule),
        ).fetchone()
        return self._row_to_candidate(row) if row else None

    # ------------------------------------------------------------------
    # Promotion
    # ------------------------------------------------------------------

    def promote(self, candidate_id: str) -> Tuple[str, str]:
        candidate = self.get_candidate(candidate_id)
        if not candidate:
            raise ValueError("Candidate not found")
        if candidate.status == "promoted" and candidate.promoted_memory_id:
            return candidate.promoted_memory_id, candidate.detail_path

        category, level = self._promotion_target(candidate)
        existing = self.memory_store.find_duplicate_semantic(candidate.proposed_rule, threshold=0.80)
        if existing:
            memory_id = existing.id
            self.memory_store.update_semantic_confidence(
                existing.id, min(1.0, max(existing.confidence, candidate.confidence) + 0.05)
            )
            self.memory_store.increment_semantic_activation(existing.id)
        else:
            record = SemanticRecord(
                rule=candidate.proposed_rule,
                source_episodes=[candidate.id],
                confidence=candidate.confidence,
                category=category,
                abstraction_level=level,
            )
            memory_id = self.memory_store.add_semantic(record)

        if category == "workflow" and level >= 3:
            self._upsert_procedural(candidate)

        detail_path = self._write_detail(candidate, memory_id, category, level)
        self._conn().execute(
            """UPDATE experience_candidates
               SET status='promoted', promoted_memory_id=?, detail_path=?
               WHERE id=?""",
            (memory_id, detail_path, candidate.id),
        )
        self._conn().commit()
        return memory_id, detail_path

    @staticmethod
    def _promotion_target(candidate: ExperienceCandidate) -> Tuple[str, int]:
        if candidate.signal_type in ("workflow", "pattern") or candidate.category == "workflow":
            level = 3 if candidate.quality_score >= 0.62 else 2
            return "workflow", level
        if candidate.signal_type == "correction":
            return "pitfall", 2 if candidate.quality_score < 0.78 else 3
        if candidate.signal_type == "preference":
            return "preference", 1
        return candidate.category or "general", candidate.abstraction_level or 2

    def _upsert_procedural(self, candidate: ExperienceCandidate):
        name = self._strategy_name(candidate.title or candidate.id)
        existing = self.memory_store.get_procedural_by_name(name)
        if existing:
            self.memory_store.update_procedural_usage(existing.id, success=True)
            self.memory_store.update_procedural_priority(existing.id, 0.03)
            return
        self.memory_store.add_procedural(
            ProceduralRecord(
                strategy_name=name,
                description=candidate.proposed_rule[:260],
                priority=0.55 + min(0.25, max(0.0, candidate.quality_score - 0.55)),
                success_rate=0.65 + min(0.25, max(0.0, candidate.quality_score - 0.55)),
                usage_count=1,
                conditions=[candidate.signal_type, candidate.category, "review_promoted"],
            )
        )

    @staticmethod
    def _strategy_name(title: str) -> str:
        text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", title).strip("_")
        if not text:
            text = "workflow_experience"
        return "reviewed_" + text[:48].lower()

    def _write_detail(self, candidate: ExperienceCandidate, memory_id: str, category: str, level: int) -> str:
        ts = time.strftime("%Y-%m-%d_%H%M%S", time.localtime(candidate.created_at))
        safe_title = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "-", candidate.title)[:48].strip("-")
        filename = f"{ts}_{candidate.id}_{safe_title or 'experience'}.md"
        path = self.recipes_dir / filename
        body = [
            f"# {candidate.title or candidate.id}",
            "",
            f"- Candidate ID: `{candidate.id}`",
            f"- Session ID: `{candidate.session_id}`",
            f"- Signal Type: `{candidate.signal_type}`",
            f"- Category: `{category}`",
            f"- Abstraction Level: `{level}`",
            f"- Confidence: `{candidate.confidence:.2f}`",
            f"- Quality Score: `{candidate.quality_score:.2f}`",
            f"- Promoted Memory ID: `{memory_id}`",
            "",
            "## Decision",
            candidate.decision or "",
            "",
            "## Promotion Notes",
            candidate.promotion_notes or "",
            "",
            "## Proposed Rule",
            candidate.proposed_rule or "",
            "",
            "## Summary",
            candidate.summary or "",
            "",
            "## Evidence",
            candidate.evidence or "",
            "",
            "## Source Context",
            "```text",
            candidate.context or "",
            "```",
            "",
        ]
        path.write_text("\n".join(body), encoding="utf-8")
        return str(path)

    @staticmethod
    def _row_to_candidate(row) -> ExperienceCandidate:
        data = dict(zip(CANDIDATE_COLUMNS, row))
        return ExperienceCandidate(
            id=data.get("id") or "",
            created_at=data.get("created_at") or 0.0,
            session_id=data.get("session_id") or "",
            title=data.get("title") or "",
            signal_type=data.get("signal_type") or "workflow",
            summary=data.get("summary") or "",
            context=data.get("context") or "",
            decision=data.get("decision") or "",
            proposed_rule=data.get("proposed_rule") or "",
            category=data.get("category") or "workflow",
            abstraction_level=data.get("abstraction_level") if data.get("abstraction_level") is not None else 3,
            confidence=data.get("confidence") if data.get("confidence") is not None else 0.7,
            status=data.get("status") or "candidate",
            source_range=json.loads(data.get("source_range") or "{}"),
            promoted_memory_id=data.get("promoted_memory_id") or "",
            detail_path=data.get("detail_path") or "",
            evidence=data.get("evidence") or "",
            promotion_notes=data.get("promotion_notes") or "",
            quality_score=data.get("quality_score") if data.get("quality_score") is not None else 0.0,
        )


_experience_store: Optional[ExperienceCandidateStore] = None


def get_experience_store() -> ExperienceCandidateStore:
    global _experience_store
    if _experience_store is None:
        _experience_store = ExperienceCandidateStore()
    return _experience_store
