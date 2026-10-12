# 10: README 作品集包装

**What to build:** 一份能拿去求职展示的 README，讲清项目、怎么跑、效果与关键设计。包装经用户收窄为**只做 README**（不含 GitHub CI 徽章/展示）。

**Blocked by:** 06, 08, 09

**Status:** done

- [x] 项目简介 + 四层架构（Function Call / MCP / Agent / A2A）+ 三 Agent 协作图（配一张架构图）
- [x] 快速开始：**本地 PyCharm 起 4 个服务**（`python -m agents.retrieval` / `agents.reply` / `agents.reception` / `ui`，env `A2A_LOCAL=1`）+ `aliyun_API_KEY` 环境变量说明（不做 docker 部署）
- [x] 演示：一条问答 + trace 面板（含工具调用）截图/说明 + 冒烟延迟数字（票 11）
- [x] 关键设计要点（编排单入口 / MCP 工具桥检索 / 检索质量 recall@2 / 降级兜底 / 提示词可复现）指向 `docs/`
- [x] 许可与语料来源标注（RAGFlow Apache-2.0，票 03）

## Comments

- 2026-10-08 落地：仓库根 `README.md`。
- 含：一句话定位 + **四层表 + mermaid 架构图** + 三 Agent 职责 + 技术栈 + 目录结构 + **本地 PyCharm 运行步骤**（含入库两步 + `A2A_LOCAL` 说明）+ 测试（**51 passed, 4 deselected**，外部测 `-m e2e`/`-m llm`）+ 效果（**recall@2=0.81**、性能冒烟数字**如实标注超软目标 + 优化方向**）+ 设计要点（配置驱动/提示词一致性校验/降级兜底/进程内测试/日志规范）+ 语料许可（RAGFlow Apache-2.0）+ 已知局限。
- 数字均取自实测（`docs/records/`），未粉饰。
- 至此 13 张实施工单：01–06、08–13 done，07 dropped。项目收尾完成。
