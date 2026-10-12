# 变更历史 · 多用户隔离 / 双层缓存(语义) / 登录迁 MySQL

字段：日期 / 工单 / 改动 / 关联配置·文件 / 效果证据（离线 vs 真机分开写清，不臆造数字）。

## 一、多用户隔离（身份贯穿 + 会话/缓存作用域）

### v1 — 2026-10-09
- 背景：改造前全链路只有会话级 `context_id`（来源 UI cookie `a2a_cs_sid`，换浏览器=换身份），
  且答案缓存 key 仅归一化问题、**跨用户共享**（A 的答案会被 B 命中）——最大隔离缺口。
- 改动：
  1. **身份贯穿**：新增 `user_id`，经 **A2A message.metadata** 传递（写用 `google.protobuf.json_format.ParseDict`，
     读用 `MessageToDict`），`context_id` 仍保持会话级（同一用户可多会话）。UI 侧 `ui/provider.py` 注入，
     接待 `agents/reception/executor.py` 的 `_extract_user_id()` 读出并透传给 service。
  2. **会话隔离**：`ReceptionService.stream` 的 session key 由 `context_id` 改为 **`{user_id}:{context_id}`**
     （`user_id` 为空回落 `context_id`），防止被猜到 context_id 即读他人历史。
  3. **答案缓存隔离**：`answer_cache.AnswerCache` 加 `scope`（`user` 默认 / `global`）+ `get/set(text, user_id)`，
     scope=user 时 key 前缀 `user_id`（空→`anon` 桶）。配置 `reception.yaml: answer_cache.scope`。
  4. **登录身份层**：新增 `src/ui/auth.py`（签名 cookie `a2a_cs_uid`，与 context 解耦）+ UI 登录路由
     `/login` `/api/register` `/api/login` `/api/logout` `/api/me`，`/` 与 `/api/chat` 未登录拦截（401/302）；
     前端 `static/login.html` + `index.html`（用户名显示/退出/401 跳转）。
- 兼容：`user_id=None` 全链路回落旧行为；既有接待/答复测试注入 fake 不受影响。
- 效果证据：离线 `tests/unit/test_answer_cache.py`（跨用户隔离/anon 桶/global 共享）、
  `tests/unit/test_service_stream.py`（缓存按用户隔离 + 会话按 `{uid}:{cid}` 隔离）、
  `tests/integration/test_ui_auth.py`（未登录 401、注册/登录发 cookie、登录后 user_id 透传 provider、两用户 id 互异）。

## 二、语义缓存 · 第二层（Milvus `cs_qc_lzs`，接待经 A2A 调检索）

### v1 — 2026-10-09
- 设计取舍：只有**检索 Agent** 进程持有 BGE-M3 + Milvus 连接，故语义缓存落在检索侧，接待不 import Milvus、
  经 A2A 读写。第一层=进程内精确 `AnswerCache`；Redis 精确层在单机/无容器场景无收益 → 不做。
- 改动：
  1. 新增 `agents/retrieval/semantic_cache.py`：`SemanticCacheStore`（独立集合 `cs_qc_lzs`，字段
     id/user_id/query_text/answer_text/dense_vector/created_at），**dense 做 L2 归一化后 IP=余弦**（阈值才有确定语义），
     按 `user_id` + `created_at`(TTL) 过滤；纯函数 `normalize/row_id/lookup_filter/decide_hit/build_row` 可离线测。
  2. `retriever.py` 复用已加载 `_client/_ef`/信号量懒建 store，加 `cache_lookup`/`cache_write`（异常吞掉不影响核心检索）。
  3. `retrieval/executor.py` 按 payload `action` 分发 `retrieve`(默认) / `cache_lookup` / `cache_write`；
     **两个缓存分支任何异常都回 `COMPLETED (hit=False/written=False)`，绝不 FAILED**（缓存是优化非依赖）。
  4. 新增 `agents/reception/semantic_cache.py` 的 `A2ASemanticCache`（每次新建独立 httpx client；读写失败降级）。
     `service.stream` 链路改为：**L1 → triage → L2 命中 → ReAct+检索+生成**；L2 命中即回填 L1 并短路；
     成功答案仅在独立提问(standalone)时写回 L2。接待 `app.py` 按 `semantic_cache.enabled` +
     `orchestration.downstream_retrieval_url` 注入。
  5. 配置：`retrieval.yaml: semantic_cache {enabled, collection, threshold=0.90, ttl_seconds=86400, dense_nprobe}`
     （阈值/TTL/集合名的**唯一真相源**在服务端）；`reception.yaml: semantic_cache {enabled, timeout_seconds}` + 新增 `downstream_retrieval_url`。
- 效果证据：
  - 离线（0 额度，假 client/embed）：`tests/unit/test_semantic_cache.py`（归一化/过滤表达式/命中判定/建集合/读写编排）、
    `tests/integration/test_retrieval_contract.py`（action 分发 + 异常降级不 FAILED）、`tests/unit/test_service_stream.py`
    （L2 命中短路+回填 L1、miss 跑链路+写回、lookup 异常降级、record 异常吞掉）。
  - **真机（VM Milvus 19530 OPEN，已实跑）**：改写"RAGFlow能读取哪些文件格式"命中 **0.9543**、"部署方法"命中 **0.9722**、
    无关"今天天气"最近余弦仅 0.4026（正确未命中）、同句切 u2 无行（隔离生效）；集合 `cs_qc_lzs` 建→写→检索→收尾删除正常，未在库留残留。
  - 阈值观察：真·改写 0.95+，无关 0.40，**0.90 偏稳**；如需更宽容可降至 ~0.86，暂保持 0.90。
  - 待办：全栈起来测「相似但不完全相同」重复问句的端到端时延收益（本次仅进程内直连 store 验通，未跑完整 4 服务）。

## 三、登录后端：SQLite → VM MySQL

### v1 — 2026-10-09（用户改需求：改用 SQL 数据库方案，先内置 user1/2/3，注册后置）
- 改动：
  1. 抽出共享口令哈希 `ui/auth.hash_password`（PBKDF2-HMAC-SHA256，两后端一致）；`AuthContext` 加 `registration_enabled`；
     `make_auth_context` 按 `auth.backend`（`sqlite`|`mysql`）选择存储；默认仍 sqlite 以免影响离线/回退。
  2. 新增 `ui/mysql_store.py`：`MySQLUserStore`（**注入 connection_factory**，接口同 `UserStore`）+ `make_mysql_store`
     （CREATE DATABASE/TABLE IF NOT EXISTS + 灌 seed），表名/库名白名单校验防注入；口令只从 `A2A_MYSQL_PASSWORD` 读。
  3. `config/ui.yaml: auth.backend=mysql`，`mysql {host 192.168.88.101, port 3306, database a2a_cs, user root, password_env}`，
     `seed_users` 内置 user1/user2/user3（口令 123456，仅演示），`registration_enabled: false`。
  4. `ui/app.py` 的 `/api/register` 在 `registration_enabled=false` 时返回 403（自助注册后置）。
- 依赖：仅把 **pymysql 装进项目 `.venv`**（`uv pip install --python <项目venv>`），未碰 rag_env/Anaconda 等其它环境。
- 效果证据：
  - 离线：`tests/unit/test_mysql_store.py`（假 DB-API 驱动 create/verify/username、重名/弱口令、种子幂等、标识符校验）、
    `tests/integration/test_ui_auth.py`（注册关闭→403；内置账号登录成功并透传 user_id）。
  - **真机（VM MySQL 3306，已实跑）**：建库 `a2a_cs`、建表 `users`、灌入 user1/2/3（PBKDF2）；正确口令返回 user_id、
    错误口令 None、username 反查正常、重复创建被拒。users 表保留在 VM 供登录使用。
  - 待办：完整 `python -m ui`(uvicorn) HTTP-through 走 MySQL 未实跑（store 已真机验、路由离线验、接口与 SQLite 后端 drop-in 一致）。

## 四、持久化记忆（会话短期历史落 MySQL）

### v1 — 2026-10-09（用户选：只存会话短期历史 / 0 额度 / 只恢复当前会话，不注入跨会话回忆）
- 背景：改造前 `InMemorySessionStore` 只在进程内存，接待重启即丢历史。
- 改动：
  1. `agents/reception/session.py` 新增 `MySQLSessionStore`（接口同 `InMemorySessionStore`：`history(key)`/`append(key,u,a)`，
     key 沿用 service 组合的 `{user_id}:{context_id}`，用户隔离顺手即得）；表 `a2a_cs.mem_session`
     （id/session_key/role/text/created_at，索引 (session_key,id)），首次用时 `CREATE TABLE IF NOT EXISTS`。
  2. `history` 读回 `ORDER BY id DESC LIMIT max_history_rounds*2` 再反转成时序（与内存 deque 语义一致）；
     **history/append 全 try/except 降级**：读失败当空历史、写失败静默，绝不因 DB 抖动打断主链路。
  3. `make_session_store` 加 `mysql` 分支（含 `CREATE DATABASE IF NOT EXISTS` + pymysql DictCursor 连接工厂，
     **init 失败自动回退内存**），`connection_factory` 可注入 → 离线用假 DB-API 测。
  4. `config/agents/reception.yaml: session.backend=mysql` + `session.mysql {host/port/database a2a_cs/user/password_env/table mem_session}`，
     口令走环境变量 `A2A_MYSQL_PASSWORD`（与登录同库不同表，两处配置有意重复，见边界）。
- 依赖：复用已装的 pymysql（仅项目 .venv）。
- 效果证据：
  - 离线（0 额度，假 DB-API）：`tests/unit/test_session_mysql.py`（append/history 时序往返、读回封顶 max*2、按 session_key 隔离、DB 失败降级、make_session_store 未知后端回落内存）。
  - **真机（VM MySQL 3306，已实跑）**：backend=mysql、store=MySQLSessionStore；写入按时间序正确；**重建 store（模拟进程重启）读回完全一致**；其它 session_key 空；追加后读回封顶 12(=6×2)；冒烟行已清理，`mem_session` 表保留供运行使用。
  - 待办：完整 `python -m agents.reception`(uvicorn) HTTP 端到端未实跑（store 已真机验、接口与内存后端 drop-in 一致）。

## 五、多会话历史列表 + 开启注册

### v1 — 2026-10-09（用户选：复用登录页注册标签 / 首句自动生成标题 / 列表支持 新建·切换·删除·重命名）
- 背景：注册功能此前关闭、且一浏览器只有一个会话（context_id 来自单 cookie），无法列/切/删历史会话。
- 改动：
  1. 开启自助注册：`ui.yaml auth.registration_enabled: true`（登录页“注册”标签直接生效；接待/后端逻辑不变）。
  2. 新增 `src/ui/conversation_store.py`：`ConversationStore`（注入 connection_factory，接口可离线测）+ `make_conversation_store`
     建库建表 `a2a_cs.conversations(user_id, context_id, title, created_at, updated_at)`，PK `(user_id,context_id)`；
     `list/touch_or_create(首句 derive_title)/rename/get_messages(读 mem_session)/delete(连带清该 session_key 的 mem_session)`；
     库表名白名单、口令走 `A2A_MYSQL_PASSWORD`。
  3. `ui/app.py`：`create_app` 加 `conversation_store` 注入参数（默认 None，**不自动连库**，离线测试零网络）；新增端点
     `GET /api/conversations`、`GET /api/messages?context_id=`、`POST /api/conversations/rename`、`POST /api/conversations/delete`（均需登录，未登录 401；越权 rename/delete 命中不到 → 404；未注入 store → 列表空/rename 501）；
     `POST /api/chat` 改为优先取 body `context_id`，登录且有 store 时 `touch_or_create`（首句建会话/刷新 updated_at，异常吞掉）。
  4. `build_app()` 真启动装配：建 auth + 尽力建 conversation_store（连不上只警告、列表降级，不阻断起服）；`ui/__main__.py` 改用 `build_app`。
  5. 前端 `index.html`：加左侧栏（“新建会话”按钮 + 历史列表，每项 hover 出重命名/删除；点切换拉 `/api/messages` 回显；发送带 active context_id；首句才落库、发完刷新列表）。
- 依赖：复用 pymysql（仅项目 .venv）。接待一行未改（每会话消息正文仍由接待写 `mem_session`）。
- 效果证据：
  - 离线（0 额度）：`tests/unit/test_conversation_store.py`（假 DB-API：标题派生/列表按 updated 倒序/读 mem_session 时序且按 session_key 隔离/rename/删除连带清消息/缺表当空）；
    `tests/integration/test_ui_conversations.py`（端点：401、chat upsert+list、rename/delete/越权404、messages、未注入 store 空列表/501）。
  - **真机（VM MySQL 3306，已实跑）**：建 conversations 表；两会话按 updated 倒序、标题取首句；`get_messages` 时序读回；他人 user 读不到（隔离）；rename 命中 True/越权 False；delete 会话并从 mem_session 清其消息、列表随之减少；冒烟行已清理。
  - 待办：浏览器整栈（uvicorn+接待）端到端未实跑（前端为静态页改动，Ctrl+F5 生效；store/端点已真机+离线验）。

### v1.1 — 2026-10-10（修复：兜底/缓存命中回合未落会话历史）
- 现象：同一账号在两个浏览器登录，浏览器 A 不刷新能看到对话、一刷新就空；浏览器 B 刷新后列表有该会话但点开无内容。
- 定位（查库）：`conversations` 有 6 行、`mem_session` 只有 4 个 key；缺的正是 "hello"、"你都能干什么" 两条 → 属非客服兜底回合。session_key 格式两边一致（`{完整user_uuid}:ui-xxx`），**不是** context_id 错配。
- 根因：写路径不对称——BFF 每次 chat 都 `touch_or_create` 写索引；但接待 `ReceptionService.stream` **只在正常生成成功路径 `session.append`**，非客服兜底 / L1 缓存命中 / L2 语义缓存命中 / 异常兜底 四条 return 路径都没写历史 → 刷新/换端读 `mem_session` 为空。
- 修复：接待在这四条产出最终答复的分支也都 `await self._session.append(sess_key, text, 该回合答案)`，令会话索引与消息正文一致。
- 效果证据：`tests/unit/test_service_stream.py` 新增 3 条回归（非客服兜底 / L1 缓存命中 / 异常兜底 均落历史，断言 `appended` 元组）；离线全量 **152 passed**。
- 遗留：修复前已存在的“空索引行”（无对应 mem_session）正文不可回填，需手动删除或在列表忽略。

### v1.2 — 2026-10-10（协作过程 trace 随会话持久化 + 左栏贴最左）
- 现象：刷新/换浏览器后文字气泡还在，但"工具调用/协作过程"块没了。根因：`mem_session` 每行只存 role+text，trace 只在当轮 SSE 里渲染、从没落库。
- 改动：
  1. `mem_session` 增加可空 `trace`(MEDIUMTEXT) 列；`MySQLSessionStore._ensure_table` 对老表用 `SHOW COLUMNS`+`ALTER ADD COLUMN` 幂等补列。
  2. `session.append(..., trace=None)`（三种后端一致）；接待 `ReceptionService.stream` 每条产出最终答复的分支都把该回合 trace 一并写入 assistant 行；`history()`（喂 LLM）继续只取 role/text，不受影响。
  3. `ConversationStore.get_messages` 读 `trace` 并 `_parse_trace`；老行/无列时回退只读文本（trace=None），失败返回 []。
  4. 前端 `renderHistory`：客服消息带 trace 时复用 `renderTrace()` 渲染协作过程块；另把 `.shell` 改满宽让左侧栏贴页面最左、聊天列在剩余区居中。
- 效果证据：离线 `tests/unit/test_service_stream.py::test_success_path_persists_full_trace_to_session` + `tests/unit/test_conversation_store.py`（trace 解析/坏值 None）；**真机（VM MySQL）** 对既有 `mem_session` 成功补列并读回完整 trace（tool_calls/retrieval/timings）。全量 **155 passed**。

## 全量回归
- `pytest`（默认排除 `llm`/`e2e`）：**155 passed, 4 deselected**，全程离线、0 大模型额度、无意外文件副作用。

## 已知边界与后续
- 语义缓存按 `user_id` 隔离 → 命中率低于共享缓存（隔离代价）；跨用户复用需显式改 scope。
- 持久化记忆当前只**存/恢复同一会话**（session_key=`{user_id}:{context_id}`）；**跨设备续聊不成立**——`context_id` 来自浏览器 cookie，换设备即新会话。要真跨设备需让 context_id 派生自登录态。
- `mem_session` 行随轮次单调增长（只在读端封顶 `max_history_rounds*2`），demo 量小无碍；长期跑需加清理任务（本次不做）。
- MySQL 连接参数在 `ui.yaml auth.mysql` 与 `reception.yaml session.mysql` 两处有意重复（同库不同表）；合并到 `common.yaml infra.mysql` 属另一小改。
- MySQL 演示账号是弱口令(123456)，生产必须改强口令 + 开启注册/RBAC + 口令走密钥管理；注册功能仍后置（`/api/register`→403）。
- 会话列表功能让 **UI BFF 也开始读写 `mem_session`**（消息正文由接待写、BFF 读回显/删除时清）——同库跨模块的务实取舍，未来若要严格分层可让接待暴露“取/删某会话消息”的 A2A 能力。
- 左栏只列**已持久化**的会话：全新未发过消息的会话不落 `conversations` 表，故不在列表出现（首条消息才建档）。
