"""工单 09：性能冒烟（票 11 §1/§5）。

对运行中的全栈打真实请求，测：检索 P95、端到端完整答复、首 token(TTFB)。
单流 N 次 + 3 并发一批。结果写 docs/records/perf-smoke.md，对照票 11 软目标。

用法（服务在跑）：
    PYTHONPATH=src ./.venv/Scripts/python.exe scripts/perf_smoke.py
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
UI_URL = "http://127.0.0.1:8080"
RETRIEVAL_URL = "http://127.0.0.1:8002"
SINGLE_N = 5
CONCURRENCY = 3
QUESTIONS = [
    "如何获取 RAGFlow 的 API Key",
    "怎么在本地部署大模型",
    "如何创建和配置知识库",
    "RAGFlow 支持 RAPTOR 吗，怎么开启",
    "怎么构建知识图谱",
]


def pct(xs, p):
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((p / 100) * (len(xs) - 1)))))
    return xs[k]


async def one_chat(client: httpx.AsyncClient, msg: str) -> dict:
    """返回 {ttfb_s, total_s, ok}。"""
    t0 = time.perf_counter()
    ttfb = None
    async with client.stream("POST", f"{UI_URL}/api/chat", json={"message": msg}) as r:
        buf = ""
        async for ch in r.aiter_text():
            if ttfb is None and ch.strip():
                ttfb = time.perf_counter() - t0
            buf += ch
    return {"ttfb_s": ttfb or 0.0, "total_s": time.perf_counter() - t0, "ok": r.status_code == 200}


async def one_retrieval(msg: str) -> float:
    from a2a.client import A2ACardResolver, ClientConfig, create_client
    from a2a.helpers import new_data_message
    from a2a.types import Role, SendMessageRequest
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=240) as c0:
        card = await A2ACardResolver(httpx_client=c0, base_url=RETRIEVAL_URL).get_agent_card()
        cl = await create_client(agent=card, client_config=ClientConfig(streaming=False, httpx_client=c0))
        try:
            req = SendMessageRequest(message=new_data_message({"query": msg, "top_k": 8}, role=Role.ROLE_USER))
            async for _ in cl.send_message(req):
                pass
        finally:
            await cl.close()
    return time.perf_counter() - t0


async def main():
    if not _open(UI_URL):
        print("UI 未运行，跳过冒烟"); return
    retrieval_ms, single, conc = [], [], []
    async with httpx.AsyncClient(timeout=240) as client:
        for q in QUESTIONS:
            retrieval_ms.append(await one_retrieval(q) * 1000)
        for i in range(SINGLE_N):
            single.append(await one_chat(client, QUESTIONS[i % len(QUESTIONS)]))
        batch = await asyncio.gather(*[one_chat(client, QUESTIONS[i % len(QUESTIONS)]) for i in range(CONCURRENCY)])
        conc.extend(batch)

    s_total = [x["total_s"] for x in single]
    s_ttfb = [x["ttfb_s"] for x in single]
    c_total = [x["total_s"] for x in conc]
    md = _report(retrieval_ms, s_total, s_ttfb, c_total)
    out = REPO_ROOT / "docs/records/perf-smoke.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md)


def _open(url):
    import socket
    from urllib.parse import urlparse
    u = urlparse(url); s = socket.socket(); s.settimeout(2)
    try:
        s.connect((u.hostname, u.port)); return True
    except OSError:
        return False
    finally:
        s.close()


def _report(retrieval_ms, s_total, s_ttfb, c_total):
    def avg(xs): return round(statistics.fmean(xs), 2) if xs else 0
    return f"""# 性能冒烟记录（工单 09 / 票 11 §5）

> 环境：宿主机 CPU（BGE-M3+reranker，infer_concurrency=1）+ VM Milvus + qwen3.8-max。
> 演示级样本（单流 {len(s_total)} 次 + {len(c_total)} 并发），非正式压测。

| 指标 | 结果 | 软目标（票 11） |
|---|---|---|
| 检索 P95 | {pct(retrieval_ms,95)/1000:.2f}s | ≤ ~2s |
| 端到端 平均 | {avg(s_total)}s | ≤ ~15s |
| 端到端 P95 | {pct(s_total,95):.2f}s | ≤ ~15s |
| 首 token TTFB 平均 | {avg(s_ttfb)}s | ≤ ~8s |
| {len(c_total)} 并发 端到端 平均 | {avg(c_total)}s | — |

说明：端到端含 ReAct 多轮工具调用（kb_search 常多次）+ 答复生成，故高于单次检索；
并发下因检索 CPU 推理串行（信号量=1），端到端时延上升属预期。数字随机器/额度波动，仅展示。
"""


if __name__ == "__main__":
    asyncio.run(main())
