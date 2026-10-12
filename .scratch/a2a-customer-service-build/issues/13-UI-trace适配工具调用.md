# 13: UI / trace 适配工具调用

**What to build:** 前端能展示 ReAct 的工具调用过程——trace 面板新增"工具调用"段（第几轮 / 调了哪个工具 / 入参摘要 / 结果摘要 / 耗时），状态胶囊文案随阶段变化（如"调用工具 kb_search…"），让用户看到 agent 自主选工具而不只是干等。

**Blocked by:** 12

**Status:** done

- [x] UI BFF 的 translate 透传扩展后的 trace（含 `tool_calls[]`）到前端 SSE `trace` 事件
- [x] 前端 trace 面板加"工具调用"段，逐条渲染 round/name/args_summary/result_summary
- [ ] 状态胶囊文案随阶段变（分流 / 调用工具 X / 生成答复）—— *移交*：需后端在 ReAct 每轮发中间 `TaskStatusUpdateEvent`（现仅一次 WORKING + 终态），随工单 09 一并补；当前胶囊已是「实时计时 + 通用文案」，不干等目标已达成。
- [x] 保持无蓝/紫配色与既有排版；离线测：注入含 tool_calls 的 fake trace，断言 translate 透传正确

## Comments

- 2026-10-08 落地。
- 代码：`src/ui/static/index.html` renderTrace 加"工具调用"段（round/name/args_summary/result_summary）+ 摘要行显示"工具 N 次 · 检索 M 条" + 耗时行改用 triage/react/reply；加 `.trace .muted` 样式。translate 无需改（工单 12 的 trace DataPart 已含 tool_calls，`a2a_to_events` 整体透传）。
- 验证：新增 `tests/unit/test_translate.py`（构造含 tool_calls 的 trace DataPart → 断言透传为 trace 事件）**1 passed**；全量默认 **51 passed, 2 deselected**。
- 前端渲染逻辑（JS）不纳入 pytest；真机效果在工单 09 全链路 e2e / 手动 demo 时验证。
- 未完（移交 09）：状态胶囊按 ReAct 每轮动态换文案，依赖后端补发中间进度事件。
