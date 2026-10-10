"""分流判定（工单 05，票 06 §6；工单：扩为四类意图）。

用 LLM 按提示词把输入分到 chitchat|knowledge|transaction|out_of_scope，输出
{intent, confidence, reason}，并派生 is_service=(intent != out_of_scope)。解析稳健：
LLM 异常或解析不出枚举时保守当 knowledge（走安全全链路，宁多查不误拒）。
兼容旧输出（带 is_service 布尔、无枚举 intent）。提示词来自 config/agents/reception.yaml。
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

_JSON_RE = re.compile(r"\{.*\}", re.S)

INTENTS = ("chitchat", "knowledge", "transaction", "out_of_scope")


def _parse_verdict(raw: str) -> dict:
    """从模型输出里稳健抽取一个 JSON 对象。"""
    if not raw:
        return {}
    candidates = _JSON_RE.findall(raw)
    for cand in reversed(candidates):
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except (ValueError, TypeError):
            continue
    return {}


def _confidence(verdict: dict) -> float:
    try:
        return float(verdict.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


class TriageAgent:
    def __init__(self, llm, prompts: dict) -> None:
        self._llm = llm
        self._prompt = prompts.get("triage", "").strip()

    async def classify(self, text: str) -> dict:
        messages = [
            {"role": "system", "content": self._prompt},
            {"role": "user", "content": text},
        ]
        try:
            raw = await self._llm.chat(messages)
        except Exception as exc:  # noqa: BLE001 - 判定失败保守当 knowledge（走安全全链路）
            logger.error("分流判定失败，保守放行 err=%s", exc)
            return {"intent": "knowledge", "is_service": True, "confidence": 0.0, "reason": "triage-error"}

        verdict = _parse_verdict(raw)
        intent = str(verdict.get("intent", "")).strip().lower()

        if intent in INTENTS:
            return {
                "intent": intent,
                "is_service": intent != "out_of_scope",
                "confidence": _confidence(verdict),
                "reason": str(verdict.get("reason", "")),
            }

        # 向后兼容：旧输出带 is_service 布尔、intent 是自由标签
        if "is_service" in verdict:
            is_service = bool(verdict.get("is_service"))
            return {
                "intent": intent or ("knowledge" if is_service else "out_of_scope"),
                "is_service": is_service,
                "confidence": _confidence(verdict),
                "reason": str(verdict.get("reason", "")),
            }

        logger.warning("分流输出无法识别，保守当 knowledge raw=%r", raw[:80])
        return {"intent": "knowledge", "is_service": True, "confidence": 0.0, "reason": "triage-unparsed"}
