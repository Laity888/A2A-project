# 提示词记录 · 接待 Agent（reception）

当前生效提示词与关键参数。**运行时唯一源是 `config/`**，本文件是档案；一致性校验由
`tests/unit/test_prompt_consistency.py` 比对下面的 `promptspec` 块与 config，漂移即测试红。
变更历史见 `docs/records/reception.md`。

## 当前版本

```promptspec
- id: reception.triage
  file: config/agents/reception.yaml
  path: prompts.triage
  text: |
    你是客服意图分类器，把用户输入分到四类之一，严格只输出一行 JSON，禁止输出任何解释、答案、前后缀或 Markdown：
    {"intent": "chitchat|knowledge|transaction|out_of_scope", "confidence": 0.9, "reason": "一句话理由"}
    判定标准：chitchat=寒暄/问候/闲聊且与产品无关(如"哈喽""你是谁""谢谢")；knowledge=产品使用/配置/报错等需查知识库的专业咨询；transaction=下单/查订单/退款/转人工等业务操作；out_of_scope=与客服完全无关的越界请求。
    一句里既有寒暄又有实质问题时按实质部分判(如"你好，我想问部署"→knowledge)；拿不准时倾向 knowledge。
- id: reception.fallback_reply
  file: config/agents/reception.yaml
  path: prompts.fallback_reply
  text: |
    抱歉，我目前只能处理客服/工单相关的问题。
- id: reception.agent
  file: config/agents/reception.yaml
  path: prompts.agent
  text: |
    你是客服调度 agent。你可以调用提供的工具来回答问题：
    - 涉及产品使用/配置/报错等知识问题，用 kb_search 检索知识库；
    - 需要下单/查订单/转人工时，用对应工具；
    - 每个子问题只检索一次，整轮最多检索 2 次：不要用相同或近似的问题重复调用 kb_search；
    - 信息足够后立即停止调用工具，交由系统生成最终答复。
    每次只调用必要的工具，不要编造工具没有返回的事实。
- id: reception.max_steps
  file: config/agents/reception.yaml
  path: agent.max_steps
  value: 4
- id: reception.max_tool_calls
  file: config/agents/reception.yaml
  path: agent.max_tool_calls
  value: 3
- id: reception.llm_model
  file: config/common.yaml
  path: llm.model
  value: qwen3.8-max
- id: reception.llm_temperature
  file: config/common.yaml
  path: llm.temperature
  value: 0.3
- id: reception.llm_model_override
  file: config/agents/reception.yaml
  path: llm.model
  value: deepseek-v4-flash-0731
- id: reception.triage_timeout_seconds
  file: config/agents/reception.yaml
  path: llm.triage_timeout_seconds
  value: 20
- id: reception.triage_max_retries
  file: config/agents/reception.yaml
  path: llm.triage_max_retries
  value: 0
- id: reception.answer_cache_enabled
  file: config/agents/reception.yaml
  path: answer_cache.enabled
  value: true
- id: reception.answer_cache_ttl_seconds
  file: config/agents/reception.yaml
  path: answer_cache.ttl_seconds
  value: 86400
- id: reception.answer_cache_normalize
  file: config/agents/reception.yaml
  path: answer_cache.normalize
  value: true
```

## 字段说明

| id | 用途 | 输出 schema |
|---|---|---|
| reception.triage | 分流判定（四类分治） | 模型输出 `{intent(chitchat\|knowledge\|transaction\|out_of_scope), confidence, reason}`；`is_service` 由 `agents/reception/triage.py` 派生（工单 12/ReAct 改造后从票 06 §6 二元扩到四类） |
| reception.fallback_reply | 非客服/兜底话术 | text |

生效模型/参数：接待侧分流 + ReAct 决策走 `config/agents/reception.yaml.llm.model`（当前 `deepseek-v4-flash-0731`，提速，见工单：ReAct 改造），temperature 沿用 `config/common.yaml` 的 `0.3`；答复由 reply Agent 走 `config/common.yaml.llm.model`（`qwen3.8-max`）合成 grounded 文本；检索召回数 `retrieval.top_k=8`（`config/agents/retrieval.yaml`，票 04 §4 定案 k=3 后工单 09 因 2630 条语料召回太窄调到 8）。

## 实测效果备注

分流四分支由 `tests/integration/test_reception_contract.py` 断言（非客服→兜底且不调下游等）。
真实分流样例效果证据随工单 09 补。
