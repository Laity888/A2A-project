# 变更历史 · 接待 reception

字段：版本号 / 日期 / 改动原因 / 关联 ticket·commit / 当时模型与参数快照 / 效果证据。

## reception.triage

### v1 — 2026-10-08（工单 05）
- 改动：新建分流判定提示词（中文，要求严格输出 JSON `{is_service,intent,reason}`；拿不准倾向 true）。
- 关联：实施工单 05；决策票 06 §6。
- 模型/参数快照：model=qwen3.8-max，temperature=0.3，`retrieval_top_k=3`。
- 输出 schema：`{is_service, intent, reason}`。
- 效果证据：`tests/integration/test_reception_contract.py`（非客服→兜底不调下游 / 客服→编排 / 失败兜底 / 空结果）。真实分流样例待工单 09。

## reception.fallback_reply

### v1 — 2026-10-08（工单 05）
- 改动：非客服/异常统一兜底话术。
- 关联：工单 05；票 06 §7。
- 模型/参数快照：N/A（不调模型，直接返回）。
- 效果证据：同上契约测断言非客服与下游失败都返回兜底且 COMPLETED。

## reception.agent

### v2 — 2026-10-09（性能优化 · 第一档）
- 改动：ReAct system 提示词新增「每个子问题只检索一次：不要用相同或近似的问题重复调用 `kb_search`」，并把收尾表述收紧为「信息足够后立即停止调用工具」，约束重复检索、降低检索调用次数。
- 关联：`docs/prompts/reception.md` 的 `reception.agent` 同步更新，一致性校验通过（改提示词=同 commit 更新记录）。
- 效果证据：待起服务实测单次问答的 `kb_search` 调用次数下降；离线由 `test_reception_contract.py`、`test_service_stream.py` 保证行为不回退。

## 编排行为（接待 service / executor）

### v1 — 2026-10-09（性能优化 · 第一档：流式 + 每轮状态）
- 改动：`ReceptionService` 由一次性 `handle()` 改为异步生成器 `stream()`，逐段产出 `status/token/trace` 事件；`handle()` 保留为排空包装（向后兼容）。`ReceptionAgentExecutor` 据此：
  - 发**中间 WORKING 状态事件**（分流 → 调用工具 X → 生成答复），补齐工单 09/13 遗留的「状态胶囊按阶段变文案」；
  - 答复以**增量 artifact**下发（多段共用同一 `artifact_id`，首块 append=False、后续 append=True、末块 last_chunk=True），UI 逐字显示；
  - `ReActAgent` 改为 `run_stream`（逐轮 `tool_start/tool_done` 事件）+ `run` 排空包装。
- 关联：接待对下游答复的调用改为 `ReplyClient.reply_stream`（`streaming=True` 透传答复 token，只取 artifact 正文、不并入状态文本）；答复 Agent 侧详见 `docs/records/reply.md`。
- 效果证据：离线单测 `tests/unit/test_service_stream.py`（分阶段状态 + 多段 token + trace + 兜底）、`tests/unit/test_react_agent.py`（`run_stream` 事件）、`tests/unit/test_translate.py`（status 事件带阶段文案）。真机流式观感与检索次数下降需在起服务（+ VM Milvus）后复测，未臆造新延迟数字。

## 模型与延迟（提速 · 第二档）

### v1 — 2026-10-09（分流/ReAct 走快模型 + 分流快速失败）
- 改动：分流 + ReAct 路由的模型由 `qwen3.8-max` 换为 **`deepseek-v4-flash-0731`**（配置驱动：`reception.yaml: llm.model`）。已实测该 flash 支持 OpenAI function-calling（含并行多 `tool_calls`），可安全承担 ReAct 决策。最终答复仍由 reply 用 `qwen3.8-max`（见 `docs/records/reply.md`）。
- 分流快速失败：给 TriageAgent 单独构造短超时客户端 `triage_timeout_seconds=20`、`triage_max_retries=0`。此前一次偶发把 120s 超时打满又重试 → 分流 ~241s；现在慢响应 20s 即失败，且失败本就保守当作「客服问题」放行，不影响主流程。
- 实现：`common.config.llm_for(cfg, **over)` 派生每角色 `LLMConfig`（model/timeout/retries/max_tokens 覆盖）；`reception/app.py` 分别构造 react 客户端与 triage 客户端。
- 关联：`docs/prompts/reception.md` 新增 `reception.llm_model_override` / `reception.triage_timeout_seconds` / `reception.triage_max_retries`，纳入一致性校验。
- 效果证据：离线 `tests/unit/test_llm_config_roles.py`（覆盖/类型强转/配置读取/`max_tokens` 透传）。端到端延迟改善幅度需起服务实测后更新，未臆造新数字。

### v2 — 2026-10-09（提速 · 第二档续：治过度检索 + 耗时拆分）
- 现象：换 flash 后复测，分流 16s→~2.3s、答复 56s→~32s，但**工具涨到 ~72s**——flash 一次问了 ~3 次 `kb_search`（6 来源），每次都是 CPU 检索且被 `infer_concurrency=1` 串行；且工具结果不断塞进上下文 → 后续决策也更慢。瓶颈从"模型延迟"转移到"检索次数 × 每次成本"。
- 改动：
  1. `ReActAgent` 新增**工具调用总数硬上限** `max_tool_calls`（配置 `agent.max_tool_calls=3`）：达到即停止再调工具，封顶检索次数与上下文膨胀。
  2. `prompts.agent` 收紧为「整轮最多检索 2 次」。
  3. `ReActAgent.run_stream` 现分别累计 `llm_ms`（决策）与 `tool_ms`（检索执行）+ `n_calls`，经 `service` 写进 `trace.timings`（`react_llm_ms`/`react_tool_ms`/`n_tool_calls`），UI「耗时」行展示为「工具 Xms（决策 Yms · 检索 Zms · N次）」，下次跑就能定位是"次数"还是"单次成本"。
- 关联：`docs/prompts/reception.md` 新增 `reception.max_tool_calls`、同步 `reception.agent` 原文，纳入一致性校验。
- 效果证据：离线 `tests/unit/test_react_agent.py`（上限生效 + 耗时拆分）、`tests/unit/test_service_stream.py`（trace 拆分键）；全量 `pytest` 70 passed。真实延迟待复测；若定位到"单次检索"仍是大头，下一步是削减 rerank 候选数或释放内存（当前仅 ~2.5GB 空闲）。

### v3 — 2026-10-09（修复：重复问题第二次不检索）
- 现象：同一浏览器（同 `context_id`）连问两次相同问题，第二次 trace 显示「工具 0 次 · 检索 0ms」，答复变成"资料没检索到"。根因：会话历史里已带第一次的完整答案，flash 据此判定"不用再查"→ 0 工具调用 → 答复无 chunks。
- 修复：`ReceptionService.stream` 加**兜底检索闸门** —— 客服问题若本轮**一次工具都没调**，强制用用户原问题补一次 `kb_search`（发「补充检索知识库…」状态、计入 evidence 与 `react_stats`）；补检索失败也不致命（catch 后继续走空证据）。已调工具的轮次（如查订单/转人工）不受影响。
- 取舍：未采用"从 ReAct 上下文里剔除历史旧答案"这条更激进的根因修法，以保留多轮追问所需的上下文；兜底闸门更稳、副作用小。
- 效果证据：离线 `tests/unit/test_service_stream.py::test_service_stream_grounding_guard_when_no_tool_called`（模型 0 工具 → 兜底检索生效、chunks 进答复）；全量 `pytest` 71 passed。

### v4 — 2026-10-09（答案缓存：重复问题秒回）
- 动机：同一问题被重复提问时，第二次仍把 triage→ReAct→检索→生成整条链路重跑一遍，产出一样的内容，浪费算力与时延。
- 实现：新增 `agents/reception/answer_cache.py`（进程内答案缓存）——key 为**归一化后的问题文本**（转小写 + 去空白/中英文标点，让"支持哪些文件格式"与"支持的文件格式 有哪些？"归一），TTL 默认 **86400s（一天）**，LRU 上限 `max_entries=512`，时钟可注入便于测过期。
- 接入：`ReceptionService.stream` 开头**每轮都查**缓存，命中即发「命中缓存」状态 + 直接吐答案 + `cache_hit` trace 并 return（跳过整条链路）；仅当该轮为独立提问（无 history）且答复非空时才写回缓存。配置 `reception.yaml: answer_cache {enabled, ttl_seconds=86400, normalize, max_entries}`，`app.py` 构造注入。
- 安全边界：**写**只写无上下文的独立提问（不把带上下文的追问答案缓存下来）；**读**不加限制——任何轮次命中即返回（含同一会话内重复问），因为缓存里只存过自洽的独立问题，命中即当前问句与之同义，可安全返回。带指代的追问（"那第二个呢"）字面不同于已存 key，不会误命中。知识型答复与用户无关故 key 不含 `context_id`、跨会话共享；TTL 兜住知识库更新后的陈旧。UI trace 显示「命中缓存 · 跳过检索/生成」、耗时「命中缓存 · Xms」。
- 关联：`docs/prompts/reception.md` 新增 `reception.answer_cache_*` 三条纳入一致性校验。
- 效果证据：离线 `tests/unit/test_answer_cache.py`（roundtrip/归一化/禁用/TTL 过期/LRU 淘汰）+ `tests/unit/test_service_stream.py::test_service_stream_answer_cache_skips_pipeline`（第二次不再触发检索/生成、归一化问法也命中）；全量 `pytest` 79 passed。
