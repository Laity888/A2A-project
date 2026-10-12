# 提示词记录 · 答复 Agent（reply）

当前生效提示词与参数。**运行时唯一源是 `config/`**，本文件是档案；一致性校验见
`tests/unit/test_prompt_consistency.py`。变更历史见 `docs/records/reply.md`。

## 当前版本

```promptspec
- id: reply.system
  file: config/agents/reply.yaml
  path: prompts.system
  text: |
    你是中文客服助手。请严格依据下面提供的「参考资料」回答用户问题：
    - 只使用参考资料与对话历史中的事实，不得编造资料之外的信息。
    - 回答简洁、友好、口语化：直给要点，控制在 200 字以内，不要复述整段资料；需要用户补充信息时明确说明。
    - 若参考资料不足以回答，如实说明并给出下一步建议（如转人工）。
- id: reply.llm_model
  file: config/common.yaml
  path: llm.model
  value: qwen3.8-max
- id: reply.llm_temperature
  file: config/common.yaml
  path: llm.temperature
  value: 0.3
- id: reply.model_override
  file: config/agents/reply.yaml
  path: llm.model
  value: qwen3.8-max
- id: reply.max_tokens
  file: config/agents/reply.yaml
  path: llm.max_tokens
  value: 600
```

## 字段说明

| id | 用途 | 输出 |
|---|---|---|
| reply.system | 基于 chunks+history 生成中文最终答复的 system prompt | TextPart 文本 |

答复消息组装顺序：system → history（user/assistant）→ 末尾 user（参考资料 + 问题），见 `agents/reply/agent.py:build_reply_messages`。

## 实测效果备注

`tests/integration/test_reply_contract.py` 断言组装结构与产出；真实出文见 `-m llm` e2e。
效果样例证据随工单 09 补。
