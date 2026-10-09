"""答复 Agent 的答复逻辑：把 DataPart 载荷组装成对话消息，交给 LLM 生成中文答复。

与工单 04 契约（票 06 §3）对齐：输入 {query, chunks, history}，输出最终答复文本。
提示词来自 config/agents/reply.yaml（票 09 登记）；LLM 走 common.llm_client（带退避重试）。
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Protocol

logger = logging.getLogger(__name__)


class ChatClient(Protocol):
    """答复需要的最小 LLM 能力：给一组消息，返回文本。"""

    async def chat(self, messages: list[dict[str, str]]) -> str: ...


def _format_context(chunks: list[dict[str, Any]]) -> str:
    if not chunks:
        return "（无检索结果）"
    blocks = []
    for i, c in enumerate(chunks, start=1):
        text = c.get("text", "")
        source = c.get("source_url") or c.get("source") or ""
        blocks.append(f"[{i}] {text}\n（来源：{source}）")
    return "\n\n".join(blocks)


def _format_memory_block(memory: dict[str, Any]) -> str:
    """把长期记忆/偏好渲染成一小段可注入提示词的文本；无内容返回空串。"""
    if not memory:
        return ""
    lines: list[str] = []
    profile_text = (memory.get("profile_text") or "").strip()
    if profile_text:
        lines.append("【用户偏好】" + profile_text)
    recent = [str(q).strip() for q in (memory.get("recent") or []) if str(q).strip()]
    if recent:
        lines.append("【最近咨询】" + " / ".join(recent))
    return "\n".join(lines)


_CHITCHAT_DIRECTIVE = (
    "\n\n【本轮为闲聊/寒暄】请忽略上面「必须依据参考资料、资料不足则转人工」的约束，"
    "自然、友好、简短地回应用户即可；若用户设了偏好风格则沿用。"
)


def build_reply_messages(
    payload: dict[str, Any], system_prompt: str
) -> list[dict[str, str]]:
    """把 {query, chunks, history, memory?, mode?} 组装成 OpenAI 风格消息列表。

    顺序：system（含可选偏好/记忆块；chitchat 时附闲聊指令）→ history（仅 user/assistant）
    → 末尾一条 user（含参考资料 + 当前问题；chitchat 时只放用户消息）。
    """
    query = payload.get("query", "") or ""
    chunks = payload.get("chunks") or []
    history = payload.get("history") or []
    mode = payload.get("mode")

    sys_content = system_prompt
    if mode == "chitchat":
        sys_content += _CHITCHAT_DIRECTIVE
    mem_block = _format_memory_block(payload.get("memory"))
    if mem_block:
        sys_content = f"{sys_content}\n\n{mem_block}"

    messages: list[dict[str, str]] = [{"role": "system", "content": sys_content}]

    for turn in history:
        role = turn.get("role")
        text = turn.get("text", "")
        if role in ("user", "assistant") and text:
            messages.append({"role": role, "content": text})

    if mode == "chitchat":
        messages.append({"role": "user", "content": f"用户消息：{query}"})
        return messages

    user_content = f"用户问题：{query}\n\n参考资料：\n{_format_context(chunks)}"
    messages.append({"role": "user", "content": user_content})
    return messages


class ReplyAgent:
    """答复 Agent：组装消息 + 调 LLM。"""

    def __init__(self, llm: ChatClient, prompts: dict[str, Any]) -> None:
        self._llm = llm
        self._system = prompts.get("system", "")

    async def generate(self, payload: dict[str, Any]) -> str:
        messages = build_reply_messages(payload, self._system)
        logger.info("答复生成 query_len=%d chunks=%d history=%d",
                    len(payload.get("query", "") or ""),
                    len(payload.get("chunks") or []),
                    len(payload.get("history") or []))
        return await self._llm.chat(messages)

    async def generate_stream(self, payload: dict[str, Any]) -> AsyncIterator[str]:
        """流式生成答复：逐段产出文本增量。

        LLM 客户端支持 chat_stream 时真流式；否则（fake/无流式能力）退化为
        一次性产出完整文本的单个增量，保持与既有注入接口的向后兼容。
        """
        messages = build_reply_messages(payload, self._system)
        logger.info("答复流式生成 query_len=%d chunks=%d history=%d",
                    len(payload.get("query", "") or ""),
                    len(payload.get("chunks") or []),
                    len(payload.get("history") or []))
        if hasattr(self._llm, "chat_stream"):
            async for delta in self._llm.chat_stream(messages):
                yield delta
        else:  # 无流式能力：整段文本当作单个增量吐出（向后兼容）
            yield await self._llm.chat(messages)
