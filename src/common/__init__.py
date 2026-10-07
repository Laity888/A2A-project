"""common 包：三 Agent + UI 共享的配置、日志、A2A 装配与 LLM 客户端。

约定见票 06 §10：每 Agent 一套 config/agents/*.yaml + src/agents/<name>/，
公共项集中在 common + config/common.yaml。参数不硬编码，密钥只从环境变量读。
"""
