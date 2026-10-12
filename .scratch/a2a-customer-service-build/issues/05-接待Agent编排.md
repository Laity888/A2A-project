# 05: 接待 Agent（编排脊柱）

**What to build:** 对外唯一入口的接待 Agent：分流、编排 检索→答复、维持会话、统一兜底，并对外流式 + trace。做完即可端到端问一次客服（进程内）。

**Blocked by:** 03, 04

**Status:** done

- [x] 作 A2A server（`triage`, text→text）同时作 client 依次调 检索→答复；每跳各建 Task、同 `context_id`（票 06 §1/§2）—— *细化移交*：逐跳 `metadata.parent_task_id` 串联随 09 全链路补。
- [x] LLM 结构化分流 `{is_service,intent,reason}`；非客服 → 兜底话术 `COMPLETED` 且**不调下游**（票 06 §6）
- [x] 会话按 `context_id` 存（最近 N 轮，配置），把 `history` 注入答复（票 06 §9 / 票 11 §3）—— 默认内存 store 离线可用；Redis 后端已实现，留部署阶段启用。
- [x] 产出 trace `Artifact`（DataPart）；对外流式能力由 SDK streaming 提供（票 07 §5）—— *细化移交*：逐跳中间 `TaskStatusUpdateEvent` 随 06/09 补。
- [x] 降级：下游失败 → 兜底、任务仍 `COMPLETED`；技术细节只进日志（票 06 §7）
- [x] 四条核心分支测（非客服下游 0 调用 / 下游失败兜底 / 检索空 ≠ 失败 / 正常产出）+ history 注入（票 10 §4/§6）
- [ ] uvicorn 单 worker + asyncio 启动（票 11 §3）—— *移交*：真正起服务在 07/09（三进程/容器）落地时统一处理。

## Comments

- 2026-10-08 落地（项目 `.venv`）。工单05 契约 **5 passed**；全量默认 **32 passed, 2 deselected**。
- 代码：`src/agents/reception/{service,executor,triage,hub,session,app}.py` + `config/agents/reception.yaml`（补 `orchestration.retrieval_top_k/client_timeout_seconds`、`session.backend`、triage 提示词由占位改可用）。
  - `ReceptionService.handle`：分流 →（非客服：兜底、不触发下游）/（客服：取历史 → `hub.retrieve` → `hub.reply` → 追加会话）；任一异常统一兜底、任务仍 COMPLETED。
  - `ReceptionAgentExecutor`：入站文本 → WORKING → service → 出站 Artifact（答复 TextPart + trace DataPart）→ COMPLETED。
  - 依赖全部注入：真实 `TriageAgent`(LLM) + `A2AHub`(create_client 调检索/答复) + `make_session_store`(memory/redis)；测试注入 fake 离线跑。
- **明确未完（移交）**：① 逐跳 `parent_task_id` 串联、② 对外逐跳 `TaskStatusUpdateEvent`、③ 三进程/容器真实启动与 uvicorn worker、④ 真实三 Agent 全链路 `-m e2e` —— 均依赖服务同时在线，随工单 06（UI 消费 trace）/ 07（compose）/ 09（全链路 e2e）一并补齐。
