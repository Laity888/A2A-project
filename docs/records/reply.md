# 变更历史 · 答复 reply

## reply.system

### v1 — 2026-10-08（工单 04）
- 改动：新建中文 RAG 生成 system 提示词（只用资料+历史、不编造、资料不足转人工）。
- 关联：决策票 06 §3；实施工单 04。
- 模型/参数快照：model=qwen3.8-max，temperature=0.3，timeout=120s。
- 输出：TextPart 中文答复。
- 效果证据：`tests/integration/test_reply_contract.py`（组装结构 + 产出 + 失败置 FAILED）；真实出文 `-m llm`。样例待工单 09。

## 生成行为（reply agent）

### v1 — 2026-10-09（性能优化 · 第一档：流式生成）
- 改动：答复由「整段生成完再一次性返回」改为**逐 token 流式产出**，消除「干等 80 秒后整屏刷出」的观感。
  - `common/llm_client.py` 新增 `chat_stream`（DashScope `stream=True`；只在**未吐出任何 token 前**对可重试错误退避重发，一旦吐字后再错直接抛 `LLMError` 交上层兜底）；`chat` 原样保留。
  - `ReplyAgent` 新增 `generate_stream`：LLM 支持流式则真流式，否则（fake/无流式能力）退化为单个增量、向后兼容。
  - `ReplyAgentExecutor`：把答复按增量 artifact 下发，多段共用同一 `artifact_id`（首块 `append=False`、后续 `append=True`、末块 `last_chunk=True`）。
  - 卡片 `agent_card.streaming` 由 `false` 改 `true`（供接待以 `streaming=True` 连）。
- 关联：接待侧 `ReplyClient.reply_stream` 透传；见 `docs/records/reception.md` 编排行为 v1。
- 效果证据：离线 `tests/integration/test_reply_contract.py`（fake 支持 `generate_stream`、happy 产出 + 失败置 FAILED）。真机逐字观感待起服务 + VM 后验证，未臆造新延迟数字。

### v2 — 2026-10-09（提速 · 第二档：限长 + 简洁）
- 改动：答复仍保持强模型 `qwen3.8-max`，但**限长以缩短生成**（此前实测答复生成 ~56s）。
  - `reply.yaml: llm.max_tokens=600` —— `LLMClient` 在设了 `max_tokens` 时透传给 `create()`（`chat`/`chat_stream`/`chat_tools` 一致），封顶输出长度。
  - `prompts.system` 第二条收紧为「直给要点，控制在 200 字以内，不要复述整段资料」，在保 grounded 质量的前提下减少冗长生成。
- 关联：`docs/prompts/reply.md` 同步 `reply.system` 原文，并新增 `reply.model_override` / `reply.max_tokens` 纳入一致性校验；配置读取见 `tests/unit/test_llm_config_roles.py`。
- 效果证据：离线测覆盖配置与透传；答复实际耗时下降幅度需起服务实测后更新，未臆造新数字。
