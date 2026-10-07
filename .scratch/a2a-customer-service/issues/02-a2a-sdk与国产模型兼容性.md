Type: research
Status: resolved

## Question

官方 A2A Python SDK 是否强制依赖 Google ADK / Gemini？能不能用国产模型（通义千问）？

## Answer

不依赖。`a2a-sdk` 只要求 Python ≥3.10，核心 SDK 不绑 Google；LLM 由自定义 `AgentExecutor` 决定。最小 Agent = `AgentCard` + `AgentExecutor` + `DefaultRequestHandler`，暴露两个端点：`/.well-known/agent-card.json`（发现）与 JSON-RPC 服务端点。

通义千问 DashScope 提供 OpenAI 兼容接口（`compatible-mode/v1`），改 `base_url` + `api_key` 即可接入。

Docker 坑：Agent Card 里的 URL 必须**外部可达**（不能只写 `127.0.0.1`），端口映射要与 Card 声明一致。

来源：
- https://pypi.org/project/a2a-sdk/1.1.1/
- https://a2a-protocol.org/dev/tutorials/python/5-start-server/
- https://help.aliyun.com/zh/model-studio/base-url
- https://api-docs.deepseek.com/zh-cn/