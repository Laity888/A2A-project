"""接待 Agent 编排核心（工单 05 起，工单 12 升级为 ReAct + MCP 工具；工单：改造为流式事件源）。

依赖全部注入（llm / triage / tool_broker / reply_client / session），便于离线测试：
  分流 → 非客服兜底(不触发工具/下游)；
  客服 → ReAct 循环（经 MCP tool_broker 自主选并调工具，kb_search 桥检索 A2A→RAG）
        → 用工具证据(chunks) 流式调答复 Agent 合成 grounded 答复 → 追加会话 → 答案 + trace。
任一环节异常统一走兜底、任务仍 COMPLETED（票 06 §7）。

`stream()` 逐段产出内部事件，供 executor 翻成 A2A 中间状态 + 流式 artifact：
  {"ev":"status","label":str,"phase":str,"round"?:int,"name"?:str}
  {"ev":"sources","data":{"sources":[{source,source_url,category,score}...],"count":int}}  # 答复生成前
  {"ev":"token","text":str}      # 答复正文增量
  {"ev":"trace","data":dict}     # 协作过程 trace
`handle()` 保留为排空包装（向后兼容），返回 {answer,is_service,intent,trace}。

模块分工（ADR-0003 分流四类分治 + ADR-0002 答复流式 落地）：
  `stream()` = 只负责共 setup + 按意图分派，不再直接放事件源；
  `_emit_cache_hit / _emit_fallback / _emit_chitchat / _emit_semantic_hit / _emit_main_pipeline`
  = 每个分支一个 async 子生成器，只"为自己那一路的变化"而变（Divergent Change 拆分）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any, AsyncIterator

from agents.reception.react import ReActAgent

logger = logging.getLogger(__name__)


def _summarize_args(args: dict) -> str:
    try:
        return json.dumps(args, ensure_ascii=False)[:120]
    except (TypeError, ValueError):
        return str(args)[:120]


def _pref_sig(profile: dict | None) -> str:
    """把用户偏好字典压成稳定短签名；无偏好→空串。换任一偏好即变签名→缓存 miss→按新偏好重生成。"""
    if not profile:
        return ""
    items = "|".join(f"{k}={profile[k]}" for k in sorted(profile))
    return hashlib.sha1(items.encode("utf-8")).hexdigest()[:16]


def _make_trace(
    *,
    is_service: bool,
    intent: str,
    timings: dict[str, Any],
    tool_calls: list[dict] | None = None,
    retrieval: dict[str, Any] | None = None,
    cache_hit: bool | None = None,
    cache_level: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """接待 trace dict 单一构造点（Duplicated Code 消除）。

    必带：is_service / intent / tool_calls（默认空列表）/ timings；
    可选：retrieval / cache_hit / cache_level / error——只在 caller 显式传时才入 dict，
    避免异常兜底路径被凭空塞一个 `retrieval: {count:0, chunks:[]}`（保持既有 shape）。
    下游只用 `[]` / `.get()` 读，不敏感键顺序。
    """
    trace: dict[str, Any] = {
        "is_service": is_service,
        "intent": intent,
        "tool_calls": tool_calls or [],
        "timings": timings,
    }
    if retrieval is not None:
        trace["retrieval"] = retrieval
    if cache_hit is not None:
        trace["cache_hit"] = cache_hit
    if cache_level is not None:
        trace["cache_level"] = cache_level
    if error is not None:
        trace["error"] = error
    return trace


class ReceptionService:
    def __init__(
        self, *, llm, triage, tool_broker, reply_client, session,
        agent_prompt: str, fallback_text: str, max_steps: int = 4, max_tool_calls: int | None = None,
        answer_cache=None, semantic_cache=None, memory=None,
    ) -> None:
        self._llm = llm
        self._triage = triage
        self._broker = tool_broker
        self._reply = reply_client
        self._session = session
        self._agent_prompt = agent_prompt
        self._fallback = fallback_text
        self._cache = answer_cache
        self._scache = semantic_cache
        self._memory = memory
        self._react = ReActAgent(
            llm=llm, broker=tool_broker, system_prompt=agent_prompt,
            max_steps=max_steps, max_tool_calls=max_tool_calls,
        )

    async def stream(self, text: str, context_id: str, user_id: str | None = None) -> AsyncIterator[dict[str, Any]]:
        """流式编排入口：共 setup + 按意图分派到分支子生成器。

        分支顺序（ADR-0003）：
          L1 答案缓存 → 非客服/out_of_scope 兜底 → chitchat 轻量答复 → L2 语义缓存 → 主链路。
        每个分支只吐自己那一路的事件；stream() 本身不再持有任何具体阶段逻辑。
        """
        t0 = time.perf_counter()
        sess_key = f"{user_id}:{context_id}" if user_id else context_id

        # 先吐首个状态，让前端第一时间拿到第一帧反馈（不必等会话/偏好读完）。
        yield {"ev": "status", "phase": "triage", "label": "正在判断问题类型…"}

        # 会话历史与用户偏好互不依赖 → 并发读，省掉一次串行等待（更早进入下一步）。
        # profile 既算缓存签名（换风格即 miss 重生成），也供后续注入。
        history, profile = await asyncio.gather(
            self._session.history(sess_key), self._load_profile(user_id)
        )
        standalone = not history  # 仅独立提问才「写」缓存（避免把带上下文的追问答案缓存下来）
        pref_sig = _pref_sig(profile)

        # L1 答案缓存命中：跳过 triage/ReAct/检索/生成整条链路。
        # 「读」不加 standalone 限制——缓存里只存过独立自洽问题，命中即当前问句与之同义，可安全返回。
        # 按 user_id + 偏好签名作用域，不跨用户/跨风格串答案。
        if self._cache is not None:
            cached = self._cache.get(text, user_id, pref_sig=pref_sig)
            if cached is not None:
                async for ev in self._emit_cache_hit(sess_key, text, cached, user_id, context_id, t0):
                    yield ev
                return

        verdict = await self._triage.classify(text) or {}
        is_service = bool(verdict.get("is_service", True))
        intent = str(verdict.get("intent", ""))
        t_tri = time.perf_counter()

        if not is_service or intent == "out_of_scope":
            async for ev in self._emit_fallback(
                sess_key, text, intent, verdict.get("reason", ""), t0, t_tri
            ):
                yield ev
            return

        if intent == "chitchat":
            async for ev in self._emit_chitchat(
                text, context_id, user_id, history, profile, sess_key, t0, t_tri
            ):
                yield ev
            return

        # L2 语义缓存：仅非交易类（transaction 的结果不缓存）。命中 → 回填 L1 并直接返回。
        if self._scache is not None and intent != "transaction":
            t_sc = time.perf_counter()
            sem = await self._lookup_semantic_cache(text, user_id, pref_sig)
            if sem:
                scache_ms = round((time.perf_counter() - t_sc) * 1000, 1)
                async for ev in self._emit_semantic_hit(
                    sess_key, text, sem, user_id, context_id, standalone, pref_sig, t0, t_tri, scache_ms
                ):
                    yield ev
                return

        # 主链路：ReAct + 检索证据 + 答复流式 + trace 组装 + 缓存/记忆写回。
        memory_ctx = await self._build_memory_ctx(profile, user_id)
        async for ev in self._emit_main_pipeline(
            text, context_id, user_id, history, intent, memory_ctx,
            sess_key, standalone, pref_sig, t0, t_tri,
        ):
            yield ev

    # ---------- 分支子生成器（每路只因自己变化）----------

    async def _emit_cache_hit(
        self, sess_key: str, text: str, cached: str,
        user_id: str | None, context_id: str, t0: float,
    ) -> AsyncIterator[dict[str, Any]]:
        """L1 答案缓存命中：吐 status + token + trace 三段，落会话与 query_history。"""
        logger.info("答案缓存命中 user_id=%s context_id=%s", user_id, context_id)
        trace = _make_trace(
            is_service=True, intent="cache", cache_hit=True,
            retrieval={"count": 0, "chunks": []},
            timings={"cache_hit_ms": round((time.perf_counter() - t0) * 1000, 1)},
        )
        await self._session.append(sess_key, text, cached, trace=trace)
        await self._append_history(user_id, "cache", text, context_id)
        yield {"ev": "status", "phase": "cache", "label": "命中缓存，直接返回…"}
        yield {"ev": "token", "text": cached}
        yield {"ev": "trace", "data": trace}

    async def _emit_fallback(
        self, sess_key: str, text: str, intent: str, reason: str,
        t0: float, t_tri: float,
    ) -> AsyncIterator[dict[str, Any]]:
        """非客服 / out_of_scope：直接兜底，不触发工具与下游；仍落会话历史（票 06 §6/§7）。"""
        logger.info("非客服/越界，兜底 intent=%s reason=%s", intent, reason)
        trace = _make_trace(
            is_service=False, intent=intent,
            retrieval={"count": 0, "chunks": []},
            timings={"triage_ms": round((t_tri - t0) * 1000, 1)},
        )
        await self._session.append(sess_key, text, self._fallback, trace=trace)
        yield {"ev": "token", "text": self._fallback}
        yield {"ev": "trace", "data": trace}

    async def _emit_chitchat(
        self, text: str, context_id: str, user_id: str | None,
        history: list[dict], profile: dict[str, str], sess_key: str,
        t0: float, t_tri: float,
    ) -> AsyncIterator[dict[str, Any]]:
        """闲聊：跳检索与缓存，走轻量答复（仍带偏好风格）；不写 query_history、不进 L1/L2。"""
        memory_ctx = await self._build_memory_ctx(profile, user_id)
        yield {"ev": "status", "phase": "answer", "label": "正在回复…"}
        parts: list[str] = []
        try:
            async for delta in self._reply.reply_stream(
                text, [], history, context_id=context_id, memory=memory_ctx, mode="chitchat"
            ):
                if delta:
                    parts.append(delta)
                    yield {"ev": "token", "text": delta}
        except Exception as exc:  # noqa: BLE001 - 闲聊生成失败兜底一句
            logger.error("闲聊答复失败 err=%s", exc)
            if not parts:
                parts.append(self._fallback)
                yield {"ev": "token", "text": self._fallback}
        answer = "".join(parts) or self._fallback
        trace = _make_trace(
            is_service=True, intent="chitchat",
            retrieval={"count": 0, "chunks": []},
            timings={"triage_ms": round((t_tri - t0) * 1000, 1)},
        )
        await self._session.append(sess_key, text, answer, trace=trace)
        yield {"ev": "trace", "data": trace}

    async def _emit_semantic_hit(
        self, sess_key: str, text: str, sem: str,
        user_id: str | None, context_id: str, standalone: bool, pref_sig: str,
        t0: float, t_tri: float, scache_ms: float,
    ) -> AsyncIterator[dict[str, Any]]:
        """L2 语义缓存命中：回填 L1（便于后续精确重复走进程内快路径）+ 落会话与 query_history。"""
        if self._cache is not None and standalone:
            self._cache.set(text, sem, user_id, pref_sig=pref_sig)
        logger.info("语义缓存命中 user_id=%s context_id=%s", user_id, context_id)
        trace = _make_trace(
            is_service=True, intent="cache", cache_hit=True, cache_level="semantic",
            retrieval={"count": 0, "chunks": []},
            timings={
                "triage_ms": round((t_tri - t0) * 1000, 1),
                "scache_ms": scache_ms,
            },
        )
        await self._session.append(sess_key, text, sem, trace=trace)
        await self._append_history(user_id, "cache", text, context_id)
        yield {"ev": "status", "phase": "cache", "label": "命中语义缓存，直接返回…"}
        yield {"ev": "token", "text": sem}
        yield {"ev": "trace", "data": trace}

    async def _emit_main_pipeline(
        self, text: str, context_id: str, user_id: str | None,
        history: list[dict], intent: str, memory_ctx: dict[str, Any] | None,
        sess_key: str, standalone: bool, pref_sig: str,
        t0: float, t_tri: float,
    ) -> AsyncIterator[dict[str, Any]]:
        """主链路：ReAct 循环 → 补检索兜底 → sources 早现 → 答复流式 → trace 组装 → 写缓存/历史。

        异常路径统一走 `_emit_error_fallback`（票 06 §7：兜底仍 COMPLETED）。
        """
        yield {"ev": "status", "phase": "react", "label": "正在拆解问题、准备检索…"}
        evidence: list[dict] = []
        react_stats: dict = {}
        try:
            async for ev in self._react.run_stream(text, history, context_id=context_id):
                et = ev.get("type")
                if et == "tool_start":
                    yield {
                        "ev": "status", "phase": "tool",
                        "round": ev.get("round"), "name": ev.get("name"),
                        "label": f"调用工具 {ev.get('name', '')}…",
                    }
                elif et == "tool_done":
                    evidence.append(ev)
                elif et == "result":
                    react_stats = ev

            # 兜底：客服问题但模型一次工具都没调（常被会话历史里的旧答案带偏，误以为已知）
            # → 强制补一次 kb_search（用用户原问题），保证答复有 grounded 依据（重复问题也能稳定检索）
            if not evidence:
                yield {"ev": "status", "phase": "tool", "name": "kb_search", "label": "补充检索知识库…"}
                t_g = time.perf_counter()
                try:
                    result = await self._broker.call("kb_search", {"query": text}, context_id=context_id)
                    evidence.append({"round": 1, "name": "kb_search", "args": {"query": text}, "result": result})
                    react_stats["tool_ms"] = float(react_stats.get("tool_ms", 0.0)) + (time.perf_counter() - t_g) * 1000
                    react_stats["n_calls"] = int(react_stats.get("n_calls", 0)) + 1
                    logger.info("客服问题未触发工具，兜底补检索 kb_search")
                except Exception as gexc:  # noqa: BLE001 - 兜底检索失败不致命，继续走空证据
                    logger.warning("兜底 kb_search 失败 err=%s", gexc)

            t_react = time.perf_counter()
            chunks = self._collect_chunks(evidence)
            # 检索到的来源先推给前端（答复开始生成前就能看到"参考 N 篇"，填住空窗、更显可信）。
            if chunks:
                sources = [
                    {k: c.get(k) for k in ("source", "source_url", "category", "score")}
                    for c in chunks if isinstance(c, dict)
                ]
                yield {"ev": "sources", "data": {"sources": sources, "count": len(sources)}}

            yield {"ev": "status", "phase": "answer", "label": "正在生成答复…"}
            parts: list[str] = []
            async for delta in self._reply.reply_stream(
                text, chunks, history, context_id=context_id, memory=memory_ctx
            ):
                if delta:
                    parts.append(delta)
                    yield {"ev": "token", "text": delta}
            t_rep = time.perf_counter()
            answer = "".join(parts)
        except Exception as exc:  # noqa: BLE001 - 工具/下游失败统一兜底，仍 COMPLETED（票 06 §7）
            async for ev in self._emit_error_fallback(sess_key, text, intent, exc, t0, t_tri):
                yield ev
            return

        trace = _make_trace(
            is_service=True, intent=intent,
            tool_calls=[
                {"round": e.get("round"), "name": e.get("name"),
                 "args_summary": _summarize_args(e.get("args", {})),
                 "result_summary": str(e.get("result", ""))[:160]}
                for e in evidence
            ],
            retrieval={
                "count": len(chunks),
                "chunks": [
                    {k: c.get(k) for k in ("text", "source", "source_url", "category", "score")}
                    for c in chunks if isinstance(c, dict)
                ],
            },
            timings={
                "triage_ms": round((t_tri - t0) * 1000, 1),
                "react_ms": round((t_react - t_tri) * 1000, 1),
                "react_llm_ms": round(float(react_stats.get("llm_ms", 0.0)), 1),
                "react_tool_ms": round(float(react_stats.get("tool_ms", 0.0)), 1),
                "n_tool_calls": int(react_stats.get("n_calls", len(evidence))),
                "reply_ms": round((t_rep - t_react) * 1000, 1),
            },
        )
        await self._session.append(sess_key, text, answer, trace=trace)
        await self._append_history(user_id, intent, text, context_id)
        # 写回：仅独立提问 + 非 transaction + 有答复；语义缓存 record 异常吞掉不致命。
        if self._cache is not None and standalone and answer and intent != "transaction":
            self._cache.set(text, answer, user_id, pref_sig=pref_sig)
        if self._scache is not None and standalone and answer and intent != "transaction":
            try:
                await self._scache.record(text, answer, user_id, pref_sig=pref_sig)
            except Exception as exc:  # noqa: BLE001 - 写缓存失败不影响本次答复
                logger.warning("语义缓存 record 异常（忽略）err=%s", exc)
        logger.info("接待完成 context_id=%s 工具调用=%d 命中=%d", context_id, len(evidence), len(chunks))
        yield {"ev": "trace", "data": trace}

    async def _emit_error_fallback(
        self, sess_key: str, text: str, intent: str, exc: Exception,
        t0: float, t_tri: float,
    ) -> AsyncIterator[dict[str, Any]]:
        """主链路异常兜底（票 06 §7：任一环节异常 → 兜底话术，任务仍 COMPLETED）。

        trace 里不塞 `retrieval`（保持既有 shape；helper 依 None 跳过）。
        """
        logger.error("接待编排失败走兜底 err=%s", exc)
        trace = _make_trace(
            is_service=True, intent=intent, error=str(exc),
            timings={"triage_ms": round((t_tri - t0) * 1000, 1)},
        )
        await self._session.append(sess_key, text, self._fallback, trace=trace)
        yield {"ev": "token", "text": self._fallback}
        yield {"ev": "trace", "data": trace}

    # ---------- 公共 handle 与工具辅助 ----------

    async def handle(self, text: str, context_id: str, user_id: str | None = None) -> dict[str, Any]:
        """排空 stream 组装成一次性结果（向后兼容既有调用点）。"""
        answer_parts: list[str] = []
        trace: dict[str, Any] | None = None
        async for ev in self.stream(text, context_id, user_id=user_id):
            if ev["ev"] == "token":
                answer_parts.append(ev["text"])
            elif ev["ev"] == "trace":
                trace = ev["data"]
        return {
            "answer": "".join(answer_parts),
            "is_service": bool((trace or {}).get("is_service", True)),
            "intent": (trace or {}).get("intent", ""),
            "trace": trace,
        }

    async def _lookup_semantic_cache(
        self, text: str, user_id: str | None, pref_sig: str
    ) -> str | None:
        """语义缓存 lookup 统一异常降级：客户端异常一律当未命中（走正常链路）。"""
        try:
            return await self._scache.lookup(text, user_id, pref_sig=pref_sig)
        except Exception as exc:  # noqa: BLE001 - 缓存客户端异常不致命
            logger.warning("语义缓存 lookup 异常（忽略）err=%s", exc)
            return None

    async def _load_profile(self, user_id: str | None) -> dict[str, str]:
        """读用户偏好（to_thread 卸载阻塞）；无记忆库/异常 → 空 dict（不影响主链路）。"""
        if self._memory is None:
            return {}
        try:
            return await asyncio.to_thread(self._memory.get_profile, user_id) or {}
        except Exception as exc:  # noqa: BLE001 - 偏好读失败当无偏好
            logger.warning("偏好读取失败（当无偏好）err=%s", exc)
            return {}

    async def _build_memory_ctx(self, profile: dict[str, str], user_id: str | None) -> dict[str, Any] | None:
        """用已读的 profile + 懒读的最近咨询，组装答复注入上下文；两者皆空 → None（不注入）。"""
        recent: list[dict[str, Any]] = []
        if self._memory is not None:
            try:
                recent = await asyncio.to_thread(self._memory.get_recent_history, user_id) or []
            except Exception as exc:  # noqa: BLE001 - 历史读失败当无历史，仍可用偏好
                logger.warning("最近咨询读取失败（忽略）err=%s", exc)
                recent = []
        profile_text = "，".join(f"{k}: {v}" for k, v in (profile or {}).items())
        queries = [h.get("query", "") for h in recent if h.get("query")]
        if not profile_text and not queries:
            return None
        return {"profile_text": profile_text, "recent": queries}

    async def _append_history(self, user_id: str | None, intent: str, query: str, context_id: str | None) -> None:
        """把一次真实客服问答记进长期历史（to_thread 卸载阻塞）；无记忆库则 no-op。写失败已降级。"""
        if self._memory is None:
            return
        try:
            await asyncio.to_thread(self._memory.append_history, user_id, intent, query, context_id)
        except Exception as exc:  # noqa: BLE001 - 记历史失败绝不影响本次答复
            logger.warning("长期历史写入失败（忽略）err=%s", exc)

    @staticmethod
    def _collect_chunks(evidence: list[dict]) -> list[dict]:
        """把 kb_search 工具返回的 chunks 汇总出来（供答复 Agent 用）。"""
        chunks: list[dict] = []
        for e in evidence:
            if e.get("name") != "kb_search":
                continue
            raw = e.get("result")
            try:
                data = json.loads(raw) if isinstance(raw, str) else raw
            except (ValueError, TypeError):
                continue
            if isinstance(data, dict):
                chunks.extend(c for c in data.get("chunks", []) if isinstance(c, dict))
        return chunks
