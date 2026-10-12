# 提示词记录 · 索引（docs/prompts）

目的：**记载关键、有用的提示词与影响效果的运行时参数，可复现从当前到上线的全部效果**（票 09）。

- 运行时唯一源是 `config/`；本目录是档案，记录「当前版」，变更历史在 `../records/`。
- 每个 `<agent>.md` 内嵌一个 ```promptspec``` YAML 块，逐条声明 `file` + `path` + `text`/`value`；
  `tests/unit/test_prompt_consistency.py` 会逐条与 config 比对，**漂移即测试红**。
- DoD：**改提示词或生效参数 = 同一次 commit 同步更新记录**（票 09 §6）。

## 索引

| Agent | 记录文件 | 内容 |
|---|---|---|
| 接待 reception | `reception.md` | 分流提示词、兜底话术；`retrieval_top_k`、LLM 模型/温度 |
| 检索 retrieval | `retrieval.md` | 无 LLM 提示词；`top_k/rerank_n/权重/切片尺寸/infer_concurrency` 等生效参数 |
| 答复 reply | `reply.md` | 生成 system 提示词；LLM 模型/温度 |

变更历史：`../records/{reception,retrieval,reply}.md`。

手动跑校验：`./.venv/Scripts/python.exe -m common.prompt_check`
