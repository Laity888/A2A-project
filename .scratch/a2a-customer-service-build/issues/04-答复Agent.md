# 04: 答复 Agent

**What to build:** 一个纯 A2A server 答复 Agent：给定 `{query, chunks, history}` 用 LLM 产出中文最终答复文本。可先用预置 chunks 独立测，不必等检索就绪。

**Blocked by:** 01

**Status:** done

- [x] `answer` skill（json→text，票 06 §8）；输入 DataPart `{query,chunks,history}`（单轮 history 空数组）→ 输出 `TextPart`（票 06 §3）
- [x] 中文 system + 生成提示词，走 `config/agents/reply.yaml`（票 09 规范登记）
- [x] 经 DashScope OpenAI 兼容接口、Key 读环境变量 `aliyun_API_KEY`、超时 120s（票 05 硬约束）
- [x] 契约测用 fake LLM 断言产出结构与提示词模板；e2e（`-m llm`/`-m e2e`）真 LLM 出文（票 10 §2）
- [x] LLM 失败 → 本 Agent 返回 `TASK_STATE_FAILED`，由接待兜底（票 06 §7）

## Comments

- 2026-10-08 落地（共享 venv + a2a-sdk 1.2.2）。`pytest` **16 passed / 1 deselected，EXIT=0**；`-m llm` 收集到真实 LLM e2e（无 Key 自动 skip）。
- 新增文件：`src/agents/reply/agent.py`（`build_reply_messages` + `ReplyAgent`）、`src/agents/reply/executor.py`（`ReplyAgentExecutor`：DataPart 解析→TextPart，异常→FAILED，退化纯文本当 query）、重写 `src/agents/reply/app.py`（`build_app(config=None, agent=None)`，默认真实 LLMClient、测试可注入 fake）；`config/agents/reply.yaml` 的 system 提示词由占位改为可用 RAG 版；`tests/integration/test_reply_contract.py`（组装单测 + happy 驱动 + 失败置 FAILED + 真实 LLM e2e）。
- 测试策略落实：`pyproject` 加 `addopts = "-m 'not llm and not e2e'"`，默认只跑无外部依赖快测；外部测靠 `-m llm`/`-m e2e` opt-in（票 10 §1）。
- 留给后续工单：真实 `chunks` 由工单 03（检索）产出后经工单 05（接待）中转注入；提示词按票 09 登记到 `docs/prompts/reply.md` + `docs/records/` 由工单 08 的一致性校验接管。
- 已知无害噪声：失败用例触发 a2a-sdk 后台 consumer 协程清理告警（stderr RuntimeWarning），不影响结果；真实生命周期在工单 05 处理。
