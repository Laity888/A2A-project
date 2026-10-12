# 07: docker-compose 全栈部署

**What to build:** `docker compose up` 一键起三 Agent + UI 全栈，连 VM 已有中间件，从 UI 端到端跑通。

**Blocked by:** 05, 06

**Status:** dropped

- [ ] compose 只起 reception(8001) / retrieval(8002) / reply(8003) 内网不映射 + ui(8080) 映射宿主；Milvus / MySQL / Redis 沿用 VM `192.168.88.101`（票 08 §1/§3）
- [ ] 单 bridge 网络、service name 互访、三 Agent Card `public_url` 写 service name（票 08 §2 / 票 05 教训）
- [ ] 单 Dockerfile 多 target：base（轻依赖）/ retrieval（+torch+pymilvus+模型），三 Agent 同镜像不同 `command`（票 08 §4）
- [ ] 模型目录只读挂载进检索容器 + 模型来源配置项（OOM 兜底切外置）（票 08 §5 / 票 11 §7）
- [ ] `config/` 只读 bind mount、`aliyun_API_KEY` 走 env 不进镜像 / git（票 08 §6/§8）
- [ ] 起全栈后从 UI 走通一条客服问答（连 VM 数据）

## Comments

- 2026-10-08 用户决定**不做容器化部署**：以后一律在 PyCharm 本地起 4 个服务（`python -m agents.retrieval/reply/reception` + `python -m ui`，env `A2A_LOCAL=1`）。本地起法在工单 06/12 已就绪（service-name→127.0.0.1 的 URL 改写 + 各服务 `__main__`），无需 compose。
- 决策票 08（部署拓扑）保留为"暂不执行"的历史决策；若将来要上 VM/分享再回来做 07。
- 连带：工单 10 README 的"如何运行"章节改写成本地 PyCharm 起法，并从其 Blocked by 去掉 07。
