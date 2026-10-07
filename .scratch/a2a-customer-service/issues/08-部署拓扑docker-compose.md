Type: task
Status: resolved
Blocked by: 06

## Question

在 CentOS VM 上用 docker-compose 把全栈跑起来。

- 拓扑：3 个 Agent 容器 + Milvus + MySQL + Redis + Web UI，各自端口怎么分？
- Agent Card URL 方案：容器之间如何互相发现？Card 里写容器名还是宿主 IP？
- 端口映射：宿主机端口与 Card 声明的端口必须一致。
- 数据持久化：Milvus / MySQL 的 volume 怎么挂。

依赖 06：交互契约定了，才知道谁连谁、要开哪些端口。

## Answer

部署拓扑定稿。**产出为决策 spec（可交 `/to-tickets` 落地）；实际 compose / Dockerfile 与 VM 部署交实施票**（3 个 Agent 尚未实现）。

**总览**：compose 只起自研服务（接待 `8001` / 检索 `8002` / 答复 `8003` / UI `8080`），中间件（Milvus / MySQL / Redis）沿用 VM `192.168.88.101` 已有实例；对外只暴露 UI `8080`。

### 1. 服务边界
- compose 内：`reception`、`retrieval`、`reply`、`ui` 四个服务。
- compose 外（VM 已有实例，按 IP 连）：Milvus `192.168.88.101:19530`、MySQL `192.168.88.101:3306`、Redis `192.168.88.101:6379`。
- 理由：数据已灌（`a2a_cs_lzs` / `cs_kb_lzs`）；6G VM 再叠 Milvus（3–4G）必 OOM。

### 2. 服务发现与 Agent Card URL
- compose 单 bridge 网络；容器间用 **service name** 互访。
- 三个 Agent 的 Card `public_url` 一律写 service name：`reception` → `http://reception:8001`；`retrieval` → `http://retrieval:8002`；`reply` → `http://reply:8003`。
- 接待 调下游：配置里写 `http://retrieval:8002` / `http://reply:8003`，走 `A2ACardResolver` 拉 Card。
- 浏览器只连 UI，**不直连任何 Agent**（06 单入口）→ 满足 05 的 `public_url` 教训。

### 3. 端口
- 接待 `8001`（容器内，不映射宿主）
- 检索 `8002`（容器内，不映射宿主）
- 答复 `8003`（容器内，不映射宿主）
- UI(FastAPI) `8080` → 宿主 `8080`
- 中间件沿用 `19530` / `3306` / `6379`
- 内部 Agent 不映射宿主（单入口、减少暴露面）；调试可**可选**加 reception 映射（默认注释）。端口一律写配置。

### 4. 镜像构建
- 单 `Dockerfile`、多 build target：
  - `base`：a2a-sdk + openai 等轻依赖 → 接待 / 答复 用。
  - `retrieval`：继承 base + torch(CPU) / pymilvus / sentence-transformers → 检索 用。
- 三个 Agent 用同一仓库镜像、靠 `command` 区分（`python -m agents.reception` 等）。

### 5. 模型挂载与 OOM（04 遗留定稿）
- BGE-M3 + bge-reranker-large 放 VM 某路径，以**只读 volume** 挂进 检索 容器。
- 模型来源做成**配置项**（本地目录 / 远端推理服务 URL）→ 实测 OOM 时改配置切「模型外置到开发机」，不改代码。

### 6. 配置注入
- `config/` 目录以**只读 bind mount** 进容器：`config/common.yaml`（Milvus / MySQL / Redis 连接、LLM、日志）+ `config/agents/{reception,retrieval,reply}.yaml`。
- `aliyun_API_KEY` 走 compose `environment`（或 `.env`），**不进镜像、不进 git**。
- 改端口 / 模型 / 提示词无需重建镜像。

### 7. 网络与外部中间件
- compose 自建单 bridge 网络。
- 连 VM 中间件：配置直接写 `192.168.88.101`（04 已验证可达）。
- `host.docker.internal`（Linux 需 `extra_hosts: host-gateway`）作为备选，写进注释。

### 8. 数据持久化
- compose 内**无数据 volume**（Milvus / MySQL / Redis 由 VM 已有实例负责持久化）。
- 检索 容器只读挂模型目录（非持久化数据）。

### 不在本票范围（留给实施票）
- 实际 `docker-compose.yml` / `Dockerfile` / `.env.example` 的编写。
- VM 上的模型拷贝、镜像构建、`docker compose up` 与实测。
- 三个 Agent 的实现本身。

### 依赖（后续实施票使用）
- `docker` / `docker compose`（VM 已装，待确认）；镜像 base 建议 `python:3.10-slim`（对齐 05 的 Python 3.10.20）。
- 配置项新增：`common.yaml` 的 Milvus / MySQL / Redis 连接、各 Agent 端口与 `public_url`、下游 URL、模型来源。

## Comments

- 2026-10-07（第 1 轮，已收敛）：
  - Q1 服务边界 = **(a)** 只把自研服务（接待/检索/答复/UI）放进 compose；Milvus / MySQL / Redis 沿用 VM `192.168.88.101` 上已有实例（外部服务，按 IP 连）。理由：数据已灌、6G VM 再叠 Milvus 必 OOM。
  - Q2 服务发现 = **(a)** compose 单网络 + service name 互访；三个 Agent 的 Card `public_url` 一律写 service name；浏览器只连 UI，不直连 Agent。满足 05 的 public_url 教训。
  - Q3 端口 = **(a)** 接待 `8001` / 检索 `8002` / 答复 `8003`（仅 compose 网络内，不映射宿主）；UI(FastAPI) `8080` 映射宿主；中间件沿用 `19530`/`3306`/`6379`。
  - Q4 产出范围 = **(a)** 只定拓扑决策，写进 `## Answer` 作为交给 `/to-tickets` 的 spec；实际 compose / Dockerfile 与 VM 落地交实施票（3 个 Agent 尚未实现）。
  - 第 2 轮待问：模型目录挂载 + OOM 定稿、镜像构建方式、配置注入、网络与外部中间件连通。
- 2026-10-07（第 2 轮，已收敛，本票闭环）：四个决策全部按推荐定。
  - Q5 模型挂载 = **(a)** BGE-M3 + bge-reranker-large 放 VM、只读 volume 挂进 检索 容器；模型来源做成配置项，OOM 时改配置切「外置到开发机」。
  - Q6 镜像 = **(a)** 单 Dockerfile 多 target（`base` 轻依赖 / `retrieval` 含 torch+pymilvus），三 Agent 同镜像不同 `command`。
  - Q7 配置注入 = **(a)** `config/` 只读 bind mount + `aliyun_API_KEY` 走环境变量（不进镜像/git）。
  - Q8 网络 = **(a)** compose 单 bridge 网络 + 外部中间件按 `192.168.88.101` 直连；`host.docker.internal` 作备选注释。
  - 两轮共 8 个决策全部按推荐收敛；`## Answer` 定稿，置 `resolved`，回写 `map.md`。本票产出为决策 spec，实际 compose/Dockerfile/VM 落地交 `/to-tickets` 的实施票。