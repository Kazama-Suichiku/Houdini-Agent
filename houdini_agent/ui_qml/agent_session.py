# -*- coding: utf-8 -*-
"""
UI-agnostic agent backend for the QML frontend.

Wraps the EXISTING backend (AIClient + HoudiniMCP) — no behavioural changes to
either. Owns the conversation history and the system prompt, and exposes a single
`run()` that drives `AIClient.agent_loop_auto(...)` with caller-supplied callbacks.

Threading contract (enforced by the caller, mirroring the old RunMixin):
  - `run()` executes on a BACKGROUND thread.
  - The tool executor registered via `set_tool_executor()` is invoked by AIClient
    on that same background thread; the caller's executor is responsible for
    marshalling Houdini (`hou.*`) calls onto the Qt main thread.
"""

import copy
import re


def _make_system_prompt(with_thinking=True):
    """Reuse the real prompt builder (pure string builder, no UI state)."""
    try:
        from houdini_agent.ui.system_prompt_mixin import SystemPromptMixin

        class _SP(SystemPromptMixin):
            pass

        return _SP()._build_system_prompt(with_thinking=with_thinking, skip_doc_index=False)
    except Exception as e:
        print("[agent_session] system prompt fallback:", e)
        return ("You are an expert Houdini technical artist embedded as an agent. "
                "Use the provided tools to inspect and modify the Houdini scene. "
                "Prefer reading the network before changing it, keep changes undoable, "
                "and verify results before summarizing.")


class AgentSession:
    def __init__(self):
        from houdini_agent.utils.ai_client import AIClient, HOUDINI_TOOLS
        from houdini_agent.utils.mcp import HoudiniMCP

        self.client = AIClient()
        self.mcp = HoudiniMCP()
        # share the stop event so execute_python / execute_shell can be interrupted
        self.mcp.set_stop_event(self.client._stop_event)

        self.tools = list(HOUDINI_TOOLS)
        # Meshy 集成（自包含包，import 即自注册到 ToolRegistry）
        try:
            from houdini_agent import meshy
            self.tools = self.tools + list(meshy.MESHY_TOOLS)
        except Exception as e:
            print("[agent_session] meshy tools unavailable:", e)
        self.history = []  # OpenAI-style message list (system prompt added per run)

        self._sys_think = _make_system_prompt(True)
        self._sys_no_think = _make_system_prompt(False)

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
            images=None, rag=True, memory=False):
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
        messages = self.build_messages(enable_thinking)

        # inject RAG / long-term memory as system context (right after the prompt)
        extra = self._context_messages(user_text, rag, memory)
        if extra:
            messages[1:1] = extra

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
            supports_vision=bool(supports_vision),
            tools_override=tools_override,
            context_limit=context_limit,
            on_content=callbacks.get("on_content"),
            on_thinking=callbacks.get("on_thinking"),
            on_tool_call=callbacks.get("on_tool_call"),
            on_tool_result=callbacks.get("on_tool_result"),
            on_tool_args_delta=callbacks.get("on_tool_args_delta"),
            on_iteration_start=callbacks.get("on_iteration_start"),
        )
        self._absorb(result)
        return result

    def _absorb(self, result):
        """Append the assistant/tool message chain to history (mirrors _on_agent_done)."""
        if not isinstance(result, dict):
            return
        new_messages = result.get("new_messages", []) or []
        for i, nm in enumerate(new_messages):
            # drop the trailing pure-text assistant message; it is re-added below cleanly
            if (i == len(new_messages) - 1 and nm.get("role") == "assistant"
                    and not nm.get("tool_calls")):
                continue
            clean = dict(nm)
            clean.pop("reasoning_content", None)
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
