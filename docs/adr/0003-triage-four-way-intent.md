# 分流从"是否客服"二元扩到四类分治（对票 06 §3/§6 定案的有意扩展）

Status: accepted

原始契约定案见 `.scratch/a2a-customer-service/issues/06-agent-a2a交互契约.md` §3「分流判定结果 `{is_service: bool, intent: str, reason: str}`」与 §6「非客服：直接兜底、不触发下游」（二元）。本 ADR 记录工单 12 接待 ReAct 改造后，分流从"是否客服"二元扩到 `chitchat / knowledge / transaction / out_of_scope` 四类分治的定案与影响。

## 决策

接待 triage 让模型输出 `{intent: chitchat|knowledge|transaction|out_of_scope, confidence, reason}`；`is_service` 由 `triage.py` 从 intent 派生（`out_of_scope` → False，其余 → True）保留向后字段。四类分派各自路径：

- `chitchat`：跳检索、跳缓存读写（不进 L1/L2、不写 query_history），走轻量答复（`mode="chitchat"`，仍注入偏好风格）。
- `knowledge`：走完整链路（triage → 语义缓存 L2 查 → ReAct+kb_search → 答复流式 → 写 L1/L2 → 写 query_history）。
- `transaction`：走完整链路但**不缓存**（交易结果实时性要求，`intent != "transaction"` 才写 L1/L2），工具集走 create_ticket / get_order_status / escalate_human 等 mock。
- `out_of_scope`：直接兜底、不触发下游（同票 06 §6 原语义）。

判定标准与提示词全文见 `config/agents/reception.yaml:prompts.triage`，档案见 `docs/prompts/reception.md`。

## 考虑过但否决的方案

- **守票 06 §6 二元**（`is_service` + `intent` 只是描述字段，不参与路径分派）：与初始契约文本一致，代价是寒暄也走一次 ReAct + 检索 + 答复，交易结果被无差别缓存 → 前者实测多耗 ~50-100s、后者可能答旧的。→ 否决。
- **只加 `chitchat` 一路，交易与知识合一**：缓存与流水仍会串。→ 否决。
- **让答复侧也按 intent 差异化提示词**：越界，答复侧只关心是否有 chunks 与 memory，intent 由接待消化。

## 影响

- **票 06 §3 定案的 DataPart schema（`{is_service, intent, reason}`）里 `intent` 语义从"自由文本标签"变成"四值枚举"**。下游答复 Agent 现况不读 `intent`（`reply/executor.py`），故不破坏契约；接待内部 `ReceptionService.stream()` 按 intent 分派。
- 语义缓存 L1/L2 与长期记忆 `query_history` 的写侧条件收紧到 `intent != "transaction"`，且 L1/L2 key 均折入 `pref_sig` 偏好签名（详见 ADR-0001）。换任一偏好 → miss → 按新偏好重生成。
- trace 事件里 `intent` 字段可能出现合成值 `"cache"`（L1/L2 命中路径）与四值枚举并存。Standards 审的 Primitive Obsession 命中此处——判定为"文档级合成标记"不硬约束，四值枚举本身仍在 `triage.py` 保持干净。
- 档案漂移点：`docs/prompts/reception.md` 的"生效模型/参数"与"字段说明"表已在本轮同步回写为四类 + deepseek-v4-flash-0731；后续任何"改回二元"的提议视为与本 ADR 冲突。
