# 跨跳 `parent_task_id` 链路追溯作品集不做（对票 06 §2 定案的有意收窄）

Status: accepted

原始契约定案见 `.scratch/a2a-customer-service/issues/06-agent-a2a交互契约.md` §2「`Message.metadata` 带 `parent_task_id`（配合 `reference_task_ids`）指向上游 Task，链路可追溯」。工单 05 注明"移交工单 09 补"，但工单 09 验收项无此项，handoff §6 亦未列。本 ADR 记录**作品集范围主动收窄**该链路追溯，不做代码落地。

## 决策

作品集演示范围只做 **`context_id` 跨跳透传**（一次用户请求三跳同一 `context_id`，UI trace 面板已展示），**不做 `parent_task_id` / `reference_task_ids` 落地**。链路追溯改由：

- `context_id`（三跳一致，A2A 原生归组）
- 接待 trace DataPart 的 `tool_calls[].round` 与 `timings.*_ms`（工具级链路耗时）
- 日志按 `context_id` grep

三者合起来覆盖作品集演示需求。任何"跨会话按 Task ID 反查具体上游 Task"的诉求属真实生产环境需求，作品集不做。

## 考虑过但否决的方案

- **落地代码补票 06 §2**：新增 metadata 字段传 `parent_task_id`，三跳各自要建/取 Task id、注入 metadata、下游要 unwrap。工作量约 1-2 天，需扩测；作品集里没有"按 Task 反查"的 UI/日志入口，落地即死代码。→ 否决。
- **改票 06 §2 定案**：`## Answer` 是 wayfinding 定案快照，直接改破坏历史；本 ADR 引用票 06 §2 说明"定案时想做，作品集阶段主动不做"更清楚。
- **让工单 09 补**：工单 09 已验收，且其验收项写的是"e2e 与性能冒烟"，与本字段无关。

## 影响

- 票 06 §2 定案的 `parent_task_id` / `reference_task_ids` 部分**作品集阶段悬空**，代码里不出现；`Message.metadata` 只有 `context_id`（接待 → 检索、接待 → 答复两跳都传同一 `context_id`）。
- 未来 v2 若接真实工单/订单系统或多实例部署，重开此项时用 ADR 状态 `superseded` 指向新 ADR，不直接改本 ADR。
- 任何 Standards 审再次点名"缺 `parent_task_id`"（如 `review-20261012.md` Spec 轴 a-1）视为与本 ADR 冲突，评审员需给出推翻本 ADR 的理由才能升格为待做。
