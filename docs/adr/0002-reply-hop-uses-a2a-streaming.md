# 内部跳答复走 A2A 流式（对票 06 §5 "非流式"定案的有意偏离）

Status: accepted

原始契约定案见 `.scratch/a2a-customer-service/issues/06-agent-a2a交互契约.md` §5「内部跳（接待 ↔ 检索、接待 ↔ 答复）：非流式（`ClientConfig(streaming=false)`）」。本 ADR 记录接待 → 答复这一跳在实施工单 05 → handoff §6.2「答复真·流式」演进中，最终选择 `streaming=True` 的边界与代价。接待 → 检索一跳仍守非流式（`semantic_cache.py` 读写走 `streaming=False`；票 06 §5 该部分不变）。

## 决策

接待 → 答复一跳用 `ClientConfig(streaming=True)`，`ReplyClient.reply_stream()` 逐段产出答复文本增量，`ReceptionService.stream()` 以内部 `{"ev":"token"}` 事件透传给 executor，executor 复用同一个 `artifact_id` 逐段 `add_artifact(append=True, ...)`，UI 端 SSE 逐段吐字。原始 grounded 答复合成逻辑（`{query, chunks, history, memory, mode}` DataPart）不变，只是接收侧从"一次性聚合"改为"逐段增量"。

## 考虑过但否决的方案

- **严守票 06 §5，接待 → 答复非流式**：与既有契约文本一致，无需 ADR；代价是答复段 UX 回退到"等完整答复再显"，且体感提速措施（`sources` 事件早于答复第一段吐）失效——`sources` 事件本就是为在答复生成前填住空窗而生，答复不流式则空窗重新出现。→ 否决。
- **答复侧真·流式仅到 UI 之间，接待 → 答复仍非流式**：中间多一次 await 聚合，实测端到端延迟差 <50ms（答复段本身 ~2-5s），但代码里会多一处"聚合再拆段"的样板。→ 否决。
- **答复侧内部跳与对外都流式，检索跳也改流式**：检索段本身 ~500ms-2s，Milvus hybrid 无逐段增量语义，改流式只是空转协议开销。→ 检索跳守票 06 §5 非流式。

## 影响

- **票 06 §5 的对外承诺（"内部跳非流式"）在答复这一跳被有意收窄**；接待 → 检索一跳仍守。文档描述答复流式的档案同步见 `docs/prompts/reply.md` 与工单 06 验收；后续任何"改回非流式"的提议视为与本 ADR 冲突，需显式推翻。
- 依赖 `a2a-sdk` 1.2.2 流式 artifact 的多段协议：`TaskUpdater.add_artifact` 必须复用同一个 `str(uuid4())` 作 `artifact_id`（首块 `append=False`、后续 `True`、末块 `last_chunk=True`），否则第二段起报 `append=True for nonexistent artifact_id`。已在 `reception/executor.py` 与 `reply/executor.py` 落实。
- 非流式 client（`streaming=False`）用 `get_stream_response_text` 读聚合 artifact 会在多 text part 间插 `\n`；单测断言按聚合口径写，真流式 UI 端逐 event `+=` 无此问题。
- 单测桩（`tests/unit/test_service_stream.py` 里的 `MultiReply`）以 `async for yield` 假流式提供增量，不真跑网络；集成测在 `tests/integration/`。
