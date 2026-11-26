# -*- coding: utf-8 -*-
"""
UI-agnostic agent backend for the QML frontend.

Provides `BaseAgentSession` (shared turn execution, history management,
ephemeral RAG/memory injection, compaction write-back) and `AgentSession`
(in-process HoudiniMCP backend). `BridgeAgentSession` subclasses
`BaseAgentSession` to route Houdini tools over TCP bridge without duplicating
session logic.
"""

import copy
import re


def _make_system_prompt(with_thinking=True):
    """Build the canonical Houdini TA system prompt without depending on legacy UI."""
    try:
        from houdini_agent.core.system_prompt import build_system_prompt
        return build_system_prompt(with_thinking=with_thinking, skip_doc_index=False)
    except Exception as e:
        print("[agent_session] system prompt fallback:", e)
        return ("You are an expert Houdini technical artist embedded as an agent. "
                "Use the provided tools to inspect and modify the Houdini scene. "
                "Prefer reading the network before changing it, keep changes undoable, "
                "and verify results before summarizing.")


class BaseAgentSession:
    """Shared session lifecycle for in-process MCP and standalone Bridge modes."""

    def _init_common(self, system_note=""):
        from houdini_agent.utils.ai_client import AIClient, HOUDINI_TOOLS

        self.client = AIClient()
        self.tools = list(HOUDINI_TOOLS)
        # Meshy 集成（自包含包，import 即自注册到 ToolRegistry）
        try:
            from houdini_agent import meshy
            self.tools = self.tools + list(meshy.MESHY_TOOLS)
        except Exception as e:
            print(f"[{self.__class__.__name__}] meshy tools unavailable:", e)

        self.history = []  # OpenAI-style message list (system prompt added per run)
        self._sys_think = _make_system_prompt(True) + system_note
        self._sys_no_think = _make_system_prompt(False) + system_note

    # ---- wiring ----
    def set_tool_executor(self, fn):
        """fn(tool_name, **kwargs) -> dict — provided by the Qt controller."""
        self.client.set_tool_executor(fn)

    def stop(self):
        self.client.request_stop()

    # ---- messages ----
    def build_messages(self, with_thinking):
        sys = self._sys_think if with_thinking else self._sys_no_think
        return [{"role": "system", "content": sys}] + copy.deepcopy(self.history)

    # ---- run one user turn ----
    def run(self, user_text, model, provider, mode, callbacks, context_limit=128000,
            enable_thinking=True, supports_vision=False, tools=None, max_iter=None,
            think_tags=True,
            images=None, rag=True, memory=False,
            initial_actual_tokens=0, state_context=""):
        self.client.reset_stop()
        incoming_images = images or []
        usable_images = incoming_images if supports_vision else []
        if usable_images:
            content = self._build_content(user_text, usable_images)
        elif incoming_images and not supports_vision:
            content = (user_text or "") + "\n\n[系统提示：当前模型不支持图片输入，用户附加的图片已忽略。]"
        else:
            content = user_text
        self.history.append({"role": "user", "content": content})
        # think_tags=False：模型有原生推理摘要（如 ChatGPT 订阅），不再要求正文里写 <think>
        messages = self.build_messages(enable_thinking and think_tags)

        # 对齐 Claude Agent SDK：动态 RAG / 记忆只追加到本轮末尾的 user 消息，
        # 绝不插在历史前缀 messages[1:1]，保证整段历史 Prompt Cache 100% 稳定命中！
        extra = self._context_messages(user_text, rag, memory)
        if extra:
            self._append_ephemeral_context(messages, extra)

        if max_iter is None:
            max_iter = 15 if mode == "Ask" else 999
        tools_override = tools if tools is not None else self.tools
        # 非视觉模型默认禁用视口截图检查工具（它依赖把图片回注给模型分析）
        if not supports_vision and tools_override:
            tools_override = [
                t for t in tools_override
                if (t.get("function") or {}).get("name") != "capture_viewport"
            ]

        result = self.client.agent_loop_auto(
            messages=messages,
            model=model,
            provider=provider,
            max_iterations=max_iter,
            max_tokens=None,
            enable_thinking=enable_thinking,
            think_tags=bool(think_tags),
            supports_vision=bool(supports_vision),
            tools_override=tools_override,
            context_limit=context_limit,
            initial_actual_tokens=int(initial_actual_tokens or 0),
            state_context=state_context or "",
            on_content=callbacks.get("on_content"),
            on_thinking=callbacks.get("on_thinking"),
            on_tool_call=callbacks.get("on_tool_call"),
            on_tool_result=callbacks.get("on_tool_result"),
            on_tool_args_delta=callbacks.get("on_tool_args_delta"),
            on_iteration_start=callbacks.get("on_iteration_start"),
            on_compaction=callbacks.get("on_compaction"),
        )
        self._absorb(result)
        return result

    def _absorb(self, result):
        """Append the assistant/tool message chain to history (or write back compressed history)."""
        if not isinstance(result, dict):
            return
        compressed = result.get("compressed_history")
        if isinstance(compressed, list) and compressed:
            rebuilt = []
            for i, nm in enumerate(compressed):
                if (i == len(compressed) - 1 and nm.get("role") == "assistant"
                        and not nm.get("tool_calls")):
                    continue
                clean = dict(nm)
                clean.pop("reasoning_content", None)
                clean.pop("_anthropic_thinking_blocks", None)
                rebuilt.append(clean)
            self.history = rebuilt
        else:
            new_messages = result.get("new_messages", []) or []
            for i, nm in enumerate(new_messages):
                # drop the trailing pure-text assistant message; it is re-added below cleanly
                if (i == len(new_messages) - 1 and nm.get("role") == "assistant"
                        and not nm.get("tool_calls")):
                    continue
                clean = dict(nm)
                clean.pop("reasoning_content", None)
                clean.pop("_anthropic_thinking_blocks", None)
                self.history.append(clean)

        final = result.get("final_content", "") or ""
        clean = re.sub(r"<think>[\s\S]*?</think>", "", final).strip()
        if clean or not self.history or self.history[-1].get("role") != "assistant":
            self.history.append({"role": "assistant", "content": clean or "(no content)"})

    def reset(self):
        self.history = []

    # ---- helpers ----
    @staticmethod
    def _build_content(text, images):
        """OpenAI multimodal content if images present, else a plain string.
        images: list of (b64, media_type)."""
        if not images:
            return text
        parts = [{"type": "text", "text": text}]
        for b64, mt in images:
            parts.append({"type": "image_url",
                          "image_url": {"url": "data:%s;base64,%s" % (mt, b64)}})
        return parts

    @staticmethod
    def _append_ephemeral_context(messages, extra):
        """将每轮动态检索的 RAG / 记忆附加到最后一条 user 消息末尾，不破坏历史 Prompt Cache 前缀。"""
        if not messages or not extra:
            return
        chunks = [str(m.get("content", "")).strip() for m in extra if isinstance(m, dict) and m.get("content")]
        if not chunks:
            return
        block = "\n\n<retrieved_context>\n" + "\n\n".join(chunks) + "\n</retrieved_context>"
        target = messages[-1]
        if target.get("role") != "user":
            return
        c = target.get("content")
        if isinstance(c, list):
            target["content"] = list(c) + [{"type": "text", "text": block}]
        else:
            target["content"] = (str(c) if c is not None else "") + block

    @staticmethod
    def _context_messages(query, rag, memory):
        out = []
        if rag:
            try:
                from houdini_agent.utils.doc_rag import get_doc_index
                s = get_doc_index().auto_retrieve(query, max_chars=1200)
                if s:
                    out.append({"role": "system", "content": s})
            except Exception:
                pass
        if memory:
            try:
                from houdini_agent.utils.memory_store import get_memory_store
                ms = get_memory_store()
                parts = []
                for r in ms.get_core_memories(5):
                    parts.append("- " + str(getattr(r, "rule_text", r)))
                for r, _score in ms.search_episodic(query, top_k=3):
                    parts.append("- " + str(getattr(r, "task", r)))
                if parts:
                    out.append({"role": "system", "content": "长期记忆参考:\n" + "\n".join(parts)})
            except Exception:
                pass
        return out


class AgentSession(BaseAgentSession):
    """In-process Houdini panel session (executes tools via local HoudiniMCP)."""

    def __init__(self):
        from houdini_agent.utils.mcp import HoudiniMCP

        self._init_common()
        self.mcp = HoudiniMCP()
        # share the stop event so execute_python / execute_shell can be interrupted
        self.mcp.set_stop_event(self.client._stop_event)
